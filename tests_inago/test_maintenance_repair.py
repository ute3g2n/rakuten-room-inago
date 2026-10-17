"""U14 synthetic repair-stage contracts (C33-C37)."""

from __future__ import annotations

from importlib import import_module, util
from typing import Any

import pytest


CASE_NODES = {
    "C33": "tests_inago/test_maintenance_repair.py::test_c33_contract",
    "C34": "tests_inago/test_maintenance_repair.py::test_c34_contract",
    "C35": "tests_inago/test_maintenance_repair.py::test_c35_contract",
    "C36": "tests_inago/test_maintenance_repair.py::test_c36_contract",
    "C37": "tests_inago/test_maintenance_repair.py::test_c37_contract",
}
BASE_SHA = "a" * 64
FIXED_SHA = "b" * 64


def _require_repair_api() -> tuple[Any, Any, Any, Any, Any]:
    """Fail in the test body when the repair implementation is not present yet."""

    module_name = "automation.inago.maintenance.repair"
    spec = util.find_spec(module_name)
    assert spec is not None, "U14 repair-stage behavior is not implemented"
    module = import_module(module_name)
    controller_type = getattr(module, "RepairController", None)
    ledger_type = getattr(module, "RepairTestLedger", None)
    evidence_type = getattr(module, "TestRunEvidence", None)
    review_type = getattr(module, "RepairReview", None)
    error_type = getattr(module, "RepairContractError", None)
    assert all(
        item is not None
        for item in (
            controller_type,
            ledger_type,
            evidence_type,
            review_type,
            error_type,
        )
    ), "U14 repair-stage contract API is incomplete"
    return controller_type, ledger_type, evidence_type, review_type, error_type


def _evidence(
    evidence_type,
    *,
    node: str,
    outcome: str,
    exit_code: int,
    collected: int,
    assertion_failures: int,
    errors: int,
    code_sha256: str = BASE_SHA,
    semantic_case_id: str = "C34",
):
    return evidence_type(
        node=node,
        outcome=outcome,
        exit_code=exit_code,
        collected=collected,
        assertion_failures=assertion_failures,
        errors=errors,
        code_sha256=code_sha256,
        semantic_case_id=semantic_case_id,
    )


def test_c33_contract():
    controller_type, _, _, _, _ = _require_repair_api()
    controller = controller_type(
        identity_resolver=lambda identity: "read_only_reviewer"
    )

    owned_patch = controller.assess_patch(
        changed_paths=("automation/inago/maintenance/fix.py",),
        owned_paths=("automation/inago/maintenance/fix.py",),
        baseline_assertions={
            "tests_inago/test_fix.py": {"blocked-state": "status == 'blocked'"}
        },
        candidate_assertions={
            "tests_inago/test_fix.py": {"blocked-state": "status == 'blocked'"}
        },
    )
    assert owned_patch.accepted is True

    weakened_assertion = controller.assess_patch(
        changed_paths=(
            "automation/inago/maintenance/fix.py",
            "tests_inago/test_fix.py",
        ),
        owned_paths=("automation/inago/maintenance/fix.py", "tests_inago/test_fix.py"),
        baseline_assertions={
            "tests_inago/test_fix.py": {"blocked-state": "status == 'blocked'"}
        },
        candidate_assertions={
            "tests_inago/test_fix.py": {"blocked-state": "status != 'open'"}
        },
    )
    assert weakened_assertion.accepted is False

    unowned_patch = controller.assess_patch(
        changed_paths=("automation/inago/maintenance/unowned.py",),
        owned_paths=("automation/inago/maintenance/fix.py",),
        baseline_assertions={},
        candidate_assertions={},
    )
    assert unowned_patch.accepted is False


def test_c34_contract():
    controller_type, _, evidence_type, _, _ = _require_repair_api()
    controller = controller_type(
        identity_resolver=lambda identity: "read_only_reviewer"
    )

    def require_blocked(status: str) -> None:
        assert status == "blocked"

    with pytest.raises(AssertionError):
        require_blocked("open")
    red = _evidence(
        evidence_type,
        node=CASE_NODES["C34"],
        outcome="FAIL",
        exit_code=1,
        collected=1,
        assertion_failures=1,
        errors=0,
    )
    assert controller.classify_test_evidence(red) == "meaningful_red"

    environment_error = _evidence(
        evidence_type,
        node=CASE_NODES["C34"],
        outcome="ERROR",
        exit_code=2,
        collected=0,
        assertion_failures=0,
        errors=1,
    )
    assert controller.classify_test_evidence(environment_error) == "environment_error"

    require_blocked("blocked")
    fixed = _evidence(
        evidence_type,
        node=CASE_NODES["C34"],
        outcome="PASS",
        exit_code=0,
        collected=1,
        assertion_failures=0,
        errors=0,
        code_sha256=FIXED_SHA,
    )
    assert controller.classify_test_evidence(fixed) == "passed"


def test_c35_contract():
    _, ledger_type, _, _, error_type = _require_repair_api()
    cases = {
        "semantic-pass": "tests_inago/test_fix.py::test_passed",
        "semantic-fail": "tests_inago/test_fix.py::test_failed",
        "semantic-not-run": "tests_inago/test_fix.py::test_not_run",
        "semantic-new": "tests_inago/test_fix.py::test_new",
    }
    ledger = ledger_type(
        campaign_id="campaign-c35", required_cases=cases, code_sha256=BASE_SHA
    )
    ledger.record_result("semantic-pass", outcome="PASS", code_sha256=BASE_SHA)
    ledger.record_result("semantic-fail", outcome="FAIL", code_sha256=BASE_SHA)
    ledger.record_result("semantic-not-run", outcome="NOT_RUN", code_sha256=BASE_SHA)

    assert set(ledger.select_retry_nodes()) == {
        cases["semantic-fail"],
        cases["semantic-not-run"],
        cases["semantic-new"],
    }
    with pytest.raises(error_type):
        ledger.add_case("semantic-pass", "tests_inago/test_fix.py::test_passed_renamed")

    token = ledger.begin_full_regression()
    with pytest.raises(error_type):
        ledger.begin_full_regression(nested=True)
    with pytest.raises(error_type):
        ledger.begin_full_regression()
    assert token

    for invalid_status in ("SKIP", "XFAIL", "ERROR", "UNVERIFIED"):
        invalid_status_ledger = ledger_type(
            campaign_id=f"campaign-c35-{invalid_status.lower()}",
            required_cases=cases,
            code_sha256=BASE_SHA,
        )
        invalid_status_ledger.record_result(
            "semantic-pass", outcome=invalid_status, code_sha256=BASE_SHA
        )
        with pytest.raises(error_type):
            invalid_status_ledger.select_retry_nodes()


def test_c36_contract():
    _, ledger_type, _, _, error_type = _require_repair_api()
    cases = {"existing-case": "tests_inago/test_fix.py::test_existing"}
    ledger = ledger_type(
        campaign_id="campaign-c36", required_cases=cases, code_sha256=BASE_SHA
    )
    token = ledger.begin_full_regression()
    ledger.record_full_regression(
        token,
        code_sha256=BASE_SHA,
        results={"existing-case": "PASS"},
    )

    assert ledger.completion_status(current_code_sha256=BASE_SHA) == "ready"
    assert ledger.completion_status(current_code_sha256=FIXED_SHA) == "hold"

    ledger.add_case("new-semantic-case", "tests_inago/test_fix.py::test_new_behavior")
    assert ledger.select_retry_nodes() == (
        "tests_inago/test_fix.py::test_new_behavior",
    )
    with pytest.raises(error_type):
        ledger.add_case(
            "new-semantic-case", "tests_inago/test_fix.py::test_new_behavior_renamed"
        )
    assert ledger.completion_status(current_code_sha256=FIXED_SHA) == "hold"


def test_c37_contract():
    controller_type, _, _, review_type, _ = _require_repair_api()
    host_roles = {
        "implementation-author": "implementer",
        "independent-reviewer": "read_only_reviewer",
        "writer-reviewer": "reviewer_with_write_access",
    }
    controller = controller_type(
        identity_resolver=lambda identity: host_roles[identity]
    )

    def review(
        *,
        reviewer_id: str,
        reviewed_commit: str = FIXED_SHA,
        reviewed_result_version: str = "result-v2",
        findings=(),
    ):
        return review_type(
            author_id="implementation-author",
            reviewer_id=reviewer_id,
            tested_commit=FIXED_SHA,
            reviewed_commit=reviewed_commit,
            tested_result_version="result-v2",
            reviewed_result_version=reviewed_result_version,
            unresolved_critical_findings=tuple(findings),
        )

    assert (
        controller.validate_review(review(reviewer_id="independent-reviewer")) is True
    )
    assert (
        controller.validate_review(review(reviewer_id="implementation-author")) is False
    )
    assert controller.validate_review(review(reviewer_id="writer-reviewer")) is False
    assert (
        controller.validate_review(
            review(reviewer_id="independent-reviewer", reviewed_commit=BASE_SHA)
        )
        is False
    )
    assert (
        controller.validate_review(
            review(
                reviewer_id="independent-reviewer", reviewed_result_version="result-v1"
            )
        )
        is False
    )
    assert (
        controller.validate_review(
            review(
                reviewer_id="independent-reviewer", findings=("critical: unsafe diff",)
            )
        )
        is False
    )
