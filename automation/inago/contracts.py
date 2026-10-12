"""Typed persistence contracts shared by the Inago runtime modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


class PersistenceContractError(ValueError):
    """The requested write violates an entity or persistence contract."""


class RejectedContract(PersistenceContractError):
    """An input or existing state cannot be adopted for this write."""


class IdempotencyConflict(RejectedContract):
    """A stable ID was already used with different business content."""


class InjectedFailure(RuntimeError):
    """Deterministic test-only failure raised at a named persistence point."""


class UnsupportedSchemaVersion(RuntimeError):
    """The database schema is outside the reader's supported version range."""


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Runtime artifact metadata for one externally generated response file."""

    artifact_ref: str
    path: Path
    content_hash: str
    source_kind: str
    created_at: str
    retention_expires_at: str | None = None
    status: str = "available"


@dataclass(frozen=True, slots=True)
class DraftCommitRequest:
    """A persisted AI envelope plus the response artifact awaiting adoption."""

    run_id: str
    job_id: str
    settings_version: str
    input_refs: tuple[str, ...]
    input_hash: str
    attempt_no: int
    draft_id: str
    generator_version: str
    pattern_version: str
    generated_at: str
    committed_at: str
    response: Mapping[str, Any]
    artifact: ArtifactRecord


@dataclass(frozen=True, slots=True)
class DraftCommitResult:
    """Outcome of one atomic draft, reservation, and daily-count write."""

    draft_id: str
    offer_key: str
    draft_version: int
    committed_count: int
    already_committed: bool


@dataclass(frozen=True, slots=True)
class CopyTarget:
    """A program-selected offer and draft version a copy subagent may address."""

    offer_key: str
    draft_version: int


@dataclass(frozen=True, slots=True)
class AIJobRequest:
    """Program-owned inputs and authorization for one AI-O root execution."""

    job_id: str
    run_id: str
    attempt: int
    task_id: str
    target_date: str
    settings_version: str
    input_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    deadline_at: str
    deadline_monotonic: float
    input_payload: Mapping[str, Any]
    trigger: Literal["scheduled_daily", "user_request"]
    explicit_user_request: bool
    source_kind: str
    copy_targets: tuple[CopyTarget, ...] = ()


@dataclass(frozen=True, slots=True)
class AISubjobResult:
    """One specialist result observed inside the AI-O CLI execution."""

    job_id: str
    parent_job_id: str
    run_id: str
    attempt: int
    task_id: str
    agent_id: str
    skill_name: str
    target_date: str
    settings_version: str
    input_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    input_hash: str
    deadline_at: str
    result_status: Literal["success", "empty", "error"]
    payload: Mapping[str, Any]
    resolved_model: str = "unobserved"
    reasoning_setting: str = "unobserved"
    auth_mode: str = "unobserved"


@dataclass(frozen=True, slots=True)
class AIJobResult:
    """A root AI-O outcome, separate from later program adoption."""

    job_id: str
    run_id: str
    attempt: int
    task_id: str
    agent_id: str
    skill_name: str
    target_date: str
    settings_version: str
    input_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    input_hash: str
    deadline_at: str
    result_status: Literal[
        "success", "empty", "error", "timed_out", "cancelled", "late_result"
    ]
    adoption_status: Literal[
        "pending", "accepted", "rejected_contract", "expired", "conflict"
    ]
    payload: Mapping[str, Any] | None
    error: Mapping[str, Any] | None
    artifact: ArtifactRecord | None
    subjobs: tuple[AISubjobResult, ...] = ()
    resolved_model: str = "unobserved"
    reasoning_setting: str = "unobserved"
    auth_mode: str = "unobserved"
    process_returncode: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
