"""One-time, read-only import from the original Markdown records."""

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from automation.schema_validator import validate_document, validate_product_store


GENRE = "時短・料理・調理・家電"
STORE_FILES = ("products.json", "drafts.json", "posts-log.jsonl", "post-status.json", "dashboard-days.json")


def _jst_now() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, document: dict) -> None:
    _atomic_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def _table_rows(markdown: str, heading: str) -> list[tuple[int, list[str]]]:
    lines = markdown.splitlines()
    started = False
    in_table = False
    rows = []
    for line_number, line in enumerate(lines, 1):
        if line.strip() == heading:
            started = True
            continue
        if not started:
            continue
        if in_table and not line.startswith("|"):
            break
        if not line.startswith("|"):
            continue
        in_table = True
        cells = [cell.strip() for cell in line.rstrip().split("|")[1:-1]]
        if cells[0] in {"ID", "日付"} or all(re.fullmatch(r"-+", cell or "") for cell in cells):
            continue
        rows.append((line_number, cells))
    return rows


def _normalize_url(url: str) -> tuple[str, str | None]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("invalid product URL")
    path = parsed.path.rstrip("/") + "/"
    normalized = urlunsplit(("https", parsed.netloc.lower(), path, "", ""))
    pieces = [piece for piece in path.split("/") if piece]
    code = "/".join(pieces[-2:]) if len(pieces) >= 2 else None
    return normalized, code


def _drafts(markdown: str, known_ids: set[str], needs_review: list[dict]) -> list[dict]:
    result = []
    for block in re.split(r"(?=^## )", markdown, flags=re.MULTILINE):
        id_match = re.search(r"\bID[：:]\s*(P\d{2,})", block)
        if not id_match:
            continue
        product_id = id_match.group(1)
        url_match = re.search(r"^- 商品[：:]\s*(https://\S+)", block, flags=re.MULTILINE)
        body_match = re.search(r"```text\s*\n(.*?)\n```", block, flags=re.DOTALL)
        if product_id not in known_ids or not url_match or not body_match:
            needs_review.append({"source": "post-drafts.md", "product_id": product_id, "reason": "missing product, URL, or body"})
            continue
        normalized, _ = _normalize_url(url_match.group(1))
        result.append({"product_id": product_id, "url": normalized, "body": body_match.group(1).strip(), "status": "needs_review", "checked_at": None})
    return result


def migrate_legacy_data(legacy: Path, destination: Path) -> dict:
    legacy = Path(legacy)
    destination = Path(destination)
    existing = [path for name in STORE_FILES if (path := destination / name).exists()]
    if existing:
        if len(existing) != len(STORE_FILES):
            raise RuntimeError("partial migration exists; review files before retry")
        products = json.loads((destination / "products.json").read_text(encoding="utf-8"))
        if products["products"]:
            return {"migrated_products": 0, "posted_ids": [], "needs_review": [], "skipped_existing": True}
    products_text = (legacy / "products.md").read_text(encoding="utf-8")
    drafts_text = (legacy / "post-drafts.md").read_text(encoding="utf-8")
    log_text = (legacy / "posts-log.md").read_text(encoding="utf-8")
    needs_review: list[dict] = []
    collisions: list[dict] = []
    products = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    id_mapping = {}
    for line_number, cells in _table_rows(products_text, "## 候補一覧"):
        if len(cells) != 11 or not re.fullmatch(r"P\d{2,}", cells[0]):
            needs_review.append({"source": "products.md", "line": line_number, "reason": "unparsed product row"})
            continue
        product_id, _, name, url, _, _, _, _, _, checked, posted = cells
        try:
            normalized, code = _normalize_url(url)
        except ValueError:
            needs_review.append({"source": "products.md", "line": line_number, "reason": "invalid product URL"})
            continue
        if product_id in seen_ids or normalized in seen_urls:
            collisions.append({"source": "products.md", "line": line_number, "product_id": product_id, "reason": "duplicate ID or normalized URL"})
            continue
        if checked and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
            needs_review.append({"source": "products.md", "line": line_number, "reason": "invalid checked date"})
            checked = ""
        if posted and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted):
            needs_review.append({"source": "products.md", "line": line_number, "reason": "invalid posted date"})
            posted = ""
        products.append({"id": product_id, "name": name, "url": normalized, "normalized_url": normalized, "genre": GENRE, "status": "posted" if posted else "needs_review", "item_code": code, "model_candidate": None, "checked_at": checked or None, "posted_at": posted or None})
        seen_ids.add(product_id)
        seen_urls.add(normalized)
        id_mapping[product_id] = product_id
    if "P03" not in seen_ids:
        raise ValueError("P03 is missing from legacy products")
    known = {product["id"] for product in products}
    drafts = _drafts(drafts_text, known, needs_review)
    posted_rows = []
    for line_number, cells in _table_rows(log_text, "## 直近の投稿"):
        if len(cells) != 9:
            needs_review.append({"source": "posts-log.md", "line": line_number, "reason": "unparsed post row"})
            continue
        date, product_cell, _, _, _, status, reaction, _, _ = cells
        id_match = re.match(r"(P\d{2,})\b", product_cell)
        if not id_match:
            needs_review.append({"source": "posts-log.md", "line": line_number, "reason": "missing product ID"})
            continue
        product_id = id_match.group(1)
        if status == "実施済み" and product_id in known:
            posted_rows.append({"date": date, "product_id": product_id, "status": "posted", "source": "legacy_user_confirmation", "posted_at": None, "reaction": reaction or None, "rating": None})
        elif status == "計画":
            needs_review.append({"source": "posts-log.md", "line": line_number, "product_id": product_id, "reason": "pre-operation plan not carried into production"})
        else:
            needs_review.append({"source": "posts-log.md", "line": line_number, "product_id": product_id, "reason": "unconfirmed posting state"})
    if "P03" not in {row["product_id"] for row in posted_rows}:
        raise ValueError("P03 lacks confirmed posted row")
    product_store = {"version": 1, "products": products, "next_id": max(int(x["id"][1:]) for x in products) + 1}
    draft_store = {"version": 1, "drafts": drafts}
    now = _jst_now()
    status_store = {"version": 1, "items": {row["product_id"]: {"product_id": row["product_id"], "scheduled_date": None, "posted": True, "ever_marked_posted": True, "posted_at": None, "updated_at": now, "correction_history": []} for row in posted_rows}}
    days_store = {"version": 1, "days": []}
    validate_product_store(product_store)
    for name, document in (("drafts", draft_store), ("post-status", status_store), ("dashboard-days", days_store)):
        validate_document(name, document)
    for row in posted_rows:
        validate_document("posts-log-record", row)
    report = {"migrated_at": now, "source": str(legacy), "destination": str(destination), "migrated_products": len(products), "migrated_drafts": len(drafts), "migrated_posts": len(posted_rows), "posted_ids": sorted(status_store["items"]), "id_mapping": id_mapping, "needs_review": needs_review, "collisions": collisions, "skipped_existing": False, "unmigrated_legacy_files": sorted(path.name for path in legacy.glob("*.md") if path.name not in {"products.md", "post-drafts.md", "posts-log.md"})}
    destination.mkdir(parents=True, exist_ok=True)
    _atomic_json(destination / "products.json", product_store)
    _atomic_json(destination / "drafts.json", draft_store)
    _atomic_text(destination / "posts-log.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in posted_rows))
    _atomic_json(destination / "post-status.json", status_store)
    _atomic_json(destination / "dashboard-days.json", days_store)
    _atomic_json(destination / "migration-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=Path, default=Path("rakuten-room"))
    parser.add_argument("--destination", type=Path, default=Path("rakuten-room-kai"))
    args = parser.parse_args()
    report = migrate_legacy_data(args.legacy, args.destination)
    print(json.dumps({key: report[key] for key in ("migrated_products", "posted_ids", "needs_review", "skipped_existing")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
