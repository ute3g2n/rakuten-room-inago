"""R06-only ROOM condition popup and retry contract cases."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess

from automation.inago.clock import FakeClock
from automation.inago import orchestrator as daily
from automation.inago.repository import initialize_database
from automation.inago.orchestrator import DailyPorts
from tests_inago import test_step21_browser as browser_fixtures
from tests_inago.test_step21_browser import room_request, source, ui_server


SETTINGS_VERSION = "R06-fixture-settings"
TARGET_DATE = "2026-10-16"
NOW = f"{TARGET_DATE}T13:00:00+09:00"


def _insert_isolated_run(root: Path, run_id: str) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "state.sqlite", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    initialize_database(connection)
    connection.execute(
        "INSERT INTO settings_version "
        "(version,confirmed_at,genres_json,themes_json,pattern_refs_json,change_reason,created_at) "
        "VALUES (?, ?, '[]', '[]', '[]', ?, ?)",
        (SETTINGS_VERSION, NOW, "isolated R06 fixture", NOW),
    )
    connection.execute(
        "INSERT INTO run (run_id,target_date,mode,status,settings_version,seed,started_at,"
        "draft_deadline,end_deadline,attempt_used,committed_count,checkpoint,metrics_status) "
        "VALUES (?, ?, 'daily', 'running', ?, 'R06-SEED', ?, ?, ?, 0, 0, NULL, 'pending')",
        (
            run_id,
            TARGET_DATE,
            SETTINGS_VERSION,
            NOW,
            "2099-10-16T17:45:00+09:00",
            "2099-10-16T18:00:00+09:00",
        ),
    )
    return connection


def _empty_results_room_html(query: str) -> str:
    html = browser_fixtures.room_html(0, initial=0, terminal=True, query=query)
    return html.replace(
        '<div id="end" style="display:none">検索結果は以上です</div>',
        '<div id="end" style="display:block">検索結果がありません</div>',
    )


def _uncloseable_room_html(query: str) -> str:
    html = browser_fixtures.room_html(0, initial=0, terminal=True, query=query)
    close_control = '<a class="close" onclick="this.parentElement.style.display=\'none\'">閉じる</a>'
    return html.replace(close_control, "")


class BrowserRoomSequence:
    fixture_only = True

    def __init__(self, data_root: Path, ui_server, fail_attempts: set[int]) -> None:
        self.data_root = data_root
        self.ui_server = ui_server
        self.fail_attempts = fail_attempts
        self.requests = []

    def search(self, request):
        self.requests.append(request)
        self.ui_server["html"] = (
            _uncloseable_room_html(request.query)
            if request.attempt_no in self.fail_attempts
            else _empty_results_room_html(request.query)
        )
        return source(
            self.data_root / f"attempt-{request.attempt_no}",
            self.ui_server,
            timeout=2,
        ).search(request)


def _room_ports(room_port) -> DailyPorts:
    def current_settings_version(connection):
        return SETTINGS_VERSION

    def cancel_owned_jobs(_run_id):
        return True

    def cancel_metrics_job(_run_id):
        return True

    current_settings_version.fixture_only = True
    cancel_owned_jobs.fixture_only = True
    cancel_metrics_job.fixture_only = True
    return DailyPorts(
        current_settings_version=current_settings_version,
        theme_planner=None,
        room_port=room_port,
        copy_spec_factory=None,
        copy_runner=None,
        metrics_port=None,
        cancel_owned_jobs=cancel_owned_jobs,
        cancel_metrics_job=cancel_metrics_job,
        fixture_only=True,
    )


def _room_task() -> dict[str, object]:
    return {
        "task_number": 1,
        "query": "収納用品",
        "parent_refs": ["THEME-R06"],
        "level": "theme_fallback",
        "theme_id": "THEME-R06",
        "query_kind": "theme_fallback",
        "keyword_snapshot_ref": None,
        "selected_word_ref": None,
    }


def _delayed_close_room_html() -> str:
    html = browser_fixtures.room_html(1, initial=1, terminal=True)
    toggle = (
        '<div class="search-detail-wrap" onclick="const popup=document.querySelector('
        "'.itemSearch-popup');popup.style.display=popup.style.display==='block'?"
        "'none':'block'\">詳細検索</div>"
    )
    delayed_toggle = (
        '<div class="search-detail-wrap" onclick="fetch(\'/condition-toggle-used\');'
        "const popup=document.querySelector('.itemSearch-popup');"
        "if(popup.style.display!=='block')popup.style.display='block'"
        '">詳細検索</div>'
    )
    html = html.replace(toggle, delayed_toggle)
    close_control = '<a class="close" onclick="this.parentElement.style.display=\'none\'">閉じる</a>'
    delayed_close_control = (
        '<a class="close" onclick="fetch(\'/condition-close-control-used\');'
        "setTimeout(()=>this.parentElement.style.display='none',1800)"
        '">閉じる</a>'
    )
    return html.replace(close_control, delayed_close_control)


def _ambiguous_close_room_html() -> str:
    html = browser_fixtures.room_html(1, initial=1, terminal=True)
    close_control = '<a class="close" onclick="this.parentElement.style.display=\'none\'">閉じる</a>'
    duplicate_control = (
        '<button type="button" onclick="fetch(\'/wrong-close-control-used\')">'
        "閉じる</button>"
    )
    html = html.replace(close_control, duplicate_control + close_control)
    return html.replace(
        "</script>",
        "</script><script>document.addEventListener('keydown',event=>{if(event.key==='Escape'){"
        "document.querySelector('.itemSearch-popup').style.display='none';"
        "fetch('/escape-close-used')}});</script>",
    )


def test_n09(tmp_path, ui_server):
    """An explicit close control may settle after 1.5 seconds without losing conditions."""

    ui_server["html"] = _delayed_close_room_html()
    request = replace(room_request(), attempt_id="R06-N09-A1", run_id="R06-N09-RUN")

    response = source(tmp_path, ui_server, timeout=8).search(request)

    assert response["status"] == "observed", response.get("error_code")
    observation_path = next(
        (tmp_path / "observations").glob("room-R06-N09-A1-observation-*.json")
    )
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    events = [
        event
        for event in observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    stages = [event["stage"] for event in events]
    assert stages.index("room_condition_popup_closed") < stages.index(
        "room_results_scan_begin"
    )
    close_control = next(
        event
        for event in events
        if event["stage"] == "room_condition_close_control_complete"
    )
    assert close_control["room_condition_close_method"] == "close_control"
    assert close_control["room_condition_popup_state"] == "hidden"
    assert close_control["room_condition_close_control_count"] == 1
    verified = next(
        event for event in events if event["stage"] == "room_conditions_verified"
    )
    assert verified["room_conditions_likes_verified"] is True
    assert verified["room_conditions_comments_verified"] is True
    assert ui_server["requests"].count("/condition-toggle-used") == 1
    assert ui_server["requests"].count("/condition-close-control-used") == 1


def test_n09b_already_closed_and_ambiguous_controls(tmp_path, ui_server):
    """Closed state is idempotent; ambiguous close controls use one Escape fallback."""

    module_url = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "inago"
        / "adapters"
        / "public_sources.mjs"
    ).as_uri()
    script = (
        f'import("{module_url}").then(async (module) => {{ '
        "const result = await module.closeConditionPopup(null, "
        "{isVisible: async () => false}, Date.now() + 1000); "
        "process.stdout.write(JSON.stringify(result)); "
        "}).catch(error => { process.stderr.write(error.name); process.exitCode = 1; });"
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "closed": True,
        "method": "already_closed",
        "closeControlCount": 0,
    }

    attempt_id = "R06-N09B-AMBIGUOUS"
    ui_server["html"] = _ambiguous_close_room_html()
    request = replace(room_request(), attempt_id=attempt_id, run_id=f"{attempt_id}-RUN")
    response = source(tmp_path / attempt_id, ui_server, timeout=8).search(request)
    assert response["status"] == "observed", response.get("error_code")
    observation_path = next(
        (tmp_path / attempt_id / "observations").glob(
            f"room-{attempt_id}-observation-*.json"
        )
    )
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    assert observation["trace_state"] == "healthy"
    stages = [
        event
        for event in observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    ambiguous = next(
        event
        for event in stages
        if event["stage"] == "room_condition_close_control_ambiguous"
    )
    assert ambiguous["room_condition_close_control_count"] == 2
    closed = next(
        event
        for event in stages
        if event["stage"] == "room_condition_escape_close_complete"
    )
    assert closed["room_condition_close_method"] == "escape"
    assert ui_server["requests"].count("/escape-close-used") == 1
    assert "/wrong-close-control-used" not in ui_server["requests"]

    common_trace_path = tmp_path / attempt_id / observation["trace_ref"]
    common_trace = [
        json.loads(line)
        for line in common_trace_path.read_text(encoding="utf-8").splitlines()
    ]
    common_close = next(
        event
        for event in common_trace
        if event.get("operation") == "room_condition_popup_closed"
    )
    assert common_close["room_condition_popup_state"] == "hidden"
    assert common_close["room_conditions_likes_verified"] is True
    assert common_close["room_conditions_comments_verified"] is True


def test_n10(tmp_path, ui_server):
    """Close failure retries the same task, preserves history, and stops after three tries."""

    task = _room_task()
    recovery_root = tmp_path / "recovery"
    recovery_run_id = "R06-N10-RECOVERY"
    recovery_connection = _insert_isolated_run(recovery_root, recovery_run_id)
    recovery_room = BrowserRoomSequence(
        recovery_root / "public-source", ui_server, fail_attempts={1}
    )
    recovery_clock = FakeClock(datetime.fromisoformat(NOW))
    recovery_state = daily._daily_state(None)[1]
    recovery_state["tasks"] = [task]
    try:
        daily._search_task(
            recovery_connection,
            run_id=recovery_run_id,
            task=task,
            settings_version=SETTINGS_VERSION,
            seed="R06-SEED",
            target_date=TARGET_DATE,
            state=recovery_state,
            ports=_room_ports(recovery_room),
            clock=recovery_clock,
            data_root=recovery_root,
        )
        recovery_rows = recovery_connection.execute(
            "SELECT attempt_no,retry_of,source_status,error_code FROM search_attempt "
            "WHERE run_id=? ORDER BY attempt_no",
            (recovery_run_id,),
        ).fetchall()
        assert [tuple(row) for row in recovery_rows] == [
            (1, None, "unavailable", "condition_popup_close_failed"),
            (2, recovery_room.requests[0].attempt_id, "observed", None),
        ]
        assert recovery_rows[1][1] == daily._task_attempt_id(recovery_run_id, 1, 1)
        assert [request.retry_of for request in recovery_room.requests] == [
            None,
            daily._task_attempt_id(recovery_run_id, 1, 1),
        ]
        assert recovery_state["source_failures"] == 1
        assert recovery_state["source_stopped"] is False
        assert recovery_state["processed_attempts"] == [
            daily._task_attempt_id(recovery_run_id, 1, 1),
            daily._task_attempt_id(recovery_run_id, 1, 2),
        ]
        assert (
            recovery_connection.execute(
                "SELECT attempt_used FROM run WHERE run_id=?", (recovery_run_id,)
            ).fetchone()[0]
            == 2
        )
    finally:
        recovery_connection.close()

    failed_root = tmp_path / "three-failures"
    failed_run_id = "R06-N10-THREE-FAILURES"
    failed_connection = _insert_isolated_run(failed_root, failed_run_id)
    failed_room = BrowserRoomSequence(
        failed_root / "public-source", ui_server, fail_attempts={1, 2, 3}
    )
    failed_clock = FakeClock(datetime.fromisoformat(NOW))
    failed_state = daily._daily_state(None)[1]
    failed_state["tasks"] = [task]
    try:
        daily._search_task(
            failed_connection,
            run_id=failed_run_id,
            task=task,
            settings_version=SETTINGS_VERSION,
            seed="R06-SEED",
            target_date=TARGET_DATE,
            state=failed_state,
            ports=_room_ports(failed_room),
            clock=failed_clock,
            data_root=failed_root,
        )
        failed_rows = failed_connection.execute(
            "SELECT attempt_no,retry_of,source_status,error_code FROM search_attempt "
            "WHERE run_id=? ORDER BY attempt_no",
            (failed_run_id,),
        ).fetchall()
        assert [row[0] for row in failed_rows] == [1, 2, 3]
        assert [row[1] for row in failed_rows] == [
            None,
            daily._task_attempt_id(failed_run_id, 1, 1),
            daily._task_attempt_id(failed_run_id, 1, 2),
        ]
        assert all(
            row[2:] == ("unavailable", "condition_popup_close_failed")
            for row in failed_rows
        )
        assert failed_state["source_failures"] == 3
        assert failed_state["source_stopped"] is True
        assert len(failed_room.requests) == 3
        assert (
            failed_connection.execute(
                "SELECT attempt_used FROM run WHERE run_id=?", (failed_run_id,)
            ).fetchone()[0]
            == 3
        )
        failure_observations = list(
            (failed_root / "public-source").glob(
                "attempt-*/observations/room-*-observation-*.json"
            )
        )
        assert len(failure_observations) == 3
        for path in failure_observations:
            observation = json.loads(path.read_text(encoding="utf-8"))
            assert observation["trace_state"] == "healthy"
            failure = next(
                event
                for event in observation["node_events"]
                if event.get("event") == "public_source_failure"
            )
            assert failure["error_code"] == "condition_popup_close_failed"
            assert failure["stage"] == "room_condition_popup_close_failed"
    finally:
        failed_connection.close()
