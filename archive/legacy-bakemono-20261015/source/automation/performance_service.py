"""Build a deterministic performance input without inventing post-level outcomes."""

import json
from datetime import date
from pathlib import Path

from automation.attribution import match_order
from automation.schema_validator import validate_document


def posted_ids_from_stores(root: Path) -> set[str]:
    status = json.loads((root / "post-status.json").read_text(encoding="utf-8"))
    posted = {pid for pid, row in status["items"].items() if row["posted"] or row["ever_marked_posted"]}
    for line in (root / "posts-log.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row["status"] == "posted":
                posted.add(row["product_id"])
    return posted


def build_performance_input(root: Path, analysis_date: str, affiliate: dict, room: dict,
                            prior_patterns: dict | None = None) -> dict:
    """Separate post reactions, day aggregates and confirmed order products."""
    root = Path(root)
    validate_document("affiliate-observation", affiliate)
    validate_document("room-observation", room)
    products = json.loads((root / "products.json").read_text(encoding="utf-8"))["products"]
    posted_ids = posted_ids_from_stores(root)
    room_rows: dict[str, list[dict]] = {}
    for obs in room["observations"]:
        if obs["product_id"] in posted_ids:
            room_rows.setdefault(obs["product_id"], []).append(obs)
    attribution = [match_order(order, products, posted_ids) for order in affiliate["orders"]]
    by_id = {p["id"]: p for p in products}
    post_rows = []
    comparable = 0
    for pid in sorted(posted_ids):
        product = by_id.get(pid)
        if product is None:
            continue
        observed = sorted((r for r in room_rows.get(pid, []) if r["status"] == "observed"),
                          key=lambda r: r["observed_at"], reverse=True)
        latest = observed[0] if observed else None
        age = (date.fromisoformat(analysis_date) - date.fromisoformat(product["posted_at"])).days if product.get("posted_at") else None
        mature = age is not None and age >= 1
        if latest and mature:
            comparable += 1
        orders = [a for a in attribution if a["product_id"] == pid]
        post_rows.append({"product_id": pid, "genre": product["genre"],
                          "posted_at": product.get("posted_at"), "observation_days": age,
                          "mature": mature, "room_post_id": latest["room_post_id"] if latest else None,
                          "likes": latest["likes"] if latest else None,
                          "reaction_label": ("公開反応なし" if latest["likes"] == 0 else "反応あり") if latest else "未取得",
                          "attribution_level": "post_confirmed_by_order_product_match" if orders else
                          "post_reaction_only" if latest else "unavailable",
                          "confirmed_orders": orders})
    result = {"analysis_date": analysis_date, "post_observations": post_rows,
              "day_aggregates": [{"target_date": affiliate["target_date"],
                                  "attribution_level": "day_aggregate" if affiliate["status"] == "observed" else "unavailable",
                                  "clicks": affiliate["clicks"], "sales_count": affiliate["sales_count"],
                                  "sales_yen": affiliate["sales_yen"]}],
              "shop_aggregates": [], "purchased_items": attribution,
              "comparison": {"comparable_reaction_count": comparable,
                             "trend_ready": comparable >= 3},
              "missing_or_unattributed_data": [a for a in attribution if a["product_id"] is None],
              "prior_pattern_ledger": prior_patterns or {}}
    validate_document("performance-input", result)
    return result
