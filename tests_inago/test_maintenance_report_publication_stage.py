from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

import pytest

from automation.inago.maintenance.store import (
    LeaseToken,
    MaintenanceJobRecord,
    StepAttemptRecord,
)
from automation.inago.maintenance.controller import MaintenanceController
import automation.inago.maintenance.production_workflow as production_workflow
from automation.inago.maintenance.production_workflow import (
    run_daily_report_publication_stage,
)
from automation.inago.maintenance.workflow_provider import (
    MaintenanceWorkflowContext,
    WorkflowResumeState,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("report_status", "expected_status", "expected_job_status", "expected_reason"),
    [
        ("complete", "complete", "complete", None),
        ("pending", "waiting", "blocked", "report_status_pending"),
        ("blocked", "blocked", "blocked", "report_status_blocked"),
    ],
)
def test_publication_stage_recovers_after_push_before_artifact_save(
    tmp_path: Path,
    monkeypatch,
    report_status: str,
    expected_status: str,
    expected_job_status: str,
    expected_reason: str | None,
):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    attempts_root = runtime_root / "attempts"
    job_id = "job-u21-report-publication"
    attempt_id = "attempt-u21-report-publication"
    attempt_root = attempts_root / job_id / attempt_id
    attempt_root.mkdir(parents=True)

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repository)],
        check=True,
        capture_output=True,
    )
    _git(repository, "config", "user.name", "U21 Publication Fixture")
    _git(repository, "config", "user.email", "u21-publication@example.invalid")
    (repository / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "code.py")
    _git(repository, "commit", "-m", "synthetic deployed code")
    code_commit = _git(repository, "rev-parse", "HEAD")

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "--set-upstream", "origin", "main")

    identity = {
        "incident_id": "incident-u21-publication",
        "source_launch_id": "launch-u21-publication",
        "source_run_id": None,
        "target_date": "2026-10-19",
        "job_id": job_id,
    }
    html_path = f"{job_id}/{attempt_id}/report.html"
    deployment_path = f"{job_id}/{attempt_id}/deployment-result.json"
    html = b"<!doctype html><html><body>approved report</body></html>\n"
    html_sha256 = hashlib.sha256(html).hexdigest()
    review = {
        "schema_version": "maintenance-report-review-v1",
        "reviewer_id": "rr-inago-incident-report-reviewer",
        "reviewed_version": "report-u21-v1",
        "reviewed_sha256": html_sha256,
        "status": "approved",
        "reviewed_at": "2026-10-19T03:00:00+09:00",
        "findings": [],
    }
    result_document = {
        "schema_version": "maintenance-report-result-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": "report-u21-v1",
        "created_at_jst": "2026-10-19T03:00:00+09:00",
        "report_status": report_status,
        "stage_status": "reviewed",
        "review": review,
        "output": {"path": html_path, "sha256": html_sha256},
        "source_versions": {
            "investigation": "investigation-u21-v1",
            "plan": "plan-u21-v1",
            "implementation": code_commit,
            "tests": "tests-u21-v1",
            "deployment_status": "deployed",
            "next_day_status": "pending",
        },
        "next_action": "publish",
    }
    result_bytes = (
        json.dumps(
            result_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    (attempt_root / "report.html").write_bytes(html)
    (attempt_root / "result.json").write_bytes(result_bytes)
    deployment_document = {
        "schema_version": "maintenance-deployment-result-v1",
        "deployment_id": "deployment-u21-publication",
        "release_id": "release-u21-publication",
        "status": "deployed",
        "reason_code": None,
        "source_commit": code_commit,
        "base_commit": code_commit,
        "tested_commit": code_commit,
        "pushed_commit": code_commit,
        "active_commit": code_commit,
        "remote_name": "origin",
        "target_branch": "main",
    }
    deployment_bytes = (
        json.dumps(
            deployment_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    (attempt_root / "deployment-result.json").write_bytes(deployment_bytes)
    deployment_sha256 = hashlib.sha256(deployment_bytes).hexdigest()

    job = MaintenanceJobRecord(
        job_id=job_id,
        incident_id=identity["incident_id"],
        release_id="release-u21-publication",
        source_run_id=None,
        result_version="release-u21-v1",
        resume_count=0,
        status="running",
        created_at="2026-10-19T03:00:00+09:00",
        updated_at="2026-10-19T03:00:00+09:00",
        finished_at=None,
        terminal_reason_code=None,
    )
    attempt = StepAttemptRecord(
        attempt_id=attempt_id,
        job_id=job_id,
        step_key="maintenance_report",
        sequence=4,
        attempt_no=1,
        source_run_id=None,
        status="complete",
        result_version="report-u21-v1",
        output_path=f"{job_id}/{attempt_id}/result.json",
        output_sha256=hashlib.sha256(result_bytes).hexdigest(),
        started_at="2026-10-19T03:00:00+09:00",
        finished_at="2026-10-19T03:00:00+09:00",
    )

    class Manifest:
        def identity(self):
            return identity

    class Store:
        database_path = runtime_root / "maintenance.sqlite"

        def get_job_input_manifest(self, *, job_id: str):
            assert job_id == identity["job_id"]
            return Manifest()

        def get_step_attempt_review(self, *, attempt_id: str):
            assert attempt_id == attempt_id_expected
            return {
                "status": "approved",
                "reviewed_version": "report-u21-v1",
                "reviewer_id": "rr-inago-incident-report-reviewer",
            }

        def read_attempt_artifact(
            self, *, attempts_root: Path, relative_path: str, expected_sha256: str
        ) -> bytes:
            raw = attempts_root.joinpath(*relative_path.split("/")).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == expected_sha256
            return raw

    attempt_id_expected = attempt_id
    store = Store()
    source_snapshot = {
        "implementation": {"result_commit": code_commit},
        "tests": {"status": "passed"},
        "deployment": {
            "status": "deployed",
            "tested_commit": code_commit,
            "pushed_commit": code_commit,
            "active_commit": code_commit,
            "evidence_href": deployment_path,
        },
    }

    class Guard:
        held = False

        def acquire(self):
            if self.held:
                return False
            self.held = True
            return True

        def release(self):
            self.held = False

    class Controller:
        finalized = []

        def finalize_job(self, **kwargs):
            self.finalized.append(kwargs)

    guard = Guard()
    controller = Controller()
    workflow = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=identity["source_launch_id"],
            source_run_id=identity["source_run_id"],
            target_date=identity["target_date"],
        ),
        check=SimpleNamespace(check_id="check-u21-publication"),
        queue=SimpleNamespace(queue_id="queue-u21-publication"),
        lease=LeaseToken("maintenance:u21-publication", "worker-u21", 1),
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=job, attempts=(attempt,)),
        state={
            "job": job,
            "report_attempt": attempt,
            "report_result": result_document,
            "report_review": review,
            "report_source_snapshot": source_snapshot,
            "report_source_artifacts": {
                "deployment_result": {
                    "path": deployment_path,
                    "sha256": deployment_sha256,
                }
            },
            "report_base_commit": code_commit,
            "report_html_path": html_path,
            "report_html_sha256": html_sha256,
            "report_input_refs": ["evidence/u21/input.json"],
            "report_evidence_refs": ["evidence/u21/input.json"],
            "precheck_identity": identity,
        },
    )

    original_save = production_workflow.save_attempt_artifact
    save_failed = False

    def fail_first_publication_save(*args, **kwargs):
        nonlocal save_failed
        if kwargs.get("filename") == "report-publication.json" and not save_failed:
            save_failed = True
            raise OSError("synthetic crash after remote push")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(
        production_workflow, "save_attempt_artifact", fail_first_publication_save
    )

    workflow.state["precheck_identity"] = {**identity, "job_id": "job-other"}
    rejected = run_daily_report_publication_stage(
        workflow,
        controller,
        store,
        repository=repository,
        remote_name="origin",
        target_branch="main",
        exclusion_guard=guard,
    )
    assert rejected.status == "blocked"
    assert rejected.reason_code == "report_publication_identity_mismatch"
    assert not guard.held
    assert _git(remote, "rev-parse", "refs/heads/main") == code_commit
    workflow.state["precheck_identity"] = identity

    target_mismatch = run_daily_report_publication_stage(
        workflow,
        controller,
        store,
        repository=repository,
        remote_name="origin",
        target_branch="other",
        exclusion_guard=guard,
    )
    assert target_mismatch.status == "blocked"
    assert target_mismatch.reason_code == "report_publication_target_mismatch"
    assert _git(remote, "rev-parse", "refs/heads/main") == code_commit

    first = run_daily_report_publication_stage(
        workflow,
        controller,
        store,
        repository=repository,
        remote_name="origin",
        target_branch="main",
        exclusion_guard=guard,
    )
    assert first.status == "blocked"
    assert first.reason_code == "report_publication_failed"
    assert not guard.held
    assert not (attempt_root / "report-publication.json").exists()

    remote_commit = _git(remote, "rev-parse", "refs/heads/main")
    monkeypatch.setattr(production_workflow, "save_attempt_artifact", original_save)
    resumed = run_daily_report_publication_stage(
        workflow,
        controller,
        store,
        repository=repository,
        remote_name="origin",
        target_branch="main",
        exclusion_guard=guard,
    )
    assert resumed.status == expected_status
    assert not guard.held
    publication_record = json.loads(
        (attempt_root / "report-publication.json").read_text(encoding="utf-8")
    )
    assert publication_record["publication_commit"] == remote_commit
    assert publication_record["report_sha256"] == html_sha256
    assert controller.finalized[-1]["final_status"] == expected_job_status
    assert controller.finalized[-1]["reason_code"] == expected_reason
    assert (
        controller.finalized[-1]["incident_status"]
        == {
            "complete": "resolved",
            "pending": "in_progress",
            "blocked": "blocked",
        }[report_status]
    )
    assert (
        _git(
            repository,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            remote_commit,
        )
        == f"reports/incidents/{identity['target_date']}/job-{hashlib.sha256(job_id.encode()).hexdigest()[:20]}.html"
    )


def test_report_stage_falls_back_and_invokes_author_runner(tmp_path: Path, monkeypatch):
    source_launch_id = "launch-report-stage-fallback"
    target_date = "2026-10-17"
    store = make_store(tmp_path)
    incident_id = create_incident(store, launch_id=source_launch_id)
    job_start = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-report-stage-fallback",
        base_commit=_git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD"),
        result_commit=_git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD"),
    )
    assert job_start.job_id == "job-release-report-stage-fallback"
    store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=job_start.job_id,
        created_at=BASE_TIME,
    )
    job = store.list_workflow_jobs_for_launch(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=job_start.job_id,
    )[0]
    lease = store.acquire_lease(
        lease_key=f"maintenance:{job.job_id}",
        owner_id="report-stage-test",
        owner_pid=1,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    controller = MaintenanceController(store)
    worktrees_root = store.database_path.parent / "worktrees"
    worktrees_root.mkdir(parents=True)
    identity = {
        "incident_id": incident_id,
        "source_launch_id": source_launch_id,
        "source_run_id": None,
        "target_date": target_date,
        "job_id": job.job_id,
    }
    workflow = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date=target_date,
        ),
        check=SimpleNamespace(check_id="check-report-stage-fallback"),
        queue=SimpleNamespace(queue_id="queue-report-stage-fallback"),
        lease=lease,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=job, attempts=()),
        state={
            "job": job,
            "precheck_identity": {
                **identity,
                "input_refs": [
                    f"daily-launch/{source_launch_id}",
                    f"daily-result-observation/{source_launch_id}",
                ],
                "evidence_refs": [f"daily-evidence/{source_launch_id}/index.json"],
            },
        },
    )
    author_calls: list[str] = []

    class AuthorSession:
        def run_turn(self, *_args, **_kwargs):
            author_calls.append("run_turn")
            raise production_workflow.MaintenanceAIBlockedError(
                "synthetic_author_transport_unavailable"
            )

        def close(self):
            author_calls.append("close")

    class Adapter:
        def __init__(self, *_args, **_kwargs):
            pass

        def plan_session(self, **kwargs):
            return SimpleNamespace(
                role_config_sha256="a" * 64,
                cwd=kwargs["cwd"],
            )

        def create_session(self, _plan):
            return AuthorSession()

    monkeypatch.setattr(production_workflow, "MaintenanceAIAdapter", Adapter)
    result = production_workflow.run_daily_report_stage(
        workflow, controller, store, timeout_seconds=5
    )

    assert workflow.state["report_preparation_observation"] == (
        "report_source_attempt_unavailable"
    )
    assert workflow.state["report_source_snapshot"]["identity"] == identity
    assert author_calls == ["run_turn", "close"]
    assert result.status == "blocked"
    assert result.reason_code == "synthetic_author_transport_unavailable"
