"""R10 daily allocation, deadline, result, and Windows exit contracts."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import venv
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from automation.inago import cli, orchestrator as daily, repository
from automation.inago.clock import FakeClock
from automation.inago.daily_ai import DailyAIRootRunner
from automation.inago.live_sources import PublicBrowserSource, PublicSourceConfig
from automation.inago.orchestrator import DailyPorts
from automation.inago.recovery import plan_resume
from tests_inago.test_step13 import (
    runtime_case as step13_runtime_case,
    _make_ports,
    _run,
)


SETTINGS_VERSION = "R10-fixture-settings"
TARGET_DATE = "2026-10-16"
NOW = f"{TARGET_DATE}T13:00:00+09:00"


def _insert_settings(connection: sqlite3.Connection, created_at: str = NOW) -> None:
    connection.execute(
        "INSERT INTO settings_version "
        "(version,confirmed_at,genres_json,themes_json,pattern_refs_json,change_reason,created_at) "
        "VALUES (?, ?, '[]', '[]', '[]', ?, ?)",
        (SETTINGS_VERSION, created_at, "isolated R10 fixture", created_at),
    )


def _fixture_state() -> tuple[dict[str, object], dict[str, object]]:
    root, state = daily._daily_state(None)
    state.update(
        initial_theme_ids=["INITIAL-1", "INITIAL-2", "INITIAL-3"],
        theme_targets={
            "INITIAL-1": 5,
            "INITIAL-2": 5,
            "INITIAL-3": 5,
            "ADDITIONAL-1": 3,
        },
        additional_events=1,
        source_failures=4,
        error_count=1,
        errors=[{"phase": "copy", "code": "copy_generation_failed"}],
    )
    root["daily_orchestration"] = state
    return root, state


def _insert_running_run(
    connection: sqlite3.Connection,
    *,
    run_id: str = "RUN-R10",
    target_date: str = TARGET_DATE,
    status: str = "running",
    started_at: str = NOW,
    draft_deadline: str | None = None,
    end_deadline: str | None = None,
    committed_count: int = 0,
    checkpoint: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO run "
        "(run_id,target_date,mode,status,settings_version,seed,started_at,"
        "draft_deadline,end_deadline,attempt_used,committed_count,checkpoint,metrics_status) "
        "VALUES (?, ?, 'daily', ?, ?, 'R10-SEED', ?, ?, ?, 0, ?, ?, 'not_started')",
        (
            run_id,
            target_date,
            status,
            SETTINGS_VERSION,
            started_at,
            draft_deadline,
            end_deadline,
            committed_count,
            checkpoint,
        ),
    )


def _new_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    repository.initialize_database(connection)
    _insert_settings(connection)
    return connection


def test_n20_allocation_shortfall_and_posted_count_are_separate(tmp_path):
    database = _new_database(tmp_path / "R10" / "N20" / "state.sqlite")
    checkpoint, state = _fixture_state()
    _insert_running_run(
        database,
        status="partial",
        committed_count=12,
        checkpoint=json.dumps(checkpoint, ensure_ascii=False),
    )
    database.execute(
        "INSERT INTO offer "
        "(offer_key,shop_id,item_id,name,url,image_refs_json,main_theme_id,"
        "aux_theme_ids_json,first_seen,last_seen,facts_json) "
        "VALUES ('shop-n20:item001','shop-n20','item001','fixture offer',"
        "'https://example.invalid/item001','[]','INITIAL-1','[]',?,?, '[]')",
        (NOW, NOW),
    )
    database.execute(
        "INSERT INTO draft "
        "(draft_id,offer_key,run_id,version,body,generated_at,committed_at,"
        "generator_version,pattern_version,status,artifact_ref,pattern_id,evidence_refs_json) "
        "VALUES ('DRAFT-N20','shop-n20:item001','RUN-R10',1,'test body',?,?,"
        "'fixture','fixture','posted',NULL,'P01','[]')",
        (NOW, NOW),
    )
    database.execute(
        "INSERT INTO publication_event "
        "(event_id,offer_key,draft_id,draft_version,reported_at,actual_posted_at,"
        "room_url,evidence_kind,valid,revision) "
        "VALUES ('POST-N20','shop-n20:item001','DRAFT-N20',1,?,?,NULL,"
        "'human_statement',1,0)",
        (NOW, NOW),
    )

    row = database.execute("SELECT * FROM run WHERE run_id='RUN-R10'").fetchone()
    outcome = daily._outcome_from_run(row, connection=database)

    assert outcome.status == "partial"
    assert outcome.daily_target == 15
    assert outcome.initial_allocation_count == 15
    assert outcome.supplemental_allocation_count == 3
    assert outcome.committed_count == 12
    assert outcome.shortfall_count == 3
    assert outcome.actual_posted_count == 1
    assert outcome.source_failures == 4
    assert outcome.error_count == 1
    assert outcome.primary_error == {
        "phase": "copy",
        "code": "copy_generation_failed",
    }
    assert outcome.secondary_errors == ()
    database.close()


def test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines(
    step13_runtime_case,
):
    step13_runtime_case.clock.current = datetime.fromisoformat(
        "2026-10-12T18:00:00+09:00"
    )
    bundle = _make_ports(step13_runtime_case)

    outcome = _run(step13_runtime_case, bundle, run_id="RUN-R10-1800")

    assert outcome.status != "missed"
    assert outcome.run_id == "RUN-R10-1800"
    assert bundle.room.requests
    assert len(bundle.metrics.calls) == 1
    run = step13_runtime_case.connection.execute(
        "SELECT status,draft_deadline,end_deadline,attempt_used,metrics_status "
        "FROM run WHERE run_id=?",
        (outcome.run_id,),
    ).fetchone()
    assert run[0] != "missed"
    assert run[1] is None and run[2] is None
    assert run[3] > 0
    assert run[4] == "observed"


def test_n20g_daily_task_scheduler_has_no_run_wide_execution_limit():
    root = Path(__file__).resolve().parents[1]
    task = ElementTree.parse(
        root / "automation/inago/task-xml/RakutenRoomInagoDaily.xml"
    ).getroot()
    namespaces = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    assert (
        task.findtext("task:Settings/task:ExecutionTimeLimit", namespaces=namespaces)
        == "PT0S"
    )


def test_n20h_confirmed_room_timeout_stops_run_instead_of_retrying(
    step13_runtime_case,
):
    """A confirmed request-scoped cancel keeps ROOM retry and daily work live."""

    bundle = _make_ports(step13_runtime_case, posts_per_search=5)
    started = threading.Event()
    cancelled = threading.Event()
    original_search = bundle.room.search

    def blocked_search(request):
        started.set()
        cancelled.wait(timeout=5)
        return original_search(request)

    def cancel(run_id: str, request_id: str) -> bool:
        bundle.cancellations.append(run_id)
        cancelled.set()
        return True

    bundle.room.search = blocked_search
    bundle.ports = replace(bundle.ports, cancel_room_request=cancel)

    def advance_clock():
        assert started.wait(timeout=3), "owned ROOM request did not start"
        step13_runtime_case.clock.advance(480)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    outcome = _run(step13_runtime_case, bundle, run_id="RUN-R10-N20H")

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert len(bundle.room.requests) > 1
    assert len(bundle.runner.requests) == 15
    assert bundle.cancellations == ["RUN-R10-N20H"]
    attempt = step13_runtime_case.connection.execute(
        "SELECT source_status,error_code FROM search_attempt WHERE run_id=?",
        ("RUN-R10-N20H",),
    ).fetchone()
    assert tuple(attempt) == ("unavailable", "room_request_timeout")


def test_n20i_confirmed_ai_timeout_stops_run_without_replaying_work(
    step13_runtime_case,
):
    """A confirmed AI-job cancel discards its late reply and permits later jobs."""

    bundle = _make_ports(step13_runtime_case, posts_per_search=5)
    started = threading.Event()
    cancelled = threading.Event()
    original_run = bundle.runner.run

    def blocked_run(request):
        started.set()
        cancelled.wait(timeout=5)
        return original_run(request)

    def cancel(run_id: str, request_id: str) -> bool:
        bundle.cancellations.append(run_id)
        cancelled.set()
        return True

    bundle.runner.run = blocked_run
    bundle.ports = replace(bundle.ports, cancel_ai_request=cancel)

    def advance_clock():
        assert started.wait(timeout=3), "owned AI-O request did not start"
        step13_runtime_case.clock.advance(480)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    outcome = _run(step13_runtime_case, bundle, run_id="RUN-R10-N20I")

    assert outcome.committed_count > 0
    assert len(bundle.room.requests) > 1
    assert len(bundle.runner.requests) > 1
    assert bundle.cancellations == ["RUN-R10-N20I"]
    assert (
        step13_runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", ("RUN-R10-N20I",)
        ).fetchone()[0]
        == outcome.committed_count
    )
    checkpoint = step13_runtime_case.connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", ("RUN-R10-N20I",)
    ).fetchone()[0]
    state = json.loads(checkpoint)["daily_orchestration"]
    assert state["source_stopped"] is False
    assert {"phase": "copy", "code": "ai_request_timeout"} in state["errors"]


def test_n20j_room_timeout_cancels_only_request_and_continues(
    step13_runtime_case,
):
    bundle = _make_ports(step13_runtime_case, posts_per_search=5)
    started = threading.Event()
    cancelled = threading.Event()
    original_search = bundle.room.search

    def blocked_search(request):
        started.set()
        cancelled.wait(timeout=5)
        return original_search(request)

    def cancel(run_id: str, request_id: str) -> bool:
        bundle.cancellations.append(run_id)
        cancelled.set()
        return True

    bundle.room.search = blocked_search
    bundle.ports = replace(bundle.ports, cancel_room_request=cancel)

    def advance_clock():
        assert started.wait(timeout=3), "owned ROOM request did not start"
        step13_runtime_case.clock.advance(480)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    outcome = _run(step13_runtime_case, bundle, run_id="RUN-R10-N20J")

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert len(bundle.room.requests) > 1
    assert len(bundle.runner.requests) == 15
    assert bundle.cancellations == ["RUN-R10-N20J"]
    attempt = step13_runtime_case.connection.execute(
        "SELECT source_status,error_code FROM search_attempt "
        "WHERE run_id=? ORDER BY attempt_no LIMIT 1",
        ("RUN-R10-N20J",),
    ).fetchone()
    assert tuple(attempt) == ("unavailable", "room_request_timeout")


def test_n20k_ai_timeout_cancels_only_job_and_continues(
    step13_runtime_case,
):
    bundle = _make_ports(step13_runtime_case, posts_per_search=5)
    started = threading.Event()
    cancelled = threading.Event()
    original_run = bundle.runner.run

    def blocked_run(request):
        started.set()
        cancelled.wait(timeout=5)
        return original_run(request)

    def cancel(run_id: str, request_id: str) -> bool:
        bundle.cancellations.append(run_id)
        cancelled.set()
        return True

    bundle.runner.run = blocked_run
    bundle.ports = replace(bundle.ports, cancel_ai_request=cancel)

    def advance_clock():
        assert started.wait(timeout=3), "owned AI-O request did not start"
        step13_runtime_case.clock.advance(480)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    outcome = _run(step13_runtime_case, bundle, run_id="RUN-R10-N20K")

    assert outcome.committed_count > 0
    assert len(bundle.room.requests) > 1
    assert len(bundle.runner.requests) > 1
    assert bundle.cancellations == ["RUN-R10-N20K"]
    generation = step13_runtime_case.connection.execute(
        "SELECT outcome,error_code FROM generation_attempt "
        "WHERE run_id=? ORDER BY rowid LIMIT 1",
        ("RUN-R10-N20K",),
    ).fetchone()
    assert tuple(generation) == ("error", "timed_out")
    checkpoint = step13_runtime_case.connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", ("RUN-R10-N20K",)
    ).fetchone()[0]
    state = json.loads(checkpoint)["daily_orchestration"]
    assert state["source_stopped"] is False
    assert {"phase": "copy", "code": "ai_request_timeout"} in state["errors"]


def test_n20l_production_request_cancellers_preserve_run_for_later_work(tmp_path):
    source = PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=Path(sys.executable),
            evidence_root=tmp_path / "observations",
            environment="fixture",
            fixture_url="http://127.0.0.1/",
        )
    )
    current_process = object()
    other_process = object()
    current_token = threading.Event()
    other_token = threading.Event()
    source._active = {
        current_process: "RUN-SCOPE",
        other_process: "OTHER-RUN",
    }
    source._active_request_tokens = {
        current_process: ("RUN-SCOPE", "ATTEMPT-SCOPE"),
        other_process: ("OTHER-RUN", "OTHER-ATTEMPT"),
    }
    source._pending_requests = {
        ("RUN-SCOPE", "ATTEMPT-SCOPE"): current_token,
        ("OTHER-RUN", "OTHER-ATTEMPT"): other_token,
    }
    source._terminate_tree = Mock(return_value=True)

    assert source.cancel_active_request("RUN-SCOPE", "ATTEMPT-SCOPE") is True
    source._terminate_tree.assert_called_once_with(current_process)
    assert "RUN-SCOPE" not in source._cancelled_runs
    assert current_token.is_set()
    assert not other_token.is_set()

    class Session:
        def __init__(self):
            self.is_closed = False
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            self.is_closed = True

    root = object.__new__(DailyAIRootRunner)
    root.context = {"run_id": "RUN-SCOPE"}
    root.session = Session()
    root._guard = threading.Lock()
    root._active_job_lock = threading.Lock()
    root._active_job_run_id = "RUN-SCOPE"
    root._active_job_id = "JOB-SCOPE"
    root._active_job_cancel_event = threading.Event()
    root._retained_dirs = {tmp_path / "jobs" / "active-request"}
    retained = set(root._retained_dirs)

    assert root.cancel_active_request("RUN-SCOPE", "JOB-SCOPE") is True
    assert root.session.close_calls == 1
    assert root._active_job_cancel_event.is_set()
    assert root.context["run_id"] == "RUN-SCOPE"
    assert root._retained_dirs == retained


def test_n20m_ai_cancel_confirmation_is_reset_for_each_job():
    clock = FakeClock(datetime.fromisoformat(NOW))
    started = threading.Event()
    cancelled = threading.Event()
    cancel_calls = []

    class Runner:
        def run(self, request):
            if request.job_id == "JOB-FIRST":
                started.set()
                cancelled.wait(timeout=3)
                return SimpleNamespace(result_status="timed_out")
            return SimpleNamespace(result_status="timed_out")

    def cancel(request):
        cancel_calls.append("JOB-FIRST")
        cancelled.set()
        return True

    wrapped = daily._DeadlineCopyRunner(Runner(), clock=clock, cancel=cancel)

    def request(job_id: str, deadline: float):
        return SimpleNamespace(
            job_id=job_id,
            run_id="RUN-N20M",
            attempt=1,
            task_id="TASK-N20M",
            target_date=TARGET_DATE,
            settings_version=SETTINGS_VERSION,
            input_refs=("INPUT-N20M",),
            evidence_refs=(),
            deadline_at=NOW,
            deadline_monotonic=deadline,
        )

    def advance_clock():
        assert started.wait(timeout=3), "first AI job did not start"
        clock.advance(8)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    first = wrapped.run(request("JOB-FIRST", clock.monotonic() + 8))
    assert first.result_status == "timed_out"
    assert wrapped.last_cancel_confirmed is True

    second = wrapped.run(request("JOB-SECOND", clock.monotonic() + 8))
    assert second.result_status == "timed_out"
    assert wrapped.last_cancel_confirmed is None
    assert cancel_calls == ["JOB-FIRST"]


def test_n20p_ai_cancel_confirmation_is_job_scoped_after_request_identity_change():
    clock = FakeClock(datetime.fromisoformat(NOW))
    started = threading.Event()
    cancelled = threading.Event()
    cancel_calls = []

    class Runner:
        def run(self, request):
            if request.job_id == "JOB-FIRST":
                started.set()
                cancelled.wait(timeout=3)
            return SimpleNamespace(result_status="timed_out")

    def cancel(request):
        cancel_calls.append(request.job_id)
        cancelled.set()
        return True

    wrapped = daily._DeadlineCopyRunner(Runner(), clock=clock, cancel=cancel)

    def request(job_id: str, deadline: float):
        return SimpleNamespace(
            job_id=job_id,
            run_id="RUN-N20P",
            attempt=1,
            task_id="TASK-N20P",
            target_date=TARGET_DATE,
            settings_version=SETTINGS_VERSION,
            input_refs=("INPUT-N20P",),
            evidence_refs=(),
            deadline_at=NOW,
            deadline_monotonic=deadline,
        )

    def advance_clock():
        assert started.wait(timeout=3), "first AI job did not start"
        clock.advance(8)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    first = wrapped.run(request("JOB-FIRST", clock.monotonic() + 8))
    assert first.result_status == "timed_out"
    assert wrapped.last_cancel_confirmed is True
    second = wrapped.run(request("JOB-SECOND", clock.monotonic() + 8))
    assert second.result_status == "timed_out"
    assert wrapped.last_cancel_confirmed is None
    assert cancel_calls == ["JOB-FIRST"]


def test_n20n_unconfirmed_ai_cancel_stops_before_next_offer(step13_runtime_case):
    bundle = _make_ports(step13_runtime_case, posts_per_search=5)
    started = threading.Event()
    released = threading.Event()
    original_run = bundle.runner.run

    def blocked_first_run(request):
        if not started.is_set():
            started.set()
            released.wait(timeout=5)
            return original_run(request)
        return original_run(request)

    def unconfirmed_cancel(run_id: str, request_id: str) -> bool:
        bundle.cancellations.append(run_id)
        released.set()
        return False

    bundle.runner.run = blocked_first_run
    bundle.ports = replace(bundle.ports, cancel_ai_request=unconfirmed_cancel)

    def advance_clock():
        assert started.wait(timeout=3), "owned AI-O request did not start"
        step13_runtime_case.clock.advance(480)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    _run(step13_runtime_case, bundle, run_id="RUN-R10-N20N")

    assert len(bundle.runner.requests) == 1
    assert bundle.cancellations == ["RUN-R10-N20N"]
    checkpoint = step13_runtime_case.connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", ("RUN-R10-N20N",)
    ).fetchone()[0]
    state = json.loads(checkpoint)["daily_orchestration"]
    assert state["source_stopped"] is True
    assert state["source_stopped_reason"] == "ai_request_timeout_cancel_failed"


def test_n20q_unconfirmed_job_cancel_does_not_start_next_offer(step13_runtime_case):
    bundle = _make_ports(step13_runtime_case, posts_per_search=5)
    started = threading.Event()
    released = threading.Event()
    original_run = bundle.runner.run

    def blocked_first_run(request):
        if not started.is_set():
            started.set()
            released.wait(timeout=5)
        return original_run(request)

    def unconfirmed_cancel(run_id: str, job_id: str) -> bool:
        bundle.cancellations.append((run_id, job_id))
        released.set()
        return False

    bundle.runner.run = blocked_first_run
    bundle.ports = replace(bundle.ports, cancel_ai_request=unconfirmed_cancel)

    def advance_clock():
        assert started.wait(timeout=3), "owned AI-O request did not start"
        step13_runtime_case.clock.advance(480)

    clock_driver = threading.Thread(target=advance_clock, daemon=True)
    clock_driver.start()
    _run(step13_runtime_case, bundle, run_id="RUN-R10-N20Q")

    assert len(bundle.runner.requests) == 1
    assert bundle.cancellations == [("RUN-R10-N20Q", bundle.runner.requests[0].job_id)]
    checkpoint = step13_runtime_case.connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", ("RUN-R10-N20Q",)
    ).fetchone()[0]
    state = json.loads(checkpoint)["daily_orchestration"]
    assert state["source_stopped"] is True
    assert state["source_stopped_reason"] == "ai_request_timeout_cancel_failed"


def test_n20o_cancelled_queued_room_request_never_launches(tmp_path):
    source = PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=Path(sys.executable),
            evidence_root=tmp_path / "observations",
            environment="fixture",
            fixture_url="http://127.0.0.1/",
        )
    )
    request = SimpleNamespace(
        run_id="RUN-N20O",
        attempt_id="ATTEMPT-N20O",
        deadline_at=NOW,
    )
    result = []
    source._serial.acquire()
    worker = threading.Thread(
        target=lambda: result.append(source._invoke("room", request)), daemon=True
    )
    worker.start()
    try:
        key = (request.run_id, request.attempt_id)
        for _ in range(300):
            with source._lock:
                pending = key in source._pending_requests
            if pending:
                break
            threading.Event().wait(0.01)
        assert pending, "ROOM request was not registered before serialization wait"
        with patch("automation.inago.live_sources.subprocess.Popen") as popen:
            assert (
                source.cancel_active_request(request.run_id, request.attempt_id) is True
            )
            source._serial.release()
            worker.join(timeout=3)
            assert not worker.is_alive(), "cancelled ROOM request did not exit"
            popen.assert_not_called()
    finally:
        if source._serial.locked():
            source._serial.release()
    assert result == [{"error_code": "source_cancelled"}]


def test_n20r_cancel_targets_current_queued_room_request(tmp_path):
    source = PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=Path(sys.executable),
            evidence_root=tmp_path / "observations",
            environment="fixture",
            fixture_url="http://127.0.0.1/",
        )
    )
    requests = [
        SimpleNamespace(
            run_id="RUN-N20O",
            attempt_id=f"ATTEMPT-N20O-{index}",
            deadline_at=NOW,
        )
        for index in (1, 2)
    ]
    results = {}
    source._serial.acquire()

    def invoke(request):
        results[request.attempt_id] = source._invoke("room", request)

    def wait_until_registered(request):
        key = (request.run_id, request.attempt_id)
        for _ in range(300):
            with source._lock:
                pending = key in source._pending_requests
            if pending:
                return
            threading.Event().wait(0.01)
        raise AssertionError(
            "ROOM request was not registered before serialization wait"
        )

    workers = [
        threading.Thread(target=invoke, args=(request,), daemon=True)
        for request in requests
    ]
    try:
        workers[0].start()
        wait_until_registered(requests[0])
        assert (
            source.cancel_active_request(requests[0].run_id, requests[0].attempt_id)
            is True
        )
        workers[1].start()
        wait_until_registered(requests[1])
        with patch("automation.inago.live_sources.subprocess.Popen") as popen:
            assert (
                source.cancel_active_request(requests[1].run_id, requests[1].attempt_id)
                is True
            )
            source._serial.release()
            for worker in workers:
                worker.join(timeout=3)
                assert not worker.is_alive(), "cancelled ROOM request did not exit"
            popen.assert_not_called()
    finally:
        if source._serial.locked():
            source._serial.release()

    assert results == {
        request.attempt_id: {"error_code": "source_cancelled"} for request in requests
    }


def test_n20s_cancel_before_room_worker_registration_leaves_tombstone(tmp_path):
    source = PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=Path(sys.executable),
            evidence_root=tmp_path / "observations",
            environment="fixture",
            fixture_url="http://127.0.0.1/",
        )
    )
    request = SimpleNamespace(
        run_id="RUN-N20S",
        attempt_id="ATTEMPT-N20S",
        deadline_at=NOW,
    )

    assert source.cancel_active_request(request.run_id, request.attempt_id) is True
    with patch("automation.inago.live_sources.subprocess.Popen") as popen:
        result = source._invoke("room", request)
        popen.assert_not_called()

    assert result == {"error_code": "source_cancelled"}
    assert (request.run_id, request.attempt_id) in source._cancelled_requests


def test_n20c_primary_error_precedence_and_resolved_diagnostics(tmp_path):
    database = _new_database(tmp_path / "R10" / "N20c" / "state.sqlite")
    checkpoint, state = _fixture_state()
    state["error_count"] = 2
    daily._record_orchestration_error(
        state,
        daily._CopyContractRejected(
            run_id="RUN-R10",
            offer_key="shop-n20:item001",
            error_code="copy_runner_contract_rejected",
            cause_code="input_hash",
            artifact_ref="ARTIFACT-R10",
        ),
    )
    checkpoint["daily_orchestration"] = state
    _insert_running_run(
        database,
        status="failed",
        committed_count=12,
        checkpoint=json.dumps(checkpoint, ensure_ascii=False),
    )

    failed_row = database.execute("SELECT * FROM run WHERE run_id='RUN-R10'").fetchone()
    failed_outcome = daily._outcome_from_run(failed_row, connection=database)
    assert failed_outcome.primary_error == {
        "phase": "copy",
        "code": "copy_runner_contract_rejected",
        "references": {
            "run_id": "RUN-R10",
            "offer_key": "shop-n20:item001",
            "cause_code": "input_hash",
            "artifact_ref": "ARTIFACT-R10",
        },
    }
    assert failed_outcome.secondary_errors == (
        {"phase": "copy", "code": "copy_generation_failed"},
    )

    state["error_count"] = 0
    checkpoint["daily_orchestration"] = state
    database.execute(
        "UPDATE run SET status='complete', committed_count=15, checkpoint=? "
        "WHERE run_id='RUN-R10'",
        (json.dumps(checkpoint, ensure_ascii=False),),
    )
    recovered_row = database.execute(
        "SELECT * FROM run WHERE run_id='RUN-R10'"
    ).fetchone()
    recovered_outcome = daily._outcome_from_run(recovered_row, connection=database)
    assert recovered_outcome.primary_error is None
    assert recovered_outcome.secondary_errors == (
        {"phase": "copy", "code": "copy_generation_failed"},
        failed_outcome.primary_error,
    )
    database.close()


def test_n20b_remove_run_deadlines_preserve_v5_values_and_keep_request_timeout(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "R10" / "N20b" / "state.sqlite"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(database_path, isolation_level=None)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(repository, "CURRENT_SCHEMA_VERSION", 5)
    repository.initialize_database(database)
    _insert_settings(database)
    old_draft_deadline = f"{TARGET_DATE}T17:45:00+09:00"
    old_end_deadline = f"{TARGET_DATE}T18:00:00+09:00"
    _insert_running_run(
        database,
        status="partial",
        draft_deadline=old_draft_deadline,
        end_deadline=old_end_deadline,
        committed_count=12,
    )
    monkeypatch.setattr(repository, "CURRENT_SCHEMA_VERSION", 6)
    assert repository.initialize_database(database) == 6
    assert database.execute("PRAGMA user_version").fetchone()[0] == 6
    migrated = database.execute("SELECT * FROM run WHERE run_id='RUN-R10'").fetchone()
    assert migrated["draft_deadline"] == old_draft_deadline
    assert migrated["end_deadline"] == old_end_deadline

    snapshot = {
        "run_id": "RUN-R10",
        "target_date": TARGET_DATE,
        "settings_version": SETTINGS_VERSION,
        "seed": "R10-SEED",
        "search_attempts": 3,
        "unfinished_generation_attempts": {"shop-n20:item001": 1},
        "committed_count": 12,
        "run_status": "partial",
        "metrics_pending": True,
    }
    after_cutoffs = datetime.fromisoformat(f"{TARGET_DATE}T18:01:00+09:00")
    decision = plan_resume(
        snapshot,
        now=after_cutoffs,
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    assert decision["action"] == "resume_remaining"
    assert decision["allowed"]["search"] is True
    assert decision["allowed"]["generation"] is True
    assert decision["allowed"]["metrics"] is True

    class Metrics:
        timeout_seconds = 15

        def __init__(self):
            self.deadline_at = None

        def collect(self, _connection, *, run_id, deadline_at):
            assert run_id == "RUN-R10"
            self.deadline_at = deadline_at
            return "observed"

    metrics = Metrics()
    ports = DailyPorts(
        current_settings_version=lambda _connection: SETTINGS_VERSION,
        theme_planner=None,
        room_port=None,
        copy_spec_factory=None,
        copy_runner=None,
        metrics_port=metrics,
        cancel_owned_jobs=lambda _run_id: True,
        cancel_metrics_job=lambda _run_id: True,
        fixture_only=True,
    )
    clock = FakeClock(after_cutoffs)
    state = daily._daily_state(migrated["checkpoint"])[1]
    metrics_status = daily._collect_metrics(
        database,
        run_id="RUN-R10",
        target_date=TARGET_DATE,
        state=state,
        ports=ports,
        clock=clock,
        data_root=tmp_path / "R10" / "N20b",
    )
    assert metrics_status == "observed"
    assert (
        datetime.fromisoformat(metrics.deadline_at) - after_cutoffs
    ).total_seconds() == 15

    ports_for_create = SimpleNamespace(
        current_settings_version=lambda _connection: SETTINGS_VERSION
    )
    next_day = "2026-10-17"
    new_run = daily._create_run(
        database,
        target_date=next_day,
        now=FakeClock(datetime.fromisoformat(f"{next_day}T13:00:00+09:00")).now_jst(),
        ports=ports_for_create,
        run_id_factory=lambda _date: "RUN-R10-NEW",
        seed_factory=lambda: "R10-NEW-SEED",
    )
    assert new_run["target_date"] == next_day
    assert new_run["draft_deadline"] is None
    assert new_run["end_deadline"] is None
    database.close()


def test_n20d_v5_migration_preserves_run_child_rows(tmp_path, monkeypatch):
    database_path = tmp_path / "R10" / "N20d" / "state.sqlite"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(database_path, isolation_level=None)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(repository, "CURRENT_SCHEMA_VERSION", 5)
    repository.initialize_database(database)
    _insert_settings(database)
    _insert_running_run(
        database,
        status="partial",
        draft_deadline=f"{TARGET_DATE}T17:45:00+09:00",
        end_deadline=f"{TARGET_DATE}T18:00:00+09:00",
        committed_count=12,
    )
    database.execute(
        "INSERT INTO search_attempt "
        "(attempt_id,run_id,query,parent_refs_json,level,attempt_no,started_at,"
        "ended_at,source_status,source_order,max_posts,retry_of,error_code) "
        "VALUES ('SEARCH-N20D','RUN-R10','theme','[]','parent',1,?,?,"
        "'observed','internal',50,NULL,NULL)",
        (NOW, NOW),
    )
    monkeypatch.setattr(repository, "CURRENT_SCHEMA_VERSION", 6)

    assert repository.initialize_database(database) == 6

    preserved = database.execute(
        "SELECT run_id,attempt_no FROM search_attempt WHERE attempt_id='SEARCH-N20D'"
    ).fetchone()
    assert tuple(preserved) == ("RUN-R10", 1)
    assert tuple(
        database.execute(
            "SELECT draft_deadline,end_deadline FROM run WHERE run_id='RUN-R10'"
        ).fetchone()
    ) == (
        f"{TARGET_DATE}T17:45:00+09:00",
        f"{TARGET_DATE}T18:00:00+09:00",
    )
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    assert database.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    database.close()


def test_n20e_completed_run_resumes_only_pending_metrics_after_1800(tmp_path):
    data_root = tmp_path / "R10" / "N20e"
    database = _new_database(data_root / "state.sqlite")
    checkpoint, state = daily._daily_state(None)
    state.update(
        phase="finished",
        terminal_status="complete",
        metrics_status="pending",
        metrics_started_at=f"{TARGET_DATE}T17:59:00+09:00",
        error_count=0,
        errors=[],
    )
    checkpoint["daily_orchestration"] = state
    _insert_running_run(
        database,
        status="complete",
        committed_count=15,
        checkpoint=json.dumps(checkpoint, ensure_ascii=False),
    )
    database.execute(
        "UPDATE run SET metrics_status='pending',finished_at=? WHERE run_id='RUN-R10'",
        (f"{TARGET_DATE}T17:59:00+09:00",),
    )
    original_finished_at = database.execute(
        "SELECT finished_at FROM run WHERE run_id='RUN-R10'"
    ).fetchone()[0]

    class Metrics:
        timeout_seconds = 15

        def __init__(self):
            self.calls = []

        def collect(self, connection, *, run_id, deadline_at):
            self.calls.append((run_id, deadline_at))
            assert tuple(
                connection.execute(
                    "SELECT status,committed_count FROM run WHERE run_id=?", (run_id,)
                ).fetchone()
            ) == ("complete", 15)
            return "observed"

    class Forbidden:
        def search(self, _request):
            raise AssertionError("completed-run metrics resume replayed ROOM")

    class ForbiddenCopy:
        def run(self, _request):
            raise AssertionError("completed-run metrics resume replayed COPY")

    metrics = Metrics()
    cancelled = []

    def cancel_metrics(run_id):
        cancelled.append(run_id)
        return True

    ports = DailyPorts(
        current_settings_version=lambda _connection: SETTINGS_VERSION,
        theme_planner=None,
        room_port=Forbidden(),
        copy_spec_factory=None,
        copy_runner=ForbiddenCopy(),
        metrics_port=metrics,
        cancel_owned_jobs=lambda _run_id: True,
        cancel_metrics_job=cancel_metrics,
        fixture_only=True,
    )
    clock = FakeClock(datetime.fromisoformat(f"{TARGET_DATE}T18:01:00+09:00"))

    outcome = daily.run_daily(
        database,
        data_root=data_root,
        ports=ports,
        clock=clock,
    )

    assert outcome.status == "complete"
    assert outcome.metrics_status == "observed"
    assert outcome.resumed is True
    assert outcome.committed_count == 15
    assert cancelled == ["RUN-R10"]
    assert metrics.calls == [("RUN-R10", f"{TARGET_DATE}T18:01:15+09:00")]
    persisted = database.execute(
        "SELECT status,metrics_status,finished_at,attempt_used,committed_count "
        "FROM run WHERE run_id='RUN-R10'"
    ).fetchone()
    assert tuple(persisted) == ("complete", "observed", original_finished_at, 0, 15)
    database.close()


def test_n21_result_exit_precedence_and_windows_child_exit_propagation(tmp_path):
    successful_result = {
        "status": "complete",
        "daily_status": "complete",
        "metrics_status": "observed",
        "export": {"status": "ready"},
        "preservation": {
            "status": "ready",
            "end_backup": {"status": "ready"},
            "monthly_restore": {"status": "already_checked"},
        },
    }
    assert cli.production_daily_exit_code(successful_result) == 0
    assert cli.production_daily_exit_code({"status": "not_due"}) == 0
    assert cli.production_daily_exit_code({"status": "partial"}) == 5
    assert cli.production_daily_exit_code({"status": "shortfall"}) == 5
    assert cli.production_daily_exit_code({"status": "failed"}) == 4
    assert cli.production_daily_exit_code({"status": "locked"}) == 4
    assert (
        cli.production_daily_exit_code(
            {**successful_result, "metrics_status": "unavailable"}
        )
        == 4
    )
    assert (
        cli.production_daily_exit_code(
            {**successful_result, "export": {"status": "failed"}}
        )
        == 4
    )
    assert (
        cli.production_daily_exit_code(
            {**successful_result, "preservation": {"status": "failed"}}
        )
        == 4
    )
    assert (
        cli.production_daily_exit_code(
            {
                **successful_result,
                "primary_error": {"phase": "copy", "code": "contract_rejected"},
            }
        )
        == 4
    )

    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None, "Windows PowerShell is required for N21"
    project = tmp_path / "wrapper-project"
    (project / "automation" / "inago").mkdir(parents=True)
    (project / "automation" / "__init__.py").write_text("", encoding="utf-8")
    (project / "automation" / "inago" / "__init__.py").write_text("", encoding="utf-8")
    (project / "automation" / "inago" / "cli.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "raise SystemExit(int(Path('child-exit').read_text(encoding='utf-8')))\n",
        encoding="utf-8",
    )
    launcher_dir = project / "automation" / "inago" / "maintenance"
    launcher_dir.mkdir()
    (launcher_dir / "__init__.py").write_text("", encoding="utf-8")
    (launcher_dir / "daily_launcher.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(int(Path('child-exit').read_text(encoding='utf-8')))\n",
        encoding="utf-8",
    )
    wrapper = Path(__file__).resolve().parents[1] / "automation" / "inago" / "run.ps1"
    shutil.copy2(wrapper, project / "automation" / "inago" / "run.ps1")
    venv.EnvBuilder(with_pip=False, clear=True).create(project / ".venv-inago")
    runtime_root = project / "rakuten-room-inago" / "runtime"
    (runtime_root / "config").mkdir(parents=True)
    (runtime_root / "config" / "settings.json").write_text("{}", encoding="utf-8")
    for exit_code in (4, 5):
        (project / "child-exit").write_text(str(exit_code), encoding="utf-8")
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(project / "automation" / "inago" / "run.ps1"),
                "daily",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == exit_code, completed.stderr


def test_n21b_fixture_exit_code_separates_unmet_from_processing_errors():
    partial = daily.DailyRunOutcome(
        status="partial",
        run_id="RUN-R10-FIXTURE",
        target_date=TARGET_DATE,
        committed_count=12,
        attempt_used=3,
        metrics_status="observed",
        shortfall_count=3,
    )
    shortfall = daily.DailyRunOutcome(
        status="shortfall",
        run_id="RUN-R10-FIXTURE",
        target_date=TARGET_DATE,
        committed_count=0,
        attempt_used=0,
        metrics_status="observed",
        shortfall_count=15,
    )
    failed_partial = daily.DailyRunOutcome(
        status="partial",
        run_id="RUN-R10-FIXTURE",
        target_date=TARGET_DATE,
        committed_count=12,
        attempt_used=3,
        metrics_status="observed",
        shortfall_count=3,
        error_count=1,
        primary_error={"phase": "copy", "code": "contract_rejected"},
    )
    complete = daily.DailyRunOutcome(
        status="complete",
        run_id="RUN-R10-FIXTURE",
        target_date=TARGET_DATE,
        committed_count=15,
        attempt_used=3,
        metrics_status="observed",
    )

    assert cli.daily_run_exit_code(partial) == cli.EXIT_DAILY_UNMET
    assert cli.daily_run_exit_code(shortfall) == cli.EXIT_DAILY_UNMET
    assert cli.daily_run_exit_code(failed_partial) == cli.EXIT_RUNTIME_ERROR
    assert cli.daily_run_exit_code(complete) == 0


def test_n21c_fixture_metrics_error_precedes_daily_shortfall():
    partial_metrics_failure = daily.DailyRunOutcome(
        status="partial",
        run_id="RUN-R10-FIXTURE",
        target_date=TARGET_DATE,
        committed_count=12,
        attempt_used=3,
        metrics_status="unavailable",
        shortfall_count=3,
    )
    partial_with_observed_metrics = daily.DailyRunOutcome(
        status="partial",
        run_id="RUN-R10-FIXTURE",
        target_date=TARGET_DATE,
        committed_count=12,
        attempt_used=3,
        metrics_status="observed",
        shortfall_count=3,
    )

    assert cli.daily_run_exit_code(partial_metrics_failure) == cli.EXIT_RUNTIME_ERROR
    assert (
        cli.daily_run_exit_code(partial_with_observed_metrics) == cli.EXIT_DAILY_UNMET
    )
