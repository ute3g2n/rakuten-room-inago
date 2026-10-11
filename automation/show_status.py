"""Show saved execution state without launching an LLM."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.status_service import query_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--flow")
    parser.add_argument("--date")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--run-id")
    parser.add_argument("--source", choices=("manual", "chat", "scheduler"), default="manual")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.date and not args.flow:
        parser.error("--date requires --flow")
    root = Path(__file__).resolve().parents[1] / "rakuten-room-kai/orchestrator"
    try:
        rows = query_status(root, latest=args.latest or not (args.flow or args.failed_only or args.run_id),
                            flow=args.flow, target_date=args.date, failed_only=args.failed_only,
                            days=args.days, run_id=args.run_id)
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    elif not rows:
        print("該当する実行履歴はありません")
    else:
        for row in rows:
            print(f"{row['target_date']} {row['flow']} {row['status']} run_id={row['run_id']}")
            print(f"開始 {row['started_at']} / 終了 {row['finished_at']} / 最終工程 {row['last_stage']}")
            print(f"準備済み {row['eligible_count'] if row['eligible_count'] is not None else '未集計'}件")
            for action in row["actions"]:
                print(f"本人対応: {action}")
            print(f"ログ: {row['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
