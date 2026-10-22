"""Component Trends queries retain the original theme and durable identity."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from automation.inago.daily_theme_planner import DurableTrendsThemePlanner
from automation.inago import orchestrator as daily
from automation.inago.theme_queries import choose_theme_query, theme_query_candidates
from automation.inago.trends_adapter import DurableGroupKeywordSession
from tests_inago.test_step21_daily import FixtureKeywordRoot
from tests_inago.test_step13 import SETTINGS_VERSION
from tests_inago.test_step21_trends_retry import (
    ACQUISITION_ID,
    RUN_ID,
    THEME_ID,
    SequencePort,
    response_for,
    retry_case,
    session,
)


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


def test_compound_query_is_saved_and_reused_with_original_theme_fallback(retry_case):
    connection, clock = retry_case
    set_theme(connection, "下着・靴下")
    port = SequencePort([zero_response])

    plan = make_plan(connection, clock, port)

    assert port.requests[0].parent_word in {"下着", "靴下"}
    assert plan.fallback.query == "下着・靴下"
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
    assert [item["query"] for item in tasks] == ["電子レンジ 調理", "下着・靴下"]
    assert plan.fallback.query == "下着・靴下"


def test_failed_component_acquisition_keeps_original_theme_fallback(retry_case):
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
    assert plan.fallback.query == "下着・靴下"
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
