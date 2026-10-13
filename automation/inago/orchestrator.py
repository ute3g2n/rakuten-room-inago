"""Finite, resumable daily control flow for the Inago runtime."""

from __future__ import annotations

import json
import queue
import re
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass, replace
from contextlib import ExitStack
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from automation.inago.clock import Clock, SystemClock
from automation.inago.copy_service import (
    CopyGenerationSpec,
    CopyRunner,
    generate_and_commit_copy,
)
from automation.inago.contracts import AIJobRequest, AIJobResult
from automation.inago.metrics import (
    MetricsCollectionResult,
    MetricsDeadlineExceeded,
    commit_metrics_result,
)
from automation.inago.report_apply import apply_pending_reports
from automation.inago.repository import validate_entity
from automation.inago.room_adapter import (
    RoomSearchPort,
    RoomSearchSession,
)
from automation.inago.run_lock import RunLock, RunLockBusy
from automation.inago.trends_adapter import TrendsRequest, load_grouped_snapshot
from automation.inago.selection import (
    SourcePost,
    evaluate_sources,
    identify_offer_from_saved_url,
    load_offer_history,
    load_theme_selection_events,
    load_theme_candidates,
    record_theme_result_word_refs,
    select_theme_batch,
)


JST = ZoneInfo("Asia/Tokyo")
DAILY_START = time(13, 0)
DRAFT_CUTOFF = time(17, 45)
METRICS_CUTOFF = time(18, 0)
DAILY_TARGET = 15
PER_THEME_TARGET = 5
MAX_POSTS_PER_SEARCH = 50
MAX_COPY_ATTEMPTS = 3
_DAILY_CHECKPOINT_KEY = "daily_orchestration"
_DAILY_CHECKPOINT_VERSION = 2
_DAILY_RULES_VERSION = "requirements-1.4"
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


class DailyOrchestrationError(RuntimeError):
    """A daily phase cannot continue without violating its persisted contract."""


class _CancelRunWork:
    """Cancel one owned job at most once and retain explicit cancellation evidence."""

    def __init__(self, run_id: str, callback: Callable[[str], bool]) -> None:
        self.run_id = run_id
        self.callback = callback
        self._lock = threading.Lock()
        self._called = False
        self.confirmed = False
        self.error_code: str | None = None

    def __call__(self, run_id: str) -> bool:
        if run_id != self.run_id:
            self.error_code = "cancel_run_id_mismatch"
            return False
        with self._lock:
            if not self._called:
                self._called = True
                try:
                    self.confirmed = self.callback(run_id) is True
                except Exception as exc:
                    self.error_code = type(exc).__name__
                else:
                    if not self.confirmed:
                        self.error_code = "cancel_not_confirmed"
            return self.confirmed


def _call_before_deadline(
    callback: Callable[[], Any],
    *,
    deadline_monotonic: float,
    clock: Clock,
    cancel: Callable[[], Any],
) -> tuple[bool, Any]:
    """Run a data-only external call off-thread and discard any late response."""

    if clock.deadline_remaining(deadline_monotonic) <= 0:
        cancel()
        return False, None
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            results.put((True, callback()))
        except BaseException as exc:
            results.put((False, exc))

    worker = threading.Thread(target=invoke, name="inago-owned-job", daemon=True)
    worker.start()
    while True:
        remaining = clock.deadline_remaining(deadline_monotonic)
        if remaining <= 0:
            cancel()
            return False, None
        try:
            succeeded, value = results.get(timeout=min(0.05, remaining))
        except queue.Empty:
            continue
        if clock.deadline_remaining(deadline_monotonic) <= 0:
            cancel()
            return False, None
        if not succeeded:
            raise value
        return True, value


class _DeadlineRoomSearchPort:
    """Enforce the run's 17:45 cutoff around one ROOM observation request."""

    def __init__(
        self,
        port: RoomSearchPort,
        *,
        deadline_monotonic: float,
        clock: Clock,
        cancel: Callable[[], Any],
    ) -> None:
        self._port = port
        self._deadline = deadline_monotonic
        self._clock = clock
        self._cancel = cancel

    def search(self, request):
        completed, response = _call_before_deadline(
            lambda: self._port.search(request),
            deadline_monotonic=self._deadline,
            clock=self._clock,
            cancel=self._cancel,
        )
        if completed:
            return response
        return {
            "attempt_id": request.attempt_id,
            "run_id": request.run_id,
            "query": request.query,
            "parent_refs": list(request.parent_refs),
            "level": request.level,
            "likes_min": request.likes_min,
            "max_posts": request.max_posts,
            **(
                {
                    "query_kind": request.query_kind,
                    "keyword_snapshot_ref": request.keyword_snapshot_ref,
                    "selected_word_ref": request.selected_word_ref,
                    "loaded_posts": 0,
                    "condition_evidence_ref": None,
                }
                if request.query_kind
                else {}
            ),
            "current_search_id": None,
            "search_loaded": False,
            "internal_order_confirmed": False,
            "status": "unavailable",
            "fetched_at": None,
            "end_reached": False,
            "error_code": "daily_deadline_exceeded",
            "posts": [],
        }


class _DeadlineCopyRunner:
    """Discard an AI-O result that arrives at or after its request deadline."""

    def __init__(
        self,
        runner: CopyRunner,
        *,
        clock: Clock,
        cancel: Callable[[], Any],
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._cancel = cancel

    def run(self, request: AIJobRequest) -> AIJobResult:
        completed, result = _call_before_deadline(
            lambda: self._runner.run(request),
            deadline_monotonic=request.deadline_monotonic,
            clock=self._clock,
            cancel=self._cancel,
        )
        if completed:
            return result
        return AIJobResult(
            job_id=request.job_id,
            run_id=request.run_id,
            attempt=request.attempt,
            task_id=request.task_id,
            agent_id="AI-O",
            skill_name="rakuten-room-inago",
            target_date=request.target_date,
            settings_version=request.settings_version,
            input_refs=request.input_refs,
            evidence_refs=request.evidence_refs,
            input_hash="deadline-discarded",
            deadline_at=request.deadline_at,
            result_status="timed_out",
            adoption_status="pending",
            payload=None,
            error={"code": "daily_deadline_exceeded"},
            artifact=None,
            started_at=None,
            finished_at=_now(self._clock).isoformat(),
        )


@dataclass(frozen=True, slots=True)
class DailySearchPlan:
    """One unmodified observed expression or the original theme fallback."""

    task_id: str
    theme_id: str
    query: str
    parent_refs: tuple[str, ...]
    level: Literal["related", "theme_fallback"]
    kind: Literal["related", "theme_fallback"]
    keyword_snapshot_ref: str | None = None
    selected_word_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DailyThemePlan:
    """The saved selected expression queue followed by one theme-only search."""

    theme_id: str
    keyword_refs: tuple[str, ...]
    related: tuple[DailySearchPlan, ...]
    fallback: DailySearchPlan
    snapshot_ref: str
    rules_version: str = "requirements-1.4"


class ThemePlanner(Protocol):
    def plan_theme(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        settings_version: str,
        event_kind: str,
        ordinal: int,
        theme_id: str,
        seed: str,
    ) -> DailyThemePlan:
        """Load or persist this event's observed Trends/KEY snapshot plan."""


class CopySpecFactory(Protocol):
    def __call__(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        offer_key: str,
        theme_id: str,
        target_date: str,
        settings_version: str,
        deadline_at: str,
        deadline_monotonic: float,
    ) -> CopyGenerationSpec | None:
        """Build a copy request from the saved offer and only its available facts."""


@dataclass(frozen=True, slots=True)
class OfferIntakeRequest:
    """Observed product identity and evidence handed to the offer repository port."""

    run_id: str
    settings_version: str
    theme_id: str
    offer_key: str
    source_key: str
    item_url: str
    image_ref: str | None
    evidence_ref: str
    observed_at: str


class OfferIntakePort(Protocol):
    def persist_offer(
        self, connection: sqlite3.Connection, request: OfferIntakeRequest
    ) -> None:
        """Persist this identified offer from captured facts; unknowns stay unknown."""


class MetricsPort(Protocol):
    def collect(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        deadline_at: str,
    ) -> str | MetricsCollectionResult:
        """Return status or data-only results using a separate read-only connection."""


@dataclass(frozen=True, slots=True)
class DailyPorts:
    """Explicit runtime ports; tests supply local fixture implementations.

    Both cancellation callbacks must return True only after all work owned by
    that run has stopped; None, False, or an exception means cancellation could
    not be confirmed and is persisted as an operational error.
    """

    current_settings_version: Callable[[sqlite3.Connection], str]
    theme_planner: ThemePlanner
    room_port: RoomSearchPort
    copy_spec_factory: CopySpecFactory
    copy_runner: CopyRunner
    metrics_port: MetricsPort
    cancel_owned_jobs: Callable[[str], bool]
    cancel_metrics_job: Callable[[str], bool]
    offer_intake: OfferIntakePort | None = None
    fixture_only: bool = False


@dataclass(frozen=True, slots=True)
class DailyRunOutcome:
    status: str
    run_id: str | None
    target_date: str
    committed_count: int
    attempt_used: int
    metrics_status: str
    resumed: bool = False
    reason: str | None = None


def _now(clock: Clock) -> datetime:
    value = clock.now_jst()
    if value.tzinfo is None or value.utcoffset() is None:
        raise DailyOrchestrationError("daily clock must return an aware datetime")
    return value.astimezone(JST)


def _at(target: date, wall_time: time) -> datetime:
    return datetime.combine(target, wall_time, tzinfo=JST)


def _strict_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DailyOrchestrationError("run_checkpoint_invalid_json") from exc
    if not isinstance(parsed, dict):
        raise DailyOrchestrationError("run_checkpoint_not_object")
    return parsed


def _daily_state(checkpoint: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _strict_json_object(checkpoint)
    state = root.get(_DAILY_CHECKPOINT_KEY)
    if state is None:
        state = {
            "schema_version": _DAILY_CHECKPOINT_VERSION,
            "rules_version": _DAILY_RULES_VERSION,
            "initial_plan_index": 0,
            "offer_theme_ids": {},
            "theme_targets": {},
            "used_queries": [],
            "source_stopped": False,
            "phase": "initial",
            "initial_theme_ids": [],
            "plans": {},
            "tasks": [],
            "task_cursor": 0,
            "additional_events": 0,
            "cancelled_at_draft_cutoff": False,
            "source_failures": 0,
            "error_count": 0,
            "processed_attempts": [],
            "handled_offer_keys": [],
            "errors": [],
        }
        root[_DAILY_CHECKPOINT_KEY] = state
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != _DAILY_CHECKPOINT_VERSION
        or state.get("rules_version") != _DAILY_RULES_VERSION
    ):
        raise DailyOrchestrationError("daily_checkpoint_version_invalid")
    if state.get("phase") not in {
        "initial",
        "additional",
        "finished",
    }:
        raise DailyOrchestrationError("daily_checkpoint_phase_invalid")
    if not isinstance(state.get("plans"), dict) or not isinstance(
        state.get("tasks"), list
    ):
        raise DailyOrchestrationError("daily_checkpoint_work_invalid")
    cursor = state.get("task_cursor")
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise DailyOrchestrationError("daily_checkpoint_cursor_invalid")
    index = state.get("initial_plan_index")
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index > len(state.get("initial_theme_ids", []))
    ):
        raise DailyOrchestrationError("daily_checkpoint_theme_cursor_invalid")
    if not isinstance(state.get("offer_theme_ids"), dict) or not isinstance(
        state.get("used_queries"), list
    ):
        raise DailyOrchestrationError("daily_checkpoint_assignment_invalid")
    return root, state


def _save_state(
    connection: sqlite3.Connection, run_id: str, state: Mapping[str, Any]
) -> None:
    if connection.in_transaction:
        raise DailyOrchestrationError("checkpoint_save_requires_no_open_transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise DailyOrchestrationError("daily_run_missing_during_checkpoint")
        root, _ = _daily_state(row["checkpoint"])
        root[_DAILY_CHECKPOINT_KEY] = dict(state)
        encoded = json.dumps(
            root,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        connection.execute(
            "UPDATE run SET checkpoint=? WHERE run_id=?", (encoded, run_id)
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _append_error(
    state: dict[str, Any], phase: str, code: str, detail: str | None = None
) -> None:
    errors = state.setdefault("errors", [])
    if not isinstance(errors, list):
        raise DailyOrchestrationError("daily_checkpoint_errors_invalid")
    item = {"phase": phase, "code": code}
    if detail:
        item["detail"] = detail[:300]
    errors.append(item)


def _run_by_date(connection: sqlite3.Connection, target_date: str):
    return connection.execute(
        "SELECT * FROM run WHERE target_date=? AND mode='daily'", (target_date,)
    ).fetchone()


def _create_run(
    connection: sqlite3.Connection,
    *,
    target_date: str,
    now: datetime,
    ports: DailyPorts,
    run_id_factory: Callable[[date], str],
    seed_factory: Callable[[], str],
    missed: bool,
):
    existing = _run_by_date(connection, target_date)
    if existing is not None:
        return existing
    settings_version = ports.current_settings_version(connection)
    if not isinstance(settings_version, str) or not settings_version.strip():
        raise DailyOrchestrationError("active_settings_version_missing")
    if (
        connection.execute(
            "SELECT 1 FROM settings_version WHERE version=?", (settings_version,)
        ).fetchone()
        is None
    ):
        raise DailyOrchestrationError("active_settings_version_unregistered")
    run_date = date.fromisoformat(target_date)
    run_id = run_id_factory(run_date)
    seed = seed_factory()
    if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
        raise DailyOrchestrationError("daily_run_id_invalid")
    if not isinstance(seed, str) or not seed.strip() or seed != seed.strip():
        raise DailyOrchestrationError("daily_seed_invalid")
    started_at = now.isoformat()
    status = "missed" if missed else "running"
    finished_at = started_at if missed else None
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = _run_by_date(connection, target_date)
        if existing is not None:
            connection.rollback()
            return existing
        connection.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, checkpoint, metrics_status, finished_at)
            VALUES (?, ?, 'daily', ?, ?, ?, ?, ?, ?, 0, 0, NULL, 'not_started', ?)
            """,
            (
                run_id,
                target_date,
                status,
                settings_version,
                seed,
                started_at,
                _at(run_date, DRAFT_CUTOFF).isoformat(),
                _at(run_date, METRICS_CUTOFF).isoformat(),
                finished_at,
            ),
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    return connection.execute("SELECT * FROM run WHERE run_id=?", (run_id,)).fetchone()


def _set_running_for_resume(connection: sqlite3.Connection, run_id: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT status FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise DailyOrchestrationError("daily_run_missing_on_resume")
        if row["status"] not in {"running", "partial", "failed", "shortfall"}:
            raise DailyOrchestrationError("daily_run_not_resumable")
        connection.execute(
            "UPDATE run SET status='running', finished_at=NULL WHERE run_id=?",
            (run_id,),
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _status_counts(connection: sqlite3.Connection, run_id: str) -> tuple[int, int]:
    row = connection.execute(
        "SELECT committed_count, attempt_used FROM run WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise DailyOrchestrationError("daily_run_missing")
    return int(row["committed_count"]), int(row["attempt_used"])


def _finish(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    clock: Clock,
    state: dict[str, Any],
    status: str,
    reason: str | None,
    preserve_checkpoint: bool = False,
) -> DailyRunOutcome:
    if status not in {"complete", "partial", "failed", "shortfall", "missed"}:
        raise DailyOrchestrationError("daily final status is unsupported")
    state["last_reason"] = reason
    if not preserve_checkpoint:
        state["terminal_status"] = status
        if status in {"complete", "failed", "shortfall", "missed"}:
            state["phase"] = "finished"
    _save_state(connection, run_id, state)
    finished_at = _now(clock).isoformat()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE run SET status=?, finished_at=? WHERE run_id=?",
            (status, finished_at, run_id),
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    row = connection.execute("SELECT * FROM run WHERE run_id=?", (run_id,)).fetchone()
    return DailyRunOutcome(
        status=status,
        run_id=run_id,
        target_date=str(row["target_date"]),
        committed_count=int(row["committed_count"]),
        attempt_used=int(row["attempt_used"]),
        metrics_status=str(row["metrics_status"]),
        reason=reason,
    )


def _outcome_from_run(row, *, resumed: bool = False, reason: str | None = None):
    return DailyRunOutcome(
        status=str(row["status"]),
        run_id=str(row["run_id"]),
        target_date=str(row["target_date"]),
        committed_count=int(row["committed_count"]),
        attempt_used=int(row["attempt_used"]),
        metrics_status=str(row["metrics_status"]),
        resumed=resumed,
        reason=reason,
    )


def _validate_plan(plan: DailyThemePlan, theme_id: str) -> None:
    if not isinstance(plan, DailyThemePlan) or plan.theme_id != theme_id:
        raise DailyOrchestrationError("theme_plan_identity_mismatch")
    if plan.rules_version != _DAILY_RULES_VERSION:
        raise DailyOrchestrationError("theme_plan_rules_version_invalid")
    if (
        not isinstance(plan.keyword_refs, tuple)
        or len(plan.keyword_refs) > 5
        or len(set(plan.keyword_refs)) != len(plan.keyword_refs)
    ):
        raise DailyOrchestrationError("theme_plan_keyword_refs_invalid")
    if not isinstance(plan.related, tuple) or len(plan.related) != len(
        plan.keyword_refs
    ):
        raise DailyOrchestrationError("theme_related_queue_invalid")
    if not isinstance(plan.snapshot_ref, str) or not plan.snapshot_ref:
        raise DailyOrchestrationError("theme_snapshot_ref_missing")
    seen = set()
    for search in (*plan.related, plan.fallback):
        if not isinstance(search, DailySearchPlan) or search.theme_id != theme_id:
            raise DailyOrchestrationError("theme_search_identity_mismatch")
        if (
            not _SAFE_TASK_ID.fullmatch(search.task_id)
            or not isinstance(search.query, str)
            or not search.query.strip()
        ):
            raise DailyOrchestrationError("theme_search_input_invalid")
        if (
            search.level != search.kind
            or search.kind not in {"related", "theme_fallback"}
            or search.parent_refs != (theme_id,)
        ):
            raise DailyOrchestrationError("theme_search_kind_or_refs_invalid")
        if search.kind == "related":
            if (
                search.keyword_snapshot_ref != plan.snapshot_ref
                or search.selected_word_ref not in plan.keyword_refs
            ):
                raise DailyOrchestrationError("theme_search_word_ref_invalid")
        elif (
            search.keyword_snapshot_ref is not None
            or search.selected_word_ref is not None
        ):
            raise DailyOrchestrationError("theme_fallback_contains_related_ref")
        if search.task_id in seen:
            raise DailyOrchestrationError("theme_search_task_duplicated")
        seen.add(search.task_id)
    if plan.fallback.kind != "theme_fallback" or any(
        item.kind != "related" for item in plan.related
    ):
        raise DailyOrchestrationError("theme_search_queue_order_invalid")
    if tuple(item.selected_word_ref for item in plan.related) != plan.keyword_refs:
        raise DailyOrchestrationError("theme_selected_order_changed")


def _plan_to_dict(plan: DailyThemePlan) -> dict[str, Any]:
    def search_to_dict(search):
        return {
            "task_id": search.task_id,
            "theme_id": search.theme_id,
            "query": search.query,
            "parent_refs": list(search.parent_refs),
            "level": search.level,
            "kind": search.kind,
            "keyword_snapshot_ref": search.keyword_snapshot_ref,
            "selected_word_ref": search.selected_word_ref,
        }

    return {
        "theme_id": plan.theme_id,
        "keyword_refs": list(plan.keyword_refs),
        "related": [search_to_dict(item) for item in plan.related],
        "fallback": search_to_dict(plan.fallback),
        "snapshot_ref": plan.snapshot_ref,
        "rules_version": plan.rules_version,
    }


def _plan_from_dict(value: object) -> DailyThemePlan:
    if not isinstance(value, dict) or set(value) != {
        "theme_id",
        "keyword_refs",
        "related",
        "fallback",
        "snapshot_ref",
        "rules_version",
    }:
        raise DailyOrchestrationError("saved_theme_plan_fields_invalid")

    def search_from_dict(item):
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "task_id",
                "theme_id",
                "query",
                "parent_refs",
                "level",
                "kind",
                "keyword_snapshot_ref",
                "selected_word_ref",
            }
            or not isinstance(item["parent_refs"], list)
        ):
            raise DailyOrchestrationError("saved_search_plan_invalid")
        return DailySearchPlan(**{**item, "parent_refs": tuple(item["parent_refs"])})

    if not isinstance(value["keyword_refs"], list) or not isinstance(
        value["related"], list
    ):
        raise DailyOrchestrationError("saved_theme_plan_lists_invalid")
    plan = DailyThemePlan(
        theme_id=value["theme_id"],
        keyword_refs=tuple(value["keyword_refs"]),
        related=tuple(search_from_dict(item) for item in value["related"]),
        fallback=search_from_dict(value["fallback"]),
        snapshot_ref=value["snapshot_ref"],
        rules_version=value["rules_version"],
    )
    _validate_plan(plan, plan.theme_id)
    return plan


def _task(search: DailySearchPlan, *, phase: str, ordinal: int) -> dict[str, Any]:
    return {
        "task_number": ordinal,
        "phase": phase,
        "event_ordinal": ordinal,
        "theme_id": search.theme_id,
        "task_id": search.task_id,
        "query": search.query,
        "parent_refs": list(search.parent_refs),
        "level": search.level,
        "kind": search.kind,
        "query_kind": search.kind,
        "keyword_snapshot_ref": search.keyword_snapshot_ref,
        "selected_word_ref": search.selected_word_ref,
    }


def _get_plan(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    settings_version: str,
    seed: str,
    event_kind: str,
    ordinal: int,
    theme_id: str,
    state: dict[str, Any],
    planner: ThemePlanner,
) -> DailyThemePlan:
    key = f"{event_kind}:{ordinal:02d}"
    raw = state["plans"].get(key)
    if raw is not None:
        plan = _plan_from_dict(raw)
        _validate_plan(plan, theme_id)
        _validate_plan_snapshot(
            connection,
            plan,
            run_id=run_id,
            settings_version=settings_version,
            seed=seed,
        )
        return plan
    # The planner must return data tied to its persisted Trends/KEY snapshot;
    # live collection is owned by the later external-integration step.
    plan = planner.plan_theme(
        connection,
        run_id=run_id,
        settings_version=settings_version,
        event_kind=event_kind,
        ordinal=ordinal,
        theme_id=theme_id,
        seed=seed,
    )
    _validate_plan(plan, theme_id)
    _validate_plan_snapshot(
        connection, plan, run_id=run_id, settings_version=settings_version, seed=seed
    )
    event_id = f"theme-selection:{run_id}:{event_kind}:{ordinal:02d}"
    events = load_theme_selection_events(connection, run_id, event_kind=event_kind)
    event = next((item for item in events if item.count_event_id == event_id), None)
    if event is None or event.theme_id != theme_id:
        raise DailyOrchestrationError("theme_selection_event_missing_or_mismatched")
    if event.result_word_refs:
        if event.result_word_refs != plan.keyword_refs:
            raise DailyOrchestrationError("persisted_theme_refs_changed")
    else:
        record_theme_result_word_refs(
            connection,
            run_id,
            event_kind=event_kind,
            ordinal=ordinal,
            result_word_refs=plan.keyword_refs,
        )
    state["plans"][key] = _plan_to_dict(plan)
    _save_state(connection, run_id, state)
    return plan


def _validate_plan_snapshot(
    connection, plan, *, run_id, settings_version, seed
) -> None:
    request = TrendsRequest(
        plan.snapshot_ref, run_id, settings_version, plan.fallback.query, range="3m"
    )
    saved = load_grouped_snapshot(connection, plan.snapshot_ref, request=request)
    if (
        saved is None
        or saved.decisions is None
        or saved.snapshot.status != "observed"
        or saved.seed != seed
    ):
        raise DailyOrchestrationError("theme_plan_snapshot_not_adopted")
    if tuple(item.word_id for item in saved.selected) != plan.keyword_refs or tuple(
        item.raw for item in saved.selected
    ) != tuple(item.query for item in plan.related):
        raise DailyOrchestrationError("theme_plan_raw_or_order_changed")


def _make_tasks(
    plans: Sequence[DailyThemePlan], *, phase: str, first_number: int
) -> list[dict[str, Any]]:
    searches = [search for plan in plans for search in (*plan.related, plan.fallback)]
    return [
        _task(search, phase=phase, ordinal=first_number + offset)
        for offset, search in enumerate(searches)
    ]


def _ensure_initial_work(
    connection, *, run_id, settings_version, seed, state, ports, clock
) -> None:
    if state.get("initial_ready") is True:
        return
    events = select_theme_batch(
        connection, run_id, event_kind="initial", selected_at=_now(clock).isoformat()
    )
    theme_ids = [item.theme_id for item in events]
    saved = state.get("initial_theme_ids", [])
    if saved and saved != theme_ids:
        raise DailyOrchestrationError("initial_theme_selection_changed")
    state["initial_theme_ids"] = theme_ids
    state["initial_ready"] = True
    state["phase"] = "initial"
    _save_state(connection, run_id, state)


def _append_initial_theme(
    connection, *, run_id, settings_version, seed, state, ports, clock
) -> bool:
    index = int(state["initial_plan_index"])
    if index >= len(state["initial_theme_ids"]):
        return False
    theme_id = state["initial_theme_ids"][index]
    plan = _get_plan(
        connection,
        run_id=run_id,
        settings_version=settings_version,
        seed=seed,
        event_kind="initial",
        ordinal=index + 1,
        theme_id=theme_id,
        state=state,
        planner=ports.theme_planner,
    )
    state["tasks"].extend(
        _make_tasks((plan,), phase="initial", first_number=len(state["tasks"]) + 1)
    )
    state["theme_targets"][theme_id] = PER_THEME_TARGET
    state["initial_plan_index"] = index + 1
    _save_state(connection, run_id, state)
    return True


def _add_additional_theme(
    connection, *, run_id, settings_version, seed, state, ports, clock
) -> bool:
    ordinal = int(state.get("additional_events", 0)) + 1
    event_id = f"theme-selection:{run_id}:additional:{ordinal:02d}"
    events = load_theme_selection_events(connection, run_id)
    event = next((item for item in events if item.count_event_id == event_id), None)
    if event is None:
        used = frozenset(item.theme_id for item in events)
        remaining = [
            item
            for item in load_theme_candidates(connection, run_id)
            if item.state == "active" and item.theme_id not in used
        ]
        if not remaining:
            state["themes_exhausted"] = True
            _save_state(connection, run_id, state)
            return False
        event = select_theme_batch(
            connection,
            run_id,
            event_kind="additional",
            selected_at=_now(clock).isoformat(),
            first_ordinal=ordinal,
            exclude_theme_ids=used,
        )[0]
    plan = _get_plan(
        connection,
        run_id=run_id,
        settings_version=settings_version,
        seed=seed,
        event_kind="additional",
        ordinal=ordinal,
        theme_id=event.theme_id,
        state=state,
        planner=ports.theme_planner,
    )
    state["tasks"].extend(
        _make_tasks((plan,), phase="additional", first_number=len(state["tasks"]) + 1)
    )
    state["theme_targets"][event.theme_id] = min(
        PER_THEME_TARGET, DAILY_TARGET - _draft_count(connection, run_id)
    )
    state["additional_events"] = ordinal
    state["phase"] = "additional"
    _save_state(connection, run_id, state)
    return True


def _task_attempt_id(run_id: str, task_number: int, local_attempt: int) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"inago-daily:{run_id}:{task_number}:{local_attempt}",
    ).hex


def _attempt_row(connection: sqlite3.Connection, attempt_id: str):
    return connection.execute(
        "SELECT * FROM search_attempt WHERE attempt_id=?", (attempt_id,)
    ).fetchone()


def _finalize_interrupted_search(
    connection: sqlite3.Connection, attempt_id: str, clock: Clock
) -> None:
    now = _now(clock).isoformat()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = _attempt_row(connection, attempt_id)
        if row is not None and row["ended_at"] is None:
            connection.execute(
                "UPDATE search_attempt SET ended_at=?, source_status='unavailable', "
                "error_code='process_interrupted' WHERE attempt_id=? AND ended_at IS NULL",
                (now, attempt_id),
            )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _persisted_source_identities(
    connection: sqlite3.Connection, attempt_id: str
) -> dict[str, str | None]:
    rows = connection.execute(
        "SELECT decision.source_key, decision.primary_reason, source.item_url "
        "FROM candidate_decision AS decision "
        "JOIN source_post AS source USING (source_key) "
        "WHERE decision.stage=? AND decision.rule_version='room-identity-v1'",
        (f"daily-identity:{attempt_id}",),
    ).fetchall()
    identities: dict[str, str | None] = {}
    for row in rows:
        source_key = str(row["source_key"])
        if row["primary_reason"] != "identity_confirmed":
            identities[source_key] = None
            continue
        identity = identify_offer_from_saved_url(row["item_url"])
        if identity.status != "identified" or identity.offer_key is None:
            raise DailyOrchestrationError("saved_source_identity_cannot_be_rederived")
        identities[source_key] = identity.offer_key
    return identities


def _source_posts_for_attempt(
    connection: sqlite3.Connection,
    attempt_id: str,
    identities: Mapping[str, str | None],
) -> tuple[SourcePost, ...]:
    rows = connection.execute(
        "SELECT source_key, likes, image_ref, item_url, order_index "
        "FROM source_post WHERE attempt_id=? ORDER BY order_index",
        (attempt_id,),
    ).fetchall()
    result: list[SourcePost] = []
    for row in rows:
        item_url = row["item_url"]
        result.append(
            SourcePost(
                source_key=str(row["source_key"]),
                offer_key=identities.get(str(row["source_key"])),
                likes=row["likes"],
                image_ref=row["image_ref"],
                is_product=item_url is not None,
                order_index=int(row["order_index"]),
            )
        )
    return tuple(result)


def _persist_candidate_decisions(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    attempt_id: str,
    evaluation: Any,
    clock: Clock,
) -> None:
    representative_for_offer = {
        item.offer_key: item for item in evaluation.representatives
    }
    decided_at = _now(clock).isoformat()
    connection.execute("BEGIN IMMEDIATE")
    try:
        for decision in evaluation.decisions:
            representative = representative_for_offer.get(decision.offer_key)
            offer_row = (
                connection.execute(
                    "SELECT 1 FROM offer WHERE offer_key=?", (decision.offer_key,)
                ).fetchone()
                if decision.offer_key is not None
                else None
            )
            persisted_offer_key = decision.offer_key if offer_row is not None else None
            decision_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"inago-decision:{run_id}:{attempt_id}:{decision.source_key}",
            ).hex
            entity = {
                "decision_id": decision_id,
                "run_id": run_id,
                "offer_key": persisted_offer_key,
                "source_key": decision.source_key,
                "stage": f"daily-search:{attempt_id}",
                "primary_reason": decision.primary_reason,
                "secondary_reasons": list(decision.secondary_reasons),
                "representative_ref": (
                    representative.source_key if representative is not None else None
                ),
                "representative_likes": (
                    representative.likes if representative is not None else None
                ),
                "rule_version": "selection-v1",
                "decided_at": decided_at,
            }
            validate_entity("candidate_decision", entity)
            connection.execute(
                """
                INSERT OR IGNORE INTO candidate_decision
                    (decision_id, run_id, offer_key, source_key, stage, primary_reason,
                     secondary_reasons_json, representative_ref, representative_likes,
                     rule_version, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    run_id,
                    persisted_offer_key,
                    decision.source_key,
                    entity["stage"],
                    decision.primary_reason,
                    json.dumps(list(decision.secondary_reasons), separators=(",", ":")),
                    entity["representative_ref"],
                    entity["representative_likes"],
                    entity["rule_version"],
                    decided_at,
                ),
            )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _draft_count(connection: sqlite3.Connection, run_id: str) -> int:
    row = connection.execute(
        "SELECT committed_count FROM run WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise DailyOrchestrationError("daily_run_missing_during_count")
    return int(row["committed_count"])


def _theme_draft_count(
    connection: sqlite3.Connection,
    run_id: str,
    theme_id: str,
    *,
    state: Mapping[str, Any] | None = None,
) -> int:
    if state is not None:
        assignments = state.get("offer_theme_ids", {})
        return sum(
            assignments.get(row[0]) == theme_id
            for row in connection.execute(
                "SELECT DISTINCT offer_key FROM draft WHERE run_id=?", (run_id,)
            )
        )
    return int(
        connection.execute(
            """
            SELECT COUNT(*) FROM draft
              JOIN offer USING (offer_key)
             WHERE draft.run_id=? AND offer.main_theme_id=?
            """,
            (run_id, theme_id),
        ).fetchone()[0]
    )


def _mark_offer_handled(state: dict[str, Any], offer_key: str) -> None:
    handled = state.setdefault("handled_offer_keys", [])
    if not isinstance(handled, list):
        raise DailyOrchestrationError("handled_offer_keys_invalid")
    if offer_key not in handled:
        handled.append(offer_key)


def _intake_missing_offers(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    settings_version: str,
    theme_id: str,
    attempt_id: str,
    identities: Mapping[str, str | None],
    port: OfferIntakePort | None,
) -> None:
    """Register identified observations before selection, without inventing facts."""

    rows = connection.execute(
        "SELECT source_key, likes, item_url, image_ref, evidence_ref, observed_at "
        "FROM source_post WHERE attempt_id=? ORDER BY order_index",
        (attempt_id,),
    ).fetchall()
    for row in rows:
        source_key = str(row["source_key"])
        offer_key = identities.get(source_key)
        if offer_key is None:
            continue
        existing = connection.execute(
            "SELECT 1 FROM offer WHERE offer_key=?", (offer_key,)
        ).fetchone()
        if existing is not None:
            continue
        if port is None:
            raise DailyOrchestrationError("offer_intake_required_for_new_identity")
        item_url = row["item_url"]
        if not isinstance(item_url, str) or not item_url:
            raise DailyOrchestrationError("identified_source_item_url_missing")
        port.persist_offer(
            connection,
            OfferIntakeRequest(
                run_id=run_id,
                settings_version=settings_version,
                theme_id=theme_id,
                offer_key=offer_key,
                source_key=source_key,
                item_url=item_url,
                image_ref=row["image_ref"],
                evidence_ref=str(row["evidence_ref"]),
                observed_at=str(row["observed_at"]),
            ),
        )
        persisted = connection.execute(
            "SELECT shop_id, item_id, main_theme_id FROM offer WHERE offer_key=?",
            (offer_key,),
        ).fetchone()
        if (
            persisted is None
            or f"{persisted['shop_id']}:{persisted['item_id']}" != offer_key
            or persisted["main_theme_id"] != theme_id
        ):
            raise DailyOrchestrationError("offer_intake_identity_not_persisted")


def _process_search_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    settings_version: str,
    theme_id: str,
    seed: str,
    attempt_id: str,
    identities: Mapping[str, str | None] | None,
    state: dict[str, Any],
    ports: DailyPorts,
    clock: Clock,
    data_root: Path,
) -> None:
    row = _attempt_row(connection, attempt_id)
    if row is None or row["ended_at"] is None:
        raise DailyOrchestrationError("search_result_not_durably_finalized")
    processed = state.setdefault("processed_attempts", [])
    if attempt_id in processed:
        return
    if row["source_status"] != "observed":
        state["source_failures"] = int(state.get("source_failures", 0)) + 1
    persisted_identities = (
        dict(identities)
        if identities is not None
        else _persisted_source_identities(connection, attempt_id)
    )
    source_rows = connection.execute(
        "SELECT source_key FROM source_post WHERE attempt_id=?", (attempt_id,)
    ).fetchall()
    if source_rows and not persisted_identities:
        raise DailyOrchestrationError("search_identity_checkpoint_missing")
    _intake_missing_offers(
        connection,
        run_id=run_id,
        settings_version=settings_version,
        theme_id=theme_id,
        attempt_id=attempt_id,
        identities=persisted_identities,
        port=ports.offer_intake,
    )
    posts = _source_posts_for_attempt(connection, attempt_id, persisted_identities)
    evaluation = evaluate_sources(
        posts, seed=seed, history=load_offer_history(connection, run_id)
    )
    _persist_candidate_decisions(
        connection,
        run_id=run_id,
        attempt_id=attempt_id,
        evaluation=evaluation,
        clock=clock,
    )
    handled = set(state.get("handled_offer_keys", []))
    for representative in evaluation.eligible_representatives:
        offer_key = representative.offer_key
        if offer_key in handled or _draft_count(connection, run_id) >= DAILY_TARGET:
            continue
        if _now(clock) >= _at(date.fromisoformat(target_date), DRAFT_CUTOFF):
            break
        offer = connection.execute(
            "SELECT main_theme_id FROM offer WHERE offer_key=?", (offer_key,)
        ).fetchone()
        if offer is None:
            _mark_offer_handled(state, offer_key)
            state["error_count"] = int(state.get("error_count", 0)) + 1
            _append_error(state, "copy", "offer_record_missing")
            _save_state(connection, run_id, state)
            continue
        if (
            _theme_draft_count(connection, run_id, theme_id, state=state)
            >= state["theme_targets"][theme_id]
        ):
            break
        assigned = state.setdefault("offer_theme_ids", {})
        if offer_key not in assigned:
            assigned[offer_key] = theme_id
            _save_state(connection, run_id, state)
        now = _now(clock)
        deadline_at = _at(date.fromisoformat(target_date), DRAFT_CUTOFF).isoformat()
        deadline_monotonic = clock.monotonic() + max(
            0.0,
            (_at(date.fromisoformat(target_date), DRAFT_CUTOFF) - now).total_seconds(),
        )
        spec = ports.copy_spec_factory(
            connection,
            run_id=run_id,
            offer_key=offer_key,
            theme_id=theme_id,
            target_date=target_date,
            settings_version=settings_version,
            deadline_at=deadline_at,
            deadline_monotonic=deadline_monotonic,
        )
        if spec is None:
            _mark_offer_handled(state, offer_key)
            state["error_count"] = int(state.get("error_count", 0)) + 1
            _append_error(state, "copy", "copy_input_unavailable")
            _save_state(connection, run_id, state)
            continue
        if (
            spec.run_id != run_id
            or spec.offer_key != offer_key
            or spec.target_date != target_date
            or spec.settings_version != settings_version
            or spec.deadline_at != deadline_at
        ):
            raise DailyOrchestrationError("copy_spec_run_identity_mismatch")
        outcome = generate_and_commit_copy(
            connection,
            spec,
            ports.copy_runner,
            data_root=data_root,
            clock=clock,
        )
        if outcome.status in {"committed", "already_committed"}:
            _mark_offer_handled(state, offer_key)
            _save_state(connection, run_id, state)
        elif outcome.status in {"pending_commit", "saved_response", "in_progress"}:
            raise DailyOrchestrationError(
                f"copy_resume_required:{outcome.error_code or outcome.status}"
            )
        else:
            _mark_offer_handled(state, offer_key)
            state["error_count"] = int(state.get("error_count", 0)) + 1
            _append_error(
                state,
                "copy",
                outcome.error_code or f"copy_{outcome.status}",
            )
            _save_state(connection, run_id, state)
    if attempt_id not in processed:
        processed.append(attempt_id)
    _save_state(connection, run_id, state)


def _search_task(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    task: Mapping[str, Any],
    settings_version: str,
    seed: str,
    target_date: str,
    state: dict[str, Any],
    ports: DailyPorts,
    clock: Clock,
    data_root: Path,
) -> None:
    task_number = int(task["task_number"])
    query = str(task["query"])
    used_queries = state.setdefault("used_queries", [])
    first_id = _task_attempt_id(run_id, task_number, 1)
    if query in used_queries and _attempt_row(connection, first_id) is None:
        return
    for local_attempt in range(1, 4):
        if _now(clock) >= _at(date.fromisoformat(target_date), DRAFT_CUTOFF):
            return
        if _draft_count(connection, run_id) >= DAILY_TARGET:
            return
        run_row = connection.execute(
            "SELECT attempt_used FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        if run_row is None:
            return
        attempt_id = _task_attempt_id(run_id, task_number, local_attempt)
        retry_of = (
            _task_attempt_id(run_id, task_number, local_attempt - 1)
            if local_attempt > 1
            else None
        )
        existing = _attempt_row(connection, attempt_id)
        if existing is None:
            result = RoomSearchSession(connection, ports.room_port, clock=clock).search(
                attempt_id=attempt_id,
                run_id=run_id,
                query=str(task["query"]),
                parent_refs=tuple(task["parent_refs"]),
                level=str(task["level"]),
                retry_of=retry_of,
                query_kind=task["query_kind"],
                keyword_snapshot_ref=task["keyword_snapshot_ref"],
                selected_word_ref=task["selected_word_ref"],
            )
            status = result.status
            if query not in used_queries:
                used_queries.append(query)
                _save_state(connection, run_id, state)
        else:
            if query not in used_queries:
                used_queries.append(query)
                _save_state(connection, run_id, state)
            if existing["ended_at"] is None:
                _finalize_interrupted_search(connection, attempt_id, clock)
            elif (
                existing["query"] != task["query"]
                or tuple(json.loads(existing["parent_refs_json"]))
                != tuple(task["parent_refs"])
                or existing["level"] != task["level"]
            ):
                raise DailyOrchestrationError("saved_search_input_changed")
            refreshed = _attempt_row(connection, attempt_id)
            status = str(refreshed["source_status"])
        if _now(clock) >= _at(date.fromisoformat(target_date), DRAFT_CUTOFF):
            state.setdefault("late_search_attempts", []).append(attempt_id)
            _save_state(connection, run_id, state)
            return
        _process_search_result(
            connection,
            run_id=run_id,
            target_date=target_date,
            settings_version=settings_version,
            theme_id=str(task["theme_id"]),
            seed=seed,
            attempt_id=attempt_id,
            identities=None,
            state=state,
            ports=ports,
            clock=clock,
            data_root=data_root,
        )
        if status not in {"partial", "unavailable", "invalid"}:
            return
    state["source_stopped"] = True
    _save_state(connection, run_id, state)


def _cancel_owned_work(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    state: dict[str, Any],
    ports: DailyPorts,
    force: bool = False,
) -> None:
    if state.get("cancelled_at_draft_cutoff") is True:
        return
    state["cancelled_at_draft_cutoff"] = True
    _save_state(connection, run_id, state)
    pending_search = connection.execute(
        "SELECT 1 FROM search_attempt WHERE run_id=? AND ended_at IS NULL LIMIT 1",
        (run_id,),
    ).fetchone()
    pending_copy = connection.execute(
        "SELECT 1 FROM generation_attempt WHERE run_id=? AND outcome='in_progress' LIMIT 1",
        (run_id,),
    ).fetchone()
    if force or pending_search or pending_copy:
        try:
            cancellation_confirmed = ports.cancel_owned_jobs(run_id)
        except Exception as exc:
            cancellation_error = type(exc).__name__
            cancellation_confirmed = False
        else:
            cancellation_error = "cancel_not_confirmed"
        if not cancellation_confirmed:
            state["error_count"] = int(state.get("error_count", 0)) + 1
            _append_error(
                state,
                "cutoff",
                "owned_job_cancel_failed",
                cancellation_error,
            )
            _save_state(connection, run_id, state)


def _main_database_path(connection: sqlite3.Connection) -> Path:
    databases = connection.execute("PRAGMA database_list").fetchall()
    main_files = [str(row[2]) for row in databases if str(row[1]) == "main"]
    if len(main_files) != 1 or not main_files[0]:
        raise DailyOrchestrationError("metrics_database_must_be_a_file")
    return Path(main_files[0]).expanduser().resolve()


def _open_read_only_connection(database_path: Path) -> sqlite3.Connection:
    readonly = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True, timeout=5)
    readonly.row_factory = sqlite3.Row
    readonly.execute("PRAGMA query_only=ON")
    return readonly


def _write_metrics_status_in_transaction(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    state: dict[str, Any],
    status: str,
) -> None:
    if status not in {"observed", "partial", "unavailable", "not_started"}:
        raise DailyOrchestrationError("metrics_status_invalid")
    state["metrics_status"] = status
    row = connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise DailyOrchestrationError("daily_run_missing_during_metrics_commit")
    root, _ = _daily_state(row["checkpoint"])
    root[_DAILY_CHECKPOINT_KEY] = dict(state)
    encoded = json.dumps(
        root,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    connection.execute(
        "UPDATE run SET metrics_status=?, checkpoint=? WHERE run_id=?",
        (status, encoded, run_id),
    )


def _persist_metrics_status(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    state: dict[str, Any],
    status: str,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        _write_metrics_status_in_transaction(
            connection, run_id=run_id, state=state, status=status
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _collect_metrics(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    state: dict[str, Any],
    ports: DailyPorts,
    clock: Clock,
    data_root: Path,
) -> str:
    row = connection.execute(
        "SELECT metrics_status, end_deadline FROM run WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise DailyOrchestrationError("daily_run_missing_before_metrics")
    current_status = str(row["metrics_status"])
    if current_status in {
        "observed",
        "partial",
        "unavailable",
        "not_started",
    } and state.get("metrics_started_at"):
        return current_status
    deadline = _at(date.fromisoformat(target_date), METRICS_CUTOFF)
    if _now(clock) >= deadline:
        if current_status == "pending":
            _persist_metrics_status(
                connection,
                run_id=run_id,
                state=state,
                status="unavailable",
            )
            return "unavailable"
        return current_status
    if current_status == "pending":
        # A prior process may have stopped after starting this request. Its late
        # answer must not be duplicated or re-adopted on a restart.
        status = "unavailable"
    else:
        state["metrics_started_at"] = _now(clock).isoformat()
        connection.execute(
            "UPDATE run SET metrics_status='pending' WHERE run_id=?", (run_id,)
        )
        _save_state(connection, run_id, state)
        deadline_monotonic = clock.monotonic() + max(
            0.0, (deadline - _now(clock)).total_seconds()
        )
        cancel_metrics = _CancelRunWork(run_id, ports.cancel_metrics_job)
        metrics_database_path = _main_database_path(connection)

        def collect_with_read_only_connection() -> str | MetricsCollectionResult:
            metrics_connection = _open_read_only_connection(metrics_database_path)
            try:
                return ports.metrics_port.collect(
                    metrics_connection,
                    run_id=run_id,
                    deadline_at=str(row["end_deadline"]),
                )
            finally:
                metrics_connection.close()

        try:
            completed, result = _call_before_deadline(
                collect_with_read_only_connection,
                deadline_monotonic=deadline_monotonic,
                clock=clock,
                cancel=lambda: cancel_metrics(run_id),
            )
            if not completed:
                status = "unavailable"
                if cancel_metrics.error_code:
                    state["error_count"] = int(state.get("error_count", 0)) + 1
                    _append_error(
                        state,
                        "metrics",
                        "metrics_job_cancel_failed",
                        cancel_metrics.error_code,
                    )
            else:
                status = result
                if isinstance(result, MetricsCollectionResult):
                    if result.status == "not_started":
                        status = "not_started"
                        _persist_metrics_status(
                            connection,
                            run_id=run_id,
                            state=state,
                            status=status,
                        )
                        return status
                    status = result.status
                    try:
                        commit_metrics_result(
                            connection,
                            data_root=data_root,
                            result=result,
                            expected_run_id=run_id,
                            clock=clock,
                            deadline_at=deadline,
                            before_commit=lambda: _write_metrics_status_in_transaction(
                                connection,
                                run_id=run_id,
                                state=state,
                                status=result.status,
                            ),
                        )
                        return status
                    except MetricsDeadlineExceeded:
                        status = "unavailable"
                        _append_error(state, "metrics", "metrics_commit_deadline")
                    except Exception as exc:
                        status = "unavailable"
                        _append_error(
                            state, "metrics", "metrics_commit_error", type(exc).__name__
                        )
            if status not in {"observed", "partial", "unavailable", "not_started"}:
                status = "unavailable"
        except Exception as exc:
            status = "unavailable"
            _append_error(state, "metrics", "metrics_port_error", type(exc).__name__)
        if _now(clock) >= _at(date.fromisoformat(target_date), METRICS_CUTOFF):
            status = "partial" if status == "observed" else "unavailable"
    _persist_metrics_status(connection, run_id=run_id, state=state, status=status)
    return status


def run_daily(
    connection: sqlite3.Connection,
    *,
    data_root: str | Path,
    ports: DailyPorts,
    clock: Clock | None = None,
    run_id_factory: Callable[[date], str] | None = None,
    seed_factory: Callable[[], str] | None = None,
) -> DailyRunOutcome:
    """Run or resume today's finite daily flow under the real Windows lock."""

    system_clock = clock or SystemClock()
    now = _now(system_clock)
    target_date = now.date().isoformat()
    if now.time() < DAILY_START:
        return DailyRunOutcome("not_due", None, target_date, 0, 0, "not_started")
    id_factory = run_id_factory or (lambda target: f"INAGO-{target.isoformat()}")
    actual_seed_factory = seed_factory or (lambda: secrets.token_urlsafe(24))
    root = Path(data_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    existing_before_lock = _run_by_date(connection, target_date)
    proposed_run_id = (
        str(existing_before_lock["run_id"])
        if existing_before_lock is not None
        else id_factory(now.date())
    )
    if not isinstance(proposed_run_id, str) or not _SAFE_RUN_ID.fullmatch(
        proposed_run_id
    ):
        raise DailyOrchestrationError("daily_run_id_invalid")
    try:
        lock = RunLock.acquire(
            root / ".daily-run.lock",
            root / ".daily-run-owner.json",
            run_id=proposed_run_id,
        )
    except RunLockBusy:
        existing = _run_by_date(connection, target_date)
        if existing is not None:
            return _outcome_from_run(existing, resumed=True, reason="daily_lock_busy")
        return DailyRunOutcome(
            "locked", None, target_date, 0, 0, "not_started", reason="daily_lock_busy"
        )

    with lock, ExitStack() as daily_resources:
        existing = _run_by_date(connection, target_date)
        if now.time() >= METRICS_CUTOFF:
            if existing is not None:
                if str(existing["metrics_status"]) == "pending":
                    _, state = _daily_state(existing["checkpoint"])
                    _persist_metrics_status(
                        connection,
                        run_id=str(existing["run_id"]),
                        state=state,
                        status="unavailable",
                    )
                    existing = _run_by_date(connection, target_date)
                return _outcome_from_run(existing, reason="run_already_started_today")
            missed = _create_run(
                connection,
                target_date=target_date,
                now=now,
                ports=ports,
                run_id_factory=lambda _target: proposed_run_id,
                seed_factory=actual_seed_factory,
                missed=True,
            )
            return _outcome_from_run(missed, reason="scheduled_start_after_1800")

        resumed = existing is not None
        run = existing or _create_run(
            connection,
            target_date=target_date,
            now=now,
            ports=ports,
            run_id_factory=lambda _target: proposed_run_id,
            seed_factory=actual_seed_factory,
            missed=False,
        )
        run_id = str(run["run_id"])
        if run["status"] in {"complete", "missed"}:
            return _outcome_from_run(run, resumed=resumed)
        _daily_state(run["checkpoint"])
        _set_running_for_resume(connection, run_id)
        current = connection.execute(
            "SELECT * FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        if current is None:
            raise DailyOrchestrationError("daily_run_disappeared")
        settings_version = str(current["settings_version"])
        seed = str(current["seed"])
        _, state = _daily_state(current["checkpoint"])
        if state.get("phase") == "finished":
            terminal = state.get("terminal_status")
            if terminal not in {"complete", "failed", "shortfall", "missed"}:
                raise DailyOrchestrationError("saved_terminal_status_invalid")
            return _finish(
                connection,
                run_id=run_id,
                clock=system_clock,
                state=state,
                status=terminal,
                reason=state.get("last_reason"),
            )

        if state.pop("recoverable_error_pending", False):
            state["error_count"] = max(0, int(state.get("error_count", 0)) - 1)
            _save_state(connection, run_id, state)
        draft_deadline = _at(date.fromisoformat(target_date), DRAFT_CUTOFF)
        deadline_monotonic = system_clock.monotonic() + max(
            0.0, (draft_deadline - _now(system_clock)).total_seconds()
        )
        cancel_run_work = _CancelRunWork(run_id, ports.cancel_owned_jobs)
        original_copy_runner = ports.copy_runner
        ports = replace(
            ports,
            room_port=_DeadlineRoomSearchPort(
                ports.room_port,
                deadline_monotonic=deadline_monotonic,
                clock=system_clock,
                cancel=lambda: cancel_run_work(run_id),
            ),
            copy_runner=_DeadlineCopyRunner(
                ports.copy_runner,
                clock=system_clock,
                cancel=lambda: cancel_run_work(run_id),
            ),
            cancel_owned_jobs=cancel_run_work,
        )

        orchestration_failed = False
        try:
            report_result = apply_pending_reports(
                connection, run_id=run_id, clock=system_clock
            )
            state["report_apply"] = {
                "applied_count": len(report_result.applied_action_ids),
                "pending_count": len(report_result.pending_action_ids),
                "exploration_allowed": report_result.exploration_allowed,
                "error": report_result.error,
            }
            _save_state(connection, run_id, state)
            if not report_result.exploration_allowed:
                state["error_count"] = int(state.get("error_count", 0)) + 1
                _append_error(
                    state,
                    "reports",
                    report_result.error or "pending_reports_block_exploration",
                )
            elif _now(system_clock) < _at(
                date.fromisoformat(target_date), DRAFT_CUTOFF
            ):
                if all(
                    callable(getattr(original_copy_runner, name, None))
                    for name in (
                        "prepare_daily_run",
                        "start_daily_root",
                        "sync_daily_checkpoint",
                        "close_daily_run",
                    )
                ):
                    original_copy_runner.prepare_daily_run(connection, run_id)

                    def close_root():
                        original_copy_runner.close_daily_run()
                        original_copy_runner.sync_daily_checkpoint(connection)

                    daily_resources.callback(close_root)
                    original_copy_runner.start_daily_root(
                        deadline_monotonic=deadline_monotonic
                    )
                    original_copy_runner.sync_daily_checkpoint(connection)
                _ensure_initial_work(
                    connection,
                    run_id=run_id,
                    settings_version=settings_version,
                    seed=seed,
                    state=state,
                    ports=ports,
                    clock=system_clock,
                )
                while _draft_count(connection, run_id) < DAILY_TARGET and not state.get(
                    "source_stopped"
                ):
                    if _now(system_clock) >= _at(
                        date.fromisoformat(target_date), DRAFT_CUTOFF
                    ):
                        break
                    tasks = state["tasks"]
                    cursor = int(state["task_cursor"])
                    if cursor >= len(tasks):
                        if _append_initial_theme(
                            connection,
                            run_id=run_id,
                            settings_version=settings_version,
                            seed=seed,
                            state=state,
                            ports=ports,
                            clock=system_clock,
                        ):
                            continue
                        if not _add_additional_theme(
                            connection,
                            run_id=run_id,
                            settings_version=settings_version,
                            seed=seed,
                            state=state,
                            ports=ports,
                            clock=system_clock,
                        ):
                            break
                        continue
                    task = tasks[cursor]
                    if (
                        _theme_draft_count(
                            connection, run_id, str(task["theme_id"]), state=state
                        )
                        >= state["theme_targets"][str(task["theme_id"])]
                    ):
                        state["task_cursor"] = cursor + 1
                        _save_state(connection, run_id, state)
                        continue
                    _search_task(
                        connection,
                        run_id=run_id,
                        task=task,
                        settings_version=settings_version,
                        seed=seed,
                        target_date=target_date,
                        state=state,
                        ports=ports,
                        clock=system_clock,
                        data_root=root,
                    )
                    if _now(system_clock) >= _at(
                        date.fromisoformat(target_date), DRAFT_CUTOFF
                    ):
                        _cancel_owned_work(
                            connection,
                            run_id=run_id,
                            state=state,
                            ports=ports,
                            force=True,
                        )
                        break
                    state["task_cursor"] = cursor + 1
                    _save_state(connection, run_id, state)

            if (
                _now(system_clock) >= _at(date.fromisoformat(target_date), DRAFT_CUTOFF)
                and state.get("cancelled_at_draft_cutoff") is not True
            ):
                _cancel_owned_work(
                    connection,
                    run_id=run_id,
                    state=state,
                    ports=ports,
                    force=False,
                )
        except Exception as exc:
            orchestration_failed = True
            state["error_count"] = int(state.get("error_count", 0)) + 1
            state["recoverable_error_pending"] = True
            _append_error(
                state,
                "orchestration",
                f"step13_error_{type(exc).__name__}",
            )
            _save_state(connection, run_id, state)

        metric_status = _collect_metrics(
            connection,
            run_id=run_id,
            target_date=target_date,
            state=state,
            ports=ports,
            clock=system_clock,
            data_root=root,
        )
        count, attempts = _status_counts(connection, run_id)
        if count >= DAILY_TARGET:
            status, reason = "complete", "daily_target_reached"
        elif count > 0:
            status = "partial"
            reason = (
                "draft_cutoff_1745"
                if _now(system_clock)
                >= _at(date.fromisoformat(target_date), DRAFT_CUTOFF)
                else "daily_interrupted_or_candidate_shortage"
            )
        elif int(state.get("error_count", 0)) or int(state.get("source_failures", 0)):
            status, reason = "failed", "daily_processing_error"
        else:
            status = "shortfall"
            reason = (
                "draft_cutoff_1745"
                if _now(system_clock)
                >= _at(date.fromisoformat(target_date), DRAFT_CUTOFF)
                else "candidate_or_theme_exhausted"
            )
        outcome = _finish(
            connection,
            run_id=run_id,
            clock=system_clock,
            state=state,
            status=status,
            reason=reason,
            preserve_checkpoint=orchestration_failed,
        )
        return DailyRunOutcome(
            outcome.status,
            outcome.run_id,
            outcome.target_date,
            outcome.committed_count,
            attempts,
            metric_status,
            resumed=resumed,
            reason=reason,
        )
