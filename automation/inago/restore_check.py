"""Validate a backup in an isolated monthly restore-check directory."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from automation.inago.backup import _read_backup_manifest
from automation.inago.contracts import PersistenceContractError
from automation.inago.paths import SAFE_FIXTURE_BASE
from automation.inago.retention import RetentionResult, _parse_jst, apply_retention


_DAILY_DIRECTORY = re.compile(r"daily-\d{8}\Z")
_FINAL_DAILY_DIRECTORY = re.compile(r"daily-final-\d{8}-[0-9a-f]{32}\Z")
_ARTIFACT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_REPORT_VERSION = 1


class RestoreCheckError(PersistenceContractError):
    """A backup cannot be safely validated in isolation."""


@dataclass(frozen=True, slots=True)
class RestoreCheckResult:
    directory: Path
    database_path: Path
    checked_at: datetime
    backup_directory: Path
    integrity_check: str
    foreign_key_errors: int
    schema_version: int
    table_counts_match: bool
    expired_artifacts: int
    expired_aggregates: int
    posted_count: int
    active_reservations: int
    pending_reports: int
    correction_count: int
    run_count: int
    file_deletion_failures: tuple[str, ...]
    selection_allowed: bool
    requires_supplementation: bool
    old_tasks_enabled: bool | None
    old_task_activation_attempts: int
    old_task_state_observed: bool
    already_checked: bool = False
    expired_trace_events: int = 0
    expired_trace_files: int = 0
    trace_cleanup_failures: int = 0


def _strict_child(path: Path, parent: Path) -> bool:
    try:
        return bool(path.relative_to(parent).parts)
    except ValueError:
        return False


def _validate_fixture_root(backup_root: Path, fixture_mode: bool) -> None:
    if fixture_mode and not _strict_child(
        backup_root.resolve(), SAFE_FIXTURE_BASE.resolve()
    ):
        raise RestoreCheckError(
            "fixture restore-check must stay under the private test area"
        )


def _safe_bundle_file(bundle_root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in ("", ".", "..") for part in relative.parts)
        or "\\" in relative_path
        or ":" in relative_path
    ):
        raise RestoreCheckError("manifest artifact path is unsafe")
    path = bundle_root.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(bundle_root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise RestoreCheckError(
            "manifest artifact path escaped the backup bundle"
        ) from exc
    cursor = bundle_root.resolve()
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RestoreCheckError("manifest artifact path contains a symbolic link")
    if not resolved.is_file():
        raise RestoreCheckError("manifest artifact is not a regular file")
    return resolved


def _validate_bundle(bundle: Path, backup_root: Path) -> dict[str, Any]:
    try:
        resolved_root = backup_root.resolve(strict=True)
        resolved_bundle = bundle.resolve(strict=True)
        if not _strict_child(resolved_bundle, resolved_root):
            raise RestoreCheckError(
                "backup bundle must be under the explicit backup_root"
            )
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, RestoreCheckError):
            raise
        raise RestoreCheckError("backup bundle or root cannot be resolved") from exc
    if resolved_bundle.parent != resolved_root or not (
        _DAILY_DIRECTORY.fullmatch(resolved_bundle.name)
        or _FINAL_DAILY_DIRECTORY.fullmatch(resolved_bundle.name)
    ):
        raise RestoreCheckError(
            "restore-check input must be one daily backup generation"
        )
    if resolved_bundle.is_symlink():
        raise RestoreCheckError("backup bundle cannot be a symbolic link")
    manifest = _read_backup_manifest(resolved_bundle)
    if manifest is None:
        raise RestoreCheckError("backup bundle manifest or SQLite snapshot is invalid")
    if not isinstance(manifest.get("table_counts"), dict) or not isinstance(
        manifest.get("artifacts"), list
    ):
        raise RestoreCheckError("backup manifest has an invalid structure")
    if not isinstance(manifest.get("settings_versions"), list) or not isinstance(
        manifest.get("operational_counts"), dict
    ):
        raise RestoreCheckError(
            "backup manifest is missing settings or operational counts"
        )
    validation = manifest.get("validation")
    if (
        not isinstance(validation, dict)
        or validation.get("db_integrity") is not True
        or validation.get("counts_match") is not True
        or validation.get("details_expired_filtered") is not True
        or validation.get("errors") != []
    ):
        raise RestoreCheckError(
            "backup manifest does not contain a successful validation record"
        )
    refs: set[str] = set()
    for item in manifest["artifacts"]:
        if not isinstance(item, dict):
            raise RestoreCheckError("backup manifest has an invalid artifact entry")
        ref = item.get("artifact_ref")
        bundle_path = item.get("bundle_path")
        if (
            not isinstance(ref, str)
            or not _ARTIFACT_REF.fullmatch(ref)
            or ref in refs
            or not isinstance(bundle_path, str)
        ):
            raise RestoreCheckError(
                "backup manifest has duplicate or incomplete artifact entries"
            )
        refs.add(ref)
        _safe_bundle_file(resolved_bundle, bundle_path)
    return manifest


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = int(
            connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        )
    return counts


def _state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "posted_count": int(
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
        "correction_count": int(
            connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE action = 'correction'"
            ).fetchone()[0]
        ),
        "run_count": int(connection.execute("SELECT COUNT(*) FROM run").fetchone()[0]),
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _result_from_report(
    report: dict[str, Any],
    *,
    directory: Path,
    backup_directory: Path,
    already_checked: bool,
) -> RestoreCheckResult:
    required = {
        "checked_at",
        "integrity_check",
        "foreign_key_errors",
        "schema_version",
        "table_counts_match",
        "expired_artifacts",
        "expired_aggregates",
        "posted_count",
        "active_reservations",
        "pending_reports",
        "correction_count",
        "run_count",
    }
    if report.get("report_version") != _REPORT_VERSION or not required.issubset(report):
        raise RestoreCheckError("existing monthly restore-check report is incomplete")
    if (
        report.get("selection_allowed") is not False
        or report.get("old_tasks_enabled") is not None
        or report.get("old_task_activation_attempts") != 0
        or report.get("old_task_state_observed") is not False
    ):
        raise RestoreCheckError(
            "restore-check report misstates task activation or observation"
        )
    if report.get("requires_supplementation") is not True:
        raise RestoreCheckError(
            "restore-check report is missing the supplementation gate"
        )
    if report.get("integrity_check") != "ok" or report.get("foreign_key_errors") != 0:
        raise RestoreCheckError(
            "restore-check report contains a failed database validation"
        )
    if report.get("table_counts_match") is not True:
        raise RestoreCheckError("restore-check report contains a table-count mismatch")
    restored_data = directory / "restored-data"
    database_path = restored_data / "state.sqlite"
    if not database_path.is_file() or database_path.is_symlink():
        raise RestoreCheckError("monthly restore-check database is missing")
    return RestoreCheckResult(
        directory=directory,
        database_path=database_path,
        checked_at=_parse_jst(report["checked_at"], name="checked_at"),
        backup_directory=backup_directory,
        integrity_check=str(report["integrity_check"]),
        foreign_key_errors=int(report["foreign_key_errors"]),
        schema_version=int(report["schema_version"]),
        table_counts_match=bool(report["table_counts_match"]),
        expired_artifacts=int(report["expired_artifacts"]),
        expired_aggregates=int(report["expired_aggregates"]),
        posted_count=int(report["posted_count"]),
        active_reservations=int(report["active_reservations"]),
        pending_reports=int(report["pending_reports"]),
        correction_count=int(report["correction_count"]),
        run_count=int(report["run_count"]),
        file_deletion_failures=tuple(report.get("file_deletion_failures", [])),
        expired_trace_events=int(report.get("expired_trace_events", 0)),
        expired_trace_files=int(report.get("expired_trace_files", 0)),
        trace_cleanup_failures=int(report.get("trace_cleanup_failures", 0)),
        selection_allowed=False,
        requires_supplementation=True,
        old_tasks_enabled=None,
        old_task_activation_attempts=0,
        old_task_state_observed=False,
        already_checked=already_checked,
    )


def _prepare_isolated_database(
    bundle: Path,
    stage_directory: Path,
    final_directory: Path,
    manifest: dict[str, Any],
    now: datetime,
) -> tuple[RetentionResult, dict[str, int], int, bool]:
    restored_data = stage_directory / "restored-data"
    restored_data.mkdir()
    staged_db_path = restored_data / "state.sqlite"
    source = sqlite3.connect(f"file:{bundle / 'state.sqlite'}?mode=ro", uri=True)
    target = sqlite3.connect(staged_db_path)
    try:
        source.backup(target)
        target.commit()
    except sqlite3.Error as exc:
        raise RestoreCheckError(
            "SQLite backup API could not copy the saved database"
        ) from exc
    finally:
        source.close()
        target.close()

    connection = sqlite3.connect(staged_db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        artifact_root = restored_data / "artifacts"
        for item in manifest["artifacts"]:
            source_path = _safe_bundle_file(bundle, str(item["bundle_path"]))
            destination = artifact_root / str(item["artifact_ref"]) / source_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            connection.execute(
                "UPDATE artifact SET path = ? WHERE artifact_ref = ?",
                (str(destination.resolve()), str(item["artifact_ref"])),
            )

        included_refs = {str(item["artifact_ref"]) for item in manifest["artifacts"]}
        rows = connection.execute(
            "SELECT artifact_ref, status FROM artifact"
        ).fetchall()
        for row in rows:
            if row["artifact_ref"] in included_refs:
                continue
            if not _ARTIFACT_REF.fullmatch(str(row["artifact_ref"])):
                raise RestoreCheckError(
                    "database contains an unsafe artifact reference"
                )
            placeholder = restored_data / "expired" / f"{row['artifact_ref']}.expired"
            connection.execute(
                """
                UPDATE artifact
                SET path = ?, status = CASE WHEN status = 'available' THEN 'expired' ELSE status END
                WHERE artifact_ref = ?
                """,
                (str(placeholder), row["artifact_ref"]),
            )
        connection.commit()

        original_counts = {
            str(name): int(count) for name, count in manifest["table_counts"].items()
        }
        before_retention_counts = _table_counts(connection)
        if before_retention_counts != original_counts:
            raise RestoreCheckError(
                "restored SQLite table counts differ from the backup manifest"
            )
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema_version != int(manifest["schema_version"]):
            raise RestoreCheckError(
                "restored SQLite schema version differs from the backup manifest"
            )
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise RestoreCheckError("restored SQLite integrity_check did not return ok")
        fk_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RestoreCheckError(
                "restored SQLite database contains foreign-key violations"
            )

        settings_versions = [
            str(row[0])
            for row in connection.execute(
                "SELECT version FROM settings_version ORDER BY version"
            )
        ]
        if settings_versions != manifest["settings_versions"]:
            raise RestoreCheckError(
                "restored settings versions differ from the backup manifest"
            )
        state_before_retention = _state_counts(connection)
        operational = manifest["operational_counts"]
        expected_state = {
            "posted_count": int(operational.get("posted", -1)),
            "active_reservations": int(operational.get("active_reservations", -1)),
            "pending_reports": int(operational.get("pending_reports", -1)),
            "correction_count": int(operational.get("corrections", -1)),
            "run_count": int(operational.get("runs", -1)),
        }
        if state_before_retention != expected_state:
            raise RestoreCheckError(
                "restored posting or pending state differs from the backup manifest"
            )

        retention = apply_retention(connection, data_root=restored_data, now=now)
        state = _state_counts(connection)
        if state != state_before_retention:
            raise RestoreCheckError(
                "retention changed persistent posting, reservation, or report state"
            )
        after_counts = _table_counts(connection)
        for table in (
            "settings_version",
            "run",
            "offer",
            "draft",
            "reservation",
            "publication_event",
            "human_action",
            "exclusion_key",
        ):
            if after_counts.get(table) != original_counts.get(table):
                raise RestoreCheckError(f"retention changed protected table {table}")

        final_data_root = final_directory / "restored-data"
        artifact_paths = connection.execute(
            "SELECT artifact_ref, path FROM artifact"
        ).fetchall()
        for row in artifact_paths:
            stored_path = Path(str(row["path"]))
            try:
                relative_path = stored_path.relative_to(restored_data)
            except ValueError as exc:
                raise RestoreCheckError(
                    "restored artifact path escaped the isolated data directory"
                ) from exc
            if not relative_path.parts:
                raise RestoreCheckError(
                    "restored artifact path must identify a child of the data directory"
                )
            connection.execute(
                "UPDATE artifact SET path = ? WHERE artifact_ref = ?",
                (str(final_data_root / relative_path), str(row["artifact_ref"])),
            )
        connection.commit()
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if (
            integrity != "ok"
            or connection.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise RestoreCheckError(
                "isolated database failed post-retention integrity checks"
            )
        return retention, state, schema_version, True
    finally:
        connection.close()


def check_monthly_restore(
    backup_directory: str | Path,
    *,
    backup_root: str | Path,
    now: str | datetime,
    fixture_mode: bool = True,
    reuse_existing: bool = False,
) -> RestoreCheckResult:
    """Copy a daily backup to a separate monthly directory and validate it.

    The function accepts a backup generation, never a live database path. The
    check is fail-closed: restored state always requires latest posting/report
    supplementation before a later daily selection can be enabled.
    """

    current = _parse_jst(now, name="now")
    root = Path(backup_root).expanduser().resolve()
    _validate_fixture_root(root, fixture_mode)
    raw_bundle = Path(backup_directory).expanduser()
    if raw_bundle.is_symlink():
        raise RestoreCheckError("backup bundle cannot be a symbolic link")
    bundle = raw_bundle.resolve()
    manifest = _validate_bundle(bundle, root)
    month = current.strftime("%Y-%m")
    restore_root = root / "restore-check"
    if restore_root.exists() and (
        restore_root.is_symlink() or not restore_root.is_dir()
    ):
        raise RestoreCheckError("restore-check root is not a regular directory")
    restore_root.mkdir(parents=True, exist_ok=True)
    final_directory = restore_root / month
    report_path = final_directory / "report.json"

    def existing_result() -> RestoreCheckResult | None:
        if final_directory.is_symlink():
            raise RestoreCheckError(
                "monthly restore-check directory cannot be a symbolic link"
            )
        if report_path.is_file() and not report_path.is_symlink():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RestoreCheckError(
                    "existing monthly restore-check report cannot be read"
                ) from exc
            try:
                reported_bundle = Path(
                    str(report.get("backup_directory", ""))
                ).resolve()
            except (OSError, RuntimeError, ValueError) as exc:
                raise RestoreCheckError(
                    "existing monthly restore-check backup cannot be resolved"
                ) from exc
            if reported_bundle != bundle:
                if not reuse_existing:
                    raise RestoreCheckError(
                        "this month already has a restore-check for another backup"
                    )
                if reported_bundle.parent != root or not (
                    _DAILY_DIRECTORY.fullmatch(reported_bundle.name)
                    or _FINAL_DAILY_DIRECTORY.fullmatch(reported_bundle.name)
                ):
                    raise RestoreCheckError(
                        "existing monthly restore-check references an unsafe backup"
                    )
            return _result_from_report(
                report,
                directory=final_directory,
                backup_directory=reported_bundle,
                already_checked=True,
            )
        if final_directory.exists() or final_directory.is_symlink():
            raise RestoreCheckError(
                "a partial monthly restore-check exists; refusing to overwrite it"
            )
        return None

    completed = existing_result()
    if completed is not None:
        return completed

    lock_directory = restore_root / f".{month}.lock"
    try:
        lock_directory.mkdir()
    except FileExistsError as exc:
        raise RestoreCheckError(
            "a monthly restore-check is already in progress"
        ) from exc
    stage_directory = restore_root / f".{month}.{uuid.uuid4().hex}.staging"
    try:
        completed = existing_result()
        if completed is not None:
            return completed
        stage_directory.mkdir()
        (
            retention,
            state,
            schema_version,
            table_counts_match,
        ) = _prepare_isolated_database(
            bundle, stage_directory, final_directory, manifest, current
        )
        report: dict[str, Any] = {
            "report_version": _REPORT_VERSION,
            "checked_at": current.isoformat(),
            "backup_directory": str(bundle),
            "database_path": str(final_directory / "restored-data" / "state.sqlite"),
            "integrity_check": "ok",
            "foreign_key_errors": 0,
            "schema_version": schema_version,
            "table_counts_match": table_counts_match,
            "expired_artifacts": retention.expired_artifacts,
            "expired_aggregates": retention.expired_aggregates,
            "file_deletion_failures": list(retention.file_deletion_failures),
            "expired_trace_events": retention.expired_trace_events,
            "expired_trace_files": retention.expired_trace_files,
            "trace_cleanup_failures": retention.trace_cleanup_failures,
            **state,
            "selection_allowed": False,
            "requires_supplementation": True,
            "old_tasks_enabled": None,
            "old_task_activation_attempts": 0,
            "old_task_state_observed": False,
            "supplementation_required": [
                "latest_posting_and_report_evidence",
                "pending_report_cohort_application",
                "old_task_disabled_state_confirmation",
            ],
        }
        _write_json(stage_directory / "report.json", report)
        os.replace(stage_directory, final_directory)
        return _result_from_report(
            report,
            directory=final_directory,
            backup_directory=bundle,
            already_checked=False,
        )
    except BaseException as exc:
        if stage_directory.exists():
            shutil.rmtree(stage_directory, ignore_errors=True)
        if isinstance(exc, RestoreCheckError):
            raise
        raise RestoreCheckError("isolated monthly restore-check failed") from exc
    finally:
        if lock_directory.exists():
            shutil.rmtree(lock_directory, ignore_errors=True)
