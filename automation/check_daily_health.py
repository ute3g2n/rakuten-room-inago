"""Scheduled or manual read-only health assessment."""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.health_service import evaluate_daily_health


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(timezone(timedelta(hours=9))).date().isoformat())
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-save", action="store_true", help="Evaluate without updating health history or status")
    args = parser.parse_args(argv)
    target = date.fromisoformat(args.date)
    root = Path(__file__).resolve().parents[1] / "rakuten-room-kai/orchestrator"
    result = evaluate_daily_health(root, target, save=not args.no_save)
    print(json.dumps(result, ensure_ascii=False) if args.json else
          f"{result['target_date']} {result['status']} run_id={result['run_id']}")
    return 0 if result["status"] in {"healthy", "scheduled"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
