"""Validate and route the daily check and incident investigation stages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
import hashlib
import json
import os
from pathlib import PurePosixPath
from pathlib import Path
import re
import uuid
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from automation.inago.maintenance.safe_file import (
    SafeFileError,
    open_file_beneath,
    publish_file_beneath,
    unlink_file_beneath,
)

from automation.inago.maintenance.paths import (
    UnsafeMaintenancePathError,
    _has_reparse_point,
)


CHECK_RESPONSE_SCHEMA_VERSION = "maintenance-check-response-v1"
INVESTIGATION_RESPONSE_SCHEMA_VERSION = "maintenance-investigation-response-v2"
INVESTIGATION_REVIEW_SCHEMA_VERSION = "maintenance-investigation-review-v1"
HANDOFF_SCHEMA_VERSION = "maintenance-handoff-v1"

_CONTEXT_FIELDS = (
    "incident_id",
    "source_launch_id",
    "source_run_id",
    "target_date",
    "job_id",
    "attempt_id",
    "base_commit",
    "input_refs",
    "evidence_refs",
)
_OPTIONAL_CONTEXT_FIELDS = ("attempt", "allowed_outputs")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = re.compile(
    r"^(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$",
    re.I,
)
_PRECLASSIFICATIONS = frozenset(
    {
        "normal_candidate",
        "candidate_shortfall",
        "recovered_retry",
        "external_wait",
        "needs_investigation",
        "contract_violation",
        "unknown",
    }
)
_NORMAL_PRECLASSIFICATIONS = frozenset(
    {"normal_candidate", "candidate_shortfall", "recovered_retry"}
)
_FACT_FIELDS = frozenset(
    {
        "fact_id",
        "classification",
        "phase",
        "target_date",
        "operation",
        "expected",
        "actual",
        "impact",
        "recovery",
        "cause_confidence",
        "cause_statement",
        "evidence_refs",
    }
)


class InvestigationContractError(ValueError):
    """A stage response, identity, or handoff does not match its contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MaintenanceCheckStageResult:
    """A non-persistent result from the check-stage router."""

    status: str
    next_stage: str | None
    started_steps: tuple[str, ...]
    incident_write_allowed: bool
    reason_code: str
    response: Mapping[str, Any] | None = None


@lru_cache(maxsize=3)
def _schema_validator(name: str) -> Draft202012Validator:
    if name not in {
        "maintenance-check-response.schema.json",
        "maintenance-investigation-response.schema.json",
        "maintenance-investigation-review.schema.json",
    }:
        raise InvestigationContractError("response_schema_unknown")
    path = Path(__file__).resolve().parents[1] / "schemas" / name
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker())
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise InvestigationContractError("response_schema_unavailable") from exc


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None


def _safe_attempt_path_id(value: object) -> bool:
    return (
        _safe_id(value)
        and not any(char in _WINDOWS_INVALID_PATH_CHARS for char in value)
        and not value.endswith((".", " "))
        and _WINDOWS_RESERVED_NAMES.fullmatch(value) is None
    )


def _safe_ref(value: object) -> bool:
    if not isinstance(value, str) or _REF_PATTERN.fullmatch(value) is None:
        return False
    return all(part not in {".", ".."} for part in value.split("/"))


def _validate_refs(value: object, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise InvestigationContractError(f"{field}_invalid")
    refs = list(value)
    if any(not _safe_ref(ref) for ref in refs) or len(set(refs)) != len(refs):
        raise InvestigationContractError(f"{field}_invalid")
    return refs


def _validate_context(context: object) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise InvestigationContractError("job_context_invalid")
    data = dict(context)
    for key in _CONTEXT_FIELDS:
        if key not in data:
            raise InvestigationContractError("job_context_incomplete")
    for key in ("incident_id", "source_launch_id"):
        if not _safe_id(data[key]):
            raise InvestigationContractError(f"{key}_invalid")
    for key in ("job_id", "attempt_id"):
        if not _safe_attempt_path_id(data[key]):
            raise InvestigationContractError(f"{key}_invalid")
    if data["source_run_id"] is not None and not _safe_id(data["source_run_id"]):
        raise InvestigationContractError("source_run_id_invalid")
    if not isinstance(data["target_date"], str):
        raise InvestigationContractError("target_date_invalid")
    try:
        if date.fromisoformat(data["target_date"]).isoformat() != data["target_date"]:
            raise ValueError
    except ValueError as exc:
        raise InvestigationContractError("target_date_invalid") from exc
    if (
        not isinstance(data["base_commit"], str)
        or _COMMIT_PATTERN.fullmatch(data["base_commit"]) is None
    ):
        raise InvestigationContractError("base_commit_invalid")
    data["input_refs"] = _validate_refs(data["input_refs"], "input_refs")
    data["evidence_refs"] = _validate_refs(data["evidence_refs"], "evidence_refs")
    if "attempt" in data and (
        isinstance(data["attempt"], bool)
        or not isinstance(data["attempt"], int)
        or data["attempt"] < 1
    ):
        raise InvestigationContractError("attempt_invalid")
    if "allowed_outputs" in data:
        outputs = data["allowed_outputs"]
        if (
            not isinstance(outputs, (list, tuple))
            or isinstance(outputs, (str, bytes))
            or not 1 <= len(outputs) <= 16
        ):
            raise InvestigationContractError("allowed_outputs_invalid")
        normalized = [_safe_output_path(path) for path in outputs]
        if len({path.casefold() for path in normalized}) != len(normalized):
            raise InvestigationContractError("allowed_outputs_invalid")
        data["allowed_outputs"] = normalized
    unknown = set(data) - set(_CONTEXT_FIELDS) - set(_OPTIONAL_CONTEXT_FIELDS)
    if unknown:
        raise InvestigationContractError("job_context_field_unknown")
    return data


def _validate_echo(
    response: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    allow_evidence_subset: bool = False,
) -> None:
    for key in _CONTEXT_FIELDS:
        actual = response.get(key, object())
        expected = context[key]
        if key == "evidence_refs" and allow_evidence_subset:
            refs = _validate_refs(actual, "response_evidence_refs")
            if not set(refs).issubset(expected):
                raise InvestigationContractError("response_evidence_ref_not_allowed")
            continue
        if key in {"input_refs", "evidence_refs"}:
            actual = list(actual) if isinstance(actual, (list, tuple)) else actual
        if actual != expected:
            raise InvestigationContractError(f"{key}_mismatch")
    for key in _OPTIONAL_CONTEXT_FIELDS:
        if key in context:
            actual = response.get(key, object())
            expected = context[key]
            if key == "allowed_outputs" and isinstance(actual, (list, tuple)):
                actual = [_safe_output_path(path) for path in actual]
            if actual != expected:
                raise InvestigationContractError(f"{key}_mismatch")


def _validate_document(value: object, schema_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InvestigationContractError("response_not_object")
    data = dict(value)
    try:
        _schema_validator(schema_name).validate(data)
    except ValidationError as exc:
        raise InvestigationContractError("response_schema_invalid") from exc
    return data


def _check_response(response: object, context: Mapping[str, Any]) -> dict[str, Any]:
    data = _validate_document(response, "maintenance-check-response.schema.json")
    _validate_echo(data, context, allow_evidence_subset=True)
    allowed_evidence = set(context["evidence_refs"])
    if "evidence_refs" in data:
        refs = _validate_refs(data["evidence_refs"], "response_evidence_refs")
        if not set(refs).issubset(allowed_evidence):
            raise InvestigationContractError("response_evidence_ref_not_allowed")
    compatible_assessments = {
        "daily_complete": {"normal"},
        "daily_target_unmet": {"normal"},
        "daily_recovered_retry": {"normal"},
        "daily_failure_observed": {"needs_investigation"},
        "external_dependency_wait": {"waiting_external"},
        "daily_observation_incomplete": {"unknown"},
    }
    if data["assessment"] not in compatible_assessments[data["reason_code"]]:
        raise InvestigationContractError("check_assessment_reason_mismatch")
    return data


def run_check_stage(
    context: Mapping[str, Any],
    preclassification: str,
    check_runner: Callable[[dict[str, Any]], object],
) -> MaintenanceCheckStageResult:
    """Run the check callback once and route without writing incident state.

    ``check_runner`` is the controller's model/runtime boundary. This helper
    passes only the validated job identity, refs, and ``stage=check``. It never
    persists data or starts the investigation stage itself.
    """

    try:
        job = _validate_context(context)
    except InvestigationContractError as exc:
        return MaintenanceCheckStageResult("blocked", None, (), False, exc.code, None)
    if (
        not isinstance(preclassification, str)
        or preclassification not in _PRECLASSIFICATIONS
    ):
        return MaintenanceCheckStageResult(
            "blocked", None, (), False, "preclassification_invalid", None
        )
    if preclassification in {"unknown", "contract_violation"}:
        return MaintenanceCheckStageResult(
            "blocked",
            None,
            (),
            False,
            f"preclassification_{preclassification}",
            None,
        )
    if not callable(check_runner):
        return MaintenanceCheckStageResult(
            "blocked", None, (), False, "check_runner_invalid", None
        )

    expected_context = deepcopy(job)
    check_context = deepcopy(expected_context)
    check_context["stage"] = "check"
    try:
        raw_response = check_runner(check_context)
        response = _check_response(raw_response, expected_context)
    except Exception:
        # Do not copy runtime exception text into a persisted or user-visible
        # incident record. The caller can retain a separately redacted log.
        return MaintenanceCheckStageResult(
            "blocked", None, ("check",), False, "check_response_invalid", None
        )

    expected_reason_codes = {
        "normal_candidate": "daily_complete",
        "candidate_shortfall": "daily_target_unmet",
        "recovered_retry": "daily_recovered_retry",
    }
    if (
        preclassification in expected_reason_codes
        and response["reason_code"] != expected_reason_codes[preclassification]
    ):
        return MaintenanceCheckStageResult(
            "blocked",
            None,
            ("check",),
            False,
            "check_preclassification_reason_mismatch",
            response,
        )

    assessment = response["assessment"]
    if preclassification in {"unknown", "contract_violation"}:
        status, next_stage = "blocked", None
        reason_code = f"preclassification_{preclassification}"
    elif preclassification == "external_wait":
        status, next_stage = "waiting_external", None
        reason_code = "external_dependency_wait"
    elif preclassification == "needs_investigation":
        if assessment == "unknown":
            status, next_stage = "blocked", None
        else:
            status, next_stage = "investigation_required", "investigation"
        reason_code = "daily_failure_observed"
    elif assessment == "unknown":
        status, next_stage = "blocked", None
        reason_code = response["reason_code"]
    elif assessment == "waiting_external":
        status, next_stage = "waiting_external", None
        reason_code = response["reason_code"]
    elif assessment == "needs_investigation":
        status, next_stage = "investigation_required", "investigation"
        reason_code = response["reason_code"]
    elif preclassification in _NORMAL_PRECLASSIFICATIONS:
        status, next_stage = "checked_ok", None
        reason_code = response["reason_code"]
    else:
        status, next_stage = "blocked", None
        reason_code = response["reason_code"]

    return MaintenanceCheckStageResult(
        status,
        next_stage,
        ("check",),
        False,
        reason_code,
        response,
    )


def validate_investigation_response(
    response: object, context: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate the investigation output and all fact-to-evidence bindings."""

    job = _validate_context(context)
    data = _validate_document(
        response, "maintenance-investigation-response.schema.json"
    )
    _validate_echo(data, job, allow_evidence_subset=True)
    if data.get("stage") != "investigation":
        raise InvestigationContractError("investigation_stage_mismatch")
    if data["schema_version"] != INVESTIGATION_RESPONSE_SCHEMA_VERSION:
        raise InvestigationContractError("investigation_schema_version_mismatch")
    data.setdefault("artifact_origin", "agent")
    data.setdefault("operation_result", "succeeded")
    if data["artifact_origin"] not in {"agent", "controller_fallback"}:
        raise InvestigationContractError("investigation_artifact_origin_invalid")
    if data["operation_result"] not in {"succeeded", "failed", "unknown", "not_performed"}:
        raise InvestigationContractError("investigation_operation_result_invalid")
    allowed_evidence = set(job["evidence_refs"])
    if not set(data["evidence_refs"]).issubset(allowed_evidence):
        raise InvestigationContractError("investigation_evidence_ref_not_allowed")

    seen_fact_ids: set[str] = set()
    for fact in data["facts"]:
        if set(fact) != _FACT_FIELDS:
            raise InvestigationContractError("fact_fields_invalid")
        if fact["fact_id"] in seen_fact_ids:
            raise InvestigationContractError("fact_id_duplicate")
        seen_fact_ids.add(fact["fact_id"])
        if fact["target_date"] != job["target_date"]:
            raise InvestigationContractError("fact_target_date_mismatch")
        refs = _validate_refs(fact["evidence_refs"], "fact_evidence_refs")
        if not refs or not set(refs).issubset(allowed_evidence):
            raise InvestigationContractError("fact_evidence_ref_not_allowed")
        confidence = fact["cause_confidence"]
        statement = fact["cause_statement"]
        if confidence == "unknown":
            if statement is not None and statement.strip():
                raise InvestigationContractError("unknown_cause_statement_present")
        elif not isinstance(statement, str) or not statement.strip():
            raise InvestigationContractError("cause_statement_required")
        elif not refs:
            raise InvestigationContractError("cause_evidence_required")

    data["status"] = "complete"
    return data


def controller_fallback_investigation_response(
    context: Mapping[str, Any], *, report_markdown: str, failure_reason: str,
    checked_evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Build a valid, explicitly controller-authored unknown investigation."""

    job = _validate_context(context)
    available = set(job["evidence_refs"])
    used_refs = [ref for ref in (checked_evidence_refs or []) if ref in available]
    response = {
        **{key: job[key] for key in _CONTEXT_FIELDS},
        "schema_version": INVESTIGATION_RESPONSE_SCHEMA_VERSION,
        "stage": "investigation",
        "summary": (
            "調査役の通常回答を取得できなかったため、確認範囲と不明点を"
            "管理側が記録しました。根本原因は未確認です。"
        ),
        "facts": [],
        "investigation_limits": [
            "調査役の正常な構造化応答を取得できませんでした。",
            f"通常回答の失敗分類: {failure_reason}。例外の詳細は相関ログを参照してください。",
        ],
        "non_incident_observations": [],
        "verification_candidates": [
            "次の調査担当は利用可能な証拠参照を読み、未確認項目を確認してください。",
            "原因が確認できない場合は、操作境界の記録を追加する具体案を計画してください。",
        ],
        "missing_evidence": [
            "調査役の通常回答が得られなかったため、内容のある事実一覧は作成できませんでした。"
        ],
        "review_required": True,
        "report_markdown": report_markdown,
        "evidence_refs": used_refs,
        "artifact_origin": "controller_fallback",
        "operation_result": "failed",
        "fallback_reason_code": failure_reason,
        "fallback_artifact_refs": [
            f"{job['job_id']}/{job['attempt_id']}/stage-handoff.json"
        ],
    }
    return validate_investigation_response(response, context)


def render_investigation_markdown(
    validated: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    result_version: str,
    created_at: datetime | str,
    synthetic_fixture: bool = False,
) -> str:
    """Return the author's validated Markdown without transforming it."""

    _validate_context(context)
    response = dict(validated)
    response.pop("status", None)
    data = validate_investigation_response(response, context)
    if not _safe_id(result_version):
        raise InvestigationContractError("result_version_invalid")
    if not isinstance(created_at, (datetime, str)):
        raise InvestigationContractError("created_at_invalid")
    if not isinstance(synthetic_fixture, bool):
        raise InvestigationContractError("synthetic_fixture_invalid")
    return data["report_markdown"]


def _safe_output_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise InvestigationContractError("handoff_output_path_invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {".", ".."} or ":" in part for part in path.parts)
    ):
        raise InvestigationContractError("handoff_output_path_invalid")
    for part in path.parts:
        if (
            part.endswith((".", " "))
            or any(char in _WINDOWS_INVALID_PATH_CHARS for char in part)
            or _WINDOWS_RESERVED_NAMES.fullmatch(part) is not None
            or any(ord(char) < 32 for char in part)
        ):
            raise InvestigationContractError("handoff_output_path_invalid")
    return path.as_posix()


def validate_investigation_review(
    review: object,
    context: Mapping[str, Any],
    *,
    result_version: str,
    author_id: str,
    author_execution_id: str,
    reviewer_id: str,
    reviewer_execution_id: str,
    review_round: int,
) -> dict[str, Any]:
    """Validate an independent review against the exact investigation version."""

    job = _validate_context(context)
    if not _safe_id(result_version):
        raise InvestigationContractError("result_version_invalid")
    if not _safe_id(author_id) or not _safe_id(reviewer_id):
        raise InvestigationContractError("reviewer_identity_invalid")
    if author_id == reviewer_id:
        raise InvestigationContractError("reviewer_must_be_independent")
    if not _safe_id(author_execution_id) or not _safe_id(reviewer_execution_id):
        raise InvestigationContractError("review_execution_identity_invalid")
    if author_execution_id == reviewer_execution_id:
        raise InvestigationContractError("review_execution_must_be_fresh")
    if (
        isinstance(review_round, bool)
        or not isinstance(review_round, int)
        or not 1 <= review_round
    ):
        raise InvestigationContractError("review_round_invalid")

    data = _validate_document(review, "maintenance-investigation-review.schema.json")
    _validate_echo(data, job)
    if data["reviewer_id"] != reviewer_id:
        raise InvestigationContractError("reviewer_id_mismatch")
    if data["review_execution_id"] != reviewer_execution_id:
        raise InvestigationContractError("review_execution_id_mismatch")
    if data["reviewed_version"] != result_version:
        raise InvestigationContractError("reviewed_version_mismatch")
    if data["stage"] != "investigation_review":
        raise InvestigationContractError("review_stage_mismatch")
    if data["status"] == "changes_requested" and not data["findings"]:
        raise InvestigationContractError("review_findings_required")

    return {
        "handoff_allowed": True,
        "next_action": "handoff",
        "reviewer_id": reviewer_id,
        "review_execution_id": reviewer_execution_id,
        "reviewed_version": result_version,
        "review_round": review_round,
        "status": data["status"],
        "findings": list(data["findings"]),
    }


def build_investigation_handoff_payload(
    context: Mapping[str, Any],
    *,
    result_version: str,
    reviewed_version: str,
    output_path: str,
    output_sha256: str,
    facts_path: str,
    facts_sha256: str,
    report_path: str,
    report_sha256: str,
    review_status: str,
) -> dict[str, Any]:
    """Build the exact maintenance-handoff-v1 envelope accepted by the store."""

    job = _validate_context(context)
    if not _safe_id(result_version) or reviewed_version != result_version:
        raise InvestigationContractError("handoff_review_version_mismatch")
    if review_status not in {
        "approved",
        "changes_requested",
        "rejected",
        "unavailable",
    }:
        raise InvestigationContractError("handoff_review_status_invalid")
    if (
        not isinstance(output_sha256, str)
        or _SHA256_PATTERN.fullmatch(output_sha256) is None
    ):
        raise InvestigationContractError("handoff_output_digest_invalid")
    artifact_inputs = {
        "facts": (facts_path, facts_sha256, "facts.json"),
        "report": (report_path, report_sha256, "investigation.md"),
    }
    artifacts: dict[str, dict[str, str]] = {}
    for key, (path, digest, filename) in artifact_inputs.items():
        safe_path = _safe_output_path(path)
        if safe_path != f"{job['job_id']}/{job['attempt_id']}/{filename}":
            raise InvestigationContractError("handoff_output_path_invalid")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise InvestigationContractError("handoff_output_digest_invalid")
        artifacts[key] = {"path": safe_path, "sha256": digest}
    safe_output_path = _safe_output_path(output_path)
    if safe_output_path != f"{job['job_id']}/{job['attempt_id']}/result.json":
        raise InvestigationContractError("handoff_output_path_invalid")
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "incident_id": job["incident_id"],
        "job_id": job["job_id"],
        "attempt_id": job["attempt_id"],
        "source_run_id": job["source_run_id"],
        "result_version": result_version,
        "reviewed_version": reviewed_version,
        "output": {
            "path": safe_output_path,
            "sha256": output_sha256,
            "artifacts": artifacts,
        },
    }


def save_attempt_artifact(
    output_root: str | Path,
    attempts_root: str | Path,
    context: Mapping[str, Any],
    *,
    filename: str,
    content: str | bytes,
    stage: str = "investigation",
    atomic: bool = False,
) -> tuple[str, str]:
    """Create one immutable stage artifact under its exact job/attempt directory."""

    job = _validate_context(context)
    allowed_names = {
        "investigation": {
            "facts.json", "investigation.md", "result.json", "stage-handoff.json",
            "controller-fallback.json",
        },
        "planning": {
            "plan.md",
            "stage-handoff.json",
            "controller-fallback.json",
            "repair-scope.json",
            "requirement-step-case-map.json",
            "result.json",
            "review.json",
            "review.md",
        },
        "repair": {
            "stage-handoff.json",
            "repair-setup.json",
            "implementation-result.json",
            "patch-assessment.json",
            "test-ledger.json",
            "review-input.json",
            "review-target.json",
            "review-response.json",
            "review.json",
            "repair-handoff.json",
            "result.json",
        },
        "deployment": {"deployment-candidate.json", "deployment-result.json"},
        "report": {
            "report-input.json",
            "report-response.json",
            "report.html",
            "review-input.json",
            "review-response.json",
            "review.json",
            "review.md",
            "result.json",
            "report-publication.json",
            "report-revision-author-match-trace.json",
        },
    }
    versioned_report_name = (
        stage == "report"
        and re.fullmatch(
            r"(?:report-fallback-[a-f0-9]{20}\.html|report-input-fallback-[a-f0-9]{20}\.json|review-input-fallback-[a-f0-9]{20}\.json|review-response-fallback-[a-f0-9]{20}\.json|review-fallback-[a-f0-9]{20}\.json)",
            filename,
        )
        is not None
    )
    if stage not in allowed_names or (
        filename not in allowed_names[stage] and not versioned_report_name
    ):
        raise InvestigationContractError(f"{stage}_artifact_name_invalid")
    if isinstance(content, str):
        encoded = content.encode("utf-8")
    elif isinstance(content, bytes):
        encoded = content
    else:
        raise InvestigationContractError("investigation_artifact_content_invalid")

    base = Path(attempts_root).expanduser()
    root = Path(output_root).expanduser()
    try:
        _assert_no_reparse_components(base)
        _assert_no_reparse_components(root)
        base_resolved = base.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
        expected = base_resolved / job["job_id"] / job["attempt_id"]
        if (
            not base_resolved.is_dir()
            or not root_resolved.is_dir()
            or root_resolved != expected
        ):
            raise InvestigationContractError("investigation_output_root_invalid")
        relative_root = root_resolved.relative_to(base_resolved)
        if relative_root.parts != (job["job_id"], job["attempt_id"]):
            raise InvestigationContractError("investigation_output_root_invalid")
    except InvestigationContractError:
        raise
    except (OSError, RuntimeError, ValueError, UnsafeMaintenancePathError) as exc:
        raise InvestigationContractError("investigation_output_root_invalid") from exc

    relative_path = PurePosixPath(job["job_id"], job["attempt_id"], filename).as_posix()
    created = False
    temporary_relative_path: str | None = None
    try:
        if atomic:
            temporary_relative_path = PurePosixPath(
                job["job_id"],
                job["attempt_id"],
                f".{filename}.{uuid.uuid4().hex}.tmp",
            ).as_posix()
            with open_file_beneath(base, temporary_relative_path, "xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                publish_file_beneath(base, temporary_relative_path, relative_path)
            except FileExistsError:
                with open_file_beneath(base, relative_path, "rb") as stream:
                    existing_content = stream.read()
                if existing_content == encoded:
                    return relative_path, hashlib.sha256(encoded).hexdigest()
                raise InvestigationContractError(
                    "investigation_artifact_already_exists"
                )
        else:
            try:
                with open_file_beneath(base, relative_path, "xb") as stream:
                    created = True
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                with open_file_beneath(base, relative_path, "rb") as stream:
                    existing_content = stream.read()
                if existing_content == encoded:
                    return relative_path, hashlib.sha256(encoded).hexdigest()
                raise InvestigationContractError(
                    "investigation_artifact_already_exists"
                )
    except InvestigationContractError as exc:
        if created:
            try:
                unlink_file_beneath(base, relative_path)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                exc.add_note(f"created artifact cleanup failed: {cleanup_error!r}")
        raise
    except (OSError, RuntimeError, ValueError, UnsafeMaintenancePathError) as exc:
        if atomic:
            try:
                with open_file_beneath(base, relative_path, "rb") as stream:
                    existing_content = stream.read()
            except (OSError, RuntimeError, ValueError, UnsafeMaintenancePathError):
                existing_content = None
            if existing_content == encoded:
                return relative_path, hashlib.sha256(encoded).hexdigest()
        if created:
            try:
                unlink_file_beneath(base, relative_path)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                exc.add_note(f"created artifact cleanup failed: {cleanup_error!r}")
        raise InvestigationContractError("investigation_artifact_write_failed") from exc
    finally:
        if temporary_relative_path is not None:
            try:
                unlink_file_beneath(base, temporary_relative_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    return relative_path, hashlib.sha256(encoded).hexdigest()


def _assert_no_reparse_components(path: Path) -> None:
    """Reject symlink/junction components on the path to a U12 artifact."""

    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if _has_reparse_point(current):
                raise InvestigationContractError("investigation_path_reparse_point")
        except UnsafeMaintenancePathError as exc:
            raise InvestigationContractError(
                "investigation_path_uninspectable"
            ) from exc
