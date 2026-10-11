import copy
import json
from pathlib import Path

import pytest

from automation.attribution import AttributionMismatch, match_order, normalize_name
from automation.pattern_ledger import apply_analysis
from automation.performance_service import build_performance_input


def product(pid="P01", name="テスト 商品", code="shop/item", status="posted"):
    return {"id": pid, "name": name, "item_code": code,
            "url": f"https://item.rakuten.co.jp/{code}/", "genre": "家事ラク用品",
            "status": status, "posted_at": "2026-09-20"}


def order(name="ﾃｽﾄ  商品", status="未確定", **extra):
    return {"order_key": "order-1", "product_name": name, "amount_yen": 1200,
            "status": status, **extra}


def input_snapshot(tmp_path, orders=None, likes=0, observed=True):
    root = tmp_path
    (root / "products.json").write_text(json.dumps({"products": [product()]}, ensure_ascii=False), encoding="utf-8")
    (root / "post-status.json").write_text(json.dumps({"items": {"P01": {"posted": True, "ever_marked_posted": True}}}), encoding="utf-8")
    (root / "posts-log.jsonl").write_text("", encoding="utf-8")
    affiliate = {"target_date": "2026-09-23", "status": "observed", "observed_at": "2026-09-24T13:00:00+09:00",
                 "clicks": 1, "sales_count": 1, "sales_yen": 1200, "unconfirmed_commission_yen": 50,
                 "orders": orders or [], "errors": []}
    room = {"target_date": "2026-09-24", "status": "observed" if observed else "unavailable",
            "observations": [{"product_id": "P01", "room_post_id": "room-1",
                              "observed_at": "2026-09-24T12:00:00+09:00", "likes": likes,
                              "status": "observed"}] if observed else [], "errors": []}
    return build_performance_input(root, "2026-09-24", affiliate, room)


def empty_analysis():
    return {"analysis_date": "2026-09-24", "post_decisions": [], "cohort_insights": [],
            "winning_conditions": [], "single_change_experiments": [], "deprioritized_conditions": [],
            "research_requests": [], "daily_plan_constraints": [], "held_items": [],
            "missing_or_unattributed_data": []}


def test_exact_match_priority_and_ambiguity():
    p = product()
    assert normalize_name("  ﾃｽﾄ　 商品 ") == normalize_name(p["name"])
    assert match_order(order(), [p], {"P01"})["attribution_level"] == "post_confirmed_by_order_product_match"
    assert match_order(order("different", item_code="shop/item"), [p], {"P01"})["matching_method"] == "item_code"
    assert match_order(order("different", product_url=p["url"] + "?x=1"), [p], {"P01"})["matching_method"] == "normalized_url"
    assert match_order(order("テスト"), [p], {"P01"})["attribution_level"] == "purchased_item_unattributed"
    assert match_order(order(item_code="wrong/item"), [p], {"P01"})["product_id"] is None
    with pytest.raises(AttributionMismatch):
        match_order(order(), [p, product("P02", code="else/item")], {"P01", "P02"})


def test_clicks_and_zero_likes_stay_separate(tmp_path):
    snapshot = input_snapshot(tmp_path, [order()], likes=0)
    assert snapshot["day_aggregates"][0]["clicks"] == 1
    assert "clicks" not in snapshot["post_observations"][0]
    assert snapshot["post_observations"][0]["likes"] == 0
    assert snapshot["post_observations"][0]["confirmed_orders"][0]["product_id"] == "P01"
    assert snapshot["comparison"]["trend_ready"] is False
    unavailable = input_snapshot(tmp_path, observed=False)
    assert unavailable["post_observations"][0]["likes"] is None
    assert unavailable["post_observations"][0]["reaction_label"] == "未取得"


def test_a_request_idempotence_and_order_status_change(tmp_path):
    snapshot = input_snapshot(tmp_path, [order()])
    analysis = empty_analysis()
    analysis["post_decisions"] = [{"product_id": "P01", "rating": "A", "attribution":
                                   "post_confirmed_by_order_product_match", "evidence": "order-1"}]
    analysis["winning_conditions"] = [{"condition": "忙しい家庭向け", "evidence": "order-1"}]
    analysis["research_requests"] = [{"condition": "忙しい家庭向け", "evidence": "別商品3件"}]
    first = apply_analysis(tmp_path, "run-1", snapshot, analysis)
    second = apply_analysis(tmp_path, "run-2", snapshot, analysis)
    assert first["observation_key"] == second["observation_key"]
    assert len((tmp_path / "performance/observations.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert second["research_queue"]["requests"][0]["requested_count"] == 3
    assert second["research_queue"]["requests"][0]["exclude_product_ids"] == ["P01"]
    changed = copy.deepcopy(snapshot)
    changed["purchased_items"][0]["order_status"] = "確定"
    changed["post_observations"][0]["confirmed_orders"][0]["order_status"] = "確定"
    apply_analysis(tmp_path, "run-3", changed, analysis)
    assert len((tmp_path / "performance/observations.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_b_c_hold_guard_and_single_variable(tmp_path):
    snapshot = input_snapshot(tmp_path, likes=0)
    for rating in ("B", "C"):
        analysis = empty_analysis()
        analysis["post_decisions"] = [{"product_id": "P01", "rating": rating,
                                       "attribution": "day_aggregate", "evidence": "clicks 1"}]
        with pytest.raises(ValueError):
            apply_analysis(tmp_path, "run", snapshot, analysis)
    analysis = empty_analysis()
    analysis["post_decisions"] = [{"product_id": "P01", "rating": "hold",
                                   "attribution": "unattributed", "evidence": "未取得"}]
    analysis["single_change_experiments"] = [{"variable": "冒頭", "change": "短く", "evidence": "反応弱め"},
                                              {"variable": "時刻", "change": "夜", "evidence": "反応弱め"}]
    with pytest.raises(ValueError):
        apply_analysis(tmp_path, "run", snapshot, analysis)
    analysis["single_change_experiments"].pop()
    assert apply_analysis(tmp_path, "run", snapshot, analysis)["counts"]["hold"] == 1
