"""Create consistent, self-contained daily SQLite backup generations."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from automation.inago.contracts import PersistenceContractError
from automation.inago.paths import SAFE_FIXTURE_BASE
from automation.inago.repository import validate_entity
from automation.inago.retention import _parse_jst


_DAILY_DIRECTORY = re.compile(r"daily-(\d{8})\Z")
_FINAL_DAILY_DIRECTORY = re.compile(r"daily-final-(\d{8})-([0-9a-f]{32})\Z")
_ARTIFACT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MANIFEST_VERSION = 1
_GENERATION_LIMIT = 7


class BackupError(PersistenceContractError):
    """A backup could not be created or validated safely."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    directory: Path
    created_at: datetime
    operation_id: str
    schema_version: int
    table_counts: dict[str, int]
    artifact_count: int
    retained_generations: tuple[Path, ...]
    pruned_generations: tuple[Path, ...]


def _strict_child(path: Path, parent: Path) -> bool:
    try:
        return bool(path.relative_to(parent).parts)
    except ValueError:
        return False


def _validate_fixture_destination(path: Path, fixture_mode: bool) -> None:
    if not fixture_mode:
        return
    fixture_root = SAFE_FIXTURE_BASE.resolve()
    if not _strict_child(path.resolve(), fixture_root):
        raise BackupError(
            "fixture backup destination must stay under the private test area"
        )


def _validate_fixture_data_root(path: Path, fixture_mode: bool) -> None:
    if fixture_mode and not path.resolve().is_relative_to(SAFE_FIXTURE_BASE.resolve()):
        raise BackupError("fixture data_root must stay under the private test area")


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        counts[table] = int(
            connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        )
    return counts


def _foreign_key_errors(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]


def _resolve_artifact(data_root: Path, raw_path: str) -> Path:
    root = data_root.resolve()
    path = Path(os.path.abspath(Path(raw_path).expanduser()))
    try:
        relative = path.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise BackupError(
            "available artifact escaped the explicit data root or is missing"
        ) from exc
    if not relative.parts:
        raise BackupError(
            "available artifact is not a regular file inside the data root"
        )
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BackupError("available artifact path contains a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BackupError(
            "available artifact is missing or cannot be resolved"
        ) from exc
    if not resolved.is_file():
        raise BackupError(
            "available artifact is not a regular file inside the data root"
        )
    return resolved


def _artifact_manifest(
    snapshot: sqlite3.Connection,
    *,
    data_root: Path,
    stage_root: Path,
    now: datetime,
) -> list[dict[str, str | None]]:
    draft_refs = {
        str(row[0])
        for row in snapshot.execute(
            "SELECT artifact_ref FROM draft WHERE artifact_ref IS NOT NULL"
        )
    }
    records: list[dict[str, str | None]] = []
    rows = snapshot.execute(
        """
        SELECT artifact_ref, path, source_kind, created_at, retention_expires_at
        FROM artifact
        WHERE status = 'available'
        ORDER BY artifact_ref
        """
    ).fetchall()
    artifact_root = stage_root / "artifacts"
    for row in rows:
        artifact_ref = str(row["artifact_ref"])
        expires_at = row["retention_expires_at"]
        preserve_draft_generation = (
            row["source_kind"] == "generation" and artifact_ref in draft_refs
        )
        if expires_at is not None and not preserve_draft_generation:
            if _parse_jst(expires_at, name="retention_expires_at") <= now:
                continue
        if not _ARTIFACT_REF.fullmatch(artifact_ref):
            raise BackupError("artifact_ref is not a safe bundle identifier")
        source = _resolve_artifact(data_root, str(row["path"]))
        relative_bundle_path = Path("artifacts") / artifact_ref / source.name
        destination = stage_root / relative_bundle_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        records.append(
            {
                "artifact_ref": artifact_ref,
                "source_kind": str(row["source_kind"]),
                "created_at": str(row["created_at"]),
                "retention_expires_at": (
                    str(expires_at) if expires_at is not None else None
                ),
                "bundle_path": relative_bundle_path.as_posix(),
            }
        )
    return records


def _read_backup_manifest(directory: Path) -> dict[str, Any] | None:
    if directory.is_symlink() or not directory.is_dir():
        return None
    start_match = _DAILY_DIRECTORY.fullmatch(directory.name)
    final_match = _FINAL_DAILY_DIRECTORY.fullmatch(directory.name)
    if start_match is None and final_match is None:
        return None
    match = start_match or final_match
    generation_stage = "start" if start_match is not None else "final"
    manifest_path = directory / "manifest.json"
    database_path = directory / "state.sqlite"
    if manifest_path.is_symlink() or database_path.is_symlink():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("manifest_version") != _MANIFEST_VERSION
        or manifest.get("backup_type") != "daily-sqlite"
        or manifest.get("database_file") != "state.sqlite"
        or manifest.get("generation_stage", "start") != generation_stage
        or not database_path.is_file()
        or not isinstance(manifest.get("table_counts"), dict)
        or not isinstance(manifest.get("settings_versions"), list)
        or not isinstance(manifest.get("operational_counts"), dict)
        or not isinstance(manifest.get("artifacts"), list)
        or not isinstance(manifest.get("validation"), dict)
    ):
        return None
    try:
        created_at = _parse_jst(manifest["created_at"], name="created_at")
        if created_at.strftime("%Y%m%d") != match.group(1):
            return None
    except (KeyError, PersistenceContractError):
        return None
    if manifest["validation"].get("db_integrity") is not True:
        return None
    if manifest["validation"].get("counts_match") is not True:
        return None
    return manifest


def list_daily_backups(
    backup_root: str | Path,
    *,
    generation_stage: Literal["start", "final"] = "start",
) -> tuple[Path, ...]:
    """Return valid daily start or final generations, oldest first."""

    return _list_daily_backups(backup_root, generation_stage=generation_stage)


def list_final_daily_backups(backup_root: str | Path) -> tuple[Path, ...]:
    """Return valid end-of-day generations, oldest first."""

    return _list_daily_backups(backup_root, generation_stage="final")


def _list_daily_backups(
    backup_root: str | Path,
    *,
    generation_stage: Literal["start", "final"],
) -> tuple[Path, ...]:
    if generation_stage not in {"start", "final"}:
        raise BackupError("backup generation stage is invalid")

    root = Path(backup_root).expanduser().resolve()
    if not root.is_dir():
        return ()
    entries: list[tuple[str, str, Path]] = []
    for path in root.iterdir():
        manifest = _read_backup_manifest(path)
        if (
            manifest is None
            or manifest.get("generation_stage", "start") != generation_stage
        ):
            continue
        entries.append((str(manifest["created_at"]), path.name, path.resolve()))
    return tuple(path for _created_at, _name, path in sorted(entries))


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise BackupError("backup manifest could not be written atomically") from exc


def _prune_old_generations(
    backup_root: Path,
    *,
    generation_stage: Literal["start", "final"] = "start",
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    existing = list_daily_backups(backup_root, generation_stage=generation_stage)
    excess = existing[:-_GENERATION_LIMIT]
    for generation in excess:
        if generation.is_symlink() or _read_backup_manifest(generation) is None:
            raise BackupError("refusing to prune an unrecognized backup directory")
        try:
            shutil.rmtree(generation)
        except OSError as exc:
            raise BackupError(
                "an old daily backup generation could not be pruned"
            ) from exc
    retained = list_daily_backups(backup_root, generation_stage=generation_stage)
    return retained, tuple(excess)


def _operational_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "posted": int(
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE valid = 1"
            ).fetchone()[0]
        ),
        "active_reservations": int(
            connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status = 'active'"
            ).fetchone()[0]
        ),
        "pending_reports": int(
            connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status = 'pending'"
            ).fetchone()[0]
        ),
        "corrections": int(
            connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE action = 'correction'"
            ).fetchone()[0]
        ),
        "runs": int(connection.execute("SELECT COUNT(*) FROM run").fetchone()[0]),
    }


def _record_backup_status(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    observed_at: datetime,
    status: str,
    backup_path: Path | None,
    validation: dict[str, Any],
    cause: str,
    checkpoint: str = "daily_generation",
) -> None:
    entity = {
        "operation_id": operation_id,
        "type": "backup",
        "run_ref": None,
        "checkpoint": checkpoint,
        "cause": cause,
        "owner": "automation.inago.backup",
        "status": status,
        "backup_path": str(backup_path) if backup_path is not None else None,
        "restored_at": None,
        "validation": validation,
        "observed_at": observed_at.isoformat(),
    }
    try:
        validate_entity("backup", entity)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO backup(
                operation_id, type, run_ref, checkpoint, cause, owner,
                status, backup_path, restored_at, validation_json, observed_at
            ) VALUES (?, 'backup', NULL, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                operation_id,
                entity["checkpoint"],
                cause,
                entity["owner"],
                status,
                entity["backup_path"],
                json.dumps(validation, ensure_ascii=False, sort_keys=True),
                observed_at.isoformat(),
            ),
        )
        connection.commit()
    except BaseException as exc:
        if connection.in_transaction:
            connection.rollback()
        raise BackupError("backup operation status could not be recorded") from exc


def create_daily_backup(
    source: sqlite3.Connection,
    *,
    data_root: str | Path,
    backup_root: str | Path,
    now: str | datetime,
    fixture_mode: bool = True,
    generation_stage: Literal["start", "final"] = "start",
) -> BackupResult:
    """Publish one consistent daily backup through SQLite's backup API.

    The destination is explicit. In fixture mode it is constrained to
    ``非公開作業データ/イナゴ実装試験``; this function never chooses the production
    backup path on behalf of the caller.
    """

    if generation_stage not in {"start", "final"}:
        raise BackupError("backup generation stage is invalid")
    current = _parse_jst(now, name="now")
    root = Path(data_root).expanduser().resolve()
    destination_root = Path(backup_root).expanduser().resolve()
    expected_database = (root / "state.sqlite").resolve()
    _validate_fixture_data_root(root, fixture_mode)
    if not root.is_dir():
        raise BackupError("data_root must already exist")
    if (
        destination_root == root
        or destination_root.is_relative_to(root)
        or root.is_relative_to(destination_root)
    ):
        raise BackupError("backup_root and data_root must be separate trees")
    _validate_fixture_destination(destination_root, fixture_mode)

    database_rows = source.execute("PRAGMA database_list").fetchall()
    main_path = Path(str(database_rows[0][2])).resolve() if database_rows else Path()
    if main_path != expected_database:
        raise BackupError("source connection must point to data_root/state.sqlite")
    if source.in_transaction:
        raise BackupError("backup must start outside an active database transaction")

    operation_id = str(uuid.uuid4())
    date_name = current.strftime("%Y%m%d")
    daily_name = (
        f"daily-{date_name}"
        if generation_stage == "start"
        else f"daily-final-{date_name}-{uuid.uuid4().hex}"
    )
    final_directory = destination_root / daily_name
    stage_directory = destination_root / f".{daily_name}.{uuid.uuid4().hex}.staging"
    validation: dict[str, Any] = {
        "db_integrity": None,
        "counts_match": None,
        "details_expired_filtered": None,
        "errors": ["backup_failed"],
    }
    try:
        destination_root.mkdir(parents=True, exist_ok=True)
        if destination_root.is_symlink():
            raise BackupError("backup_root cannot be a symbolic link")
        if generation_stage == "start" and (
            final_directory.exists() or final_directory.is_symlink()
        ):
            raise BackupError("a daily backup generation already exists for this date")
        stage_directory.mkdir()
        staged_db = stage_directory / "state.sqlite"
        target = sqlite3.connect(staged_db)
        target.row_factory = sqlite3.Row
        try:
            source.backup(target)
            target.commit()
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise BackupError("SQLite backup integrity_check did not return ok")
            foreign_key_errors = _foreign_key_errors(target)
            if foreign_key_errors:
                raise BackupError("SQLite backup contains foreign-key violations")
            validation = {
                "db_integrity": True,
                "counts_match": True,
                "details_expired_filtered": True,
                "errors": [],
            }
            schema_version = int(target.execute("PRAGMA user_version").fetchone()[0])
            table_counts = _table_counts(target)
            artifacts = _artifact_manifest(
                target,
                data_root=root,
                stage_root=stage_directory,
                now=current,
            )
            settings_versions = [
                str(row[0])
                for row in target.execute(
                    "SELECT version FROM settings_version ORDER BY version"
                )
            ]
            operational_counts = _operational_counts(target)
        finally:
            target.close()

        manifest: dict[str, Any] = {
            "manifest_version": _MANIFEST_VERSION,
            "backup_type": "daily-sqlite",
            "generation_stage": generation_stage,
            "created_at": current.isoformat(),
            "database_file": "state.sqlite",
            "schema_version": schema_version,
            "table_counts": table_counts,
            "settings_versions": settings_versions,
            "operational_counts": operational_counts,
            "artifacts": artifacts,
            "validation": validation,
            "snapshot_semantics": "sqlite-backup-api-consistent-snapshot",
        }
        _atomic_write_json(stage_directory / "manifest.json", manifest)
        os.replace(stage_directory, final_directory)
        retained, pruned = _prune_old_generations(
            destination_root, generation_stage=generation_stage
        )
        cause = (
            "daily_generation"
            if generation_stage == "start"
            else "daily_final_generation"
        )
        _record_backup_status(
            source,
            operation_id=operation_id,
            observed_at=current,
            status="complete",
            backup_path=final_directory,
            validation=validation,
            cause=cause,
            checkpoint=cause,
        )
    except BaseException as exc:
        if stage_directory.exists():
            shutil.rmtree(stage_directory, ignore_errors=True)
        failure_status = dict(validation)
        if not failure_status["errors"]:
            failure_status["errors"] = ["backup_or_generation_retention_failed"]
        try:
            _record_backup_status(
                source,
                operation_id=operation_id,
                observed_at=current,
                status="failed",
                backup_path=final_directory if final_directory.is_dir() else None,
                validation=failure_status,
                cause=(
                    "daily_generation_failed"
                    if generation_stage == "start"
                    else "daily_final_generation_failed"
                ),
                checkpoint=(
                    "daily_generation"
                    if generation_stage == "start"
                    else "daily_final_generation"
                ),
            )
        except BackupError as record_exc:
            raise BackupError(
                "daily backup failed and its operation status could not be recorded"
            ) from record_exc
        if isinstance(exc, BackupError):
            raise
        raise BackupError(
            "daily backup failed; source drafts and posting state were preserved"
        ) from exc
    return BackupResult(
        directory=final_directory,
        created_at=current,
        operation_id=operation_id,
        schema_version=schema_version,
        table_counts=table_counts,
        artifact_count=len(artifacts),
        retained_generations=retained,
        pruned_generations=pruned,
    )
