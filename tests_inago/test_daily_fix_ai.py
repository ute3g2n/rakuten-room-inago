"""R09 regressions for per-turn identity, contract stop, and copy recovery."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago import copy_service, orchestrator
from automation.inago.ai_runner import (
    _validate_identity,
    compute_input_hash,
)
from automation.inago.artifact_io import write_temp_response
from automation.inago.clock import FakeClock
from automation.inago.contracts import (
    AIJobRequest,
    AIJobResult,
    AISubjobResult,
    ArtifactRecord,
)
from automation.inago.copy_service import CopyGenerationSpec, generate_and_commit_copy
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.settings import register_external_settings
from tests_inago.test_step08 import _base_request, _delegation, _root_response, _runner
from tests_inago.test_step13 import (
    FakeCopyRunner,
    RuntimeCase,
    SETTINGS_VERSION,
    _insert_offer,
    _make_ports,
)


NOW = "2026-10-16T13:00:00+09:00"
TARGET_DATE = "2026-10-16"
BASIC_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "specifications"
    / "preparation"
    / "初期設定引渡し.json"
)
DETAIL_RULES_PATH = (
    Path(__file__).resolve().parents[1]
    / "worklogs"
    / "projects"
    / "preparation"
    / "candidates"
    / "文型詳細ルール.json"
)


@pytest.fixture
def daily_case(tmp_path: Path, fixture_config_path: Path):
    paths = resolve_runtime_paths(
        data_root=tmp_path / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    basic_settings_path = BASIC_SETTINGS_PATH
    basic = json.loads(basic_settings_path.read_text(encoding="utf-8"))
    details = json.loads((DETAIL_RULES_PATH).read_text(encoding="utf-8"))
    register_external_settings(connection, basic, details, created_at=NOW)
    case = RuntimeCase(
        connection=connection,
        data_root=paths.data_root,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )
    try:
        yield case
    finally:
        connection.close()


def _insert_daily_run(connection: sqlite3.Connection, run_id: str) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, ?, 'daily', 'running', ?, ?, ?, ?, ?, 0, 0, '{}', 'not_started', NULL)
        """,
        (
            run_id,
            TARGET_DATE,
            SETTINGS_VERSION,
            f"{run_id}-SEED",
            NOW,
            f"{TARGET_DATE}T17:45:00+09:00",
            f"{TARGET_DATE}T18:00:00+09:00",
        ),
    )


def _copy_spec(run_id: str, offer_key: str, suffix: str) -> CopyGenerationSpec:
    input_ref = f"{run_id}-{suffix}-INPUT"
    return CopyGenerationSpec(
        run_id=run_id,
        offer_key=offer_key,
        draft_version=1,
        target_date=TARGET_DATE,
        settings_version=SETTINGS_VERSION,
        input_refs=(input_ref,),
        evidence_refs=(input_ref,),
        input_payload={
            "offer": {"offer_key": offer_key, "draft_version": 1},
            "purpose": "紹介文の試験用生成",
            "facts": [
                {
                    "field": name,
                    "value": None,
                    "availability": "unknown",
                    "source_ref": None,
                }
                for name in ("price", "stock", "shipping", "rating", "promotion")
            ],
            "author_experience": {"availability": "unknown", "value": None},
        },
        deadline_at=f"{TARGET_DATE}T17:45:00+09:00",
        deadline_monotonic=10_000,
        generator_version="daily-fix-ai-v1",
        pattern_version=SETTINGS_VERSION,
    )


def _fake_generation_result(
    request: AIJobRequest,
    *,
    status: str,
    body: str = "試験用の紹介本文です。",
) -> AIJobResult:
    target = request.copy_targets[0]
    child = AISubjobResult(
        job_id=f"{request.job_id}-a{request.attempt}-child-01",
        parent_job_id=request.job_id,
        run_id=request.run_id,
        attempt=request.attempt,
        task_id="T02",
        agent_id="AG-COPY",
        skill_name="rr-inago-copywriter",
        target_date=request.target_date,
        settings_version=request.settings_version,
        input_refs=request.input_refs,
        evidence_refs=request.evidence_refs,
        input_hash=f"child-{request.job_id}-{request.attempt}",
        deadline_at=request.deadline_at,
        result_status="success",
        payload={
            "offer_key": target.offer_key,
            "draft_version": target.draft_version,
            "body": body,
            "pattern_id": "P01",
            "evidence_refs": list(request.evidence_refs),
            "selection_reason": "試験入力だけを使用",
            "used_input_refs": list(request.input_refs),
            "omitted_unknowns": [
                "price",
                "stock",
                "shipping",
                "rating",
                "promotion",
            ],
        },
        resolved_model="fixture-model",
        reasoning_setting="fixture",
        auth_mode="fixture",
    )
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
        result_status=status,  # type: ignore[arg-type]
        adoption_status="pending",
        payload={"summary": "fixture result"} if status == "success" else None,
        error={"code": "fixture_generation_error"} if status == "error" else None,
        artifact=None,
        subjobs=(child,) if status == "success" else (),
        resolved_model="fixture-model",
        reasoning_setting="fixture",
        auth_mode="fixture",
        started_at=NOW,
        finished_at=NOW,
    )


def test_n17_preserves_first_identity_mismatch_and_stops_the_daily_run(
    daily_case: RuntimeCase, tmp_path: Path
) -> None:
    validation_request = _base_request()
    expected_hash = compute_input_hash(validation_request.input_payload)
    valid_response = _root_response(validation_request, delegations=[])
    assert _validate_identity(validation_request, expected_hash, valid_response) is None
    for field, value in (
        ("job_id", "OLD-TURN-JOB"),
        ("attempt", "1"),
        ("settings_version", "OTHER-VERSION"),
        ("input_refs", "NOT-AN-ARRAY"),
        ("input_hash", "OLD-TURN-HASH"),
    ):
        mismatch = dict(valid_response)
        mismatch[field] = value
        assert _validate_identity(validation_request, expected_hash, mismatch) == field

    success_runner = FakeCopyRunner()

    class RejectAfterFourteen:
        fixture_only = True

        def __init__(self) -> None:
            self.requests = []
            self.results = []
            self.validator = None
            self.bad_input_hash = "f" * 64

        def run(self, request: AIJobRequest) -> AIJobResult:
            self.requests.append(request)
            if len(self.requests) <= 14:
                result = success_runner.run(request)
            else:
                wrong_hash = self.bad_input_hash
                if wrong_hash == compute_input_hash(request.input_payload):
                    wrong_hash = "e" * 64
                    self.bad_input_hash = wrong_hash
                raw = _root_response(request, delegations=[], input_hash=wrong_hash)
                raw_bytes = json.dumps(raw, ensure_ascii=False).encode("utf-8")
                validator_root = tmp_path / "validator"
                validator_root.mkdir()
                validator, _, _ = _runner(
                    validator_root,
                    response=raw_bytes,
                    clock=daily_case.clock,
                )
                self.validator = validator
                result = validator.run(request)
            self.results.append(result)
            return result

    runner = RejectAfterFourteen()
    ports_bundle = _make_ports(daily_case, posts_per_search=5)
    ports_bundle.ports = replace(ports_bundle.ports, copy_runner=runner)

    outcome = orchestrator.run_daily(
        daily_case.connection,
        data_root=daily_case.data_root,
        ports=ports_bundle.ports,
        clock=daily_case.clock,
        run_id_factory=lambda _date: "RUN-N17",
        seed_factory=lambda: "RUN-N17-SEED",
    )

    assert len(runner.requests) == 15
    assert runner.results[-1].adoption_status == "rejected_contract"
    assert runner.results[-1].error == {"code": "input_hash"}
    assert runner.results[-1].subjobs == ()
    assert runner.results[-1].artifact is not None
    raw_response = json.loads(
        runner.results[-1].artifact.path.read_text(encoding="utf-8")
    )
    assert raw_response["input_hash"] == runner.bad_input_hash
    assert raw_response["delegations"] == []

    connection = daily_case.connection
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id='RUN-N17'"
        ).fetchone()[0]
        == 14
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM reservation WHERE status='active'"
        ).fetchone()[0]
        == 14
    )
    assert (
        connection.execute(
            "SELECT committed_count FROM run WHERE run_id='RUN-N17'"
        ).fetchone()[0]
        == 14
    )
    assert outcome.status == "partial"
    assert outcome.reason == "copy_runner_contract_rejected"

    _, state = orchestrator._daily_state(
        connection.execute(
            "SELECT checkpoint FROM run WHERE run_id='RUN-N17'"
        ).fetchone()[0]
    )
    error = state["errors"][-1]
    assert error["phase"] == "copy"
    assert error["code"] == "copy_runner_contract_rejected"
    assert error["references"]["cause_code"] == "input_hash"
    assert (
        error["references"]["artifact_ref"] == runner.results[-1].artifact.artifact_ref
    )

    root_job = connection.execute(
        "SELECT error_json, artifact_ref, adoption_status FROM ai_job "
        "WHERE job_id=? AND agent_id='AI-O'",
        (runner.requests[-1].job_id,),
    ).fetchone()
    assert json.loads(root_job["error_json"]) == {
        "code": "copy_runner_contract_rejected",
        "cause_code": "input_hash",
        "artifact_ref": runner.results[-1].artifact.artifact_ref,
    }
    assert root_job["artifact_ref"] == runner.results[-1].artifact.artifact_ref
    assert root_job["adoption_status"] == "rejected_contract"
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM ai_job WHERE job_id LIKE ? AND agent_id!='AI-O'",
            (f"{runner.requests[-1].job_id}%",),
        ).fetchone()[0]
        == 0
    )
    assert runner.validator is not None


def test_n18_current_turn_identity_and_same_root_key_then_two_copies(
    daily_case: RuntimeCase,
) -> None:
    from automation.inago.daily_ai import DailyAIRootRunner

    run_id = "RUN-N18"
    _insert_daily_run(daily_case.connection, run_id)
    offer_a, _, _ = _insert_offer(
        daily_case.connection, theme_id="G01-T01", item_number=901
    )
    offer_b, _, _ = _insert_offer(
        daily_case.connection, theme_id="G01-T01", item_number=902
    )

    key_payload = {
        "word_ids": ["WORD-N18"],
        "keywords": {
            "snapshot_id": "SNAP-N18",
            "query_word": "試験テーマ",
            "words": [{"word_id": "WORD-N18", "raw": "試験語", "source_rank": 1}],
        },
    }
    key_request = AIJobRequest(
        job_id="JOB-N18-KEY",
        run_id=run_id,
        attempt=1,
        task_id="T02",
        target_date=TARGET_DATE,
        settings_version=SETTINGS_VERSION,
        input_refs=("N18-KEY-INPUT",),
        evidence_refs=("N18-KEY-EVIDENCE",),
        deadline_at=f"{TARGET_DATE}T17:45:00+09:00",
        deadline_monotonic=10_000,
        input_payload=key_payload,
        trigger="scheduled_daily",
        explicit_user_request=False,
        source_kind="daily",
    )

    sessions = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.starts = 1
            self.process_id = 4242
            self.is_closed = False
            self.prompts = []
            self.turns = []
            sessions.append(self)

        def start(self, **kwargs):
            return "N18-ROOT"

        def run_turn(self, prompt, schema, *, cwd, **kwargs):
            job = json.loads((cwd / "job-input.json").read_text(encoding="utf-8"))
            self.prompts.append(prompt)
            self.turns.append(job)
            if job["copy_targets"]:
                target = job["copy_targets"][0]
                agent_id = "AG-COPY"
                skill_name = "rr-inago-copywriter"
                body = f"試験用の紹介本文です。商品 {target['offer_key']} の特徴をお届けします。"
                payload = {
                    "offer_key": target["offer_key"],
                    "draft_version": target["draft_version"],
                    "body": body,
                    "pattern_id": "P01",
                    "evidence_refs": list(job["evidence_refs"]),
                    "selection_reason": "現在turnの試験入力を使用",
                    "used_input_refs": list(job["input_refs"]),
                    "omitted_unknowns": [
                        "price",
                        "stock",
                        "shipping",
                        "rating",
                        "promotion",
                    ],
                }
            else:
                agent_id = "AG-KEY"
                skill_name = "rr-inago-keywords"
                keywords = job["input_payload"]["keywords"]
                payload = {
                    "snapshot_id": keywords["snapshot_id"],
                    "query_word": keywords["query_word"],
                    "decisions": [
                        {
                            "word_id": word_id,
                            "decision": "candidate",
                            "group_ref": word_id,
                            "reason": "試験語を分類",
                        }
                        for word_id in job["input_payload"]["word_ids"]
                    ],
                }
            declaration = _delegation(
                agent_id,
                "T02",
                skill_name,
                payload,
                input_refs=list(job["input_refs"]),
                evidence_refs=list(job["evidence_refs"]),
            )
            response = {
                "protocol_version": job["protocol_version"],
                "schema_version": job["schema_version"],
                "run_id": job["run_id"],
                "job_id": job["job_id"],
                "attempt": job["attempt"],
                "task_id": job["task_id"],
                "agent_id": "AI-O",
                "skill_name": "rakuten-room-inago",
                "target_date": job["target_date"],
                "settings_version": job["settings_version"],
                "input_refs": job["input_refs"],
                "evidence_refs": job["evidence_refs"],
                "input_hash": job["input_hash"],
                "deadline_at": job["deadline_at"],
                "result_status": "success",
                "payload": {
                    "summary": "現在turnの処理結果",
                    "details": [],
                    "references": list(job["input_refs"]),
                },
                "error": None,
                "delegations": [declaration],
            }
            agent_type = skill_name
            event = {
                "type": "collab_tool_call",
                "tool": "spawn_agent",
                "call_id": f"N18-CALL-{len(self.turns)}",
                "arguments": {"agent_type": agent_type},
            }
            return SimpleNamespace(
                thread_id="N18-ROOT",
                turn_id=f"N18-TURN-{len(self.turns)}",
                final_text=json.dumps(response, ensure_ascii=False),
                process_returncode=None,
                events=({"method": "item/completed", "params": {"item": event}},),
            )

        def close(self):
            self.is_closed = True

    class RecordingDailyAIRootRunner(DailyAIRootRunner):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.job_results = []

        def run(self, request, *, cancel_event=None):
            result = super().run(request, cancel_event=cancel_event)
            self.job_results.append(result)
            return result

    runner = RecordingDailyAIRootRunner(
        data_root=daily_case.data_root / "n18-runtime",
        session_factory=FakeSession,
        clock=daily_case.clock,
        auth_mode_provider=lambda: "chatgpt",
        require_observed_metadata=False,
    )
    runner.prepare_daily_run(daily_case.connection, run_id)
    runner.start_daily_root(deadline_monotonic=10_000)
    try:
        key_result = runner.run(key_request)
        runner.sync_daily_checkpoint(daily_case.connection)
        copy_results = []
        for offer_key, suffix in ((offer_a, "A"), (offer_b, "B")):
            spec = _copy_spec(run_id, offer_key, f"N18-{suffix}")
            outcome = generate_and_commit_copy(
                daily_case.connection,
                spec,
                runner,
                data_root=runner.data_root,
                clock=daily_case.clock,
            )
            copy_results.append(outcome)
            runner.sync_daily_checkpoint(daily_case.connection)

        assert key_result.result_status == "success"
        assert key_result.subjobs[0].agent_id == "AG-KEY"
        assert len(key_result.subjobs) == 1
        assert key_result.subjobs[0].parent_job_id == key_request.job_id
        assert key_result.subjobs[0].input_hash != key_result.input_hash
        assert [outcome.status for outcome in copy_results] == [
            "committed",
            "committed",
        ]
        assert [outcome.commit_result.committed_count for outcome in copy_results] == [
            1,
            2,
        ]
        assert len(sessions) == 1 and runner.process_starts == 1
        assert [job["job_id"] for job in sessions[0].turns] == [
            "JOB-N18-KEY",
            copy_results[0].commit_result.draft_id.replace("D-GA-", "J-GA-"),
            copy_results[1].commit_result.draft_id.replace("D-GA-", "J-GA-"),
        ]
        hashes = [job["input_hash"] for job in sessions[0].turns]
        refs = [tuple(job["input_refs"]) for job in sessions[0].turns]
        assert len(set(hashes)) == 3
        assert len(set(refs)) == 3
        assert len(runner.job_results) == 3
        assert all(
            len(result.subjobs) == 1
            and result.subjobs[0].parent_job_id == result.job_id
            and result.subjobs[0].job_id.startswith(result.job_id + "-a")
            for result in runner.job_results
        )
        assert all(
            f'"input_hash":"{job["input_hash"]}"' in prompt
            for job, prompt in zip(sessions[0].turns, sessions[0].prompts)
        )
        assert all(
            "never carry identity or hash values over" in prompt
            for prompt in sessions[0].prompts
        )

        copy_jobs = daily_case.connection.execute(
            "SELECT job_id,input_hash,artifact_ref,adoption_status "
            "FROM ai_job WHERE run_id=? AND agent_id='AG-COPY' ORDER BY rowid",
            (run_id,),
        ).fetchall()
        assert len(copy_jobs) == 2
        root_jobs = daily_case.connection.execute(
            "SELECT job_id FROM ai_job WHERE run_id=? AND agent_id='AI-O' ORDER BY rowid",
            (run_id,),
        ).fetchall()
        root_job_ids = {row["job_id"] for row in root_jobs}
        assert all(
            any(row["job_id"].startswith(root_id + "-a") for root_id in root_job_ids)
            for row in copy_jobs
        )
        assert len({row["input_hash"] for row in copy_jobs}) == 2
        assert len({row["artifact_ref"] for row in copy_jobs}) == 2
        assert all(row["adoption_status"] == "accepted" for row in copy_jobs)
        assert (
            daily_case.connection.execute(
                "SELECT COUNT(*) FROM draft WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 2
        )
        assert (
            daily_case.connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
            == 2
        )
        assert (
            daily_case.connection.execute(
                "SELECT committed_count FROM run WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 2
        )
    finally:
        runner.close_daily_run()
        runner.sync_daily_checkpoint(daily_case.connection)


@pytest.mark.parametrize("scenario", ["generation_retries", "saved_response_resume"])
def test_n19_generation_retry_stops_at_three_and_saved_response_is_reused(
    daily_case: RuntimeCase, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    run_id = f"RUN-N19-{scenario}"
    _insert_daily_run(daily_case.connection, run_id)
    offer_key, _, _ = _insert_offer(
        daily_case.connection,
        theme_id="G01-T01",
        item_number=911 if scenario == "generation_retries" else 912,
    )
    spec = _copy_spec(run_id, offer_key, "N19")

    class SequenceRunner:
        fixture_only = True

        def __init__(self, statuses: list[str]) -> None:
            self.statuses = list(statuses)
            self.requests = []

        def run(self, request: AIJobRequest) -> AIJobResult:
            self.requests.append(request)
            status = self.statuses.pop(0)
            return _fake_generation_result(request, status=status)

    if scenario == "generation_retries":
        runner = SequenceRunner(["empty", "error", "success"])
        outcome = generate_and_commit_copy(
            daily_case.connection,
            spec,
            runner,
            data_root=daily_case.data_root,
            clock=daily_case.clock,
        )
        attempts = daily_case.connection.execute(
            "SELECT attempt_no,outcome FROM generation_attempt "
            "WHERE run_id=? AND offer_key=? ORDER BY attempt_no",
            (run_id, offer_key),
        ).fetchall()
        assert outcome.status == "committed"
        assert outcome.attempts_used == 3
        assert len(runner.requests) == 3
        assert [tuple(row) for row in attempts] == [
            (1, "empty"),
            (2, "error"),
            (3, "success"),
        ]
    else:
        runner = SequenceRunner(["success"])
        commit = copy_service.commit_draft
        calls = 0

        def fail_once(connection, request, *, clock):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("fixture save failure")
            return commit(connection, request, clock=clock)

        monkeypatch.setattr(copy_service, "commit_draft", fail_once)
        pending = generate_and_commit_copy(
            daily_case.connection,
            spec,
            runner,
            data_root=daily_case.data_root,
            clock=daily_case.clock,
        )
        saved_ref = daily_case.connection.execute(
            "SELECT artifact_ref FROM generation_attempt WHERE run_id=? AND offer_key=?",
            (run_id, offer_key),
        ).fetchone()[0]
        resumed = generate_and_commit_copy(
            daily_case.connection,
            spec,
            runner,
            data_root=daily_case.data_root,
            clock=daily_case.clock,
        )
        assert pending.status == "pending_commit"
        assert resumed.status == "committed" and resumed.resumed is True
        assert resumed.attempts_used == 1
        assert len(runner.requests) == 1
        assert calls == 2
        assert (
            daily_case.connection.execute(
                "SELECT COUNT(*) FROM generation_attempt WHERE run_id=? AND offer_key=?",
                (run_id, offer_key),
            ).fetchone()[0]
            == 1
        )
        assert (
            daily_case.connection.execute(
                "SELECT artifact_ref FROM generation_attempt WHERE run_id=? AND offer_key=?",
                (run_id, offer_key),
            ).fetchone()[0]
            == saved_ref
        )

    assert (
        daily_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        daily_case.connection.execute(
            "SELECT COUNT(*) FROM reservation WHERE offer_key=? AND status='active'",
            (offer_key,),
        ).fetchone()[0]
        == 1
    )
    assert (
        daily_case.connection.execute(
            "SELECT committed_count FROM run WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        == 1
    )


def test_r09_contract_rejection_resume_preserves_cause_and_artifact(
    daily_case: RuntimeCase,
) -> None:
    run_id = "RUN-R09-RESUME"
    _insert_daily_run(daily_case.connection, run_id)
    offer_key, _, _ = _insert_offer(
        daily_case.connection, theme_id="G01-T01", item_number=919
    )
    spec = _copy_spec(run_id, offer_key, "R09-RESUME")

    class ContractRejectedRunner:
        fixture_only = True

        def __init__(self) -> None:
            self.requests: list[AIJobRequest] = []

        def run(self, request: AIJobRequest) -> AIJobResult:
            self.requests.append(request)
            result = _fake_generation_result(request, status="error")
            artifact = write_temp_response(
                data_root=daily_case.data_root,
                artifact_ref=f"{request.job_id}-raw-response",
                payload={"result_status": "error", "cause": "input_hash"},
                source_kind="daily",
                created_at=NOW,
            )
            return replace(
                result,
                adoption_status="rejected_contract",
                error={"code": "input_hash"},
                artifact=artifact,
            )

    runner = ContractRejectedRunner()
    first = generate_and_commit_copy(
        daily_case.connection,
        spec,
        runner,
        data_root=daily_case.data_root,
        clock=daily_case.clock,
    )
    resumed = generate_and_commit_copy(
        daily_case.connection,
        spec,
        runner,
        data_root=daily_case.data_root,
        clock=daily_case.clock,
    )

    assert first.status == "contract_error"
    assert first.cause_code == "input_hash"
    assert first.artifact_ref is not None
    assert resumed.status == "contract_error" and resumed.resumed is True
    assert resumed.error_code == "copy_runner_contract_rejected"
    assert resumed.cause_code == "input_hash"
    assert resumed.artifact_ref == first.artifact_ref
    assert len(runner.requests) == 1
    root_job = daily_case.connection.execute(
        "SELECT error_json, artifact_ref FROM ai_job "
        "WHERE job_id=? AND agent_id='AI-O'",
        (runner.requests[0].job_id,),
    ).fetchone()
    assert json.loads(root_job["error_json"]) == {
        "code": "copy_runner_contract_rejected",
        "cause_code": "input_hash",
        "artifact_ref": first.artifact_ref,
    }
    assert root_job["artifact_ref"] == first.artifact_ref
    attempt = daily_case.connection.execute(
        "SELECT outcome, error_code, artifact_ref FROM generation_attempt "
        "WHERE run_id=? AND offer_key=?",
        (run_id, offer_key),
    ).fetchone()
    assert tuple(attempt) == (
        "error",
        "copy_runner_contract_rejected",
        first.artifact_ref,
    )
    trace = next(
        (daily_case.data_root / "logs" / "copy-generation" / TARGET_DATE).glob(
            "*.jsonl"
        )
    )
    trace_events = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    resumed_event = next(
        event
        for event in trace_events
        if event["event"] == "contract_rejection_resumed"
    )
    assert resumed_event["cause_code"] == "input_hash"
    assert resumed_event["artifact_ref"] == first.artifact_ref


def test_r09_invalid_runner_artifact_ref_is_recorded_as_contract_error(
    daily_case: RuntimeCase, tmp_path: Path
) -> None:
    run_id = "RUN-R09-INVALID-ARTIFACT"
    _insert_daily_run(daily_case.connection, run_id)
    offer_key, _, _ = _insert_offer(
        daily_case.connection, theme_id="G01-T01", item_number=920
    )
    spec = _copy_spec(run_id, offer_key, "R09-INVALID-ARTIFACT")

    class InvalidArtifactRunner:
        fixture_only = True

        def __init__(self) -> None:
            self.requests: list[AIJobRequest] = []

        def run(self, request: AIJobRequest) -> AIJobResult:
            self.requests.append(request)
            result = _fake_generation_result(request, status="success")
            artifact = ArtifactRecord(
                artifact_ref=None,  # type: ignore[arg-type]
                path=tmp_path / "invalid-response.json",
                content_hash="0" * 64,
                source_kind="daily",
                created_at=NOW,
            )
            return replace(result, artifact=artifact)

    runner = InvalidArtifactRunner()
    outcome = generate_and_commit_copy(
        daily_case.connection,
        spec,
        runner,
        data_root=daily_case.data_root,
        clock=daily_case.clock,
    )

    assert outcome.status == "contract_error"
    assert outcome.error_code == "copy_runner_result_invalid"
    assert outcome.cause_code == "artifact_ref"
    assert outcome.artifact_ref is None
    assert len(runner.requests) == 1
    attempt = daily_case.connection.execute(
        "SELECT outcome, error_code, artifact_ref FROM generation_attempt "
        "WHERE run_id=? AND offer_key=?",
        (run_id, offer_key),
    ).fetchone()
    assert tuple(attempt) == ("error", "copy_runner_result_invalid", None)
    ai_job = daily_case.connection.execute(
        "SELECT error_json, artifact_ref FROM ai_job "
        "WHERE job_id=? AND agent_id='AI-O'",
        (runner.requests[0].job_id,),
    ).fetchone()
    assert json.loads(ai_job["error_json"]) == {
        "code": "copy_runner_result_invalid",
        "cause_code": "artifact_ref",
    }
    assert ai_job["artifact_ref"] is None
    trace = next(
        (daily_case.data_root / "logs" / "copy-generation" / TARGET_DATE).glob(
            "*.jsonl"
        )
    )
    trace_events = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    result_event = next(
        event for event in trace_events if event["event"] == "ai_result_received"
    )
    rejected_event = next(
        event for event in trace_events if event["event"] == "ai_contract_rejected"
    )
    assert "artifact_ref" not in result_event
    assert rejected_event["cause_code"] == "artifact_ref"


def test_r09_contract_rejection_trace_records_runner_invocation(
    daily_case: RuntimeCase,
) -> None:
    run_id = "RUN-R09-TRACE-INVOCATION"
    _insert_daily_run(daily_case.connection, run_id)
    offer_key, _, _ = _insert_offer(
        daily_case.connection, theme_id="G01-T01", item_number=921
    )
    spec = _copy_spec(run_id, offer_key, "R09-TRACE-INVOCATION")

    class ContractRejectedRunner:
        fixture_only = True

        def run(self, request: AIJobRequest) -> AIJobResult:
            result = _fake_generation_result(request, status="success")
            artifact = write_temp_response(
                data_root=daily_case.data_root,
                artifact_ref=f"{request.job_id}-raw-response",
                payload={"result_status": "rejected_contract"},
                source_kind="daily",
                created_at=NOW,
            )
            return replace(
                result,
                adoption_status="rejected_contract",
                error={"code": "input_hash"},
                artifact=artifact,
            )

    outcome = generate_and_commit_copy(
        daily_case.connection,
        spec,
        ContractRejectedRunner(),
        data_root=daily_case.data_root,
        clock=daily_case.clock,
    )

    assert outcome.status == "contract_error"
    trace = next(
        (daily_case.data_root / "logs" / "copy-generation" / TARGET_DATE).glob(
            "*.jsonl"
        )
    )
    trace_events = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    rejected_event = next(
        event for event in trace_events if event["event"] == "ai_contract_rejected"
    )
    assert rejected_event["ai_run_invoked"] is True
    assert rejected_event["cause_code"] == "input_hash"


def test_r09_expired_attempt_resume_uses_expiry_event(daily_case: RuntimeCase) -> None:
    run_id = "RUN-R09-EXPIRED-RESUME"
    _insert_daily_run(daily_case.connection, run_id)
    offer_key, _, _ = _insert_offer(
        daily_case.connection, theme_id="G01-T01", item_number=922
    )
    spec = _copy_spec(run_id, offer_key, "R09-EXPIRED-RESUME")

    class TimedOutRunner:
        fixture_only = True

        def __init__(self) -> None:
            self.requests: list[AIJobRequest] = []

        def run(self, request: AIJobRequest) -> AIJobResult:
            self.requests.append(request)
            return _fake_generation_result(request, status="timed_out")

    runner = TimedOutRunner()
    first = generate_and_commit_copy(
        daily_case.connection,
        spec,
        runner,
        data_root=daily_case.data_root,
        clock=daily_case.clock,
    )
    resumed = generate_and_commit_copy(
        daily_case.connection,
        spec,
        runner,
        data_root=daily_case.data_root,
        clock=daily_case.clock,
    )

    assert first.status == "expired" and first.error_code == "timed_out"
    assert resumed.status == "expired" and resumed.resumed is True
    assert len(runner.requests) == 1
    trace = next(
        (daily_case.data_root / "logs" / "copy-generation" / TARGET_DATE).glob(
            "*.jsonl"
        )
    )
    trace_events = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "expired_attempt_resumed" for event in trace_events)
    assert not any(
        event["event"] == "contract_rejection_resumed" for event in trace_events
    )
