"""Fixture-only contracts for metrics, attribution, and optional explanations."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from automation.inago.clock import FakeClock
from automation.inago.metrics import (
    DailyMetricObservation,
    MetricsCollectionResult,
    MetricsAuthenticationRequired,
    MetricsSnapshot,
    MetricsSourceResult,
    OrderItemObservation,
    OrderWindowObservation,
    RoomPostObservation,
    collect_metrics_in_order,
    commit_metrics_result,
    maybe_explain_metrics,
)
from automation.inago.orchestrator import _collect_metrics, _daily_state
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.settings import register_external_settings
from automation.inago.statistics import summarize_metrics


ROOT = Path(__file__).resolve().parents[1]
BASIC_SETTINGS_PATH = (
    ROOT / "docs" / "specifications" / "preparation" / "初期設定引渡し.json"
)
DETAIL_RULES_PATH = (
    ROOT
    / "worklogs"
    / "projects"
    / "preparation"
    / "candidates"
    / "文型詳細ルール.json"
)
PRIVATE_TEST_ROOT = ROOT / "非公開作業データ" / "イナゴ実装試験" / "step-14"
JST = ZoneInfo("Asia/Tokyo")
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
RUN_ID = "STEP14-RUN"
NOW = "2026-10-12T13:20:00+09:00"


@dataclass
class MetricsCase:
    connection: sqlite3.Connection
    data_root: Path
    clock: FakeClock
    private_root: Path


@pytest.fixture
def metrics_case(tmp_path: Path, fixture_config_path: Path) -> Any:
    private_root = PRIVATE_TEST_ROOT / f"case-{uuid.uuid4().hex}"
    paths = resolve_runtime_paths(
        data_root=private_root / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    basic = json.loads((BASIC_SETTINGS_PATH).read_text(encoding="utf-8"))
    detailed = json.loads((DETAIL_RULES_PATH).read_text(encoding="utf-8"))
    register_external_settings(connection, basic, detailed, created_at=NOW)
    _seed_daily_state(connection)
    case = MetricsCase(
        connection,
        paths.data_root,
        FakeClock(datetime.fromisoformat(NOW)),
        private_root,
    )
    try:
        yield case
    finally:
        connection.close()
        resolved_root = private_root.resolve()
        allowed_root = PRIVATE_TEST_ROOT.resolve()
        if allowed_root in resolved_root.parents:
            shutil.rmtree(resolved_root, ignore_errors=True)


def _seed_daily_state(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, '2026-10-12', 'daily', 'partial', ?, 'STEP14-SEED',
                '2026-10-12T13:00:00+09:00', '2026-10-12T17:45:00+09:00',
                '2026-10-12T18:00:00+09:00', 1, 1, NULL, 'not_started', NULL)
        """,
        (RUN_ID, SETTINGS_VERSION),
    )
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES ('shop_a:item_1', 'shop_a', 'item_1', '架空の試験商品',
                'https://item.rakuten.co.jp/shop_a/item_1/', '[]',
                'G01-T01', '[]', ?, ?, '[]')
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES ('D-STEP14', 'shop_a:item_1', ?, 1, '保存済みの架空本文', ?, ?,
                'fixture-generator', 'confirmed-0.3-step46-all132', 'committed',
                NULL, 'P01', '[]')
        """,
        (RUN_ID, NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO publication_event
            (event_id, offer_key, draft_id, draft_version, reported_at,
             actual_posted_at, room_url, evidence_kind, valid, revision)
        VALUES ('PUB-STEP14', 'shop_a:item_1', 'D-STEP14', 1, ?,
                '2026-10-12T13:00:00+09:00',
                'https://room.rakuten.co.jp/room_demo/items/PUB-STEP14',
                'human_statement', 1, 0)
        """,
        (NOW,),
    )
    connection.commit()


def _snapshots(*, observed_at: str = NOW) -> tuple[MetricsSnapshot, ...]:
    return (
        MetricsSnapshot(
            source_key="affiliate",
            source="affiliate",
            status="observed",
            observed_at=observed_at,
            payload={"fixture": True, "scope": "affiliate"},
        ),
        MetricsSnapshot(
            source_key="room",
            source="room",
            status="observed",
            observed_at=observed_at,
            payload={"fixture": True, "scope": "room"},
        ),
    )


def _daily_rows(
    *, observed_at: str = NOW, sales_yen: int = 1000
) -> tuple[DailyMetricObservation, ...]:
    return tuple(
        DailyMetricObservation(
            source_key="affiliate",
            ref="2026-10-11",
            metric=metric,
            value=value,
            unit=unit,
            observed_at=observed_at,
            window="affiliate-daily:2026-10-11",
            availability="observed",
            provisional=provisional,
        )
        for metric, value, unit, provisional in (
            ("clicks", 0, "count", False),
            ("sales_count", 0, "count", True),
            ("sales_yen", sales_yen, "JPY", True),
            ("commission_unconfirmed_yen", 50, "JPY", True),
        )
    )


def _order(
    *,
    order_ref: str | None = "ORDER-1",
    temporary: bool = False,
    sales_yen: int = 1000,
    commission_yen: int = 50,
    order_status: str = "未確定",
    observed_at: str = NOW,
    item_code: str | None = "shop_a:item_1",
    product_url: str | None = "https://item.rakuten.co.jp/shop_a/item_1/",
    shop_name: str | None = "店舗A",
    product_name: str | None = "架空の試験商品",
    entry_post_ref: str | None = None,
) -> OrderItemObservation:
    return OrderItemObservation(
        source_key="affiliate",
        order_ref=order_ref,
        is_temporary_ref=temporary,
        occurred_at="2026-10-11T10:00:00+09:00",
        shop_name=shop_name,
        product_name=product_name,
        item_code=item_code,
        product_url=product_url,
        sales_yen=sales_yen,
        commission_yen=commission_yen,
        order_status=order_status,
        link_type="商品リンク",
        window="affiliate-orders:2026-10",
        observed_at=observed_at,
        entry_post_ref=entry_post_ref,
    )


def _room_row(
    *,
    observed_at: str = NOW,
    likes: int | None = 0,
    post_created_at: str | None = "2026-10-12T13:00:00+09:00",
) -> RoomPostObservation:
    return RoomPostObservation(
        source_key="room",
        post_id="ROOM-POST-1",
        room_post_url="https://room.rakuten.co.jp/room_demo/items/PUB-STEP14",
        item_url="https://item.rakuten.co.jp/shop_a/item_1/",
        likes=likes,
        observed_at=observed_at,
        window="room-recent-30d:2026-09-13/2026-10-12",
        post_created_at=post_created_at,
    )


def _result(
    *,
    run_id: str = RUN_ID,
    observed_at: str = NOW,
    sales_yen: int = 1000,
    orders: tuple[OrderItemObservation, ...] | None = None,
    room_posts: tuple[RoomPostObservation, ...] | None = None,
    order_windows: tuple[OrderWindowObservation, ...] | None = None,
    snapshots: tuple[MetricsSnapshot, ...] | None = None,
    status: str = "observed",
) -> MetricsCollectionResult:
    order_items = (
        orders
        if orders is not None
        else (_order(sales_yen=sales_yen, observed_at=observed_at),)
    )
    monthly_counts = order_windows
    if monthly_counts is None and status in {"observed", "partial"}:
        monthly_counts = (
            OrderWindowObservation(
                source_key="affiliate",
                window="affiliate-orders:2026-10",
                count=len(order_items),
                observed_at=observed_at,
                availability="observed",
            ),
        )
    return MetricsCollectionResult(
        run_id=run_id,
        status=status,
        observed_at=observed_at,
        snapshots=snapshots or _snapshots(observed_at=observed_at),
        daily_observations=_daily_rows(observed_at=observed_at, sales_yen=sales_yen),
        orders=order_items,
        order_windows=monthly_counts or (),
        room_posts=(
            room_posts
            if room_posts is not None
            else (_room_row(observed_at=observed_at),)
        ),
    )


def _commit(
    case: MetricsCase,
    result: MetricsCollectionResult,
    *,
    deadline_at: str = "2026-10-12T18:00:00+09:00",
) -> Any:
    return commit_metrics_result(
        case.connection,
        data_root=case.data_root,
        result=result,
        clock=case.clock,
        deadline_at=datetime.fromisoformat(deadline_at),
    )


def _seed_next_day_run(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('STEP14-RUN-2', '2026-10-13', 'daily', 'partial', ?, 'STEP14-SEED-2',
                '2026-10-13T13:00:00+09:00', '2026-10-13T17:45:00+09:00',
                '2026-10-13T18:00:00+09:00', 0, 0, NULL, 'not_started', NULL)
        """,
        (SETTINGS_VERSION,),
    )
    connection.commit()


def test_commit_keeps_observed_zero_null_and_scope_separate(
    metrics_case: MetricsCase,
) -> None:
    _commit(metrics_case, _result())
    connection = metrics_case.connection
    rows = connection.execute(
        "SELECT scope, ref, metric, value_json, availability, provisional, source_ref "
        "FROM metric_observation ORDER BY scope, metric"
    ).fetchall()

    clicks = next(row for row in rows if row["metric"] == "clicks")
    assert clicks["value_json"] == "0"
    assert clicks["availability"] == "observed"
    commission = next(
        row for row in rows if row["metric"] == "commission_unconfirmed_yen"
    )
    assert commission["value_json"] == "50"
    assert commission["provisional"] == 1
    room = next(row for row in rows if row["scope"] == "room_post")
    assert room["value_json"] == "0"
    assert room["ref"] == "PUB-STEP14"
    assert room["source_ref"]

    artifacts = connection.execute(
        "SELECT source_kind, retention_expires_at FROM artifact"
    ).fetchall()
    assert len(artifacts) == 2
    assert {row["source_kind"] for row in artifacts} == {"metrics"}
    assert {row["retention_expires_at"] for row in artifacts} == {
        "2027-01-10T13:20:00+09:00"
    }


def test_successful_missing_room_post_is_not_zero_and_unknown_age_is_null(
    metrics_case: MetricsCase,
) -> None:
    metrics_case.connection.execute(
        "UPDATE publication_event SET actual_posted_at=NULL WHERE event_id='PUB-STEP14'"
    )
    metrics_case.connection.commit()
    row = _room_row(likes=None, post_created_at=None)
    result = _result(room_posts=(row,))
    _commit(metrics_case, result)
    observation = metrics_case.connection.execute(
        "SELECT value_json, availability, window FROM metric_observation "
        "WHERE scope='room_post' AND metric='likes'"
    ).fetchone()
    assert observation["value_json"] in (None, "null")
    assert observation["availability"] == "unknown"
    assert json.loads(observation["window"])["post_age_seconds"] is None


def test_order_correction_updates_current_value_and_keeps_observation_history(
    metrics_case: MetricsCase,
) -> None:
    _commit(metrics_case, _result())
    _seed_next_day_run(metrics_case.connection)
    corrected = _result(
        run_id="STEP14-RUN-2",
        observed_at="2026-10-13T13:20:00+09:00",
        sales_yen=800,
        orders=(
            _order(
                sales_yen=800,
                commission_yen=40,
                order_status="破棄",
                observed_at="2026-10-13T13:20:00+09:00",
            ),
        ),
    )
    metrics_case.clock.advance(24 * 60 * 60)
    _commit(metrics_case, corrected, deadline_at="2026-10-13T18:00:00+09:00")
    order = metrics_case.connection.execute(
        "SELECT sales_yen, commission_yen, order_status, match_kind "
        "FROM revenue_order WHERE order_ref='ORDER-1'"
    ).fetchone()
    history = metrics_case.connection.execute(
        "SELECT value_json FROM metric_observation "
        "WHERE scope='order_item' AND ref='ORDER-1' AND metric='sales_yen' "
        "ORDER BY observed_at"
    ).fetchall()
    assert order["sales_yen"] == 800
    assert order["commission_yen"] == 40
    assert order["order_status"] == "破棄"
    assert order["match_kind"] == "purchased_item_match"
    assert [row["value_json"] for row in history] == ["1000", "800"]
    summary = summarize_metrics(metrics_case.connection)
    assert summary.orders_current_sales_yen == 800
    assert summary.affiliate_daily["sales_yen"].known_total == 800
    assert "order_sales_snapshots_sum" not in summary.__dataclass_fields__


def test_temporary_order_ref_becomes_stable_once_without_duplicate(
    metrics_case: MetricsCase,
) -> None:
    temporary = _order(order_ref=None, temporary=True)
    _commit(metrics_case, _result(orders=(temporary,)))
    metrics_case.clock.advance(60)
    stable = _order(observed_at=metrics_case.clock.now_jst().isoformat())
    corrected = _result(
        observed_at=metrics_case.clock.now_jst().isoformat(),
        orders=(stable,),
    )
    _commit(metrics_case, corrected)
    orders = metrics_case.connection.execute(
        "SELECT order_ref, is_temporary_ref FROM revenue_order"
    ).fetchall()
    assert [tuple(row) for row in orders] == [("ORDER-1", 0)]


def test_purchase_match_does_not_infer_entry_post_and_only_direct_ref_confirms(
    metrics_case: MetricsCase,
) -> None:
    _commit(metrics_case, _result())
    current = metrics_case.connection.execute(
        "SELECT match_kind, matched_offer_key, entry_post_ref FROM revenue_order"
    ).fetchone()
    assert tuple(current) == ("purchased_item_match", "shop_a:item_1", None)

    metrics_case.clock.advance(60)
    direct = _result(
        observed_at=metrics_case.clock.now_jst().isoformat(),
        orders=(
            _order(
                observed_at=metrics_case.clock.now_jst().isoformat(),
                entry_post_ref="PUB-STEP14",
            ),
        ),
    )
    _commit(metrics_case, direct)
    confirmed = metrics_case.connection.execute(
        "SELECT match_kind, entry_post_ref FROM revenue_order WHERE order_ref='ORDER-1'"
    ).fetchone()
    assert tuple(confirmed) == ("entry_post_confirmed", "PUB-STEP14")


def test_ambiguous_item_code_stays_unmatched_without_store_evidence(
    metrics_case: MetricsCase,
) -> None:
    metrics_case.connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES ('shop_b:item_1', 'shop_b', 'item_1', '架空の試験商品',
                'https://item.rakuten.co.jp/shop_b/item_1/', '[]',
                'G01-T01', '[]', ?, ?, '[]')
        """,
        (NOW, NOW),
    )
    ambiguous = _order(
        item_code="item_1",
        product_url=None,
        product_name="架空の試験商品",
        shop_name="同名店舗",
    )
    _commit(metrics_case, _result(orders=(ambiguous,)))
    order = metrics_case.connection.execute(
        "SELECT match_kind, matched_offer_key, entry_post_ref FROM revenue_order"
    ).fetchone()
    assert tuple(order) == ("unmatched", None, None)


def test_unique_exact_product_name_can_match_but_never_proves_entry_post(
    metrics_case: MetricsCase,
) -> None:
    by_name = _order(item_code=None, product_url=None)
    _commit(metrics_case, _result(orders=(by_name,)))
    order = metrics_case.connection.execute(
        "SELECT match_kind, matched_offer_key, entry_post_ref FROM revenue_order"
    ).fetchone()
    assert tuple(order) == ("purchased_item_match", "shop_a:item_1", None)


def test_auth_failure_keeps_saved_draft_and_does_not_report_zero(
    metrics_case: MetricsCase,
) -> None:
    before = metrics_case.connection.execute(
        "SELECT body, version FROM draft WHERE draft_id='D-STEP14'"
    ).fetchone()
    unavailable = MetricsCollectionResult(
        run_id=RUN_ID,
        status="unavailable",
        observed_at=NOW,
        snapshots=(
            MetricsSnapshot(
                source_key="affiliate",
                source="affiliate",
                status="unavailable",
                observed_at=NOW,
                payload={"error_code": "auth_required"},
            ),
            MetricsSnapshot(
                source_key="room",
                source="room",
                status="unavailable",
                observed_at=NOW,
                payload={"error_code": "not_attempted_after_deadline"},
            ),
        ),
        daily_observations=tuple(
            DailyMetricObservation(
                source_key="affiliate",
                ref="2026-10-11",
                metric=metric,
                value=None,
                unit=unit,
                observed_at=NOW,
                window="affiliate-daily:2026-10-11",
                availability="unavailable",
                provisional=provisional,
            )
            for metric, unit, provisional in (
                ("clicks", "count", False),
                ("sales_count", "count", True),
                ("sales_yen", "JPY", True),
                ("commission_unconfirmed_yen", "JPY", True),
            )
        ),
        order_windows=(
            OrderWindowObservation(
                source_key="affiliate",
                window="affiliate-orders:2026-10",
                count=None,
                observed_at=NOW,
                availability="unavailable",
            ),
        ),
    )
    metrics_case.connection.execute(
        "UPDATE run SET metrics_status='pending' WHERE run_id=?", (RUN_ID,)
    )
    _commit(metrics_case, unavailable)
    after = metrics_case.connection.execute(
        "SELECT body, version FROM draft WHERE draft_id='D-STEP14'"
    ).fetchone()
    assert tuple(after) == tuple(before)
    assert (
        metrics_case.connection.execute(
            "SELECT COUNT(*) FROM metric_observation WHERE value_json IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    assert summarize_metrics(metrics_case.connection).order_count.known_total is None
    unavailable = metrics_case.connection.execute(
        "SELECT value_json, availability FROM metric_observation "
        "WHERE scope='room_post' AND metric='likes'"
    ).fetchone()
    assert unavailable["value_json"] is None
    assert unavailable["availability"] == "unavailable"
    assert (
        metrics_case.connection.execute(
            "SELECT COUNT(*) FROM revenue_order"
        ).fetchone()[0]
        == 0
    )


def test_optional_insight_is_never_called_without_explicit_request(
    metrics_case: MetricsCase,
) -> None:
    class Insight:
        def __init__(self) -> None:
            self.calls = 0

        def explain(self, summary: Any) -> dict[str, Any]:
            self.calls += 1
            return {"text": "fixture explanation", "summary_ref": "SUMMARY-1"}

    insight = Insight()
    before = metrics_case.connection.execute(
        "SELECT COUNT(*) FROM artifact"
    ).fetchone()[0]
    result = maybe_explain_metrics(
        {"fixture": True}, explicitly_requested=False, port=insight
    )
    assert result is None
    assert insight.calls == 0
    assert (
        metrics_case.connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
        == before
    )


def test_optional_insight_failure_does_not_change_saved_metrics(
    metrics_case: MetricsCase,
) -> None:
    _commit(metrics_case, _result())
    before = metrics_case.connection.execute(
        "SELECT COUNT(*) FROM metric_observation"
    ).fetchone()[0]

    class BrokenInsight:
        def explain(self, summary: Any) -> dict[str, Any]:
            raise TimeoutError("fixture timeout")

    response = maybe_explain_metrics(
        {"fixture": True}, explicitly_requested=True, port=BrokenInsight()
    )
    after = metrics_case.connection.execute(
        "SELECT COUNT(*) FROM metric_observation"
    ).fetchone()[0]
    assert response.status == "unavailable"
    assert response.error_code == "TimeoutError"
    assert after == before


def test_successful_room_query_marks_unreturned_post_missing_not_zero(
    metrics_case: MetricsCase,
) -> None:
    _commit(metrics_case, _result(room_posts=()))
    missing = metrics_case.connection.execute(
        "SELECT value_json, availability FROM metric_observation "
        "WHERE scope='room_post' AND ref='PUB-STEP14' AND metric='likes'"
    ).fetchone()
    assert missing["value_json"] is None
    assert missing["availability"] == "not_present"


def test_empty_official_order_csv_is_zero_not_missing(
    metrics_case: MetricsCase,
) -> None:
    _commit(metrics_case, _result(orders=()))
    summary = summarize_metrics(metrics_case.connection)
    assert summary.order_count.known_total == 0
    assert summary.order_count.missing_count == 0
    assert summary.orders_current_sales_yen == 0


def test_unreported_post_time_stays_unknown_when_room_item_is_missing(
    metrics_case: MetricsCase,
) -> None:
    metrics_case.connection.execute(
        "UPDATE publication_event SET actual_posted_at=NULL WHERE event_id='PUB-STEP14'"
    )
    metrics_case.connection.commit()
    _commit(metrics_case, _result(room_posts=()))
    missing = metrics_case.connection.execute(
        "SELECT value_json, availability, window FROM metric_observation "
        "WHERE scope='room_post' AND ref='PUB-STEP14' AND metric='likes'"
    ).fetchone()
    assert missing["value_json"] is None
    assert missing["availability"] == "unknown"
    assert json.loads(missing["window"])["post_age_seconds"] is None


def test_source_order_and_deadline_budget_are_shared_across_affiliate_then_room() -> (
    None
):
    clock = FakeClock(datetime.fromisoformat(NOW))
    deadline = datetime.fromisoformat("2026-10-12T18:00:00+09:00")
    calls: list[tuple[str, float]] = []

    class Affiliate:
        def collect(self, **kwargs: Any) -> MetricsSourceResult:
            calls.append(("affiliate", kwargs["remaining_seconds"]))
            clock.advance(45)
            raise MetricsAuthenticationRequired()

    class Room:
        def collect(self, **kwargs: Any) -> MetricsSourceResult:
            calls.append(("room", kwargs["remaining_seconds"]))
            return MetricsSourceResult(
                snapshot=MetricsSnapshot(
                    source_key="room",
                    source="room",
                    status="observed",
                    observed_at=clock.now_jst().isoformat(),
                    payload={"fixture": True},
                )
            )

    result = collect_metrics_in_order(
        Affiliate(),
        Room(),
        run_id=RUN_ID,
        target_date="2026-10-12",
        deadline_at=deadline,
        clock=clock,
    )
    assert [source for source, _ in calls] == ["affiliate", "room"]
    assert calls[0][1] == 4 * 60 * 60 + 40 * 60
    assert calls[1][1] == calls[0][1] - 45
    assert result.status == "partial"
    assert result.snapshots[0].payload == {"error_code": "authentication_required"}


def test_deadline_exhaustion_does_not_start_sources() -> None:
    clock = FakeClock(datetime.fromisoformat("2026-10-12T18:00:00+09:00"))
    calls: list[str] = []

    class NeverCalled:
        def collect(self, **kwargs: Any) -> MetricsSourceResult:
            calls.append("called")
            raise AssertionError("source must not run after deadline")

    result = collect_metrics_in_order(
        NeverCalled(),
        NeverCalled(),
        run_id=RUN_ID,
        target_date="2026-10-12",
        deadline_at=datetime.fromisoformat("2026-10-12T18:00:00+09:00"),
        clock=clock,
    )
    assert calls == []
    assert result.status == "unavailable"
    assert [snapshot.payload["error_code"] for snapshot in result.snapshots] == [
        "shared_deadline_exhausted",
        "shared_deadline_exhausted",
    ]


def test_metrics_commit_rejects_after_deadline_without_db_side_effects(
    metrics_case: MetricsCase,
) -> None:
    metrics_case.clock.advance(5 * 60 * 60)
    before = metrics_case.connection.execute(
        "SELECT COUNT(*) FROM artifact"
    ).fetchone()[0]
    with pytest.raises(ValueError, match="deadline"):
        _commit(metrics_case, _result())
    assert (
        metrics_case.connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
        == before
    )
    assert (
        metrics_case.connection.execute(
            "SELECT COUNT(*) FROM metric_observation"
        ).fetchone()[0]
        == 0
    )


def test_structured_result_and_daily_status_commit_together_on_main_connection(
    metrics_case: MetricsCase,
) -> None:
    class StructuredPort:
        def collect(
            self,
            connection: sqlite3.Connection,
            *,
            run_id: str,
            deadline_at: str,
        ) -> MetricsCollectionResult:
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
            assert (
                connection.execute(
                    "SELECT target_date FROM run WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                == "2026-10-12"
            )
            with pytest.raises(sqlite3.OperationalError):
                connection.execute(
                    "UPDATE run SET metrics_status='observed' WHERE run_id=?", (run_id,)
                )
            return _result()

    _, state = _daily_state(None)
    ports = SimpleNamespace(
        metrics_port=StructuredPort(),
        cancel_metrics_job=lambda run_id: True,
    )
    status = _collect_metrics(
        metrics_case.connection,
        run_id=RUN_ID,
        target_date="2026-10-12",
        state=state,
        ports=ports,
        clock=metrics_case.clock,
        data_root=metrics_case.data_root,
    )
    stored_status = metrics_case.connection.execute(
        "SELECT metrics_status, checkpoint FROM run WHERE run_id=?", (RUN_ID,)
    ).fetchone()
    checkpoint_state = json.loads(stored_status["checkpoint"])["daily_orchestration"]
    assert status == "observed"
    assert stored_status["metrics_status"] == "observed"
    assert checkpoint_state["metrics_status"] == "observed"
    assert (
        metrics_case.connection.execute(
            "SELECT COUNT(*) FROM metric_observation"
        ).fetchone()[0]
        > 0
    )


def test_metrics_commit_failure_rolls_back_rows_but_preserves_source_artifacts(
    metrics_case: MetricsCase,
) -> None:
    with pytest.raises(RuntimeError, match="fixture failure"):
        commit_metrics_result(
            metrics_case.connection,
            data_root=metrics_case.data_root,
            result=_result(),
            clock=metrics_case.clock,
            deadline_at=datetime.fromisoformat("2026-10-12T18:00:00+09:00"),
            before_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("fixture failure")
            ),
        )
    assert (
        metrics_case.connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
        == 0
    )
    assert (
        metrics_case.connection.execute(
            "SELECT COUNT(*) FROM metric_observation"
        ).fetchone()[0]
        == 0
    )
    assert list((metrics_case.data_root / "artifacts" / "metrics").glob("*.json"))


def test_affiliate_adapter_uses_configured_window_and_preserves_null_vs_zero() -> None:
    node_script = r"""
      import { previousDailyDates, orderMonths, normalizeDailyResponse } from './automation/inago/adapters/affiliate.mjs';
      const dates = previousDailyDates('2026-10-13', '2026-09-01');
      const months = orderMonths('2026-10-13', '2026-09-01');
      const data = normalizeDailyResponse({
        status: 'observed', observedAt: '2026-10-13T13:20:00+09:00',
        expectedDates: dates, rows: [{ date: '2026-10-12', clicks: 0, salesCount: null,
          salesYen: 0, commissionUnconfirmedYen: null }], emptyMeansZero: false,
      });
      process.stdout.write(JSON.stringify({ dates, months, data }));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)
    assert payload["dates"][0] == "2026-10-12"
    assert payload["dates"][-1] == "2026-10-06"
    assert payload["months"] == ["2026-10", "2026-09"]
    row = payload["data"]["rows"][0]
    assert row["clicks"] == 0
    assert row["salesCount"] is None
    assert row["salesYen"] == 0
    assert row["commissionUnconfirmedYen"] is None


def test_room_adapter_requires_observed_post_and_keeps_read_failure_null() -> None:
    node_script = r"""
      import { normalizeRoomMetricsResponse, roomWindowStart } from './automation/inago/adapters/room_metrics.mjs';
      const data = normalizeRoomMetricsResponse({
        status: 'observed', observedAt: '2026-10-13T13:20:00+09:00',
        roomUrl: 'https://room.rakuten.co.jp/room_demo',
        posts: [{ postId: 'POST-1', roomPostUrl: 'https://room.rakuten.co.jp/room_demo/items/POST-1',
          itemUrl: 'https://item.rakuten.co.jp/shop_a/item_1/', likes: 0, createdAt: null }],
      });
      process.stdout.write(JSON.stringify({ data, start: roomWindowStart('2026-10-13T13:20:00+09:00') }));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)
    assert payload["start"] == "2026-09-13T13:20:00+09:00"
    assert payload["data"]["posts"][0]["likes"] == 0
    assert payload["data"]["posts"][0]["createdAt"] is None


def test_affiliate_order_csv_keeps_null_commission_and_temporary_reference() -> None:
    node_script = r"""
      import { normalizeOrderCsv } from './automation/inago/adapters/affiliate.mjs';
      const csv = 'date,rewards,amount,shop_name,item_name,status,link_type,item_code\n'
        + '2026-10-11 10:00,0,1000,店舗A,"架空, 試験商品",0 - 未確定,1 - 商品リンク,shop_a:item_1\n';
      const result = normalizeOrderCsv(csv, '2026-10');
      process.stdout.write(JSON.stringify(result));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "observed"
    assert len(payload["orders"]) == 1
    assert payload["orders"][0]["orderRef"] is None
    assert payload["orders"][0]["isTemporaryRef"] is True
    assert payload["orders"][0]["commissionYen"] == 0
    assert payload["orders"][0]["salesYen"] == 1000
    assert payload["orders"][0]["productName"] == "架空, 試験商品"
    assert payload["orders"][0]["occurredAt"] == "2026-10-11T10:00:00+09:00"
