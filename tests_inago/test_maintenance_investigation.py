"""U12 synthetic investigation and independent review contracts."""

from __future__ import annotations

import json
import sqlite3
import re
from datetime import timedelta

import pytest

from automation.inago.maintenance.controller import (
    InvestigationAttemptRequest,
    MaintenanceController,
)
from automation.inago.maintenance.store import MaintenanceStoreError
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)
from tests_inago.maintenance_investigation_support import (
    make_context,
    make_investigation_response,
    make_review_response,
)


def _start_attempt(tmp_path):
    store = make_store(tmp_path / "store")
    controller = MaintenanceController(store)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    assert job.started and job.job_id is not None
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=4101,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=6),
        ttl_seconds=300,
    )
    controller.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id="launch-1",
        source_run_id=None,
        target_date="2026-10-17",
        job_id=job.job_id,
        created_at=BASE_TIME + timedelta(seconds=6),
    )
    attempt = controller.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=7),
    )
    context = make_context(
        incident_id=incident_id,
        source_launch_id="launch-1",
        source_run_id=None,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
    )
    attempts_root = tmp_path / "attempts"
    output_root = attempts_root / job.job_id / attempt.attempt_id
    output_root.mkdir(parents=True)
    return store, controller, lease, context, attempts_root, output_root


def _handoff_count(store):
    with sqlite3.connect(store.database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM handoff_artifact").fetchone()[0]


def _complete_investigation(controller, **kwargs):
    return controller.complete_investigation_attempt(
        InvestigationAttemptRequest(**kwargs)
    )


def test_c19_check_record(tmp_path):
    store = make_store(tmp_path)
    controller = MaintenanceController(store)
    context = make_context(source_launch_id="launch-check-normal")
    calls = []

    def check_runner(job):
        calls.append(job)
        return {
            "schema_version": "maintenance-check-response-v1",
            **{key: value for key, value in job.items() if key != "stage"},
            "stage": "check",
            "assessment": "normal",
            "reason_code": "daily_complete",
            "summary": "The synthetic normal check completed.",
        }

    outcome = controller.run_daily_check_stage(
        context=context,
        preclassification="normal_candidate",
        check_runner=check_runner,
        observed_at=BASE_TIME + timedelta(minutes=1),
    )
    assert outcome.stage.status == "checked_ok"
    assert outcome.record.created is True
    assert len(calls) == 1
    assert store.counts()["daily_check"] == 1

    mismatched_reason = controller.run_daily_check_stage(
        context=make_context(source_launch_id="launch-check-reason-mismatch"),
        preclassification="normal_candidate",
        check_runner=lambda job: {
            **{key: value for key, value in job.items() if key != "stage"},
            "schema_version": "maintenance-check-response-v1",
            "stage": "check",
            "assessment": "normal",
            "reason_code": "daily_target_unmet",
            "summary": "Synthetic mismatched reason code.",
        },
        observed_at=BASE_TIME + timedelta(minutes=2),
    )
    assert mismatched_reason.stage.status == "blocked"
    assert mismatched_reason.record.created is True
    assert mismatched_reason.stage.reason_code == (
        "check_preclassification_reason_mismatch"
    )

    failure_context = make_context(source_launch_id="launch-check-failure")
    failure_runner = lambda job: {
        **{key: value for key, value in job.items() if key != "stage"},
        "schema_version": "maintenance-check-response-v1",
        "stage": "check",
        "assessment": "needs_investigation",
        "reason_code": "daily_failure_observed",
        "summary": "Synthetic failure observation.",
    }
    with pytest.raises(ValueError, match="daily_check_incident_required"):
        controller.run_daily_check_stage(
            context=failure_context,
            preclassification="needs_investigation",
            check_runner=failure_runner,
            observed_at=BASE_TIME + timedelta(minutes=2, seconds=1),
        )
    failure_outcome = controller.run_daily_check_stage(
        context=failure_context,
        preclassification="needs_investigation",
        check_runner=failure_runner,
        observed_at=BASE_TIME + timedelta(minutes=2, seconds=2),
        incident={
            "dedupe_key": "daily:synthetic-component:synthetic-failure",
            "component": "daily_pipeline",
            "event_code": "daily_error",
        },
    )
    assert failure_outcome.record.incident_id is not None
    assert failure_outcome.record.created is True

    unknown_context = make_context(source_launch_id="launch-check-unknown")
    no_model_calls = []
    unknown = controller.run_daily_check_stage(
        context=unknown_context,
        preclassification="unknown",
        check_runner=lambda job: no_model_calls.append(job),
        observed_at=BASE_TIME + timedelta(minutes=3),
    )
    assert unknown.stage.status == "blocked"
    assert no_model_calls == []
    assert store.counts()["daily_check"] == 4


def test_c29_contract(tmp_path):
    from automation.inago.maintenance.investigation import (
        render_investigation_markdown,
        validate_investigation_response,
    )

    context = make_context()
    response = make_investigation_response(context)

    validated = validate_investigation_response(response, context)
    markdown = render_investigation_markdown(
        validated,
        context,
        result_version="investigation-v1",
        created_at="2026-10-17T22:30:00+09:00",
        synthetic_fixture=True,
    )

    assert isinstance(markdown, str)
    chapter_numbers = re.findall(r"^## (\d+)\.", markdown, flags=re.MULTILINE)
    assert chapter_numbers == [str(number) for number in range(1, 10)]
    assert context["target_date"] in markdown
    assert "investigation-v1" in markdown
    assert "unknown" in markdown
    assert "未確認" in markdown
    assert "外部側停止が原因" not in markdown
    assert "合成fixtureから生成した検証用レポート" in markdown
    saved_markdown = tmp_path / "investigation.md"
    saved_markdown.write_text(markdown, encoding="utf-8")
    assert saved_markdown.read_text(encoding="utf-8") == markdown

    real_mode = render_investigation_markdown(
        validated,
        context,
        result_version="investigation-v1",
        created_at="2026-10-17T22:30:00+09:00",
    )
    assert "合成fixtureから生成した検証用レポート" not in real_mode

    # Every fact keeps its full details in the report, including facts beyond
    # the first three event slots.
    four_facts = make_investigation_response(context)
    fourth = dict(four_facts["facts"][0])
    fourth.update(
        {
            "fact_id": "fact-004",
            "operation": "Fourth synthetic operation",
            "phase": "metrics",
        }
    )
    four_facts["facts"].extend(
        [dict(fourth, fact_id="fact-002"), dict(fourth, fact_id="fact-003"), fourth]
    )
    four_report = render_investigation_markdown(
        four_facts,
        context,
        result_version="investigation-v2",
        created_at="2026-10-17T22:30:00+09:00",
        synthetic_fixture=True,
    )
    assert "fact-004" in four_report
    assert "Fourth synthetic operation" in four_report

    # A cause stated as confirmed without allowed supporting evidence is not
    # a valid investigation response.
    unsupported_cause = make_investigation_response(
        context,
        cause_confidence="confirmed",
        cause_statement="外部側停止が原因",
        fact_evidence_refs=["evidence/not-allowed"],
    )
    with pytest.raises(ValueError):
        validate_investigation_response(unsupported_cause, context)

    # Even an otherwise valid response cannot cite another target date or
    # turn the nullable source run ID into a concrete run.
    identity_mismatches = (
        ("incident_id", "incident-other"),
        ("source_launch_id", "launch-other"),
        ("source_run_id", "run-not-in-context"),
        ("target_date", "2026-10-16"),
        ("job_id", "job-other"),
        ("attempt_id", "attempt-other"),
        ("base_commit", "c" * 40),
        ("input_refs", ["input/unapproved"]),
        ("evidence_refs", ["evidence/unapproved"]),
    )
    for field, value in identity_mismatches:
        mismatched_identity = make_investigation_response(context)
        mismatched_identity[field] = value
        with pytest.raises(ValueError):
            validate_investigation_response(mismatched_identity, context)

    wrong_fact_date = make_investigation_response(context)
    wrong_fact_date["facts"][0]["target_date"] = "2026-10-16"
    with pytest.raises(ValueError):
        validate_investigation_response(wrong_fact_date, context)

    wrong_fact_ref = make_investigation_response(
        context, fact_evidence_refs=["evidence/unapproved"]
    )
    with pytest.raises(ValueError):
        validate_investigation_response(wrong_fact_ref, context)

    unknown_with_claim = make_investigation_response(
        context,
        cause_confidence="unknown",
        cause_statement="外部側停止が原因",
    )
    with pytest.raises(ValueError):
        validate_investigation_response(unknown_with_claim, context)

    for field, value in (
        ("job_id", "CONIN$"),
        ("attempt_id", "attempt:stream"),
        ("job_id", "COM¹"),
    ):
        unsafe_path_identity = make_investigation_response(context)
        unsafe_path_identity[field] = value
        with pytest.raises(ValueError):
            validate_investigation_response(unsafe_path_identity, unsafe_path_identity)

    missing_review = make_investigation_response(context)
    missing_review["review_required"] = False
    with pytest.raises(ValueError):
        validate_investigation_response(missing_review, context)

    missing_schema_version = make_investigation_response(context)
    missing_schema_version.pop("schema_version")
    with pytest.raises(ValueError):
        validate_investigation_response(missing_schema_version, context)

    # A blocked investigation still saves the facts and nine-chapter report.
    store, controller, lease, context, attempts_root, output_root = _start_attempt(
        tmp_path / "blocked"
    )
    blocked_response = make_investigation_response(context, status="blocked")
    blocked_review = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        reviewed_version="investigation-blocked-v1",
        status="approved",
    )
    blocked_outcome = _complete_investigation(
        controller,
        context=context,
        response=blocked_response,
        attempts_root=attempts_root,
        output_root=output_root,
        result_version="investigation-blocked-v1",
        created_at=BASE_TIME + timedelta(seconds=8),
        completed_at=BASE_TIME + timedelta(seconds=10),
        review_started_at=BASE_TIME + timedelta(seconds=9),
        reviewed_at=BASE_TIME + timedelta(seconds=11),
        accepted_at=BASE_TIME + timedelta(seconds=12),
        lease=lease,
        author_id="implementation-agent",
        author_execution_id="investigator-turn-001",
        reviewer_id="independent-reviewer",
        reviewer_execution_id="review-turn-001",
        review_runner=lambda _metadata: blocked_review,
        review_round=1,
        handoff_root=attempts_root,
        synthetic_fixture=True,
    )
    assert blocked_outcome.investigation_status == "blocked"
    assert blocked_outcome.next_action == "blocked"
    assert blocked_outcome.handoff is None
    assert (output_root / "facts.json").is_file()
    assert (output_root / "investigation.md").is_file()
    assert (output_root / "result.json").is_file()
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "complete"
    result_document = json.loads(
        (output_root / "result.json").read_text(encoding="utf-8")
    )
    assert result_document["review"]["status"] == "approved"
    assert result_document["review"]["gate_status"] == "rejected"
    assert result_document["review"]["reason_code"] == "investigation_blocked"
    with pytest.raises(MaintenanceStoreError, match="step_predecessor_not_approved"):
        controller.begin_step_attempt(
            job_id=context["job_id"],
            step_key="change_planning",
            sequence=2,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=13),
        )
    assert _handoff_count(store) == 0


def test_c30_contract():
    from automation.inago.maintenance.investigation import (
        build_investigation_handoff_payload,
        validate_investigation_review,
    )

    context = make_context()
    author_id = "implementation-agent"
    reviewer_id = "reviewer-b"
    result_version = "investigation-v1"
    output_path = f"{context['job_id']}/{context['attempt_id']}/result.json"
    output_sha256 = "b" * 64
    facts_path = f"{context['job_id']}/{context['attempt_id']}/facts.json"
    report_path = f"{context['job_id']}/{context['attempt_id']}/investigation.md"
    facts_sha256 = "c" * 64
    report_sha256 = "d" * 64

    approved_review = make_review_response(
        context,
        reviewer_id=reviewer_id,
        reviewed_version=result_version,
        status="approved",
    )
    validated_approval = validate_investigation_review(
        approved_review,
        context,
        result_version=result_version,
        author_id=author_id,
        author_execution_id="investigator-turn-001",
        reviewer_id=reviewer_id,
        reviewer_execution_id="review-turn-001",
        review_round=1,
        max_review_rounds=3,
    )
    assert validated_approval["handoff_allowed"] is True
    assert validated_approval["next_action"] == "handoff"

    payload = build_investigation_handoff_payload(
        context,
        result_version=result_version,
        reviewed_version=result_version,
        output_path=output_path,
        output_sha256=output_sha256,
        facts_path=facts_path,
        facts_sha256=facts_sha256,
        report_path=report_path,
        report_sha256=report_sha256,
        review_status="approved",
    )
    assert payload == {
        "schema_version": "maintenance-handoff-v1",
        "incident_id": context["incident_id"],
        "job_id": context["job_id"],
        "attempt_id": context["attempt_id"],
        "source_run_id": context["source_run_id"],
        "result_version": result_version,
        "reviewed_version": result_version,
        "output": {
            "path": output_path,
            "sha256": output_sha256,
            "artifacts": {
                "facts": {"path": facts_path, "sha256": facts_sha256},
                "report": {"path": report_path, "sha256": report_sha256},
            },
        },
    }

    same_author = make_review_response(
        context,
        reviewer_id=author_id,
        reviewed_version=result_version,
        status="approved",
    )
    with pytest.raises(ValueError):
        validate_investigation_review(
            same_author,
            context,
            result_version=result_version,
            author_id=author_id,
            author_execution_id="investigator-turn-001",
            reviewer_id=author_id,
            reviewer_execution_id="review-turn-001",
            review_round=1,
            max_review_rounds=3,
        )

    old_version_review = make_review_response(
        context,
        reviewer_id=reviewer_id,
        reviewed_version="investigation-v0",
        status="approved",
    )
    with pytest.raises(ValueError):
        validate_investigation_review(
            old_version_review,
            context,
            result_version=result_version,
            author_id=author_id,
            author_execution_id="investigator-turn-001",
            reviewer_id=reviewer_id,
            reviewer_execution_id="review-turn-001",
            review_round=1,
            max_review_rounds=3,
        )
    with pytest.raises(ValueError):
        build_investigation_handoff_payload(
            context,
            result_version=result_version,
            reviewed_version="investigation-v0",
            output_path=output_path,
            output_sha256=output_sha256,
            facts_path=facts_path,
            facts_sha256=facts_sha256,
            report_path=report_path,
            report_sha256=report_sha256,
            review_status="approved",
        )

    invalid_status = make_review_response(
        context,
        reviewer_id=reviewer_id,
        reviewed_version=result_version,
        status="looks-good",
    )
    with pytest.raises(ValueError):
        validate_investigation_review(
            invalid_status,
            context,
            result_version=result_version,
            author_id=author_id,
            author_execution_id="investigator-turn-001",
            reviewer_id=reviewer_id,
            reviewer_execution_id="review-turn-001",
            review_round=1,
            max_review_rounds=3,
        )

    third_round_changes = make_review_response(
        context,
        reviewer_id=reviewer_id,
        reviewed_version="investigation-v3",
        status="changes_requested",
        findings=["The evidence does not support a causal claim."],
    )
    blocked = validate_investigation_review(
        third_round_changes,
        context,
        result_version="investigation-v3",
        author_id=author_id,
        author_execution_id="investigator-turn-001",
        reviewer_id=reviewer_id,
        reviewer_execution_id="review-turn-001",
        review_round=3,
        max_review_rounds=3,
    )
    assert blocked["handoff_allowed"] is False
    assert blocked["next_action"] == "blocked"

    same_execution = make_review_response(
        context,
        reviewer_id=reviewer_id,
        review_execution_id="investigator-turn-001",
        reviewed_version=result_version,
        status="approved",
    )
    with pytest.raises(ValueError):
        validate_investigation_review(
            same_execution,
            context,
            result_version=result_version,
            author_id=author_id,
            author_execution_id="investigator-turn-001",
            reviewer_id=reviewer_id,
            reviewer_execution_id="investigator-turn-001",
            review_round=1,
        )
    with pytest.raises(ValueError):
        build_investigation_handoff_payload(
            context,
            result_version="investigation-v3",
            reviewed_version="investigation-v3",
            output_path=output_path,
            output_sha256=output_sha256,
            facts_path=facts_path,
            facts_sha256=facts_sha256,
            report_path=report_path,
            report_sha256=report_sha256,
            review_status="changes_requested",
        )


def test_c30_controller_handoff(tmp_path):
    store, controller, lease, context, attempts_root, output_root = _start_attempt(
        tmp_path
    )
    result_version = "investigation-v1"
    reviewer_id = "independent-reviewer"
    review = make_review_response(
        context,
        reviewer_id=reviewer_id,
        reviewed_version=result_version,
        status="approved",
    )
    outcome = _complete_investigation(
        controller,
        context=context,
        response=make_investigation_response(context),
        attempts_root=attempts_root,
        output_root=output_root,
        result_version=result_version,
        created_at=BASE_TIME + timedelta(seconds=8),
        completed_at=BASE_TIME + timedelta(seconds=10),
        review_started_at=BASE_TIME + timedelta(seconds=9),
        reviewed_at=BASE_TIME + timedelta(seconds=11),
        accepted_at=BASE_TIME + timedelta(seconds=12),
        lease=lease,
        author_id="implementation-agent",
        author_execution_id="investigator-turn-001",
        reviewer_id=reviewer_id,
        reviewer_execution_id="review-turn-001",
        review_runner=lambda _metadata: review,
        review_round=1,
        handoff_root=attempts_root,
        synthetic_fixture=True,
    )
    assert outcome.next_action == "handoff"
    assert outcome.handoff is not None and outcome.handoff.accepted is True
    assert (
        store.get_step_attempt(attempt_id=context["attempt_id"]).status
        == "handoff_accepted"
    )
    assert _handoff_count(store) == 1
    result_document = json.loads(
        (output_root / "result.json").read_text(encoding="utf-8")
    )
    assert result_document["review"]["author_execution_id"] == "investigator-turn-001"
    assert result_document["review"]["review_execution_id"] == "review-turn-001"
    assert result_document["review"]["findings"] == []


def test_c30_handoff_rejects_artifact_change_after_review(tmp_path):
    store, controller, lease, context, attempts_root, output_root = _start_attempt(
        tmp_path
    )
    reviewer_id = "independent-reviewer"
    review = make_review_response(
        context,
        reviewer_id=reviewer_id,
        reviewed_version="investigation-v1",
        status="approved",
    )

    def tampering_reviewer(_metadata):
        (output_root / "facts.json").write_text("{}", encoding="utf-8")
        return review

    with pytest.raises(MaintenanceStoreError, match="handoff_facts_digest_mismatch"):
        _complete_investigation(
            controller,
            context=context,
            response=make_investigation_response(context),
            attempts_root=attempts_root,
            output_root=output_root,
            result_version="investigation-v1",
            created_at=BASE_TIME + timedelta(seconds=8),
            completed_at=BASE_TIME + timedelta(seconds=10),
            review_started_at=BASE_TIME + timedelta(seconds=9),
            reviewed_at=BASE_TIME + timedelta(seconds=11),
            accepted_at=BASE_TIME + timedelta(seconds=12),
            lease=lease,
            author_id="implementation-agent",
            author_execution_id="investigator-turn-001",
            reviewer_id=reviewer_id,
            reviewer_execution_id="review-turn-001",
            review_runner=tampering_reviewer,
            review_round=1,
            handoff_root=attempts_root,
            synthetic_fixture=True,
        )
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "complete"
    assert _handoff_count(store) == 0
    with pytest.raises(
        MaintenanceStoreError, match="step_predecessor_handoff_not_accepted"
    ):
        controller.begin_step_attempt(
            job_id=context["job_id"],
            step_key="change_planning",
            sequence=2,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=13),
        )


def test_c30_persists_review_findings_for_retry(tmp_path):
    store, controller, lease, context, attempts_root, output_root = _start_attempt(
        tmp_path
    )
    reviewer_id = "independent-reviewer"
    finding = "Connect the fact to an approved evidence reference."
    review = make_review_response(
        context,
        reviewer_id=reviewer_id,
        reviewed_version="investigation-v1",
        status="changes_requested",
        findings=[finding],
    )
    outcome = _complete_investigation(
        controller,
        context=context,
        response=make_investigation_response(context),
        attempts_root=attempts_root,
        output_root=output_root,
        result_version="investigation-v1",
        created_at=BASE_TIME + timedelta(seconds=8),
        completed_at=BASE_TIME + timedelta(seconds=10),
        review_started_at=BASE_TIME + timedelta(seconds=9),
        reviewed_at=BASE_TIME + timedelta(seconds=11),
        accepted_at=BASE_TIME + timedelta(seconds=12),
        lease=lease,
        author_id="implementation-agent",
        author_execution_id="investigator-turn-001",
        reviewer_id=reviewer_id,
        reviewer_execution_id="review-turn-001",
        review_runner=lambda _metadata: review,
        review_round=1,
        handoff_root=attempts_root,
        synthetic_fixture=True,
    )
    assert outcome.next_action == "revise"
    assert outcome.review_findings == (finding,)
    result_document = json.loads(
        (output_root / "result.json").read_text(encoding="utf-8")
    )
    assert result_document["review"]["findings"] == [finding]
    assert result_document["review"]["author_execution_id"] == "investigator-turn-001"
    retry = controller.begin_step_attempt(
        job_id=context["job_id"],
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=13),
    )
    assert retry.status == "running"
    assert retry.attempt_no == 2


def test_c30_records_safe_reviewer_failure_code(tmp_path):
    store, controller, lease, context, attempts_root, output_root = _start_attempt(
        tmp_path
    )
    outcome = _complete_investigation(
        controller,
        context=context,
        response=make_investigation_response(context),
        attempts_root=attempts_root,
        output_root=output_root,
        result_version="investigation-v1",
        created_at=BASE_TIME + timedelta(seconds=8),
        completed_at=BASE_TIME + timedelta(seconds=10),
        review_started_at=BASE_TIME + timedelta(seconds=9),
        reviewed_at=BASE_TIME + timedelta(seconds=11),
        accepted_at=BASE_TIME + timedelta(seconds=12),
        lease=lease,
        author_id="implementation-agent",
        author_execution_id="investigator-turn-001",
        reviewer_id="independent-reviewer",
        reviewer_execution_id="review-turn-001",
        review_runner=lambda _metadata: (_ for _ in ()).throw(
            RuntimeError("private reviewer response text")
        ),
        review_round=1,
        handoff_root=attempts_root,
        synthetic_fixture=True,
    )
    result_text = (output_root / "result.json").read_text(encoding="utf-8")
    result_document = json.loads(result_text)
    assert outcome.review_status == "unavailable"
    assert outcome.review_reason_code == "review_runner_failed"
    assert result_document["review"]["reason_code"] == "review_runner_failed"
    assert "private reviewer response text" not in result_text
    assert _handoff_count(store) == 0
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "complete"
    with pytest.raises(MaintenanceStoreError, match="step_predecessor_not_approved"):
        controller.begin_step_attempt(
            job_id=context["job_id"],
            step_key="change_planning",
            sequence=2,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=13),
        )


def test_c30_expired_lease_blocks_before_writing_or_reviewing(tmp_path):
    store, controller, lease, context, attempts_root, output_root = _start_attempt(
        tmp_path
    )
    reviewer_calls = []
    with pytest.raises(Exception, match="lease_token_invalid_or_expired"):
        _complete_investigation(
            controller,
            context=context,
            response=make_investigation_response(context),
            attempts_root=attempts_root,
            output_root=output_root,
            result_version="investigation-v1",
            created_at=BASE_TIME + timedelta(minutes=6),
            completed_at=BASE_TIME + timedelta(minutes=6, seconds=2),
            review_started_at=BASE_TIME + timedelta(minutes=6, seconds=1),
            reviewed_at=BASE_TIME + timedelta(minutes=6, seconds=3),
            accepted_at=BASE_TIME + timedelta(minutes=6, seconds=4),
            lease=lease,
            author_id="implementation-agent",
            author_execution_id="investigator-turn-001",
            reviewer_id="independent-reviewer",
            reviewer_execution_id="review-turn-001",
            review_runner=lambda metadata: reviewer_calls.append(metadata),
            review_round=1,
            handoff_root=attempts_root,
            synthetic_fixture=True,
        )
    assert reviewer_calls == []
    assert list(output_root.iterdir()) == []
