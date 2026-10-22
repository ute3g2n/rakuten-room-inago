"""Strict fixture-first contracts for observed Google Trends related Top data.

This module validates observations but does not control a browser or connect to
Google Trends. STEP 01 did not establish a permitted unattended UI connection.
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, ValidationError

from automation.inago.clock import Clock
from automation.inago.contracts import (
    FailureObservationCapture,
    IdempotencyConflict,
    RejectedContract,
)
from automation.inago.repository import _insert_artifact, validate_entity
from automation.jsonschema_formats import rfc3339_format_checker


_ROOT = Path(__file__).resolve().parents[2]
_BROWSER_SCHEMA_PATH = (
    _ROOT / "automation" / "inago" / "schemas" / "keywords.schema.json"
)
_KEY_RESPONSE_SCHEMA_PATH = (
    _ROOT / "automation" / "inago" / "schemas" / "ai-keywords.schema.json"
)
_BROWSER_SOURCE = "google_trends_public_ui"
_SNAPSHOT_SAVEPOINT = "inago_keyword_snapshot"
_WHITESPACE = re.compile(r"\s+")
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_SEED = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SCOPED_QUERY = {
    "language": "ja",
    "geo": "JP",
    "search_type": "web",
    "range": "90d",
    "list_type": "Top",
}
_LEVEL_LIMITS = {1: 20, 2: 10}
_THREE_MONTH_LABELS = frozenset(
    {
        "過去3か月間",
        "過去3か月",
        "過去3ヶ月間",
        "過去3ヶ月",
        "過去90日間",
        "Past3months",
    }
)


class TrendsContractError(RejectedContract):
    """An external observation or KEY response violates its narrow contract."""


class KeywordAcquisitionStopped(RejectedContract):
    """The daily keyword source failed or was invalid, so no more words may be used."""


@dataclass(frozen=True, slots=True)
class TrendsRequest:
    request_id: str
    run_id: str
    settings_version: str
    parent_word: str
    language: str = "ja"
    geo: str = "JP"
    search_type: str = "web"
    range: str = "90d"
    list_type: str = "Top"
    deadline_at: str | None = None


@dataclass(frozen=True, slots=True)
class ObservedVariant:
    word_id: str
    raw: str
    source_rank: int
    popularity_index: int | float | None


@dataclass(frozen=True, slots=True)
class TrendsWord:
    """One NFKC/whitespace-normalized term with all observed spellings retained."""

    word_id: str
    raw: str
    normalized: str
    source_rank: int
    popularity_index: int | float | None
    variants: tuple[ObservedVariant, ...]


@dataclass(frozen=True, slots=True)
class TrendsSnapshot:
    request: TrendsRequest
    snapshot_id: str
    status: str
    current_parent: str | None
    refresh_complete: bool
    fetched_at: str | None
    words: tuple[TrendsWord, ...]
    error_code: str | None
    ui_period_label: str | None = None

    @property
    def availability(self) -> str:
        if self.status == "observed":
            return "observed"
        if self.status == "unavailable":
            return "unavailable"
        return "unmatched"


@dataclass(frozen=True, slots=True)
class KeywordDecision:
    word_id: str
    decision: str
    reason: str


@dataclass(frozen=True, slots=True)
class StoredTrendsSnapshot:
    snapshot: TrendsSnapshot
    decisions: tuple[KeywordDecision, ...] | None


class BrowserSnapshotPort(Protocol):
    def fetch(self, request: TrendsRequest) -> Mapping[str, Any]:
        """Return one response for this exact request; never search another source."""


class FixtureBrowserPort:
    """Return only a fixture keyed by request ID; no network or browser is created."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        self._responses = {key: dict(value) for key, value in responses.items()}
        self.requests: list[TrendsRequest] = []

    def fetch(self, request: TrendsRequest) -> Mapping[str, Any]:
        self.requests.append(request)
        response = self._responses.get(request.request_id)
        if response is None:
            return {
                "request_id": request.request_id,
                "snapshot_id": f"missing-{request.request_id}",
                "run_id": request.run_id,
                "settings_version": request.settings_version,
                "source": _BROWSER_SOURCE,
                "parent_word": request.parent_word,
                "current_parent": None,
                "refresh_complete": False,
                **_SCOPED_QUERY,
                "status": "unavailable",
                "fetched_at": None,
                "error_code": "fixture_not_found",
                "words": [],
            }
        return dict(response)


class TrendKeywordSession:
    """Enforce one same-run request per parent and stop after the first bad result."""

    def __init__(
        self,
        port: BrowserSnapshotPort,
        *,
        run_id: str,
        settings_version: str,
    ) -> None:
        self._port = port
        self.run_id = _required_text(run_id, "run_id")
        self.settings_version = _required_text(settings_version, "settings_version")
        _require_safe_ref(self.run_id, "run_id")
        _require_safe_ref(self.settings_version, "settings_version")
        self._attempted_parents: set[str] = set()
        self._request_ids: set[str] = set()
        self._snapshot_ids: set[str] = set()
        self.stopped = False

    def fetch(self, parent_word: str, *, snapshot_id: str) -> TrendsSnapshot:
        if self.stopped:
            raise KeywordAcquisitionStopped(
                "daily keyword handling is stopped after a failed or invalid Trends result"
            )
        parent = _required_text(parent_word, "parent_word")
        request_id = _required_text(snapshot_id, "snapshot_id")
        _require_safe_ref(request_id, "request_id")
        normalized_parent = _normalize(parent)
        if normalized_parent in self._attempted_parents:
            raise KeywordAcquisitionStopped(
                "this parent already has a same-run Trends attempt; do not fetch or reuse another result"
            )
        if request_id in self._request_ids:
            raise KeywordAcquisitionStopped("request_id cannot be reused in one run")
        self._attempted_parents.add(normalized_parent)
        self._request_ids.add(request_id)
        request = TrendsRequest(
            request_id=request_id,
            run_id=self.run_id,
            settings_version=self.settings_version,
            parent_word=parent,
        )
        try:
            response = self._port.fetch(request)
            snapshot = parse_trends_response(response, request)
        except Exception as exc:
            self.stopped = True
            if isinstance(exc, TrendsContractError):
                raise
            return _invalid_snapshot(request, request_id, "source_adapter_error")
        if snapshot.snapshot_id in self._snapshot_ids:
            snapshot = _invalid_snapshot(
                request,
                snapshot.snapshot_id,
                "snapshot_id_reused",
                current_parent=snapshot.current_parent,
                refresh_complete=snapshot.refresh_complete,
                fetched_at=snapshot.fetched_at,
            )
        self._snapshot_ids.add(snapshot.snapshot_id)
        if snapshot.status != "observed":
            self.stopped = True
        return snapshot


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrendsContractError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise TrendsContractError(f"{field} cannot have surrounding whitespace")
    return value


def _require_safe_ref(value: str, field: str) -> None:
    if not _SAFE_REF.fullmatch(value):
        raise TrendsContractError(f"{field} is not a safe persisted reference")


def _require_safe_seed(value: str) -> None:
    if not _SAFE_SEED.fullmatch(value):
        raise TrendsContractError("seed is not a safe persisted seed")


def _aware_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return value


def _browser_schema() -> Draft202012Validator:
    try:
        schema = json.loads(_BROWSER_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=rfc3339_format_checker())
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise TrendsContractError(
            "Trends browser response schema is unavailable"
        ) from exc


def _normalize(raw: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", raw).strip())


def _invalid_snapshot(
    request: TrendsRequest,
    snapshot_id: str,
    error_code: str,
    *,
    current_parent: str | None = None,
    refresh_complete: bool = False,
    fetched_at: str | None = None,
) -> TrendsSnapshot:
    return TrendsSnapshot(
        request=request,
        snapshot_id=snapshot_id,
        status="invalid",
        current_parent=current_parent if isinstance(current_parent, str) else None,
        refresh_complete=refresh_complete is True,
        fetched_at=_aware_timestamp(fetched_at),
        words=(),
        error_code=error_code,
    )


def parse_trends_response(
    response: Mapping[str, Any], request: TrendsRequest
) -> TrendsSnapshot:
    """Validate query freshness before exposing any term to KEY or selection."""

    snapshot_id = response.get("snapshot_id") if isinstance(response, Mapping) else None
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        snapshot_id = f"invalid-{request.request_id}"
    if not _SAFE_REF.fullmatch(snapshot_id):
        return _invalid_snapshot(request, request.request_id, "snapshot_id_invalid")
    if not isinstance(response, Mapping):
        return _invalid_snapshot(request, snapshot_id, "response_not_object")
    if response.get("status") == "unavailable" and response.get("words"):
        return _invalid_snapshot(request, snapshot_id, "stale_words_on_failure")
    try:
        _browser_schema().validate(dict(response))
    except ValidationError:
        return _invalid_snapshot(request, snapshot_id, "response_schema_invalid")

    expected = {
        "request_id": request.request_id,
        "run_id": request.run_id,
        "settings_version": request.settings_version,
        "source": _BROWSER_SOURCE,
        "parent_word": request.parent_word,
        **_SCOPED_QUERY,
    }
    if any(response.get(key) != value for key, value in expected.items()):
        return _invalid_snapshot(
            request,
            snapshot_id,
            "request_contract_mismatch",
            current_parent=response.get("current_parent"),
            refresh_complete=response.get("refresh_complete") is True,
            fetched_at=_aware_timestamp(response.get("fetched_at")),
        )

    status = response["status"]
    if status == "unavailable":
        return TrendsSnapshot(
            request=request,
            snapshot_id=snapshot_id,
            status="unavailable",
            current_parent=None,
            refresh_complete=False,
            fetched_at=None,
            words=(),
            error_code=response["error_code"],
        )

    fetched_at = _aware_timestamp(response.get("fetched_at"))
    if (
        response["current_parent"] != request.parent_word
        or response["refresh_complete"] is not True
        or fetched_at is None
        or response["error_code"] is not None
    ):
        return _invalid_snapshot(
            request,
            snapshot_id,
            "query_freshness_mismatch",
            current_parent=response["current_parent"],
            refresh_complete=response["refresh_complete"],
            fetched_at=fetched_at,
        )

    try:
        observed = _parse_observed_words(response["words"])
    except TrendsContractError:
        return _invalid_snapshot(
            request,
            snapshot_id,
            "observed_words_invalid",
            current_parent=response["current_parent"],
            refresh_complete=True,
            fetched_at=fetched_at,
        )
    return TrendsSnapshot(
        request=request,
        snapshot_id=snapshot_id,
        status="observed",
        current_parent=response["current_parent"],
        refresh_complete=True,
        fetched_at=fetched_at,
        words=observed,
        error_code=None,
    )


def _parse_observed_words(values: object) -> tuple[TrendsWord, ...]:
    if not isinstance(values, list):
        raise TrendsContractError("Trends words must be an array")
    observations: list[ObservedVariant] = []
    seen_word_ids: set[str] = set()
    for item in values:
        if not isinstance(item, Mapping):
            raise TrendsContractError("Trends word must be an object")
        word_id = _required_text(item.get("word_id"), "word_id")
        _require_safe_ref(word_id, "word_id")
        raw = item.get("raw")
        if not isinstance(raw, str) or not raw.strip():
            raise TrendsContractError(
                "raw observed term must contain non-whitespace text"
            )
        rank = item.get("source_rank")
        popularity_index = item.get("popularity_index")
        if word_id in seen_word_ids:
            raise TrendsContractError("Trends word IDs must be unique in one snapshot")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise TrendsContractError("source_rank must be a positive observed rank")
        if popularity_index is not None and (
            isinstance(popularity_index, bool)
            or not isinstance(popularity_index, (int, float))
            or not math.isfinite(float(popularity_index))
            or popularity_index < 0
        ):
            raise TrendsContractError(
                "popularity_index must be observed numeric data or null"
            )
        if not _normalize(raw):
            raise TrendsContractError("normalized Trends word cannot be empty")
        seen_word_ids.add(word_id)
        observations.append(ObservedVariant(word_id, raw, rank, popularity_index))

    groups: dict[str, list[ObservedVariant]] = {}
    for observation in observations:
        groups.setdefault(_normalize(observation.raw), []).append(observation)
    normalized_words: list[tuple[int, int, TrendsWord]] = []
    for first_position, (normalized, variants) in enumerate(groups.items()):
        primary = min(variants, key=lambda value: value.source_rank)
        normalized_words.append(
            (
                primary.source_rank,
                first_position,
                TrendsWord(
                    word_id=primary.word_id,
                    raw=primary.raw,
                    normalized=normalized,
                    source_rank=primary.source_rank,
                    popularity_index=primary.popularity_index,
                    variants=tuple(variants),
                ),
            )
        )
    normalized_words.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in normalized_words)


def build_key_input(
    snapshot: TrendsSnapshot,
    *,
    level: int,
    parent_definition: str,
    child_definition: str,
) -> dict[str, Any]:
    """Send only observed IDs, normalized terms, and the explicit classification rule."""

    _require_observed(snapshot)
    if isinstance(level, bool) or level not in _LEVEL_LIMITS:
        raise TrendsContractError("KEY level must be 1 or 2")
    parent_rule = _required_text(parent_definition, "parent_definition")
    child_rule = _required_text(child_definition, "child_definition")
    return {
        "snapshot_id": snapshot.snapshot_id,
        "parent_word": snapshot.request.parent_word,
        "level": level,
        "classification_definition": {
            "parent": parent_rule,
            "child": child_rule,
            "exclusions": ["recipe", "shop_name", "attribute_only"],
        },
        "words": [
            {"word_id": word.word_id, "term": word.normalized}
            for word in snapshot.words
        ],
    }


def validate_key_decisions(
    snapshot: TrendsSnapshot,
    key_input: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
) -> tuple[KeywordDecision, ...]:
    """Require one schema-valid decision for every and only sent word ID."""

    _require_observed(snapshot)
    if not isinstance(key_input, Mapping):
        raise TrendsContractError("KEY input must be an object")
    expected_words = [
        {"word_id": word.word_id, "term": word.normalized} for word in snapshot.words
    ]
    if (
        key_input.get("snapshot_id") != snapshot.snapshot_id
        or key_input.get("parent_word") != snapshot.request.parent_word
        or isinstance(key_input.get("level"), bool)
        or key_input.get("level") not in _LEVEL_LIMITS
        or not isinstance(key_input.get("classification_definition"), Mapping)
        or set(key_input.get("classification_definition", {}))
        != {"parent", "child", "exclusions"}
        or key_input.get("words") != expected_words
        or set(key_input)
        != {"snapshot_id", "parent_word", "level", "classification_definition", "words"}
        or not isinstance(
            key_input.get("classification_definition", {}).get("parent"), str
        )
        or not key_input.get("classification_definition", {}).get("parent", "").strip()
        or not isinstance(
            key_input.get("classification_definition", {}).get("child"), str
        )
        or not key_input.get("classification_definition", {}).get("child", "").strip()
        or key_input.get("classification_definition", {}).get("exclusions")
        != ["recipe", "shop_name", "attribute_only"]
    ):
        raise TrendsContractError("KEY input no longer matches its observed snapshot")
    if not isinstance(responses, Sequence) or isinstance(responses, (str, bytes)):
        raise TrendsContractError("KEY responses must be an array")
    expected_ids = [word.word_id for word in snapshot.words]
    expected_set = set(expected_ids)
    if len(responses) != len(expected_ids):
        raise TrendsContractError(
            "KEY must return one decision for every observed word"
        )
    try:
        # Historical v1.3 snapshots have a different decision contract. This
        # reader does not authorize child classification in the Q1.4 daily path.
        schema = {
            "type": "object",
            "required": ["word_id", "decision", "reason"],
            "properties": {
                "word_id": {"type": "string", "minLength": 1},
                "decision": {"enum": ["child", "not_child", "unknown"]},
                "reason": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise TrendsContractError("KEY response schema is unavailable") from exc

    by_id: dict[str, KeywordDecision] = {}
    for response in responses:
        if not isinstance(response, Mapping):
            raise TrendsContractError("KEY response must be an object")
        try:
            validator.validate(dict(response))
        except ValidationError as exc:
            raise TrendsContractError(
                "KEY response violates ai-keywords schema"
            ) from exc
        word_id = response["word_id"]
        if word_id not in expected_set:
            raise TrendsContractError("KEY returned a word ID absent from the snapshot")
        if word_id in by_id:
            raise TrendsContractError("KEY returned a duplicate word ID")
        reason = response["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise TrendsContractError("KEY reason must contain an explanation")
        by_id[word_id] = KeywordDecision(word_id, response["decision"], reason)
    if set(by_id) != expected_set:
        raise TrendsContractError("KEY omitted one or more observed word IDs")
    return tuple(by_id[word_id] for word_id in expected_ids)


def child_candidates(
    snapshot: TrendsSnapshot,
    decisions: Sequence[KeywordDecision],
    *,
    level: int,
    seed: str,
) -> tuple[TrendsWord, ...]:
    """Apply the 20/10 cap by observed rank, randomizing only a tied boundary."""

    _require_observed(snapshot)
    if isinstance(level, bool) or level not in _LEVEL_LIMITS:
        raise TrendsContractError("keyword level must be 1 or 2")
    seed_value = _required_text(seed, "saved run seed")
    by_id = _validated_decision_map(snapshot, decisions)
    ordered_children = sorted(
        (word for word in snapshot.words if by_id[word.word_id].decision == "child"),
        key=lambda word: word.source_rank,
    )
    limit = _LEVEL_LIMITS[level]
    if len(ordered_children) <= limit:
        return tuple(ordered_children)

    selected: list[TrendsWord] = []
    index = 0
    while index < len(ordered_children):
        rank = ordered_children[index].source_rank
        end = index + 1
        while end < len(ordered_children) and ordered_children[end].source_rank == rank:
            end += 1
        rank_group = ordered_children[index:end]
        slots = limit - len(selected)
        if len(rank_group) <= slots:
            selected.extend(rank_group)
            index = end
            continue
        if slots > 0:
            rng = random.Random(
                f"{seed_value}|{snapshot.snapshot_id}|level-{level}|rank-{rank}"
            )
            selected.extend(rng.sample(rank_group, slots))
        break
    return tuple(selected)


def _validated_decision_map(
    snapshot: TrendsSnapshot, decisions: Sequence[KeywordDecision]
) -> dict[str, KeywordDecision]:
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
        raise TrendsContractError("keyword decisions must be a sequence")
    expected = {word.word_id for word in snapshot.words}
    result: dict[str, KeywordDecision] = {}
    for decision in decisions:
        if not isinstance(decision, KeywordDecision):
            raise TrendsContractError("keyword decision has an unsupported shape")
        if decision.word_id not in expected or decision.word_id in result:
            raise TrendsContractError(
                "keyword decision contains an unknown or repeated ID"
            )
        if decision.decision not in {"child", "not_child", "unknown"}:
            raise TrendsContractError(
                "keyword decision is outside the three allowed values"
            )
        if not isinstance(decision.reason, str) or not decision.reason.strip():
            raise TrendsContractError("keyword decision reason is required")
        result[decision.word_id] = decision
    if set(result) != expected:
        raise TrendsContractError(
            "keyword decisions must cover the complete observed snapshot"
        )
    return result


def choose_keyword(
    candidates: Sequence[TrendsWord], *, seed: str, context: str
) -> TrendsWord | None:
    """Uniformly choose from the observed candidate count, with no padding."""

    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise TrendsContractError("keyword candidates must be a sequence")
    if not candidates:
        return None
    if any(not isinstance(candidate, TrendsWord) for candidate in candidates):
        raise TrendsContractError("keyword candidates contain an unsupported record")
    seed_value = _required_text(seed, "saved run seed")
    context_value = _required_text(context, "selection context")
    ids = [candidate.word_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise TrendsContractError("keyword candidate IDs must be unique")
    return random.Random(f"{seed_value}|{context_value}").choice(list(candidates))


def save_keyword_snapshot(
    connection: sqlite3.Connection,
    snapshot: TrendsSnapshot,
    *,
    decisions: Sequence[KeywordDecision] = (),
) -> str:
    """Persist a validated observed/zero/failure snapshot into the existing Q-derived table."""

    _validate_snapshot_state(snapshot)
    if snapshot.status == "observed":
        decision_map = _validated_decision_map(snapshot, decisions) if decisions else {}
    else:
        if snapshot.words or decisions:
            raise TrendsContractError(
                "failed or invalid snapshots cannot retain candidate words"
            )
        decision_map = {}
    if snapshot.status not in {"observed", "unavailable", "invalid"}:
        raise TrendsContractError("unknown snapshot status")

    words_json_value = []
    decision_records: list[dict[str, Any]] = [
        {
            "kind": "snapshot",
            "run_id": snapshot.request.run_id,
            "settings_version": snapshot.request.settings_version,
            "request_id": snapshot.request.request_id,
            "requested_parent": snapshot.request.parent_word,
            "current_parent": snapshot.current_parent,
            "refresh_complete": snapshot.refresh_complete,
            "language": snapshot.request.language,
            "geo": snapshot.request.geo,
            "search_type": snapshot.request.search_type,
            "range": snapshot.request.range,
            "list_type": snapshot.request.list_type,
            "status": snapshot.status,
            "error_code": snapshot.error_code,
        }
    ]
    for word in snapshot.words:
        decision = decision_map.get(word.word_id)
        words_json_value.append(
            {
                "raw": word.raw,
                "normalized": word.normalized,
                "source_rank": word.source_rank,
                "popularity_index": word.popularity_index,
                "semantic_reason": (
                    decision.reason if decision else "classification_pending"
                ),
                "is_child": bool(decision and decision.decision == "child"),
            }
        )
        decision_records.append(
            {
                "kind": "word",
                "word_id": word.word_id,
                "decision": decision.decision if decision else "pending",
                "reason": decision.reason if decision else "classification_pending",
                "variants": [
                    {
                        "word_id": item.word_id,
                        "raw": item.raw,
                        "source_rank": item.source_rank,
                        "popularity_index": item.popularity_index,
                    }
                    for item in word.variants
                ],
            }
        )

    entity = {
        "snapshot_id": snapshot.snapshot_id,
        "parent_word": snapshot.request.parent_word,
        "source": _BROWSER_SOURCE,
        "geo": snapshot.request.geo,
        "range": snapshot.request.range,
        "search_type": snapshot.request.search_type,
        "list_type": snapshot.request.list_type,
        "words": words_json_value,
        "fetched_at": snapshot.fetched_at,
        "availability": snapshot.availability,
        "filter_decisions": decision_records,
    }
    validate_entity("keyword_snapshot", entity)

    words_json = _canonical_json(words_json_value)
    decisions_json = _canonical_json(decision_records)
    persisted_values = (
        snapshot.request.parent_word,
        _BROWSER_SOURCE,
        snapshot.request.geo,
        snapshot.request.range,
        snapshot.request.search_type,
        snapshot.request.list_type,
        words_json,
        snapshot.fetched_at,
        snapshot.availability,
        decisions_json,
    )

    owns_transaction = not connection.in_transaction
    savepoint = f"SAVEPOINT {_SNAPSHOT_SAVEPOINT}"
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(savepoint)
    try:
        run = connection.execute(
            "SELECT mode, status, settings_version FROM run WHERE run_id=?",
            (snapshot.request.run_id,),
        ).fetchone()
        if (
            run is None
            or run["mode"] != "daily"
            or run["status"] not in {"running", "partial"}
            or run["settings_version"] != snapshot.request.settings_version
        ):
            raise TrendsContractError(
                "snapshot does not match an active daily run and settings version"
            )
        same_parent_rows = connection.execute(
            "SELECT snapshot_id, filter_decisions_json FROM keyword_snapshot "
            "WHERE parent_word=? AND source=?",
            (snapshot.request.parent_word, _BROWSER_SOURCE),
        ).fetchall()
        for row in same_parent_rows:
            if row[0] == snapshot.snapshot_id:
                continue
            try:
                records = json.loads(row[1])
            except (TypeError, json.JSONDecodeError) as exc:
                raise TrendsContractError(
                    "stored parent snapshot metadata is not readable"
                ) from exc
            if not isinstance(records, list):
                raise TrendsContractError("stored parent snapshot metadata is invalid")
            metadata = [
                item
                for item in records
                if isinstance(item, dict) and item.get("kind") == "snapshot"
            ]
            if len(metadata) != 1:
                raise TrendsContractError("stored parent snapshot lineage is invalid")
            prior = metadata[0]
            if (
                prior.get("run_id") == snapshot.request.run_id
                and prior.get("settings_version") == snapshot.request.settings_version
            ):
                raise IdempotencyConflict(
                    "the same run already has a Trends snapshot for this parent"
                )
        existing = connection.execute(
            "SELECT parent_word, source, geo, range, search_type, list_type, words_json, "
            "fetched_at, availability, filter_decisions_json "
            "FROM keyword_snapshot WHERE snapshot_id=?",
            (snapshot.snapshot_id,),
        ).fetchone()
        if existing is not None:
            current_values = tuple(existing[index] for index in range(10))
            if current_values != persisted_values:
                if not _is_pending_snapshot_upgrade(
                    current_values,
                    persisted_values,
                    words_json_value,
                    snapshot,
                    decision_map,
                ):
                    raise IdempotencyConflict(
                        "snapshot_id was already persisted with different content"
                    )
                connection.execute(
                    "UPDATE keyword_snapshot SET words_json=?, filter_decisions_json=? "
                    "WHERE snapshot_id=?",
                    (words_json, decisions_json, snapshot.snapshot_id),
                )
        else:
            connection.execute(
                "INSERT INTO keyword_snapshot "
                "(snapshot_id, parent_word, source, geo, range, search_type, list_type, "
                "words_json, fetched_at, availability, filter_decisions_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (snapshot.snapshot_id, *persisted_values),
            )
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE {_SNAPSHOT_SAVEPOINT}")
        return snapshot.snapshot_id
    except BaseException:
        if owns_transaction:
            if connection.in_transaction:
                connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO {_SNAPSHOT_SAVEPOINT}")
            connection.execute(f"RELEASE {_SNAPSHOT_SAVEPOINT}")
        raise


def load_keyword_snapshot(
    connection: sqlite3.Connection,
    snapshot_id: str,
    *,
    request: TrendsRequest,
) -> StoredTrendsSnapshot | None:
    """Load only one exact snapshot after matching its run, version, and parent."""

    snapshot_ref = _required_text(snapshot_id, "snapshot_id")
    _require_safe_ref(snapshot_ref, "snapshot_id")
    row = connection.execute(
        "SELECT parent_word, source, geo, range, search_type, list_type, words_json, "
        "fetched_at, availability, filter_decisions_json "
        "FROM keyword_snapshot WHERE snapshot_id=?",
        (snapshot_ref,),
    ).fetchone()
    if row is None:
        return None
    try:
        words_data = json.loads(row[6])
        records = json.loads(row[9])
    except (TypeError, json.JSONDecodeError) as exc:
        raise TrendsContractError("stored Trends snapshot JSON is unreadable") from exc
    if not isinstance(words_data, list) or not isinstance(records, list):
        raise TrendsContractError("stored Trends snapshot JSON has the wrong shape")
    metadata_records = [
        item
        for item in records
        if isinstance(item, dict) and item.get("kind") == "snapshot"
    ]
    word_records = [
        item
        for item in records
        if isinstance(item, dict) and item.get("kind") == "word"
    ]
    if len(metadata_records) != 1 or len(word_records) != len(words_data):
        raise TrendsContractError("stored Trends snapshot lineage is incomplete")
    metadata = metadata_records[0]
    expected_metadata = {
        "run_id": request.run_id,
        "settings_version": request.settings_version,
        "request_id": request.request_id,
        "requested_parent": request.parent_word,
        "language": request.language,
        "geo": request.geo,
        "search_type": request.search_type,
        "range": request.range,
        "list_type": request.list_type,
    }
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        raise TrendsContractError(
            "stored snapshot does not match the requested run context"
        )
    if (
        row[0] != request.parent_word
        or row[1] != _BROWSER_SOURCE
        or row[2] != request.geo
        or row[3] != request.range
        or row[4] != request.search_type
        or row[5] != request.list_type
    ):
        raise TrendsContractError(
            "stored snapshot entity differs from its recorded context"
        )
    status = metadata.get("status")
    if status not in {"observed", "unavailable", "invalid"}:
        raise TrendsContractError("stored snapshot status is invalid")
    snapshot = TrendsSnapshot(
        request=request,
        snapshot_id=snapshot_ref,
        status=status,
        current_parent=metadata.get("current_parent"),
        refresh_complete=metadata.get("refresh_complete") is True,
        fetched_at=row[7],
        words=_restore_words(words_data, word_records),
        error_code=metadata.get("error_code"),
    )
    _validate_snapshot_state(snapshot)
    if row[8] != snapshot.availability:
        raise TrendsContractError(
            "stored availability differs from the snapshot status"
        )
    if status != "observed" and snapshot.words:
        raise TrendsContractError("failed snapshot contains candidate words")
    if status == "observed" and (
        snapshot.current_parent != request.parent_word
        or snapshot.refresh_complete is not True
        or _aware_timestamp(snapshot.fetched_at) is None
    ):
        raise TrendsContractError(
            "stored observed snapshot is not a fresh current-parent result"
        )

    decision_by_id: dict[str, KeywordDecision | None] = {}
    for record in word_records:
        word_id = record.get("word_id")
        if not isinstance(word_id, str) or word_id in decision_by_id:
            raise TrendsContractError(
                "stored word decisions contain an invalid or repeated ID"
            )
        state = record.get("decision")
        reason = record.get("reason")
        if state == "pending" and reason == "classification_pending":
            decision_by_id[word_id] = None
        elif (
            state in {"child", "not_child", "unknown"}
            and isinstance(reason, str)
            and reason.strip()
        ):
            decision_by_id[word_id] = KeywordDecision(word_id, state, reason)
        else:
            raise TrendsContractError("stored keyword decision is invalid")
    expected_word_ids = {word.word_id for word in snapshot.words}
    if set(decision_by_id) != expected_word_ids:
        raise TrendsContractError("stored decisions do not match the observed word IDs")
    if not snapshot.words:
        decisions: tuple[KeywordDecision, ...] | None = ()
    elif all(value is None for value in decision_by_id.values()):
        decisions = None
    elif any(value is None for value in decision_by_id.values()):
        raise TrendsContractError(
            "stored keyword decisions are only partially classified"
        )
    else:
        decisions = tuple(
            decision_by_id[word.word_id]
            for word in snapshot.words
            if decision_by_id[word.word_id] is not None
        )
        _validated_decision_map(snapshot, decisions)
    return StoredTrendsSnapshot(snapshot=snapshot, decisions=decisions)


def _restore_words(
    words_data: list[Any], word_records: list[dict[str, Any]]
) -> tuple[TrendsWord, ...]:
    if len(words_data) != len(word_records):
        raise TrendsContractError(
            "stored observed terms and decisions have different lengths"
        )
    restored: list[TrendsWord] = []
    seen_word_ids: set[str] = set()
    for word_data, record in zip(words_data, word_records):
        if not isinstance(word_data, dict) or not isinstance(record, dict):
            raise TrendsContractError("stored observed term is not an object")
        word_id = record.get("word_id")
        if (
            not isinstance(word_id, str)
            or not _SAFE_REF.fullmatch(word_id)
            or word_id in seen_word_ids
        ):
            raise TrendsContractError(
                "stored observed terms contain invalid or duplicate word IDs"
            )
        seen_word_ids.add(word_id)
        variants_value = record.get("variants")
        if not isinstance(variants_value, list) or not variants_value:
            raise TrendsContractError("stored raw word variants are missing")
        variants: list[ObservedVariant] = []
        for item in variants_value:
            if not isinstance(item, dict):
                raise TrendsContractError("stored raw word variant is invalid")
            variant_id = item.get("word_id")
            raw = item.get("raw")
            rank = item.get("source_rank")
            popularity_index = item.get("popularity_index")
            if (
                not isinstance(variant_id, str)
                or not _SAFE_REF.fullmatch(variant_id)
                or not isinstance(raw, str)
                or not raw.strip()
                or isinstance(rank, bool)
                or not isinstance(rank, int)
                or rank < 1
                or (
                    popularity_index is not None
                    and (
                        isinstance(popularity_index, bool)
                        or not isinstance(popularity_index, (int, float))
                        or not math.isfinite(float(popularity_index))
                        or popularity_index < 0
                    )
                )
            ):
                raise TrendsContractError(
                    "stored raw word variant violates its contract"
                )
            variants.append(ObservedVariant(variant_id, raw, rank, popularity_index))
        primary = min(variants, key=lambda value: value.source_rank)
        normalized = word_data.get("normalized")
        if (
            not isinstance(normalized, str)
            or normalized != _normalize(primary.raw)
            or record["word_id"] != primary.word_id
            or word_data.get("source_rank") != primary.source_rank
            or word_data.get("popularity_index") != primary.popularity_index
            or word_data.get("is_child") is not (record.get("decision") == "child")
            or word_data.get("semantic_reason") != record.get("reason")
        ):
            raise TrendsContractError(
                "stored normalized term differs from its observations"
            )
        restored.append(
            TrendsWord(
                word_id=word_id,
                raw=primary.raw,
                normalized=normalized,
                source_rank=primary.source_rank,
                popularity_index=primary.popularity_index,
                variants=tuple(variants),
            )
        )
    return tuple(restored)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_snapshot_state(snapshot: TrendsSnapshot) -> None:
    if not isinstance(snapshot, TrendsSnapshot):
        raise TrendsContractError("snapshot has an unsupported record type")
    if snapshot.status == "observed":
        if (
            snapshot.current_parent != snapshot.request.parent_word
            or snapshot.refresh_complete is not True
            or _aware_timestamp(snapshot.fetched_at) is None
            or snapshot.error_code is not None
        ):
            raise TrendsContractError(
                "observed snapshot is not a fresh current-parent result"
            )
        return
    if snapshot.status == "unavailable":
        if (
            snapshot.words
            or snapshot.current_parent is not None
            or snapshot.refresh_complete is not False
            or snapshot.fetched_at is not None
            or not isinstance(snapshot.error_code, str)
            or not snapshot.error_code.strip()
        ):
            raise TrendsContractError(
                "unavailable snapshot has inconsistent failure state"
            )
        return
    if snapshot.status == "invalid":
        if (
            snapshot.words
            or not isinstance(snapshot.error_code, str)
            or not snapshot.error_code.strip()
        ):
            raise TrendsContractError(
                "invalid snapshot must discard words and retain a reason"
            )
        return
    raise TrendsContractError("unknown snapshot status")


def _is_pending_snapshot_upgrade(
    current_values: tuple[Any, ...],
    new_values: tuple[Any, ...],
    new_words: list[dict[str, Any]],
    snapshot: TrendsSnapshot,
    decisions: Mapping[str, KeywordDecision],
) -> bool:
    if not decisions or snapshot.status != "observed":
        return False
    if current_values[:6] != new_values[:6] or current_values[7:9] != new_values[7:9]:
        return False
    try:
        old_words = json.loads(current_values[6])
        old_records = json.loads(current_values[9])
    except (TypeError, json.JSONDecodeError):
        return False
    old_by_id = {
        record.get("word_id"): record
        for record in old_records
        if isinstance(record, dict) and record.get("kind") == "word"
    }
    metadata = [
        record
        for record in old_records
        if isinstance(record, dict) and record.get("kind") == "snapshot"
    ]
    if len(metadata) != 1 or len(old_by_id) != len(snapshot.words):
        return False
    expected_metadata = {
        "kind": "snapshot",
        "run_id": snapshot.request.run_id,
        "settings_version": snapshot.request.settings_version,
        "request_id": snapshot.request.request_id,
        "requested_parent": snapshot.request.parent_word,
        "current_parent": snapshot.current_parent,
        "refresh_complete": snapshot.refresh_complete,
        "language": snapshot.request.language,
        "geo": snapshot.request.geo,
        "search_type": snapshot.request.search_type,
        "range": snapshot.request.range,
        "list_type": snapshot.request.list_type,
        "status": snapshot.status,
        "error_code": snapshot.error_code,
    }
    if metadata[0] != expected_metadata:
        return False
    old_signatures = [
        (
            item.get("raw"),
            item.get("normalized"),
            item.get("source_rank"),
            item.get("popularity_index"),
        )
        for item in old_words
        if isinstance(item, dict)
    ]
    new_signatures = [
        (item["raw"], item["normalized"], item["source_rank"], item["popularity_index"])
        for item in new_words
    ]
    if old_signatures != new_signatures:
        return False
    for word in snapshot.words:
        old = old_by_id.get(word.word_id)
        if (
            not isinstance(old, dict)
            or old.get("decision") != "pending"
            or old.get("reason") != "classification_pending"
            or old.get("variants")
            != [
                {
                    "word_id": variant.word_id,
                    "raw": variant.raw,
                    "source_rank": variant.source_rank,
                    "popularity_index": variant.popularity_index,
                }
                for variant in word.variants
            ]
        ):
            return False
    return True


def _require_observed(snapshot: TrendsSnapshot) -> None:
    if snapshot.status != "observed":
        raise KeywordAcquisitionStopped(
            "KEY and selection require a successfully refreshed current-parent snapshot"
        )


GROUP_RULES_VERSION = "requirements-1.4"
TOP_WORD_LIMIT = 20
ROOM_WORD_LIMIT = 5
_GROUP_SAVEPOINT = "inago_keyword_groups14"


@dataclass(frozen=True, slots=True)
class GroupedKeywordDecision:
    word_id: str
    decision: str
    group_ref: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class SelectedRoomKeyword:
    group_ref: str
    word_id: str
    raw: str
    source_rank: int


@dataclass(frozen=True, slots=True)
class StoredGroupedSnapshot:
    snapshot: TrendsSnapshot
    decisions: tuple[GroupedKeywordDecision, ...] | None
    selected: tuple[SelectedRoomKeyword, ...]
    seed: str | None


def parse_grouped_top_response(
    response: Mapping[str, Any], request: TrendsRequest
) -> TrendsSnapshot:
    """Accept current Q1.4 Top data and retain the raw first twenty before grouping."""

    if request.range != "3m":
        raise TrendsContractError("new daily keyword acquisition requires range=3m")
    if not isinstance(response, Mapping):
        return _invalid_snapshot(request, request.request_id, "response_not_object")
    try:
        _browser_schema().validate(dict(response))
    except ValidationError:
        return _invalid_snapshot(request, request.request_id, "response_schema_invalid")
    expected = {
        "request_id": request.request_id,
        "snapshot_id": request.request_id,
        "run_id": request.run_id,
        "settings_version": request.settings_version,
        "source": _BROWSER_SOURCE,
        "parent_word": request.parent_word,
        "language": "ja",
        "geo": "JP",
        "search_type": "web",
        "range": "3m",
        "list_type": "Top",
    }
    if any(response.get(key) != value for key, value in expected.items()):
        return _invalid_snapshot(request, request.request_id, "query_scope_mismatch")
    if response["status"] == "unavailable":
        return TrendsSnapshot(
            request,
            request.request_id,
            "unavailable",
            None,
            False,
            None,
            (),
            response["error_code"],
        )
    period = _normalize(response.get("ui_period_label", "")).replace(" ", "")
    if (
        response["current_parent"] != request.parent_word
        or response["refresh_complete"] is not True
        or period not in _THREE_MONTH_LABELS
        or _aware_timestamp(response["fetched_at"]) is None
    ):
        return _invalid_snapshot(
            request, request.request_id, "query_freshness_mismatch"
        )
    ordered = sorted(
        enumerate(response["words"]), key=lambda item: (item[1]["source_rank"], item[0])
    )[:TOP_WORD_LIMIT]
    words = []
    seen = set()
    for _, item in ordered:
        word_id = item["word_id"]
        if (
            not _SAFE_REF.fullmatch(word_id)
            or word_id in seen
            or not _normalize(item["raw"])
        ):
            return _invalid_snapshot(
                request, request.request_id, "word_identity_invalid"
            )
        seen.add(word_id)
        variant = ObservedVariant(
            word_id, item["raw"], item["source_rank"], item["popularity_index"]
        )
        words.append(
            TrendsWord(
                word_id,
                item["raw"],
                _normalize(item["raw"]),
                item["source_rank"],
                item["popularity_index"],
                (variant,),
            )
        )
    return TrendsSnapshot(
        request,
        request.request_id,
        "observed",
        request.parent_word,
        True,
        response["fetched_at"],
        tuple(words),
        None,
        response["ui_period_label"],
    )


def grouped_key_input(snapshot: TrendsSnapshot) -> dict[str, Any]:
    """Expose only the observed raw Top20 and its stable snapshot identity to KEY."""

    _require_group_snapshot(snapshot)
    _require_observed(snapshot)
    return {
        "snapshot_id": snapshot.snapshot_id,
        "query_word": snapshot.request.parent_word,
        "words": [
            {"word_id": word.word_id, "raw": word.raw, "source_rank": word.source_rank}
            for word in snapshot.words
        ],
    }


def validate_grouped_key_response(
    snapshot: TrendsSnapshot, response: Mapping[str, Any]
) -> tuple[GroupedKeywordDecision, ...]:
    """Bind one batch result to every observed ID and validate group references."""

    _require_group_snapshot(snapshot)
    _require_observed(snapshot)
    try:
        document = json.loads(_KEY_RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(document).validate(dict(response))
    except (
        OSError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise TrendsContractError("grouped KEY response violates schema") from exc
    if (
        response["snapshot_id"] != snapshot.snapshot_id
        or response["query_word"] != snapshot.request.parent_word
    ):
        raise TrendsContractError("grouped KEY snapshot or query mismatch")
    decisions = tuple(GroupedKeywordDecision(**item) for item in response["decisions"])
    _group_decision_map(snapshot, decisions)
    return tuple(
        {item.word_id: item for item in decisions}[word.word_id]
        for word in snapshot.words
    )


def _group_decision_map(
    snapshot: TrendsSnapshot, decisions: Sequence[GroupedKeywordDecision]
) -> dict[str, GroupedKeywordDecision]:
    expected = {word.word_id: word for word in snapshot.words}
    result: dict[str, GroupedKeywordDecision] = {}
    for item in decisions:
        if (
            not isinstance(item, GroupedKeywordDecision)
            or item.word_id not in expected
            or item.word_id in result
            or not isinstance(item.reason, str)
            or not item.reason.strip()
            or item.decision
            not in {"query_same", "duplicate_member", "candidate", "unknown"}
        ):
            raise TrendsContractError(
                "grouped KEY contains missing, duplicate or invalid word identity"
            )
        if item.decision == "query_same":
            if item.group_ref is not None:
                raise TrendsContractError(
                    "query-only words cannot belong to a selectable group"
                )
        elif item.group_ref not in expected:
            raise TrendsContractError("group reference is not an observed word ID")
        if item.decision in {"candidate", "unknown"} and item.group_ref != item.word_id:
            raise TrendsContractError(
                "candidate group anchors must use their own observed ID"
            )
        if (
            _normalize(expected[item.word_id].raw)
            == _normalize(snapshot.request.parent_word)
            and item.decision != "query_same"
        ):
            raise TrendsContractError(
                "the query itself cannot be a ROOM related-word candidate"
            )
        result[item.word_id] = item
    if set(result) != set(expected):
        raise TrendsContractError(
            "grouped KEY must cover exactly the observed Top20 IDs"
        )
    groups: dict[str, list[str]] = {}
    for item in result.values():
        if item.group_ref is not None:
            anchor = result[item.group_ref]
            if anchor.decision not in {"candidate", "unknown"}:
                raise TrendsContractError("group has no candidate anchor")
            groups.setdefault(item.group_ref, []).append(item.word_id)
    order = {word.word_id: i for i, word in enumerate(snapshot.words)}
    for anchor, members in groups.items():
        if min(members, key=order.__getitem__) != anchor:
            raise TrendsContractError("group anchor must be its first observed member")
        if result[anchor].decision == "unknown" and len(members) != 1:
            raise TrendsContractError(
                "uncertain relationships cannot silently merge other words"
            )
    return result


def draw_room_keywords(
    snapshot: TrendsSnapshot, decisions: Sequence[GroupedKeywordDecision], *, seed: str
) -> tuple[SelectedRoomKeyword, ...]:
    """Uniformly draw up to five groups and one unmodified raw expression per group."""

    _require_group_snapshot(snapshot)
    _require_observed(snapshot)
    by_id = _group_decision_map(snapshot, decisions)
    groups: dict[str, list[TrendsWord]] = {}
    for word in snapshot.words:
        group = by_id[word.word_id].group_ref
        if group is not None:
            groups.setdefault(group, []).append(word)
    saved_seed = _required_text(seed, "saved run seed")
    rng = random.Random(f"{saved_seed}|{snapshot.snapshot_id}|{GROUP_RULES_VERSION}")
    chosen = rng.sample(list(groups), min(ROOM_WORD_LIMIT, len(groups)))
    selected = []
    for group in chosen:
        word = rng.choice(groups[group])
        selected.append(
            SelectedRoomKeyword(group, word.word_id, word.raw, word.source_rank)
        )
    return tuple(selected)


def _require_group_snapshot(snapshot: TrendsSnapshot) -> None:
    if not isinstance(snapshot, TrendsSnapshot) or snapshot.request.range != "3m":
        raise TrendsContractError("Q1.4 grouping requires a three-month snapshot")
    if (
        snapshot.snapshot_id != snapshot.request.request_id
        or len(snapshot.words) > TOP_WORD_LIMIT
    ):
        raise TrendsContractError("Q1.4 snapshot identity or Top20 size is invalid")
    if (
        snapshot.request.language,
        snapshot.request.geo,
        snapshot.request.search_type,
        snapshot.request.list_type,
    ) != ("ja", "JP", "web", "Top"):
        raise TrendsContractError("Q1.4 snapshot scope is not Japan/web/Top")
    if any(not isinstance(word, TrendsWord) for word in snapshot.words):
        raise TrendsContractError("Q1.4 snapshot contains malformed observed words")
    if len({word.word_id for word in snapshot.words}) != len(snapshot.words):
        raise TrendsContractError("Q1.4 snapshot contains repeated word IDs")
    for word in snapshot.words:
        if (
            not isinstance(word, TrendsWord)
            or not _SAFE_REF.fullmatch(word.word_id)
            or not isinstance(word.raw, str)
            or not _normalize(word.raw)
            or word.normalized != _normalize(word.raw)
            or isinstance(word.source_rank, bool)
            or not isinstance(word.source_rank, int)
            or word.source_rank < 1
            or (
                word.popularity_index is not None
                and (
                    isinstance(word.popularity_index, bool)
                    or not isinstance(word.popularity_index, (int, float))
                    or not math.isfinite(word.popularity_index)
                    or word.popularity_index < 0
                )
            )
        ):
            raise TrendsContractError("Q1.4 snapshot contains malformed observed words")
    if snapshot.status == "observed":
        if (
            snapshot.current_parent != snapshot.request.parent_word
            or snapshot.refresh_complete is not True
            or _aware_timestamp(snapshot.fetched_at) is None
            or not isinstance(snapshot.ui_period_label, str)
            or _normalize(snapshot.ui_period_label).replace(" ", "")
            not in _THREE_MONTH_LABELS
            or snapshot.error_code is not None
        ):
            raise TrendsContractError("Q1.4 observed snapshot is not fresh and scoped")
    elif snapshot.status in {"invalid", "unavailable"}:
        if snapshot.words or not snapshot.error_code:
            raise TrendsContractError("failed snapshot cannot keep candidate words")
    else:
        raise TrendsContractError("unknown snapshot status")


def _group_record(
    snapshot: TrendsSnapshot,
    decisions: Sequence[GroupedKeywordDecision] | None,
    seed: str | None,
    selected: Sequence[SelectedRoomKeyword] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = _group_decision_map(snapshot, decisions) if decisions is not None else {}
    words = []
    for word in snapshot.words:
        decision = by_id.get(word.word_id)
        words.append(
            {
                "word_id": word.word_id,
                "raw": word.raw,
                "normalized": word.normalized,
                "source_rank": word.source_rank,
                "popularity_index": word.popularity_index,
                "decision": decision.decision if decision else "pending",
                "group_ref": decision.group_ref if decision else None,
                "semantic_reason": (
                    decision.reason if decision else "classification_pending"
                ),
            }
        )
    metadata = [
        {
            "kind": "snapshot",
            "rules_version": GROUP_RULES_VERSION,
            "run_id": snapshot.request.run_id,
            "settings_version": snapshot.request.settings_version,
            "request_id": snapshot.request.request_id,
            "status": snapshot.status,
            "error_code": snapshot.error_code,
            "current_parent": snapshot.current_parent,
            "refresh_complete": snapshot.refresh_complete,
            "classification_complete": decisions is not None,
            "seed": seed,
            "selected": [
                {
                    "group_ref": word.group_ref,
                    "word_id": word.word_id,
                    "raw": word.raw,
                    "source_rank": word.source_rank,
                }
                for word in selected
            ],
        }
    ]
    return words, metadata


def save_grouped_snapshot(
    connection: sqlite3.Connection,
    snapshot: TrendsSnapshot,
    *,
    decisions: Sequence[GroupedKeywordDecision] | None = None,
    seed: str | None = None,
) -> str:
    """Save raw first, then atomically adopt grouping and saved draws exactly once."""

    _require_group_snapshot(snapshot)
    if decisions is not None:
        _require_observed(snapshot)
        if seed is None:
            raise TrendsContractError("completed grouping requires the saved run seed")
    elif seed is not None:
        raise TrendsContractError("a pending grouping cannot claim a draw seed")
    words, records = _group_record(snapshot, decisions, seed)
    entity = {
        "snapshot_id": snapshot.snapshot_id,
        "parent_word": snapshot.request.parent_word,
        "source": _BROWSER_SOURCE,
        "geo": "JP",
        "range": "3m",
        "search_type": "web",
        "list_type": "Top",
        "words": words,
        "fetched_at": snapshot.fetched_at,
        "availability": snapshot.availability,
        "filter_decisions": records,
        "ui_period_label": snapshot.ui_period_label,
    }
    validate_entity("keyword_snapshot", entity)
    owns = not connection.in_transaction
    connection.execute("BEGIN IMMEDIATE" if owns else f"SAVEPOINT {_GROUP_SAVEPOINT}")
    try:
        run = connection.execute(
            "SELECT mode,status,settings_version,seed FROM run WHERE run_id=?",
            (snapshot.request.run_id,),
        ).fetchone()
        if (
            run is None
            or run[0] != "daily"
            or run[1] not in {"running", "partial"}
            or run[2] != snapshot.request.settings_version
        ):
            raise TrendsContractError(
                "grouped snapshot does not match its active run, settings or seed"
            )
        previous = load_grouped_snapshot(
            connection, snapshot.snapshot_id, request=snapshot.request
        )
        if previous is not None and previous.decisions is not None:
            if tuple(decisions or ()) != previous.decisions or seed != previous.seed:
                raise IdempotencyConflict(
                    "saved group decisions or draw seed cannot change"
                )
        if seed is not None and seed != run[3]:
            raise TrendsContractError("draw seed differs from the saved run seed")
        if decisions is not None and (previous is None or previous.decisions is None):
            selected = draw_room_keywords(snapshot, decisions, seed=seed)
            words, records = _group_record(snapshot, decisions, seed, selected)
        values = (
            snapshot.request.parent_word,
            _BROWSER_SOURCE,
            "JP",
            "3m",
            "web",
            "Top",
            _canonical_json(words),
            snapshot.fetched_at,
            snapshot.availability,
            _canonical_json(records),
            snapshot.ui_period_label,
        )
        if previous is not None:
            if previous.snapshot != snapshot:
                raise IdempotencyConflict(
                    "snapshot identity already has different raw observations"
                )
            if previous.decisions is not None:
                # The previous branch validated content; preserve stored draws.
                pass
            elif decisions is not None:
                connection.execute(
                    "UPDATE keyword_snapshot SET words_json=?,filter_decisions_json=? WHERE snapshot_id=?",
                    (values[6], values[9], snapshot.snapshot_id),
                )
        else:
            others = connection.execute(
                "SELECT snapshot_id,filter_decisions_json FROM keyword_snapshot WHERE parent_word=? AND range='3m'",
                (snapshot.request.parent_word,),
            ).fetchall()
            for row in others:
                prior = json.loads(row[1])
                if any(
                    isinstance(item, dict)
                    and item.get("kind") == "snapshot"
                    and item.get("run_id") == snapshot.request.run_id
                    for item in prior
                ):
                    raise IdempotencyConflict(
                        "this run already captured this theme query"
                    )
            connection.execute(
                "INSERT INTO keyword_snapshot (snapshot_id,parent_word,source,geo,range,search_type,list_type,words_json,fetched_at,availability,filter_decisions_json,ui_period_label) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot.snapshot_id, *values),
            )
        if owns:
            connection.commit()
        else:
            connection.execute(f"RELEASE {_GROUP_SAVEPOINT}")
        return snapshot.snapshot_id
    except BaseException:
        if owns:
            connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO {_GROUP_SAVEPOINT}")
            connection.execute(f"RELEASE {_GROUP_SAVEPOINT}")
        raise


def load_grouped_snapshot(
    connection: sqlite3.Connection, snapshot_id: str, *, request: TrendsRequest
) -> StoredGroupedSnapshot | None:
    """Read stored decisions and raw draws; resuming performs no new random draw."""

    row = connection.execute(
        "SELECT parent_word,source,geo,range,search_type,list_type,words_json,fetched_at,availability,filter_decisions_json,ui_period_label FROM keyword_snapshot WHERE snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    if (
        tuple(row[:6])
        != (request.parent_word, _BROWSER_SOURCE, "JP", "3m", "web", "Top")
        or snapshot_id != request.request_id
    ):
        raise TrendsContractError("stored grouping query identity differs from request")
    try:
        words_data = json.loads(row[6])
        records = json.loads(row[9])
        if (
            not isinstance(words_data, list)
            or not isinstance(records, list)
            or any(not isinstance(item, dict) for item in records)
        ):
            raise ValueError("stored snapshot JSON is not a record list")
        metadata = [item for item in records if item.get("kind") == "snapshot"]
        if len(metadata) != 1:
            raise ValueError("snapshot lineage missing")
        meta = metadata[0]
        if (
            meta["rules_version"] != GROUP_RULES_VERSION
            or meta["run_id"] != request.run_id
            or meta["settings_version"] != request.settings_version
            or meta["request_id"] != request.request_id
        ):
            raise ValueError("snapshot lineage mismatch")
        if not isinstance(meta["classification_complete"], bool):
            raise ValueError("classification state must be explicit")
        words = tuple(
            TrendsWord(
                item["word_id"],
                item["raw"],
                item["normalized"],
                item["source_rank"],
                item["popularity_index"],
                (
                    ObservedVariant(
                        item["word_id"],
                        item["raw"],
                        item["source_rank"],
                        item["popularity_index"],
                    ),
                ),
            )
            for item in words_data
        )
        snapshot = TrendsSnapshot(
            request,
            snapshot_id,
            meta["status"],
            meta["current_parent"],
            meta["refresh_complete"],
            row[7],
            words,
            meta["error_code"],
            row[10],
        )
        _require_group_snapshot(snapshot)
        decisions = (
            tuple(
                GroupedKeywordDecision(
                    item["word_id"],
                    item["decision"],
                    item["group_ref"],
                    item["semantic_reason"],
                )
                for item in words_data
            )
            if meta["classification_complete"]
            else None
        )
        if decisions is not None:
            _group_decision_map(snapshot, decisions)
            run = connection.execute(
                "SELECT settings_version,seed FROM run WHERE run_id=?",
                (request.run_id,),
            ).fetchone()
            groups = {
                item.group_ref for item in decisions if item.group_ref is not None
            }
            if (
                not isinstance(meta["seed"], str)
                or not meta["seed"].strip()
                or run is None
                or run[0] != request.settings_version
                or run[1] != meta["seed"]
                or len(meta["selected"]) != min(ROOM_WORD_LIMIT, len(groups))
            ):
                raise ValueError("completed grouping is missing saved draw results")
        elif meta["selected"] or meta["seed"] is not None:
            raise ValueError("pending grouping claims selection")
        selected = tuple(SelectedRoomKeyword(**item) for item in meta["selected"])
        if len(selected) > ROOM_WORD_LIMIT or len(
            {item.group_ref for item in selected}
        ) != len(selected):
            raise ValueError("stored selection repeats a group or exceeds five")
        by_id = {word.word_id: word for word in words}
        decision_map = {item.word_id: item for item in decisions or ()}
        for item in selected:
            word = by_id[item.word_id]
            if (
                item.raw != word.raw
                or item.source_rank != word.source_rank
                or decision_map[item.word_id].group_ref != item.group_ref
            ):
                raise ValueError("stored selection mismatches observed raw word")
        return StoredGroupedSnapshot(snapshot, decisions, selected, meta["seed"])
    except (ValueError, KeyError, TypeError) as exc:
        raise TrendsContractError(
            "stored grouped snapshot is incomplete or inconsistent"
        ) from exc


class GroupKeywordSession:
    """Acquire Q1.4 theme Top data once per query and stop after source failure."""

    def __init__(
        self, port: BrowserSnapshotPort, *, run_id: str, settings_version: str
    ):
        self.port = port
        self.run_id = _required_text(run_id, "run_id")
        self.settings_version = _required_text(settings_version, "settings_version")
        self.attempted_queries: set[str] = set()
        self.stopped = False

    def fetch(self, query_word: str, *, snapshot_id: str) -> TrendsSnapshot:
        if self.stopped:
            raise KeywordAcquisitionStopped("daily keyword acquisition is stopped")
        query = _required_text(query_word, "query_word")
        _require_safe_ref(snapshot_id, "snapshot_id")
        normalized = _normalize(query)
        if normalized in self.attempted_queries:
            raise IdempotencyConflict(
                "load the saved snapshot instead of refetching this query"
            )
        self.attempted_queries.add(normalized)
        request = TrendsRequest(
            snapshot_id, self.run_id, self.settings_version, query, range="3m"
        )
        try:
            result = parse_grouped_top_response(self.port.fetch(request), request)
        except Exception as exc:
            self.stopped = True
            raise KeywordAcquisitionStopped(
                f"keyword port failed: {type(exc).__name__}"
            ) from exc
        if result.status != "observed":
            self.stopped = True
        return result


@dataclass(frozen=True, slots=True)
class TrendsAcquisitionResult:
    """Durable outcome for one run/theme/settings Trends acquisition."""

    acquisition_id: str
    status: str
    attempts_used: int
    snapshot: TrendsSnapshot | None
    next_retry_at: str | None = None


class DurableGroupKeywordSession:
    """Fetch one theme's public Top20 at most five times across process restarts.

    Every request is reserved in SQLite before the source is called. A successful
    response is saved before it is returned so a later grouping/save failure can
    resume from the same response without another browser request.
    """

    MAX_ATTEMPTS = 5
    MIN_RETRY_SECONDS = 60
    NON_RETRYABLE_SOURCE_ERRORS = frozenset(
        {
            "http_401",
            "http_403",
            "access_denied",
            "source_access_stopped",
            "source_cancelled",
            "owned_cleanup_unconfirmed",
        }
    )
    PROCESSING_ERROR_SOURCE_ERRORS = frozenset(
        {
            "invalid_request",
            "source_response_invalid",
            "source_process_error",
            "browser_adapter_error",
            "query_widget_ambiguous",
            "query_widget_contract_mismatch",
        }
    )

    def __init__(
        self,
        connection: sqlite3.Connection,
        port: BrowserSnapshotPort,
        clock: Clock,
        *,
        run_id: str,
        settings_version: str,
        event_kind: str,
        ordinal: int,
        theme_id: str,
        seed: str,
        deadline_at: str | None = None,
        acquisition_id: str,
    ) -> None:
        self.connection = connection
        self.port = port
        self.clock = clock
        self.run_id = _required_text(run_id, "run_id")
        self.settings_version = _required_text(settings_version, "settings_version")
        self.event_kind = event_kind
        self.ordinal = ordinal
        self.theme_id = _required_text(theme_id, "theme_id")
        self.seed = _required_text(seed, "seed")
        self.deadline_at = (
            _parse_aware_datetime(deadline_at, "deadline_at")
            if deadline_at is not None
            else None
        )
        self.acquisition_id = _required_text(acquisition_id, "acquisition_id")
        interval = getattr(port, "trends_min_interval_seconds", 0)
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(interval)
            or interval < 0
        ):
            raise TrendsContractError(
                "Trends source interval must be finite and non-negative"
            )
        self.minimum_request_interval_seconds = float(interval)
        for value, field in (
            (self.run_id, "run_id"),
            (self.settings_version, "settings_version"),
            (self.theme_id, "theme_id"),
            (self.acquisition_id, "acquisition_id"),
        ):
            _require_safe_ref(value, field)
        _require_safe_seed(self.seed)
        if event_kind not in {"initial", "additional"}:
            raise TrendsContractError("event_kind must be initial or additional")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise TrendsContractError("theme ordinal must be a positive integer")
        if connection.in_transaction:
            raise TrendsContractError(
                "durable Trends attempts require an idle, exclusively owned connection"
            )

    def fetch(self, query_word: str) -> TrendsAcquisitionResult:
        query = _required_text(query_word, "query_word")
        self._ensure_acquisition(query)
        previous = self._load_acquisition()
        if (
            previous["status"] == "processing_error"
            and previous["error_code"]
            in {"query_widget_contract_mismatch", "query_widget_ambiguous"}
            and int(previous["attempts_used"]) < self.MAX_ATTEMPTS
        ):
            # A new invocation can use the repaired UI adapter. Retain every
            # failed attempt and continue the same acquisition's attempt budget.
            self._set_acquisition_status("collecting", None, None)
        while True:
            row = self._load_acquisition()
            status = str(row["status"])
            attempts_used = int(row["attempts_used"])
            if status == "collecting":
                pending_success = self.connection.execute(
                    "SELECT 1 FROM trends_acquisition_attempt WHERE acquisition_id=? "
                    "AND status='response_ready' LIMIT 1",
                    (self.acquisition_id,),
                ).fetchone()
                if pending_success is not None:
                    self._set_acquisition_status("response_ready", None, None)
                    row = self._load_acquisition()
                    status = "response_ready"
            if status == "response_ready":
                if self.expire_pending_response_at_deadline():
                    row = self._load_acquisition()
                    return TrendsAcquisitionResult(
                        self.acquisition_id,
                        "deadline",
                        int(row["attempts_used"]),
                        None,
                        row["next_retry_at"],
                    )
                return self._saved_response(row)
            if status in {"observed", "normal_zero"}:
                return self._saved_adopted_snapshot(row)
            if status in {"exhausted", "deadline"}:
                return TrendsAcquisitionResult(
                    self.acquisition_id,
                    status,
                    attempts_used,
                    None,
                    row["next_retry_at"],
                )
            if status == "processing_error":
                raise TrendsContractError(
                    "saved Trends acquisition is a processing error and requires diagnosis"
                )

            self._mark_abandoned_attempt_if_any()
            row = self._load_acquisition()
            if row["status"] != "collecting":
                continue
            attempts_used = int(row["attempts_used"])
            if attempts_used >= self.MAX_ATTEMPTS:
                self._set_acquisition_status(
                    "exhausted", None, "attempt_budget_exhausted"
                )
                return TrendsAcquisitionResult(
                    self.acquisition_id, "exhausted", attempts_used, None
                )

            if attempts_used and row["next_retry_at"]:
                retry_at = _parse_aware_datetime(row["next_retry_at"], "next_retry_at")
                now = self.clock.now_jst()
                if now.tzinfo is None or now.utcoffset() is None:
                    raise TrendsContractError("retry clock must return an aware time")
                now = now.astimezone(retry_at.tzinfo)
                if self.deadline_at is not None:
                    now_at_deadline = now.astimezone(self.deadline_at.tzinfo)
                    if (
                        retry_at > self.deadline_at
                        or now_at_deadline >= self.deadline_at
                    ):
                        self._set_acquisition_status(
                            "deadline", retry_at.isoformat(), "retry_after_deadline"
                        )
                        return TrendsAcquisitionResult(
                            self.acquisition_id,
                            "deadline",
                            attempts_used,
                            None,
                            retry_at.isoformat(),
                        )
                self.clock.wait(max(0.0, (retry_at - now).total_seconds()))
                if self._deadline_reached():
                    self._set_acquisition_status(
                        "deadline", retry_at.isoformat(), "retry_after_deadline"
                    )
                    return TrendsAcquisitionResult(
                        self.acquisition_id,
                        "deadline",
                        attempts_used,
                        None,
                        retry_at.isoformat(),
                    )

            now = self.clock.now_jst()
            if now.tzinfo is None or now.utcoffset() is None:
                raise TrendsContractError("retry clock must return an aware time")
            if self._deadline_reached():
                self._set_acquisition_status("deadline", None, "daily_cutoff")
                return TrendsAcquisitionResult(
                    self.acquisition_id, "deadline", attempts_used, None
                )

            if not self._wait_for_source_interval():
                paced = self._load_acquisition()
                return TrendsAcquisitionResult(
                    self.acquisition_id,
                    "deadline",
                    attempts_used,
                    None,
                    paced["next_retry_at"],
                )
            request = self._reserve_attempt(query, attempts_used + 1)
            try:
                response = self.port.fetch(request)
            except TimeoutError:
                response = _unavailable_group_response(request, "source_timeout")
            except ConnectionError:
                response = _unavailable_group_response(
                    request, "source_connection_error"
                )
            except Exception as exc:
                error_code = _safe_error_code(type(exc).__name__)
                response = {"error_code": error_code}
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    error_code,
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code=error_code,
                )
                raise TrendsContractError(
                    "Trends source raised an unexpected implementation error"
                ) from exc
            retry_after = _retry_after_seconds(response)
            if isinstance(response, Mapping) and "retry_after_seconds" in response:
                response = {
                    key: value
                    for key, value in response.items()
                    if key != "retry_after_seconds"
                }
            try:
                snapshot = parse_grouped_top_response(response, request)
            except Exception as exc:
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    _safe_error_code(type(exc).__name__),
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code="response_contract_invalid",
                )
                raise TrendsContractError(
                    "Trends response could not be validated"
                ) from exc

            if snapshot.status == "invalid":
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    snapshot.error_code or "response_contract_invalid",
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code=snapshot.error_code,
                )
                raise TrendsContractError(
                    "Trends response identity, scope or shape mismatched"
                )

            source_error = snapshot.error_code or "source_unavailable"
            if (
                snapshot.status == "unavailable"
                and source_error in self.NON_RETRYABLE_SOURCE_ERRORS
            ):
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    source_error,
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code=source_error,
                )
                raise KeywordAcquisitionStopped(
                    "Trends access was denied or cancelled; do not retry through another route"
                )

            if (
                snapshot.status == "unavailable"
                and source_error in self.PROCESSING_ERROR_SOURCE_ERRORS
            ):
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    source_error,
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code=source_error,
                )
                raise TrendsContractError(
                    "Trends source violated its request or response contract"
                )

            if self._deadline_reached():
                self._finish_attempt(
                    request.request_id,
                    "deadline",
                    (
                        source_error
                        if snapshot.status == "unavailable"
                        else "daily_cutoff"
                    ),
                    response,
                    None,
                    acquisition_status="deadline",
                    acquisition_error_code="daily_cutoff",
                )
                return TrendsAcquisitionResult(
                    self.acquisition_id,
                    "deadline",
                    attempts_used + 1,
                    None,
                )

            if snapshot.status == "observed":
                self._finish_attempt(
                    request.request_id,
                    "response_ready",
                    None,
                    response,
                    None,
                    acquisition_status="response_ready",
                )
                return TrendsAcquisitionResult(
                    self.acquisition_id,
                    "response_ready",
                    attempts_used + 1,
                    snapshot,
                )

            wait_seconds = max(
                self.MIN_RETRY_SECONDS,
                self.minimum_request_interval_seconds,
                retry_after if retry_after is not None else 0,
            )
            retry_at = self.clock.now_jst() + timedelta(seconds=wait_seconds)
            # The acquisition stops at five attempts. The public source's
            # not-before time still applies to the next theme and survives restart.
            next_retry = (
                retry_at.isoformat()
                if attempts_used + 1 < self.MAX_ATTEMPTS
                or self.minimum_request_interval_seconds
                else None
            )
            if attempts_used + 1 >= self.MAX_ATTEMPTS:
                acquisition_status = "exhausted"
                acquisition_next_retry_at = None
                acquisition_error_code = source_error or "attempt_budget_exhausted"
            elif self.deadline_at is not None and retry_at > self.deadline_at:
                acquisition_status = "deadline"
                acquisition_next_retry_at = retry_at.isoformat()
                acquisition_error_code = "retry_after_deadline"
            else:
                acquisition_status = "collecting"
                acquisition_next_retry_at = retry_at.isoformat()
                acquisition_error_code = snapshot.error_code
            self._finish_attempt(
                request.request_id,
                "unavailable",
                source_error,
                response,
                next_retry,
                acquisition_status=acquisition_status,
                acquisition_next_retry_at=acquisition_next_retry_at,
                acquisition_error_code=acquisition_error_code,
            )
            if attempts_used + 1 >= self.MAX_ATTEMPTS:
                return TrendsAcquisitionResult(
                    self.acquisition_id, "exhausted", attempts_used + 1, None
                )
            if self.deadline_at is not None and retry_at > self.deadline_at:
                return TrendsAcquisitionResult(
                    self.acquisition_id,
                    "deadline",
                    attempts_used + 1,
                    None,
                    retry_at.isoformat(),
                )

    def _wait_for_source_interval(self) -> bool:
        """Keep public requests apart across themes and process restarts."""
        if not self.minimum_request_interval_seconds:
            return True
        previous = self.connection.execute(
            "SELECT ended_at,next_retry_at FROM trends_acquisition_attempt "
            "WHERE ended_at IS NOT NULL ORDER BY julianday(ended_at) DESC,request_id DESC LIMIT 1"
        ).fetchone()
        if previous is None:
            return True
        ready_at = _parse_aware_datetime(previous[0], "ended_at") + timedelta(
            seconds=self.minimum_request_interval_seconds
        )
        if previous[1]:
            ready_at = max(
                ready_at, _parse_aware_datetime(previous[1], "next_retry_at")
            )
        now = self.clock.now_jst()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TrendsContractError("retry clock must return an aware time")
        if ready_at <= now:
            return True
        if self.deadline_at is not None and ready_at >= self.deadline_at:
            self._set_acquisition_status(
                "deadline", ready_at.isoformat(), "retry_after_deadline"
            )
            return False
        self._set_acquisition_status("collecting", ready_at.isoformat(), None)
        logging.getLogger(__name__).info(
            "Trends public interval acquisition_id=%s ready_at=%s wait_seconds=%.1f",
            self.acquisition_id,
            ready_at.isoformat(),
            (ready_at - now).total_seconds(),
        )
        self.clock.wait((ready_at - now).total_seconds())
        if self._deadline_reached():
            self._set_acquisition_status(
                "deadline", ready_at.isoformat(), "retry_after_deadline"
            )
            return False
        return True

    def adopt_snapshot(self, snapshot: TrendsSnapshot) -> None:
        """Link the already persisted raw/grouped snapshot to its acquisition."""

        if self.expire_pending_response_at_deadline():
            raise TrendsContractError("Trends snapshot arrived after the daily cutoff")

        row = self._load_acquisition()
        if (
            snapshot.request.run_id != self.run_id
            or snapshot.request.settings_version != self.settings_version
            or snapshot.request.parent_word != row["query_word"]
            or snapshot.status != "observed"
        ):
            raise TrendsContractError("adopted snapshot does not match its acquisition")
        if row["status"] in {"observed", "normal_zero"}:
            if row["snapshot_id"] != snapshot.snapshot_id:
                raise IdempotencyConflict(
                    "acquisition was adopted with another snapshot"
                )
            return
        if row["status"] != "response_ready":
            raise TrendsContractError("only a saved successful response can be adopted")
        response_row = self.connection.execute(
            "SELECT request_id FROM trends_acquisition_attempt "
            "WHERE acquisition_id=? AND status='response_ready' ORDER BY attempt_no DESC LIMIT 1",
            (self.acquisition_id,),
        ).fetchone()
        if response_row is None or response_row["request_id"] != snapshot.snapshot_id:
            raise TrendsContractError("snapshot request ID differs from saved response")
        stored = load_grouped_snapshot(
            self.connection, snapshot.snapshot_id, request=snapshot.request
        )
        if stored is None:
            raise TrendsContractError("successful grouped snapshot is not persisted")
        status = "normal_zero" if not stored.snapshot.words else "observed"
        self._set_acquisition_status(status, None, None, snapshot.snapshot_id)

    def expire_pending_response_at_deadline(self) -> bool:
        """Discard a saved success only when its optional cutoff has passed."""

        row = self._load_acquisition()
        if row["status"] == "deadline":
            return True
        if row["status"] != "response_ready" or not self._deadline_reached():
            return False
        attempt = self.connection.execute(
            "SELECT request_id FROM trends_acquisition_attempt WHERE acquisition_id=? "
            "AND status='response_ready' ORDER BY attempt_no DESC LIMIT 1",
            (self.acquisition_id,),
        ).fetchone()
        if attempt is None:
            raise TrendsContractError("saved successful Trends response is missing")
        now = self.clock.now_jst().isoformat()

        def expire():
            updated = self.connection.execute(
                "UPDATE trends_acquisition_attempt SET status='deadline',ended_at=?,"
                "error_code='daily_cutoff',next_retry_at=NULL WHERE request_id=? "
                "AND status='response_ready'",
                (now, attempt[0]),
            ).rowcount
            if updated != 1:
                raise IdempotencyConflict("saved Trends response changed during cutoff")
            self.connection.execute(
                "UPDATE trends_acquisition SET status='deadline',next_retry_at=NULL,"
                "snapshot_id=NULL,error_code='daily_cutoff',updated_at=? "
                "WHERE acquisition_id=? AND status='response_ready'",
                (now, self.acquisition_id),
            )

        self._transaction(expire)
        return True

    def _deadline_reached(self) -> bool:
        if self.deadline_at is None:
            return False
        now = self.clock.now_jst()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TrendsContractError("retry clock must return an aware time")
        return now.astimezone(self.deadline_at.tzinfo) >= self.deadline_at

    def _request_deadline_at(self) -> str | None:
        return self.deadline_at.isoformat() if self.deadline_at is not None else None

    def _ensure_acquisition(self, query: str) -> None:
        event_id = f"theme-selection:{self.run_id}:{self.event_kind}:{self.ordinal:02d}"
        self._transaction(
            lambda: self._ensure_acquisition_in_transaction(query, event_id)
        )

    def _ensure_acquisition_in_transaction(self, query: str, event_id: str) -> None:
        run = self.connection.execute(
            "SELECT mode,status,settings_version,seed FROM run WHERE run_id=?",
            (self.run_id,),
        ).fetchone()
        event = self.connection.execute(
            "SELECT selection_id,theme_id,seed FROM theme_selection WHERE run_id=? "
            "AND event_kind=? AND count_event_id=?",
            (self.run_id, self.event_kind, event_id),
        ).fetchone()
        if (
            run is None
            or tuple(run) != ("daily", "running", self.settings_version, self.seed)
            or event is None
            or tuple(event) != (event_id, self.theme_id, self.seed)
        ):
            raise TrendsContractError(
                "acquisition run, theme selection or settings identity differs"
            )
        current = self.connection.execute(
            "SELECT acquisition_id,run_id,settings_version,event_kind,ordinal,theme_id,"
            "query_word,seed FROM trends_acquisition WHERE acquisition_id=?",
            (self.acquisition_id,),
        ).fetchone()
        expected = (
            self.acquisition_id,
            self.run_id,
            self.settings_version,
            self.event_kind,
            self.ordinal,
            self.theme_id,
            query,
            self.seed,
        )
        if current is not None:
            if tuple(current) != expected:
                raise IdempotencyConflict(
                    "saved Trends acquisition identity or query changed"
                )
            return
        now = self.clock.now_jst().isoformat()
        self.connection.execute(
            "INSERT INTO trends_acquisition (acquisition_id,run_id,settings_version,"
            "event_kind,ordinal,theme_id,query_word,seed,status,attempts_used,next_retry_at,"
            "snapshot_id,error_code,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'collecting',0,NULL,NULL,NULL,?,?)",
            (*expected, now, now),
        )

    def _load_acquisition(self):
        row = self.connection.execute(
            "SELECT * FROM trends_acquisition WHERE acquisition_id=?",
            (self.acquisition_id,),
        ).fetchone()
        if row is None:
            raise TrendsContractError("durable Trends acquisition disappeared")
        return row

    def _reserve_attempt(self, query: str, attempt_no: int) -> TrendsRequest:
        request_id = f"{self.acquisition_id}-a{attempt_no}"
        _require_safe_ref(request_id, "request_id")
        now = self.clock.now_jst().isoformat()

        def reserve():
            row = self._load_acquisition()
            if row["status"] != "collecting" or row["attempts_used"] + 1 != attempt_no:
                raise IdempotencyConflict("Trends attempt budget changed concurrently")
            prior = self.connection.execute(
                "SELECT request_id FROM trends_acquisition_attempt "
                "WHERE acquisition_id=? ORDER BY attempt_no DESC LIMIT 1",
                (self.acquisition_id,),
            ).fetchone()
            self.connection.execute(
                "INSERT INTO trends_acquisition_attempt (request_id,acquisition_id,attempt_no,"
                "retry_of,started_at,ended_at,status,error_code,evidence_ref,next_retry_at,response_json) "
                "VALUES (?,?,?,?,?,NULL,'started',NULL,NULL,NULL,NULL)",
                (
                    request_id,
                    self.acquisition_id,
                    attempt_no,
                    prior[0] if prior else None,
                    now,
                ),
            )
            self.connection.execute(
                "UPDATE trends_acquisition SET attempts_used=?,next_retry_at=NULL,updated_at=? "
                "WHERE acquisition_id=?",
                (attempt_no, now, self.acquisition_id),
            )

        self._transaction(reserve)
        return TrendsRequest(
            request_id,
            self.run_id,
            self.settings_version,
            query,
            range="3m",
            deadline_at=self._request_deadline_at(),
        )

    def _mark_abandoned_attempt_if_any(self) -> None:
        row = self.connection.execute(
            "SELECT request_id,attempt_no FROM trends_acquisition_attempt WHERE acquisition_id=? "
            "AND status='started' ORDER BY attempt_no DESC LIMIT 1",
            (self.acquisition_id,),
        ).fetchone()
        if row is None:
            return
        now = self.clock.now_jst().isoformat()
        saved_reader = getattr(self.port, "load_saved_response", None)
        saved_response = (
            saved_reader(row["request_id"]) if callable(saved_reader) else None
        )
        if isinstance(saved_response, Mapping):
            acquisition = self._load_acquisition()
            request = TrendsRequest(
                row["request_id"],
                self.run_id,
                self.settings_version,
                acquisition["query_word"],
                range="3m",
                deadline_at=self._request_deadline_at(),
            )
            retry_after = _retry_after_seconds(saved_response)
            response = {
                key: value
                for key, value in saved_response.items()
                if key != "retry_after_seconds"
            }
            snapshot = parse_grouped_top_response(response, request)
            if snapshot.status == "invalid":
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    snapshot.error_code or "response_contract_invalid",
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code=snapshot.error_code,
                )
                return
            if snapshot.status == "observed":
                self._finish_attempt(
                    request.request_id,
                    "response_ready",
                    None,
                    response,
                    None,
                    acquisition_status="response_ready",
                )
                return
            source_error = snapshot.error_code or "source_unavailable"
            if source_error in self.NON_RETRYABLE_SOURCE_ERRORS:
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    source_error,
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code=source_error,
                )
                return
            if source_error in self.PROCESSING_ERROR_SOURCE_ERRORS:
                self._finish_attempt(
                    request.request_id,
                    "processing_error",
                    source_error,
                    response,
                    None,
                    acquisition_status="processing_error",
                    acquisition_error_code=source_error,
                )
                return
            if self._deadline_reached():
                self._finish_attempt(
                    request.request_id,
                    "deadline",
                    (
                        source_error
                        if snapshot.status == "unavailable"
                        else "daily_cutoff"
                    ),
                    response,
                    None,
                    acquisition_status="deadline",
                    acquisition_error_code="daily_cutoff",
                )
                return
            next_number = int(row["attempt_no"]) + 1
            next_retry = None
            retry_at = None
            if (
                next_number <= self.MAX_ATTEMPTS
                or self.minimum_request_interval_seconds
            ):
                wait_seconds = max(
                    self.MIN_RETRY_SECONDS,
                    self.minimum_request_interval_seconds,
                    retry_after if retry_after is not None else 0,
                )
                retry_at = self.clock.now_jst() + timedelta(seconds=wait_seconds)
                next_retry = retry_at.isoformat()
            if next_number > self.MAX_ATTEMPTS:
                acquisition_status = "exhausted"
                acquisition_next_retry_at = None
                acquisition_error_code = source_error
            elif (
                self.deadline_at is not None
                and retry_at is not None
                and retry_at > self.deadline_at
            ):
                acquisition_status = "deadline"
                acquisition_next_retry_at = retry_at.isoformat()
                acquisition_error_code = "retry_after_deadline"
            else:
                acquisition_status = "collecting"
                acquisition_next_retry_at = next_retry
                acquisition_error_code = source_error
            self._finish_attempt(
                request.request_id,
                "unavailable",
                source_error,
                response,
                next_retry,
                acquisition_status=acquisition_status,
                acquisition_next_retry_at=acquisition_next_retry_at,
                acquisition_error_code=acquisition_error_code,
            )
            return
        retry_at = (
            self.clock.now_jst()
            + timedelta(
                seconds=max(
                    self.MIN_RETRY_SECONDS, self.minimum_request_interval_seconds
                )
            )
        ).isoformat()

        def mark():
            self.connection.execute(
                "UPDATE trends_acquisition_attempt SET status='interrupted',ended_at=?,"
                "error_code='process_interrupted',next_retry_at=? "
                "WHERE request_id=? AND status='started'",
                (now, retry_at, row[0]),
            )
            self.connection.execute(
                "UPDATE trends_acquisition SET next_retry_at=?,updated_at=? "
                "WHERE acquisition_id=?",
                (retry_at, now, self.acquisition_id),
            )

        self._transaction(mark)

    def _finish_attempt(
        self,
        request_id: str,
        status: str,
        error_code: str | None,
        response: object,
        next_retry_at: str | None,
        *,
        acquisition_status: str | None = None,
        acquisition_next_retry_at: str | None = None,
        acquisition_error_code: str | None = None,
    ) -> None:
        now = self.clock.now_jst().isoformat()
        failure_capture: FailureObservationCapture | None = None
        evidence_artifact = None
        capture_observation = getattr(self.port, "capture_failure_observation", None)
        if status != "response_ready" and callable(capture_observation):
            try:
                acquisition = self._load_acquisition()
                request = TrendsRequest(
                    request_id,
                    self.run_id,
                    self.settings_version,
                    str(acquisition["query_word"]),
                    range="3m",
                    deadline_at=self._request_deadline_at(),
                )
                captured = capture_observation(
                    request,
                    response if isinstance(response, Mapping) else {},
                    error_code,
                )
                if isinstance(captured, FailureObservationCapture):
                    failure_capture = captured
                    if captured.status == "available":
                        evidence_artifact = captured.artifact
                        if evidence_artifact is None:
                            failure_capture = FailureObservationCapture(
                                "invalid", error_code="failure_artifact_missing"
                            )
                else:
                    failure_capture = FailureObservationCapture(
                        "invalid", error_code="failure_capture_result_invalid"
                    )
            except Exception:
                # Diagnostic preservation must never replace the source failure.
                failure_capture = FailureObservationCapture(
                    "write_failed", error_code="artifact_capture_failed"
                )

        response_value = dict(response) if isinstance(response, Mapping) else None
        if failure_capture is not None:
            if response_value is None:
                response_value = {"error_code": error_code}
            response_value["_evidence_capture"] = {
                "status": failure_capture.status,
                **(
                    {"error_code": failure_capture.error_code}
                    if failure_capture.error_code
                    else {}
                ),
            }
        response_json = (
            _canonical_json(response_value) if response_value is not None else None
        )

        def finish():
            if evidence_artifact is not None:
                _insert_artifact(self.connection, evidence_artifact)
            updated = self.connection.execute(
                "UPDATE trends_acquisition_attempt SET status=?,ended_at=?,error_code=?,"
                "response_json=?,next_retry_at=?,evidence_ref=? "
                "WHERE request_id=? AND status='started'",
                (
                    status,
                    now,
                    error_code,
                    response_json,
                    next_retry_at,
                    evidence_artifact.artifact_ref if evidence_artifact else None,
                    request_id,
                ),
            ).rowcount
            if updated != 1:
                raise IdempotencyConflict("reserved Trends attempt is no longer active")
            if acquisition_status is None:
                self.connection.execute(
                    "UPDATE trends_acquisition SET updated_at=? WHERE acquisition_id=?",
                    (now, self.acquisition_id),
                )
            else:
                transitioned = self.connection.execute(
                    "UPDATE trends_acquisition SET status=?,next_retry_at=?,error_code=?,"
                    "snapshot_id=NULL,updated_at=? WHERE acquisition_id=?",
                    (
                        acquisition_status,
                        acquisition_next_retry_at,
                        acquisition_error_code,
                        now,
                        self.acquisition_id,
                    ),
                ).rowcount
                if transitioned != 1:
                    raise IdempotencyConflict(
                        "Trends acquisition disappeared during attempt completion"
                    )

        self._transaction(finish)

    def _set_acquisition_status(
        self,
        status: str,
        next_retry_at: str | None,
        error_code: str | None,
        snapshot_id: str | None = None,
    ) -> None:
        now = self.clock.now_jst().isoformat()

        def update():
            self.connection.execute(
                "UPDATE trends_acquisition SET status=?,next_retry_at=?,error_code=?,"
                "snapshot_id=?,updated_at=? WHERE acquisition_id=?",
                (
                    status,
                    next_retry_at,
                    error_code,
                    snapshot_id,
                    now,
                    self.acquisition_id,
                ),
            )

        self._transaction(update)

    def _saved_response(self, row) -> TrendsAcquisitionResult:
        saved = self.connection.execute(
            "SELECT request_id,response_json FROM trends_acquisition_attempt "
            "WHERE acquisition_id=? AND status='response_ready' ORDER BY attempt_no DESC LIMIT 1",
            (self.acquisition_id,),
        ).fetchone()
        if saved is None or not saved["response_json"]:
            raise TrendsContractError("saved successful Trends response is missing")
        request = TrendsRequest(
            saved["request_id"],
            self.run_id,
            self.settings_version,
            row["query_word"],
            range="3m",
            deadline_at=self._request_deadline_at(),
        )
        snapshot = parse_grouped_top_response(
            json.loads(saved["response_json"]), request
        )
        if snapshot.status != "observed":
            raise TrendsContractError(
                "saved successful Trends response no longer validates"
            )
        return TrendsAcquisitionResult(
            self.acquisition_id,
            "response_ready",
            int(row["attempts_used"]),
            snapshot,
        )

    def _saved_adopted_snapshot(self, row) -> TrendsAcquisitionResult:
        latest = self.connection.execute(
            "SELECT request_id FROM trends_acquisition_attempt WHERE acquisition_id=? "
            "AND status='response_ready' ORDER BY attempt_no DESC LIMIT 1",
            (self.acquisition_id,),
        ).fetchone()
        if latest is None:
            raise TrendsContractError(
                "adopted Trends acquisition has no successful request"
            )
        request = TrendsRequest(
            latest[0],
            self.run_id,
            self.settings_version,
            row["query_word"],
            range="3m",
            deadline_at=self._request_deadline_at(),
        )
        stored = load_grouped_snapshot(
            self.connection, row["snapshot_id"], request=request
        )
        if stored is None or stored.snapshot.status != "observed":
            raise TrendsContractError("adopted Trends snapshot cannot be loaded")
        return TrendsAcquisitionResult(
            self.acquisition_id,
            str(row["status"]),
            int(row["attempts_used"]),
            stored.snapshot,
        )

    def _transaction(self, operation) -> None:
        if self.connection.in_transaction:
            raise TrendsContractError(
                "durable Trends state cannot be nested in a caller transaction"
            )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            operation()
            self.connection.commit()
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise


def _parse_aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TrendsContractError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TrendsContractError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TrendsContractError(f"{field} must include a timezone")
    return parsed


def _safe_error_code(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return normalized[:80] or "source_error"


def _retry_after_seconds(response: object) -> int | None:
    if not isinstance(response, Mapping):
        return None
    raw = response.get("retry_after_seconds")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return min(raw, 24 * 60 * 60)


def _unavailable_group_response(request: TrendsRequest, code: str) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "snapshot_id": request.request_id,
        "run_id": request.run_id,
        "settings_version": request.settings_version,
        "source": _BROWSER_SOURCE,
        "parent_word": request.parent_word,
        "current_parent": None,
        "refresh_complete": False,
        "language": "ja",
        "geo": "JP",
        "search_type": "web",
        "range": request.range,
        "list_type": "Top",
        "ui_period_label": None,
        "status": "unavailable",
        "fetched_at": None,
        "error_code": code,
        "words": [],
    }
