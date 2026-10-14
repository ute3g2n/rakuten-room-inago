import json
import sys
from datetime import date
from pathlib import Path

import pytest

from automation.codex_cli_runner import CodexCliRunner
from automation.jobs.contracts import CONTRACTS, check_job_output, prepare_job
from automation.run_context import RunContext


ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fixtures" / "fake_codex.py"
DATE = "2026-09-25"


def payload_for(job):
    value = {field: [] for field in CONTRACTS[job].required}
    if job == "performance":
        value["performance_input"] = {"analysis_date": DATE}
    if job == "research_plan":
        value["inventory_brief"] = {"genre_counts": {}}
        value["genre_targets"] = {}
    if job == "recovery":
        value = {"failed_run_id": "run-old", "failed_step": "triage", "failure_manifest": {}}
    if job == "daily_plan":
        value["genre_targets"] = {"家事ラク用品": 2}
    return value


def output_for(job, run_id):
    common = {"run_id": run_id, "target_date": DATE}
    return {
        "research_plan": {"target_date": DATE, "genre_priorities": [], "search_queries": [],
                          "winning_conditions": [], "deprioritized_conditions": [], "research_requests": []},
        "triage": {**common, "selected": [], "excluded": [], "uncertain": []},
        "product_verify": {**common, "verified": [], "rejected": [], "unavailable": []},
        "copywriter": {**common, "drafts": [], "unsupported_claims": []},
        "quality_gate": {**common, "accepted": [], "rejected": [], "required_edits": []},
        "daily_plan": {"target_date": DATE, "posts": [], "shortfall": [{"code": "EMPTY_STOCK", "detail": "no verified drafts"}], "applied_learning": []},
        "performance": {"analysis_date": DATE, "post_decisions": [], "cohort_insights": [],
                        "winning_conditions": [], "single_change_experiments": [],
                        "deprioritized_conditions": [], "research_requests": [],
                        "daily_plan_constraints": [], "held_items": [], "missing_or_unattributed_data": []},
        "recovery": {"failed_run_id": "run-old", "failed_step": "triage", "cause": "fetch failed",
                     "retryable": False, "proposed_action": "check input", "requires_user_action": True},
    }[job]


@pytest.mark.parametrize("job", list(CONTRACTS))
def test_fake_cli_contract_success_and_missing_input(tmp_path, job):
    context = RunContext.create(tmp_path / "orchestrator", "research", date.fromisoformat(DATE), "manual")
    payload = payload_for(job)
    with pytest.raises(ValueError):
        prepare_job(ROOT, context.logger.run_dir, job, context.run_id, DATE, {})
    spec = prepare_job(ROOT, context.logger.run_dir, job, context.run_id, DATE, payload)
    output = output_for(job, context.run_id)
    runner = CodexCliRunner(ROOT, executable=Path(sys.executable), executable_prefix=[str(FAKE)],
                            extra_env={"FAKE_PAYLOAD": json.dumps(output, ensure_ascii=False)}, timeout_seconds=5)
    result = runner.run(context, job, spec["subskill"], spec["input_path"], spec["envelope"],
                        spec["schema_name"], output_validator=lambda value: check_job_output(job, value, payload))
    assert result.success, (job, result.error_code)
    assert json.loads(spec["output_path"].read_text(encoding="utf-8")) == output
    assert not result.attempts[0]["escalated"]


def test_semantic_rejection_id_url_and_forbidden_copy(tmp_path):
    triage = output_for("triage", "run")
    triage["selected"] = [{"product_id": "P99", "reason": "unknown"}]
    with pytest.raises(ValueError):
        check_job_output("triage", triage, {"api_candidates": [{"product_id": "P01"}]})
    copy = output_for("copywriter", "run")
    copy["drafts"] = [{"product_id": "P01", "url": "https://item.rakuten.co.jp/wrong/item/",
                       "body": "No.1", "status": "draft", "checked_at": DATE}]
    with pytest.raises(ValueError):
        check_job_output("copywriter", copy, {"verified_products": [{"product_id": "P01", "url": "https://item.rakuten.co.jp/shop/item/"}]})
    with pytest.raises(ValueError):
        prepare_job(ROOT, tmp_path, "copywriter", "run", DATE,
                    {"verified_products": [], "single_change_experiments": [], "access_key": "secret"})


def test_fake_cli_forbidden_output_is_not_saved(tmp_path):
    context = RunContext.create(tmp_path / "orchestrator", "research", date.fromisoformat(DATE), "manual")
    payload = payload_for("copywriter")
    payload["verified_products"] = [{"product_id": "P01", "url": "https://item.rakuten.co.jp/shop/item/"}]
    spec = prepare_job(ROOT, context.logger.run_dir, "copywriter", context.run_id, DATE, payload)
    bad = output_for("copywriter", context.run_id)
    bad["drafts"] = [{"product_id": "P01", "url": "https://item.rakuten.co.jp/shop/item/",
                      "body": "No.1", "status": "accepted", "checked_at": DATE}]
    runner = CodexCliRunner(ROOT, executable=Path(sys.executable), executable_prefix=[str(FAKE)],
                            extra_env={"FAKE_PAYLOAD": json.dumps(bad, ensure_ascii=False)}, timeout_seconds=5)
    result = runner.run(context, "copywriter", spec["subskill"], spec["input_path"], spec["envelope"],
                        spec["schema_name"], output_validator=lambda value: check_job_output("copywriter", value, payload))
    assert result.error_code == "DATA_MISMATCH"
    assert not spec["output_path"].exists()
