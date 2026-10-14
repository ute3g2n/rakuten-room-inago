"""Deterministic inventory count and duplicate triage."""

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from automation.config_loader import load_config
from automation.schema_validator import validate_document, validate_product_store


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") + "/", "", ""))


def normalize_code(code: str | None) -> str | None:
    return code.replace(":", "/").lower() if code else None


def build_inventory_brief(root: Path, target_date: str) -> dict:
    root = Path(root)
    products_store = json.loads((root / "products.json").read_text(encoding="utf-8"))
    drafts_store = json.loads((root / "drafts.json").read_text(encoding="utf-8"))
    status_store = json.loads((root / "post-status.json").read_text(encoding="utf-8"))
    validate_product_store(products_store)
    validate_document("drafts", drafts_store)
    validate_document("post-status", status_store)
    posted_ids = {product_id for product_id, item in status_store["items"].items()
                  if item["posted"] or item["ever_marked_posted"]}
    posted_ids.update(product["id"] for product in products_store["products"] if product["status"] == "posted")
    planned_ids = set()
    for line in (root / "posts-log.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            validate_document("posts-log-record", row)
            if row["status"] == "posted":
                posted_ids.add(row["product_id"])
            elif row["status"] == "planned":
                planned_ids.add(row["product_id"])
    drafts = {draft["product_id"]: draft for draft in drafts_store["drafts"] if draft["status"] == "accepted"}
    config = load_config()
    genre_counts = {genre: 0 for genre in config["genre_targets"]}
    eligible_ids = []
    today = date.fromisoformat(target_date)
    for product in products_store["products"]:
        if product["id"] in posted_ids or product["id"] in planned_ids or product["status"] != "eligible":
            continue
        checked = product["checked_at"]
        draft = drafts.get(product["id"])
        if not checked or not draft or normalize_url(draft["url"]) != normalize_url(product["url"]):
            continue
        age = (today - date.fromisoformat(checked)).days
        if age < 0 or age >= 7:
            continue
        genre_counts[product["genre"]] += 1
        eligible_ids.append(product["id"])
    shortfall_count = sum(max(0, target - genre_counts[genre])
                          for genre, target in config["genre_targets"].items())
    result = {"target_date": target_date, "eligible_count": len(eligible_ids),
              "draft_count": len(drafts), "posted_count": len(posted_ids),
              "shortfall_count": shortfall_count,
              "inventory_shortfall_count": max(0, config["inventory_target"] - len(eligible_ids)),
              "genre_counts": genre_counts,
              "needs_review_count": sum(product["status"] == "needs_review" for product in products_store["products"]),
              "eligible_product_ids": eligible_ids}
    validate_document("inventory-brief", result)
    return result


def classify_candidates(candidates: list[dict], products: list[dict], posted_ids: set[str],
                        planned_ids: set[str]) -> list[dict]:
    known_urls = {}
    known_codes = {}
    known_models = {}
    for product in products:
        known_urls[normalize_url(product["normalized_url"])] = product["id"]
        if product["item_code"]:
            known_codes[normalize_code(product["item_code"])] = product["id"]
        if product["model_candidate"]:
            known_models[product["model_candidate"].casefold()] = product["id"]
    seen_candidates = set()
    result = []
    for candidate in candidates:
        url = normalize_url(candidate["itemUrl"])
        code = normalize_code(candidate.get("itemCode"))
        key = (url, code)
        match = known_urls.get(url) or known_codes.get(code)
        model_match = known_models.get(str(candidate.get("model_candidate") or "").casefold())
        if key in seen_candidates:
            classification = "duplicate"
        elif match in posted_ids:
            classification = "already-posted"
        elif match in planned_ids:
            classification = "planned"
        elif match:
            classification = "in-ledger"
        elif model_match:
            classification = "review-model"
        else:
            classification = "new"
        result.append({**candidate, "classification": classification,
                       "matched_product_id": match or model_match})
        seen_candidates.add(key)
    return result
