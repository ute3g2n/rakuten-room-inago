"""Q1.4 bounded source rows and condition evidence, without network access."""

from dataclasses import replace

import pytest

from automation.inago.room_adapter import RoomSearchRequest, parse_room_response
from tests_inago.test_step11 import _post, _response, DEADLINE


def request():
    return RoomSearchRequest(
        "A1",
        "RUN-STEP11",
        "揚げ物",
        ("theme-01",),
        "related",
        1,
        None,
        DEADLINE,
        query_kind="related",
        keyword_snapshot_ref="KW1",
        selected_word_ref="W1",
    )


def response(count, *, end=False):
    value = _response(
        "A1", posts=[_post(i, likes=None) for i in range(1, count + 1)], end_reached=end
    )
    value.update(
        level="related",
        query_kind="related",
        keyword_snapshot_ref="KW1",
        selected_word_ref="W1",
        condition_evidence_ref="CONDITION-A1",
        loaded_posts=count,
    )
    return value


def test_batch_eighty_uses_first_fifty_and_does_not_validate_unused_tail():
    payload = response(80)
    payload["posts"][50] = {"unusable_tail": True}
    result = parse_room_response(payload, request())
    assert result.status == "observed" and result.error_code is None
    assert len(result.posts) == 50
    assert result.loaded_posts == 80 and result.unused_posts == 30
    assert result.end_reason == "limit_reached" and result.end_reached is False
    assert result.condition_evidence_ref == "CONDITION-A1"
    assert [p.order_index for p in result.posts] == list(range(1, 51))


@pytest.mark.parametrize("count", [0, 37, 50])
def test_terminal_actual_rows_are_success_even_under_fifty(count):
    result = parse_room_response(response(count, end=True), request())
    assert result.status == "observed" and len(result.posts) == count
    assert result.loaded_posts == count and result.unused_posts == 0
    assert result.end_reason == ("limit_reached" if count == 50 else "exhausted")


def test_known_condition_contradiction_is_invalid_but_unread_counts_are_valid():
    payload = response(2, end=True)
    assert parse_room_response(payload, request()).status == "observed"
    payload["posts"][0]["likes"] = 299
    bad = parse_room_response(payload, request())
    assert bad.status == "invalid" and bad.posts == ()
    assert bad.error_code == "search_condition_contradicted"


@pytest.mark.parametrize(
    ("failed_product_rows", "expected_status"),
    [(10, "observed"), (11, "partial")],
)
def test_up_to_ten_unresolved_product_rows_do_not_fail_a_room_search(
    failed_product_rows, expected_status
):
    payload = response(50, end=True)
    for row in payload["posts"][:failed_product_rows]:
        row.update(item_url=None, observed_shop_id=None, observed_item_id=None)

    result = parse_room_response(payload, request())

    assert result.status == expected_status
    assert len(result.posts) == 50
    if failed_product_rows <= 10:
        assert result.error_code is None
    else:
        assert result.error_code == "product_resolution_failures_exceeded"


@pytest.mark.parametrize(
    "field,value",
    [
        ("likes_min", 0),
        ("condition_evidence_ref", None),
        ("selected_word_ref", "OTHER"),
        ("current_search_id", "OLD"),
        ("internal_order_confirmed", False),
    ],
)
def test_mismatched_or_unverified_search_cannot_supply_candidates(field, value):
    payload = response(50)
    payload[field] = value
    result = parse_room_response(payload, request())
    assert result.status == "invalid" and result.posts == ()


def test_different_posts_for_same_product_use_fifty_slots_before_product_grouping():
    payload = response(80)
    for item in payload["posts"][:50]:
        item.update(
            item_url="https://item.rakuten.co.jp/shop-a/same/", observed_item_id="same"
        )
    result = parse_room_response(payload, request())
    assert len(result.posts) == 50
    assert {item.offer_key for item in result.posts} == {"shop-a:same"}
    assert result.unused_posts == 30


def test_repeat_read_of_same_physical_row_does_not_use_an_extra_slot():
    payload = response(50, end=True)
    payload["posts"].insert(1, dict(payload["posts"][0]))
    payload["loaded_posts"] = 51
    result = parse_room_response(payload, request())
    assert result.status == "observed" and len(result.posts) == 50
    assert len({item.source_key for item in result.posts}) == 50


def test_short_nonterminal_and_partial_timeout_are_not_normal_zero():
    assert parse_room_response(response(0), request()).status == "partial"
    payload = response(50)
    payload.update(status="partial", error_code="source_timeout")
    result = parse_room_response(payload, request())
    assert result.status == "partial" and result.error_code == "source_timeout"


def test_theme_fallback_has_no_invented_related_keyword_ref():
    req = replace(
        request(),
        level="theme_fallback",
        query_kind="theme_fallback",
        keyword_snapshot_ref=None,
        selected_word_ref=None,
    )
    payload = response(0, end=True)
    payload.update(
        level="theme_fallback",
        query_kind="theme_fallback",
        keyword_snapshot_ref=None,
        selected_word_ref=None,
    )
    assert parse_room_response(payload, req).status == "observed"
