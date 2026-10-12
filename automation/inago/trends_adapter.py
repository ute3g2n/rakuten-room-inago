"""Strict fixture-first contracts for observed Google Trends related Top data.

This module validates observations but does not control a browser or connect to
Google Trends. STEP 01 did not establish a permitted unattended UI connection.
"""

from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from automation.inago.contracts import IdempotencyConflict, RejectedContract
from automation.inago.repository import validate_entity


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
_SCOPED_QUERY = {
    "language": "ja",
    "geo": "JP",
    "search_type": "web",
    "range": "90d",
    "list_type": "Top",
}
_LEVEL_LIMITS = {1: 20, 2: 10}


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
        return Draft202012Validator(schema, format_checker=FormatChecker())
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
        schema = json.loads(_KEY_RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
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
                "semantic_reason": decision.reason
                if decision
                else "classification_pending",
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
