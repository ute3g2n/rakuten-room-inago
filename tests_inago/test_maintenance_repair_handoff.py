import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago.maintenance import production_workflow as workflow
from automation.inago.maintenance.store import MaintenanceStoreError


def _encoded(document):
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def test_repair_handoff_is_hash_bound_and_resumable(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    attempts_root = runtime_root / "attempts"
    worktree_root = runtime_root / "worktrees" / "job-handoff"
    job_id = "job-handoff"
    attempt_id = "repair-attempt"
    plan_attempt_id = "plan-attempt"
    result_version = f"repair:{attempt_id}:implementation-v1"
    base_commit = "a" * 40
    code_sha256 = "c" * 64
    file_sha256 = "d" * 64
    identity = {
        "incident_id": "incident-handoff",
        "source_launch_id": "launch-handoff",
        "source_run_id": "run-handoff",
        "target_date": "2026-10-19",
        "job_id": job_id,
    }
    product_path = "automation/inago/daily_pipeline.py"
    test_path = "tests_inago/test_candidate.py"
    owned_paths = [product_path, test_path]
    case_id = "CASE-HANDOFF-1"
    case_node = f"{test_path}::test_candidate"
    case_nodes = [
        {
            "case_id": case_id,
            "node": case_node,
            "requirement_step": [{"requirement_id": "REQ-1", "step_number": 1}],
        }
    ]
    product_file_sha256 = {product_path: file_sha256}
    worktree_root.mkdir(parents=True)
    attempt_root = attempts_root / job_id / attempt_id
    attempt_root.mkdir(parents=True)
    plan_root = attempts_root / job_id / plan_attempt_id
    plan_root.mkdir(parents=True)

    def write(relative_path, document_or_bytes):
        path = attempts_root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            document_or_bytes
            if isinstance(document_or_bytes, bytes)
            else _encoded(document_or_bytes)
        )
        path.write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    scope_path = f"{job_id}/{plan_attempt_id}/repair-scope.json"
    scope_sha256 = write(scope_path, {"repair_scope": {"owned_paths": owned_paths}})
    case_map_path = f"{job_id}/{plan_attempt_id}/requirement-step-case-map.json"
    case_map = {
        "schema_version": "maintenance-requirement-step-case-map-v1",
        "result_version": "plan-version-1",
        "case_nodes": case_nodes,
    }
    case_map_sha256 = write(case_map_path, case_map)
    plan_path = f"{job_id}/{plan_attempt_id}/result.json"
    plan = {
        "schema_version": "maintenance-planning-result-v1",
        "identity": identity,
        "attempt_id": plan_attempt_id,
        "result_version": "plan-version-1",
        "base_commit": base_commit,
        "plan_status": "ready",
        "next_action": "handoff",
        "review": {
            "status": "approved",
            "gate_status": "approved",
            "reviewed_version": "plan-version-1",
            "reviewer_id": "plan-reviewer",
        },
        "output": {
            "repair_scope": {"path": scope_path, "sha256": scope_sha256},
            "requirement_step_case_map": {
                "path": case_map_path,
                "sha256": case_map_sha256,
            },
        },
    }
    plan_sha256 = write(plan_path, plan)

    setup_path = f"{job_id}/{attempt_id}/repair-setup.json"
    setup = {
        "schema_version": "maintenance-repair-setup-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "plan_attempt_id": plan_attempt_id,
        "plan_result_version": "plan-version-1",
        "plan_output_sha256": plan_sha256,
        "base_commit": base_commit,
        "worktree_commit": base_commit,
        "worktree_root": str(worktree_root),
        "owned_paths": owned_paths,
        "case_manifest": {
            "schema_version": case_map["schema_version"],
            "result_version": "plan-version-1",
            "case_nodes": case_nodes,
        },
        "case_manifest_sha256": "e" * 64,
        "campaign_status": "pending_candidate_code",
        "prior_review_findings": [],
    }
    setup_sha256 = write(setup_path, setup)

    author_response = {
        **identity,
        "schema_version": "maintenance-repair-author-response-v1",
        "attempt_id": attempt_id,
        "result_version": result_version,
        "status": "implemented",
        "summary": "Synthetic candidate implementation.",
        "changed_files": [product_path],
    }
    implementer_role_config_sha256 = "f" * 64
    implementation_turn = {
        "turn_id": "implementer-turn",
        "thread_id": "implementer-thread",
        "observation_status": "configured_unverified",
    }
    implementation_output_path = (
        f"{job_id}/{attempt_id}/B--common-implementer--{'1' * 32}.json"
    )
    implementation_envelope = {
        "schema_version": "maintenance-ai-response-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "owner": {
            "parent_key": "B",
            "role_name": "common-implementer",
            "role_config_sha256": implementer_role_config_sha256,
        },
        "allowed_outputs": [product_path],
        "thread_id": implementation_turn["thread_id"],
        "turn_id": implementation_turn["turn_id"],
        "observation_status": implementation_turn["observation_status"],
        "response": author_response,
    }
    implementation_ai_sha256 = write(
        implementation_output_path, implementation_envelope
    )
    implementation_path = f"{job_id}/{attempt_id}/implementation-result.json"
    implementation = {
        "schema_version": "maintenance-repair-implementation-result-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "setup_sha256": setup_sha256,
        "plan_output_sha256": plan_sha256,
        "base_commit": base_commit,
        "response": author_response,
        "ai_output": {
            "path": implementation_output_path,
            "sha256": implementation_ai_sha256,
        },
        "turn": implementation_turn,
        "changed_product_paths": [product_path],
        "product_file_sha256": product_file_sha256,
        "code_sha256": code_sha256,
        "author_id": "repair-author",
    }
    implementation_sha256 = write(implementation_path, implementation)

    patch_path = f"{job_id}/{attempt_id}/patch-assessment.json"
    ledger_path = f"{job_id}/{attempt_id}/test-ledger.json"
    patch = {
        "schema_version": "maintenance-repair-patch-assessment-v1",
        "attestation_source": "host_collected",
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "tested_code_sha256": code_sha256,
        "assessment": {
            "accepted": True,
            "reason_codes": [],
            "changed_paths": [product_path],
            "assertion_snapshot_sha256": "8" * 64,
        },
        "owned_paths": owned_paths,
        "baseline_assertions": {},
        "candidate_assertions": {},
    }
    patch_sha256 = write(patch_path, patch)
    ledger = {
        "schema_version": "maintenance-repair-test-ledger-v1",
        "attestation_source": "host_collected",
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "campaign_id": f"repair-{attempt_id}-campaign",
        "code_sha256": code_sha256,
        "tested_code_sha256": code_sha256,
        "invocation_parent_id": None,
        "semantic_signatures": [
            {"semantic_case_id": case_id, "signature": "case-signature"}
        ],
        "frozen_cases": [],
        "frozen_pass_receipts": [],
        "required_cases": [{"semantic_case_id": case_id, "node": case_node}],
        "results": [
            {
                "semantic_case_id": case_id,
                "runs": [
                    {
                        "outcome": "PASS",
                        "code_sha256": code_sha256,
                        "receipt_id": "case-run-receipt",
                        "receipt_sha256": "9" * 64,
                    }
                ],
            }
        ],
        "full_regression": {
            "run_id": "full-run",
            "receipt_id": "full-run-receipt",
            "receipt_sha256": "a" * 64,
            "token": "full-run-token",
            "code_sha256": code_sha256,
            "cases": [{"semantic_case_id": case_id, "node": case_node}],
            "results": [{"semantic_case_id": case_id, "outcome": "PASS"}],
            "exit_code": 0,
            "collected": 1,
            "errors": 0,
            "skips": 0,
            "collection_errors": 0,
            "invocation_parent_id": None,
        },
        "c34_evidence": [],
    }
    ledger_sha256 = write(ledger_path, ledger)
    review_target_path = f"{job_id}/{attempt_id}/review-target.json"
    review_target = {
        "schema_version": "maintenance-repair-review-target-v1",
        "attestation_source": "host_collected",
        "identity": identity,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "code_sha256": code_sha256,
        "full_regression_receipt_id": "full-run-receipt",
        "full_regression_receipt_sha256": "a" * 64,
        "output": {
            "patch_assessment": {"path": patch_path, "sha256": patch_sha256},
            "test_ledger": {"path": ledger_path, "sha256": ledger_sha256},
        },
    }
    review_target_sha256 = write(review_target_path, review_target)

    review_input_path = f"{job_id}/{attempt_id}/review-input.json"
    product_diff_sha256 = hashlib.sha256(
        b"synthetic product diff"
        + json.dumps(
            product_file_sha256,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    review_input = {
        "schema_version": "maintenance-repair-review-input-v1",
        "identity": identity,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "base_commit": base_commit,
        "candidate_code_sha256": code_sha256,
        "product_diff_sha256": product_diff_sha256,
        "product_paths": [product_path],
        "product_file_sha256": product_file_sha256,
        "approved_plan": {
            "path": plan_path,
            "sha256": plan_sha256,
            "result_version": "plan-version-1",
            "reviewer_id": "plan-reviewer",
        },
        "implementation": {
            "path": implementation_path,
            "sha256": implementation_sha256,
            "author_id": "repair-author",
            "turn_id": implementation_turn["turn_id"],
            "thread_id": implementation_turn["thread_id"],
        },
        "patch_assessment": {"path": patch_path, "sha256": patch_sha256},
        "test_ledger": {"path": ledger_path, "sha256": ledger_sha256},
        "review_target": {
            "path": review_target_path,
            "sha256": review_target_sha256,
        },
        "approved_cases": [{"case_id": case_id, "node": case_node}],
    }
    review_input_sha256 = write(review_input_path, review_input)

    execution_id = f"repair-review-{review_input_sha256[:24]}"
    reviewer_response_schema = workflow._daily_repair_review_response_schema(
        identity=identity,
        attempt_id=attempt_id,
        result_version=result_version,
        reviewer_execution_id=execution_id,
        reviewer_output_sha256=review_input_sha256,
        review_target_sha256=review_target_sha256,
        code_sha256=code_sha256,
        product_paths=[product_path],
        case_ids=[case_id],
    )
    reviewer_response = {
        key: value["const"]
        for key, value in reviewer_response_schema["properties"].items()
        if "const" in value
    }
    reviewer_response.update(
        status="approved",
        summary="Approved synthetic product diff and test results.",
        findings=[],
    )
    reviewer_id = "repair-reviewer-thread"
    reviewer_turn_id = "repair-reviewer-turn"
    reviewer_session_key = "B:common-reviewer"
    reviewer_role_config_sha256 = "b" * 64
    reviewer_output_path = f"{job_id}/{attempt_id}/B--common-reviewer--{'2' * 32}.json"
    reviewer_envelope = {
        "schema_version": "maintenance-ai-response-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "owner": {
            "parent_key": "B",
            "role_name": "common-reviewer",
            "role_config_sha256": reviewer_role_config_sha256,
        },
        "allowed_outputs": ["review-response.json"],
        "thread_id": reviewer_id,
        "turn_id": reviewer_turn_id,
        "observation_status": "verified",
        "response": reviewer_response,
    }
    reviewer_output_sha256 = write(reviewer_output_path, reviewer_envelope)
    review_response_path = f"{job_id}/{attempt_id}/review-response.json"
    review_response = {
        "schema_version": "maintenance-repair-review-result-v1",
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "review_input": {"path": review_input_path, "sha256": review_input_sha256},
        "review_target": {
            "path": review_target_path,
            "sha256": review_target_sha256,
        },
        "ai_output": {
            "path": reviewer_output_path,
            "sha256": reviewer_output_sha256,
        },
        "reviewer": {
            "role": "common-reviewer",
            "role_config_sha256": reviewer_role_config_sha256,
            "session_key": reviewer_session_key,
            "turn_id": reviewer_turn_id,
            "thread_id": reviewer_id,
            "observation_status": "verified",
            "execution_id": execution_id,
        },
        "response": reviewer_response,
    }
    write(review_response_path, review_response)

    host_receipt_id = "host-review-receipt"
    receipt_id = "approval-receipt"
    receipt_sha256 = "c" * 64
    review_path = f"{job_id}/{attempt_id}/review.json"
    review = {
        "schema_version": "maintenance-repair-review-v1",
        "attestation_source": "host_collected",
        "status": "approved",
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "author_id": "repair-author",
        "reviewer_id": reviewer_id,
        "tested_result_version": result_version,
        "reviewed_result_version": result_version,
        "unresolved_critical_findings": [],
        "tested_code_sha256": code_sha256,
        "reviewed_code_sha256": code_sha256,
        "host_receipt_id": host_receipt_id,
        "receipt_id": receipt_id,
        "receipt_sha256": receipt_sha256,
        "reviewed_output_sha256": review_target_sha256,
        "patch_sha256": patch_sha256,
        "test_ledger_sha256": ledger_sha256,
    }
    review_sha256 = write(review_path, review)
    result_path = f"{job_id}/{attempt_id}/result.json"
    result = {
        "schema_version": "maintenance-repair-result-v1",
        "attestation_source": "host_collected",
        "identity": identity,
        "attempt_id": attempt_id,
        "result_version": result_version,
        "tested_code_sha256": code_sha256,
        "repair_status": "complete",
        "completion_status": "ready",
        "review_status": "approved",
        "output": {
            "patch_assessment": {"path": patch_path, "sha256": patch_sha256},
            "test_ledger": {"path": ledger_path, "sha256": ledger_sha256},
            "review_target": {
                "path": review_target_path,
                "sha256": review_target_sha256,
            },
            "review": {"path": review_path, "sha256": review_sha256},
        },
        "reviewer_id": reviewer_id,
        "reviewed_result_version": result_version,
        "reviewed_code_sha256": code_sha256,
        "reviewed_output_sha256": review_target_sha256,
    }
    result_sha256 = write(result_path, result)

    attempt = SimpleNamespace(
        attempt_id=attempt_id,
        job_id=job_id,
        step_key="maintenance_repair",
        sequence=3,
        attempt_no=1,
        source_run_id=identity["source_run_id"],
        status="complete",
        result_version=result_version,
        output_path=result_path,
        output_sha256=result_sha256,
    )
    plan_attempt = SimpleNamespace(
        attempt_id=plan_attempt_id,
        job_id=job_id,
        step_key="maintenance_planning",
        sequence=2,
        status="complete",
        result_version="plan-version-1",
        output_path=plan_path,
        output_sha256=plan_sha256,
    )
    host_execution = SimpleNamespace(
        host_receipt_id=host_receipt_id,
        job_id=job_id,
        attempt_id=attempt_id,
        result_version=result_version,
        author_id="repair-author",
        reviewer_id=reviewer_id,
        reviewer_session_key=reviewer_session_key,
        reviewer_turn_id=reviewer_turn_id,
        reviewer_output_sha256=reviewer_output_sha256,
        reviewed_output_sha256=review_target_sha256,
        code_sha256=code_sha256,
        patch_sha256=patch_sha256,
        test_ledger_sha256=ledger_sha256,
        approved=True,
        unresolved_critical_findings=(),
    )
    approval_receipt = SimpleNamespace(
        receipt_id=receipt_id,
        job_id=job_id,
        attempt_id=attempt_id,
        result_version=result_version,
        author_id="repair-author",
        reviewer_id=reviewer_id,
        reviewer_role="read_only_reviewer",
        host_receipt_id=host_receipt_id,
        reviewed_output_sha256=review_target_sha256,
        code_sha256=code_sha256,
        patch_sha256=patch_sha256,
        test_ledger_sha256=ledger_sha256,
        receipt_sha256=receipt_sha256,
        consumed=True,
    )

    class FakeStore:
        database_path = runtime_root / "state.sqlite"

        def get_job_input_manifest(self, *, job_id):
            assert job_id == identity["job_id"]
            return SimpleNamespace(identity=lambda: identity)

        def get_step_attempt_review(self, *, attempt_id):
            if attempt_id == plan_attempt_id:
                return {
                    "status": "approved",
                    "reviewed_version": "plan-version-1",
                    "reviewer_id": "plan-reviewer",
                }
            assert attempt_id == attempt_id_value
            return {"status": "approved", "reviewed_version": result_version}

        def get_repair_step_predecessor(self, *, attempt_id):
            assert attempt_id == attempt_id_value
            return {
                "attempt_id": plan_attempt_id,
                "result_version": "plan-version-1",
                "output_sha256": plan_sha256,
            }

        def get_step_attempt(self, *, attempt_id):
            return plan_attempt if attempt_id == plan_attempt_id else attempt

        def get_job_base_commit(self, *, job_id):
            assert job_id == identity["job_id"]
            return base_commit

        def get_latest_deployed_commit(self):
            return base_commit

        def get_repair_host_review_execution(self, *, host_receipt_id):
            assert host_receipt_id == host_execution.host_receipt_id
            return host_execution

        def get_repair_review_receipt(self, *, receipt_id):
            assert receipt_id == approval_receipt.receipt_id
            return approval_receipt

        def read_attempt_artifact(
            self, *, attempts_root, relative_path, expected_sha256
        ):
            content = (
                Path(attempts_root).joinpath(*relative_path.split("/")).read_bytes()
            )
            if hashlib.sha256(content).hexdigest() != expected_sha256:
                raise MaintenanceStoreError("attempt_output_digest_mismatch")
            return content

    attempt_id_value = attempt_id
    fake_store = FakeStore()
    monkeypatch.setattr(workflow, "_has_reparse_point", lambda _path: False)
    monkeypatch.setattr(
        workflow,
        "_run_git",
        lambda _root, *args: (
            f"{base_commit}\n".encode("ascii")
            if args[0] == "rev-parse"
            else b"synthetic product diff"
        ),
    )
    monkeypatch.setattr(workflow, "repository_code_sha256", lambda _root: code_sha256)
    monkeypatch.setattr(
        workflow,
        "_daily_repair_product_file_hashes",
        lambda _root, _paths: product_file_sha256,
    )
    monkeypatch.setattr(
        workflow,
        "_changed_paths_since",
        lambda _root, _base: (product_path, test_path),
    )

    workflow_context = SimpleNamespace(
        launch=SimpleNamespace(
            source_launch_id=identity["source_launch_id"],
            target_date=identity["target_date"],
            source_run_id=identity["source_run_id"],
        ),
        historical_input=None,
    )
    handoff, handoff_path, handoff_sha256 = workflow._ensure_daily_repair_handoff(
        store=fake_store,
        workflow=workflow_context,
        attempt=attempt,
        identity=identity,
        result_document=result,
    )
    assert handoff["candidate"]["code_sha256"] == code_sha256
    assert handoff["review"]["receipt_id"] == receipt_id
    assert handoff["test"]["case_count"] == 1
    assert handoff_path == f"{job_id}/{attempt_id}/repair-handoff.json"
    handoff_bytes = attempts_root.joinpath(*handoff_path.split("/")).read_bytes()
    assert hashlib.sha256(handoff_bytes).hexdigest() == handoff_sha256

    (
        resumed_handoff,
        resumed_path,
        resumed_sha256,
    ) = workflow._ensure_daily_repair_handoff(
        store=fake_store,
        workflow=workflow_context,
        attempt=attempt,
        identity=identity,
        result_document=result,
    )
    assert resumed_handoff == handoff
    assert resumed_path == handoff_path
    assert resumed_sha256 == handoff_sha256

    handoff_file = attempts_root.joinpath(*handoff_path.split("/"))
    handoff_file.write_bytes(handoff_bytes + b"tampered")
    with pytest.raises(workflow.RepairContractError):
        workflow._ensure_daily_repair_handoff(
            store=fake_store,
            workflow=workflow_context,
            attempt=attempt,
            identity=identity,
            result_document=result,
        )
