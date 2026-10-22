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
from jsonschema import Draft202012Validator

from automation.inago.ai_runner import (
    AIJobRequest,
    AIJobRunner,
    CopyTarget,
    authorized_agent_task,
    compute_input_hash,
    _child_agent_type,
    _rollout_settings,
)
from automation.inago.clock import FakeClock, JST
from automation.jsonschema_formats import rfc3339_format_checker


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
            "keywords": {
                "snapshot_id": "KEYWORDS-01",
                "query_word": "テーマ",
                "words": [{"word_id": "WORD-01", "raw": "用途", "source_rank": 1}],
            },
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


def test_child_agent_type_reads_spawn_from_saved_codex_rollout() -> None:
    event = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "spawn_agent",
            "call_id": "call-rollout-1",
            "arguments": json.dumps(
                {"agent_type": "rr-inago-observation", "task_name": "ag_obs"}
            ),
        },
    }

    assert _child_agent_type(event) == "rr-inago-observation"


def _runner(
    tmp_path: Path,
    *,
    response: bytes | None,
    events: list[dict[str, object]] | None = None,
    exit_code: int = 0,
    sleep: float = 0.0,
    clock: object | None = None,
    auth_mode: str = "chatgpt",
    require_observed_metadata: bool = False,
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
        auth_mode_provider=lambda: auth_mode,
        require_observed_metadata=require_observed_metadata,
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
        {
            "snapshot_id": "KEYWORDS-01",
            "query_word": "テーマ",
            "decisions": [
                {
                    "word_id": "WORD-01",
                    "decision": "candidate",
                    "group_ref": "WORD-01",
                    "reason": "用途が合う",
                }
            ],
        },
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
    assert result.auth_mode == "chatgpt"
    assert result.artifact is not None and result.artifact.path.is_file()
    recorded = json.loads(record_path.read_text(encoding="utf-8"))
    argv = recorded["argv"]
    assert argv[:1] == ["exec"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" not in argv
    assert argv[argv.index("--config") + 1] == "agents.enabled=true"
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
    assert (
        "Copy protocol_version, schema_version, run_id, job_id, attempt, task_id, "
        "agent_id, skill_name, target_date, settings_version, input_refs, "
        "evidence_refs, input_hash, and deadline_at exactly from job-input.json; "
        "never recompute or replace input_hash."
    ) in recorded["stdin"]
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
    trace_path = next((runner.data_root / "logs").rglob("*.jsonl"))
    trace_records = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    child_records = trace_records[-1]["child_agents"]
    assert child_records == [
        {
            "agent_id": "AG-KEY",
            "task_id": "T02",
            "skill_name": "rr-inago-keywords",
            "result_status": "success",
            "resolved_model": "unobserved",
            "reasoning_setting": "unobserved",
            "auth_mode": "chatgpt",
        }
    ]


@pytest.mark.parametrize("invented_keywords", [False, True])
def test_copy_only_job_does_not_authorize_missing_keyword_work(
    tmp_path: Path, invented_keywords: bool
) -> None:
    request = _base_request(input_payload={"offer": {"offer_key": "shop-A:item-1"}})
    agent_id = "AG-KEY" if invented_keywords else "AG-COPY"
    skill = "rr-inago-keywords" if invented_keywords else "rr-inago-copywriter"
    payload = (
        {"snapshot_id": "unavailable", "query_word": "unavailable", "decisions": []}
        if invented_keywords
        else {
            "offer_key": "shop-A:item-1",
            "draft_version": 1,
            "body": "確認できる商品情報に基づく紹介文です。",
            "pattern_id": "P01",
            "evidence_refs": ["EVIDENCE-ROOM-1"],
            "selection_reason": "確認できる特徴を紹介するため",
            "used_input_refs": ["INPUT-OFFERS-1"],
            "omitted_unknowns": [],
        }
    )
    response = _root_response(
        request, delegations=[_delegation(agent_id, "T02", skill, payload)]
    )
    runner, record_path, _ = _runner(
        tmp_path,
        response=json.dumps(response, ensure_ascii=False).encode("utf-8"),
        events=[_spawn_event(skill)],
    )

    result = runner.run(request)

    recorded = json.loads(record_path.read_text(encoding="utf-8"))
    staged_input = json.loads(recorded["stdin"].splitlines()[-1])
    assert "AG-KEY" not in {
        child["agent_id"] for child in staged_input["authorized_subagents"]
    }
    assert "rr-inago-keywords.toml" not in recorded["agent_configs"]
    if invented_keywords:
        assert result.adoption_status == "rejected_contract"
        assert result.error == {"code": "delegations[0].agent_id"}
    else:
        assert result.result_status == "success"
        assert [child.agent_id for child in result.subjobs] == ["AG-COPY"]


def test_runner_persists_sanitized_per_job_trace_events(tmp_path: Path) -> None:
    private_marker = "PRIVATE_FIXTURE_TEXT_MUST_NOT_ENTER_TRACE"
    request = _base_request(
        input_payload={"private_text": private_marker, "word_ids": ["WORD-01"]}
    )
    response = _root_response(request)
    response["payload"]["details"] = [private_marker]
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response, ensure_ascii=False).encode("utf-8"),
        events=[{"type": "thread.started", "thread_id": "fixture-session-id"}],
    )

    result = runner.run(request)

    assert result.result_status == "success"
    trace_files = list((runner.data_root / "logs").rglob("*.jsonl"))
    assert len(trace_files) == 2
    job_trace_files = [path for path in trace_files if request.job_id in path.stem]
    assert len(job_trace_files) == 1
    trace_text = job_trace_files[0].read_text(encoding="utf-8")
    records = [json.loads(line) for line in trace_text.splitlines()]
    assert [record["event"] for record in records] == [
        "job_started",
        "auth_observed",
        "cli_started",
        "cli_finished",
        "artifact_saved",
        "cli_event",
        "job_finished",
    ]
    assert all(record["job_id"] == request.job_id for record in records)
    assert all(record["run_id"] == request.run_id for record in records)
    assert records[-1]["result_status"] == "success"
    assert records[-1]["resolved_model"] == "unobserved"
    assert records[5]["event_type"] == "thread.started"
    assert private_marker not in trace_text
    assert "fixture-session-id" not in trace_text
    assert "PRIVATE_FIXTURE_TEXT_MUST_NOT_ENTER_TRACE" not in trace_text


def test_api_key_auth_is_rejected_before_cli_start(tmp_path: Path) -> None:
    request = _base_request()
    runner, record_path, _ = _runner(
        tmp_path,
        response=None,
        auth_mode="api_key",
    )

    result = runner.run(request)

    assert result.result_status == "error"
    assert result.error == {"code": "auth_mode_not_permitted"}
    assert result.auth_mode == "api_key"
    assert not record_path.exists()
    trace_files = list((runner.data_root / "logs").rglob("*.jsonl"))
    assert len(trace_files) == 2
    job_trace_files = [path for path in trace_files if request.job_id in path.stem]
    assert len(job_trace_files) == 1
    records = [
        json.loads(line)
        for line in job_trace_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in records] == [
        "job_started",
        "auth_observed",
        "job_finished",
    ]
    assert records[-1]["error_code"] == "auth_mode_not_permitted"


@pytest.mark.parametrize("inherited_history", [False, True])
def test_load_session_trace_links_root_spawn_to_completed_native_child(
    tmp_path: Path,
    inherited_history: bool,
) -> None:
    thread_id = "fake-root-01"
    sessions = tmp_path / "codex-home" / "sessions" / "2026" / "10" / "12"
    sessions.mkdir(parents=True)
    root_events = [
        {"type": "session_meta", "payload": {"id": thread_id}},
        {
            "type": "turn_context",
            "payload": {
                "model": "gpt-6-luna" if inherited_history else "gpt-6.1-sol",
                "effort": "max" if inherited_history else "xhigh",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "spawn_agent",
                "call_id": "call-child-1",
                "arguments": json.dumps(
                    {"agent_type": "rr-inago-observation", "task_name": "obs"}
                ),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-child-1",
                "output": "completed child rollout",
            },
        },
    ]
    child_events = [
        {
            "type": "session_meta",
            "payload": {
                "id": "fake-child-01",
                "parent_thread_id": thread_id,
                "thread_source": "subagent",
                "agent_role": "rr-inago-observation",
                "agent_path": "/root/observation",
            },
        },
        {
            "type": "turn_context",
            "payload": {"model": "gpt-6.1-sol", "effort": "xhigh"},
        },
    ]
    if inherited_history:
        child_events[1:1] = [
            {"type": "session_meta", "payload": {"id": thread_id}},
            {
                "type": "turn_context",
                "payload": {"model": "gpt-6-luna", "effort": "max"},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "thread_settings_applied",
                    "thread_id": "fake-child-01",
                },
            },
        ]
    root_path = sessions / f"rollout-2026-10-12T04-00-00-{thread_id}.jsonl"
    child_path = sessions / "rollout-2026-10-12T04-00-01-fake-child-01.jsonl"
    root_path.write_text(
        "".join(json.dumps(event) + "\n" for event in root_events),
        encoding="utf-8",
    )
    child_path.write_text(
        "".join(json.dumps(event) + "\n" for event in child_events),
        encoding="utf-8",
    )
    runner, _, _ = _runner(tmp_path, response=None)
    runner.environment = {"CODEX_HOME": str(tmp_path / "codex-home")}

    trace = runner._load_session_trace(
        [{"type": "thread.started", "thread_id": thread_id}],
        started_at="2026-10-12T13:00:00+09:00",
        finished_at="2026-10-12T13:00:10+09:00",
    )

    assert trace is not None
    assert trace.error is None
    assert trace.thread_id == thread_id
    assert trace.root_model == ("gpt-6-luna" if inherited_history else "gpt-6.1-sol")
    assert trace.root_reasoning_setting == ("max" if inherited_history else "xhigh")
    assert len(trace.spawn_events) == 1
    assert len(trace.children) == 1
    assert trace.children[0].agent_type == "rr-inago-observation"
    assert trace.children[0].model == "gpt-6.1-sol"
    assert trace.children[0].reasoning_setting == "xhigh"


@pytest.mark.parametrize(
    "boundary_thread,own_contexts",
    [
        ("root", [{"model": "gpt-6.1-sol", "effort": "xhigh"}]),
        ("child", []),
        (
            "child",
            [
                {"model": "gpt-6.1-sol", "effort": "xhigh"},
                {"model": "gpt-6.1-sol", "effort": "high"},
            ],
        ),
    ],
)
def test_forked_rollout_requires_consistent_observed_own_settings(
    boundary_thread: str, own_contexts: list[dict[str, str]]
) -> None:
    events = [
        {"type": "session_meta", "payload": {"id": "child"}},
        {"type": "session_meta", "payload": {"id": "root"}},
        {
            "type": "turn_context",
            "payload": {"model": "gpt-6-luna", "effort": "max"},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "thread_settings_applied",
                "thread_id": boundary_thread,
            },
        },
        *({"type": "turn_context", "payload": context} for context in own_contexts),
    ]

    assert _rollout_settings(events) == (None, None)


def test_live_ai_case_registry_covers_seven_roles_with_synthetic_safe_inputs() -> None:
    config = json.loads(
        (PROJECT_ROOT / "tests_inago" / "config" / "live-ai.json").read_text(
            encoding="utf-8"
        )
    )
    registry = json.loads(
        (PROJECT_ROOT / "tests_inago" / "live_ai_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases = registry["cases"]

    assert config["auth_mode"] == "chatgpt"
    assert config["allowed_auth_modes"] == ["chatgpt"]
    assert config["require_observed_metadata"] is True
    assert config["sandbox"] == "read-only"
    assert config["native_subagents"] is True
    assert config["billing"]["allow_api_key_fallback"] is False
    assert config["billing"]["allow_paid_api_or_model_switch"] is False
    assert {case["task_id"] for case in cases} == {
        "T02",
        "T05",
        "T06",
        "T07",
        "T08",
        "T09",
        "T10",
    }
    assert len({case["case_id"] for case in cases}) == 8
    daily_cases = [case for case in cases if case["task_id"] == "T02"]
    assert sorted(len(case["copy_targets"]) for case in daily_cases) == [1, 2]
    for case in cases:
        assert case["input_payload"]["synthetic_test_only"] is True
        assert case["input_refs"] and len(set(case["input_refs"])) == len(
            case["input_refs"]
        )
        assert case["evidence_refs"] and len(set(case["evidence_refs"])) == len(
            case["evidence_refs"]
        )
        if case["task_id"] == "T02":
            assert case["trigger"] == "scheduled_daily"
            assert case["explicit_user_request"] is False
            assert "search_limit" not in case["input_payload"]
            assert len({target["offer_key"] for target in case["copy_targets"]}) == len(
                case["copy_targets"]
            )
        else:
            assert case["trigger"] == "user_request"
            assert case["explicit_user_request"] is True
            assert case["request_text"].startswith("私が明示的に依頼します。")

    two_product_case = next(
        case for case in daily_cases if len(case["copy_targets"]) == 2
    )
    target_keys = {target["offer_key"] for target in two_product_case["copy_targets"]}
    offers = two_product_case["input_payload"]["offers"]
    assert {offer["offer_key"] for offer in offers} == target_keys
    assert offers[1]["confirmed_facts"] == []
    assert offers[1]["owner_experience"] is None

    adopted_settings = json.loads(
        (
            PROJECT_ROOT / "docs" / "specifications" / "preparation" / "初期設定引渡し.json"
        ).read_text(encoding="utf-8")
    )
    theme = next(
        item
        for item in adopted_settings["themes"]
        if item["theme_id"] == cases[0]["input_payload"]["parent_theme"]["theme_id"]
    )
    assert adopted_settings["version"] == registry["settings_version"]
    assert cases[0]["input_payload"]["parent_theme"]["name"] == theme["name"]

    def contains_sensitive_key(value: object) -> bool:
        if isinstance(value, dict):
            blocked_keys = {
                "password",
                "api_key",
                "api_token",
                "access_token",
                "refresh_token",
                "cookie",
                "secret",
            }
            if any(key.casefold() in blocked_keys for key in value):
                return True
            return any(contains_sensitive_key(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_sensitive_key(item) for item in value)
        return False

    assert not contains_sensitive_key(config)
    assert not contains_sensitive_key(registry)


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
            {
                "snapshot_id": "KEYWORDS-01",
                "query_word": "テーマ",
                "decisions": [
                    {
                        "word_id": "WORD-01",
                        "decision": "candidate",
                        "group_ref": "WORD-01",
                        "reason": "関連語",
                    }
                ],
            },
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
    Draft202012Validator(
        schema, format_checker=rfc3339_format_checker()
    ).validate(payload)


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
                    {
                        "snapshot_id": "KEYWORDS-01",
                        "query_word": "テーマ",
                        "decisions": [
                            {
                                "word_id": "WORD-01",
                                "decision": "candidate",
                                "group_ref": "WORD-01",
                                "reason": "用途",
                            }
                        ],
                    },
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
    trace_files = list((runner.data_root / "logs").rglob("*.jsonl"))
    records = [
        json.loads(line)
        for line in trace_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        record["event"] == "cli_finished" and record["process_status"] == "timed_out"
        for record in records
    )
    assert records[-1]["result_status"] == "timed_out"


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


@pytest.mark.parametrize(
    ("agent_id", "schema_name", "payload"),
    [
        (
            "AG-OBS",
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
            "AG-KEY",
            "keywords",
            {
                "snapshot_id": "KEYWORDS-01",
                "query_word": "テーマ",
                "decisions": [
                    {
                        "word_id": "WORD-01",
                        "decision": "candidate",
                        "group_ref": "WORD-01",
                        "reason": "関連語",
                    }
                ],
            },
        ),
        (
            "AG-COPY",
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
            "AG-INSIGHT",
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
            "AG-CONFIG",
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
            "AG-RECOVERY",
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
def test_root_schema_uses_strict_specialist_payload_contracts(
    agent_id: str, schema_name: str, payload: dict[str, object]
) -> None:
    root_schema = json.loads(
        (SCHEMA_ROOT / "ai-job.schema.json").read_text(encoding="utf-8")
    )
    child_schema = json.loads(
        (SCHEMA_ROOT / f"ai-{schema_name}.schema.json").read_text(encoding="utf-8")
    )
    payload_union = root_schema["$defs"].get("delegation_payload", {}).get("anyOf", [])
    normalized_child_schema = {
        key: value
        for key, value in child_schema.items()
        if key not in {"$schema", "$id", "title"}
    }

    assert normalized_child_schema in payload_union, agent_id
    assert normalized_child_schema["additionalProperties"] is False
    Draft202012Validator(normalized_child_schema).validate(payload)


def test_ai_job_output_schema_uses_codex_strict_json_schema_subset() -> None:
    schema = json.loads(
        (SCHEMA_ROOT / "ai-job.schema.json").read_text(encoding="utf-8")
    )

    def visit(node: object) -> None:
        if isinstance(node, dict):
            assert "const" not in node
            assert not isinstance(node.get("type"), list)
            if node.get("type") == "object":
                properties = node.get("properties", {})
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
