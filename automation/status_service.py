"""Read-only audit queries for CLI and chat status requests."""

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
RUN_ID = re.compile(r"^\d{8}-\d{6}-(?:daily|research|performance|setup|config-change|recovery|status)-[0-9a-f]{6}$")


def query_status(runtime_root: Path, *, latest: bool = False, flow: str | None = None,
                 target_date: str | None = None, failed_only: bool = False,
                 days: int = 7, run_id: str | None = None) -> list[dict]:
    runtime = Path(runtime_root)
    if run_id and not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    if target_date:
        date.fromisoformat(target_date)
    if days < 1 or days > 365:
        raise ValueError("days must be 1..365")
    paths = list(runtime.glob("runs/*/*/manifest.json"))
    rows = []
    cutoff = datetime.now(JST).date() - timedelta(days=days - 1)
    for path in paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if run_id and manifest["run_id"] != run_id:
            continue
        if flow and manifest["flow"] != flow:
            continue
        if target_date and manifest["target_date"] != target_date:
            continue
        if failed_only and (manifest["status"] not in {"failed", "action_required"} or
                            date.fromisoformat(manifest["target_date"]) < cutoff):
            continue
        inventory = path.parent / "outputs/inventory-brief.json"
        ready = None
        if inventory.is_file():
            try:
                ready = json.loads(inventory.read_text(encoding="utf-8")).get("eligible_count")
            except ValueError:
                ready = None
        actions = []
        for error in manifest.get("errors", []):
            code = error["code"]
            if code == "AUTH_REQUIRED":
                actions.append("本人がブラウザで楽天アフィリエイトへ再ログインする")
            elif code == "CAPTCHA_REQUIRED":
                actions.append("本人がブラウザで確認画面を完了する")
            elif code in {"INPUT_INVALID", "DATA_MISMATCH"}:
                actions.append("入力または台帳の不整合を確認する")
        setup_review = path.parent / "outputs/setup-review.json"
        if manifest["flow"] == "setup" and setup_review.is_file():
            try:
                if json.loads(setup_review.read_text(encoding="utf-8")).get("requires_user_review"):
                    actions.append("プロフィールと移行後の要確認項目を確認する")
            except ValueError:
                pass
        daily_report = path.parent / "outputs/daily-report.json"
        if manifest["flow"] == "daily" and daily_report.is_file():
            try:
                if json.loads(daily_report.read_text(encoding="utf-8")).get("shortfall"):
                    actions.append("確認済み商品と下書きの不足を確認する")
            except ValueError:
                pass
        rows.append({"run_id": manifest["run_id"], "flow": manifest["flow"],
                     "target_date": manifest["target_date"], "status": manifest["status"],
                     "source": manifest["source"], "started_at": manifest["started_at"],
                     "finished_at": manifest["finished_at"], "last_stage": manifest["last_stage"],
                     "eligible_count": ready, "actions": sorted(set(actions)),
                     "manifest_path": str(path)})
    rows.sort(key=lambda item: (item["started_at"], item["run_id"]), reverse=True)
    return rows[:1] if latest else rows
