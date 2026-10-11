"""Map existing affiliate and public ROOM JSON into explicit observed/unavailable snapshots."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from automation.inventory_service import normalize_url
from automation.schema_validator import validate_document


JST = timezone(timedelta(hours=9))


def _jst(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(JST).isoformat(timespec="seconds")


def _unavailable_affiliate(target_date: str, detail: str) -> dict:
    result = {"target_date": target_date, "status": "unavailable", "observed_at": None,
              "clicks": None, "sales_count": None, "sales_yen": None,
              "unconfirmed_commission_yen": None, "orders": [],
              "errors": [{"code": "AFFILIATE_FETCH_FAILED", "detail": detail}]}
    validate_document("affiliate-observation", result)
    return result


def affiliate_snapshot(latest: dict, last_run: dict, target_date: str) -> dict:
    if last_run.get("status") != "observed":
        return _unavailable_affiliate(target_date, "latest fetch was unavailable")
    day = latest.get("byDate", {}).get(target_date)
    if not day or day.get("status") != "observed":
        return _unavailable_affiliate(target_date, "target day was not observed")
    orders = []
    month = latest.get("orderDetailsByMonth", {}).get(target_date[:7])
    if month and month.get("status") == "observed":
        for row in month.get("orders", []):
            key = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
            orders.append({"order_key": key, "product_name": row.get("productName") or "未確認",
                           "amount_yen": row["salesJpy"], "status": row["status"]})
    result = {"target_date": target_date, "status": "observed",
              "observed_at": _jst(day["observedAt"]), "clicks": day["clicks"],
              "sales_count": day["ordersUnconfirmed"], "sales_yen": day["salesJpyUnconfirmed"],
              "unconfirmed_commission_yen": day["commissionJpyUnconfirmed"],
              "orders": orders, "errors": [] if month and month.get("status") == "observed"
              else [{"code": "ORDER_DETAILS_UNAVAILABLE", "detail": "order details not observed"}]}
    validate_document("affiliate-observation", result)
    return result


def room_snapshot(latest: dict, last_run: dict, target_date: str, products: list[dict]) -> dict:
    if last_run.get("status") != "observed":
        result = {"target_date": target_date, "status": "unavailable", "observations": [],
                  "errors": [{"code": "ROOM_FETCH_FAILED", "detail": "latest fetch was unavailable"}]}
        validate_document("room-observation", result)
        return result
    by_url = {}
    for product in products:
        by_url.setdefault(normalize_url(product["url"]), []).append(product["id"])
    observations = []
    for post_id, post in latest.get("postsById", {}).items():
        matches = by_url.get(normalize_url(post.get("itemUrl") or ""), [])
        observed = post.get("status") == "observed" and isinstance(post.get("likes"), int)
        observations.append({"product_id": matches[0] if len(matches) == 1 else None,
                             "room_post_id": str(post.get("postId") or post_id),
                             "observed_at": _jst(post["observedAt"]),
                             "likes": post["likes"] if observed else None,
                             "status": "observed" if observed and len(matches) == 1 else "unmatched" if observed else "unavailable"})
    result = {"target_date": target_date, "status": "observed", "observations": observations, "errors": []}
    validate_document("room-observation", result)
    return result
