"""Theme replenishment depends on committed bodies, not selected candidates."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from automation.inago import orchestrator
from tests_inago.test_step13 import _make_ports, _run as run_legacy
from tests_inago.test_product_page_selection import (
    _FixtureOCR,
    _enable_product_page_pipeline,
    _run as run_pipeline,
    runtime_case,
)


def _committed_for_theme(case, theme_id):
    return case.connection.execute(
        "SELECT COUNT(*) FROM draft JOIN offer USING(offer_key) "
        "WHERE offer.main_theme_id=?",
        (theme_id,),
    ).fetchone()[0]


@pytest.mark.parametrize("pipeline", [False, True], ids=["legacy", "product-page"])
@pytest.mark.parametrize("related_bodies", [0, 3, 4, 5])
def test_related_exhaustion_replenishes_only_missing_committed_bodies(
    runtime_case, pipeline, related_bodies
):
    bundle = _make_ports(runtime_case, untried=True, posts_per_search=5)
    original_search = bundle.room.search
    first_theme = []
    first_requests = []
    counts_at_fallback = []
    counts_before_next_theme = []

    def search(request):
        theme = request.parent_refs[0]
        if not first_theme:
            first_theme.append(theme)
        response = original_search(request)
        if theme == first_theme[0]:
            first_requests.append(request)
            if request.query_kind == "related":
                related_no = sum(x.query_kind == "related" for x in first_requests)
                start, end = (0, min(2, related_bodies)) if related_no == 1 else (
                    min(2, related_bodies), related_bodies
                )
            else:
                counts_at_fallback.append(_committed_for_theme(runtime_case, theme))
                start, end = related_bodies, 5
            response["posts"] = response["posts"][start:end]
            response["loaded_posts"] = len(response["posts"])
        elif not counts_before_next_theme:
            counts_before_next_theme.append(
                _committed_for_theme(runtime_case, first_theme[0])
            )
        return response

    bundle.room.search = search
    if pipeline:
        _enable_product_page_pipeline(bundle, runtime_case)
        outcome = run_pipeline(
            runtime_case, bundle, _FixtureOCR(), run_id="FALLBACK-COUNTS"
        )
    else:
        outcome = run_legacy(runtime_case, bundle, run_id="FALLBACK-COUNTS")

    expected_kinds = ["related", "related"]
    if related_bodies < 5:
        expected_kinds.append("theme_fallback")
    assert [x.query_kind for x in first_requests] == expected_kinds
    assert counts_at_fallback == ([related_bodies] if related_bodies < 5 else [])
    assert counts_before_next_theme == [5]
    assert _committed_for_theme(runtime_case, first_theme[0]) == 5
    assert outcome.status == "complete" and outcome.committed_count == 15
    fallback = [x for x in first_requests if x.query_kind == "theme_fallback"]
    for request in fallback:
        assert request.query == f"theme::{first_theme[0]}"
        assert request.likes_min == 300
        assert request.keyword_snapshot_ref is None
        assert request.selected_word_ref is None
    assert len(bundle.runner.requests) == 15


@pytest.mark.parametrize("extra_candidate", [False, True], ids=["needs-fallback", "reuse-result"])
def test_selected_five_with_copy_omission_replenishes_to_five_bodies(
    runtime_case, monkeypatch, extra_candidate
):
    bundle = _make_ports(runtime_case, posts_per_search=6)
    original_search = bundle.room.search
    first_theme = []
    first_requests = []
    counts_at_fallback = []

    def search(request):
        theme = request.parent_refs[0]
        if not first_theme:
            first_theme.append(theme)
        response = original_search(request)
        if theme == first_theme[0]:
            first_requests.append(request)
            if request.query_kind == "related":
                response["posts"] = response["posts"][:6 if extra_candidate else 5]
            else:
                counts_at_fallback.append(_committed_for_theme(runtime_case, theme))
                response["posts"] = response["posts"][5:6]
        else:
            response["posts"] = response["posts"][:5]
        response["loaded_posts"] = len(response["posts"])
        return response

    bundle.room.search = search
    _enable_product_page_pipeline(bundle, runtime_case)
    generate = orchestrator.generate_and_commit_copy
    omitted = []

    def omit_first(connection, spec, runner, **kwargs):
        if not omitted:
            omitted.append(spec.offer_key)
            return SimpleNamespace(status="insufficient_information", error_code=None)
        assert spec.offer_key not in omitted
        return generate(connection, spec, runner, **kwargs)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", omit_first)
    outcome = run_pipeline(
        runtime_case, bundle, _FixtureOCR(), run_id="FALLBACK-COPY-OMISSION"
    )

    assert [x.query_kind for x in first_requests] == (
        ["related"] if extra_candidate else ["related", "theme_fallback"]
    )
    assert counts_at_fallback == ([] if extra_candidate else [4])
    assert _committed_for_theme(runtime_case, first_theme[0]) == 5
    assert outcome.status == "complete" and outcome.committed_count == 15
    assert len(bundle.runner.requests) == 15


@pytest.mark.parametrize("related_task", [False, True], ids=["empty-key-selection", "related-consumed"])
def test_resume_restores_missing_fallback_without_renumbering(related_task):
    related = orchestrator.DailySearchPlan(
        "related-a", "A", "related-a", ("A",), "related", "related", "snapshot-a", "word-a"
    )
    fallback = orchestrator.DailySearchPlan(
        "fallback-a", "A", "theme-a", ("A",), "theme_fallback", "theme_fallback"
    )
    plan = orchestrator.DailyThemePlan(
        "A", ("word-a",) if related_task else (),
        (related,) if related_task else (), fallback, "snapshot-a"
    )
    processed = orchestrator._task(related, phase="initial", ordinal=7)
    next_related = orchestrator._task(
        orchestrator.DailySearchPlan(
            "related-b", "B", "related-b", ("B",), "related", "related", "snapshot-b", "word-b"
        ), phase="initial", ordinal=8
    )
    state = {
        "tasks": [processed, next_related] if related_task else [],
        "task_cursor": 1 if related_task else 0,
        "initial_theme_ids": ["A", "B"],
        "initial_plan_index": 2 if related_task else 1,
        "additional_events": 0,
        "plans": {"initial:01": {"theme_id": "A"}, "initial:02": {"theme_id": "B"}},
    }

    orchestrator._reconcile_pending_theme_tasks(state, plan=plan, event_kind="initial")
    first = [dict(task) for task in state["tasks"]]
    orchestrator._reconcile_pending_theme_tasks(state, plan=plan, event_kind="initial")

    assert state["tasks"] == first
    fallback_tasks = [task for task in state["tasks"] if task["kind"] == "theme_fallback"]
    assert len(fallback_tasks) == 1
    assert fallback_tasks[0]["query"] == "theme-a"
    assert fallback_tasks[0]["selected_word_ref"] is None
    if related_task:
        assert state["tasks"][0] == processed
        assert state["tasks"][2] == next_related
        assert fallback_tasks[0]["task_number"] == 9
    assert state["task_cursor"] == (1 if related_task else 0)


def test_plan_saved_before_task_append_does_not_add_fallback_twice():
    fallback = orchestrator.DailySearchPlan(
        "fallback-a", "A", "theme-a", ("A",), "theme_fallback", "theme_fallback"
    )
    plan = orchestrator.DailyThemePlan("A", (), (), fallback, "snapshot-a")
    state = {
        "tasks": [], "task_cursor": 0,
        "initial_theme_ids": ["A"], "initial_plan_index": 0,
        "additional_events": 0, "plans": {"initial:01": {"theme_id": "A"}},
    }
    orchestrator._reconcile_pending_theme_tasks(state, plan=plan, event_kind="initial")
    assert state["tasks"] == []


def test_resume_existing_selected_candidates_reuses_next_saved_product(
    runtime_case, monkeypatch
):
    bundle = _make_ports(runtime_case, posts_per_search=6)
    original_search = bundle.room.search
    first_theme = []
    first_requests = []

    def search(request):
        if not first_theme:
            first_theme.append(request.parent_refs[0])
        if request.parent_refs[0] == first_theme[0]:
            first_requests.append(request)
        response = original_search(request)
        if request.parent_refs[0] != first_theme[0]:
            response["posts"] = response["posts"][:5]
        response["loaded_posts"] = len(response["posts"])
        return response

    bundle.room.search = search
    _enable_product_page_pipeline(bundle, runtime_case)
    process_copies = orchestrator._process_selected_copies

    def interrupt_before_copy(*_args, **_kwargs):
        raise RuntimeError("fixture interruption with five selected candidates")

    monkeypatch.setattr(orchestrator, "_process_selected_copies", interrupt_before_copy)
    ocr = _FixtureOCR()
    first = run_pipeline(
        runtime_case, bundle, ocr, run_id="FALLBACK-RESUME-ACTIVE"
    )
    assert first.committed_count == 0
    assert len(first_requests) == 1

    monkeypatch.setattr(orchestrator, "_process_selected_copies", process_copies)
    generate = orchestrator.generate_and_commit_copy
    omitted = []

    def omit_first(connection, spec, runner, **kwargs):
        if not omitted:
            omitted.append(spec.offer_key)
            return SimpleNamespace(status="insufficient_information", error_code=None)
        assert spec.offer_key not in omitted
        return generate(connection, spec, runner, **kwargs)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", omit_first)
    resumed = run_pipeline(
        runtime_case, bundle, ocr, run_id="FALLBACK-RESUME-ACTIVE"
    )

    assert resumed.status == "complete" and resumed.committed_count == 15
    assert _committed_for_theme(runtime_case, first_theme[0]) == 5
    assert [request.query_kind for request in first_requests] == ["related"]
    assert len(bundle.room.requests) == 3
    assert len(bundle.runner.requests) == 15


def test_resume_processed_related_search_commits_before_deciding_fallback(
    runtime_case, monkeypatch
):
    bundle = _make_ports(runtime_case, posts_per_search=5)
    _enable_product_page_pipeline(bundle, runtime_case)
    process_copies = orchestrator._process_selected_copies

    def interrupt_with_legacy_processed_checkpoint(connection, *, state, run_id, **_kwargs):
        source_attempt = state["selected_products"][0]["source_attempt_id"]
        state.setdefault("processed_attempts", []).append(source_attempt)
        state["task_cursor"] = 1
        orchestrator._save_state(connection, run_id, state)
        raise RuntimeError("fixture legacy deferred-copy checkpoint")

    monkeypatch.setattr(
        orchestrator, "_process_selected_copies", interrupt_with_legacy_processed_checkpoint
    )
    ocr = _FixtureOCR()
    first = run_pipeline(runtime_case, bundle, ocr, run_id="FALLBACK-RESUME-PROCESSED")
    assert first.committed_count == 0
    first_theme = bundle.room.requests[0].parent_refs[0]
    monkeypatch.setattr(orchestrator, "_process_selected_copies", process_copies)

    resumed = run_pipeline(runtime_case, bundle, ocr, run_id="FALLBACK-RESUME-PROCESSED")

    assert resumed.status == "complete" and resumed.committed_count == 15
    assert _committed_for_theme(runtime_case, first_theme) == 5
    assert [r.query_kind for r in bundle.room.requests if r.parent_refs[0] == first_theme] == ["related"]
    assert len(bundle.runner.requests) == 15
