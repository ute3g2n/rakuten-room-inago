import pytest

from automation.jobs.contracts import check_job_output
from automation.research_batching import (
    candidate_is_postable,
    chunk_candidates,
    distribute_target,
    order_triaged_candidates,
    select_candidate_pool,
)


def test_candidate_pool_balances_genres_and_respects_hard_limit():
    candidates = [
        {"product_id": f"P{index:02}", "genre": genre}
        for index, genre in enumerate(
            [genre for genre in ("料理", "家事", "収納", "子育て", "美容") for _ in range(30)], 1)
    ]
    pool = select_candidate_pool(candidates, {
        "料理": 6, "家事": 2, "収納": 2, "子育て": 3, "美容": 2,
    }, limit=30)

    assert len(pool) == 30
    assert len({item["product_id"] for item in pool}) == 30
    assert [item["genre"] for item in pool[:15]] == [
        "料理", "料理", "料理", "料理", "料理", "料理",
        "家事", "家事", "収納", "収納", "子育て", "子育て", "子育て", "美容", "美容",
    ]


def test_candidate_pool_fills_from_available_genres_when_a_quota_is_empty():
    candidates = [{"product_id": f"P{index:02}", "genre": "料理"} for index in range(1, 25)]
    pool = select_candidate_pool(candidates, {"料理": 6, "家事": 2}, limit=20)

    assert len(pool) == 20
    assert all(item["genre"] == "料理" for item in pool)


def test_candidates_accept_complete_or_provisional_a_or_b_scores_with_available_stock():
    base_snapshot = {"status": "observed", "availability": "in_stock", "name": "商品", "price_yen": 1000}
    complete_b = {"status": "complete", "grade": "B", "score": 6,
                  "provisional_grade": "B",
                  "confirmed_factors": 6, "confirmed_max": 12,
                  "full_max": 12, "unconfirmed_factors": []}
    assert candidate_is_postable(complete_b, base_snapshot)
    assert candidate_is_postable({**complete_b, "grade": "A", "provisional_grade": "A", "score": 9}, base_snapshot)
    provisional_b = {"status": "provisional", "grade": None, "provisional_grade": "B", "score": 6,
                     "confirmed_factors": 4, "confirmed_max": 8, "full_max": 12,
                     "unconfirmed_factors": ["shop_strength", "review_freshness"]}
    provisional_a = {"status": "provisional", "grade": None, "provisional_grade": "A", "score": 9,
                     "confirmed_factors": 5, "confirmed_max": 10, "full_max": 12,
                     "unconfirmed_factors": ["review_freshness"]}
    assert candidate_is_postable(provisional_b, base_snapshot)
    assert candidate_is_postable(provisional_a, base_snapshot)
    assert not candidate_is_postable({**complete_b, "grade": "C", "score": 5}, base_snapshot)
    assert not candidate_is_postable({**complete_b, "grade": "A", "score": 6}, base_snapshot)
    assert not candidate_is_postable({**complete_b, "confirmed_max": 10}, base_snapshot)
    assert not candidate_is_postable({**provisional_b, "score": 5, "provisional_grade": "C"}, base_snapshot)
    assert not candidate_is_postable({**provisional_b, "confirmed_max": 10}, base_snapshot)
    assert not candidate_is_postable({**provisional_b, "unconfirmed_factors": []}, base_snapshot)
    assert not candidate_is_postable({**provisional_b, "status": "hold"}, base_snapshot)
    assert not candidate_is_postable(complete_b, {**base_snapshot, "availability": None})
    assert not candidate_is_postable(complete_b, {**base_snapshot, "availability": "out_of_stock"})
    assert not candidate_is_postable(complete_b, {**base_snapshot, "price_yen": None})


def test_page_checks_are_partitioned_into_ten_item_batches():
    batches = list(chunk_candidates([{"id": f"P{index:02}"} for index in range(1, 24)], 10))

    assert [len(batch) for batch in batches] == [10, 10, 3]
    assert [item["id"] for batch in batches for item in batch] == [f"P{index:02}" for index in range(1, 24)]


def test_triaged_queue_keeps_initial_priority_then_uses_uncertain_and_excluded():
    pool = [{"product_id": f"P{index:02}"} for index in range(1, 5)]
    triage = {"selected": [{"product_id": "P02"}, {"product_id": "P01"}],
              "uncertain": [{"product_id": "P04"}],
              "excluded": [{"product_id": "P03"}]}

    assert [item["product_id"] for item in order_triaged_candidates(pool, triage, 4)] == [
        "P02", "P01", "P04", "P03"]
    assert [item["product_id"] for item in order_triaged_candidates(pool, triage, 3)] == [
        "P02", "P01", "P04"]


def test_target_count_keeps_the_configured_genre_mix():
    weights = {"料理": 6, "家事": 2, "収納": 2, "子育て": 3, "美容": 2}

    assert distribute_target(15, weights) == weights
    assert distribute_target(5, weights) == {"料理": 5, "家事": 0, "収納": 0, "子育て": 0, "美容": 0}


def test_triage_must_classify_every_bounded_candidate():
    payload = {"api_candidates": [{"product_id": "P01"}, {"product_id": "P02"}],
               "inventory_brief": {}}
    output = {"run_id": "test", "target_date": "2026-09-25",
              "selected": [{"product_id": "P01", "reason": "確認対象"}],
              "excluded": [], "uncertain": []}

    with pytest.raises(ValueError, match="every input candidate"):
        check_job_output("triage", output, payload)
