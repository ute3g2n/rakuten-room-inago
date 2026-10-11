"""Conservative order-product attribution. Aggregate clicks never enter this module."""

import unicodedata

from automation.inventory_service import normalize_code, normalize_url


class AttributionMismatch(ValueError):
    code = "DATA_MISMATCH"


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _posted(product: dict, posted_ids: set[str]) -> bool:
    return product["id"] in posted_ids or product.get("status") == "posted"


def match_order(order: dict, products: list[dict], posted_ids: set[str]) -> dict:
    """Return an evidence-bearing match or an explicit unattributed purchase."""
    candidates = [p for p in products if _posted(p, posted_ids)]
    code = normalize_code(order.get("item_code"))
    url = normalize_url(order["product_url"]) if order.get("product_url") else None
    method = None
    matches: list[dict] = []
    if code:
        method = "item_code"
        matches = [p for p in candidates if normalize_code(p.get("item_code")) == code]
    if not matches and url:
        method = "normalized_url"
        matches = [p for p in candidates if normalize_url(p["url"]) == url]
    if not matches and not code and not url:
        method = "exact_normalized_name"
        name = normalize_name(order["product_name"])
        matches = [p for p in candidates if normalize_name(p["name"]) == name]
        if order.get("shop_name"):
            matches = [p for p in matches if p.get("shop_name") and
                       normalize_name(p["shop_name"]) == normalize_name(order["shop_name"])]
    if len(matches) > 1:
        raise AttributionMismatch(f"DATA_MISMATCH: order {order['order_key']} has multiple product IDs")
    evidence = {"order_key": order["order_key"], "order_status": order["status"],
                "amount_yen": order["amount_yen"], "original_product_name": order["product_name"],
                "normalized_product_name": normalize_name(order["product_name"]),
                "order_occurred_at": order.get("occurred_at"), "matching_method": method}
    if not matches:
        return {**evidence, "product_id": None,
                "attribution_level": "purchased_item_unattributed",
                "detail": "購入商品は確認済み・入口投稿は未特定"}
    return {**evidence, "product_id": matches[0]["id"],
            "attribution_level": "post_confirmed_by_order_product_match",
            "matched_product_name": matches[0]["name"],
            "normalized_matched_name": normalize_name(matches[0]["name"])}
