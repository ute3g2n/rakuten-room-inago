"""Own the daily CLI child and persist a safe completion notice for maintenance."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo

from automation.inago.maintenance.store import (
    MaintenanceStore,
    MaintenanceStoreError,
)
from automation.inago.maintenance.paths import UnsafeMaintenancePathError
from automation.inago.maintenance.process_identity import (
    current_process_start_time,
    observe_process,
)


JST = ZoneInfo("Asia/Tokyo")
EXIT_RUNTIME_ERROR = 4
EXIT_DAILY_UNMET = 5
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ERROR_CLASS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_VALID_TRACE_STATUSES = {"healthy", "degraded", "unavailable", "not_written"}


class DailyLauncherError(RuntimeError):
    """A safe daily launcher failure code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DailyProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    pid: int | None


@dataclass(frozen=True, slots=True)
class DailyLaunchOutcome:
    launch_id: str
    exit_code: int
    notification_status: str
    summary: Mapping[str, object]
    stderr_summary: str


ProcessRunner = Callable[
    [Sequence[str], Path, Callable[[int], None]], DailyProcessResult
]


def _safe_code(value: object) -> str | None:
    if isinstance(value, str) and _CODE_PATTERN.fullmatch(value):
        return value
    return None


def _safe_error_class(value: object) -> str | None:
    if isinstance(value, str) and _ERROR_CLASS_PATTERN.fullmatch(value):
        return value
    return None


def _safe_id(value: object) -> str | None:
    if isinstance(value, str) and _ID_PATTERN.fullmatch(value):
        return value
    return None


def _parse_result(stdout: bytes, stderr: bytes) -> dict[str, object] | None:
    """Return one JSON object from child output without retaining raw output."""

    candidates: list[dict[str, object]] = []
    for raw in (stdout, stderr):
        try:
            lines = raw.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError:
            continue
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("status"), str):
                candidates.append(value)
                break
    if not candidates:
        return None
    # A runtime_error on stderr is more specific than an earlier stdout note.
    for candidate in candidates:
        if candidate.get("status") in {"runtime_error", "log_unavailable"}:
            return candidate
    return candidates[0]


def _extract_trace_status(payload: Mapping[str, object]) -> str:
    trace = payload.get("pipeline_trace")
    if not isinstance(trace, Mapping):
        phase_results = payload.get("phase_results")
        trace = (
            phase_results.get("trace") if isinstance(phase_results, Mapping) else None
        )
    if isinstance(trace, Mapping):
        value = trace.get("status")
        if isinstance(value, str) and value in _VALID_TRACE_STATUSES:
            return value
    return "not_written"


def _extract_failed_checks(payload: Mapping[str, object]) -> list[str]:
    values = payload.get("failed_checks")
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values[:32]:
        code = _safe_code(value)
        if code is not None:
            result.append(code)
    return result


def _extract_secondary_classes(payload: Mapping[str, object]) -> list[str]:
    values = payload.get("secondary_errors")
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values[:32]:
        if not isinstance(value, Mapping):
            continue
        error_class = _safe_error_class(value.get("code"))
        if error_class is not None:
            result.append(error_class)
    return result


def _summarize_result(
    *,
    launch_id: str,
    target_date: str,
    process_result: DailyProcessResult,
    completed_at: datetime,
) -> tuple[dict[str, object], str]:
    payload = _parse_result(process_result.stdout, process_result.stderr)
    raw_status = (
        payload.get("overall_status", payload.get("status")) if payload else None
    )
    overall_status = _safe_code(raw_status) or "unknown"
    daily_status = (
        _safe_code(payload.get("daily_status")) if payload is not None else None
    ) or overall_status
    source_run_id = None
    identity_invalid = False
    failure_phase = None
    reason_code = None
    primary_error_class = None
    failed_checks: list[str] = []
    secondary_error_classes: list[str] = []
    if payload is not None:
        result_target = payload.get("target_date")
        if result_target is not None and result_target != target_date:
            identity_invalid = True
        elif result_target is None and overall_status in {
            "complete",
            "partial",
            "shortfall",
            "not_due",
        }:
            identity_invalid = True
        raw_run_id = payload.get("run_id", payload.get("source_run_id"))
        if raw_run_id is not None:
            source_run_id = _safe_id(raw_run_id)
            if source_run_id is None:
                identity_invalid = True
        if (
            overall_status in {"complete", "partial", "shortfall"}
            and source_run_id is None
        ):
            identity_invalid = True
        failure_phase = _safe_code(payload.get("phase"))
        primary_error = payload.get("primary_error")
        if isinstance(primary_error, Mapping):
            failure_phase = failure_phase or _safe_code(primary_error.get("phase"))
            primary_error_class = _safe_error_class(primary_error.get("code"))
        if payload.get("status") == "runtime_error":
            primary_error_class = _safe_error_class(payload.get("reason"))
            failure_phase = failure_phase or "daily"
        reason_code = _safe_code(payload.get("reason"))
        failed_checks = _extract_failed_checks(payload)
        secondary_error_classes = _extract_secondary_classes(payload)
        if payload.get("status") == "runtime_error":
            overall_status = "failed"
            daily_status = "failed"
        elif payload.get("status") == "blocked":
            overall_status = "blocked"
            daily_status = "unknown"
    if identity_invalid:
        source_run_id = None
        overall_status = "unknown"
        daily_status = "unknown"
        reason_code = "daily_result_identity_invalid"
        failure_phase = "completion"
        primary_error_class = "DailyResultIdentityError"

    if payload is None:
        overall_status = "unknown"
        daily_status = "unknown"
        reason_code = "daily_result_unavailable"
        primary_error_class = "DailyResultUnavailableError"
        failure_phase = "completion"
    elif reason_code is None:
        if primary_error_class is not None:
            reason_code = "daily_runtime_error"
        elif overall_status in {"partial", "shortfall"}:
            reason_code = "daily_target_unmet"
        elif overall_status not in {"complete"}:
            reason_code = "daily_result_failed"

    if payload is not None and payload.get("status") == "blocked":
        launch_status = "blocked"
    elif (
        not identity_invalid
        and process_result.returncode in {0, EXIT_DAILY_UNMET}
        and overall_status in {"complete", "partial", "shortfall", "not_due"}
    ):
        launch_status = "complete"
    elif payload is None:
        launch_status = "unknown"
    else:
        launch_status = "failed"

    if launch_status == "complete" and overall_status == "complete":
        check_status = "normal"
        reason_code = None
    elif launch_status == "complete" and overall_status in {"partial", "shortfall"}:
        check_status = "normal_shortfall"
    elif launch_status == "complete" and overall_status == "not_due":
        check_status = "unknown"
        reason_code = "daily_not_due"
    else:
        check_status = "incomplete"
        if reason_code is None:
            reason_code = "daily_result_failed"

    trace_status = (
        _extract_trace_status(payload) if payload is not None else "not_written"
    )
    summary: dict[str, object] = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": launch_id,
        "target_date": target_date,
        "status": launch_status,
        "exit_code": process_result.returncode,
        "source_run_id": source_run_id,
        "overall_status": overall_status,
        "daily_status": daily_status,
        "reason_code": reason_code,
        "failure_phase": failure_phase,
        "failed_checks": failed_checks,
        "primary_error_class": primary_error_class,
        "secondary_error_classes": secondary_error_classes,
        "trace_status": trace_status,
    }
    summary["completed_at"] = completed_at.astimezone(JST).isoformat()
    safe_stderr = json.dumps(
        {
            "source_launch_id": launch_id,
            "status": launch_status,
            "reason_code": reason_code,
            "primary_error_class": primary_error_class,
            "trace_status": trace_status,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    summary["_check_status"] = check_status
    return summary, safe_stderr


def _completion_is_consistent(summary: Mapping[str, object], check_status: str) -> bool:
    """Reject a child result whose status, check, and process code disagree."""

    launch_status = summary["status"]
    overall_status = summary["overall_status"]
    daily_status = summary["daily_status"]
    exit_code = summary["exit_code"]
    reason_code = summary["reason_code"]
    if launch_status == "complete":
        if check_status == "normal":
            return (
                overall_status == "complete"
                and daily_status == "complete"
                and exit_code == 0
                and reason_code is None
            )
        if check_status == "normal_shortfall":
            return (
                overall_status in {"partial", "shortfall"}
                and daily_status in {"partial", "shortfall"}
                and exit_code == EXIT_DAILY_UNMET
            )
        if check_status == "unknown":
            return (
                overall_status == "not_due"
                and daily_status == "not_due"
                and exit_code == 0
                and reason_code == "daily_not_due"
            )
        return False
    if check_status != "incomplete":
        return False
    if launch_status == "blocked":
        return (
            overall_status == "blocked"
            and daily_status == "unknown"
            and exit_code == EXIT_RUNTIME_ERROR
        )
    if launch_status == "unknown":
        return (
            overall_status == "unknown" and daily_status == "unknown" and exit_code != 0
        )
    return (
        launch_status == "failed"
        and overall_status
        not in {"blocked", "unknown", "complete", "partial", "shortfall", "not_due"}
        and exit_code not in {0, EXIT_DAILY_UNMET}
    )


def _contract_failure_result(pid: int | None, target_date: str) -> DailyProcessResult:
    return DailyProcessResult(
        returncode=EXIT_RUNTIME_ERROR,
        stdout=json.dumps(
            {
                "status": "runtime_error",
                "reason": "DailyResultContractError",
                "target_date": target_date,
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        stderr=b"",
        pid=pid,
    )


def run_daily_process(
    command: Sequence[str], cwd: Path, on_started: Callable[[int], None]
) -> DailyProcessResult:
    """Wait for the daily CLI without applying a whole-run deadline."""

    if not command or any(not isinstance(value, str) or not value for value in command):
        raise DailyLauncherError("daily_command_invalid")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        try:
            on_started(int(process.pid))
        except Exception:
            try:
                process.kill()
            except OSError:
                pass
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            raise
        try:
            stdout, stderr = process.communicate()
        except Exception:
            try:
                process.kill()
            except OSError:
                pass
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            raise
    except OSError:
        raise
    return DailyProcessResult(
        returncode=int(process.returncode),
        stdout=stdout,
        stderr=stderr,
        pid=int(process.pid),
    )


def run_daily_launch(
    store: MaintenanceStore,
    *,
    data_root: str | Path,
    config_path: str | Path,
    now: datetime,
    process_runner: ProcessRunner = run_daily_process,
    command: Sequence[str] | None = None,
    cwd: str | Path,
) -> DailyLaunchOutcome:
    """Persist launch ownership, run the CLI child, then save one final notice."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise DailyLauncherError("launch_clock_must_be_timezone_aware")
    launched_at = now.astimezone(JST)
    target_date = launched_at.date().isoformat()
    launch_id = uuid.uuid4().hex
    try:
        store.record_daily_launch_start(
            source_launch_id=launch_id,
            target_date=target_date,
            owner_pid=os.getpid(),
            owner_start_time=current_process_start_time(),
            started_at=launched_at,
        )
    except MaintenanceStoreError as exc:
        raise DailyLauncherError(exc.code) from exc
    except (OSError, sqlite3.Error) as exc:
        raise DailyLauncherError("daily_launch_start_unavailable") from exc

    daily_command = (
        tuple(command)
        if command is not None
        else (
            sys.executable,
            "-B",
            "-m",
            "automation.inago.cli",
            "daily",
            "--environment",
            "production",
            "--config",
            str(Path(config_path)),
            "--data-root",
            str(Path(data_root)),
        )
    )
    child_pid: int | None = None
    try:

        def record_child_started(pid: int) -> None:
            nonlocal child_pid
            store.record_daily_launch_child(
                source_launch_id=launch_id,
                child_pid=pid,
                child_start_time=observe_process(pid).start_time,
            )
            child_pid = pid

        process_result = process_runner(daily_command, Path(cwd), record_child_started)
        if (
            isinstance(process_result.returncode, bool)
            or not isinstance(process_result.returncode, int)
            or not 0 <= process_result.returncode <= 255
        ):
            raise DailyLauncherError("daily_process_exit_code_invalid")
    except Exception as exc:
        error_class = type(exc).__name__
        process_result = DailyProcessResult(
            returncode=EXIT_RUNTIME_ERROR,
            stdout=b"",
            stderr=json.dumps(
                {"status": "runtime_error", "reason": error_class}
            ).encode("utf-8"),
            pid=child_pid,
        )

    completion_child_pid = (
        process_result.pid if process_result.pid is not None else child_pid
    )

    completed_at = datetime.now(JST)
    summary, safe_stderr = _summarize_result(
        launch_id=launch_id,
        target_date=target_date,
        process_result=process_result,
        completed_at=completed_at,
    )
    if not _completion_is_consistent(summary, str(summary["_check_status"])):
        process_result = _contract_failure_result(completion_child_pid, target_date)
        summary, safe_stderr = _summarize_result(
            launch_id=launch_id,
            target_date=target_date,
            process_result=process_result,
            completed_at=completed_at,
        )
    check_status = str(summary.pop("_check_status"))
    result_summary = dict(summary)
    result_summary.pop("completed_at", None)
    notification_status = "saved"
    try:
        store.record_daily_launch_completion(
            source_launch_id=launch_id,
            target_date=target_date,
            child_pid=completion_child_pid,
            source_run_id=(
                str(summary["source_run_id"])
                if summary["source_run_id"] is not None
                else None
            ),
            launch_status=str(summary["status"]),
            exit_code=process_result.returncode,
            completed_at=completed_at,
            check_status=check_status,
            reason_code=(
                str(summary["reason_code"])
                if summary["reason_code"] is not None
                else None
            ),
            result_summary=result_summary,
        )
    except (MaintenanceStoreError, OSError, sqlite3.Error, ValueError):
        notification_status = "unavailable"
        safe_stderr = json.dumps(
            {
                "source_launch_id": launch_id,
                "status": "notification_unavailable",
                "reason_code": "completion_save_failed",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    return DailyLaunchOutcome(
        launch_id=launch_id,
        exit_code=process_result.returncode,
        notification_status=notification_status,
        summary=summary,
        stderr_summary=safe_stderr,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inago-daily-launcher")
    parser.add_argument("--config", required=True)
    parser.add_argument("--data-root", required=True)
    return parser


def _emit(stream, payload: Mapping[str, object]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_root = Path(args.data_root).expanduser()
    try:
        store = MaintenanceStore(
            data_root,
            daily_database_path=data_root / "state.sqlite",
        )
        if not store.database_path.is_file():
            raise DailyLauncherError("maintenance_database_unavailable")
        outcome = run_daily_launch(
            store,
            data_root=data_root,
            config_path=Path(args.config).expanduser(),
            now=datetime.now(JST),
            cwd=Path(__file__).resolve().parents[3],
        )
    except (
        DailyLauncherError,
        MaintenanceStoreError,
        UnsafeMaintenancePathError,
        OSError,
    ) as exc:
        reason = getattr(exc, "code", "daily_launcher_unavailable")
        _emit(sys.stderr, {"status": "blocked", "reason_code": reason})
        return EXIT_RUNTIME_ERROR
    _emit(sys.stdout, outcome.summary)
    sys.stderr.write(outcome.stderr_summary + "\n")
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
