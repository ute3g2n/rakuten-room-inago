"""U14 synthetic repair-stage contracts (C33-C37)."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from importlib import import_module, util
import inspect
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

import pytest


CASE_NODES = {
    "C33": "tests_inago/test_maintenance_repair.py::test_c33_contract",
    "C34": "tests_inago/test_maintenance_repair.py::test_c34_contract",
    "C35": "tests_inago/test_maintenance_repair.py::test_c35_contract",
    "C36": "tests_inago/test_maintenance_repair.py::test_c36_contract",
    "C37": "tests_inago/test_maintenance_repair.py::test_c37_contract",
    "C62": "tests_inago/test_maintenance_repair.py::test_c62_repair_scope_binding",
    "C63": "tests_inago/test_maintenance_repair.py::test_c63_pinned_predecessor_contract",
    "C64": "tests_inago/test_maintenance_repair.py::test_c64_persisted_campaign_recovery",
    "C65": "tests_inago/test_maintenance_repair.py::test_c65_default_repository_resolver",
    "C66": "tests_inago/test_maintenance_repair.py::test_c66_full_regression_receipt_contract",
    "C67": "tests_inago/test_maintenance_repair.py::test_c67_runner_boundary_contract",
    "C61": "tests_inago/test_maintenance_repair.py::test_c61_frozen_case_registry_contract",
}
BASE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FIXED_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
REPAIR_SCOPE = {"owned_paths": ["automation/inago/maintenance/fix.py"]}
_TINY_GIT_CHECKOUT_TEMPS: list[tempfile.TemporaryDirectory] = []


@pytest.fixture(autouse=True)
def _cleanup_tiny_git_checkouts():
    yield
    while _TINY_GIT_CHECKOUT_TEMPS:
        _TINY_GIT_CHECKOUT_TEMPS.pop().cleanup()


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


def _require_full_regression_api():
    module = import_module("automation.inago.maintenance.repair")
    evidence_type = getattr(module, "FullRegressionEvidence", None)
    ledger_type = getattr(module, "RepairTestLedger", None)
    error_type = getattr(module, "RepairContractError", None)
    assert (
        evidence_type is not None
    ), "host-observed full-regression evidence is required"
    assert ledger_type is not None and error_type is not None
    return evidence_type, ledger_type, error_type


def _with_repair_scope(response, owned_paths=None):
    response["repair_scope"] = {
        "owned_paths": list(
            REPAIR_SCOPE["owned_paths"] if owned_paths is None else owned_paths
        )
    }
    return response


def _start_approved_repair_attempt(tmp_path):
    from tests_inago.test_maintenance_planning import (
        BASE_TIME,
        _complete_plan,
        _make_plan_response,
        _start_planning_attempt,
    )

    (
        store,
        controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        plan_attempt,
    ) = _start_planning_attempt(tmp_path)
    plan_outcome = _complete_plan(
        controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        _with_repair_scope(_make_plan_response(plan_context)),
    )
    repair_attempt = controller.begin_repair_attempt(
        job_id=plan_context["job_id"],
        attempts_root=attempts_root,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=18),
    )
    return (
        store,
        controller,
        lease,
        plan_context,
        attempts_root,
        plan_attempt,
        plan_outcome,
        repair_attempt,
    )


def _make_repair_context(plan_context, repair_attempt):
    investigation_module = import_module("automation.inago.maintenance.investigation")
    return {
        field: (
            repair_attempt.attempt_id if field == "attempt_id" else plan_context[field]
        )
        for field in investigation_module._CONTEXT_FIELDS
    }


def _make_repair_request(
    *, store, lease, plan_context, repair_attempt, attempts_root, result_version
):
    from tests_inago.test_maintenance_planning import BASE_TIME

    controller_module = import_module("automation.inago.maintenance.controller")
    request_type = getattr(controller_module, "RepairAttemptRequest")
    repair_module = import_module("automation.inago.maintenance.repair")
    review_type = getattr(repair_module, "RepairReview")
    repair_context = _make_repair_context(plan_context, repair_attempt)
    output_root = attempts_root / plan_context["job_id"] / repair_attempt.attempt_id
    output_root.mkdir(parents=True, exist_ok=True)
    review = review_type(
        author_id="repair-author",
        reviewer_id="repair-reviewer",
        tested_result_version=result_version,
        reviewed_result_version=result_version,
        tested_code_sha256=BASE_SHA,
        reviewed_code_sha256=BASE_SHA,
    )
    return request_type(
        context=repair_context,
        attempts_root=attempts_root,
        output_root=output_root,
        lease=lease,
        result_version=result_version,
        completed_at=BASE_TIME + timedelta(seconds=19),
        reviewed_at=BASE_TIME + timedelta(seconds=20),
        review=review,
    )


def _make_tiny_git_checkout(root, files):
    temp_dir = tempfile.TemporaryDirectory(prefix="u14-")
    _TINY_GIT_CHECKOUT_TEMPS.append(temp_dir)
    root = Path(temp_dir.name) / "checkout"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n*.py[cod]\n", encoding="utf-8"
    )
    for relative_path, contents in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "codex-test@example.invalid"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Codex Test"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "core.longpaths=true", "add", "--all"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "create isolated test checkout"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def _create_persisted_campaign(
    tmp_path,
    *,
    files,
    required_cases,
    semantic_signatures,
    invocation_parent_id=None,
):
    from tests_inago.test_maintenance_planning import BASE_TIME

    from automation.inago.maintenance.repair import (
        HostPytestRunner,
        RepairTestLedger,
        repository_code_sha256,
    )

    checkout_root = _make_tiny_git_checkout(tmp_path / "checkout", files)
    code_sha256 = repository_code_sha256(checkout_root)
    (
        store,
        _,
        _,
        plan_context,
        _,
        _,
        _,
        repair_attempt,
    ) = _start_approved_repair_attempt(tmp_path / "workflow")
    campaign_id = f"campaign-{uuid4().hex}"
    ledger = RepairTestLedger.create_in_store(
        store=store,
        campaign_id=campaign_id,
        job_id=plan_context["job_id"],
        attempt_id=repair_attempt.attempt_id,
        code_sha256=code_sha256,
        required_cases=required_cases,
        semantic_signatures=semantic_signatures,
        invocation_parent_id=invocation_parent_id,
        created_at=BASE_TIME + timedelta(seconds=21),
    )
    runner = HostPytestRunner(store=store, checkout_root=checkout_root)
    return store, ledger, runner, checkout_root, plan_context


def _reopen_store(store):
    from automation.inago.maintenance.store import MaintenanceStore

    data_root = store.database_path.parent.parent
    daily_database_path = data_root.parent / "daily-runtime" / "state.sqlite"
    reopened = MaintenanceStore(
        data_root,
        daily_database_path=daily_database_path,
    )
    reopened.initialize()
    return reopened


def _make_ledger(
    ledger_type,
    cases,
    *,
    campaign_id="campaign-test",
    code_sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    job_id=None,
    attempt_id=None,
):
    return ledger_type(
        campaign_id=campaign_id,
        required_cases=cases,
        semantic_signatures={case_id: f"meaning:{case_id}" for case_id in cases},
        code_sha256=code_sha256,
        invocation_parent_id=None,
        _job_id=job_id,
        _attempt_id=attempt_id,
    )


def _full_regression_evidence(
    evidence_type,
    *,
    token,
    cases,
    code_sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    results=None,
    **overrides,
):
    case_ids = (
        tuple(cases.keys())
        if hasattr(cases, "keys")
        else tuple(case_id for case_id, _ in cases)
    )
    values = {
        "run_id": "host-run-c36",
        "token": token,
        "receipt_id": "unissued-receipt",
        "code_sha256": code_sha256,
        "cases": (tuple(cases.items()) if hasattr(cases, "items") else tuple(cases)),
        "results": (
            {case_id: "PASS" for case_id in case_ids} if results is None else results
        ),
        "exit_code": 0,
        "collected": len(cases),
        "errors": 0,
        "skips": 0,
        "collection_errors": 0,
        "invocation_parent_id": None,
    }
    values.update(overrides)
    return evidence_type(**values)


def _install_fake_pytest_executor(monkeypatch, repair_module, *, result_factory=None):
    """Supply local pytest results for receipt/parser mechanics."""

    import xml.etree.ElementTree as ET

    executions = []

    def execute(argv, *, cwd, env, timeout):
        assert env.get("PYTHONOPTIMIZE") == "0"
        del timeout
        junit_index = argv.index("--junitxml")
        junit_path = Path(argv[junit_index + 1])
        requested_nodes = tuple(argv[junit_index + 2 :])
        executions.append(requested_nodes)
        if result_factory is None:
            junit_cases = tuple((node, "PASS") for node in requested_nodes)
            exit_code = 0
        else:
            junit_cases, exit_code = result_factory(requested_nodes, Path(cwd))

        failure_count = sum(status == "FAIL" for _, status in junit_cases)
        error_count = sum(status == "ERROR" for _, status in junit_cases)
        skip_count = sum(status == "SKIP" for _, status in junit_cases)
        root = ET.Element("testsuites")
        suite = ET.SubElement(
            root,
            "testsuite",
            tests=str(len(junit_cases)),
            failures=str(failure_count),
            errors=str(error_count),
            skipped=str(skip_count),
        )
        for node, status in junit_cases:
            module_path, test_name = node.split("::", 1)
            case = ET.SubElement(
                suite,
                "testcase",
                classname=module_path.removesuffix(".py").replace("/", "."),
                name=test_name,
            )
            if status == "FAIL":
                ET.SubElement(case, "failure")
            elif status == "ERROR":
                ET.SubElement(case, "error")
            elif status == "SKIP":
                ET.SubElement(case, "skipped")
        junit_path.write_bytes(
            ET.tostring(root, encoding="utf-8", xml_declaration=True)
        )
        return subprocess.CompletedProcess(argv, exit_code, stdout=b"", stderr=b"")

    monkeypatch.setattr(repair_module, "_run_local_pytest", execute)
    return executions


def _evidence(
    evidence_type,
    *,
    node: str,
    outcome: str,
    exit_code: int,
    collected: int,
    assertion_failures: int,
    errors: int,
    code_sha256: str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
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


def test_c33_contract(tmp_path):
    controller_type, ledger_type, _, review_type, error_type = _require_repair_api()
    controller_module = import_module("automation.inago.maintenance.controller")
    maintenance_controller_type = getattr(controller_module, "MaintenanceController")
    request_type = getattr(controller_module, "RepairAttemptRequest")
    investigation_module = import_module("automation.inago.maintenance.investigation")
    repair_module = import_module("automation.inago.maintenance.repair")
    host_evidence_type = getattr(repair_module, "RepairHostEvidence", None)
    assert host_evidence_type is not None, "trusted host evidence type is required"
    from tests_inago.test_maintenance_planning import (
        BASE_TIME,
        PLAN_VERSION,
        _complete_plan,
        _make_plan_response,
        _start_planning_attempt,
    )

    controller_parameters = inspect.signature(maintenance_controller_type).parameters
    assert "repair_host_evidence_resolver" in controller_parameters
    request_parameters = inspect.signature(request_type).parameters
    assert not any(name.startswith("host_") for name in request_parameters)

    # Start from a genuinely approved U13 predecessor so this exercises the
    # sequence-3 completion boundary, rather than only the patch assessor.
    (
        store,
        planning_controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        plan_attempt,
    ) = _start_planning_attempt(tmp_path)
    _complete_plan(
        planning_controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        _with_repair_scope(_make_plan_response(plan_context)),
    )
    repair_attempt = planning_controller.begin_repair_attempt(
        job_id=plan_context["job_id"],
        attempts_root=attempts_root,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=18),
    )
    predecessor_pin = store.get_repair_step_predecessor(
        attempt_id=repair_attempt.attempt_id
    )
    repair_context = {
        field: (
            repair_attempt.attempt_id if field == "attempt_id" else plan_context[field]
        )
        for field in investigation_module._CONTEXT_FIELDS
    }
    repair_output = attempts_root / plan_context["job_id"] / repair_attempt.attempt_id
    repair_output.mkdir(parents=True)
    review = review_type(
        author_id="repair-author",
        reviewer_id="repair-reviewer",
        tested_result_version="repair-v1",
        reviewed_result_version="repair-v1",
        tested_code_sha256=BASE_SHA,
        reviewed_code_sha256=BASE_SHA,
    )
    request = request_type(
        context=repair_context,
        attempts_root=attempts_root,
        output_root=repair_output,
        lease=lease,
        result_version="repair-v1",
        completed_at=BASE_TIME + timedelta(seconds=19),
        reviewed_at=BASE_TIME + timedelta(seconds=20),
        review=review,
    )

    # A caller cannot complete by supplying self-declared host_* fields, and a
    # controller without a host evidence resolver must stop closed.
    with pytest.raises(TypeError):
        request_type(
            context=repair_context,
            attempts_root=attempts_root,
            output_root=repair_output,
            lease=lease,
            result_version="repair-v1",
            completed_at=BASE_TIME + timedelta(seconds=19),
            reviewed_at=BASE_TIME + timedelta(seconds=20),
            review=review,
            host_changed_paths=("automation/inago/maintenance/fix.py",),
        )
    controller = maintenance_controller_type(
        store, identity_resolver=lambda _: "unused"
    )
    with pytest.raises(error_type):
        controller.complete_repair_attempt(request)

    received_contexts = []
    cases = {"c34": "tests_inago/test_maintenance_repair.py::test_c34_contract"}
    not_ready_ledger = _make_ledger(
        ledger_type,
        cases,
        campaign_id="host-evidence",
        job_id=repair_context["job_id"],
        attempt_id=repair_context["attempt_id"],
    )

    def trusted_resolver(evidence_context):
        received_contexts.append(evidence_context)
        return host_evidence_type(
            changed_paths=("automation/inago/maintenance/fix.py",),
            owned_paths=("automation/inago/maintenance/fix.py",),
            baseline_assertions={},
            candidate_assertions={},
            test_ledger=not_ready_ledger,
            current_code_sha256=BASE_SHA,
            test_evidence=(),
            approved_cases=cases,
            job_id=evidence_context.job_id,
            attempt_id=evidence_context.attempt_id,
            base_commit=evidence_context.base_commit,
            plan_attempt_id=predecessor_pin["attempt_id"],
            plan_result_version=predecessor_pin["result_version"],
            plan_output_sha256=predecessor_pin["output_sha256"],
            checkout_root=str(tmp_path),
        )

    trusted_controller = maintenance_controller_type(
        store,
        identity_resolver=lambda _: "unused",
        repair_host_evidence_resolver=trusted_resolver,
    )
    with pytest.raises(error_type) as held:
        trusted_controller.complete_repair_attempt(request)
    # The provider's owned diff is accepted; the empty host ledger then holds
    # completion.  No request-supplied path, owner, hash, or test result exists.
    assert held.value.code == "repair_regression_not_ready"
    assert len(received_contexts) == 1
    assert received_contexts[0].job_id == repair_context["job_id"]
    assert received_contexts[0].attempt_id == repair_context["attempt_id"]
    assert received_contexts[0].base_commit == repair_context["base_commit"]
    approved_plan = received_contexts[0].approved_plan
    assert approved_plan["schema_version"] == "maintenance-planning-result-v1"
    assert (
        approved_plan["identity"]
        == store.get_job_input_manifest(job_id=repair_context["job_id"]).identity()
    )
    assert approved_plan["attempt_id"] == plan_attempt.attempt_id
    assert approved_plan["result_version"] == PLAN_VERSION
    assert approved_plan["plan_status"] == "ready"
    assert approved_plan["plan_document"] == plan_context["plan_document"]
    assert approved_plan["output"]["plan"]["sha256"]
    assert approved_plan["review"]["status"] == "approved"

    patch_controller = controller_type(
        identity_resolver=lambda identity: "read_only_reviewer"
    )
    owned_patch = patch_controller.assess_patch(
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

    weakened_assertion = patch_controller.assess_patch(
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

    unowned_patch = patch_controller.assess_patch(
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
    full_evidence_type, ledger_type, error_type = _require_full_regression_api()
    cases = {
        "semantic-pass": "tests_inago/test_fix.py::test_passed",
        "semantic-fail": "tests_inago/test_fix.py::test_failed",
        "semantic-not-run": "tests_inago/test_fix.py::test_not_run",
        "semantic-new": "tests_inago/test_fix.py::test_new",
    }
    signatures = {case_id: f"meaning:{case_id}" for case_id in cases}
    with pytest.raises(error_type):
        ledger_type(
            campaign_id="campaign-c35-no-signatures",
            required_cases=cases,
            code_sha256=BASE_SHA,
            invocation_parent_id=None,
        )
    with pytest.raises(error_type):
        ledger_type(
            campaign_id="campaign-c35-partial-signatures",
            required_cases=cases,
            semantic_signatures={"semantic-pass": "meaning:pass"},
            code_sha256=BASE_SHA,
            invocation_parent_id=None,
        )
    with pytest.raises(error_type):
        ledger_type(
            campaign_id="campaign-c35-duplicate-signatures",
            required_cases=cases,
            semantic_signatures={case_id: "same meaning" for case_id in cases},
            code_sha256=BASE_SHA,
            invocation_parent_id=None,
        )

    ledger = ledger_type(
        campaign_id="campaign-c35",
        required_cases=cases,
        semantic_signatures=signatures,
        code_sha256=BASE_SHA,
        invocation_parent_id=None,
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
    with pytest.raises(error_type):
        ledger.add_case(
            "semantic-renamed",
            "tests_inago/test_fix.py::test_passed_renamed",
            semantic_signature=signatures["semantic-pass"],
        )
    with pytest.raises(error_type):
        ledger.add_case(
            "semantic-no-signature",
            "tests_inago/test_fix.py::test_no_signature",
        )

    token = ledger.begin_full_regression()
    with pytest.raises(error_type):
        ledger.begin_full_regression()
    with pytest.raises(error_type):
        ledger.add_case(
            "semantic-in-flight",
            "tests_inago/test_fix.py::test_in_flight",
            semantic_signature="meaning:in-flight",
        )
    assert token

    # Bare PASS mappings are caller assertions, not proof that a host runner
    # collected and executed the exact campaign once at the claimed code hash.
    bare_ledger = _make_ledger(
        ledger_type, {"one": "tests_inago/test_fix.py::test_one"}
    )
    bare_token = bare_ledger.begin_full_regression()
    with pytest.raises((error_type, TypeError)):
        bare_ledger.record_full_regression(
            bare_token,
            code_sha256=BASE_SHA,
            results={"one": "PASS"},
        )

    def assert_invalid_full_run(**evidence_overrides):
        invalid_ledger = _make_ledger(
            ledger_type,
            {"one": "tests_inago/test_fix.py::test_one"},
            campaign_id=f"campaign-invalid-{len(evidence_overrides)}-{uuid4().hex}",
        )
        invalid_token = invalid_ledger.begin_full_regression()
        evidence_arguments = {
            "token": invalid_token,
            "cases": invalid_ledger.required_cases,
            **evidence_overrides,
        }
        evidence = _full_regression_evidence(
            full_evidence_type,
            **evidence_arguments,
        )
        with pytest.raises(error_type):
            invalid_ledger.record_full_regression(evidence)

    assert_invalid_full_run(token="unrelated-run-token")
    assert_invalid_full_run(code_sha256=FIXED_SHA)
    assert_invalid_full_run(cases=(("other", "tests_inago/test_fix.py::test_other"),))
    assert_invalid_full_run(exit_code=1)
    assert_invalid_full_run(collected=0)
    assert_invalid_full_run(errors=1)
    assert_invalid_full_run(skips=1)
    assert_invalid_full_run(collection_errors=1)
    assert_invalid_full_run(invocation_parent_id="nested-parent-run")
    assert_invalid_full_run(results={"one": "FAIL"})

    nested_ledger = ledger_type(
        campaign_id="campaign-c35-nested",
        required_cases=cases,
        semantic_signatures=signatures,
        code_sha256=BASE_SHA,
        invocation_parent_id="full-regression-parent-run",
    )
    with pytest.raises(error_type):
        nested_ledger.begin_full_regression()
    with pytest.raises((AttributeError, TypeError)):
        nested_ledger.invocation_parent_id = None

    for invalid_status in ("SKIP", "XFAIL", "ERROR", "UNVERIFIED"):
        invalid_status_ledger = ledger_type(
            campaign_id=f"campaign-c35-{invalid_status.lower()}",
            required_cases=cases,
            semantic_signatures=signatures,
            code_sha256=BASE_SHA,
            invocation_parent_id=None,
        )
        invalid_status_ledger.record_result(
            "semantic-pass", outcome=invalid_status, code_sha256=BASE_SHA
        )
        with pytest.raises(error_type):
            invalid_status_ledger.select_retry_nodes()


def test_c36_contract():
    _, ledger_type, _, _, error_type = _require_repair_api()
    full_evidence_type, ledger_type, error_type = _require_full_regression_api()
    cases = {"existing-case": "tests_inago/test_fix.py::test_existing"}
    ledger = ledger_type(
        campaign_id="campaign-c36",
        required_cases=cases,
        semantic_signatures={"existing-case": "existing behavior stays blocked"},
        code_sha256=BASE_SHA,
        invocation_parent_id=None,
    )
    token = ledger.begin_full_regression()
    with pytest.raises(error_type):
        ledger.add_case(
            "in-flight-case",
            "tests_inago/test_fix.py::test_in_flight",
            semantic_signature="new in-flight behavior",
        )
    ledger.record_full_regression(
        _full_regression_evidence(
            full_evidence_type,
            token=token,
            cases=cases,
        )
    )

    assert ledger.completion_status(current_code_sha256=BASE_SHA) == "ready"
    assert ledger.completion_status(current_code_sha256=FIXED_SHA) == "hold"
    with pytest.raises(error_type):
        ledger.add_case(
            "after-consumption",
            "tests_inago/test_fix.py::test_after_consumption",
            semantic_signature="added after one-use run",
        )

    next_ledger = ledger.fork_campaign_with_case(
        campaign_id="campaign-c36-next",
        code_sha256=FIXED_SHA,
        semantic_case_id="new-semantic-case",
        node="tests_inago/test_fix.py::test_new_behavior",
        semantic_signature="a genuinely new repair behavior",
    )
    assert next_ledger.select_retry_nodes() == (
        cases["existing-case"],
        "tests_inago/test_fix.py::test_new_behavior",
    )
    assert next_ledger.frozen_cases == {}
    retried_result = next_ledger.record_result(
        "existing-case",
        outcome="FAIL",
        code_sha256=FIXED_SHA,
    )
    assert retried_result.outcome == "FAIL"
    with pytest.raises(error_type):
        next_ledger.fork_campaign_with_case(
            campaign_id="campaign-c36-duplicate-meaning",
            code_sha256=FIXED_SHA,
            semantic_case_id="renamed-same-case",
            node="tests_inago/test_fix.py::test_renamed_same_behavior",
            semantic_signature="a genuinely new repair behavior",
        )
    assert next_ledger.completion_status(current_code_sha256=FIXED_SHA) == "hold"


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
        reviewed_result_version: str = "result-v2",
        findings=(),
    ):
        return review_type(
            author_id="implementation-author",
            reviewer_id=reviewer_id,
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
    assert not hasattr(review(reviewer_id="independent-reviewer"), "tested_commit")
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


def test_c61_frozen_case_registry_contract():
    full_evidence_type, ledger_type, error_type = _require_full_regression_api()
    prior_cases = {
        "previously-passed": "tests_inago/test_fix.py::test_previous_behavior"
    }
    prior_signature = "the previously passing behavior remains protected"
    ledger = ledger_type(
        campaign_id="campaign-c61-prior",
        required_cases=prior_cases,
        semantic_signatures={"previously-passed": prior_signature},
        code_sha256=BASE_SHA,
        invocation_parent_id=None,
    )
    token = ledger.begin_full_regression()
    ledger.record_full_regression(
        _full_regression_evidence(
            full_evidence_type,
            token=token,
            cases=prior_cases,
        )
    )

    campaign = ledger.fork_campaign_with_case(
        campaign_id="campaign-c61-fork",
        code_sha256=FIXED_SHA,
        semantic_case_id="first-new-case",
        node="tests_inago/test_fix.py::test_first_new_behavior",
        semantic_signature="a distinct newly required behavior",
    )

    with pytest.raises(error_type):
        campaign.add_case(
            "previously-passed",
            "tests_inago/test_fix.py::test_relabelled_old_case",
            semantic_signature="a different claimed behavior",
        )
    with pytest.raises(error_type):
        campaign.add_case(
            "renamed-old-node",
            prior_cases["previously-passed"],
            semantic_signature="a different claimed behavior",
        )
    with pytest.raises(error_type):
        campaign.add_case(
            "renamed-old-meaning",
            "tests_inago/test_fix.py::test_relabelled_old_meaning",
            semantic_signature="  the previously passing behavior   remains protected ",
        )

    campaign.add_case(
        "genuinely-new-case",
        "tests_inago/test_fix.py::test_genuinely_new_behavior",
        semantic_signature="an additional independent repair behavior",
    )
    assert campaign.frozen_cases == {}
    assert (
        campaign.required_cases["previously-passed"] == prior_cases["previously-passed"]
    )
    assert campaign.select_retry_nodes() == (
        prior_cases["previously-passed"],
        "tests_inago/test_fix.py::test_first_new_behavior",
        "tests_inago/test_fix.py::test_genuinely_new_behavior",
    )


def test_c62_repair_scope_binding(tmp_path):
    from automation.inago.maintenance.planning import PlanningContractError
    from tests_inago.test_maintenance_planning import (
        PLAN_VERSION,
        _complete_plan,
        _make_plan_response,
        _start_planning_attempt,
    )

    invalid_scopes = (
        None,
        [],
        ["/outside/absolute.py"],
        ["C:/outside/drive.py"],
        ["automation/inago/maintenance/./fix.py"],
        ["automation/inago/../outside.py"],
        ["automation/inago/maintenance/z.py", "automation/inago/maintenance/a.py"],
        ["automation/inago/maintenance/fix.py"] * 2,
        [
            "automation/inago/maintenance",
            "automation/inago/maintenance/fix.py",
        ],
    )
    for index, invalid_scope in enumerate(invalid_scopes):
        case_root = tmp_path / f"invalid-scope-{index}"
        case_root.mkdir()
        (
            store,
            controller,
            lease,
            context,
            attempts_root,
            plan_output,
            _,
        ) = _start_planning_attempt(case_root)
        response = _make_plan_response(context)
        if invalid_scope is None:
            response.pop("repair_scope", None)
        else:
            _with_repair_scope(response, invalid_scope)
        with pytest.raises(PlanningContractError) as error:
            _complete_plan(
                controller,
                lease,
                context,
                attempts_root,
                plan_output,
                response,
            )
        assert error.value.code == "repair_scope_invalid"

    valid_root = tmp_path / "valid-scope"
    valid_root.mkdir()
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        plan_output,
        _,
    ) = _start_planning_attempt(valid_root)
    expected_scope = {"owned_paths": ["automation/inago/maintenance/fix.py"]}
    outcome = _complete_plan(
        controller,
        lease,
        context,
        attempts_root,
        plan_output,
        _with_repair_scope(_make_plan_response(context), expected_scope["owned_paths"]),
    )
    approved_result = json.loads(
        store.read_attempt_artifact(
            attempts_root=attempts_root,
            relative_path=outcome.output_path,
            expected_sha256=outcome.output_sha256,
        )
    )
    assert approved_result["result_version"] == PLAN_VERSION
    assert approved_result["repair_scope"] == expected_scope
    scope_artifact = approved_result["output"]["repair_scope"]
    assert scope_artifact["path"].endswith("repair-scope.json")
    scope_bytes = store.read_attempt_artifact(
        attempts_root=attempts_root,
        relative_path=scope_artifact["path"],
        expected_sha256=scope_artifact["sha256"],
    )
    assert hashlib.sha256(scope_bytes).hexdigest() == scope_artifact["sha256"]
    assert json.loads(scope_bytes) == {"repair_scope": expected_scope}


def test_c63_pinned_predecessor_contract(tmp_path):
    from tests_inago.test_maintenance_planning import BASE_TIME, PLAN_VERSION

    controller_module = import_module("automation.inago.maintenance.controller")
    maintenance_controller_type = getattr(controller_module, "MaintenanceController")
    store_error_type = import_module(
        "automation.inago.maintenance.store"
    ).MaintenanceStoreError
    repair_module = import_module("automation.inago.maintenance.repair")
    error_type = getattr(repair_module, "RepairContractError")

    (
        store,
        _,
        lease,
        plan_context,
        attempts_root,
        plan_attempt,
        plan_outcome,
        repair_attempt,
    ) = _start_approved_repair_attempt(tmp_path / "replacement")
    expected_pin = {
        "attempt_id": plan_attempt.attempt_id,
        "result_version": PLAN_VERSION,
        "output_sha256": plan_outcome.output_sha256,
    }
    assert (
        store.get_repair_step_predecessor(attempt_id=repair_attempt.attempt_id)
        == expected_pin
    )

    store.record_step_review(
        attempt_id=plan_attempt.attempt_id,
        reviewer_id="planner-reviewer",
        reviewed_version=PLAN_VERSION,
        status="changes_requested",
        reviewed_at=BASE_TIME + timedelta(seconds=22),
    )
    later_plan = store.begin_step_attempt(
        job_id=plan_context["job_id"],
        step_key="maintenance_planning",
        sequence=2,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=23),
    )
    assert later_plan.attempt_id != plan_attempt.attempt_id
    assert later_plan.attempt_no == 2
    store.record_step_review(
        attempt_id=plan_attempt.attempt_id,
        reviewer_id="planner-reviewer",
        reviewed_version=PLAN_VERSION,
        status="approved",
        reviewed_at=BASE_TIME + timedelta(seconds=24),
    )
    assert (
        store.get_repair_step_predecessor(attempt_id=repair_attempt.attempt_id)
        == expected_pin
    )

    observed_contexts = []

    def stop_after_loading_pinned_plan(context):
        observed_contexts.append(context)
        return None

    controller = maintenance_controller_type(
        store,
        identity_resolver=lambda _: "unused",
        repair_host_evidence_resolver=stop_after_loading_pinned_plan,
    )
    request = _make_repair_request(
        store=store,
        lease=lease,
        plan_context=plan_context,
        repair_attempt=repair_attempt,
        attempts_root=attempts_root,
        result_version="repair-c63",
    )
    with pytest.raises(error_type) as resolver_error:
        controller.complete_repair_attempt(request)
    assert resolver_error.value.code == "repair_host_evidence_invalid"
    assert len(observed_contexts) == 1
    assert observed_contexts[0].approved_plan["attempt_id"] == plan_attempt.attempt_id
    assert observed_contexts[0].approved_plan["result_version"] == PLAN_VERSION
    assert observed_contexts[0].approved_plan["repair_scope"]["owned_paths"] == tuple(
        REPAIR_SCOPE["owned_paths"]
    )

    tamper_root = tmp_path / "artifact-tamper"
    tamper_root.mkdir()
    (
        store,
        _,
        lease,
        plan_context,
        attempts_root,
        plan_attempt,
        plan_outcome,
        repair_attempt,
    ) = _start_approved_repair_attempt(tamper_root)
    artifact_path = attempts_root / plan_outcome.output_path
    artifact_path.write_bytes(artifact_path.read_bytes() + b" ")
    calls = []
    tamper_controller = maintenance_controller_type(
        store,
        identity_resolver=lambda _: "unused",
        repair_host_evidence_resolver=lambda context: calls.append(context),
    )
    tampered_request = _make_repair_request(
        store=store,
        lease=lease,
        plan_context=plan_context,
        repair_attempt=repair_attempt,
        attempts_root=attempts_root,
        result_version="repair-c63-tampered",
    )
    with pytest.raises(error_type) as tamper_error:
        tamper_controller.complete_repair_attempt(tampered_request)
    assert tamper_error.value.code == "repair_predecessor_unavailable"
    assert calls == []

    with pytest.raises(store_error_type) as missing_pin:
        store.begin_step_attempt(
            job_id=plan_context["job_id"],
            step_key="maintenance_repair",
            sequence=3,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=24),
        )
    assert missing_pin.value.code == "repair_predecessor_pin_required"


def test_c64_persisted_campaign_recovery(tmp_path, monkeypatch):
    import sqlite3

    from automation.inago.maintenance.repair import (
        HostPytestRunner,
        RepairContractError,
        RepairTestLedger,
        repository_code_sha256,
    )

    old_node = "tests_inago/test_campaign.py::test_previous_behavior"
    new_node = "tests_inago/test_campaign.py::test_new_behavior"
    files = {
        "automation/inago/maintenance/fix.py": "value = 1\n",
        "tests_inago/test_campaign.py": (
            "def test_previous_behavior():\n    assert True\n\n"
            "def test_new_behavior():\n    assert True\n"
        ),
    }
    store, ledger, runner, checkout_root, _ = _create_persisted_campaign(
        tmp_path,
        files=files,
        required_cases={"previously-passed": old_node},
        semantic_signatures={"previously-passed": "the previous behavior"},
    )
    repair_module = import_module("automation.inago.maintenance.repair")
    executions = _install_fake_pytest_executor(monkeypatch, repair_module)
    prior_receipt = runner.run_case(ledger, "previously-passed")
    ledger.record_case_receipt(prior_receipt)
    parent_snapshot = store.load_repair_campaign(campaign_id=ledger.campaign_id)
    assert any(
        result["semantic_case_id"] == "previously-passed"
        and result["outcome"] == "PASS"
        and result["receipt_id"] == prior_receipt.receipt_id
        for result in parent_snapshot["results"]
    )
    case_receipts = tuple(
        receipt
        for receipt in parent_snapshot["run_receipts"]
        if receipt["receipt_id"] == prior_receipt.receipt_id
    )
    assert len(case_receipts) == 1
    case_receipt = case_receipts[0]
    assert case_receipt["run_kind"] == "case"
    assert case_receipt["campaign_id"] == ledger.campaign_id
    assert case_receipt["attempt_id"] == parent_snapshot["attempt_id"]
    assert case_receipt["semantic_case_id"] == "previously-passed"
    assert case_receipt["cases"] == (("previously-passed", old_node),)
    assert case_receipt["results"] == {"previously-passed": "PASS"}
    assert case_receipt["code_sha256"] == prior_receipt.code_sha256
    assert case_receipt["receipt_id"] == prior_receipt.receipt_id
    assert case_receipt["consumed"] is True
    assert case_receipt["receipt_sha256"] == prior_receipt.receipt_sha256
    assert len(case_receipt["receipt_sha256"]) == 64

    (checkout_root / "automation/inago/maintenance/fix.py").write_text(
        "value = 2\n", encoding="utf-8"
    )
    next_hash = repository_code_sha256(checkout_root)
    next_ledger = ledger.fork_campaign_with_case(
        campaign_id=f"campaign-{uuid4().hex}",
        code_sha256=next_hash,
        semantic_case_id="new-case",
        node=new_node,
        semantic_signature="an additional repair behavior",
    )

    reopened_store = _reopen_store(store)
    restored = RepairTestLedger.from_store(
        store=reopened_store,
        campaign_id=next_ledger.campaign_id,
    )
    assert restored.frozen_cases == {}
    assert restored.select_retry_nodes() == (old_node, new_node)
    with pytest.raises(RepairContractError):
        restored.add_case(
            "previously-passed",
            "tests_inago/test_campaign.py::test_relabelled_previous",
            semantic_signature="a different semantic behavior",
        )
    with pytest.raises(RepairContractError):
        restored.add_case(
            "renamed-old-node",
            old_node,
            semantic_signature="a different semantic behavior",
        )
    with pytest.raises(RepairContractError):
        restored.add_case(
            "renamed-old-meaning",
            "tests_inago/test_campaign.py::test_relabelled_meaning",
            semantic_signature="  the   previous behavior ",
        )

    token = restored.begin_full_regression()
    assert token
    started_store = _reopen_store(reopened_store)
    started_ledger = RepairTestLedger.from_store(
        store=started_store,
        campaign_id=next_ledger.campaign_id,
    )
    assert started_ledger.select_retry_nodes() == (old_node, new_node)
    with pytest.raises(RepairContractError) as in_progress_replay:
        started_ledger.begin_full_regression()
    assert in_progress_replay.value.code == "full_regression_token_already_used"

    expected_full_cases = (
        ("previously-passed", old_node),
        ("new-case", new_node),
    )
    approved_cases = {
        "previously-passed": old_node,
        "new-case": new_node,
    }
    campaign_snapshot = started_store.load_repair_campaign(
        campaign_id=next_ledger.campaign_id
    )
    assert campaign_snapshot["state"] == "full_regression_started"
    assert campaign_snapshot["full_snapshot"] == expected_full_cases

    # Claim the persisted nonce, then simulate a process crash before the
    # runner can create a child or receipt. The live owner and unexpired lease
    # must both prevent recovery.
    claimed = started_store._claim_repair_runner_nonce(
        campaign_id=started_ledger.campaign_id,
        capability=started_store._repair_runner_capability,
        token=started_ledger._full_regression_token,
        nonce=started_ledger._pending_nonce,
    )
    assert claimed[2] == expected_full_cases
    with pytest.raises(RepairContractError) as unexpired_lease:
        started_ledger.abandon_interrupted_regression()
    assert unexpired_lease.value.code == "repair_runner_lease_not_expired"

    with sqlite3.connect(started_store.database_path) as connection:
        owner_pid, owner_start_id = connection.execute(
            "SELECT full_regression_owner_pid, full_regression_owner_start_id "
            "FROM repair_campaign WHERE campaign_id = ?",
            (started_ledger.campaign_id,),
        ).fetchone()
    from datetime import datetime as RealDateTime, timezone as datetime_timezone

    store_module = import_module("automation.inago.maintenance.store")
    child_pid = 2_000_000_000
    child_start_id = "u14-simulated-runner-child"
    real_process_identity = store_module._host_process_identity

    def simulated_live_process(pid):
        if pid == owner_pid:
            return owner_start_id, True
        if pid == child_pid:
            return child_start_id, True
        return real_process_identity(pid)

    with monkeypatch.context() as child_start:
        child_start.setattr(
            store_module,
            "_host_process_identity",
            simulated_live_process,
        )
        assert (
            started_store._record_repair_runner_child_start(
                campaign_id=started_ledger.campaign_id,
                child_pid=child_pid,
                capability=started_store._repair_runner_capability,
                started_at=RealDateTime.now(datetime_timezone.utc),
            )
            == child_start_id
        )

    class ExpiredLeaseDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            future = RealDateTime.now(tz or datetime_timezone.utc) + timedelta(
                seconds=601
            )
            return cls.fromtimestamp(future.timestamp(), tz or datetime_timezone.utc)

    with monkeypatch.context() as crash_window:
        crash_window.setattr(store_module, "datetime", ExpiredLeaseDateTime)
        crash_window.setattr(repair_module, "datetime", ExpiredLeaseDateTime)
        with pytest.raises(RepairContractError) as live_owner:
            started_ledger.abandon_interrupted_regression()
        assert live_owner.value.code == "repair_runner_owner_still_alive"

        def owner_dead_child_alive(pid):
            if pid == owner_pid:
                return owner_start_id, False
            if pid == child_pid:
                return child_start_id, True
            return None

        crash_window.setattr(
            store_module, "_host_process_identity", owner_dead_child_alive
        )
        with pytest.raises(RepairContractError) as live_child:
            started_ledger.abandon_interrupted_regression()
        assert live_child.value.code == "repair_runner_child_still_alive"

        def dead_owner_and_child(pid):
            if pid == owner_pid:
                return owner_start_id, False
            if pid == child_pid:
                return child_start_id, False
            return None

        crash_window.setattr(
            store_module, "_host_process_identity", dead_owner_and_child
        )
        started_ledger.abandon_interrupted_regression()

    failed_store = _reopen_store(started_store)
    failed_snapshot = failed_store.load_repair_campaign(
        campaign_id=started_ledger.campaign_id
    )
    assert failed_snapshot["state"] == "full_regression_failed"
    assert failed_snapshot["full_regression_failure_code"] == "runner_interrupted"
    assert failed_snapshot["run_receipts"] == ()

    recovered_ledger = started_ledger.fork_after_failed_regression(
        campaign_id=f"campaign-{uuid4().hex}",
        code_sha256=next_hash,
    )
    final_store = _reopen_store(failed_store)
    final_ledger = RepairTestLedger.from_store(
        store=final_store,
        campaign_id=recovered_ledger.campaign_id,
    )
    assert final_ledger.frozen_cases == {}
    assert final_ledger.select_retry_nodes() == (old_node, new_node)
    final_ledger.begin_full_regression()
    runner_after_recovery = HostPytestRunner(
        store=final_store,
        checkout_root=checkout_root,
    )
    evidence = runner_after_recovery.run_full_regression(final_ledger)
    assert evidence.cases == expected_full_cases
    assert executions == [(old_node,), (old_node, new_node)]
    final_ledger.record_full_regression(evidence)

    final_store = _reopen_store(started_store)
    final_ledger = RepairTestLedger.from_store(
        store=final_store,
        campaign_id=recovered_ledger.campaign_id,
    )
    assert final_ledger.frozen_cases["previously-passed"].node == old_node
    frozen_receipt = final_ledger.frozen_pass_receipts["previously-passed"]
    assert frozen_receipt["code_sha256"] == next_hash
    assert frozen_receipt["code_sha256"] != prior_receipt.code_sha256
    assert final_ledger.completion_status(current_code_sha256=next_hash) == "ready"
    final_snapshot = final_store.load_repair_campaign(
        campaign_id=recovered_ledger.campaign_id
    )
    completion_cases = {
        case_id: frozen.node for case_id, frozen in final_ledger.frozen_cases.items()
    }
    completion_cases.update(dict(final_snapshot["full_regression"]["cases"]))
    assert completion_cases == approved_cases
    assert final_snapshot["full_regression"]["cases"] == expected_full_cases
    with pytest.raises(RepairContractError) as replay:
        final_ledger.begin_full_regression()
    assert replay.value.code == "full_regression_token_already_used"


def _c65_fixture_autouse_status(decorator):
    """Return True/False when autouse is literal, or None if it is ambiguous."""

    import ast

    if not isinstance(decorator, ast.Call):
        return False

    positional = []
    for argument in decorator.args:
        if isinstance(argument, ast.Starred):
            expanded = argument.value
            if not isinstance(expanded, (ast.List, ast.Tuple)):
                return None
            positional.extend(expanded.elts)
        else:
            positional.append(argument)
    if len(positional) > 1 or any(
        not isinstance(argument, ast.Constant) or argument.value is not None
        for argument in positional
    ):
        return None

    autouse_values = []
    for keyword in decorator.keywords:
        if keyword.arg == "autouse":
            autouse_values.append(keyword.value)
            continue
        if keyword.arg is not None:
            continue
        if not isinstance(keyword.value, ast.Dict):
            return None
        for key, value in zip(keyword.value.keys, keyword.value.values):
            if key is None:
                return None
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                return None
            if key.value == "autouse":
                autouse_values.append(value)
    if not autouse_values:
        return False
    if len(autouse_values) != 1:
        return None
    autouse_value = autouse_values[0]
    if not isinstance(autouse_value, ast.Constant) or not isinstance(
        autouse_value.value, bool
    ):
        return None
    return autouse_value.value


def _assert_c65_live_assertion_guard(source_text=None, *, _strict_dynamic_calls=None):
    """Reject disabled assertions and returns before C65's core check runs."""

    import ast

    strict_dynamic_calls = (
        source_text is not None
        if _strict_dynamic_calls is None
        else _strict_dynamic_calls
    )
    if source_text is None:
        assert (
            _c65_guard_source_fingerprints() == _C65_GUARD_SOURCE_FINGERPRINTS
        ), "C65 test, fixture, mark, or guard source changed"

    source = (
        Path(__file__).read_text(encoding="utf-8")
        if source_text is None
        else source_text
    )
    module = ast.parse(source, filename=__file__)
    postponed_annotations = False
    for index, statement in enumerate(module.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__":
            postponed_annotations = postponed_annotations or any(
                alias.name == "annotations" for alias in statement.names
            )
            continue
        break
    functions = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_c65_default_repository_resolver"
    ]
    assert len(functions) == 1
    function = functions[0]
    body = function.body

    expected_core_test = ast.dump(
        ast.parse("assessment.accepted is False", mode="eval").body,
        include_attributes=False,
    )
    expected_reason_test = ast.dump(
        ast.parse(
            '"assertion_expression_weakened_or_changed" in assessment.reason_codes',
            mode="eval",
        ).body,
        include_attributes=False,
    )
    core_assertions = [
        (index, statement)
        for index, statement in enumerate(body)
        if isinstance(statement, ast.Assert)
        and ast.dump(statement.test, include_attributes=False) == expected_core_test
    ]
    reason_assertions = [
        (index, statement)
        for index, statement in enumerate(body)
        if isinstance(statement, ast.Assert)
        and ast.dump(statement.test, include_attributes=False) == expected_reason_test
    ]
    assert (
        len(core_assertions) == 1
    ), "C65 core assertion must stay executable at function level"
    assert (
        len(reason_assertions) == 1
    ), "C65 rejection-reason assertion must stay executable at function level"
    assert core_assertions[0][0] < reason_assertions[0][0]
    assert not any(
        isinstance(node, ast.Return) for node in ast.walk(function)
    ), "C65 must not contain an early return that bypasses its assertions"
    blocked_api_names = {
        "pytest.skip",
        "pytest.skipif",
        "pytest.importorskip",
        "pytest.xfail",
        "pytest.skip.Exception",
        "pytest.xfail.Exception",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
        "unittest.SkipTest",
        "unittest.case.skip",
        "unittest.case.skipIf",
        "unittest.case.skipUnless",
        "unittest.case.expectedFailure",
        "unittest.case.SkipTest",
        "unittest.case.TestCase.skipTest",
    }
    blocked_api_members = {
        name.rsplit(".", 1)[-1]
        for name in blocked_api_names
        if name.rsplit(".", 1)[-1] != "Exception"
    }
    blocked_dynamic_code_names = {
        "builtins.compile",
        "builtins.eval",
        "builtins.exec",
        "inline.lambda_invocation",
    }

    def uses_blocked_api(targets):
        return any(
            target in blocked_api_names
            or any(
                target.startswith(f"{blocked_api}.")
                for blocked_api in blocked_api_names
            )
            or target.rsplit(".", 1)[-1] in blocked_api_members
            for target in targets
        )

    def uses_blocked_dynamic_code(targets):
        return any(
            target in blocked_dynamic_code_names
            or any(
                target.startswith(f"{blocked_name}.")
                for blocked_name in blocked_dynamic_code_names
            )
            for target in targets
        )

    local_function_prefix = "__local_function__:"
    local_function_definitions = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node is not function
    ]
    local_function_candidates = {}
    for node in local_function_definitions:
        local_function_candidates.setdefault(node.name, []).append(node)
    local_functions = {
        name: candidates[0] for name, candidates in local_function_candidates.items()
    }
    ambiguous_local_function_names = {
        name
        for name, candidates in local_function_candidates.items()
        if len(candidates) > 1
    }
    builtin_callable_names = {
        "all",
        "any",
        "bool",
        "classmethod",
        "callable",
        "compile",
        "dict",
        "enumerate",
        "eval",
        "exec",
        "float",
        "getattr",
        "hasattr",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "max",
        "min",
        "object",
        "open",
        "print",
        "property",
        "range",
        "repr",
        "set",
        "setattr",
        "sorted",
        "str",
        "sum",
        "staticmethod",
        "super",
        "tuple",
        "type",
        "vars",
        "zip",
    }

    def dotted_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = dotted_name(node.value)
            return f"{parent}.{node.attr}" if parent else None
        if isinstance(node, ast.Call):
            function = dotted_name(node.func)
            return f"{function}()" if function else None
        return None

    module_aliases = {}
    imported_api_aliases = {}
    assignment_aliases = {}
    ambiguous_binding_names = set()
    has_framework_star_import = False

    def walk_scope(root):
        scope_boundaries = (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.Lambda,
        )
        yield root
        for child in ast.iter_child_nodes(root):
            if isinstance(child, scope_boundaries):
                continue
            yield from walk_scope(child)

    module_scope_nodes = list(walk_scope(module))
    pytest_plugins_bound = any(
        isinstance(node, ast.Name) and node.id == "pytest_plugins"
        for node in module_scope_nodes
    ) or any(
        (
            isinstance(node, ast.Import)
            and any(
                (alias.asname or alias.name.split(".", 1)[0]) == "pytest_plugins"
                for alias in node.names
            )
        )
        or (
            isinstance(node, ast.ImportFrom)
            and any(
                (alias.asname or alias.name) == "pytest_plugins" for alias in node.names
            )
        )
        for node in module_scope_nodes
    )
    assert not pytest_plugins_bound, "C65 must not bind pytest_plugins"
    module_import_time_calls = [
        node for node in module_scope_nodes if isinstance(node, ast.Call)
    ]
    module_scope_classes = []
    module_scope_functions = []

    def collect_module_scope_definitions(root):
        for child in ast.iter_child_nodes(root):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_scope_functions.append(child)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.ClassDef):
                module_scope_classes.append(child)
                continue
            collect_module_scope_definitions(child)

    collect_module_scope_definitions(module)
    assert not module_scope_classes, "C65 must not execute module-level class bodies"

    def is_literal_data(expression):
        if isinstance(expression, ast.Constant):
            return isinstance(
                expression.value, (str, bytes, int, float, complex, bool, type(None))
            )
        if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
            return all(is_literal_data(item) for item in expression.elts)
        if isinstance(expression, ast.Dict):
            return all(
                key is not None and is_literal_data(key) and is_literal_data(value)
                for key, value in zip(expression.keys, expression.values)
            )
        return False

    def is_safe_runtime_annotation(expression):
        if isinstance(expression, ast.Constant):
            return isinstance(expression.value, (str, type(None)))
        if isinstance(expression, ast.Name):
            return expression.id in {
                "bool",
                "bytes",
                "complex",
                "dict",
                "float",
                "frozenset",
                "int",
                "list",
                "object",
                "set",
                "str",
                "tuple",
                "type",
            }
        if isinstance(expression, ast.Tuple):
            return all(is_safe_runtime_annotation(item) for item in expression.elts)
        return False

    safe_module_imports = {
        "builtins",
        "hashlib",
        "inspect",
        "json",
        "operator",
        "pytest",
        "subprocess",
        "sys",
        "tempfile",
        "unittest",
    }
    safe_from_imports = {
        "__future__": {"annotations"},
        "builtins": {"compile", "eval", "exec", "print"},
        "datetime": {"timedelta"},
        "importlib": {"import_module", "util"},
        "pathlib": {"Path"},
        "pytest": {
            "fixture",
            "importorskip",
            "mark",
            "param",
            "raises",
            "skip",
            "skipif",
            "usefixtures",
            "xfail",
        },
        "typing": {"Any"},
        "unittest": {"SkipTest", "skip", "skipIf", "skipUnless"},
        "uuid": {"uuid4"},
    }
    safe_reachable_module_imports = safe_module_imports | {"ast"}
    safe_reachable_from_imports = {
        **safe_from_imports,
        "tests_inago.test_maintenance_planning": {
            "BASE_TIME",
            "_complete_plan",
            "_make_plan_response",
            "_start_planning_attempt",
        },
    }
    safe_dynamic_import_modules = {
        "automation.inago.maintenance.controller",
        "automation.inago.maintenance.repair",
        "tests_inago.maintenance_investigation_support",
        "tests_inago.test_maintenance_planning",
    }

    def is_safe_module_import(statement):
        if isinstance(statement, ast.Import):
            return all(alias.name in safe_module_imports for alias in statement.names)
        if isinstance(statement, ast.ImportFrom):
            allowed_names = safe_from_imports.get(statement.module or "", set())
            return (
                statement.level == 0
                and bool(allowed_names)
                and all(
                    alias.name != "*" and alias.name in allowed_names
                    for alias in statement.names
                )
            )
        return False

    def is_safe_reachable_import(statement):
        if isinstance(statement, ast.Import):
            return all(
                alias.name in safe_reachable_module_imports for alias in statement.names
            )
        if isinstance(statement, ast.ImportFrom):
            allowed_names = safe_reachable_from_imports.get(
                statement.module or "", set()
            )
            return (
                statement.level == 0
                and bool(allowed_names)
                and all(
                    alias.name != "*" and alias.name in allowed_names
                    for alias in statement.names
                )
            )
        return False

    header_calls = []
    header_named_expressions = []
    header_attribute_accesses = []
    header_subscript_accesses = []
    non_literal_header_defaults = []
    unsafe_runtime_annotations = []
    for function_node in module_scope_functions:
        arguments = function_node.args
        header_defaults = [
            *arguments.defaults,
            *(value for value in arguments.kw_defaults if value is not None),
        ]
        non_literal_header_defaults.extend(
            value for value in header_defaults if not is_literal_data(value)
        )
        header_expressions = [
            *header_defaults,
        ]
        all_arguments = [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ]
        if arguments.vararg is not None:
            all_arguments.append(arguments.vararg)
        if arguments.kwarg is not None:
            all_arguments.append(arguments.kwarg)
        header_expressions.extend(
            argument.annotation
            for argument in all_arguments
            if argument.annotation is not None
        )
        if function_node.returns is not None:
            header_expressions.append(function_node.returns)
        evaluated_header_expressions = [
            *arguments.defaults,
            *(value for value in arguments.kw_defaults if value is not None),
        ]
        if not postponed_annotations:
            runtime_annotations = [
                argument.annotation
                for argument in all_arguments
                if argument.annotation is not None
            ]
            evaluated_header_expressions.extend(runtime_annotations)
            unsafe_runtime_annotations.extend(
                expression
                for expression in runtime_annotations
                if not is_safe_runtime_annotation(expression)
            )
            if function_node.returns is not None:
                evaluated_header_expressions.append(function_node.returns)
                if not is_safe_runtime_annotation(function_node.returns):
                    unsafe_runtime_annotations.append(function_node.returns)
        header_attribute_accesses.extend(
            node
            for expression in evaluated_header_expressions
            for node in ast.walk(expression)
            if isinstance(node, ast.Attribute)
        )
        header_subscript_accesses.extend(
            node
            for expression in evaluated_header_expressions
            for node in ast.walk(expression)
            if isinstance(node, ast.Subscript)
        )
        header_named_expressions.extend(
            node
            for expression in (*header_expressions, *function_node.decorator_list)
            for node in ast.walk(expression)
            if isinstance(node, ast.NamedExpr)
        )
        header_calls.extend(
            node
            for expression in header_expressions
            for node in ast.walk(expression)
            if isinstance(node, ast.Call)
        )
    assert not header_named_expressions, (
        "C65 must not use assignment expressions in module-level function headers "
        "or decorators"
    )
    assert not header_attribute_accesses, (
        "C65 must not evaluate arbitrary module attributes in module-level "
        "function headers"
    )
    assert (
        not header_subscript_accesses
    ), "C65 must not subscript evaluated module-level function headers"
    assert (
        not non_literal_header_defaults
    ), "C65 must use literal-only module-level function defaults"
    assert (
        not unsafe_runtime_annotations
    ), "C65 must use safe runtime annotations or verified postponed annotations"
    assert not header_calls, "C65 must not execute module-level function-header calls"
    relevant_nodes = module_scope_nodes + list(walk_scope(function))
    assignment_nodes = [
        node
        for node in relevant_nodes
        if isinstance(
            node,
            (
                ast.Assign,
                ast.AnnAssign,
                ast.NamedExpr,
                ast.For,
                ast.AsyncFor,
                ast.comprehension,
                ast.Match,
            ),
        )
    ]

    def bound_names(target):
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, ast.Starred):
            return bound_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return set().union(*(bound_names(value) for value in target.elts))
        return set()

    def record_assignment_alias(target, source):
        if isinstance(source, ast.Starred):
            source = source.value
        if isinstance(target, ast.Starred):
            target = target.value
        if isinstance(target, ast.Name):
            assignment_aliases.setdefault(target.id, []).append(source)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(source, (ast.Tuple, ast.List)) and len(target.elts) == len(
                source.elts
            ):
                for target_item, source_item in zip(target.elts, source.elts):
                    record_assignment_alias(target_item, source_item)
                return
            ambiguous_binding_names.update(bound_names(target))

    def record_for_loop_aliases(node):
        if not isinstance(node.iter, (ast.Tuple, ast.List)):
            ambiguous_binding_names.update(bound_names(node.target))
            return
        iterable_elements = []

        def expand_starred(value):
            if not isinstance(value, (ast.Tuple, ast.List)):
                ambiguous_binding_names.update(bound_names(node.target))
                return
            for expanded_element in value.elts:
                if isinstance(expanded_element, ast.Starred):
                    expand_starred(expanded_element.value)
                else:
                    iterable_elements.append(expanded_element)

        for element in node.iter.elts:
            if isinstance(element, ast.Starred):
                expand_starred(element.value)
            else:
                iterable_elements.append(element)
        if not iterable_elements:
            ambiguous_binding_names.update(bound_names(node.target))
            return
        for element in iterable_elements:
            record_assignment_alias(node.target, element)

    def record_match_aliases(node):
        for case in node.cases:
            for pattern_node in ast.walk(case.pattern):
                capture_name = None
                if isinstance(pattern_node, (ast.MatchAs, ast.MatchStar)):
                    capture_name = pattern_node.name
                elif isinstance(pattern_node, ast.MatchMapping):
                    capture_name = pattern_node.rest
                if capture_name is None or capture_name == "_":
                    continue
                # Pattern captures can hold any part of the subject. Preserve
                # subject provenance conservatively so API mutations through a
                # capture are detected, including sequence/mapping patterns.
                assignment_aliases.setdefault(capture_name, []).append(node.subject)
                ambiguous_binding_names.add(capture_name)

    # Context-manager targets have runtime-provided values. Their provenance
    # is unknown unless a more specific assignment in the guarded source proves
    # otherwise.
    for candidate in ast.walk(module):
        if isinstance(candidate, (ast.With, ast.AsyncWith)):
            for item in candidate.items:
                if item.optional_vars is not None:
                    ambiguous_binding_names.update(bound_names(item.optional_vars))
    for argument in (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ):
        ambiguous_binding_names.add(argument.arg)
    if function.args.vararg is not None:
        ambiguous_binding_names.add(function.args.vararg.arg)
    if function.args.kwarg is not None:
        ambiguous_binding_names.add(function.args.kwarg.arg)

    def framework_module_name(name):
        return (
            name == "pytest"
            or name.startswith("pytest.")
            or (name == "unittest" or name.startswith("unittest."))
        )

    def local_return_values(expression, resolving=()):
        if not isinstance(expression, ast.Call):
            return (), False
        call_targets, call_unknown = resolve_api_expression(
            expression.func, strict_names=True
        )
        local_names = {
            target[len(local_function_prefix) :]
            for target in call_targets
            if target.startswith(local_function_prefix)
        }
        if not local_names:
            return (), call_unknown
        values = []
        unknown = call_unknown
        for local_name in local_names:
            if local_name in resolving:
                unknown = True
                continue
            local_function = local_functions.get(local_name)
            if local_function is None:
                unknown = True
                continue
            returns = [
                node.value
                for node in walk_scope(local_function)
                if isinstance(node, ast.Return) and node.value is not None
            ]
            if not returns:
                unknown = True
            values.extend(returns)
        return tuple(values), unknown

    def sys_modules_registry_reference(expression, resolving=()):
        targets, unknown = resolve_api_expression(expression, strict_names=True)
        if "sys.modules" in targets:
            return True
        if isinstance(expression, ast.Name) and expression.id not in resolving:
            sources = assignment_aliases.get(expression.id, ())
            if sources:
                return any(
                    sys_modules_registry_reference(source, resolving + (expression.id,))
                    for source in sources
                )
        if isinstance(expression, ast.Call):
            values, return_unknown = local_return_values(expression, resolving)
            if values:
                call_targets, _call_unknown = resolve_api_expression(
                    expression.func, strict_names=True
                )
                local_names = tuple(
                    target[len(local_function_prefix) :]
                    for target in call_targets
                    if target.startswith(local_function_prefix)
                )
                return any(
                    sys_modules_registry_reference(value, resolving + local_names)
                    for value in values
                )
            return return_unknown or unknown
        return False

    for node in relevant_nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = alias.name
                local = alias.asname or imported.split(".", 1)[0]
                module_aliases.setdefault(local, set()).add(
                    imported if alias.asname else imported.split(".", 1)[0]
                )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if any(alias.name == "*" for alias in node.names):
                # Any star import can expose a module-level pytestmark or a
                # fixture decorator through arbitrary module exports.
                has_framework_star_import = True
                continue
            if node.level != 0:
                continue
            for alias in node.names:
                imported_api_aliases.setdefault(alias.asname or alias.name, set()).add(
                    f"{module_name}.{alias.name}"
                )
    assert (
        not has_framework_star_import
    ), "C65 must not use pytest or unittest star imports or arbitrary-module star imports"

    for node in assignment_nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                record_assignment_alias(target, node.value)
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                record_assignment_alias(node.target, node.value)
        elif isinstance(node, ast.NamedExpr):
            record_assignment_alias(node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            record_for_loop_aliases(node)
        elif isinstance(node, ast.comprehension):
            record_for_loop_aliases(node)
        elif isinstance(node, ast.Match):
            record_match_aliases(node)

    def resolve_api_name(name, resolving=(), strict_names=False):
        if name is None:
            return set(), False
        if name in resolving:
            return set(), True
        # Opaque function/fixture parameters shadow module globals and
        # builtins at runtime. Do not classify them as known-safe by spelling.
        if name in ambiguous_binding_names:
            return {name}, True
        if name in assignment_aliases:
            resolved = set()
            unknown = name in ambiguous_local_function_names
            for source_expression in assignment_aliases[name]:
                source_targets, source_unknown = resolve_api_expression(
                    source_expression,
                    resolving + (name,),
                    strict_names=True,
                )
                resolved.update(source_targets)
                unknown = unknown or source_unknown
            resolved.update(imported_api_aliases.get(name, ()))
            if name in local_functions:
                resolved.add(f"{local_function_prefix}{name}")
            resolved.update(module_aliases.get(name, ()))
            if (
                name in builtin_callable_names
                and name not in local_functions
                and name not in imported_api_aliases
                and name not in module_aliases
            ):
                resolved.add(f"builtins.{name}")
            return resolved, unknown
        if name in ambiguous_local_function_names:
            return {f"{local_function_prefix}{name}"}, True
        # Module/local/import bindings take precedence over builtins in Python.
        if name in local_functions:
            return {f"{local_function_prefix}{name}"}, False
        if name in imported_api_aliases:
            return set(imported_api_aliases[name]), False
        if name in module_aliases:
            return set(module_aliases[name]), False
        if name in builtin_callable_names:
            return {f"builtins.{name}"}, False

        root, separator, suffix = name.partition(".")
        if root in assignment_aliases:
            resolved_roots, unknown = resolve_api_name(
                root, resolving, strict_names=strict_names
            )
            return {
                f"{resolved_root}{separator}{suffix}"
                for resolved_root in resolved_roots
            }, unknown
        if root in ambiguous_binding_names:
            return {name}, True
        if root in ambiguous_local_function_names:
            return {f"{local_function_prefix}{name}"}, True
        if root in local_functions:
            return {f"{local_function_prefix}{root}.{suffix}"}, False
        if root in imported_api_aliases:
            return {
                f"{imported_root}{separator}{suffix}"
                for imported_root in imported_api_aliases[root]
            }, False
        if root in module_aliases:
            return {
                f"{resolved_root}{separator}{suffix}"
                for resolved_root in module_aliases[root]
            }, False
        if root in builtin_callable_names and separator:
            return {f"builtins.{root}.{suffix}"}, False
        if root == "builtins" and suffix.partition(".")[0] in builtin_callable_names:
            return {name}, False
        return {name}, strict_names

    def resolve_api_expression(expression, resolving=(), strict_names=False):
        if isinstance(expression, ast.Attribute) and isinstance(
            expression.value, ast.Call
        ):
            base_targets, base_unknown = resolve_api_expression(
                expression.value, resolving, strict_names=strict_names
            )
            return {
                f"{base_target}.{expression.attr}" for base_target in base_targets
            }, base_unknown
        if isinstance(expression, (ast.Name, ast.Attribute)):
            return resolve_api_name(
                dotted_name(expression), resolving, strict_names=strict_names
            )
        if isinstance(expression, ast.Call):
            function_targets, function_unknown = resolve_api_name(
                dotted_name(expression.func),
                resolving,
                strict_names=strict_names,
            )
            if (
                function_targets == {"importlib.import_module"}
                and not function_unknown
                and expression.args
                and isinstance(expression.args[0], ast.Constant)
                and isinstance(expression.args[0].value, str)
            ):
                return {expression.args[0].value}, False
            if (
                function_targets.intersection({"getattr", "builtins.getattr"})
                and not function_unknown
                and len(expression.args) >= 2
                and isinstance(expression.args[1], ast.Constant)
                and isinstance(expression.args[1].value, str)
            ):
                object_targets, object_unknown = resolve_api_expression(
                    expression.args[0], resolving, strict_names=strict_names
                )
                return {
                    f"{object_target}.{expression.args[1].value}"
                    for object_target in object_targets
                }, object_unknown
            if (
                function_targets.intersection({"vars", "builtins.vars"})
                and not function_unknown
                and expression.args
            ):
                object_targets, object_unknown = resolve_api_expression(
                    expression.args[0], resolving, strict_names=strict_names
                )
                return {
                    f"{object_target}.__dict__" for object_target in object_targets
                }, object_unknown
            known_constructors = {
                "automation.inago.maintenance.controller.MaintenanceController",
                "automation.inago.maintenance.repair.RepairController",
                "automation.inago.maintenance.repair.RepairHostEvidenceContext",
            }
            if (
                not function_unknown
                and function_targets
                and function_targets.issubset(known_constructors)
            ):
                return {f"{target}()" for target in function_targets}, False
            if function_targets.intersection(
                {"importlib.import_module", "builtins.__import__"}
            ):
                return set(), True
            if function_targets.intersection({"getattr", "builtins.getattr"}):
                return set(), True
            if function_targets and all(
                target.startswith(local_function_prefix) for target in function_targets
            ):
                return {f"{target}()" for target in function_targets}, False
        return set(), True

    def framework_api_paths(expression, resolving=()):
        if sys_modules_registry_reference(expression, resolving):
            return {"sys.modules"}
        if isinstance(expression, ast.BoolOp):
            return set().union(
                *(framework_api_paths(value, resolving) for value in expression.values)
            )
        if isinstance(expression, ast.IfExp):
            return framework_api_paths(
                expression.body, resolving
            ) | framework_api_paths(expression.orelse, resolving)
        if isinstance(expression, ast.Name) and expression.id not in resolving:
            alias_paths = set()
            for source in assignment_aliases.get(expression.id, ()):
                alias_paths.update(
                    framework_api_paths(source, resolving + (expression.id,))
                )
            if alias_paths:
                return alias_paths
        if isinstance(expression, ast.Call):
            call_targets, call_unknown = resolve_api_expression(
                expression.func, strict_names=True
            )
            if (
                not call_unknown
                and call_targets.intersection({"vars", "builtins.vars"})
                and expression.args
            ):
                module_paths = framework_api_paths(expression.args[0], resolving)
                if module_paths:
                    return {f"{path}.__dict__" for path in module_paths}
            returned_values, return_unknown = local_return_values(expression, resolving)
            if returned_values:
                local_names = tuple(
                    target[len(local_function_prefix) :]
                    for target in call_targets
                    if target.startswith(local_function_prefix)
                )
                paths = set()
                for value in returned_values:
                    paths.update(framework_api_paths(value, resolving + local_names))
                return paths
            if return_unknown and expression.func is not None:
                return set()
        if isinstance(expression, ast.Attribute):
            if isinstance(expression.value, ast.Subscript):
                registry_paths = sys_module_registry_paths(expression.value)
                if registry_paths:
                    return {
                        f"{registry_path}.{expression.attr}"
                        for registry_path in registry_paths
                    }
            base_paths = framework_api_paths(expression.value, resolving)
            if base_paths:
                return {f"{base_path}.{expression.attr}" for base_path in base_paths}
        if isinstance(expression, ast.Subscript):
            registry_paths = sys_module_registry_paths(expression)
            if registry_paths:
                return registry_paths
            paths = set()
            for value in literal_container_elements(
                expression.value, expression.slice, resolving
            ):
                paths.update(framework_api_paths(value, resolving))
            if paths:
                return paths
            if isinstance(expression.value, ast.Name) and any(
                contains_indirect_target(source)
                for source in assignment_aliases.get(expression.value.id, ())
            ):
                return set()
            return framework_api_paths(expression.value, resolving)
        targets, _unknown = resolve_api_expression(expression, strict_names=True)
        return {target for target in targets if framework_module_name(target)}

    def static_string_values(expression, resolving=()):
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return {expression.value}, False
        if not isinstance(expression, ast.Name) or expression.id in resolving:
            return set(), True
        sources = assignment_aliases.get(expression.id, ())
        if not sources:
            return set(), True
        values = set()
        unknown = False
        for source in sources:
            source_values, source_unknown = static_string_values(
                source, resolving + (expression.id,)
            )
            values.update(source_values)
            unknown = unknown or source_unknown
        return values, unknown

    def sys_module_registry_paths(expression):
        if not isinstance(expression, ast.Subscript):
            return set()
        registry = expression.value
        if sys_modules_registry_reference(registry):
            # Any write into sys.modules is prohibited because C65 performs
            # later imports whose provenance must remain host-controlled.
            return {"sys.modules"}
        registry_targets, registry_unknown = resolve_api_expression(
            registry, strict_names=True
        )

        def resembles_sys_modules_alias(candidate, resolving=()):
            if isinstance(candidate, ast.Attribute) and candidate.attr == "modules":
                return True
            if not isinstance(candidate, ast.Name) or candidate.id in resolving:
                return False
            if candidate.id in {"modules", "sys_modules"}:
                return True
            return any(
                resembles_sys_modules_alias(source, resolving + (candidate.id,))
                for source in assignment_aliases.get(candidate.id, ())
            )

        if "sys.modules" not in registry_targets:
            if registry_unknown and resembles_sys_modules_alias(registry):
                # An unresolved registry alias may still be sys.modules.
                return {"pytest"}
            return set()
        key_values, key_unknown = static_string_values(expression.slice)
        if key_unknown:
            # A dynamic sys.modules key may resolve to pytest/unittest at runtime.
            # Treat it as a framework module so writes fail closed.
            return {"pytest"}
        return {key for key in key_values if framework_module_name(key)}

    def literal_container_elements(expression, key, resolving=()):
        if isinstance(expression, ast.Name):
            if expression.id in resolving:
                return ()
            values = []
            for source in assignment_aliases.get(expression.id, ()):
                values.extend(
                    literal_container_elements(
                        source, key, resolving + (expression.id,)
                    )
                )
            return tuple(values)
        if isinstance(expression, ast.Call):
            returned_values, _return_unknown = local_return_values(
                expression, resolving
            )
            call_targets, _call_unknown = resolve_api_expression(
                expression.func, strict_names=True
            )
            local_names = tuple(
                target[len(local_function_prefix) :]
                for target in call_targets
                if target.startswith(local_function_prefix)
            )
            values = []
            for returned in returned_values:
                values.extend(
                    literal_container_elements(returned, key, resolving + local_names)
                )
            return tuple(values)
        if not isinstance(key, ast.Constant):
            return ()
        if (
            isinstance(expression, (ast.List, ast.Tuple))
            and type(key.value) is int
            and -len(expression.elts) <= key.value < len(expression.elts)
        ):
            return (expression.elts[key.value],)
        if isinstance(expression, ast.Dict):
            return tuple(
                value
                for candidate, value in zip(expression.keys, expression.values)
                if isinstance(candidate, ast.Constant)
                and type(candidate.value) is type(key.value)
                and candidate.value == key.value
            )
        return ()

    def is_framework_dict_mutation_call(call):
        mutation_targets, mutation_unknown = resolve_api_expression(
            call.func, strict_names=True
        )
        mutation_names = {
            "clear",
            "pop",
            "popitem",
            "setdefault",
            "update",
            "__init__",
            "__ior__",
            "__setitem__",
        }
        if not mutation_unknown and mutation_targets.intersection(
            {"operator.setitem", "operator.delitem"}
        ):
            # These APIs mutate a mapping without a receiver method to inspect.
            # Fail closed on them because a mapping alias may hide a framework
            # module dictionary or sys.modules.
            return True
        if not mutation_unknown and "operator.ior" in mutation_targets:
            if not call.args:
                return True
            in_place_target = call.args[0]
            return (
                sys_modules_registry_reference(in_place_target)
                or "sys.modules" in framework_api_paths(in_place_target)
                or any(
                    path.endswith(".__dict__")
                    for path in framework_api_paths(in_place_target)
                )
                or has_indirect_assignment_source(in_place_target)
                or contains_indirect_target(in_place_target)
            )
        if mutation_unknown:
            return False

        for target in mutation_targets:
            if target in {"operator.setitem", "operator.delitem"}:
                return True
            if target == "operator.ior":
                continue
            method_name = target.rsplit(".", 1)[-1]
            if method_name not in mutation_names:
                continue
            receiver = target[: -(len(method_name) + 1)]
            if (
                receiver == "sys.modules"
                or receiver == "builtins.sys.modules"
                or framework_module_name(receiver)
                or receiver.endswith(".__dict__")
            ):
                return True
            if receiver == "builtins.dict":
                if not call.args or isinstance(call.args[0], ast.Starred):
                    return True
                mapping = call.args[0]
                mapping_paths = framework_api_paths(mapping)
                if (
                    sys_modules_registry_reference(mapping)
                    or "sys.modules" in mapping_paths
                    or any(
                        framework_module_name(path) or path.endswith(".__dict__")
                        for path in mapping_paths
                    )
                    or has_indirect_assignment_source(mapping)
                    or contains_indirect_target(mapping)
                ):
                    return True
                # An unbound dict mutator with an opaque mapping target could
                # be mutating a framework registry. Accept only literal
                # containers or names whose complete assignments prove they
                # are literal containers.
                if not is_verified_nonframework_mapping(mapping):
                    return True
        return False

    def target_leaves(target):
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                yield from target_leaves(element)
            return
        yield target

    def contains_indirect_target(expression):
        return any(
            isinstance(node, (ast.Call, ast.Subscript)) for node in ast.walk(expression)
        )

    def has_indirect_assignment_source(expression, resolving=()):
        if isinstance(expression, (ast.IfExp, ast.BoolOp)):
            return True
        if isinstance(expression, ast.Attribute):
            return has_indirect_assignment_source(expression.value, resolving)
        if not isinstance(expression, ast.Name) or expression.id in resolving:
            return False
        if expression.id in ambiguous_binding_names:
            return True
        sources = assignment_aliases.get(expression.id, ())
        return any(
            contains_indirect_target(source)
            or has_indirect_assignment_source(source, resolving + (expression.id,))
            for source in sources
        )

    def is_literal_mapping(expression, resolving=()):
        if isinstance(expression, ast.Dict):
            return all(
                key is None or is_literal_data(key) for key in expression.keys
            ) and all(is_literal_data(value) for value in expression.values)
        if isinstance(expression, (ast.List, ast.Tuple)):
            return all(is_literal_data(value) for value in expression.elts)
        if isinstance(expression, ast.Name) and expression.id not in resolving:
            sources = assignment_aliases.get(expression.id, ())
            return bool(sources) and all(
                is_literal_mapping(source, resolving + (expression.id,))
                for source in sources
            )
        return False

    def is_verified_nonframework_mapping(expression):
        if framework_api_paths(expression):
            return False
        if has_indirect_assignment_source(expression):
            return False
        return is_literal_mapping(expression)

    def has_unresolved_indirect_alias(expression, resolving=()):
        if isinstance(expression, ast.Attribute):
            return has_unresolved_indirect_alias(expression.value, resolving)
        if not isinstance(expression, ast.Name) or expression.id in resolving:
            return False
        if expression.id in ambiguous_binding_names:
            return True
        sources = assignment_aliases.get(expression.id, ())
        if not sources:
            return False
        if framework_api_paths(expression):
            return False
        return any(
            contains_indirect_target(source)
            or has_unresolved_indirect_alias(source, resolving + (expression.id,))
            for source in sources
        )

    def find_framework_api_mutations(scopes):
        mutation_details = []
        nodes = list(module_scope_nodes)
        for guarded_scope in scopes:
            nodes.extend(walk_scope(guarded_scope))
            for candidate in ast.walk(guarded_scope):
                if isinstance(candidate, ast.Lambda):
                    # Lambda bodies are deferred scopes, but callbacks passed
                    # to higher-order calls can still mutate pytest/sys.modules
                    # before the guarded assertions complete.
                    nodes.extend(ast.walk(candidate.body))
        for node in nodes:
            values_to_check = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
            elif isinstance(node, ast.AugAssign):
                targets = (node.target,)
            elif isinstance(node, ast.Delete):
                targets = node.targets
            elif isinstance(node, ast.NamedExpr):
                targets = (node.target,)
                values_to_check.append(node.value)
            elif isinstance(node, ast.Call) and is_framework_dict_mutation_call(node):
                mutation_details.append(
                    (node.lineno, ast.unparse(node.func), ("framework module dict",))
                )
                continue
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {
                    "clear",
                    "pop",
                    "popitem",
                    "setdefault",
                    "update",
                    "__init__",
                    "__ior__",
                    "__setitem__",
                }
                and (
                    contains_indirect_target(node.func.value)
                    or has_indirect_assignment_source(node.func.value)
                    or has_unresolved_indirect_alias(node.func.value)
                )
            ):
                mutation_details.append(
                    (
                        node.lineno,
                        ast.unparse(node.func),
                        ("unresolved indirect mutation target",),
                    )
                )
                continue
            else:
                continue

            for target in targets:
                for leaf in target_leaves(target):
                    if isinstance(leaf, ast.Name):
                        # Rebinding an imported pytest/unittest symbol or module
                        # alias can redirect later assertions and API calls.
                        if (
                            leaf.id not in imported_api_aliases
                            and leaf.id not in module_aliases
                            and not (
                                isinstance(node, ast.AugAssign)
                                and leaf.id in assignment_aliases
                            )
                        ):
                            continue
                    elif not isinstance(leaf, (ast.Attribute, ast.Subscript)):
                        continue
                    paths = framework_api_paths(leaf)
                    if paths:
                        mutation_details.append(
                            (node.lineno, ast.unparse(leaf), tuple(sorted(paths)))
                        )
                    elif isinstance(leaf, ast.Attribute) and contains_indirect_target(
                        leaf.value
                    ):
                        mutation_details.append(
                            (
                                node.lineno,
                                ast.unparse(leaf),
                                ("unresolved indirect mutation target",),
                            )
                        )
                    elif isinstance(leaf, ast.Attribute) and (
                        has_indirect_assignment_source(leaf.value)
                        or has_unresolved_indirect_alias(leaf.value)
                    ):
                        mutation_details.append(
                            (
                                node.lineno,
                                ast.unparse(leaf),
                                ("unresolved indirect mutation alias",),
                            )
                        )
                    elif isinstance(leaf, ast.Subscript) and (
                        contains_indirect_target(leaf.value)
                        or has_indirect_assignment_source(leaf.value)
                        or has_unresolved_indirect_alias(leaf.value)
                    ):
                        mutation_details.append(
                            (
                                node.lineno,
                                ast.unparse(leaf),
                                ("unresolved indirect mutation target",),
                            )
                        )
                    elif (
                        isinstance(leaf, ast.Name)
                        and isinstance(node, ast.AugAssign)
                        and (
                            has_indirect_assignment_source(leaf)
                            or has_unresolved_indirect_alias(leaf)
                        )
                    ):
                        mutation_details.append(
                            (
                                node.lineno,
                                ast.unparse(leaf),
                                ("unresolved indirect mutation alias",),
                            )
                        )
            for value in values_to_check:
                paths = framework_api_paths(value)
                if paths:
                    mutation_details.append(
                        (node.lineno, ast.unparse(node), tuple(sorted(paths)))
                    )
        return mutation_details

    unresolved_call_details = []
    framework_api_mutations = []

    verified_live_indirect_calls = {
        ("_cleanup_tiny_git_checkouts", "_TINY_GIT_CHECKOUT_TEMPS.pop"),
        (
            "_cleanup_tiny_git_checkouts",
            "_TINY_GIT_CHECKOUT_TEMPS.pop().cleanup",
        ),
        ("_make_tiny_git_checkout", "_TINY_GIT_CHECKOUT_TEMPS.append"),
        ("_make_tiny_git_checkout", "root.mkdir"),
        ("_make_tiny_git_checkout", "files.items"),
        ("_make_tiny_git_checkout", "(root / '.gitignore').write_text"),
        ("_make_tiny_git_checkout", "path.parent.mkdir"),
        ("_make_tiny_git_checkout", "path.write_text"),
        (
            "test_c65_default_repository_resolver",
            "subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=checkout_root, check=True, capture_output=True, text=True).stdout.strip",
        ),
        ("test_c65_default_repository_resolver", "monkeypatch.setattr"),
        (
            "test_c65_default_repository_resolver",
            "planning_controller.begin_repair_attempt",
        ),
        (
            "test_c65_default_repository_resolver",
            "store.read_attempt_artifact",
        ),
        (
            "test_c65_default_repository_resolver",
            "(checkout_root / 'automation/inago/maintenance/fix.py').write_text",
        ),
        (
            "test_c65_default_repository_resolver",
            "(checkout_root / 'tests_inago/test_scope.py').write_text",
        ),
        (
            "test_c65_default_repository_resolver",
            "(checkout_root / 'automation/inago/maintenance/unowned.py').write_text",
        ),
    }

    def is_verified_live_indirect_call(scope, call):
        return (
            source_text is None
            and not strict_dynamic_calls
            and (scope.name, ast.unparse(call.func)) in verified_live_indirect_calls
        )

    def has_unverified_dotted_receiver(call):
        if not isinstance(call.func, ast.Attribute):
            return False
        if framework_api_paths(call.func.value):
            return False
        if call.func.attr in {
            "clear",
            "pop",
            "popitem",
            "setdefault",
            "update",
            "__init__",
            "__ior__",
            "__setitem__",
        } and has_indirect_assignment_source(call.func.value):
            # The mutation scanner reports this path as a framework mutation
            # or an unresolved mutation alias below.
            return False
        receiver_targets, receiver_unknown = resolve_api_expression(
            call.func.value, strict_names=True
        )
        return receiver_unknown or not receiver_targets

    def is_bound_pytest_monkeypatch_method(scope, call):
        if not isinstance(call.func, ast.Attribute):
            return False
        if call.func.attr not in {"setattr", "setitem"}:
            return False
        if not isinstance(call.func.value, ast.Name):
            return False
        if call.func.value.id != "monkeypatch":
            return False
        arguments = getattr(scope, "args", None)
        if arguments is None:
            return False
        parameter_names = {
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        }
        if arguments.vararg is not None:
            parameter_names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            parameter_names.add(arguments.kwarg.arg)
        return (
            "monkeypatch" in parameter_names and "monkeypatch" not in assignment_aliases
        )

    def pytest_monkeypatch_method_targets(call):
        targets, _unknown = resolve_api_expression(call.func, strict_names=True)
        return {
            target
            for target in targets
            if target.startswith(
                ("pytest.MonkeyPatch.", "_pytest.monkeypatch.MonkeyPatch.")
            )
            and target.rsplit(".", 1)[-1] in {"setattr", "setitem"}
        }

    def callable_uses_for(scope):
        uses = []
        for node in walk_scope(scope):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Lambda):
                uses.append(({"inline.lambda_invocation"}, False, False))
                continue
            strict = strict_dynamic_calls or isinstance(node.func, ast.Name)
            targets, unknown = resolve_api_expression(node.func, strict_names=strict)
            if is_bound_pytest_monkeypatch_method(scope, node):
                targets = {f"pytest.MonkeyPatch.{node.func.attr}"}
                unknown = False
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {
                    "clear",
                    "pop",
                    "popitem",
                    "setdefault",
                    "update",
                    "__init__",
                    "__ior__",
                    "__setitem__",
                }
                and has_indirect_assignment_source(node.func.value)
            ):
                # This receiver is handled by the mutation scanner below.
                # It must not be reported as an unresolved dotted call first.
                targets = {"__scanned_indirect_mutation__"}
                unknown = False
            if any(
                target.startswith(local_function_prefix) and target.endswith("()")
                for target in targets
            ):
                # A local function call's return value is not a verified callable.
                # Do not treat a factory result as the function that was inspected.
                unknown = True
            if has_unverified_dotted_receiver(
                node
            ) and not is_bound_pytest_monkeypatch_method(scope, node):
                unknown = True
            if unknown and is_verified_host_resolver_call(scope, node):
                targets = {
                    "automation.inago.maintenance.repair.RepositoryRepairEvidenceResolver"
                }
                unknown = False
            if unknown and is_verified_live_indirect_call(scope, node):
                targets = {"__verified_safe_indirect_call__"}
                unknown = False
            if unknown:
                unresolved_call_details.append(
                    (
                        scope.name,
                        ast.unparse(node.func),
                        node.lineno,
                        tuple(sorted(targets)),
                    )
                )
            uses.append(
                (
                    targets,
                    unknown,
                    isinstance(node.func, ast.Name),
                )
            )
        return uses

    callback_keyword_names = {
        "callback",
        "cmp",
        "comparator",
        "condition",
        "executor",
        "factory",
        "filter",
        "func",
        "function",
        "handler",
        "hook",
        "identity_resolver",
        "key",
        "operation",
        "predicate",
        "resolver",
        "target",
        "transform",
        "visitor",
    }
    positional_callback_targets = {
        "builtins.filter": {0},
        "builtins.iter": {0},
        "builtins.map": {0},
        "operator.call": {0},
        "builtins.sorted": {1},
        "functools.reduce": {0},
        "itertools.starmap": {0},
    }
    safe_callback_targets = {
        "builtins.abs",
        "builtins.bool",
        "builtins.float",
        "builtins.int",
        "builtins.len",
        "builtins.str",
    }
    callback_blocked_details = []
    callback_unresolved_details = []

    def callback_slots_for(call):
        call_targets, call_unknown = resolve_api_expression(
            call.func, strict_names=True
        )
        positional_slots = set()
        if not call_unknown:
            for target in call_targets:
                positional_slots.update(positional_callback_targets.get(target, ()))
        return positional_slots

    def mutation_value_positions(scope, call):
        call_name = dotted_name(call.func)
        call_targets, _call_unknown = resolve_api_expression(
            call.func, strict_names=True
        )
        is_builtin_setattr = call_name in {
            "setattr",
            "builtins.setattr",
            "object.__setattr__",
            "type.__setattr__",
        } or bool(
            {
                "builtins.setattr",
                "builtins.object.__setattr__",
                "builtins.type.__setattr__",
            }.intersection(call_targets)
        )
        if is_builtin_setattr:
            return {2}, set(), True

        is_bound_setter = is_bound_pytest_monkeypatch_method(scope, call)
        method_targets = pytest_monkeypatch_method_targets(call)
        is_unbound_setter = bool(method_targets)
        method_names = {target.rsplit(".", 1)[-1] for target in method_targets}
        if is_bound_setter:
            method_names = {call.func.attr}
        if not (is_bound_setter or is_unbound_setter):
            return set(), set(), False

        # MonkeyPatch.setattr supports (target, value), (target, name, value),
        # and the corresponding unbound forms with `self` as the first arg.
        # setitem has one value argument after mapping and key.
        value_positions = set()
        if "setattr" in method_names:
            positional_count = len(call.args)
            if is_bound_setter:
                if positional_count == 2:
                    value_positions.add(1)
                elif positional_count >= 3:
                    value_positions.add(2)
            else:
                if positional_count == 3:
                    value_positions.add(2)
                elif positional_count >= 4:
                    value_positions.add(3)
        if "setitem" in method_names:
            positional_count = len(call.args)
            if is_bound_setter and positional_count >= 3:
                value_positions.add(2)
            elif is_unbound_setter and positional_count >= 4:
                value_positions.add(3)
        value_keywords = {"value"}
        if (
            "setattr" in method_names
            and not value_positions
            and not any(keyword.arg == "value" for keyword in call.keywords)
            and any(keyword.arg == "name" for keyword in call.keywords)
        ):
            # In pytest's string-target overload, `name` is the replacement
            # value when `value` is omitted. Treat this form as potentially
            # mutating even when the target expression is ambiguous.
            value_keywords.add("name")
        return value_positions, value_keywords, True

    def framework_mutator_target_paths(scope, call):
        call_name = dotted_name(call.func)
        call_targets, _call_unknown = resolve_api_expression(
            call.func, strict_names=True
        )
        builtin_setter_targets = {
            "builtins.setattr",
            "builtins.object.__setattr__",
            "builtins.type.__setattr__",
        }
        is_builtin_setattr = call_name in {
            "setattr",
            "builtins.setattr",
            "object.__setattr__",
            "type.__setattr__",
        } or bool(builtin_setter_targets.intersection(call_targets))
        if is_builtin_setattr:
            target = (
                call.args[0]
                if call.args
                else next(
                    (
                        keyword.value
                        for keyword in call.keywords
                        if keyword.arg == "object"
                    ),
                    None,
                )
            )
            return framework_module_paths_for_setter_target(target)

        is_bound_setter = is_bound_pytest_monkeypatch_method(scope, call)
        method_targets = pytest_monkeypatch_method_targets(call)
        if not (is_bound_setter or method_targets):
            return set()
        method_names = {target.rsplit(".", 1)[-1] for target in method_targets}
        if is_bound_setter:
            method_names = {call.func.attr}
        if "setattr" in method_names:
            target = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "target"),
                None,
            )
            if target is None:
                target_index = 1 if method_targets and not is_bound_setter else 0
                if len(call.args) > target_index:
                    target = call.args[target_index]
            return framework_module_paths_for_setter_target(target)
        if "setitem" in method_names:
            target = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "dic"),
                None,
            )
            if target is None:
                target_index = 1 if method_targets and not is_bound_setter else 0
                if len(call.args) > target_index:
                    target = call.args[target_index]
            if target is None or contains_indirect_target(target):
                return {"pytest"}
            if isinstance(target, ast.Name) and has_indirect_assignment_source(target):
                return {"pytest"}
            target_paths = framework_api_paths(target) if target is not None else set()
            return {
                path
                for path in target_paths
                if path == "sys.modules"
                or framework_module_name(path)
                or path.endswith(".__dict__")
            }
        return set()

    def framework_module_paths_for_setter_target(target):
        if target is None:
            return {"pytest"}
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            return {target.value} if framework_module_name(target.value) else set()
        if isinstance(target, ast.Name) and has_indirect_assignment_source(target):
            resolved, unresolved = resolve_api_expression(target, strict_names=True)
            framework_targets = {
                value
                for value in resolved
                if value == "sys.modules" or framework_module_name(value)
            }
            if framework_targets:
                return framework_targets
            if (
                unresolved
                or not resolved
                or any(value.startswith(local_function_prefix) for value in resolved)
            ):
                # A helper/call/subscript-derived target is not proven outside
                # the framework namespace. Setter calls fail closed on that
                # provenance.
                return {"pytest"}
            return set()
        string_values, string_unknown = static_string_values(target)
        framework_strings = {
            value for value in string_values if framework_module_name(value)
        }
        if framework_strings:
            return framework_strings
        if string_values and not string_unknown:
            return set()
        api_paths = framework_api_paths(target)
        framework_paths = {
            path
            for path in api_paths
            if path == "sys.modules" or framework_module_name(path)
        }
        if framework_paths:
            return framework_paths
        resolved, unknown = resolve_api_expression(target, strict_names=True)
        if (
            any(value.startswith(local_function_prefix) for value in resolved)
            or unknown
        ):
            # Helper return values and dynamic targets are not proven outside
            # the framework namespace, so setter calls fail closed.
            return {"pytest"}
        return set()

    def is_verified_live_noncallable_mutation_value(scope, call, expression):
        return (
            source_text is None
            and not strict_dynamic_calls
            and scope.name == "test_c65_default_repository_resolver"
            and dotted_name(call.func) == "monkeypatch.setattr"
            and isinstance(expression, ast.Name)
            and expression.id == "base_commit"
        )

    def inspect_callback_value(expression, scope, call, resolving=()):
        if isinstance(expression, ast.Lambda):
            if any(isinstance(node, ast.Lambda) for node in ast.walk(expression.body)):
                callback_unresolved_details.append(
                    (scope.name, ast.unparse(call.func), "nested callback lambda")
                )
                return
            for node in ast.walk(expression.body):
                if isinstance(node, (ast.Name, ast.Attribute)):
                    targets, _unknown = resolve_api_expression(node, strict_names=True)
                    if uses_blocked_api(targets) or uses_blocked_dynamic_code(targets):
                        callback_blocked_details.append(
                            (scope.name, ast.unparse(call.func), ast.unparse(node))
                        )
                if isinstance(node, ast.Raise) and node.exc is not None:
                    raised_expression = (
                        node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                    )
                    targets, _unknown = resolve_api_expression(
                        raised_expression, strict_names=True
                    )
                    if uses_blocked_api(targets):
                        callback_blocked_details.append(
                            (scope.name, ast.unparse(call.func), ast.unparse(node))
                        )
                if isinstance(node, ast.Call):
                    targets, unknown = resolve_api_expression(
                        node.func, strict_names=True
                    )
                    if uses_blocked_api(targets) or uses_blocked_dynamic_code(targets):
                        callback_blocked_details.append(
                            (scope.name, ast.unparse(call.func), ast.unparse(node))
                        )
                    elif unknown or has_unverified_dotted_receiver(node):
                        callback_unresolved_details.append(
                            (scope.name, ast.unparse(call.func), ast.unparse(node.func))
                        )
                    else:
                        enqueue_local_targets(targets)
                if isinstance(node, ast.Attribute):
                    _targets, unknown = resolve_api_expression(node, strict_names=True)
                    if unknown:
                        callback_unresolved_details.append(
                            (scope.name, ast.unparse(call.func), ast.unparse(node))
                        )
            return

        if isinstance(expression, ast.Name) and expression.id in resolving:
            callback_unresolved_details.append(
                (scope.name, ast.unparse(call.func), ast.unparse(expression))
            )
            return

        targets, unknown = resolve_api_expression(expression, strict_names=True)
        if uses_blocked_api(targets) or uses_blocked_dynamic_code(targets):
            callback_blocked_details.append(
                (scope.name, ast.unparse(call.func), ast.unparse(expression))
            )
            return
        if any(
            target.startswith(local_function_prefix) and target.endswith("()")
            for target in targets
        ):
            callback_unresolved_details.append(
                (scope.name, ast.unparse(call.func), ast.unparse(expression))
            )
            return
        if isinstance(expression, ast.Name) and expression.id in assignment_aliases:
            for source in assignment_aliases[expression.id]:
                inspect_callback_value(
                    source, scope, call, resolving + (expression.id,)
                )
            return
        if unknown or not targets:
            callback_unresolved_details.append(
                (scope.name, ast.unparse(call.func), ast.unparse(expression))
            )
            return
        local_targets = {
            target for target in targets if target.startswith(local_function_prefix)
        }
        if local_targets:
            for target in local_targets:
                helper_name = target[len(local_function_prefix) :].split(".", 1)[0]
                helper = local_functions.get(helper_name)
                if helper is None:
                    callback_unresolved_details.append(
                        (scope.name, ast.unparse(call.func), ast.unparse(expression))
                    )
                    continue
                helper_arguments = [
                    *helper.args.posonlyargs,
                    *helper.args.args,
                    *helper.args.kwonlyargs,
                ]
                if helper.args.vararg is not None:
                    helper_arguments.append(helper.args.vararg)
                if helper.args.kwarg is not None:
                    helper_arguments.append(helper.args.kwarg)
                # A pytest callback receives values from its caller's runtime
                # iteration. Do not assume a named local callback is safe when
                # its formal arguments can alias a framework namespace.
                ambiguous_binding_names.update(
                    argument.arg for argument in helper_arguments
                )
            enqueue_local_targets(local_targets)
            if local_targets != targets:
                callback_unresolved_details.append(
                    (scope.name, ast.unparse(call.func), ast.unparse(expression))
                )
            return
        if targets.issubset(safe_callback_targets):
            return
        callback_unresolved_details.append(
            (scope.name, ast.unparse(call.func), ast.unparse(expression))
        )

    def inspect_passed_callable_references(expression, scope, call):
        for node in ast.walk(expression):
            if isinstance(node, (ast.Name, ast.Attribute)):
                targets, _unknown = resolve_api_expression(node, strict_names=True)
                if uses_blocked_api(targets) or uses_blocked_dynamic_code(targets):
                    callback_blocked_details.append(
                        (scope.name, ast.unparse(call.func), ast.unparse(node))
                    )
            if isinstance(node, ast.Call):
                targets, _unknown = resolve_api_expression(node, strict_names=True)
                if uses_blocked_api(targets) or uses_blocked_dynamic_code(targets):
                    callback_blocked_details.append(
                        (scope.name, ast.unparse(call.func), ast.unparse(node))
                    )
            if isinstance(node, ast.Lambda):
                inspect_callback_value(node, scope, call)

    def callback_uses_for(scope):
        for node in walk_scope(scope):
            if not isinstance(node, ast.Call):
                continue
            positional_slots = callback_slots_for(node)
            (
                mutation_positions,
                mutation_keywords,
                is_mutator,
            ) = mutation_value_positions(scope, node)
            target_paths = framework_mutator_target_paths(scope, node)
            if target_paths:
                framework_api_mutations.append(
                    (node.lineno, ast.unparse(node.func), tuple(sorted(target_paths)))
                )
            if is_mutator and (
                any(isinstance(argument, ast.Starred) for argument in node.args)
                or any(keyword.arg is None for keyword in node.keywords)
            ):
                callback_unresolved_details.append(
                    (
                        scope.name,
                        ast.unparse(node.func),
                        "starred or expanded mutator arguments",
                    )
                )
            for index, argument in enumerate(node.args):
                inspect_passed_callable_references(argument, scope, node)
                if index in mutation_positions:
                    if not is_verified_live_noncallable_mutation_value(
                        scope, node, argument
                    ):
                        inspect_callback_value(argument, scope, node)
                elif index in positional_slots:
                    inspect_callback_value(argument, scope, node)
            for keyword in node.keywords:
                inspect_passed_callable_references(keyword.value, scope, node)
                if keyword.arg in mutation_keywords:
                    if not is_verified_live_noncallable_mutation_value(
                        scope, node, keyword.value
                    ):
                        inspect_callback_value(keyword.value, scope, node)
                elif keyword.arg in callback_keyword_names:
                    inspect_callback_value(keyword.value, scope, node)

    def is_verified_host_resolver_call(scope, call):
        if (
            source_text is not None
            or scope.name != "test_c65_default_repository_resolver"
            or not isinstance(call.func, ast.Name)
            or call.func.id != "resolver"
            or len(call.args) != 1
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "resolver_context"
            or call.keywords
        ):
            return False

        def single_assignment(name):
            values = assignment_aliases.get(name, ())
            return values[0] if len(values) == 1 else None

        resolver_source = single_assignment("resolver")
        controller_source = single_assignment("controller")
        controller_type_source = single_assignment("maintenance_controller_type")
        controller_module_source = single_assignment("controller_module")
        return (
            isinstance(resolver_source, ast.Attribute)
            and resolver_source.attr == "_repair_host_evidence_resolver"
            and isinstance(resolver_source.value, ast.Name)
            and resolver_source.value.id == "controller"
            and isinstance(controller_source, ast.Call)
            and isinstance(controller_source.func, ast.Name)
            and controller_source.func.id == "maintenance_controller_type"
            and isinstance(controller_type_source, ast.Call)
            and isinstance(controller_type_source.func, ast.Name)
            and controller_type_source.func.id == "getattr"
            and len(controller_type_source.args) == 2
            and isinstance(controller_type_source.args[0], ast.Name)
            and controller_type_source.args[0].id == "controller_module"
            and isinstance(controller_type_source.args[1], ast.Constant)
            and controller_type_source.args[1].value == "MaintenanceController"
            and isinstance(controller_module_source, ast.Call)
            and isinstance(controller_module_source.func, ast.Name)
            and controller_module_source.func.id == "import_module"
            and controller_module_source.args
            and isinstance(controller_module_source.args[0], ast.Constant)
            and controller_module_source.args[0].value
            == "automation.inago.maintenance.controller"
        )

    def validate_nested_definition_headers(scope):
        nested_definitions = [
            node
            for node in ast.walk(scope)
            if node is not scope
            and isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            )
        ]
        assert not any(
            isinstance(node, ast.ClassDef) for node in nested_definitions
        ), "C65 must not execute nested class bodies in guarded scopes"

        nonliteral_defaults = []
        decorated_nested_functions = []
        unsafe_nested_annotations = []
        for nested in nested_definitions:
            if isinstance(nested, ast.Lambda):
                arguments = nested.args
                decorators = ()
                annotations = ()
            elif isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = nested.args
                decorators = nested.decorator_list
                annotations = [
                    argument.annotation
                    for argument in (
                        *arguments.posonlyargs,
                        *arguments.args,
                        *arguments.kwonlyargs,
                        *((arguments.vararg,) if arguments.vararg is not None else ()),
                        *((arguments.kwarg,) if arguments.kwarg is not None else ()),
                    )
                    if argument.annotation is not None
                ]
                if nested.returns is not None:
                    annotations.append(nested.returns)
            else:
                continue
            defaults = [
                *arguments.defaults,
                *(value for value in arguments.kw_defaults if value is not None),
            ]
            nonliteral_defaults.extend(
                value for value in defaults if not is_literal_data(value)
            )
            decorated_nested_functions.extend(decorators)
            if not postponed_annotations:
                unsafe_nested_annotations.extend(
                    expression
                    for expression in annotations
                    if not is_safe_runtime_annotation(expression)
                )
        assert (
            not nonliteral_defaults
        ), "C65 must use literal-only nested function and lambda defaults"
        assert (
            not decorated_nested_functions
        ), "C65 must not execute nested function decorators in guarded scopes"
        assert (
            not unsafe_nested_annotations
        ), "C65 must use safe nested annotations or verified postponed annotations"

    def validate_reachable_scope_imports(scope):
        for node in walk_scope(scope):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert is_safe_reachable_import(
                    node
                ), "C65 must use verified imports in the target and reachable helpers"
            if not isinstance(node, ast.Call):
                continue
            targets, unknown = resolve_api_expression(node.func, strict_names=True)
            if "importlib.import_module" in targets:
                module_name = (
                    node.args[0].value
                    if node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    else None
                )
                assert (
                    not unknown and module_name in safe_dynamic_import_modules
                ), "C65 must use verified dynamic imports in the target and reachable helpers"
            elif "builtins.__import__" in targets:
                module_name = (
                    node.args[0].value
                    if node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    else None
                )
                assert (
                    not unknown and module_name in safe_dynamic_import_modules
                ), "C65 must use verified dynamic imports in the target and reachable helpers"

    def resolved_decorator(decorator):
        if not isinstance(decorator, ast.Call):
            return resolve_api_expression(decorator, strict_names=True)
        function_targets, function_unknown = resolve_api_expression(
            decorator.func, strict_names=True
        )
        if function_targets.intersection({"getattr", "builtins.getattr"}):
            return resolve_api_expression(decorator, strict_names=True)
        return function_targets, function_unknown

    def nested_decorator_uses(decorator):
        uses = [resolved_decorator(decorator)]
        for expression in ast.walk(decorator):
            if isinstance(expression, ast.Call):
                uses.append(resolve_api_expression(expression.func, strict_names=True))
            elif isinstance(expression, ast.Attribute):
                resolved = resolve_api_expression(expression, strict_names=True)
                if strict_dynamic_calls or uses_blocked_api(resolved[0]):
                    uses.append(resolved)
        return uses

    def module_pytestmark_uses():
        uses = []
        mutated = False
        direct_assignment_names = set()
        for node in module_scope_nodes:
            if isinstance(node, ast.Import):
                if any(
                    (alias.asname or alias.name.split(".", 1)[0]) == "pytestmark"
                    for alias in node.names
                ):
                    mutated = True
            elif isinstance(node, ast.ImportFrom):
                if any(
                    (alias.asname or alias.name) == "pytestmark" for alias in node.names
                ):
                    mutated = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else (node.target,)
                )
                if any(not isinstance(target, ast.Name) for target in targets):
                    mutated = True
                    continue
                mark_targets = [
                    target_node
                    for target in targets
                    for target_node in ast.walk(target)
                    if isinstance(target_node, ast.Name)
                    and target_node.id == "pytestmark"
                ]
                if not mark_targets:
                    continue
                direct_assignment_names.update(id(target) for target in mark_targets)
                value = node.value
                for expression in ast.walk(value):
                    if isinstance(expression, ast.Call):
                        uses.append(
                            resolve_api_expression(expression.func, strict_names=True)
                        )
                    elif isinstance(expression, ast.Attribute):
                        uses.append(
                            resolve_api_expression(expression, strict_names=True)
                        )
                if isinstance(value, ast.Name):
                    uses.append(resolve_api_expression(value, strict_names=True))
            elif isinstance(node, ast.AugAssign):
                if (
                    not isinstance(node.target, ast.Name)
                    or node.target.id == "pytestmark"
                ):
                    mutated = True
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                if not isinstance(node.target, ast.Name):
                    mutated = True
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                if any(
                    item.optional_vars is not None
                    and not isinstance(item.optional_vars, ast.Name)
                    for item in node.items
                ):
                    mutated = True
            elif isinstance(node, ast.Match):
                if any(
                    (
                        isinstance(pattern_node, (ast.MatchAs, ast.MatchStar))
                        and pattern_node.name == "pytestmark"
                    )
                    or (
                        isinstance(pattern_node, ast.MatchMapping)
                        and pattern_node.rest == "pytestmark"
                    )
                    for case in node.cases
                    for pattern_node in ast.walk(case.pattern)
                ):
                    mutated = True
            elif isinstance(node, ast.Name) and node.id == "pytestmark":
                if (
                    isinstance(node.ctx, ast.Load)
                    or id(node) not in direct_assignment_names
                ):
                    mutated = True
            elif isinstance(node, ast.Call) and any(
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id in {"globals", "locals", "vars"}
                for expression in ast.walk(node)
            ):
                mutated = True
        return uses, mutated

    def enqueue_autouse_fixtures():
        for candidate in ast.walk(module):
            if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in candidate.decorator_list:
                decorator_expression = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                decorator_targets, decorator_unknown = resolve_api_expression(
                    decorator_expression, strict_names=True
                )
                if (
                    decorator_unknown
                    or not decorator_targets
                    or uses_blocked_api(decorator_targets)
                    or any(
                        target.split(".", 1)[0] not in {"pytest", "unittest"}
                        and target.split(".", 1)[0] != "builtins"
                        for target in decorator_targets
                    )
                ):
                    raise AssertionError(
                        "C65 must not have skip or xfail decorators or dynamic assignment"
                    )
                if not isinstance(decorator, ast.Call):
                    continue
                fixture_decorator = any(
                    target.endswith(".fixture") for target in decorator_targets
                )
                autouse_status = _c65_fixture_autouse_status(decorator)
                if fixture_decorator and autouse_status is None:
                    raise AssertionError(
                        "C65 must use literal fixture options for autouse detection"
                    )
                if not fixture_decorator and (
                    autouse_status is True
                    or (
                        autouse_status is None
                        and any(
                            keyword.arg in {"autouse", None}
                            for keyword in decorator.keywords
                        )
                    )
                ):
                    raise AssertionError(
                        "C65 must verify each ambiguous autouse fixture decorator"
                    )
                if autouse_status is True:
                    if decorator_unknown or not fixture_decorator:
                        raise AssertionError(
                            "C65 must verify each autouse fixture decorator"
                        )
                    if candidate not in inspected_helpers:
                        pending_helpers.append(candidate)

    inspected_helpers = []
    pending_helpers = []

    def add_helper_scope(scope):
        inspected_helpers.append(scope)
        scope_nodes = list(walk_scope(scope))
        for node in scope_nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported = alias.name
                    local = alias.asname or imported.split(".", 1)[0]
                    module_aliases.setdefault(local, set()).add(
                        imported if alias.asname else imported.split(".", 1)[0]
                    )
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                if any(alias.name == "*" for alias in node.names):
                    raise AssertionError(
                        "C65 must not use pytest or unittest star imports or arbitrary-module star imports"
                    )
                if node.level != 0:
                    continue
                for alias in node.names:
                    imported_api_aliases.setdefault(
                        alias.asname or alias.name, set()
                    ).add(f"{module_name}.{alias.name}")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    record_assignment_alias(target, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                record_assignment_alias(node.target, node.value)
            elif isinstance(node, ast.NamedExpr):
                record_assignment_alias(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                record_for_loop_aliases(node)
            elif isinstance(node, ast.comprehension):
                record_for_loop_aliases(node)
            elif isinstance(node, ast.Match):
                record_match_aliases(node)
        validate_reachable_scope_imports(scope)
        validate_nested_definition_headers(scope)

    def enqueue_local_targets(targets):
        for target in targets:
            if not target.startswith(local_function_prefix):
                continue
            local_name = target[len(local_function_prefix) :].split(".", 1)[0]
            if local_name == "_assert_c65_live_assertion_guard":
                continue
            helper = local_functions.get(local_name)
            if helper is not None and helper not in inspected_helpers:
                pending_helpers.append(helper)

    fixture_names_by_function = {}

    def enqueue_local_fixtures(scope):
        fixture_bindings = {}
        for candidate in local_function_definitions:
            for candidate_decorator in candidate.decorator_list:
                decorator_expression = (
                    candidate_decorator.func
                    if isinstance(candidate_decorator, ast.Call)
                    else candidate_decorator
                )
                targets, unknown = resolve_api_expression(
                    decorator_expression, strict_names=True
                )
                if not any(target.endswith(".fixture") for target in targets):
                    continue
                if unknown:
                    raise AssertionError("C65 must resolve local fixture decorators")
                if candidate.name in ambiguous_local_function_names:
                    raise AssertionError(
                        "C65 must not have ambiguous local fixture names"
                    )
                fixture_names = [candidate.name]
                if isinstance(candidate_decorator, ast.Call):
                    explicit_names = [
                        keyword.value
                        for keyword in candidate_decorator.keywords
                        if keyword.arg == "name"
                    ]
                    if len(explicit_names) > 1:
                        raise AssertionError("C65 must use one literal fixture name")
                    if explicit_names:
                        explicit_name = explicit_names[0]
                        if not isinstance(
                            explicit_name, ast.Constant
                        ) or not isinstance(explicit_name.value, str):
                            raise AssertionError(
                                "C65 must use literal local fixture names"
                            )
                        fixture_names = [explicit_name.value]
                for fixture_name in fixture_names:
                    previous = fixture_bindings.get(fixture_name)
                    if previous is not None and previous is not candidate:
                        raise AssertionError(
                            "C65 must not have ambiguous local fixture names"
                        )
                    fixture_bindings[fixture_name] = candidate
                fixture_names_by_function[candidate] = tuple(fixture_names)

        parameter_names = {
            argument.arg
            for argument in (
                *scope.args.posonlyargs,
                *scope.args.args,
                *scope.args.kwonlyargs,
            )
        }
        fixture_names = parameter_names.intersection(
            {*local_functions, *fixture_bindings}
        )
        if scope in fixture_names_by_function:
            for parameter_name in parameter_names:
                if parameter_name not in fixture_bindings:
                    # An injected fixture can come from an uninspected plugin
                    # or conftest. Treat its value as opaque unless a local
                    # fixture provider was resolved above.
                    ambiguous_binding_names.add(parameter_name)
        required_fixture_names = set()
        for decorator in scope.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            targets, unknown = resolve_api_expression(decorator.func, strict_names=True)
            if not any(target.endswith(".usefixtures") for target in targets):
                continue
            if unknown:
                raise AssertionError("C65 must resolve usefixtures decorators")
            for expression in (*decorator.args, *decorator.keywords):
                value = (
                    expression.value
                    if isinstance(expression, ast.keyword)
                    else expression
                )
                if not isinstance(value, ast.Constant) or not isinstance(
                    value.value, str
                ):
                    raise AssertionError("C65 must use literal fixture names")
                names = {
                    name.strip() for name in value.value.split(",") if name.strip()
                }
                fixture_names.update(names)
                required_fixture_names.update(names)
        for fixture_name in fixture_names:
            if fixture_name in ambiguous_local_function_names:
                raise AssertionError("C65 must not have ambiguous local fixture names")
            fixture = local_functions.get(fixture_name) or fixture_bindings.get(
                fixture_name
            )
            if fixture is None:
                if fixture_name in required_fixture_names:
                    raise AssertionError(
                        "C65 must verify each local usefixtures fixture"
                    )
                continue
            if fixture not in inspected_helpers:
                pending_helpers.append(fixture)

    validate_reachable_scope_imports(function)
    validate_nested_definition_headers(function)
    for targets, _unknown, _bare_name in callable_uses_for(function):
        enqueue_local_targets(targets)
    callback_uses_for(function)
    for decorator in function.decorator_list:
        enqueue_local_targets(resolved_decorator(decorator)[0])
    enqueue_local_fixtures(function)
    enqueue_autouse_fixtures()

    while pending_helpers:
        helper = pending_helpers.pop()
        if helper in inspected_helpers:
            continue
        add_helper_scope(helper)
        for targets, _unknown, _bare_name in callable_uses_for(helper):
            enqueue_local_targets(targets)
        callback_uses_for(helper)
        for decorator in helper.decorator_list:
            enqueue_local_targets(resolved_decorator(decorator)[0])
        enqueue_local_fixtures(helper)

    inspected_functions = [function, *inspected_helpers]

    def bind_local_call_arguments(scopes):
        for scope in scopes:
            scope_nodes = list(walk_scope(scope))
            for candidate in ast.walk(scope):
                if not isinstance(candidate, ast.Lambda):
                    continue
                lambda_arguments = [
                    *candidate.args.posonlyargs,
                    *candidate.args.args,
                    *candidate.args.kwonlyargs,
                ]
                if candidate.args.vararg is not None:
                    lambda_arguments.append(candidate.args.vararg)
                if candidate.args.kwarg is not None:
                    lambda_arguments.append(candidate.args.kwarg)
                # A callback's runtime argument comes from an enclosing
                # higher-order call. Unless that call's iterable is proven,
                # treat each lambda parameter as ambiguous when it is passed
                # to a local helper that may mutate a framework namespace.
                ambiguous_binding_names.update(
                    argument.arg for argument in lambda_arguments
                )
                scope_nodes.extend(walk_scope(candidate.body))
            for node in scope_nodes:
                if not isinstance(node, ast.Call):
                    continue
                targets, _unknown = resolve_api_expression(node.func, strict_names=True)
                for target in targets:
                    if not target.startswith(local_function_prefix):
                        continue
                    helper_name = target[len(local_function_prefix) :].split(".", 1)[0]
                    helper = local_functions.get(helper_name)
                    if helper is None:
                        continue
                    formal_arguments = [
                        *helper.args.posonlyargs,
                        *helper.args.args,
                        *helper.args.kwonlyargs,
                    ]
                    actual_by_name = {}
                    for index, actual in enumerate(node.args):
                        if isinstance(actual, ast.Starred):
                            continue
                        if index < len(formal_arguments):
                            actual_by_name[formal_arguments[index].arg] = actual
                    for keyword in node.keywords:
                        if keyword.arg is not None:
                            actual_by_name[keyword.arg] = keyword.value
                    for formal in formal_arguments:
                        actual = actual_by_name.get(formal.arg)
                        if actual is None:
                            ambiguous_binding_names.add(formal.arg)
                            continue
                        if (
                            helper_name == "_with_repair_scope"
                            and formal.arg == "response"
                            and isinstance(actual, ast.Call)
                            and resolve_api_expression(actual.func, strict_names=True)[
                                0
                            ]
                            == {
                                "tests_inago.test_maintenance_planning._make_plan_response"
                            }
                        ):
                            # The approved planning fixture helper returns the
                            # response mapping consumed by this local wrapper.
                            assignment_aliases.setdefault(formal.arg, []).append(
                                ast.Dict(keys=[], values=[])
                            )
                            continue
                        assignment_aliases.setdefault(formal.arg, []).append(actual)
                        if not framework_api_paths(actual) and not is_literal_data(
                            actual
                        ):
                            ambiguous_binding_names.add(formal.arg)

    bind_local_call_arguments(inspected_functions)

    for fixture in inspected_helpers:
        if not any(
            isinstance(decorator, ast.Call)
            and any(
                target.endswith(".fixture")
                for target in resolve_api_expression(decorator.func, strict_names=True)[
                    0
                ]
            )
            for decorator in fixture.decorator_list
        ):
            continue
        return_values = []
        opaque_yield_from = False
        for result_node in walk_scope(fixture):
            if not isinstance(result_node, (ast.Return, ast.Yield, ast.YieldFrom)):
                continue
            result_value = result_node.value
            if result_value is None:
                continue
            if isinstance(result_node, ast.YieldFrom):
                # A fixture's ``yield from`` produces each iterable element,
                # not the iterable object itself. Expand only plain literal
                # list/tuple elements; opaque or starred iterables are marked
                # ambiguous below so they cannot hide framework provenance.
                if isinstance(result_value, (ast.List, ast.Tuple)) and all(
                    not isinstance(element, ast.Starred)
                    for element in result_value.elts
                ):
                    return_values.extend(result_value.elts)
                else:
                    opaque_yield_from = True
                continue
            return_values.append(result_value)
        provider_names = fixture_names_by_function.get(fixture, (fixture.name,))
        for provider_name in provider_names:
            if return_values:
                assignment_aliases.setdefault(provider_name, []).extend(return_values)
            if not return_values or opaque_yield_from:
                # A dependency with no statically visible result (or an
                # opaque yield-from branch) is not safe to treat as ordinary.
                ambiguous_binding_names.add(provider_name)

    framework_api_mutations.extend(find_framework_api_mutations(inspected_functions))
    callable_uses = [
        use for scope in inspected_functions for use in callable_uses_for(scope)
    ]
    decorator_uses = [
        use
        for scope in inspected_functions
        for decorator in scope.decorator_list
        for use in nested_decorator_uses(decorator)
    ]
    raise_uses = []
    for scope in inspected_functions:
        for node in walk_scope(scope):
            if isinstance(node, ast.Raise) and node.exc is not None:
                raised_expression = (
                    node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                )
                raise_uses.append(
                    resolve_api_expression(raised_expression, strict_names=True)
                )
    assert not any(
        uses_blocked_dynamic_code(targets)
        for targets, _unknown, _bare_name in callable_uses
    ), "C65 must not execute dynamic code or invoke inline lambdas"
    assert not any(
        unknown and not bare_name for _targets, unknown, bare_name in callable_uses
    ), f"C65 must not call unresolved indirect targets: {unresolved_call_details!r}"
    assert not any(
        uses_blocked_api(targets) for targets, _unknown in raise_uses
    ), "C65 must not raise SkipTest to bypass its assertions"
    assert not any(
        uses_blocked_api(targets) for targets, _unknown, _bare_name in callable_uses
    ), "C65 must not skip or xfail its guard assertions"
    assert not any(
        unknown and (bare_name or strict_dynamic_calls)
        for _targets, unknown, bare_name in callable_uses
    ), "C65 must not call an alias with a dynamic assignment"
    assert not callback_blocked_details, (
        "C65 must not pass skip, xfail, or dynamic-code APIs as values/callbacks: "
        f"{callback_blocked_details!r}"
    )
    assert not callback_unresolved_details, (
        "C65 must not pass unresolved callbacks or indirect callback bodies: "
        f"{callback_unresolved_details!r}"
    )
    assert not framework_api_mutations, (
        "C65 must not mutate pytest or unittest API objects or aliases: "
        f"{framework_api_mutations!r}"
    )
    assert not any(
        uses_blocked_api(targets) for targets, _unknown in decorator_uses
    ), "C65 must not have skip or xfail decorators"
    assert not any(
        unknown for _targets, unknown in decorator_uses
    ), "C65 must not use a decorator alias with a dynamic assignment"
    ambient_mark_uses, module_mark_mutated = module_pytestmark_uses()
    assert not module_mark_mutated, "C65 must not mutate module-level pytestmark"
    assert not any(
        uses_blocked_api(targets) for targets, _unknown in ambient_mark_uses
    ), "C65 must not inherit module-level skip or xfail marks"
    assert not any(
        unknown for _targets, unknown in ambient_mark_uses
    ), "C65 must not inherit unresolved module-level marks"
    assert (
        not module_import_time_calls
    ), "C65 must not execute module-level import-time calls"
    assert all(
        is_safe_module_import(statement)
        for statement in module.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
    ), "C65 must use verified top-level imports"

    def is_static_module_value(expression):
        return is_literal_data(expression)

    def is_static_module_annotation(expression):
        return postponed_annotations or is_safe_runtime_annotation(expression)

    def is_safe_module_decorator(decorator):
        if not isinstance(decorator, (ast.Name, ast.Attribute, ast.Call)):
            return False
        if isinstance(decorator, ast.Call):
            targets, unknown = resolve_api_expression(decorator.func, strict_names=True)
            if unknown or not targets:
                return False
            if targets == {"pytest.fixture"}:
                if decorator.args or any(
                    keyword.arg != "autouse"
                    or not isinstance(keyword.value, ast.Constant)
                    or not isinstance(keyword.value.value, bool)
                    for keyword in decorator.keywords
                ):
                    return False
                return len(decorator.keywords) <= 1
            if targets == {"pytest.mark.usefixtures"}:
                return (
                    not decorator.keywords
                    and bool(decorator.args)
                    and all(
                        isinstance(argument, ast.Constant)
                        and isinstance(argument.value, str)
                        for argument in decorator.args
                    )
                )
            return False
        targets, unknown = resolve_api_expression(decorator, strict_names=True)
        return not unknown and targets == {"pytest.fixture"}

    def is_safe_module_statement(index, statement):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            return True
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            return is_safe_module_import(statement)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return all(
                is_safe_module_decorator(decorator)
                for decorator in statement.decorator_list
            )
        if isinstance(statement, ast.Assign):
            return (
                bool(statement.targets)
                and all(isinstance(target, ast.Name) for target in statement.targets)
                and is_static_module_value(statement.value)
            )
        if isinstance(statement, ast.AnnAssign):
            return (
                isinstance(statement.target, ast.Name)
                and is_static_module_annotation(statement.annotation)
                and (statement.value is None or is_static_module_value(statement.value))
            )
        return False

    assert all(
        is_safe_module_statement(index, statement)
        for index, statement in enumerate(module.body)
    ), "C65 must use data-only module declarations and verified module-level function decorators"

    core_index = core_assertions[0][0]
    assess_assignments = [
        index
        for index, statement in enumerate(body)
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "assessment"
            for target in ast.walk(statement)
        )
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and statement.value.func.attr == "assess_patch"
    ]
    assert any(
        index < core_index for index in assess_assignments
    ), "C65 core assertion must follow the live assess_patch call"


@pytest.fixture
def _c65_live_assertion_guard():
    _assert_c65_live_assertion_guard()


_C65_GUARD_SOURCE_FINGERPRINTS = {
    "test_c65_default_repository_resolver": "e07fb59a2e434044d995acb3453ec37aecbe61def4060b47c4c6331b1ad905a0",
    "_c65_live_assertion_guard": "3cad232945ba1a065917e8b6368ec58942f5b8dd0c4d7572ab19349367bc5395",
    "_c65_fixture_autouse_status": "fa809ca020be6f8f0916f7df906c4b16854d80821fa24e37bd67579b24d17438",
    "_assert_c65_live_assertion_guard": "647118d010d357fc75805f042e8131d3c8f6937f93590d4a861c7aaed0cd81ed",
    "_c65_guard_source_fingerprints": "02b1b217b4c913bafdc0135b9e43b80e16dd52daadc81826fc4cf005b006f030",
    "_make_tiny_git_checkout": "d9271948e99f7af0fb2d044a03c5d6c091466751378f0f92a097454f9c5e32ce",
    "module_pytestmark": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "module_function_decorators": "fba3c0b9f6aa7490f8357f09cfb8d2170a71fd1fdc6e65d441af4cfa2ee04185",
    "module_import_time_calls": "5dbe45a046998fdf3b602fc82c955e3b5978c22d74e60ef827ea4ac477d68f14",
    "autouse_fixture:_cleanup_tiny_git_checkouts": "495c64abd3414648cda3eddd5798e72f25a22cd60c0f52c60390a3df80b0994b",
}


def _c65_guard_source_fingerprints():
    import ast

    source = Path(__file__).read_text(encoding="utf-8")
    module = ast.parse(source, filename=__file__)
    target_names = tuple(_C65_GUARD_SOURCE_FINGERPRINTS)
    function_names = {
        name
        for name in target_names
        if name
        not in {
            "module_pytestmark",
            "module_function_decorators",
            "module_import_time_calls",
        }
        and not name.startswith("autouse_fixture:")
    }
    target_nodes = {
        node.name: [node]
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in function_names
    }

    def walk_module_scope(root):
        yield root
        for child in ast.iter_child_nodes(root):
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            ):
                continue
            yield from walk_module_scope(child)

    def walk_module_statements(root):
        for child in ast.iter_child_nodes(root):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield child
                continue
            if isinstance(child, ast.ClassDef):
                yield child
                continue
            yield child
            yield from walk_module_statements(child)

    module_pytestmark_nodes = []
    direct_pytestmark_assignment_names = set()
    for node in walk_module_scope(module):
        if isinstance(node, ast.Import) and any(
            (alias.asname or alias.name.split(".", 1)[0]) == "pytestmark"
            for alias in node.names
        ):
            module_pytestmark_nodes.append(node)
        elif isinstance(node, ast.ImportFrom) and any(
            (alias.asname or alias.name) == "pytestmark" for alias in node.names
        ):
            module_pytestmark_nodes.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            mark_targets = [
                target_node
                for target in targets
                for target_node in ast.walk(target)
                if isinstance(target_node, ast.Name) and target_node.id == "pytestmark"
            ]
            if mark_targets:
                module_pytestmark_nodes.append(node)
                direct_pytestmark_assignment_names.update(
                    id(target) for target in mark_targets
                )
        elif isinstance(node, ast.AugAssign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in ast.walk(node.target)
        ):
            module_pytestmark_nodes.append(node)
        elif isinstance(node, ast.Match) and any(
            (
                isinstance(pattern_node, (ast.MatchAs, ast.MatchStar))
                and pattern_node.name == "pytestmark"
            )
            or (
                isinstance(pattern_node, ast.MatchMapping)
                and pattern_node.rest == "pytestmark"
            )
            for case in node.cases
            for pattern_node in ast.walk(case.pattern)
        ):
            module_pytestmark_nodes.append(node)
        elif isinstance(node, ast.Name) and node.id == "pytestmark":
            if (
                isinstance(node.ctx, ast.Load)
                or id(node) not in direct_pytestmark_assignment_names
            ):
                module_pytestmark_nodes.append(node)
        elif isinstance(node, ast.Call) and any(
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id in {"globals", "locals", "vars"}
            for expression in ast.walk(node)
        ):
            module_pytestmark_nodes.append(node)
    target_nodes["module_pytestmark"] = module_pytestmark_nodes
    target_nodes["module_function_decorators"] = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.decorator_list
    ]
    module_statement_nodes = list(walk_module_statements(module))
    module_scope_calls = [
        node for node in walk_module_scope(module) if isinstance(node, ast.Call)
    ]
    module_function_nodes = [
        node
        for node in module_statement_nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for function_node in module_function_nodes:
        arguments = function_node.args
        header_expressions = [
            *arguments.defaults,
            *(value for value in arguments.kw_defaults if value is not None),
            *function_node.decorator_list,
        ]
        all_arguments = [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ]
        if arguments.vararg is not None:
            all_arguments.append(arguments.vararg)
        if arguments.kwarg is not None:
            all_arguments.append(arguments.kwarg)
        header_expressions.extend(
            argument.annotation
            for argument in all_arguments
            if argument.annotation is not None
        )
        if function_node.returns is not None:
            header_expressions.append(function_node.returns)
        for expression in header_expressions:
            module_scope_calls.extend(
                node for node in ast.walk(expression) if isinstance(node, ast.Call)
            )
    module_functions = {node.name: node for node in module_function_nodes}
    module_import_time_nodes = []
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else ()
        if isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        if any(
            isinstance(target, ast.Name)
            and target.id == "_C65_GUARD_SOURCE_FINGERPRINTS"
            for target in targets
        ):
            continue
        module_import_time_nodes.append(node)
    module_import_time_nodes.extend(module_scope_calls)
    for call in module_scope_calls:
        if isinstance(call.func, ast.Name) and call.func.id in module_functions:
            module_import_time_nodes.append(module_functions[call.func.id])
    target_nodes["module_import_time_calls"] = sorted(
        {id(node): node for node in module_import_time_nodes}.values(),
        key=lambda node: (node.lineno, node.col_offset),
    )

    pytest_module_aliases = {"pytest"}
    pytest_fixture_aliases = set()
    for node in walk_module_scope(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest":
                    pytest_module_aliases.add(alias.asname or "pytest")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == "pytest":
                pytest_fixture_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "fixture"
                )

    def dotted_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = dotted_name(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None

    def is_pytest_fixture_expression(expression):
        if isinstance(expression, ast.Name):
            return expression.id in pytest_fixture_aliases
        if not isinstance(expression, ast.Attribute) or expression.attr != "fixture":
            return False
        dotted = dotted_name(expression)
        return bool(dotted) and dotted.split(".", 1)[0] in pytest_module_aliases

    updated_fixture_aliases = True
    while updated_fixture_aliases:
        updated_fixture_aliases = False
        for node in walk_module_scope(module):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if not is_pytest_fixture_expression(node.value):
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id not in pytest_fixture_aliases
                ):
                    pytest_fixture_aliases.add(target.id)
                    updated_fixture_aliases = True

    def is_pytest_fixture_call(function):
        return is_pytest_fixture_expression(function)

    autouse_fixture_nodes = {}
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and is_pytest_fixture_call(decorator.func)
            and _c65_fixture_autouse_status(decorator) is not False
            for decorator in node.decorator_list
        ):
            autouse_fixture_nodes[f"autouse_fixture:{node.name}"] = [node]
    target_nodes.update(autouse_fixture_nodes)
    assert set(target_nodes) == set(target_names)

    source_lines = source.splitlines()

    def canonical_source(node):
        decorators = getattr(node, "decorator_list", ())
        first_line = min([node.lineno, *(item.lineno for item in decorators)])
        last_line = node.end_lineno
        # Exact normalized source is deliberate: Python's AST and tokenizer
        # representations differ across supported minor versions (notably
        # f-strings). Normalizing line endings while retaining all source
        # tokens and whitespace makes this guard fingerprint version-stable.
        return "\n".join(source_lines[first_line - 1 : last_line])

    return {
        name: hashlib.sha256(
            repr([canonical_source(node) for node in target_nodes[name]]).encode(
                "utf-8"
            )
        ).hexdigest()
        for name in target_names
    }


@pytest.mark.usefixtures("_c65_live_assertion_guard")
def test_c65_default_repository_resolver(tmp_path, monkeypatch):
    from tests_inago.test_maintenance_planning import (
        BASE_TIME,
        _complete_plan,
        _make_plan_response,
        _start_planning_attempt,
    )

    checkout_root = _make_tiny_git_checkout(
        tmp_path / "checkout",
        {
            "automation/inago/maintenance/fix.py": "value = 1\n",
            "automation/inago/maintenance/unowned.py": "value = 'original'\n",
            "tests_inago/test_scope.py": ("def test_scope():\n    assert 1 == 1\n"),
        },
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    planning_module = import_module("tests_inago.test_maintenance_planning")
    investigation_support = import_module(
        "tests_inago.maintenance_investigation_support"
    )
    monkeypatch.setattr(planning_module, "BASE_COMMIT", base_commit)
    monkeypatch.setattr(investigation_support, "BASE_COMMIT", base_commit)

    (
        store,
        planning_controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        plan_attempt,
    ) = _start_planning_attempt(tmp_path / "workflow")
    expected_scope = {
        "owned_paths": [
            "automation/inago/maintenance/fix.py",
            "tests_inago/test_scope.py",
        ]
    }
    plan_outcome = _complete_plan(
        planning_controller,
        lease,
        plan_context,
        attempts_root,
        plan_output,
        _with_repair_scope(
            _make_plan_response(plan_context), expected_scope["owned_paths"]
        ),
    )
    repair_attempt = planning_controller.begin_repair_attempt(
        job_id=plan_context["job_id"],
        attempts_root=attempts_root,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=18),
    )
    approved_plan = json.loads(
        store.read_attempt_artifact(
            attempts_root=attempts_root,
            relative_path=plan_outcome.output_path,
            expected_sha256=plan_outcome.output_sha256,
        )
    )
    assert approved_plan["repair_scope"] == expected_scope

    (checkout_root / "automation/inago/maintenance/fix.py").write_text(
        "value = 2\n", encoding="utf-8"
    )
    (checkout_root / "tests_inago/test_scope.py").write_text(
        "def test_scope():\n    assert True\n", encoding="utf-8"
    )
    repair_module = import_module("automation.inago.maintenance.repair")
    controller_module = import_module("automation.inago.maintenance.controller")
    resolver_type = getattr(repair_module, "RepositoryRepairEvidenceResolver")
    context_type = getattr(repair_module, "RepairHostEvidenceContext")
    maintenance_controller_type = getattr(controller_module, "MaintenanceController")
    controller = maintenance_controller_type(
        store,
        identity_resolver=lambda _: "unused",
        repair_checkout_root=checkout_root,
    )
    resolver = controller._repair_host_evidence_resolver
    assert isinstance(resolver, resolver_type)
    resolver_context = context_type(
        job_id=plan_context["job_id"],
        attempt_id=repair_attempt.attempt_id,
        base_commit=base_commit,
        approved_plan=approved_plan,
        attempts_root=attempts_root,
    )
    evidence = resolver(resolver_context)
    assert evidence.changed_paths == (
        "automation/inago/maintenance/fix.py",
        "tests_inago/test_scope.py",
    )
    assert evidence.owned_paths == tuple(expected_scope["owned_paths"])
    assert evidence.current_code_sha256 == repair_module.repository_code_sha256(
        checkout_root
    )
    assert tuple(evidence.baseline_assertions) == ("tests_inago/test_scope.py",)
    assert tuple(evidence.candidate_assertions) == ("tests_inago/test_scope.py",)
    assessment = repair_module.RepairController().assess_patch(
        changed_paths=evidence.changed_paths,
        owned_paths=evidence.owned_paths,
        baseline_assertions=evidence.baseline_assertions,
        candidate_assertions=evidence.candidate_assertions,
    )
    assert assessment.accepted is False
    assert "assertion_expression_weakened_or_changed" in assessment.reason_codes

    (checkout_root / "automation/inago/maintenance/unowned.py").write_text(
        "value = 'changed outside approved scope'\n", encoding="utf-8"
    )
    with pytest.raises(repair_module.RepairContractError) as outside_scope:
        resolver(resolver_context)
    assert outside_scope.value.code == "repair_changed_path_unowned"


def test_c66_full_regression_receipt_contract(tmp_path, monkeypatch):
    from dataclasses import replace
    import sqlite3

    from automation.inago.maintenance.repair import (
        RepairContractError,
        RepairController,
        RepairHostEvidence,
        RepairReviewEvidence,
        RepairTestLedger,
    )
    from automation.inago.maintenance.store import (
        MaintenanceStoreError,
    )
    from tests_inago.test_maintenance_planning import BASE_TIME

    repair_module = import_module("automation.inago.maintenance.repair")

    _assert_c65_live_assertion_guard()
    assert _c65_guard_source_fingerprints() == _C65_GUARD_SOURCE_FINGERPRINTS

    valid_c65_guard_source = (
        "def test_c65_default_repository_resolver(monkeypatch):\n"
        "    import pytest as controller\n"
        "    assessment = controller.assess_patch()\n"
        "    assert assessment.accepted is False\n"
        '    assert "assertion_expression_weakened_or_changed" '
        "in assessment.reason_codes\n"
    )
    reachable_helper_import_skip_source = (
        "import pytest as pt\n\n"
        "def local_skip_helper():\n"
        "    from arbitrary_module import skip\n"
        "    skip('import side effect bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_skip_helper()\n"
    )
    with pytest.raises(AssertionError, match="verified imports"):
        _assert_c65_live_assertion_guard(reachable_helper_import_skip_source)

    live_indirect_callable_source = (
        "import pytest as pt\n\n"
        "def local_dynamic_helper():\n"
        "    (getattr(pt, runtime_name))('indirect skip')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_dynamic_helper()\n"
    )
    with pytest.raises(AssertionError, match="unresolved indirect targets"):
        _assert_c65_live_assertion_guard(
            live_indirect_callable_source,
            _strict_dynamic_calls=False,
        )

    builtins_dict_exec_source = (
        "import builtins as dynamic_code\n\n"
        "def local_dynamic_helper():\n"
        "    dynamic_code.__dict__['exec'](\"import pytest; pytest.skip('dynamic')\")\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_dynamic_helper()\n"
    )
    with pytest.raises(AssertionError, match="unresolved indirect targets"):
        _assert_c65_live_assertion_guard(
            builtins_dict_exec_source,
            _strict_dynamic_calls=False,
        )

    builtins_dict_import_source = (
        "import builtins as dynamic_code\n\n"
        "def local_dynamic_helper():\n"
        "    dynamic_code.__dict__['__import__']('pytest').skip('dynamic')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_dynamic_helper()\n"
    )
    with pytest.raises(AssertionError, match="unresolved indirect targets"):
        _assert_c65_live_assertion_guard(
            builtins_dict_import_source,
            _strict_dynamic_calls=False,
        )

    higher_order_skip_callback_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted(['a'], key=pt.skip)\n"
    )
    with pytest.raises(AssertionError, match="pass skip, xfail"):
        _assert_c65_live_assertion_guard(higher_order_skip_callback_source)

    higher_order_lambda_skip_callback_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted(['a'], key=lambda value: pt.skip('callback bypass'))\n"
    )
    with pytest.raises(AssertionError, match="pass skip, xfail"):
        _assert_c65_live_assertion_guard(higher_order_lambda_skip_callback_source)

    unresolved_higher_order_callback_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted(['a'], key=runtime_callback)\n"
    )
    with pytest.raises(AssertionError, match="unresolved callbacks"):
        _assert_c65_live_assertion_guard(unresolved_higher_order_callback_source)

    direct_local_factory_call_source = (
        "import pytest as pt\n\n"
        "def make_skip():\n"
        "    return pt.skip\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    make_skip()('factory skip')\n"
    )
    with pytest.raises(AssertionError, match="unresolved indirect targets"):
        _assert_c65_live_assertion_guard(direct_local_factory_call_source)

    assigned_local_factory_alias_source = (
        "import pytest as pt\n\n"
        "def make_skip():\n"
        "    return pt.skip\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    callback = make_skip()\n"
        + "    callback('factory alias skip')\n"
    )
    with pytest.raises(
        AssertionError,
        match="unresolved indirect targets|dynamic assignment",
    ):
        _assert_c65_live_assertion_guard(assigned_local_factory_alias_source)

    monkeypatch_skip_redirection_source = (
        "import pytest as pt\n\n"
        "def local_redirection_helper(monkeypatch):\n"
        "    monkeypatch.setattr(pt, 'fail', pt.skip)\n"
        "    pt.fail('redirected skip')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_redirection_helper(monkeypatch)\n"
    )
    with pytest.raises(
        AssertionError,
        match="pass skip, xfail|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(monkeypatch_skip_redirection_source)

    monkeypatch_getattr_skip_redirection_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pt, 'fail', getattr(pt, 'skip'))\n"
        + "    pt.fail('redirected skip')\n"
    )
    with pytest.raises(
        AssertionError,
        match="pass skip, xfail|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(monkeypatch_getattr_skip_redirection_source)

    monkeypatch_local_skip_replacement_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('local replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pt, 'fail', local_skip)\n"
        + "    pt.fail('redirected skip')\n"
    )
    with pytest.raises(
        AssertionError,
        match="skip or xfail|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(monkeypatch_local_skip_replacement_source)

    builtin_setattr_local_skip_replacement_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('local replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    setattr(pt, 'fail', local_skip)\n"
        + "    pt.fail('redirected skip')\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(builtin_setattr_local_skip_replacement_source)

    monkeypatch_local_factory_replacement_source = (
        "import pytest as pt\n\n"
        "def make_skip():\n"
        "    return pt.skip\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pt, 'fail', make_skip())\n"
        + "    pt.fail('redirected skip')\n"
    )
    with pytest.raises(
        AssertionError,
        match="unresolved callbacks|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(monkeypatch_local_factory_replacement_source)

    monkeypatch_dynamic_getattr_replacement_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pt, 'fail', getattr(pt, runtime_name))\n"
        + "    pt.fail('redirected skip')\n"
    )
    with pytest.raises(
        AssertionError,
        match="unresolved callbacks|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(monkeypatch_dynamic_getattr_replacement_source)

    monkeypatch_string_target_two_argument_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('string target replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr('pytest.fail', local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(monkeypatch_string_target_two_argument_source)

    monkeypatch_keyword_value_replacement_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('keyword replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pt, 'fail', value=local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(monkeypatch_keyword_value_replacement_source)

    monkeypatch_keyword_only_replacement_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('keyword-only replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(target=pt, name='fail', value=local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(monkeypatch_keyword_only_replacement_source)

    monkeypatch_string_target_keyword_overload_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('string target keyword overload bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(target='pytest.fail', name=local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(
            monkeypatch_string_target_keyword_overload_source
        )

    unbound_monkeypatch_string_target_keyword_overload_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('unbound string target keyword overload bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.MonkeyPatch.setattr("
        + "monkeypatch, target='pytest.fail', name=local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(
            unbound_monkeypatch_string_target_keyword_overload_source
        )

    unbound_monkeypatch_keyword_only_replacement_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('unbound keyword-only replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.MonkeyPatch.setattr("
        + "monkeypatch, target=pt, name='fail', value=local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(
            unbound_monkeypatch_keyword_only_replacement_source
        )

    unbound_monkeypatch_string_target_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('unbound string replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.MonkeyPatch.setattr(monkeypatch, 'pytest.fail', local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(unbound_monkeypatch_string_target_source)

    unbound_monkeypatch_attribute_target_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('unbound attribute replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.MonkeyPatch.setattr(monkeypatch, pt, 'fail', local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(unbound_monkeypatch_attribute_target_source)

    bound_monkeypatch_setitem_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('setitem replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setitem({}, 'callback', local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(bound_monkeypatch_setitem_source)

    unbound_monkeypatch_setitem_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('unbound setitem replacement bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.MonkeyPatch.setitem(monkeypatch, {}, 'callback', local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(unbound_monkeypatch_setitem_source)

    unbound_monkeypatch_keyword_setitem_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('unbound keyword setitem bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.MonkeyPatch.setitem("
        + "monkeypatch, dic={}, name='callback', value=local_skip)\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(unbound_monkeypatch_keyword_setitem_source)

    direct_pytest_attribute_assignment_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.fail = pt.skip\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(direct_pytest_attribute_assignment_source)

    pytest_module_dict_assignment_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.__dict__['fail'] = pt.skip\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(pytest_module_dict_assignment_source)

    pytest_module_dict_update_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('module dict update bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.__dict__.update({'fail': local_skip})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(pytest_module_dict_update_source)

    pytest_module_dict_setitem_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('module dict setitem bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.__dict__.__setitem__('fail', local_skip)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(pytest_module_dict_setitem_source)

    unbound_dict_update_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    dict.update(pt.__dict__, {'fail': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(unbound_dict_update_source)

    unbound_dict_setitem_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    dict.__setitem__(pt.__dict__, 'fail', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(unbound_dict_setitem_source)

    unbound_dict_init_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    dict.__init__(pt.__dict__, {'skip': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(unbound_dict_init_source)

    vars_module_dict_update_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    vars(pt).update({'skip': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(vars_module_dict_update_source)

    sys_modules_pytest_attribute_assignment_source = (
        "import pytest as pt\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sys.modules['pytest'].fail = pt.skip\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_pytest_attribute_assignment_source)

    sys_modules_pytest_dict_assignment_source = (
        "import pytest as pt\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sys.modules['pytest'].__dict__['fail'] = pt.skip\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_pytest_dict_assignment_source)

    sys_modules_pytest_alias_key_source = (
        "import pytest as pt\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    module_name = 'pytest'\n"
        + "    sys.modules[module_name].fail = pt.skip\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_pytest_alias_key_source)

    sys_modules_dynamic_key_source = (
        "import pytest as pt\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    module_name = runtime_module_name\n"
        + "    sys.modules[module_name].fail = pt.skip\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_dynamic_key_source)

    sys_modules_alias_registry_source = (
        "import pytest as pt\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    modules = sys.modules\n"
        + "    modules['pytest'].skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_alias_registry_source)

    helper_return_registry_source = (
        "import pytest as pt\n"
        "import sys\n\n"
        "def get_registry():\n"
        "    return sys.modules\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    get_registry()['pytest'].skip = str\n"
    )
    with pytest.raises(
        AssertionError,
        match="mutate pytest or unittest API|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(helper_return_registry_source)

    helper_return_namespace_source = (
        "import pytest as pt\n\n"
        "def get_namespace():\n"
        "    return vars(pt)\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    get_namespace().update({'skip': str})\n"
    )
    with pytest.raises(
        AssertionError,
        match="mutate pytest or unittest API|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(helper_return_namespace_source)

    pytest_module_assignment_alias_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    module = sys.modules['pytest']\n"
        + "    module.skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(pytest_module_assignment_alias_source)

    namespace_assignment_alias_source = (
        "import pytest as pt\n\n"
        "def get_namespace():\n"
        "    return vars(pt)\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    namespace = get_namespace()\n"
        + "    namespace.update({'skip': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(namespace_assignment_alias_source)

    helper_registry_container_alias_source = (
        "import sys\n\n"
        "def registry_box():\n"
        "    return [sys.modules]\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = registry_box()[0]\n"
        + "    registry.update({'automation.inago.maintenance.repair': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_registry_container_alias_source)

    helper_registry_dict_update_alias_source = (
        "import sys\n\n"
        "def registry_box():\n"
        "    return [sys.modules]\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = registry_box()[0]\n"
        + "    dict.update(registry, {'automation.inago.maintenance.repair': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_registry_dict_update_alias_source)

    helper_registry_monkeypatch_alias_source = (
        "import sys\n\n"
        "def registry_box():\n"
        "    return [sys.modules]\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = registry_box()[0]\n"
        + "    monkeypatch.setitem("
        + "registry, 'automation.inago.maintenance.repair', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_registry_monkeypatch_alias_source)

    container_held_framework_module_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    box = [pt]\n"
        + "    box[0].skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(container_held_framework_module_source)

    sys_modules_alias_update_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    modules = sys.modules\n"
        + "    modules.update({'automation.inago.maintenance.repair': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_alias_update_source)

    sys_modules_dict_setitem_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    dict.__setitem__("
        + "sys.modules, 'automation.inago.maintenance.repair', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_dict_setitem_source)

    sys_modules_monkeypatch_setitem_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setitem("
        + "sys.modules, 'automation.inago.maintenance.repair', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_monkeypatch_setitem_source)

    sys_modules_alias_augmented_assignment_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    modules = sys.modules\n"
        + "    modules |= {'automation.inago.maintenance.repair': str}\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_alias_augmented_assignment_source)

    pytest_namespace_alias_augmented_assignment_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    namespace = vars(pt)\n"
        + "    namespace |= {'skip': str}\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            pytest_namespace_alias_augmented_assignment_source
        )

    operator_setitem_pytest_namespace_source = (
        "import operator as op\n"
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    op.setitem(pt.__dict__, 'skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_setitem_pytest_namespace_source)

    operator_delitem_pytest_namespace_source = (
        "import operator as op\n"
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    op.delitem(pt.__dict__, 'skip')\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_delitem_pytest_namespace_source)

    operator_setitem_sys_modules_source = (
        "import operator as op\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    op.setitem("
        + "sys.modules, 'automation.inago.maintenance.repair', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_setitem_sys_modules_source)

    operator_delitem_sys_modules_source = (
        "import operator as op\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    op.delitem(sys.modules, 'automation.inago.maintenance.repair')\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_delitem_sys_modules_source)

    operator_setitem_alias_source = (
        "import operator as op\n"
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    patch_dict = op.setitem\n"
        + "    patch_dict(pt.__dict__, 'skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_setitem_alias_source)

    operator_ior_pytest_namespace_source = (
        "import operator as op\n"
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    op.ior(pt.__dict__, {'skip': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_ior_pytest_namespace_source)

    operator_ior_alias_pytest_namespace_source = (
        "import operator as op\n"
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    patch_mapping = op.ior\n"
        + "    patch_mapping(pt.__dict__, {'skip': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_ior_alias_pytest_namespace_source)

    operator_ior_sys_modules_source = (
        "import operator as op\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    op.ior(sys.modules, "
        + "{'automation.inago.maintenance.repair': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_ior_sys_modules_source)

    operator_ior_alias_sys_modules_source = (
        "import operator as op\n"
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    patch_mapping = op.ior\n"
        + "    patch_mapping(sys.modules, "
        + "{'automation.inago.maintenance.repair': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_ior_alias_sys_modules_source)

    helper_registry_subscript_assignment_source = (
        "import sys\n\n"
        "def registry_box():\n"
        "    return [sys.modules]\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = registry_box()[0]\n"
        + "    registry['automation.inago.maintenance.repair'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_registry_subscript_assignment_source)

    conditional_pytest_namespace_subscript_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    namespace = pt.__dict__ if use_framework_namespace else {}\n"
        + "    namespace['skip'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(conditional_pytest_namespace_subscript_source)

    conditional_sys_modules_augmented_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = sys.modules if use_modules_registry else {}\n"
        + "    registry |= {'pytest': str}\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(conditional_sys_modules_augmented_source)

    helper_registry_monkeypatch_unresolved_target_source = (
        "import sys\n\n"
        "def registry_box():\n"
        "    return [sys.modules]\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = registry_box()[runtime_index]\n"
        + "    monkeypatch.setitem(registry, 'anything', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            helper_registry_monkeypatch_unresolved_target_source
        )

    helper_registry_unbound_monkeypatch_unresolved_target_source = (
        "import pytest as pt\n"
        "import sys\n\n"
        "def registry_box():\n"
        "    return [sys.modules]\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = registry_box()[runtime_index]\n"
        + "    pt.MonkeyPatch.setitem("
        + "monkeypatch, registry, 'anything', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            helper_registry_unbound_monkeypatch_unresolved_target_source
        )

    explicit_name_shadow_fixture_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='monkeypatch')\n"
        "def local_skip_fixture():\n"
        "    pt.skip('shadowed monkeypatch fixture bypass')\n\n"
        + valid_c65_guard_source.rstrip()
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(explicit_name_shadow_fixture_source)

    imported_pytest_alias_reassignment_source = (
        "from pytest import fail as fail_api, skip as skip_api\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    fail_api = skip_api\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(imported_pytest_alias_reassignment_source)

    direct_pytest_attribute_deletion_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    del pt.fail\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(direct_pytest_attribute_deletion_source)

    direct_pytest_attribute_augmented_assignment_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.fail += pt.skip\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            direct_pytest_attribute_augmented_assignment_source
        )

    pytest_skip_named_expression_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    (skip_alias := pt.skip)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(pytest_skip_named_expression_source)

    monkeypatch_keyword_expansion_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('keyword expansion bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pt, 'fail', **{'value': local_skip})\n"
    )
    with pytest.raises(AssertionError, match="unresolved callbacks"):
        _assert_c65_live_assertion_guard(monkeypatch_keyword_expansion_source)

    monkeypatch_starred_expansion_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('starred expansion bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(*(pt, 'fail', local_skip))\n"
    )
    with pytest.raises(AssertionError, match="unresolved callbacks"):
        _assert_c65_live_assertion_guard(monkeypatch_starred_expansion_source)

    monkeypatch_framework_setattr_target_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pt, 'skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(monkeypatch_framework_setattr_target_source)

    builtin_framework_setattr_target_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    setattr(pt, 'skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(builtin_framework_setattr_target_source)

    monkeypatch_framework_string_target_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr('pytest.skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(monkeypatch_framework_string_target_source)

    monkeypatch_framework_target_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    target = 'pytest.skip'\n"
        + "    monkeypatch.setattr(target, str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(monkeypatch_framework_target_alias_source)

    monkeypatch_framework_helper_target_source = (
        "import pytest as pt\n\n"
        "def pytest_target():\n"
        "    return pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    monkeypatch.setattr(pytest_target(), 'skip', str)\n"
    )
    with pytest.raises(
        AssertionError,
        match="mutate pytest or unittest API|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(monkeypatch_framework_helper_target_source)

    object_setattr_framework_target_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    object.__setattr__(pt, 'skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(object_setattr_framework_target_source)

    type_setattr_framework_target_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    type.__setattr__(pt, 'skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(type_setattr_framework_target_source)

    unbound_monkeypatch_starred_expansion_source = (
        "import pytest as pt\n\n"
        "def local_skip():\n"
        "    pt.skip('unbound starred expansion bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.MonkeyPatch.setattr(*(monkeypatch, pt, 'fail', local_skip))\n"
    )
    with pytest.raises(AssertionError, match="unresolved callbacks"):
        _assert_c65_live_assertion_guard(unbound_monkeypatch_starred_expansion_source)

    builtins_exec_alias_source = (
        "import builtins as dynamic_code\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    dynamic_code.exec(\"import pytest; pytest.skip('dynamic code')\")\n"
    )
    with pytest.raises(AssertionError, match="dynamic code"):
        _assert_c65_live_assertion_guard(builtins_exec_alias_source)

    builtins_exec_from_import_source = (
        "from builtins import exec as run_code\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    run_code(\"import pytest; pytest.skip('dynamic code')\")\n"
    )
    with pytest.raises(AssertionError, match="dynamic code"):
        _assert_c65_live_assertion_guard(builtins_exec_from_import_source)

    builtins_eval_from_import_source = (
        "from builtins import eval as evaluate\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    evaluate(\"__import__('pytest').skip('dynamic code')\")\n"
    )
    with pytest.raises(AssertionError, match="dynamic code"):
        _assert_c65_live_assertion_guard(builtins_eval_from_import_source)

    live_indirect_callable_source = (
        "import pytest as pt\n\n"
        "def local_dynamic_helper():\n"
        "    (getattr(pt, runtime_name))('indirect skip')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_dynamic_helper()\n"
    )
    with pytest.raises(AssertionError, match="unresolved indirect targets"):
        _assert_c65_live_assertion_guard(
            live_indirect_callable_source,
            _strict_dynamic_calls=False,
        )

    builtins_dict_exec_source = (
        "import builtins as dynamic_code\n\n"
        "def local_dynamic_helper():\n"
        "    dynamic_code.__dict__['exec'](\"import pytest; pytest.skip('dynamic')\")\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_dynamic_helper()\n"
    )
    with pytest.raises(AssertionError, match="unresolved indirect targets"):
        _assert_c65_live_assertion_guard(
            builtins_dict_exec_source,
            _strict_dynamic_calls=False,
        )

    builtins_dict_import_source = (
        "import builtins as dynamic_code\n\n"
        "def local_dynamic_helper():\n"
        "    dynamic_code.__dict__['__import__']('pytest').skip('dynamic')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_dynamic_helper()\n"
    )
    with pytest.raises(AssertionError, match="unresolved indirect targets"):
        _assert_c65_live_assertion_guard(
            builtins_dict_import_source,
            _strict_dynamic_calls=False,
        )

    reachable_inline_lambda_skip_source = (
        "import pytest as pt\n\n"
        "def local_inline_lambda_helper():\n"
        "    (lambda: pt.skip('inline lambda bypass'))()\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_inline_lambda_helper()\n"
    )
    with pytest.raises(AssertionError, match="dynamic code or invoke inline lambdas"):
        _assert_c65_live_assertion_guard(reachable_inline_lambda_skip_source)

    nested_class_body_skip_source = (
        "import pytest as pt\n\n"
        "def local_helper():\n"
        "    class SkipOnDefinition:\n"
        "        pt.skip('nested class body bypass')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_helper()\n"
    )
    with pytest.raises(AssertionError, match="nested class bodies"):
        _assert_c65_live_assertion_guard(nested_class_body_skip_source)

    nested_function_default_skip_source = (
        "import pytest as pt\n\n"
        "def local_helper():\n"
        "    def nested(_=pt.skip('nested default bypass')):\n"
        "        return None\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_helper()\n"
    )
    with pytest.raises(AssertionError, match="literal-only nested function"):
        _assert_c65_live_assertion_guard(nested_function_default_skip_source)

    nested_function_decorator_xfail_source = (
        "import pytest as pt\n\n"
        "def local_helper():\n"
        "    @pt.mark.xfail\n"
        "    def nested():\n"
        "        return None\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_helper()\n"
    )
    with pytest.raises(
        AssertionError,
        match="skip or xfail|nested function decorators",
    ):
        _assert_c65_live_assertion_guard(nested_function_decorator_xfail_source)

    nested_lambda_default_skip_source = (
        "import pytest as pt\n\n"
        "def local_helper():\n"
        "    callback = lambda _=pt.skip('nested lambda default bypass'): None\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_helper()\n"
    )
    with pytest.raises(AssertionError, match="literal-only nested function"):
        _assert_c65_live_assertion_guard(nested_lambda_default_skip_source)

    bare_unknown_fixture_decorator_source = (
        "import pytest as pt\n\n" "@make_autouse_fixture\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="dynamic assignment"):
        _assert_c65_live_assertion_guard(bare_unknown_fixture_decorator_source)

    unresolved_fixture_factory_source = (
        "import pytest as pt\n\n"
        "@fixture_factory('function', None, True)\n"
        "def ambiguous_autouse_fixture():\n"
        "    pt.skip('unresolved autouse fixture factory')\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="dynamic assignment"):
        _assert_c65_live_assertion_guard(unresolved_fixture_factory_source)

    aliased_pytest_skip_source = (
        "from pytest import skip as skip_now\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    skip_now('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(aliased_pytest_skip_source)

    aliased_pytest_xfail_decorator_source = (
        "import pytest as pt\n\n"
        "@pt.mark.xfail(reason='do not bypass the C65 guard')\n"
        + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(aliased_pytest_xfail_decorator_source)

    aliased_unittest_skip_decorator_source = (
        "from unittest import skip as skip_test\n\n"
        "@skip_test('do not bypass the C65 guard')\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(aliased_unittest_skip_decorator_source)

    inline_getattr_skip_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    getattr(pt, 'skip')('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(inline_getattr_skip_source)

    skip_dunder_call_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    pt.skip.__call__('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(skip_dunder_call_source)

    skip_dunder_call_decorator_source = (
        "import pytest as pt\n\n" "@pt.mark.skip.__call__\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(skip_dunder_call_decorator_source)

    shadowed_module_alias_dunder_call_source = (
        "import pytest as api\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    api = pytest.skip\n"
        + "    api.__call__('do not bypass the C65 guard')\n"
    )
    with pytest.raises(
        AssertionError,
        match="skip or xfail|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(shadowed_module_alias_dunder_call_source)

    module_pytestmark_skip_source = (
        "import pytest as pt\n"
        "pytestmark = pt.mark.skip(reason='ambient skip')\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="module-level skip or xfail"):
        _assert_c65_live_assertion_guard(module_pytestmark_skip_source)

    conditional_module_pytestmark_source = (
        "import pytest as pt\n"
        "if use_conditional_mark:\n"
        "    pytestmark = [pt.mark.xfail(reason='ambient xfail')]\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="module-level skip or xfail"):
        _assert_c65_live_assertion_guard(conditional_module_pytestmark_source)

    module_pytestmark_match_capture_source = (
        "import pytest as pt\n"
        "match pt.mark.skip:\n"
        "    case pytestmark:\n"
        "        pass\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate module-level pytestmark"):
        _assert_c65_live_assertion_guard(module_pytestmark_match_capture_source)

    module_pytestmark_append_source = (
        "import pytest as pt\n"
        "pytestmark = []\n"
        "pytestmark.append(pt.mark.skip(reason='mutated ambient skip'))\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate module-level pytestmark"):
        _assert_c65_live_assertion_guard(module_pytestmark_append_source)

    module_pytestmark_augassign_source = (
        "import pytest as pt\n"
        "pytestmark = []\n"
        "pytestmark += [pt.mark.skip(reason='augmented ambient skip')]\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate module-level pytestmark"):
        _assert_c65_live_assertion_guard(module_pytestmark_augassign_source)

    module_pytestmark_setdefault_source = (
        "import pytest as pt\n"
        "globals().setdefault('pytestmark', []).append(\n"
        "    pt.mark.skip(reason='globals ambient skip')\n"
        ")\n\n" + valid_c65_guard_source
    )
    with pytest.raises(
        AssertionError,
        match="mutate module-level pytestmark|mutate pytest or unittest API",
    ):
        _assert_c65_live_assertion_guard(module_pytestmark_setdefault_source)

    module_pytestmark_subscript_source = (
        "import pytest as pt\n"
        "import sys\n"
        "sys.modules[__name__].__dict__['pytestmark'] = pt.mark.skip\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(
        AssertionError,
        match="mutate module-level pytestmark|mutate pytest or unittest API",
    ):
        _assert_c65_live_assertion_guard(module_pytestmark_subscript_source)

    module_pytestmark_subscript_augassign_source = (
        "import pytest as pt\n"
        "import sys\n"
        "pytestmark = []\n"
        "sys.modules[__name__].__dict__['pytestmark'] += [pt.mark.skip]\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(
        AssertionError,
        match="mutate module-level pytestmark|mutate pytest or unittest API",
    ):
        _assert_c65_live_assertion_guard(module_pytestmark_subscript_augassign_source)

    module_pytestmark_for_target_source = (
        "import sys\n"
        "for sys.modules[__name__].__dict__['pytestmark'] in ():\n"
        "    pass\n\n" + valid_c65_guard_source
    )
    with pytest.raises(
        AssertionError,
        match="mutate module-level pytestmark|mutate pytest or unittest API",
    ):
        _assert_c65_live_assertion_guard(module_pytestmark_for_target_source)

    module_simple_loop_source = (
        "for _ in imported_iterable:\n    pass\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(module_simple_loop_source)

    module_simple_with_source = (
        "with imported_context:\n    pass\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(module_simple_with_source)

    arbitrary_attribute_module_pytestmark_source = (
        "import pytest as imported_module\n"
        "pytestmark = imported_module.skip_marker\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(arbitrary_attribute_module_pytestmark_source)

    header_named_expression_pytestmark_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver("
            "monkeypatch=(pytestmark := pt.mark.skip)):",
        )
    )
    with pytest.raises(
        AssertionError,
        match="assignment expressions in module-level function headers",
    ):
        _assert_c65_live_assertion_guard(header_named_expression_pytestmark_source)

    header_attribute_default_source = (
        "import arbitrary_module as imported_module\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver("
            "monkeypatch=imported_module.runtime_default):",
        )
    )
    with pytest.raises(
        AssertionError,
        match="arbitrary module attributes in module-level function headers",
    ):
        _assert_c65_live_assertion_guard(header_attribute_default_source)

    header_attribute_annotation_source = (
        "import arbitrary_module as imported_module\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver("
            "monkeypatch: imported_module.runtime_annotation = None):",
        )
    )
    with pytest.raises(
        AssertionError,
        match="arbitrary module attributes in module-level function headers",
    ):
        _assert_c65_live_assertion_guard(header_attribute_annotation_source)

    module_attribute_annotation_source = (
        "import pytest as imported_module\n"
        "runtime_annotation: imported_module.plugin_type = None\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(module_attribute_annotation_source)

    pytest_plugins_source = (
        "pytest_plugins = 'arbitrary.plugin'\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="must not bind pytest_plugins"):
        _assert_c65_live_assertion_guard(pytest_plugins_source)

    pytestmark_imports = (
        "import arbitrary_module as pytestmark\n\n",
        "from arbitrary_module import pytestmark\n\n",
        "from arbitrary_module import mark as pytestmark\n\n",
    )
    for pytestmark_import in pytestmark_imports:
        with pytest.raises(AssertionError, match="mutate module-level pytestmark"):
            _assert_c65_live_assertion_guard(pytestmark_import + valid_c65_guard_source)

    decorator_argument_helper_call_source = (
        "import pytest as pt\n"
        "def set_mark():\n"
        "    globals()['pytestmark'] = pt.mark.skip\n\n"
        "@pt.mark.parametrize('value', set_mark())\n" + valid_c65_guard_source
    )
    with pytest.raises(
        AssertionError,
        match="dynamic assignment|verified module-level function decorators",
    ):
        _assert_c65_live_assertion_guard(decorator_argument_helper_call_source)

    import_time_helper_mutation_source = (
        "import pytest as pt\n"
        "def set_mark():\n"
        "    globals()['pytestmark'] = pt.mark.skip\n"
        "set_mark()\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="module-level import-time calls"):
        _assert_c65_live_assertion_guard(import_time_helper_mutation_source)

    autouse_fixture_skip_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def ambient_skip_fixture():\n"
        "    pt.skip('ambient autouse skip')\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(autouse_fixture_skip_source)

    dict_expansion_autouse_skip_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(**{'autouse': True})\n"
        "def ambient_skip_fixture():\n"
        "    pt.skip('dict-expanded autouse skip')\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(dict_expansion_autouse_skip_source)

    positional_dict_autouse_skip_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(*[None], **{'autouse': True})\n"
        "def ambient_skip_fixture():\n"
        "    pt.skip('positional and dict-expanded autouse skip')\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(positional_dict_autouse_skip_source)

    unresolved_fixture_options_source = (
        "import pytest as pt\n"
        "fixture_options = get_fixture_options()\n\n"
        "@pt.fixture(**fixture_options)\n"
        "def possibly_autouse_fixture():\n"
        "    pt.skip('unresolved autouse fixture options')\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="literal fixture options"):
        _assert_c65_live_assertion_guard(unresolved_fixture_options_source)

    nonliteral_fixture_autouse_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=maybe_autouse)\n"
        "def possibly_autouse_fixture():\n"
        "    pt.skip('nonliteral autouse setting')\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="literal fixture options"):
        _assert_c65_live_assertion_guard(nonliteral_fixture_autouse_source)

    unresolved_positional_fixture_options_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(None, True)\n"
        "def possibly_autouse_fixture():\n"
        "    pt.skip('unresolved positional fixture options')\n\n"
        + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="literal fixture options"):
        _assert_c65_live_assertion_guard(unresolved_positional_fixture_options_source)

    nested_parametrize_xfail_source = (
        "import pytest as pt\n\n"
        "@pt.mark.parametrize(\n"
        "    'value', [pt.param(1, marks=pt.mark.xfail(reason='nested xfail'))]\n"
        ")\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(nested_parametrize_xfail_source)

    dynamic_getattr_call_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    runtime_name = input()\n"
        + "    getattr(pt, runtime_name)('do not bypass the C65 guard')\n"
    )
    with pytest.raises(
        AssertionError,
        match="dynamic assignment|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(dynamic_getattr_call_source)

    dynamic_attribute_call_source = (
        valid_c65_guard_source.rstrip()
        + "\n    runtime_api = get_runtime_api()\n"
        + "\n    runtime_api.skip('do not bypass the C65 guard')\n"
    )
    with pytest.raises(
        AssertionError,
        match="dynamic assignment|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(dynamic_attribute_call_source)

    inline_getattr_xfail_decorator_source = (
        "import pytest as pt\n\n"
        "@getattr(pt.mark, 'xfail')\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(inline_getattr_xfail_decorator_source)

    assignment_alias_call_source = (
        "import pytest\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    first_skip = pytest.skip\n"
        + "    second_skip = first_skip\n"
        + "    second_skip('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(assignment_alias_call_source)

    conditional_imported_alias_shadow_source = (
        "from pytest import skip as stop\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    if alternate:\n        stop = print\n"
        + "    stop('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(conditional_imported_alias_shadow_source)

    builtin_shadowed_by_skip_source = (
        "import builtins as b\n"
        "import pytest\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    print = pytest.skip\n"
        + "    print('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(builtin_shadowed_by_skip_source)

    imported_builtin_shadowed_by_skip_source = (
        "from builtins import print as cb\n"
        "import pytest\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    cb = pytest.skip\n"
        + "    cb('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(imported_builtin_shadowed_by_skip_source)

    unresolved_assigned_call_source = (
        valid_c65_guard_source.rstrip()
        + "\n    runtime_callback = unresolved_callback\n"
        + "    runtime_callback('unverified callback')\n"
    )
    with pytest.raises(AssertionError, match="dynamic assignment"):
        _assert_c65_live_assertion_guard(unresolved_assigned_call_source)

    assignment_alias_decorator_source = (
        "import pytest as pt\n"
        "skip_decorator: object = pt.mark.xfail\n\n"
        "@skip_decorator\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(assignment_alias_decorator_source)

    unresolved_decorator_source = (
        "@runtime_decorator_factory()\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="dynamic assignment"):
        _assert_c65_live_assertion_guard(unresolved_decorator_source)

    skiptest_raise_source = (
        "import unittest as ut\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    raise ut.SkipTest\n"
    )
    with pytest.raises(AssertionError, match="raise SkipTest"):
        _assert_c65_live_assertion_guard(skiptest_raise_source)

    pytest_skip_exception_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    raise pt.skip.Exception('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="raise SkipTest"):
        _assert_c65_live_assertion_guard(pytest_skip_exception_source)

    local_helper_skip_source = (
        "import pytest as pt\n\n"
        "def local_skip_helper():\n"
        "    pt.skip('do not bypass the C65 guard')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_skip_helper()\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(local_helper_skip_source)

    usefixtures_skip_source = (
        "import pytest as pt\n\n"
        "@pt.fixture\n"
        "def local_skip_fixture():\n"
        "    pt.skip('do not bypass the C65 guard')\n\n"
        "@pt.mark.usefixtures('local_skip_fixture')\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(usefixtures_skip_source)

    local_helper_unresolved_decorator_source = (
        "@runtime_decorator_factory()\n"
        "def local_helper_with_unverified_decorator():\n"
        "    return None\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    local_helper_with_unverified_decorator()\n"
    )
    with pytest.raises(AssertionError, match="dynamic assignment"):
        _assert_c65_live_assertion_guard(local_helper_unresolved_decorator_source)

    conditional_assignment_alias_call_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    skip_now = pt.skip\n"
        + "    if alternate:\n        skip_now = print\n"
        + "    skip_now('do not bypass the C65 guard')\n"
    )
    with pytest.raises(AssertionError, match="must not skip or xfail"):
        _assert_c65_live_assertion_guard(conditional_assignment_alias_call_source)

    conditional_assignment_alias_decorator_source = (
        "import pytest as pt\n"
        "if alternate:\n"
        "    skip_decorator = pt.mark.xfail\n"
        "else:\n"
        "    skip_decorator = ordinary_decorator\n\n"
        "@skip_decorator\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="skip or xfail"):
        _assert_c65_live_assertion_guard(conditional_assignment_alias_decorator_source)

    for star_import in (
        "from pytest import *\n\n",
        "from unittest import *\n\n",
        "from arbitrary_module import *\n\n",
        "from .helper import *\n\n",
    ):
        with pytest.raises(AssertionError, match="must not use pytest or unittest"):
            _assert_c65_live_assertion_guard(star_import + valid_c65_guard_source)

    arbitrary_top_level_import_source = (
        "import arbitrary_module\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="verified top-level imports"):
        _assert_c65_live_assertion_guard(arbitrary_top_level_import_source)

    arbitrary_from_import_source = (
        "from arbitrary_module import skip_marker as imported_marker\n"
        "pytestmark = imported_marker\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="verified top-level imports"):
        _assert_c65_live_assertion_guard(arbitrary_from_import_source)

    imported_mark_container_source = (
        "from pytest import skip as imported_marker\n"
        "pytestmark = [imported_marker]\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(imported_mark_container_source)

    imported_module_mark_source = (
        "from arbitrary_module import skip_marker as imported_marker\n"
        "pytestmark = [imported_marker]\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="verified top-level imports"):
        _assert_c65_live_assertion_guard(imported_module_mark_source)

    imported_module_assignment_source = (
        "from arbitrary_module import skip_marker as imported_marker\n"
        "candidate = imported_marker\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="verified top-level imports"):
        _assert_c65_live_assertion_guard(imported_module_assignment_source)

    imported_module_attribute_assignment_source = (
        "import pytest as imported_module\n"
        "candidate = imported_module.mark.skip\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(imported_module_attribute_assignment_source)

    overloaded_module_expression_source = (
        "candidate = 'safe' + 'value'\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(overloaded_module_expression_source)

    subscript_module_annotation_source = (
        "import pytest as Trigger\n"
        "runtime_annotation: Trigger[None] = None\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="data-only module declarations"):
        _assert_c65_live_assertion_guard(subscript_module_annotation_source)

    subscript_function_default_source = (
        "import pytest as imported_mapping\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver("
            "monkeypatch=imported_mapping['skip']):",
        )
    )
    with pytest.raises(AssertionError, match="subscript evaluated module-level"):
        _assert_c65_live_assertion_guard(subscript_function_default_source)

    subscript_function_annotation_source = (
        "import pytest as Trigger\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver("
            "monkeypatch: Trigger[None] = None):",
        )
    )
    with pytest.raises(AssertionError, match="subscript evaluated module-level"):
        _assert_c65_live_assertion_guard(subscript_function_annotation_source)

    # Preliminary review claims may omit host-only receipt provenance and may
    # carry nullable code hashes. The final receipt-bearing artifact must have
    # the full provenance envelope and non-null, lowercase SHA-256 values.
    preliminary_claim = {
        "author_id": "implementation-agent",
        "reviewer_id": "independent-reviewer",
        "tested_result_version": "repair-result-c66",
        "reviewed_result_version": "repair-result-c66",
        "unresolved_critical_findings": [],
        "tested_code_sha256": None,
        "reviewed_code_sha256": None,
    }
    repair_module._validate_schema(
        "maintenance-repair-review-claim.schema.json",
        preliminary_claim,
        "c66_preliminary_claim_invalid",
    )
    schemas_directory = Path(repair_module.__file__).resolve().parents[1] / "schemas"
    for schema_name in (
        "maintenance-repair-review-claim.schema.json",
        "maintenance-repair-review.schema.json",
        "maintenance-repair-result.schema.json",
    ):
        schema_document = json.loads(
            (schemas_directory / schema_name).read_text(encoding="utf-8")
        )
        assert "tested_commit" not in schema_document["properties"]
        assert "reviewed_commit" not in schema_document["properties"]

    final_review_schema_path = (
        Path(repair_module.__file__).resolve().parents[1]
        / "schemas"
        / "maintenance-repair-review.schema.json"
    )
    final_review_schema = json.loads(
        final_review_schema_path.read_text(encoding="utf-8")
    )
    final_provenance_fields = {
        "schema_version",
        "attestation_source",
        "identity",
        "attempt_id",
        "result_version",
        "author_id",
        "reviewer_id",
        "tested_result_version",
        "reviewed_result_version",
        "unresolved_critical_findings",
        "tested_code_sha256",
        "reviewed_code_sha256",
        "host_receipt_id",
        "receipt_id",
        "receipt_sha256",
        "reviewed_output_sha256",
        "patch_sha256",
        "test_ledger_sha256",
    }
    assert final_provenance_fields.issubset(set(final_review_schema["required"]))
    assert "tested_commit" not in final_review_schema["properties"]
    assert "reviewed_commit" not in final_review_schema["properties"]
    for code_hash_field in ("tested_code_sha256", "reviewed_code_sha256"):
        assert final_review_schema["properties"][code_hash_field]["type"] == "string"
        assert (
            final_review_schema["properties"][code_hash_field]["pattern"]
            == "^[a-f0-9]{64}$"
        )

    valid_final_review = {
        "schema_version": "maintenance-repair-review-v1",
        "attestation_source": "host_collected",
        "identity": {
            "incident_id": "incident-c66",
            "source_launch_id": "launch-c66",
            "source_run_id": None,
            "target_date": "2026-10-17",
            "job_id": "job-c66",
        },
        "attempt_id": "attempt-c66",
        "result_version": "repair-result-c66",
        "author_id": "implementation-agent",
        "reviewer_id": "independent-reviewer",
        "tested_result_version": "repair-result-c66",
        "reviewed_result_version": "repair-result-c66",
        "unresolved_critical_findings": [],
        "tested_code_sha256": BASE_SHA,
        "reviewed_code_sha256": BASE_SHA,
        "host_receipt_id": "host-review-c66",
        "receipt_id": "review-receipt-c66",
        "receipt_sha256": "d" * 64,
        "reviewed_output_sha256": "e" * 64,
        "patch_sha256": "f" * 64,
        "test_ledger_sha256": "1" * 64,
    }
    repair_module._validate_schema(
        "maintenance-repair-review.schema.json",
        valid_final_review,
        "c66_final_review_invalid",
    )
    for provenance_field in final_provenance_fields:
        missing_provenance = dict(valid_final_review)
        missing_provenance.pop(provenance_field)
        with pytest.raises(RepairContractError):
            repair_module._validate_schema(
                "maintenance-repair-review.schema.json",
                missing_provenance,
                "c66_final_review_invalid",
            )
    for code_hash_field in ("tested_code_sha256", "reviewed_code_sha256"):
        for invalid_hash in (None, "a" * 63, "A" * 64):
            invalid_review = dict(valid_final_review)
            invalid_review[code_hash_field] = invalid_hash
            with pytest.raises(RepairContractError):
                repair_module._validate_schema(
                    "maintenance-repair-review.schema.json",
                    invalid_review,
                    "c66_final_review_invalid",
                )

    node = "tests_inago/test_receipt.py::test_receipt_case"
    store, ledger, runner, _, _ = _create_persisted_campaign(
        tmp_path,
        files={
            "tests_inago/test_receipt.py": (
                "def test_receipt_case():\n    assert 2 + 2 == 4\n"
            )
        },
        required_cases={"receipt-case": node},
        semantic_signatures={"receipt-case": "the exact receipt behavior"},
    )
    _install_fake_pytest_executor(monkeypatch, repair_module)
    # An individual PASS is useful for retry selection but cannot replace the
    # final exact-case regression against the current code hash.
    individual_receipt = runner.run_case(ledger, "receipt-case")
    ledger.record_case_receipt(individual_receipt)
    assert ledger.frozen_cases["receipt-case"].node == node
    evidence_type = import_module(
        "automation.inago.maintenance.repair"
    ).FullRegressionEvidence
    token = ledger.begin_full_regression()
    assert ledger.full_regression_cases == (("receipt-case", node),)
    forged = _full_regression_evidence(
        evidence_type,
        token=token,
        cases={"receipt-case": node},
        code_sha256=ledger.code_sha256,
    )
    with pytest.raises(RepairContractError) as missing_receipt:
        ledger.record_full_regression(forged)
    assert missing_receipt.value.code == "host_run_receipt_unavailable"

    evidence = runner.run_full_regression(ledger)
    tampered_evidence = (
        replace(evidence, token="another-run-token"),
        replace(evidence, code_sha256=FIXED_SHA),
        replace(evidence, cases=(("renamed-case", node),)),
        replace(evidence, results={"receipt-case": "FAIL"}),
    )
    for altered in tampered_evidence:
        with pytest.raises(RepairContractError) as mismatch:
            ledger.record_full_regression(altered)
        assert mismatch.value.code == "full_regression_receipt_mismatch"

    record = ledger.record_full_regression(evidence)
    assert record.run_id == evidence.run_id
    assert (
        store.load_repair_campaign(campaign_id=ledger.campaign_id)["full_regression"][
            "receipt_id"
        ]
        == evidence.receipt_id
    )
    with pytest.raises(RepairContractError) as replay:
        ledger.record_full_regression(evidence)
    assert replay.value.code == "host_run_receipt_consumed"

    # Git index mode changes must affect the hash even on Windows, where
    # worktree stat data may not report executable bits. POSIX worktree mode
    # changes are included separately so unstaged chmods also invalidate it.
    from automation.inago.maintenance.repair import repository_code_sha256

    mode_root = _make_tiny_git_checkout(
        tmp_path / "execution-mode-hash",
        {"src/module.py": "value = 1\n"},
    )
    initial_index_modes = repair_module._git_index_file_modes(mode_root)
    assert initial_index_modes["src/module.py"] == 0o100644
    non_executable_hash = repository_code_sha256(mode_root)
    subprocess.run(
        ["git", "update-index", "--chmod=+x", "--", "src/module.py"],
        cwd=mode_root,
        check=True,
        capture_output=True,
    )
    executable_index_modes = repair_module._git_index_file_modes(mode_root)
    assert executable_index_modes["src/module.py"] == 0o100755
    executable_index_hash = repository_code_sha256(mode_root)
    assert non_executable_hash != executable_index_hash

    with monkeypatch.context() as mode_patch:
        mode_patch.setattr(
            repair_module,
            "_git_index_file_modes",
            lambda _root: {"src/module.py": 0o100755},
        )
        mode_patch.setattr(repair_module, "_execution_mode_bits", lambda _path: 0)
        non_executable_stat_hash = repository_code_sha256(mode_root)
        mode_patch.setattr(repair_module, "_execution_mode_bits", lambda _path: 0o100)
        executable_stat_hash = repository_code_sha256(mode_root)
    assert non_executable_stat_hash != executable_stat_hash

    tamper_store, tamper_ledger, tamper_runner, _, _ = _create_persisted_campaign(
        tmp_path / "receipt-metadata-tamper",
        files={
            "tests_inago/test_receipt.py": (
                "def test_receipt_case():\n    assert 2 + 2 == 4\n"
            )
        },
        required_cases={"receipt-case": node},
        semantic_signatures={"receipt-case": "the exact receipt behavior"},
    )
    tamper_ledger.begin_full_regression()
    tamper_evidence = tamper_runner.run_full_regression(tamper_ledger)
    with sqlite3.connect(tamper_store.database_path) as connection:
        connection.execute("DROP TRIGGER repair_run_receipt_immutable")
        connection.execute(
            "UPDATE repair_run_receipt SET code_sha256 = ? WHERE receipt_id = ?",
            (FIXED_SHA, tamper_evidence.receipt_id),
        )
        connection.commit()
    with pytest.raises(RepairContractError) as receipt_tamper:
        tamper_ledger.record_full_regression(tamper_evidence)
    assert receipt_tamper.value.code == "repair_run_receipt_corrupt"

    # Review receipts may be created only from an immutable, host-issued
    # reviewer execution row bound to this exact attempt and artifact set.
    (
        review_store,
        _review_controller,
        _review_lease,
        review_context,
        _review_attempts_root,
        _review_plan_attempt,
        _review_plan_outcome,
        review_attempt,
    ) = _start_approved_repair_attempt(tmp_path / "review-provenance")
    job_id = review_context["job_id"]
    manifest = review_store.get_job_input_manifest(job_id=job_id)
    reviewer_session_key = "c66-independent-reviewer"
    session_values = (
        (
            "c66-implementer-session",
            "common-implementer",
            "implementation-agent",
            {"type": "workspaceWrite", "networkAccess": False},
        ),
        (
            reviewer_session_key,
            "common-reviewer",
            "independent-reviewer",
            {"type": "readOnly", "networkAccess": False},
        ),
    )
    for session_key, role_name, thread_id, sandbox in session_values:
        review_store.bind_ai_parent_session(
            job_id=job_id,
            session_key=session_key,
            parent_key="B",
            role_name=role_name,
            thread_id=thread_id,
            manifest_sha256=manifest.manifest_sha256,
            requested_model="test-model",
            requested_effort="low",
            requested_service_tier="default",
            requested_fast_mode=False,
            role_config_sha256=hashlib.sha256(role_name.encode("ascii")).hexdigest(),
            sandbox=sandbox,
            created_at=BASE_TIME + timedelta(seconds=30),
        )
        review_store.record_ai_session_observation(
            job_id=job_id,
            session_key=session_key,
            thread_id=thread_id,
            status="verified",
            observed_model="test-model",
            observed_effort="low",
            observed_service_tier="default",
            observed_at=BASE_TIME + timedelta(seconds=31),
        )

    result_version = "repair-result-c66"
    reviewed_output_sha256 = hashlib.sha256(b"review-target-c66").hexdigest()
    review_code_sha256 = hashlib.sha256(b"code-c66").hexdigest()
    patch_sha256 = hashlib.sha256(b"patch-c66").hexdigest()
    test_ledger_sha256 = hashlib.sha256(b"ledger-c66").hexdigest()

    def issue_host_review(
        turn_id, *, approved=True, findings=(), reviewer_output=b"review-output"
    ):
        return review_store._record_host_repair_review_execution(
            capability=review_store._repair_review_capability,
            job_id=job_id,
            attempt_id=review_attempt.attempt_id,
            result_version=result_version,
            author_id="implementation-agent",
            reviewer_id="independent-reviewer",
            reviewer_session_key=reviewer_session_key,
            reviewer_turn_id=turn_id,
            reviewer_output_sha256=hashlib.sha256(reviewer_output).hexdigest(),
            reviewed_output_sha256=reviewed_output_sha256,
            code_sha256=review_code_sha256,
            patch_sha256=patch_sha256,
            test_ledger_sha256=test_ledger_sha256,
            approved=approved,
            unresolved_critical_findings=tuple(findings),
            created_at=BASE_TIME + timedelta(seconds=32),
        )

    def record_review_receipt(host_receipt_id, **overrides):
        values = {
            "capability": review_store._repair_review_capability,
            "job_id": job_id,
            "attempt_id": review_attempt.attempt_id,
            "result_version": result_version,
            "author_id": "implementation-agent",
            "reviewer_id": "independent-reviewer",
            "host_receipt_id": host_receipt_id,
            "reviewed_output_sha256": reviewed_output_sha256,
            "code_sha256": review_code_sha256,
            "patch_sha256": patch_sha256,
            "test_ledger_sha256": test_ledger_sha256,
            "created_at": BASE_TIME + timedelta(seconds=33),
        }
        values.update(overrides)
        return review_store.record_repair_review_receipt(**values)

    with pytest.raises(MaintenanceStoreError) as forged_review:
        record_review_receipt("f" * 64)
    assert forged_review.value.code == "repair_host_review_execution_unavailable"

    rejected_reviews = (
        issue_host_review("c66-unapproved", approved=False),
        issue_host_review(
            "c66-critical-finding",
            approved=True,
            findings=("critical: unsafe patch",),
        ),
    )
    for rejected_review in rejected_reviews:
        with pytest.raises(MaintenanceStoreError) as rejected_receipt:
            record_review_receipt(rejected_review.host_receipt_id)
        assert rejected_receipt.value.code == "repair_review_not_approved"

    approved_review = issue_host_review("c66-approved")
    with pytest.raises(MaintenanceStoreError) as mismatched_review:
        record_review_receipt(
            approved_review.host_receipt_id,
            reviewed_output_sha256="e" * 64,
        )
    assert mismatched_review.value.code == "repair_review_execution_mismatch"

    accepted_review_receipt = record_review_receipt(approved_review.host_receipt_id)
    idempotent_review_receipt = record_review_receipt(approved_review.host_receipt_id)
    assert idempotent_review_receipt.receipt_id == accepted_review_receipt.receipt_id
    assert (
        idempotent_review_receipt.receipt_sha256
        == accepted_review_receipt.receipt_sha256
    )
    different_approved_review = issue_host_review(
        "c66-second-approved", reviewer_output=b"different review output"
    )
    with pytest.raises(MaintenanceStoreError) as duplicate_review:
        record_review_receipt(different_approved_review.host_receipt_id)
    assert duplicate_review.value.code == "repair_review_receipt_conflict"

    # The preliminary RepairReview can be valid while final completion remains
    # blocked until the host issues the artifact-bound review receipt. This
    # in-memory positive ledger only gets the controller to that final gate.
    controller_module = import_module("automation.inago.maintenance.controller")
    MaintenanceController = controller_module.MaintenanceController

    (
        completion_store,
        _completion_planning_controller,
        completion_lease,
        completion_plan_context,
        completion_attempts_root,
        completion_plan_attempt,
        completion_plan_outcome,
        completion_attempt,
    ) = _start_approved_repair_attempt(tmp_path / "completion-without-review-receipt")
    completion_version = "repair-c66-no-review-receipt"
    completion_request = _make_repair_request(
        store=completion_store,
        lease=completion_lease,
        plan_context=completion_plan_context,
        repair_attempt=completion_attempt,
        attempts_root=completion_attempts_root,
        result_version=completion_version,
    )
    completion_review = replace(
        completion_request.review,
        author_id="implementation-agent",
        reviewer_id="independent-reviewer",
    )
    completion_request = replace(completion_request, review=completion_review)

    def resolve_completion_identity(identity):
        return {
            "implementation-agent": "implementer",
            "independent-reviewer": "read_only_reviewer",
        }.get(identity)

    assert (
        RepairController(identity_resolver=resolve_completion_identity).validate_review(
            completion_review
        )
        is True
    )

    completion_cases = {"receipt-case": node}
    completion_ledger = RepairTestLedger(
        campaign_id="c66-completion-ledger",
        required_cases=completion_cases,
        semantic_signatures={"receipt-case": "the exact receipt behavior"},
        code_sha256=BASE_SHA,
        invocation_parent_id=None,
        _job_id=completion_plan_context["job_id"],
        _attempt_id=completion_attempt.attempt_id,
        _created_at=BASE_TIME + timedelta(seconds=21),
    )
    completion_token = completion_ledger.begin_full_regression()
    completion_evidence = _full_regression_evidence(
        repair_module.FullRegressionEvidence,
        token=completion_token,
        cases=completion_cases,
        code_sha256=BASE_SHA,
        receipt_id="c66-fixture-run-receipt",
        receipt_sha256="c" * 64,
    )
    completion_ledger.record_full_regression(completion_evidence)
    completion_host_evidence = RepairHostEvidence(
        changed_paths=("automation/inago/maintenance/fix.py",),
        owned_paths=("automation/inago/maintenance/fix.py",),
        baseline_assertions={},
        candidate_assertions={},
        test_ledger=completion_ledger,
        current_code_sha256=BASE_SHA,
        test_evidence=(),
        approved_cases=completion_cases,
        job_id=completion_plan_context["job_id"],
        attempt_id=completion_attempt.attempt_id,
        base_commit=completion_plan_context["base_commit"],
        plan_attempt_id=completion_plan_attempt.attempt_id,
        plan_result_version=completion_store.get_step_attempt(
            completion_plan_attempt.attempt_id
        ).result_version,
        plan_output_sha256=completion_plan_outcome.output_sha256,
        checkout_root=str(completion_attempts_root),
    )
    completion_controller = MaintenanceController(
        completion_store,
        identity_resolver=resolve_completion_identity,
        repair_host_evidence_resolver=lambda _context: completion_host_evidence,
    )

    review_hash_drift_controller = MaintenanceController(
        completion_store,
        identity_resolver=resolve_completion_identity,
        repair_host_evidence_resolver=lambda _context: completion_host_evidence,
    )
    with monkeypatch.context() as review_hash_patch:
        review_hash_patch.setattr(
            controller_module, "repository_code_sha256", lambda _root: FIXED_SHA
        )
        with pytest.raises(RepairContractError) as resolver_hash_drift:
            review_hash_drift_controller.complete_repair_attempt(completion_request)
    assert resolver_hash_drift.value.code == "repair_code_hash_drift"

    hash_check_count = 0

    def drift_after_result_artifact(_root):
        nonlocal hash_check_count
        hash_check_count += 1
        return BASE_SHA if hash_check_count == 1 else FIXED_SHA

    with monkeypatch.context() as result_hash_patch:
        result_hash_patch.setattr(
            controller_module,
            "repository_code_sha256",
            drift_after_result_artifact,
        )
        with pytest.raises(RepairContractError) as result_hash_drift:
            review_hash_drift_controller.complete_repair_attempt(completion_request)
    assert result_hash_drift.value.code == "repair_code_hash_drift"
    assert hash_check_count == 2

    with monkeypatch.context() as local_review:
        local_review.setattr(
            controller_module, "repository_code_sha256", lambda _root: BASE_SHA
        )
        completion_outcome = completion_controller.complete_repair_attempt(
            completion_request
        )
    assert completion_outcome.review_status == "approved"
    assert (
        completion_store.get_step_attempt(
            attempt_id=completion_attempt.attempt_id
        ).status
        == "complete"
    )
    assert completion_store.get_step_attempt_review(
        attempt_id=completion_attempt.attempt_id
    )["status"] == "approved"

    # The C65 source guard must resolve dict mutators by their API provenance,
    # including aliases, bound methods, and arguments passed through containers.
    dict_update_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    patch = dict.update\n"
        + "    patch(pt.__dict__, {'skip': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(dict_update_alias_source)

    dict_setitem_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    patch = dict.__setitem__\n"
        + "    patch(pt.__dict__, 'skip', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(dict_setitem_alias_source)

    pytest_dict_method_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    patch = pt.__dict__.update\n"
        + "    patch({'skip': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(pytest_dict_method_alias_source)

    sys_modules_method_alias_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    patch = sys.modules.update\n"
        + "    patch({'pytest': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(sys_modules_method_alias_source)

    list_subscript_sys_modules_mutator_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    dict.update([sys.modules][0], {'pytest': str})\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(list_subscript_sys_modules_mutator_source)

    tuple_subscript_sys_modules_mutator_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    dict.__setitem__((sys.modules,)[0], 'pytest', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(tuple_subscript_sys_modules_mutator_source)

    tuple_destructured_registry_alias_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry, = (sys.modules,)\n"
        + "    registry |= {'pytest': str}\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(tuple_destructured_registry_alias_source)

    loop_bound_namespace_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    for namespace in (pt.__dict__,):\n"
        + "        namespace['skip'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(loop_bound_namespace_alias_source)

    boolop_namespace_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    namespace = pt.__dict__ or {}\n"
        + "    namespace['skip'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(boolop_namespace_alias_source)

    boolop_registry_alias_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    registry = sys.modules and sys.modules\n"
        + "    registry |= {'pytest': str}\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(boolop_registry_alias_source)

    fixture_argument_mapping_alias_source = (
        "import pytest as pt\n\n"
        "@pt.fixture\n"
        "def module_registry():\n"
        "    import sys\n"
        "    return sys.modules\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver(monkeypatch, module_registry):",
        ).rstrip()
        + "\n    monkeypatch.setitem(module_registry, 'pytest', str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_argument_mapping_alias_source)

    fixture_argument_subscript_alias_source = (
        "import pytest as pt\n\n"
        "@pt.fixture\n"
        "def module_registry():\n"
        "    import sys\n"
        "    return sys.modules\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver(monkeypatch, module_registry):",
        ).rstrip()
        + "\n    module_registry['pytest'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_argument_subscript_alias_source)

    helper_parameter_namespace_mutation_source = (
        "import pytest as pt\n\n"
        "def mutate_namespace(namespace):\n"
        "    namespace['skip'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    mutate_namespace(pt.__dict__)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_parameter_namespace_mutation_source)

    helper_parameter_registry_mutation_source = (
        "import sys\n\n"
        "def mutate_registry(registry):\n"
        "    registry['automation.inago.maintenance.repair'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    mutate_registry(sys.modules)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_parameter_registry_mutation_source)

    unbound_dict_unresolved_mapping_source = (
        "def mutate_mapping(mapping):\n"
        "    dict.update(mapping, {'key': 1})\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    mutate_mapping(runtime_mapping)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(unbound_dict_unresolved_mapping_source)

    helper_local_sys_modules_alias_source = (
        "def mutate_registry():\n"
        "    import sys\n"
        "    registry = sys.modules\n"
        "    registry['pytest'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    mutate_registry()\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_local_sys_modules_alias_source)

    helper_local_pytest_namespace_alias_source = (
        "def mutate_namespace():\n"
        "    import pytest as pt\n"
        "    namespace = pt.__dict__\n"
        "    namespace['skip'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    mutate_namespace()\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_local_pytest_namespace_alias_source)

    nested_for_destructured_pytest_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    for module, in [(pt,)]:\n"
        + "        module.skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(nested_for_destructured_pytest_alias_source)

    fixture_yield_injected_registry_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='module_registry')\n"
        "def registry_provider():\n"
        "    import sys\n"
        "    yield sys.modules\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_registry(module_registry):\n"
        "    module_registry['pytest'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_yield_injected_registry_source)

    fixture_yield_from_injected_registry_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='module_registry')\n"
        "def registry_provider():\n"
        "    import sys\n"
        "    yield from [sys.modules]\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_registry(module_registry):\n"
        "    module_registry['pytest'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_yield_from_injected_registry_source)

    fixture_yield_from_generator_registry_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='module_registry')\n"
        "def registry_provider():\n"
        "    import sys\n"
        "    yield from (item for item in (sys.modules,))\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_registry(module_registry):\n"
        "    module_registry['pytest'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_yield_from_generator_registry_source)

    fixture_yield_from_alias_registry_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='module_registry')\n"
        "def registry_provider():\n"
        "    import sys\n"
        "    values = [sys.modules]\n"
        "    yield from values\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_registry(module_registry):\n"
        "    module_registry['pytest'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_yield_from_alias_registry_source)

    fixture_yield_from_generator_registry_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='module_registry')\n"
        "def registry_provider():\n"
        "    import sys\n"
        "    yield from (item for item in (sys.modules,))\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_registry(module_registry):\n"
        "    module_registry['pytest'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_yield_from_generator_registry_source)

    fixture_yield_from_alias_registry_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='module_registry')\n"
        "def registry_provider():\n"
        "    import sys\n"
        "    values = [sys.modules]\n"
        "    yield from values\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_registry(module_registry):\n"
        "    module_registry['pytest'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_yield_from_alias_registry_source)

    fixture_return_injected_registry_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(name='module_registry')\n"
        "def registry_provider():\n"
        "    import sys\n"
        "    return sys.modules\n\n"
        "@pt.fixture\n"
        "def poison_registry(module_registry):\n"
        "    module_registry['pytest'] = str\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver(monkeypatch, poison_registry):",
        )
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(fixture_return_injected_registry_source)

    lambda_sys_modules_mutation_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted([None], key=lambda _: "
        + "sys.modules.__setitem__('pytest', str))\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(lambda_sys_modules_mutation_source)

    lambda_local_helper_namespace_mutation_source = (
        "import pytest as pt\n\n"
        "def mutate(namespace):\n"
        "    namespace['skip'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted([pt.__dict__], key=lambda namespace: mutate(namespace))\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(lambda_local_helper_namespace_mutation_source)

    named_local_helper_callback_namespace_mutation_source = (
        "import pytest as pt\n\n"
        "def poison(namespace):\n"
        "    namespace['skip'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted([pt.__dict__], key=poison)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            named_local_helper_callback_namespace_mutation_source
        )

    duplicate_local_callback_name_source = (
        "import pytest as pt\n\n"
        "def harmless_container():\n"
        "    def mutate(namespace):\n"
        "        return None\n"
        "    return mutate\n\n"
        "def mutate(namespace):\n"
        "    namespace['skip'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted([pt.__dict__], key=mutate)\n"
    )
    with pytest.raises(
        AssertionError,
        match="unresolved callbacks|unresolved indirect targets",
    ):
        _assert_c65_live_assertion_guard(duplicate_local_callback_name_source)

    duplicate_fixture_name_provider_source = (
        "import pytest as pt\n\n"
        "def injected():\n"
        "    return None\n\n"
        "@pt.fixture\n"
        "def injected():\n"
        "    pt.skip('duplicate fixture provider')\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(tmp_path, monkeypatch):",
            "def test_c65_default_repository_resolver(tmp_path, monkeypatch, injected):",
        )
    )
    with pytest.raises(AssertionError, match="ambiguous local fixture names"):
        _assert_c65_live_assertion_guard(duplicate_fixture_name_provider_source)

    operator_call_named_helper_callback_source = (
        "import operator\n"
        "import pytest as pt\n\n"
        "def poison():\n"
        "    pt.skip('operator.call callback')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    operator.call(poison)\n"
    )
    with pytest.raises(AssertionError, match="skip|mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(operator_call_named_helper_callback_source)

    iter_unresolved_callback_source = (
        valid_c65_guard_source.rstrip()
        + "\n    next(iter(runtime_callback, object()))\n"
    )
    with pytest.raises(
        AssertionError, match="unresolved callbacks|dynamic assignment|unresolved"
    ):
        _assert_c65_live_assertion_guard(iter_unresolved_callback_source)

    iter_named_helper_callback_source = (
        "import pytest as pt\n\n"
        "def skip_on_iteration():\n"
        "    pt.skip('iteration callback')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    next(iter(skip_on_iteration, object()))\n"
    )
    with pytest.raises(AssertionError, match="skip|mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(iter_named_helper_callback_source)

    iter_aliased_helper_callback_source = (
        "import pytest as pt\n\n"
        "def skip_on_iteration():\n"
        "    pt.skip('iteration callback alias')\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    callback = skip_on_iteration\n"
        + "    next(iter(callback, object()))\n"
    )
    with pytest.raises(AssertionError, match="skip|mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(iter_aliased_helper_callback_source)

    builtin_str_shadowed_local_callback_source = (
        "import pytest as pt\n\n"
        "def str(namespace):\n"
        "    namespace['skip'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted([pt.__dict__], key=str)\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(builtin_str_shadowed_local_callback_source)

    builtin_sorted_shadowed_local_call_source = (
        "import pytest as pt\n\n"
        "def sorted():\n"
        "    pt.skip = str\n\n" + valid_c65_guard_source.rstrip() + "\n    sorted()\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(builtin_sorted_shadowed_local_call_source)

    imported_builtin_name_pytest_module_alias_source = (
        "import pytest as sorted\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted.skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            imported_builtin_name_pytest_module_alias_source
        )

    ambiguous_fixture_sorted_direct_call_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_from_ambiguous_fixture(sorted):\n"
        "    sorted()\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="dynamic assignment|unresolved|alias"):
        _assert_c65_live_assertion_guard(ambiguous_fixture_sorted_direct_call_source)

    ambiguous_fixture_sorted_callback_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_from_ambiguous_fixture(sorted):\n"
        "    list(map(sorted, ()))\n\n" + valid_c65_guard_source
    )
    with pytest.raises(
        AssertionError, match="unresolved callbacks|dynamic assignment|unresolved"
    ):
        _assert_c65_live_assertion_guard(ambiguous_fixture_sorted_callback_source)

    imported_builtin_name_pytest_module_alias_source = (
        "import pytest as sorted\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    sorted.skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            imported_builtin_name_pytest_module_alias_source
        )

    ambiguous_fixture_sorted_direct_call_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_from_ambiguous_fixture(sorted):\n"
        "    sorted()\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="dynamic assignment|unresolved|alias"):
        _assert_c65_live_assertion_guard(ambiguous_fixture_sorted_direct_call_source)

    ambiguous_fixture_sorted_callback_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_from_ambiguous_fixture(sorted):\n"
        "    list(map(sorted, ()))\n\n" + valid_c65_guard_source
    )
    with pytest.raises(
        AssertionError, match="unresolved callbacks|dynamic assignment|unresolved"
    ):
        _assert_c65_live_assertion_guard(ambiguous_fixture_sorted_callback_source)

    comprehension_target_module_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    [(c65_module := c65_target.__dict__) "
        + "for c65_target in (pt,)]\n"
        + "    c65_module['skip'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(comprehension_target_module_alias_source)

    starred_iterable_sys_modules_alias_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    for registry in [*(sys.modules,)]:\n"
        + "        registry['pytest'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(starred_iterable_sys_modules_alias_source)

    match_list_capture_pytest_module_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    match [pt]:\n"
        + "        case [module]:\n"
        + "            module.skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(match_list_capture_pytest_module_alias_source)

    nested_starred_iterable_sys_modules_alias_source = (
        "import sys\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    for registry in [*[*[sys.modules]]]:\n"
        + "        registry['pytest'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            nested_starred_iterable_sys_modules_alias_source
        )

    match_capture_pytest_module_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    match pt:\n"
        + "        case module:\n"
        + "            module.skip = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(match_capture_pytest_module_alias_source)

    match_sequence_capture_pytest_dict_alias_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    match pt.__dict__:\n"
        + "        case [registry]:\n"
        + "            registry['skip'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            match_sequence_capture_pytest_dict_alias_source
        )

    helper_match_pytest_module_alias_source = (
        "import pytest as pt\n\n"
        "def poison_from_match():\n"
        "    match pt:\n"
        "        case module:\n"
        "            module.skip = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    poison_from_match()\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(helper_match_pytest_module_alias_source)

    autouse_fixture_match_pytest_dict_alias_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_from_match():\n"
        "    match pt.__dict__:\n"
        "        case [registry]:\n"
        "            registry['skip'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(autouse_fixture_match_pytest_dict_alias_source)

    match_capture_pytest_dict_subscript_source = (
        "import pytest as pt\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    match [pt]:\n"
        + "        case [module]:\n"
        + "            module.__dict__['skip'] = str\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(match_capture_pytest_dict_subscript_source)

    external_fixture_pytest_dict_subscript_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_from_external_fixture(registry):\n"
        "    registry.__dict__['skip'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(external_fixture_pytest_dict_subscript_source)

    helper_comprehension_target_module_alias_source = (
        "import pytest as pt\n\n"
        "def poison_from_comprehension():\n"
        "    [(c65_module := c65_target.__dict__) "
        "for c65_target in (pt,)]\n"
        "    c65_module['skip'] = str\n\n"
        + valid_c65_guard_source.rstrip()
        + "\n    poison_from_comprehension()\n"
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            helper_comprehension_target_module_alias_source
        )

    autouse_fixture_external_registry_subscript_source = (
        "import pytest as pt\n\n"
        "@pt.fixture(autouse=True)\n"
        "def poison_from_external_fixture(registry):\n"
        "    registry['pytest'] = str\n\n" + valid_c65_guard_source
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            autouse_fixture_external_registry_subscript_source
        )

    reachable_fixture_external_registry_attribute_source = (
        "import pytest as pt\n\n"
        "@pt.fixture\n"
        "def poison_from_external_fixture(registry):\n"
        "    registry.skip = str\n\n"
        + valid_c65_guard_source.replace(
            "def test_c65_default_repository_resolver(monkeypatch):",
            "def test_c65_default_repository_resolver("
            "monkeypatch, poison_from_external_fixture):",
        )
    )
    with pytest.raises(AssertionError, match="mutate pytest or unittest API"):
        _assert_c65_live_assertion_guard(
            reachable_fixture_external_registry_attribute_source
        )

    # A code-hash fork must carry inherited case definitions but invalidate
    # their prior PASS receipts, so every case is rerun against the new hash.
    prior_cases = {"prior-pass": "tests_inago/test_fix.py::test_prior_passed_behavior"}
    prior_ledger = RepairTestLedger(
        campaign_id="campaign-c66-changed-hash-parent",
        required_cases=prior_cases,
        semantic_signatures={"prior-pass": "prior behavior remains protected"},
        code_sha256=BASE_SHA,
        invocation_parent_id=None,
    )
    prior_token = prior_ledger.begin_full_regression()
    prior_ledger.record_full_regression(
        _full_regression_evidence(
            repair_module.FullRegressionEvidence,
            token=prior_token,
            cases=prior_cases,
            code_sha256=BASE_SHA,
        )
    )
    changed_hash_ledger = prior_ledger.fork_campaign_with_case(
        campaign_id="campaign-c66-changed-hash-child",
        code_sha256=FIXED_SHA,
        semantic_case_id="new-case",
        node="tests_inago/test_fix.py::test_new_behavior",
        semantic_signature="new behavior for the changed code",
    )
    assert changed_hash_ledger.frozen_cases == {}
    assert changed_hash_ledger.frozen_pass_receipts == {}
    assert changed_hash_ledger.select_retry_nodes() == (
        prior_cases["prior-pass"],
        "tests_inago/test_fix.py::test_new_behavior",
    )
    with pytest.raises(RepairContractError):
        changed_hash_ledger.add_case(
            "prior-pass",
            "tests_inago/test_fix.py::test_relabelled_prior_behavior",
            semantic_signature="a different claim for an existing case",
        )

    investigation_module = import_module("automation.inago.maintenance.investigation")
    (
        _,
        _,
        _,
        artifact_plan_context,
        artifact_attempts_root,
        _,
        _,
        artifact_repair_attempt,
    ) = _start_approved_repair_attempt(tmp_path / "review-target-artifact")
    artifact_context = _make_repair_context(
        artifact_plan_context, artifact_repair_attempt
    )
    artifact_output_root = (
        artifact_attempts_root
        / artifact_plan_context["job_id"]
        / artifact_repair_attempt.attempt_id
    )
    artifact_output_root.mkdir(parents=True, exist_ok=True)
    artifact_path, artifact_sha256 = investigation_module.save_attempt_artifact(
        artifact_output_root,
        artifact_attempts_root,
        artifact_context,
        filename="review-target.json",
        content="{}",
        stage="repair",
    )
    assert artifact_path.endswith("/review-target.json")
    assert len(artifact_sha256) == 64


def test_c67_runner_boundary_contract(tmp_path, monkeypatch):
    from automation.inago.maintenance.repair import RepairContractError

    repair_module = import_module("automation.inago.maintenance.repair")
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    node = "tests_inago/test_runner.py::test_case"
    nested_store, nested_ledger, nested_runner, _, _ = _create_persisted_campaign(
        tmp_path / "nested",
        files={"tests_inago/test_runner.py": "def test_case():\n    assert True\n"},
        required_cases={"runner-case": node},
        semantic_signatures={"runner-case": "a simple runner case"},
        invocation_parent_id="parent-run",
    )
    with pytest.raises(RepairContractError) as nested:
        nested_runner.run_full_regression(nested_ledger)
    assert nested.value.code == "nested_test_invocation_rejected"
    assert (
        nested_store.load_repair_campaign(campaign_id=nested_ledger.campaign_id)[
            "run_receipts"
        ]
        == ()
    )

    case_store, case_ledger, case_runner, _, _ = _create_persisted_campaign(
        tmp_path / "sandbox-case",
        files={
            "automation/inago/maintenance/fix.py": "value = 1\n",
            "tests_inago/test_runner.py": "def test_case():\n    assert True\n",
        },
        required_cases={"runner-case": node},
        semantic_signatures={"runner-case": "a sandbox-bound runner case"},
    )
    full_store, full_ledger, full_runner, _, _ = _create_persisted_campaign(
        tmp_path / "sandbox-full",
        files={
            "automation/inago/maintenance/fix.py": "value = 1\n",
            "tests_inago/test_runner.py": "def test_case():\n    assert True\n",
        },
        required_cases={"runner-case": node},
        semantic_signatures={"runner-case": "a sandbox-bound full run"},
    )
    case_receipt = case_runner.run_case(case_ledger, "runner-case")
    assert case_receipt.results == {"runner-case": "PASS"}
    assert case_receipt.exit_code == 0

    full_ledger.begin_full_regression()
    full_evidence = full_runner.run_full_regression(full_ledger)
    assert full_evidence.results == {"runner-case": "PASS"}
    assert full_evidence.exit_code == 0
    full_ledger.record_full_regression(full_evidence)
    full_snapshot = full_store.load_repair_campaign(
        campaign_id=full_ledger.campaign_id
    )
    assert full_snapshot["state"] == "full_regression_recorded"

    bad_runs = (
        (
            "missing",
            "def test_present():\n    assert True\n",
            "tests_inago/test_runner.py::test_missing",
        ),
        (
            "extra",
            "import pytest\n"
            "@pytest.mark.parametrize('value', [1, 2])\n"
            "def test_case(value):\n    assert value in (1, 2)\n",
            node,
        ),
        (
            "skip",
            "import pytest\n"
            "@pytest.mark.skip(reason='C67 skip case')\n"
            "def test_case():\n    assert True\n",
            node,
        ),
        (
            "error",
            "def test_case():\n    raise RuntimeError('C67 error case')\n",
            node,
        ),
        (
            "hash-drift",
            "from pathlib import Path\n"
            "def test_case():\n"
            "    target = Path(__file__).parents[1] / 'automation/inago/maintenance/fix.py'\n"
            "    target.write_text('value = 2\\n', encoding='utf-8')\n"
            "    assert target.exists()\n",
            node,
        ),
    )
    for name, test_source, expected_node in bad_runs:
        run_store, run_ledger, run_runner, _, _ = _create_persisted_campaign(
            tmp_path / name,
            files={
                "automation/inago/maintenance/fix.py": "value = 1\n",
                "tests_inago/test_runner.py": test_source,
            },
            required_cases={"runner-case": expected_node},
            semantic_signatures={"runner-case": f"the {name} runner case"},
        )
        run_ledger.begin_full_regression()

        def result_factory(requested_nodes, checkout_root):
            if name == "missing":
                return [("tests_inago/test_runner.py::test_present", "PASS")], 0
            if name == "extra":
                return [
                    ("tests_inago/test_runner.py::test_case[1]", "PASS"),
                    ("tests_inago/test_runner.py::test_case[2]", "PASS"),
                ], 0
            if name == "skip":
                return [(expected_node, "SKIP")], 0
            if name == "error":
                return [(expected_node, "ERROR")], 1
            if name == "hash-drift":
                (checkout_root / "automation/inago/maintenance/fix.py").write_text(
                    "value = 2\n", encoding="utf-8"
                )
                return [(expected_node, "PASS")], 0
            raise AssertionError(f"unexpected malformed run case: {name}")

        fake_executions = _install_fake_pytest_executor(
            monkeypatch,
            repair_module,
            result_factory=result_factory,
        )
        with pytest.raises(RepairContractError) as invalid_run:
            run_runner.run_full_regression(run_ledger)
        expected_errors = {
            "missing": "host_pytest_node_set_mismatch",
            "extra": "host_pytest_node_set_mismatch",
            "skip": "host_pytest_run_not_clean",
            "error": "host_pytest_run_not_clean",
            "hash-drift": "repair_code_hash_drift",
        }
        assert invalid_run.value.code == expected_errors[name]
        run_snapshot = run_store.load_repair_campaign(
            campaign_id=run_ledger.campaign_id
        )
        assert run_snapshot["state"] == "full_regression_failed"
        assert run_snapshot["full_regression_failure_code"] == expected_errors[name]
        assert run_snapshot["run_receipts"] == ()
        assert len(fake_executions) == 1

    # Pytest encodes class methods across JUnit classname and testcase name.
    # The repair runner must reconstruct the complete persisted node ID.
    class_case_id = "class-case"
    class_node = "tests_inago/test_runner.py::TestGroup::test_case"
    (
        class_store,
        class_ledger,
        class_runner,
        class_checkout_root,
        _,
    ) = _create_persisted_campaign(
        tmp_path / "class-node-junit",
        files={
            "automation/inago/maintenance/fix.py": "value = 1\n",
            "tests_inago/test_runner.py": (
                "class TestGroup:\n"
                "    def test_case(self):\n"
                "        assert True\n"
            ),
        },
        required_cases={class_case_id: class_node},
        semantic_signatures={class_case_id: "class-based exact node mapping"},
    )
    class_ledger.begin_full_regression()
    class_executions = []

    def execute_class_junit(argv, *, cwd, env, timeout):
        import xml.etree.ElementTree as ET

        assert env.get("PYTHONOPTIMIZE") == "0"
        del timeout
        junit_index = argv.index("--junitxml")
        junit_path = Path(argv[junit_index + 1])
        requested_nodes = tuple(argv[junit_index + 2 :])
        class_executions.append((Path(cwd), requested_nodes))
        assert requested_nodes == (class_node,)
        junit_root = ET.Element("testsuites")
        suite = ET.SubElement(
            junit_root,
            "testsuite",
            tests="1",
            failures="0",
            errors="0",
            skipped="0",
        )
        ET.SubElement(
            suite,
            "testcase",
            classname="tests_inago.test_runner.TestGroup",
            name="test_case",
        )
        junit_path.write_bytes(
            ET.tostring(junit_root, encoding="utf-8", xml_declaration=True)
        )
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        repair_module, "_run_local_pytest", execute_class_junit
    )
    class_evidence = class_runner.run_full_regression(class_ledger)
    assert class_executions == [(class_checkout_root, (class_node,))]
    assert class_evidence.cases == ((class_case_id, class_node),)
    assert class_evidence.results == {class_case_id: "PASS"}
    class_ledger.record_full_regression(class_evidence)
    class_snapshot = class_store.load_repair_campaign(
        campaign_id=class_ledger.campaign_id
    )
    assert class_snapshot["full_regression"]["cases"] == ((class_case_id, class_node),)
    assert class_snapshot["full_regression"]["results"] == {class_case_id: "PASS"}
    class_receipts = tuple(
        item
        for item in class_snapshot["run_receipts"]
        if item["receipt_id"] == class_evidence.receipt_id
    )
    assert len(class_receipts) == 1
    assert class_receipts[0]["cases"] == ((class_case_id, class_node),)
    assert class_receipts[0]["results"] == {class_case_id: "PASS"}
    assert class_receipts[0]["consumed"] is True

    # The primary host-run error and Store fail-close error must both remain
    # observable if durable failure closure itself fails. This private executor
    # fixture exercises error reporting only; it is not sandbox evidence.
    fail_close_node = "tests_inago/test_runner.py::test_fail_close"
    (
        fail_close_store,
        fail_close_ledger,
        fail_close_runner,
        _,
        _,
    ) = _create_persisted_campaign(
        tmp_path / "fail-close-error",
        files={
            "automation/inago/maintenance/fix.py": "value = 1\n",
            "tests_inago/test_runner.py": "def test_fail_close():\n    assert True\n",
        },
        required_cases={"runner-case": fail_close_node},
        semantic_signatures={"runner-case": "the fail-close reporting case"},
    )
    fail_close_ledger.begin_full_regression()
    close_failure_code = "synthetic_fail_close_store_error"
    close_attempts = []

    def fail_store_close(**values):
        close_attempts.append(values["failure_code"])
        raise RepairContractError(close_failure_code)

    monkeypatch.setattr(
        fail_close_store, "_fail_repair_full_regression", fail_store_close
    )

    def primary_run_failure(requested_nodes, _checkout_root):
        assert requested_nodes == (fail_close_node,)
        return [(fail_close_node, "FAIL")], 1

    failed_execution = _install_fake_pytest_executor(
        monkeypatch,
        repair_module,
        result_factory=primary_run_failure,
    )
    with pytest.raises(RepairContractError) as both_failures:
        fail_close_runner.run_full_regression(fail_close_ledger)
    assert both_failures.value.code == "host_pytest_run_not_clean"
    assert close_attempts == ["host_pytest_run_not_clean"]

    def surfaced_failure_codes(error):
        pending = [error]
        seen = set()
        visible = []
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            code = getattr(current, "code", None)
            if code is not None:
                visible.append(str(code))
            visible.append(str(current))
            visible.extend(str(note) for note in getattr(current, "__notes__", ()))
            for attribute in (
                "__cause__",
                "__context__",
                "close_error",
                "failure_close_error",
                "store_error",
                "secondary_error",
            ):
                nested = getattr(current, attribute, None)
                if isinstance(nested, BaseException):
                    pending.append(nested)
            nested_exceptions = getattr(current, "exceptions", ())
            if isinstance(nested_exceptions, tuple):
                pending.extend(
                    item
                    for item in nested_exceptions
                    if isinstance(item, BaseException)
                )
        return "\n".join(visible)

    assert close_failure_code in surfaced_failure_codes(both_failures.value)
    assert failed_execution == [(fail_close_node,)]
    unresolved_snapshot = fail_close_store.load_repair_campaign(
        campaign_id=fail_close_ledger.campaign_id
    )
    assert unresolved_snapshot["state"] == "full_regression_started"
    assert unresolved_snapshot["run_receipts"] == ()
