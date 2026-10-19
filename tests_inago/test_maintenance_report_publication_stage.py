from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

import pytest

from automation.inago.maintenance.store import (
    LeaseToken,
    MaintenanceStoreError,
    MaintenanceJobRecord,
    StepAttemptRecord,
)
from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.maintenance.store import MaintenanceStore
from automation.inago.maintenance.paths import resolve_maintenance_failure_journal_path
import automation.inago.maintenance.production_workflow as production_workflow
from automation.inago.maintenance.production_workflow import (
    run_daily_report_publication_stage,
)
from automation.inago.maintenance.workflow_provider import (
    MaintenanceWorkflowContext,
    WorkflowResumeState,
    WorkflowStageResult,
    WORKFLOW_STAGE_ORDER,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)
from automation.inago.maintenance.workflow_reconciliation import (
    reconcile_workflow_terminal,
)
import automation.inago.maintenance.workflow_reconciliation as workflow_reconciliation_module


def _git(repository: Path, *arguments: str) -> str:
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "safe.directory"
    env["GIT_CONFIG_VALUE_0"] = str(repository.resolve())
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return result.stdout.strip()


class _FC08Guard:
    def __init__(self):
        self.held = False

    def acquire(self):
        if self.held:
            return False
        self.held = True
        return True

    def release(self):
        self.held = False


def _fc08_publish_then_crash(
    data_root: str,
    daily_database_path: str,
    repository: str,
    remote_name: str,
    target_branch: str,
    identity: dict[str, object],
    job: MaintenanceJobRecord,
    report_attempt: StepAttemptRecord,
    lease: LeaseToken,
    report_base_commit: str,
    exit_code: int,
):
    """Publish in a child and terminate before terminal reconciliation starts."""

    data_root_path = Path(data_root)
    store = MaintenanceStore(
        data_root_path,
        daily_database_path=Path(daily_database_path),
    )
    store.initialize()
    attempts_root = store.database_path.parent / "attempts"
    result_bytes = store.read_attempt_artifact(
        attempts_root=attempts_root,
        relative_path=report_attempt.output_path,
        expected_sha256=report_attempt.output_sha256,
    )
    result = json.loads(result_bytes)
    html_path = result["output"]["path"]
    html_sha256 = result["output"]["sha256"]
    html = store.read_attempt_artifact(
        attempts_root=attempts_root,
        relative_path=html_path,
        expected_sha256=html_sha256,
    )
    review = result["review"]
    source_snapshot = {
        "implementation": {"result_commit": report_base_commit},
        "tests": {"status": "passed"},
        "deployment": {
            "status": "not_performed",
            "tested_commit": report_base_commit,
            "pushed_commit": report_base_commit,
            "active_commit": report_base_commit,
            "evidence_href": "deployment-not-performed.json",
        },
    }
    workflow = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=identity["source_launch_id"],
            source_run_id=identity["source_run_id"],
            target_date=identity["target_date"],
        ),
        check=SimpleNamespace(check_id="check-fc08-f1"),
        queue=SimpleNamespace(queue_id="queue-fc08-f1"),
        lease=lease,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=job, attempts=(report_attempt,)),
        state={
            "job": job,
            "report_attempt": report_attempt,
            "report_result": result,
            "report_review": review,
            "report_source_snapshot": source_snapshot,
            "report_source_artifacts": {},
            "report_base_commit": report_base_commit,
            "report_html_path": html_path,
            "report_html_sha256": html_sha256,
            "report_input_refs": ["evidence/fc08/synthetic-input.json"],
            "report_evidence_refs": ["evidence/fc08/synthetic-input.json"],
            "precheck_identity": identity,
            "invocation_id": "fc08-f1-same-job-invocation",
        },
    )
    outcome = run_daily_report_publication_stage(
        workflow,
        MaintenanceController(store),
        store,
        repository=Path(repository),
        remote_name=remote_name,
        target_branch=target_branch,
        exclusion_guard=_FC08Guard(),
    )
    if outcome.status != "complete":
        (data_root_path / "fc08-publication-child-outcome.json").write_text(
            json.dumps(
                {"status": outcome.status, "reason_code": outcome.reason_code},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(48)
    # Simulate abrupt process loss after durable publication sidecar and before
    # the caller can invoke reconcile_workflow_terminal.
    os._exit(exit_code)


def _fc08_seed_terminal_fixture(tmp_path: Path, monkeypatch):
    job_id = "job-historical-9e7f0c175cc4c874c955a5dab29c4427"
    launch_id = "historical-9e7f0c175cc4c874c955a5dab29c4427"
    incident_id = "352492da-ac58-4680-b1fd-fe5f24000229"
    target_date = "2026-10-17"
    identity = {
        "job_id": job_id,
        "incident_id": incident_id,
        "source_launch_id": launch_id,
        "source_run_id": None,
        "target_date": target_date,
    }
    store = make_store(tmp_path)
    connection = store._connect()
    try:
        connection.execute(
            "INSERT INTO incident "
            "(incident_id, dedupe_key, component, event_code, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (
                incident_id,
                "fc08:synthetic:terminal-readback",
                "daily_pipeline",
                "daily_error",
                BASE_TIME.isoformat(),
                BASE_TIME.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    daily = store.record_daily_check(
        source_launch_id=launch_id,
        source_run_id=None,
        target_date=target_date,
        check_status="abnormal",
        observed_at=BASE_TIME.isoformat(),
        reason_code="daily_error",
        incident={
            "dedupe_key": "fc08:synthetic:terminal-readback",
            "component": "daily_pipeline",
            "event_code": "daily_error",
        },
    )
    assert daily.incident_id == incident_id
    release_id = job_id.removeprefix("job-")
    job_start = create_ready_release(
        store,
        incident_id=incident_id,
        release_id=release_id,
        base_commit="a" * 40,
        result_commit="b" * 40,
        result_version="fc08-terminal-fixture-v1",
    )
    assert job_start.started
    assert job_start.job_id == job_id
    store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=job_id,
        created_at=BASE_TIME,
    )
    lease = store.acquire_lease(
        lease_key=f"maintenance:{job_id}",
        owner_id="fc08-publication-crash-child",
        owner_pid=os.getpid(),
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )

    repository = tmp_path / "fc08-publication-repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "FC08 Synthetic Publisher")
    _git(repository, "config", "user.email", "fc08-synthetic@example.invalid")
    (repository / "source.txt").write_text("synthetic deployed version\n", encoding="utf-8")
    _git(repository, "add", "source.txt")
    _git(repository, "commit", "-m", "synthetic deployed source")
    base_commit = _git(repository, "rev-parse", "HEAD")
    remote = tmp_path / "fc08-public-report.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "--set-upstream", "origin", "main")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(repository.resolve()))

    controller = MaintenanceController(store)
    attempt_records = {}
    attempts_root = store.database_path.parent / "attempts"
    report_html = b"<!doctype html><html lang=\"ja\"><body>synthetic final report</body></html>\n"
    report_html_sha = hashlib.sha256(report_html).hexdigest()
    report_path = f"{job_id}/pending/report.html"
    report_version = "fc08-report-v1"
    report_review = {
        "schema_version": "maintenance-report-review-v1",
        "reviewer_id": "rr-inago-incident-report-reviewer",
        "reviewed_version": report_version,
        "reviewed_sha256": report_html_sha,
        "status": "approved",
        "reviewed_at": "2026-10-20T10:00:00+09:00",
        "findings": [],
    }
    for sequence, step_key in (
        (1, "maintenance_investigation"),
        (2, "maintenance_planning"),
        (3, "maintenance_repair"),
        (4, "maintenance_report"),
    ):
        predecessor = attempt_records.get(2) if sequence == 3 else None
        attempt = store.begin_step_attempt(
            job_id=job_id,
            step_key=step_key,
            sequence=sequence,
            lease=lease,
            started_at=BASE_TIME,
            repair_predecessor_attempt_id=(
                predecessor.attempt_id if predecessor is not None else None
            ),
            repair_predecessor_result_version=(
                predecessor.result_version if predecessor is not None else None
            ),
            repair_predecessor_output_sha256=(
                predecessor.output_sha256 if predecessor is not None else None
            ),
        )
        attempt_records[sequence] = attempt
        attempt_root = attempts_root / job_id / attempt.attempt_id
        attempt_root.mkdir(parents=True, exist_ok=True)
        if sequence == 4:
            actual_report_path = f"{job_id}/{attempt.attempt_id}/report.html"
            report_result = {
                "schema_version": "maintenance-report-result-v1",
                "identity": identity,
                "attempt_id": attempt.attempt_id,
                "result_version": report_version,
                "created_at_jst": "2026-10-20T10:00:00+09:00",
                "report_status": "complete",
                "stage_status": "reviewed",
                "review": report_review,
                "output": {"path": actual_report_path, "sha256": report_html_sha},
                "source_versions": {
                    "investigation": "fc08-investigation-v1",
                    "plan": "fc08-plan-v1",
                    "implementation": base_commit,
                    "tests": "fc08-tests-v1",
                    "deployment_status": "not_attempted",
                    "next_day_status": "pending",
                },
                "next_action": "publish",
            }
            (attempt_root / "report.html").write_bytes(report_html)
            result_bytes = (
                json.dumps(report_result, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
        else:
            result_bytes = json.dumps(
                {"synthetic_stage": step_key, "identity": identity},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8") + b"\n"
        (attempt_root / "result.json").write_bytes(result_bytes)
        result_path = f"{job_id}/{attempt.attempt_id}/result.json"
        result_sha = hashlib.sha256(result_bytes).hexdigest()
        store.complete_step_attempt(
            attempt_id=attempt.attempt_id,
            lease=lease,
            result_version=report_version if sequence == 4 else f"fc08-stage-{sequence}-v1",
            completed_at=BASE_TIME,
            output_path=result_path,
            output_sha256=result_sha,
        )
        attempt_records[sequence] = store.get_step_attempt(attempt.attempt_id)
        if sequence == 3:
            deployment_not_performed = {
                "schema_version": "maintenance-deployment-not-performed-v1",
                "identity": identity,
                "status": "not_performed",
                "reason": "synthetic test does not deploy",
            }
            (attempt_root / "deployment-not-performed.json").write_text(
                json.dumps(deployment_not_performed, ensure_ascii=False), encoding="utf-8"
            )
        if sequence == 4:
            store.record_step_review(
                attempt_id=attempt.attempt_id,
                reviewer_id="rr-inago-incident-report-reviewer",
                reviewed_version=report_version,
                status="approved",
                reviewed_at=BASE_TIME,
            )

    invocation_id = "fc08-f1-same-job-invocation"
    handoff_root = (
        store.database_path.parent.parent
        / "workflow-handoffs"
        / job_id
        / invocation_id
    )
    handoff_root.mkdir(parents=True)
    for stage in WORKFLOW_STAGE_ORDER:
        handoff = {
            "schema_version": "maintenance-stage-handoff-v1",
            "invocation_id": invocation_id,
            "identity": identity,
            "stage": stage,
            "stage_progress": "handed_off",
            "content_result": {
                "summary": f"Synthetic content for {stage}.",
                "next_actions": ["Continue same-job reconciliation."],
            },
        }
        (handoff_root / f"{stage}.json").write_text(
            json.dumps(handoff, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        store.record_workflow_stage_observation(
            invocation_id=invocation_id,
            job_id=job_id,
            source_launch_id=launch_id,
            source_run_id=None,
            target_date=target_date,
            stage_name=stage,
            status="complete",
            reason_code=None,
            observed_at=BASE_TIME,
        )
    job = store.list_workflow_jobs_for_launch(
        source_launch_id=launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=job_id,
    )[0]
    report_attempt = attempt_records[4]
    return {
        "store": store,
        "lease": lease,
        "job": job,
        "report_attempt": report_attempt,
        "identity": identity,
        "repository": repository,
        "remote": remote,
        "base_commit": base_commit,
        "report_review": report_review,
        "report_path": f"reports/incidents/{target_date}/job-{hashlib.sha256(job_id.encode()).hexdigest()[:20]}.html",
        "daily_database_path": tmp_path / "daily-runtime" / "state.sqlite",
        "invocation_id": invocation_id,
    }


def _fc08_crash_after_publish(fixture: dict[str, object], exit_code: int = 47):
    child = multiprocessing.get_context("spawn").Process(
        target=_fc08_publish_then_crash,
        args=(
            str(fixture["store"].database_path.parent.parent),
            str(fixture["daily_database_path"]),
            str(fixture["repository"]),
            "origin",
            "main",
            fixture["identity"],
            fixture["job"],
            fixture["report_attempt"],
            fixture["lease"],
            fixture["base_commit"],
            exit_code,
        ),
    )
    child.start()
    child.join(timeout=30)
    if child.is_alive():
        child.terminate()
        child.join(timeout=5)
        pytest.fail("FC08 synthetic publication child did not exit")
    assert child.exitcode == exit_code


def test_fc08_publication_crash_resumes_same_job_and_only_reconciler_finalizes(
    tmp_path: Path, monkeypatch
):
    fixture = _fc08_seed_terminal_fixture(tmp_path, monkeypatch)
    original_store = fixture["store"]
    _fc08_crash_after_publish(fixture)

    job_before = original_store.list_workflow_jobs_for_launch(
        source_launch_id=fixture["identity"]["source_launch_id"],
        source_run_id=None,
        target_date=fixture["identity"]["target_date"],
        job_id=fixture["identity"]["job_id"],
    )[0]
    assert job_before.status in {"queued", "running"}
    assert job_before.finished_at is None
    incident_row = original_store._connect().execute(
        "SELECT status FROM incident WHERE incident_id = ?",
        (fixture["identity"]["incident_id"],),
    ).fetchone()
    assert incident_row["status"] != "resolved"
    report_attempt = fixture["report_attempt"]
    publication_path = (
        original_store.database_path.parent
        / "attempts"
        / fixture["identity"]["job_id"]
        / report_attempt.attempt_id
        / "report-publication.json"
    )
    assert publication_path.is_file()
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    assert publication["identity"] == fixture["identity"]
    assert publication["attempt_id"] == report_attempt.attempt_id
    assert _git(fixture["remote"], "rev-parse", "refs/heads/main") == publication[
        "publication_commit"
    ]
    public_bytes = subprocess.run(
        [
            "git",
            "-C",
            str(fixture["repository"]),
            "show",
            f"refs/remotes/origin/main:{publication['report_path']}",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert hashlib.sha256(public_bytes).hexdigest() == publication["report_sha256"]

    reopened = MaintenanceStore(
        original_store.database_path.parent.parent,
        daily_database_path=fixture["daily_database_path"],
    )
    reopened.initialize()
    resumed_lease = reopened.acquire_lease(
        lease_key=f"maintenance:{fixture['identity']['job_id']}",
        owner_id="fc08-same-job-reconciler",
        owner_pid=os.getpid(),
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    calls = []
    original_finalize = reopened.finalize_job

    def record_finalizer(**kwargs):
        calls.append(dict(kwargs))
        return original_finalize(**kwargs)

    reopened.finalize_job = record_finalizer
    monkeypatch.setattr(
        workflow_reconciliation_module,
        "__file__",
        str(fixture["repository"] / "automation/inago/maintenance/workflow_reconciliation.py"),
    )
    terminal = reconcile_workflow_terminal(
        reopened,
        job_id=fixture["identity"]["job_id"],
        lease=resumed_lease,
        expected_identity=fixture["identity"],
    )
    assert terminal["workflow_completion"] == "complete", json.dumps(
        {
            "reason_code": terminal["reason_code"],
            "terminal_verification": terminal["terminal_verification"],
            "missing_stages": terminal["missing_stages"],
            "missing_stage_handoffs": terminal["missing_stage_handoffs"],
            "missing_attempt_outputs": terminal["missing_attempt_outputs"],
            "report_publication_readback_reason": terminal[
                "report_publication_readback_reason"
            ],
        },
        sort_keys=True,
    )
    assert all(terminal["terminal_verification"].values())
    assert len(terminal["stage_observations"]) == 8
    assert len(terminal["stage_handoff_refs"]) == 8
    assert {item["step_key"] for item in terminal["attempt_output_refs"]} == {
        "maintenance_investigation",
        "maintenance_planning",
        "maintenance_repair",
    }
    assert terminal["deployment_output_ref"]
    assert terminal["report_publication_readback"]["public_html_sha256"] == publication[
        "report_sha256"
    ]
    assert len(calls) == 1
    assert calls[0]["final_status"] == "complete"
    assert calls[0]["incident_status"] == "in_progress"
    final_job = reopened.list_workflow_jobs_for_launch(
        source_launch_id=fixture["identity"]["source_launch_id"],
        source_run_id=None,
        target_date=fixture["identity"]["target_date"],
        job_id=fixture["identity"]["job_id"],
    )[0]
    assert final_job.status == "complete"
    incident_row = reopened._connect().execute(
        "SELECT status FROM incident WHERE incident_id = ?",
        (fixture["identity"]["incident_id"],),
    ).fetchone()
    assert incident_row["status"] == "in_progress"


def test_fc10_terminal_uses_failed_stage_handoffs_and_keeps_public_report_required(
    tmp_path: Path, monkeypatch
):
    fixture = _fc08_seed_terminal_fixture(tmp_path, monkeypatch)
    _fc08_crash_after_publish(fixture, exit_code=49)
    store = fixture["store"]
    attempts_root = store.database_path.parent / "attempts"
    connection = store._connect()
    try:
        for attempt in store.list_step_attempts(
            job_id=fixture["identity"]["job_id"]
        ):
            if attempt.sequence not in {2, 3}:
                continue
            connection.execute(
                "UPDATE step_attempt SET status = 'failed', result_version = NULL, "
                "finished_at = ?, output_path = NULL, output_sha256 = NULL "
                "WHERE attempt_id = ?",
                (BASE_TIME.isoformat(), attempt.attempt_id),
            )
        connection.commit()
    finally:
        connection.close()

    repair_attempt = next(
        attempt
        for attempt in store.list_step_attempts(job_id=fixture["identity"]["job_id"])
        if attempt.sequence == 3
    )
    investigation_attempt = next(
        attempt
        for attempt in store.list_step_attempts(job_id=fixture["identity"]["job_id"])
        if attempt.sequence == 1
    )
    (
        attempts_root
        / investigation_attempt.job_id
        / investigation_attempt.attempt_id
        / "result.json"
    ).unlink()
    (attempts_root / repair_attempt.job_id / repair_attempt.attempt_id / "deployment-not-performed.json").unlink()
    # This satisfies the former shallow five-string check but is not a valid,
    # store-bound deployment result and must not stand as terminal evidence.
    (attempts_root / repair_attempt.job_id / repair_attempt.attempt_id / "deployment-result.json").write_text(
        json.dumps(
            {
                "deployment_id": "synthetic-deployment",
                "release_id": "synthetic-release",
                "status": "garbage",
                "source_commit": "synthetic-source",
                "tested_commit": "synthetic-tested",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    handoff_root = (
        store.database_path.parent.parent
        / "workflow-handoffs"
        / fixture["identity"]["job_id"]
        / fixture["invocation_id"]
    )
    for stage in ("investigation", "planning", "repair", "deployment"):
        handoff_path = handoff_root / f"{stage}.json"
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff["operation_result"] = "not_performed" if stage == "deployment" else "failed"
        handoff["content_result"] = {
            "summary": f"{stage} did not complete; the reason was handed forward.",
            "next_actions": ["Continue to the final report with the recorded limitation."],
            "limitations": ["This stage did not produce its normal successful output."],
        }
        handoff_path.write_text(
            json.dumps(handoff, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        store.record_workflow_stage_observation(
            invocation_id=fixture["invocation_id"],
            job_id=fixture["identity"]["job_id"],
            source_launch_id=fixture["identity"]["source_launch_id"],
            source_run_id=None,
            target_date=fixture["identity"]["target_date"],
            stage_name=stage,
            status="unknown" if stage != "deployment" else "not_performed",
            reason_code=f"{stage}_result_unavailable",
            observed_at=BASE_TIME,
        )

    reopened = MaintenanceStore(
        store.database_path.parent.parent,
        daily_database_path=fixture["daily_database_path"],
    )
    reopened.initialize()
    resumed_lease = reopened.acquire_lease(
        lease_key=f"maintenance:{fixture['identity']['job_id']}",
        owner_id="fc10-handoff-terminal-reconciler",
        owner_pid=os.getpid(),
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    monkeypatch.setattr(
        workflow_reconciliation_module,
        "__file__",
        str(fixture["repository"] / "automation/inago/maintenance/workflow_reconciliation.py"),
    )
    terminal = reconcile_workflow_terminal(
        reopened,
        job_id=fixture["identity"]["job_id"],
        lease=resumed_lease,
        expected_identity=fixture["identity"],
    )

    assert terminal["workflow_completion"] == "complete", json.dumps(
        {
            "reason_code": terminal["reason_code"],
            "terminal_verification": terminal["terminal_verification"],
            "missing_attempt_outputs": terminal["missing_attempt_outputs"],
        },
        sort_keys=True,
    )
    assert terminal["terminal_verification"][
        "stage_attempt_or_failure_handoffs_read_back"
    ]
    attempt_refs = {item["step_key"]: item for item in terminal["attempt_output_refs"]}
    assert attempt_refs["maintenance_investigation"]["evidence_kind"] == "stage_failure_handoff"
    assert attempt_refs["maintenance_investigation"]["status"] == "complete"
    assert attempt_refs["maintenance_investigation"]["formal_output_observation"] == (
        "completed_output_readback_failed"
    )
    for step_key in ("maintenance_planning", "maintenance_repair"):
        assert attempt_refs[step_key]["evidence_kind"] == "stage_failure_handoff"
        assert attempt_refs[step_key]["status"] == "failed"
    assert terminal["deployment_output_ref"]["evidence_kind"] == "stage_failure_handoff"
    assert terminal["deployment_output_ref"]["stage"] == "deployment"
    assert "deployment:deployment_result_schema_invalid" in terminal[
        "deployment_output_ref"]["attempt_output_observations"]
    assert terminal["report_publication_readback"]["public_html_sha256"]
    failure_events = [
        json.loads(line)
        for line in (
            reopened.database_path.parent / "failure-journal" / "events.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line
    ]
    formal_readback_event = next(
        event
        for event in failure_events
        if event.get("operation") == "workflow.terminal.formal_attempt_output_readback"
    )
    assert formal_readback_event["invocation_id"] == fixture["invocation_id"]
    assert formal_readback_event["capture_location"] == (
        "workflow_reconciliation._attempt_and_deployment_outputs"
    )
    assert formal_readback_event["exception_chain"][:2] == [
        "HandoffContractError",
        "OSError",
    ]
    assert formal_readback_event["reason_code"] == (
        "terminal_attempt_output_readback_failed"
    )
    assert formal_readback_event["artifact_refs"] == [
        f"{investigation_attempt.job_id}/{investigation_attempt.attempt_id}/result.json"
    ]
    deployment_validation_event = next(
        event
        for event in failure_events
        if event.get("operation")
        == "workflow.terminal.deployment_result_readback_validate"
    )
    assert deployment_validation_event["invocation_id"] == fixture["invocation_id"]
    assert deployment_validation_event["reason_code"] == (
        "deployment_result_schema_invalid"
    )
    final_job = reopened.list_workflow_jobs_for_launch(
        source_launch_id=fixture["identity"]["source_launch_id"],
        source_run_id=None,
        target_date=fixture["identity"]["target_date"],
        job_id=fixture["identity"]["job_id"],
    )[0]
    assert final_job.status == "complete"
    incident_row = reopened._connect().execute(
        "SELECT status FROM incident WHERE incident_id = ?",
        (fixture["identity"]["incident_id"],),
    ).fetchone()
    assert incident_row["status"] == "in_progress"


def test_daily_report_ai_envelopes_returns_all_valid_same_attempt_turns(
    tmp_path: Path,
):
    from copy import deepcopy

    from tests_inago.test_maintenance_report import _IDENTITY, _report_response

    store = make_store(tmp_path)
    attempts_root = store.database_path.parent / "attempts"
    identity = deepcopy(_IDENTITY)
    attempt_id = "attempt-envelope-turns-001"
    attempt_root = attempts_root / identity["job_id"] / attempt_id
    attempt_root.mkdir(parents=True)

    report = _report_response()
    report["identity"] = identity
    report["result_version"] = "report-envelope-turns-v1"
    response = {**identity, "report": report}
    schema = production_workflow._daily_report_ai_response_schema("report")
    expected_turns = [
        ("a" * 32, "thread-current", "turn-current"),
        ("b" * 32, "thread-initial", "turn-initial"),
    ]
    for file_id, thread_id, turn_id in expected_turns:
        envelope = {
            "schema_version": "maintenance-ai-response-v1",
            "identity": identity,
            "attempt_id": attempt_id,
            "owner": {
                "parent_key": production_workflow._REPORT_PARENT_KEY,
                "role_name": production_workflow._REPORT_AUTHOR_ROLE,
                "role_config_sha256": "f" * 64,
            },
            "allowed_outputs": list(production_workflow._REPORT_AUTHOR_ALLOWED_OUTPUTS),
            "thread_id": thread_id,
            "turn_id": turn_id,
            "response": response,
        }
        (attempt_root / f"B--{production_workflow._REPORT_AUTHOR_ROLE}--{file_id}.json").write_text(
            json.dumps(envelope, ensure_ascii=False),
            encoding="utf-8",
        )
    invalid_envelope = deepcopy(envelope)
    invalid_envelope["owner"]["role_config_sha256"] = "a" * 64
    invalid_envelope["thread_id"] = "thread-wrong-config"
    invalid_envelope["turn_id"] = "turn-wrong-config"
    (attempt_root / f"B--{production_workflow._REPORT_AUTHOR_ROLE}--{'c' * 32}.json").write_text(
        json.dumps(invalid_envelope, ensure_ascii=False),
        encoding="utf-8",
    )

    outputs = production_workflow._daily_report_ai_envelopes(
        store=store,
        attempts_root=attempts_root,
        job_id=identity["job_id"],
        attempt_id=attempt_id,
        identity=identity,
        role_name=production_workflow._REPORT_AUTHOR_ROLE,
        role_config_sha256="f" * 64,
        allowed_outputs=production_workflow._REPORT_AUTHOR_ALLOWED_OUTPUTS,
        response_schema=schema,
    )

    assert [output[2]["turn_id"] for output in outputs] == [
        "turn-current",
        "turn-initial",
    ]


@pytest.mark.parametrize(
    ("source_attempt_id", "include_duplicate"),
    [
        ("attempt-wrapper-ambiguity-001", True),
        ("attempt-wrapper-prior-source-001", False),
    ],
)
def test_current_report_wrapper_rejects_ambiguous_or_cross_attempt_envelopes(
    tmp_path: Path, monkeypatch, source_attempt_id: str, include_duplicate: bool
):
    from copy import deepcopy

    from tests_inago.test_maintenance_report import _IDENTITY, _report_response

    identity = deepcopy(_IDENTITY)
    attempt_id = "attempt-wrapper-ambiguity-001"
    result_version = "report-wrapper-ambiguity-v1"
    created_at_jst = "2026-10-20T12:00:00+09:00"
    source_sha256 = "b" * 64
    input_sha256 = "c" * 64
    role_name = production_workflow._REPORT_AUTHOR_ROLE
    response = _report_response()
    response["identity"] = identity
    response["result_version"] = result_version
    response["created_at_jst"] = created_at_jst
    ai_response = {**identity, "report": response}
    first_path = f"{identity['job_id']}/{source_attempt_id}/first-envelope.json"
    first_sha256 = "a" * 64
    wrapper = {
        "schema_version": "maintenance-report-ai-result-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "source_sha256": source_sha256,
        "result_version": result_version,
        "input_sha256": input_sha256,
        "role": role_name,
        "source_attempt_id": source_attempt_id,
        "ai_output": {"path": first_path, "sha256": first_sha256},
        "response": ai_response,
        "created_at_jst": created_at_jst,
    }
    envelope = {
        "schema_version": "maintenance-ai-response-v1",
        "identity": identity,
        "attempt_id": source_attempt_id,
        "owner": {
            "parent_key": production_workflow._REPORT_PARENT_KEY,
            "role_name": role_name,
            "role_config_sha256": "f" * 64,
        },
        "allowed_outputs": list(production_workflow._REPORT_AUTHOR_ALLOWED_OUTPUTS),
        "thread_id": "same-input-thread",
        "turn_id": "same-input-turn",
        "response": ai_response,
    }
    outputs = [(first_path, first_sha256, envelope)]
    if include_duplicate:
        outputs.append(
            (
                f"{identity['job_id']}/{source_attempt_id}/second-envelope.json",
                "d" * 64,
                {**envelope, "thread_id": "later-thread", "turn_id": "later-turn"},
            )
        )
    monkeypatch.setattr(
        production_workflow,
        "_daily_report_read_json",
        lambda **_kwargs: wrapper,
    )
    monkeypatch.setattr(
        production_workflow,
        "_daily_report_ai_envelopes",
        lambda **_kwargs: outputs,
    )

    loaded = production_workflow._daily_report_load_current_ai_wrapper(
        store=object(),
        attempts_root=tmp_path,
        job_id=identity["job_id"],
        current_attempt_id=attempt_id,
        identity=identity,
        source_sha256=source_sha256,
        result_version=result_version,
        input_sha256=input_sha256,
        role_name=role_name,
        schema_version="maintenance-report-ai-result-v1",
        filename="report-response.json",
        allowed_outputs=production_workflow._REPORT_AUTHOR_ALLOWED_OUTPUTS,
        role_config_sha256="f" * 64,
        response_schema=production_workflow._daily_report_ai_response_schema("report"),
        extra_expected={"created_at_jst": created_at_jst},
    )

    assert loaded is None


def test_current_review_wrapper_rejects_multiple_same_html_envelopes(
    tmp_path: Path, monkeypatch
):
    from copy import deepcopy

    from tests_inago.test_maintenance_report import _IDENTITY

    identity = deepcopy(_IDENTITY)
    attempt_id = "attempt-review-wrapper-ambiguity-001"
    result_version = "report-review-wrapper-v1"
    html_sha256 = "b" * 64
    source_sha256 = "c" * 64
    input_sha256 = "d" * 64
    role_name = production_workflow._REPORT_REVIEWER_ROLE
    review = {
        "schema_version": "maintenance-report-review-v1",
        "reviewer_id": role_name,
        "reviewed_version": result_version,
        "reviewed_sha256": html_sha256,
        "status": "approved",
        "reviewed_at": "2026-10-20T00:00:00+09:00",
        "findings": [],
    }
    ai_response = {**identity, "review": review}
    first_path = f"{identity['job_id']}/{attempt_id}/first-review-envelope.json"
    first_sha256 = "a" * 64
    wrapper = {
        "schema_version": "maintenance-report-review-ai-result-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "source_sha256": source_sha256,
        "result_version": result_version,
        "input_sha256": input_sha256,
        "role": role_name,
        "source_attempt_id": attempt_id,
        "ai_output": {"path": first_path, "sha256": first_sha256},
        "response": ai_response,
        "html_sha256": html_sha256,
    }
    envelope = {
        "schema_version": "maintenance-ai-response-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "owner": {
            "parent_key": production_workflow._REPORT_PARENT_KEY,
            "role_name": role_name,
            "role_config_sha256": "f" * 64,
        },
        "allowed_outputs": list(production_workflow._REPORT_REVIEW_ALLOWED_OUTPUTS),
        "thread_id": "same-review-thread",
        "turn_id": "same-review-turn",
        "response": ai_response,
    }
    outputs = [
        (first_path, first_sha256, envelope),
        (
            f"{identity['job_id']}/{attempt_id}/second-review-envelope.json",
            "e" * 64,
            {**envelope, "thread_id": "later-review-thread", "turn_id": "later-review-turn"},
        ),
    ]
    monkeypatch.setattr(
        production_workflow,
        "_daily_report_read_json",
        lambda **_kwargs: wrapper,
    )
    monkeypatch.setattr(
        production_workflow,
        "_daily_report_ai_envelopes",
        lambda **_kwargs: outputs,
    )

    loaded = production_workflow._daily_report_load_current_ai_wrapper(
        store=object(),
        attempts_root=tmp_path,
        job_id=identity["job_id"],
        current_attempt_id=attempt_id,
        identity=identity,
        source_sha256=source_sha256,
        result_version=result_version,
        input_sha256=input_sha256,
        role_name=role_name,
        schema_version="maintenance-report-review-ai-result-v1",
        filename="review-response.json",
        allowed_outputs=production_workflow._REPORT_REVIEW_ALLOWED_OUTPUTS,
        role_config_sha256="f" * 64,
        response_schema=production_workflow._daily_report_ai_response_schema("review"),
        extra_expected={"html_sha256": html_sha256},
    )

    assert loaded is None


@pytest.mark.parametrize(
    "missing_condition",
    ["stage_handoff", "handoff_invocation_mismatch", "public_report"],
)
def test_fc08_terminal_reconciliation_never_completes_with_missing_evidence(
    tmp_path: Path, monkeypatch, missing_condition: str
):
    fixture = _fc08_seed_terminal_fixture(tmp_path, monkeypatch)
    store = fixture["store"]
    _fc08_crash_after_publish(fixture, exit_code=49)
    if missing_condition == "stage_handoff":
        (
            store.database_path.parent.parent
            / "workflow-handoffs"
            / fixture["identity"]["job_id"]
            / fixture["invocation_id"]
            / "planning.json"
        ).unlink()
    elif missing_condition == "handoff_invocation_mismatch":
        planning_path = (
            store.database_path.parent.parent
            / "workflow-handoffs"
            / fixture["identity"]["job_id"]
            / fixture["invocation_id"]
            / "planning.json"
        )
        planning = json.loads(planning_path.read_text(encoding="utf-8"))
        planning["invocation_id"] = "fc08-prior-invocation"
        planning_path.write_text(
            json.dumps(planning, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        report_path = fixture["report_path"]
        removal_repository = tmp_path / "fc08-remove-public-report"
        subprocess.run(
            ["git", "clone", str(fixture["remote"]), str(removal_repository)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(removal_repository, "config", "user.name", "FC08 Synthetic Readback")
        _git(
            removal_repository,
            "config",
            "user.email",
            "fc08-readback@example.invalid",
        )
        _git(removal_repository, "rm", "--", report_path)
        _git(removal_repository, "commit", "-m", "remove synthetic public report")
        _git(removal_repository, "push", "origin", "main")
    reopened = MaintenanceStore(
        store.database_path.parent.parent,
        daily_database_path=fixture["daily_database_path"],
    )
    reopened.initialize()
    resumed_lease = reopened.acquire_lease(
        lease_key=f"maintenance:{fixture['identity']['job_id']}",
        owner_id=f"fc08-reconciler-{missing_condition}",
        owner_pid=os.getpid(),
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    monkeypatch.setattr(
        workflow_reconciliation_module,
        "__file__",
        str(fixture["repository"] / "automation/inago/maintenance/workflow_reconciliation.py"),
    )
    terminal = reconcile_workflow_terminal(
        reopened,
        job_id=fixture["identity"]["job_id"],
        lease=resumed_lease,
        expected_identity=fixture["identity"],
    )
    assert terminal["workflow_completion"] != "complete"
    assert terminal["job_terminal_status"] == "failed"
    assert terminal["reason_code"] == "workflow_terminal_observations_incomplete"
    assert not all(terminal["terminal_verification"].values())
    if missing_condition in {"stage_handoff", "handoff_invocation_mismatch"}:
        assert "planning" in terminal["missing_stage_handoffs"]
    else:
        assert not terminal["terminal_verification"][
            "report_result_html_publication_sidecar_and_remote_readback"
        ]
    final_job = reopened.list_workflow_jobs_for_launch(
        source_launch_id=fixture["identity"]["source_launch_id"],
        source_run_id=None,
        target_date=fixture["identity"]["target_date"],
        job_id=fixture["identity"]["job_id"],
    )[0]
    assert final_job.status == "failed"
    incident_row = reopened._connect().execute(
        "SELECT status FROM incident WHERE incident_id = ?",
        (fixture["identity"]["incident_id"],),
    ).fetchone()
    assert incident_row["status"] == "in_progress"


@pytest.mark.parametrize(
    ("report_status", "expected_status", "expected_job_status", "expected_reason"),
    [
        ("complete", "complete", "complete", None),
        ("pending", "complete", "complete", None),
        ("blocked", "complete", "complete", None),
    ],
)
@pytest.mark.parametrize("failure_point", ["sidecar", "push"])
def test_publication_stage_recovers_after_push_before_artifact_save(
    tmp_path: Path,
    monkeypatch,
    report_status: str,
    expected_status: str,
    expected_job_status: str,
    expected_reason: str | None,
    failure_point: str,
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
    _git(repository, "init", "--initial-branch=main")
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
    original_publish = production_workflow.publish_reviewed_report
    save_failed = False
    push_failed = False

    def fail_first_publication_save(*args, **kwargs):
        nonlocal save_failed
        if kwargs.get("filename") == "report-publication.json" and not save_failed:
            save_failed = True
            raise OSError("synthetic crash after remote push")
        return original_save(*args, **kwargs)

    def fail_first_publication_push(*args, **kwargs):
        nonlocal push_failed
        if not push_failed and kwargs.get("target_branch") == "main":
            push_failed = True
            raise OSError("synthetic remote push failure")
        return original_publish(*args, **kwargs)

    if failure_point == "sidecar":
        monkeypatch.setattr(
            production_workflow, "save_attempt_artifact", fail_first_publication_save
        )
    else:
        monkeypatch.setattr(
            production_workflow, "publish_reviewed_report", fail_first_publication_push
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
    assert rejected.status == "unknown"
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
    assert target_mismatch.status == "unknown"
    assert target_mismatch.reason_code == "report_target_branch_mismatch"
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
    assert first.status == "unknown"
    expected_failure_reason = (
        "report_publication_sidecar_save_failed"
        if failure_point == "sidecar"
        else "report_publication_push_failed"
    )
    assert first.reason_code == expected_failure_reason
    assert not guard.held
    if failure_point == "sidecar":
        assert not (attempt_root / "report-publication.json").exists()

    failure_journal = resolve_maintenance_failure_journal_path(tmp_path)
    failure_events = [
        json.loads(line)
        for line in failure_journal.read_text(encoding="utf-8").splitlines()
    ]
    recorded_failure = next(
        event for event in failure_events
        if event.get("phase") == "failed"
        and event.get("operation") == (
            "workflow.report.publication.save_sidecar"
            if failure_point == "sidecar"
            else "workflow.report.publication.push_remote"
        )
    )
    assert recorded_failure["correlation_id"]
    assert recorded_failure["parent_operation"] == "workflow.report.publication"
    assert recorded_failure["job_id"] == job_id
    assert recorded_failure["source_launch_id"] == identity["source_launch_id"]
    assert recorded_failure["source_run_id"] is None
    assert recorded_failure["incident_id"] == identity["incident_id"]
    assert recorded_failure["input_refs"] == ["evidence/u21/input.json"]
    assert recorded_failure["capture_location"] == "run_daily_report_publication_stage"

    remote_commit_before_retry = _git(remote, "rev-parse", "refs/heads/main")
    monkeypatch.setattr(production_workflow, "save_attempt_artifact", original_save)
    monkeypatch.setattr(production_workflow, "publish_reviewed_report", original_publish)
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
    remote_commit_after_retry = _git(remote, "rev-parse", "refs/heads/main")
    if failure_point == "sidecar":
        assert remote_commit_after_retry == remote_commit_before_retry
    else:
        assert remote_commit_before_retry == code_commit
        assert remote_commit_after_retry != code_commit
    assert not guard.held
    publication_record = json.loads(
        (attempt_root / "report-publication.json").read_text(encoding="utf-8")
    )
    assert publication_record["publication_commit"] == remote_commit_after_retry
    assert publication_record["report_sha256"] == html_sha256
    assert controller.finalized == []
    assert job.status == "running"
    # Publication persists evidence; terminal reconciliation is the only
    # operation allowed to finalize the job or resolve the incident.
    assert (
        _git(
            repository,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            remote_commit_after_retry,
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
    assert result.status == "unknown"
    assert result.reason_code == "synthetic_author_transport_unavailable"


def test_fc10_fallback_review_round_uses_persisted_review_history(
    tmp_path: Path, monkeypatch
):
    source_launch_id = "launch-fc10-fallback-review-round"
    target_date = "2026-10-17"
    store = make_store(tmp_path)
    incident_id = create_incident(store, launch_id=source_launch_id)
    base_commit = _git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD")
    release = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-fc10-fallback-review-round",
        base_commit=base_commit,
        result_commit=base_commit,
    )
    store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
        created_at=BASE_TIME,
    )
    job = store.list_workflow_jobs_for_launch(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
    )[0]
    (store.database_path.parent / "attempts").mkdir(parents=True)
    lease = store.acquire_lease(
        lease_key=f"maintenance:{job.job_id}",
        owner_id="fc10-fallback-review-round-test",
        owner_pid=1,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    for attempt_no in range(1, 5):
        prior = store.begin_step_attempt(
            job_id=job.job_id,
            step_key="maintenance_report",
            sequence=4,
            lease=lease,
            started_at=BASE_TIME,
        )
        assert prior.attempt_no == attempt_no
        store.complete_step_attempt(
            attempt_id=prior.attempt_id,
            lease=lease,
            result_version=f"prior-report-{attempt_no}",
            completed_at=BASE_TIME,
        )

    class UnavailableReviewerSession:
        def run_turn(self, *_args, **_kwargs):
            raise production_workflow.MaintenanceAIBlockedError(
                "synthetic_fallback_reviewer_transport_unavailable"
            )

        def close(self):
            return None

    class UnavailableReviewerAdapter:
        def __init__(self, *_args, **_kwargs):
            pass

        def plan_session(self, **kwargs):
            return SimpleNamespace(cwd=kwargs["cwd"], role_config_sha256="a" * 64)

        def create_session(self, _plan):
            return UnavailableReviewerSession()

    monkeypatch.setattr(
        production_workflow, "MaintenanceAIAdapter", UnavailableReviewerAdapter
    )
    workflow = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date=target_date,
        ),
        check=SimpleNamespace(check_id="check-fc10-fallback-review-round"),
        queue=SimpleNamespace(queue_id="queue-fc10-fallback-review-round"),
        lease=lease,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=job, attempts=()),
        state={
            "job": job,
            "precheck_identity": store.get_job_input_manifest(
                job_id=job.job_id
            ).identity(),
            "report_base_commit": base_commit,
            "report_source_snapshot": {
                "implementation": {"result_commit": base_commit},
                "tests": {"status": "not_run"},
                "deployment": {"status": "not_attempted"},
            },
            "report_input_refs": ["historical-input/2026-10-17/observation.json"],
            "report_evidence_refs": ["historical-input/2026-10-17/source-evidence.json"],
        },
    )

    result = production_workflow._persist_minimal_daily_report(
        workflow,
        MaintenanceController(store),
        store,
        operation="synthetic_normal_report_failure",
        outcome="確認できる記録の範囲を説明します。",
        failure_reason="synthetic_report_author_unavailable",
    )

    assert result.status == "complete"
    completed = workflow.state["report_attempt"]
    assert completed.attempt_no == 5
    assert completed.status == "complete"
    assert workflow.state["report_result"]["review"]["status"] == "unavailable"
    saved_review = store.get_step_attempt_review(attempt_id=completed.attempt_id)
    assert saved_review is not None
    assert saved_review["status"] == "unavailable"


@pytest.mark.parametrize(
    "reviewer_mode",
    [
        "changes_requested_then_approved",
        "resumed_attempt_with_four_findings",
        "previous_attempt_reviewer_response_is_not_reused",
        "transport_failure",
        "fallback_review_changes_requested_then_approved",
        "fallback_revision_selects_current_turn_among_multiple_author_envelopes",
        "fallback_second_review_findings_remain_bound_to_reviewed_version",
    ],
)
def test_fc07_report_role_review_revision_or_failure_reaches_publication(
    tmp_path: Path, monkeypatch, reviewer_mode: str
):
    from copy import deepcopy

    from tests_inago.test_maintenance_report import _report_response

    source_launch_id = f"launch-fc07-{reviewer_mode}"
    target_date = "2026-10-17"
    store = make_store(tmp_path)
    incident_id = create_incident(store, launch_id=source_launch_id)
    release = create_ready_release(
        store,
        incident_id=incident_id,
        release_id=f"release-fc07-{reviewer_mode}",
        base_commit=_git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD"),
        result_commit=_git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD"),
    )
    store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
        created_at=BASE_TIME,
    )
    job = store.list_workflow_jobs_for_launch(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
    )[0]
    lease = store.acquire_lease(
        lease_key=f"maintenance:{job.job_id}",
        owner_id="fc07-report-role-test",
        owner_pid=1,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    controller = MaintenanceController(store)
    identity = {
        "incident_id": incident_id,
        "source_launch_id": source_launch_id,
        "source_run_id": None,
        "target_date": target_date,
        "job_id": job.job_id,
    }
    context = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date=target_date,
        ),
        check=SimpleNamespace(check_id=f"check-{reviewer_mode}"),
        queue=SimpleNamespace(queue_id=f"queue-{reviewer_mode}"),
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
    runtime_root = store.database_path.parent.parent
    (store.database_path.parent / "worktrees").mkdir(parents=True)
    reports: dict[tuple[str, str], list[dict[str, object]]] = {}
    reviewer_responses: list[dict[str, object]] = []
    review_inputs: list[dict[str, object]] = []
    author_inputs: list[dict[str, object]] = []
    author_attempt_ids: list[str] = []
    role_calls: list[str] = []
    normal_review_inputs: list[dict[str, object]] = []
    prepared_reports: list[object] = []
    turn_number = 0

    monkeypatch.setattr(
        production_workflow,
        "_daily_report_source_documents",
        lambda **_kwargs: {},
    )
    original_author_prompt = production_workflow._daily_report_prompt
    original_reviewer_prompt = production_workflow._daily_report_review_prompt

    def author_prompt(**kwargs):
        author_inputs.append(deepcopy(kwargs["report_input"]))
        return original_author_prompt(**kwargs)

    def reviewer_prompt(**kwargs):
        review_input = deepcopy(kwargs["review_input"])
        review_inputs.append(review_input)
        if "fallback_origin" not in review_input:
            normal_review_inputs.append(review_input)
        return original_reviewer_prompt(**kwargs)

    monkeypatch.setattr(production_workflow, "_daily_report_prompt", author_prompt)
    monkeypatch.setattr(production_workflow, "_daily_report_review_prompt", reviewer_prompt)

    previous_reviewer_attempt_id = "attempt-fc07-previous-reviewer"
    if reviewer_mode == "previous_attempt_reviewer_response_is_not_reused":
        original_prepare = production_workflow.prepare_incident_report
        original_attempt_input = production_workflow._daily_report_attempt_input
        recovery_call_count = 0
        previous_attempt = SimpleNamespace(
            attempt_id=previous_reviewer_attempt_id,
            attempt_no=0,
            status="interrupted",
        )

        def prepare_report(*args, **kwargs):
            prepared = original_prepare(*args, **kwargs)
            prepared_reports.append(prepared)
            return prepared

        def attempt_input(**kwargs):
            if (
                kwargs.get("filename") == "review-input.json"
                and kwargs.get("attempt_id") == previous_reviewer_attempt_id
                and author_inputs
                and prepared_reports
            ):
                current_author_input = author_inputs[-1]
                return {
                    "source_sha256": current_author_input["source_sha256"],
                    "result_version": current_author_input["result_version"],
                    "html_sha256": prepared_reports[-1].html_sha256,
                }
            return original_attempt_input(**kwargs)

        def recovery_attempts(**kwargs):
            nonlocal recovery_call_count
            recovery_call_count += 1
            if recovery_call_count == 1:
                return []
            return [previous_attempt]

        monkeypatch.setattr(production_workflow, "prepare_incident_report", prepare_report)
        monkeypatch.setattr(production_workflow, "_daily_report_attempt_input", attempt_input)
        monkeypatch.setattr(
            production_workflow,
            "_daily_report_recovery_attempts",
            recovery_attempts,
        )

    class FakeSession:
        def __init__(self, role: str, attempt_id: str, role_hash: str, allowed: tuple[str, ...]):
            self.role = role
            self.attempt_id = attempt_id
            self.role_hash = role_hash
            self.allowed = allowed

        def run_turn(self, *_args, **_kwargs):
            nonlocal turn_number
            turn_number += 1
            role_calls.append(self.role)
            turn_id = f"fc07-turn-{turn_number}"
            thread_id = f"fc07-thread-{turn_number}"
            if self.role == production_workflow._REPORT_AUTHOR_ROLE:
                report_input = author_inputs[-1]
                author_attempt_ids.append(self.attempt_id)
                if (
                    reviewer_mode in {
                        "fallback_review_changes_requested_then_approved",
                        "fallback_second_review_findings_remain_bound_to_reviewed_version",
                    }
                    and len(author_inputs) == 1
                ):
                    raise production_workflow.MaintenanceAIBlockedError(
                        "synthetic_normal_author_transport_unavailable"
                    )
                report = deepcopy(_report_response())
                report["identity"] = identity
                report["result_version"] = report_input["result_version"]
                report["created_at_jst"] = report_input["created_at_jst"]
                report["title"] = f"合成レポート版{len(author_inputs)}"
                report["summary"] = f"合成レポート本文版{len(author_inputs)}"
                assert report_input["evidence_refs"]
                for section in ("issues", "checks"):
                    for item in report[section]:
                        item["evidence_ref"] = report_input["evidence_refs"][0]
                if (
                    reviewer_mode
                    == "fallback_revision_selects_current_turn_among_multiple_author_envelopes"
                    and len(author_inputs) == 1
                ):
                    report["issues"][0]["evidence_ref"] = "not-an-allowed-evidence-reference"
                response = {**identity, "report": report}
            else:
                if reviewer_mode == "transport_failure":
                    raise production_workflow.MaintenanceAIBlockedError(
                        "synthetic_reviewer_transport_unavailable"
                    )
                review_input = review_inputs[-1]
                review_round = len(review_inputs)
                response = {
                    **identity,
                    "review": {
                        "schema_version": "maintenance-report-review-v1",
                        "reviewer_id": production_workflow._REPORT_REVIEWER_ROLE,
                        "reviewed_version": review_input["result_version"],
                        "reviewed_sha256": review_input["html_sha256"],
                        "status": (
                            "changes_requested"
                            if review_round == 1
                            or (
                                reviewer_mode
                                == "fallback_second_review_findings_remain_bound_to_reviewed_version"
                                and review_round == 2
                            )
                            else "approved"
                        ),
                        "reviewed_at": "2026-10-20T00:00:00+09:00",
                        "findings": (
                            [
                                {
                                    "severity": "medium",
                                    "location": f"本文{review_round}-{finding_index}",
                                    "finding": f"版{review_round}の確認事項{finding_index}を具体化してください。",
                                }
                                for finding_index in range(1, 5)
                            ]
                            if reviewer_mode == "resumed_attempt_with_four_findings"
                            and review_round == 1
                            else [
                                {
                                    "severity": "medium",
                                    "location": f"本文{review_round}",
                                    "finding": f"版{review_round}の確認事項を具体化してください。",
                                }
                            ]
                            if (
                                review_round == 1
                                or reviewer_mode
                                == "fallback_second_review_findings_remain_bound_to_reviewed_version"
                            )
                            else []
                        ),
                    },
                }
            envelope = {
                "schema_version": "maintenance-ai-response-v1",
                "identity": identity,
                "attempt_id": self.attempt_id,
                "owner": {
                    "parent_key": production_workflow._REPORT_PARENT_KEY,
                    "role_name": self.role,
                },
                "allowed_outputs": list(self.allowed),
                "thread_id": thread_id,
                "turn_id": turn_id,
                "response": response,
            }
            path = f"{job.job_id}/{self.attempt_id}/synthetic-{self.role}.json"
            reports.setdefault((self.attempt_id, self.role), []).append({
                "path": path,
                "sha256": "a" * 64,
                "envelope": envelope,
            })
            if self.role == production_workflow._REPORT_REVIEWER_ROLE:
                reviewer_responses.append(envelope)
            return SimpleNamespace(
                turn=SimpleNamespace(turn_id=turn_id, thread_id=thread_id),
                response=response,
            )

        def close(self):
            return None

    class FakeAdapter:
        def __init__(self, *_args, **_kwargs):
            pass

        def plan_session(self, **kwargs):
            return SimpleNamespace(
                role_name=kwargs["role_name"],
                attempt_id=kwargs["attempt_id"],
                role_config_sha256="f" * 64,
                cwd=kwargs["cwd"],
            )

        def create_session(self, plan):
            allowed = (
                production_workflow._REPORT_AUTHOR_ALLOWED_OUTPUTS
                if plan.role_name == production_workflow._REPORT_AUTHOR_ROLE
                else production_workflow._REPORT_REVIEW_ALLOWED_OUTPUTS
            )
            return FakeSession(
                plan.role_name, plan.attempt_id, plan.role_config_sha256, allowed
            )

    def envelopes(*, attempt_id: str, role_name: str, **_kwargs):
        if (
            reviewer_mode == "previous_attempt_reviewer_response_is_not_reused"
            and attempt_id == previous_reviewer_attempt_id
            and role_name == production_workflow._REPORT_REVIEWER_ROLE
            and author_inputs
            and prepared_reports
        ):
            review = {
                "schema_version": "maintenance-report-review-v1",
                "reviewer_id": production_workflow._REPORT_REVIEWER_ROLE,
                "reviewed_version": author_inputs[-1]["result_version"],
                "reviewed_sha256": prepared_reports[-1].html_sha256,
                "status": "approved",
                "reviewed_at": "2026-10-20T00:00:00+09:00",
                "findings": [],
            }
            envelope = {
                "schema_version": "maintenance-ai-response-v1",
                "identity": identity,
                "attempt_id": previous_reviewer_attempt_id,
                "owner": {
                    "parent_key": production_workflow._REPORT_PARENT_KEY,
                    "role_name": role_name,
                    "role_config_sha256": "f" * 64,
                },
                "allowed_outputs": list(production_workflow._REPORT_REVIEW_ALLOWED_OUTPUTS),
                "thread_id": "old-review-thread",
                "turn_id": "old-review-turn",
                "response": {**identity, "review": review},
            }
            return [
                (
                    f"{job.job_id}/{previous_reviewer_attempt_id}/synthetic-review.json",
                    "e" * 64,
                    envelope,
                )
            ]
        saved = reports.get((attempt_id, role_name), [])
        return [
            (item["path"], item["sha256"], item["envelope"])
            for item in saved
        ]

    monkeypatch.setattr(production_workflow, "MaintenanceAIAdapter", FakeAdapter)
    monkeypatch.setattr(production_workflow, "_daily_report_ai_envelopes", envelopes)
    publication_calls: list[dict[str, object]] = []

    def publish(context_arg, _controller, _store, **kwargs):
        publication_calls.append(
            {
                "identity": context_arg.state.get("precheck_identity"),
                "report": context_arg.state.get("report_result"),
                "kwargs": kwargs,
            }
        )
        return WorkflowStageResult(status="complete")

    monkeypatch.setattr(production_workflow, "run_daily_report_publication_stage", publish)
    resumed_attempt_id = None
    if reviewer_mode == "resumed_attempt_with_four_findings":
        started = controller.begin_step_attempt(
            job_id=job.job_id,
            step_key="maintenance_report",
            sequence=4,
            lease=lease,
            started_at=BASE_TIME,
        )
        resumed_attempt_id = started.attempt_id
    provider = production_workflow.build_production_workflow_provider(store)
    result = provider._stage_runners["report"](context)

    assert result.status == "complete"
    assert len(publication_calls) == 1
    assert publication_calls[0]["identity"]["job_id"] == job.job_id
    assert role_calls, {
        key: context.state.get(key)
        for key in (
            "report_preparation_observation",
            "report_source_snapshot",
            "report_source_artifacts",
            "report_fallback_observation",
            "report_result",
        )
    }
    if reviewer_mode in {
        "changes_requested_then_approved",
        "resumed_attempt_with_four_findings",
        "previous_attempt_reviewer_response_is_not_reused",
    }:
        assert role_calls == [
            production_workflow._REPORT_AUTHOR_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
            production_workflow._REPORT_AUTHOR_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
        ], {
            "role_calls": role_calls,
            "report_fallback_observation": context.state.get("report_fallback_observation"),
            "report_result": context.state.get("report_result"),
        }
        assert len(author_inputs) == 2
        assert author_inputs[1]["prior_review_findings"]
        if reviewer_mode == "previous_attempt_reviewer_response_is_not_reused":
            assert len(normal_review_inputs) == 2
            assert normal_review_inputs[0]["result_version"] == author_inputs[0]["result_version"]
            assert normal_review_inputs[1]["result_version"] == author_inputs[1]["result_version"]
        if reviewer_mode == "resumed_attempt_with_four_findings":
            assert len(author_inputs[1]["prior_review_findings"]) == 4
            assert author_attempt_ids[0] == resumed_attempt_id
            assert context.state["report_attempt"].attempt_id != resumed_attempt_id
            assert context.state["report_attempt"].status == "complete"
            assert publication_calls[0]["report"]["attempt_id"] == author_attempt_ids[-1]
        assert author_inputs[0]["result_version"] != author_inputs[1]["result_version"]
        assert review_inputs[0]["html_sha256"] != review_inputs[1]["html_sha256"]
        assert publication_calls[0]["report"]["review"]["status"] == "approved"
    elif reviewer_mode in {
        "fallback_review_changes_requested_then_approved",
        "fallback_revision_selects_current_turn_among_multiple_author_envelopes",
    }:
        assert role_calls == [
            production_workflow._REPORT_AUTHOR_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
            production_workflow._REPORT_AUTHOR_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
        ]
        assert len(author_inputs) == 2
        first_attempt_id = author_attempt_ids[0]
        if (
            reviewer_mode
            == "fallback_revision_selects_current_turn_among_multiple_author_envelopes"
        ):
            assert len(reports[(first_attempt_id, production_workflow._REPORT_AUTHOR_ROLE)]) == 2
            assert reports[(first_attempt_id, production_workflow._REPORT_AUTHOR_ROLE)][0]["envelope"]["response"]["report"]["issues"][0]["evidence_ref"] == "not-an-allowed-evidence-reference"
        first_review_envelope = reviewer_responses[0]
        first_review = first_review_envelope["response"]["review"]
        assert first_review["reviewed_version"] == review_inputs[0]["result_version"]
        assert review_inputs[0]["attempt_id"] == first_attempt_id
        expected_finding = (
            f"{first_review['findings'][0]['location']}: "
            f"{first_review['findings'][0]['finding']}"
        )
        assert author_inputs[1]["prior_findings"] == [expected_finding]
        assert len(review_inputs) == 2
        assert review_inputs[0]["result_version"] != review_inputs[1]["result_version"]
        assert review_inputs[0]["html_path"] != review_inputs[1]["html_path"]
        assert review_inputs[0]["html_sha256"] != review_inputs[1]["html_sha256"]
        published_report = publication_calls[0]["report"]
        assert isinstance(published_report, dict)
        assert published_report["report_status"] == "complete"
        assert published_report["review"]["status"] == "approved"
        assert published_report["review"]["reviewed_version"] == review_inputs[1]["result_version"]
        assert published_report["review"]["reviewed_sha256"] == review_inputs[1]["html_sha256"]
        assert published_report["output"]["path"] == review_inputs[1]["html_path"]
        assert published_report["output"]["sha256"] == review_inputs[1]["html_sha256"]
    elif reviewer_mode == "fallback_second_review_findings_remain_bound_to_reviewed_version":
        assert role_calls == [
            production_workflow._REPORT_AUTHOR_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
            production_workflow._REPORT_AUTHOR_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
        ]
        assert len(review_inputs) == 2
        second_review = review_inputs[1]
        published_report = publication_calls[0]["report"]
        assert isinstance(published_report, dict)
        # The second finding belongs to the exact revision the reviewer saw.
        # A separately-rendered fallback may still be published, but must not
        # claim that the stale finding reviewed that different HTML version.
        assert published_report["review"]["status"] == "unavailable"
        assert published_report["review"]["reviewed_version"] == published_report["result_version"]
        assert published_report["review"]["reviewed_sha256"] == published_report["output"]["sha256"]
        assert published_report["review"]["findings"] == []
        assert published_report["output"]["path"] != second_review["html_path"]
        assert published_report["output"]["sha256"] != second_review["html_sha256"]
        assert published_report["report_status"] == "pending"
        attempt_root = (
            store.database_path.parent
            / "attempts"
            / job.job_id
            / context.state["report_attempt"].attempt_id
        )
        response_path = attempt_root / f"review-response-fallback-{second_review['html_sha256'][:20]}.json"
        prior_review = json.loads(response_path.read_text(encoding="utf-8"))
        reviewed_input_path = prior_review["review_input"]["path"]
        reviewed_input_sha256 = prior_review["review_input"]["sha256"]
        reviewed_input_file = store.database_path.parent / "attempts" / Path(*reviewed_input_path.split("/"))
        assert reviewed_input_file.is_file()
        assert hashlib.sha256(reviewed_input_file.read_bytes()).hexdigest() == reviewed_input_sha256
        assert reviewed_input_file.read_bytes() == (
            json.dumps(second_review, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        assert prior_review["review"]["reviewed_version"] == second_review["result_version"]
        assert prior_review["review"]["reviewed_sha256"] == second_review["html_sha256"]
        assert prior_review["review"]["findings"][0]["finding"] == "版2の確認事項を具体化してください。"
    else:
        assert role_calls == [
            production_workflow._REPORT_AUTHOR_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
            production_workflow._REPORT_REVIEWER_ROLE,
        ]
        published_report = publication_calls[0]["report"]
        assert isinstance(published_report, dict)
        assert published_report["report_status"] == "pending"
        assert published_report["stage_status"] == "review_unavailable"
        assert published_report["review"]["status"] == "unavailable"
        assert published_report["report_origin"] == "controller_fallback"


def test_fc10_repair_setup_cause_survives_into_not_run_handoff(
    tmp_path: Path, monkeypatch
):
    source_launch_id = "launch-fc10-repair-cause"
    target_date = "2026-10-17"
    store = make_store(tmp_path)
    incident_id = create_incident(store, launch_id=source_launch_id)
    base_commit = _git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD")
    release = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-fc10-repair-cause",
        base_commit=base_commit,
        result_commit=base_commit,
    )
    manifest = store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
        created_at=BASE_TIME,
    )
    job = store.list_workflow_jobs_for_launch(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
    )[0]
    lease = store.acquire_lease(
        lease_key=f"maintenance:{job.job_id}",
        owner_id="fc10-repair-cause-test",
        owner_pid=1,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    controller = MaintenanceController(store)
    started = controller.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_repair",
        sequence=3,
        lease=lease,
        started_at=BASE_TIME,
    )
    monkeypatch.setattr(
        production_workflow,
        "_ensure_repair_worktree",
        lambda *, project_root, worktree_root, base_commit: base_commit,
    )
    context = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date=target_date,
        ),
        check=SimpleNamespace(check_id="check-fc10-repair-cause"),
        queue=SimpleNamespace(queue_id="queue-fc10-repair-cause"),
        lease=lease,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=job, attempts=()),
        state={
            "job": job,
            "precheck_identity": {
                **manifest.identity(),
                "input_refs": [f"daily-launch/{source_launch_id}"],
                "evidence_refs": [f"daily-evidence/{source_launch_id}/index.json"],
            },
        },
    )

    result = production_workflow.run_daily_repair_test_stage(
        context, controller, store
    )

    assert result.status == "unknown"
    assert result.reason_code == "attempt_id_invalid"
    assert context.state["repair_setup_failure"].__class__ is MaintenanceStoreError
    assert context.state["repair_author_failure"] is context.state["repair_setup_failure"]
    not_run = context.state["repair_test_not_performed"]
    assert not_run["outcome"] == "NOT_RUN"
    assert not_run["cause_class"] == "MaintenanceStoreError"
    assert not_run["artifact_ref"]["path"] == (
        f"{job.job_id}/{started.attempt_id}/repair-handoff.json"
    )
    handoff_bytes = store.read_attempt_artifact(
        attempts_root=store.database_path.parent / "attempts",
        relative_path=not_run["artifact_ref"]["path"],
        expected_sha256=not_run["artifact_ref"]["sha256"],
    )
    handoff = json.loads(handoff_bytes)
    assert handoff["operation_result"] == "not_performed"
    assert handoff["reason_code"] == "attempt_id_invalid"
    assert any(
        frame.get("function") == "_daily_repair_setup_document"
        for frame in handoff["stack_frames"]
    )


def test_fc10_planning_fallback_saves_with_manifest_only_context(
    tmp_path: Path, monkeypatch
):
    source_launch_id = "launch-fc10-planning-fallback-context"
    target_date = "2026-10-17"
    store = make_store(tmp_path)
    incident_id = create_incident(store, launch_id=source_launch_id)
    base_commit = _git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD")
    release = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-fc10-planning-fallback-context",
        base_commit=base_commit,
        result_commit=base_commit,
    )
    manifest = store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
        created_at=BASE_TIME,
    )
    job = store.list_workflow_jobs_for_launch(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=release.job_id,
    )[0]
    lease = store.acquire_lease(
        lease_key=f"maintenance:{job.job_id}",
        owner_id="fc10-planning-fallback-test",
        owner_pid=1,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    controller = MaintenanceController(store)
    begun = controller.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_planning",
        sequence=2,
        lease=lease,
        started_at=BASE_TIME,
    )
    attempt = controller.get_step_attempt(attempt_id=begun.attempt_id)
    attempts_root = store.database_path.parent / "attempts"
    output_root = attempts_root / job.job_id / attempt.attempt_id
    output_root.mkdir(parents=True)
    precheck_identity = {
        **manifest.identity(),
        "input_refs": [f"daily-launch/{source_launch_id}"],
        "evidence_refs": [f"daily-evidence/{source_launch_id}/index.json"],
    }
    planning_context = {
        **precheck_identity,
        "attempt_id": attempt.attempt_id,
        "attempt": attempt.attempt_no,
        "base_commit": base_commit,
        "planning_scope": "この計画専用の追加コンテキスト",
    }
    context = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date=target_date,
        ),
        check=SimpleNamespace(check_id="check-fc10-planning-fallback"),
        queue=SimpleNamespace(queue_id="queue-fc10-planning-fallback"),
        lease=lease,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=job, attempts=()),
        state={
            "job": job,
            "invocation_id": "fc10-planning-fallback-invocation",
            "precheck_identity": precheck_identity,
            "planning_attempt": attempt,
            "planning_session_plan": SimpleNamespace(cwd=Path(__file__).resolve().parents[1]),
            "planning_context": planning_context,
            "planning_facts": [{"fact": "調査資料に原因を特定できる情報がない"}],
            "planning_daily_completion": {"status": "unknown"},
            "planning_output_root": output_root,
            "planning_attempts_root": attempts_root,
            "planning_manifest": manifest,
        },
    )
    monkeypatch.setattr(
        production_workflow,
        "prepare_daily_planning_stage",
        lambda *_args: WorkflowStageResult(status="complete"),
    )

    class FailedSession:
        def run_turn(self, prompt, *_args, **_kwargs):
            assert "この計画専用の追加コンテキスト" in prompt
            raise production_workflow.MaintenanceAIBlockedError(
                "synthetic_planning_ai_failure"
            )

        def close(self):
            return None

    class FailedAdapter:
        def create_session(self, _session_plan):
            return FailedSession()

    monkeypatch.setattr(
        production_workflow,
        "_adapter_for_session_plan",
        lambda *_args: FailedAdapter(),
    )

    result = production_workflow.run_daily_planning_author_stage(
        context, controller, store
    )

    assert result.status == "unknown"
    assert result.reason_code == "synthetic_planning_ai_failure"
    assert context.state["planning_context"]["planning_scope"] == "この計画専用の追加コンテキスト"
    assert context.state["planning_stage_handoff_path"] == (
        f"{job.job_id}/{attempt.attempt_id}/stage-handoff.json"
    )
    saved_handoff = json.loads(
        (output_root / "stage-handoff.json").read_text(encoding="utf-8")
    )
    assert saved_handoff["invocation_id"] == "fc10-planning-fallback-invocation"
    assert (output_root / "controller-fallback.json").is_file()
    assert (output_root / "stage-handoff.json").is_file()
    assert (output_root / "plan.md").is_file()


def test_fc10_planning_author_invalid_timeout_returns_unknown_with_invocation_handoff(
    tmp_path: Path,
):
    source_launch_id = "launch-fc10-planning-invalid-timeout"
    store = make_store(tmp_path)
    context = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date="2026-10-17",
        ),
        check=SimpleNamespace(check_id="check-fc10-planning-invalid-timeout"),
        queue=SimpleNamespace(queue_id="queue-fc10-planning-invalid-timeout"),
        lease=None,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=None, attempts=()),
        state={"invocation_id": "fc10-planning-invalid-timeout-invocation"},
    )

    result = production_workflow.run_daily_planning_author_stage(
        context, MaintenanceController(store), store, timeout_seconds=0
    )

    assert result.status == "unknown"
    assert result.reason_code == "planning_timeout_invalid"
    handoff = context.state["planning_stage_handoff"]
    assert handoff["operation_result"] == "not_performed"
    assert handoff["invocation_id"] == context.state["invocation_id"]
    assert handoff["content_result"]["summary"]
    assert handoff["content_result"]["next_actions"]


def test_fc07_missing_identity_still_persists_substantive_fallback_html(
    tmp_path: Path, monkeypatch
):
    source_launch_id = "launch-fc07-missing-identity"
    store = make_store(tmp_path)
    attempts_root = store.database_path.parent / "attempts"
    attempts_root.mkdir(parents=True, exist_ok=True)
    (store.database_path.parent / "worktrees").mkdir(parents=True, exist_ok=True)
    incident_id = create_incident(store, launch_id=source_launch_id)
    release = create_ready_release(
        store,
        incident_id=incident_id,
        release_id="release-fc07-missing-identity",
        base_commit=_git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD"),
        result_commit=_git(Path(__file__).resolve().parents[1], "rev-parse", "HEAD"),
    )
    store.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date="2026-10-17",
        job_id=release.job_id,
        created_at=BASE_TIME,
    )
    job = store.list_workflow_jobs_for_launch(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date="2026-10-17",
        job_id=release.job_id,
    )[0]
    lease = store.acquire_lease(
        lease_key=f"maintenance:{job.job_id}",
        owner_id="fc07-missing-identity",
        owner_pid=1,
        owner_start_time=BASE_TIME,
        now=BASE_TIME,
    )
    authoritative_identity = store.get_job_input_manifest(job_id=job.job_id).identity()
    missing_identity = {
        key: value for key, value in authoritative_identity.items()
        if key != "incident_id"
    }
    monkeypatch.setattr(
        store,
        "get_job_input_manifest",
        lambda **_kwargs: SimpleNamespace(identity=lambda: dict(missing_identity)),
    )

    class ReviewerSession:
        def run_turn(self, *_args, **_kwargs):
            raise production_workflow.MaintenanceAIBlockedError(
                "synthetic_reviewer_transport_unavailable"
            )

        def close(self):
            return None

    class Adapter:
        def __init__(self, *_args, **_kwargs):
            pass

        def plan_session(self, **kwargs):
            return SimpleNamespace(cwd=kwargs["cwd"])

        def create_session(self, _plan):
            return ReviewerSession()

    monkeypatch.setattr(production_workflow, "MaintenanceAIAdapter", Adapter)
    workflow = MaintenanceWorkflowContext(
        launch=SimpleNamespace(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date="2026-10-17",
        ),
        check=SimpleNamespace(check_id="check-fc07-missing-identity"),
        queue=SimpleNamespace(queue_id="queue-fc07-missing-identity"),
        lease=lease,
        cancel_event=threading.Event(),
        resume_state=WorkflowResumeState(job=job, attempts=()),
        state={
            "job": job,
            "precheck_identity": {
                **authoritative_identity,
                "input_refs": ["daily-launch/launch-fc07-missing-identity"],
                "evidence_refs": [],
            },
        },
    )

    result = production_workflow._persist_minimal_daily_report(
        workflow,
        MaintenanceController(store),
        store,
        operation="synthetic_report_author_failure",
        outcome="通常報告の作成に失敗した",
        failure_reason="synthetic_author_failure",
    )

    assert result.status == "complete"
    attempts_root = store.database_path.parent / "attempts"
    report_path = attempts_root / job.job_id / workflow.state["report_attempt"].attempt_id
    html_ref = workflow.state["report_html_path"]
    html = (attempts_root / Path(*html_ref.split("/"))).read_text(encoding="utf-8")
    assert html_ref.startswith(f"{job.job_id}/{workflow.state['report_attempt'].attempt_id}/report-fallback-")
    assert len(html) > 500
    assert "識別情報の不足: incident_id" in html
    assert "確認できていません" in html
    input_ref = workflow.state["report_result"]["report_input"]
    input_document = json.loads(
        (report_path / Path(*input_ref["path"].split("/")).name).read_text(encoding="utf-8")
    )
    assert input_document["schema_version"] == "maintenance-report-fallback-input-v1"
    assert "incident_id" not in input_document["identity"]
    assert workflow.state["report_attempt"].job_id == job.job_id


def test_fc07_completed_fallback_result_reads_exact_report_input_reference(
    tmp_path: Path, monkeypatch
):
    job_id = "job-fc07-reopen-fallback"
    attempt_id = "attempt-fc07-reopen-fallback"
    identity = {
        "incident_id": "incident-fc07-reopen-fallback",
        "source_launch_id": "launch-fc07-reopen-fallback",
        "source_run_id": None,
        "target_date": "2026-10-17",
        "job_id": job_id,
    }
    store = make_store(tmp_path)
    attempts_root = store.database_path.parent / "attempts"
    attempt_root = attempts_root / job_id / attempt_id
    attempt_root.mkdir(parents=True)
    selected = {
        "schema_version": "maintenance-report-fallback-input-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "operation": "synthetic_report_author_failure",
        "outcome": "synthetic fallback report",
    }
    decoy = {**selected, "outcome": "decoy fallback report"}
    selected_bytes = (json.dumps(selected, sort_keys=True) + "\n").encode("utf-8")
    decoy_bytes = (json.dumps(decoy, sort_keys=True) + "\n").encode("utf-8")
    selected_name = "report-input-fallback-11111111111111111111.json"
    decoy_name = "report-input-fallback-00000000000000000000.json"
    (attempt_root / selected_name).write_bytes(selected_bytes)
    (attempt_root / decoy_name).write_bytes(decoy_bytes)
    result_document = {
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": "report-fallback-synthetic",
        "report_origin": "controller_fallback",
        "report_input": {
            "path": f"{job_id}/{attempt_id}/{selected_name}",
            "sha256": hashlib.sha256(selected_bytes).hexdigest(),
        },
    }
    selected_reads: list[str] = []
    original_read_json = production_workflow._daily_report_read_json

    def read_result_or_artifact(**kwargs):
        if kwargs["relative_path"] == f"{job_id}/{attempt_id}/result.json":
            return result_document
        selected_reads.append(kwargs["relative_path"])
        return original_read_json(**kwargs)

    monkeypatch.setattr(production_workflow, "_daily_report_read_json", read_result_or_artifact)
    resolved = production_workflow._daily_report_attempt_input(
        store=store,
        attempts_root=attempts_root,
        attempt_id=attempt_id,
        job_id=job_id,
        report_input_ref=result_document["report_input"],
    )
    assert resolved == selected
    assert selected_reads == [f"{job_id}/{attempt_id}/{selected_name}"]
    assert resolved["identity"] == identity
    assert resolved["attempt_id"] == attempt_id
    assert resolved["schema_version"] == "maintenance-report-fallback-input-v1"


def test_fc07_reopening_completed_fallback_uses_result_bound_input(
    tmp_path: Path, monkeypatch
):
    job_id = "job-fc07-resume-bound-input"
    attempt_id = "attempt-fc07-resume-bound-input"
    identity = {
        "incident_id": "incident-fc07-resume-bound-input",
        "source_launch_id": "launch-fc07-resume-bound-input",
        "source_run_id": None,
        "target_date": "2026-10-17",
        "job_id": job_id,
    }
    store = make_store(tmp_path)
    attempts_root = store.database_path.parent / "attempts"
    attempt_root = attempts_root / job_id / attempt_id
    attempt_root.mkdir(parents=True)
    selected = {
        "schema_version": "maintenance-report-fallback-input-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "operation": "synthetic_report_author_failure",
        "outcome": "the exact completed fallback report input",
    }
    selected_bytes = (json.dumps(selected, sort_keys=True) + "\n").encode("utf-8")
    selected_name = "report-input-fallback-22222222222222222222.json"
    (attempt_root / selected_name).write_bytes(selected_bytes)
    decoy = {**selected, "outcome": "must not be loaded when the completed result points elsewhere"}
    decoy_name = "report-input-fallback-11111111111111111111.json"
    (attempt_root / decoy_name).write_text(
        json.dumps(decoy, sort_keys=True) + "\n", encoding="utf-8"
    )
    result_document = {
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": "report-fallback-resume-v1",
        "report_origin": "controller_fallback",
        "report_input": {
            "path": f"{job_id}/{attempt_id}/{selected_name}",
            "sha256": hashlib.sha256(selected_bytes).hexdigest(),
        },
    }
    job = SimpleNamespace(job_id=job_id, incident_id=identity["incident_id"], status="complete")
    attempt = SimpleNamespace(
        job_id=job_id,
        attempt_id=attempt_id,
        step_key="maintenance_report",
        sequence=4,
        attempt_no=1,
        status="complete",
        result_version=result_document["result_version"],
        output_path=f"{job_id}/{attempt_id}/result.json",
        output_sha256="a" * 64,
    )
    workflow = SimpleNamespace(
        resume_state=SimpleNamespace(job=job, attempts=(attempt,)),
        launch=SimpleNamespace(
            source_launch_id=identity["source_launch_id"],
            source_run_id=None,
            target_date=identity["target_date"],
        ),
        state={},
    )
    monkeypatch.setattr(
        store,
        "get_job_input_manifest",
        lambda **_kwargs: SimpleNamespace(identity=lambda: dict(identity)),
    )
    original_read_json = production_workflow._daily_report_read_json

    def read_result_and_input(**kwargs):
        if kwargs["relative_path"] == attempt.output_path:
            return result_document
        return original_read_json(**kwargs)

    monkeypatch.setattr(production_workflow, "_daily_report_read_json", read_result_and_input)
    original_input = production_workflow._daily_report_attempt_input
    references_seen: list[object] = []

    class InputLoaded(Exception):
        def __init__(self, document):
            self.document = document

    def capture_result_reference(**kwargs):
        references_seen.append(kwargs.get("report_input_ref"))
        document = original_input(**kwargs)
        raise InputLoaded(document)

    monkeypatch.setattr(
        production_workflow, "_daily_report_attempt_input", capture_result_reference
    )
    with pytest.raises(InputLoaded) as caught:
        production_workflow._restore_completed_daily_report_context(workflow, store)

    assert references_seen == [result_document["report_input"]]
    assert caught.value.document == selected
    assert caught.value.document["identity"] == identity
    assert caught.value.document["attempt_id"] == attempt_id
    assert caught.value.document["schema_version"] == "maintenance-report-fallback-input-v1"


@pytest.mark.parametrize("review_status", ["rejected", "changes_requested"])
def test_fc07_completed_fallback_preserves_nonapproved_review_for_pending_publish(
    tmp_path: Path, monkeypatch, review_status: str
):
    job_id = f"job-fc07-pending-review-{review_status}"
    attempt_id = f"attempt-fc07-pending-review-{review_status}"
    identity = {
        "incident_id": f"incident-fc07-pending-review-{review_status}",
        "source_launch_id": f"launch-fc07-pending-review-{review_status}",
        "source_run_id": None,
        "target_date": "2026-10-17",
        "job_id": job_id,
    }
    storage_identity = dict(identity)
    store = make_store(tmp_path)
    attempts_root = store.database_path.parent / "attempts"
    attempt_root = attempts_root / job_id / attempt_id
    attempt_root.mkdir(parents=True)
    html_bytes = (
        "<!doctype html><html lang='ja'><meta charset='utf-8'>"
        "<title>合成の未承認報告</title><body><main>原因は確認中です。"
        "次に同じjobの記録を確認します。</main></body></html>\n"
    ).encode("utf-8")
    html_sha256 = hashlib.sha256(html_bytes).hexdigest()
    html_name = f"report-fallback-{html_sha256[:20]}.html"
    html_path = f"{job_id}/{attempt_id}/{html_name}"
    (attempt_root / html_name).write_bytes(html_bytes)
    input_document = {
        "schema_version": "maintenance-report-fallback-input-v1",
        "identity": identity,
        "storage_identity": storage_identity,
        "attempt_id": attempt_id,
        "operation": "synthetic_report_author_failure",
        "outcome": "the reviewer did not approve the report",
        "failure_reason": "synthetic_author_failure",
        "input_refs": [],
        "evidence_refs": [],
    }
    input_bytes = (json.dumps(input_document, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    input_name = "report-input-fallback-33333333333333333333.json"
    input_path = f"{job_id}/{attempt_id}/{input_name}"
    (attempt_root / input_name).write_bytes(input_bytes)
    result_version = f"fallback-{review_status}-r1"
    review_document = {
        "schema_version": "maintenance-report-review-v1",
        "reviewer_id": production_workflow._REPORT_REVIEWER_ROLE,
        "reviewed_version": result_version,
        "reviewed_sha256": html_sha256,
        "status": review_status,
        "reviewed_at": "2026-10-20T01:00:00+09:00",
        "findings": (
            [{"severity": "high", "location": "本文", "finding": "説明を確認してください。"}]
            if review_status == "rejected"
            else []
        ),
    }
    result_document = {
        "schema_version": "maintenance-report-result-v1",
        "identity": storage_identity,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "report_status": "pending",
        "stage_status": "reviewed",
        "review": review_document,
        "output": {"path": html_path, "sha256": html_sha256},
        "source_versions": {
            "investigation": "unknown",
            "plan": "unknown",
            "implementation": "a" * 40,
            "tests": "unknown",
            "deployment_status": "not_attempted",
            "next_day_status": "not_observed",
        },
        "next_action": "publish",
        "report_origin": "controller_fallback",
        "report_input": {"path": input_path, "sha256": hashlib.sha256(input_bytes).hexdigest()},
    }
    result_bytes = (json.dumps(result_document, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    result_path = f"{job_id}/{attempt_id}/result.json"
    (attempt_root / "result.json").write_bytes(result_bytes)
    job = SimpleNamespace(job_id=job_id, incident_id=identity["incident_id"], status="complete")
    attempt = SimpleNamespace(
        job_id=job_id,
        attempt_id=attempt_id,
        step_key="maintenance_report",
        sequence=4,
        attempt_no=1,
        status="complete",
        result_version=result_version,
        output_path=result_path,
        output_sha256=hashlib.sha256(result_bytes).hexdigest(),
    )
    workflow = SimpleNamespace(
        resume_state=SimpleNamespace(job=job, attempts=(attempt,)),
        launch=SimpleNamespace(
            source_launch_id=identity["source_launch_id"],
            source_run_id=None,
            target_date=identity["target_date"],
        ),
        state={},
    )
    monkeypatch.setattr(
        store,
        "get_job_input_manifest",
        lambda **_kwargs: SimpleNamespace(identity=lambda: dict(identity)),
    )
    monkeypatch.setattr(
        store,
        "get_step_attempt_review",
        lambda **_kwargs: {
            "reviewer_id": production_workflow._REPORT_REVIEWER_ROLE,
            "reviewed_version": result_version,
            "status": review_status,
        },
    )

    restored = production_workflow._restore_completed_daily_report_context(workflow, store)

    assert restored.status == "complete"
    assert workflow.state["report_result"]["report_status"] == "pending"
    assert workflow.state["report_result"]["stage_status"] == "reviewed"
    assert workflow.state["report_result"]["next_action"] == "publish"
    assert workflow.state["report_review"]["status"] == review_status
    assert workflow.state["report_review"]["status"] != "approved"
    assert workflow.state["report_attempt"].job_id == job_id
    assert workflow.state["report_attempt"].attempt_id == attempt_id
    assert workflow.state["report_html_path"] == html_path
