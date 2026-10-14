"""Atomically advance the sequential implementation ledger."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.atomic_io import atomic_write_json


JST = timezone(timedelta(hours=9))


def advance(step: str, status: str, files: list[str], checks: list[str], codes: list[int], blockers: list[str] | None = None) -> None:
    path = Path("rakuten-room-kai/implementation/execution-ledger.json")
    ledger = json.loads(path.read_text(encoding="utf-8"))
    entry = ledger["steps"][step]
    if entry["status"] == "success" and status != "success":
        raise ValueError("completed step cannot regress")
    if status == "success" and int(step) > 0 and ledger["steps"][f"{int(step)-1:02}"]["status"] != "success":
        raise ValueError("prior step is incomplete")
    now = datetime.now(JST).isoformat(timespec="seconds")
    entry.update(status=status, started_at=entry["started_at"] or now,
                 finished_at=now if status in {"success", "blocked", "failed"} else None,
                 report_path=f"rakuten-room-kai/implementation/reports/step-{step}.md" if status == "success" else None,
                 verification_commands=checks, exit_codes=codes,
                 changed_files=sorted(set(files)), blockers=blockers or [])
    ledger["current_step"] = step if status != "success" or step == "19" else f"{int(step)+1:02}"
    atomic_write_json(path, ledger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("step")
    parser.add_argument("status")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument("--code", action="append", type=int, default=[])
    parser.add_argument("--blocker", action="append", default=[])
    args = parser.parse_args()
    advance(args.step, args.status, args.file, args.check, args.code, args.blocker)
