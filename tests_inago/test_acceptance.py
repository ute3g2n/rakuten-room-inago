"""Cross-step fixture and source-document acceptance checks for STEP 19."""

from __future__ import annotations

import pytest

from automation.inago.acceptance import (
    DOCUMENT_CASE_IDS,
    FIXTURE_CASE_IDS,
    EXPECTED_CASE_IDS,
    load_acceptance_registry,
    main as acceptance_main,
    record_document_evidence,
    run_fixture_subset_for_selected_cases,
    validate_document_contract,
    validate_simulation_result,
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
    return run_fixture_subset_for_selected_cases(
        project_root,
        acceptance_registry,
        selected_case_ids,
    )


@pytest.mark.fixture_contract
def test_acceptance_registry_covers_every_case(acceptance_registry):
    assert tuple(acceptance_registry) == EXPECTED_CASE_IDS
    assert len(acceptance_registry) == 77
    assert len({case.case_id for case in acceptance_registry.values()}) == 77
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
