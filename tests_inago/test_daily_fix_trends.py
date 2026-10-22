"""R05-only Trends UI and retry contract cases."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from automation.inago import orchestrator as daily
from automation.inago import trends_adapter as trends
from automation.inago.daily_theme_planner import DurableTrendsThemePlanner
from automation.inago.trends_adapter import TrendsRequest, parse_grouped_top_response
from tests_inago import test_step21_browser as browser_fixtures
from tests_inago.test_step21_browser import ui_server
from tests_inago.test_step21_trends_retry import (
    NOW,
    RUN_ID,
    SETTINGS_VERSION,
    SequencePort,
    THEME_ID,
    THEME_WORD,
    response_for,
    response_for_dummy_unavailable,
    retry_case,
)


def _period_refresh_delayed_fixture() -> str:
    html = browser_fixtures.trends_html(count=20).replace(
        "topMode?'関連  語':'注目 語'",
        "topMode?(window.__periodDataReady?'期間更新語':'旧期間Top語'):'旧期間注目語'",
    )
    html = browser_fixtures.trends_with_period_picker(html)
    return html.replace(
        "document.querySelector('#period-menu').style.display='none';",
        "document.querySelector('#period-menu').style.display='none';"
        "setTimeout(()=>{window.__periodDataReady=true;"
        "document.querySelector('#words').textContent='期間データ更新'},650)",
    )


def test_n06(tmp_path, ui_server):
    """Wait for the selected period's refresh before reading its ranked rows."""

    ui_server["pages"].update(
        {
            "/trends/": browser_fixtures.trends_home_html(),
            "/trends/explore": _period_refresh_delayed_fixture(),
        }
    )
    request = TrendsRequest("R05-N06-a1", "R05-RUN-N06", "V1", "電子レンジ", range="3m")

    response = browser_fixtures.source(tmp_path, ui_server, timeout=8).fetch(request)
    snapshot = parse_grouped_top_response(response, request)

    assert snapshot.status == "observed", response.get("error_code")
    assert snapshot.ui_period_label == "過去 90 日間"
    assert [word.source_rank for word in snapshot.words] == list(range(1, 21))
    assert all(word.raw.startswith("期間更新語") for word in snapshot.words)

    observation_path = next(
        (tmp_path / "observations").glob("trends-R05-N06-a1-observation-*.json")
    )
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    events = observation["node_events"]
    assert any(
        event.get("stage") == "trends_related_widget_wait_begin" for event in events
    )
    ready = next(
        event
        for event in events
        if event.get("stage") == "trends_related_widget_wait_ready"
    )
    assert ready["related_widget_generation"] > ready["generation_before_period"]
    assert ready["widget_match_count"] == ready["widget_match_visible_count"] == 1
    assert ready["widget_visible_count"] == 2


def _insert_theme_selection(connection, ordinal: int, theme_id: str) -> None:
    selection_id = f"theme-selection:{RUN_ID}:initial:{ordinal:02d}"
    connection.execute(
        "INSERT INTO theme_selection (selection_id,run_id,theme_id,event_kind,"
        "count_event_id,seed,result_word_refs_json,selected_at) "
        "VALUES (?, ?, ?, 'initial', ?, 'SEED-1', '[]', ?)",
        (selection_id, RUN_ID, theme_id, selection_id, NOW),
    )


def _durable_session(
    connection,
    clock,
    port,
    *,
    ordinal: int,
    theme_id: str,
    deadline_at: str | None = "2026-10-14T17:45:00+09:00",
):
    return trends.DurableGroupKeywordSession(
        connection,
        port,
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=ordinal,
        theme_id=theme_id,
        seed="SEED-1",
        deadline_at=deadline_at,
        acquisition_id=f"{RUN_ID}:initial:{ordinal:02d}:{theme_id}",
    )


def _assert_exact_theme_fallback(connection, theme_id: str, query: str, ordinal: int):
    acquisition_id = f"{RUN_ID}:initial:{ordinal:02d}:{theme_id}"
    plan = daily.DailyThemePlan(
        theme_id=theme_id,
        keyword_refs=(),
        related=(),
        fallback=daily.DailySearchPlan(
            f"R05-fallback-{ordinal:02d}",
            theme_id,
            query,
            (theme_id,),
            "theme_fallback",
            "theme_fallback",
        ),
        snapshot_ref=None,
        acquisition_status="exhausted",
        acquisition_ref=acquisition_id,
    )
    daily._validate_plan(plan, theme_id)
    daily._validate_plan_snapshot(
        connection,
        plan,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        seed="SEED-1",
        event_kind="initial",
        ordinal=ordinal,
    )
    tasks = daily._make_tasks([plan], phase="initial", first_number=ordinal)
    assert len(tasks) == 1
    assert tasks[0]["query"] == query
    assert tasks[0]["query_kind"] == "theme_fallback"
    assert tasks[0]["keyword_snapshot_ref"] is None
    assert tasks[0]["selected_word_ref"] is None


def test_n07(retry_case):
    """Replay the four-group incident and keep ambiguity out of exhaustion."""

    connection, clock = retry_case
    groups = [
        (1, THEME_ID, THEME_WORD),
        (2, "T02", "加湿器"),
        (3, "T03", "調理器具"),
        (4, "T04", "掃除機"),
        (5, "T05", "珈琲メーカー"),
        (6, "T06", "収納用品"),
    ]
    for ordinal, theme_id, _query in groups[1:]:
        _insert_theme_selection(connection, ordinal, theme_id)

    replay_request_count = 0
    for ordinal, theme_id, query in groups[:3]:
        port = SequencePort(
            [response_for_dummy_unavailable("source_timeout") for _ in range(5)]
        )
        result = _durable_session(
            connection, clock, port, ordinal=ordinal, theme_id=theme_id
        ).fetch(query)
        acquisition_id = f"{RUN_ID}:initial:{ordinal:02d}:{theme_id}"
        replay_request_count += len(port.requests)
        assert result.status == "exhausted" and result.attempts_used == 5
        assert result.snapshot is None and len(port.requests) == 5
        rows = connection.execute(
            "SELECT request_id,attempt_no,status,error_code,retry_of,next_retry_at "
            "FROM trends_acquisition_attempt WHERE acquisition_id=? ORDER BY attempt_no",
            (acquisition_id,),
        ).fetchall()
        assert [row[1] for row in rows] == [1, 2, 3, 4, 5]
        assert [row[2] for row in rows] == ["unavailable"] * 5
        assert [row[3] for row in rows] == ["source_timeout"] * 5
        assert rows[0][4] is None
        assert [row[4] for row in rows[1:]] == [row[0] for row in rows[:-1]]
        for row in rows[:-1]:
            assert datetime.fromisoformat(row[5]) - datetime.fromisoformat(
                connection.execute(
                    "SELECT ended_at FROM trends_acquisition_attempt WHERE request_id=?",
                    (row[0],),
                ).fetchone()[0]
            ) >= timedelta(seconds=60)
        assert connection.execute(
            "SELECT status,attempts_used,snapshot_id FROM trends_acquisition "
            "WHERE acquisition_id=?",
            (acquisition_id,),
        ).fetchone()[:] == ("exhausted", 5, None)
        _assert_exact_theme_fallback(connection, theme_id, query, ordinal)

    ordinal, theme_id, query = groups[3]
    ambiguity_port = SequencePort(
        [response_for_dummy_unavailable("source_timeout") for _ in range(4)]
        + [response_for_dummy_unavailable("query_widget_ambiguous")]
    )
    acquisition_id = f"{RUN_ID}:initial:{ordinal:02d}:{theme_id}"
    with pytest.raises(trends.TrendsContractError):
        _durable_session(
            connection, clock, ambiguity_port, ordinal=ordinal, theme_id=theme_id
        ).fetch(query)
    replay_request_count += len(ambiguity_port.requests)
    ambiguous_rows = connection.execute(
        "SELECT request_id,attempt_no,status,error_code,retry_of,next_retry_at "
        "FROM trends_acquisition_attempt WHERE acquisition_id=? ORDER BY attempt_no",
        (acquisition_id,),
    ).fetchall()
    assert len(ambiguity_port.requests) == 5
    assert [row[2] for row in ambiguous_rows] == [
        "unavailable",
        "unavailable",
        "unavailable",
        "unavailable",
        "processing_error",
    ]
    assert [row[3] for row in ambiguous_rows] == [
        "source_timeout",
        "source_timeout",
        "source_timeout",
        "source_timeout",
        "query_widget_ambiguous",
    ]
    assert ambiguous_rows[-1][5] is None
    assert connection.execute(
        "SELECT status,attempts_used,snapshot_id FROM trends_acquisition "
        "WHERE acquisition_id=?",
        (acquisition_id,),
    ).fetchone()[:] == ("processing_error", 5, None)
    assert replay_request_count == 20

    ordinal, theme_id, query = groups[4]

    def http_429_retry_after_90(request):
        response = response_for(request, status="unavailable", error="http_429")
        response["retry_after_seconds"] = 90
        return response

    success_port = SequencePort(
        [
            http_429_retry_after_90,
            response_for_dummy_unavailable("source_connection_error"),
            response_for,
        ]
    )
    started = clock.now_jst()
    result = _durable_session(
        connection, clock, success_port, ordinal=ordinal, theme_id=theme_id
    ).fetch(query)
    assert result.status == "response_ready" and result.attempts_used == 3
    assert result.snapshot is not None and len(success_port.requests) == 3
    assert clock.now_jst() - started == timedelta(seconds=150)
    success_id = f"{RUN_ID}:initial:{ordinal:02d}:{theme_id}"
    success_rows = connection.execute(
        "SELECT status,error_code,next_retry_at FROM trends_acquisition_attempt "
        "WHERE acquisition_id=? ORDER BY attempt_no",
        (success_id,),
    ).fetchall()
    assert [row[0] for row in success_rows] == [
        "unavailable",
        "unavailable",
        "response_ready",
    ]
    assert [row[1] for row in success_rows] == [
        "http_429",
        "source_connection_error",
        None,
    ]

    ordinal, theme_id, query = groups[5]
    deadline = datetime.fromisoformat("2026-10-14T17:45:00+09:00")
    clock.advance((deadline - clock.now_jst()).total_seconds() - 30)
    cutoff_port = SequencePort([http_429_retry_after_90])
    cutoff = _durable_session(
        connection,
        clock,
        cutoff_port,
        ordinal=ordinal,
        theme_id=theme_id,
    ).fetch(query)
    assert cutoff.status == "deadline" and cutoff.attempts_used == 1
    assert len(cutoff_port.requests) == 1
    cutoff_id = f"{RUN_ID}:initial:{ordinal:02d}:{theme_id}"
    cutoff_plan = daily.DailyThemePlan(
        theme_id=theme_id,
        keyword_refs=(),
        related=(),
        fallback=None,
        snapshot_ref=None,
        acquisition_status="deadline",
        acquisition_ref=cutoff_id,
    )
    daily._validate_plan(cutoff_plan, theme_id)
    daily._validate_plan_snapshot(
        connection,
        cutoff_plan,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        seed="SEED-1",
        event_kind="initial",
        ordinal=ordinal,
    )
    assert daily._make_tasks([cutoff_plan], phase="initial", first_number=ordinal) == []


def test_n07b_trends_retry_crosses_former_daily_cutoffs_with_bounded_timeouts(
    retry_case, tmp_path, ui_server
):
    """Continue bounded Trends work across 17:45 and 18:00."""

    connection, clock = retry_case
    themes = [
        {"theme_id": THEME_ID, "name": THEME_WORD},
        {"theme_id": "T02", "name": "加湿器"},
    ]
    connection.execute(
        "UPDATE settings_version SET themes_json=? WHERE version=?",
        (json.dumps(themes, ensure_ascii=False), SETTINGS_VERSION),
    )
    _insert_theme_selection(connection, 2, "T02")
    clock.advance(
        (
            datetime.fromisoformat("2026-10-14T17:44:30+09:00") - clock.now_jst()
        ).total_seconds()
    )

    def http_429_retry_after_960(request):
        payload = response_for(request, status="unavailable", error="http_429")
        payload["retry_after_seconds"] = 960
        return payload

    success_port = SequencePort([http_429_retry_after_960, response_for])
    success = _durable_session(
        connection,
        clock,
        success_port,
        ordinal=1,
        theme_id=THEME_ID,
        deadline_at=None,
    ).fetch(THEME_WORD)

    assert success.status == "response_ready"
    assert success.snapshot is not None and success.snapshot.status == "observed"
    assert len(success.snapshot.words) == 1
    assert len(success_port.requests) == 2
    assert [request.deadline_at for request in success_port.requests] == [None, None]
    assert clock.now_jst() == datetime.fromisoformat("2026-10-14T18:00:30+09:00")
    assert (
        connection.execute(
            "SELECT status FROM trends_acquisition WHERE acquisition_id=?",
            (f"{RUN_ID}:initial:01:{THEME_ID}",),
        ).fetchone()[0]
        == "response_ready"
    )

    timeout_port = SequencePort(
        [TimeoutError("bounded source timeout") for _ in range(5)]
    )
    timeout_plan = DurableTrendsThemePlanner(timeout_port, object(), clock).plan_theme(
        connection,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=2,
        theme_id="T02",
        seed="SEED-1",
    )
    assert timeout_plan.acquisition_status == "exhausted"
    assert timeout_plan.fallback is not None
    assert len(timeout_port.requests) == trends.DurableGroupKeywordSession.MAX_ATTEMPTS
    assert [request.deadline_at for request in timeout_port.requests] == [None] * 5
    assert connection.execute(
        "SELECT status,attempts_used,error_code FROM trends_acquisition "
        "WHERE acquisition_id=?",
        (f"{RUN_ID}:initial:02:T02",),
    ).fetchone()[:] == ("exhausted", 5, "source_timeout")
    assert clock.now_jst() == datetime.fromisoformat("2026-10-14T18:04:30+09:00")

    timeout_configured_source = browser_fixtures.source(tmp_path, ui_server, timeout=4)
    assert (
        timeout_configured_source._request_timeout_seconds(
            TrendsRequest("N07B-TIMEOUT", RUN_ID, SETTINGS_VERSION, THEME_WORD)
        )
        == 4
    )


def _n08_period_page(
    base_html: str,
    *,
    on_period_selected: str = "",
    inject_period_script: bool = False,
) -> str:
    page = browser_fixtures.trends_with_period_picker(base_html)
    if not on_period_selected:
        on_period_selected = (
            "setTimeout(()=>{const widget=Array.from(document.querySelectorAll('.fe-related-queries'))"
            ".find(item=>item.querySelector('h2')?.textContent.trim()==='関連キーワード');"
            "if(widget)widget.querySelectorAll('.label-text span').forEach((row,index)=>"
            "row.textContent='対象期間'+(index+1))},50)"
        )
    if inject_period_script and on_period_selected:
        page = page.replace(
            "document.querySelector('#period-menu').style.display='none';",
            "document.querySelector('#period-menu').style.display='none';"
            f"{on_period_selected};",
        )
    return page


def _n08_fetch(tmp_path, ui_server, case: str, explore_html: str):
    case_root = tmp_path / case
    ui_server["pages"].clear()
    ui_server["pages"].update(
        {
            "/trends/": browser_fixtures.trends_home_html(),
            "/trends/explore": explore_html,
        }
    )
    request = TrendsRequest(
        f"R05-N08-{case}-a1", "R05-RUN-N08", "V1", "電子レンジ", range="3m"
    )
    response = browser_fixtures.source(case_root, ui_server, timeout=4).fetch(request)
    snapshot = parse_grouped_top_response(response, request)
    observation_path = next(
        (case_root / "observations").glob(
            f"trends-{request.request_id}-observation-*.json"
        )
    )
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    return snapshot, response, observation["node_events"]


def test_related_widget_waits_for_nonzero_layout(tmp_path, ui_server):
    html = browser_fixtures.trends_html(count=20, refresh_after_period=True).replace(
        '<div class="fe-related-queries"><h2>関連キーワード</h2>',
        '<div class="fe-related-queries" style="width:0;height:0;overflow:hidden"><h2>関連キーワード</h2>',
    )
    snapshot, response, events = _n08_fetch(
        tmp_path,
        ui_server,
        "layout-pending",
        _n08_period_page(
            html,
            inject_period_script=True,
            on_period_selected="setTimeout(()=>{document.querySelectorAll('.fe-related-queries')[1].removeAttribute('style')},650)",
        ),
    )
    assert snapshot.status == "observed", response.get("error_code")
    assert len(snapshot.words) == 20
    assert any(
        event.get("stage") == "trends_related_widget_wait_ready" for event in events
    )


def test_trends_request_has_its_own_finite_timeout(tmp_path, ui_server):
    port = browser_fixtures.source(tmp_path, ui_server, timeout=17100)
    request = TrendsRequest(
        "trends-time-budget", "RUN-BUDGET", "V1", "電子レンジ", range="3m"
    )
    assert port._request_timeout_seconds(request) == 120
    assert port.config.timeout_seconds == 17100


def test_n08(tmp_path, ui_server):
    """Distinguish absent, duplicated, wrong-type, hidden, and exact widgets."""

    delayed = browser_fixtures.trends_html(
        count=20, refresh_after_period=True
    ).replace(
        '<div class="fe-related-queries"><h2>関連キーワード</h2>',
        '<div class="fe-related-queries" style="display:none"><h2>関連キーワード</h2>',
    )
    delayed_snapshot, delayed_response, delayed_events = _n08_fetch(
        tmp_path,
        ui_server,
        "delayed",
        _n08_period_page(
            delayed,
            inject_period_script=True,
            on_period_selected=(
                "setTimeout(()=>{document.querySelectorAll('.fe-related-queries')[1].style.display='block'},650)"
            ),
        ),
    )
    assert delayed_snapshot.status == "observed", delayed_response.get("error_code")
    assert [word.source_rank for word in delayed_snapshot.words] == list(range(1, 21))
    delayed_begin = next(
        event
        for event in delayed_events
        if event.get("stage") == "trends_related_widget_wait_begin"
    )
    assert delayed_begin["widget_match_count"] == 1
    assert delayed_begin["widget_match_visible_count"] == 0
    delayed_ready = next(
        event
        for event in delayed_events
        if event.get("stage") == "trends_related_widget_wait_ready"
    )
    assert delayed_ready["widget_match_visible_count"] == 1
    assert delayed_ready["widget_outcome"] == "ready"

    multiple = browser_fixtures.trends_html(count=20, refresh_after_period=True)
    multiple += (
        '<script>const related=document.querySelectorAll(".fe-related-queries");'
        "document.body.appendChild(related[1].cloneNode(true));</script>"
    )
    multiple_snapshot, multiple_response, multiple_events = _n08_fetch(
        tmp_path,
        ui_server,
        "multiple",
        _n08_period_page(multiple, inject_period_script=True),
    )
    assert multiple_snapshot.status == "unavailable"
    assert (
        multiple_snapshot.error_code
        == multiple_response["error_code"]
        == "query_widget_ambiguous"
    )
    ambiguous = next(
        event
        for event in multiple_events
        if event.get("stage") == "trends_related_widget_contract_error"
    )
    assert ambiguous["widget_match_count"] == 2
    assert ambiguous["widget_match_visible_count"] == 2
    assert ambiguous["widget_outcome"] == "ambiguous"

    wrong_type = (
        browser_fixtures.trends_html(count=20, refresh_after_period=True)
        .replace(
            '<div class="fe-related-queries"><h2>関連トピック</h2><a class="progress-label">誤ったトピック</a></div>',
            "",
        )
        .replace("<h2>関連キーワード</h2>", "<h2>異なる種類</h2>")
    )
    wrong_snapshot, wrong_response, wrong_events = _n08_fetch(
        tmp_path,
        ui_server,
        "wrong-type",
        _n08_period_page(wrong_type, inject_period_script=True),
    )
    assert wrong_snapshot.status == "unavailable"
    assert (
        wrong_snapshot.error_code
        == wrong_response["error_code"]
        == "query_widget_contract_mismatch"
    )
    mismatch = next(
        event
        for event in wrong_events
        if event.get("stage") == "trends_related_widget_contract_error"
    )
    assert mismatch["widget_match_count"] == 0
    assert mismatch["widget_wrong_visible_count"] == 1
    assert mismatch["widget_kind"] == "unknown"
    assert mismatch["widget_outcome"] == "contract_mismatch"

    hidden_twin = browser_fixtures.trends_html(
        count=20, refresh_after_period=True
    )
    hidden_twin += (
        '<script>const matches=Array.from(document.querySelectorAll(".fe-related-queries"));'
        'const clone=matches[1].cloneNode(true);clone.style.display="none";'
        "document.body.appendChild(clone);</script>"
    )
    hidden_snapshot, hidden_response, hidden_events = _n08_fetch(
        tmp_path,
        ui_server,
        "hidden-twin",
        _n08_period_page(hidden_twin, inject_period_script=True),
    )
    assert hidden_snapshot.status == "observed", hidden_response.get("error_code")
    hidden_ready = next(
        event
        for event in hidden_events
        if event.get("stage") == "trends_related_widget_wait_ready"
    )
    assert hidden_ready["widget_match_count"] == 2
    assert hidden_ready["widget_match_visible_count"] == 1

    exact_snapshot, exact_response, exact_events = _n08_fetch(
        tmp_path,
        ui_server,
        "exact-scope",
        _n08_period_page(
            browser_fixtures.trends_html(count=20, refresh_after_period=True),
            inject_period_script=True,
        ),
    )
    assert exact_snapshot.status == "observed", exact_response.get("error_code")
    assert [word.source_rank for word in exact_snapshot.words] == list(range(1, 21))
    exact_ready = next(
        event
        for event in exact_events
        if event.get("stage") == "trends_related_widget_wait_ready"
    )
    assert exact_ready["widget_scope"] == "explore_related_queries"
    assert exact_ready["widget_kind"] == "related_keywords"
    assert exact_ready["widget_match_count"] == 1
    assert exact_ready["widget_match_visible_count"] == 1


def test_n08b_related_keyword_heading_alias_and_semantic_level(tmp_path, ui_server):
    """Recognize a related-keyword heading when the UI uses a semantic h3 alias."""

    alias = browser_fixtures.trends_html(
        count=20, refresh_after_period=True
    ).replace(
        '<div class="fe-related-queries"><h2>関連キーワード</h2>',
        '<div class="fe-related-queries"><h3>関連する検索キーワード</h3>',
    )
    page = _n08_period_page(
        alias,
        inject_period_script=True,
        on_period_selected=(
            "setTimeout(()=>{const widget=Array.from(document.querySelectorAll('.fe-related-queries'))"
            ".find(item=>item.querySelector('h3')?.textContent.trim()==='関連する検索キーワード');"
            "if(widget)widget.querySelectorAll('.label-text span').forEach((row,index)=>"
            "row.textContent='期間反映語'+(index+1))},50)"
        ),
    )
    snapshot, response, events = _n08_fetch(tmp_path, ui_server, "heading-alias", page)

    assert snapshot.status == "observed", response.get("error_code")
    assert [word.source_rank for word in snapshot.words] == list(range(1, 21))
    ready = next(
        event
        for event in events
        if event.get("stage") == "trends_related_widget_wait_ready"
    )
    assert ready["widget_selector_version"] == "related-widget-v5"
    assert ready["widget_kind"] == "related_keywords"
    assert ready["related_widget_generation"] > ready["generation_before_period"]


def test_n08c_title_container_alias_and_unknown_heading_stay_safe(tmp_path, ui_server):
    """Use a semantic title container while keeping unrelated labels unmatched."""

    title_alias = browser_fixtures.trends_html(count=20).replace(
        '<div class="fe-related-queries"><h2>関連キーワード</h2>',
        '<div class="fe-related-queries"><div class="fe-related-queries-title">関連するクエリ</div>',
    )
    title_page = _n08_period_page(
        title_alias,
        inject_period_script=True,
        on_period_selected=(
            "setTimeout(()=>{const widget=Array.from(document.querySelectorAll('.fe-related-queries'))"
            ".find(item=>item.querySelector('.fe-related-queries-title'));"
            "if(widget)widget.querySelectorAll('.label-text span').forEach((row,index)=>"
            "row.textContent='期間反映語'+(index+1))},50)"
        ),
    )
    snapshot, response, events = _n08_fetch(
        tmp_path, ui_server, "title-container", title_page
    )
    assert snapshot.status == "observed", response.get("error_code")
    assert [word.source_rank for word in snapshot.words] == list(range(1, 21))
    ready = next(
        event
        for event in events
        if event.get("stage") == "trends_related_widget_wait_ready"
    )
    assert ready["widget_selector_version"] == "related-widget-v5"
    assert ready["widget_kind"] == "related_keywords"
    assert ready["related_widget_generation"] > ready["generation_before_period"]

    unknown_title = browser_fixtures.trends_html(count=20).replace(
        '<div class="fe-related-queries"><h2>関連キーワード</h2>',
        '<div class="fe-related-queries"><div class="fe-related-queries-title">人気</div>',
    )
    unknown_page = _n08_period_page(
        unknown_title,
        inject_period_script=True,
        on_period_selected=(
            "setTimeout(()=>{const widget=Array.from(document.querySelectorAll('.fe-related-queries'))"
            ".find(item=>item.querySelector('.fe-related-queries-title'));"
            "if(widget)widget.querySelectorAll('.label-text span').forEach((row,index)=>"
            "row.textContent='期間反映語'+(index+1))},50)"
        ),
    )
    unknown_snapshot, unknown_response, unknown_events = _n08_fetch(
        tmp_path, ui_server, "unknown-title", unknown_page
    )
    assert unknown_snapshot.status == "unavailable"
    assert unknown_response["error_code"] == "query_widget_contract_mismatch"
    mismatch = next(
        event
        for event in unknown_events
        if event.get("stage") == "trends_related_widget_contract_error"
    )
    assert mismatch["widget_selector_version"] == "related-widget-v5"
    assert mismatch["widget_match_count"] == 0
    assert mismatch["widget_visible_count"] == 2
    assert mismatch["widget_wrong_visible_count"] == 2
    assert mismatch["widget_empty_evidence"] == "none"
