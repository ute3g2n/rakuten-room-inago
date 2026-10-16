"""ROOM condition popup and detail-resolution contract cases."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess

import pytest

from automation.inago.clock import FakeClock, JST
from automation.inago import orchestrator as daily
from automation.inago.repository import initialize_database
from automation.inago.orchestrator import DailyPorts
from automation.inago.live_sources import PublicBrowserSource, PublicSourceConfig
from automation.inago.room_adapter import parse_room_response
from tests_inago import test_step21_browser as browser_fixtures
from tests_inago.test_step21_browser import room_request, source, ui_server


SETTINGS_VERSION = "R06-fixture-settings"
TARGET_DATE = "2026-10-16"
NOW = f"{TARGET_DATE}T13:00:00+09:00"


def _browser_deadline_clock() -> FakeClock:
    """Keep real browser child-process deadlines ahead of wall-clock time."""

    return FakeClock(datetime.now(JST))


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


def _search_submit_room_html() -> str:
    html = browser_fixtures.room_html(1, initial=1, terminal=True)
    popup_end = '</div></div>\n    <div id="rows">'
    popup_with_search = (
        '</div><button type="button" onclick="fetch(\'/condition-apply-used\');'
        "this.closest('.itemSearch-popup').style.display='none'\">検索</button></div>\n"
        '    <div id="rows">'
    )
    assert popup_end in html
    html = html.replace(popup_end, popup_with_search, 1)
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
            timeout=8,
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


def _room_observation(root: Path, attempt_id: str) -> dict[str, object]:
    path = next((root / "observations").glob(f"room-{attempt_id}-observation-*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def _timed_fixture_source(
    root: Path,
    ui_server,
    *,
    timeout: float = 8,
    click_timeout_ms: int = 0,
    destination_timeout_ms: int = 0,
    request_deadline_ms: int = 0,
    scroll_delay_ms: int = 0,
    expire_deadline_before_restore: bool = False,
) -> PublicBrowserSource:
    return PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=browser_fixtures.CHROME,
            evidence_root=root / "observations",
            environment="fixture",
            timeout_seconds=timeout,
            fixture_url=ui_server["url"],
            headless=True,
            fixture_room_scroll_delay_ms=scroll_delay_ms,
            fixture_detail_click_timeout_ms=click_timeout_ms,
            fixture_product_destination_timeout_ms=destination_timeout_ms,
            fixture_request_deadline_ms=request_deadline_ms,
            fixture_expire_deadline_before_room_restore=expire_deadline_before_restore,
        )
    )


def _unique_same_tab_room_html() -> str:
    html = browser_fixtures.room_html(80, initial=80, terminal=True)
    default_control = (
        '<div class="market-detail-link" onclick="window.open('
        "'https://item.rakuten.co.jp/shop/item-'+Math.ceil(current/10)+'/'"
        ')">楽天市場で詳細を見る</div>'
    )
    row_link_control = (
        '<div class="market-detail-link" onclick="location.href='
        "'https://item.rakuten.co.jp/shop/item-'+current+'/'"
        '">楽天市場で詳細を見る</div>'
    )
    assert default_control in html
    return html.replace(default_control, row_link_control)


def _obscured_detail_room_html(*, late_popup_delay_ms: int = 0) -> str:
    html = browser_fixtures.room_html(1, initial=1, terminal=True)
    html = html.replace(
        '<input id="search-box"',
        "<style>#modal{position:relative}.click-shield{position:absolute;inset:0;z-index:10}</style>"
        '<input id="search-box"',
    )
    html = html.replace(
        "<div class=\"close-button\" onclick=\"document.querySelector('#modal').style.display='none'\">閉じる</div>",
        "<div class=\"close-button\" onclick=\"document.querySelector('#modal').style.display='none'\">閉じる</div>"
        '<div class="click-shield" aria-hidden="true"></div>',
    )
    if late_popup_delay_ms:
        original = (
            "function openPreview(n){current=n;"
            "document.querySelector('#modal').style.display='block'}"
        )
        delayed = (
            "function openPreview(n){current=n;"
            "document.querySelector('#modal').style.display='block';"
            f"setTimeout(()=>window.open('https://item.rakuten.co.jp/shop/late-item/'),{late_popup_delay_ms})}}"
        )
        assert original in html
        html = html.replace(original, delayed)
    return html


def _row_click_failure_room_html(count: int, failed_rows: set[int]) -> str:
    html = browser_fixtures.room_html(count, initial=count, terminal=True)
    html = html.replace(
        '<input id="search-box"',
        "<style>.detail-control-container{position:relative;display:inline-block}"
        ".click-shield{position:absolute;inset:0;z-index:10;display:none}</style>"
        '<input id="search-box"',
    )
    control = (
        '<div class="market-detail-link" onclick="window.open('
        "'https://item.rakuten.co.jp/shop/item-'+Math.ceil(current/10)+'/'"
        ')">楽天市場で詳細を見る</div>'
    )
    wrapped_control = (
        '<div class="detail-control-container">'
        + control
        + '<div class="click-shield" aria-hidden="true"></div></div>'
    )
    assert control in html
    html = html.replace(control, wrapped_control)
    existing = (
        "function openPreview(n){current=n;"
        "document.querySelector('#modal').style.display='block'}"
    )
    replacement = (
        "function openPreview(n){current=n;"
        "document.querySelector('#modal').style.display='block';"
        f"document.querySelector('.click-shield').style.display={sorted(failed_rows)!r}.includes(n)?'block':'none'"
        "}"
    )
    assert existing in html
    return html.replace(existing, replacement)


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
        "searchControlCount": 0,
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


def test_n09c_public_search_action_applies_verified_conditions(tmp_path, ui_server):
    """An explicit search action closes the conditions sheet and starts results."""

    attempt_id = "R06-N09C-SEARCH-SUBMIT"
    ui_server["html"] = _search_submit_room_html()
    request = replace(room_request(), attempt_id=attempt_id, run_id=f"{attempt_id}-RUN")

    response = source(tmp_path, ui_server, timeout=8).search(request)

    assert response["status"] == "observed", response.get("error_code")
    observation_path = next(
        (tmp_path / "observations").glob(f"room-{attempt_id}-observation-*.json")
    )
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    assert observation["trace_state"] == "healthy"
    events = [
        event
        for event in observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    submitted = next(
        event
        for event in events
        if event["stage"] == "room_condition_search_submit_complete"
    )
    assert submitted["room_condition_close_method"] == "search_submit"
    assert submitted["room_condition_search_control_count"] == 1
    assert submitted["room_condition_popup_state"] == "hidden"
    assert events.index(submitted) < next(
        index
        for index, event in enumerate(events)
        if event["stage"] == "room_results_scan_begin"
    )
    assert ui_server["requests"].count("/condition-apply-used") == 1
    common_trace = [
        json.loads(line)
        for line in (tmp_path / observation["trace_ref"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    common_submit = next(
        event
        for event in common_trace
        if event.get("operation") == "room_condition_search_submit_complete"
    )
    assert common_submit["room_condition_close_method"] == "search_submit"


def test_n10(tmp_path, ui_server):
    """Close failure retries the same task, preserves history, and stops after three tries."""

    task = _room_task()
    recovery_root = tmp_path / "recovery"
    recovery_run_id = "R06-N10-RECOVERY"
    recovery_connection = _insert_isolated_run(recovery_root, recovery_run_id)
    recovery_room = BrowserRoomSequence(
        recovery_root / "public-source", ui_server, fail_attempts={1}
    )
    recovery_clock = _browser_deadline_clock()
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
    failed_clock = _browser_deadline_clock()
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


def test_n11(tmp_path, ui_server):
    """Normal clicks resolve both an ancestor link and an href-less public handler."""

    cases = (
        (
            "ancestor-link",
            browser_fixtures.room_html(1, initial=1, terminal=True, wrapped_href=True),
            "ancestor",
            True,
        ),
        (
            "handler-div",
            browser_fixtures.room_html(1, initial=1, terminal=True),
            "none",
            False,
        ),
    )
    for case_name, html, expected_scope, has_href in cases:
        case_root = tmp_path / case_name
        ui_server["html"] = html
        ui_server["pages"].clear()
        request = replace(
            room_request(),
            attempt_id=f"R07N11-{case_name.upper()}",
            run_id=f"R07N11-{case_name.upper()}-RUN",
        )

        payload = source(case_root, ui_server, timeout=12).search(request)
        result = parse_room_response(payload, request)

        assert result.status == "observed", payload.get("error_code")
        assert len(result.posts) == 1
        assert result.posts[0].item_url == ("https://item.rakuten.co.jp/shop/item-1/")
        assert result.posts[0].offer_key == "shop:item-1"
        observation = _room_observation(case_root, request.attempt_id)
        assert observation["trace_state"] == "healthy"
        events = [
            event
            for event in observation["node_events"]
            if event.get("event") == "public_source_stage"
        ]
        inspected = next(
            event
            for event in events
            if event["stage"] == "room_detail_control_inspected"
        )
        assert inspected["element_tag"] == "div"
        assert inspected["element_role"] == "other"
        assert inspected["element_visible"] is True
        assert inspected["element_enabled"] is True
        assert inspected["element_obscured"] is False
        assert inspected["link_scope"] == expected_scope
        assert inspected["has_href"] is has_href
        if not has_href:
            assert inspected["handler_kind"] == "onclick"
            assert inspected["handler_tag"] == "div"
        stages = [event["stage"] for event in events]
        assert stages.index("room_detail_button_click_begin") < stages.index(
            "room_detail_button_click_complete"
        )
        assert stages.index("room_product_destination_wait_begin") < stages.index(
            "room_product_destination_wait_complete"
        )
        assert "room_popup_received" in stages
        assert "room_product_resolved" in stages

        common_trace = [
            json.loads(line)
            for line in (case_root / observation["trace_ref"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert any(
            event.get("operation") == "room_detail_button_click_begin"
            and event.get("phase") == "begin"
            for event in common_trace
        )
        assert any(
            event.get("operation") == "room_detail_button_click_complete"
            and event.get("phase") == "end"
            for event in common_trace
        )


def test_n12(tmp_path, ui_server):
    """Same-tab product routes restore the original first-50 source order."""

    attempt_id = "R07-N12-80-SAME-TAB"
    ui_server["html"] = _unique_same_tab_room_html()
    request = replace(room_request(), attempt_id=attempt_id, run_id=f"{attempt_id}-RUN")
    payload = source(tmp_path, ui_server, timeout=90).search(request)
    result = parse_room_response(payload, request)

    assert result.status == "observed", payload.get("error_code")
    assert len(result.posts) == 50
    assert [post.order_index for post in result.posts] == list(range(1, 51))
    assert [post.offer_key for post in result.posts] == [
        f"shop:item-{index}" for index in range(1, 51)
    ]
    assert result.posts[1].offer_key == "shop:item-2"
    assert result.posts[49].offer_key == "shop:item-50"

    observation = _room_observation(tmp_path, attempt_id)
    assert observation["trace_state"] == "healthy"
    events = [
        event
        for event in observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    assert (
        sum(event["stage"] == "room_same_tab_navigation_observed" for event in events)
        == 50
    )
    assert sum(event["stage"] == "room_search_restored" for event in events) == 50
    assert (
        sum(event["stage"] == "room_detail_button_click_complete" for event in events)
        == 50
    )
    assert not any(
        event["stage"] == "room_product_destination_wait_error" for event in events
    )
    common_trace = [
        json.loads(line)
        for line in (tmp_path / observation["trace_ref"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert (
        len(
            [
                event
                for event in common_trace
                if event.get("operation") == "room_search_restored"
                and event.get("phase") == "end"
            ]
        )
        == 50
    )


def test_n13(tmp_path, ui_server):
    """Click and destination deadlines stay separate and late popups are discarded."""

    click_attempt = "R07-N13-CLICK-TIMEOUT"
    ui_server["html"] = _obscured_detail_room_html(late_popup_delay_ms=80)
    request = replace(
        room_request(), attempt_id=click_attempt, run_id=f"{click_attempt}-RUN"
    )
    click_root = tmp_path / "click-timeout"
    click_payload = _timed_fixture_source(
        click_root,
        ui_server,
        timeout=8,
        click_timeout_ms=150,
        destination_timeout_ms=1500,
    ).search(request)
    click_result = parse_room_response(click_payload, request)

    assert click_result.status == "unavailable"
    assert click_payload["error_code"] == "room_search_restore_failed"
    click_observation = _room_observation(click_root, click_attempt)
    assert click_observation["trace_state"] == "healthy"
    click_events = [
        event
        for event in click_observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    click_error = next(
        event
        for event in click_events
        if event["stage"] == "room_detail_button_click_error"
    )
    assert click_error["error_class"] == "TimeoutError"
    assert click_error["failed_operation"] == "room_detail_button_click_begin"
    assert click_error["element_obscured"] is True
    assert "room_product_destination_wait_begin" not in {
        event["stage"] for event in click_events
    }
    assert "room_popup_received" not in {event["stage"] for event in click_events}
    inspected = next(
        event
        for event in click_events
        if event["stage"] == "room_detail_control_inspected"
    )
    assert inspected["element_obscured"] is True
    assert "/detail-click-used" not in ui_server["requests"]

    destination_attempt = "R07-N13-DESTINATION-TIMEOUT"
    no_route = browser_fixtures.room_html(1, initial=1, terminal=True)
    handler = (
        '<div class="market-detail-link" onclick="window.open('
        "'https://item.rakuten.co.jp/shop/item-'+Math.ceil(current/10)+'/'"
        ')">楽天市場で詳細を見る</div>'
    )
    no_route = no_route.replace(
        handler,
        '<div class="market-detail-link" onclick="fetch(\'/click-used\')">'
        "楽天市場で詳細を見る</div>",
    )
    assert handler not in no_route
    ui_server["html"] = no_route
    ui_server["pages"].clear()
    destination_request = replace(
        room_request(),
        attempt_id=destination_attempt,
        run_id=f"{destination_attempt}-RUN",
    )
    destination_root = tmp_path / "destination-timeout"
    destination_payload = _timed_fixture_source(
        destination_root,
        ui_server,
        timeout=8,
        click_timeout_ms=1000,
        destination_timeout_ms=180,
    ).search(destination_request)
    destination_result = parse_room_response(destination_payload, destination_request)

    assert destination_result.status == "observed"
    assert destination_result.posts[0].offer_key is None
    destination_observation = _room_observation(destination_root, destination_attempt)
    destination_events = [
        event
        for event in destination_observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    assert any(
        event["stage"] == "room_detail_button_click_complete"
        for event in destination_events
    )
    wait_error = next(
        event
        for event in destination_events
        if event["stage"] == "room_product_destination_wait_error"
    )
    assert wait_error["error_class"] == "TimeoutError"
    assert wait_error["failed_operation"] == "room_product_destination_wait_begin"
    assert wait_error["timeout_ms"] == 180
    assert wait_error["wait_elapsed_ms"] <= 600
    assert not any(
        event["stage"] == "room_popup_received" for event in destination_events
    )

    deadline_attempt = "R07-N13-REQUEST-DEADLINE"
    ui_server["pages"].clear()
    deadline_request = replace(
        room_request(), attempt_id=deadline_attempt, run_id=f"{deadline_attempt}-RUN"
    )
    deadline_root = tmp_path / "request-deadline"
    deadline_payload = _timed_fixture_source(
        deadline_root,
        ui_server,
        timeout=8,
        destination_timeout_ms=60_000,
        request_deadline_ms=4500,
    ).search(deadline_request)

    assert deadline_payload["status"] == "unavailable"
    assert deadline_payload["error_code"] == "source_timeout"
    deadline_observation = _room_observation(deadline_root, deadline_attempt)
    assert deadline_observation["trace_state"] == "healthy"
    deadline_events = [
        event
        for event in deadline_observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    assert any(
        event["stage"] == "room_detail_button_click_complete"
        for event in deadline_events
    )
    deadline_error = next(
        event
        for event in deadline_events
        if event["stage"] == "room_product_destination_wait_error"
    )
    assert deadline_error["error_class"] == "TimeoutError"
    assert deadline_error["failed_operation"] == "room_product_destination_wait_begin"
    assert deadline_error["wait_elapsed_ms"] <= 4500
    common_trace = [
        json.loads(line)
        for line in (deadline_root / deadline_observation["trace_ref"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        event.get("operation") == "room_product_destination_wait_begin"
        and event.get("phase") == "begin"
        for event in common_trace
    )
    assert any(
        event.get("operation") == "room_product_destination_wait_error"
        and event.get("phase") == "error"
        and event.get("failed_operation") == "room_product_destination_wait_begin"
        and event.get("error_class") == "TimeoutError"
        for event in common_trace
    )


@pytest.mark.parametrize(
    ("failure_count", "failed_rows"),
    [(1, {1}), (10, set(range(1, 11)))],
    ids=["one-row-timeout", "ten-row-timeouts"],
)
def test_n14(tmp_path, ui_server, failure_count, failed_rows):
    """A detail-click timeout is row scoped while the query deadline remains."""

    attempt_id = f"R08-N14-{failure_count}-ROW-TIMEOUT"
    request = replace(
        room_request(),
        attempt_id=attempt_id,
        run_id=f"{attempt_id}-RUN",
    )
    ui_server["html"] = _row_click_failure_room_html(failure_count + 1, failed_rows)
    evidence_root = tmp_path / f"n14-{failure_count}"
    payload = _timed_fixture_source(
        evidence_root,
        ui_server,
        timeout=30,
        click_timeout_ms=180,
    ).search(request)
    result = parse_room_response(payload, request)

    assert result.status == "observed"
    assert result.error_code is None
    assert len(result.posts) == failure_count + 1
    assert [post.order_index for post in result.posts] == list(
        range(1, failure_count + 2)
    )
    assert sum(post.identity_status != "identified" for post in result.posts) == (
        failure_count
    )
    assert result.posts[0].offer_key is None
    assert result.posts[-1].offer_key is not None

    observation = _room_observation(evidence_root, attempt_id)
    assert observation["trace_state"] == "healthy"
    stages = [
        event
        for event in observation["node_events"]
        if event.get("event") == "public_source_stage"
    ]
    click_errors = [
        event for event in stages if event["stage"] == "room_detail_button_click_error"
    ]
    assert [event["row_index"] for event in click_errors] == sorted(failed_rows)
    assert all(event["scope"] == "row" for event in click_errors)
    assert all(event["error_class"] == "TimeoutError" for event in click_errors)
    summary = next(
        event for event in stages if event["stage"] == "room_product_resolution_summary"
    )
    assert summary["scope"] == "query"
    assert summary["accepted_posts"] == failure_count + 1
    assert summary["row_failure_count"] == failure_count
    assert summary["identified_row_count"] == 1
    assert not any(
        event.get("event") == "public_source_failure"
        for event in observation["node_events"]
    )
    common_trace = [
        json.loads(line)
        for line in (evidence_root / observation["trace_ref"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        event.get("operation") == "room_detail_button_click_error"
        and event.get("scope") == "row"
        and event.get("row_index") == 1
        and event.get("error_class") == "TimeoutError"
        for event in common_trace
    )


def test_n15(tmp_path, ui_server):
    """Eleven failures become partial; row 50 still blocks row 51 replacement."""

    cases = [
        ("eleven-failures", set(range(1, 12)), "partial", 11),
        ("row-50-fails", {50}, "observed", 1),
    ]
    for case_name, failed_rows, expected_status, expected_failures in cases:
        attempt_id = f"R08-N15-{case_name.upper()}"
        request = replace(
            room_request(),
            attempt_id=attempt_id,
            run_id=f"{attempt_id}-RUN",
        )
        ui_server["html"] = _row_click_failure_room_html(80, failed_rows)
        evidence_root = tmp_path / case_name
        payload = _timed_fixture_source(
            evidence_root,
            ui_server,
            timeout=40,
            click_timeout_ms=100,
        ).search(request)
        result = parse_room_response(payload, request)

        assert result.status == expected_status
        assert len(result.posts) == 50
        assert [post.order_index for post in result.posts] == list(range(1, 51))
        assert result.loaded_posts == 80
        assert result.unused_posts == 30
        assert sum(post.identity_status != "identified" for post in result.posts) == (
            expected_failures
        )
        assert result.posts[49].order_index == 50
        if case_name == "row-50-fails":
            assert result.posts[49].offer_key is None
            assert result.error_code is None
        else:
            assert result.error_code == "product_resolution_failures_exceeded"

        observation = _room_observation(evidence_root, attempt_id)
        assert observation["trace_state"] == "healthy"
        stages = [
            event
            for event in observation["node_events"]
            if event.get("event") == "public_source_stage"
        ]
        click_errors = [
            event
            for event in stages
            if event["stage"] == "room_detail_button_click_error"
        ]
        assert [event["row_index"] for event in click_errors] == sorted(failed_rows)
        assert all(event["scope"] == "row" for event in click_errors)
        assert not any(event.get("row_index") == 51 for event in stages)
        summary = next(
            event
            for event in stages
            if event["stage"] == "room_product_resolution_summary"
        )
        assert summary["scope"] == "query"
        assert summary["loaded_posts"] == 80
        assert summary["accepted_posts"] == 50
        assert summary["row_failure_count"] == expected_failures
        assert summary["identified_row_count"] == 50 - expected_failures


def test_n16(tmp_path, ui_server):
    """Query failures keep query scope, retry safely, and stop with a reason."""

    def observe_case(
        case_name,
        html,
        *,
        timeout=12,
        request_deadline_ms=0,
        expire_deadline_before_restore=False,
    ):
        attempt_id = f"R08-N16-{case_name.upper()}"
        request = replace(
            room_request(),
            attempt_id=attempt_id,
            run_id=f"{attempt_id}-RUN",
        )
        ui_server["html"] = html
        evidence_root = tmp_path / case_name
        payload = _timed_fixture_source(
            evidence_root,
            ui_server,
            timeout=timeout,
            request_deadline_ms=request_deadline_ms,
            expire_deadline_before_restore=expire_deadline_before_restore,
        ).search(request)
        result = parse_room_response(payload, request)
        observation = _room_observation(evidence_root, attempt_id)
        common_trace = [
            json.loads(line)
            for line in (evidence_root / observation["trace_ref"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        return result, observation, common_trace

    deadline_result, deadline_observation, deadline_trace = observe_case(
        "request-deadline",
        browser_fixtures.room_html(1, initial=1, terminal=True, detail_delay_ms=8000),
        timeout=12,
        request_deadline_ms=4500,
    )
    assert deadline_result.status == "unavailable"
    assert deadline_result.error_code == "source_timeout"
    deadline_failure = next(
        event
        for event in deadline_observation["node_events"]
        if event.get("event") == "public_source_failure"
    )
    assert deadline_failure["scope"] == "query"
    assert deadline_failure["row_index"] == 1
    assert any(
        event.get("scope") == "query"
        and event.get("row_index") == 1
        and event.get("primary_error_code") == "source_timeout"
        for event in deadline_trace
    )

    denied_result, denied_observation, denied_trace = observe_case(
        "authentication-denied",
        "<main>アクセスが拒否されました</main>",
        timeout=8,
    )
    assert denied_result.status == "unavailable"
    assert denied_result.error_code == "access_denied"
    denied_failure = next(
        event
        for event in denied_observation["node_events"]
        if event.get("event") == "public_source_failure"
    )
    assert denied_failure["scope"] == "query"
    assert any(
        event.get("scope") == "query"
        and event.get("primary_error_code") == "access_denied"
        for event in denied_trace
    )

    condition_result, condition_observation, condition_trace = observe_case(
        "condition-unverified",
        _uncloseable_room_html("調理器具"),
        timeout=4,
    )
    assert condition_result.status == "unavailable"
    assert condition_result.error_code == "condition_popup_close_failed"
    assert any(
        event.get("stage") == "room_condition_popup_close_failed"
        and event.get("scope") == "query"
        for event in condition_observation["node_events"]
    )
    assert any(
        event.get("scope") == "query"
        and event.get("primary_error_code") == "condition_popup_close_failed"
        for event in condition_trace
    )

    restore_result, restore_observation, restore_trace = observe_case(
        "list-restore-deadline",
        _unique_same_tab_room_html(),
        timeout=15,
        expire_deadline_before_restore=True,
    )
    assert restore_result.status == "unavailable"
    assert restore_result.error_code == "source_timeout"
    assert any(
        event.get("stage") == "room_search_restore_skipped_deadline"
        and event.get("scope") == "query"
        and event.get("row_index") == 1
        for event in restore_observation["node_events"]
    )
    assert any(
        event.get("scope") == "query"
        and event.get("row_index") == 1
        and event.get("primary_error_code") == "source_timeout"
        for event in restore_trace
    )

    short_result, short_observation, _ = observe_case(
        "stable-short-list",
        browser_fixtures.room_html(2, initial=2, terminal=False),
        timeout=15,
    )
    assert short_result.status == "observed"
    assert short_result.loaded_posts == 2
    assert short_result.end_reached is True
    assert len(short_result.posts) == 2
    assert any(
        event.get("stage") == "room_short_batch_stable"
        and event.get("loaded_posts") == 2
        and event.get("stable_round") == 3
        for event in short_observation["node_events"]
    )

    empty_result, empty_observation, _ = observe_case(
        "explicit-empty",
        _empty_results_room_html("調理器具"),
    )
    assert empty_result.status == "observed"
    assert empty_result.posts == ()
    assert empty_result.end_reached is True
    assert empty_observation["diagnostics"]["end_evidence"] == "explicit_no_results"

    no_signal_result, no_signal_observation, _ = observe_case(
        "empty-without-end",
        browser_fixtures.room_html(0, initial=0, terminal=False),
        timeout=4,
    )
    assert no_signal_result.status == "unavailable"
    assert no_signal_result.error_code in {"source_timeout", "incomplete_search"}
    assert no_signal_observation["trace_state"] == "healthy"

    stale_result, stale_observation, stale_trace = observe_case(
        "stale-query-page",
        _empty_results_room_html("別の検索条件"),
    )
    assert stale_result.status == "unavailable"
    assert stale_result.error_code == "query_not_reflected"
    stale_failure = next(
        event
        for event in stale_observation["node_events"]
        if event.get("event") == "public_source_failure"
    )
    assert stale_failure["scope"] == "query"
    assert any(
        event.get("scope") == "query"
        and event.get("primary_error_code") == "query_not_reflected"
        for event in stale_trace
    )

    task = _room_task()
    recovered_root = tmp_path / "one-failure-recovers"
    recovered_run_id = "R08-N16-RECOVERY"
    recovered_connection = _insert_isolated_run(recovered_root, recovered_run_id)
    recovered_room = BrowserRoomSequence(
        recovered_root / "public-source", ui_server, fail_attempts={1}
    )
    recovered_state = daily._daily_state(None)[1]
    recovered_state["tasks"] = [task]
    try:
        daily._search_task(
            recovered_connection,
            run_id=recovered_run_id,
            task=task,
            settings_version=SETTINGS_VERSION,
            seed="R08-SEED",
            target_date=TARGET_DATE,
            state=recovered_state,
            ports=_room_ports(recovered_room),
            clock=_browser_deadline_clock(),
            data_root=recovered_root,
        )
        recovered_rows = recovered_connection.execute(
            "SELECT attempt_no,retry_of,source_status,error_code FROM search_attempt "
            "WHERE run_id=? ORDER BY attempt_no",
            (recovered_run_id,),
        ).fetchall()
        assert [row[0] for row in recovered_rows] == [1, 2]
        assert recovered_rows[0][2:] == (
            "unavailable",
            "condition_popup_close_failed",
        )
        assert recovered_rows[1][2:] == ("observed", None)
        assert recovered_rows[1][1] == recovered_room.requests[0].attempt_id
        assert recovered_state["source_failures"] == 1
        assert recovered_state["source_stopped"] is False
        assert recovered_state["source_stopped_reason"] is None
    finally:
        recovered_connection.close()

    stopped_root = tmp_path / "three-query-failures"
    stopped_run_id = "R08-N16-STOPPED"
    stopped_connection = _insert_isolated_run(stopped_root, stopped_run_id)
    stopped_room = BrowserRoomSequence(
        stopped_root / "public-source", ui_server, fail_attempts={1, 2, 3}
    )
    stopped_state = daily._daily_state(None)[1]
    stopped_state["tasks"] = [task]
    try:
        daily._search_task(
            stopped_connection,
            run_id=stopped_run_id,
            task=task,
            settings_version=SETTINGS_VERSION,
            seed="R08-SEED",
            target_date=TARGET_DATE,
            state=stopped_state,
            ports=_room_ports(stopped_room),
            clock=_browser_deadline_clock(),
            data_root=stopped_root,
        )
        stopped_rows = stopped_connection.execute(
            "SELECT attempt_no,retry_of,source_status,error_code FROM search_attempt "
            "WHERE run_id=? ORDER BY attempt_no",
            (stopped_run_id,),
        ).fetchall()
        assert [row[0] for row in stopped_rows] == [1, 2, 3]
        assert [row[1] for row in stopped_rows] == [
            None,
            stopped_room.requests[0].attempt_id,
            stopped_room.requests[1].attempt_id,
        ]
        assert all(
            row[2:] == ("unavailable", "condition_popup_close_failed")
            for row in stopped_rows
        )
        assert stopped_state["source_failures"] == 3
        assert stopped_state["source_stopped"] is True
        assert stopped_state["source_stopped_reason"] == "attempt_budget_exhausted"
        assert len(stopped_room.requests) == 3
        for request in stopped_room.requests:
            observation_root = (
                stopped_root / "public-source" / f"attempt-{request.attempt_no}"
            )
            observation = _room_observation(observation_root, request.attempt_id)
            failure = next(
                event
                for event in observation["node_events"]
                if event.get("event") == "public_source_failure"
            )
            assert failure["scope"] == "query"
            common_trace = [
                json.loads(line)
                for line in (observation_root / observation["trace_ref"])
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            assert any(
                event.get("scope") == "query"
                and event.get("primary_error_code") == "condition_popup_close_failed"
                for event in common_trace
            )
    finally:
        stopped_connection.close()
