"""Read existing API batch snapshots without mistaking partial failure for zero."""

import copy
import json
from pathlib import Path

from automation.inventory_service import classify_candidates
from automation.schema_validator import validate_document, validate_product_store


def build_research_input(inventory_brief: dict, genre_plan: dict) -> dict:
    """Shape new inventory counts for the existing Node query planner."""
    validate_document("inventory-brief", inventory_brief)
    queue = []
    for genre in genre_plan["genres"]:
        count = inventory_brief["genre_counts"][genre["name"]]
        shortage = max(0, genre["inventoryTarget"] - count)
        queue.append({"id": genre["id"], "name": genre["name"], "shortage": shortage,
                      "coverageDays": count / genre["postsPerDay"]})
    return {"totals": {"shortage": sum(item["shortage"] for item in queue)},
            "researchQueue": queue}


def reclassify_snapshot(snapshot: dict, root: Path) -> dict:
    """Replace legacy Markdown classifications with the new stores' decision."""
    root = Path(root)
    product_store = json.loads((root / "products.json").read_text(encoding="utf-8"))
    status_store = json.loads((root / "post-status.json").read_text(encoding="utf-8"))
    validate_product_store(product_store)
    validate_document("post-status", status_store)
    posted = {product["id"] for product in product_store["products"] if product["status"] == "posted"}
    posted.update(product_id for product_id, item in status_store["items"].items()
                  if item["posted"] or item["ever_marked_posted"])
    planned = set()
    for line in (root / "posts-log.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            validate_document("posts-log-record", row)
            if row["status"] == "posted":
                posted.add(row["product_id"])
            elif row["status"] == "planned":
                planned.add(row["product_id"])
    result = copy.deepcopy(snapshot)
    classified = classify_candidates(result["items"], product_store["products"], posted, planned)
    for item in classified:
        item["status"] = item.pop("classification")
        matched = item.pop("matched_product_id")
        item["matchedProductIds"] = [matched] if matched else []
        item["matchedPostedProductIds"] = [matched] if matched in posted else []
        item["matchedPlannedProductIds"] = [matched] if matched in planned else []
        item["eligible"] = item["status"] == "new"
    result["items"] = classified
    summary = result["summary"]
    for status, field in (("new", "newCandidates"), ("already-posted", "alreadyPosted"),
                          ("planned", "planned"), ("in-ledger", "alreadyInLedger"),
                          ("review-model", "needsReview"), ("duplicate", "duplicateCandidates")):
        summary[field] = sum(item["status"] == status for item in classified)
    return result


def summarize_snapshot(snapshot: dict) -> dict:
    summary = snapshot["summary"]
    required = ("rawResults", "uniqueResults", "duplicatesRemoved", "newCandidates",
                "alreadyPosted", "planned", "alreadyInLedger", "failedQueries",
                "succeededQueries", "totalQueries")
    if any(not isinstance(summary.get(key), int) or summary[key] < 0 for key in required):
        raise ValueError("invalid research summary")
    if summary["rawResults"] - summary["duplicatesRemoved"] != summary["uniqueResults"]:
        raise ValueError("deduplication counts do not match")
    classes = sum(summary.get(key, 0) for key in ("newCandidates", "alreadyPosted", "planned",
                                                "alreadyInLedger", "needsReview", "duplicateCandidates"))
    if classes != summary["uniqueResults"]:
        raise ValueError("candidate classes do not match unique results")
    items = snapshot.get("items")
    if not isinstance(items, list) or len(items) != summary["uniqueResults"]:
        raise ValueError("candidate item count does not match unique results")
    expected_status = {"new": "newCandidates", "already-posted": "alreadyPosted",
                       "planned": "planned", "in-ledger": "alreadyInLedger",
                       "review-model": "needsReview", "duplicate": "duplicateCandidates"}
    if any(sum(item.get("status") == status for item in items) != summary.get(key, 0)
           for status, key in expected_status.items()):
        raise ValueError("candidate status counts do not match summary")
    if summary["failedQueries"] + summary["succeededQueries"] != summary["totalQueries"]:
        raise ValueError("query outcomes do not match total")
    return {"status": "partial" if summary["failedQueries"] else "success",
            "counts": {"raw": summary["rawResults"], "unique": summary["uniqueResults"],
                       "duplicates_removed": summary["duplicatesRemoved"],
                       "new": summary["newCandidates"], "already_posted": summary["alreadyPosted"],
                       "planned": summary["planned"], "in_ledger": summary["alreadyInLedger"],
                       "needs_review": summary.get("needsReview", 0),
                       "duplicate_candidates": summary.get("duplicateCandidates", 0)},
            "failed_queries": summary["failedQueries"],
            "succeeded_queries": summary["succeededQueries"]}
