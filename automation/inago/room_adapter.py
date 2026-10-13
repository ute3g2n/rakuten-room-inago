"""Fixture-first contracts for ROOM public-search observations.

This module never opens a browser, logs in, or makes a network request. STEP 01
did not establish a permitted unattended ROOM connection, so only an explicitly
provided observation port can supply data here.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from automation.inago.clock import Clock, SystemClock
from automation.inago.contracts import RejectedContract
from automation.inago.repository import validate_entity
from automation.inago.selection import ProductIdentity, identify_offer


_ROOT = Path(__file__).resolve().parents[2]
_ROOM_SCHEMA_PATH = (
    _ROOT / "automation" / "inago" / "schemas" / "room-source.schema.json"
)
_OBS_SCHEMA_PATH = (
    _ROOT / "automation" / "inago" / "schemas" / "ai-observation.schema.json"
)
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_OBSERVABLE_FIELDS = frozenset(
    {"query", "likes_min", "current_search_id", "search_loaded", "internal_order"}
)
_RESERVATION_ERROR = "in_progress"


class RoomContractError(RejectedContract):
    """A ROOM observation cannot be used under the known search contract."""


class RoomUnknownScreen(RoomContractError):
    """The screen contract is unknown, so observation must stop safely."""


@dataclass(frozen=True, slots=True)
class RoomSearchRequest:
    attempt_id: str
    run_id: str
    query: str
    parent_refs: tuple[str, ...]
    level: str
    attempt_no: int
    retry_of: str | None
    deadline_at: str
    likes_min: int = 300
    max_posts: int = 50
    query_kind: str | None = None
    keyword_snapshot_ref: str | None = None
    selected_word_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RoomPost:
    source_key: str
    stable_room_id: str | None
    room_url: str | None
    order_index: int
    likes: int | None
    comments: int | None
    item_url: str | None
    offer_key: str | None
    identity_status: str
    image_ref: str | None
    actor_ref: str | None
    published_at: str | None
    evidence_ref: str
    observed_at: str
    availability: str


@dataclass(frozen=True, slots=True)
class RoomSearchResult:
    request: RoomSearchRequest
    status: str
    posts: tuple[RoomPost, ...]
    end_reached: bool
    fetched_at: str | None
    error_code: str | None
    loaded_posts: int | None = None
    unused_posts: int = 0
    end_reason: str | None = None
    condition_evidence_ref: str | None = None


class RoomSearchPort(Protocol):
    def search(self, request: RoomSearchRequest) -> Mapping[str, Any]:
        """Return one data-only observation for exactly this reserved attempt.

        The orchestrator invokes this port on an owned worker thread so it can
        enforce the 17:45 deadline. Implementations must not use the caller's
        SQLite connection and must support cancellation from the corresponding
        ``cancel_owned_jobs`` port. A browser adapter must establish that its
        supported browser driver is safe to use from this execution model before
        it is connected here; no browser integration is assumed by this contract.
        """


class FixtureRoomSearchPort:
    """Return fixed observations only; this port has no browser or network code."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        self._responses = {key: dict(value) for key, value in responses.items()}
        self.requests: list[RoomSearchRequest] = []

    def search(self, request: RoomSearchRequest) -> Mapping[str, Any]:
        self.requests.append(request)
        response = self._responses.get(request.attempt_id)
        if response is None:
            return _unavailable_response(request, "fixture_not_found")
        return dict(response)


class RoomSearchSession:
    """Reserve each attempt before the port call and persist validated results."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        port: RoomSearchPort,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._connection = connection
        self._port = port
        self._clock = clock or SystemClock()

    def search(
        self,
        *,
        attempt_id: str,
        run_id: str,
        query: str,
        parent_refs: Sequence[str],
        level: str,
        retry_of: str | None = None,
        query_kind: str | None = None,
        keyword_snapshot_ref: str | None = None,
        selected_word_ref: str | None = None,
    ) -> RoomSearchResult:
        request = self._reserve(
            attempt_id=attempt_id,
            run_id=run_id,
            query=query,
            parent_refs=parent_refs,
            level=level,
            retry_of=retry_of,
            query_kind=query_kind,
            keyword_snapshot_ref=keyword_snapshot_ref,
            selected_word_ref=selected_word_ref,
        )
        try:
            response = self._port.search(request)
        except Exception:
            result = RoomSearchResult(
                request=request,
                status="unavailable",
                posts=(),
                end_reached=False,
                fetched_at=None,
                error_code="source_adapter_error",
            )
        else:
            result = parse_room_response(response, request)
        self._finalize(result)
        return result

    def _reserve(
        self,
        *,
        attempt_id: str,
        run_id: str,
        query: str,
        parent_refs: Sequence[str],
        level: str,
        retry_of: str | None,
        query_kind: str | None,
        keyword_snapshot_ref: str | None,
        selected_word_ref: str | None,
    ) -> RoomSearchRequest:
        _safe_ref(attempt_id, "attempt_id")
        _safe_ref(run_id, "run_id")
        _required_text(query, "query", max_length=500)
        if level not in {"combined", "parent", "related", "theme_fallback"}:
            raise RoomContractError("search level is invalid")
        current = level in {"related", "theme_fallback"}
        if current:
            if query_kind != level:
                raise RoomContractError("current query kind must match its level")
            if level == "related":
                _safe_ref(keyword_snapshot_ref, "keyword_snapshot_ref")
                _safe_ref(selected_word_ref, "selected_word_ref")
            elif keyword_snapshot_ref is not None or selected_word_ref is not None:
                raise RoomContractError("theme fallback cannot claim a related word")
        elif (
            query_kind is not None
            or keyword_snapshot_ref is not None
            or selected_word_ref is not None
        ):
            raise RoomContractError("legacy search cannot be relabelled current")
        if retry_of is not None:
            _safe_ref(retry_of, "retry_of")
        refs = _validated_refs(parent_refs)
        if self._connection.in_transaction:
            raise RoomContractError(
                "search attempts must commit before the external port is called"
            )
        now = self._clock.now_jst()
        started_at = _aware_timestamp(now.isoformat(), "search clock")
        if started_at is None:
            raise RoomContractError("search clock must return an offset-aware time")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            run = connection.execute(
                "SELECT mode, status, attempt_used, draft_deadline,settings_version FROM run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None or run["mode"] != "daily":
                raise RoomContractError("ROOM search requires a known daily run")
            if run["status"] not in {"running", "partial"}:
                raise RoomContractError("daily run is not accepting ROOM searches")
            if current and level == "related":
                from automation.inago.trends_adapter import (
                    TrendsRequest,
                    load_grouped_snapshot,
                )

                snapshot = connection.execute(
                    "SELECT parent_word FROM keyword_snapshot WHERE snapshot_id=?",
                    (keyword_snapshot_ref,),
                ).fetchone()
                if snapshot is None:
                    raise RoomContractError("related snapshot is missing")
                saved = load_grouped_snapshot(
                    connection,
                    keyword_snapshot_ref,
                    request=TrendsRequest(
                        keyword_snapshot_ref,
                        run_id,
                        run["settings_version"],
                        snapshot[0],
                        range="3m",
                    ),
                )
                if (
                    saved is None
                    or saved.decisions is None
                    or not any(
                        item.word_id == selected_word_ref and item.raw == query
                        for item in saved.selected
                    )
                ):
                    raise RoomContractError(
                        "related query is not a saved selected expression"
                    )
            deadline_text = _aware_timestamp(run["draft_deadline"], "draft_deadline")
            if deadline_text is None:
                raise RoomContractError("draft_deadline must be offset-aware")
            deadline = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
            if now >= deadline.astimezone(now.tzinfo):
                raise RoomContractError("ROOM search deadline has passed")
            attempts = int(
                connection.execute(
                    "SELECT COUNT(*) FROM search_attempt WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
            )
            if attempts != run["attempt_used"]:
                raise RoomContractError(
                    "daily search attempt counter disagrees with persisted attempts"
                )
            if not current and attempts >= 9:
                raise RoomContractError("daily run already used 9 search attempts")
            if connection.execute(
                "SELECT 1 FROM search_attempt WHERE attempt_id = ?", (attempt_id,)
            ).fetchone():
                raise RoomContractError("attempt_id has already been reserved")

            if retry_of is None:
                attempt_no = attempts + 1
            else:
                previous = connection.execute(
                    "SELECT query_kind,keyword_snapshot_ref,selected_word_ref FROM search_attempt WHERE attempt_id=?",
                    (retry_of,),
                ).fetchone()
                if current and (
                    previous is None
                    or tuple(previous)
                    != (query_kind, keyword_snapshot_ref, selected_word_ref)
                ):
                    raise RoomContractError("retry changed its selected word identity")
                self._validate_retry(
                    retry_of,
                    run_id=run_id,
                    query=query,
                    parent_refs=refs,
                    level=level,
                )
                attempt_no = attempts + 1

            search_attempt_entity = {
                "attempt_id": attempt_id,
                "run_id": run_id,
                "query": query,
                "parent_refs": list(refs),
                "level": level,
                "attempt_no": attempt_no,
                "started_at": started_at,
                "ended_at": None,
                "source_status": "invalid",
                "source_order": "internal",
                "max_posts": 50,
                "retry_of": retry_of,
                "error_code": _RESERVATION_ERROR,
                **(
                    {
                        "query_kind": query_kind,
                        "keyword_snapshot_ref": keyword_snapshot_ref,
                        "selected_word_ref": selected_word_ref,
                    }
                    if current
                    else {}
                ),
            }
            validate_entity("search_attempt", search_attempt_entity)
            connection.execute(
                """
                INSERT INTO search_attempt
                    (attempt_id, run_id, query, parent_refs_json, level,
                     attempt_no, started_at, ended_at, source_status,
                     source_order, max_posts, retry_of, error_code,query_kind,keyword_snapshot_ref,selected_word_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'invalid', 'internal', 50, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    run_id,
                    query,
                    json.dumps(list(refs), ensure_ascii=False),
                    level,
                    attempt_no,
                    started_at,
                    retry_of,
                    _RESERVATION_ERROR,
                    query_kind if current else "legacy",
                    keyword_snapshot_ref,
                    selected_word_ref,
                ),
            )
            updated_run = connection.execute(
                "UPDATE run SET attempt_used = ? WHERE run_id = ? AND attempt_used = ?",
                (attempt_no, run_id, attempts),
            )
            if updated_run.rowcount != 1:
                raise RoomContractError(
                    "daily search attempt counter changed concurrently"
                )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

        return RoomSearchRequest(
            attempt_id=attempt_id,
            run_id=run_id,
            query=query,
            parent_refs=refs,
            level=level,
            attempt_no=attempt_no,
            retry_of=retry_of,
            deadline_at=run["draft_deadline"],
            query_kind=query_kind,
            keyword_snapshot_ref=keyword_snapshot_ref,
            selected_word_ref=selected_word_ref,
        )

    def _validate_retry(
        self,
        retry_of: str,
        *,
        run_id: str,
        query: str,
        parent_refs: tuple[str, ...],
        level: str,
    ) -> None:
        connection = self._connection
        previous = connection.execute(
            "SELECT * FROM search_attempt WHERE attempt_id = ?", (retry_of,)
        ).fetchone()
        if previous is None or previous["run_id"] != run_id:
            raise RoomContractError("retry_of must reference an attempt in this run")
        if (
            previous["query"] != query
            or tuple(json.loads(previous["parent_refs_json"])) != parent_refs
            or previous["level"] != level
        ):
            raise RoomContractError("retry input must match the original search")
        if previous["ended_at"] is None or previous["source_status"] not in {
            "partial",
            "unavailable",
            "invalid",
        }:
            raise RoomContractError("only a completed partial/failed attempt can retry")
        if connection.execute(
            "SELECT 1 FROM search_attempt WHERE retry_of = ?", (retry_of,)
        ).fetchone():
            raise RoomContractError("retry_of must be the latest attempt in its chain")

        rows = connection.execute(
            "SELECT attempt_id, retry_of FROM search_attempt WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        parents = {row["attempt_id"]: row["retry_of"] for row in rows}
        root = retry_of
        seen: set[str] = set()
        while parents.get(root) is not None:
            if root in seen:
                raise RoomContractError("retry chain contains a cycle")
            seen.add(root)
            root = parents[root]
        family = {root}
        frontier = [root]
        while frontier:
            current = frontier.pop()
            for child, parent in parents.items():
                if parent == current and child not in family:
                    family.add(child)
                    frontier.append(child)
        if len(family) >= 3:
            raise RoomContractError("search allows initial attempt plus two retries")
        root_row = connection.execute(
            "SELECT query, parent_refs_json, level FROM search_attempt WHERE attempt_id = ?",
            (root,),
        ).fetchone()
        if (
            root_row is None
            or root_row["query"] != query
            or tuple(json.loads(root_row["parent_refs_json"])) != parent_refs
            or root_row["level"] != level
        ):
            raise RoomContractError("retry chain must keep its original search input")

    def _finalize(self, result: RoomSearchResult) -> None:
        connection = self._connection
        ended_at = self._clock.now_jst().isoformat()
        validate_entity(
            "search_attempt",
            {
                "attempt_id": result.request.attempt_id,
                "run_id": result.request.run_id,
                "query": result.request.query,
                "parent_refs": list(result.request.parent_refs),
                "level": result.request.level,
                "attempt_no": result.request.attempt_no,
                "started_at": self._search_started_at(result.request.attempt_id),
                "ended_at": ended_at,
                "source_status": result.status,
                "source_order": "internal",
                "max_posts": 50,
                "retry_of": result.request.retry_of,
                "error_code": result.error_code,
                **(
                    {
                        "query_kind": result.request.query_kind,
                        "keyword_snapshot_ref": result.request.keyword_snapshot_ref,
                        "selected_word_ref": result.request.selected_word_ref,
                    }
                    if result.request.query_kind
                    else {}
                ),
            },
        )
        connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = connection.execute(
                "SELECT ended_at, source_status FROM search_attempt WHERE attempt_id = ?",
                (result.request.attempt_id,),
            ).fetchone()
            if attempt is None or attempt["ended_at"] is not None:
                raise RoomContractError("reserved attempt is no longer pending")
            for post in result.posts:
                entity = {
                    "source_key": post.source_key,
                    "stable_room_id": post.stable_room_id,
                    "room_url": post.room_url,
                    "attempt_id": result.request.attempt_id,
                    "order_index": post.order_index,
                    "likes": post.likes,
                    "comments": post.comments,
                    "item_url": post.item_url,
                    "image_ref": post.image_ref,
                    "actor_ref": post.actor_ref,
                    "published_at": post.published_at,
                    "evidence_ref": post.evidence_ref,
                    "observed_at": post.observed_at,
                    "availability": post.availability,
                }
                validate_entity("source_post", entity)
                connection.execute(
                    """
                    INSERT INTO source_post
                        (source_key, stable_room_id, room_url, attempt_id,
                         order_index, likes, comments, item_url, image_ref,
                         actor_ref, published_at, evidence_ref, observed_at,
                         availability)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        entity[field]
                        for field in (
                            "source_key",
                            "stable_room_id",
                            "room_url",
                            "attempt_id",
                            "order_index",
                            "likes",
                            "comments",
                            "item_url",
                            "image_ref",
                            "actor_ref",
                            "published_at",
                            "evidence_ref",
                            "observed_at",
                            "availability",
                        )
                    ),
                )
                # Persist the identity decision in the same transaction as the
                # source post. The offer row can be registered by a later
                # intake adapter, so the durable identity marker deliberately
                # references the source post and re-derives its key from the
                # exact saved item URL on resume.
                identity_confirmed = (
                    post.identity_status == "identified" and post.offer_key is not None
                )
                decision_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"inago-identity:{result.request.run_id}:"
                    f"{result.request.attempt_id}:{post.source_key}",
                ).hex
                decision = {
                    "decision_id": decision_id,
                    "run_id": result.request.run_id,
                    "offer_key": None,
                    "source_key": post.source_key,
                    "stage": f"daily-identity:{result.request.attempt_id}",
                    "primary_reason": (
                        "identity_confirmed"
                        if identity_confirmed
                        else "identity_unresolved"
                    ),
                    "secondary_reasons": [],
                    "representative_ref": None,
                    "representative_likes": None,
                    "rule_version": "room-identity-v1",
                    "decided_at": ended_at,
                }
                validate_entity("candidate_decision", decision)
                connection.execute(
                    """
                    INSERT INTO candidate_decision
                        (decision_id, run_id, offer_key, source_key, stage,
                         primary_reason, secondary_reasons_json, representative_ref,
                         representative_likes, rule_version, decided_at)
                    VALUES (?, ?, NULL, ?, ?, ?, '[]', NULL, NULL, ?, ?)
                    """,
                    (
                        decision_id,
                        result.request.run_id,
                        post.source_key,
                        f"daily-identity:{result.request.attempt_id}",
                        decision["primary_reason"],
                        "room-identity-v1",
                        ended_at,
                    ),
                )
            connection.execute(
                """
                UPDATE search_attempt
                   SET ended_at = ?, source_status = ?, error_code = ?,observed_posts=?,loaded_posts=?,end_reason=?,condition_evidence_ref=?
                 WHERE attempt_id = ? AND ended_at IS NULL
                """,
                (
                    ended_at,
                    result.status,
                    result.error_code,
                    len(result.posts),
                    result.loaded_posts,
                    result.end_reason,
                    result.condition_evidence_ref,
                    result.request.attempt_id,
                ),
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def _search_started_at(self, attempt_id: str) -> str:
        row = self._connection.execute(
            "SELECT started_at FROM search_attempt WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise RoomContractError("reserved search attempt is missing")
        return str(row[0])


def parse_room_response(
    response: Mapping[str, Any], request: RoomSearchRequest
) -> RoomSearchResult:
    """Reject stale search data and preserve only the current internal result order."""

    if request.level in {"related", "theme_fallback"}:
        return _parse_current_room_response(response, request)
    return _parse_legacy_room_response(response, request)


def _parse_current_room_response(
    response: Mapping[str, Any], request: RoomSearchRequest
) -> RoomSearchResult:
    """Bound rows before inspecting the unused tail; require current condition evidence."""

    if not isinstance(response, Mapping) or not isinstance(response.get("posts"), list):
        return _invalid_result(request, "response_not_object")
    if (
        request.query_kind != request.level
        or request.likes_min != 300
        or request.max_posts != 50
        or (
            request.level == "related"
            and not (request.keyword_snapshot_ref and request.selected_word_ref)
        )
        or (
            request.level == "theme_fallback"
            and (
                request.keyword_snapshot_ref is not None
                or request.selected_word_ref is not None
            )
        )
        or any(
            response.get(key) != getattr(request, key)
            for key in ("query_kind", "keyword_snapshot_ref", "selected_word_ref")
        )
    ):
        return _invalid_result(request, "search_conditions_mismatch")
    rows = response["posts"]
    loaded = response.get("loaded_posts")
    if not isinstance(loaded, int) or isinstance(loaded, bool) or loaded < len(rows):
        return _invalid_result(request, "loaded_count_invalid")
    # Stable post IDs identify repeat reads. Without one, only an exactly equal
    # row at the same search-local position is a repeat; product IDs never do.
    accepted = []
    seen = {}
    for row in rows:
        if len(accepted) == 50:
            break
        if not isinstance(row, Mapping):
            return _invalid_result(request, "post_field_invalid")
        stable = row.get("stable_room_id")
        index = row.get("internal_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 1:
            return _invalid_result(request, "post_field_invalid")
        if stable is not None and not isinstance(stable, str):
            return _invalid_result(request, "post_field_invalid")
        key = ("stable", stable) if stable is not None else ("position", index)
        if key in seen:
            if stable is None and row != seen[key]:
                return _invalid_result(request, "internal_order_invalid")
            continue
        seen[key] = row
        accepted.append(row)
    limited = dict(response, posts=accepted)
    try:
        _room_schema().validate(limited)
    except ValidationError:
        return _invalid_result(request, "response_schema_invalid")
    if response["status"] != "unavailable":
        evidence = response.get("condition_evidence_ref")
        if not isinstance(evidence, str) or not _SAFE_REF.fullmatch(evidence):
            return _invalid_result(request, "search_conditions_unverified")
        if any(row["likes"] is not None and row["likes"] < 300 for row in accepted):
            return _invalid_result(request, "search_condition_contradicted")
    # Reuse the historical row/URL validator after the new bounded contract has
    # been checked. Reaching 50 is a successful end condition, not a timeout.
    at_limit = len(accepted) == 50
    actual_end = response["end_reached"]
    if at_limit and response["status"] == "observed":
        limited["end_reached"] = True
    result = _parse_legacy_room_response(limited, request)
    return replace(
        result,
        posts=tuple(
            replace(
                post, source_key=f"{request.attempt_id}:position:{post.order_index}"
            )
            for post in result.posts
        ),
        end_reached=actual_end if result.status != "invalid" else False,
        loaded_posts=loaded,
        unused_posts=loaded - len(result.posts),
        end_reason=(
            ("limit_reached" if at_limit else "exhausted" if actual_end else "error")
            if result.status in {"observed", "partial"}
            else "invalid" if result.status == "invalid" else "error"
        ),
        condition_evidence_ref=(
            response.get("condition_evidence_ref")
            if result.status in {"observed", "partial"}
            else None
        ),
    )


def _parse_legacy_room_response(
    response: Mapping[str, Any], request: RoomSearchRequest
) -> RoomSearchResult:
    """Read historical 1.3 observations without reinterpreting their completion rules."""

    if not isinstance(response, Mapping):
        return _invalid_result(request, "response_not_object")
    try:
        _room_schema().validate(dict(response))
    except ValidationError:
        return _invalid_result(request, "response_schema_invalid")
    expected = {
        "attempt_id": request.attempt_id,
        "run_id": request.run_id,
        "query": request.query,
        "parent_refs": list(request.parent_refs),
        "level": request.level,
        "likes_min": 300,
        "max_posts": 50,
    }
    if any(response.get(field) != value for field, value in expected.items()):
        return _invalid_result(request, "search_conditions_mismatch")
    if response["status"] == "unavailable":
        return RoomSearchResult(
            request=request,
            status="unavailable",
            posts=(),
            end_reached=False,
            fetched_at=None,
            error_code=response["error_code"],
        )
    fetched_at = _aware_timestamp(response.get("fetched_at"), "fetched_at")
    if (
        response["current_search_id"] != request.attempt_id
        or response["search_loaded"] is not True
        or response["internal_order_confirmed"] is not True
        or fetched_at is None
    ):
        return _invalid_result(request, "search_conditions_mismatch")

    posts: list[RoomPost] = []
    seen_room_ids: set[str] = set()
    last_internal_index = 0
    raw_posts = response["posts"]
    over_limit = False
    for raw in raw_posts:
        internal_index = raw["internal_index"]
        if internal_index <= last_internal_index:
            return _invalid_result(request, "internal_order_invalid")
        last_internal_index = internal_index
        stable_room_id = raw["stable_room_id"]
        if stable_room_id is not None:
            try:
                _safe_ref(stable_room_id, "stable_room_id")
            except RoomContractError:
                return _invalid_result(request, "post_field_invalid")
            if stable_room_id in seen_room_ids:
                continue
        if len(posts) >= 50:
            over_limit = True
            break
        if stable_room_id is not None:
            seen_room_ids.add(stable_room_id)
        order_index = len(posts) + 1
        item_url = raw["item_url"]
        try:
            room_url = _http_url_or_none(raw["room_url"], "room_url")
            if item_url is not None:
                _http_url_or_none(item_url, "item_url")
        except RoomContractError:
            return _invalid_result(request, "post_url_invalid")
        url_ids = _product_url_ids(item_url)
        observed_shop_id = raw["observed_shop_id"]
        observed_item_id = raw["observed_item_id"]
        if url_ids is not None:
            if observed_shop_id is None:
                observed_shop_id = url_ids[0]
            if observed_item_id is None:
                observed_item_id = url_ids[1]
        identity: ProductIdentity = identify_offer(
            item_url,
            observed_shop_id=observed_shop_id,
            observed_item_id=observed_item_id,
        )
        source_key = (
            f"{request.attempt_id}:room:{stable_room_id}"
            if stable_room_id is not None
            else (
                f"{request.attempt_id}:url:{room_url}:position:{internal_index}"
                if room_url is not None
                else f"{request.attempt_id}:position:{internal_index}"
            )
        )
        observed_at = _aware_timestamp(raw["observed_at"], "post observed_at")
        if observed_at is None:
            return _invalid_result(request, "post_timestamp_invalid")
        published_at = raw["published_at"]
        if published_at is not None:
            published_at = _aware_timestamp(published_at, "published_at")
            if published_at is None:
                return _invalid_result(request, "post_timestamp_invalid")
        try:
            evidence_ref = _required_text(raw["evidence_ref"], "evidence_ref")
        except RoomContractError:
            return _invalid_result(request, "post_field_invalid")
        posts.append(
            RoomPost(
                source_key=source_key,
                stable_room_id=stable_room_id,
                room_url=room_url,
                order_index=order_index,
                likes=raw["likes"],
                comments=raw["comments"],
                item_url=item_url,
                offer_key=identity.offer_key,
                identity_status=identity.status,
                image_ref=raw["image_ref"],
                actor_ref=raw["actor_ref"],
                published_at=published_at,
                evidence_ref=evidence_ref,
                observed_at=observed_at,
                availability=raw["availability"],
            )
        )

    incomplete = (
        response["status"] == "partial" or not response["end_reached"] or over_limit
    )
    status = "partial" if incomplete else "observed"
    error_code = response["error_code"]
    if error_code is None and over_limit:
        error_code = "max_posts_reached"
    elif error_code is None and not response["end_reached"]:
        error_code = "search_not_terminal"
    elif error_code is None and response["status"] == "partial":
        error_code = "source_partial"
    return RoomSearchResult(
        request=request,
        status=status,
        posts=tuple(posts),
        end_reached=response["end_reached"],
        fetched_at=fetched_at,
        error_code=error_code,
    )


def validate_room_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    snapshot_id: str,
    known_screen: bool,
) -> tuple[dict[str, Any], ...]:
    """Validate only known-screen, allowlisted OBS fields and snapshot refs."""

    if not known_screen:
        raise RoomUnknownScreen("unknown ROOM screen; stop observation")
    _safe_ref(snapshot_id, "snapshot_id")
    try:
        schema = json.loads(_OBS_SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RoomContractError("OBS response schema is unavailable") from exc
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise RoomContractError("OBS observations must be an array")
    validated: list[dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise RoomContractError("OBS observation must be an object")
        value = dict(observation)
        try:
            validator.validate(value)
        except ValidationError as exc:
            raise RoomContractError("OBS observation schema is invalid") from exc
        if value["field"] not in _OBSERVABLE_FIELDS:
            raise RoomContractError("OBS field is not allowlisted for ROOM search")
        if value["ref"] != snapshot_id:
            raise RoomContractError("OBS ref must name the reviewed snapshot")
        if value["availability"] != "observed" and value["value"] is not None:
            raise RoomContractError("unavailable OBS fields must retain a null value")
        validated.append(value)
    return tuple(validated)


def _unavailable_response(
    request: RoomSearchRequest, error_code: str
) -> dict[str, Any]:
    return {
        "attempt_id": request.attempt_id,
        "run_id": request.run_id,
        "query": request.query,
        "parent_refs": list(request.parent_refs),
        "level": request.level,
        "likes_min": 300,
        "max_posts": 50,
        "current_search_id": None,
        "search_loaded": False,
        "internal_order_confirmed": False,
        "status": "unavailable",
        "fetched_at": None,
        "end_reached": False,
        "error_code": error_code,
        "posts": [],
    }


def _invalid_result(request: RoomSearchRequest, error_code: str) -> RoomSearchResult:
    return RoomSearchResult(
        request=request,
        status="invalid",
        posts=(),
        end_reached=False,
        fetched_at=None,
        error_code=error_code,
    )


def _room_schema() -> Draft202012Validator:
    try:
        schema = json.loads(_ROOM_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker())
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RoomContractError("ROOM source response schema is unavailable") from exc


def _required_text(value: object, field: str, *, max_length: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > max_length
        or "\x00" in value
    ):
        raise RoomContractError(f"{field} must be bounded non-empty text")
    return value


def _safe_ref(value: object, field: str) -> str:
    text = _required_text(value, field, max_length=128)
    if not _SAFE_REF.fullmatch(text):
        raise RoomContractError(f"{field} is not a safe persisted reference")
    return text


def _validated_refs(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise RoomContractError("parent_refs must be an array")
    refs = tuple(_safe_ref(value, "parent_ref") for value in values)
    if not refs or len(refs) != len(set(refs)):
        raise RoomContractError("parent_refs must be non-empty and unique")
    return refs


def _aware_timestamp(value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return value


def _http_url_or_none(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RoomContractError(f"{field} must be an HTTP(S) URL or null")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RoomContractError(f"{field} is malformed") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise RoomContractError(f"{field} must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise RoomContractError(f"{field} cannot contain credentials")
    return value


def _product_url_ids(value: str | None) -> tuple[str, str] | None:
    """Read only the shop/item path from an observed Rakuten item URL."""

    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or (parsed.hostname or "").casefold() != "item.rakuten.co.jp"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        len(parts) < 2
        or not _SAFE_REF.fullmatch(parts[0])
        or not _SAFE_REF.fullmatch(parts[1])
    ):
        return None
    return parts[0], parts[1]
