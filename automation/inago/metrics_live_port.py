"""Convert the read-only live metrics adapter output to the SQLite contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
from typing import Any, Mapping

from automation.inago.metrics import (
    DailyMetricObservation,
    MetricsCollectionResult,
    MetricsSnapshot,
    OrderItemObservation,
    OrderWindowObservation,
    RoomPostObservation,
)


class MetricsAdapterError(RuntimeError):
    """A live metrics process failed its output or path contract."""


def _status(value: object) -> str:
    return (
        value
        if isinstance(value, str)
        and value in {"observed", "partial", "unavailable", "not_present"}
        else "unavailable"
    )


def _load_document(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MetricsAdapterError(f"{label}_document_invalid")
    return dict(value)


def _room_post_id(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise MetricsAdapterError("room_post_record_invalid")
    if isinstance(value, int):
        if value <= 0:
            raise MetricsAdapterError("room_post_record_invalid")
        return str(value)
    if not isinstance(value, str):
        raise MetricsAdapterError("room_post_record_invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(char) < 32 for char in normalized)
    ):
        raise MetricsAdapterError("room_post_record_invalid")
    return normalized


def metrics_result_from_documents(
    *,
    run_id: str,
    target_date: str,
    affiliate: Mapping[str, Any],
    room: Mapping[str, Any],
) -> MetricsCollectionResult:
    """Create scoped metric records while retaining true zero and unknown null."""

    affiliate_doc = _load_document(affiliate, "affiliate")
    room_doc = _load_document(room, "room")
    affiliate_time = affiliate_doc.get("observedAt")
    room_time = room_doc.get("observedAt")
    try:
        affiliate_dt = datetime.fromisoformat(
            str(affiliate_time).replace("Z", "+00:00")
        )
        room_dt = datetime.fromisoformat(str(room_time).replace("Z", "+00:00"))
        if affiliate_dt.tzinfo is None or room_dt.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise MetricsAdapterError("source_observation_time_invalid") from exc
    latest_observation = max(affiliate_dt, room_dt)
    observed_at = latest_observation.isoformat()
    if (
        latest_observation.astimezone(timezone(timedelta(hours=9))).date().isoformat()
        != target_date
    ):
        raise MetricsAdapterError("source_observation_date_mismatch")

    affiliate_status = _status(affiliate_doc.get("status"))
    room_status = _status(room_doc.get("status"))
    daily = affiliate_doc.get("daily")
    rows = daily.get("rows", []) if isinstance(daily, Mapping) else []
    if not isinstance(rows, list):
        raise MetricsAdapterError("affiliate_daily_rows_invalid")
    snapshots = (
        MetricsSnapshot(
            source_key="affiliate",
            source="affiliate",
            status=affiliate_status,
            observed_at=affiliate_dt.isoformat(),
            payload={
                "status": affiliate_status,
                "daily_date_count": len(rows),
                "order_count": (
                    len(affiliate_doc.get("orders", []))
                    if isinstance(affiliate_doc.get("orders", []), list)
                    else 0
                ),
                "error_code": affiliate_doc.get("errorCode"),
            },
        ),
        MetricsSnapshot(
            source_key="room",
            source="room",
            status=room_status,
            observed_at=room_dt.isoformat(),
            payload={
                "status": room_status,
                "post_count": (
                    len(room_doc.get("posts", []))
                    if isinstance(room_doc.get("posts", []), list)
                    else 0
                ),
                "window_start": room_doc.get("windowStart"),
                "error_code": room_doc.get("errorCode"),
            },
        ),
    )
    daily_records: list[DailyMetricObservation] = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("date"), str):
            raise MetricsAdapterError("affiliate_daily_row_invalid")
        for metric, key, unit, provisional in (
            ("clicks", "clicks", "count", False),
            ("sales_count", "salesCount", "count", True),
            ("sales_yen", "salesYen", "JPY", True),
            ("commission_unconfirmed_yen", "commissionUnconfirmedYen", "JPY", True),
        ):
            value = row.get(key)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise MetricsAdapterError("affiliate_metric_value_invalid")
            daily_records.append(
                DailyMetricObservation(
                    source_key="affiliate",
                    ref=row["date"],
                    metric=metric,
                    value=value,
                    unit=unit,
                    observed_at=affiliate_dt.isoformat(),
                    window="affiliate-daily-7d",
                    availability="observed" if value is not None else "unknown",
                    provisional=provisional,
                )
            )

    raw_orders = affiliate_doc.get("orders", [])
    raw_windows = affiliate_doc.get("orderWindows", [])
    raw_posts = room_doc.get("posts", [])
    if not all(
        isinstance(items, list) for items in (raw_orders, raw_windows, raw_posts)
    ):
        raise MetricsAdapterError("metrics_record_list_invalid")
    orders = tuple(
        OrderItemObservation(
            source_key="affiliate",
            order_ref=item.get("orderRef"),
            is_temporary_ref=item.get("isTemporaryRef") is True,
            occurred_at=item.get("occurredAt"),
            shop_name=item.get("shopName"),
            product_name=item.get("productName"),
            item_code=item.get("itemCode"),
            product_url=item.get("productUrl"),
            sales_yen=item.get("salesYen"),
            commission_yen=item.get("commissionYen"),
            order_status=item.get("orderStatus"),
            link_type=item.get("linkType"),
            window=item.get("window"),
            observed_at=affiliate_dt.isoformat(),
        )
        for item in raw_orders
        if isinstance(item, Mapping)
    )
    if len(orders) != len(raw_orders):
        raise MetricsAdapterError("affiliate_order_record_invalid")
    order_windows = tuple(
        OrderWindowObservation(
            source_key="affiliate",
            window=item.get("window"),
            count=item.get("count"),
            observed_at=item.get("observedAt", affiliate_dt.isoformat()),
            availability=item.get("availability", "unknown"),
        )
        for item in raw_windows
        if isinstance(item, Mapping)
    )
    if len(order_windows) != len(raw_windows):
        raise MetricsAdapterError("affiliate_order_window_invalid")
    room_posts = tuple(
        RoomPostObservation(
            source_key="room",
            post_id=_room_post_id(item.get("postId")),
            room_post_url=item.get("roomPostUrl"),
            item_url=item.get("itemUrl"),
            likes=item.get("likes"),
            observed_at=room_dt.isoformat(),
            window="room-recent-30d",
            post_created_at=item.get("createdAt"),
        )
        for item in raw_posts
        if isinstance(item, Mapping)
    )
    if len(room_posts) != len(raw_posts):
        raise MetricsAdapterError("room_post_record_invalid")
    statuses = (affiliate_status, room_status)
    final_status = (
        "observed"
        if all(status == "observed" for status in statuses)
        else (
            "partial"
            if any(status in {"observed", "partial"} for status in statuses)
            else "unavailable"
        )
    )
    return MetricsCollectionResult(
        run_id=run_id,
        status=final_status,
        observed_at=observed_at,
        snapshots=snapshots,
        daily_observations=tuple(daily_records),
        orders=orders,
        order_windows=order_windows,
        room_posts=room_posts,
    )


class NodeMetricsPort:
    """Invoke the audited Node/Playwright read-only collector for one run."""

    fixture_only = False

    def __init__(
        self,
        *,
        node_executable: str,
        browser_executable: str | Path,
        script_path: str | Path,
        output_root: str | Path,
        runtime_root: str | Path,
        owner_room_url: str,
        affiliate_auth_state: str | Path,
        timeout_seconds: int = 1800,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.node_executable = node_executable
        self.browser_executable = Path(browser_executable).resolve()
        self.script_path = Path(script_path).resolve()
        self.output_root = Path(output_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.owner_room_url = owner_room_url
        self.affiliate_auth_state = Path(affiliate_auth_state).resolve()
        self.timeout_seconds = timeout_seconds
        self.environment = dict(environment) if environment is not None else None
        self._guard = threading.Lock()
        self._processes: dict[str, subprocess.Popen] = {}
        if self.output_root != self.runtime_root / "artifacts" / "metrics":
            raise MetricsAdapterError(
                "metrics_output_root_must_be_private_runtime_child"
            )
        if (
            not self.browser_executable.is_file()
            or not self.script_path.is_file()
            or timeout_seconds < 1
        ):
            raise MetricsAdapterError("metrics_adapter_configuration_invalid")

    def collect(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        deadline_at: str,
    ) -> MetricsCollectionResult:
        row = connection.execute(
            "SELECT target_date FROM run WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise MetricsAdapterError("metrics_run_not_found")
        target_date = str(row[0])
        try:
            deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise MetricsAdapterError("metrics_deadline_invalid") from exc
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise MetricsAdapterError("metrics_deadline_invalid")
        remaining = (deadline - datetime.now(deadline.tzinfo)).total_seconds()
        timeout = min(self.timeout_seconds, int(remaining))
        if timeout < 1:
            raise MetricsAdapterError("metrics_deadline_exhausted")
        env = dict(self.environment if self.environment is not None else os.environ)
        env["INAGO_RUNTIME_ROOT"] = str(self.runtime_root)
        env["INAGO_OWNER_ROOM_URL"] = self.owner_room_url
        env["INAGO_AFFILIATE_AUTH_STATE"] = str(self.affiliate_auth_state)
        env["INAGO_BROWSER_EXECUTABLE"] = str(self.browser_executable)
        self.output_root.mkdir(parents=True, exist_ok=True)
        command = [
            self.node_executable,
            str(self.script_path),
            "--output-root",
            str(self.output_root),
            "--target-date",
            target_date,
            "--deadline-seconds",
            str(timeout),
        ]
        process = None
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.runtime_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            with self._guard:
                self._processes[run_id] = process
            stdout, _stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                if not self._stop_process_tree(process):
                    raise MetricsAdapterError(
                        "metrics_process_cleanup_unconfirmed"
                    ) from exc
            raise MetricsAdapterError("metrics_process_timeout") from exc
        except OSError as exc:
            raise MetricsAdapterError(type(exc).__name__) from exc
        finally:
            if process is None or process.poll() is not None:
                with self._guard:
                    if self._processes.get(run_id) is process:
                        self._processes.pop(run_id, None)
        if process.returncode != 0:
            raise MetricsAdapterError("metrics_process_failed")
        try:
            lines = stdout.decode("utf-8").splitlines()
            if not lines or not lines[-1].strip():
                raise ValueError("metrics worker returned no JSON line")
            output = json.loads(lines[-1])
            raw_attempt = Path(output["attemptDir"])
            if self._has_reparse_point(raw_attempt):
                raise ValueError
            attempt = raw_attempt.resolve(strict=True)
            if not attempt.is_relative_to(self.output_root):
                raise ValueError
            affiliate_path = attempt / "affiliate.json"
            room_path = attempt / "room.json"
            if self._has_reparse_point(affiliate_path) or self._has_reparse_point(
                room_path
            ):
                raise ValueError
            affiliate = json.loads(affiliate_path.read_text(encoding="utf-8"))
            room = json.loads(room_path.read_text(encoding="utf-8"))
        except (
            UnicodeError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise MetricsAdapterError("metrics_output_contract_invalid") from exc
        return metrics_result_from_documents(
            run_id=run_id,
            target_date=target_date,
            affiliate=affiliate,
            room=room,
        )

    def cancel_run(self, run_id: str) -> bool:
        """Stop only the Node process tree started for the named daily run."""

        with self._guard:
            process = self._processes.get(run_id)
        if process is None:
            return True
        if process.poll() is not None:
            with self._guard:
                if self._processes.get(run_id) is process:
                    self._processes.pop(run_id, None)
            return True
        if not self._stop_process_tree(process):
            return False
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        if process.poll() is None:
            return False
        with self._guard:
            if self._processes.get(run_id) is process:
                self._processes.pop(run_id, None)
        return True

    @staticmethod
    def _has_reparse_point(path: Path) -> bool:
        cursor = path
        while True:
            try:
                info = cursor.lstat()
            except FileNotFoundError:
                pass
            except OSError:
                return True
            else:
                if (
                    cursor.is_symlink()
                    or getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    return True
            if cursor.parent == cursor:
                return False
            cursor = cursor.parent

    @staticmethod
    def _stop_process_tree(process: subprocess.Popen) -> bool:
        if process.poll() is not None:
            return True
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                    shell=False,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            if result.returncode != 0:
                return False
        else:
            try:
                process.terminate()
            except OSError:
                return False
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                return False
        return process.poll() is not None
