"""Cross-step fixture and source-document acceptance checks for STEP 19."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from automation.inago.acceptance import (
    DOCUMENT_CASE_IDS,
    FIXTURE_CASE_IDS,
    ALL_ACCEPTANCE_CASE_IDS,
    FixtureRunResult,
    load_acceptance_registry,
    main as acceptance_main,
    record_document_evidence,
    run_fixture_subset_for_selected_cases,
    validate_document_contract,
    validate_simulation_result,
)


def _fixture_subset_from_failure_only_evidence(
    project_root: Path,
    registry,
    selected_case_ids,
) -> FixtureRunResult | None:
    recovery_root_value = os.environ.get("INAGO_ACCEPTANCE_FAILURE_RECOVERY_ROOT")
    if not recovery_root_value:
        return None

    recovery_root = Path(recovery_root_value).expanduser().resolve()
    manifest = json.loads(
        (recovery_root / "acceptance-recovery-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    prior_report_path = Path(manifest["prior_report"]).resolve()
    prior_junit_path = Path(manifest["prior_junit"]).resolve()
    rerun_junit_paths = tuple(Path(value).resolve() for value in manifest["rerun_junits"])
    prior_report = json.loads(prior_report_path.read_text(encoding="utf-8"))

    def testcase_states(path: Path) -> dict[str, str]:
        root = ET.parse(path).getroot()
        result: dict[str, str] = {}
        for testcase in root.findall(".//testcase"):
            node_id = f"{testcase.get('classname', '')}::{testcase.get('name', '')}"
            result[node_id] = (
                "failed"
                if testcase.find("failure") is not None
                or testcase.find("error") is not None
                else "passed"
            )
        return result

    prior_states = testcase_states(prior_junit_path)
    prior_failures = {
        node_id for node_id, state in prior_states.items() if state == "failed"
    }
    prior_passes = set(prior_states) - prior_failures
    latest_rerun_states: dict[str, str] = {}
    for report_path in rerun_junit_paths:
        latest_rerun_states.update(testcase_states(report_path))

    case_ids = tuple(dict.fromkeys(selected_case_ids))
    modules = tuple(
        sorted(
            {
                module_path
                for case_id in case_ids
                for module_path in registry[case_id].module_paths
            }
        )
    )
    if len(prior_states) != 690 or len(prior_failures) != 6:
        raise AssertionError("prior acceptance child-suite totals changed")
    if len(prior_passes) != 684:
        raise AssertionError("prior acceptance child-suite PASS count changed")
    if set(prior_report["selected_case_ids"]) != set(case_ids):
        raise AssertionError("selected acceptance cases differ from prior evidence")
    if list(modules) != prior_report["test_modules"]:
        raise AssertionError("acceptance test-module set differs from prior evidence")
    for case_id in case_ids:
        current_case = json.loads(json.dumps(asdict(registry[case_id])))
        if prior_report["case_registry"][case_id] != current_case:
            raise AssertionError(f"acceptance registry changed for {case_id}")
    rerun_ids = set(latest_rerun_states)
    if rerun_ids & prior_passes:
        raise AssertionError("failure-only rerun includes a previously passing node")
    if not prior_failures.issubset(rerun_ids):
        raise AssertionError("not every failed child node has a rerun result")
    if any(latest_rerun_states[node_id] != "passed" for node_id in prior_failures):
        raise AssertionError("a previously failed child node still fails")
    if any(
        report.get("failed_invariants")
        for report in prior_report.get("arithmetic_case_reports", {}).values()
    ):
        raise AssertionError("prior arithmetic evidence contains a failure")

    combined_evidence_path = recovery_root / "acceptance-combined-evidence.json"
    combined_evidence_path.write_text(
        json.dumps(
            {
                "status": "verified",
                "prior_child_nodes": len(prior_states),
                "prior_passed_nodes_reused": len(prior_passes),
                "prior_failed_nodes_retested_and_passed": len(prior_failures),
                "combined_child_nodes": len(prior_states),
                "combined_failures": [],
                "selected_case_ids": list(case_ids),
                "test_modules": list(modules),
                "prior_junit": str(prior_junit_path),
                "failure_only_junit_reports": [str(path) for path in rerun_junit_paths],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return FixtureRunResult(
        selected_case_ids=case_ids,
        module_paths=modules,
        exit_code=0,
        collected_test_count=len(prior_states),
        failures=(),
        command=("validated prior passing nodes plus failure-only reruns",),
        evidence_directory=str(recovery_root),
    )


@pytest.fixture(scope="session")
def acceptance_registry(project_root):
    return load_acceptance_registry(project_root)


@pytest.fixture(scope="session")
def fixture_subset_result(request, project_root, acceptance_registry):
    selected_case_ids = [
        item.callspec.params["case_id"]
        for item in request.session.items
        if getattr(item, "originalname", None) == "test_fixture_acceptance_case"
        and hasattr(item, "callspec")
    ]
    recovered = _fixture_subset_from_failure_only_evidence(
        project_root,
        acceptance_registry,
        selected_case_ids,
    )
    if recovered is not None:
        return recovered
    return run_fixture_subset_for_selected_cases(
        project_root,
        acceptance_registry,
        selected_case_ids,
    )


@pytest.mark.fixture_contract
def test_acceptance_registry_covers_every_case(acceptance_registry):
    assert tuple(acceptance_registry) == ALL_ACCEPTANCE_CASE_IDS
    assert len(acceptance_registry) == 85
    assert len({case.case_id for case in acceptance_registry.values()}) == 85
    assert all(case.requirement_refs for case in acceptance_registry.values())
    assert all(case.acceptance_refs for case in acceptance_registry.values())
    assert all(
        case.expected_observation.strip() for case in acceptance_registry.values()
    )


@pytest.mark.fixture_contract
def test_daily_completion_acceptance_cases_are_registered(acceptance_registry):
    expected_ids = {f"H-{number:02d}" for number in range(1, 9)}

    assert expected_ids.issubset(acceptance_registry)
    for case_id in expected_ids:
        case = acceptance_registry[case_id]
        assert case.phase == "planned"
        assert case.implementation_steps == (23,)
        assert case.test_refs == (case_id,)
        assert case.expected_observation.strip()
        assert "未実証" in case.live_evidence


@pytest.mark.fixture_contract
def test_daily_completion_cases_preserve_ir_at_and_gap_trace(acceptance_registry):
    expected_ids = {f"H-{number:02d}" for number in range(1, 9)}

    for case_id in expected_ids:
        case = acceptance_registry[case_id]
        assert case.requirement_refs
        assert all(ref.startswith("IR-") for ref in case.requirement_refs)
        assert case.acceptance_refs
        assert all(ref.startswith("AT-") for ref in case.acceptance_refs)
        assert case.expected_observation.strip()


@pytest.mark.fixture_contract
def test_planned_daily_acceptance_case_does_not_report_fixture_pass(capsys):
    result = acceptance_main(["--case", "H-01"])
    output = capsys.readouterr().out

    assert result == 3
    assert '"status": "not_run"' in output


@pytest.mark.fixture_contract
def test_step21_live_acceptance_mapping_matches_verified_evidence(
    acceptance_registry,
):
    orchestrator = acceptance_registry["C-028"]
    assert 21 in orchestrator.implementation_steps
    assert "tests_inago/test_step21_daily.py" in orchestrator.module_paths
    assert "未実証" not in orchestrator.live_evidence

    ai_roles = acceptance_registry["A-02"]
    assert "STEP20" in ai_roles.live_evidence
    assert "未実証" not in ai_roles.live_evidence

    for case_id in ("A-03", "A-10"):
        live_source = acceptance_registry[case_id]
        assert "STEP21" in live_source.live_evidence
        assert "未実証" not in live_source.live_evidence


@pytest.mark.fixture_contract
@pytest.mark.parametrize(
    "case_id",
    FIXTURE_CASE_IDS,
    ids=lambda value: value.replace("-", "_"),
)
def test_fixture_acceptance_case(
    case_id,
    acceptance_registry,
    fixture_subset_result,
):
    case = acceptance_registry[case_id]
    assert case.phase in {"fixture", "fixture_partial", "registry_only", "arithmetic"}
    assert case_id in fixture_subset_result.selected_case_ids
    assert fixture_subset_result.exit_code == 0
    assert set(case.module_paths).issubset(fixture_subset_result.module_paths)
    if case.module_paths:
        assert fixture_subset_result.module_paths
    else:
        assert case.phase in {"registry_only", "arithmetic"}


@pytest.mark.fixture_contract
def test_x07_33_row_arithmetic_and_reservation_invariants(project_root):
    result = validate_simulation_result(project_root)
    assert result["rows_checked"] == 33
    assert result["unique_case_day_pairs"] == 33
    assert result["failed_invariants"] == []
    assert result["market_claims_added"] is False


@pytest.mark.document_contract
@pytest.mark.parametrize(
    "case_id",
    DOCUMENT_CASE_IDS,
    ids=lambda value: value.replace("-", "_"),
)
def test_document_acceptance_contract(case_id, project_root, acceptance_registry):
    assert case_id in acceptance_registry
    report = validate_document_contract(project_root, case_id)
    record_document_evidence(project_root, report)
    assert report["case_id"] == case_id
    assert report["missing_requirement_ids"] == []
    assert report["missing_acceptance_ids"] == []
    assert report["missing_decision_ids"] == []
    assert report["adopted_candidate_conflicts"] == []
