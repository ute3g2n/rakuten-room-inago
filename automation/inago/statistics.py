"""Conservation checks for observed posts, product representatives, and outcomes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import sqlite3
from typing import Sequence

from automation.inago.contracts import RejectedContract
from automation.inago.selection import (
    MAX_POSTS_PER_SEARCH,
    PRIMARY_REASON_ORDER,
    SelectionResult,
)
from automation.inago.metrics import MetricTotal, MetricsSummary


DAILY_TARGET = 15
_SOURCE_STATUSES = frozenset({"observed", "partial", "unavailable", "invalid"})


@dataclass(frozen=True, slots=True)
class SearchAttempt:
    """One counted search attempt, including successful zero and failed attempts."""

    attempt_no: int
    source_status: str
    selection: SelectionResult


@dataclass(frozen=True, slots=True)
class DailyStatistics:
    seed: str | None
    search_attempts: int
    search_status_counts: dict[str, int]
    observed_posts: int
    representative_count: int
    distinct_offer_count: int
    committed_count: int
    posted_count: int
    planned_count: int
    shortfall_count: int
    primary_reason_counts: dict[str, int]


def summarize_daily(
    attempts: Sequence[SearchAttempt], *, committed_count: int, posted_count: int
) -> DailyStatistics:
    """Keep raw observations, per-search representatives, daily union, D, and P separate."""

    if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)):
        raise RejectedContract("search attempts must be a sequence")
    attempt_numbers = []
    seeds: set[str] = set()
    observed_posts = 0
    representative_count = 0
    distinct_offer_keys: set[str] = set()
    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for attempt in attempts:
        if not isinstance(attempt, SearchAttempt):
            raise RejectedContract("daily statistics contain an unsupported attempt")
        if (
            isinstance(attempt.attempt_no, bool)
            or not isinstance(attempt.attempt_no, int)
            or attempt.attempt_no < 1
        ):
            raise RejectedContract("search attempt number must be a positive integer")
        attempt_numbers.append(attempt.attempt_no)
        if (
            not isinstance(attempt.source_status, str)
            or attempt.source_status not in _SOURCE_STATUSES
        ):
            raise RejectedContract("search attempt status is unsupported")
        result = attempt.selection
        if not isinstance(result, SelectionResult):
            raise RejectedContract("search attempt has no selection result")
        if not isinstance(result.seed, str) or not result.seed.strip():
            raise RejectedContract("selection result has no saved seed")
        seeds.add(result.seed)
        if not 0 <= result.observed_posts <= MAX_POSTS_PER_SEARCH:
            raise RejectedContract("one search attempt may contain at most 50 posts")
        if len(result.decisions) != result.observed_posts:
            raise RejectedContract("every observed post must have exactly one decision")
        per_attempt_reasons = Counter(
            decision.primary_reason for decision in result.decisions
        )
        if per_attempt_reasons.keys() - set(PRIMARY_REASON_ORDER):
            raise RejectedContract("primary reason is outside the registered order")
        if sum(per_attempt_reasons.values()) != result.observed_posts:
            raise RejectedContract("primary reasons do not conserve observed posts")
        if len(result.representatives) > result.observed_posts:
            raise RejectedContract("representatives cannot exceed observed posts")
        if len(result.eligible_representatives) > len(result.representatives):
            raise RejectedContract(
                "eligible representatives exceed all representatives"
            )
        representative_keys = [item.offer_key for item in result.representatives]
        if len(set(representative_keys)) != len(representative_keys):
            raise RejectedContract(
                "one search cannot have two representatives per offer"
            )
        if not {item.offer_key for item in result.eligible_representatives}.issubset(
            set(representative_keys)
        ):
            raise RejectedContract("eligible candidates must be search representatives")
        if any(
            not isinstance(decision.primary_reason, str)
            or not decision.primary_reason.strip()
            for decision in result.decisions
        ):
            raise RejectedContract("every observed post requires one primary reason")
        for representative in result.representatives:
            if not representative.offer_key:
                raise RejectedContract(
                    "a representative must have a resolved offer key"
                )
            distinct_offer_keys.add(representative.offer_key)
        observed_posts += result.observed_posts
        representative_count += len(result.representatives)
        reason_counts.update(per_attempt_reasons)
        status_counts.update((attempt.source_status,))

    if len(set(attempt_numbers)) != len(attempt_numbers):
        raise RejectedContract("search attempt numbers cannot repeat")
    if sorted(attempt_numbers) != list(range(1, len(attempt_numbers) + 1)):
        raise RejectedContract("search attempts must preserve the saved 1..N sequence")
    if len(seeds) > 1:
        raise RejectedContract("daily selection results must retain one saved run seed")
    if observed_posts > MAX_POSTS_PER_SEARCH * len(attempts):
        raise RejectedContract("observed posts exceed 50 times the counted attempts")
    if (
        isinstance(committed_count, bool)
        or not isinstance(committed_count, int)
        or not 0 <= committed_count <= DAILY_TARGET
    ):
        raise RejectedContract("committed_count must be between 0 and the 15 target")
    if (
        isinstance(posted_count, bool)
        or not isinstance(posted_count, int)
        or posted_count < 0
    ):
        raise RejectedContract("posted_count must be a separate non-negative count")

    return DailyStatistics(
        seed=next(iter(seeds)) if seeds else None,
        search_attempts=len(attempts),
        search_status_counts=dict(status_counts),
        observed_posts=observed_posts,
        representative_count=representative_count,
        distinct_offer_count=len(distinct_offer_keys),
        committed_count=committed_count,
        posted_count=posted_count,
        planned_count=DAILY_TARGET,
        shortfall_count=DAILY_TARGET - committed_count,
        primary_reason_counts={
            reason: reason_counts[reason]
            for reason in PRIMARY_REASON_ORDER
            if reason_counts[reason]
        },
    )


def _metric_totals(rows: Sequence[sqlite3.Row]) -> MetricTotal:
    known_values: list[float] = []
    for row in rows:
        if row["availability"] != "observed" or row["value_json"] is None:
            continue
        value = json.loads(row["value_json"])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        known_values.append(float(value))
    return MetricTotal(
        known_total=sum(known_values) if known_values else None,
        observed_count=len(known_values),
        missing_count=len(rows) - len(known_values),
    )


def summarize_metrics(connection: sqlite3.Connection) -> MetricsSummary:
    """Summarize the latest values in each scope without cross-scope allocation."""

    daily_rows = connection.execute(
        """
        SELECT ref, metric, value_json, observed_at, window, availability, observation_id
          FROM metric_observation
         WHERE scope='day_aggregate'
         ORDER BY observed_at DESC, observation_id DESC
        """
    ).fetchall()
    latest_daily: dict[tuple[str, str], sqlite3.Row] = {}
    for row in daily_rows:
        key = (str(row["ref"]), str(row["metric"]))
        latest_daily.setdefault(key, row)
    daily_by_metric: dict[str, list[sqlite3.Row]] = {}
    for row in latest_daily.values():
        daily_by_metric.setdefault(str(row["metric"]), []).append(row)
    affiliate = {
        metric: _metric_totals(values) for metric, values in daily_by_metric.items()
    }

    orders = connection.execute(
        "SELECT is_temporary_ref, sales_yen, commission_yen FROM revenue_order ORDER BY order_ref"
    ).fetchall()
    stable_orders = [row for row in orders if not row["is_temporary_ref"]]
    order_sales = [row["sales_yen"] for row in stable_orders]
    order_commission = [row["commission_yen"] for row in stable_orders]

    order_window_rows = connection.execute(
        """
        SELECT ref, value_json, observed_at, window, availability, observation_id
          FROM metric_observation
         WHERE scope='order_item' AND metric='sales_count' AND ref LIKE 'order-window:%'
         ORDER BY observed_at DESC, observation_id DESC
        """
    ).fetchall()
    latest_order_windows: dict[str, sqlite3.Row] = {}
    for row in order_window_rows:
        latest_order_windows.setdefault(str(row["ref"]), row)
    order_window_values = list(latest_order_windows.values())
    order_windows = {
        str(row["ref"]): _metric_totals([row]) for row in order_window_values
    }
    order_count = (
        _metric_totals(order_window_values)
        if order_window_values
        else MetricTotal(None, 0, 1)
    )

    room_rows = connection.execute(
        """
        SELECT ref, value_json, observed_at, availability, observation_id
          FROM metric_observation
         WHERE scope='room_post' AND metric='likes'
         ORDER BY observed_at DESC, observation_id DESC
        """
    ).fetchall()
    latest_room: dict[str, sqlite3.Row] = {}
    for row in room_rows:
        latest_room.setdefault(str(row["ref"]), row)
    room_values = list(latest_room.values())
    orders_complete_empty = (
        not orders and order_count.known_total == 0 and order_count.missing_count == 0
    )
    return MetricsSummary(
        affiliate_daily=affiliate,
        order_count=order_count,
        order_windows=order_windows,
        order_unresolved_count=sum(1 for row in orders if row["is_temporary_ref"]),
        orders_current_sales_yen=(
            sum(float(value) for value in order_sales)
            if stable_orders
            and not any(row["is_temporary_ref"] for row in orders)
            and all(value is not None for value in order_sales)
            else 0.0
            if orders_complete_empty
            else None
        ),
        orders_current_commission_unconfirmed_yen=(
            sum(float(value) for value in order_commission)
            if stable_orders
            and not any(row["is_temporary_ref"] for row in orders)
            and all(value is not None for value in order_commission)
            else 0.0
            if orders_complete_empty
            else None
        ),
        room_post_count=sum(
            1 for row in room_values if row["availability"] == "observed"
        ),
        room_missing_count=sum(
            1 for row in room_values if row["availability"] != "observed"
        ),
        room_likes=_metric_totals(room_values),
    )
