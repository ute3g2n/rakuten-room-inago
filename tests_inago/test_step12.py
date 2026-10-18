"""Copy generation retry, identity, and save-resume contracts for STEP 12."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pytest

from automation.inago.ai_runner import compute_input_hash
from automation.inago.clock import FakeClock
from automation.inago.contracts import (
    AIJobRequest,
    AIJobResult,
    AISubjobResult,
    InjectedFailure,
    RejectedContract,
)
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.copy_service import (
    CopyGenerationSpec,
    generate_and_commit_copy,
)


JST = ZoneInfo("Asia/Tokyo")
RUN_ID = "STEP12-RUN"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
OFFER_KEY = "shop_test:item_A1"
NOW = "2026-10-12T13:01:00+09:00"


@dataclass
class RuntimeCase:
    connection: sqlite3.Connection
    data_root: Path
    clock: FakeClock


@pytest.fixture
def runtime_case(tmp_path: Path, fixture_config_path: Path) -> RuntimeCase:
    paths = resolve_runtime_paths(
        data_root=tmp_path / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    connection.execute(
        """
        INSERT INTO settings_version
            (version, confirmed_at, genres_json, themes_json, pattern_refs_json,
             change_reason, created_at)
        VALUES (?, NULL, '[]', '[]', '[\"P01\",\"P02\",\"P03\",\"P04\",\"P05\"]',
                NULL, ?)
        """,
        (SETTINGS_VERSION, NOW),
    )
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, '2026-10-12', 'daily', 'running', ?, 'STEP12-SEED',
                '2026-10-12T13:00:00+09:00', '2026-10-12T17:45:00+09:00',
                '2026-10-12T18:00:00+09:00', 0, 0, NULL, 'not_started', NULL)
        """,
        (RUN_ID, SETTINGS_VERSION),
    )
    seed_offer(connection, "A1")
    case = RuntimeCase(
        connection,
        paths.data_root,
        FakeClock(datetime(2026, 10, 12, 13, 1, tzinfo=JST)),
    )
    try:
        yield case
    finally:
        connection.close()


def seed_offer(connection: sqlite3.Connection, suffix: str) -> str:
    offer_key = f"shop_test:item_{suffix}"
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, 'shop_test', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        (
            offer_key,
            f"item_{suffix}",
            f"架空品{suffix}",
            f"https://example.invalid/items/{suffix}",
            NOW,
            NOW,
        ),
    )
    return offer_key


def make_spec(
    *,
    offer_key: str = OFFER_KEY,
    draft_version: int = 1,
    input_payload: Mapping[str, Any] | None = None,
) -> CopyGenerationSpec:
    payload = input_payload or {
        "offer": {"offer_key": offer_key, "draft_version": draft_version},
        "purpose": "商品情報を紹介する",
        "facts": [
            {
                "field": field,
                "value": None,
                "availability": "unknown",
                "source_ref": None,
            }
            for field in ("price", "stock", "shipping", "rating", "promotion")
        ],
        "author_experience": {"availability": "unknown", "value": None},
    }
    return CopyGenerationSpec(
        run_id=RUN_ID,
        offer_key=offer_key,
        draft_version=draft_version,
        target_date="2026-10-12",
        settings_version=SETTINGS_VERSION,
        input_refs=("INPUT-A1",),
        evidence_refs=("INPUT-A1",),
        input_payload=payload,
        deadline_at="2026-10-12T17:45:00+09:00",
        deadline_monotonic=10_000,
        generator_version="copy-service-v1",
        pattern_version=SETTINGS_VERSION,
    )


class FakeCopyRunner:
    def __init__(
        self,
        outcomes: list[str] | None = None,
        *,
        body: str = "fixture body for item A1",
        offer_key: str = OFFER_KEY,
        draft_version: int = 1,
        pattern_id: str = "P01",
        subjob_input_refs: tuple[str, ...] | None = None,
        include_scouting_subjobs: bool = False,
    ) -> None:
        self.outcomes = list(outcomes or ["success"])
        self.body = body
        self.offer_key = offer_key
        self.draft_version = draft_version
        self.pattern_id = pattern_id
        self.subjob_input_refs = subjob_input_refs
        self.include_scouting_subjobs = include_scouting_subjobs
        self.requests: list[AIJobRequest] = []

    def run(self, request: AIJobRequest) -> AIJobResult:
        self.requests.append(request)
        status = self.outcomes.pop(0) if self.outcomes else "error"
        started_at = "2026-10-12T13:01:00+09:00"
        finished_at = "2026-10-12T13:01:01+09:00"
        payload = {
            "offer_key": self.offer_key,
            "draft_version": self.draft_version,
            "body": self.body,
            "pattern_id": self.pattern_id,
            "evidence_refs": ["INPUT-A1"],
            "selection_reason": "確認できる商品情報に合わせた",
            "used_input_refs": ["INPUT-A1"],
            "omitted_unknowns": ["price", "stock", "shipping", "rating", "promotion"],
        }
        subjobs: tuple[AISubjobResult, ...] = ()
        if status in {"success", "empty", "error"} and status != "error":
            scouting = (
                (
                    AISubjobResult(
                        job_id=f"{request.job_id}-a{request.attempt}-obs",
                        parent_job_id=request.job_id,
                        run_id=request.run_id,
                        attempt=request.attempt,
                        task_id="T02",
                        agent_id="AG-OBS",
                        skill_name="rr-inago-observation",
                        target_date=request.target_date,
                        settings_version=request.settings_version,
                        input_refs=request.input_refs,
                        evidence_refs=request.evidence_refs,
                        input_hash="observation-input",
                        deadline_at=request.deadline_at,
                        result_status="success",
                        payload={"field": "likes", "value": None},
                        resolved_model="fixture-model",
                        reasoning_setting="fixture",
                        auth_mode="fixture",
                    ),
                    AISubjobResult(
                        job_id=f"{request.job_id}-a{request.attempt}-key",
                        parent_job_id=request.job_id,
                        run_id=request.run_id,
                        attempt=request.attempt,
                        task_id="T02",
                        agent_id="AG-KEY",
                        skill_name="rr-inago-keywords",
                        target_date=request.target_date,
                        settings_version=request.settings_version,
                        input_refs=request.input_refs,
                        evidence_refs=request.evidence_refs,
                        input_hash="keywords-input",
                        deadline_at=request.deadline_at,
                        result_status="success",
                        payload={"word_id": "WORD-1", "decision": "omit"},
                        resolved_model="fixture-model",
                        reasoning_setting="fixture",
                        auth_mode="fixture",
                    ),
                )
                if self.include_scouting_subjobs
                else ()
            )
            subjobs = (
                *scouting,
                AISubjobResult(
                    job_id=f"{request.job_id}-a{request.attempt}-child-01",
                    parent_job_id=request.job_id,
                    run_id=request.run_id,
                    attempt=request.attempt,
                    task_id="T02",
                    agent_id="AG-COPY",
                    skill_name="rr-inago-copywriter",
                    target_date=request.target_date,
                    settings_version=request.settings_version,
                    input_refs=self.subjob_input_refs or request.input_refs,
                    evidence_refs=request.evidence_refs,
                    input_hash="child-input-contract",
                    deadline_at=request.deadline_at,
                    result_status=status,
                    payload=payload,
                    resolved_model="fixture-model",
                    reasoning_setting="fixture",
                    auth_mode="fixture",
                ),
            )
        root_status = status
        return AIJobResult(
            job_id=request.job_id,
            run_id=request.run_id,
            attempt=request.attempt,
            task_id=request.task_id,
            agent_id="AI-O",
            skill_name="rakuten-room-inago",
            target_date=request.target_date,
            settings_version=request.settings_version,
            input_refs=request.input_refs,
            evidence_refs=request.evidence_refs,
            input_hash=compute_input_hash(request.input_payload),
            deadline_at=request.deadline_at,
            result_status=root_status,  # type: ignore[arg-type]
            adoption_status="pending",
            payload={"result_status": root_status},
            error={"code": "fixture_error"} if root_status == "error" else None,
            artifact=None,
            subjobs=subjobs,
            resolved_model="fixture-model",
            reasoning_setting="fixture",
            auth_mode="fixture",
            started_at=started_at,
            finished_at=finished_at,
        )


def run_copy(
    case: RuntimeCase,
    runner: FakeCopyRunner,
    *,
    spec: CopyGenerationSpec | None = None,
    export_notifier: Any = None,
):
    return generate_and_commit_copy(
        case.connection,
        spec or make_spec(),
        runner,
        data_root=case.data_root,
        clock=case.clock,
        export_notifier=export_notifier,
    )


def test_one_offer_commits_one_body_and_replay_never_regenerates(
    runtime_case: RuntimeCase,
) -> None:
    runner = FakeCopyRunner()
    notices: list[str] = []
    first = run_copy(
        runtime_case,
        runner,
        export_notifier=lambda result: notices.append(result.draft_id),
    )

    assert first.status == "committed", first
    assert first.commit_result is not None
    assert first.commit_result.committed_count == 1
    assert len(runner.requests) == 1
    assert runner.requests[0].trigger == "scheduled_daily"
    assert runner.requests[0].explicit_user_request is False
    assert runner.requests[0].copy_targets[0].offer_key == OFFER_KEY
    assert runner.requests[0].copy_targets[0].draft_version == 1
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id = ? AND offer_key = ?",
            (RUN_ID, OFFER_KEY),
        ).fetchone()[0]
        == 1
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM reservation WHERE offer_key = ? AND status = 'active'",
            (OFFER_KEY,),
        ).fetchone()[0]
        == 1
    )
    copy_job = runtime_case.connection.execute(
        """
        SELECT task_id, agent_id, skill_name, input_refs_json, input_hash,
               resolved_model, reasoning_setting, auth_mode, adoption_status
          FROM ai_job WHERE agent_id = 'AG-COPY' AND run_id = ?
        """,
        (RUN_ID,),
    ).fetchone()
    assert tuple(copy_job) == (
        "T02",
        "AG-COPY",
        "rr-inago-copywriter",
        '["INPUT-A1"]',
        "child-input-contract",
        "fixture-model",
        "fixture",
        "fixture",
        "accepted",
    )
    assert (
        runtime_case.connection.execute(
            "SELECT draft_id FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()["draft_id"]
        == first.commit_result.draft_id
    )

    replay = run_copy(runtime_case, FakeCopyRunner(["error"]))
    assert replay.status == "already_committed"
    assert replay.commit_result is not None and replay.commit_result.already_committed
    assert len(runner.requests) == 1
    assert (
        runtime_case.connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 1
    )
    assert len(notices) == 1


def test_generation_persists_all_successful_native_specialist_jobs_atomically(
    runtime_case: RuntimeCase,
) -> None:
    result = run_copy(
        runtime_case,
        FakeCopyRunner(include_scouting_subjobs=True),
    )

    assert result.status == "committed"
    rows = runtime_case.connection.execute(
        """
        SELECT agent_id, skill_name, resolved_model, reasoning_setting, auth_mode
          FROM ai_job
         WHERE run_id = ?
         ORDER BY agent_id
        """,
        (RUN_ID,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("AG-COPY", "rr-inago-copywriter", "fixture-model", "fixture", "fixture"),
        ("AG-KEY", "rr-inago-keywords", "fixture-model", "fixture", "fixture"),
        ("AG-OBS", "rr-inago-observation", "fixture-model", "fixture", "fixture"),
        ("AI-O", "rakuten-room-inago", "fixture-model", "fixture", "fixture"),
    ]


@pytest.mark.parametrize(
    "statuses", [["empty", "error", "success"], ["error", "error", "error"]]
)
def test_only_empty_or_generation_errors_retry_up_to_three_calls(
    runtime_case: RuntimeCase, statuses: list[str]
) -> None:
    runner = FakeCopyRunner(statuses)
    result = run_copy(runtime_case, runner)

    assert len(runner.requests) == 3
    assert [request.attempt for request in runner.requests] == [1, 2, 3]
    attempts = runtime_case.connection.execute(
        "SELECT attempt_no, outcome FROM generation_attempt WHERE run_id = ? AND offer_key = ? ORDER BY attempt_no",
        (RUN_ID, OFFER_KEY),
    ).fetchall()
    expected_outcomes = statuses
    assert [tuple(row) for row in attempts] == [
        (index, outcome if outcome != "success" else "success")
        for index, outcome in enumerate(expected_outcomes, start=1)
    ]
    assert result.status == (
        "committed" if statuses[-1] == "success" else "generation_failed"
    )

    fourth = run_copy(runtime_case, FakeCopyRunner(["success"]))
    assert fourth.status == (
        "already_committed" if statuses[-1] == "success" else "attempts_exhausted"
    )
    assert len(runner.requests) == 3
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ? AND offer_key = ?",
            (RUN_ID, OFFER_KEY),
        ).fetchone()[0]
        == 3
    )


@pytest.mark.parametrize(
    "statuses", [["empty", "error", "success"], ["error", "error", "error"]]
)
def test_specialist_empty_or_error_retries_when_orchestrator_succeeds(
    runtime_case: RuntimeCase, statuses: list[str]
) -> None:
    class SpecialistOutcomeRunner(FakeCopyRunner):
        def __init__(self, child_statuses: list[str]) -> None:
            super().__init__(["success"] * len(child_statuses))
            self.child_statuses = list(child_statuses)

        def run(self, request: AIJobRequest) -> AIJobResult:
            result = super().run(request)
            child = replace(result.subjobs[0], result_status=self.child_statuses.pop(0))
            return replace(result, subjobs=(child,))

    runner = SpecialistOutcomeRunner(statuses)
    result = run_copy(runtime_case, runner)

    assert result.status == (
        "committed" if statuses[-1] == "success" else "generation_failed"
    )
    assert len(runner.requests) == 3
    assert [request.attempt for request in runner.requests] == [1, 2, 3]
    assert [
        row[0]
        for row in runtime_case.connection.execute(
            "SELECT result_status FROM ai_job WHERE run_id = ? AND agent_id = 'AI-O' ORDER BY attempt",
            (RUN_ID,),
        ).fetchall()
    ] == ["success"] * 3
    assert [
        tuple(row)
        for row in runtime_case.connection.execute(
            "SELECT attempt_no, outcome FROM generation_attempt WHERE run_id = ? ORDER BY attempt_no",
            (RUN_ID,),
        ).fetchall()
    ] == [(index, status) for index, status in enumerate(statuses, start=1)]
    assert [
        row[0]
        for row in runtime_case.connection.execute(
            "SELECT result_status FROM ai_job WHERE run_id = ? AND agent_id = 'AG-COPY' ORDER BY attempt",
            (RUN_ID,),
        ).fetchall()
    ] == statuses


@pytest.mark.parametrize(
    "runner_kwargs",
    [
        {"offer_key": "shop_test:item_OTHER"},
        {"draft_version": 2},
        {"subjob_input_refs": ("INPUT-OTHER",)},
    ],
)
def test_wrong_offer_version_or_input_contract_stops_without_retry(
    runtime_case: RuntimeCase, runner_kwargs: dict[str, Any]
) -> None:
    runner = FakeCopyRunner(["success", "success"], **runner_kwargs)

    result = run_copy(runtime_case, runner)

    assert result.status == "contract_error"
    assert result.error_code == "copy_identity_mismatch"
    assert len(runner.requests) == 1
    assert tuple(
        runtime_case.connection.execute(
            "SELECT outcome, error_code FROM generation_attempt WHERE run_id = ? AND offer_key = ?",
            (RUN_ID, OFFER_KEY),
        ).fetchone()
    ) == ("error", "copy_identity_mismatch")
    assert tuple(
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id = ?", (RUN_ID,)
        ).fetchone()
    ) == (0,)


def test_input_offer_version_mismatch_is_rejected_before_reserving_a_call(
    runtime_case: RuntimeCase,
) -> None:
    spec = make_spec(
        draft_version=2,
        input_payload={"offer": {"offer_key": OFFER_KEY, "draft_version": 1}},
    )
    runner = FakeCopyRunner()

    with pytest.raises(RejectedContract, match="input payload"):
        run_copy(runtime_case, runner, spec=spec)

    assert runner.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )


def test_existing_draft_with_another_requested_version_is_not_rewritten(
    runtime_case: RuntimeCase,
) -> None:
    first = run_copy(runtime_case, FakeCopyRunner())
    other_version = make_spec(
        draft_version=2,
        input_payload={"offer": {"offer_key": OFFER_KEY, "draft_version": 2}},
    )
    retry_runner = FakeCopyRunner()

    result = run_copy(runtime_case, retry_runner, spec=other_version)

    assert first.status == "committed"
    assert result.status == "contract_error"
    assert result.error_code == "existing_draft_version_mismatch"
    assert retry_runner.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT version FROM draft WHERE run_id = ? AND offer_key = ?",
            (RUN_ID, OFFER_KEY),
        ).fetchone()[0]
        == 1
    )
    assert (
        runtime_case.connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "body,tag_count",
    [
        ("x" * 37, 0),
        ("x" * 483, 0),
        ("short text " + " ".join(f"#tag{i}" for i in range(13)), 13),
    ],
)
def test_nonbinding_body_and_tag_guidance_never_blocks_unknown_facts(
    runtime_case: RuntimeCase, body: str, tag_count: int
) -> None:
    spec = make_spec()
    runner = FakeCopyRunner(body=body)

    result = run_copy(runtime_case, runner, spec=spec)

    assert result.status == "committed", result
    assert len(runner.requests) == 1
    assert runner.requests[0].input_payload == spec.input_payload
    saved = runtime_case.connection.execute(
        "SELECT body FROM draft WHERE offer_key = ?", (OFFER_KEY,)
    ).fetchone()[0]
    assert saved == body
    assert len(saved) == len(body)
    assert saved.count("#") == tag_count


@pytest.mark.parametrize(
    "failpoint,expected_resume",
    [("before_commit", "committed"), ("after_commit", "already_committed")],
)
def test_save_failure_resumes_same_artifact_without_ai_call(
    runtime_case: RuntimeCase,
    monkeypatch: pytest.MonkeyPatch,
    failpoint: str,
    expected_resume: str,
) -> None:
    from automation.inago import copy_service

    original_commit = copy_service.commit_draft
    calls = 0

    def fail_once(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_commit(*args, failpoint=failpoint, **kwargs)
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(copy_service, "commit_draft", fail_once)
    runner = FakeCopyRunner()
    first = run_copy(runtime_case, runner)
    assert first.status == "pending_commit"
    assert len(runner.requests) == 1
    saved_before = runtime_case.connection.execute(
        "SELECT outcome, artifact_ref FROM generation_attempt WHERE run_id = ? AND offer_key = ?",
        (RUN_ID, OFFER_KEY),
    ).fetchone()
    assert saved_before[0] == "success"
    assert saved_before[1]
    artifact_path = runtime_case.connection.execute(
        "SELECT path FROM artifact WHERE artifact_ref = ?", (saved_before[1],)
    ).fetchone()[0]
    payload_before = json.loads(Path(artifact_path).read_text(encoding="utf-8"))

    resumed = run_copy(runtime_case, FakeCopyRunner(["error"]))

    assert resumed.status == expected_resume
    assert calls == (2 if failpoint == "before_commit" else 1)
    assert len(runner.requests) == 1
    saved_after = runtime_case.connection.execute(
        "SELECT artifact_ref FROM generation_attempt WHERE run_id = ? AND offer_key = ?",
        (RUN_ID, OFFER_KEY),
    ).fetchone()[0]
    assert saved_after == saved_before[1]
    assert json.loads(Path(artifact_path).read_text(encoding="utf-8")) == payload_before
    assert (
        runtime_case.connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 1
    )
    trace_files = list(
        (runtime_case.data_root / "logs" / "copy-generation").rglob("*.jsonl")
    )
    assert len(trace_files) == 1
    trace_text = trace_files[0].read_text(encoding="utf-8")
    trace_events = [json.loads(line)["event"] for line in trace_text.splitlines()]
    assert "response_checkpoint_saved" in trace_events
    assert "draft_commit_failed" in trace_events
    assert "fixture body for item A1" not in trace_text
    if failpoint == "before_commit":
        assert "saved_response_resume" in trace_events
        assert "draft_committed" in trace_events


def test_export_notification_failure_does_not_undo_saved_body(
    runtime_case: RuntimeCase,
) -> None:
    def fail_export(_result: Any) -> None:
        raise OSError("fixture export failure")

    result = run_copy(runtime_case, FakeCopyRunner(), export_notifier=fail_export)

    assert result.status == "committed"
    assert result.export_error == "OSError"
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 1
    )
    assert (
        runtime_case.connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 1
    )


def test_pending_post_conflict_blocks_generation_before_reserving_attempt(
    runtime_case: RuntimeCase,
) -> None:
    runtime_case.connection.execute(
        """
        INSERT INTO human_action
            (action_id, action, offer_key, draft_id, draft_version, prior_revision,
             receipt_revision, reason, reported_at, old_event_ref, new_body,
             apply_status, applied_at, applied_run_id, apply_error)
        VALUES ('PENDING-POST', 'posted', ?, NULL, 1, 0, 1, NULL, ?, NULL, NULL,
                'pending', NULL, NULL, NULL)
        """,
        (OFFER_KEY, NOW),
    )
    runner = FakeCopyRunner()

    with pytest.raises(RejectedContract, match="pending posted report"):
        run_copy(runtime_case, runner)

    assert runner.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )
    assert (
        runtime_case.connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )


def test_late_root_result_is_never_adopted_or_retried(
    runtime_case: RuntimeCase,
) -> None:
    runner = FakeCopyRunner(["late_result", "success"])

    result = run_copy(runtime_case, runner)

    assert result.status == "expired"
    assert result.error_code == "late_result"
    assert len(runner.requests) == 1
    assert tuple(
        runtime_case.connection.execute(
            "SELECT outcome, error_code FROM generation_attempt WHERE run_id = ?",
            (RUN_ID,),
        ).fetchone()
    ) == ("error", "late_result")
    assert (
        runtime_case.connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )


def test_runner_exceptions_are_recorded_and_retry_only_three_times(
    runtime_case: RuntimeCase,
) -> None:
    class RaisingRunner:
        def __init__(self) -> None:
            self.requests: list[AIJobRequest] = []

        def run(self, request: AIJobRequest) -> AIJobResult:
            self.requests.append(request)
            raise OSError("fixture runner I/O failure")

    runner = RaisingRunner()

    result = run_copy(runtime_case, runner)  # type: ignore[arg-type]

    assert result.status == "generation_failed"
    assert len(runner.requests) == 3
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM ai_job WHERE run_id = ? AND result_status = 'error'",
            (RUN_ID,),
        ).fetchone()[0]
        == 3
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "mode",
    ["root_identity", "missing_copy", "bad_rationale", "extra_output_field"],
)
def test_runner_contract_errors_stop_without_retry(
    runtime_case: RuntimeCase, mode: str
) -> None:
    class InvalidResultRunner(FakeCopyRunner):
        def run(self, request: AIJobRequest) -> AIJobResult:
            result = super().run(request)
            if mode == "root_identity":
                return replace(result, run_id="ANOTHER-RUN")
            if mode == "missing_copy":
                return replace(result, subjobs=())
            payload = dict(result.subjobs[0].payload)
            if mode == "extra_output_field":
                payload["unexpected"] = "not in the role output contract"
                child = replace(result.subjobs[0], payload=payload)
                return replace(result, subjobs=(child,))
            child = replace(
                result.subjobs[0],
                payload={**payload, "selection_reason": " "},
            )
            return replace(result, subjobs=(child,))

    runner = InvalidResultRunner(["success", "success"])
    result = run_copy(runtime_case, runner)

    assert result.status == "contract_error"
    assert len(runner.requests) == 1
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 1
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )


def test_expired_deadline_and_existing_in_progress_attempt_do_not_call_ai(
    runtime_case: RuntimeCase,
) -> None:
    expired_runner = FakeCopyRunner()
    expired = run_copy(
        runtime_case,
        expired_runner,
        spec=replace(make_spec(), deadline_monotonic=0),
    )
    assert expired.status == "expired"
    assert expired_runner.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )

    from automation.inago.repository import reserve_generation_attempt

    reserve_generation_attempt(
        runtime_case.connection,
        run_id=RUN_ID,
        offer_key=OFFER_KEY,
        settings_version=SETTINGS_VERSION,
        generation_attempt_id="GA-INTERRUPTED",
        attempt_no=1,
        started_at=NOW,
        clock=runtime_case.clock,
    )
    interrupted_runner = FakeCopyRunner()
    resumed = run_copy(runtime_case, interrupted_runner)
    assert resumed.status == "in_progress"
    assert resumed.error_code == "in_progress_recovery_required"
    assert interrupted_runner.requests == []


@pytest.mark.parametrize(
    "spec_changes",
    [
        {"run_id": "unsafe run id"},
        {"draft_version": True},
        {"pattern_version": "different"},
        {"deadline_monotonic": float("nan")},
        {
            "input_payload": {
                "offer": {"offer_key": OFFER_KEY, "draft_version": 1},
                "bad": float("nan"),
            }
        },
    ],
)
def test_invalid_program_inputs_fail_before_any_generation_attempt(
    runtime_case: RuntimeCase, spec_changes: dict[str, Any]
) -> None:
    spec = replace(make_spec(), **spec_changes)
    runner = FakeCopyRunner()

    with pytest.raises(RejectedContract):
        run_copy(runtime_case, runner, spec=spec)

    assert runner.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "spec_changes",
    [
        {"target_date": "2026-10-13"},
        {"deadline_at": "2026-10-13T17:45:00+09:00"},
        {
            "settings_version": "other-settings-version",
            "pattern_version": "other-settings-version",
        },
    ],
)
def test_run_date_deadline_and_settings_must_match_before_ai_call(
    runtime_case: RuntimeCase, spec_changes: dict[str, Any]
) -> None:
    runner = FakeCopyRunner()
    spec = replace(make_spec(), **spec_changes)

    if "deadline_at" in spec_changes:
        outcome = generate_and_commit_copy(
            runtime_case.connection,
            spec,
            runner,
            data_root=runtime_case.data_root,
            clock=runtime_case.clock,
        )

        assert outcome.status == "committed"
        assert len(runner.requests) == 1
        assert runner.requests[0].deadline_at == spec.deadline_at
        assert (
            runtime_case.connection.execute(
                "SELECT COUNT(*) FROM draft WHERE run_id = ?", (RUN_ID,)
            ).fetchone()[0]
            == 1
        )
        return

    with pytest.raises(RejectedContract, match="started run"):
        generate_and_commit_copy(
            runtime_case.connection,
            spec,
            runner,
            data_root=runtime_case.data_root,
            clock=runtime_case.clock,
        )

    assert runner.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )


def test_rejected_root_contract_is_not_treated_as_retryable_generation_error(
    runtime_case: RuntimeCase,
) -> None:
    class RejectedRootRunner(FakeCopyRunner):
        def run(self, request: AIJobRequest) -> AIJobResult:
            result = super().run(request)
            return replace(
                result,
                adoption_status="rejected_contract",
                result_status="error",
            )

    runner = RejectedRootRunner(["error", "success"])
    result = run_copy(runtime_case, runner)

    assert result.status == "contract_error"
    assert result.error_code == "copy_runner_contract_rejected"
    assert len(runner.requests) == 1
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 1
    )


def test_fourteen_prior_drafts_remain_unchanged_when_fifteenth_commits(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    connection.execute(
        """
        INSERT INTO artifact
            (artifact_ref, path, hash, created_at, source_kind,
             retention_expires_at, status)
        VALUES ('OLD-ARTIFACT', 'fixture://old', 'fixture', ?, 'generation', NULL, 'available')
        """,
        (NOW,),
    )
    for index in range(1, 15):
        suffix = f"OLD{index:02d}"
        offer_key = seed_offer(connection, suffix)
        draft_id = f"OLD-DRAFT-{index:02d}"
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES (?, ?, ?, 1, ?, ?, ?, 'old-generator', ?, 'committed',
                    'OLD-ARTIFACT', 'P01', '[\"INPUT-OLD\"]')
            """,
            (
                draft_id,
                offer_key,
                RUN_ID,
                f"old-body-{index}",
                NOW,
                NOW,
                SETTINGS_VERSION,
            ),
        )
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES (?, ?, ?, 'active', ?, NULL, NULL)
            """,
            (f"OLD-RES-{index:02d}", offer_key, draft_id, NOW),
        )
    connection.execute(
        "UPDATE run SET committed_count = 14 WHERE run_id = ?", (RUN_ID,)
    )
    before = connection.execute(
        "SELECT draft_id, offer_key, body, version, status FROM draft WHERE run_id = ? ORDER BY draft_id",
        (RUN_ID,),
    ).fetchall()

    result = run_copy(runtime_case, FakeCopyRunner())

    after = connection.execute(
        "SELECT draft_id, offer_key, body, version, status FROM draft WHERE run_id = ? ORDER BY draft_id",
        (RUN_ID,),
    ).fetchall()
    assert result.status == "committed", result
    assert (
        result.commit_result is not None and result.commit_result.committed_count == 15
    )
    assert len(before) == 14
    after_by_id = {row[0]: tuple(row) for row in after}
    assert all(after_by_id[row[0]] == tuple(row) for row in before)
    assert len(after) == 15
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM reservation WHERE status = 'active'"
        ).fetchone()[0]
        == 15
    )
