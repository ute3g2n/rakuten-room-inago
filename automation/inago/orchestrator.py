"""Finite, resumable daily control flow for the Inago runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import queue
import re
import secrets
import sqlite3
import threading
import traceback
import uuid
from dataclasses import dataclass, replace
from contextlib import ExitStack
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from automation.inago.ai_runner import compute_input_hash
from automation.inago.clock import Clock, SystemClock
from automation.inago.copy_service import (
    CopyGenerationSpec,
    CopyRunner,
    generate_and_commit_copy,
)
from automation.inago.contracts import AIJobRequest, AIJobResult, RejectedContract
from automation.inago.metrics import (
    MetricsCollectionResult,
    MetricsDeadlineExceeded,
    commit_metrics_result,
)
from automation.inago.report_apply import apply_pending_reports
from automation.inago.report_import import import_offline_report_batches
from automation.inago.retention import apply_retention, validate_data_root
from automation.inago.repository import (
    LEGACY_UNASSIGNED_THEME_ID,
    offer_needs_name_observation,
    validate_entity,
)
from automation.inago.room_adapter import (
    RoomSearchPort,
    RoomSearchSession,
)
from automation.inago.run_lock import RunLock, RunLockBusy
from automation.inago.theme_queries import theme_query_candidates
from automation.inago.trends_adapter import TrendsRequest, load_grouped_snapshot
from automation.inago.trace import TraceSession, TraceWriter, make_trace_event
from automation.inago.selection import (
    Representative,
    SelectionResult,
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
DAILY_TARGET = 15
PER_THEME_TARGET = 5
AI_REQUEST_TIMEOUT_SECONDS = 480
METRICS_REQUEST_TIMEOUT_SECONDS = 300
MAX_COPY_ATTEMPTS = 3
_DAILY_CHECKPOINT_KEY = "daily_orchestration"
_DAILY_CHECKPOINT_VERSION = 4
_DAILY_RULES_VERSION = "requirements-1.5"
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")
_SAFE_ACQUISITION_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
_SAFE_ORCHESTRATION_REASON_CODE = re.compile(
    r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z"
)


class DailyOrchestrationError(RuntimeError):
    """A daily phase cannot continue without violating its persisted contract."""


_COPY_SPEC_REJECTION_CODES = {
    "copy evidence references must be available inputs": (
        "copy_evidence_refs_not_input"
    ),
    "copy evidence references must be unique": "copy_evidence_refs_not_unique",
    "copy input references must be non-empty and unique": ("copy_input_refs_invalid"),
    "copy references must be safe non-empty identifiers": ("copy_refs_unsafe"),
    "copy input payload does not match the requested offer/version": (
        "copy_offer_version_mismatch"
    ),
}


class _CopySpecContractRejected(DailyOrchestrationError):
    """A copy-spec contract rejection with safe, structured diagnostic fields."""

    def __init__(self, *, run_id: str, offer_key: str, rejection: RejectedContract):
        self.phase = "copy_spec"
        self.error_code = _COPY_SPEC_REJECTION_CODES.get(
            str(rejection), "copy_spec_contract_rejected"
        )
        self.references = {"run_id": run_id, "offer_key": offer_key}
        super().__init__(self.error_code)


class _CopyContractRejected(DailyOrchestrationError):
    """A rejected AI copy result with safe cause and artifact references."""

    def __init__(
        self,
        *,
        run_id: str,
        offer_key: str,
        error_code: str,
        cause_code: str | None,
        artifact_ref: str | None,
    ):
        self.phase = "copy"
        self.error_code = (
            error_code
            if isinstance(error_code, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", error_code)
            else "copy_contract_rejected"
        )
        self.references = {"run_id": run_id, "offer_key": offer_key}
        if cause_code is not None:
            self.references["cause_code"] = cause_code
        if artifact_ref is not None:
            self.references["artifact_ref"] = artifact_ref
        super().__init__(self.error_code)


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


def _cancel_request_job(
    callback: Callable[[str, str], bool] | None, run_id: str, request_id: str
) -> bool:
    """Cancel only the active ROOM or AI request; missing support is unconfirmed."""

    if callback is None:
        return False
    try:
        return callback(run_id, request_id) is True
    except Exception:
        return False


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


class _DeadlineCopyRunner:
    """Discard an AI-O result that arrives at or after its request deadline."""

    def __init__(
        self,
        runner: CopyRunner,
        *,
        clock: Clock,
        cancel: Callable[[AIJobRequest], Any],
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._cancel = cancel
        self.last_cancel_confirmed: bool | None = None

    def run(self, request: AIJobRequest) -> AIJobResult:
        self.last_cancel_confirmed = None

        def cancel_request() -> bool:
            self.last_cancel_confirmed = self._cancel(request) is True
            return self.last_cancel_confirmed

        completed, result = _call_before_deadline(
            lambda: self._runner.run(request),
            deadline_monotonic=request.deadline_monotonic,
            clock=self._clock,
            cancel=cancel_request,
        )
        if completed:
            return result
        error_code = (
            "ai_request_timeout"
            if self.last_cancel_confirmed
            else "ai_request_timeout_cancel_unconfirmed"
        )
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
            input_hash=compute_input_hash(request.input_payload),
            deadline_at=request.deadline_at,
            result_status="timed_out",
            adoption_status="pending",
            payload=None,
            error={"code": error_code},
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
    fallback: DailySearchPlan | None
    snapshot_ref: str | None
    acquisition_status: str = "observed"
    acquisition_ref: str | None = None
    rules_version: str = "requirements-1.5"
    has_related_words: bool | None = None


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


class ProductPageOCRPort(Protocol):
    """OCR one selected product's saved page images and persist its product JSON.

    STEP04 implements this contract. The OCR port must preserve every image's
    success/empty/error state and return ``status`` as complete, partial, or
    failed. The orchestrator calls it for every active selection before it
    starts any copy request. The returned mapping contains ``status``, ordered
    ``images`` (each with ``order``, ``status`` and optional bounded refs), and
    ``ocr_ref``: the stable reference to the product-level JSON that the copy
    input factory can resolve for this same run and offer.
    """

    def process_product(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        offer_key: str,
        shop_id: str,
        item_id: str,
        page_ref: str | None,
        source_evidence_ref: str | None,
        data_root: Path,
    ) -> Mapping[str, Any]:
        """Return durable OCR state for this run/product identity."""


class ProductPageCapturePort(Protocol):
    """Capture one exact saved Rakuten item URL without searching ROOM again."""

    def capture_product_page_inputs(self, request: Any) -> Mapping[str, Any]:
        """Return the bounded capture state for the requested item identity."""


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

    ``cancel_owned_jobs`` stops the whole run for explicit run cancellation.
    Request timeout callbacks stop only the active ROOM or AI job so a bounded
    retry can continue. Missing or unconfirmed request cancellation is fatal.
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
    cancel_room_request: Callable[[str, str], bool] | None = None
    cancel_ai_request: Callable[[str, str], bool] | None = None
    product_page_ocr: ProductPageOCRPort | None = None
    product_page_capture: ProductPageCapturePort | None = None


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
    daily_target: int = DAILY_TARGET
    initial_allocation_count: int = 0
    supplemental_allocation_count: int = 0
    shortfall_count: int = 0
    actual_posted_count: int = 0
    source_failures: int = 0
    error_count: int = 0
    primary_error: Mapping[str, Any] | None = None
    secondary_errors: tuple[Mapping[str, Any], ...] = ()


def _now(clock: Clock) -> datetime:
    value = clock.now_jst()
    if value.tzinfo is None or value.utcoffset() is None:
        raise DailyOrchestrationError("daily clock must return an aware datetime")
    return value.astimezone(JST)


def _jst_date(value: object) -> date:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DailyOrchestrationError("offer_intake_observation_time_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyOrchestrationError("offer_intake_observation_time_invalid")
    return parsed.astimezone(JST).date()


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


def _migrate_daily_checkpoint_v2(state: object) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise DailyOrchestrationError("daily_checkpoint_version_invalid")
    if (
        state.get("schema_version") != 2
        or state.get("rules_version") != "requirements-1.4"
    ):
        raise DailyOrchestrationError("daily_checkpoint_version_invalid")
    plans = state.get("plans")
    if not isinstance(plans, dict):
        raise DailyOrchestrationError("daily_checkpoint_work_invalid")
    old_fields = {
        "theme_id",
        "keyword_refs",
        "related",
        "fallback",
        "snapshot_ref",
        "rules_version",
    }
    migrated_plans: dict[str, Any] = {}
    for key, value in plans.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, dict)
            or set(value) != old_fields
            or value.get("rules_version") != "requirements-1.4"
        ):
            raise DailyOrchestrationError("saved_theme_plan_fields_invalid")
        migrated_plan = dict(value)
        migrated_plan.update(
            acquisition_status="observed",
            acquisition_ref=None,
            legacy_acquisition=True,
            rules_version=_DAILY_RULES_VERSION,
        )
        migrated_plans[key] = migrated_plan
    migrated = dict(state)
    migrated.update(
        schema_version=_DAILY_CHECKPOINT_VERSION,
        rules_version=_DAILY_RULES_VERSION,
        migrated_from_schema_version=2,
        plans=migrated_plans,
        selected_products=[],
        next_selection_order=1,
    )
    return migrated


def _migrate_daily_checkpoint_v3(state: object) -> dict[str, Any]:
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 3
        or state.get("rules_version") != _DAILY_RULES_VERSION
    ):
        raise DailyOrchestrationError("daily_checkpoint_version_invalid")
    migrated = dict(state)
    migrated.update(
        schema_version=_DAILY_CHECKPOINT_VERSION,
        rules_version=_DAILY_RULES_VERSION,
        migrated_from_schema_version=3,
        selected_products=[],
        next_selection_order=1,
    )
    return migrated


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
            "source_stopped_reason": None,
            "phase": "initial",
            "initial_theme_ids": [],
            "primary_error": None,
            "plans": {},
            "tasks": [],
            "task_cursor": 0,
            "additional_events": 0,
            "source_failures": 0,
            "error_count": 0,
            "processed_attempts": [],
            "handled_offer_keys": [],
            "selected_products": [],
            "next_selection_order": 1,
            "errors": [],
        }
        root[_DAILY_CHECKPOINT_KEY] = state
    elif (
        isinstance(state, dict)
        and state.get("schema_version") == 2
        and state.get("rules_version") == "requirements-1.4"
    ):
        state = _migrate_daily_checkpoint_v2(state)
        root[_DAILY_CHECKPOINT_KEY] = state
    elif (
        isinstance(state, dict)
        and state.get("schema_version") == 3
        and state.get("rules_version") == _DAILY_RULES_VERSION
    ):
        state = _migrate_daily_checkpoint_v3(state)
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
    if not isinstance(state.get("selected_products"), list) or any(
        not isinstance(item, dict) for item in state["selected_products"]
    ):
        raise DailyOrchestrationError("daily_selection_manifest_invalid")
    next_order = state.get("next_selection_order", 1)
    if isinstance(next_order, bool) or not isinstance(next_order, int) or next_order < 1:
        raise DailyOrchestrationError("daily_selection_order_invalid")
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
    state: dict[str, Any],
    phase: str,
    code: str,
    detail: str | None = None,
    references: Mapping[str, str] | None = None,
) -> None:
    errors = state.setdefault("errors", [])
    if not isinstance(errors, list):
        raise DailyOrchestrationError("daily_checkpoint_errors_invalid")
    item = {"phase": phase, "code": code}
    if detail:
        item["detail"] = detail[:300]
    if references:
        run_id = references.get("run_id")
        offer_key = references.get("offer_key")
        safe_references = {}
        if isinstance(run_id, str) and _SAFE_RUN_ID.fullmatch(run_id):
            safe_references["run_id"] = run_id
        if isinstance(offer_key, str) and _SAFE_ACQUISITION_REF.fullmatch(offer_key):
            safe_references["offer_key"] = offer_key
        cause_code = references.get("cause_code")
        if isinstance(cause_code, str) and _SAFE_ACQUISITION_REF.fullmatch(cause_code):
            safe_references["cause_code"] = cause_code
        artifact_ref = references.get("artifact_ref")
        if isinstance(artifact_ref, str) and _SAFE_ACQUISITION_REF.fullmatch(
            artifact_ref
        ):
            safe_references["artifact_ref"] = artifact_ref
        if safe_references:
            item["references"] = safe_references
    errors.append(item)


def _record_product_slot_failure(
    state: dict[str, Any],
    *,
    run_id: str,
    entry: Mapping[str, Any],
    phase: str,
    code: str,
    failed_at: str,
) -> None:
    offer_key = entry.get("offer_key")
    if not isinstance(offer_key, str):
        return
    active = state.setdefault("active_product_slot_failures", [])
    if any(
        isinstance(item, dict)
        and item.get("offer_key") == offer_key
        and item.get("phase") == phase
        for item in active
    ):
        return
    failure = {
        "offer_key": offer_key,
        "theme_id": entry.get("theme_id"),
        "phase": phase,
        "code": code,
        "page_ref": entry.get("page_ref"),
        "selection_order": entry.get("selection_order"),
        "slot_id": entry.get("slot_id")
        or _product_slot_id(run_id, entry.get("selection_order")),
        "failed_at": failed_at,
    }
    active.append(failure)
    state["error_count"] = int(state.get("error_count", 0)) + 1
    references = {"run_id": run_id, "offer_key": offer_key}
    if isinstance(entry.get("page_ref"), str):
        references["artifact_ref"] = entry["page_ref"]
    _append_error(state, phase, code, references=references)
    state["primary_error"] = dict(state["errors"][-1])


def _product_slot_id(run_id: str, selection_order: object) -> str | None:
    if isinstance(selection_order, bool) or not isinstance(selection_order, int):
        return None
    if selection_order < 1:
        return None
    return f"{run_id}:slot:{selection_order:03d}"


def _assign_product_slot_replacement(
    state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """Bind one new selection to one explicit, still-unfilled failed slot."""

    active = state.get("active_product_slot_failures", [])
    if not isinstance(active, list):
        return
    offer_key = entry.get("offer_key")
    theme_id = entry.get("theme_id")
    if not isinstance(offer_key, str) or not isinstance(theme_id, str):
        return
    for failure in active:
        if (
            not isinstance(failure, dict)
            or failure.get("offer_key") == offer_key
            or isinstance(failure.get("replacement_offer_key"), str)
            or _product_slot_failure_theme_id(state, failure) != theme_id
        ):
            continue
        slot_id = failure.get("slot_id")
        if not isinstance(slot_id, str):
            slot_id = _product_slot_id(
                str(entry.get("run_id", "")), failure.get("selection_order")
            )
            if slot_id is None:
                continue
            failure["slot_id"] = slot_id
        failure["replacement_offer_key"] = offer_key
        failure["replacement_slot_id"] = slot_id
        entry["replacement_slot_id"] = slot_id
        return


def _product_slot_failure_theme_id(
    state: Mapping[str, Any], failure: Mapping[str, Any]
) -> str | None:
    """Recover a legacy failure's theme from its saved selection identity."""

    theme_id = failure.get("theme_id")
    if isinstance(theme_id, str):
        return theme_id
    for selected in state.get("selected_products", []):
        if (
            isinstance(selected, Mapping)
            and selected.get("offer_key") == failure.get("offer_key")
            and isinstance(selected.get("theme_id"), str)
        ):
            return selected["theme_id"]
    assignments = state.get("offer_theme_ids", {})
    if isinstance(assignments, Mapping):
        theme_id = assignments.get(failure.get("offer_key"))
        if isinstance(theme_id, str):
            return theme_id
    return None


def _archive_resolved_error(
    state: dict[str, Any], error: Mapping[str, Any], *, resolution: str, at: str
) -> None:
    state.setdefault("resolved_error_history", []).append(
        {"error": dict(error), "resolution": resolution, "resolved_at": at}
    )


def _resolve_product_slot_failure_on_commit(
    state: dict[str, Any],
    *,
    committed_entry: Mapping[str, Any],
    committed_at: str,
) -> None:
    active = state.get("active_product_slot_failures", [])
    if not isinstance(active, list) or not active:
        return
    committed_offer = committed_entry.get("offer_key")
    selected_index = next(
        (
            index
            for index, failure in enumerate(active)
            if isinstance(failure, dict)
            and failure.get("offer_key") == committed_offer
        ),
        None,
    )
    if selected_index is None:
        replacement_slot_id = committed_entry.get("replacement_slot_id")
        selected_index = next(
            (
                index
                for index, failure in enumerate(active)
                if isinstance(failure, dict)
                and failure.get("replacement_offer_key") == committed_offer
                and isinstance(committed_entry.get("theme_id"), str)
                and _product_slot_failure_theme_id(state, failure)
                == committed_entry["theme_id"]
                and isinstance(replacement_slot_id, str)
                and failure.get("slot_id") == replacement_slot_id
                and failure.get("replacement_slot_id") == replacement_slot_id
            ),
            None,
        )
    if selected_index is None:
        return
    failure = active.pop(selected_index)
    errors = state.setdefault("errors", [])
    removed_error = None
    for index in range(len(errors) - 1, -1, -1):
        error = errors[index]
        references = error.get("references") if isinstance(error, dict) else None
        if (
            isinstance(error, dict)
            and error.get("phase") == failure.get("phase")
            and isinstance(references, dict)
            and references.get("offer_key") == failure.get("offer_key")
        ):
            removed_error = errors.pop(index)
            break
    if removed_error is not None:
        state["error_count"] = max(0, int(state.get("error_count", 0)) - 1)
        _archive_resolved_error(
            state,
            removed_error,
            resolution=(
                "same_offer_operation_recovered"
                if failure.get("offer_key") == committed_offer
                else "impact_recovered_by_supplement"
            ),
            at=committed_at,
        )
    primary = state.get("primary_error")
    primary_refs = primary.get("references") if isinstance(primary, dict) else None
    if (
        isinstance(primary, dict)
        and primary.get("phase") == failure.get("phase")
        and isinstance(primary_refs, dict)
        and primary_refs.get("offer_key") == failure.get("offer_key")
    ):
        if errors:
            state["primary_error"] = dict(errors[-1])
        else:
            state.pop("primary_error", None)
    resolved = state.setdefault("resolved_product_slot_failures", [])
    is_same_offer = failure.get("offer_key") == committed_offer
    resolved.append(
        {
            **failure,
            "resolution": (
                "same_offer_operation_recovered"
                if is_same_offer
                else "impact_recovered_by_supplement"
            ),
            "replacement_offer_key": committed_offer,
            "replacement_page_ref": committed_entry.get("page_ref"),
            "committed_at": committed_at,
        }
    )


def _record_orchestration_error(state: dict[str, Any], exc: Exception) -> None:
    if isinstance(exc, (_CopySpecContractRejected, _CopyContractRejected)):
        _append_error(
            state,
            exc.phase,
            exc.error_code,
            references=exc.references,
        )
        state["primary_error"] = dict(state["errors"][-1])
        return
    if isinstance(exc, sqlite3.Error):
        _append_error(state, "save", "save_error")
        state["primary_error"] = dict(state["errors"][-1])
        return
    _append_error(
        state,
        "orchestration",
        f"step13_error_{type(exc).__name__}",
    )
    primary_error = dict(state["errors"][-1])
    try:
        from automation.inago.daily_pipeline import (
            _safe_diagnostic_class,
            _safe_exception_chain_details,
            _safe_exception_frame_details,
        )

        exception_chain, chain_truncated = _safe_exception_chain_details(exc)
        exception_frames, frames_truncated = _safe_exception_frame_details(exc)
        diagnostic = {
            "exception_class": _safe_diagnostic_class(exc),
            "exception_chain": exception_chain,
            "exception_chain_truncated": chain_truncated,
            "exception_frames": exception_frames,
            "exception_frames_truncated": frames_truncated,
        }
        reason_code = str(exc)
        if (
            isinstance(exc, DailyOrchestrationError)
            and _SAFE_ORCHESTRATION_REASON_CODE.fullmatch(reason_code)
        ):
            diagnostic["reason_code"] = reason_code
        primary_error["diagnostic"] = diagnostic
        state["errors"][-1] = primary_error
    except Exception:
        # Diagnostics must never change the original orchestration failure.
        pass
    state["primary_error"] = primary_error


def _run_by_date(connection: sqlite3.Connection, target_date: str):
    return connection.execute(
        "SELECT * FROM run WHERE target_date=? AND mode='daily'", (target_date,)
    ).fetchone()


def _run_create_diagnostic(
    trace: TraceSession | None,
    *,
    transaction_phase: str,
    proposed_run_id: str,
    persisted_run_id: str | None,
    run_persisted: bool | None,
    exc: BaseException | None = None,
) -> None:
    """Emit allowlisted run creation diagnostics without affecting the outcome."""

    error_name = getattr(exc, "sqlite_errorname", None)
    allowed_error_names = {
        "SQLITE_BUSY",
        "SQLITE_LOCKED",
        "SQLITE_CONSTRAINT",
        "SQLITE_CONSTRAINT_CHECK",
        "SQLITE_CONSTRAINT_FOREIGNKEY",
        "SQLITE_CONSTRAINT_NOTNULL",
        "SQLITE_CONSTRAINT_PRIMARYKEY",
        "SQLITE_CONSTRAINT_UNIQUE",
        "SQLITE_MISMATCH",
    }
    if error_name not in allowed_error_names:
        error_name = "unknown"
    constraint_label = {
        "SQLITE_CONSTRAINT_UNIQUE": "unique",
        "SQLITE_CONSTRAINT_PRIMARYKEY": "primary_key",
        "SQLITE_CONSTRAINT_NOTNULL": "not_null",
        "SQLITE_CONSTRAINT_CHECK": "check",
        "SQLITE_CONSTRAINT_FOREIGNKEY": "foreign_key",
    }.get(error_name, "unknown")
    frames = traceback.extract_tb(exc.__traceback__) if exc is not None else []
    failure_line = frames[-1].lineno if frames else None
    logger = logging.getLogger(__name__)

    def safe_log(level: str, message: str, extra: dict[str, Any]) -> None:
        try:
            getattr(logger, level)(message, extra=extra)
        except Exception:
            # Diagnostic I/O must never replace the daily transaction outcome.
            return

    trace_result = None
    if trace is not None:
        try:
            event = make_trace_event(
                run_id=proposed_run_id,
                target_date=trace.target_date,
                component="orchestrator",
                operation="run_create",
                phase="error" if exc is not None else "status",
                clock=trace.clock,
                event_kind="transaction",
                status="failed" if exc is not None else transaction_phase,
                processing_label="run_create",
                table_label="run",
                constraint_label=constraint_label,
                transaction_phase=transaction_phase,
                proposed_run_id=proposed_run_id,
                persisted_run_id=persisted_run_id,
                run_persisted=run_persisted,
                trace_requested=True,
                error_class=type(exc).__name__ if exc is not None else None,
                primary_error_code=type(exc).__name__ if exc is not None else None,
                error_code_number=getattr(exc, "sqlite_errorcode", None),
                error_name=error_name,
                failure_module=(
                    "automation.inago.orchestrator" if exc is not None else None
                ),
                failure_function="_create_run" if exc is not None else None,
                failure_line=failure_line,
            )
            trace_result = trace.writer.write(event)
        except Exception as trace_exc:
            safe_log(
                "warning",
                "daily_run_create_trace_write_failed",
                {"trace_error_class": type(trace_exc).__name__},
            )
    safe_log(
        "info",
        "daily_run_create_transaction",
        {
            "processing_label": "run_create",
            "table_label": "run",
            "constraint_label": constraint_label,
            "transaction_phase": transaction_phase,
            "proposed_run_id": proposed_run_id,
            "persisted_run_id": persisted_run_id,
            "run_persisted": run_persisted,
            "exception_class": type(exc).__name__ if exc is not None else None,
            "error_code_number": getattr(exc, "sqlite_errorcode", None),
            "error_name": error_name,
            "failure_module": (
                "automation.inago.orchestrator" if exc is not None else None
            ),
            "failure_function": "_create_run" if exc is not None else None,
            "failure_line": failure_line,
            "trace_requested": trace is not None,
            "trace_persisted": bool(
                trace_result is not None
                and trace_result.status in {"written", "appended"}
            ),
            "trace_status": (
                trace_result.status if trace_result is not None else "unavailable"
            ),
        },
    )


def _create_run(
    connection: sqlite3.Connection,
    *,
    target_date: str,
    now: datetime,
    ports: DailyPorts,
    run_id_factory: Callable[[date], str],
    seed_factory: Callable[[], str],
    diagnostic_trace: TraceSession | None = None,
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
    status = "running"
    finished_at = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        _run_create_diagnostic(
            diagnostic_trace,
            transaction_phase="begin",
            proposed_run_id=run_id,
            persisted_run_id=None,
            run_persisted=False,
        )
        existing = _run_by_date(connection, target_date)
        if existing is not None:
            connection.rollback()
            _run_create_diagnostic(
                diagnostic_trace,
                transaction_phase="rollback",
                proposed_run_id=run_id,
                persisted_run_id=str(existing["run_id"]),
                run_persisted=True,
            )
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
                None,
                None,
                finished_at,
            ),
        )
        connection.commit()
        _run_create_diagnostic(
            diagnostic_trace,
            transaction_phase="commit",
            proposed_run_id=run_id,
            persisted_run_id=run_id,
            run_persisted=True,
        )
    except BaseException as exc:
        rollback_error = None
        if connection.in_transaction:
            try:
                connection.rollback()
            except BaseException as rollback_exc:
                rollback_error = rollback_exc
                _run_create_diagnostic(
                    diagnostic_trace,
                    transaction_phase="rollback_failed",
                    proposed_run_id=run_id,
                    persisted_run_id=None,
                    run_persisted=None,
                    exc=rollback_exc,
                )
            else:
                _run_create_diagnostic(
                    diagnostic_trace,
                    transaction_phase="rollback",
                    proposed_run_id=run_id,
                    persisted_run_id=None,
                    run_persisted=False,
                )
        if rollback_error is None:
            try:
                persisted = _run_by_date(connection, target_date)
                persisted_id = (
                    str(persisted["run_id"]) if persisted is not None else None
                )
                is_persisted: bool | None = persisted is not None
            except sqlite3.Error:
                persisted_id = None
                is_persisted = None
        else:
            persisted_id = None
            is_persisted = None
        _run_create_diagnostic(
            diagnostic_trace,
            transaction_phase=(
                "rollback_failed" if rollback_error is not None else "rollback"
            ),
            proposed_run_id=run_id,
            persisted_run_id=persisted_id,
            run_persisted=is_persisted,
            exc=exc,
        )
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
    return _outcome_from_run(row, connection=connection, reason=reason)


def _outcome_from_run(
    row,
    *,
    connection: sqlite3.Connection | None = None,
    resumed: bool = False,
    reason: str | None = None,
):
    _, state = _daily_state(row["checkpoint"])
    initial_theme_ids = state.get("initial_theme_ids", [])
    if not isinstance(initial_theme_ids, list):
        initial_theme_ids = []
    initial_allocations = min(DAILY_TARGET, len(initial_theme_ids) * PER_THEME_TARGET)
    targets = state.get("theme_targets", {})
    if not isinstance(targets, Mapping):
        targets = {}
    supplemental_theme_ids = [
        theme_id
        for theme_id, value in targets.items()
        if theme_id not in initial_theme_ids
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    ]
    supplemental_target_count = sum(
        int(targets[theme_id]) for theme_id in supplemental_theme_ids
    )
    if connection is not None:
        run_id = str(row["run_id"])
        initial_selected_count = sum(
            _selection_count(connection, run_id, state, theme_id=theme_id)
            for theme_id in initial_theme_ids
            if isinstance(theme_id, str)
        )
        known_committed_count = len(_committed_offer_themes(connection, run_id, state))
        untracked_committed_count = max(
            0, int(row["committed_count"]) - known_committed_count
        )
        initial_filled_count = min(
            initial_allocations,
            initial_selected_count + untracked_committed_count,
        )
        supplemental_allocations = min(
            supplemental_target_count,
            max(0, DAILY_TARGET - initial_filled_count),
        )
    else:
        supplemental_allocations = supplemental_target_count
    errors = state.get("errors", [])
    if not isinstance(errors, list):
        errors = []
    error_count = int(state.get("error_count", 0))
    primary_error = state.get("primary_error")
    if not isinstance(primary_error, Mapping):
        primary_error = None
    if primary_error is None and errors and isinstance(errors[0], Mapping):
        primary_error = dict(errors[0])
    if primary_error is None and (str(row["status"]) == "failed" or error_count > 0):
        primary_error = {
            "phase": "daily",
            "code": reason or "daily_processing_error",
        }
    if error_count == 0:
        primary_error = None
    secondary_errors = tuple(
        dict(item)
        for item in errors
        if isinstance(item, Mapping) and dict(item) != primary_error
    )
    actual_posted_count = 0
    if connection is not None:
        actual_posted_count = int(
            connection.execute(
                "SELECT COUNT(DISTINCT event.event_id) "
                "FROM publication_event AS event "
                "JOIN draft ON draft.draft_id=event.draft_id "
                "AND draft.version=event.draft_version "
                "WHERE draft.run_id=? AND event.valid=1",
                (str(row["run_id"]),),
            ).fetchone()[0]
        )
    committed_count = int(row["committed_count"])
    return DailyRunOutcome(
        status=str(row["status"]),
        run_id=str(row["run_id"]),
        target_date=str(row["target_date"]),
        committed_count=committed_count,
        attempt_used=int(row["attempt_used"]),
        metrics_status=str(row["metrics_status"]),
        resumed=resumed,
        reason=reason,
        daily_target=DAILY_TARGET,
        initial_allocation_count=initial_allocations,
        supplemental_allocation_count=supplemental_allocations,
        shortfall_count=max(0, DAILY_TARGET - committed_count),
        actual_posted_count=actual_posted_count,
        source_failures=int(state.get("source_failures", 0)),
        error_count=error_count,
        primary_error=(dict(primary_error) if primary_error is not None else None),
        secondary_errors=secondary_errors,
    )


def _validate_plan(
    plan: DailyThemePlan,
    theme_id: str,
    *,
    allow_legacy_acquisition: bool = False,
    allow_legacy_query_fallback: bool = False,
) -> None:
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
    if not isinstance(plan.related, tuple):
        raise DailyOrchestrationError("theme_related_queue_invalid")
    if plan.acquisition_status not in {"observed", "exhausted", "deadline"}:
        raise DailyOrchestrationError("theme_acquisition_status_invalid")
    if plan.has_related_words is not None and not isinstance(
        plan.has_related_words, bool
    ):
        raise DailyOrchestrationError("theme_raw_related_word_state_invalid")
    if plan.acquisition_ref is not None and not _SAFE_ACQUISITION_REF.fullmatch(
        plan.acquisition_ref
    ):
        raise DailyOrchestrationError("theme_acquisition_ref_invalid")
    if plan.acquisition_status == "observed":
        if (
            not isinstance(plan.snapshot_ref, str)
            or not plan.snapshot_ref
            or len(plan.related) != len(plan.keyword_refs)
            or (plan.acquisition_ref is None and not allow_legacy_acquisition)
            or (
                plan.fallback is None
                and plan.has_related_words is not True
                and not allow_legacy_query_fallback
            )
            or (
                plan.fallback is not None
                and plan.has_related_words is True
                and not allow_legacy_query_fallback
            )
        ):
            raise DailyOrchestrationError("theme_snapshot_or_related_queue_missing")
    else:
        if (
            plan.snapshot_ref is not None
            or plan.acquisition_ref is None
            or plan.keyword_refs
            or plan.related
        ):
            raise DailyOrchestrationError("failed_theme_plan_claims_keyword_data")
        if not _SAFE_ACQUISITION_REF.fullmatch(plan.acquisition_ref):
            raise DailyOrchestrationError("theme_acquisition_ref_invalid")
        if plan.acquisition_status == "exhausted" and plan.fallback is None:
            raise DailyOrchestrationError("exhausted_theme_plan_missing_fallback")
        if plan.acquisition_status == "exhausted" and plan.has_related_words is True:
            raise DailyOrchestrationError("exhausted_theme_plan_claims_related_words")
        if plan.acquisition_status == "deadline" and plan.fallback is not None:
            raise DailyOrchestrationError("deadline_theme_plan_has_fallback")
        if (
            plan.acquisition_status == "deadline"
            and plan.has_related_words is not None
        ):
            raise DailyOrchestrationError("deadline_theme_plan_claims_related_state")
    searches = (*plan.related, *((plan.fallback,) if plan.fallback is not None else ()))
    seen = set()
    for search in searches:
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
    if plan.fallback is not None and plan.fallback.kind != "theme_fallback":
        raise DailyOrchestrationError("theme_fallback_kind_invalid")
    if any(item.kind != "related" for item in plan.related):
        raise DailyOrchestrationError("theme_search_queue_order_invalid")
    if (
        plan.acquisition_status == "observed"
        and tuple(item.selected_word_ref for item in plan.related) != plan.keyword_refs
    ):
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
        "fallback": (
            search_to_dict(plan.fallback) if plan.fallback is not None else None
        ),
        "snapshot_ref": plan.snapshot_ref,
        "acquisition_status": plan.acquisition_status,
        "acquisition_ref": plan.acquisition_ref,
        "rules_version": plan.rules_version,
        "has_related_words": plan.has_related_words,
    }


def _plan_from_dict(
    value: object,
    *,
    allow_legacy_acquisition: bool = False,
    allow_legacy_query_fallback: bool = False,
) -> DailyThemePlan:
    legacy_current_fields = {
        "theme_id",
        "keyword_refs",
        "related",
        "fallback",
        "snapshot_ref",
        "acquisition_status",
        "acquisition_ref",
        "rules_version",
    }
    current_fields = legacy_current_fields | {"has_related_words"}
    legacy_fields = legacy_current_fields | {"legacy_acquisition"}
    legacy_current_fields_with_migration = current_fields | {"legacy_acquisition"}
    if not isinstance(value, dict) or not (
        set(value) == current_fields
        or (
            allow_legacy_acquisition
            and set(value) == legacy_fields
            and value.get("legacy_acquisition") is True
        )
        or (
            allow_legacy_query_fallback
            and set(value) == legacy_current_fields
        )
        or (
            allow_legacy_acquisition
            and allow_legacy_query_fallback
            and set(value) == legacy_current_fields_with_migration
            and value.get("legacy_acquisition") is True
        )
    ):
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
    fallback = (
        search_from_dict(value["fallback"]) if value["fallback"] is not None else None
    )
    plan = DailyThemePlan(
        theme_id=value["theme_id"],
        keyword_refs=tuple(value["keyword_refs"]),
        related=tuple(search_from_dict(item) for item in value["related"]),
        fallback=fallback,
        snapshot_ref=value["snapshot_ref"],
        acquisition_status=value["acquisition_status"],
        acquisition_ref=value["acquisition_ref"],
        rules_version=value["rules_version"],
        has_related_words=value.get("has_related_words"),
    )
    _validate_plan(
        plan,
        plan.theme_id,
        allow_legacy_acquisition=allow_legacy_acquisition,
        allow_legacy_query_fallback=allow_legacy_query_fallback,
    )
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


def _reconcile_pending_theme_tasks(
    state: dict[str, Any],
    *,
    plan: DailyThemePlan,
    event_kind: str,
) -> None:
    tasks = state.get("tasks")
    cursor = state.get("task_cursor")
    if (
        not isinstance(tasks, list)
        or isinstance(cursor, bool)
        or not isinstance(cursor, int)
        or not 0 <= cursor <= len(tasks)
    ):
        raise DailyOrchestrationError("daily_checkpoint_cursor_invalid")
    processed = tasks[:cursor]
    pending = tasks[cursor:]
    reconciled: list[dict[str, Any]] = []
    for raw_task in pending:
        if not isinstance(raw_task, dict):
            raise DailyOrchestrationError("daily_checkpoint_task_invalid")
        is_theme_fallback = (
            raw_task.get("phase") == event_kind
            and raw_task.get("theme_id") == plan.theme_id
            and raw_task.get("kind") == "theme_fallback"
        )
        if is_theme_fallback and plan.fallback is None:
            continue
        if is_theme_fallback and plan.fallback is not None:
            raw_task = {
                **raw_task,
                "query": plan.fallback.query,
                "task_id": plan.fallback.task_id,
                "parent_refs": list(plan.fallback.parent_refs),
                "level": plan.fallback.level,
                "kind": plan.fallback.kind,
                "query_kind": plan.fallback.kind,
                "keyword_snapshot_ref": None,
                "selected_word_ref": None,
            }
        reconciled.append(raw_task)
    state["tasks"] = processed + [
        {**task, "task_number": cursor + offset + 1}
        for offset, task in enumerate(reconciled)
    ]


def _normalize_theme_query_fallback(
    plan: DailyThemePlan,
    *,
    has_related_words: bool | None,
    query_word: str | None,
    event_kind: str,
    ordinal: int,
) -> DailyThemePlan:
    if plan.acquisition_status == "deadline":
        return replace(plan, fallback=None, has_related_words=None)
    has_raw_words = bool(has_related_words)
    if has_raw_words:
        return replace(plan, fallback=None, has_related_words=True)
    fallback_query = query_word or (
        plan.fallback.query if plan.fallback is not None else None
    )
    if not isinstance(fallback_query, str) or not fallback_query.strip():
        raise DailyOrchestrationError("theme_raw_zero_query_missing")
    fallback_task_id = (
        plan.fallback.task_id
        if plan.fallback is not None
        else f"{plan.theme_id}-fallback-{event_kind}-{ordinal:02d}"
    )
    return replace(
        plan,
        fallback=DailySearchPlan(
            fallback_task_id,
            plan.theme_id,
            fallback_query,
            (plan.theme_id,),
            "theme_fallback",
            "theme_fallback",
        ),
        has_related_words=False,
    )


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
        legacy_query_fallback = True
        legacy_plan = (
            state.get("migrated_from_schema_version") in {2, 3}
            and isinstance(raw, dict)
            and raw.get("legacy_acquisition") is True
        )
        plan = _plan_from_dict(
            raw,
            allow_legacy_acquisition=legacy_plan,
            allow_legacy_query_fallback=legacy_query_fallback,
        )
        _validate_plan(
            plan,
            theme_id,
            allow_legacy_acquisition=legacy_plan,
            allow_legacy_query_fallback=legacy_query_fallback,
        )
        has_related_words, query_word = _validate_plan_snapshot(
            connection,
            plan,
            run_id=run_id,
            settings_version=settings_version,
            seed=seed,
            event_kind=event_kind,
            ordinal=ordinal,
            allow_legacy_acquisition=legacy_plan,
            allow_legacy_query_fallback=legacy_query_fallback,
        )
        normalized_plan = _normalize_theme_query_fallback(
            plan,
            has_related_words=has_related_words,
            query_word=query_word,
            event_kind=event_kind,
            ordinal=ordinal,
        )
        if normalized_plan != plan:
            _reconcile_pending_theme_tasks(
                state,
                plan=normalized_plan,
                event_kind=event_kind,
            )
            state["plans"][key] = _plan_to_dict(normalized_plan)
            _save_state(connection, run_id, state)
        plan = normalized_plan
        _validate_plan(plan, theme_id, allow_legacy_acquisition=legacy_plan)
        _validate_plan_snapshot(
            connection,
            plan,
            run_id=run_id,
            settings_version=settings_version,
            seed=seed,
            event_kind=event_kind,
            ordinal=ordinal,
            allow_legacy_acquisition=legacy_plan,
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
    _validate_plan(
        plan,
        theme_id,
        allow_legacy_query_fallback=plan.has_related_words is None
        or isinstance(plan.has_related_words, bool),
    )
    has_related_words, query_word = _validate_plan_snapshot(
        connection,
        plan,
        run_id=run_id,
        settings_version=settings_version,
        seed=seed,
        event_kind=event_kind,
        ordinal=ordinal,
        allow_legacy_query_fallback=plan.has_related_words is None
        or isinstance(plan.has_related_words, bool),
    )
    plan = _normalize_theme_query_fallback(
        plan,
        has_related_words=has_related_words,
        query_word=query_word,
        event_kind=event_kind,
        ordinal=ordinal,
    )
    _validate_plan(plan, theme_id)
    _validate_plan_snapshot(
        connection,
        plan,
        run_id=run_id,
        settings_version=settings_version,
        seed=seed,
        event_kind=event_kind,
        ordinal=ordinal,
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


def _reconcile_saved_theme_plans(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    settings_version: str,
    seed: str,
    state: dict[str, Any],
    planner: ThemePlanner,
) -> None:
    """Migrate only pending legacy fallback tasks before a resumed search."""

    plans = state.get("plans")
    if not isinstance(plans, dict):
        raise DailyOrchestrationError("daily_checkpoint_work_invalid")
    for key, raw_plan in list(plans.items()):
        if not isinstance(key, str) or not isinstance(raw_plan, dict):
            raise DailyOrchestrationError("saved_theme_plan_fields_invalid")
        match = re.fullmatch(r"(initial|additional):(\d{2})", key)
        theme_id = raw_plan.get("theme_id")
        if match is None or not isinstance(theme_id, str):
            raise DailyOrchestrationError("saved_theme_plan_identity_invalid")
        _get_plan(
            connection,
            run_id=run_id,
            settings_version=settings_version,
            seed=seed,
            event_kind=match.group(1),
            ordinal=int(match.group(2)),
            theme_id=theme_id,
            state=state,
            planner=planner,
        )


def _theme_query_matches_fallback(connection, plan, query, settings_version) -> bool:
    if plan.fallback is None:
        return True
    if query == plan.fallback.query:
        # Preserve the existing full-query contract for saved legacy plans.
        return True
    row = connection.execute(
        "SELECT themes_json FROM settings_version WHERE version=?",
        (settings_version,),
    ).fetchone()
    try:
        themes = json.loads(row[0]) if row else []
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(themes, list):
        return False
    names = [
        item.get("name")
        for item in themes
        if isinstance(item, dict) and item.get("theme_id") == plan.theme_id
    ]
    if not names:
        return False
    if len(names) != 1 or not isinstance(names[0], str) or not names[0].strip():
        return False
    name = names[0].strip()
    return plan.fallback.query == name and (
        query == name or query in theme_query_candidates(name)
    )


def _validate_plan_snapshot(
    connection,
    plan,
    *,
    run_id,
    settings_version,
    seed,
    event_kind,
    ordinal,
    allow_legacy_acquisition=False,
    allow_legacy_query_fallback=False,
) -> tuple[bool | None, str | None]:
    acquisition = None
    query_word = None
    if plan.acquisition_ref is None and not (
        allow_legacy_acquisition and plan.acquisition_status == "observed"
    ):
        raise DailyOrchestrationError("theme_acquisition_state_not_adopted")
    if plan.acquisition_ref is not None:
        row = connection.execute(
            "SELECT run_id,settings_version,event_kind,ordinal,theme_id,query_word,seed,"
            "status,attempts_used,snapshot_id FROM trends_acquisition "
            "WHERE acquisition_id=?",
            (plan.acquisition_ref,),
        ).fetchone()
        if (
            row is None
            or tuple(row[:5])
            != (run_id, settings_version, event_kind, ordinal, plan.theme_id)
            or row["seed"] != seed
            or not isinstance(row["query_word"], str)
            or not row["query_word"].strip()
            or not _theme_query_matches_fallback(
                connection, plan, row["query_word"], settings_version
            )
            or (
                plan.acquisition_status == "observed"
                and (
                    row["status"] not in {"observed", "normal_zero"}
                    or row["snapshot_id"] != plan.snapshot_ref
                    or not 1 <= row["attempts_used"] <= 5
                )
            )
            or (
                plan.acquisition_status != "observed"
                and (
                    row["status"] != plan.acquisition_status
                    or row["snapshot_id"] is not None
                )
            )
            or (plan.acquisition_status == "exhausted" and row["attempts_used"] != 5)
            or (
                plan.acquisition_status == "deadline"
                and not 0 <= row["attempts_used"] <= 5
            )
        ):
            raise DailyOrchestrationError("theme_acquisition_state_not_adopted")
        query_word = str(row["query_word"])
        attempt_rows = connection.execute(
            "SELECT attempt_no,status,error_code FROM trends_acquisition_attempt "
            "WHERE acquisition_id=? ORDER BY attempt_no",
            (plan.acquisition_ref,),
        ).fetchall()
        if [item[0] for item in attempt_rows] != list(
            range(1, row["attempts_used"] + 1)
        ):
            raise DailyOrchestrationError(
                "theme_acquisition_attempt_history_incomplete"
            )
        if plan.acquisition_status == "observed":
            if (
                not attempt_rows
                or attempt_rows[-1][1] != "response_ready"
                or any(
                    item[1] not in {"unavailable", "interrupted"}
                    and not (
                        item[1] == "processing_error"
                        and item[2]
                        in {"query_widget_contract_mismatch", "query_widget_ambiguous"}
                    )
                    for item in attempt_rows[:-1]
                )
            ):
                raise DailyOrchestrationError("theme_acquisition_state_not_adopted")
            acquisition = row
        else:
            allowed_attempt_states = {"unavailable", "interrupted"}
            if plan.acquisition_status == "deadline" and attempt_rows:
                if attempt_rows[-1][1] == "deadline":
                    allowed_attempt_states.add("deadline")
            if any(
                item[1] not in allowed_attempt_states
                and not (
                    item[1] == "processing_error"
                    and item[2]
                    in {"query_widget_contract_mismatch", "query_widget_ambiguous"}
                )
                for item in attempt_rows
            ):
                error = (
                    "theme_acquisition_exhaustion_unverified"
                    if plan.acquisition_status == "exhausted"
                    else "theme_acquisition_deadline_unverified"
                )
                raise DailyOrchestrationError(error)
            return (
                False if plan.acquisition_status == "exhausted" else None,
                query_word,
            )
    if plan.snapshot_ref is None or (acquisition is None and plan.fallback is None):
        raise DailyOrchestrationError("theme_snapshot_or_fallback_missing")
    request = TrendsRequest(
        plan.snapshot_ref,
        run_id,
        settings_version,
        query_word if query_word is not None else plan.fallback.query,
        range="3m",
    )
    saved = load_grouped_snapshot(connection, plan.snapshot_ref, request=request)
    if (
        saved is None
        or saved.decisions is None
        or saved.snapshot.status != "observed"
        or saved.seed != seed
    ):
        raise DailyOrchestrationError("theme_plan_snapshot_not_adopted")
    if acquisition is not None and (
        (acquisition["status"] == "normal_zero" and bool(saved.snapshot.words))
        or (acquisition["status"] == "observed" and not saved.snapshot.words)
    ):
        raise DailyOrchestrationError("theme_acquisition_zero_status_mismatch")
    if tuple(item.word_id for item in saved.selected) != plan.keyword_refs or tuple(
        item.raw for item in saved.selected
    ) != tuple(item.query for item in plan.related):
        raise DailyOrchestrationError("theme_plan_raw_or_order_changed")
    has_related_words = bool(saved.snapshot.words)
    if (
        plan.has_related_words is not None
        and plan.has_related_words is not has_related_words
    ):
        raise DailyOrchestrationError("theme_raw_related_word_state_mismatch")
    if not allow_legacy_query_fallback:
        if has_related_words and plan.fallback is not None:
            raise DailyOrchestrationError("theme_raw_hits_have_parent_fallback")
        if not has_related_words:
            if plan.fallback is None:
                raise DailyOrchestrationError("theme_raw_zero_missing_query_fallback")
            if query_word is not None and plan.fallback.query != query_word:
                raise DailyOrchestrationError("theme_raw_zero_fallback_query_mismatch")
    return has_related_words, query_word


def _make_tasks(
    plans: Sequence[DailyThemePlan], *, phase: str, first_number: int
) -> list[dict[str, Any]]:
    searches = [
        search
        for plan in plans
        for search in (
            *plan.related,
            *((plan.fallback,) if plan.fallback is not None else ()),
        )
    ]
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
        PER_THEME_TARGET,
        max(0, DAILY_TARGET - _selection_count(connection, run_id, state)),
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


def _committed_offer_themes(
    connection: sqlite3.Connection, run_id: str, state: Mapping[str, Any]
) -> dict[str, str]:
    assignments = state.get("offer_theme_ids", {})
    rows = connection.execute(
        "SELECT DISTINCT draft.offer_key, offer.main_theme_id "
        "FROM draft JOIN offer USING (offer_key) WHERE draft.run_id=?",
        (run_id,),
    ).fetchall()
    return {
        str(row["offer_key"]): str(
            assignments.get(str(row["offer_key"])) or row["main_theme_id"]
        )
        for row in rows
    }


def _active_selected_entries(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    manifest = state.get("selected_products", [])
    if not isinstance(manifest, list):
        raise DailyOrchestrationError("daily_selection_manifest_invalid")
    return [
        item
        for item in manifest
        if isinstance(item, dict)
        and item.get("status")
        in {"selected", "ocr_in_progress", "ocr_complete", "ocr_partial", "copy_in_progress"}
    ]


def _selection_count(
    connection: sqlite3.Connection,
    run_id: str,
    state: Mapping[str, Any],
    *,
    theme_id: str | None = None,
) -> int:
    committed = _committed_offer_themes(connection, run_id, state)
    offers = set(committed)
    themes = dict(committed)
    for entry in _active_selected_entries(state):
        offer_key = entry.get("offer_key")
        selected_theme = entry.get("theme_id")
        if isinstance(offer_key, str) and isinstance(selected_theme, str):
            offers.add(offer_key)
            themes.setdefault(offer_key, selected_theme)
    if theme_id is None:
        return len(offers)
    return sum(themes.get(offer_key) == theme_id for offer_key in offers)


def _sync_committed_selection_manifest(
    connection: sqlite3.Connection, run_id: str, state: dict[str, Any]
) -> None:
    from automation.inago.product_page_inputs import selection_manifest

    manifest = selection_manifest(state)
    by_offer = {
        entry.get("offer_key"): entry
        for entry in manifest
        if isinstance(entry.get("offer_key"), str)
    }
    rows = connection.execute(
        "SELECT DISTINCT offer.offer_key, offer.shop_id, offer.item_id, offer.main_theme_id "
        "FROM draft JOIN offer USING (offer_key) WHERE draft.run_id=?",
        (run_id,),
    ).fetchall()
    next_order = int(state.get("next_selection_order", 1))
    for row in rows:
        offer_key = str(row["offer_key"])
        theme_id = state.setdefault("offer_theme_ids", {}).get(
            offer_key, str(row["main_theme_id"])
        )
        state["offer_theme_ids"][offer_key] = theme_id
        entry = by_offer.get(offer_key)
        if entry is None:
            entry = {
                "offer_key": offer_key,
                "run_id": run_id,
                "theme_id": theme_id,
                "selection_order": next_order,
                "shop_id": str(row["shop_id"]),
                "item_id": str(row["item_id"]),
                "page_ref": None,
                "source_evidence_ref": None,
                "status": "committed",
            }
            manifest.append(entry)
            by_offer[offer_key] = entry
            next_order += 1
        else:
            entry.update(
                run_id=run_id,
                theme_id=theme_id,
                shop_id=str(row["shop_id"]),
                item_id=str(row["item_id"]),
                status="committed",
            )
    orders = [
        int(entry["selection_order"])
        for entry in manifest
        if isinstance(entry.get("selection_order"), int)
        and not isinstance(entry.get("selection_order"), bool)
    ]
    if orders:
        next_order = max(next_order, max(orders) + 1)
    state["next_selection_order"] = next_order


def _theme_draft_count(
    connection: sqlite3.Connection,
    run_id: str,
    theme_id: str,
    *,
    state: Mapping[str, Any] | None = None,
) -> int:
    if state is not None:
        return _selection_count(connection, run_id, state, theme_id=theme_id)
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
            "SELECT main_theme_id,facts_json,last_seen FROM offer WHERE offer_key=?",
            (offer_key,),
        ).fetchone()
        if existing is not None:
            if not offer_needs_name_observation(existing[0], existing[1]):
                continue
            if existing[0] != LEGACY_UNASSIGNED_THEME_ID:
                last_seen_day = _jst_date(existing[2])
                observed_day = _jst_date(row["observed_at"])
                if last_seen_day >= observed_day:
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
        expected_theme_id = (
            theme_id
            if existing is None or existing[0] == LEGACY_UNASSIGNED_THEME_ID
            else existing[0]
        )
        persisted = connection.execute(
            "SELECT shop_id, item_id, main_theme_id FROM offer WHERE offer_key=?",
            (offer_key,),
        ).fetchone()
        if (
            persisted is None
            or f"{persisted['shop_id']}:{persisted['item_id']}" != offer_key
            or persisted["main_theme_id"] != expected_theme_id
        ):
            raise DailyOrchestrationError("offer_intake_identity_not_persisted")


def _room_product_fact(ports: DailyPorts, evidence_ref: str) -> Mapping[str, Any] | None:
    getter = getattr(ports.room_port, "product_fact", None)
    if not callable(getter):
        return None
    value = getter(evidence_ref)
    return value if isinstance(value, Mapping) else None


def _product_page_pipeline_enabled(
    ports: DailyPorts, evidence_refs: Sequence[str]
) -> bool:
    if ports.product_page_ocr is not None:
        return True
    for evidence_ref in evidence_refs:
        fact = _room_product_fact(ports, evidence_ref)
        if isinstance(fact, Mapping) and isinstance(
            fact.get("product_page_identity"), Mapping
        ):
            return True
    return False


def _offer_identity(
    connection: sqlite3.Connection, offer_key: str
) -> tuple[str, str] | None:
    row = connection.execute(
        "SELECT shop_id, item_id FROM offer WHERE offer_key=?", (offer_key,)
    ).fetchone()
    if row is None:
        return None
    return str(row["shop_id"]), str(row["item_id"])


def _attempt_product_candidates(
    connection: sqlite3.Connection,
    run_id: str,
    attempt_id: str,
    identities: Mapping[str, str | None],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    from automation.inago.product_page_inputs import product_page_ref

    rows = connection.execute(
        "SELECT source_key, evidence_ref, item_url FROM source_post WHERE attempt_id=?",
        (attempt_id,),
    ).fetchall()
    evidence_by_source = {str(row["source_key"]): str(row["evidence_ref"]) for row in rows}
    candidates: dict[str, dict[str, str]] = {}
    for source_key, offer_key in identities.items():
        if offer_key is None:
            continue
        identity = _offer_identity(connection, offer_key)
        if identity is None:
            source_row = next(
                (row for row in rows if str(row["source_key"]) == source_key), None
            )
            saved_url = source_row["item_url"] if source_row is not None else None
            parsed = identify_offer_from_saved_url(saved_url)
            if parsed.status != "identified" or parsed.offer_key != offer_key:
                continue
            shop_id, item_id = offer_key.split(":", maxsplit=1)
        else:
            shop_id, item_id = identity
        candidates[offer_key] = {
            "offer_key": offer_key,
            "shop_id": shop_id,
            "item_id": item_id,
            "page_ref": product_page_ref(run_id, shop_id, item_id),
        }
    return list(candidates.values()), evidence_by_source


def _cleanup_unselected_product_inputs(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    state: dict[str, Any],
    candidates: Sequence[Mapping[str, str]],
    data_root: Path,
    target_date: str,
    clock: Clock,
) -> None:
    from automation.inago.product_page_inputs import (
        cleanup_unselected_product_page_folders,
        load_product_page_inputs,
        selection_manifest,
    )

    manifest = selection_manifest(state)
    keep = {
        str(entry["offer_key"])
        for entry in manifest
        if isinstance(entry.get("offer_key"), str)
    }
    keep.update(_committed_offer_themes(connection, run_id, state))
    image_counts: dict[str, int] = {}
    for candidate in candidates:
        offer_key = candidate.get("offer_key")
        if not isinstance(offer_key, str) or offer_key in keep:
            continue
        try:
            page = load_product_page_inputs(
                data_root,
                run_id,
                str(candidate.get("shop_id", "")),
                str(candidate.get("item_id", "")),
                expected_ref=(
                    candidate.get("page_ref")
                    if isinstance(candidate.get("page_ref"), str)
                    else None
                ),
            )
        except (OSError, ValueError, json.JSONDecodeError):
            page = None
        if isinstance(page, Mapping) and isinstance(page.get("images"), list):
            image_counts[offer_key] = len(page["images"])
    result = cleanup_unselected_product_page_folders(
        data_root, run_id, candidates, keep
    )
    deleted = {item.get("offer_key") for item in result.get("deleted", [])}
    failures = result.get("failed", [])
    failed_by_offer = {
        item.get("offer_key"): item
        for item in failures
        if isinstance(item, Mapping)
    }
    for candidate in candidates:
        offer_key = candidate.get("offer_key")
        page_ref = candidate.get("page_ref")
        if (
            not isinstance(offer_key, str)
            or offer_key in keep
            or not isinstance(page_ref, str)
        ):
            continue
        failure = failed_by_offer.get(offer_key)
        _trace_product_stage(
            data_root,
            run_id=run_id,
            target_date=target_date,
            offer_key=offer_key,
            page_ref=page_ref,
            operation="product_unselected_cleanup",
            phase="error" if failure else "status",
            clock=clock,
            status=("failed" if failure else "deleted" if offer_key in deleted else "not_present"),
            image_count=image_counts.get(offer_key),
            error_code=("product_unselected_cleanup_failed" if failure else None),
            error_class=(failure.get("error_class") if failure else None),
        )
    if not failures:
        return
    recorded = state.setdefault("product_page_cleanup_failures", [])
    known = {
        (item.get("offer_key"), item.get("error_class"))
        for item in recorded
        if isinstance(item, dict)
    }
    for item in failures:
        identity = (item.get("offer_key"), item.get("error_class"))
        if identity in known:
            continue
        known.add(identity)
        recorded.append({"offer_key": identity[0], "error_class": identity[1]})
        state["error_count"] = int(state.get("error_count", 0)) + 1
        _append_error(
            state,
            "product_page_cleanup",
            "unselected_folder_cleanup_failed",
            references={"run_id": run_id, "offer_key": str(identity[0])},
        )


def _recover_unselected_product_folder_cleanup(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    state: dict[str, Any],
    data_root: Path,
    target_date: str,
    clock: Clock,
) -> None:
    from automation.inago.product_page_inputs import (
        cleanup_unselected_product_page_folders,
        product_page_ref,
        selection_manifest,
    )

    failures = state.get("product_page_cleanup_failures", [])
    if not isinstance(failures, list) or not failures:
        return
    keep = {
        str(entry["offer_key"])
        for entry in selection_manifest(state)
        if isinstance(entry.get("offer_key"), str)
    }
    keep.update(_committed_offer_themes(connection, run_id, state))
    candidates: list[dict[str, str]] = []
    for failure in failures:
        offer_key = failure.get("offer_key") if isinstance(failure, Mapping) else None
        if not isinstance(offer_key, str) or offer_key in keep:
            continue
        offer = connection.execute(
            "SELECT shop_id,item_id FROM offer WHERE offer_key=?", (offer_key,)
        ).fetchone()
        if offer is None:
            continue
        shop_id, item_id = str(offer["shop_id"]), str(offer["item_id"])
        candidates.append(
            {
                "offer_key": offer_key,
                "shop_id": shop_id,
                "item_id": item_id,
                "page_ref": product_page_ref(run_id, shop_id, item_id),
            }
        )
    if not candidates:
        return
    result = cleanup_unselected_product_page_folders(
        data_root, run_id, candidates, keep
    )
    failed_offers = {
        item.get("offer_key")
        for item in result.get("failed", [])
        if isinstance(item, Mapping)
    }
    resolved_offers = {
        item.get("offer_key")
        for item in result.get("deleted", [])
        if isinstance(item, Mapping)
    }
    candidate_by_offer = {item["offer_key"]: item for item in candidates}
    errors = state.setdefault("errors", [])
    for offer_key in resolved_offers - failed_offers:
        candidate = candidate_by_offer.get(offer_key)
        if candidate is None:
            continue
        for index in range(len(errors) - 1, -1, -1):
            error = errors[index]
            references = error.get("references") if isinstance(error, dict) else None
            if (
                isinstance(error, dict)
                and error.get("phase") == "product_page_cleanup"
                and error.get("code") == "unselected_folder_cleanup_failed"
                and isinstance(references, dict)
                and references.get("offer_key") == offer_key
            ):
                resolved_error = errors.pop(index)
                state["error_count"] = max(0, int(state.get("error_count", 0)) - 1)
                _archive_resolved_error(
                    state,
                    resolved_error,
                    resolution="unselected_folder_cleanup_recovered",
                    at=_now(clock).isoformat(),
                )
                break
        _trace_product_stage(
            data_root,
            run_id=run_id,
            target_date=target_date,
            offer_key=offer_key,
            page_ref=candidate["page_ref"],
            operation="product_unselected_cleanup",
            phase="end",
            clock=clock,
            status="deleted_or_absent",
        )
    state["product_page_cleanup_failures"] = [
        failure
        for failure in failures
        if not isinstance(failure, Mapping)
        or failure.get("offer_key") not in resolved_offers - failed_offers
    ]
    primary = state.get("primary_error")
    if isinstance(primary, Mapping) and not any(
        dict(error) == dict(primary)
        for error in errors
        if isinstance(error, Mapping)
    ):
        if errors:
            state["primary_error"] = dict(errors[-1])
        else:
            state.pop("primary_error", None)


def _trace_product_stage(
    data_root: Path,
    *,
    run_id: str,
    target_date: str,
    offer_key: str,
    page_ref: str,
    operation: str,
    phase: str,
    clock: Clock,
    status: str | None = None,
    image_count: int | None = None,
    deleted_file_count: int | None = None,
    input_ref_count: int | None = None,
    error_code: str | None = None,
    error_class: str | None = None,
    failure_module: str | None = None,
    failure_function: str | None = None,
    failure_line: int | None = None,
    at: str | None = None,
) -> None:
    try:
        trace_kwargs: dict[str, Any] = {}
        if _SAFE_ACQUISITION_REF.fullmatch(offer_key):
            trace_kwargs["task_id"] = offer_key
        session = TraceSession(
            TraceWriter(data_root, run_id, target_date, clock=clock),
            component="product_page",
            request_id=page_ref,
            clock=clock,
            **trace_kwargs,
        )
        fields: dict[str, Any] = {
            "event_kind": "product_page_pipeline",
            "status": status,
            "image_count": image_count,
            "deleted_file_count": deleted_file_count,
            "input_ref_count": input_ref_count,
            "code_sha256": _product_pipeline_code_sha256(),
        }
        if error_code:
            fields["primary_error_code"] = error_code
        if error_class:
            fields["error_class"] = error_class
        if failure_module:
            fields["failure_module"] = failure_module
        if failure_function:
            fields["failure_function"] = failure_function
        if failure_line:
            fields["failure_line"] = failure_line
        session.record_stage(operation, phase=phase, at=at, **fields)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "product_page_pipeline_trace_failed error_type=%s",
            type(exc).__name__,
        )


@lru_cache(maxsize=1)
def _product_pipeline_code_sha256() -> str | None:
    paths = (
        "orchestrator.py",
        "product_page_inputs.py",
        "product_ocr.py",
        "production_runtime.py",
        "prompt_builder.py",
        "ai_runner.py",
        "trace.py",
    )
    digest = hashlib.sha256()
    base = Path(__file__).resolve().parent
    try:
        for name in paths:
            digest.update(name.encode("ascii"))
            digest.update(b"\0")
            digest.update((base / name).read_bytes())
    except OSError:
        return None
    return digest.hexdigest()


def _safe_trace_frame(error: BaseException) -> dict[str, Any]:
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return {}
    frame = frames[-1]
    module = Path(frame.filename).stem
    function = frame.name
    result: dict[str, Any] = {}
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", module):
        result["failure_module"] = module
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9._:-]{0,127}", function):
        result["failure_function"] = function
    if frame.lineno > 0:
        result["failure_line"] = frame.lineno
    return result


def _cleanup_committed_product_images(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    state: dict[str, Any],
    entry: dict[str, Any],
    data_root: Path,
    clock: Clock,
) -> None:
    from automation.inago.product_page_inputs import cleanup_committed_product_images

    previous_cleanup = entry.get("image_cleanup")
    if isinstance(previous_cleanup, Mapping) and previous_cleanup.get("status") == "complete":
        return
    offer_key = entry.get("offer_key")
    shop_id = entry.get("shop_id")
    item_id = entry.get("item_id")
    page_ref = entry.get("page_ref")
    if not all(isinstance(value, str) and value for value in (offer_key, shop_id, item_id)):
        return
    if not isinstance(page_ref, str):
        entry["image_cleanup"] = {"status": "not_applicable"}
        return

    cleanup_trace = None
    try:
        trace_kwargs: dict[str, Any] = {}
        if _SAFE_ACQUISITION_REF.fullmatch(offer_key):
            trace_kwargs["task_id"] = offer_key
        cleanup_trace = TraceSession(
            TraceWriter(data_root, run_id, target_date, clock=clock),
            component="product_page",
            request_id=page_ref,
            clock=clock,
            **trace_kwargs,
        )
        cleanup_trace.record_stage(
            "product_image_cleanup",
            phase="begin",
            event_kind="product_page_cleanup",
            status="started",
            scope="cleanup",
        )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "product_page_cleanup_trace_begin_failed error_type=%s",
            type(exc).__name__,
        )
        cleanup_trace = None

    result = cleanup_committed_product_images(
        data_root,
        run_id,
        offer_key,
        shop_id,
        item_id,
        page_ref,
        now=lambda: _now(clock).isoformat(),
    )
    if cleanup_trace is not None:
        try:
            common_trace_fields = {
                "event_kind": "product_page_cleanup",
                "status": result.get("status"),
                "scope": "cleanup",
                "image_count": result.get("image_count"),
                "deleted_file_count": result.get("deleted_file_count"),
                "code_sha256": result.get("code_sha256"),
            }
            if result.get("status") == "complete":
                cleanup_trace.record_stage(
                    "product_image_cleanup", phase="end", **common_trace_fields
                )
            else:
                error_fields = {
                    **common_trace_fields,
                    "primary_error_code": "product_image_cleanup_failed",
                    "error_class": result.get("error_class"),
                    "scope": "cleanup",
                    "failure_module": result.get("failure_module"),
                    "failure_function": result.get("failure_function"),
                    "failure_line": result.get("failure_line"),
                }
                cleanup_trace.record_stage(
                    "product_image_cleanup", phase="error", **error_fields
                )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "product_page_cleanup_trace_end_failed error_type=%s",
                type(exc).__name__,
            )
    entry["image_cleanup"] = result
    failures = state.setdefault("product_image_cleanup_failures", [])
    errors = state.setdefault("errors", [])
    matching_errors = [
        error
        for error in errors
        if isinstance(error, dict)
        and error.get("phase") == "product_image_cleanup"
        and isinstance(error.get("references"), dict)
        and error["references"].get("offer_key") == offer_key
    ]
    if result.get("status") == "complete":
        if matching_errors:
            for index in range(len(errors) - 1, -1, -1):
                if errors[index] in matching_errors:
                    resolved_error = errors.pop(index)
                    state["error_count"] = max(
                        0, int(state.get("error_count", 0)) - 1
                    )
                    _archive_resolved_error(
                        state,
                        resolved_error,
                        resolution="product_image_cleanup_recovered",
                        at=str(result.get("finished_at") or _now(clock).isoformat()),
                    )
                    break
            primary = state.get("primary_error")
            if (
                isinstance(primary, dict)
                and primary.get("phase") == "product_image_cleanup"
                and isinstance(primary.get("references"), dict)
                and primary["references"].get("offer_key") == offer_key
            ):
                if errors:
                    state["primary_error"] = dict(errors[-1])
                else:
                    state.pop("primary_error", None)
        failures[:] = [
            failure
            for failure in failures
            if not isinstance(failure, dict) or failure.get("offer_key") != offer_key
        ]
        resolved = state.setdefault("resolved_product_image_cleanup_failures", [])
        if not any(
            isinstance(item, dict) and item.get("offer_key") == offer_key
            for item in resolved
        ):
            resolved.append(
                {"offer_key": offer_key, "resolved_at": result.get("finished_at")}
            )
        return

    error_class = result.get("error_class")
    if not any(
        isinstance(failure, dict) and failure.get("offer_key") == offer_key
        for failure in failures
    ):
        failures.append({"offer_key": offer_key, "error_class": error_class})
    if not matching_errors:
        state["error_count"] = int(state.get("error_count", 0)) + 1
        _append_error(
            state,
            "product_image_cleanup",
            "product_image_cleanup_failed",
            references={"run_id": run_id, "offer_key": offer_key, "artifact_ref": page_ref},
        )
        state["primary_error"] = dict(state["errors"][-1])


def _recover_committed_product_image_cleanup(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    state: dict[str, Any],
    data_root: Path,
    clock: Clock,
) -> None:
    from automation.inago.product_page_inputs import selection_manifest

    if not state.get("product_page_pipeline"):
        return
    _sync_committed_selection_manifest(connection, run_id, state)
    for entry in selection_manifest(state):
        offer_key = entry.get("offer_key")
        if not isinstance(offer_key, str):
            continue
        committed = connection.execute(
            "SELECT 1 FROM draft WHERE run_id=? AND offer_key=? LIMIT 1",
            (run_id, offer_key),
        ).fetchone()
        if committed is None:
            continue
        entry["status"] = "committed"
        committed_at = entry.get("committed_at")
        if not isinstance(committed_at, str):
            committed_at = _now(clock).isoformat()
            entry["committed_at"] = committed_at
        _resolve_product_slot_failure_on_commit(
            state,
            committed_entry=entry,
            committed_at=committed_at,
        )
        _cleanup_committed_product_images(
            connection,
            run_id=run_id,
            target_date=target_date,
            state=state,
            entry=entry,
            data_root=data_root,
            clock=clock,
        )


def _select_attempt_candidates(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    theme_id: str,
    attempt_id: str,
    evaluation: Any,
    evidence_by_source: Mapping[str, str],
    state: dict[str, Any],
    ports: DailyPorts,
    clock: Clock,
    data_root: Path,
    recovering_offer_keys: set[str] | None = None,
) -> None:
    from automation.inago.product_page_inputs import selection_manifest, upsert_selection

    _sync_committed_selection_manifest(connection, run_id, state)
    manifest = selection_manifest(state)
    capture_failures = state.setdefault("product_page_capture_failures", [])
    if not isinstance(capture_failures, list):
        raise DailyOrchestrationError("product_page_capture_failures_invalid")
    recovering_offer_keys = recovering_offer_keys or set()
    known = {
        str(entry["offer_key"])
        for entry in manifest
        if isinstance(entry.get("offer_key"), str)
    }
    known.update(
        str(item["offer_key"])
        for item in capture_failures
        if isinstance(item, dict) and isinstance(item.get("offer_key"), str)
        and item["offer_key"] not in recovering_offer_keys
    )
    pipeline_enabled = _product_page_pipeline_enabled(
        ports, tuple(evidence_by_source.values())
    )
    for representative in evaluation.eligible_representatives:
        offer_key = representative.offer_key
        if not isinstance(offer_key, str) or offer_key in known:
            continue
        if _selection_count(connection, run_id, state) >= DAILY_TARGET:
            break
        offer = connection.execute(
            "SELECT main_theme_id,shop_id,item_id FROM offer WHERE offer_key=?",
            (offer_key,),
        ).fetchone()
        if offer is None:
            state["error_count"] = int(state.get("error_count", 0)) + 1
            _append_error(state, "selection", "offer_record_missing")
            continue
        selected_theme = str(theme_id)
        if _selection_count(connection, run_id, state, theme_id=selected_theme) >= int(
            state["theme_targets"].get(selected_theme, PER_THEME_TARGET)
        ):
            continue
        source_key = str(representative.source_key)
        evidence_ref = evidence_by_source.get(source_key)
        fact = _room_product_fact(ports, evidence_ref) if evidence_ref else None
        page_identity = fact.get("product_page_identity") if fact else None
        page_ref = fact.get("product_page_ref") if fact else None
        if page_identity is not None and (
            not isinstance(page_identity, Mapping)
            or page_identity.get("run_id") != run_id
            or page_identity.get("shop_id") != str(offer["shop_id"])
            or page_identity.get("item_id") != str(offer["item_id"])
        ):
            raise DailyOrchestrationError("product_page_selection_identity_mismatch")
        if page_ref is not None and (
            not isinstance(page_ref, str)
            or not _SAFE_ACQUISITION_REF.fullmatch(page_ref)
        ):
            raise DailyOrchestrationError("product_page_selection_ref_invalid")
        page_document = None
        if pipeline_enabled and page_ref is None:
            from automation.inago.product_page_inputs import product_page_ref

            page_ref = product_page_ref(
                run_id, str(offer["shop_id"]), str(offer["item_id"])
            )
        if isinstance(page_ref, str):
            from automation.inago.product_page_inputs import load_product_page_inputs

            page_document = load_product_page_inputs(
                data_root,
                run_id,
                str(offer["shop_id"]),
                str(offer["item_id"]),
                expected_ref=page_ref,
            )
            if page_document is not None:
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=offer_key,
                    page_ref=page_ref,
                    operation="product_page_capture",
                    phase="status",
                    clock=clock,
                    status=str(page_document.get("html_state", "unknown")),
                    image_count=len(page_document.get("images", [])),
                    at=page_document.get("captured_at"),
                )
        if pipeline_enabled and (
            page_document is None
            or page_document.get("html_state") != "captured"
        ):
            if not any(
                isinstance(item, dict) and item.get("offer_key") == offer_key
                for item in capture_failures
            ):
                failure = {
                    "offer_key": offer_key,
                    "page_ref": page_ref,
                    "source_attempt_id": attempt_id,
                    "code": "product_page_capture_failed",
                    "failed_at": _now(clock).isoformat(),
                }
                if isinstance(page_document, Mapping) and isinstance(
                    page_document.get("html_error_class"), str
                ):
                    failure["error_class"] = page_document["html_error_class"]
                capture_failures.append(failure)
            known.add(offer_key)
            if isinstance(page_ref, str):
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=offer_key,
                    page_ref=page_ref,
                    operation="product_page_capture",
                    phase="error",
                    clock=clock,
                    status="capture_failed",
                    error_code="product_page_capture_failed",
                    error_class=(
                        page_document.get("html_error_class")
                        if isinstance(page_document, Mapping)
                        and isinstance(page_document.get("html_error_class"), str)
                        else None
                    ),
                )
            _save_state(connection, run_id, state)
            continue
        order = int(state.get("next_selection_order", 1))
        entry = {
            "offer_key": offer_key,
            "run_id": run_id,
            "theme_id": selected_theme,
            "selection_order": order,
            "slot_id": _product_slot_id(run_id, order),
            "shop_id": str(offer["shop_id"]),
            "item_id": str(offer["item_id"]),
            "page_ref": page_ref,
            "source_evidence_ref": evidence_ref,
            "source_attempt_id": attempt_id,
            "source_key": source_key,
            "status": "selected",
            "selected_at": _now(clock).isoformat(),
        }
        _assign_product_slot_replacement(state, entry)
        upsert_selection(manifest, entry)
        state.setdefault("offer_theme_ids", {})[offer_key] = selected_theme
        state["next_selection_order"] = order + 1
        known.add(offer_key)
        if offer_key in recovering_offer_keys:
            capture_failures[:] = [
                failure
                for failure in capture_failures
                if not (
                    isinstance(failure, dict)
                    and failure.get("offer_key") == offer_key
                    and failure.get("source_attempt_id") == attempt_id
                )
            ]
        _save_state(connection, run_id, state)
        if isinstance(page_ref, str):
            _trace_product_stage(
                data_root,
                run_id=run_id,
                target_date=target_date,
                offer_key=offer_key,
                page_ref=page_ref,
                operation="product_selection",
                phase="status",
                clock=clock,
                status="selected",
                image_count=(
                    len(page_document.get("images", []))
                    if isinstance(page_document, Mapping)
                    and isinstance(page_document.get("images"), list)
                    else None
                ),
            )


def _product_capture_source_row(
    connection: sqlite3.Connection, attempt_id: str, offer_key: str
) -> sqlite3.Row | None:
    rows = connection.execute(
        "SELECT source_key,evidence_ref,item_url,order_index FROM source_post "
        "WHERE attempt_id=? ORDER BY order_index",
        (attempt_id,),
    ).fetchall()
    for row in rows:
        identity = identify_offer_from_saved_url(row["item_url"])
        if identity.status == "identified" and identity.offer_key == offer_key:
            return row
    return None


def _task_theme_for_attempt(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    attempt_id: str,
    state: Mapping[str, Any],
) -> str | None:
    attempt = _attempt_row(connection, attempt_id)
    if attempt is None:
        return None
    global_attempt_no = int(attempt["attempt_no"])
    for task in state.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        task_number = task.get("task_number")
        theme_id = task.get("theme_id")
        if (
            isinstance(task_number, bool)
            or not isinstance(task_number, int)
            or not isinstance(theme_id, str)
        ):
            continue
        for local_attempt in range(1, global_attempt_no + 1):
            if _task_attempt_id(run_id, task_number, local_attempt) == attempt_id:
                return theme_id
    return None


def _capture_saved_product_page(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    attempt_id: str,
    offer_key: str,
    ports: DailyPorts,
    data_root: Path,
) -> Mapping[str, Any] | None:
    from automation.inago.product_page_inputs import (
        ProductPageCaptureRequest,
        load_product_page_inputs,
        product_page_ref,
    )

    capture = getattr(ports.product_page_capture, "capture_product_page_inputs", None)
    if not callable(capture):
        return None
    row = _product_capture_source_row(connection, attempt_id, offer_key)
    if row is None:
        return None
    identity = identify_offer_from_saved_url(row["item_url"])
    if identity.status != "identified" or identity.offer_key != offer_key:
        return None
    shop_id, item_id = str(identity.shop_id), str(identity.item_id)
    expected_ref = product_page_ref(run_id, shop_id, item_id)
    try:
        existing = load_product_page_inputs(
            data_root, run_id, shop_id, item_id, expected_ref=expected_ref
        )
    except (OSError, ValueError, json.JSONDecodeError):
        existing = None
    if isinstance(existing, Mapping) and existing.get("html_state") == "captured":
        return existing
    digest = hashlib.sha256(
        f"{run_id}\0{attempt_id}\0{offer_key}".encode("utf-8")
    ).hexdigest()[:32]
    request = ProductPageCaptureRequest(
        request_id=f"product-page-{digest}",
        run_id=run_id,
        attempt_id=attempt_id,
        shop_id=shop_id,
        item_id=item_id,
        item_url=str(row["item_url"]),
        row_index=int(row["order_index"]),
    )
    try:
        result = capture(request)
    except Exception:
        return None
    if (
        not isinstance(result, Mapping)
        or result.get("request_id") != request.request_id
        or result.get("run_id") != run_id
        or result.get("shop_id") != shop_id
        or result.get("item_id") != item_id
        or result.get("page_ref") != expected_ref
        or result.get("state") not in {"captured", "existing_preserved"}
    ):
        return None
    try:
        page = load_product_page_inputs(
            data_root, run_id, shop_id, item_id, expected_ref=expected_ref
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(page, Mapping) or page.get("html_state") != "captured":
        return None
    return page


def _recover_product_page_capture_failures(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    state: dict[str, Any],
    ports: DailyPorts,
    data_root: Path,
    clock: Clock,
) -> int:
    """Retry saved product URLs and reselect captured candidates without ROOM search."""

    selected_entries = state.get("selected_products")
    selected_failed_before = sum(
        1
        for entry in selected_entries
        if isinstance(entry, Mapping)
        and entry.get("status") == "failed"
        and entry.get("ocr_error_code") == "product_page_capture_failed"
    ) if isinstance(selected_entries, list) else 0
    if not callable(
        getattr(ports.product_page_capture, "capture_product_page_inputs", None)
    ):
        state["product_page_capture_recovery"] = {
            "capture_adapter_available": False,
            "selected_failed_before": selected_failed_before,
            "selected_recovered": 0,
            "selected_failed_after": selected_failed_before,
            "unselected_recovered": 0,
        }
        return 0
    from automation.inago.product_page_inputs import (
        load_product_page_inputs,
        selection_manifest,
    )

    failures = state.setdefault("product_page_capture_failures", [])
    if not isinstance(failures, list) or any(
        not isinstance(item, dict) for item in failures
    ):
        raise DailyOrchestrationError("product_page_capture_failures_invalid")
    manifest = selection_manifest(state)
    recovered_selected = 0
    for entry in manifest:
        if (
            entry.get("status") != "failed"
            or entry.get("ocr_error_code") != "product_page_capture_failed"
        ):
            continue
        attempt_id = entry.get("source_attempt_id")
        offer_key = entry.get("offer_key")
        if not isinstance(attempt_id, str) or not isinstance(offer_key, str):
            continue
        page = _capture_saved_product_page(
            connection,
            run_id=run_id,
            attempt_id=attempt_id,
            offer_key=offer_key,
            ports=ports,
            data_root=data_root,
        )
        if page is None:
            continue
        recovered_page_ref = page.get("page_ref")
        if (
            not isinstance(recovered_page_ref, str)
            or not _SAFE_ACQUISITION_REF.fullmatch(recovered_page_ref)
        ):
            continue
        entry["page_ref"] = recovered_page_ref
        entry["status"] = "selected"
        for key in (
            "ocr_state",
            "ocr_error_code",
            "ocr_images",
            "ocr_ref",
            "ocr_completed_at",
        ):
            entry.pop(key, None)
        failures[:] = [
            failure
            for failure in failures
            if failure.get("offer_key") != offer_key
        ]
        recovered_selected += 1
        _save_state(connection, run_id, state)

    remaining_total = max(0, DAILY_TARGET - _selection_count(connection, run_id, state))
    recovered_by_attempt: dict[str, list[Representative]] = {}
    eligible_failures: list[tuple[int, int, str, str, str]] = []
    attempt_order: dict[str, int] = {}
    for failure in failures:
        attempt_id = failure.get("source_attempt_id")
        offer_key = failure.get("offer_key")
        if not isinstance(attempt_id, str) or not isinstance(offer_key, str):
            continue
        attempt = _attempt_row(connection, attempt_id)
        row = _product_capture_source_row(connection, attempt_id, offer_key)
        theme_id = _task_theme_for_attempt(
            connection, run_id=run_id, attempt_id=attempt_id, state=state
        )
        if attempt is None or row is None or theme_id is None:
            continue
        attempt_order[attempt_id] = int(attempt["attempt_no"])
        eligible_failures.append(
            (
                int(attempt["attempt_no"]),
                int(row["order_index"]),
                attempt_id,
                offer_key,
                theme_id,
            )
        )
    eligible_failures.sort()
    theme_remaining = {
        str(theme): max(
            0,
            int(target)
            - _selection_count(connection, run_id, state, theme_id=str(theme)),
        )
        for theme, target in state.get("theme_targets", {}).items()
        if isinstance(target, int) and not isinstance(target, bool)
    }
    recovered_unselected: set[str] = set()
    for _attempt_order, _row_order, attempt_id, offer_key, theme_id in eligible_failures:
        if remaining_total <= 0 or theme_remaining.get(theme_id, 0) <= 0:
            continue
        if any(
            entry.get("offer_key") == offer_key
            for entry in _active_selected_entries(state)
        ):
            continue
        page = _capture_saved_product_page(
            connection,
            run_id=run_id,
            attempt_id=attempt_id,
            offer_key=offer_key,
            ports=ports,
            data_root=data_root,
        )
        if page is None:
            continue
        failure = next(
            (
                item
                for item in failures
                if item.get("offer_key") == offer_key
                and item.get("source_attempt_id") == attempt_id
            ),
            None,
        )
        if failure is None:
            continue
        offer = _offer_identity(connection, offer_key)
        if offer is None:
            continue
        source_row = _product_capture_source_row(connection, attempt_id, offer_key)
        if source_row is None:
            continue
        recovered_by_attempt.setdefault(attempt_id, []).append(
            Representative(
                offer_key=offer_key,
                source_key=str(source_row["source_key"]),
                likes=None,
                image_ref=None,
                order_index=int(source_row["order_index"]),
            )
        )
        recovered_unselected.add(offer_key)
        remaining_total -= 1
        theme_remaining[theme_id] = theme_remaining.get(theme_id, 0) - 1

    from automation.inago.product_page_inputs import load_product_page_inputs

    for attempt_id, recovered in sorted(
        recovered_by_attempt.items(), key=lambda item: attempt_order.get(item[0], 0)
    ):
        theme_id = _task_theme_for_attempt(
            connection, run_id=run_id, attempt_id=attempt_id, state=state
        )
        if theme_id is None:
            continue
        evidence_rows = connection.execute(
            "SELECT source_key,evidence_ref FROM source_post WHERE attempt_id=?",
            (attempt_id,),
        ).fetchall()
        evidence_by_source = {
            str(row["source_key"]): str(row["evidence_ref"]) for row in evidence_rows
        }
        candidates, _ = _attempt_product_candidates(
            connection,
            run_id,
            attempt_id,
            _persisted_source_identities(connection, attempt_id),
        )
        evaluation = SelectionResult(
            seed="saved-product-page-recovery",
            observed_posts=len(recovered),
            decisions=(),
            representatives=tuple(recovered),
            eligible_representatives=tuple(recovered),
        )
        _select_attempt_candidates(
            connection,
            run_id=run_id,
            target_date=target_date,
            theme_id=theme_id,
            attempt_id=attempt_id,
            evaluation=evaluation,
            evidence_by_source=evidence_by_source,
            state=state,
            ports=ports,
            clock=clock,
            data_root=data_root,
            recovering_offer_keys={
                representative.offer_key for representative in recovered
            },
        )
        _cleanup_unselected_product_inputs(
            connection,
            run_id=run_id,
            state=state,
            candidates=[
                candidate
                for candidate in candidates
                if candidate.get("offer_key") in recovered_unselected
            ],
            data_root=data_root,
            target_date=target_date,
            clock=clock,
        )
    selected_failed_after = sum(
        1
        for entry in manifest
        if entry.get("status") == "failed"
        and entry.get("ocr_error_code") == "product_page_capture_failed"
    )
    unselected_recovered_count = len(recovered_unselected)
    state["product_page_capture_recovery"] = {
        "capture_adapter_available": True,
        "selected_failed_before": selected_failed_before,
        "selected_recovered": recovered_selected,
        "selected_failed_after": selected_failed_after,
        "unselected_recovered": unselected_recovered_count,
    }
    return recovered_selected + unselected_recovered_count


def _ocr_selected_products(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    state: dict[str, Any],
    ports: DailyPorts,
    data_root: Path,
    clock: Clock,
) -> list[dict[str, Any]]:
    from automation.inago.product_page_inputs import selection_manifest

    ocr = ports.product_page_ocr
    if ocr is None:
        raise DailyOrchestrationError("product_page_ocr_port_unavailable")
    _sync_committed_selection_manifest(connection, run_id, state)
    manifest = selection_manifest(state)
    active = sorted(_active_selected_entries(state), key=lambda item: item["selection_order"])
    for entry in active:
        try:
            saved_page = None
            if entry.get("page_ref"):
                from automation.inago.product_page_inputs import load_product_page_inputs

                saved_page = load_product_page_inputs(
                    data_root,
                    run_id,
                    str(entry["shop_id"]),
                    str(entry["item_id"]),
                    expected_ref=str(entry["page_ref"]),
                )
            page_ref = entry.get("page_ref")
            if (
                not isinstance(saved_page, Mapping)
                or saved_page.get("html_state") != "captured"
                or not isinstance(saved_page.get("images"), list)
            ):
                entry["status"] = "failed"
                entry["ocr_state"] = "failed"
                entry["ocr_error_code"] = "product_page_capture_failed"
                if isinstance(page_ref, str):
                    _trace_product_stage(
                        data_root,
                        run_id=run_id,
                        target_date=target_date,
                        offer_key=str(entry["offer_key"]),
                        page_ref=page_ref,
                        operation="product_page_capture",
                        phase="error",
                        clock=clock,
                        status="capture_failed",
                        error_code="product_page_capture_failed",
                    )
                _record_product_slot_failure(
                    state,
                    run_id=run_id,
                    entry=entry,
                    phase="product_page_capture",
                    code="product_page_capture_failed",
                    failed_at=_now(clock).isoformat(),
                )
                _save_state(connection, run_id, state)
                continue
            if entry.get("status") in {
                "ocr_complete",
                "ocr_partial",
                "copy_in_progress",
            }:
                continue
            entry["status"] = "ocr_in_progress"
            _save_state(connection, run_id, state)
            if isinstance(page_ref, str):
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=str(entry["offer_key"]),
                    page_ref=page_ref,
                    operation="product_image_ocr",
                    phase="begin",
                    clock=clock,
                    status="started",
                    image_count=(
                        len(saved_page.get("images", []))
                        if isinstance(saved_page, Mapping)
                        and isinstance(saved_page.get("images"), list)
                        else None
                    ),
                )
            outcome = ocr.process_product(
                connection,
                run_id=run_id,
                offer_key=str(entry["offer_key"]),
                shop_id=str(entry["shop_id"]),
                item_id=str(entry["item_id"]),
                page_ref=entry.get("page_ref"),
                source_evidence_ref=entry.get("source_evidence_ref"),
                data_root=data_root,
            )
            if not isinstance(outcome, Mapping):
                raise DailyOrchestrationError("product_page_ocr_result_invalid")
            product_status = outcome.get("status")
            image_results = outcome.get("images")
            if product_status not in {"complete", "partial", "failed"} or not isinstance(
                image_results, list
            ):
                raise DailyOrchestrationError("product_page_ocr_result_invalid")
            ocr_ref = outcome.get("ocr_ref")
            if product_status in {"complete", "partial"} and (
                not isinstance(ocr_ref, str)
                or not _SAFE_ACQUISITION_REF.fullmatch(ocr_ref)
            ):
                raise DailyOrchestrationError("product_page_ocr_ref_missing")
            normalized_images = []
            for image in image_results:
                if not isinstance(image, Mapping) or image.get("status") not in {
                    "success",
                    "empty",
                    "error",
                }:
                    raise DailyOrchestrationError("product_page_ocr_image_result_invalid")
                normalized = {
                    "order": (
                        image.get("order")
                        if isinstance(image.get("order"), int)
                        and not isinstance(image.get("order"), bool)
                        else len(normalized_images)
                    ),
                    "status": image["status"],
                }
                for key in ("image_ref", "source_ref", "ocr_ref", "error_code"):
                    value = image.get(key)
                    if isinstance(value, str) and _SAFE_ACQUISITION_REF.fullmatch(value):
                        normalized[key] = value
                normalized_images.append(normalized)
            expected_image_count = (
                len(saved_page.get("images", []))
                if isinstance(saved_page, Mapping)
                and isinstance(saved_page.get("images"), list)
                else None
            )
            if expected_image_count is not None and len(normalized_images) != expected_image_count:
                raise DailyOrchestrationError("product_page_ocr_image_count_mismatch")
            image_orders = [image["order"] for image in normalized_images]
            if (
                len(set(image_orders)) != len(image_orders)
                or sorted(image_orders) != list(range(len(normalized_images)))
            ):
                raise DailyOrchestrationError("product_page_ocr_image_order_invalid")
            if product_status == "complete" and any(
                image["status"] == "error" for image in normalized_images
            ):
                raise DailyOrchestrationError("product_page_ocr_complete_has_errors")
            entry["ocr_images"] = normalized_images
            entry["ocr_ref"] = (
                ocr_ref
                if isinstance(ocr_ref, str)
                and _SAFE_ACQUISITION_REF.fullmatch(ocr_ref)
                else None
            )
            entry["ocr_error_code"] = (
                outcome.get("error_code")
                if isinstance(outcome.get("error_code"), str)
                and _SAFE_ACQUISITION_REF.fullmatch(outcome["error_code"])
                else None
            )
            entry["ocr_state"] = product_status
            entry["ocr_completed_at"] = _now(clock).isoformat()
            if isinstance(page_ref, str):
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=str(entry["offer_key"]),
                    page_ref=page_ref,
                    operation="product_image_ocr",
                    phase="end" if product_status != "failed" else "error",
                    clock=clock,
                    status=product_status,
                    image_count=len(normalized_images),
                    error_code=(
                        "product_page_ocr_failed"
                        if product_status == "failed"
                        else None
                    ),
                )
            if product_status == "failed":
                entry["status"] = "failed"
                _record_product_slot_failure(
                    state,
                    run_id=run_id,
                    entry=entry,
                    phase="product_page_ocr",
                    code=entry["ocr_error_code"] or "ocr_failed",
                    failed_at=_now(clock).isoformat(),
                )
            else:
                entry["status"] = "ocr_partial" if product_status == "partial" else "ocr_complete"
            _save_state(connection, run_id, state)
        except Exception as exc:
            page_ref = entry.get("page_ref")
            if isinstance(page_ref, str):
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=str(entry["offer_key"]),
                    page_ref=page_ref,
                    operation="product_image_ocr",
                    phase="error",
                    clock=clock,
                    status="failed",
                    error_code="product_page_ocr_failed",
                    error_class=type(exc).__name__,
                    **_safe_trace_frame(exc),
                )
            entry["status"] = "failed"
            entry["ocr_state"] = "failed"
            entry["ocr_error_code"] = (
                getattr(exc, "error_code", None)
                if isinstance(getattr(exc, "error_code", None), str)
                else type(exc).__name__
            )
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="product_page_ocr",
                code="ocr_failed",
                failed_at=_now(clock).isoformat(),
            )
            _save_state(connection, run_id, state)
    return [
        entry
        for entry in active
        if entry.get("status") in {"ocr_complete", "ocr_partial", "copy_in_progress"}
    ]


def _process_selected_copies(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    target_date: str,
    settings_version: str,
    state: dict[str, Any],
    ports: DailyPorts,
    clock: Clock,
    data_root: Path,
    on_draft_commit: Callable[[sqlite3.Connection], None] | None,
) -> None:
    for entry in _ocr_selected_products(
        connection,
        run_id=run_id,
        target_date=target_date,
        state=state,
        ports=ports,
        data_root=data_root,
        clock=clock,
    ):
        offer_key = str(entry["offer_key"])
        existing = connection.execute(
            "SELECT 1 FROM draft WHERE run_id=? AND offer_key=? LIMIT 1",
            (run_id, offer_key),
        ).fetchone()
        if existing is not None:
            entry["status"] = "committed"
            committed_at = entry.get("committed_at")
            if not isinstance(committed_at, str):
                committed_at = _now(clock).isoformat()
                entry["committed_at"] = committed_at
            _resolve_product_slot_failure_on_commit(
                state,
                committed_entry=entry,
                committed_at=committed_at,
            )
            _cleanup_committed_product_images(
                connection,
                run_id=run_id,
                target_date=target_date,
                state=state,
                entry=entry,
                data_root=data_root,
                clock=clock,
            )
            _save_state(connection, run_id, state)
            continue
        theme_id = str(entry["theme_id"])
        offer = connection.execute(
            "SELECT 1 FROM offer WHERE offer_key=?", (offer_key,)
        ).fetchone()
        if offer is None:
            entry["status"] = "failed"
            entry["copy_error_code"] = "offer_record_missing"
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="copy",
                code="offer_record_missing",
                failed_at=_now(clock).isoformat(),
            )
            _save_state(connection, run_id, state)
            continue
        entry["status"] = "copy_in_progress"
        _save_state(connection, run_id, state)
        now = _now(clock)
        deadline_at = (now + timedelta(seconds=AI_REQUEST_TIMEOUT_SECONDS)).isoformat()
        deadline_monotonic = clock.monotonic() + AI_REQUEST_TIMEOUT_SECONDS
        try:
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
        except RejectedContract as exc:
            page_ref = entry.get("page_ref")
            if isinstance(page_ref, str):
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=offer_key,
                    page_ref=page_ref,
                    operation="product_ai_input",
                    phase="error",
                    clock=clock,
                    status="rejected",
                    error_code="product_copy_spec_rejected",
                    error_class=type(exc).__name__,
                    **_safe_trace_frame(exc),
                )
            entry["status"] = "failed"
            entry["copy_error_code"] = "copy_spec_contract_rejected"
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="copy",
                code="copy_spec_contract_rejected",
                failed_at=_now(clock).isoformat(),
            )
            _save_state(connection, run_id, state)
            continue
        if spec is None:
            page_ref = entry.get("page_ref")
            if isinstance(page_ref, str):
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=offer_key,
                    page_ref=page_ref,
                    operation="product_ai_input",
                    phase="status",
                    clock=clock,
                    status="missing",
                )
            entry["status"] = "failed"
            entry["copy_error_code"] = "copy_input_unavailable"
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="copy",
                code="copy_input_unavailable",
                failed_at=_now(clock).isoformat(),
            )
            _save_state(connection, run_id, state)
            continue
        if (
            spec.run_id != run_id
            or spec.offer_key != offer_key
            or spec.target_date != target_date
            or spec.settings_version != settings_version
            or spec.deadline_at != deadline_at
        ):
            entry["status"] = "failed"
            entry["copy_error_code"] = "copy_spec_run_identity_mismatch"
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="copy",
                code="copy_spec_run_identity_mismatch",
                failed_at=_now(clock).isoformat(),
            )
            _save_state(connection, run_id, state)
            continue
        page_ref = entry.get("page_ref")
        if isinstance(page_ref, str):
            _trace_product_stage(
                data_root,
                run_id=run_id,
                target_date=target_date,
                offer_key=offer_key,
                page_ref=page_ref,
                operation="product_ai_input",
                phase="status",
                clock=clock,
                status="ready",
                image_count=(
                    len(entry.get("ocr_images", []))
                    if isinstance(entry.get("ocr_images"), list)
                    else None
                ),
                input_ref_count=len(spec.input_refs),
            )
        try:
            outcome = generate_and_commit_copy(
                connection,
                spec,
                ports.copy_runner,
                data_root=data_root,
                clock=clock,
            )
        except RejectedContract as exc:
            if isinstance(page_ref, str):
                _trace_product_stage(
                    data_root,
                    run_id=run_id,
                    target_date=target_date,
                    offer_key=offer_key,
                    page_ref=page_ref,
                    operation="product_copy_commit",
                    phase="error",
                    clock=clock,
                    status="rejected",
                    error_code="product_copy_commit_rejected",
                    error_class=type(exc).__name__,
                    **_safe_trace_frame(exc),
                )
            entry["status"] = "failed"
            entry["copy_error_code"] = "copy_spec_contract_rejected"
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="copy",
                code="copy_spec_contract_rejected",
                failed_at=_now(clock).isoformat(),
            )
            _save_state(connection, run_id, state)
            continue
        if isinstance(page_ref, str):
            _trace_product_stage(
                data_root,
                run_id=run_id,
                target_date=target_date,
                offer_key=offer_key,
                page_ref=page_ref,
                operation="product_copy_commit",
                phase="status",
                clock=clock,
                status=outcome.status,
                image_count=(
                    len(entry.get("ocr_images", []))
                    if isinstance(entry.get("ocr_images"), list)
                    else None
                ),
                input_ref_count=len(spec.input_refs),
                error_code=(
                    "product_copy_commit_failed"
                    if outcome.status not in {"committed", "already_committed"}
                    else None
                ),
            )
        if outcome.status in {"committed", "already_committed"}:
            entry["status"] = "committed"
            entry["committed_at"] = _now(clock).isoformat()
            _resolve_product_slot_failure_on_commit(
                state,
                committed_entry=entry,
                committed_at=entry["committed_at"],
            )
            if on_draft_commit is not None:
                on_draft_commit(connection)
            _cleanup_committed_product_images(
                connection,
                run_id=run_id,
                target_date=target_date,
                state=state,
                entry=entry,
                data_root=data_root,
                clock=clock,
            )
            _save_state(connection, run_id, state)
            continue
        if outcome.status in {"pending_commit", "saved_response", "in_progress"}:
            entry["status"] = "copy_in_progress"
            entry["copy_error_code"] = outcome.error_code or outcome.status
            _save_state(connection, run_id, state)
            raise DailyOrchestrationError(
                f"copy_resume_required:{outcome.error_code or outcome.status}"
            )
        if outcome.status == "contract_error":
            entry["status"] = "failed"
            entry["copy_error_code"] = outcome.error_code or "copy_contract_rejected"
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="copy",
                code=entry["copy_error_code"],
                failed_at=_now(clock).isoformat(),
            )
            _save_state(connection, run_id, state)
            continue
        if outcome.status == "expired" and outcome.error_code == "timed_out":
            entry["status"] = "failed"
            confirmed = getattr(ports.copy_runner, "last_cancel_confirmed", None) is True
            entry["copy_error_code"] = (
                "ai_request_timeout" if confirmed else "ai_request_timeout_cancel_failed"
            )
            _record_product_slot_failure(
                state,
                run_id=run_id,
                entry=entry,
                phase="copy",
                code=entry["copy_error_code"],
                failed_at=_now(clock).isoformat(),
            )
            if not confirmed:
                state["source_stopped"] = True
                state["source_stopped_reason"] = entry["copy_error_code"]
                state["primary_error"] = dict(state["errors"][-1])
            _save_state(connection, run_id, state)
            continue
        entry["status"] = "failed"
        entry["copy_error_code"] = outcome.error_code or f"copy_{outcome.status}"
        _record_product_slot_failure(
            state,
            run_id=run_id,
            entry=entry,
            phase="copy",
            code=entry["copy_error_code"],
            failed_at=_now(clock).isoformat(),
        )
        _save_state(connection, run_id, state)
    state["selection_flush_due"] = False
    _save_state(connection, run_id, state)


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
    on_draft_commit: Callable[[sqlite3.Connection], None] | None = None,
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
    source_rows = connection.execute(
        "SELECT source_key,evidence_ref FROM source_post WHERE attempt_id=?",
        (attempt_id,),
    ).fetchall()
    evidence_by_source = {
        str(item["source_key"]): str(item["evidence_ref"]) for item in source_rows
    }
    pipeline_enabled = bool(state.get("product_page_pipeline")) or (
        ports.product_page_ocr is not None
    ) or _product_page_pipeline_enabled(
        ports, [str(item["evidence_ref"]) for item in source_rows]
    )
    if pipeline_enabled:
        state["product_page_pipeline"] = True
        candidates, evidence_by_source = _attempt_product_candidates(
            connection, run_id, attempt_id, persisted_identities
        )
        _select_attempt_candidates(
            connection,
            run_id=run_id,
            target_date=target_date,
            theme_id=theme_id,
            attempt_id=attempt_id,
            evaluation=evaluation,
            evidence_by_source=evidence_by_source,
            state=state,
            ports=ports,
            clock=clock,
            data_root=data_root,
        )
        _cleanup_unselected_product_inputs(
            connection,
            run_id=run_id,
            state=state,
            candidates=candidates,
            data_root=data_root,
            target_date=target_date,
            clock=clock,
        )
        if attempt_id not in processed:
            processed.append(attempt_id)
        _save_state(connection, run_id, state)
        if _selection_count(connection, run_id, state) >= DAILY_TARGET:
            _process_selected_copies(
                connection,
                run_id=run_id,
                target_date=target_date,
                settings_version=settings_version,
                state=state,
                ports=ports,
                clock=clock,
                data_root=data_root,
                on_draft_commit=on_draft_commit,
            )
        return
    handled = set(state.get("handled_offer_keys", []))
    for representative in evaluation.eligible_representatives:
        if state.get("source_stopped"):
            break
        offer_key = representative.offer_key
        if offer_key in handled or _draft_count(connection, run_id) >= DAILY_TARGET:
            continue
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
        deadline_at = (now + timedelta(seconds=AI_REQUEST_TIMEOUT_SECONDS)).isoformat()
        deadline_monotonic = clock.monotonic() + AI_REQUEST_TIMEOUT_SECONDS
        try:
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
        except RejectedContract as exc:
            raise _CopySpecContractRejected(
                run_id=run_id, offer_key=offer_key, rejection=exc
            ) from None
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
        try:
            outcome = generate_and_commit_copy(
                connection,
                spec,
                ports.copy_runner,
                data_root=data_root,
                clock=clock,
            )
        except RejectedContract as exc:
            raise _CopySpecContractRejected(
                run_id=run_id, offer_key=offer_key, rejection=exc
            ) from None
        if outcome.status in {"committed", "already_committed"}:
            _mark_offer_handled(state, offer_key)
            _save_state(connection, run_id, state)
            if on_draft_commit is not None:
                on_draft_commit(connection)
        elif outcome.status in {"pending_commit", "saved_response", "in_progress"}:
            raise DailyOrchestrationError(
                f"copy_resume_required:{outcome.error_code or outcome.status}"
            )
        elif outcome.status == "contract_error":
            raise _CopyContractRejected(
                run_id=run_id,
                offer_key=offer_key,
                error_code=outcome.error_code or "copy_contract_rejected",
                cause_code=outcome.cause_code,
                artifact_ref=outcome.artifact_ref,
            )
        elif outcome.status == "expired" and outcome.error_code == "timed_out":
            _mark_offer_handled(state, offer_key)
            confirmed = (
                getattr(ports.copy_runner, "last_cancel_confirmed", None) is True
            )
            error_code = (
                "ai_request_timeout"
                if confirmed
                else "ai_request_timeout_cancel_failed"
            )
            state["error_count"] = int(state.get("error_count", 0)) + 1
            _append_error(state, "copy", error_code)
            if not confirmed:
                state["source_stopped"] = True
                state["source_stopped_reason"] = error_code
                state["primary_error"] = dict(state["errors"][-1])
            _save_state(connection, run_id, state)
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
    on_draft_commit: Callable[[sqlite3.Connection], None] | None = None,
) -> None:
    task_number = int(task["task_number"])
    query = str(task["query"])
    used_queries = state.setdefault("used_queries", [])
    first_id = _task_attempt_id(run_id, task_number, 1)
    if query in used_queries and _attempt_row(connection, first_id) is None:
        return
    local_attempt = 1
    compatible_attempt_count = 0
    retry_of: str | None = None
    while compatible_attempt_count < 3:
        if state.get("source_stopped"):
            return
        if _draft_count(connection, run_id) >= DAILY_TARGET:
            return
        run_row = connection.execute(
            "SELECT attempt_used FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        if run_row is None:
            return
        max_local_attempt = max(3, int(run_row["attempt_used"]) + 3)
        if local_attempt > max_local_attempt:
            break
        attempt_id = _task_attempt_id(run_id, task_number, local_attempt)
        existing = _attempt_row(connection, attempt_id)
        if existing is not None and existing["ended_at"] is None:
            _finalize_interrupted_search(connection, attempt_id, clock)
            existing = _attempt_row(connection, attempt_id)

        if existing is not None and (
            existing["query"] != task["query"]
            or tuple(json.loads(existing["parent_refs_json"]))
            != tuple(task["parent_refs"])
            or existing["level"] != task["level"]
        ):
            superseded_attempts = state.setdefault("superseded_search_attempts", [])
            if isinstance(superseded_attempts, list) and not any(
                isinstance(entry, Mapping) and entry.get("attempt_id") == attempt_id
                for entry in superseded_attempts
            ):
                superseded_attempts.append(
                    {
                        "attempt_id": attempt_id,
                        "task_number": task_number,
                        "reason_code": "saved_search_input_changed",
                    }
                )
                _save_state(connection, run_id, state)
            # Keep the old attempt as history; never apply its results to the
            # updated task inputs. A new local id is created on the next pass.
            local_attempt += 1
            continue

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
            status = str(existing["source_status"])
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
            on_draft_commit=on_draft_commit,
        )
        attempt = _attempt_row(connection, attempt_id)
        if (
            attempt is not None
            and attempt["error_code"] == "room_request_timeout_cancel_unconfirmed"
        ):
            state["error_count"] = int(state.get("error_count", 0)) + 1
            state["source_stopped"] = True
            state["source_stopped_reason"] = "room_request_timeout_cancel_failed"
            _append_error(state, "room", "room_request_timeout_cancel_failed")
            state["primary_error"] = dict(state["errors"][-1])
            _save_state(connection, run_id, state)
            return
        compatible_attempt_count += 1
        retry_of = attempt_id
        if status not in {"partial", "unavailable", "invalid"}:
            return
        local_attempt += 1
    state["source_stopped"] = True
    state["source_stopped_reason"] = "attempt_budget_exhausted"
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
        "SELECT metrics_status FROM run WHERE run_id=?", (run_id,)
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
    if current_status == "pending":
        # A prior process may have stopped after starting this request. Its late
        # answer must not be duplicated or re-adopted on a restart.
        status = "unavailable"
    else:
        state["metrics_started_at"] = _now(clock).isoformat()
        timeout_value = getattr(
            ports.metrics_port,
            "timeout_seconds",
            METRICS_REQUEST_TIMEOUT_SECONDS,
        )
        if (
            isinstance(timeout_value, bool)
            or not isinstance(timeout_value, (int, float))
            or not math.isfinite(float(timeout_value))
            or float(timeout_value) <= 0
        ):
            _append_error(state, "metrics", "metrics_request_timeout_invalid")
            _persist_metrics_status(
                connection,
                run_id=run_id,
                state=state,
                status="unavailable",
            )
            return "unavailable"
        request_timeout = float(timeout_value)
        deadline = _now(clock) + timedelta(seconds=request_timeout)
        request_deadline_at = deadline.isoformat()
        connection.execute(
            "UPDATE run SET metrics_status='pending' WHERE run_id=?", (run_id,)
        )
        _save_state(connection, run_id, state)
        deadline_monotonic = clock.monotonic() + request_timeout
        cancel_metrics = _CancelRunWork(run_id, ports.cancel_metrics_job)
        metrics_database_path = _main_database_path(connection)

        def collect_with_read_only_connection() -> str | MetricsCollectionResult:
            metrics_connection = _open_read_only_connection(metrics_database_path)
            try:
                return ports.metrics_port.collect(
                    metrics_connection,
                    run_id=run_id,
                    deadline_at=request_deadline_at,
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
                    if any(
                        isinstance(snapshot.payload, Mapping)
                        and snapshot.payload.get("error_code")
                        == "authentication_required"
                        for snapshot in result.snapshots
                    ):
                        _append_error(state, "authentication", "auth_wait")
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
    on_draft_commit: Callable[[sqlite3.Connection], None] | None = None,
    manual: bool = False,
    cause_resolved: bool = False,
    target_date: str | None = None,
) -> DailyRunOutcome:
    """Run or resume one explicit daily target under its shared runtime lock."""

    system_clock = clock or SystemClock()
    now = _now(system_clock)
    selected_target_date = target_date or now.date().isoformat()
    try:
        selected_date = date.fromisoformat(selected_target_date)
    except (TypeError, ValueError) as exc:
        raise DailyOrchestrationError("daily_target_date_invalid") from exc
    if selected_date.isoformat() != selected_target_date:
        raise DailyOrchestrationError("daily_target_date_invalid")
    target_date = selected_target_date
    if selected_date > now.date() or (
        not manual
        and (
            selected_date < now.date()
            or selected_date == now.date() and now.time() < DAILY_START
        )
    ):
        return DailyRunOutcome("not_due", None, target_date, 0, 0, "not_started")
    id_factory = run_id_factory or (lambda target: f"INAGO-{target.isoformat()}")
    actual_seed_factory = seed_factory or (lambda: secrets.token_urlsafe(24))
    requested_root = Path(data_root).expanduser()
    root = validate_data_root(requested_root, allow_missing=True)
    root.mkdir(parents=True, exist_ok=True)
    root = validate_data_root(requested_root)
    existing_before_lock = _run_by_date(connection, target_date)
    proposed_run_id = (
        str(existing_before_lock["run_id"])
        if existing_before_lock is not None
        else id_factory(selected_date)
    )
    if not isinstance(proposed_run_id, str) or not _SAFE_RUN_ID.fullmatch(
        proposed_run_id
    ):
        raise DailyOrchestrationError("daily_run_id_invalid")
    from automation.inago.maintenance.deployment import (
        FileDeploymentExclusionGuard,
    )

    try:
        lock = RunLock.acquire(
            root / ".daily-run.lock",
            root / ".daily-run-owner.json",
            run_id=proposed_run_id,
        )
    except RunLockBusy:
        existing = _run_by_date(connection, target_date)
        if existing is not None:
            return _outcome_from_run(
                existing,
                connection=connection,
                resumed=True,
                reason="daily_lock_busy",
            )
        return DailyRunOutcome(
            "locked", None, target_date, 0, 0, "not_started", reason="daily_lock_busy"
        )

    runtime_exclusion = FileDeploymentExclusionGuard(
        root / ".maintenance-deployment.lock"
    )
    with runtime_exclusion, lock, ExitStack() as daily_resources:
        run_trace = TraceSession(
            TraceWriter(root, proposed_run_id, target_date, clock=system_clock),
            component="orchestrator",
            request_id=f"daily-{proposed_run_id}",
            task_id="daily",
            clock=system_clock,
        )
        run_trace.start(operation="retention")
        try:
            retention = apply_retention(
                connection,
                data_root=root,
                now=_now(system_clock),
            )
        except BaseException as exc:
            try:
                run_trace.fail(
                    exc,
                    error_code="retention_failed",
                    failed_operation="retention",
                    scope="run",
                )
            except Exception as trace_error:
                logging.getLogger(__name__).warning(
                    "retention trace unavailable error_type=%s",
                    type(trace_error).__name__,
                )
            raise
        run_trace.record_stage(
            "retention",
            phase="end",
            event_kind="retention_applied",
            status="success",
        )
        if (
            retention.trace_cleanup_failures
            or retention.source_observation_deletion_failures
            or retention.file_deletion_failures
            or retention.unregistered_failure_artifact_cleanup_failures
        ):
            run_trace.mark_degraded()
        logging.getLogger(__name__).info(
            "daily_retention_applied",
            extra={
                "run_id": proposed_run_id,
                "expired_artifacts": retention.expired_artifacts,
                "expired_source_posts": retention.expired_source_posts,
                "expired_keyword_snapshots": retention.expired_keyword_snapshots,
                "expired_trends_responses": retention.expired_trends_responses,
                "expired_source_observation_files": (
                    retention.expired_source_observation_files
                ),
                "source_observation_deletion_failures": (
                    retention.source_observation_deletion_failures
                ),
                "expired_unregistered_failure_artifacts": (
                    retention.expired_unregistered_failure_artifacts
                ),
                "unregistered_failure_artifact_cleanup_failures": (
                    retention.unregistered_failure_artifact_cleanup_failures
                ),
                "expired_trace_events": retention.expired_trace_events,
                "expired_trace_files": retention.expired_trace_files,
                "trace_cleanup_failures": retention.trace_cleanup_failures,
                "trace_state": run_trace.health,
            },
        )
        if (
            retention.source_observation_deletion_failures
            or retention.file_deletion_failures
            or retention.unregistered_failure_artifact_cleanup_failures
        ):
            raise DailyOrchestrationError("daily_retention_cleanup_failed")
        existing = _run_by_date(connection, target_date)
        resumed = existing is not None
        run = existing or _create_run(
            connection,
            target_date=target_date,
            now=now,
            ports=ports,
            run_id_factory=lambda _target: proposed_run_id,
            seed_factory=actual_seed_factory,
            diagnostic_trace=run_trace,
        )
        run_id = str(run["run_id"])
        if resumed and cause_resolved:
            _, previous_state = _daily_state(run["checkpoint"])
            previous_state.setdefault("cause_resolution_claims", []).append(
                {
                    "observed_at": _now(system_clock).isoformat(),
                    "owner_claimed_resolved": True,
                    "effect": "none_without_operation_evidence",
                }
            )
            _save_state(connection, run_id, previous_state)
            run = connection.execute(
                "SELECT * FROM run WHERE run_id=?", (run_id,)
            ).fetchone()
        if resumed:
            _, cleanup_state = _daily_state(run["checkpoint"])
            if cleanup_state.get("product_page_pipeline"):
                _recover_unselected_product_folder_cleanup(
                    connection,
                    run_id=run_id,
                    state=cleanup_state,
                    data_root=root,
                    target_date=target_date,
                    clock=system_clock,
                )
                _recover_committed_product_image_cleanup(
                    connection,
                    run_id=run_id,
                    target_date=target_date,
                    state=cleanup_state,
                    data_root=root,
                    clock=system_clock,
                )
                _save_state(connection, run_id, cleanup_state)
                run = connection.execute(
                    "SELECT * FROM run WHERE run_id=?", (run_id,)
                ).fetchone()
        if run["status"] in {"complete", "missed"}:
            if run["status"] == "complete" and run["metrics_status"] in {
                "pending",
                "not_started",
            }:
                _, state = _daily_state(run["checkpoint"])
                state.pop("metrics_started_at", None)
                if run["metrics_status"] == "pending":
                    cancel_metrics = _CancelRunWork(run_id, ports.cancel_metrics_job)
                    if not cancel_metrics(run_id):
                        state["error_count"] = int(state.get("error_count", 0)) + 1
                        _append_error(
                            state,
                            "metrics",
                            "metrics_previous_request_cancel_failed",
                            cancel_metrics.error_code,
                        )
                        state["primary_error"] = dict(state["errors"][-1])
                        _persist_metrics_status(
                            connection,
                            run_id=run_id,
                            state=state,
                            status="unavailable",
                        )
                        current = connection.execute(
                            "SELECT * FROM run WHERE run_id=?", (run_id,)
                        ).fetchone()
                        return _outcome_from_run(
                            current,
                            connection=connection,
                            resumed=True,
                            reason="metrics_previous_request_cancel_failed",
                        )
                _persist_metrics_status(
                    connection,
                    run_id=run_id,
                    state=state,
                    status="not_started",
                )
                metric_status = _collect_metrics(
                    connection,
                    run_id=run_id,
                    target_date=target_date,
                    state=state,
                    ports=ports,
                    clock=system_clock,
                    data_root=root,
                )
                current = connection.execute(
                    "SELECT * FROM run WHERE run_id=?", (run_id,)
                ).fetchone()
                return replace(
                    _outcome_from_run(
                        current,
                        connection=connection,
                        resumed=True,
                        reason="completed_run_metrics_resumed",
                    ),
                    metrics_status=metric_status,
                )
            return _outcome_from_run(run, connection=connection, resumed=resumed)
        saved_root = _strict_json_object(run["checkpoint"])
        saved_daily = saved_root.get(_DAILY_CHECKPOINT_KEY)
        if (
            isinstance(saved_daily, dict)
            and saved_daily.get("schema_version") == 2
            and saved_daily.get("rules_version") == "requirements-1.4"
        ):
            _, migrated_state = _daily_state(run["checkpoint"])
            _save_state(connection, run_id, migrated_state)
            run = connection.execute(
                "SELECT * FROM run WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise DailyOrchestrationError("daily_run_disappeared")
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
        if resumed:
            _reconcile_saved_theme_plans(
                connection,
                run_id=run_id,
                settings_version=settings_version,
                seed=seed,
                state=state,
                planner=ports.theme_planner,
            )
        if resumed and state.get("source_stopped"):
            state.setdefault("source_stop_history", []).append(
                {
                    "reason": state.get("source_stopped_reason"),
                    "resumed_at": _now(system_clock).isoformat(),
                }
            )
            state["source_stopped"] = False
            state["source_stopped_reason"] = None
            _save_state(connection, run_id, state)
        if state.get("phase") == "finished" and state.get("terminal_status") in {
            "failed",
            "shortfall",
        } and str(current["status"]) == "running":
            previous_terminal = state.get("terminal_status")
            state.setdefault("terminal_history", []).append(
                {
                    "status": previous_terminal,
                    "reason": state.get("last_reason"),
                    "resumed_at": _now(system_clock).isoformat(),
                }
            )
            initial_ids = state.get("initial_theme_ids", [])
            initial_finished = (
                isinstance(initial_ids, list)
                and int(state.get("initial_plan_index", 0)) >= len(initial_ids)
            )
            state["phase"] = (
                "additional"
                if int(state.get("additional_events", 0)) > 0 or initial_finished
                else "initial"
            )
            state.pop("terminal_status", None)
            state.pop("last_reason", None)
            _save_state(connection, run_id, state)
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

        startup_deadline_monotonic = (
            system_clock.monotonic() + AI_REQUEST_TIMEOUT_SECONDS
        )
        cancel_run_work = _CancelRunWork(run_id, ports.cancel_owned_jobs)
        original_copy_runner = ports.copy_runner
        ports = replace(
            ports,
            copy_runner=_DeadlineCopyRunner(
                ports.copy_runner,
                clock=system_clock,
                cancel=lambda request: _cancel_request_job(
                    ports.cancel_ai_request, request.run_id, request.job_id
                ),
            ),
            cancel_owned_jobs=cancel_run_work,
        )

        orchestration_failed = False
        contract_error_reason: str | None = None
        try:
            report_import = import_offline_report_batches(
                connection,
                runtime_root=root,
                clock=system_clock,
                trace_run_id=run_id,
                trace_target_date=target_date,
            )
            state["report_import"] = {
                "files_processed": report_import.files_processed,
                "reports_received": report_import.reports_received,
            }
            _save_state(connection, run_id, state)
            report_result = apply_pending_reports(
                connection,
                run_id=run_id,
                clock=system_clock,
                data_root=root,
            )
            state["report_apply"] = {
                "applied_count": len(report_result.applied_action_ids),
                "pending_count": len(report_result.pending_action_ids),
                "exploration_allowed": report_result.exploration_allowed,
                "error": report_result.error,
            }
            _save_state(connection, run_id, state)
            if not report_result.exploration_allowed:
                if not state.get("report_apply_error_pending"):
                    state["error_count"] = int(state.get("error_count", 0)) + 1
                    _append_error(
                        state,
                        "reports",
                        report_result.error or "pending_reports_block_exploration",
                    )
                state["report_apply_error_pending"] = True
                _save_state(connection, run_id, state)
            else:
                if state.pop("report_apply_error_pending", False):
                    state["error_count"] = max(0, int(state.get("error_count", 0)) - 1)
                    _save_state(connection, run_id, state)
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
                        deadline_monotonic=startup_deadline_monotonic
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
                _recover_product_page_capture_failures(
                    connection,
                    run_id=run_id,
                    target_date=target_date,
                    state=state,
                    ports=ports,
                    data_root=root,
                    clock=system_clock,
                )
                _sync_committed_selection_manifest(connection, run_id, state)
                _save_state(connection, run_id, state)
                if state.get("product_page_pipeline") and (
                    _selection_count(connection, run_id, state) >= DAILY_TARGET
                    or state.get("selection_flush_due") is True
                ):
                    _process_selected_copies(
                        connection,
                        run_id=run_id,
                        target_date=target_date,
                        settings_version=settings_version,
                        state=state,
                        ports=ports,
                        clock=system_clock,
                        data_root=root,
                        on_draft_commit=on_draft_commit,
                    )
                while _draft_count(connection, run_id) < DAILY_TARGET and not state.get(
                    "source_stopped"
                ):
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
                            state["selection_flush_due"] = True
                            _save_state(connection, run_id, state)
                            if state.get("product_page_pipeline") and _active_selected_entries(
                                state
                            ):
                                _process_selected_copies(
                                    connection,
                                    run_id=run_id,
                                    target_date=target_date,
                                    settings_version=settings_version,
                                    state=state,
                                    ports=ports,
                                    clock=system_clock,
                                    data_root=root,
                                    on_draft_commit=on_draft_commit,
                                )
                            if _draft_count(connection, run_id) >= DAILY_TARGET:
                                continue
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
                        on_draft_commit=on_draft_commit,
                    )
                    state["task_cursor"] = cursor + 1
                    _save_state(connection, run_id, state)
                if (
                    state.get("product_page_pipeline")
                    and state.get("source_stopped")
                    and _active_selected_entries(state)
                ):
                    state["selection_flush_due"] = True
                    _save_state(connection, run_id, state)
                    _process_selected_copies(
                        connection,
                        run_id=run_id,
                        target_date=target_date,
                        settings_version=settings_version,
                        state=state,
                        ports=ports,
                        clock=system_clock,
                        data_root=root,
                        on_draft_commit=on_draft_commit,
                    )
        except Exception as exc:
            orchestration_failed = True
            if isinstance(exc, _CopyContractRejected):
                contract_error_reason = exc.error_code
            state["error_count"] = int(state.get("error_count", 0)) + 1
            _record_orchestration_error(state, exc)
            state.setdefault("active_resume_errors", []).append(
                dict(state["errors"][-1])
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
        if count >= DAILY_TARGET and not orchestration_failed:
            pending_resume_errors = state.get("active_resume_errors", [])
            if isinstance(pending_resume_errors, list) and pending_resume_errors:
                errors = state.setdefault("errors", [])
                for pending_error in pending_resume_errors:
                    if not isinstance(pending_error, Mapping):
                        continue
                    for error_index in range(len(errors) - 1, -1, -1):
                        if errors[error_index] == pending_error:
                            resolved_error = errors.pop(error_index)
                            state["error_count"] = max(
                                0, int(state.get("error_count", 0)) - 1
                            )
                            _archive_resolved_error(
                                state,
                                resolved_error,
                                resolution="same_run_target_reached_after_resume",
                                at=_now(system_clock).isoformat(),
                            )
                            break
                state["active_resume_errors"] = []
                primary = state.get("primary_error")
                if isinstance(primary, Mapping) and not any(
                    dict(error) == dict(primary)
                    for error in state.get("errors", [])
                    if isinstance(error, Mapping)
                ):
                    if state.get("errors"):
                        state["primary_error"] = dict(state["errors"][-1])
                    else:
                        state.pop("primary_error", None)
            _save_state(connection, run_id, state)
        if count >= DAILY_TARGET:
            if int(state.get("error_count", 0)) == 0:
                status, reason = "complete", "daily_target_reached"
            else:
                status, reason = "partial", "daily_target_reached_with_unresolved_errors"
        elif count > 0:
            status = "partial"
            reason = contract_error_reason or "daily_interrupted_or_candidate_shortage"
        elif int(state.get("error_count", 0)) or int(state.get("source_failures", 0)):
            status, reason = (
                "failed",
                contract_error_reason or "daily_processing_error",
            )
        else:
            status = "shortfall"
            reason = "candidate_or_theme_exhausted"
        outcome = _finish(
            connection,
            run_id=run_id,
            clock=system_clock,
            state=state,
            status=status,
            reason=reason,
            preserve_checkpoint=orchestration_failed,
        )
        return replace(
            outcome,
            attempt_used=attempts,
            metrics_status=metric_status,
            resumed=resumed,
            reason=reason,
        )
