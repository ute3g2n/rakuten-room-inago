"""U11 deterministic daily outcome preclassification contracts."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from automation.inago.maintenance.detector import preclassify_daily_outcome
from tests_inago.maintenance_detector_support import (
    CHECKED_AT,
    LAUNCH_ID,
    TARGET_DATE,
    clone_case,
)


def _classify(completion, daily_result, manifest):
    return preclassify_daily_outcome(
        expected_target_date=TARGET_DATE,
        expected_source_launch_id=LAUNCH_ID,
        completion=completion,
        daily_result=daily_result,
        evidence_manifest=manifest,
        checked_at=CHECKED_AT,
    )


def _assert_result_schema(result):
    schema_path = (
        Path(__file__).parents[1]
        / "automation"
        / "inago"
        / "schemas"
        / "maintenance-check-result.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)


def test_c20_contract():
    completion, daily_result, manifest = clone_case(status="shortfall")

    result = _classify(completion, daily_result, manifest)

    assert result["preclassification"] == "candidate_shortfall"
    assert result["check_status"] == "normal_shortfall"
    assert result["counts"]["daily_target"] == 15
    assert result["counts"]["supplemental_allocation_count"] == 3
    assert result["counts"]["committed_count"] == 12
    assert result["counts"]["shortfall_count"] == 3
    assert (
        result["counts"]["daily_target"]
        != 15 + result["counts"]["supplemental_allocation_count"]
    )
    assert result["routing"] == {
        "next_action": "check_only",
        "auto_dispatch": False,
        "steps_started": [],
        "incident_write_allowed": False,
    }
    _assert_result_schema(result)


def test_c21_contract():
    completion, daily_result, manifest = clone_case(status="complete")
    daily_result["retry_summary"] = {
        "attempt_count": 1,
        "recovered_count": 1,
        "unresolved_count": 0,
        "contract_violation_count": 0,
    }

    recovered = _classify(completion, daily_result, manifest)

    assert recovered["preclassification"] == "recovered_retry"
    assert recovered["check_status"] == "normal"
    assert recovered["retry_summary"]["recovered_count"] == 1
    assert recovered["routing"]["steps_started"] == []

    completion2, daily_result2, manifest2 = clone_case(status="complete")
    daily_result2["retry_summary"] = {
        "attempt_count": 1,
        "recovered_count": 1,
        "unresolved_count": 0,
        "contract_violation_count": 1,
    }
    violation = _classify(completion2, daily_result2, manifest2)

    assert violation["preclassification"] == "contract_violation"
    assert violation["check_status"] == "abnormal"
    assert violation["reason_code"] == "retry_contract_violation"
    assert violation["routing"]["steps_started"] == []
    _assert_result_schema(recovered)
    _assert_result_schema(violation)


def test_c22_contract():
    completion, daily_result, manifest = clone_case(status="complete")
    daily_result.pop("committed_count")
    daily_result["phase_results"]["metrics"]["status"] = "unknown"
    completion["trace_status"] = "degraded"
    daily_result["phase_results"]["trace"].update(
        {"status": "degraded", "event_count": 2}
    )
    manifest["classes"] = [
        {
            **entry,
            "status": "not_listed" if entry["class_id"] == "L02" else entry["status"],
            "artifact_count": (
                0 if entry["class_id"] == "L02" else entry["artifact_count"]
            ),
            "record_count": (
                None if entry["class_id"] == "L02" else entry["record_count"]
            ),
        }
        for entry in manifest["classes"]
    ]
    manifest["classes"] = [
        {
            **entry,
            "status": "expired" if entry["class_id"] == "M03" else entry["status"],
        }
        for entry in manifest["classes"]
    ]

    result = _classify(completion, daily_result, manifest)

    assert result["preclassification"] == "unknown"
    assert result["check_status"] == "unknown"
    assert result["counts"]["committed_count"] is None
    assert result["phase_results"]["metrics"] == "unknown"
    assert {("L02", "not_listed"), ("M03", "expired")} <= {
        (item["class_id"], item["status"])
        for item in result["evidence"]["unavailable_classes"]
    }
    assert result["routing"]["next_action"] == "check_only"
    _assert_result_schema(result)

    # A malformed manifest stays unknown with its specific failure reason.
    valid_completion, valid_daily, invalid_manifest = clone_case(status="complete")
    invalid_manifest["classes"] = []
    malformed = _classify(valid_completion, valid_daily, invalid_manifest)
    assert malformed["preclassification"] == "unknown"
    assert malformed["reason_code"] == "manifest_invalid"
    assert malformed["evidence"]["status"] == "invalid"
    _assert_result_schema(malformed)

    for non_object_manifest in ([], "bad"):
        valid_completion, valid_daily, _manifest = clone_case(status="complete")
        invalid_root = _classify(valid_completion, valid_daily, non_object_manifest)
        assert invalid_root["preclassification"] == "unknown"
        assert invalid_root["reason_code"] == "manifest_invalid"
        assert invalid_root["evidence"]["status"] == "invalid"
        _assert_result_schema(invalid_root)

    for missing_field in ("captured_at", "artifacts", "events", "redaction"):
        valid_completion, valid_daily, incomplete_manifest = clone_case(
            status="complete"
        )
        incomplete_manifest.pop(missing_field)
        incomplete = _classify(valid_completion, valid_daily, incomplete_manifest)
        assert incomplete["preclassification"] == "unknown"
        assert incomplete["reason_code"] == "manifest_invalid"
        assert incomplete["evidence"]["status"] == "invalid"
        _assert_result_schema(incomplete)

    valid_completion, valid_daily, incomplete_class = clone_case(status="complete")
    incomplete_class["classes"][0].pop("artifact_count")
    missing_class_count = _classify(valid_completion, valid_daily, incomplete_class)
    assert missing_class_count["preclassification"] == "unknown"
    assert missing_class_count["reason_code"] == "manifest_invalid"
    assert missing_class_count["evidence"]["status"] == "invalid"
    _assert_result_schema(missing_class_count)

    valid_completion, valid_daily, boolean_version_manifest = clone_case(
        status="complete"
    )
    boolean_version_manifest["schema_version"] = True
    boolean_version = _classify(valid_completion, valid_daily, boolean_version_manifest)
    assert boolean_version["preclassification"] == "unknown"
    assert boolean_version["reason_code"] == "manifest_invalid"
    assert boolean_version["evidence"]["status"] == "invalid"
    _assert_result_schema(boolean_version)

    # The nested daily phase must agree with the top-level daily status.
    valid_completion, inconsistent_daily, valid_manifest = clone_case(status="complete")
    inconsistent_daily["phase_results"]["daily"]["status"] = "partial"
    inconsistent = _classify(valid_completion, inconsistent_daily, valid_manifest)
    assert inconsistent["preclassification"] == "contract_violation"
    assert inconsistent["reason_code"] == "status_mismatch"
    _assert_result_schema(inconsistent)

    valid_completion, malformed_daily, valid_manifest = clone_case(status="complete")
    malformed_daily["phase_results"]["daily"]["status"] = "unexpected_status"
    malformed_phase = _classify(valid_completion, malformed_daily, valid_manifest)
    assert malformed_phase["preclassification"] == "contract_violation"
    assert malformed_phase["reason_code"] == "status_mismatch"
    _assert_result_schema(malformed_phase)

    malformed_status_cases = (
        (
            "daily_schema",
            lambda completion, daily: daily.update({"schema_version": []}),
        ),
        (
            "overall_status",
            lambda completion, daily: daily.update({"overall_status": {}}),
        ),
        ("daily_status", lambda completion, daily: daily.update({"daily_status": []})),
        (
            "daily_phase_status",
            lambda completion, daily: daily["phase_results"]["daily"].update(
                {"status": []}
            ),
        ),
        (
            "completion_status",
            lambda completion, daily: completion.update({"status": []}),
        ),
    )
    for _name, corrupt in malformed_status_cases:
        valid_completion, malformed_daily, valid_manifest = clone_case(
            status="complete"
        )
        corrupt(valid_completion, malformed_daily)
        malformed_status = _classify(valid_completion, malformed_daily, valid_manifest)
        assert malformed_status["preclassification"] == "contract_violation"
        assert malformed_status["reason_code"] == "status_mismatch"
        _assert_result_schema(malformed_status)

    # Missing daily trace data is incomplete evidence, not a contradiction
    # with the completion summary. Keep a known external wait visible.
    missing_completion, _missing_result, _missing_manifest = clone_case(
        status="failed", run_id=None
    )
    missing_completion["trace_status"] = "not_written"
    missing_completion["reason_code"] = "transport_error"
    external = preclassify_daily_outcome(
        expected_target_date=TARGET_DATE,
        expected_source_launch_id=LAUNCH_ID,
        completion=missing_completion,
        daily_result=None,
        evidence_manifest=None,
        checked_at=CHECKED_AT,
    )
    assert external["preclassification"] == "external_wait"
    assert external["reason_code"] == "external_dependency_wait"
    _assert_result_schema(external)

    contradictory = dict(missing_completion)
    contradictory.update(
        {
            "status": "unknown",
            "overall_status": "failed",
            "daily_status": "failed",
        }
    )
    mismatch = preclassify_daily_outcome(
        expected_target_date=TARGET_DATE,
        expected_source_launch_id=LAUNCH_ID,
        completion=contradictory,
        daily_result=None,
        evidence_manifest=None,
        checked_at=CHECKED_AT,
    )
    assert mismatch["preclassification"] == "contract_violation"
    assert mismatch["reason_code"] == "status_mismatch"
    _assert_result_schema(mismatch)


def test_c19_contract():
    from automation.inago.maintenance.investigation import run_check_stage
    from tests_inago.maintenance_investigation_support import (
        make_check_response,
        make_context,
    )

    cases = (
        ("normal_candidate", "normal", "checked_ok", None, 1),
        ("candidate_shortfall", "normal", "checked_ok", None, 1),
        ("recovered_retry", "normal", "checked_ok", None, 1),
        (
            "needs_investigation",
            "needs_investigation",
            "investigation_required",
            "investigation",
            1,
        ),
        # A machine/AI mismatch is routed to investigation, never accepted as
        # a normal completion.
        (
            "needs_investigation",
            "normal",
            "investigation_required",
            "investigation",
            1,
        ),
        (
            "needs_investigation",
            "waiting_external",
            "investigation_required",
            "investigation",
            1,
        ),
        ("external_wait", "waiting_external", "waiting_external", None, 1),
        # Machine-observed external waits remain waiting even if the model
        # reports normal. Unknown or contradictory machine results never call AI.
        ("external_wait", "normal", "waiting_external", None, 1),
        ("unknown", "waiting_external", "blocked", None, 0),
        ("contract_violation", "waiting_external", "blocked", None, 0),
    )

    for index, (
        preclassification,
        assessment,
        status,
        next_stage,
        expected_calls,
    ) in enumerate(cases):
        context = make_context(
            incident_id=f"incident-c19-{index:02d}",
            job_id=f"job-c19-{index:02d}",
            attempt_id=f"attempt-c19-{index:02d}",
        )
        calls: list[dict] = []

        def check_runner(job):
            calls.append(job)
            response = make_check_response(context, assessment=assessment)
            expected_reason = {
                "candidate_shortfall": "daily_target_unmet",
                "recovered_retry": "daily_recovered_retry",
            }.get(preclassification)
            if expected_reason is not None:
                response["reason_code"] = expected_reason
            return response

        result = run_check_stage(context, preclassification, check_runner)

        assert len(calls) == expected_calls
        if calls:
            assert calls[0]["stage"] == "check"
            assert all(calls[0][key] == value for key, value in context.items())
        assert result.status == status
        assert result.next_stage == next_stage
        assert result.started_steps == (("check",) if calls else ())
        assert result.incident_write_allowed is False

    context = make_context()

    def mutate_identity(job):
        job["source_launch_id"] = "launch-mutated"
        return make_check_response(job, assessment="normal")

    mutated = run_check_stage(context, "normal_candidate", mutate_identity)
    assert mutated.status == "blocked"
    assert mutated.reason_code == "check_response_invalid"

    context = make_context()

    def contradictory_reason(job):
        response = make_check_response(job, assessment="normal")
        response["reason_code"] = "daily_failure_observed"
        return response

    mismatch = run_check_stage(context, "normal_candidate", contradictory_reason)
    assert mismatch.status == "blocked"
    assert mismatch.reason_code == "check_response_invalid"
