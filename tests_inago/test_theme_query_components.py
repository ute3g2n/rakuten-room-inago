"""Component Trends queries retain the original theme and durable identity."""

import json
import shutil
from dataclasses import replace
from datetime import timedelta

import pytest

from automation.inago.daily_theme_planner import DurableTrendsThemePlanner
from automation.inago import orchestrator as daily
from automation.inago.paths import SAFE_FIXTURE_BASE, resolve_runtime_paths
from automation.inago.theme_queries import choose_theme_query, theme_query_candidates
from automation.inago.trends_adapter import DurableGroupKeywordSession
from tests_inago.test_step21_daily import FixtureKeywordRoot
from tests_inago.test_step13 import SETTINGS_VERSION, RuntimeCase, _make_ports
from tests_inago.test_step21_trends_retry import (
    ACQUISITION_ID,
    RUN_ID,
    THEME_ID,
    SequencePort,
    response_for,
    retry_case,
    session,
)


@pytest.fixture
def resume_runtime(retry_case, tmp_path, fixture_config_path):
    connection, clock = retry_case
    private_root = SAFE_FIXTURE_BASE / "theme-query-resume" / tmp_path.name
    paths = resolve_runtime_paths(
        data_root=private_root / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    case = RuntimeCase(connection=connection, data_root=paths.data_root, clock=clock)
    bundle = _make_ports(case, posts_per_search=0)
    try:
        yield case, bundle
    finally:
        if private_root.resolve().is_relative_to(SAFE_FIXTURE_BASE.resolve()):
            shutil.rmtree(private_root, ignore_errors=True)


def _legacy_resume_state(
    connection, plan, *, task_cursor, committed_offer_key, tasks=None
):
    if tasks is None:
        tasks = daily._make_tasks([plan], phase="initial", first_number=1)
    state = {
        "schema_version": daily._DAILY_CHECKPOINT_VERSION,
        "rules_version": daily._DAILY_RULES_VERSION,
        "initial_plan_index": 1,
        "offer_theme_ids": {committed_offer_key: THEME_ID},
        "theme_targets": {THEME_ID: 5},
        "used_queries": [],
        "source_stopped": False,
        "source_stopped_reason": None,
        "phase": "initial",
        "initial_theme_ids": [THEME_ID],
        "primary_error": None,
        "plans": {"initial:01": daily._plan_to_dict(plan)},
        "tasks": tasks,
        "task_cursor": task_cursor,
        "additional_events": 0,
        "source_failures": 0,
        "error_count": 0,
        "processed_attempts": (
            [{"task_number": 1, "task_id": tasks[0]["task_id"], "status": "done"}]
            if task_cursor
            else []
        ),
        "handled_offer_keys": [committed_offer_key],
        "selected_products": [],
        "next_selection_order": 1,
        "errors": [],
        "initial_ready": True,
    }
    connection.execute(
        "UPDATE run SET checkpoint=? WHERE run_id=?",
        (json.dumps({"daily_orchestration": state}), RUN_ID),
    )
    return state


def _insert_committed_resume_draft(connection):
    offer_key = "resume-shop:resume-item"
    connection.execute(
        "INSERT INTO offer (offer_key,shop_id,item_id,name,url,image_refs_json,"
        "main_theme_id,aux_theme_ids_json,first_seen,last_seen,facts_json) "
        "VALUES (?, 'resume-shop','resume-item','fixture','https://example.test/item',"
        "'[]', ?, '[]', ?, ?, '[]')",
        (offer_key, THEME_ID, "2026-10-14T13:00:00+09:00", "2026-10-14T13:00:00+09:00"),
    )
    connection.execute(
        "INSERT INTO draft (draft_id,offer_key,run_id,version,body,generated_at,"
        "committed_at,generator_version,pattern_version,status,artifact_ref,"
        "pattern_id,evidence_refs_json) VALUES ('D-RESUME-01', ?, ?, 1, 'fixture',"
        "'2026-10-14T13:00:00+09:00','2026-10-14T13:00:00+09:00','fixture',"
        "'fixture','committed',NULL,'P01','[]')",
        (offer_key, RUN_ID),
    )
    connection.execute(
        "UPDATE run SET committed_count=1 WHERE run_id=?", (RUN_ID,)
    )
    return offer_key


def zero_response(request):
    response = response_for(request)
    response["words"] = []
    return response


def set_theme(connection, name):
    connection.execute(
        "UPDATE settings_version SET themes_json=? WHERE version=?",
        (json.dumps([{"theme_id": THEME_ID, "name": name}]), SETTINGS_VERSION),
    )


def make_plan(connection, clock, port):
    return DurableTrendsThemePlanner(port, object(), clock).plan_theme(
        connection,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed="SEED-1",
    )


def validate(connection, plan):
    daily._validate_plan(plan, THEME_ID)
    daily._validate_plan_snapshot(
        connection,
        plan,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        seed="SEED-1",
        event_kind="initial",
        ordinal=1,
    )


def insert_second_selection(connection, clock):
    event = f"theme-selection:{RUN_ID}:initial:02"
    connection.execute(
        "INSERT INTO theme_selection (selection_id,run_id,theme_id,event_kind,"
        "count_event_id,seed,result_word_refs_json,selected_at) "
        "VALUES (?, ?, ?, 'initial', ?, 'SEED-1', '[]', ?)",
        (event, RUN_ID, "T02", event, clock.now_jst().isoformat()),
    )


def test_compound_query_is_saved_and_reused_as_theme_fallback(retry_case):
    connection, clock = retry_case
    set_theme(connection, "下着・靴下")
    port = SequencePort([zero_response])

    plan = make_plan(connection, clock, port)

    assert port.requests[0].parent_word in {"下着", "靴下"}
    assert plan.fallback.query == port.requests[0].parent_word
    assert plan.related == ()
    validate(connection, plan)
    stored = connection.execute(
        "SELECT query_word,attempts_used,status FROM trends_acquisition"
    ).fetchone()
    assert tuple(stored) == (port.requests[0].parent_word, 1, "normal_zero")
    assert make_plan(connection, clock, port) == plan
    assert len(port.requests) == 1


def test_existing_full_theme_query_remains_usable_on_resume(retry_case):
    connection, clock = retry_case
    set_theme(connection, "下着・靴下")
    port = SequencePort([zero_response])
    session(connection, clock, port).fetch("下着・靴下")

    plan = make_plan(connection, clock, port)

    validate(connection, plan)
    assert plan.fallback.query == "下着・靴下"
    assert len(port.requests) == 1


def test_component_query_does_not_allow_changed_theme_fallback(retry_case):
    connection, clock = retry_case
    set_theme(connection, "下着・靴下")
    plan = make_plan(connection, clock, SequencePort([zero_response]))
    changed = replace(plan, fallback=replace(plan.fallback, query="別テーマ"))
    with pytest.raises(daily.DailyOrchestrationError):
        validate(connection, changed)


def test_component_query_does_not_allow_unrelated_saved_query(retry_case):
    connection, clock = retry_case
    set_theme(connection, "下着・靴下")
    plan = make_plan(connection, clock, SequencePort([zero_response]))
    connection.execute(
        "UPDATE trends_acquisition SET query_word='無関係' WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    )
    with pytest.raises(daily.DailyOrchestrationError):
        validate(connection, plan)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("スキンケア", ("スキンケア",)),
        ("下着・靴下", ("下着", "靴下")),
        ("調味料・加工食品・惣菜", ("調味料", "加工食品", "惣菜")),
        ("ペットケア・用品", ("ペットケア", "ペット用品")),
        ("美容ツール・器具", ("美容ツール", "美容器具")),
        ("ボディ・セルフケア", ("ボディケア", "セルフケア")),
        ("ペット家電・見守り機器", ("ペット家電", "ペット見守り機器")),
        ("子ども移動・車載安全用品", ("子ども移動", "子ども車載安全用品")),
        (" 靴 ・ 履物 ・ 靴 ", ("靴", "履物")),
    ],
)
def test_component_candidates_preserve_theme_context(name, expected):
    assert theme_query_candidates(name) == expected
    selected = choose_theme_query(name, "SEED-1", ACQUISITION_ID)
    assert selected in expected
    assert choose_theme_query(name, "SEED-1", ACQUISITION_ID) == selected


def test_component_selection_uses_local_seed_and_can_choose_each_component():
    import random

    state = random.getstate()
    words = {
        choose_theme_query("下着・靴下", f"SEED-{index}", ACQUISITION_ID)
        for index in range(50)
    }
    assert words == {"下着", "靴下"}
    assert random.getstate() == state


def test_component_snapshot_feeds_key_and_room_without_changing_raw_words(retry_case):
    connection, clock = retry_case
    set_theme(connection, "下着・靴下")
    port = SequencePort([response_for])
    port.fixture_only = True
    key = FixtureKeywordRoot()
    planner = DurableTrendsThemePlanner(port, key, clock)
    plan = planner.plan_theme(
        connection,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed="SEED-1",
    )
    validate(connection, plan)
    assert key.requests[0].input_payload["keywords"]["query_word"] in {"下着", "靴下"}
    assert [item.query for item in plan.related] == ["電子レンジ 調理"]
    tasks = daily._make_tasks([plan], phase="initial", first_number=1)
    assert [item["query"] for item in tasks] == ["電子レンジ 調理"]
    assert plan.fallback is None


def test_raw_trends_hits_with_no_key_selection_do_not_use_query_fallback():
    plan = DurableTrendsThemePlanner._plan(
        THEME_ID,
        "肉・魚・たんぱく食材",
        "initial",
        1,
        ACQUISITION_ID,
        status="observed",
        snapshot_ref="snapshot-with-raw-hits",
        selected=(),
        query_word="肉",
        has_related_words=True,
    )

    assert plan.related == ()
    assert plan.fallback is None
    assert daily._make_tasks([plan], phase="initial", first_number=1) == []


def test_compound_theme_fallback_uses_exact_trends_parent_query(retry_case):
    connection, clock = retry_case
    set_theme(connection, "肉・魚・たんぱく食材")
    plan = make_plan(connection, clock, SequencePort([zero_response]))

    tasks = daily._make_tasks([plan], phase="initial", first_number=1)

    acquisition_id = plan.acquisition_ref
    assert len(tasks) == 1
    assert tasks[0]["query"] == plan.fallback.query
    assert tasks[0]["query_kind"] == "theme_fallback"
    assert tasks[0]["query"] in theme_query_candidates("肉・魚・たんぱく食材")
    stored = connection.execute(
        "SELECT query_word FROM trends_acquisition WHERE acquisition_id=?",
        (acquisition_id,),
    ).fetchone()
    assert tasks[0]["query"] == stored[0]


def test_resume_migrates_legacy_fallback_plan_and_task_to_saved_trends_query(
    resume_runtime, retry_case, monkeypatch
):
    case, bundle = resume_runtime
    connection, clock = retry_case
    set_theme(connection, "肉・魚・たんぱく食材")
    plan = make_plan(connection, clock, SequencePort([zero_response]))
    saved_query = connection.execute(
        "SELECT query_word FROM trends_acquisition WHERE acquisition_id=?",
        (plan.acquisition_ref,),
    ).fetchone()[0]
    assert saved_query in theme_query_candidates("肉・魚・たんぱく食材")
    assert plan.fallback.query == saved_query
    committed_offer_key = _insert_committed_resume_draft(connection)
    original_state = _legacy_resume_state(
        connection,
        plan,
        task_cursor=0,
        committed_offer_key=committed_offer_key,
    )
    old_task_id = original_state["tasks"][0]["task_id"]
    searched = []

    def observe_search(_connection, *, run_id, task, state, **_kwargs):
        searched.append(dict(task))
        state["source_stopped"] = True
        state["source_stopped_reason"] = "fixture_search_observed"
        daily._save_state(connection, run_id, state)

    monkeypatch.setattr(daily, "_search_task", observe_search)
    outcome = daily.run_daily(
        connection,
        data_root=case.data_root,
        ports=bundle.ports,
        clock=clock,
        run_id_factory=lambda _target: RUN_ID,
        seed_factory=lambda: "SEED-1",
        manual=True,
        target_date="2026-10-14",
    )

    assert searched == [
        {
            **original_state["tasks"][0],
            "query": saved_query,
        }
    ]
    saved_root = json.loads(
        connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )
    state = saved_root["daily_orchestration"]
    assert state["plans"]["initial:01"]["fallback"]["query"] == saved_query
    assert state["tasks"][0]["task_id"] == old_task_id
    assert state["tasks"][0]["query"] == saved_query
    assert state["task_cursor"] == 1
    assert state["processed_attempts"] == original_state["processed_attempts"]
    assert state["handled_offer_keys"] == [committed_offer_key]
    assert any(
        item.get("offer_key") == committed_offer_key and item.get("status") == "committed"
        for item in state["selected_products"]
    )
    assert outcome.committed_count == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM draft WHERE run_id=?", (RUN_ID,)
    ).fetchone()[0] == 1


def test_resume_removes_legacy_parent_fallback_plan_and_task_when_related_queries_exist(
    resume_runtime, retry_case, monkeypatch
):
    case, bundle = resume_runtime
    connection, clock = retry_case
    set_theme(connection, "肉・魚・たんぱく食材")
    port = SequencePort([response_for])
    port.fixture_only = True
    plan = DurableTrendsThemePlanner(port, FixtureKeywordRoot(), clock).plan_theme(
        connection,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed="SEED-1",
    )
    assert len(plan.related) == 1
    assert plan.fallback is None
    legacy_plan = replace(
        plan,
        fallback=daily.DailySearchPlan(
            task_id=f"{THEME_ID}-fallback-initial-01",
            theme_id=THEME_ID,
            query="肉・魚・たんぱく食材",
            parent_refs=(THEME_ID,),
            level="theme_fallback",
            kind="theme_fallback",
        ),
    )
    committed_offer_key = _insert_committed_resume_draft(connection)
    original_state = _legacy_resume_state(
        connection,
        legacy_plan,
        task_cursor=1,
        committed_offer_key=committed_offer_key,
    )
    processed_task = dict(original_state["tasks"][0])
    processed_attempts = list(original_state["processed_attempts"])
    legacy_fallback_task_id = original_state["tasks"][1]["task_id"]
    searched = []

    def observe_search(_connection, *, run_id, task, state, **_kwargs):
        searched.append(dict(task))
        state["source_stopped"] = True
        state["source_stopped_reason"] = "fixture_search_observed"
        daily._save_state(connection, run_id, state)

    monkeypatch.setattr(daily, "_search_task", observe_search)
    outcome = daily.run_daily(
        connection,
        data_root=case.data_root,
        ports=bundle.ports,
        clock=clock,
        run_id_factory=lambda _target: RUN_ID,
        seed_factory=lambda: "SEED-1",
        manual=True,
        target_date="2026-10-14",
    )

    saved_root = json.loads(
        connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )
    state = saved_root["daily_orchestration"]
    assert searched == []
    assert state["plans"]["initial:01"]["fallback"] is None
    assert state["tasks"] == [processed_task]
    assert state["tasks"][0]["task_id"] != legacy_fallback_task_id
    assert state["task_cursor"] == 1
    assert state["processed_attempts"] == processed_attempts
    assert state["handled_offer_keys"] == [committed_offer_key]
    assert outcome.committed_count == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM draft WHERE run_id=?", (RUN_ID,)
    ).fetchone()[0] == 1


def test_resume_removes_legacy_parent_fallback_when_raw_hits_have_no_key_selection(
    resume_runtime, retry_case, monkeypatch
):
    case, bundle = resume_runtime
    connection, clock = retry_case
    theme_word = "肉・魚・たんぱく食材"
    set_theme(connection, theme_word)

    class RejectAllKeywordRoot(FixtureKeywordRoot):
        def run(self, request):
            result = super().run(request)
            child = result.subjobs[0]
            child.payload = {
                **child.payload,
                "decisions": [
                    {
                        "word_id": word["word_id"],
                        "decision": "query_same",
                        "group_ref": None,
                        "reason": "fixture excludes all related terms",
                    }
                    for word in request.input_payload["keywords"]["words"]
                ],
            }
            return result

    port = SequencePort([response_for])
    port.fixture_only = True
    keyword_root = RejectAllKeywordRoot()
    plan = DurableTrendsThemePlanner(port, keyword_root, clock).plan_theme(
        connection,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed="SEED-1",
    )
    assert keyword_root.requests[0].input_payload["keywords"]["words"]
    assert plan.related == ()
    legacy_plan = replace(
        plan,
        fallback=daily.DailySearchPlan(
            task_id=f"{THEME_ID}-fallback-initial-01",
            theme_id=THEME_ID,
            query=theme_word,
            parent_refs=(THEME_ID,),
            level="theme_fallback",
            kind="theme_fallback",
        ),
    )
    legacy_fallback_task = daily._task(
        legacy_plan.fallback, phase="initial", ordinal=1
    )
    committed_offer_key = _insert_committed_resume_draft(connection)
    original_state = _legacy_resume_state(
        connection,
        legacy_plan,
        task_cursor=0,
        committed_offer_key=committed_offer_key,
        tasks=[legacy_fallback_task],
    )
    assert len(original_state["tasks"]) == 1
    assert original_state["tasks"][0]["query"] == theme_word
    searched = []

    def observe_search(_connection, *, run_id, task, state, **_kwargs):
        searched.append(dict(task))
        state["source_stopped"] = True
        state["source_stopped_reason"] = "fixture_search_observed"
        daily._save_state(connection, run_id, state)

    monkeypatch.setattr(daily, "_search_task", observe_search)
    outcome = daily.run_daily(
        connection,
        data_root=case.data_root,
        ports=bundle.ports,
        clock=clock,
        run_id_factory=lambda _target: RUN_ID,
        seed_factory=lambda: "SEED-1",
        manual=True,
        target_date="2026-10-14",
    )

    saved_root = json.loads(
        connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )
    state = saved_root["daily_orchestration"]
    assert searched == []
    assert state["plans"]["initial:01"]["fallback"] is None
    assert state["tasks"] == []
    assert state["task_cursor"] == 0
    assert state["processed_attempts"] == original_state["processed_attempts"]
    assert state["handled_offer_keys"] == [committed_offer_key]
    assert outcome.committed_count == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM draft WHERE run_id=?", (RUN_ID,)
    ).fetchone()[0] == 1


def test_failed_component_acquisition_uses_trends_query_fallback(retry_case):
    connection, clock = retry_case
    set_theme(connection, "下着・靴下")
    port = SequencePort(
        [
            lambda request: response_for(
                request, status="unavailable", error="source_timeout"
            )
        ]
        * 5
    )
    plan = make_plan(connection, clock, port)
    assert plan.acquisition_status == "exhausted"
    assert len(port.requests) == 5
    assert len({item.parent_word for item in port.requests}) == 1
    assert port.requests[0].parent_word in {"下着", "靴下"}
    assert plan.fallback.query == port.requests[0].parent_word
    validate(connection, plan)


def test_public_source_interval_survives_new_theme_session(retry_case):
    connection, clock = retry_case
    first_port = SequencePort([zero_response])
    first_port.trends_min_interval_seconds = 180
    session(connection, clock, first_port).fetch("下着")
    start = clock.now_jst()
    next_port = SequencePort([zero_response])
    next_port.trends_min_interval_seconds = 180
    insert_second_selection(connection, clock)
    next_session = DurableGroupKeywordSession(
        connection,
        next_port,
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=2,
        theme_id="T02",
        seed="SEED-1",
        acquisition_id=f"{RUN_ID}:initial:02:T02",
    )
    next_session.fetch("靴下")
    assert (clock.now_jst() - start).total_seconds() == 180
    assert len(next_port.requests) == 1


@pytest.mark.parametrize("retry_after,expected_wait", [(None, 180), (240, 240)])
def test_public_source_interval_applies_to_retries(
    retry_case, retry_after, expected_wait
):
    connection, clock = retry_case

    def unavailable(request):
        result = response_for(request, status="unavailable", error="http_429")
        if retry_after is not None:
            result["retry_after_seconds"] = retry_after
        return result

    port = SequencePort([unavailable, zero_response])
    port.trends_min_interval_seconds = 180
    start = clock.now_jst()
    session(connection, clock, port).fetch("下着")
    assert (clock.now_jst() - start).total_seconds() == expected_wait


def test_public_source_interval_does_not_send_request_beyond_deadline(retry_case):
    connection, clock = retry_case
    port = SequencePort([zero_response])
    port.trends_min_interval_seconds = 180
    session(connection, clock, port).fetch("下着")
    start = clock.now_jst()
    next_port = SequencePort([])
    next_port.trends_min_interval_seconds = 180
    insert_second_selection(connection, clock)
    next_session = DurableGroupKeywordSession(
        connection,
        next_port,
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=2,
        theme_id="T02",
        seed="SEED-1",
        acquisition_id=f"{RUN_ID}:initial:02:T02",
        deadline_at=(start + timedelta(seconds=90)).isoformat(),
    )
    result = next_session.fetch("靴下")
    assert result.status == "deadline"
    assert result.attempts_used == 0
    assert next_port.requests == []
    assert clock.now_jst() == start


@pytest.mark.parametrize("recover_saved_response", [False, True])
def test_final_retry_after_applies_to_next_theme_after_restart(
    retry_case, recover_saved_response
):
    connection, clock = retry_case
    saved = {}

    def ordinary_failure(request):
        return response_for(request, status="unavailable", error="source_timeout")

    def final_failure(request):
        result = response_for(request, status="unavailable", error="http_429")
        result["retry_after_seconds"] = 600
        if recover_saved_response:
            saved[request.request_id] = result
            raise KeyboardInterrupt()
        return result

    port = SequencePort([ordinary_failure] * 4 + [final_failure])
    port.trends_min_interval_seconds = 180
    if recover_saved_response:
        with pytest.raises(KeyboardInterrupt):
            session(connection, clock, port).fetch("下着")
        resumed = SequencePort([], saved_responses=saved)
        resumed.trends_min_interval_seconds = 180
        result = session(connection, clock, resumed).fetch("下着")
        assert resumed.requests == []
    else:
        result = session(connection, clock, port).fetch("下着")
    assert result.status == "exhausted" and result.attempts_used == 5
    start = clock.now_jst()
    insert_second_selection(connection, clock)
    next_port = SequencePort([zero_response])
    next_port.trends_min_interval_seconds = 180
    next_session = DurableGroupKeywordSession(
        connection,
        next_port,
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=2,
        theme_id="T02",
        seed="SEED-1",
        acquisition_id=f"{RUN_ID}:initial:02:T02",
    )
    next_session.fetch("靴下")
    assert (clock.now_jst() - start).total_seconds() == 600
