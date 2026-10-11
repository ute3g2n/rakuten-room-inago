"""Temporary dashboard fixture and localhost server for Step 13 browser tests."""

import argparse
import json
import shutil
from pathlib import Path

from automation.dashboard_service import upsert_dashboard
from automation.dashboard_server import make_server


ROOT = Path(__file__).resolve().parents[1]
GENRES = ["家事ラク用品", "住まい・収納・インテリア", "子育て・キッズ用品"]
DAYS = ["2026-09-25", "2026-09-26", "2026-09-27"]


def prepare(workspace: Path) -> Path:
    data = workspace / "rakuten-room-kai"
    data.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "rakuten-room-kai/dashboard-assets", data / "dashboard-assets")
    products = []
    drafts = []
    for number, day in enumerate(DAYS, 1):
        pid = f"P{number:02}"
        url = f"https://item.rakuten.co.jp/test-shop/item-{number}/"
        name = "商品<script>window.__xss=1</script>😺" if number == 3 else f"テスト商品{number}"
        body = "一行目\n<img src=x onerror=window.__xss=2>😺" if number == 3 else f"商品{number}の本文😺"
        products.append({"id": pid, "name": name, "url": url, "normalized_url": url,
                         "genre": GENRES[number - 1], "status": "eligible", "item_code": f"test-shop/item-{number}",
                         "model_candidate": None, "checked_at": "2026-09-25", "posted_at": None})
        drafts.append({"product_id": pid, "url": url, "body": body, "status": "accepted", "checked_at": "2026-09-25"})
    (data / "products.json").write_text(json.dumps({"version": 1, "products": products, "next_id": 4}, ensure_ascii=False), encoding="utf-8")
    (data / "drafts.json").write_text(json.dumps({"version": 1, "drafts": drafts}, ensure_ascii=False), encoding="utf-8")
    (data / "post-status.json").write_text(json.dumps({"version": 1, "items": {}}, ensure_ascii=False), encoding="utf-8")
    (data / "posts-log.jsonl").write_text("", encoding="utf-8")
    for number, day in enumerate(DAYS, 1):
        product, draft = products[number - 1], drafts[number - 1]
        plan = {"target_date": day, "posts": [{"product_id": product["id"], "name": product["name"],
                "url": product["url"], "genre": product["genre"], "body": draft["body"],
                "scheduled_at": f"{day}T08:00:00+09:00", "role": "紹介"}],
                "shortfall": [{"code": "INVENTORY_SHORT", "detail": "fixture"}], "applied_learning": []}
        upsert_dashboard(data, plan)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "serve", "regenerate"))
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    data = args.workspace.resolve() / "rakuten-room-kai"
    if args.command == "prepare":
        prepare(args.workspace.resolve())
        print(json.dumps({"status": "ready", "data": str(data)}), flush=True)
    elif args.command == "serve":
        server = make_server(data, 0)
        print(json.dumps({"port": server.server_port}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    else:
        day = DAYS[-1]
        plan = json.loads((data / "daily-plans" / f"{day}.json").read_text(encoding="utf-8"))
        upsert_dashboard(data, plan)
        print(json.dumps({"status": "regenerated"}), flush=True)


if __name__ == "__main__":
    main()
