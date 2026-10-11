"""Build one safe, date-ordered posting dashboard from validated plans."""

import html
import json
from datetime import date
from pathlib import Path

from automation import atomic_io
from automation.atomic_io import atomic_write_json, atomic_write_text
from automation.inventory_service import normalize_url
from automation.redaction import redact
from automation.schema_validator import SchemaValidationError, validate_document, validate_product_store


class DashboardError(ValueError):
    def __init__(self, detail: str):
        super().__init__(f"DATA_MISMATCH: {detail}")
        self.code = "DATA_MISMATCH"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_plan(root: Path, plan: dict, allowed_existing_ids: set[str]) -> None:
    try:
        validate_document("daily-plan", plan)
        products = _load(root / "products.json")
        drafts = _load(root / "drafts.json")
        validate_product_store(products)
        validate_document("drafts", drafts)
    except (OSError, ValueError, SchemaValidationError) as error:
        raise DashboardError(str(error)) from error
    product_by_id = {item["id"]: item for item in products["products"]}
    draft_by_id = {item["product_id"]: item for item in drafts["drafts"]}
    if len(draft_by_id) != len(drafts["drafts"]):
        raise DashboardError("duplicate draft ID")
    seen = set()
    for post in plan["posts"]:
        product_id = post["product_id"]
        if product_id in seen:
            raise DashboardError(f"duplicate product ID: {product_id}")
        seen.add(product_id)
        product = product_by_id.get(product_id)
        draft = draft_by_id.get(product_id)
        if not product or not draft or draft["status"] != "accepted":
            raise DashboardError(f"missing accepted product or draft: {product_id}")
        if product_id not in allowed_existing_ids:
            if product["status"] != "eligible" or not product["checked_at"] or not draft["checked_at"]:
                raise DashboardError(f"product is not confirmed eligible: {product_id}")
            product_age = (date.fromisoformat(plan["target_date"]) - date.fromisoformat(product["checked_at"])).days
            draft_age = (date.fromisoformat(plan["target_date"]) - date.fromisoformat(draft["checked_at"])).days
            if not (0 <= product_age < 7 and 0 <= draft_age < 7):
                raise DashboardError(f"product or draft confirmation is stale: {product_id}")
        if (product["status"] == "posted" or product["posted_at"]) and product_id not in allowed_existing_ids:
            raise DashboardError(f"posted product cannot be planned: {product_id}")
        if post["scheduled_at"][:10] != plan["target_date"]:
            raise DashboardError(f"scheduled date mismatch: {product_id}")
        if (post["name"] != product["name"] or post["genre"] != product["genre"]
                or post["body"] != draft["body"]
                or normalize_url(post["url"]) != normalize_url(product["url"])
                or normalize_url(post["url"]) != normalize_url(draft["url"])):
            raise DashboardError(f"ID, URL, or body mismatch: {product_id}")
    status = _load(root / "post-status.json")
    validate_document("post-status", status)
    if any(product_id not in allowed_existing_ids and product_id in status["items"]
           and status["items"][product_id]["ever_marked_posted"]
           for product_id in seen):
        raise DashboardError("previously posted product cannot be planned")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _render(root: Path, plans: list[dict], post_status: dict) -> str:
    navigation = []
    sections = []
    for index, plan in enumerate(plans):
        day = plan["target_date"]
        planned = len(plan["posts"])
        posted = sum(bool(post_status["items"].get(item["product_id"], {}).get("posted"))
                     for item in plan["posts"])
        navigation.append(f'<a href="#day-{day}" data-day-link="{day}">{day} <span>{posted}/{planned}</span></a>')
        cards = []
        for post in plan["posts"]:
            product_id = post["product_id"]
            body_id = f"body-{day}-{product_id}"
            cards.append(
                f'<article class="post-card" data-product-id="{product_id}">'
                f'<div class="post-meta"><strong>{_escape(product_id)}｜{_escape(post["name"])}</strong>'
                f'<span>{_escape(post["genre"])} · {_escape(post["scheduled_at"])} JST</span></div>'
                f'<a class="product-link" href="{_escape(post["url"])}" target="_blank" rel="noopener noreferrer">楽天の商品ページを開く</a>'
                f'<div id="{body_id}" class="copy-body">{_escape(post["body"])}</div>'
                f'<div class="post-actions"><button type="button" data-copy-id="{body_id}">本文をコピー</button>'
                f'<label><input type="checkbox" data-post-id="{product_id}" data-date="{day}" disabled> 投稿済み</label></div>'
                '</article>'
            )
        if not cards:
            cards.append('<p class="empty-day">この日の確認済み投稿文は準備中です。</p>')
        if plan["shortfall"]:
            reasons = "".join(f"<li>{_escape(item['detail'])}</li>" for item in plan["shortfall"])
            cards.append(f'<div class="day-shortfall"><p>予定{planned}件。不足理由:</p><ul>{reasons}</ul></div>')
        opened = " open" if index == 0 else ""
        sections.append(f'<details id="day-{day}"{opened}><summary><span>{day}</span> '
                        f'<span class="day-count">{posted}/{planned} 投稿済み</span></summary>'
                        + ''.join(cards) + '</details>')
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>楽天ROOM 投稿管理</title>'
        '<link rel="stylesheet" href="dashboard-assets/style.css"></head><body>'
        '<div class="layout"><aside><h1>投稿管理</h1><nav aria-label="日付">'
        + ''.join(navigation) + '</nav></aside><main>'
        '<p id="server-status" role="status">保存サーバー未接続。投稿済みチェックは保存できません。</p>'
        + ''.join(sections) + '</main></div>'
        '<script defer src="dashboard-assets/app.js"></script></body></html>'
    )


def _upsert_dashboard(root: Path, plan: dict) -> Path:
    root = Path(root)
    try:
        validate_document("daily-plan", plan)
    except SchemaValidationError as error:
        raise DashboardError(str(error)) from error
    plan_dir = root / "daily-plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    day = plan["target_date"]
    current_path = plan_dir / f"{day}.json"
    existing = _load(current_path) if current_path.is_file() else {"posts": []}
    allowed_existing_ids = {post["product_id"] for post in existing["posts"]}
    _validate_plan(root, plan, allowed_existing_ids)
    other_plans = []
    for path in plan_dir.glob("*.json"):
        if path.stem != day:
            previous = _load(path)
            validate_document("daily-plan", previous)
            other_plans.append(previous)
    existing_ids = {post["product_id"] for previous in other_plans for post in previous["posts"]}
    if any(post["product_id"] in existing_ids for post in plan["posts"]):
        raise DashboardError("product ID already appears on another day")
    plans = sorted([plan, *other_plans], key=lambda item: item["target_date"], reverse=True)
    status = _load(root / "post-status.json")
    validate_document("post-status", status)
    days = {"version": 1, "days": [{"date": item["target_date"],
                                     "product_ids": [post["product_id"] for post in item["posts"]]}
                                    for item in plans]}
    validate_document("dashboard-days", days)
    html_text = _render(root, plans, status)
    target = root / "posting-dashboard.html"
    writes = (target, plan_dir / f"{day}.json", root / "dashboard-days.json")
    before = {path: path.read_bytes() if path.is_file() else None for path in writes}
    try:
        atomic_write_text(target, html_text)
        atomic_write_json(plan_dir / f"{day}.json", plan)
        atomic_write_json(root / "dashboard-days.json", days)
    except OSError:
        for path, content in before.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_io.atomic_write_text(path, content.decode("utf-8"))
            except OSError:
                pass
        raise
    return target


def record_dashboard_error(root: Path, detail: str) -> None:
    atomic_write_json(Path(root) / "dashboard-error.json", {
        "status": "error", "errors": [{"code": "DATA_MISMATCH", "detail": redact(detail)}]})


def upsert_dashboard(root: Path, plan: dict) -> Path:
    try:
        target = _upsert_dashboard(Path(root), plan)
    except DashboardError as error:
        record_dashboard_error(Path(root), str(error))
        raise
    atomic_write_json(Path(root) / "dashboard-error.json", {"status": "ok", "errors": []})
    return target
