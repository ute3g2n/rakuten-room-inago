"""Handoff identity, version, and artifact boundary contract cases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import timedelta
from unittest.mock import patch

import pytest

from automation.inago.app_server import AppServerError, AppServerSession, AppServerTurn
from automation.inago.maintenance.ai_adapter import (
    MaintenanceAIAdapter,
    MaintenanceAIBlockedError,
    MaintenanceAISession,
)
from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.maintenance.store import (
    HandoffContractError,
    MaintenanceStoreError,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)


def _job_identity(incident_id: str, job_id: str) -> dict[str, object]:
    return {
        "incident_id": incident_id,
        "source_launch_id": "launch-1",
        "source_run_id": None,
        "target_date": "2026-10-17",
        "job_id": job_id,
    }


def _adapter(tmp_path: Path, store):
    attempts_root = tmp_path / "attempts"
    worktrees_root = tmp_path / "worktrees"
    attempts_root.mkdir()
    worktrees_root.mkdir()
    return (
        MaintenanceAIAdapter(
            MaintenanceController(store),
            base_command=("codex", "app-server"),
            project_root=Path(__file__).resolve().parents[1],
            attempts_root=attempts_root,
            worktrees_root=worktrees_root,
        ),
        attempts_root,
        worktrees_root,
    )


def _bind_job(store, incident_id: str, job_id: str):
    return MaintenanceController(store).bind_job_input_manifest(
        **_job_identity(incident_id, job_id),
        created_at=(BASE_TIME + timedelta(seconds=6)).isoformat(),
    )


def _start_test_attempt(store, job_id: str) -> str:
    lease = store.acquire_lease(
        lease_key="maintenance-test-worker",
        owner_id="maintenance-test-owner",
        owner_pid=4201,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=180,
    )
    attempt = store.begin_step_attempt(
        job_id=job_id,
        step_key="u07-session",
        sequence=1,
        lease=lease,
        started_at=(BASE_TIME + timedelta(seconds=11)).isoformat(),
    )
    return attempt.attempt_id


def _bind_ai_session(store, plan, *, thread_id="thread-ai-output"):
    return store.bind_ai_parent_session(
        job_id=plan.manifest.job_id,
        session_key=plan.session_key,
        parent_key=plan.parent_key,
        role_name=plan.role_name,
        thread_id=thread_id,
        manifest_sha256=plan.manifest.manifest_sha256,
        requested_model=plan.requested_model,
        requested_effort=plan.requested_effort,
        requested_service_tier=plan.requested_service_tier,
        requested_fast_mode=plan.requested_fast_mode,
        role_config_sha256=plan.role_config_sha256,
        sandbox=plan.sandbox_policy,
        created_at=(BASE_TIME + timedelta(seconds=20)).isoformat(),
    )


def test_c24_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()

    assert set(adapter.allowed_roles("A")) == {
        "rr-inago-incident-investigator",
        "rr-inago-incident-investigation-reviewer",
        "common-execution-planner",
        "common-execution-plan-reviewer",
    }
    for role_name in adapter.allowed_roles("A"):
        plan = adapter.plan_session(
            parent_key="A",
            role_name=role_name,
            job_id=job.job_id,
            attempt_id=attempt_id,
            identity=_job_identity(incident_id, job.job_id),
            cwd=adapter.project_root,
            attempt_output_root=output_root,
            worktree_root=worktree_root,
        )
        assert plan.requested_model == "gpt-6.1-sol"
        assert plan.requested_effort == "xhigh"
        assert plan.requested_service_tier == "default"
        assert plan.observed_settings == {
            "model": None,
            "effort": None,
            "service_tier": None,
        }
        assert plan.observation_status == "configured_unverified"
        assert not any("service_tier=" in value for value in plan.command)
        assert "features.fast_mode=false" in plan.command

    plan = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=adapter.project_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )
    with patch("automation.inago.maintenance.ai_adapter.AppServerSession") as factory:
        adapter.create_session(plan)
    assert factory.call_args.kwargs["model"] == "gpt-6.1-sol"
    assert factory.call_args.kwargs["effort"] == "xhigh"
    assert factory.call_args.kwargs["service_tier"] == "default"
    assert factory.call_args.kwargs["log_root"] == adapter.logs_root
    assert factory.call_args.kwargs["fast_mode"] is False
    assert factory.call_args.kwargs["role_name"] == plan.role_name
    assert factory.call_args.kwargs["role_config_sha256"] == plan.role_config_sha256
    assert (
        factory.call_args.kwargs["developer_instructions"]
        == plan.developer_instructions
    )
    assert factory.call_args.kwargs["sandbox_policy"] == plan.sandbox_policy


def test_c25_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()

    assert set(adapter.allowed_roles("B")) == {
        "common-implementer",
        "common-tester",
        "common-reviewer",
        "rr-inago-incident-reporter",
        "rr-inago-incident-report-reviewer",
    }
    for role_name in adapter.allowed_roles("B"):
        plan = adapter.plan_session(
            parent_key="B",
            role_name=role_name,
            job_id=job.job_id,
            attempt_id=attempt_id,
            identity=_job_identity(incident_id, job.job_id),
            cwd=worktree_root,
            attempt_output_root=output_root,
            worktree_root=worktree_root,
        )
        assert plan.requested_model == "gpt-6-luna"
        assert plan.requested_effort == "max"
        assert plan.requested_service_tier == "priority"
        assert 'service_tier="fast"' in plan.command
        assert "features.fast_mode=true" in plan.command

    plan = adapter.plan_session(
        parent_key="B",
        role_name="common-implementer",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=worktree_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )
    with patch("automation.inago.maintenance.ai_adapter.AppServerSession") as factory:
        adapter.create_session(plan)
    assert factory.call_args.kwargs["model"] == "gpt-6-luna"
    assert factory.call_args.kwargs["effort"] == "max"
    assert factory.call_args.kwargs["service_tier"] == "priority"
    assert factory.call_args.kwargs["log_root"] == adapter.logs_root
    assert factory.call_args.kwargs["fast_mode"] is True
    assert factory.call_args.kwargs["role_name"] == plan.role_name
    assert factory.call_args.kwargs["role_config_sha256"] == plan.role_config_sha256
    assert (
        factory.call_args.kwargs["developer_instructions"]
        == plan.developer_instructions
    )
    assert factory.call_args.kwargs["sandbox_policy"] == plan.sandbox_policy


def test_c26_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / "missing-attempt"
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()

    with pytest.raises(MaintenanceAIBlockedError, match="job_input_manifest_missing"):
        adapter.plan_session(
            parent_key="B",
            role_name="common-implementer",
            job_id=job.job_id,
            attempt_id="missing-attempt",
            identity=_job_identity(incident_id, job.job_id),
            cwd=worktree_root,
            attempt_output_root=output_root,
            worktree_root=worktree_root,
        )

    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    plan = adapter.plan_session(
        parent_key="B",
        role_name="common-implementer",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=worktree_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )
    with pytest.raises(
        MaintenanceAIBlockedError, match="runtime_observation_unavailable"
    ):
        adapter.require_verified_runtime(plan)
    assert plan.observation_status == "configured_unverified"
    assert plan.observed_settings["service_tier"] is None

    with pytest.raises(MaintenanceAIBlockedError, match="role_model_effort_mismatch"):
        adapter.validate_role_settings(
            parent_key="B",
            role_name="common-implementer",
            model="gpt-6.1-sol",
            effort="xhigh",
        )
    with pytest.raises(MaintenanceAIBlockedError, match="model_effort_unavailable"):
        adapter.validate_model_capability("gpt-6-luna", "max", available_models=())


def test_c27_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    common = {
        "job_id": job.job_id,
        "attempt_id": attempt_id,
        "identity": _job_identity(incident_id, job.job_id),
        "attempt_output_root": output_root,
        "worktree_root": worktree_root,
    }

    for unrelated_output_root in (
        attempts_root / "different-job" / attempt_id,
        attempts_root / job.job_id / "different-attempt",
    ):
        unrelated_output_root.mkdir(parents=True)
        with pytest.raises(MaintenanceAIBlockedError, match="write_root_out_of_scope"):
            adapter.plan_session(
                parent_key="A",
                role_name="rr-inago-incident-investigator",
                cwd=adapter.project_root,
                **{**common, "attempt_output_root": unrelated_output_root},
            )

    unrelated_worktree = worktrees_root / "different-job"
    unrelated_worktree.mkdir()
    with pytest.raises(MaintenanceAIBlockedError, match="write_root_out_of_scope"):
        adapter.plan_session(
            parent_key="B",
            role_name="common-implementer",
            cwd=unrelated_worktree,
            **{**common, "worktree_root": unrelated_worktree},
        )

    investigator = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        cwd=adapter.project_root,
        **common,
    )
    assert investigator.sandbox_policy == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert not investigator.log_path.is_relative_to(output_root.resolve())

    implementer = adapter.plan_session(
        parent_key="B",
        role_name="common-implementer",
        cwd=worktree_root,
        **common,
    )
    assert implementer.sandbox_policy == {
        "type": "workspaceWrite",
        "networkAccess": False,
    }
    assert not implementer.log_path.is_relative_to(worktree_root.resolve())

    reviewer = adapter.plan_session(
        parent_key="B",
        role_name="common-reviewer",
        cwd=worktree_root,
        **common,
    )
    assert reviewer.sandbox_policy == {
        "type": "readOnly",
        "networkAccess": False,
    }
    for policy in (
        investigator.sandbox_policy,
        implementer.sandbox_policy,
        reviewer.sandbox_policy,
    ):
        assert not {
            "access",
            "readOnlyAccess",
            "readableRoots",
            "writableRoots",
        }.intersection(policy)

    with pytest.raises(MaintenanceAIBlockedError, match="cwd_out_of_scope"):
        adapter.plan_session(
            parent_key="B",
            role_name="common-implementer",
            job_id=job.job_id,
            attempt_id=attempt_id,
            identity=_job_identity(incident_id, job.job_id),
            cwd=adapter.project_root,
            attempt_output_root=output_root,
            worktree_root=worktree_root,
        )

    reviewer_plan = adapter.plan_session(
        parent_key="B",
        role_name="common-reviewer",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=worktree_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )
    assert reviewer_plan.sandbox_policy == {
        "type": "readOnly",
        "networkAccess": False,
    }


@pytest.mark.parametrize("linked_root", ("attempts", "worktrees"))
def test_c27_rejects_parent_reparse_points_during_planning(tmp_path, linked_root):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    outside_root = tmp_path / f"outside-{linked_root}"
    outside_root.mkdir()

    if linked_root == "attempts":
        linked_parent = attempts_root / job.job_id
        output_root = linked_parent / attempt_id
        output_root.mkdir(parents=True)
        worktree_root = worktrees_root / job.job_id
        worktree_root.mkdir()
    else:
        linked_parent = worktrees_root / job.job_id
        linked_parent.mkdir()
        output_root = attempts_root / job.job_id / attempt_id
        output_root.mkdir(parents=True)
        worktree_root = linked_parent

    # Replacing the parent with a directory symlink reproduces a junction escape.
    if linked_root == "attempts":
        output_root.rmdir()
        linked_parent.rmdir()
    else:
        linked_parent.rmdir()
    try:
        linked_parent.symlink_to(outside_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable on this host: {exc}")
    if linked_root == "attempts":
        (outside_root / attempt_id).mkdir()

    with pytest.raises(MaintenanceAIBlockedError, match="write_root_out_of_scope"):
        adapter.plan_session(
            parent_key="A",
            role_name="rr-inago-incident-investigator",
            job_id=job.job_id,
            attempt_id=attempt_id,
            identity=_job_identity(incident_id, job.job_id),
            cwd=adapter.project_root,
            attempt_output_root=output_root,
            worktree_root=worktree_root,
        )


def test_c27_revalidates_attempt_parent_before_turn(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    plan = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=adapter.project_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )

    job_root = attempts_root / job.job_id
    saved_job_root = attempts_root / f"{job.job_id}-saved"
    job_root.rename(saved_job_root)
    outside_root = tmp_path / "outside-attempt-parent"
    outside_root.mkdir()
    try:
        job_root.symlink_to(outside_root, target_is_directory=True)
    except OSError as exc:
        saved_job_root.rename(job_root)
        pytest.skip(f"directory symlinks are unavailable on this host: {exc}")

    class NeverCalledAppServer:
        def run_turn(self, *_args, **_kwargs):
            raise AssertionError("reparsed output root reached app-server")

    session = MaintenanceAISession(
        MaintenanceController(store), plan, NeverCalledAppServer()
    )
    with pytest.raises(MaintenanceAIBlockedError, match="allowed_outputs_invalid"):
        session.run_turn(
            "safe test prompt",
            {"type": "object"},
            allowed_outputs=("result.json",),
            deadline=1.0,
        )


def test_c27_revalidates_attempt_parent_before_persisting_response(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    plan = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=adapter.project_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )
    _bind_ai_session(store, plan)
    job_root = attempts_root / job.job_id
    saved_job_root = attempts_root / f"{job.job_id}-saved"
    outside_root = tmp_path / "outside-persist-parent"
    outside_root.mkdir()

    class ReparseDuringTurnAppServer:
        def run_turn(self, *_args, **_kwargs):
            job_root.rename(saved_job_root)
            try:
                job_root.symlink_to(outside_root, target_is_directory=True)
            except OSError as exc:
                saved_job_root.rename(job_root)
                pytest.skip(f"directory symlinks are unavailable on this host: {exc}")
            return AppServerTurn(
                thread_id="thread-ai-output",
                turn_id="turn-ai-output",
                final_text=json.dumps(_job_identity(incident_id, job.job_id)),
                events=(),
                process_returncode=0,
                observed_model="gpt-6.1-sol",
                observed_effort="xhigh",
                observed_service_tier="default",
            )

    session = MaintenanceAISession(
        MaintenanceController(store), plan, ReparseDuringTurnAppServer()
    )
    with pytest.raises(MaintenanceAIBlockedError, match="response_output_root_invalid"):
        session.run_turn(
            "safe test prompt",
            {"type": "object"},
            allowed_outputs=("result.json",),
            deadline=1.0,
        )

    assert not list(outside_root.iterdir())


def test_c27_turn_cwd_stays_at_managed_workspace(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    plan = adapter.plan_session(
        parent_key="B",
        role_name="common-implementer",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=worktree_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )

    class NeverCalledAppServer:
        def run_turn(self, *_args, **_kwargs):
            raise AssertionError("out-of-scope cwd reached app-server")

    session = MaintenanceAISession(
        MaintenanceController(store), plan, NeverCalledAppServer()
    )
    with pytest.raises(MaintenanceAIBlockedError, match="cwd_out_of_scope"):
        session.run_turn("prompt", {}, allowed_outputs=(), cwd=tmp_path, deadline=1.0)


def test_c27_ai_response_is_validated_bound_and_saved_in_attempt_output(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    plan = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=adapter.project_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )
    _bind_ai_session(store, plan)
    response = {
        **_job_identity(incident_id, job.job_id),
        "summary": "validated test result",
    }

    class FakeAppServer:
        def run_turn(self, *_args, **_kwargs):
            return AppServerTurn(
                thread_id="thread-ai-output",
                turn_id="turn-ai-output",
                final_text=json.dumps(response),
                events=(),
                process_returncode=0,
                observed_model="gpt-6.1-sol",
                observed_effort="xhigh",
                observed_service_tier="default",
            )

    schema = {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "source_launch_id": {"type": "string"},
            "source_run_id": {"type": ["string", "null"]},
            "target_date": {"type": "string"},
            "job_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": [
            "incident_id",
            "source_launch_id",
            "source_run_id",
            "target_date",
            "job_id",
            "summary",
        ],
        "additionalProperties": False,
    }
    result = MaintenanceAISession(
        MaintenanceController(store), plan, FakeAppServer()
    ).run_turn(
        "safe test prompt",
        schema,
        allowed_outputs=("facts.json", "investigation.md", "result.json"),
        deadline=100.0,
    )

    assert result.response == response
    assert result.output_path.is_relative_to(output_root.resolve())
    saved = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert saved["identity"] == plan.manifest.identity()
    assert saved["attempt_id"] == attempt_id
    assert saved["owner"] == {
        "parent_key": "A",
        "role_name": "rr-inago-incident-investigator",
        "role_config_sha256": plan.role_config_sha256,
    }
    assert saved["allowed_outputs"] == [
        "facts.json",
        "investigation.md",
        "result.json",
    ]
    assert saved["response"] == response
    assert hashlib.sha256(result.output_path.read_bytes()).hexdigest() == (
        result.output_sha256
    )


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    (
        ("identity", "response_identity_mismatch"),
        ("missing-null-identity", "response_identity_mismatch"),
        ("schema", "response_schema_invalid"),
    ),
)
def test_c27_ai_response_contract_mismatch_is_not_saved(
    tmp_path, mutation, reason_code
):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    plan = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=adapter.project_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )
    _bind_ai_session(store, plan)
    response = {**_job_identity(incident_id, job.job_id), "summary": "test result"}
    if mutation == "identity":
        response["incident_id"] = "wrong-incident"
    elif mutation == "missing-null-identity":
        response.pop("source_run_id")
    else:
        response.pop("summary")

    class FakeAppServer:
        def run_turn(self, *_args, **_kwargs):
            return AppServerTurn(
                thread_id="thread-ai-output",
                turn_id="turn-ai-output",
                final_text=json.dumps(response),
                events=(),
                process_returncode=0,
                observed_model="gpt-6.1-sol",
                observed_effort="xhigh",
                observed_service_tier="default",
            )

    schema = {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "source_launch_id": {"type": "string"},
            "source_run_id": {"type": ["string", "null"]},
            "target_date": {"type": "string"},
            "job_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": [
            "incident_id",
            "source_launch_id",
            "source_run_id",
            "target_date",
            "job_id",
            "summary",
        ],
        "additionalProperties": False,
    }
    if mutation == "missing-null-identity":
        schema["required"].remove("source_run_id")
    session = MaintenanceAISession(MaintenanceController(store), plan, FakeAppServer())

    with pytest.raises(MaintenanceAIBlockedError, match=reason_code):
        session.run_turn(
            "safe test prompt",
            schema,
            allowed_outputs=("result.json",),
            deadline=100.0,
        )

    assert not list(output_root.glob("*.json"))


@pytest.mark.parametrize(
    ("schema", "allowed_outputs", "reason_code"),
    (
        ({"type": "object"}, ("../outside.json",), "allowed_outputs_invalid"),
        ({"type": "object"}, ("report.",), "allowed_outputs_invalid"),
        ({"type": "object"}, ("report ",), "allowed_outputs_invalid"),
        ({"type": "object"}, ("NUL",), "allowed_outputs_invalid"),
        ({"type": "object"}, ("nul.txt",), "allowed_outputs_invalid"),
        ({"type": "object"}, ("CON.txt",), "allowed_outputs_invalid"),
        ({"type": "object"}, ("folder/COM1.json",), "allowed_outputs_invalid"),
        ({"type": "object"}, ("bad?.json",), "allowed_outputs_invalid"),
        (
            {"type": "object"},
            ("result.json", "RESULT.json"),
            "allowed_outputs_invalid",
        ),
        ({"type": "invalid"}, (), "output_schema_invalid"),
    ),
)
def test_c27_ai_response_rejects_invalid_contract_before_turn(
    tmp_path, schema, allowed_outputs, reason_code
):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    adapter, attempts_root, worktrees_root = _adapter(tmp_path, store)
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    plan = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=adapter.project_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )

    class NeverCalledAppServer:
        def run_turn(self, *_args, **_kwargs):
            raise AssertionError("invalid allowed output reached app-server")

    session = MaintenanceAISession(
        MaintenanceController(store), plan, NeverCalledAppServer()
    )
    with pytest.raises(MaintenanceAIBlockedError, match=reason_code):
        session.run_turn(
            "safe test prompt",
            schema,
            allowed_outputs=allowed_outputs,
            deadline=100.0,
        )


def test_c27_turn_sends_workspace_mode_without_folder_roots(tmp_path):
    from types import SimpleNamespace

    app_server = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=tmp_path / "session.jsonl",
        model=None,
        sandbox_policy={"type": "workspaceWrite", "networkAccess": False},
    )
    app_server.thread_id = "thread-c27"
    app_server.process = SimpleNamespace(pid=123, poll=lambda: 0)
    app_server.start = lambda **_kwargs: "thread-c27"
    calls = []
    app_server._call = lambda method, params, **_kwargs: (
        calls.append((method, params)) or {"turn": {"id": "turn-c27"}}
    )
    notifications = iter(
        (
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-c27",
                    "turnId": "turn-c27",
                    "item": {
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "structured output",
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-c27",
                    "turnId": "turn-c27",
                    "turn": {"id": "turn-c27", "status": "completed"},
                },
            },
        )
    )
    app_server._receive = lambda *_args, **_kwargs: next(notifications)
    app_server._log = lambda *_args, **_kwargs: None

    result = app_server.run_turn(
        "safe test prompt",
        {"type": "object", "properties": {}},
        cwd=tmp_path,
        deadline=100.0,
    )

    turn_call = next(params for method, params in calls if method == "turn/start")
    assert turn_call["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "networkAccess": False,
    }
    assert "writableRoots" not in turn_call["sandboxPolicy"]
    assert "readOnlyAccess" not in turn_call["sandboxPolicy"]
    assert result.final_text == "structured output"


def test_c27_app_server_log_is_confined_to_host_owned_root(tmp_path):
    log_root = tmp_path / "maintenance-logs"
    log_root.mkdir()
    safe_log = log_root / "2026-10-17" / "job-1" / "B--common-reviewer.jsonl"
    safe_session = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=safe_log,
        log_root=log_root,
    )
    safe_session._log("root_ready", model="gpt-6-luna")
    assert safe_log.is_file()

    outside_log = tmp_path / "outside.jsonl"
    escaped_session = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=outside_log,
        log_root=log_root,
    )
    with pytest.raises(AppServerError, match="log_path_out_of_scope"):
        escaped_session._log("root_ready", model="gpt-6-luna")
    assert not outside_log.exists()


def test_c27_log_parent_reparse_point_blocks_session_before_log_creation(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    _bind_job(store, incident_id, job.job_id)
    attempt_id = _start_test_attempt(store, job.job_id)
    configured_root = tmp_path / "configured-maintenance-root"
    attempts_root = configured_root / "attempts"
    attempts_root.mkdir(parents=True)
    worktrees_root = tmp_path / "worktrees"
    worktrees_root.mkdir()
    output_root = attempts_root / job.job_id / attempt_id
    output_root.mkdir(parents=True)
    worktree_root = worktrees_root / job.job_id
    worktree_root.mkdir()
    adapter = MaintenanceAIAdapter(
        MaintenanceController(store),
        base_command=("codex", "app-server"),
        project_root=Path(__file__).resolve().parents[1],
        attempts_root=attempts_root,
        worktrees_root=worktrees_root,
    )
    plan = adapter.plan_session(
        parent_key="A",
        role_name="rr-inago-incident-investigator",
        job_id=job.job_id,
        attempt_id=attempt_id,
        identity=_job_identity(incident_id, job.job_id),
        cwd=adapter.project_root,
        attempt_output_root=output_root,
        worktree_root=worktree_root,
    )

    saved_root = tmp_path / "configured-maintenance-root-saved"
    configured_root.rename(saved_root)
    outside_root = tmp_path / "outside-log-root"
    outside_root.mkdir()
    try:
        configured_root.symlink_to(outside_root, target_is_directory=True)
    except OSError as exc:
        saved_root.rename(configured_root)
        pytest.skip(f"directory symlinks are unavailable on this host: {exc}")

    with pytest.raises(MaintenanceAIBlockedError, match="log_path_invalid"):
        adapter.create_session(plan)

    assert not (outside_root / "logs").exists()


def test_u07_job_input_manifest_and_session_bindings_are_immutable(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    store.record_daily_check(
        source_launch_id="launch-2",
        source_run_id=None,
        target_date="2026-10-17",
        check_status="abnormal",
        observed_at=(BASE_TIME + timedelta(seconds=1)).isoformat(),
        reason_code="daily_error",
        incident={
            "dedupe_key": "daily:component-a:error-a",
            "component": "daily_pipeline",
            "event_code": "daily_error",
        },
    )
    job = create_ready_release(store, incident_id=incident_id)
    manifest = _bind_job(store, incident_id, job.job_id)
    assert manifest.identity() == _job_identity(incident_id, job.job_id)
    assert manifest.source_run_id is None
    assert (
        store.get_job_input_manifest(
            job_id=job.job_id,
            expected_identity=_job_identity(incident_id, job.job_id),
        )
        == manifest
    )
    with pytest.raises(
        MaintenanceStoreError, match="job_input_manifest_binding_mismatch"
    ):
        store.bind_job_input_manifest(
            incident_id=incident_id,
            source_launch_id="launch-1",
            source_run_id="run-inferred",
            target_date="2026-10-17",
            job_id=job.job_id,
            created_at=(BASE_TIME + timedelta(seconds=7)).isoformat(),
        )
    with pytest.raises(
        MaintenanceStoreError, match="job_input_manifest_identity_mismatch"
    ):
        store.get_job_input_manifest(
            job_id=job.job_id,
            expected_identity={
                **_job_identity(incident_id, job.job_id),
                "source_launch_id": "launch-2",
            },
        )
    with pytest.raises(MaintenanceStoreError, match="job_input_manifest_conflict"):
        store.bind_job_input_manifest(
            incident_id=incident_id,
            source_launch_id="launch-2",
            source_run_id=None,
            target_date="2026-10-17",
            job_id=job.job_id,
            created_at=(BASE_TIME + timedelta(seconds=8)).isoformat(),
        )

    sandbox = {"type": "readOnly", "networkAccess": False}
    session_values = {
        "job_id": job.job_id,
        "session_key": "B:common-reviewer",
        "parent_key": "B",
        "role_name": "common-reviewer",
        "thread_id": "thr_persistent-1",
        "manifest_sha256": manifest.manifest_sha256,
        "requested_model": "gpt-6-luna",
        "requested_effort": "max",
        "requested_service_tier": "priority",
        "requested_fast_mode": True,
        "role_config_sha256": "a" * 64,
        "sandbox": sandbox,
        "created_at": (BASE_TIME + timedelta(seconds=9)).isoformat(),
    }
    stored_session = store.bind_ai_parent_session(**session_values)
    assert stored_session.thread_id == "thr_persistent-1"
    resumed_store = make_store(tmp_path)
    assert (
        resumed_store.get_ai_parent_session(
            job_id=job.job_id,
            session_key="B:common-reviewer",
        )
        == stored_session
    )
    assert resumed_store.bind_ai_parent_session(**session_values) == stored_session
    with pytest.raises(MaintenanceStoreError, match="ai_session_identity_conflict"):
        resumed_store.bind_ai_parent_session(
            **{**session_values, "thread_id": "thr-persistent-2"}
        )

    observation_id = resumed_store.record_ai_session_observation(
        job_id=job.job_id,
        session_key="B:common-reviewer",
        thread_id="thr_persistent-1",
        status="configured_unverified",
        observed_at=(BASE_TIME + timedelta(seconds=10)).isoformat(),
        reason_code="runtime_observation_unavailable",
    )
    assert observation_id
    with sqlite3.connect(resumed_store.database_path) as connection:
        with pytest.raises(
            sqlite3.IntegrityError, match="job_input_manifest_immutable"
        ):
            connection.execute(
                "UPDATE job_input_manifest SET source_launch_id = 'launch-2' WHERE job_id = ?",
                (job.job_id,),
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="maintenance_ai_session_immutable"
        ):
            connection.execute(
                "DELETE FROM maintenance_ai_session WHERE job_id = ?",
                (job.job_id,),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="maintenance_ai_observation_immutable",
        ):
            connection.execute(
                "UPDATE maintenance_ai_observation SET status = 'verified' "
                "WHERE observation_id = ?",
                (observation_id,),
            )


def test_u07_management_schema_v1_upgrades_without_fabricating_manifest(tmp_path):
    from automation.inago.maintenance.store import (
        APPLICATION_ID,
        SCHEMA_VERSION,
        MaintenanceStore,
    )

    data_root = tmp_path / "legacy-data"
    daily_database = tmp_path / "legacy-daily" / "state.sqlite"
    store = MaintenanceStore(data_root, daily_database_path=daily_database)
    store.database_path.parent.mkdir(parents=True)
    initial_sql = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "inago"
        / "maintenance"
        / "sql"
        / "001_initial.sql"
    ).read_text(encoding="utf-8")
    with sqlite3.connect(store.database_path) as connection:
        connection.executescript(initial_sql)
        connection.execute(
            "INSERT INTO schema_migration (version, applied_at, description) "
            "VALUES (1, ?, ?)",
            (BASE_TIME.isoformat(), "legacy schema"),
        )
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 1")

    assert store.initialize(applied_at=BASE_TIME) == SCHEMA_VERSION
    assert store.counts()["job_input_manifest"] == 0
    with pytest.raises(MaintenanceStoreError, match="job_input_manifest_missing"):
        store.get_job_input_manifest(job_id="legacy-job")


def test_c26_app_server_start_does_not_probe_folder_acl_schema(tmp_path, monkeypatch):
    from types import SimpleNamespace

    class InertThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    calls = []
    app_server = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=tmp_path / "session.jsonl",
        sandbox_policy={"type": "readOnly", "networkAccess": False},
    )

    def no_schema_probe(*_args, **_kwargs):
        raise AssertionError("folder ACL schema probe should not run")

    monkeypatch.setattr("automation.inago.app_server.subprocess.run", no_schema_probe)
    monkeypatch.setattr(
        "automation.inago.app_server.subprocess.Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=123, poll=lambda: None),
    )
    monkeypatch.setattr("automation.inago.app_server.threading.Thread", InertThread)
    app_server._send = lambda message: calls.append(("send", message))

    def rpc(method, params, *, deadline, cancel_event=None):
        calls.append((method, params))
        if method == "account/read":
            return {"account": {"type": "chatgpt"}}
        if method == "thread/start":
            return {"thread": {"id": "thread-c26"}}
        return {}

    app_server._call = rpc
    assert app_server.start(deadline=100.0) == "thread-c26"
    assert any(method == "thread/start" for method, _ in calls)
    events = [
        json.loads(line)
        for line in (tmp_path / "session.jsonl").read_text().splitlines()
    ]
    assert [event["event"] for event in events] == ["process_started", "root_ready"]


def test_c26_transport_log_failure_still_interrupts_and_closes(tmp_path):
    app_server = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=tmp_path / "session.jsonl",
    )
    app_server.thread_id = "thread-timeout"
    app_server.start = lambda **_kwargs: "thread-timeout"
    app_server._call = lambda *_args, **_kwargs: {"turn": {"id": "turn-timeout"}}
    app_server._receive = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AppServerError("timed_out")
    )
    attempted_log = []
    cleanup_calls = []

    def log(event, **_fields):
        attempted_log.append(event)
        if event == "job_transport_failed":
            raise AppServerError("log_write_failed")

    app_server._log = log
    app_server.interrupt = lambda: cleanup_calls.append("interrupt")
    app_server.close = lambda: cleanup_calls.append("close")

    with pytest.raises(AppServerError, match="timed_out"):
        app_server.run_turn(
            "safe test prompt",
            {"type": "object", "properties": {}},
            cwd=tmp_path,
            deadline=100.0,
        )

    assert attempted_log[-1] == "job_transport_failed"
    assert cleanup_calls == ["interrupt", "close"]


def test_c26_app_server_checks_model_and_effort_before_thread_start(tmp_path):
    app_server = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=tmp_path / "session.jsonl",
        model="gpt-6-luna",
        effort="max",
        service_tier="priority",
    )
    requested = []

    def catalog(method, params, *, deadline, cancel_event=None):
        requested.append((method, params))
        if params.get("cursor") is None:
            return {"data": [], "nextCursor": "models-page-2"}
        return {
            "data": [
                {
                    "id": "gpt-6-luna",
                    "model": "gpt-6-luna",
                    "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                }
            ],
            "nextCursor": None,
        }

    app_server._call = catalog
    with pytest.raises(AppServerError, match="effort_unavailable"):
        app_server._verify_model_capability(deadline=10.0)
    assert requested == [
        ("model/list", {"limit": 100, "includeHidden": False}),
        (
            "model/list",
            {
                "limit": 100,
                "includeHidden": False,
                "cursor": "models-page-2",
            },
        ),
    ]


def test_c26_app_server_blocks_unsupported_fast_tier_and_disabled_feature(tmp_path):
    app_server = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=tmp_path / "session.jsonl",
        model="gpt-6-luna",
        effort="max",
        service_tier="priority",
        fast_mode=True,
    )
    app_server._call = lambda *_args, **_kwargs: {
        "data": [
            {
                "id": "gpt-6-luna",
                "model": "gpt-6-luna",
                "supportedReasoningEfforts": [{"reasoningEffort": "max"}],
                "serviceTiers": [],
            }
        ]
    }
    with pytest.raises(AppServerError, match="service_tier_unavailable"):
        app_server._verify_model_capability(deadline=10.0)

    app_server._call = lambda *_args, **_kwargs: {
        "data": [{"name": "fast_mode", "enabled": False}],
        "nextCursor": None,
    }
    with pytest.raises(AppServerError, match="fast_mode_unavailable"):
        app_server._verify_fast_mode_capability(deadline=10.0)


def test_c24_app_server_passes_role_instructions_and_settings_to_thread_start(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    calls = []

    class InertThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    app_server = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=tmp_path / "session.jsonl",
        model="gpt-6.1-sol",
        effort="xhigh",
        service_tier="default",
        fast_mode=False,
        role_name="rr-inago-incident-investigator",
        role_config_sha256="a" * 64,
        developer_instructions="Follow the investigation role contract.",
        sandbox_policy={"type": "readOnly", "networkAccess": False},
    )
    monkeypatch.setattr(
        "automation.inago.app_server.subprocess.Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=123, poll=lambda: None),
    )
    monkeypatch.setattr("automation.inago.app_server.threading.Thread", InertThread)
    app_server._send = lambda message: calls.append(("send", message))

    def rpc(method, params, *, deadline, cancel_event=None):
        calls.append((method, params))
        if method == "account/read":
            return {"account": {"type": "chatgpt"}}
        if method == "model/list":
            return {
                "data": [
                    {
                        "model": "gpt-6.1-sol",
                        "supportedReasoningEfforts": [{"reasoningEffort": "xhigh"}],
                    }
                ]
            }
        if method == "thread/start":
            return {"thread": {"id": "thread-c24"}}
        return {}

    app_server._call = rpc
    assert app_server.start(deadline=100.0) == "thread-c24"
    thread_call = next(call for call in calls if call[0] == "thread/start")
    assert thread_call[1]["model"] == "gpt-6.1-sol"
    assert thread_call[1]["serviceTier"] == "default"
    assert thread_call[1]["developerInstructions"] == (
        "Follow the investigation role contract."
    )
    assert thread_call[1]["sandbox"] == "read-only"


def test_c24_generic_app_server_root_log_preserves_daily_fields(tmp_path, monkeypatch):
    from types import SimpleNamespace

    records = []

    class InertThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    app_server = AppServerSession(
        command=("codex", "app-server"),
        cwd=tmp_path,
        log_path=tmp_path / "daily-session.jsonl",
    )
    app_server._log = lambda event, **fields: records.append((event, fields))
    monkeypatch.setattr(
        "automation.inago.app_server.subprocess.Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=321, poll=lambda: None),
    )
    monkeypatch.setattr("automation.inago.app_server.threading.Thread", InertThread)

    def rpc(method, _params, *, deadline, cancel_event=None):
        if method == "account/read":
            return {"account": {"type": "chatgpt"}}
        if method == "thread/start":
            return {"thread": {"id": "thread-daily"}}
        return {}

    app_server._call = rpc
    assert app_server.start(deadline=100.0) == "thread-daily"
    root_ready = next(fields for event, fields in records if event == "root_ready")
    assert root_ready == {"thread_id": "thread-daily", "process_pid": 321}


def test_c28_contract(tmp_path):
    store = make_store(tmp_path)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4201,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=10),
        ttl_seconds=30,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="investigation",
        sequence=1,
        lease=lease,
        started_at=(BASE_TIME + timedelta(seconds=11)).isoformat(),
    )

    with pytest.raises(MaintenanceStoreError, match="step_attempt_not_complete"):
        store.record_step_review(
            attempt_id=attempt.attempt_id,
            reviewer_id="independent-reviewer",
            reviewed_version="result-v1",
            status="approved",
            reviewed_at=(BASE_TIME + timedelta(seconds=11)).isoformat(),
        )

    handoff_root = tmp_path / "handoffs"
    output_path = handoff_root / "jobs" / job.job_id / "investigation.json"
    output_path.parent.mkdir(parents=True)
    output_bytes = json.dumps({"result": "safe", "version": "result-v1"}).encode(
        "utf-8"
    )
    output_path.write_bytes(output_bytes)
    output_hash = hashlib.sha256(output_bytes).hexdigest()
    store.complete_step_attempt(
        attempt_id=attempt.attempt_id,
        lease=lease,
        result_version="result-v1",
        output_path="jobs/job-release-1/investigation.json",
        output_sha256=output_hash,
        completed_at=(BASE_TIME + timedelta(seconds=12)).isoformat(),
    )
    with pytest.raises(MaintenanceStoreError, match="step_review_version_mismatch"):
        store.record_step_review(
            attempt_id=attempt.attempt_id,
            reviewer_id="independent-reviewer",
            reviewed_version="old-result-v0",
            status="approved",
            reviewed_at=(BASE_TIME + timedelta(seconds=12)).isoformat(),
        )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO review_result "
            "(review_id, scope_type, scope_id, reviewer_id, reviewed_version, status, reviewed_at) "
            "VALUES (?, 'step_attempt', ?, ?, ?, 'approved', ?)",
            (
                "stale-review",
                attempt.attempt_id,
                "independent-reviewer",
                "old-result-v0",
                (BASE_TIME + timedelta(seconds=13)).isoformat(),
            ),
        )

    payload = {
        "schema_version": "maintenance-handoff-v1",
        "incident_id": incident_id,
        "job_id": job.job_id,
        "attempt_id": attempt.attempt_id,
        "source_run_id": None,
        "result_version": "result-v1",
        "reviewed_version": "result-v1",
        "output": {
            "path": "jobs/job-release-1/investigation.json",
            "sha256": output_hash,
        },
    }
    assert payload["source_run_id"] is None

    for field, value in (
        ("incident_id", "incident-other"),
        ("job_id", "job-other"),
        ("attempt_id", "attempt-other"),
        ("result_version", "other-version"),
        ("reviewed_version", "old-result-v0"),
    ):
        with pytest.raises(HandoffContractError):
            store.accept_handoff(
                {**payload, field: value},
                handoff_root=handoff_root,
                lease=lease,
                accepted_at=(BASE_TIME + timedelta(seconds=14)).isoformat(),
            )

    output_path.unlink()
    with pytest.raises(HandoffContractError):
        store.accept_handoff(
            payload,
            handoff_root=handoff_root,
            lease=lease,
            accepted_at=(BASE_TIME + timedelta(seconds=15)).isoformat(),
        )
    output_path.write_bytes(output_bytes + b"tampered")
    with pytest.raises(HandoffContractError):
        store.accept_handoff(
            payload,
            handoff_root=handoff_root,
            lease=lease,
            accepted_at=(BASE_TIME + timedelta(seconds=16)).isoformat(),
        )
    output_path.write_bytes(output_bytes)

    store.record_step_review(
        attempt_id=attempt.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="result-v1",
        status="changes_requested",
        reviewed_at=(BASE_TIME + timedelta(seconds=18)).isoformat(),
    )
    with pytest.raises(HandoffContractError):
        store.accept_handoff(
            payload,
            handoff_root=handoff_root,
            lease=lease,
            accepted_at=(BASE_TIME + timedelta(seconds=19)).isoformat(),
        )
    store.record_step_review(
        attempt_id=attempt.attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="result-v1",
        status="approved",
        reviewed_at=(BASE_TIME + timedelta(seconds=20)).isoformat(),
    )
    unrelated_lease = store.acquire_lease(
        lease_key="other-maintenance-worker",
        owner_id="worker-b",
        owner_pid=4202,
        owner_start_time=(BASE_TIME + timedelta(seconds=20)).isoformat(),
        now=BASE_TIME + timedelta(seconds=20),
        ttl_seconds=30,
    )
    with pytest.raises(HandoffContractError, match="handoff_job_lease_mismatch"):
        store.accept_handoff(
            payload,
            handoff_root=handoff_root,
            lease=unrelated_lease,
            accepted_at=(BASE_TIME + timedelta(seconds=20)).isoformat(),
        )
    accepted = store.accept_handoff(
        payload,
        handoff_root=handoff_root,
        lease=lease,
        accepted_at=(BASE_TIME + timedelta(seconds=21)).isoformat(),
    )
    assert accepted.accepted
    assert accepted.source_run_id is None
    assert store.get_step_attempt(attempt.attempt_id).status == "handoff_accepted"
    with pytest.raises(MaintenanceStoreError, match="step_handoff_already_accepted"):
        store.record_step_review(
            attempt_id=attempt.attempt_id,
            reviewer_id="independent-reviewer",
            reviewed_version="result-v1",
            status="changes_requested",
            reviewed_at=(BASE_TIME + timedelta(seconds=22)).isoformat(),
        )
