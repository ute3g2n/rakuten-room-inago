"""Synthetic maintenance investigation contracts for U12 tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


TARGET_DATE = "2026-10-17"
INCIDENT_ID = "incident-20261017-001"
LAUNCH_ID = "launch-20261017-130001"
RUN_ID = None
JOB_ID = "job-20261017-001"
ATTEMPT_ID = "attempt-20261017-001"
BASE_COMMIT = "a" * 40
INPUT_REFS = ["input/daily-result-001", "input/evidence-manifest-001"]
EVIDENCE_REFS = ["evidence/trace-001", "evidence/daily-result-001"]


def make_context(**overrides: Any) -> dict[str, Any]:
    """Return a fresh context containing only synthetic IDs and references."""
    context: dict[str, Any] = {
        "incident_id": INCIDENT_ID,
        "source_launch_id": LAUNCH_ID,
        "source_run_id": RUN_ID,
        "target_date": TARGET_DATE,
        "job_id": JOB_ID,
        "attempt_id": ATTEMPT_ID,
        "base_commit": BASE_COMMIT,
        "input_refs": deepcopy(INPUT_REFS),
        "evidence_refs": deepcopy(EVIDENCE_REFS),
    }
    context.update(overrides)
    return context


def make_check_response(
    context: dict[str, Any], *, assessment: str = "normal"
) -> dict[str, Any]:
    reason_codes = {
        "normal": "daily_complete",
        "needs_investigation": "daily_failure_observed",
        "waiting_external": "external_dependency_wait",
        "unknown": "daily_observation_incomplete",
    }
    return {
        "schema_version": "maintenance-check-response-v1",
        **deepcopy(context),
        "stage": "check",
        "assessment": assessment,
        "reason_code": reason_codes[assessment],
        "summary": "Synthetic check observation.",
    }


def make_investigation_response(
    context: dict[str, Any],
    *,
    cause_confidence: str = "unknown",
    cause_statement: str | None = None,
    fact_evidence_refs: list[str] | None = None,
    status: str = "complete",
) -> dict[str, Any]:
    if fact_evidence_refs is None:
        fact_evidence_refs = ["evidence/trace-001"]
    return {
        "schema_version": "maintenance-investigation-response-v1",
        **deepcopy(context),
        "stage": "investigation",
        "status": status,
        "summary": "A synthetic Trends observation needs isolated follow-up.",
        "facts": [
            {
                "fact_id": "fact-001",
                "classification": "incident",
                "phase": "daily",
                "target_date": context["target_date"],
                "operation": "Fetch the daily Trends summary.",
                "expected": "The summary is available for the target date.",
                "actual": "The summary response was empty.",
                "impact": "The maintenance check cannot confirm completion.",
                "recovery": "No recovery action was attempted.",
                "cause_confidence": cause_confidence,
                "cause_statement": cause_statement,
                "evidence_refs": deepcopy(fact_evidence_refs),
            }
        ],
        "investigation_limits": ["The upstream cause has not been verified."],
        "non_incident_observations": [],
        "verification_candidates": ["Repeat a read-only observation in isolation."],
        "missing_evidence": ["A correlated upstream response record."],
        "review_required": True,
    }


def make_review_response(
    context: dict[str, Any],
    *,
    reviewer_id: str,
    review_execution_id: str = "review-turn-001",
    reviewed_version: str,
    status: str,
    findings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "maintenance-investigation-review-v1",
        **deepcopy(context),
        "stage": "investigation_review",
        "reviewer_id": reviewer_id,
        "review_execution_id": review_execution_id,
        "reviewed_version": reviewed_version,
        "status": status,
        "findings": [] if findings is None else deepcopy(findings),
    }
