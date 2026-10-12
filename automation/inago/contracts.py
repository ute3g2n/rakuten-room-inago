"""Typed persistence contracts shared by the Inago runtime modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


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
