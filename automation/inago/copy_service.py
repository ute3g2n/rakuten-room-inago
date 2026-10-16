"""One-offer copy generation with bounded retries and same-body recovery."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Literal, Mapping, Protocol
from urllib.parse import quote

from automation.inago.artifact_io import write_temp_response
from automation.inago.clock import Clock, SystemClock
from automation.inago.contracts import (
    AIJobRequest,
    AIJobResult,
    ArtifactRecord,
    CopyTarget,
    DraftCommitRequest,
    DraftCommitResult,
    InjectedFailure,
    PersistenceContractError,
    RejectedContract,
)
from automation.inago.repository import (
    commit_draft,
    finish_generation_attempt,
    load_copy_generation_state,
    read_existing_draft_result,
    reserve_generation_attempt,
    validate_entity,
)


_COPY_RESPONSE_SCHEMA_VERSION = "copy-response-1"
_COPY_PROTOCOL_VERSION = "1"
_COPY_TRACE_LOCK = Lock()
_NON_RETRYABLE_CODES = frozenset(
    {
        "copy_identity_mismatch",
        "copy_input_mismatch",
        "copy_payload_contract_invalid",
        "copy_delegation_missing",
        "copy_runner_result_invalid",
        "copy_runner_contract_rejected",
        "late_result",
        "timed_out",
        "cancelled",
        "in_progress_recovery_required",
    }
)


class CopyRunner(Protocol):
    """The single Codex-root execution entry point; tests use a local fake."""

    def run(self, request: AIJobRequest) -> AIJobResult:
        """Run the authorized T02 job and return its observed root/subjob data."""


@dataclass(frozen=True, slots=True)
class CopyGenerationSpec:
    """Immutable inputs for one selected offer and one requested draft version."""

    run_id: str
    offer_key: str
    draft_version: int
    target_date: str
    settings_version: str
    input_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    input_payload: Mapping[str, Any]
    deadline_at: str
    deadline_monotonic: float
    generator_version: str
    pattern_version: str


@dataclass(frozen=True, slots=True)
class CopyGenerationOutcome:
    """Machine result; an export notification is downstream of the DB commit."""

    status: Literal[
        "committed",
        "already_committed",
        "pending_commit",
        "generation_failed",
        "attempts_exhausted",
        "contract_error",
        "expired",
        "in_progress",
    ]
    commit_result: DraftCommitResult | None
    attempts_used: int
    attempt_no: int | None = None
    resumed: bool = False
    error_code: str | None = None
    export_error: str | None = None
    cause_code: str | None = None
    artifact_ref: str | None = None


def _timestamp(clock: Clock) -> str:
    now = clock.now_jst()
    if now.tzinfo is None or now.utcoffset() is None:
        raise RejectedContract("copy clock must return an offset-aware time")
    return now.isoformat()


def _is_safe_artifact_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is not None
    )


def _trace_copy_event(
    data_root: str | Path,
    spec: CopyGenerationSpec,
    event: str,
    *,
    clock: Clock,
    attempt_no: int | None = None,
    status: str | None = None,
    error_code: str | None = None,
    cause_code: str | None = None,
    artifact_ref: str | None = None,
    ai_run_invoked: bool | None = None,
) -> None:
    """Persist lifecycle metadata without recording prompts, facts, or draft text."""

    record: dict[str, Any] = {
        "schema_version": "1",
        "at": _timestamp(clock),
        "event": event,
        "run_id": spec.run_id,
        "offer_key": spec.offer_key,
        "draft_version": spec.draft_version,
        "settings_version": spec.settings_version,
    }
    if attempt_no is not None:
        record["attempt_no"] = attempt_no
    if status is not None:
        record["status"] = status
    if isinstance(error_code, str) and re.fullmatch(
        r"[A-Za-z0-9._:-]{1,128}", error_code
    ):
        record["error_code"] = error_code
    if isinstance(cause_code, str) and re.fullmatch(
        r"[A-Za-z0-9._:-]{1,128}", cause_code
    ):
        record["cause_code"] = cause_code
    if _is_safe_artifact_ref(artifact_ref):
        record["artifact_ref"] = artifact_ref
    if ai_run_invoked is not None:
        record["ai_run_invoked"] = ai_run_invoked

    safe = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._-"
    filename = (
        f"{quote(spec.run_id, safe=safe)}--{quote(spec.offer_key, safe=safe)}.jsonl"
    )
    path = Path(data_root) / "logs" / "copy-generation" / spec.target_date / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        with _COPY_TRACE_LOCK:
            descriptor = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)
    except (OSError, TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "copy lifecycle trace append failed for run %s event %s",
            spec.run_id,
            event,
        )


def _root_job_id(attempt_id: str) -> str:
    return f"J-{attempt_id}"


def _draft_id(attempt_id: str) -> str:
    return f"D-{attempt_id}"


def _artifact_ref(attempt_id: str, attempt_no: int) -> str:
    return f"{attempt_id}-copy-a{attempt_no}"


def _check_spec(spec: CopyGenerationSpec) -> None:
    for field, value in (
        ("run_id", spec.run_id),
        ("offer_key", spec.offer_key),
        ("settings_version", spec.settings_version),
        ("generator_version", spec.generator_version),
    ):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise RejectedContract(f"{field} must be a non-empty identifier")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", spec.run_id):
        raise RejectedContract("run_id is not a safe job identifier")
    if (
        isinstance(spec.draft_version, bool)
        or not isinstance(spec.draft_version, int)
        or spec.draft_version < 1
    ):
        raise RejectedContract("draft_version must be positive")
    if spec.pattern_version != spec.settings_version:
        raise RejectedContract("pattern version must match the run settings version")
    if (
        not isinstance(spec.input_refs, tuple)
        or not spec.input_refs
        or any(not isinstance(ref, str) for ref in spec.input_refs)
        or len(set(spec.input_refs)) != len(spec.input_refs)
    ):
        raise RejectedContract("copy input references must be non-empty and unique")
    if (
        not isinstance(spec.evidence_refs, tuple)
        or any(not isinstance(ref, str) for ref in spec.evidence_refs)
        or len(set(spec.evidence_refs)) != len(spec.evidence_refs)
    ):
        raise RejectedContract("copy evidence references must be unique")
    if not set(spec.evidence_refs).issubset(spec.input_refs):
        raise RejectedContract("copy evidence references must be available inputs")
    reference_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
    if any(
        not isinstance(ref, str) or not reference_pattern.fullmatch(ref)
        for ref in (*spec.input_refs, *spec.evidence_refs)
    ):
        raise RejectedContract("copy references must be safe non-empty identifiers")
    if not isinstance(spec.input_payload, Mapping):
        raise RejectedContract("copy input payload must be an object")
    try:
        target_date = datetime.strptime(spec.target_date, "%Y-%m-%d")
        deadline = datetime.fromisoformat(spec.deadline_at.replace("Z", "+00:00"))
        json.dumps(
            dict(spec.input_payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise RejectedContract(
            "copy date, deadline, or input must be valid strict JSON"
        ) from exc
    if target_date.strftime("%Y-%m-%d") != spec.target_date:
        raise RejectedContract("target_date must be an ISO date")
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise RejectedContract("deadline_at must include a timezone offset")
    try:
        monotonic_deadline = float(spec.deadline_monotonic)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RejectedContract("deadline_monotonic must be finite") from exc
    if isinstance(spec.deadline_monotonic, bool) or not math.isfinite(
        monotonic_deadline
    ):
        raise RejectedContract("deadline_monotonic must be finite")
    offer = spec.input_payload.get("offer")
    if not isinstance(offer, Mapping):
        raise RejectedContract("copy input payload must include the target offer")
    if (
        offer.get("offer_key") != spec.offer_key
        or offer.get("draft_version") != spec.draft_version
    ):
        raise RejectedContract(
            "copy input payload does not match the requested offer/version"
        )


def _new_ai_request(
    spec: CopyGenerationSpec,
    *,
    attempt_id: str,
    attempt_no: int,
) -> AIJobRequest:
    return AIJobRequest(
        job_id=_root_job_id(attempt_id),
        run_id=spec.run_id,
        attempt=attempt_no,
        task_id="T02",
        target_date=spec.target_date,
        settings_version=spec.settings_version,
        input_refs=spec.input_refs,
        evidence_refs=spec.evidence_refs,
        deadline_at=spec.deadline_at,
        deadline_monotonic=spec.deadline_monotonic,
        input_payload=spec.input_payload,
        trigger="scheduled_daily",
        explicit_user_request=False,
        source_kind="daily",
        copy_targets=(CopyTarget(spec.offer_key, spec.draft_version),),
    )


def _root_record(
    request: AIJobRequest,
    result: AIJobResult | None,
    *,
    finished_at: str,
    error_code: str | None = None,
    cause_code: str | None = None,
) -> dict[str, Any]:
    if result is None:
        recorded_error: dict[str, str] = {"code": error_code or "runner_error"}
        if isinstance(cause_code, str) and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", cause_code
        ):
            recorded_error["cause_code"] = cause_code
        return {
            "job_id": request.job_id,
            "run_id": request.run_id,
            "protocol_version": _COPY_PROTOCOL_VERSION,
            "schema_version": _COPY_RESPONSE_SCHEMA_VERSION,
            "task_id": request.task_id,
            "agent_id": "AI-O",
            "skill_name": "rakuten-room-inago",
            "attempt": request.attempt,
            "target_date": request.target_date,
            "settings_version": request.settings_version,
            "input_refs": request.input_refs,
            "evidence_refs": request.evidence_refs,
            "input_hash": "unavailable",
            "deadline_at": request.deadline_at,
            "started_at": finished_at,
            "finished_at": finished_at,
            "resolved_model": "unobserved",
            "reasoning_setting": "unobserved",
            "auth_mode": "unobserved",
            "result_status": "error",
            "payload": None,
            "error": recorded_error,
            "artifact_ref": None,
            "accepted_at": None,
            "adoption_status": "pending",
        }
    recorded_error = result.error
    if error_code:
        recorded_error = {"code": error_code}
        root_cause_code = cause_code or (
            result.error.get("code") if isinstance(result.error, Mapping) else None
        )
        if isinstance(root_cause_code, str) and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", root_cause_code
        ):
            recorded_error["cause_code"] = root_cause_code
        artifact_ref = result.artifact.artifact_ref if result.artifact else None
        if isinstance(artifact_ref, str) and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", artifact_ref
        ):
            recorded_error["artifact_ref"] = artifact_ref
    return {
        "job_id": result.job_id,
        "run_id": result.run_id,
        "protocol_version": _COPY_PROTOCOL_VERSION,
        "schema_version": _COPY_RESPONSE_SCHEMA_VERSION,
        "task_id": result.task_id,
        "agent_id": result.agent_id,
        "skill_name": result.skill_name,
        "attempt": result.attempt,
        "target_date": result.target_date,
        "settings_version": result.settings_version,
        "input_refs": result.input_refs,
        "evidence_refs": result.evidence_refs,
        "input_hash": result.input_hash,
        "deadline_at": result.deadline_at,
        "started_at": result.started_at or finished_at,
        "finished_at": result.finished_at or finished_at,
        "resolved_model": result.resolved_model,
        "reasoning_setting": result.reasoning_setting,
        "auth_mode": result.auth_mode,
        "result_status": result.result_status,
        "payload": result.payload,
        "error": recorded_error,
        "artifact_ref": result.artifact.artifact_ref if result.artifact else None,
        "accepted_at": None,
        "adoption_status": result.adoption_status,
    }


def _specialist_records(
    root: AIJobResult | None,
    *,
    finished_at: str,
    exclude_job_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    if root is None:
        return ()
    records: list[dict[str, Any]] = []
    for subjob in root.subjobs:
        if subjob.job_id == exclude_job_id:
            continue
        records.append(
            {
                "job_id": subjob.job_id,
                "run_id": subjob.run_id,
                "protocol_version": _COPY_PROTOCOL_VERSION,
                "schema_version": _COPY_RESPONSE_SCHEMA_VERSION,
                "task_id": subjob.task_id,
                "agent_id": subjob.agent_id,
                "skill_name": subjob.skill_name,
                "attempt": subjob.attempt,
                "target_date": subjob.target_date,
                "settings_version": subjob.settings_version,
                "input_refs": subjob.input_refs,
                "evidence_refs": subjob.evidence_refs,
                "input_hash": subjob.input_hash,
                "deadline_at": subjob.deadline_at,
                "started_at": root.started_at or finished_at,
                "finished_at": root.finished_at or finished_at,
                "resolved_model": subjob.resolved_model,
                "reasoning_setting": subjob.reasoning_setting,
                "auth_mode": subjob.auth_mode,
                "result_status": subjob.result_status,
                "payload": dict(subjob.payload),
                "error": None,
                "artifact_ref": (
                    root.artifact.artifact_ref if root.artifact is not None else None
                ),
                "accepted_at": None,
                "adoption_status": "pending",
            }
        )
    return tuple(records)


def _copy_record(
    request: AIJobRequest,
    root: AIJobResult,
    subjob: Any,
    *,
    response: Mapping[str, Any] | None,
    artifact_ref: str | None,
    finished_at: str,
    error_code: str | None = None,
) -> dict[str, Any]:
    result_status = subjob.result_status
    adoption_status = "pending"
    if error_code is not None:
        result_status = "error"
        adoption_status = "rejected_contract"
    return {
        "job_id": subjob.job_id,
        "run_id": subjob.run_id,
        "protocol_version": _COPY_PROTOCOL_VERSION,
        "schema_version": _COPY_RESPONSE_SCHEMA_VERSION,
        "task_id": subjob.task_id,
        "agent_id": subjob.agent_id,
        "skill_name": subjob.skill_name,
        "attempt": subjob.attempt,
        "target_date": subjob.target_date,
        "settings_version": subjob.settings_version,
        "input_refs": subjob.input_refs,
        "evidence_refs": subjob.evidence_refs,
        "input_hash": subjob.input_hash,
        "deadline_at": subjob.deadline_at,
        "started_at": root.started_at or finished_at,
        "finished_at": root.finished_at or finished_at,
        "resolved_model": subjob.resolved_model,
        "reasoning_setting": subjob.reasoning_setting,
        "auth_mode": subjob.auth_mode,
        "result_status": result_status,
        "payload": dict(response) if response is not None else dict(subjob.payload),
        "error": {"code": error_code} if error_code else None,
        "artifact_ref": artifact_ref,
        "accepted_at": None,
        "adoption_status": adoption_status,
    }


def _root_matches(request: AIJobRequest, result: AIJobResult) -> bool:
    return (
        result.job_id == request.job_id
        and result.run_id == request.run_id
        and result.attempt == request.attempt
        and result.task_id == request.task_id
        and result.agent_id == "AI-O"
        and result.skill_name == "rakuten-room-inago"
        and result.target_date == request.target_date
        and result.settings_version == request.settings_version
        and result.input_refs == request.input_refs
        and result.evidence_refs == request.evidence_refs
        and result.deadline_at == request.deadline_at
        and result.adoption_status
        in {"pending", "rejected_contract", "expired", "conflict"}
    )


def _subjob_matches(
    request: AIJobRequest,
    subjob: Any,
    *,
    expected_offer_key: str,
    expected_version: int,
) -> bool:
    if (
        subjob.parent_job_id != request.job_id
        or subjob.run_id != request.run_id
        or subjob.attempt != request.attempt
        or subjob.task_id != "T02"
        or subjob.agent_id != "AG-COPY"
        or subjob.skill_name != "rr-inago-copywriter"
        or subjob.target_date != request.target_date
        or subjob.settings_version != request.settings_version
        or subjob.deadline_at != request.deadline_at
        or not subjob.job_id
        or not subjob.input_hash
        or not subjob.input_refs
        or len(set(subjob.input_refs)) != len(subjob.input_refs)
        or len(set(subjob.evidence_refs)) != len(subjob.evidence_refs)
        or not set(subjob.input_refs).issubset(request.input_refs)
        or not set(subjob.evidence_refs).issubset(request.evidence_refs)
    ):
        return False
    payload = subjob.payload
    return (
        isinstance(payload, Mapping)
        and payload.get("offer_key") == expected_offer_key
        and payload.get("draft_version") == expected_version
    )


def _copy_payload(subjob: Any) -> dict[str, Any]:
    raw = subjob.payload
    if not isinstance(raw, Mapping):
        raise RejectedContract("copy response must be an object")
    required_fields = {
        "offer_key",
        "draft_version",
        "body",
        "pattern_id",
        "evidence_refs",
        "selection_reason",
        "used_input_refs",
        "omitted_unknowns",
    }
    if set(raw) != required_fields:
        raise RejectedContract("copy response fields do not match its output schema")
    for field in ("selection_reason",):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise RejectedContract("copy response rationale is missing")
    for field in ("used_input_refs", "omitted_unknowns"):
        value = raw.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise RejectedContract(f"copy response {field} must be a string array")
        if any(not item.strip() for item in value):
            raise RejectedContract(f"copy response {field} contains an empty reference")
    if not set(raw["used_input_refs"]).issubset(subjob.input_refs):
        raise RejectedContract("copy response used an input the subjob did not receive")
    if not set(raw.get("evidence_refs", [])).issubset(subjob.evidence_refs):
        raise RejectedContract("copy response evidence was not supplied to the subjob")
    payload = {
        field: raw[field]
        for field in (
            "offer_key",
            "draft_version",
            "body",
            "pattern_id",
            "evidence_refs",
        )
    }
    validate_entity("copy_response", payload)
    return payload


def _copy_checkpoint_request(
    spec: CopyGenerationSpec,
    state: Mapping[str, Any],
    *,
    clock: Clock,
) -> DraftCommitRequest:
    attempt = state["attempt"]
    ai_job = state["copy_job"]
    artifact_row = state["artifact"]
    if attempt is None or ai_job is None or artifact_row is None:
        raise PersistenceContractError("successful copy checkpoint is incomplete")
    if (
        ai_job["settings_version"] != spec.settings_version
        or ai_job["target_date"] != spec.target_date
        or int(ai_job["attempt"]) != int(attempt["attempt_no"])
        or ai_job["task_id"] != "T02"
        or ai_job["agent_id"] != "AG-COPY"
        or ai_job["skill_name"] != "rr-inago-copywriter"
        or ai_job["result_status"] != "success"
        or ai_job["adoption_status"] != "pending"
    ):
        raise RejectedContract("saved copy checkpoint no longer matches this run")
    stored_input_refs = tuple(json.loads(ai_job["input_refs_json"]))
    stored_evidence_refs = tuple(json.loads(ai_job["evidence_refs_json"]))
    if not set(stored_input_refs).issubset(spec.input_refs) or not set(
        stored_evidence_refs
    ).issubset(spec.evidence_refs):
        raise RejectedContract(
            "saved copy checkpoint references are no longer available"
        )
    payload = json.loads(ai_job["payload_json"] or "null")
    if not isinstance(payload, dict):
        raise PersistenceContractError("saved copy response payload is missing")
    artifact = ArtifactRecord(
        artifact_ref=artifact_row["artifact_ref"],
        path=Path(artifact_row["path"]),
        content_hash=artifact_row["hash"],
        source_kind=artifact_row["source_kind"],
        created_at=artifact_row["created_at"],
        retention_expires_at=artifact_row["retention_expires_at"],
        status=artifact_row["status"],
    )
    return DraftCommitRequest(
        run_id=spec.run_id,
        job_id=ai_job["job_id"],
        settings_version=ai_job["settings_version"],
        input_refs=stored_input_refs,
        input_hash=ai_job["input_hash"],
        attempt_no=int(attempt["attempt_no"]),
        draft_id=_draft_id(attempt["generation_attempt_id"]),
        generator_version=spec.generator_version,
        pattern_version=spec.pattern_version,
        generated_at=ai_job["finished_at"],
        committed_at=_timestamp(clock),
        response=payload,
        artifact=artifact,
    )


def _notify_export(
    callback: Callable[[DraftCommitResult], None] | None,
    result: DraftCommitResult,
) -> str | None:
    if callback is None:
        return None
    try:
        callback(result)
    except Exception as exc:  # Export remains downstream and cannot undo a commit.
        return type(exc).__name__
    return None


def _make_outcome(
    status: Literal[
        "committed",
        "already_committed",
        "pending_commit",
        "generation_failed",
        "attempts_exhausted",
        "contract_error",
        "expired",
        "in_progress",
    ],
    *,
    result: DraftCommitResult | None = None,
    attempts_used: int,
    attempt_no: int | None = None,
    resumed: bool = False,
    error_code: str | None = None,
    export_notifier: Callable[[DraftCommitResult], None] | None = None,
) -> CopyGenerationOutcome:
    export_error = (
        _notify_export(export_notifier, result)
        if result is not None and status in {"committed", "already_committed"}
        else None
    )
    return CopyGenerationOutcome(
        status=status,
        commit_result=result,
        attempts_used=attempts_used,
        attempt_no=attempt_no,
        resumed=resumed,
        error_code=error_code,
        export_error=export_error,
    )


def _persist_attempt_result(
    connection: sqlite3.Connection,
    *,
    attempt: Mapping[str, Any],
    request: AIJobRequest,
    root: AIJobResult | None,
    outcome: Literal["success", "empty", "error"],
    error_code: str | None,
    cause_code: str | None = None,
    clock: Clock,
    artifacts: tuple[ArtifactRecord, ...] = (),
    copy_record: Mapping[str, Any] | None = None,
) -> str:
    finished_at = _timestamp(clock)
    root_record = _root_record(
        request,
        root,
        finished_at=finished_at,
        error_code=error_code,
        cause_code=cause_code,
    )
    copy_job_id = copy_record.get("job_id") if copy_record is not None else None
    records: tuple[Mapping[str, Any], ...] = (
        root_record,
        *_specialist_records(
            root,
            finished_at=finished_at,
            exclude_job_id=copy_job_id,
        ),
    )
    if copy_record is not None:
        records = (*records, copy_record)
    root_artifacts = (root.artifact,) if root is not None and root.artifact else ()
    merged_artifacts = tuple(dict.fromkeys((*root_artifacts, *artifacts)))
    attempt_artifact_ref = (
        artifacts[-1].artifact_ref
        if artifacts
        else (
            root.artifact.artifact_ref if root is not None and root.artifact else None
        )
    )
    finish_generation_attempt(
        connection,
        generation_attempt_id=str(attempt["generation_attempt_id"]),
        run_id=request.run_id,
        offer_key=request.copy_targets[0].offer_key,
        attempt_no=request.attempt,
        outcome=outcome,
        artifact_ref=attempt_artifact_ref,
        error_code=error_code,
        ended_at=finished_at,
        artifacts=merged_artifacts,
        ai_jobs=records,
    )
    return finished_at


def _commit_saved_response(
    connection: sqlite3.Connection,
    request: DraftCommitRequest,
    *,
    data_root: str | Path,
    spec: CopyGenerationSpec,
    clock: Clock,
    attempt_no: int,
    attempts_used: int,
    resumed: bool,
    export_notifier: Callable[[DraftCommitResult], None] | None,
) -> CopyGenerationOutcome:
    _trace_copy_event(
        data_root,
        spec,
        "draft_commit_started",
        clock=clock,
        attempt_no=attempt_no,
        status="pending",
        artifact_ref=request.artifact.artifact_ref,
        ai_run_invoked=False,
    )
    try:
        result = commit_draft(connection, request, clock=clock)
    except (sqlite3.Error, OSError, InjectedFailure):
        outcome = CopyGenerationOutcome(
            status="pending_commit",
            commit_result=None,
            attempts_used=attempts_used,
            attempt_no=attempt_no,
            resumed=resumed,
            error_code="draft_save_failed",
        )
        _trace_copy_event(
            data_root,
            spec,
            "draft_commit_failed",
            clock=clock,
            attempt_no=attempt_no,
            status=outcome.status,
            error_code=outcome.error_code,
            artifact_ref=request.artifact.artifact_ref,
            ai_run_invoked=False,
        )
        return outcome
    except (RejectedContract, PersistenceContractError) as exc:
        outcome = CopyGenerationOutcome(
            status="contract_error",
            commit_result=None,
            attempts_used=attempts_used,
            attempt_no=attempt_no,
            resumed=resumed,
            error_code="draft_commit_contract_error",
        )
        _trace_copy_event(
            data_root,
            spec,
            "draft_commit_rejected",
            clock=clock,
            attempt_no=attempt_no,
            status=outcome.status,
            error_code=outcome.error_code,
            artifact_ref=request.artifact.artifact_ref,
            ai_run_invoked=False,
        )
        return outcome
    outcome = _make_outcome(
        "committed",
        result=result,
        attempts_used=attempts_used,
        attempt_no=attempt_no,
        resumed=resumed,
        export_notifier=export_notifier,
    )
    _trace_copy_event(
        data_root,
        spec,
        "draft_committed",
        clock=clock,
        attempt_no=attempt_no,
        status=outcome.status,
        artifact_ref=request.artifact.artifact_ref,
        ai_run_invoked=False,
    )
    return outcome


def generate_and_commit_copy(
    connection: sqlite3.Connection,
    spec: CopyGenerationSpec,
    runner: CopyRunner,
    *,
    data_root: str | Path,
    clock: Clock | None = None,
    export_notifier: Callable[[DraftCommitResult], None] | None = None,
) -> CopyGenerationOutcome:
    """Generate at most three times, then atomically adopt one body for one offer."""

    _check_spec(spec)
    system_clock = clock or SystemClock()
    _trace_copy_event(
        data_root,
        spec,
        "generation_requested",
        clock=system_clock,
        status="started",
        ai_run_invoked=False,
    )
    state = load_copy_generation_state(
        connection, run_id=spec.run_id, offer_key=spec.offer_key
    )
    if state["run"]["settings_version"] != spec.settings_version:
        raise RejectedContract("copy settings version differs from the started run")
    if state["run"]["target_date"] != spec.target_date:
        raise RejectedContract("copy target date differs from the started run")
    prior = read_existing_draft_result(
        connection, run_id=spec.run_id, offer_key=spec.offer_key
    )
    latest = state["attempt"]
    attempts_used = int(latest["attempt_no"]) if latest is not None else 0
    if prior is not None:
        if prior.draft_version != spec.draft_version:
            return CopyGenerationOutcome(
                status="contract_error",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempts_used or None,
                resumed=True,
                error_code="existing_draft_version_mismatch",
            )
        return _make_outcome(
            "already_committed",
            result=prior,
            attempts_used=attempts_used,
            attempt_no=attempts_used or None,
            export_notifier=export_notifier,
        )
    if latest is not None and latest["outcome"] == "in_progress":
        return CopyGenerationOutcome(
            status="in_progress",
            commit_result=None,
            attempts_used=attempts_used,
            attempt_no=attempts_used,
            resumed=True,
            error_code="in_progress_recovery_required",
        )
    if latest is not None and latest["outcome"] == "success":
        if int(state["run"]["committed_count"]) >= 15:
            return CopyGenerationOutcome(
                status="attempts_exhausted",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempts_used,
                resumed=True,
                error_code="daily_limit_reached",
            )
        checkpoint = _copy_checkpoint_request(spec, state, clock=system_clock)
        _trace_copy_event(
            data_root,
            spec,
            "saved_response_resume",
            clock=system_clock,
            attempt_no=attempts_used,
            status="checkpoint_reused",
            artifact_ref=checkpoint.artifact.artifact_ref,
            ai_run_invoked=False,
        )
        return _commit_saved_response(
            connection,
            checkpoint,
            data_root=data_root,
            spec=spec,
            clock=system_clock,
            attempt_no=attempts_used,
            attempts_used=attempts_used,
            resumed=True,
            export_notifier=export_notifier,
        )
    if latest is not None and latest["error_code"] in _NON_RETRYABLE_CODES:
        status = (
            "expired"
            if latest["error_code"] in {"late_result", "timed_out", "cancelled"}
            else "contract_error"
        )
        artifact_ref = (
            latest["artifact_ref"]
            if _is_safe_artifact_ref(latest["artifact_ref"])
            else None
        )
        cause_code = None
        root_job = connection.execute(
            "SELECT error_json, artifact_ref FROM ai_job "
            "WHERE job_id=? AND run_id=? AND agent_id='AI-O'",
            (_root_job_id(latest["generation_attempt_id"]), spec.run_id),
        ).fetchone()
        if root_job is not None:
            if root_job["artifact_ref"] != artifact_ref:
                artifact_ref = None
            try:
                root_error = json.loads(root_job["error_json"] or "null")
            except (TypeError, json.JSONDecodeError):
                root_error = None
            if isinstance(root_error, Mapping):
                recorded_cause = root_error.get("cause_code")
                if isinstance(recorded_cause, str) and re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", recorded_cause
                ):
                    cause_code = recorded_cause
        resumed_event = (
            "expired_attempt_resumed"
            if status == "expired"
            else "contract_rejection_resumed"
        )
        _trace_copy_event(
            data_root,
            spec,
            resumed_event,
            clock=system_clock,
            attempt_no=attempts_used,
            status=status,
            error_code=latest["error_code"],
            cause_code=cause_code,
            artifact_ref=artifact_ref,
            ai_run_invoked=False,
        )
        return CopyGenerationOutcome(
            status=status,
            commit_result=None,
            attempts_used=attempts_used,
            attempt_no=attempts_used,
            resumed=True,
            error_code=latest["error_code"],
            cause_code=cause_code,
            artifact_ref=artifact_ref,
        )

    for attempt_no in range(attempts_used + 1, 4):
        if system_clock.deadline_remaining(spec.deadline_monotonic) <= 0:
            return CopyGenerationOutcome(
                status="expired",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code="timed_out",
            )
        attempt_id = f"GA-{uuid.uuid4().hex}"
        started_at = _timestamp(system_clock)
        attempt = reserve_generation_attempt(
            connection,
            run_id=spec.run_id,
            offer_key=spec.offer_key,
            settings_version=spec.settings_version,
            generation_attempt_id=attempt_id,
            attempt_no=attempt_no,
            started_at=started_at,
            clock=system_clock,
        )
        _trace_copy_event(
            data_root,
            spec,
            "generation_attempt_reserved",
            clock=system_clock,
            attempt_no=attempt_no,
            status="in_progress",
            ai_run_invoked=False,
        )
        request = _new_ai_request(spec, attempt_id=attempt_id, attempt_no=attempt_no)
        attempts_used = attempt_no
        try:
            root = runner.run(request)
        except Exception as exc:
            error_code = f"runner_error_{type(exc).__name__}"
            _trace_copy_event(
                data_root,
                spec,
                "ai_runner_failed",
                clock=system_clock,
                attempt_no=attempt_no,
                status="error",
                error_code=f"runner_error_{type(exc).__name__}",
                ai_run_invoked=True,
            )
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=None,
                outcome="error",
                error_code=error_code,
                clock=system_clock,
            )
            if attempt_no < 3:
                continue
            return CopyGenerationOutcome(
                status="generation_failed",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
            )

        root_artifact = getattr(root, "artifact", None)
        root_artifact_ref = getattr(root_artifact, "artifact_ref", None)
        malformed_artifact_ref = root_artifact is not None and (
            not isinstance(root_artifact, ArtifactRecord)
            or not _is_safe_artifact_ref(root_artifact_ref)
        )
        _trace_copy_event(
            data_root,
            spec,
            "ai_result_received",
            clock=system_clock,
            attempt_no=attempt_no,
            status=root.result_status if isinstance(root, AIJobResult) else "invalid",
            error_code=(
                root.error.get("code")
                if isinstance(root, AIJobResult) and isinstance(root.error, Mapping)
                else None
            ),
            artifact_ref=root_artifact_ref,
            ai_run_invoked=True,
        )

        if (
            not isinstance(root, AIJobResult)
            or not _root_matches(request, root)
            or malformed_artifact_ref
        ):
            error_code = "copy_runner_result_invalid"
            cause_code = "artifact_ref" if malformed_artifact_ref else None
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=None,
                outcome="error",
                error_code=error_code,
                cause_code=cause_code,
                clock=system_clock,
            )
            _trace_copy_event(
                data_root,
                spec,
                "ai_contract_rejected",
                clock=system_clock,
                attempt_no=attempt_no,
                status="error",
                error_code=error_code,
                cause_code=cause_code,
                ai_run_invoked=True,
            )
            return CopyGenerationOutcome(
                status="contract_error",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
                cause_code=cause_code,
            )

        if root.result_status in {"late_result", "timed_out", "cancelled"}:
            error_code = root.result_status
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=root,
                outcome="error",
                error_code=error_code,
                clock=system_clock,
            )
            return CopyGenerationOutcome(
                status="expired",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
            )

        if root.adoption_status in {"rejected_contract", "conflict", "expired"}:
            error_code = "copy_runner_contract_rejected"
            cause_code = (
                root.error.get("code")
                if isinstance(root.error, Mapping)
                and isinstance(root.error.get("code"), str)
                and re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
                    root.error["code"],
                )
                else None
            )
            artifact_ref = root.artifact.artifact_ref if root.artifact else None
            if not isinstance(artifact_ref, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", artifact_ref
            ):
                artifact_ref = None
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=root,
                outcome="error",
                error_code=error_code,
                clock=system_clock,
            )
            _trace_copy_event(
                data_root,
                spec,
                "ai_contract_rejected",
                clock=system_clock,
                attempt_no=attempt_no,
                status="error",
                error_code=error_code,
                cause_code=cause_code,
                artifact_ref=artifact_ref,
                ai_run_invoked=True,
            )
            return CopyGenerationOutcome(
                status="contract_error",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
                cause_code=cause_code,
                artifact_ref=artifact_ref,
            )

        if root.result_status in {"empty", "error"}:
            error_code = (
                "empty_response"
                if root.result_status == "empty"
                else "ai_generation_error"
            )
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=root,
                outcome=root.result_status,
                error_code=error_code,
                clock=system_clock,
            )
            if attempt_no < 3:
                continue
            return CopyGenerationOutcome(
                status="generation_failed",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
            )

        matching = [subjob for subjob in root.subjobs if subjob.agent_id == "AG-COPY"]
        if len(matching) != 1:
            error_code = "copy_delegation_missing"
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=root,
                outcome="error",
                error_code=error_code,
                clock=system_clock,
            )
            return CopyGenerationOutcome(
                status="contract_error",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
            )
        subjob = matching[0]
        if not _subjob_matches(
            request,
            subjob,
            expected_offer_key=spec.offer_key,
            expected_version=spec.draft_version,
        ):
            error_code = "copy_identity_mismatch"
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=root,
                outcome="error",
                error_code=error_code,
                clock=system_clock,
                copy_record=_copy_record(
                    request,
                    root,
                    subjob,
                    response=None,
                    artifact_ref=None,
                    finished_at=_timestamp(system_clock),
                    error_code=error_code,
                ),
            )
            return CopyGenerationOutcome(
                status="contract_error",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
            )

        if subjob.result_status in {"empty", "error"}:
            error_code = (
                "empty_response"
                if subjob.result_status == "empty"
                else "ai_generation_error"
            )
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=root,
                outcome=subjob.result_status,
                error_code=error_code,
                clock=system_clock,
                copy_record=_copy_record(
                    request,
                    root,
                    subjob,
                    response=None,
                    artifact_ref=None,
                    finished_at=_timestamp(system_clock),
                ),
            )
            if attempt_no < 3:
                continue
            return CopyGenerationOutcome(
                status="generation_failed",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
            )

        try:
            if subjob.result_status != "success":
                raise RejectedContract("copy subjob status is not adoptable")
            raw_body = (
                subjob.payload.get("body")
                if isinstance(subjob.payload, Mapping)
                else None
            )
            if isinstance(raw_body, str) and not raw_body.strip():
                _persist_attempt_result(
                    connection,
                    attempt=attempt,
                    request=request,
                    root=root,
                    outcome="empty",
                    error_code="empty_response",
                    clock=system_clock,
                    copy_record=_copy_record(
                        request,
                        root,
                        subjob,
                        response=None,
                        artifact_ref=None,
                        finished_at=_timestamp(system_clock),
                    ),
                )
                if attempt_no < 3:
                    continue
                return CopyGenerationOutcome(
                    status="generation_failed",
                    commit_result=None,
                    attempts_used=attempts_used,
                    attempt_no=attempt_no,
                    error_code="empty_response",
                )
            response = _copy_payload(subjob)
        except (PersistenceContractError, TypeError, KeyError, ValueError) as exc:
            error_code = "copy_payload_contract_invalid"
            _persist_attempt_result(
                connection,
                attempt=attempt,
                request=request,
                root=root,
                outcome="error",
                error_code=error_code,
                clock=system_clock,
                copy_record=_copy_record(
                    request,
                    root,
                    subjob,
                    response=None,
                    artifact_ref=None,
                    finished_at=_timestamp(system_clock),
                    error_code=error_code,
                ),
            )
            return CopyGenerationOutcome(
                status="contract_error",
                commit_result=None,
                attempts_used=attempts_used,
                attempt_no=attempt_no,
                error_code=error_code,
            )

        response_artifact = write_temp_response(
            data_root=data_root,
            artifact_ref=_artifact_ref(attempt_id, attempt_no),
            payload=response,
            source_kind="generation",
            created_at=root.finished_at or _timestamp(system_clock),
        )
        copy_record = _copy_record(
            request,
            root,
            subjob,
            response=response,
            artifact_ref=response_artifact.artifact_ref,
            finished_at=_timestamp(system_clock),
        )
        _persist_attempt_result(
            connection,
            attempt=attempt,
            request=request,
            root=root,
            outcome="success",
            error_code=None,
            clock=system_clock,
            artifacts=(response_artifact,),
            copy_record=copy_record,
        )
        _trace_copy_event(
            data_root,
            spec,
            "response_checkpoint_saved",
            clock=system_clock,
            attempt_no=attempt_no,
            status="success",
            artifact_ref=response_artifact.artifact_ref,
            ai_run_invoked=True,
        )
        commit_request = DraftCommitRequest(
            run_id=spec.run_id,
            job_id=subjob.job_id,
            settings_version=spec.settings_version,
            input_refs=tuple(subjob.input_refs),
            input_hash=subjob.input_hash,
            attempt_no=attempt_no,
            draft_id=_draft_id(attempt_id),
            generator_version=spec.generator_version,
            pattern_version=spec.pattern_version,
            generated_at=root.finished_at or _timestamp(system_clock),
            committed_at=_timestamp(system_clock),
            response=response,
            artifact=response_artifact,
        )
        return _commit_saved_response(
            connection,
            commit_request,
            data_root=data_root,
            spec=spec,
            clock=system_clock,
            attempt_no=attempt_no,
            attempts_used=attempts_used,
            resumed=False,
            export_notifier=export_notifier,
        )

    return CopyGenerationOutcome(
        status="attempts_exhausted",
        commit_result=None,
        attempts_used=attempts_used,
        attempt_no=attempts_used or None,
        error_code="copy_attempt_limit_3",
    )
