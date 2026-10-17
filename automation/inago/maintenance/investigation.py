"""Validate and route the daily check and incident investigation stages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
import hashlib
import html
import json
import os
from pathlib import PurePosixPath
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from automation.inago.maintenance.safe_file import (
    SafeFileError,
    open_file_beneath,
    unlink_file_beneath,
)

from automation.inago.maintenance.paths import (
    UnsafeMaintenancePathError,
    _has_reparse_point,
)


CHECK_RESPONSE_SCHEMA_VERSION = "maintenance-check-response-v1"
INVESTIGATION_RESPONSE_SCHEMA_VERSION = "maintenance-investigation-response-v1"
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


def _validate_echo(response: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    for key in _CONTEXT_FIELDS:
        actual = response.get(key, object())
        expected = context[key]
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
    _validate_echo(data, context)
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
    _validate_echo(data, job)
    if data.get("stage") != "investigation":
        raise InvestigationContractError("investigation_stage_mismatch")
    if data["schema_version"] != INVESTIGATION_RESPONSE_SCHEMA_VERSION:
        raise InvestigationContractError("investigation_schema_version_mismatch")
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

    return data


def _markdown_text(value: object, *, fallback: str = "未確認") -> str:
    if not isinstance(value, str) or not value.strip():
        value = fallback
    flattened = " ".join(value.split())
    escaped = html.escape(flattened, quote=False)
    for char in ("\\", "`", "*", "_", "[", "]", "|", "#", ">"):
        escaped = escaped.replace(char, f"&#{ord(char)};")
    return escaped


def _fact_lines(fact: Mapping[str, Any]) -> list[str]:
    confidence = fact["cause_confidence"]
    confidence_label = {
        "confirmed": "確認できた",
        "limited": "限定的",
        "unknown": "unknown（不明）",
    }[confidence]
    cause = (
        _markdown_text(fact["cause_statement"])
        if confidence != "unknown"
        else "未確認。根本原因は断定していません。"
    )
    refs = ", ".join(_markdown_text(ref) for ref in fact["evidence_refs"])
    return [
        f"- fact_id: `{_markdown_text(fact['fact_id'])}` / 区分: {_markdown_text(fact['classification'])} / 工程: {_markdown_text(fact['phase'])}",
        f"- 操作: {_markdown_text(fact['operation'])}",
        f"- 期待: {_markdown_text(fact['expected'])}",
        f"- 実結果: {_markdown_text(fact['actual'])}",
        f"- 影響: {_markdown_text(fact['impact'])}",
        f"- 回復・再試行: {_markdown_text(fact['recovery'])}",
        f"- 原因確度: {confidence_label}。{cause}",
        f"- 根拠: {refs}",
    ]


def render_investigation_markdown(
    validated: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    result_version: str,
    created_at: datetime | str,
    synthetic_fixture: bool = False,
) -> str:
    """Render the fixed nine-chapter report from validated facts only."""

    job = _validate_context(context)
    data = validate_investigation_response(validated, job)
    if not _safe_id(result_version):
        raise InvestigationContractError("result_version_invalid")
    if isinstance(created_at, datetime):
        parsed_at = created_at
    elif isinstance(created_at, str):
        try:
            parsed_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvestigationContractError("created_at_invalid") from exc
    else:
        raise InvestigationContractError("created_at_invalid")
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        raise InvestigationContractError("created_at_timezone_missing")
    created = parsed_at.isoformat()

    if not isinstance(synthetic_fixture, bool):
        raise InvestigationContractError("synthetic_fixture_invalid")
    facts = data["facts"]
    lines = [
        f"# 日次不具合調査レポート（{_markdown_text(job['target_date'])}）",
        "",
        "## 1. 対象runと結論",
        "",
        f"- incident_id: `{_markdown_text(job['incident_id'])}`",
        f"- 対象日（JST）: {_markdown_text(job['target_date'])}",
        f"- source_launch_id: `{_markdown_text(job['source_launch_id'])}`",
        f"- source_run_id: `{_markdown_text(job['source_run_id'] or 'null')}`",
        f"- 調査版 / base commit: `{result_version}` / `{_markdown_text(job['base_commit'])}`",
        f"- 調査結論: {_markdown_text(data['summary'])}",
        f"- 状態: `{data['status']}`。review_required: `{str(data['review_required']).lower()}`",
        "",
        "## 2. 目標・実績・影響",
        "",
        "| 区分 | 期待 | 実結果 | 影響 | 根拠 |",
        "|---|---|---|---|---|",
    ]
    if synthetic_fixture:
        lines[2:2] = [
            "> 合成fixtureから生成した検証用レポート。実障害の調査結果ではありません。",
            "",
        ]
    if facts:
        for fact in facts:
            refs = ", ".join(_markdown_text(ref) for ref in fact["evidence_refs"])
            lines.append(
                "| {classification} / {phase} | {expected} | {actual} | {impact} | {refs} |".format(
                    classification=_markdown_text(fact["classification"]),
                    phase=_markdown_text(fact["phase"]),
                    expected=_markdown_text(fact["expected"]),
                    actual=_markdown_text(fact["actual"]),
                    impact=_markdown_text(fact["impact"]),
                    refs=refs,
                )
            )
    else:
        lines.append("| 該当なし | 未確認 | 未確認 | 未確認 | 根拠なし |")
    lines.extend(["", "集計単位・対象範囲: 入力で許可された参照だけ。未指定の件数は補完していません。", ""])

    chapter_facts = [facts[index] if index < len(facts) else None for index in range(3)]
    chapter_titles = (
        "## 3. 工程別事象A",
        "## 4. 工程別事象B・回復",
        "## 5. 工程別事象C・契約結果",
    )
    for title, fact in zip(chapter_titles, chapter_facts):
        lines.extend([title, ""])
        if fact is None:
            lines.append("該当なし。確認できる事象はありません。")
        else:
            lines.extend(_fact_lines(fact))
        lines.append("")
    if len(facts) > 3:
        lines.extend(["追加の工程別事象", ""])
        for fact in facts[3:]:
            lines.extend([f"### 事象 `{_markdown_text(fact['fact_id'])}`", ""])
            lines.extend(_fact_lines(fact))
            lines.append("")

    lines.extend(["## 6. 日次が最終状態に至った経路", ""])
    if facts:
        for index, fact in enumerate(facts, start=1):
            lines.append(
                f"{index}. `{_markdown_text(fact['phase'])}`: {_markdown_text(fact['actual'])}（{_markdown_text(fact['fact_id'])}）"
            )
    else:
        lines.append("1. 入力証拠に経路を示す事実はありません。未確認として扱います。")
    lines.extend(["", "runの最終statusとOS/CLIの終了値は、このstageの入力にないため未確認です。", ""])

    lines.extend(["## 7. 障害でなかった工程・監視/記録上の課題", ""])
    lines.extend(
        [
            "| 項目 | 観測結果 | 判定根拠 | 欠測・限界 |",
            "|---|---|---|---|",
        ]
    )
    observations = data["non_incident_observations"]
    if observations:
        for item in observations:
            lines.append(
                f"| {_markdown_text(item['item'])} | {_markdown_text(item['observation'])} | {_markdown_text(item['basis'])} | {_markdown_text(item['limits'])} |"
            )
    else:
        lines.append("| 該当なし | 未確認 | 根拠不足のため正常判定なし | 未確認 |")
    lines.extend(["", "## 8. 原因確度と次の検証候補", "", "### 確認できた事実", ""])
    for fact in facts:
        refs = ", ".join(_markdown_text(ref) for ref in fact["evidence_refs"])
        lines.append(
            f"- `{_markdown_text(fact['fact_id'])}`: {_markdown_text(fact['actual'])}（根拠: {refs}）"
        )
    if not facts:
        lines.append("- 確認済み事実はありません。")
    lines.extend(["", "### 未確定の原因", ""])
    unknown_facts = [fact for fact in facts if fact["cause_confidence"] == "unknown"]
    if unknown_facts:
        for fact in unknown_facts:
            lines.append(
                f"- `{_markdown_text(fact['fact_id'])}`: unknown（不明）。根本原因は未確認です。"
            )
    else:
        lines.append("- 未確定の原因は報告されていません。")
    lines.extend(["", "### 調査・試験から判明した不足", ""])
    for item in data["missing_evidence"] + data["investigation_limits"]:
        lines.append(f"- {_markdown_text(item)}")
    if not data["missing_evidence"] and not data["investigation_limits"]:
        lines.append("- 不足として記録された項目はありません。")
    lines.extend(
        [
            "",
            "### 次に必要な検証・修正候補",
            "",
            "| 対象 | 根拠 | 検証方法 | 失敗時の停止/再開条件 |",
            "|---|---|---|---|",
        ]
    )
    if data["verification_candidates"]:
        for item in data["verification_candidates"]:
            lines.append(
                f"| {_markdown_text(item)} | 上記の許可済みfact/evidence参照 | 読取専用の隔離確認 | 根拠不足ならblockedとして再開条件を記録 |"
            )
    else:
        lines.append("| 該当なし | 未確認 | 追加情報をcontrollerへ依頼 | 情報取得後に再開 |")

    lines.extend(
        [
            "",
            "## 9. 根拠・確認限界",
            "",
            "| evidence_ref / fact_id | 資料・観測範囲 | 版・時刻 | 内容確認結果 | 公開上の制限 |",
            "|---|---|---|---|---|",
        ]
    )
    refs_to_report = sorted(
        {ref for fact in facts for ref in fact["evidence_refs"]}
        | set(data["evidence_refs"])
    )
    if refs_to_report:
        for ref in refs_to_report:
            lines.append(
                f"| `{_markdown_text(ref)}` | 許可された参照ID。本文は複製していません。 | `{_markdown_text(job['base_commit'])}` / 未確認 | 対応するfactを確認 | 原本は参照のみ |"
            )
    else:
        lines.append("| 該当なし | 許可された証拠参照はありません。 | 未確認 | 内容未確認 | 原本は参照のみ |")
    lines.extend(
        [
            "",
            f"本書は{created}に調査版 `{result_version}` として生成しました。独立reviewは別stageで行います。未確認事項を完了扱いにしていません。",
            "",
        ]
    )
    return "\n".join(lines)


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
    max_review_rounds: int = 3,
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
        isinstance(max_review_rounds, bool)
        or not isinstance(max_review_rounds, int)
        or not 1 <= max_review_rounds <= 3
        or isinstance(review_round, bool)
        or not isinstance(review_round, int)
        or not 1 <= review_round <= max_review_rounds
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

    if data["status"] == "approved":
        next_action = "handoff"
        handoff_allowed = True
    elif data["status"] == "changes_requested" and review_round < max_review_rounds:
        next_action = "revise"
        handoff_allowed = False
    else:
        next_action = "blocked"
        handoff_allowed = False
    return {
        "handoff_allowed": handoff_allowed,
        "next_action": next_action,
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
    if review_status != "approved":
        raise InvestigationContractError("handoff_review_not_approved")
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
) -> tuple[str, str]:
    """Create one immutable U12 artifact under its exact job/attempt directory."""

    job = _validate_context(context)
    if filename not in {"facts.json", "investigation.md", "result.json"}:
        raise InvestigationContractError("investigation_artifact_name_invalid")
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
    try:
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
            raise InvestigationContractError("investigation_artifact_already_exists")
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
        if created:
            try:
                unlink_file_beneath(base, relative_path)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                exc.add_note(f"created artifact cleanup failed: {cleanup_error!r}")
        raise InvestigationContractError("investigation_artifact_write_failed") from exc

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
