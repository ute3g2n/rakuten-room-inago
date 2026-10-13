"""Sequential theme search, unlimited numeric count and Q1.4 resume contracts."""

from dataclasses import replace
import json

import pytest

from automation.inago import orchestrator as daily
from automation.inago import trends_adapter as trends
from tests_inago.test_step13 import (
    runtime_case,
    _make_ports,
    _run,
    FakeRoomPort,
    RUN_ID,
)


from tests_inago.fixtures_q14 import CurrentPlanner


class CurrentRoom(FakeRoomPort):
    def __init__(self, clock, *, counts=(5,), fail=False):
        super().__init__(clock)
        self.counts = counts
        self.per_theme = {}
        self.fail = fail

    def search(self, request):
        theme = request.parent_refs[0]
        index = self.per_theme.get(theme, 0)
        self.per_theme[theme] = index + 1
        self.posts_per_search = self.counts[index % len(self.counts)]
        self.status = "unavailable" if self.fail else "observed"
        result = super().search(request)
        result.update(
            query_kind=request.query_kind,
            keyword_snapshot_ref=request.keyword_snapshot_ref,
            selected_word_ref=request.selected_word_ref,
            loaded_posts=len(result["posts"]),
            condition_evidence_ref=(
                f"COND-{request.attempt_no}" if not self.fail else None
            ),
        )
        for number, item in enumerate(result["posts"], index * 10 + 1):
            item_id = f"item-{number:02}"
            item.update(
                likes=None,
                item_url=f"https://item.rakuten.co.jp/shop-{theme.lower()}/{item_id}/",
                observed_item_id=item_id,
            )
        return result


def current_bundle(
    case, *, words=5, counts=(5,), keyword_fail_at=None, room_fail=False
):
    bundle = _make_ports(case)
    planner = CurrentPlanner(case, word_count=words, fail_at=keyword_fail_at)
    room = CurrentRoom(case.clock, counts=counts, fail=room_fail)
    bundle.planner = planner
    bundle.room = room
    bundle.ports = replace(bundle.ports, theme_planner=planner, room_port=room)
    return bundle


def test_first_related_word_fills_each_theme_and_does_not_prefetch_next_theme(
    runtime_case,
):
    bundle = current_bundle(runtime_case)

    def before_search(request):
        assert len(bundle.planner.calls) == len(bundle.room.requests)

    bundle.room.before_search = before_search
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "complete" and outcome.committed_count == 15
    assert outcome.attempt_used == 3 and len(bundle.room.requests) == 3
    assert all(item.query_kind == "related" for item in bundle.room.requests)
    assert len(bundle.planner.calls) == 3
    assert bundle.metrics.started_at == ["2026-10-12T13:00:00+09:00"]


def test_each_theme_uses_related_queue_then_theme_fallback_before_next_theme(
    runtime_case,
):
    bundle = current_bundle(runtime_case, counts=(1, 0, 1, 0, 0, 1))
    outcome = _run(runtime_case, bundle)
    requests = bundle.room.requests
    assert outcome.committed_count == 15 and outcome.attempt_used > 9
    for group in range(3):
        six = requests[group * 6 : (group + 1) * 6]
        assert len({item.parent_refs[0] for item in six}) == 1
        assert [item.query_kind for item in six] == ["related"] * 5 + ["theme_fallback"]
    assert len({item.parent_refs[0] for item in requests}) >= 4
    assert [item.attempt_no for item in requests] == list(range(1, len(requests) + 1))


@pytest.mark.parametrize("words", [0, 3])
def test_fewer_groups_use_actual_queue_and_single_fallback_without_new_words(
    runtime_case, words
):
    bundle = current_bundle(runtime_case, words=words, counts=(0,))
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "shortfall" and outcome.committed_count == 0
    assert len(bundle.planner.calls) == 30
    assert len(set(bundle.planner.calls)) == 30
    assert outcome.attempt_used == 30 * (words + 1)
    assert outcome.attempt_used > 9
    assert len({item.query for item in bundle.room.requests}) == len(
        bundle.room.requests
    )


def test_keyword_failure_goes_to_metrics_without_fallback_or_next_theme(runtime_case):
    bundle = current_bundle(runtime_case, keyword_fail_at=1)
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "failed" and outcome.attempt_used == 0
    assert len(bundle.planner.calls) == 1 and bundle.room.requests == []
    assert bundle.metrics.calls


def test_room_failure_has_finite_retries_and_does_not_start_other_queries(runtime_case):
    bundle = current_bundle(runtime_case, room_fail=True)
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "failed" and outcome.attempt_used == 3
    assert len({item.query for item in bundle.room.requests}) == 1
    assert bundle.metrics.calls


def test_old_checkpoint_is_not_relabelled_as_current_search_rules(runtime_case):
    from automation.inago.orchestrator import _daily_state

    with pytest.raises(daily.DailyOrchestrationError, match="version"):
        _daily_state(
            json.dumps(
                {"daily_orchestration": {"schema_version": 1, "phase": "initial"}}
            )
        )


def test_new_checkpoint_saves_rules_raw_order_and_no_nine_limit(runtime_case):
    bundle = current_bundle(runtime_case)
    outcome = _run(runtime_case, bundle)
    state = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert state["schema_version"] == 2 and state["rules_version"] == "requirements-1.4"
    assert all(
        plan["rules_version"] == "requirements-1.4" for plan in state["plans"].values()
    )
    assert all(
        task["query_kind"] == "related" or task["query_kind"] == "theme_fallback"
        for task in state["tasks"]
    )
    assert len(state["offer_theme_ids"]) == 15
    assert outcome.committed_count == 15


def test_recovery_preserves_twelve_used_searches_and_permits_remaining_work():
    from automation.inago.recovery import plan_resume
    from tests_inago.test_step16 import _resume_snapshot, JST
    from datetime import datetime

    result = plan_resume(
        _resume_snapshot(search_attempts=12),
        now=datetime(2026, 10, 13, 13, 35, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    assert result["preserved"]["search_attempts"] == 12
    assert result["allowed"]["search"] is True


def test_initial_eleven_are_replenished_by_four_from_one_new_theme(runtime_case):
    bundle = current_bundle(runtime_case, counts=(1, 0, 1, 0, 0, 1))

    def before_search(request):
        themes = list(bundle.room.per_theme)
        if len(themes) == 1:
            bundle.room.posts_per_search = 5
        elif len(themes) == 4:
            bundle.room.posts_per_search = 4

    bundle.room.before_search = before_search
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "complete" and outcome.committed_count == 15
    assert bundle.planner.counts_before_plan == [0, 5, 8, 11]
    assert len(bundle.planner.calls) == 4
    assert outcome.attempt_used == 14
    state = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert state["theme_targets"][bundle.planner.calls[-1]] == 4


def test_fifty_source_rows_are_saved_but_only_five_per_theme_are_assigned(runtime_case):
    bundle = current_bundle(runtime_case, counts=(80,))
    outcome = _run(runtime_case, bundle)
    assert outcome.committed_count == 15 and outcome.attempt_used == 3
    assert len(bundle.runner.requests) == 15
    rows = runtime_case.connection.execute(
        "SELECT observed_posts,loaded_posts,end_reason FROM search_attempt WHERE run_id=? ORDER BY attempt_no",
        (RUN_ID,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [(50, 80, "limit_reached")] * 3
    state = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert len(state["offer_theme_ids"]) == 15


def test_old_persisted_run_is_rejected_before_mutating_its_status(runtime_case):
    bundle = current_bundle(runtime_case)
    _run(runtime_case, bundle)
    old = json.dumps({"daily_orchestration": {"schema_version": 1, "phase": "initial"}})
    runtime_case.connection.execute(
        "UPDATE run SET status='failed',checkpoint=? WHERE run_id=?", (old, RUN_ID)
    )
    before = tuple(
        runtime_case.connection.execute(
            "SELECT status,checkpoint,attempt_used,committed_count FROM run WHERE run_id=?",
            (RUN_ID,),
        ).fetchone()
    )
    with pytest.raises(daily.DailyOrchestrationError, match="version"):
        _run(runtime_case, bundle)
    after = tuple(
        runtime_case.connection.execute(
            "SELECT status,checkpoint,attempt_used,committed_count FROM run WHERE run_id=?",
            (RUN_ID,),
        ).fetchone()
    )
    assert after == before


def test_theme_plan_cannot_replace_saved_original_expression(runtime_case):
    bundle = current_bundle(runtime_case)
    original = bundle.planner.plan_theme

    def changed(connection, **values):
        plan = original(connection, **values)
        return replace(
            plan,
            related=(
                replace(plan.related[0], query="AIが作った未取得語"),
                *plan.related[1:],
            ),
        )

    bundle.planner.plan_theme = changed
    result = _run(runtime_case, bundle)
    assert result.status == "failed" and result.attempt_used == 0
    assert bundle.room.requests == [] and bundle.runner.requests == []


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "old_fields",
        "missing_fallback",
        "foreign_theme",
        "wrong_rules",
        "wrong_kind",
        "foreign_word",
        "wrong_order",
        "fallback_word",
        "duplicate_task",
        "bad_lists",
        "empty_query",
    ],
)
def test_resume_from_saved_plan_checks_contents_before_queueing(
    runtime_case, monkeypatch, fault
):
    bundle = current_bundle(runtime_case)
    original = daily._make_tasks

    def interrupt(*args, **kwargs):
        raise RuntimeError("fixture interruption after plan save")

    monkeypatch.setattr(daily, "_make_tasks", interrupt)
    first = _run(runtime_case, bundle)
    assert first.attempt_used == 0 and len(bundle.planner.calls) == 1
    root = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )
    plan = root["daily_orchestration"]["plans"]["initial:01"]
    if fault == "old_fields":
        plan["combined"] = None
    elif fault == "missing_fallback":
        del plan["fallback"]
    elif fault == "foreign_theme":
        plan["related"][0]["theme_id"] = "OTHER"
    elif fault == "wrong_rules":
        plan["rules_version"] = "requirements-1.3"
    elif fault == "wrong_kind":
        plan["related"][0]["kind"] = "parent"
    elif fault == "foreign_word":
        plan["related"][0]["selected_word_ref"] = "OTHER"
    elif fault == "wrong_order":
        plan["keyword_refs"].reverse()
    elif fault == "fallback_word":
        plan["fallback"]["selected_word_ref"] = "W1"
    elif fault == "duplicate_task":
        plan["related"][1]["task_id"] = plan["related"][0]["task_id"]
    elif fault == "bad_lists":
        plan["related"] = "invalid"
    elif fault == "empty_query":
        plan["related"][0]["query"] = ""
    runtime_case.connection.execute(
        "UPDATE run SET checkpoint=? WHERE run_id=?", (json.dumps(root), RUN_ID)
    )
    monkeypatch.setattr(daily, "_make_tasks", original)
    second = _run(runtime_case, bundle)
    if fault == "none":
        assert second.status == "complete" and second.committed_count == 15
        assert len(bundle.planner.calls) == 3
    else:
        assert second.status == "failed" and second.attempt_used == 0
        assert len(bundle.planner.calls) == 1 and bundle.room.requests == []
