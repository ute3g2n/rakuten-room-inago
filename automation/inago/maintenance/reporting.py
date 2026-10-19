"""Build, validate, and publish evidence-bound incident reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from automation.inago.maintenance.investigation import (
    InvestigationContractError,
    _assert_no_reparse_components,
    _validate_context,
    save_attempt_artifact,
)


REPORT_RESPONSE_SCHEMA_VERSION = "maintenance-report-response-v1"
REPORT_RESULT_SCHEMA_VERSION = "maintenance-report-result-v1"
REPORT_REVIEW_SCHEMA_VERSION = "maintenance-report-review-v1"
OUTPUT_FILENAME = "report.html"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_COMMIT_PATTERN = re.compile(r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ISO_JST_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+09:00$"
)
_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_EVENT_LABELS = {
    "observed": "当日発生",
    "investigation": "調査で判明",
    "test": "試験で確認",
}
_CONFIDENCE_LABELS = {
    "confirmed": "確認済み",
    "limited": "一部確認",
    "unknown": "未確定",
}
_STATUS_LABELS = {
    "complete": "確認完了",
    "pending": "未確認・保留",
    "blocked": "停止中",
}
_SENSITIVE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.I),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|cookie)\s*[:=]\s*\S+",
        re.I,
    ),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


class ReportContractError(ValueError):
    """A report, its available inputs, or its publication state is invalid."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReportStageArtifacts:
    """Validated report files ready for review and controller persistence."""

    report_status: str
    html_path: str
    html_sha256: str
    html: str
    result_path: str
    result_sha256: str
    result_json: str


@dataclass(frozen=True)
class ReportPublication:
    """Commit and push observations for one reviewed report file."""

    commit: str
    base_commit: str
    code_commit: str
    remote_name: str
    target_branch: str


@lru_cache(maxsize=3)
def _schema_validator(name: str) -> Draft202012Validator:
    if name not in {
        "maintenance-report-response.schema.json",
        "maintenance-report-result.schema.json",
        "maintenance-report-review.schema.json",
    }:
        raise ReportContractError("report_schema_unknown")
    path = Path(__file__).resolve().parents[1] / "schemas" / name
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise ReportContractError("report_schema_unavailable") from exc


def _id(value: object, code: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ReportContractError(code)
    return value


def _safe_href(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReportContractError("report_link_invalid")
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise ReportContractError("report_link_invalid")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith("/"):
        raise ReportContractError("report_link_invalid")
    if value.startswith("#"):
        if re.fullmatch(r"#[A-Za-z][A-Za-z0-9_-]{0,127}", value) is None:
            raise ReportContractError("report_link_invalid")
        return value
    decoded_path = unquote(parsed.path)
    if (
        parsed.query
        or parsed.fragment
        or any(part in {"", ".", ".."} for part in decoded_path.split("/"))
    ):
        raise ReportContractError("report_link_invalid")
    return value


def _validate_jst(value: object) -> str:
    if not isinstance(value, str):
        raise ReportContractError("report_created_at_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReportContractError("report_created_at_invalid") from exc
    if parsed.utcoffset() != timedelta(hours=9):
        raise ReportContractError("report_created_at_not_jst")
    return value


def _validate_sources(job: Mapping[str, Any], sources: object) -> dict[str, Any]:
    if not isinstance(sources, Mapping):
        raise ReportContractError("report_sources_invalid")
    data = dict(sources)
    required = {
        "identity",
        "investigation",
        "plan",
        "implementation",
        "tests",
        "deployment",
        "next_day",
        "evidence_index_href",
        "evidence_index_label",
    }
    if set(data) != required:
        raise ReportContractError("report_sources_invalid")
    identity = {
        key: job[key]
        for key in (
            "incident_id",
            "source_launch_id",
            "source_run_id",
            "target_date",
            "job_id",
        )
    }
    if data["identity"] != identity:
        raise ReportContractError("report_identity_mismatch")
    allowed_refs = set(job["evidence_refs"])
    href_fields = ["evidence_index_href"]
    for name in ("investigation", "plan", "tests", "deployment"):
        item = data[name]
        if not isinstance(item, Mapping):
            raise ReportContractError("report_sources_invalid")
        data[name] = dict(item)
    for name in ("investigation", "plan"):
        item = data[name]
        if set(item) != {"result_version", "review_status", "href"}:
            raise ReportContractError("report_sources_invalid")
        _id(item["result_version"], "report_source_version_invalid")
        if item["review_status"] not in {
            "approved",
            "changes_requested",
            "rejected",
            "unavailable",
            "not_started",
        }:
            raise ReportContractError("report_review_status_invalid")
        href_fields.append(f"{name}.href")
    implementation = data["implementation"]
    if set(implementation) != {
        "base_commit",
        "result_commit",
        "review_status",
    }:
        raise ReportContractError("report_sources_invalid")
    if (
        not isinstance(implementation["base_commit"], str)
        or _COMMIT_PATTERN.fullmatch(implementation["base_commit"]) is None
        or not isinstance(implementation["result_commit"], str)
        or _COMMIT_PATTERN.fullmatch(implementation["result_commit"]) is None
        or implementation["review_status"]
        not in {
            "approved",
            "changes_requested",
            "rejected",
            "unavailable",
            "not_started",
        }
    ):
        raise ReportContractError("report_implementation_status_invalid")
    tests = data["tests"]
    if set(tests) != {"status", "result_version", "summary", "evidence_href"}:
        raise ReportContractError("report_sources_invalid")
    if tests["status"] not in {
        "passed",
        "failed",
        "blocked",
        "not_run",
        "skipped",
        "error",
    }:
        raise ReportContractError("report_test_status_invalid")
    _id(tests["result_version"], "report_source_version_invalid")
    for key in ("summary",):
        if not isinstance(tests[key], str) or not tests[key].strip():
            raise ReportContractError("report_sources_invalid")
    href_fields.append("tests.evidence_href")
    deployment = data["deployment"]
    if set(deployment) != {
        "status",
        "tested_commit",
        "pushed_commit",
        "active_commit",
        "evidence_href",
    }:
        raise ReportContractError("report_sources_invalid")
    if deployment["status"] not in {
        "deployed",
        "pending",
        "blocked",
        "failed",
        "rolled_back",
        "not_attempted",
    }:
        raise ReportContractError("report_deployment_status_invalid")
    for field in ("tested_commit", "pushed_commit", "active_commit"):
        commit = deployment[field]
        if commit is not None and (
            not isinstance(commit, str) or _COMMIT_PATTERN.fullmatch(commit) is None
        ):
            raise ReportContractError("report_deployment_commit_invalid")
    if deployment["status"] == "deployed" and any(
        deployment[field] != implementation["result_commit"]
        for field in ("tested_commit", "pushed_commit", "active_commit")
    ):
        raise ReportContractError("report_deployment_commit_mismatch")
    if deployment["status"] != "deployed" and deployment["active_commit"] is not None:
        raise ReportContractError("report_active_commit_unverified")
    href_fields.append("deployment.evidence_href")
    next_day = data["next_day"]
    if set(next_day) != {"status", "observed_at", "evidence_href"} or next_day[
        "status"
    ] not in {
        "verified",
        "pending",
        "not_observed",
        "recurred",
        "blocked",
    }:
        raise ReportContractError("report_next_day_status_invalid")
    if next_day["status"] in {"verified", "recurred"}:
        observed_at = next_day["observed_at"]
        if not isinstance(observed_at, str) or not _ISO_JST_PATTERN.fullmatch(
            observed_at
        ):
            raise ReportContractError("report_next_day_observation_missing")
        if (
            not isinstance(next_day["evidence_href"], str)
            or not next_day["evidence_href"]
        ):
            raise ReportContractError("report_next_day_evidence_missing")
        try:
            observed = datetime.fromisoformat(observed_at)
            expected_day = date.fromisoformat(job["target_date"]) + timedelta(days=1)
        except (TypeError, ValueError) as exc:
            raise ReportContractError("report_next_day_observation_invalid") from exc
        if observed.date() != expected_day:
            raise ReportContractError("report_next_day_observation_date_mismatch")
        href_fields.append("next_day.evidence_href")
    elif next_day["status"] == "not_observed":
        observed_at = next_day["observed_at"]
        if (
            not isinstance(observed_at, str)
            or not _ISO_JST_PATTERN.fullmatch(observed_at)
            or next_day["evidence_href"] is not None
        ):
            raise ReportContractError("report_next_day_observation_unverified")
        try:
            observed = datetime.fromisoformat(observed_at)
            expected_day = date.fromisoformat(job["target_date"]) + timedelta(days=1)
        except (TypeError, ValueError) as exc:
            raise ReportContractError("report_next_day_observation_invalid") from exc
        if observed.date() < expected_day:
            raise ReportContractError("report_next_day_observation_date_mismatch")
    elif next_day["status"] == "blocked":
        if (next_day["observed_at"] is None) != (next_day["evidence_href"] is None):
            raise ReportContractError("report_next_day_observation_unverified")
        if next_day["observed_at"] is not None:
            observed_at = next_day["observed_at"]
            if not isinstance(observed_at, str) or not _ISO_JST_PATTERN.fullmatch(
                observed_at
            ):
                raise ReportContractError("report_next_day_observation_invalid")
            try:
                observed = datetime.fromisoformat(observed_at)
                expected_day = date.fromisoformat(job["target_date"]) + timedelta(
                    days=1
                )
            except (TypeError, ValueError) as exc:
                raise ReportContractError(
                    "report_next_day_observation_invalid"
                ) from exc
            if observed.date() != expected_day:
                raise ReportContractError("report_next_day_observation_date_mismatch")
            href_fields.append("next_day.evidence_href")
    elif next_day["observed_at"] is not None or next_day["evidence_href"] is not None:
        raise ReportContractError("report_next_day_observation_unverified")
    data["evidence_index_href"] = _safe_href(data["evidence_index_href"])
    if (
        not isinstance(data["evidence_index_label"], str)
        or not data["evidence_index_label"].strip()
    ):
        raise ReportContractError("report_sources_invalid")
    for field in href_fields:
        prefix, _, key = field.partition(".")
        raw = data[prefix][key] if key else data[prefix]
        safe = _safe_href(raw)
        if safe not in allowed_refs:
            raise ReportContractError("report_evidence_ref_not_allowed")
        if key:
            data[prefix][key] = safe
        else:
            data[prefix] = safe
    return data


def _report_status(sources: Mapping[str, Any]) -> str:
    statuses = (
        sources["tests"]["status"],
        sources["deployment"]["status"],
        sources["next_day"]["status"],
    )
    if any(
        value in {"failed", "blocked", "error", "rolled_back", "recurred"}
        for value in statuses
    ):
        return "blocked"
    if (
        sources["tests"]["status"] != "passed"
        or sources["deployment"]["status"] != "deployed"
        or sources["next_day"]["status"] != "verified"
    ):
        return "pending"
    return "complete"


def _validate_response(response: object, job: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        raise ReportContractError("report_response_invalid")
    data = dict(response)
    try:
        _schema_validator("maintenance-report-response.schema.json").validate(data)
    except ValidationError as exc:
        raise ReportContractError("report_response_schema_invalid") from exc
    expected_identity = {
        key: job[key]
        for key in (
            "incident_id",
            "source_launch_id",
            "source_run_id",
            "target_date",
            "job_id",
        )
    }
    if data["identity"] != expected_identity:
        raise ReportContractError("report_identity_mismatch")
    if data["schema_version"] != REPORT_RESPONSE_SCHEMA_VERSION:
        raise ReportContractError("report_response_version_invalid")
    _validate_jst(data["created_at_jst"])
    refs = set(job["evidence_refs"])
    for item in data["issues"]:
        item["evidence_ref"] = _safe_href(item["evidence_ref"])
        if item["evidence_ref"] not in refs:
            raise ReportContractError("report_evidence_ref_not_allowed")
    for item in data["checks"]:
        item["evidence_ref"] = _safe_href(item["evidence_ref"])
        if item["evidence_ref"] not in refs:
            raise ReportContractError("report_evidence_ref_not_allowed")
    text_values: list[str] = [data["title"], data["summary"], data["flow_tree"]]
    for item in data["issues"]:
        text_values.extend(
            str(item[key])
            for key in (
                "phase",
                "title",
                "operation",
                "expected",
                "actual",
                "impact",
                "recovery",
                "cause_statement",
                "changed_behavior",
                "confirmation_scope",
            )
        )
    for item in data["checks"]:
        text_values.extend(
            str(item[key]) for key in ("stage", "version", "expected", "actual")
        )
    recovery = data["recovery"]
    text_values.extend(
        str(recovery[key])
        for key in ("stop_reason", "resume_condition", "side_effects")
        if recovery[key] is not None
    )
    text_values.extend(str(value) for value in recovery["unknowns"])
    if any(
        pattern.search(value)
        for value in text_values
        for pattern in _SENSITIVE_PATTERNS
    ):
        raise ReportContractError("report_sensitive_text_detected")
    return data


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _render_issue_rows(issues: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for index, item in enumerate(issues, start=1):
        href = _e(item["evidence_ref"])
        rows.append(
            "<tr>"
            f'<td><a href="#issue-{index}">{_e(item["phase"])}</a></td>'
            f'<td>{_e(_EVENT_LABELS[item["event_class"]])}</td>'
            f'<td>{_e(item["expected"])}</td>'
            f'<td>{_e(item["actual"])}<br><strong>影響:</strong> {_e(item["impact"])}</td>'
            f'<td>{_e(item["recovery"])}</td>'
            f'<td><a href="{href}">根拠を確認</a></td>'
            "</tr>"
        )
    return "\n".join(rows)


def _render_issue_details(issues: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for index, item in enumerate(issues, start=1):
        href = _e(item["evidence_ref"])
        issue_class = {
            "observed": "actual",
            "investigation": "investigation",
            "test": "test",
        }[item["event_class"]]
        rows.append(
            f'<article class="issue {issue_class}" id="issue-{index}">'
            f"<h3>{_e(item['title'])}</h3>"
            f'<p><span class="status pending">{_e(_EVENT_LABELS[item["event_class"]])}</span></p>'
            "<h4>操作・期待・実結果</h4>"
            f"<p>{_e(item['operation'])}<br><strong>期待:</strong> {_e(item['expected'])}<br><strong>実結果:</strong> {_e(item['actual'])}</p>"
            "<h4>影響と回復</h4>"
            f"<p>{_e(item['impact'])}<br><strong>回復:</strong> {_e(item['recovery'])}</p>"
            "<h4>原因と分かっている範囲</h4>"
            f"<p><strong>{_e(_CONFIDENCE_LABELS[item['cause_confidence']])}:</strong> {_e(item['cause_statement'])}</p>"
            "<h4>変更後の動作</h4>"
            f"<p>{_e(item['changed_behavior'])}</p>"
            "<h4>確認範囲と根拠</h4>"
            f'<p>{_e(item["confirmation_scope"])} · <a href="{href}">確認記録を見る</a></p>'
            "</article>"
        )
    return "\n".join(rows)


def _render_check_rows(
    checks: Sequence[Mapping[str, Any]], sources: Mapping[str, Any]
) -> str:
    status_names = {
        "passed": ("passed", "確認済み"),
        "failed": ("failed", "失敗"),
        "blocked": ("blocked", "停止"),
        "not_run": ("pending", "未実施"),
        "skipped": ("pending", "未実施"),
        "error": ("failed", "確認できず"),
        "deployed": ("passed", "反映済み"),
        "pending": ("pending", "保留"),
        "rolled_back": ("blocked", "復旧済み・未反映"),
        "not_attempted": ("pending", "未実施"),
        "verified": ("passed", "確認済み"),
        "not_observed": ("pending", "未観測"),
        "recurred": ("failed", "再発"),
    }
    rows = []
    for item in checks:
        css, label = status_names[item["status"]]
        rows.append(
            "<tr>"
            f"<td>{_e(item['stage'])}</td><td>参考記録に記載</td>"
            f"<td>{_e(item['expected'])}</td><td>{_e(item['actual'])}</td>"
            f'<td><span class="status {css}">{label}</span></td>'
            f'<td><a href="{_e(item["evidence_ref"])}">確認記録を見る</a></td>'
            "</tr>"
        )
    source_rows = (
        (
            "修正後テスト",
            sources["tests"]["result_version"],
            "必須テストがPASS",
            sources["tests"]["summary"],
            sources["tests"]["status"],
            sources["tests"]["evidence_href"],
        ),
        (
            "本番反映",
            sources["implementation"]["result_commit"],
            "試験済み版を反映",
            f"tested={sources['deployment']['tested_commit']} / pushed={sources['deployment']['pushed_commit']} / active={sources['deployment']['active_commit']}",
            sources["deployment"]["status"],
            sources["deployment"]["evidence_href"],
        ),
        (
            "次回の日次確認",
            sources["implementation"]["result_commit"],
            "修正事象の再発有無を自然観測",
            sources["next_day"]["observed_at"] or "まだ観測していません",
            sources["next_day"]["status"],
            sources["next_day"]["evidence_href"] or sources["evidence_index_href"],
        ),
    )
    for stage, version, expected, actual, status, href in source_rows:
        css, label = status_names[status]
        rows.append(
            "<tr>"
            f"<td>{_e(stage)}</td><td>参考記録に記載</td><td>{_e(expected)}</td>"
            f'<td>{_e(actual)}</td><td><span class="status {css}">{label}</span></td>'
            f'<td><a href="{_e(href)}">確認記録を見る</a></td></tr>'
        )
    return "\n".join(rows)


def _status_css(status: str) -> str:
    return {"complete": "passed", "pending": "pending", "blocked": "blocked"}[status]


def _render_html(
    *,
    template: str,
    job: Mapping[str, Any],
    sources: Mapping[str, Any],
    response: Mapping[str, Any],
    status: str,
) -> str:
    recovery = response["recovery"]
    unknowns = "\n".join(f"・{item}" for item in recovery["unknowns"]) or "なし"
    replacements = {
        "{{incident_id}}": _e(job["incident_id"]),
        "{{target_date}}": _e(job["target_date"]),
        "{{report_title}}": _e(response["title"]),
        "{{report_summary}}": _e(response["summary"]),
        "{{created_at_jst}}": _e(response["created_at_jst"]),
        "{{result_version}}": _e(response["result_version"]),
        "{{source_launch_id}}": _e(job["source_launch_id"]),
        "{{source_run_id_or_null}}": _e(job["source_run_id"] or "未取得（null）"),
        "{{status_class}}": _status_css(status),
        "{{status_label}}": _STATUS_LABELS[status],
        "{{issue_rows}}": _render_issue_rows(response["issues"]),
        "{{issue_details}}": _render_issue_details(response["issues"]),
        "{{check_rows}}": _render_check_rows(response["checks"], sources),
        "{{flow_tree}}": _e(response["flow_tree"]),
        "{{unknowns}}": _e(unknowns),
        "{{stop_reason}}": _e(recovery["stop_reason"] or "なし"),
        "{{resume_condition}}": _e(recovery["resume_condition"]),
        "{{side_effects}}": _e(recovery["side_effects"]),
        "{{investigation_version}}": _e(sources["investigation"]["result_version"]),
        "{{investigation_href}}": _e(sources["investigation"]["href"]),
        "{{plan_version}}": _e(sources["plan"]["result_version"]),
        "{{plan_href}}": _e(sources["plan"]["href"]),
        "{{base_commit}}": _e(sources["implementation"]["base_commit"]),
        "{{result_commit_or_null}}": _e(sources["implementation"]["result_commit"]),
        "{{evidence_index_href}}": _e(sources["evidence_index_href"]),
        "{{evidence_index_label}}": _e(sources["evidence_index_label"]),
        "{{reporter_id}}": "報告担当",
        "{{reviewed_version}}": _e(sources["implementation"]["result_commit"]),
        "{{review_status}}": "承認済み（プロダクトコード）",
    }
    template_tokens = set(re.findall(r"\{\{[^{}]+\}\}", template))
    if set(replacements).difference(template_tokens):
        raise ReportContractError("report_template_invalid")
    rendered = template
    for token, replacement in replacements.items():
        rendered = rendered.replace(token, replacement)
    if re.search(r"\{\{[^{}]+\}\}", rendered):
        raise ReportContractError("report_template_placeholder_unresolved")
    if "<script" in rendered.lower() or any(
        pattern.search(rendered) for pattern in _SENSITIVE_PATTERNS
    ):
        raise ReportContractError("report_sensitive_output_detected")
    return rendered


def prepare_incident_report(
    job_context: Mapping[str, Any],
    source_snapshot: object,
    response: object,
    *,
    template_path: str | Path,
) -> ReportStageArtifacts:
    """Render one report from available, identity-bound sources.

    The returned artifacts are ready for a separate report review. This helper
    does not persist to the repository, access production data, or publish.
    """

    try:
        job = _validate_context(job_context)
    except InvestigationContractError as exc:
        raise ReportContractError(exc.code) from exc
    sources = _validate_sources(job, source_snapshot)
    report = _validate_response(response, job)
    try:
        template_file = Path(template_path).resolve(strict=True)
        _assert_no_reparse_components(template_file)
        if not template_file.is_file():
            raise ReportContractError("report_template_unavailable")
        template = template_file.read_text(encoding="utf-8")
    except ReportContractError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReportContractError("report_template_unavailable") from exc
    status = _report_status(sources)
    rendered = _render_html(
        template=template,
        job=job,
        sources=sources,
        response=report,
        status=status,
    )
    report_path = PurePosixPath(
        job["job_id"], job["attempt_id"], OUTPUT_FILENAME
    ).as_posix()
    result_path = PurePosixPath(
        job["job_id"], job["attempt_id"], "result.json"
    ).as_posix()
    if not {report_path, result_path}.issubset(set(job.get("allowed_outputs", []))):
        raise ReportContractError("report_output_not_allowed")
    html_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    result_document = {
        "schema_version": REPORT_RESULT_SCHEMA_VERSION,
        "identity": sources["identity"],
        "attempt_id": job["attempt_id"],
        "result_version": report["result_version"],
        "created_at_jst": report["created_at_jst"],
        "report_status": status,
        "stage_status": "ready_for_review",
        "review": None,
        "output": {"path": report_path, "sha256": html_hash},
        "source_versions": {
            "investigation": sources["investigation"]["result_version"],
            "plan": sources["plan"]["result_version"],
            "implementation": sources["implementation"]["result_commit"],
            "tests": sources["tests"]["result_version"],
            "deployment_status": sources["deployment"]["status"],
            "next_day_status": sources["next_day"]["status"],
        },
        "next_action": "report_review",
    }
    result_json = (
        json.dumps(
            result_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    try:
        result_document = json.loads(result_json)
        _schema_validator("maintenance-report-result.schema.json").validate(
            result_document
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ReportContractError("report_result_schema_invalid") from exc
    result_hash = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    return ReportStageArtifacts(
        report_status=status,
        html_path=report_path,
        html_sha256=html_hash,
        html=rendered,
        result_path=result_path,
        result_sha256=result_hash,
        result_json=result_json,
    )


def validate_report_review(
    review: object,
    *,
    author_id: str,
    result_version: str,
    output_sha256: str,
) -> dict[str, Any]:
    """Accept a review only for this exact report author, version, and HTML."""

    if not isinstance(review, Mapping):
        raise ReportContractError("report_review_invalid")
    data = dict(review)
    try:
        _schema_validator("maintenance-report-review.schema.json").validate(data)
    except ValidationError as exc:
        raise ReportContractError("report_review_schema_invalid") from exc
    if data["reviewer_id"] == _id(author_id, "report_author_id_invalid"):
        raise ReportContractError("report_review_not_independent")
    if data["reviewed_version"] != _id(result_version, "report_result_version_invalid"):
        raise ReportContractError("report_review_version_mismatch")
    if (
        data["reviewed_sha256"] != output_sha256
        or _SHA256_PATTERN.fullmatch(output_sha256) is None
    ):
        raise ReportContractError("report_review_output_mismatch")
    _validate_jst(data["reviewed_at"])
    return data


def finalize_report_review(
    artifacts: ReportStageArtifacts,
    review: object,
    *,
    author_id: str,
) -> ReportStageArtifacts:
    """Bind one independent review to the prepared HTML without editing it."""

    validated = validate_report_review(
        review,
        author_id=author_id,
        result_version=json.loads(artifacts.result_json)["result_version"],
        output_sha256=artifacts.html_sha256,
    )
    result = json.loads(artifacts.result_json)
    if (
        result.get("stage_status") != "ready_for_review"
        or result.get("review") is not None
    ):
        raise ReportContractError("report_result_not_reviewable")
    result["review"] = validated
    result["stage_status"] = "reviewed"
    result["next_action"] = "publish"
    result_json = (
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    try:
        _schema_validator("maintenance-report-result.schema.json").validate(
            json.loads(result_json)
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ReportContractError("report_result_schema_invalid") from exc
    return ReportStageArtifacts(
        report_status=artifacts.report_status,
        html_path=artifacts.html_path,
        html_sha256=artifacts.html_sha256,
        html=artifacts.html,
        result_path=artifacts.result_path,
        result_sha256=hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
        result_json=result_json,
    )


def save_report_artifacts(
    output_root: str | Path,
    attempts_root: str | Path,
    job_context: Mapping[str, Any],
    artifacts: ReportStageArtifacts,
) -> dict[str, tuple[str, str]]:
    """Persist only the two report outputs beneath the owned attempt root."""

    try:
        report = save_attempt_artifact(
            output_root,
            attempts_root,
            job_context,
            filename=OUTPUT_FILENAME,
            content=artifacts.html,
            stage="report",
        )
        result = save_attempt_artifact(
            output_root,
            attempts_root,
            job_context,
            filename="result.json",
            content=artifacts.result_json,
            stage="report",
        )
    except InvestigationContractError as exc:
        raise ReportContractError(exc.code) from exc
    if report != (artifacts.html_path, artifacts.html_sha256) or result != (
        artifacts.result_path,
        artifacts.result_sha256,
    ):
        raise ReportContractError("report_saved_artifact_mismatch")
    return {OUTPUT_FILENAME: report, "result.json": result}


def _git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise ReportContractError("report_git_unavailable") from exc
    if completed.returncode != 0:
        raise ReportContractError("report_git_operation_failed")
    return completed.stdout.strip()


def _git_with_index(repo: Path, index_path: Path, *args: str) -> str:
    environment = dict(os.environ)
    environment["GIT_INDEX_FILE"] = str(index_path)
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
    except OSError as exc:
        raise ReportContractError("report_git_unavailable") from exc
    if completed.returncode != 0:
        raise ReportContractError("report_git_operation_failed")
    return completed.stdout.strip()


def _git_paths_with_index(repo: Path, index_path: Path, *args: str) -> list[str]:
    environment = dict(os.environ)
    environment["GIT_INDEX_FILE"] = str(index_path)
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
            check=False,
            capture_output=True,
            env=environment,
        )
    except OSError as exc:
        raise ReportContractError("report_git_unavailable") from exc
    if completed.returncode != 0:
        raise ReportContractError("report_git_operation_failed")
    return [
        item.decode("utf-8", errors="strict")
        for item in completed.stdout.split(b"\x00")
        if item
    ]


def _git_paths(repo: Path, *args: str) -> list[str]:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "core.quotepath=false",
                *args,
            ],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ReportContractError("report_git_unavailable") from exc
    if completed.returncode != 0:
        raise ReportContractError("report_git_operation_failed")
    try:
        return [
            value for value in completed.stdout.decode("utf-8").split("\x00") if value
        ]
    except UnicodeDecodeError as exc:
        raise ReportContractError("report_git_path_encoding_invalid") from exc


def _git_content_sha256(repo: Path, object_path: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "blob", object_path],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ReportContractError("report_git_unavailable") from exc
    if completed.returncode != 0:
        raise ReportContractError("report_git_blob_unavailable")
    return hashlib.sha256(completed.stdout).hexdigest()


def _resolve_commit(repo: Path, value: str) -> str:
    if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
        raise ReportContractError("report_commit_invalid")
    resolved = _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    if resolved.lower() != value.lower():
        raise ReportContractError("report_commit_invalid")
    return resolved


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def _remote_report_head(repo: Path, remote_name: str, target_branch: str) -> str | None:
    """Fetch and return one stable observation of the remote target branch."""

    rows = _git(
        repo, "ls-remote", "--heads", remote_name, f"refs/heads/{target_branch}"
    ).split()
    if not rows:
        return None
    if len(rows) != 2 or rows[1] != f"refs/heads/{target_branch}":
        raise ReportContractError("report_remote_observation_invalid")
    observed = _resolve_commit(repo, rows[0])
    tracking_ref = f"refs/remotes/{remote_name}/{target_branch}"
    _git(
        repo,
        "fetch",
        "--no-tags",
        remote_name,
        f"+refs/heads/{target_branch}:{tracking_ref}",
    )
    fetched = _resolve_commit(repo, _git(repo, "rev-parse", "--verify", tracking_ref))
    if fetched != observed:
        raise ReportContractError("report_remote_changed_during_publish")
    return observed


def _report_publication_commit(
    repo: Path,
    *,
    commit: str,
    report_path: str,
    expected_target_commit: str,
    source_code_commit: str,
    reviewed_sha256: str,
) -> tuple[str, str]:
    """Validate that a candidate commit contains only the reviewed report."""

    resolved = _resolve_commit(repo, commit)
    parents = _git(repo, "rev-list", "--parents", "-n", "1", resolved).split()
    if len(parents) != 2:
        raise ReportContractError("report_commit_scope_invalid")
    parent = _resolve_commit(repo, parents[1])
    expected = _resolve_commit(repo, expected_target_commit)
    code_commit = _resolve_commit(repo, source_code_commit)
    if not _is_ancestor(repo, expected, parent) or not _is_ancestor(
        repo, code_commit, parent
    ):
        raise ReportContractError("report_base_or_code_version_not_current")
    if _git_paths(
        repo, "diff", "--name-only", "-z", expected, parent, "--", report_path
    ):
        raise ReportContractError("report_path_conflict")
    changed = _git_paths(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-z",
        "-r",
        resolved,
    )
    if changed != [report_path]:
        raise ReportContractError("report_commit_scope_invalid")
    if _git_content_sha256(repo, f"{resolved}:{report_path}") != reviewed_sha256:
        raise ReportContractError("report_review_output_mismatch")
    return resolved, parent


def _find_report_publication(
    repo: Path,
    *,
    head: str,
    report_path: str,
    commit_message: str,
    expected_target_commit: str,
    source_code_commit: str,
    reviewed_sha256: str,
) -> tuple[str, str] | None:
    matches = _git(
        repo,
        "log",
        "--format=%H",
        "--fixed-strings",
        f"--grep={commit_message}",
        head,
        "--",
        report_path,
    ).splitlines()
    exact = [
        value
        for value in matches
        if _git(repo, "show", "-s", "--format=%s", value) == commit_message
    ]
    if len(exact) > 1:
        raise ReportContractError("report_publication_commit_ambiguous")
    if not exact:
        return None
    return _report_publication_commit(
        repo,
        commit=exact[0],
        report_path=report_path,
        expected_target_commit=expected_target_commit,
        source_code_commit=source_code_commit,
        reviewed_sha256=reviewed_sha256,
    )


def publish_reviewed_report(
    repository: str | Path,
    *,
    report_path: str,
    expected_target_commit: str,
    source_code_commit: str,
    result_version: str,
    review_document: object,
    author_id: str,
    remote_name: str,
    target_branch: str,
    commit_message: str,
) -> ReportPublication:
    """Push one reviewed report without creating a checkout or changing the index.

    A temporary Git index builds a commit containing only the report. A retry
    inspects remote history and recovers after a successful push whose local
    publication sidecar was not yet saved.
    """

    try:
        input_root = Path(repository).expanduser()
        _assert_no_reparse_components(input_root)
        repo = input_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError, InvestigationContractError) as exc:
        raise ReportContractError("report_repository_invalid") from exc
    try:
        git_root = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReportContractError("report_repository_invalid") from exc
    if not repo.is_dir() or git_root != repo:
        raise ReportContractError("report_repository_invalid")
    relative = PurePosixPath(report_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in report_path
        or not report_path.startswith("reports/incidents/")
    ):
        raise ReportContractError("report_path_invalid")
    if (
        not isinstance(commit_message, str)
        or not commit_message.strip()
        or len(commit_message) > 200
        or any(ord(char) < 32 for char in commit_message)
    ):
        raise ReportContractError("report_commit_message_invalid")
    try:
        reviewed_sha256 = (
            review_document.get("reviewed_sha256")
            if isinstance(review_document, Mapping)
            else None
        )
        if (
            not isinstance(reviewed_sha256, str)
            or _SHA256_PATTERN.fullmatch(reviewed_sha256) is None
        ):
            raise ReportContractError("report_review_output_mismatch")
        reviewed = validate_report_review(
            review_document,
            author_id=author_id,
            result_version=result_version,
            output_sha256=reviewed_sha256,
        )
    except (ReportContractError, TypeError, AttributeError) as exc:
        raise ReportContractError("report_review_invalid") from exc
    current_branch = _git(repo, "branch", "--show-current")
    if (
        not _BRANCH_PATTERN.fullmatch(target_branch)
        or current_branch not in {"", target_branch}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", target_branch)
    ):
        raise ReportContractError("report_target_branch_mismatch")
    try:
        subprocess.run(
            ["git", "-C", str(repo), "check-ref-format", "--branch", target_branch],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReportContractError("report_target_branch_mismatch") from exc
    if (
        not isinstance(remote_name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", remote_name) is None
    ):
        raise ReportContractError("report_remote_invalid")
    head = _resolve_commit(repo, _git(repo, "rev-parse", "HEAD"))
    expected = _resolve_commit(repo, expected_target_commit)
    code_commit = _resolve_commit(repo, source_code_commit)
    path = repo.joinpath(*relative.parts)
    try:
        _assert_no_reparse_components(path)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(repo)
        if not resolved_path.is_file():
            raise ReportContractError("report_file_unavailable")
        content = resolved_path.read_bytes()
    except ReportContractError:
        raise
    except (OSError, RuntimeError, ValueError, InvestigationContractError) as exc:
        raise ReportContractError("report_file_unavailable") from exc
    if hashlib.sha256(content).hexdigest() != reviewed["reviewed_sha256"]:
        raise ReportContractError("report_review_output_mismatch")
    remote_head = _remote_report_head(repo, remote_name, target_branch)
    if remote_head is not None:
        published = _find_report_publication(
            repo,
            head=remote_head,
            report_path=report_path,
            commit_message=commit_message,
            expected_target_commit=expected,
            source_code_commit=code_commit,
            reviewed_sha256=reviewed["reviewed_sha256"],
        )
        if published is not None:
            commit, base = published
            if (
                not _is_ancestor(repo, commit, remote_head)
                or _git_content_sha256(repo, f"{remote_head}:{report_path}")
                != reviewed["reviewed_sha256"]
            ):
                raise ReportContractError("report_remote_content_conflict")
            return ReportPublication(
                commit=commit,
                base_commit=base,
                code_commit=code_commit,
                remote_name=remote_name,
                target_branch=target_branch,
            )

    publication_parent = remote_head or head
    if not _is_ancestor(repo, expected, publication_parent) or not _is_ancestor(
        repo, code_commit, publication_parent
    ):
        raise ReportContractError("report_base_or_code_version_not_current")
    if _git_paths(
        repo, "diff", "--name-only", "-z", expected, publication_parent, "--", report_path
    ):
        raise ReportContractError("report_path_conflict")
    existing_report = _git_paths(
        repo, "ls-tree", "-r", "--name-only", "-z", publication_parent, "--", report_path
    )
    if existing_report:
        raise ReportContractError("report_path_conflict")
    spec = f":(literal){report_path}"
    temp_index: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="inago-report-index-", delete=False) as stream:
            temp_index = Path(stream.name)
        _git_with_index(repo, temp_index, "read-tree", publication_parent)
        _git_with_index(repo, temp_index, "add", "--", spec)
        staged = _git_paths_with_index(
            repo, temp_index, "diff", "--cached", "--name-only", "-z"
        )
        if staged != [report_path]:
            raise ReportContractError("report_staged_scope_invalid")
        if hashlib.sha256(content).hexdigest() != reviewed["reviewed_sha256"]:
            raise ReportContractError("report_review_output_mismatch")
        tree = _git_with_index(repo, temp_index, "write-tree")
        commit = _git(
            repo, "commit-tree", tree, "-p", publication_parent, "-m", commit_message
        )
        _report_publication_commit(
            repo,
            commit=commit,
            report_path=report_path,
            expected_target_commit=expected,
            source_code_commit=code_commit,
            reviewed_sha256=reviewed["reviewed_sha256"],
        )
        _git(repo, "push", remote_name, f"{commit}:refs/heads/{target_branch}")
        verified_head = _remote_report_head(repo, remote_name, target_branch)
        if (
            verified_head is None
            or not _is_ancestor(repo, commit, verified_head)
            or _git_content_sha256(repo, f"{verified_head}:{report_path}")
            != reviewed["reviewed_sha256"]
        ):
            raise ReportContractError("report_push_unverified")
    except ReportContractError:
        raise
    except Exception as exc:
        raise ReportContractError("report_publish_failed") from exc
    finally:
        if temp_index is not None:
            try:
                temp_index.unlink(missing_ok=True)
                temp_index.with_suffix(temp_index.suffix + ".lock").unlink(missing_ok=True)
            except OSError:
                pass
    return ReportPublication(
        commit=commit,
        base_commit=publication_parent,
        code_commit=code_commit,
        remote_name=remote_name,
        target_branch=target_branch,
    )
