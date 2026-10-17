"""U13 synthetic planning and run-null logging contracts."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago.maintenance.controller import (
    InvestigationAttemptRequest,
    MaintenanceController,
    PlanningAttemptRequest,
)
from automation.inago.maintenance.planning import (
    PlanningContractError,
    _validate_planning_context,
)
from automation.inago.maintenance.store import MaintenanceStoreError
from tests_inago.maintenance_investigation_support import (
    BASE_COMMIT,
    make_context as make_investigation_context,
    make_investigation_response,
    make_review_response as make_investigation_review,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)


PLAN_VERSION = "plan-v1"
INVESTIGATION_VERSION = "investigation-v1"
WORK_ROOT = Path(__file__).resolve().parents[1]
PLAN_DOCUMENT = "plans/projects/maintenance/文書整理と日次自動不具合対応_実行計画_2026-10-17.md"
REQUIREMENTS = ["MR-009", "MR-011", "MR-016"]
CASE_NODES = {
    "C31": "tests_inago/test_maintenance_planning.py::test_c31_contract",
    "C32": "tests_inago/test_maintenance_planning.py::test_c32_contract",
}
STEP_FIELDS = (
    "identity",
    "purpose",
    "requirements",
    "predecessors",
    "inputs",
    "ownership",
    "procedure",
    "outputs",
    "constraints",
    "review",
    "acceptance_evidence",
    "failure_recovery",
    "handoff",
    "full_prompt",
)
CASE_FIELDS = (
    "requirement_step",
    "purpose",
    "kind_method",
    "preconditions",
    "inputs_operations",
    "execution_configuration",
    "expected_output",
    "expected_state_logs",
    "forbidden_side_effects",
    "pass_criteria",
    "evidence",
    "cleanup_retry",
)
PROMPT_LABELS = (
    "作業ルート:",
    "計画文書:",
    "対象STEP:",
    "入力・版:",
    "開始条件:",
    "作業内容:",
    "所有ファイル:",
    "変更禁止:",
    "検証:",
    "合格条件:",
    "停止・再開:",
    "証拠:",
    "Git:",
    "次工程:",
)


def _make_plan_response(context, *, status: str = "ready"):
    prompt_details = {
        "作業ルート:": context["work_root"],
        "計画文書:": context["plan_document"],
        "対象STEP:": "STEP 1 unknown cause verification",
        "入力・版:": "investigation-v1, fact-001, "
        + BASE_COMMIT
        + ", input/daily-result-001, input/evidence-manifest-001, "
        + "evidence/trace-001, evidence/daily-result-001",
        "開始条件:": "approved investigation-v1 is current and handoff accepted",
        "作業内容:": "verify evidence read-only; do not assume the unknown cause",
        "所有ファイル:": "automation/inago/maintenance/planning.py",
        "変更禁止:": "production database, credentials, user postings, and files outside owned scope",
        "検証:": "run C31 and C32 synthetic cases and inspect result hashes",
        "合格条件:": "all requirements, facts, cases, and prompt fields are covered",
        "停止・再開:": "hold on missing evidence and resume with the same investigation version",
        "証拠:": "save plan, map, review, result, and command evidence in this attempt",
        "Git:": "stage only owned files; commit and push the current branch after review",
        "次工程:": "hand off to U14 only after independent review approval",
    }
    full_prompt = "\n".join(
        f"{label} {prompt_details[label]}" for label in PROMPT_LABELS
    )
    step = {
        "identity": {
            "number": 1,
            "name": "unknown cause verification",
            "status": "planned",
        },
        "purpose": "Verify the unknown cause before planning any repair.",
        "requirements": list(REQUIREMENTS),
        "predecessors": ["approved investigation-v1"],
        "inputs": ["fact-001", "evidence/trace-001", BASE_COMMIT],
        "ownership": ["automation/inago/maintenance/planning.py"],
        "procedure": "Collect read-only evidence; keep the cause unresolved until verified.",
        "outputs": ["verification report"],
        "constraints": [
            "Do not implement a cause-dependent fix while cause_confidence is unknown."
        ],
        "review": ["Independent reviewer checks evidence and ownership."],
        "acceptance_evidence": ["C31 evidence and reviewer decision for plan-v1."],
        "failure_recovery": [
            "Hold and state the missing evidence; resume with the same investigation version."
        ],
        "handoff": ["Continue only after the plan is approved."],
        "full_prompt": full_prompt,
    }
    cases = []
    for case_id, node in CASE_NODES.items():
        case = {field: f"Synthetic {case_id} {field}." for field in CASE_FIELDS}
        case.update(
            {
                "case_id": case_id,
                "node": node,
                "requirement_step": [
                    {"requirement_id": requirement, "step_number": 1}
                    for requirement in list(REQUIREMENTS)
                ],
            }
        )
        cases.append(case)
    return {
        "schema_version": "maintenance-planning-response-v1",
        "stage": "planning",
        "status": status,
        "summary": "Plan is evidence-bound and unknown-cause safe.",
        "sections": [
            {
                "number": number,
                "title": f"Section {number}",
                "content": f"Section {number} content.",
            }
            for number in range(15)
        ],
        "steps": [step],
        "cases": cases,
        "coverage": [
            {
                "requirement_id": requirement,
                "fact_ids": ["fact-001"],
                "evidence_refs": ["evidence/trace-001"],
                "step_numbers": [1],
                "case_ids": ["C31", "C32"],
            }
            for requirement in REQUIREMENTS
        ],
        "cause_actions": [
            {
                "fact_id": "fact-001",
                "action": "verify_only",
                "basis": "Cause confidence is unknown.",
            }
        ],
        "logging_contract": {
            "source_run_id": None,
            "overall_status": "failed",
            "daily_status": "failed",
            "reason_code": "preflight_failed",
            "failure_phase": "preflight",
            "trace_status": "not_written",
            "missing_reason": "source_run_not_saved",
            "field_bindings": [
                {
                    "field": field,
                    "schema_ref": f"#/properties/logging_contract/properties/{field}",
                    "source_ref": (
                        "automation/inago/maintenance/planning.py::expected_missing_reason"
                        if field == "missing_reason"
                        else f"automation/inago/maintenance/daily_launcher.py::{field}"
                    ),
                    "case_id": "C32",
                }
                for field in (
                    "source_run_id",
                    "overall_status",
                    "daily_status",
                    "reason_code",
                    "failure_phase",
                    "trace_status",
                    "missing_reason",
                )
            ],
        },
        "hold_conditions": [
            "Wait for external evidence before any cause-dependent repair."
        ]
        if status != "ready"
        else [],
        "final_explanation": "Cause remains unknown; no repair is planned before verification."
        if status != "ready"
        else "The synthetic plan is ready for independent review.",
        "review_required": True,
    }


def _start_planning_attempt(
    tmp_path,
    *,
    investigation_version=INVESTIGATION_VERSION,
    investigation_evidence_refs=None,
):
    store = make_store(tmp_path / "store")
    controller = MaintenanceController(store)
    incident_id = create_incident(store)
    job = create_ready_release(
        store,
        incident_id=incident_id,
        base_commit=BASE_COMMIT,
    )
    assert job.started and job.job_id is not None
    daily_summary = {
        "schema_version": "daily-launch-completion-v1",
        "source_launch_id": "launch-1",
        "target_date": "2026-10-17",
        "status": "failed",
        "exit_code": 3,
        "source_run_id": None,
        "overall_status": "failed",
        "daily_status": "failed",
        "reason_code": "preflight_failed",
        "failure_phase": "preflight",
        "failed_checks": [],
        "primary_error_class": None,
        "secondary_error_classes": [],
        "trace_status": "not_written",
    }
    daily_record = SimpleNamespace(
        source_launch_id="launch-1",
        target_date="2026-10-17",
        status="failed",
        source_run_id=None,
        exit_code=3,
        result_json=json.dumps(
            daily_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )
    store.get_daily_launch = lambda launch_id: (
        daily_record if launch_id == "launch-1" else None
    )
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
    investigation_attempt = controller.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=7),
    )
    investigation_context = make_investigation_context(
        incident_id=incident_id,
        source_launch_id="launch-1",
        source_run_id=None,
        target_date="2026-10-17",
        job_id=job.job_id,
        attempt_id=investigation_attempt.attempt_id,
        base_commit=BASE_COMMIT,
    )
    attempts_root = tmp_path / "attempts"
    investigation_output = attempts_root / job.job_id / investigation_attempt.attempt_id
    investigation_output.mkdir(parents=True)
    handoff_root = attempts_root
    investigation_response = make_investigation_response(investigation_context)
    if investigation_evidence_refs is not None:
        investigation_response["evidence_refs"] = investigation_evidence_refs
    controller.complete_investigation_attempt(
        InvestigationAttemptRequest(
            context=investigation_context,
            response=investigation_response,
            attempts_root=attempts_root,
            output_root=investigation_output,
            result_version=investigation_version,
            created_at=BASE_TIME + timedelta(seconds=8),
            completed_at=BASE_TIME + timedelta(seconds=9),
            review_started_at=BASE_TIME + timedelta(seconds=10),
            reviewed_at=BASE_TIME + timedelta(seconds=11),
            accepted_at=BASE_TIME + timedelta(seconds=12),
            lease=lease,
            author_id="implementation-agent",
            author_execution_id="investigation-turn-1",
            reviewer_id="investigation-reviewer",
            reviewer_execution_id="investigation-review-turn-1",
            review_runner=lambda _: make_investigation_review(
                investigation_context,
                reviewer_id="investigation-reviewer",
                review_execution_id="investigation-review-turn-1",
                reviewed_version=investigation_version,
                status="approved",
            ),
            review_round=1,
            handoff_root=handoff_root,
            synthetic_fixture=True,
        )
    )
    plan_attempt = controller.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_planning",
        sequence=2,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=13),
    )
    plan_output = attempts_root / job.job_id / plan_attempt.attempt_id
    plan_output.mkdir(parents=True)
    plan_context = {
        **investigation_context,
        "attempt_id": plan_attempt.attempt_id,
        "investigation_attempt_id": investigation_attempt.attempt_id,
        "investigation_result_version": investigation_version,
        "requirements": REQUIREMENTS,
        "case_manifest": [
            {"case_id": case_id, "node": node} for case_id, node in CASE_NODES.items()
        ],
        "work_root": str(WORK_ROOT),
        "plan_document": PLAN_DOCUMENT,
    }
    return (
        store,
        controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        plan_attempt,
    )


def _complete_plan(
    controller,
    lease,
    context,
    attempts_root,
    output_root,
    response,
    *,
    review_status="approved",
    review_input_observer=None,
    review_identity=None,
):
    def review_runner(review_input):
        if review_input_observer is not None:
            review_input_observer(review_input)
        return {
            "schema_version": "maintenance-planning-review-v1",
            "stage": "planning_review",
            "reviewer_id": "planner-reviewer",
            "review_execution_id": "planner-review-turn-1",
            "reviewed_version": PLAN_VERSION,
            "status": review_status,
            "findings": []
            if review_status == "approved"
            else ["External evidence is still missing."],
        }

    review_values = {
        "author_id": "implementation-agent",
        "author_execution_id": "planner-turn-1",
        "reviewer_id": "planner-reviewer",
        "reviewer_execution_id": "planner-review-turn-1",
    }
    if review_identity:
        review_values.update(review_identity)
    return controller.complete_planning_attempt(
        PlanningAttemptRequest(
            context=context,
            response=response,
            attempts_root=attempts_root,
            output_root=output_root,
            result_version=PLAN_VERSION,
            created_at=BASE_TIME + timedelta(seconds=14),
            completed_at=BASE_TIME + timedelta(seconds=15),
            review_started_at=BASE_TIME + timedelta(seconds=16),
            reviewed_at=BASE_TIME + timedelta(seconds=17),
            lease=lease,
            **review_values,
            review_runner=review_runner,
            review_round=1,
        )
    )


def test_c31_contract(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        attempt,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)
    review_inputs = []

    outcome = _complete_plan(
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        response,
        review_input_observer=review_inputs.append,
    )

    assert outcome.review_status == "approved"
    assert outcome.plan_status == "ready"
    assert set(outcome.artifacts) == {
        "plan.md",
        "requirement-step-case-map.json",
        "result.json",
        "review.json",
        "review.md",
    }
    plan = (output_root / "plan.md").read_text(encoding="utf-8")
    assert all(f"## {number}." in plan for number in range(15))
    assert "作業ルート:" in plan
    assert "Unknown-cause handling" in plan
    mapping = json.loads(
        (output_root / "requirement-step-case-map.json").read_text(encoding="utf-8")
    )
    assert {item["requirement_id"] for item in mapping["coverage"]} == set(REQUIREMENTS)
    assert all(
        {item["requirement_id"] for item in case["requirement_step"]}
        == set(REQUIREMENTS)
        for case in mapping["case_nodes"]
    )
    result = json.loads((output_root / "result.json").read_text(encoding="utf-8"))
    assert result["investigation"]["result_version"] == INVESTIGATION_VERSION
    assert result["review"]["reviewed_version"] == PLAN_VERSION
    assert result["source_run_id"] is None
    assert result["daily_completion_sha256"]
    assert len(review_inputs) == 1
    review_input = review_inputs[0]
    assert review_input["investigation_facts_path"].endswith("/facts.json")
    assert review_input["investigation_facts_sha256"]
    assert review_input["investigation_facts"]["facts"]
    assert review_input["daily_completion"]["source_launch_id"] == "launch-1"
    assert review_input["daily_completion_sha256"] == result["daily_completion_sha256"]
    persisted = store.get_step_attempt(attempt.attempt_id)
    assert persisted.status == "complete"
    assert persisted.output_sha256 == outcome.output_sha256


def test_c31_accepts_investigation_evidence_subset(tmp_path):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(
        tmp_path, investigation_evidence_refs=["evidence/trace-001"]
    )

    outcome = _complete_plan(
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _make_plan_response(context),
    )

    assert outcome.review_status == "approved"
    result = json.loads((output_root / "result.json").read_text(encoding="utf-8"))
    assert result["investigation"]["evidence_refs"] == ["evidence/trace-001"]


def test_c31_rejects_untrusted_investigation_fact_types(tmp_path):
    (_, _, _, context, _, _, _) = _start_planning_attempt(tmp_path)
    malformed_fact = {
        "fact_id": "fact-001",
        "cause_confidence": [],
        "evidence_refs": ["evidence/trace-001"],
    }

    with pytest.raises(
        PlanningContractError, match="planning_investigation_facts_invalid"
    ):
        _validate_planning_context(context, [malformed_fact])


def test_c31_rejects_invalid_review_identity_before_callback(tmp_path):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)

    with pytest.raises(PlanningContractError, match="planning_review_identity_invalid"):
        _complete_plan(
            controller,
            lease,
            context,
            attempts_root,
            output_root,
            response,
            review_identity={"reviewer_execution_id": []},
        )

    assert not any(output_root.iterdir())


def test_c31_rejects_malformed_saved_trace_status(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    daily_record = store.get_daily_launch("launch-1")
    summary = json.loads(daily_record.result_json)
    summary["trace_status"] = []
    daily_record.result_json = json.dumps(summary)

    with pytest.raises(
        PlanningContractError, match="planning_daily_completion_invalid"
    ):
        _complete_plan(
            controller,
            lease,
            context,
            attempts_root,
            output_root,
            _make_plan_response(context),
        )

    assert not any(output_root.iterdir())


@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (lambda value: value["sections"].pop(), "planning_sections_incomplete"),
        (
            lambda value: value["steps"][0].pop("full_prompt"),
            "planning_response_invalid",
        ),
        (
            lambda value: value["steps"][0].update(full_prompt="... 上記を参照"),
            "planning_response_invalid",
        ),
        (
            lambda value: value["cause_actions"][0].update(action="repair_verified"),
            "planning_unknown_cause_assumed",
        ),
        (
            lambda value: value["cases"][0].update(
                node="tests_inago/test_missing.py::test_fake"
            ),
            "planning_case_manifest_mismatch",
        ),
        (lambda value: value["coverage"].pop(), "planning_coverage_incomplete"),
    ],
)
def test_c31_rejects_incomplete_or_unsafe_plan(tmp_path, mutate, error_code):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)
    mutate(response)

    with pytest.raises(PlanningContractError, match=error_code):
        _complete_plan(controller, lease, context, attempts_root, output_root, response)

    assert not any(output_root.iterdir())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["sections"][1].update(number=True),
        lambda value: value["cause_actions"][0].update(fact_id=[]),
        lambda value: value["cause_actions"][0].update(action=[]),
    ],
)
def test_c31_rejects_malformed_scalar_types(tmp_path, mutate):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)
    mutate(response)

    with pytest.raises(PlanningContractError):
        _complete_plan(controller, lease, context, attempts_root, output_root, response)

    assert not any(output_root.iterdir())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["steps"][0]["requirements"].remove("MR-011"),
        lambda value: value["cases"][0]["requirement_step"].remove(
            {"requirement_id": "MR-011", "step_number": 1}
        ),
    ],
)
def test_c31_rejects_contradictory_requirement_step_case_edges(tmp_path, mutate):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)
    mutate(response)

    with pytest.raises(PlanningContractError, match="planning_coverage_incomplete"):
        _complete_plan(controller, lease, context, attempts_root, output_root, response)

    assert not any(output_root.iterdir())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("overall_status", "complete"),
        ("daily_status", "partial"),
        ("reason_code", "invented_reason"),
        ("failure_phase", "secret exception text"),
        ("trace_status", "healthy"),
        ("missing_reason", "raw exception text"),
    ],
)
def test_c32_rejects_unbound_or_unsafe_logging_values(tmp_path, field, value):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)
    response["logging_contract"][field] = value

    with pytest.raises(PlanningContractError):
        _complete_plan(controller, lease, context, attempts_root, output_root, response)

    assert not any(output_root.iterdir())


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("work_root", "C:/outside", "planning_workspace_root_invalid"),
        (
            "plan_document",
            "../../outside/plan.md",
            "planning_plan_document_invalid",
        ),
    ],
)
def test_c31_rejects_untrusted_workspace_paths(tmp_path, field, value, error_code):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    context[field] = value

    with pytest.raises(PlanningContractError, match=error_code):
        _complete_plan(
            controller,
            lease,
            context,
            attempts_root,
            output_root,
            _make_plan_response(context),
        )

    assert not any(output_root.iterdir())


def test_c31_waiting_external_is_reviewed_but_cannot_release_next_step(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        attempt,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context, status="waiting_external")

    outcome = _complete_plan(
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        response,
        review_status="approved",
    )

    assert outcome.plan_status == "waiting_external"
    assert outcome.review_status == "approved"
    assert outcome.gate_status == "changes_requested"
    assert outcome.next_action == "wait_external"
    assert (output_root / "plan.md").exists()
    with pytest.raises(MaintenanceStoreError, match="step_predecessor_not_approved"):
        store.begin_step_attempt(
            job_id=context["job_id"],
            step_key="maintenance_repair",
            sequence=3,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=18),
        )
    retry = store.begin_step_attempt(
        job_id=context["job_id"],
        step_key="maintenance_planning",
        sequence=2,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=18),
    )
    assert retry.attempt_no == 2
    assert store.get_step_attempt(attempt.attempt_id).status == "complete"


@pytest.mark.parametrize(
    ("plan_status", "review_status", "expected_action"),
    [
        ("waiting_external", "changes_requested", "revise"),
        ("waiting_external", "rejected", "blocked"),
        ("waiting_external", "unavailable", "blocked"),
        ("blocked", "approved", "blocked"),
    ],
)
def test_c31_next_action_respects_review_gate(
    tmp_path, plan_status, review_status, expected_action
):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context, status=plan_status)

    outcome = _complete_plan(
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        response,
        review_status=review_status,
    )

    assert outcome.next_action == expected_action


def test_c31_rejects_stale_investigation_result_before_writing(tmp_path):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(
        tmp_path,
        investigation_version="investigation-v2",
    )
    context["investigation_result_version"] = INVESTIGATION_VERSION

    with pytest.raises(
        PlanningContractError, match="planning_investigation_version_mismatch"
    ):
        _complete_plan(
            controller,
            lease,
            context,
            attempts_root,
            output_root,
            _make_plan_response(context),
        )

    assert not any(output_root.iterdir())


def test_c32_contract(tmp_path):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)

    outcome = _complete_plan(
        controller, lease, context, attempts_root, output_root, response
    )

    result = json.loads((output_root / "result.json").read_text(encoding="utf-8"))
    logging = result["logging_contract"]
    assert result["source_run_id"] is None
    assert logging["source_run_id"] is None
    assert logging["failure_phase"] == "preflight"
    assert logging["trace_status"] == "not_written"
    assert logging["missing_reason"]
    assert outcome.review_status == "approved"


@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (
            lambda value: value["logging_contract"].update(
                source_run_id="invented-run"
            ),
            "planning_source_run_id_mismatch",
        ),
        (
            lambda value: value["logging_contract"].update(missing_reason="none"),
            "planning_logging_value_mismatch",
        ),
        (
            lambda value: value["logging_contract"]["field_bindings"][0].update(
                source_ref="not-the-source"
            ),
            "planning_logging_binding_invalid",
        ),
        (
            lambda value: value["logging_contract"]["field_bindings"].pop(),
            "planning_response_invalid",
        ),
        (
            lambda value: value["logging_contract"].update(
                failure_phase="some_other_phase"
            ),
            "planning_logging_value_mismatch",
        ),
    ],
)
def test_c32_rejects_lost_run_null_or_unbound_missing_explanation(
    tmp_path, mutate, error_code
):
    (
        _,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
        _,
    ) = _start_planning_attempt(tmp_path)
    response = _make_plan_response(context)
    mutate(response)

    with pytest.raises(PlanningContractError, match=error_code):
        _complete_plan(controller, lease, context, attempts_root, output_root, response)

    assert not any(output_root.iterdir())
