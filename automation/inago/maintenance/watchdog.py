"""Reconcile daily launch records and scheduled occurrences without killing processes."""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from automation.inago.maintenance.process_identity import (
    ProcessObservation,
    observe_process,
)
from automation.inago.maintenance.store import (
    DailyLaunchRecord,
    LeaseBusyError,
    LeaseRecord,
    LeaseToken,
    MaintenanceStore,
    WatchdogQueueRecord,
)


_TASK_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LEASE_KEY = "daily-watchdog"
_TASK_NAMESPACE = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
_SCHEDULE_TZ = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True, slots=True)
class SchedulerOccurrence:
    task_key: str
    target_date: str
    scheduled_at: datetime
    enabled: bool
    history_available: bool
    last_run_time: datetime | None
    last_result: int | None


@dataclass(frozen=True, slots=True)
class WatchdogResult:
    queued_source_launch_ids: tuple[str, ...]
    queued_queue_ids: tuple[str, ...]
    reconciled_queue_ids: tuple[str, ...]
    lease_acquired: bool


ProcessProbe = Callable[[int], ProcessObservation]


def _utc(value: datetime, code: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(code)
    return value.astimezone(timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _schedule_date(value: str, code: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(code) from exc
    if parsed.isoformat() != value:
        raise ValueError(code)
    return parsed


def _observation(probe: ProcessProbe, pid: int) -> ProcessObservation:
    try:
        result = probe(pid)
    except Exception:
        return ProcessObservation("unknown", None)
    if not isinstance(result, ProcessObservation):
        return ProcessObservation("unknown", None)
    return result


def _confirmed_not_same_process(
    observation: ProcessObservation, recorded_start_time: str | None
) -> bool:
    if observation.state == "stopped":
        return True
    if observation.state != "running" or recorded_start_time is None:
        return False
    recorded = _parse_time(recorded_start_time)
    observed = _parse_time(observation.start_time)
    return recorded is not None and observed is not None and recorded != observed


def _prior_lease_observation(
    lease: LeaseRecord | None, process_probe: ProcessProbe
) -> str:
    if lease is None or lease.state != "held":
        return "unknown"
    observed = _observation(process_probe, lease.owner_pid)
    if observed.state == "stopped":
        return "dead"
    if observed.state != "running":
        return "unknown"
    previous_start = _parse_time(lease.owner_start_time)
    observed_start = _parse_time(observed.start_time)
    if previous_start is None or observed_start is None:
        return "unknown"
    return "alive" if previous_start == observed_start else "reused"


def _occurrence_key(occurrence: SchedulerOccurrence) -> str:
    if not isinstance(occurrence.task_key, str) or not _TASK_KEY.fullmatch(
        occurrence.task_key
    ):
        raise ValueError("scheduler_task_key_invalid")
    _utc(occurrence.scheduled_at, "scheduler_occurrence_time_invalid")
    try:
        target = datetime.fromisoformat(occurrence.target_date).date()
    except ValueError as exc:
        raise ValueError("scheduler_occurrence_date_invalid") from exc
    if (
        target.isoformat() != occurrence.target_date
        or target != occurrence.scheduled_at.date()
    ):
        raise ValueError("scheduler_occurrence_date_mismatch")
    return f"scheduled:{occurrence.task_key}:{occurrence.target_date}"


def _record_for_occurrence(
    store: MaintenanceStore,
    occurrence: SchedulerOccurrence,
    *,
    now: datetime,
    start_grace: timedelta,
    launches: tuple[DailyLaunchRecord, ...],
    existing_queue: WatchdogQueueRecord | None,
) -> tuple[WatchdogQueueRecord | None, bool]:
    key = _occurrence_key(occurrence)
    if not launches:
        if existing_queue is not None:
            return None, False
        if not occurrence.enabled:
            return None, False
        scheduled = _utc(occurrence.scheduled_at, "scheduler_occurrence_time_invalid")
        if _utc(now, "watchdog_clock_invalid") < scheduled + start_grace:
            return None, False
        reason = "daily_launcher_not_started"
        if occurrence.history_available and occurrence.last_run_time is not None:
            last_run = _utc(occurrence.last_run_time, "scheduler_last_run_time_invalid")
            if scheduled <= last_run <= _utc(now, "watchdog_clock_invalid"):
                reason = "daily_launch_record_missing"
        record, created = store.record_watchdog_queue(
            occurrence_key=key,
            target_date=occurrence.target_date,
            reason_code=reason,
            now=now,
            status="unknown",
        )
        return record, created

    if existing_queue is None or existing_queue.status in {"resolved", "blocked"}:
        return None, False
    if existing_queue.source_launch_id is not None:
        launch = next(
            (
                item
                for item in launches
                if item.source_launch_id == existing_queue.source_launch_id
            ),
            None,
        )
        if launch is None:
            return existing_queue, False
    else:
        launch = min(
            launches, key=lambda item: (item.started_at, item.source_launch_id)
        )
    terminal = launch.status != "running"
    reason = "daily_launch_completed" if terminal else "daily_launch_linked"
    if (
        not terminal
        and existing_queue.reason_code == "daily_launch_terminated_without_notice"
    ):
        reason = existing_queue.reason_code
    status = "resolved" if terminal else "awaiting_completion"
    if (
        existing_queue.source_launch_id == launch.source_launch_id
        and existing_queue.status == status
        and existing_queue.reason_code == reason
    ):
        return existing_queue, False
    record, _ = store.record_watchdog_queue(
        occurrence_key=key,
        target_date=occurrence.target_date,
        reason_code=reason,
        now=now,
        source_launch_id=launch.source_launch_id,
        status=status,
    )
    return record, existing_queue.source_launch_id is None


def run_watchdog_once(
    store: MaintenanceStore,
    *,
    now: datetime,
    occurrences: Iterable[SchedulerOccurrence],
    process_probe: ProcessProbe = observe_process,
    owner_id: str,
    owner_pid: int,
    owner_start_time: datetime | str,
    start_grace: timedelta = timedelta(minutes=5),
) -> WatchdogResult:
    """Enqueue only confirmed stopped launches; leave unknown process state untouched."""

    current = _utc(now, "watchdog_clock_invalid")
    if not isinstance(start_grace, timedelta) or start_grace < timedelta(0):
        raise ValueError("watchdog_start_grace_invalid")
    lease_owner_start = _utc(
        owner_start_time
        if isinstance(owner_start_time, datetime)
        else datetime.fromisoformat(owner_start_time),
        "watchdog_owner_start_time_invalid",
    )
    existing_lease = store.get_lease(_LEASE_KEY)
    prior = _prior_lease_observation(existing_lease, process_probe)
    try:
        lease = store.acquire_lease(
            lease_key=_LEASE_KEY,
            owner_id=owner_id,
            owner_pid=owner_pid,
            owner_start_time=lease_owner_start,
            now=current,
            ttl_seconds=300,
            prior_owner_observation=prior,
        )
    except LeaseBusyError:
        return WatchdogResult((), (), (), False)

    queued_launches: list[str] = []
    queued_ids: list[str] = []
    reconciled_ids: list[str] = []
    try:
        occurrence_items = tuple(occurrences)
        for occurrence in occurrence_items:
            _occurrence_key(occurrence)
        cursor = store.get_watchdog_cursor()
        today = current.astimezone(_SCHEDULE_TZ).date().isoformat()
        pending_queues = store.list_watchdog_queue(reconciliation_only=True)
        pending_dates = {queue.target_date for queue in pending_queues}
        active_occurrences: list[SchedulerOccurrence] = []
        cursor_candidates: list[str] = []
        for occurrence in occurrence_items:
            if occurrence.target_date > today:
                continue
            is_pending = occurrence.target_date in pending_dates
            is_current_date = occurrence.target_date == today
            is_new_date = cursor is None or occurrence.target_date > cursor
            scheduled = _utc(
                occurrence.scheduled_at, "scheduler_occurrence_time_invalid"
            )
            settled = not occurrence.enabled or scheduled + start_grace <= current
            if is_pending or is_current_date or (is_new_date and settled):
                active_occurrences.append(occurrence)
            if is_new_date and settled:
                cursor_candidates.append(occurrence.target_date)

        running_launches = store.list_daily_launches(running_only=True)
        target_dates = sorted(
            pending_dates
            | {item.target_date for item in active_occurrences}
            | {item.target_date for item in running_launches}
        )
        launches_by_date: dict[str, list[DailyLaunchRecord]] = {}
        queues_by_occurrence: dict[str, WatchdogQueueRecord] = {}
        queues_by_source_launch: dict[str, WatchdogQueueRecord] = {}
        if target_dates:
            for offset in range(0, len(target_dates), 400):
                date_batch = target_dates[offset : offset + 400]
                for launch in store.list_daily_launches(target_dates=date_batch):
                    launches_by_date.setdefault(launch.target_date, []).append(launch)
                for queue in store.list_watchdog_queue(target_dates=date_batch):
                    queues_by_occurrence[queue.occurrence_key] = queue
                    if queue.source_launch_id is not None:
                        queues_by_source_launch[queue.source_launch_id] = queue

        # Link late launches to their scheduled queue first. The launch scan
        # below then updates that same canonical row if the launch was lost.
        for occurrence in active_occurrences:
            key = _occurrence_key(occurrence)
            record, created = _record_for_occurrence(
                store,
                occurrence,
                now=current,
                start_grace=start_grace,
                launches=tuple(launches_by_date.get(occurrence.target_date, ())),
                existing_queue=queues_by_occurrence.get(key),
            )
            if record is None:
                continue
            queues_by_occurrence[record.occurrence_key] = record
            if record.source_launch_id is not None:
                queues_by_source_launch[record.source_launch_id] = record
            if created:
                if record.source_launch_id is None:
                    queued_ids.append(record.queue_id)
                else:
                    reconciled_ids.append(record.queue_id)

        for launch in running_launches:
            started = _parse_time(launch.started_at)
            if started is None or current - started < start_grace:
                continue
            # A missing child PID is not proof that no child exists: the launcher
            # can be interrupted after Popen() and before persisting that PID.
            if launch.child_pid is None:
                continue
            owned_processes = [
                (launch.owner_pid, launch.owner_start_time),
                (launch.child_pid, launch.child_start_time),
            ]
            if not all(
                _confirmed_not_same_process(
                    _observation(process_probe, pid), start_time
                )
                for pid, start_time in owned_processes
            ):
                continue
            existing_for_launch = queues_by_source_launch.get(launch.source_launch_id)
            if existing_for_launch is not None:
                if existing_for_launch.status in {"resolved", "blocked"}:
                    continue
                if (
                    existing_for_launch.reason_code
                    != "daily_launch_terminated_without_notice"
                ):
                    record, _ = store.record_watchdog_queue(
                        occurrence_key=existing_for_launch.occurrence_key,
                        target_date=launch.target_date,
                        source_launch_id=launch.source_launch_id,
                        status="unknown",
                        reason_code="daily_launch_terminated_without_notice",
                        now=current,
                    )
                    queues_by_occurrence[record.occurrence_key] = record
                    queues_by_source_launch[launch.source_launch_id] = record
                continue
            record, created = store.record_watchdog_queue(
                occurrence_key=f"launch:{launch.source_launch_id}",
                target_date=launch.target_date,
                source_launch_id=launch.source_launch_id,
                status="unknown",
                reason_code="daily_launch_terminated_without_notice",
                now=current,
            )
            queues_by_occurrence[record.occurrence_key] = record
            queues_by_source_launch[launch.source_launch_id] = record
            if created:
                queued_launches.append(launch.source_launch_id)
                queued_ids.append(record.queue_id)
        if cursor_candidates:
            latest_candidate = max(cursor_candidates)
            if cursor is None or latest_candidate > cursor:
                store.advance_watchdog_cursor(
                    reconciled_through_date=latest_candidate,
                    updated_at=current,
                )
    finally:
        store.release_lease(lease=lease, released_at=current)

    return WatchdogResult(
        queued_source_launch_ids=tuple(queued_launches),
        queued_queue_ids=tuple(queued_ids),
        reconciled_queue_ids=tuple(reconciled_ids),
        lease_acquired=True,
    )


def read_daily_scheduler_occurrences(
    *,
    now: datetime,
    task_name: str = "RakutenRoomInagoDaily",
    after_date: str | None = None,
    include_dates: Iterable[str] = (),
) -> tuple[SchedulerOccurrence, ...]:
    """Read the daily task definition; do not depend on its history channel."""

    if task_name != "RakutenRoomInagoDaily":
        raise ValueError("scheduler_task_name_unapproved")
    current = _utc(now, "watchdog_clock_invalid").astimezone(_SCHEDULE_TZ)
    try:
        completed = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", task_name, "/XML"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("scheduler_definition_unavailable") from exc
    if completed.returncode != 0:
        raise RuntimeError("scheduler_definition_unavailable")
    try:
        monitor = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", "RakutenRoomInagoWatchdog", "/XML"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("watchdog_definition_unavailable") from exc
    if monitor.returncode != 0:
        raise RuntimeError("watchdog_definition_unavailable")
    try:
        root = ET.fromstring(completed.stdout)
        monitor_root = ET.fromstring(monitor.stdout)
        trigger = root.find(f".//{_TASK_NAMESPACE}CalendarTrigger")
        monitor_trigger = monitor_root.find(f".//{_TASK_NAMESPACE}CalendarTrigger")
        settings = root.find(f"./{_TASK_NAMESPACE}Settings")
        start_node = (
            trigger.find(f"{_TASK_NAMESPACE}StartBoundary")
            if trigger is not None
            else None
        )
        monitor_start_node = (
            monitor_trigger.find(f"{_TASK_NAMESPACE}StartBoundary")
            if monitor_trigger is not None
            else None
        )
        daily = (
            trigger.find(
                f"{_TASK_NAMESPACE}ScheduleByDay/{_TASK_NAMESPACE}DaysInterval"
            )
            if trigger is not None
            else None
        )
        task_enabled = (
            settings.find(f"{_TASK_NAMESPACE}Enabled") if settings is not None else None
        )
        trigger_enabled = (
            trigger.find(f"{_TASK_NAMESPACE}Enabled") if trigger is not None else None
        )
        if (
            start_node is None
            or not start_node.text
            or monitor_start_node is None
            or not monitor_start_node.text
            or daily is None
            or daily.text != "1"
        ):
            raise ValueError
        start = datetime.fromisoformat(start_node.text.replace("Z", "+00:00"))
        monitor_start = datetime.fromisoformat(
            monitor_start_node.text.replace("Z", "+00:00")
        )
        if start.tzinfo is None or start.utcoffset() is None:
            start = start.replace(tzinfo=_SCHEDULE_TZ)
        if monitor_start.tzinfo is None or monitor_start.utcoffset() is None:
            monitor_start = monitor_start.replace(tzinfo=_SCHEDULE_TZ)
        enabled = (
            task_enabled is not None
            and (task_enabled.text or "").lower() == "true"
            and trigger_enabled is not None
            and (trigger_enabled.text or "").lower() == "true"
        )
    except (ET.ParseError, ValueError, AttributeError) as exc:
        raise RuntimeError("scheduler_definition_invalid") from exc

    start_date = max(
        start.astimezone(_SCHEDULE_TZ).date(),
        monitor_start.astimezone(_SCHEDULE_TZ).date(),
    )
    today = current.date()
    cursor_date = (
        _schedule_date(after_date, "watchdog_cursor_date_invalid")
        if after_date is not None
        else None
    )
    first_date = (
        max(start_date, cursor_date + timedelta(days=1))
        if cursor_date is not None
        else start_date
    )
    occurrence_dates: set[date] = set()
    day = first_date
    while day <= today:
        occurrence_dates.add(day)
        day += timedelta(days=1)
    if start_date <= today:
        occurrence_dates.add(today)
    for included in include_dates:
        included_date = _schedule_date(included, "watchdog_pending_queue_date_invalid")
        if included_date <= today:
            occurrence_dates.add(included_date)

    occurrences: list[SchedulerOccurrence] = []
    scheduled_time = start.astimezone(_SCHEDULE_TZ).timetz().replace(tzinfo=None)
    for day in sorted(occurrence_dates):
        scheduled_at = datetime.combine(day, scheduled_time, tzinfo=_SCHEDULE_TZ)
        occurrences.append(
            SchedulerOccurrence(
                task_key="daily",
                target_date=day.isoformat(),
                scheduled_at=scheduled_at,
                enabled=enabled,
                history_available=False,
                last_run_time=None,
                last_result=None,
            )
        )
    return tuple(occurrences)
