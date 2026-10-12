"""Write runtime response artifacts safely inside an explicit data root."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from automation.inago.contracts import (
    ArtifactRecord,
    IdempotencyConflict,
    PersistenceContractError,
)


_ARTIFACT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SOURCE_KINDS = {
    "external_analysis_result",
    "daily",
    "metrics",
    "generation",
}


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PersistenceContractError("artifact timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PersistenceContractError("artifact timestamp must include an offset")


def _strict_child(path: Path, parent: Path) -> bool:
    try:
        return bool(path.relative_to(parent).parts)
    except ValueError:
        return False


def _ensure_directory_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not _strict_child(resolved, root):
        raise PersistenceContractError(
            "artifact directory escaped the explicit data root"
        )
    if path.exists() or path.is_symlink():
        if not path.is_dir():
            raise PersistenceContractError("artifact directory is not a directory")
    else:
        path.mkdir()
    resolved = path.resolve()
    if not _strict_child(resolved, root) or not resolved.is_dir():
        raise PersistenceContractError(
            "artifact directory escaped the explicit data root"
        )
    return resolved


def _existing_artifact_matches(path: Path, encoded: bytes, root: Path) -> bool:
    if path.is_symlink():
        raise PersistenceContractError("artifact reference points to a symbolic link")
    resolved = path.resolve()
    if not _strict_child(resolved, root):
        raise PersistenceContractError("artifact file escaped the explicit data root")
    if not path.is_file():
        raise PersistenceContractError("artifact reference is not a regular file")
    try:
        return path.read_bytes() == encoded
    except OSError as exc:
        raise PersistenceContractError("existing artifact cannot be read") from exc


def _write_encoded_artifact(
    *,
    data_root: str | Path,
    artifact_ref: str,
    encoded: bytes,
    extension: str,
    source_kind: str,
    created_at: str,
    retention_expires_at: str | None,
) -> ArtifactRecord:
    root = Path(data_root).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        artifact_root = _ensure_directory_inside(root / "artifacts", root)
        resolved_dir = _ensure_directory_inside(artifact_root / source_kind, root)
    except OSError as exc:
        raise PersistenceContractError(
            "artifact directory could not be created safely"
        ) from exc

    destination = resolved_dir / f"{artifact_ref}{extension}"
    if destination.exists() or destination.is_symlink():
        if not _existing_artifact_matches(destination, encoded, root):
            raise IdempotencyConflict(
                "artifact_ref already identifies different runtime content"
            )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{artifact_ref}.",
            suffix=".tmp",
            dir=resolved_dir,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            if not _existing_artifact_matches(destination, encoded, root):
                raise IdempotencyConflict(
                    "artifact_ref already identifies different runtime content"
                )
    except OSError as exc:
        raise PersistenceContractError(
            "response artifact could not be written"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    resolved_path = destination.resolve()
    if not _strict_child(resolved_path, root):
        raise PersistenceContractError(
            "response artifact escaped the explicit data root"
        )

    return ArtifactRecord(
        artifact_ref=artifact_ref,
        path=resolved_path,
        content_hash=hashlib.sha256(encoded).hexdigest(),
        source_kind=source_kind,
        created_at=created_at,
        retention_expires_at=retention_expires_at,
        status="available",
    )


def write_temp_response(
    *,
    data_root: str | Path,
    artifact_ref: str,
    payload: Mapping[str, Any],
    source_kind: str,
    created_at: str,
    retention_expires_at: str | None = None,
) -> ArtifactRecord:
    """Atomically persist one JSON response before any database transaction.

    The returned content hash belongs to this runtime artifact record only; the
    function never hashes plans, source documents, repositories, or test runs.
    A later database failure leaves the saved response file intact so the
    same response can be offered to the repository again.
    """

    if not _ARTIFACT_REF.fullmatch(artifact_ref):
        raise PersistenceContractError("artifact_ref must be a safe file identifier")
    if source_kind not in _SOURCE_KINDS:
        raise PersistenceContractError("unsupported artifact source_kind")
    _validate_timestamp(created_at)
    if retention_expires_at is not None:
        _validate_timestamp(retention_expires_at)

    try:
        encoded = (
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PersistenceContractError("response payload is not strict JSON") from exc

    return _write_encoded_artifact(
        data_root=data_root,
        artifact_ref=artifact_ref,
        encoded=encoded,
        extension=".json",
        source_kind=source_kind,
        created_at=created_at,
        retention_expires_at=retention_expires_at,
    )


def write_temp_response_bytes(
    *,
    data_root: str | Path,
    artifact_ref: str,
    response: bytes,
    source_kind: str,
    created_at: str,
    retention_expires_at: str | None = None,
) -> ArtifactRecord:
    """Preserve raw CLI response bytes before parsing or contract checks."""

    if not _ARTIFACT_REF.fullmatch(artifact_ref):
        raise PersistenceContractError("artifact_ref must be a safe file identifier")
    if source_kind not in _SOURCE_KINDS:
        raise PersistenceContractError("unsupported artifact source_kind")
    if not isinstance(response, bytes):
        raise PersistenceContractError("raw response must be bytes")
    _validate_timestamp(created_at)
    if retention_expires_at is not None:
        _validate_timestamp(retention_expires_at)
    return _write_encoded_artifact(
        data_root=data_root,
        artifact_ref=artifact_ref,
        encoded=response,
        extension=".response",
        source_kind=source_kind,
        created_at=created_at,
        retention_expires_at=retention_expires_at,
    )
