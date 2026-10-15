"""Redacted, read-only observations of the active daily process."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import Callable

from automation.inago.run_lock import read_lock_owner


_DAILY_CHECKPOINT_KEY = "daily_orchestration"
_SAFE_FAILURE_KINDS = {"auth_wait", "save_error"}
_SAFE_PHASES = {"initial", "additional", "finished"}
_MAX_OBSERVATION_BYTES = 16 * 1024
_SQLITE_HEADER = b"SQLite format 3\x00"


def open_readonly_operations_connection(
    database_path: str | Path, *, timeout: float = 2
) -> sqlite3.Connection:
    """Open a consistent read-only SQLite view without creating sidecars."""

    database = Path(database_path)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    try:
        from automation.inago.production_runtime import _has_reparse_point

        if _has_reparse_point(database) or not database.is_file():
            raise sqlite3.OperationalError("operations database is unavailable")
        with database.open("rb") as source:
            header = source.read(100)
        if len(header) < 100 or header[:16] != _SQLITE_HEADER:
            raise sqlite3.DatabaseError("operations database header is invalid")
        wal_mode = header[18:20] == b"\x02\x02"
        wal_path = Path(f"{database}-wal")
        shm_path = Path(f"{database}-shm")
        journal_path = Path(f"{database}-journal")
        for sidecar in (wal_path, shm_path, journal_path):
            if _has_reparse_point(sidecar):
                raise sqlite3.OperationalError("operations sidecar path is invalid")
        wal_exists = wal_path.exists()
        shm_exists = shm_path.exists()
        journal_exists = journal_path.exists()
        if journal_exists and journal_path.stat().st_size > 0:
            raise sqlite3.OperationalError("operations rollback journal is pending")
        if wal_mode:
            if wal_exists != shm_exists:
                raise sqlite3.OperationalError("operations WAL sidecars are incomplete")
            immutable = not wal_exists
        else:
            if wal_exists or shm_exists:
                raise sqlite3.OperationalError("unexpected operations WAL sidecars")
            immutable = True
        resolved = database.resolve(strict=True)
        query = "mode=ro&immutable=1" if immutable else "mode=ro"
        connection = sqlite3.connect(
            f"{resolved.as_uri()}?{query}",
            uri=True,
            timeout=timeout,
            isolation_level=None,
        )
        if (wal_path.exists(), shm_path.exists()) != (wal_exists, shm_exists):
            connection.close()
            raise sqlite3.OperationalError("operations WAL state changed while opening")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, sqlite3.Error):
            raise
        raise sqlite3.OperationalError(
            "operations database cannot be inspected"
        ) from exc


def _timestamp(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("operations timestamps must include a timezone")
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    if len(encoded) > _MAX_OBSERVATION_BYTES:
        raise ValueError("operations observation is too large")
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def read_operations_observation(path: str | Path) -> dict[str, object] | None:
    """Load only bounded, allowlisted monitoring fields; never expose checkpoint data."""

    try:
        raw = Path(path).read_bytes()
        if len(raw) > _MAX_OBSERVATION_BYTES:
            return None
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
    ):
        return None
    run_id = payload.get("run_id")
    target_date = payload.get("target_date")
    run_status = payload.get("run_status")
    if not all(isinstance(value, str) and value for value in (run_id, target_date)):
        return None
    if not isinstance(run_status, str) or run_status not in {
        "created",
        "running",
        "complete",
        "partial",
        "failed",
        "shortfall",
        "missed",
    }:
        return None
    phase = payload.get("phase")
    failure_kind = payload.get("failure_kind")
    result: dict[str, object] = {
        "run_id": run_id,
        "target_date": target_date,
        "run_status": run_status,
        "phase": (
            phase if isinstance(phase, str) and phase in _SAFE_PHASES else "unknown"
        ),
        "checkpoint_digest": payload.get("checkpoint_digest"),
        "heartbeat_at": payload.get("heartbeat_at"),
        "progress_at": payload.get("progress_at"),
        "monitor_updated_at": payload.get("monitor_updated_at"),
    }
    if isinstance(failure_kind, str) and failure_kind in _SAFE_FAILURE_KINDS:
        result["failure_kind"] = failure_kind
    return result


class DailyOperationsMonitor:
    """Poll the run checkpoint while this process owns the Windows daily lock."""

    def __init__(
        self,
        database_path: str | Path,
        observation_path: str | Path,
        *,
        target_date: str,
        interval_seconds: float = 30,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.database_path = Path(database_path)
        self.observation_path = Path(observation_path)
        self.target_date = target_date
        self.interval_seconds = interval_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_digest: str | None = None
        self._last_progress_at: str | None = None
        self._run_id: str | None = None

    def _safe_observation_path(self) -> bool:
        try:
            from automation.inago.production_runtime import _has_reparse_point

            runtime_root = self.database_path.resolve(strict=True).parent
            observation_path = self.observation_path.resolve(strict=False)
            return observation_path.is_relative_to(
                runtime_root
            ) and not _has_reparse_point(self.observation_path)
        except (OSError, RuntimeError, ValueError):
            return False

    def start(self) -> None:
        self.observe_once()
        self._thread = threading.Thread(
            target=self._run,
            name="inago-daily-operations-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        self.observe_once(allow_released_terminal=True)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self.observe_once()

    def observe_once(self, *, allow_released_terminal: bool = False) -> bool:
        """Write one observation only when SQLite and process ownership are verified."""

        connection: sqlite3.Connection | None = None
        try:
            if not self._safe_observation_path():
                return False
            connection = open_readonly_operations_connection(
                self.database_path, timeout=2
            )
            row = connection.execute(
                """
                SELECT run_id, target_date, status, checkpoint, attempt_used,
                       committed_count, metrics_status
                  FROM run
                 WHERE mode='daily' AND target_date=?
                 ORDER BY started_at DESC LIMIT 1
                """,
                (self.target_date,),
            ).fetchone()
            if row is None:
                return False
            run_id = str(row["run_id"])
            lock_observation = read_lock_owner(
                self.observation_path.parent.parent / ".daily-run-owner.json"
            )
            owns_lock = (
                lock_observation.status == "present"
                and lock_observation.owner is not None
                and lock_observation.owner.pid == os.getpid()
                and lock_observation.owner.run_id == run_id
            )
            if not owns_lock:
                if not allow_released_terminal or str(row["status"]) == "running":
                    return False
                if lock_observation.status != "missing":
                    return False

            checkpoint = row["checkpoint"]
            checkpoint_text = str(checkpoint) if checkpoint is not None else ""
            digest_input = "\0".join(
                (
                    run_id,
                    str(row["status"]),
                    checkpoint_text,
                    str(row["attempt_used"]),
                    str(row["committed_count"]),
                    str(row["metrics_status"]),
                )
            ).encode("utf-8")
            digest = hashlib.sha256(digest_input).hexdigest()
            now_text = _timestamp(self._now())
            previous = read_operations_observation(self.observation_path)
            same_run = previous is not None and previous.get("run_id") == run_id
            if same_run and digest == self._last_digest:
                progress_at = self._last_progress_at
            elif same_run and digest == previous.get("checkpoint_digest"):
                progress_at = previous.get("progress_at")
            else:
                progress_at = now_text
            self._run_id = run_id
            self._last_digest = digest
            self._last_progress_at = (
                progress_at if isinstance(progress_at, str) else now_text
            )

            phase = "unknown"
            failure_kind: str | None = None
            try:
                root = json.loads(checkpoint_text) if checkpoint_text else {}
                state = (
                    root.get(_DAILY_CHECKPOINT_KEY) if isinstance(root, dict) else None
                )
                if isinstance(state, dict):
                    candidate = state.get("phase")
                    if candidate in _SAFE_PHASES:
                        phase = candidate
                    for error in reversed(state.get("errors", [])):
                        if not isinstance(error, dict):
                            continue
                        code = error.get("code")
                        if code in {"auth_wait", "authentication_required"}:
                            failure_kind = "auth_wait"
                            break
                        if code in {"save_error", "database_save_failed"}:
                            failure_kind = "save_error"
                            break
                    report_apply = state.get("report_apply")
                    if isinstance(report_apply, dict):
                        error = report_apply.get("error")
                        if error in {"auth_wait", "save_error"}:
                            failure_kind = error
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

            payload: dict[str, object] = {
                "schema_version": 1,
                "run_id": run_id,
                "target_date": str(row["target_date"]),
                "run_status": str(row["status"]),
                "phase": phase,
                "process_pid": os.getpid(),
                "checkpoint_digest": digest,
                "heartbeat_at": now_text,
                "progress_at": self._last_progress_at,
                "monitor_updated_at": now_text,
            }
            if failure_kind in _SAFE_FAILURE_KINDS:
                payload["failure_kind"] = failure_kind
            _atomic_json(self.observation_path, payload)
            return True
        except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError):
            return False
        finally:
            if connection is not None:
                connection.close()
