"""Fake-CLI contract tests for the AI-O native-subagent runner."""

from __future__ import annotations

import hashlib
import json
import sys
import textwrap
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from time import sleep

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from automation.inago.ai_runner import (
    AIJobRequest,
    AIJobRunner,
    CopyTarget,
    authorized_agent_task,
    compute_input_hash,
)
from automation.inago.clock import FakeClock, JST


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = PROJECT_ROOT / "automation" / "inago" / "schemas"
NOW = datetime(2026, 10, 12, 13, 0, tzinfo=JST)
FAKE_CLI = textwrap.dedent(
    r"""
    import json
    import os
    import pathlib
    import sys
    import time

    args = sys.argv[1:]
    def option(name):
        try:
            return args[args.index(name) + 1]
        except (ValueError, IndexError):
            return None
    record = {
        "argv": args,
        "stdin": sys.stdin.read(),
        "cwd": os.getcwd(),
        "skill_dirs": sorted(
            path.name
            for path in (pathlib.Path(os.getcwd()) / ".agents" / "skills").iterdir()
        ),
        "agent_configs": sorted(
            path.name
            for path in (
                pathlib.Path(os.getcwd()) / ".codex" / "agents"
            ).glob("*.toml")
        ) if (pathlib.Path(os.getcwd()) / ".codex" / "agents").exists() else [],
        "billing_override_env_present": any(
            key in os.environ
            for key in (
                "OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL",
                "OPENAI_API_BASE", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"
            )
        ),
        "output_schema": option("--output-schema"),
        "last_message": option("--output-last-message"),
        "cd": option("-C"),
    }
    pathlib.Path(os.environ["INAGO_FAKE_RECORD"]).write_text(
        json.dumps(record), encoding="utf-8"
    )
    response_file = os.environ.get("INAGO_FAKE_RESPONSE")
    if response_file:
        pathlib.Path(option("--output-last-message")).write_bytes(
            pathlib.Path(response_file).read_bytes()
        )
    for line in json.loads(os.environ.get("INAGO_FAKE_EVENTS", "[]")):
        print(json.dumps(line), flush=True)
    pathlib.Path(os.environ["INAGO_FAKE_READY"]).write_text("ready", encoding="utf-8")
    delay = float(os.environ.get("INAGO_FAKE_SLEEP", "0"))
    if delay:
        time.sleep(delay)
    sys.exit(int(os.environ.get("INAGO_FAKE_EXIT", "0")))
    """
)


def _base_request(**changes: object) -> AIJobRequest:
    request = AIJobRequest(
        job_id="JOB-DAY-001",
        run_id="RUN-DAY-001",
        attempt=1,
        task_id="T02",
        target_date="2026-10-12",
        settings_version="confirmed-0.3-step46-all132",
        input_refs=("INPUT-OFFERS-1",),
        evidence_refs=("EVIDENCE-ROOM-1",),
        deadline_at="2026-10-12T17:45:00+09:00",
        deadline_monotonic=60.0,
        input_payload={
            "offers": [{"offer_key": "shop-A:item-1", "draft_version": 1}],
            "word_ids": ["WORD-01"],
        },
        trigger="scheduled_daily",
        explicit_user_request=False,
        source_kind="daily",
        copy_targets=(CopyTarget(offer_key="shop-A:item-1", draft_version=1),),
    )
    return replace(request, **changes)


def _root_response(
    request: AIJobRequest,
    *,
    delegations: list[dict[str, object]] | None = None,
    **changes: object,
) -> dict[str, object]:
    response: dict[str, object] = {
        "protocol_version": "1",
        "schema_version": "1",
        "run_id": request.run_id,
        "job_id": request.job_id,
        "attempt": request.attempt,
        "task_id": request.task_id,
        "agent_id": "AI-O",
        "skill_name": "rakuten-room-inago",
        "target_date": request.target_date,
        "settings_version": request.settings_version,
        "input_refs": list(request.input_refs),
        "evidence_refs": list(request.evidence_refs),
        "input_hash": compute_input_hash(request.input_payload),
        "deadline_at": request.deadline_at,
        "result_status": "success",
        "payload": {
            "summary": "処理結果を契約形式で整理しました",
            "details": [],
            "references": [],
        },
        "error": None,
        "delegations": delegations or [],
    }
    response.update(changes)
    return response


def _delegation(
    agent_id: str,
    task_id: str,
    skill_name: str,
    payload: dict[str, object],
    *,
    input_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "task_id": task_id,
        "skill_name": skill_name,
        "input_refs": input_refs or ["INPUT-OFFERS-1"],
        "evidence_refs": evidence_refs or ["EVIDENCE-ROOM-1"],
        "result_status": "success",
        "payload": payload,
    }


def _spawn_event(agent_id: str, call_id: str = "call-1") -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "type": "collab_tool_call",
            "tool": "spawn_agent",
            "call_id": call_id,
            "arguments": {"agent_type": agent_id},
        },
    }


def _runner(
    tmp_path: Path,
    *,
    response: bytes | None,
    events: list[dict[str, object]] | None = None,
    exit_code: int = 0,
    sleep: float = 0.0,
    clock: object | None = None,
    max_timeout_seconds: float = 1.0,
    poll_interval_seconds: float = 0.02,
) -> tuple[AIJobRunner, Path, Path]:
    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE_CLI, encoding="utf-8")
    response_path = tmp_path / "fake-response.bin"
    if response is not None:
        response_path.write_bytes(response)
    record_path = tmp_path / "invocation.json"
    ready_path = tmp_path / "fake-ready.txt"
    environment = {
        "INAGO_FAKE_RECORD": str(record_path),
        "INAGO_FAKE_READY": str(ready_path),
        "INAGO_FAKE_EVENTS": json.dumps(events or []),
        "INAGO_FAKE_EXIT": str(exit_code),
        "INAGO_FAKE_SLEEP": str(sleep),
    }
    if response is not None:
        environment["INAGO_FAKE_RESPONSE"] = str(response_path)
    runner = AIJobRunner(
        data_root=tmp_path / "runtime-state",
        skill_root=tmp_path / "skills",
        agent_config_root=tmp_path / "agents",
        cli_command=(sys.executable, str(fake)),
        environment=environment,
        clock=clock or FakeClock(NOW),
        max_timeout_seconds=max_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    skill_root = tmp_path / "skills"
    for name in (
        "rakuten-room-inago",
        "rr-inago-observation",
        "rr-inago-keywords",
        "rr-inago-copywriter",
        "rr-inago-insight",
        "rr-inago-config",
        "rr-inago-recovery",
    ):
        skill_dir = skill_root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"# {name}\nFixture skill instructions.\n", encoding="utf-8"
        )
    runner.skill_root = skill_root
    agent_root = tmp_path / "agents"
    agent_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "rr-inago-observation",
        "rr-inago-keywords",
        "rr-inago-copywriter",
        "rr-inago-insight",
        "rr-inago-config",
        "rr-inago-recovery",
    ):
        (agent_root / f"{name}.toml").write_text(
            f'name = "{name}"\ndescription = "Fixture only"\n',
            encoding="utf-8",
        )
    runner.agent_config_root = agent_root
    return runner, record_path, response_path


def test_daily_ai_o_uses_one_read_only_cli_and_observed_native_subagent(
    tmp_path: Path,
) -> None:
    request = _base_request(attempt=2)
    delegation = _delegation(
        "AG-KEY",
        "T02",
        "rr-inago-keywords",
        {"word_id": "WORD-01", "decision": "child", "reason": "用途が合う"},
    )
    response = _root_response(request, delegations=[delegation])
    runner, record_path, _ = _runner(
        tmp_path,
        response=json.dumps(response, ensure_ascii=False).encode("utf-8"),
        events=[_spawn_event("rr-inago-keywords")],
    )

    result = runner.run(request)

    assert result.result_status == "success"
    assert result.adoption_status == "pending"
    assert result.subjobs[0].agent_id == "AG-KEY"
    assert result.subjobs[0].result_status == "success"
    assert result.subjobs[0].parent_job_id == request.job_id
    assert result.subjobs[0].run_id == request.run_id
    assert result.subjobs[0].attempt == request.attempt
    assert result.subjobs[0].settings_version == request.settings_version
    assert result.subjobs[0].input_refs == request.input_refs
    assert result.subjobs[0].evidence_refs == request.evidence_refs
    assert len(result.subjobs[0].input_hash) == 64
    assert result.resolved_model == "unobserved"
    assert result.reasoning_setting == "unobserved"
    assert result.auth_mode == "unobserved"
    assert result.artifact is not None and result.artifact.path.is_file()
    recorded = json.loads(record_path.read_text(encoding="utf-8"))
    argv = recorded["argv"]
    assert argv[:1] == ["exec"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in argv
    assert argv[argv.index("--enable") + 1] == "multi_agent"
    assert "--json" in argv and "-" in argv
    assert "--output-schema" in argv
    assert "--output-last-message" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "--yolo" not in argv
    assert "--skip-git-repo-check" in argv
    assert Path(recorded["cwd"]).parent.name == "jobs"
    assert Path(recorded["last_message"]).parent == Path(recorded["cwd"])
    assert Path(recorded["output_schema"]).parent == Path(recorded["cwd"])
    job_document = json.loads(recorded["stdin"].splitlines()[-1])
    assert job_document["agent_id"] == "AI-O"
    assert recorded["skill_dirs"] == [
        "rakuten-room-inago",
        "rr-inago-copywriter",
        "rr-inago-keywords",
        "rr-inago-observation",
    ]
    assert recorded["agent_configs"] == [
        "rr-inago-copywriter.toml",
        "rr-inago-keywords.toml",
        "rr-inago-observation.toml",
    ]


@pytest.mark.parametrize(
    ("task_id", "trigger", "explicit_user_request"),
    [
        ("T02", "user_request", False),
        ("T05", "scheduled_daily", False),
        ("T07", "scheduled_daily", True),
        ("T11", "user_request", True),
    ],
)
def test_task_trigger_gate_rejects_unapproved_invocation_before_cli_start(
    tmp_path: Path,
    task_id: str,
    trigger: str,
    explicit_user_request: bool,
) -> None:
    request = _base_request(
        task_id=task_id,
        trigger=trigger,
        explicit_user_request=explicit_user_request,
    )
    runner, record_path, _ = _runner(tmp_path, response=None)

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.adoption_status == "rejected_contract"
    assert not record_path.exists()


@pytest.mark.parametrize(
    ("agent_id", "task_id", "skill_name"),
    [
        ("AG-OBS", "T02", "rr-inago-observation"),
        ("AG-OBS", "T04", "rr-inago-observation"),
        ("AG-KEY", "T02", "rr-inago-keywords"),
        ("AG-COPY", "T02", "rr-inago-copywriter"),
        ("AG-INSIGHT", "T05", "rr-inago-insight"),
        ("AG-CONFIG", "T06", "rr-inago-config"),
        ("AG-RECOVERY", "T08", "rr-inago-recovery"),
    ],
)
def test_fixed_specialist_task_skill_matrix(
    agent_id: str, task_id: str, skill_name: str
) -> None:
    assert authorized_agent_task(agent_id, task_id, skill_name)
    assert not authorized_agent_task(agent_id, "T10", skill_name)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        (
            "observation",
            {
                "field": "likes",
                "value": 300,
                "position": 1,
                "ref": "EVIDENCE-ROOM-1",
                "availability": "observed",
                "unknown_reason": None,
            },
        ),
        (
            "keywords",
            {"word_id": "WORD-01", "decision": "child", "reason": "関連語"},
        ),
        (
            "copy",
            {
                "offer_key": "shop-A:item-1",
                "draft_version": 1,
                "body": "本文",
                "pattern_id": "P02",
                "evidence_refs": ["EVIDENCE-ROOM-1"],
                "selection_reason": "利用情報に合う",
                "used_input_refs": ["INPUT-OFFERS-1"],
                "omitted_unknowns": [],
            },
        ),
        (
            "insight",
            {
                "scope": "当日",
                "findings": [
                    {
                        "statement": "欠測があります",
                        "evidence_refs": ["EVIDENCE-ROOM-1"],
                        "comparison_basis": "当日入力",
                        "limitations": "予測はしない",
                    }
                ],
            },
        ),
        (
            "config",
            {
                "proposal_id": "proposal-1",
                "reason": "明示依頼",
                "diff": [
                    {
                        "setting_key": "theme",
                        "before": "A",
                        "after": "B",
                        "rationale": "本人依頼の案",
                    }
                ],
                "affected_settings": ["theme"],
                "requires_owner_decision": True,
            },
        ),
        (
            "recovery",
            {
                "causes": [
                    {"hypothesis": "停止位置", "evidence_refs": ["EVIDENCE-ROOM-1"]}
                ],
                "unfinished": ["工程1"],
                "resume_proposal": "同じrunから再開",
                "owner_actions": ["状態確認"],
            },
        ),
    ],
)
def test_each_specialist_payload_schema_accepts_its_declared_shape(
    name: str, payload: dict[str, object]
) -> None:
    schema = json.loads(
        (SCHEMA_ROOT / f"ai-{name}.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


@pytest.mark.parametrize("task_id", ["T02", "T05", "T06", "T07", "T08", "T09", "T10"])
def test_ai_o_role_is_fixed_for_seven_supported_tasks(task_id: str) -> None:
    assert authorized_agent_task("AI-O", task_id, "rakuten-room-inago")
    assert not authorized_agent_task("AI-O", "T04", "rakuten-room-inago")


def test_daily_cannot_delegate_to_an_individually_valid_non_daily_role(
    tmp_path: Path,
) -> None:
    request = _base_request()
    result_payload = {
        "findings": [
            {
                "statement": "当日欠測があります",
                "evidence_refs": ["EVIDENCE-ROOM-1"],
                "comparison_basis": "当日入力のみ",
                "limitations": "推測を含まない",
            }
        ],
        "scope": "日次入力",
    }
    response = _root_response(
        request,
        delegations=[
            _delegation(
                "AG-INSIGHT",
                "T05",
                "rr-inago-insight",
                result_payload,
            )
        ],
    )
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response, ensure_ascii=False).encode("utf-8"),
        events=[_spawn_event("rr-inago-insight")],
    )

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.adoption_status == "rejected_contract"
    assert result.subjobs == ()
    assert result.artifact is not None


def test_copy_offer_or_draft_version_mismatch_is_rejected_and_raw_is_preserved(
    tmp_path: Path,
) -> None:
    request = _base_request()
    copy_payload = {
        "offer_key": "shop-A:item-1",
        "draft_version": 2,
        "body": "利用できる事実に基づいた本文です。",
        "pattern_id": "P02",
        "evidence_refs": ["EVIDENCE-ROOM-1"],
        "selection_reason": "商品入力に合う",
        "used_input_refs": ["INPUT-OFFERS-1"],
        "omitted_unknowns": [],
    }
    raw = json.dumps(
        _root_response(
            request,
            delegations=[
                _delegation(
                    "AG-COPY",
                    "T02",
                    "rr-inago-copywriter",
                    copy_payload,
                )
            ],
        ),
        ensure_ascii=False,
    ).encode("utf-8")
    runner, _, _ = _runner(
        tmp_path,
        response=raw,
        events=[_spawn_event("rr-inago-copywriter")],
    )

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.adoption_status == "rejected_contract"
    assert result.subjobs == ()
    assert result.artifact is not None
    assert result.artifact.path.read_bytes() == raw
    assert result.artifact.content_hash == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    ("raw", "exit_code", "expected_status", "expected_adoption"),
    [
        (b"", 0, "empty", "pending"),
        (b"not JSON", 0, "error", "rejected_contract"),
        (b'{"partial":', 7, "error", "pending"),
    ],
)
def test_empty_malformed_and_nonzero_responses_remain_distinct_and_saved(
    tmp_path: Path,
    raw: bytes,
    exit_code: int,
    expected_status: str,
    expected_adoption: str,
) -> None:
    runner, _, _ = _runner(tmp_path, response=raw, exit_code=exit_code)

    result = runner.run(_base_request())

    assert result.result_status == expected_status
    assert result.adoption_status == expected_adoption
    assert result.artifact is not None
    assert result.artifact.path.read_bytes() == raw


def test_mismatched_root_input_hash_is_not_accepted(tmp_path: Path) -> None:
    request = _base_request()
    response = _root_response(request, input_hash="another-job-input")
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode("utf-8"),
    )

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.adoption_status == "rejected_contract"
    assert result.artifact is not None


def test_root_cannot_cite_references_outside_its_input_and_evidence(
    tmp_path: Path,
) -> None:
    request = _base_request()
    response = _root_response(request)
    response["payload"]["references"] = ["UNPROVIDED-SOURCE"]
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode("utf-8"),
    )

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.adoption_status == "rejected_contract"
    assert result.error == {"code": "payload.references"}
    assert result.artifact is not None


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("settings_version", "a-different-settings-version"),
        ("input_refs", ["ANOTHER-INPUT"]),
        ("evidence_refs", ["ANOTHER-EVIDENCE"]),
    ],
)
def test_root_version_and_reference_mismatch_is_rejected(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    request = _base_request()
    response = _root_response(request, **{field: bad_value})
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode("utf-8"),
    )

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.adoption_status == "rejected_contract"
    assert result.artifact is not None


@pytest.mark.parametrize(
    ("delegations", "events"),
    [
        (
            [
                _delegation(
                    "AG-KEY",
                    "T02",
                    "rr-inago-keywords",
                    {"word_id": "WORD-01", "decision": "child", "reason": "用途"},
                )
            ],
            [],
        ),
        ([], [_spawn_event("rr-inago-keywords")]),
    ],
)
def test_native_subagent_events_and_final_delegations_must_match(
    tmp_path: Path,
    delegations: list[dict[str, object]],
    events: list[dict[str, object]],
) -> None:
    request = _base_request()
    response = _root_response(request, delegations=delegations)
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response, ensure_ascii=False).encode("utf-8"),
        events=events,
    )

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.adoption_status == "rejected_contract"
    assert result.subjobs == ()


def test_timeout_cancels_only_the_owned_cli_and_preserves_written_output(
    tmp_path: Path,
) -> None:
    request = _base_request()
    raw = json.dumps(_root_response(request)).encode("utf-8")
    runner, _, _ = _runner(
        tmp_path,
        response=raw,
        sleep=0.8,
        max_timeout_seconds=0.3,
    )

    result = runner.run(request)

    assert result.result_status == "timed_out"
    assert result.adoption_status == "expired"
    assert result.artifact is not None
    assert result.artifact.path.read_bytes() == raw


def test_pre_cancelled_invocation_does_not_start_cli(tmp_path: Path) -> None:
    cancelled = Event()
    cancelled.set()
    runner, record_path, _ = _runner(tmp_path, response=None)

    result = runner.run(_base_request(), cancel_event=cancelled)

    assert result.result_status == "cancelled"
    assert result.adoption_status == "expired"
    assert not record_path.exists()


def test_cancellation_during_cli_execution_keeps_completed_raw_response(
    tmp_path: Path,
) -> None:
    request = _base_request()
    raw = json.dumps(_root_response(request)).encode("utf-8")
    runner, record_path, _ = _runner(tmp_path, response=raw, sleep=2.0)
    cancel_event = Event()
    ready_path = tmp_path / "fake-ready.txt"

    def cancel_when_response_is_written() -> None:
        for _ in range(300):
            if ready_path.is_file():
                cancel_event.set()
                return
            sleep(0.01)

    canceller = Thread(target=cancel_when_response_is_written, daemon=True)
    canceller.start()
    result = runner.run(request, cancel_event=cancel_event)
    canceller.join(timeout=1.0)

    assert result.result_status == "cancelled"
    assert result.adoption_status == "expired"
    assert record_path.is_file()
    assert result.artifact is not None
    assert result.artifact.path.read_bytes() == raw


def test_billing_override_environment_is_never_passed_to_codex_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret-must-not-be-forwarded")
    request = _base_request(
        task_id="T07",
        trigger="user_request",
        explicit_user_request=True,
        copy_targets=(),
    )
    response = _root_response(request, delegations=[])
    runner, record_path, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode("utf-8"),
    )

    result = runner.run(request)

    assert result.result_status == "success"
    recorded = json.loads(record_path.read_text(encoding="utf-8"))
    assert recorded["billing_override_env_present"] is False


def test_response_that_finishes_after_deadline_is_late_not_accepted(
    tmp_path: Path,
) -> None:
    class DeadlineClock(FakeClock):
        checks = 0

        def deadline_remaining(self, deadline: float) -> float:
            self.checks += 1
            return 1.0 if self.checks <= 2 else 0.0

    request = _base_request()
    raw = json.dumps(_root_response(request)).encode("utf-8")
    runner, _, _ = _runner(
        tmp_path,
        response=raw,
        clock=DeadlineClock(NOW),
        poll_interval_seconds=1.0,
    )

    result = runner.run(request)

    assert result.result_status == "late_result"
    assert result.adoption_status == "expired"
    assert result.subjobs == ()
    assert result.artifact is not None


def test_shell_metacharacters_stay_in_stdin_and_never_become_commands(
    tmp_path: Path,
) -> None:
    sentinel = PROJECT_ROOT / "inago-shell-injection-sentinel.txt"
    assert not sentinel.exists()
    injected = f"hello; New-Item -Path '{sentinel}'"
    request = _base_request(input_payload={"note": injected})
    response = _root_response(request)
    runner, record_path, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode("utf-8"),
    )

    result = runner.run(request)

    assert result.result_status == "success"
    assert not sentinel.exists()
    recorded = json.loads(record_path.read_text(encoding="utf-8"))
    staged_input = json.loads(recorded["stdin"].splitlines()[-1])
    assert staged_input["input_payload"]["note"] == injected
    assert all(injected not in argument for argument in recorded["argv"])


def test_all_seven_role_schemas_are_valid_json_schema_documents() -> None:
    schema_paths = [SCHEMA_ROOT / "ai-job.schema.json"] + [
        SCHEMA_ROOT / f"ai-{name}.schema.json"
        for name in ("observation", "keywords", "copy", "insight", "config", "recovery")
    ]

    assert len(schema_paths) == 7
    for path in schema_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
