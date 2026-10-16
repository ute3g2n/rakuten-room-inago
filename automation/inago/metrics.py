"""Normalize and persist scoped affiliate and ROOM measurements.

External calls run through read-only ports.  This module returns immutable
result envelopes and performs any SQLite writes only on the caller's writable
connection after the external call has completed.
"""

from __future__ import annotations

import json
import math
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from automation.inago.artifact_io import write_temp_response
from automation.inago.clock import Clock
from automation.inago.contracts import PersistenceContractError, RejectedContract


JST = ZoneInfo("Asia/Tokyo")
_METRIC_NAMES = frozenset(
    {"clicks", "likes", "sales_count", "sales_yen", "commission_unconfirmed_yen"}
)
_SCOPES = frozenset({"day_aggregate", "room_post", "order_item", "entry_post"})
_AVAILABILITY = frozenset(
    {"observed", "unknown", "unavailable", "not_present", "unmatched", "expired"}
)
_SOURCE_STATUSES = frozenset({"observed", "partial", "unavailable", "not_present"})
_UNITS = frozenset({"count", "JPY", "seconds", "bytes", "ratio", "text"})
_SECRET_KEY_PARTS = (
    "cookie",
    "authorization",
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "sessionid",
    "email",
    "address",
    "phone",
    "recipient",
    "customer",
    "session",
    "api_key",
    "csrf",
    "access_key",
    "refresh_token",
)


class MetricsDeadlineExceeded(PersistenceContractError):
    """Metrics data reached storage after its individual request deadline."""


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """One normalized source response, saved privately as a 90-day artifact."""

    source_key: str
    source: Literal["affiliate", "room"]
    status: Literal["observed", "partial", "unavailable", "not_present"]
    observed_at: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DailyMetricObservation:
    """One metric for one affiliate calendar-day aggregate."""

    source_key: str
    ref: str
    metric: str
    value: int | float | None
    unit: str
    observed_at: str
    window: str | None
    availability: str
    provisional: bool | None


@dataclass(frozen=True, slots=True)
class OrderItemObservation:
    """One latest affiliate order item response; past values stay in history."""

    source_key: str
    order_ref: str | None
    is_temporary_ref: bool
    occurred_at: str | None
    shop_name: str | None
    product_name: str | None
    item_code: str | None
    product_url: str | None
    sales_yen: int | float | None
    commission_yen: int | float | None
    order_status: str | None
    link_type: str | None
    window: str
    observed_at: str
    entry_post_ref: str | None = None


@dataclass(frozen=True, slots=True)
class OrderWindowObservation:
    """Whether one monthly official CSV was observed, including true zero rows."""

    source_key: str
    window: str
    count: int | None
    observed_at: str
    availability: str


@dataclass(frozen=True, slots=True)
class RoomPostObservation:
    """One public ROOM response row before it is matched to a posted event."""

    source_key: str
    post_id: str
    room_post_url: str | None
    item_url: str | None
    likes: int | None
    observed_at: str
    window: str
    post_created_at: str | None


@dataclass(frozen=True, slots=True)
class MetricsCollectionResult:
    """Data-only result returned by the external metrics worker."""

    run_id: str
    status: Literal["observed", "partial", "unavailable", "not_started"]
    observed_at: str
    snapshots: tuple[MetricsSnapshot, ...]
    daily_observations: tuple[DailyMetricObservation, ...] = ()
    orders: tuple[OrderItemObservation, ...] = ()
    order_windows: tuple[OrderWindowObservation, ...] = ()
    room_posts: tuple[RoomPostObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class MetricsSourceResult:
    """Normalized output from exactly one affiliate or ROOM source port."""

    snapshot: MetricsSnapshot
    daily_observations: tuple[DailyMetricObservation, ...] = ()
    orders: tuple[OrderItemObservation, ...] = ()
    order_windows: tuple[OrderWindowObservation, ...] = ()
    room_posts: tuple[RoomPostObservation, ...] = ()


class MetricsSourcePort(Protocol):
    def collect(
        self,
        *,
        run_id: str,
        target_date: str,
        deadline_at: str,
        remaining_seconds: float,
    ) -> MetricsSourceResult:
        """Return normalized data only; use the passed shared remaining time."""


class MetricsAuthenticationRequired(RuntimeError):
    """A source requires the owner to restore their existing authentication."""


@dataclass(frozen=True, slots=True)
class MetricsCommitOutcome:
    status: str
    artifact_refs: tuple[str, ...]
    observation_count: int
    order_count: int


@dataclass(frozen=True, slots=True)
class MetricTotal:
    """Known subtotal and missing-value count for one named scope/metric."""

    known_total: float | None
    observed_count: int
    missing_count: int


@dataclass(frozen=True, slots=True)
class MetricsSummary:
    """Display-ready totals that keep daily, order, and ROOM scopes separate."""

    affiliate_daily: Mapping[str, MetricTotal]
    order_count: MetricTotal
    order_windows: Mapping[str, MetricTotal]
    order_unresolved_count: int
    orders_current_sales_yen: float | None
    orders_current_commission_unconfirmed_yen: float | None
    room_post_count: int
    room_missing_count: int
    room_likes: MetricTotal


@dataclass(frozen=True, slots=True)
class OptionalInsightResponse:
    status: Literal["observed", "unavailable"]
    payload: Mapping[str, Any] | None = None
    error_code: str | None = None


class OptionalInsightPort(Protocol):
    def explain(self, summary: Mapping[str, Any]) -> Mapping[str, Any]:
        """Explain already-collected facts without changing source values."""


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RejectedContract(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RejectedContract(f"{field} must include a timezone offset")
    return parsed.astimezone(JST)


def _require_text(value: Any, field: str, *, max_length: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > max_length
    ):
        raise RejectedContract(f"{field} must be non-empty normalized text")
    return value


def _non_negative_number(value: Any, field: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RejectedContract(f"{field} must be a non-negative number or null")
    if not math.isfinite(float(value)) or value < 0:
        raise RejectedContract(f"{field} must be a finite non-negative number")
    return value


def _check_safe_payload(value: Any, field_path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise RejectedContract(
                    f"{field_path} contains a prohibited private field"
                )
            _check_safe_payload(item, f"{field_path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_safe_payload(item, f"{field_path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise RejectedContract(f"{field_path} contains a non-finite number")


def _canonical_https_url(value: str | None) -> str | None:
    if value is None:
        return None
    _require_text(value, "URL", max_length=2048)
    parts = urlsplit(value)
    if parts.scheme.casefold() != "https" or not parts.hostname:
        raise RejectedContract("metrics URLs must use an explicit HTTPS host")
    if parts.username or parts.password:
        raise RejectedContract("metrics URLs cannot contain user information")
    host = parts.hostname.casefold()
    port = f":{parts.port}" if parts.port and parts.port != 443 else ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", f"{host}{port}", path, parts.query, ""))


def _canonical_product_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _validate_result(
    result: MetricsCollectionResult, run_id: str
) -> dict[str, MetricsSnapshot]:
    if not isinstance(result, MetricsCollectionResult):
        raise RejectedContract("metrics worker must return a MetricsCollectionResult")
    if result.run_id != run_id:
        raise RejectedContract(
            "metrics result run_id does not match the active daily run"
        )
    if result.status not in {"observed", "partial", "unavailable", "not_started"}:
        raise RejectedContract("metrics result status is unsupported")
    _timestamp(result.observed_at, "metrics observed_at")
    if not isinstance(result.snapshots, tuple):
        raise RejectedContract("metrics snapshots must be an immutable tuple")
    if any(
        not isinstance(values, tuple)
        for values in (
            result.daily_observations,
            result.orders,
            result.order_windows,
            result.room_posts,
        )
    ):
        raise RejectedContract("metrics records must be immutable tuples")
    snapshots: dict[str, MetricsSnapshot] = {}
    for snapshot in result.snapshots:
        if not isinstance(snapshot, MetricsSnapshot):
            raise RejectedContract("metrics result contains an unsupported snapshot")
        key = _require_text(snapshot.source_key, "snapshot source_key", max_length=96)
        if key in snapshots:
            raise RejectedContract("metrics snapshot source_key cannot repeat")
        if snapshot.source not in {"affiliate", "room"}:
            raise RejectedContract("metrics snapshot source is unsupported")
        if snapshot.status not in _SOURCE_STATUSES:
            raise RejectedContract("metrics snapshot status is unsupported")
        _timestamp(snapshot.observed_at, "snapshot observed_at")
        if not isinstance(snapshot.payload, Mapping):
            raise RejectedContract("metrics snapshot payload must be an object")
        _check_safe_payload(snapshot.payload)
        snapshots[key] = snapshot
    if result.status != "not_started" and not snapshots:
        raise RejectedContract("started metrics collection must preserve source status")
    if result.status != "not_started" and not {"affiliate", "room"}.issubset(
        {snapshot.source for snapshot in snapshots.values()}
    ):
        raise RejectedContract(
            "started metrics collection must report affiliate and ROOM status"
        )
    if result.status == "observed" and any(
        snapshot.status != "observed" for snapshot in snapshots.values()
    ):
        raise RejectedContract(
            "observed metrics status requires every source to be observed"
        )
    if result.status == "unavailable" and any(
        snapshot.status in {"observed", "partial"} for snapshot in snapshots.values()
    ):
        raise RejectedContract(
            "unavailable metrics status cannot include acquired data"
        )
    return snapshots


def collect_metrics_in_order(
    affiliate_port: MetricsSourcePort,
    room_port: MetricsSourcePort,
    *,
    run_id: str,
    target_date: str,
    deadline_at: datetime,
    clock: Clock,
) -> MetricsCollectionResult:
    """Call affiliate first, then ROOM, sharing one remaining-time deadline."""

    if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
        raise RejectedContract("metrics deadline must include a timezone offset")
    deadline = deadline_at.astimezone(JST)
    _timestamp(f"{target_date}T00:00:00+09:00", "target_date")
    if len(target_date) != 10 or target_date != target_date[:10]:
        raise RejectedContract("target_date must be YYYY-MM-DD")
    snapshots: list[MetricsSnapshot] = []
    daily: list[DailyMetricObservation] = []
    orders: list[OrderItemObservation] = []
    order_windows: list[OrderWindowObservation] = []
    room_posts: list[RoomPostObservation] = []
    ports: tuple[tuple[str, MetricsSourcePort], ...] = (
        ("affiliate", affiliate_port),
        ("room", room_port),
    )
    for source, port in ports:
        now = clock.now_jst().astimezone(JST)
        remaining = max(0.0, (deadline - now).total_seconds())
        if remaining <= 0:
            snapshots.append(
                MetricsSnapshot(
                    source_key=source,
                    source=source,
                    status="unavailable",
                    observed_at=now.isoformat(),
                    payload={"error_code": "shared_deadline_exhausted"},
                )
            )
            continue
        try:
            response = port.collect(
                run_id=run_id,
                target_date=target_date,
                deadline_at=deadline.isoformat(),
                remaining_seconds=remaining,
            )
            if not isinstance(response, MetricsSourceResult):
                raise RejectedContract("metrics source returned an unsupported result")
            if response.snapshot.source != source:
                raise RejectedContract(
                    "metrics source response was assigned to another source"
                )
            if response.snapshot.source_key != source:
                raise RejectedContract("metrics source_key must identify its source")
            snapshots.append(response.snapshot)
            daily.extend(response.daily_observations)
            orders.extend(response.orders)
            order_windows.extend(response.order_windows)
            room_posts.extend(response.room_posts)
        except MetricsAuthenticationRequired:
            snapshots.append(
                MetricsSnapshot(
                    source_key=source,
                    source=source,
                    status="unavailable",
                    observed_at=clock.now_jst().astimezone(JST).isoformat(),
                    payload={"error_code": "authentication_required"},
                )
            )
        except Exception as exc:
            snapshots.append(
                MetricsSnapshot(
                    source_key=source,
                    source=source,
                    status="unavailable",
                    observed_at=clock.now_jst().astimezone(JST).isoformat(),
                    payload={"error_code": type(exc).__name__},
                )
            )
    successful = [snapshot for snapshot in snapshots if snapshot.status == "observed"]
    if len(successful) == len(snapshots):
        status: Literal["observed", "partial", "unavailable"] = "observed"
    elif successful or any(snapshot.status == "partial" for snapshot in snapshots):
        status = "partial"
    else:
        status = "unavailable"
    return MetricsCollectionResult(
        run_id=run_id,
        status=status,
        observed_at=clock.now_jst().astimezone(JST).isoformat(),
        snapshots=tuple(snapshots),
        daily_observations=tuple(daily),
        orders=tuple(orders),
        order_windows=tuple(order_windows),
        room_posts=tuple(room_posts),
    )


def _artifact_ref() -> str:
    return f"MTR-{uuid.uuid4().hex}"


def _observation_id() -> str:
    return f"MOBS-{uuid.uuid4().hex}"


def _insert_metric_observation(
    connection: sqlite3.Connection,
    *,
    scope: str,
    ref: str,
    metric: str,
    value: int | float | None,
    unit: str,
    observed_at: str,
    window: str | None,
    availability: str,
    source_ref: str,
    provisional: bool | None,
) -> None:
    _require_text(ref, "metric ref", max_length=256)
    _require_text(source_ref, "metric source_ref", max_length=128)
    _require_text(observed_at, "metric observed_at", max_length=80)
    _timestamp(observed_at, "metric observed_at")
    if scope not in _SCOPES or metric not in _METRIC_NAMES or unit not in _UNITS:
        raise RejectedContract("metric scope, name, or unit is unsupported")
    if availability not in _AVAILABILITY:
        raise RejectedContract("metric availability is unsupported")
    numeric = _non_negative_number(value, "metric value")
    if availability == "observed" and numeric is None:
        raise RejectedContract("an observed metric requires a numeric value")
    if availability != "observed" and numeric is not None:
        raise RejectedContract("a non-observed metric must keep a null value")
    if window is not None and not isinstance(window, str):
        raise RejectedContract("metric window must be a string or null")
    if provisional is not None and not isinstance(provisional, bool):
        raise RejectedContract("metric provisional must be boolean or null")
    if metric == "commission_unconfirmed_yen" and provisional is not True:
        raise RejectedContract("reported commission must remain explicitly unconfirmed")
    if (
        scope == "day_aggregate"
        and metric in {"sales_count", "sales_yen"}
        and provisional is not True
    ):
        raise RejectedContract("daily sales values must remain provisional")
    encoded = None if numeric is None else json.dumps(numeric, allow_nan=False)
    connection.execute(
        """
        INSERT INTO metric_observation
            (observation_id, scope, ref, metric, value_json, unit,
             observed_at, window, availability, source_ref, provisional)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _observation_id(),
            scope,
            ref,
            metric,
            encoded,
            unit,
            _timestamp(observed_at, "metric observed_at").isoformat(),
            window,
            availability,
            source_ref,
            None if provisional is None else int(provisional),
        ),
    )


def _same_order_identity(row: sqlite3.Row, item: OrderItemObservation) -> bool:
    if item.occurred_at is None or row["occurred_at"] != item.occurred_at:
        return False
    if item.shop_name is not None and row["shop_name"] != item.shop_name:
        return False
    identifiers = []
    if item.item_code is not None:
        identifiers.append(row["item_code"] == item.item_code)
    if item.product_url is not None:
        canonical = _canonical_https_url(item.product_url)
        identifiers.append(_canonical_https_url(row["product_url"]) == canonical)
    if not identifiers or not any(identifiers):
        return False
    if item.product_name is not None and row["product_name"] != item.product_name:
        return False
    return True


def _offer_candidates(
    connection: sqlite3.Connection, item: OrderItemObservation
) -> set[str]:
    candidates: set[str] | None = None
    if item.item_code:
        rows = connection.execute(
            "SELECT offer_key FROM offer WHERE offer_key=? OR item_id=?",
            (item.item_code, item.item_code),
        ).fetchall()
        if rows:
            candidates = {str(row["offer_key"]) for row in rows}
    if item.product_url:
        canonical = _canonical_https_url(item.product_url)
        rows = connection.execute("SELECT offer_key, url FROM offer").fetchall()
        url_matches = {
            str(row["offer_key"])
            for row in rows
            if _canonical_https_url(str(row["url"])) == canonical
        }
        if candidates is None:
            candidates = url_matches
        elif url_matches:
            candidates &= url_matches
        elif candidates:
            return set()
    if item.product_name:
        normalized_name = _canonical_product_name(item.product_name)
        name_matches = {
            str(row["offer_key"])
            for row in connection.execute(
                "SELECT offer_key, name FROM offer"
            ).fetchall()
            if _canonical_product_name(str(row["name"])) == normalized_name
        }
        if candidates is None:
            candidates = name_matches
        elif name_matches:
            candidates &= name_matches
    return candidates or set()


def _resolve_order_match(
    connection: sqlite3.Connection, item: OrderItemObservation
) -> tuple[str, str | None, str | None]:
    candidates = _offer_candidates(connection, item)
    offer_key = next(iter(candidates)) if len(candidates) == 1 else None
    if item.entry_post_ref is not None:
        _require_text(item.entry_post_ref, "entry_post_ref", max_length=128)
        event = connection.execute(
            """
            SELECT event_id, offer_key FROM publication_event
             WHERE event_id=? AND valid=1
            """,
            (item.entry_post_ref,),
        ).fetchone()
        if event is not None and offer_key == str(event["offer_key"]):
            return "entry_post_confirmed", offer_key, str(event["event_id"])
    if offer_key is not None:
        return "purchased_item_match", offer_key, None
    return "unmatched", None, None


def _resolve_order_ref(
    connection: sqlite3.Connection,
    item: OrderItemObservation,
    *,
    batch_identities: Mapping[tuple[Any, ...], int],
) -> tuple[str, bool, str | None]:
    if not isinstance(item.is_temporary_ref, bool):
        raise RejectedContract("order temporary-reference flag must be boolean")
    if item.is_temporary_ref and item.order_ref is not None:
        raise RejectedContract("temporary order references must not claim a stable ID")
    if not item.is_temporary_ref and item.order_ref is None:
        raise RejectedContract("stable order reference is required when marked stable")
    incoming_ref = (
        _require_text(item.order_ref, "order_ref", max_length=128)
        if item.order_ref is not None
        else None
    )
    rows = connection.execute(
        "SELECT * FROM revenue_order WHERE occurred_at IS NOT NULL"
    ).fetchall()
    matches = [row for row in rows if _same_order_identity(row, item)]
    item_key = (
        item.occurred_at,
        item.shop_name,
        item.item_code,
        _canonical_https_url(item.product_url),
        item.product_name,
    )
    unambiguous_batch_row = batch_identities.get(item_key, 0) == 1
    if incoming_ref is None:
        if (
            len(matches) == 1
            and matches[0]["is_temporary_ref"]
            and unambiguous_batch_row
        ):
            return str(matches[0]["order_ref"]), True, None
        return f"TMP-{uuid.uuid4().hex}", True, None
    if len(matches) == 1 and matches[0]["is_temporary_ref"] and unambiguous_batch_row:
        old_ref = str(matches[0]["order_ref"])
        existing_stable = connection.execute(
            "SELECT 1 FROM revenue_order WHERE order_ref=?", (incoming_ref,)
        ).fetchone()
        if existing_stable is None:
            return incoming_ref, False, old_ref
    return incoming_ref, False, None


def _date_for_age(value: str | None, observed_at: str) -> int | None:
    if value is None:
        return None
    seconds = (
        _timestamp(observed_at, "ROOM observed_at")
        - _timestamp(value, "ROOM created_at")
    ).total_seconds()
    if seconds < 0:
        return None
    return int(seconds)


def _room_window(window: str, age_seconds: int | None) -> str:
    return json.dumps(
        {"period": window, "post_age_seconds": age_seconds},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _store_room_posts(
    connection: sqlite3.Connection,
    *,
    result: MetricsCollectionResult,
    snapshot_refs: Mapping[str, str],
) -> int:
    room_snapshots = {s.source_key: s for s in result.snapshots if s.source == "room"}
    matched_event_ids: set[str] = set()
    count = 0
    for post in result.room_posts:
        if post.source_key not in room_snapshots:
            raise RejectedContract(
                "ROOM post does not reference a ROOM source snapshot"
            )
        _require_text(post.post_id, "ROOM post_id", max_length=128)
        _timestamp(post.observed_at, "ROOM observed_at")
        if post.likes is not None and (
            isinstance(post.likes, bool)
            or not isinstance(post.likes, int)
            or post.likes < 0
        ):
            raise RejectedContract("ROOM likes must be a non-negative integer or null")
        post_url = _canonical_https_url(post.room_post_url)
        _canonical_https_url(post.item_url)
        events: list[sqlite3.Row] = []
        if post_url:
            candidates = connection.execute(
                "SELECT event_id, actual_posted_at FROM publication_event "
                "WHERE valid=1 AND room_url=?",
                (post_url,),
            ).fetchall()
            post_observed = _timestamp(post.observed_at, "ROOM observed_at")
            window_start = post_observed - timedelta(days=30)
            events = [
                event
                for event in candidates
                if event["actual_posted_at"] is None
                or window_start
                <= _timestamp(event["actual_posted_at"], "actual_posted_at")
                <= post_observed
            ]
        snapshot = room_snapshots[post.source_key]
        source_ref = snapshot_refs[post.source_key]
        if len(events) == 1:
            event = events[0]
            event_id = str(event["event_id"])
            matched_event_ids.add(event_id)
            age_seconds = _date_for_age(
                event["actual_posted_at"] or post.post_created_at, post.observed_at
            )
            availability = "observed" if post.likes is not None else "unknown"
            value = post.likes
            ref = event_id
        else:
            age_seconds = _date_for_age(post.post_created_at, post.observed_at)
            availability = "unmatched"
            value = None
            ref = f"unmatched:{post.post_id}"
        _insert_metric_observation(
            connection,
            scope="room_post",
            ref=ref,
            metric="likes",
            value=value,
            unit="count",
            observed_at=post.observed_at,
            window=_room_window(post.window, age_seconds),
            availability=availability,
            source_ref=source_ref,
            provisional=False,
        )
        count += 1

    for snapshot in room_snapshots.values():
        if snapshot.status == "unavailable":
            missing_availability = "unavailable"
        elif snapshot.status == "observed":
            missing_availability = "not_present"
        else:
            missing_availability = "unknown"
        if snapshot.status not in {"observed", "partial", "unavailable"}:
            continue
        observed = _timestamp(snapshot.observed_at, "ROOM observed_at")
        start = observed - timedelta(days=30)
        recent_events = connection.execute(
            """
            SELECT event_id, actual_posted_at, reported_at FROM publication_event
             WHERE valid=1
            """
        ).fetchall()
        for event in recent_events:
            if event["actual_posted_at"] is None:
                reported_at = _timestamp(str(event["reported_at"]), "reported_at")
                if reported_at < start or reported_at > observed:
                    continue
                posted_at = None
            else:
                posted_at = _timestamp(
                    str(event["actual_posted_at"]), "actual_posted_at"
                )
                if posted_at < start or posted_at > observed:
                    continue
            event_id = str(event["event_id"])
            if event_id in matched_event_ids:
                continue
            _insert_metric_observation(
                connection,
                scope="room_post",
                ref=event_id,
                metric="likes",
                value=None,
                unit="count",
                observed_at=snapshot.observed_at,
                window=_room_window(
                    "room-recent-30d",
                    int((observed - posted_at).total_seconds()) if posted_at else None,
                ),
                availability=("unknown" if posted_at is None else missing_availability),
                source_ref=snapshot_refs[snapshot.source_key],
                provisional=False,
            )
            count += 1
    return count


def _store_order(
    connection: sqlite3.Connection,
    *,
    item: OrderItemObservation,
    source_ref: str,
    batch_identities: Mapping[tuple[Any, ...], int],
) -> int:
    if item.source_key == "":
        raise RejectedContract("order item source_key is required")
    if item.occurred_at is not None:
        _timestamp(item.occurred_at, "order occurred_at")
    _timestamp(item.observed_at, "order observed_at")
    _require_text(item.window, "order window", max_length=256)
    sales_yen = _non_negative_number(item.sales_yen, "order sales_yen")
    commission_yen = _non_negative_number(item.commission_yen, "order commission_yen")
    url = _canonical_https_url(item.product_url)
    for value, field in (
        (item.shop_name, "shop_name"),
        (item.product_name, "product_name"),
        (item.item_code, "item_code"),
        (item.order_status, "order_status"),
        (item.link_type, "link_type"),
    ):
        if value is not None:
            _require_text(value, field, max_length=512)
    batch_identities = batch_identities
    order_ref, temporary, old_ref = _resolve_order_ref(
        connection, item, batch_identities=batch_identities
    )
    match_kind, matched_offer_key, entry_post_ref = _resolve_order_match(
        connection, item
    )
    if old_ref is not None:
        connection.execute(
            "UPDATE revenue_order SET order_ref=? WHERE order_ref=?",
            (order_ref, old_ref),
        )
        connection.execute(
            "UPDATE metric_observation SET ref=? WHERE scope='order_item' AND ref=?",
            (order_ref, old_ref),
        )
    connection.execute(
        """
        INSERT INTO revenue_order
            (order_ref, is_temporary_ref, occurred_at, shop_name, product_name,
             item_code, product_url, sales_yen, commission_yen, order_status,
             link_type, source_ref, match_kind, matched_offer_key, entry_post_ref)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_ref) DO UPDATE SET
            is_temporary_ref=excluded.is_temporary_ref,
            occurred_at=excluded.occurred_at,
            shop_name=excluded.shop_name,
            product_name=excluded.product_name,
            item_code=excluded.item_code,
            product_url=excluded.product_url,
            sales_yen=excluded.sales_yen,
            commission_yen=excluded.commission_yen,
            order_status=excluded.order_status,
            link_type=excluded.link_type,
            source_ref=excluded.source_ref,
            match_kind=excluded.match_kind,
            matched_offer_key=excluded.matched_offer_key,
            entry_post_ref=excluded.entry_post_ref
        """,
        (
            order_ref,
            int(temporary),
            item.occurred_at,
            item.shop_name,
            item.product_name,
            item.item_code,
            url,
            sales_yen,
            commission_yen,
            item.order_status,
            item.link_type,
            source_ref,
            match_kind,
            matched_offer_key,
            entry_post_ref,
        ),
    )
    for metric, value in (
        ("sales_yen", sales_yen),
        ("commission_unconfirmed_yen", commission_yen),
    ):
        _insert_metric_observation(
            connection,
            scope="order_item",
            ref=order_ref,
            metric=metric,
            value=value,
            unit="JPY",
            observed_at=item.observed_at,
            window=item.window,
            availability="observed" if value is not None else "unknown",
            source_ref=source_ref,
            provisional=True,
        )
    return 1


def commit_metrics_result(
    connection: sqlite3.Connection,
    *,
    data_root: str | Path,
    result: MetricsCollectionResult,
    expected_run_id: str | None = None,
    clock: Clock,
    deadline_at: datetime,
    before_commit: Callable[[], None] | None = None,
) -> MetricsCommitOutcome:
    """Persist completed metrics as one SQLite transaction after raw artifacts.

    Source response artifacts are written first. If the database commit then
    fails, those files remain in the explicit private data root for recovery;
    the caller must not trigger a second external collection in the same run.
    """

    if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
        raise RejectedContract("metrics deadline must include a timezone offset")
    deadline = deadline_at.astimezone(JST)
    active_run_id = expected_run_id or result.run_id
    snapshots = _validate_result(result, active_run_id)
    if any(
        _timestamp(s.observed_at, "snapshot observed_at") >= deadline
        for s in snapshots.values()
    ):
        raise MetricsDeadlineExceeded(
            "source response timestamp is at or after request deadline"
        )
    _check_deadline(clock, deadline)
    run = connection.execute(
        "SELECT target_date FROM run WHERE run_id=?", (active_run_id,)
    ).fetchone()
    if run is None:
        raise RejectedContract("metrics result refers to an unknown run")
    result_time = _timestamp(result.observed_at, "metrics observed_at")
    if result_time >= deadline:
        raise MetricsDeadlineExceeded(
            "metrics response timestamp is at or after request deadline"
        )
    if result_time.date().isoformat() != str(run["target_date"]):
        raise RejectedContract("metrics result date does not match the active run")
    if connection.in_transaction:
        raise RejectedContract(
            "metrics persistence requires an idle writable connection"
        )
    source_refs: dict[str, str] = {}
    artifacts: list[Any] = []
    for snapshot in snapshots.values():
        created_at = _timestamp(snapshot.observed_at, "snapshot observed_at")
        expiry = created_at + timedelta(days=90)
        payload = {
            "run_id": active_run_id,
            "source_key": snapshot.source_key,
            "source": snapshot.source,
            "status": snapshot.status,
            "observed_at": created_at.isoformat(),
            "payload": dict(snapshot.payload),
        }
        artifact = write_temp_response(
            data_root=data_root,
            artifact_ref=_artifact_ref(),
            payload=payload,
            source_kind="metrics",
            created_at=created_at.isoformat(),
            retention_expires_at=expiry.isoformat(),
        )
        source_refs[snapshot.source_key] = artifact.artifact_ref
        artifacts.append(artifact)
        _check_deadline(clock, deadline)
    if any(obs.source_key not in snapshots for obs in result.daily_observations):
        raise RejectedContract("daily observation refers to an unknown source snapshot")
    if any(order.source_key not in snapshots for order in result.orders):
        raise RejectedContract("order refers to an unknown source snapshot")
    if any(item.source_key not in snapshots for item in result.order_windows):
        raise RejectedContract("order window refers to an unknown source snapshot")
    if any(post.source_key not in snapshots for post in result.room_posts):
        raise RejectedContract("ROOM post refers to an unknown source snapshot")
    for observation in result.daily_observations:
        source = snapshots[observation.source_key]
        if source.source != "affiliate":
            raise RejectedContract(
                "daily observations must refer to an affiliate source"
            )
        if (
            source.status in {"unavailable", "not_present"}
            and observation.availability == "observed"
        ):
            raise RejectedContract(
                "unavailable affiliate response cannot contain observed values"
            )
        if source.status == "not_present" and observation.availability != "not_present":
            raise RejectedContract(
                "not-present affiliate response must remain not-present"
            )
        if _timestamp(observation.observed_at, "daily observed_at") >= deadline:
            raise MetricsDeadlineExceeded(
                "daily observation timestamp is at or after request deadline"
            )
    for order in result.orders:
        if snapshots[order.source_key].source != "affiliate" or snapshots[
            order.source_key
        ].status not in {"observed", "partial"}:
            raise RejectedContract("unavailable affiliate source cannot contain orders")
        if _timestamp(order.observed_at, "order observed_at") >= deadline:
            raise MetricsDeadlineExceeded(
                "order observation timestamp is at or after request deadline"
            )
    for order_window in result.order_windows:
        source = snapshots[order_window.source_key]
        if source.source != "affiliate":
            raise RejectedContract("order windows must refer to an affiliate source")
        if _timestamp(order_window.observed_at, "order window observed_at") >= deadline:
            raise MetricsDeadlineExceeded(
                "order window timestamp is at or after request deadline"
            )
        if order_window.count is not None and (
            isinstance(order_window.count, bool)
            or not isinstance(order_window.count, int)
            or order_window.count < 0
        ):
            raise RejectedContract(
                "order window count must be a non-negative integer or null"
            )
        if order_window.availability not in _AVAILABILITY:
            raise RejectedContract("order window availability is unsupported")
        if (order_window.count is None) != (order_window.availability != "observed"):
            raise RejectedContract("order window count and availability disagree")
        if (
            source.status in {"unavailable", "not_present"}
            and order_window.count is not None
        ):
            raise RejectedContract(
                "unavailable CSV cannot have an observed order count"
            )
        if (
            source.status == "not_present"
            and order_window.availability != "not_present"
        ):
            raise RejectedContract("not-present CSV must remain not-present")
        _require_text(order_window.window, "order window", max_length=256)
    for post in result.room_posts:
        if snapshots[post.source_key].source != "room" or snapshots[
            post.source_key
        ].status not in {"observed", "partial"}:
            raise RejectedContract(
                "unavailable ROOM source cannot contain post observations"
            )
        if _timestamp(post.observed_at, "ROOM observed_at") >= deadline:
            raise MetricsDeadlineExceeded(
                "ROOM observation timestamp is at or after request deadline"
            )
    if len({obs.source_key for obs in result.daily_observations} - set(snapshots)):
        raise RejectedContract("daily observations have an invalid source key")

    identity_counts: dict[tuple[Any, ...], int] = {}
    for item in result.orders:
        identity = (
            item.occurred_at,
            item.shop_name,
            item.item_code,
            _canonical_https_url(item.product_url),
            item.product_name,
        )
        identity_counts[identity] = identity_counts.get(identity, 0) + 1

    observation_count = 0
    order_count = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for artifact in artifacts:
            connection.execute(
                """
                INSERT INTO artifact
                    (artifact_ref, path, hash, created_at, source_kind,
                     retention_expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_ref,
                    str(artifact.path),
                    artifact.content_hash,
                    artifact.created_at,
                    artifact.source_kind,
                    artifact.retention_expires_at,
                    artifact.status,
                ),
            )
        for obs in result.daily_observations:
            if obs.source_key not in source_refs:
                raise RejectedContract("daily observation source has no artifact")
            _insert_metric_observation(
                connection,
                scope="day_aggregate",
                ref=obs.ref,
                metric=obs.metric,
                value=obs.value,
                unit=obs.unit,
                observed_at=obs.observed_at,
                window=obs.window,
                availability=obs.availability,
                source_ref=source_refs[obs.source_key],
                provisional=obs.provisional,
            )
            observation_count += 1
        for item in result.orders:
            order_count += _store_order(
                connection,
                item=item,
                source_ref=source_refs[item.source_key],
                batch_identities=identity_counts,
            )
            observation_count += 2
        for order_window in result.order_windows:
            _insert_metric_observation(
                connection,
                scope="order_item",
                ref=f"order-window:{order_window.window}",
                metric="sales_count",
                value=order_window.count,
                unit="count",
                observed_at=order_window.observed_at,
                window=order_window.window,
                availability=order_window.availability,
                source_ref=source_refs[order_window.source_key],
                provisional=True,
            )
            observation_count += 1
        observation_count += _store_room_posts(
            connection, result=result, snapshot_refs=source_refs
        )
        _check_deadline(clock, deadline)
        if before_commit is not None:
            before_commit()
        _check_deadline(clock, deadline)
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    return MetricsCommitOutcome(
        status=result.status,
        artifact_refs=tuple(source_refs.values()),
        observation_count=observation_count,
        order_count=order_count,
    )


def _check_deadline(clock: Clock, deadline: datetime) -> None:
    if clock.now_jst().astimezone(JST) >= deadline:
        raise MetricsDeadlineExceeded("metrics persistence deadline has passed")


def maybe_explain_metrics(
    summary: Mapping[str, Any],
    *,
    explicitly_requested: bool,
    port: OptionalInsightPort | None,
) -> OptionalInsightResponse | None:
    """Invoke the optional explanation port only after an explicit request."""

    if explicitly_requested is not True:
        return None
    if port is None:
        return OptionalInsightResponse(
            "unavailable", error_code="insight_port_unavailable"
        )
    try:
        response = port.explain(summary)
        if not isinstance(response, Mapping):
            return OptionalInsightResponse(
                "unavailable", error_code="insight_invalid_response"
            )
        _check_safe_payload(response)
        return OptionalInsightResponse("observed", payload=dict(response))
    except Exception as exc:
        return OptionalInsightResponse("unavailable", error_code=type(exc).__name__)
