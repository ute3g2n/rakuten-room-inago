"""Detect missed, stale and action-required daily runs from saved audit records."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from automation.atomic_io import append_jsonl_unique, atomic_write_json
from automation.config_loader import load_config
from automation.schema_validator import validate_document


JST = timezone(timedelta(hours=9))


def evaluate_daily_health(runtime_root: Path, target_date: date, now: datetime | None = None,
                          save: bool = True) -> dict:
    runtime = Path(runtime_root)
    config = load_config(runtime)
    checked = (now or datetime.now(JST)).astimezone(JST)
    hour, minute = map(int, config["daily_schedule"].split(":"))
    scheduled = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=JST)
    grace = scheduled + timedelta(minutes=config["health_grace_minutes"])
    manifests = list((runtime / "runs" / target_date.isoformat()).glob("*-daily-*/manifest.json"))
    candidates = []
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        events = path.parent / "events.jsonl"
        started = events.is_file() and any(json.loads(line).get("type") == "run_started"
                                           for line in events.read_text(encoding="utf-8").splitlines() if line.strip())
        if started:
            candidates.append((manifest, path))
    candidates.sort(key=lambda item: item[0]["started_at"], reverse=True)
    current = candidates[0][0] if candidates else None
    errors = []
    if target_date < date.fromisoformat(config["operation_start_date"]) or checked < scheduled:
        status = "scheduled"
    elif current is None:
        status = "scheduled" if checked < grace else "missed"
        if status == "missed":
            errors.append({"code": "MISSED_SCHEDULE", "detail": "予定時刻と猶予を過ぎてもdaily run_startedがない"})
    elif current["status"] == "running":
        heartbeat = datetime.fromisoformat(current["last_heartbeat_at"])
        if checked - heartbeat > timedelta(minutes=config["stale_heartbeat_minutes"]):
            status = "stale_heartbeat"
            errors.append({"code": "STALE_HEARTBEAT", "detail": "実行中runのheartbeatが古い"})
        else:
            status = "healthy"
    elif current["status"] in {"failed", "action_required", "partial"}:
        status = "action_required"
        errors.extend(current["errors"] or [{"code": "DAILY_PARTIAL", "detail": "日次runが完全成功していない"}])
    else:
        status = "healthy"
    result = {"target_date": target_date.isoformat(), "status": status,
              "checked_at": checked.isoformat(timespec="seconds"),
              "operation_start_date": config["operation_start_date"],
              "run_id": current["run_id"] if current else None, "errors": errors}
    validate_document("health-status", result)
    if save:
        atomic_write_json(runtime / "status/health.json", result)
        key = f"{result['target_date']}:{result['checked_at']}:{result['status']}:{result['run_id']}"
        append_jsonl_unique(runtime / "history" / f"health-{target_date:%Y-%m}.jsonl",
                            {"check_id": key, **result}, "check_id")
    return result
