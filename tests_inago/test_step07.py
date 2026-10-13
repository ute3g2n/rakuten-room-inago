"""Fixture contracts for product selection and stage statistics in STEP 07."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from automation.inago.contracts import IdempotencyConflict, RejectedContract
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.selection import (
    OfferHistory,
    SourcePost,
    ThemeCandidate,
    choose_themes,
    evaluate_sources,
    identify_offer,
    load_offer_history,
    load_theme_candidates,
    load_theme_selection_events,
    make_offer_key,
    normalize_offer_url,
    persist_theme_selection_events,
    record_theme_result_word_refs,
    select_theme_batch,
    split_offer_key,
)
from automation.inago.settings import register_external_settings
from automation.inago.statistics import SearchAttempt, summarize_daily


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREPARATION_ROOT = PROJECT_ROOT / "plan" / "イナゴスキル事前準備"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
DETAILS_VERSION = "detailed-writing-rules-candidate-0.1-step68"
RUN_ID = "RUN-STEP07"
RUN_SEED = "TIE-20261012"
CREATED_AT = "2026-10-12T12:00:00+09:00"


@pytest.fixture
def selection_db(tmp_path: Path, fixture_config_path: Path) -> sqlite3.Connection:
    paths = resolve_runtime_paths(
        data_root=tmp_path / "step07" / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    basic = json.loads(
        (PREPARATION_ROOT / "初期設定引渡し.json").read_text(encoding="utf-8")
    )
    details = json.loads(
        (PREPARATION_ROOT / "文型詳細ルール.json").read_text(encoding="utf-8")
    )
    register_external_settings(connection, basic, details, created_at=CREATED_AT)
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, '2026-10-12', 'daily', 'running', ?, ?, ?, ?, ?,
                0, 0, NULL, 'not_started', NULL)
        """,
        (
            RUN_ID,
            SETTINGS_VERSION,
            RUN_SEED,
            "2026-10-12T13:00:00+09:00",
            "2026-10-12T17:45:00+09:00",
            "2026-10-12T18:00:00+09:00",
        ),
    )
    try:
        yield connection
    finally:
        connection.close()


def post(
    source_key: str,
    offer_key: str | None,
    likes: int | None,
    *,
    image_ref: str | None = None,
    is_product: bool = True,
) -> SourcePost:
    return SourcePost(
        source_key=source_key,
        offer_key=offer_key,
        likes=likes,
        image_ref=image_ref,
        is_product=is_product,
    )


def test_offer_key_is_reversible_and_rejects_ambiguous_identifiers() -> None:
    key = make_offer_key("shop-01", "item_ABC-9")

    assert key == "shop-01:item_ABC-9"
    assert split_offer_key(key) == ("shop-01", "item_ABC-9")
    with pytest.raises(RejectedContract):
        make_offer_key("shop:with-delimiter", "item-1")
    with pytest.raises(RejectedContract):
        split_offer_key("shop-01:item-1:variant-2")


def test_url_normalization_drops_tracking_and_identity_must_match_observed_ids() -> (
    None
):
    url = (
        "HTTPS://ITEM.RAKUTEN.CO.JP/shop-01/item-A/?scid=af_pc_etc"
        "&utm_source=fixture&sku=blue#reviews"
    )

    normalized = normalize_offer_url(url)
    identity = identify_offer(
        url, observed_shop_id="shop-01", observed_item_id="item-A"
    )

    assert normalized == "https://item.rakuten.co.jp/shop-01/item-A/?sku=blue"
    assert identity.status == "identified"
    assert identity.offer_key == "shop-01:item-A"
    assert identity.normalized_url == normalized


@pytest.mark.parametrize(
    ("url", "shop_id", "item_id", "reason"),
    [
        ("https://item.rakuten.co.jp/shop-01/item-A/", None, "item-A", "id_missing"),
        (
            "https://item.rakuten.co.jp/shop-01/item-A/",
            "shop-01",
            "item-B",
            "url_id_mismatch",
        ),
        (
            "https://example.invalid/shop-01/item-A/",
            "shop-01",
            "item-A",
            "unsupported_product_host",
        ),
        (None, "shop-01", "item-A", "url_missing"),
    ],
)
def test_unconfirmed_product_identity_never_creates_an_offer_key(
    url: str | None, shop_id: str | None, item_id: str | None, reason: str
) -> None:
    identity = identify_offer(url, observed_shop_id=shop_id, observed_item_id=item_id)

    assert identity.status == "unresolved"
    assert identity.offer_key is None
    assert identity.reason == reason


def test_exact_like_values_do_not_rank_or_exclude_verified_search_products() -> None:
    result = evaluate_sources(
        [
            post("P-600", "shop:item-600", 600),
            post("P-300", "shop:item-300", 300),
            post("P-NULL", "shop:item-null", None),
            post("P-UNKNOWN", None, 500, image_ref="img-same"),
        ],
        seed=RUN_SEED,
        history=OfferHistory(),
    )

    assert [item.source_key for item in result.eligible_representatives] == [
        "P-600",
        "P-300",
        "P-NULL",
    ]
    assert Counter(item.primary_reason for item in result.decisions) == {
        "eligible": 3,
        "offer_id_unresolved": 1,
    }
    assert sum(result.primary_reason_counts.values()) == result.observed_posts == 4


def test_first_post_is_representative_and_50_counts_raw_posts() -> None:
    repeated_offer_posts = [
        post(f"A-{likes}", "shop-A:item-A", likes, image_ref=f"img-{likes}")
        for likes in range(301, 311)
    ]
    remaining_posts = [
        post(f"C-{number}", f"shop-C:item-{number}", 300 + number)
        for number in range(39)
    ]
    result = evaluate_sources(
        repeated_offer_posts + [post("B-600", "shop-B:item-B", 600)] + remaining_posts,
        seed=RUN_SEED,
        history=OfferHistory(),
    )

    assert result.observed_posts == 50
    assert len(result.representatives) == 41
    assert result.representatives[0].offer_key == "shop-A:item-A"
    representative_a = next(
        item for item in result.representatives if item.offer_key == "shop-A:item-A"
    )
    assert representative_a.likes == 301
    assert representative_a.source_key == "A-301"
    assert "A-310" not in {item.source_key for item in result.representatives}
    assert result.primary_reason_counts["same_search_offer_duplicate"] == 9
    assert sum(result.primary_reason_counts.values()) == 50


def test_source_limit_rejects_more_than_50_observations() -> None:
    posts = [post(f"P-{n}", f"shop:item-{n}", 300) for n in range(51)]

    with pytest.raises(RejectedContract):
        evaluate_sources(posts, seed=RUN_SEED, history=OfferHistory())


def test_tied_representatives_are_reproducible_from_saved_seed() -> None:
    posts = [
        post("A-1", "shop-A:item-A", 500),
        post("A-2", "shop-A:item-A", 500),
        post("B-1", "shop-B:item-B", 500),
        post("C-1", "shop-C:item-C", 400),
    ]

    first = evaluate_sources(posts, seed=RUN_SEED, history=OfferHistory())
    resumed = evaluate_sources(posts, seed=RUN_SEED, history=OfferHistory())

    assert first == resumed
    assert [item.offer_key for item in first.representatives] == [
        item.offer_key for item in resumed.representatives
    ]
    assert (
        len([item for item in first.decisions if item.primary_reason == "eligible"])
        == 3
    )


def test_identity_not_image_controls_grouping_and_history_exclusion_order() -> None:
    history = OfferHistory(
        same_day_selected=frozenset({"shop-A:item-day"}),
        permanently_posted=frozenset({"shop-A:item-day", "shop-A:item-posted"}),
        pending_posted=frozenset({"shop-A:item-pending"}),
        active_reservations=frozenset({"shop-A:item-reserved"}),
    )
    result = evaluate_sources(
        [
            post("A-color-1", "shop-A:item-color", 400, image_ref="img-1"),
            post("A-color-2", "shop-A:item-color", 500, image_ref="img-2"),
            post("B-same-image", "shop-A:item-other", 450, image_ref="img-1"),
            post("C-other-shop", "shop-B:item-color", 425, image_ref="img-1"),
            post("DAY", "shop-A:item-day", 500),
            post("POSTED", "shop-A:item-posted", 500),
            post("PENDING", "shop-A:item-pending", 500),
            post("RESERVED", "shop-A:item-reserved", 500),
        ],
        seed=RUN_SEED,
        history=history,
    )
    by_source = {item.source_key: item for item in result.decisions}

    assert by_source["A-color-1"].primary_reason == "eligible"
    assert by_source["A-color-2"].primary_reason == "same_search_offer_duplicate"
    assert by_source["B-same-image"].primary_reason == "eligible"
    assert by_source["C-other-shop"].primary_reason == "eligible"
    assert by_source["DAY"].primary_reason == "same_day_selected"
    assert by_source["DAY"].secondary_reasons == ("permanently_posted",)
    assert by_source["POSTED"].primary_reason == "permanently_posted"
    assert by_source["PENDING"].primary_reason == "pending_posted_report"
    assert by_source["RESERVED"].primary_reason == "active_reservation"
    assert {item.offer_key for item in result.eligible_representatives} == {
        "shop-A:item-color",
        "shop-A:item-other",
        "shop-B:item-color",
    }
    assert sum(result.primary_reason_counts.values()) == len(result.decisions) == 8


def test_offer_history_reads_same_day_posted_pending_and_reserved_state_from_db(
    selection_db: sqlite3.Connection,
) -> None:
    selection_db.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('RUN-PRIOR', '2026-10-11', 'daily', 'complete', ?, 'SEED-PRIOR',
                '2026-10-11T13:00:00+09:00', '2026-10-11T17:45:00+09:00',
                '2026-10-11T18:00:00+09:00', 1, 0, NULL, 'not_started',
                '2026-10-11T15:00:00+09:00')
        """,
        (SETTINGS_VERSION,),
    )
    keys = {
        name: f"shop_test:item_{name}"
        for name in (
            "same",
            "selected",
            "posted",
            "pending",
            "reserved",
            "failed",
        )
    }
    for name, offer_key in keys.items():
        selection_db.execute(
            """
            INSERT INTO offer
                (offer_key, shop_id, item_id, name, url, image_refs_json,
                 main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
            VALUES (?, 'shop_test', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
            """,
            (
                offer_key,
                f"item_{name}",
                f"fixture {name}",
                f"https://item.rakuten.co.jp/shop_test/item_{name}/",
                "2026-10-11T13:00:00+09:00",
                "2026-10-12T13:00:00+09:00",
            ),
        )
    selection_db.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at, committed_at,
             generator_version, pattern_version, status, artifact_ref, pattern_id,
             evidence_refs_json)
        VALUES ('D-SAME', ?, ?, 1, 'body', '2026-10-12T13:01:00+09:00',
                '2026-10-12T13:01:00+09:00', 'gen', 'patterns', 'committed',
                NULL, 'P01', '[]')
        """,
        (keys["same"], RUN_ID),
    )
    for name in ("pending", "reserved"):
        selection_db.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at, committed_at,
                 generator_version, pattern_version, status, artifact_ref, pattern_id,
                 evidence_refs_json)
            VALUES (?, ?, 'RUN-PRIOR', 1, 'body', '2026-10-11T13:01:00+09:00',
                    '2026-10-11T13:01:00+09:00', 'gen', 'patterns', 'committed',
                    NULL, 'P01', '[]')
            """,
            (f"D-{name.upper()}", keys[name]),
        )
    selection_db.execute(
        """
        INSERT INTO human_action
            (action_id, action, offer_key, draft_id, draft_version, prior_revision,
             receipt_revision, reason, reported_at, old_event_ref, new_body,
             apply_status, applied_at, applied_run_id, apply_error)
        VALUES ('ACTION-PENDING', 'posted', ?, 'D-PENDING', 1, 0, 1, NULL,
                '2026-10-11T21:00:00+09:00', NULL, NULL, 'pending', NULL, NULL, NULL)
        """,
        (keys["pending"],),
    )
    selection_db.execute(
        """
        INSERT INTO reservation
            (reservation_id, offer_key, draft_id, status, created_at,
             released_at, release_reason)
        VALUES ('RES-RESERVED', ?, 'D-RESERVED', 'active',
                '2026-10-11T13:02:00+09:00', NULL, NULL)
        """,
        (keys["reserved"],),
    )
    selection_db.execute(
        """
        INSERT INTO publication_event
            (event_id, offer_key, draft_id, draft_version, reported_at,
             actual_posted_at, room_url, evidence_kind, valid, revision)
        VALUES ('EVENT-POSTED', ?, NULL, NULL, '2026-10-11T21:00:00+09:00',
                NULL, NULL, 'human_statement', 1, 0)
        """,
        (keys["posted"],),
    )
    selection_db.execute(
        """
        INSERT INTO exclusion_key
            (offer_key, reason, evidence_refs_json, effective, updated_at)
        VALUES (?, 'actual_posted', '["EVENT-POSTED"]', 1, '2026-10-11T21:00:00+09:00')
        """,
        (keys["posted"],),
    )
    selection_db.execute(
        """
        INSERT INTO generation_attempt
            (generation_attempt_id, run_id, offer_key, draft_id, attempt_no,
             outcome, artifact_ref, error_code, started_at, ended_at)
        VALUES ('GEN-FAILED', ?, ?, NULL, 3, 'error', NULL, 'fixture_error',
                '2026-10-12T13:10:00+09:00', '2026-10-12T13:11:00+09:00')
        """,
        (RUN_ID, keys["failed"]),
    )
    selection_db.execute(
        """
        INSERT INTO generation_attempt
            (generation_attempt_id, run_id, offer_key, draft_id, attempt_no,
             outcome, artifact_ref, error_code, started_at, ended_at)
        VALUES ('GEN-SELECTED', ?, ?, NULL, 1, 'in_progress', NULL, NULL,
                '2026-10-12T13:10:00+09:00', NULL)
        """,
        (RUN_ID, keys["selected"]),
    )

    history = load_offer_history(selection_db, RUN_ID)

    assert history.same_day_selected == {keys["selected"]}
    assert history.completed == {keys["same"]}
    assert keys["posted"] in history.permanently_posted
    assert history.pending_posted == {keys["pending"]}
    assert history.active_reservations == {keys["reserved"]}
    assert history.generation_failed == {keys["failed"]}


def test_theme_selection_uses_lowest_counts_seeded_ties_and_active_themes_only() -> (
    None
):
    themes = (
        ThemeCandidate("T-A", 2),
        ThemeCandidate("T-B", 0),
        ThemeCandidate("T-C", 1),
        ThemeCandidate("T-D", 0),
        ThemeCandidate("T-RETIRED", 0, state="retired"),
    )

    selected = choose_themes(themes, seed=RUN_SEED, count=3)

    assert set(selected) == {"T-B", "T-C", "T-D"}
    assert selected == choose_themes(themes, seed=RUN_SEED, count=3)
    with pytest.raises(RejectedContract):
        choose_themes(themes, seed="", count=3)


def test_initial_and_additional_theme_count_events_are_atomic_and_idempotent(
    selection_db: sqlite3.Connection,
) -> None:
    stored = json.loads(
        selection_db.execute(
            "SELECT themes_json FROM settings_version WHERE version=?",
            (SETTINGS_VERSION,),
        ).fetchone()[0]
    )
    counts = {theme["theme_id"]: 3 for theme in stored}
    counts.update(
        {
            "G01-T01": 2,
            "G01-T02": 0,
            "G01-T03": 1,
            "G01-T04": 0,
        }
    )
    for theme in stored:
        theme["selection_count"] = counts[theme["theme_id"]]
    selection_db.execute(
        "UPDATE settings_version SET themes_json=? WHERE version=?",
        (json.dumps(stored, ensure_ascii=False), SETTINGS_VERSION),
    )

    candidates = load_theme_candidates(selection_db, RUN_ID)
    assert len(candidates) == 30
    initial = choose_themes(candidates, seed=RUN_SEED, count=3)
    assert set(initial) == {"G01-T02", "G01-T03", "G01-T04"}
    saved_initial = select_theme_batch(
        selection_db,
        RUN_ID,
        event_kind="initial",
        selected_at="2026-10-12T13:01:00+09:00",
    )
    assert {event.theme_id for event in saved_initial} == set(initial)
    result_event = record_theme_result_word_refs(
        selection_db,
        RUN_ID,
        event_kind="initial",
        ordinal=1,
        result_word_refs=("WORD-OBSERVED-1", "WORD-OBSERVED-2"),
    )
    assert result_event.result_word_refs == ("WORD-OBSERVED-1", "WORD-OBSERVED-2")
    assert (
        record_theme_result_word_refs(
            selection_db,
            RUN_ID,
            event_kind="initial",
            ordinal=1,
            result_word_refs=("WORD-OBSERVED-1", "WORD-OBSERVED-2"),
        )
        == result_event
    )
    with pytest.raises(IdempotencyConflict):
        record_theme_result_word_refs(
            selection_db,
            RUN_ID,
            event_kind="initial",
            ordinal=1,
            result_word_refs=("WORD-DIFFERENT",),
        )
    replayed_initial = select_theme_batch(
        selection_db,
        RUN_ID,
        event_kind="initial",
        selected_at="2026-10-12T13:02:00+09:00",
    )

    assert [event.selection_id for event in replayed_initial] == [
        event.selection_id for event in saved_initial
    ]
    assert replayed_initial[0].result_word_refs == result_event.result_word_refs
    assert (
        load_theme_selection_events(selection_db, RUN_ID, event_kind="initial")
        == replayed_initial
    )
    assert (
        selection_db.execute("SELECT COUNT(*) FROM theme_selection").fetchone()[0] == 3
    )
    updated = load_theme_candidates(selection_db, RUN_ID)
    assert {item.theme_id: item.selection_count for item in updated}["G01-T02"] == 1
    additional = choose_themes(updated, seed=RUN_SEED, count=1)
    saved_additional = select_theme_batch(
        selection_db,
        RUN_ID,
        event_kind="additional",
        selected_at="2026-10-12T13:10:00+09:00",
    )
    assert {event.theme_id for event in saved_additional} == set(additional)
    replayed_additional = select_theme_batch(
        selection_db,
        RUN_ID,
        event_kind="additional",
        selected_at="2026-10-12T13:11:00+09:00",
    )

    assert replayed_additional == saved_additional
    assert (
        selection_db.execute("SELECT COUNT(*) FROM theme_selection").fetchone()[0] == 4
    )
    assert (
        selection_db.execute(
            "SELECT COUNT(DISTINCT count_event_id) FROM theme_selection"
        ).fetchone()[0]
        == 4
    )


def test_theme_event_conflicts_and_inactive_or_wrong_seed_choices_fail_closed(
    selection_db: sqlite3.Connection,
) -> None:
    with pytest.raises(RejectedContract):
        persist_theme_selection_events(
            selection_db,
            RUN_ID,
            event_kind="initial",
            selected_theme_ids=("UNKNOWN-THEME",),
            seed=RUN_SEED,
            selected_at="2026-10-12T13:01:00+09:00",
        )
    with pytest.raises(RejectedContract):
        persist_theme_selection_events(
            selection_db,
            RUN_ID,
            event_kind="initial",
            selected_theme_ids=("G01-T01",),
            seed="another-seed",
            selected_at="2026-10-12T13:01:00+09:00",
        )
    valid_initial = ("G01-T01", "G01-T02", "G01-T03")
    persist_theme_selection_events(
        selection_db,
        RUN_ID,
        event_kind="initial",
        selected_theme_ids=valid_initial,
        seed=RUN_SEED,
        selected_at="2026-10-12T13:01:00+09:00",
    )

    with pytest.raises(IdempotencyConflict):
        persist_theme_selection_events(
            selection_db,
            RUN_ID,
            event_kind="initial",
            selected_theme_ids=("G01-T04", "G01-T05", "G01-T06"),
            seed=RUN_SEED,
            selected_at="2026-10-12T13:02:00+09:00",
        )
    assert (
        selection_db.execute("SELECT COUNT(*) FROM theme_selection").fetchone()[0] == 3
    )


def test_daily_statistics_separate_attempts_posts_representatives_union_D_and_P() -> (
    None
):
    first_search = evaluate_sources(
        [
            *[post(f"A-{likes}", "shop-A:item-A", likes) for likes in range(301, 311)],
            post("B-600", "shop-B:item-B", 600),
        ],
        seed=RUN_SEED,
        history=OfferHistory(),
    )
    successful_zero = evaluate_sources([], seed=RUN_SEED, history=OfferHistory())
    next_search = evaluate_sources(
        [post("A-again", "shop-A:item-A", 450)],
        seed=RUN_SEED,
        history=OfferHistory(),
    )

    summary = summarize_daily(
        [
            SearchAttempt(1, "observed", first_search),
            SearchAttempt(2, "observed", successful_zero),
            SearchAttempt(3, "observed", next_search),
        ],
        committed_count=11,
        posted_count=20,
    )

    assert summary.seed == RUN_SEED
    assert summary.search_attempts == 3
    assert summary.search_status_counts == {"observed": 3}
    assert summary.observed_posts == 12
    assert summary.representative_count == 3
    assert summary.distinct_offer_count == 2
    assert summary.committed_count == 11
    assert summary.posted_count == 20
    assert summary.shortfall_count == 4
    assert summary.primary_reason_counts["same_search_offer_duplicate"] == 9
    assert sum(summary.primary_reason_counts.values()) == summary.observed_posts


def test_q19_367_observations_42_representatives_38_distinct_and_D11() -> None:
    completed_keys = {f"shop:item-{number}" for number in range(27, 38)}
    posted_keys = {f"shop:item-{number}" for number in range(20)}
    reserved_keys = {f"shop:item-{number}" for number in range(20, 27)}
    repeated_keys = [f"shop:item-{number}" for number in range(27, 31)]
    representative_keys = [
        *(f"shop:item-{number}" for number in range(38)),
        *repeated_keys,
    ]
    representatives_per_search = [6, 6, 5, 5, 5, 5, 4, 3, 3]
    observations_per_search = [41, 41, 41, 41, 41, 41, 41, 41, 39]
    attempts = []
    cursor = 0
    same_day_keys: set[str] = set()

    for attempt_no, (representative_count, observed_count) in enumerate(
        zip(representatives_per_search, observations_per_search), start=1
    ):
        keys = representative_keys[cursor : cursor + representative_count]
        cursor += representative_count
        history = OfferHistory(
            same_day_selected=frozenset(same_day_keys),
            permanently_posted=frozenset(posted_keys),
            active_reservations=frozenset(reserved_keys),
            completed=frozenset(completed_keys),
        )
        source_posts = [
            post(
                f"R-{attempt_no}-{offer_key}",
                offer_key,
                700 - int(offer_key.rsplit("-", 1)[1]),
            )
            for offer_key in keys
        ]
        for duplicate_no in range(observed_count - representative_count):
            source_posts.append(
                post(
                    f"D-{attempt_no}-{duplicate_no}",
                    keys[0],
                    300,
                )
            )
        result = evaluate_sources(source_posts, seed=RUN_SEED, history=history)
        same_day_keys.update(item.offer_key for item in result.representatives)
        attempts.append(SearchAttempt(attempt_no, "observed", result))

    assert cursor == 42
    summary = summarize_daily(attempts, committed_count=11, posted_count=20)

    assert summary.search_attempts == 9
    assert summary.observed_posts == 367
    assert summary.representative_count == 42
    assert summary.distinct_offer_count == 38
    assert summary.committed_count == 11
    assert summary.posted_count == 20
    assert summary.shortfall_count == 4
    assert summary.primary_reason_counts == {
        "active_reservation": 7,
        "completed": 11,
        "permanently_posted": 20,
        "same_day_selected": 4,
        "same_search_offer_duplicate": 325,
    }
    assert sum(summary.primary_reason_counts.values()) == 367


def test_daily_statistics_count_failed_zero_attempts_and_enforce_daily_search_bound() -> (
    None
):
    empty = evaluate_sources([], seed=RUN_SEED, history=OfferHistory())
    summary = summarize_daily(
        [
            SearchAttempt(1, "observed", empty),
            SearchAttempt(2, "unavailable", empty),
        ],
        committed_count=0,
        posted_count=0,
    )

    assert summary.search_attempts == 2
    assert summary.search_status_counts == {"observed": 1, "unavailable": 1}
    assert summary.observed_posts == 0
    with pytest.raises(RejectedContract):
        summarize_daily(
            [SearchAttempt(n, "observed", empty) for n in range(1, 11)],
            committed_count=0,
            posted_count=0,
        )
    with pytest.raises(RejectedContract):
        summarize_daily([], committed_count=16, posted_count=0)
    with pytest.raises(RejectedContract):
        summarize_daily(
            [
                SearchAttempt(1, "observed", empty),
                SearchAttempt(
                    2,
                    "observed",
                    evaluate_sources([], seed="different-seed", history=OfferHistory()),
                ),
            ],
            committed_count=0,
            posted_count=0,
        )
