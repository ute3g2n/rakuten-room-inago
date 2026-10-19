"""Build, validate, and publish evidence-bound incident reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    "observed": "当日の記録にあるエラー",
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
    "pending": "確認を続けます",
}
_SENSITIVE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.I),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|cookie)\s*[:=]\s*\S+",
        re.I,
    ),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)
_PUBLIC_INTERNAL_PATTERNS = (
    re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", re.I),
    re.compile(r"\b(?:job|launch|run|incident|attempt)-[A-Za-z0-9][A-Za-z0-9._:-]{7,}\b", re.I),
    re.compile(r"\b[A-F0-9]{40,64}\b", re.I),
    re.compile(r"\b(?:status|reason(?:_code)?|sha(?:256)?|commit)\s*[:=]", re.I),
    re.compile(
        r"\b(?:approved|blocked|changes_requested|complete|deployed|error|failed|"
        r"not_created|not_observed|not_run|not_started|not_attempted|pending|"
        r"passed|recurred|rejected|report_source_artifact_unavailable|rolled_back|"
        r"skipped|unknown|unavailable|verified)\b",
        re.I,
    ),
)
_MARKDOWN_EVIDENCE_LINK = re.compile(r"\[([^\]\r\n]{1,160})\]\(([^()\s]{1,512})\)")
_REFERENCE_SEPARATOR_ONLY = re.compile(r"^[\s,，、。;；:：|・]+$")
_REFERENCE_NOTE_WORDS = re.compile(r"(?:記録|資料|証拠|確認|取得|参照|source|evidence|記載)", re.I)
_SOURCE_SNAPSHOT_TERM = re.compile(
    r"(?<![A-Za-z0-9_])source\s+snapshot(?![A-Za-z0-9_])", re.I
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


def _normalize_reviewed_at_jst(value: object) -> str:
    """Normalize a timezone-aware reviewer timestamp without rejecting UTC."""

    if not isinstance(value, str):
        raise ReportContractError("report_reviewed_at_invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReportContractError("report_reviewed_at_invalid") from exc
    if parsed.utcoffset() is None:
        raise ReportContractError("report_reviewed_at_timezone_missing")
    return parsed.astimezone(timezone(timedelta(hours=9))).isoformat()


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
    if (
        sources["tests"]["status"] != "passed"
        or sources["deployment"]["status"] != "deployed"
        or sources["next_day"]["status"] != "verified"
    ):
        return "pending"
    return "complete"


def _evidence_display_label(reference: str) -> str:
    name = PurePosixPath(reference).name.casefold()
    if name in {"manifest.json", "source-evidence.json", "source.json"}:
        return "元の証拠一覧"
    if name == "result.json":
        return "工程結果の記録（JSON）"
    if name == "observation.json":
        return "対象日の観測記録"
    if name == "production-cli.filtered.json":
        return "本番CLIの記録"
    if name == "scheduler.filtered.json":
        return "タスク実行記録"
    if name == "read-aggregate.json":
        return "日次結果の集計記録"
    if name == "e0-manifest.json":
        return "入力資料一覧"
    if name in {"report-input.json", "report-input-fallback.json"} or name.startswith(
        "report-input-fallback-"
    ):
        return "報告の作成に使った入力"
    if name.startswith("report-fallback-") and name.endswith(".html"):
        return "レビュー対象の報告案"
    if name.startswith("review-response-fallback-") and name.endswith(".json"):
        return "レビュー指摘の記録"
    if "investigation" in name or name == "調査.md":
        return "調査結果"
    if "plan" in name or name == "計画.md":
        return "実行計画"
    if "test" in name or name.endswith(".xml"):
        return "試験記録"
    if "deploy" in name or "publication" in name:
        return "反映記録"
    if "index" in name:
        return "確認できた資料一覧"
    if name.endswith(".md"):
        return "工程の記録"
    return "関連する確認記録"


def _source_reference_label(kind: str, reference: str) -> str:
    suffix = PurePosixPath(reference).suffix.casefold()
    formats = {".json": "JSON", ".md": "文章", ".html": "HTML", ".xml": "XML"}
    detail = f"（{formats[suffix]}）" if suffix in formats else ""
    labels = {
        "investigation": "調査結果",
        "plan": "修正計画",
        "tests": "試験結果の記録",
        "deployment": "反映状況の記録",
        "next_day": "翌日の確認記録",
    }
    return labels[kind] + detail


def _source_document_available(
    source: Mapping[str, Any], evidence_index_href: str
) -> bool:
    version = source.get("result_version")
    reference = source.get("href", source.get("evidence_href"))
    return (
        isinstance(version, str)
        and version.casefold() not in {"not_created", "not_available", "unavailable"}
        and reference != evidence_index_href
    )


def _report_evidence_links(
    value: object,
    *,
    allowed_refs: set[str],
    fallback_ref: str,
) -> list[dict[str, str]]:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ReportContractError("report_evidence_ref_not_allowed")
    if value in allowed_refs:
        return [{"href": value, "label": _evidence_display_label(value)}]

    try:
        direct_reference = _safe_href(value)
    except ReportContractError:
        if not _MARKDOWN_EVIDENCE_LINK.search(value):
            raise
    else:
        if direct_reference in allowed_refs:
            return [
                {
                    "href": direct_reference,
                    "label": _evidence_display_label(direct_reference),
                }
            ]

    matches = list(_MARKDOWN_EVIDENCE_LINK.finditer(value))
    if matches:
        remainder = _MARKDOWN_EVIDENCE_LINK.sub("", value)
        if remainder and not _REFERENCE_SEPARATOR_ONLY.fullmatch(remainder):
            raise ReportContractError("report_evidence_ref_not_allowed")
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for match in matches:
            target = match.group(2)
            try:
                target = _safe_href(target)
            except ReportContractError as exc:
                raise ReportContractError("report_evidence_ref_not_allowed") from exc
            if target not in allowed_refs:
                raise ReportContractError("report_evidence_ref_not_allowed")
            if target not in seen:
                seen.add(target)
                links.append(
                    {"href": target, "label": _evidence_display_label(target)}
                )
        if links:
            return links

    # Some authors describe why there is no direct source link in this field.
    # Bind that limitation to the supplied evidence index instead of publishing
    # the free-form string as though it were a valid path.
    if (
        _REFERENCE_NOTE_WORDS.search(value)
        and fallback_ref in allowed_refs
        and not any(marker in value for marker in ("/", "\\", "[", "]", "(", ")"))
    ):
        return [{"href": fallback_ref, "label": _evidence_display_label(fallback_ref)}]
    raise ReportContractError("report_evidence_ref_not_allowed")


def _validate_internal_evidence_refs(
    value: Sequence[str] | None, *, job: Mapping[str, Any]
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReportContractError("report_internal_evidence_ref_invalid")
    refs: list[str] = []
    for reference in value:
        try:
            safe = _safe_href(reference)
        except ReportContractError as exc:
            raise ReportContractError("report_internal_evidence_ref_invalid") from exc
        parts = PurePosixPath(safe).parts
        if (
            len(parts) != 3
            or parts[0] != job["job_id"]
            or parts[1] != job["attempt_id"]
            or safe in refs
            or "%" in safe
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", parts[2])
            is None
        ):
            raise ReportContractError("report_internal_evidence_ref_invalid")
        refs.append(safe)
    return refs


def _validate_response(
    response: object,
    job: Mapping[str, Any],
    *,
    additional_allowed_refs: Sequence[str] = (),
    fallback_evidence_ref: str,
) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        raise ReportContractError("report_response_invalid")
    data = deepcopy(dict(response))
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
    refs = set(job["evidence_refs"]) | set(job["input_refs"]) | set(
        additional_allowed_refs
    )
    for item in data["issues"]:
        item["_evidence_links"] = _report_evidence_links(
            item["evidence_ref"],
            allowed_refs=refs,
            fallback_ref=fallback_evidence_ref,
        )
        item["evidence_ref"] = item["_evidence_links"][0]["href"]
    for item in data["checks"]:
        item["_evidence_links"] = _report_evidence_links(
            item["evidence_ref"],
            allowed_refs=refs,
            fallback_ref=fallback_evidence_ref,
        )
        item["evidence_ref"] = item["_evidence_links"][0]["href"]
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


def _public_text(value: object, fallback: str = "詳しい工程情報は参考資料に記録しています。") -> str:
    text = _SOURCE_SNAPSHOT_TERM.sub("確認に使った記録", str(value))
    if any(pattern.search(text) for pattern in _PUBLIC_INTERNAL_PATTERNS):
        return fallback
    return text


def _report_evidence_index(
    response: Mapping[str, Any], sources: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    references: dict[str, dict[str, str]] = {}

    def add(reference: object, preferred_label: str | None = None) -> None:
        if not isinstance(reference, str) or not reference:
            return
        if reference not in references:
            references[reference] = {
                "anchor_id": f"report-evidence-{len(references) + 1}",
                "label": preferred_label or _evidence_display_label(reference),
            }
        elif preferred_label:
            references[reference]["label"] = preferred_label

    index_href = sources["evidence_index_href"]
    for key in ("issues", "checks"):
        items = response.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            links = item.get("_evidence_links", [])
            if not isinstance(links, list):
                continue
            for link in links:
                if isinstance(link, dict):
                    add(link.get("href"))

    for name in ("investigation", "plan"):
        source = sources[name]
        if _source_document_available(source, index_href):
            add(source["href"], _source_reference_label(name, source["href"]))
    tests = sources["tests"]
    if tests["evidence_href"] != index_href:
        add(
            tests["evidence_href"],
            _source_reference_label("tests", tests["evidence_href"]),
        )
    deployment = sources["deployment"]
    if deployment["evidence_href"] != index_href:
        add(
            deployment["evidence_href"],
            _source_reference_label("deployment", deployment["evidence_href"]),
        )
    next_day = sources["next_day"]
    if next_day["evidence_href"] and next_day["evidence_href"] != index_href:
        add(
            next_day["evidence_href"],
            _source_reference_label("next_day", next_day["evidence_href"]),
        )

    add(
        index_href,
        _public_text(sources["evidence_index_label"], "当日の証拠索引"),
    )

    for key in ("issues", "checks"):
        items = response.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            links = item.get("_evidence_links", [])
            if not isinstance(links, list):
                continue
            item["_evidence_links"] = [
                {
                    **link,
                    **references[link["href"]],
                }
                for link in links
                if isinstance(link, dict)
                and isinstance(link.get("href"), str)
                and link["href"] in references
            ]
    return references


def _render_evidence_links(item: Mapping[str, Any]) -> str:
    links = item.get("_evidence_links")
    if not isinstance(links, list):
        return ""
    return "<span class=\"evidence-links\">" + " · ".join(
        f'<a href="#{_e(link["anchor_id"])}">{_e(link["label"])}を見る</a>'
        for link in links
        if isinstance(link, dict)
        and isinstance(link.get("anchor_id"), str)
        and isinstance(link.get("label"), str)
    ) + "</span>"


def _render_evidence_reference_rows(
    references: Mapping[str, Mapping[str, str]]
) -> str:
    return "\n".join(
        "<li id=\"{anchor}\"><strong>{label}</strong>。元の資料本文はこの公開ページに含めていません。</li>".format(
            anchor=_e(details["anchor_id"]), label=_e(details["label"])
        )
        for details in references.values()
    )


def _render_evidence_reference_details(
    references: Mapping[str, Mapping[str, str]]
) -> str:
    return "\n".join(
        "<li><strong>{label}</strong>: <code>{reference}</code></li>".format(
            label=_e(details["label"]), reference=_e(reference)
        )
        for reference, details in references.items()
    )


def _render_issue_rows(issues: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for index, item in enumerate(issues, start=1):
        rows.append(
            "<tr>"
            f'<td><a href="#issue-{index}">{_e(_public_text(item["phase"], "関連する工程"))}</a></td>'
            f'<td>{_e(_EVENT_LABELS[item["event_class"]])}</td>'
            f'<td>{_e(_public_text(item["expected"], "期待した内容は参考資料に記録しています。"))}</td>'
            f'<td>{_e(_public_text(item["actual"], "実際の結果は参考資料に記録しています。"))}<br><strong>影響:</strong> {_e(_public_text(item["impact"], "影響は確認できていません。"))}</td>'
            f'<td>{_e(_public_text(item["recovery"], "回復したかは確認できていません。"))}</td>'
            f"<td>{_render_evidence_links(item)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _render_issue_details(issues: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for index, item in enumerate(issues, start=1):
        issue_class = {
            "observed": "actual",
            "investigation": "investigation",
            "test": "test",
        }[item["event_class"]]
        rows.append(
            f'<article class="issue {issue_class}" id="issue-{index}">'
            f"<h3>{_e(_public_text(item['title'], '処理結果を確認できませんでした'))}</h3>"
            f'<p><span class="status pending">{_e(_EVENT_LABELS[item["event_class"]])}</span></p>'
            "<h4>操作・期待・実結果</h4>"
            f"<p>{_e(_public_text(item['operation'], '操作内容は参考資料に記録しています。'))}<br><strong>期待:</strong> {_e(_public_text(item['expected'], '期待した内容は参考資料に記録しています。'))}<br><strong>実結果:</strong> {_e(_public_text(item['actual'], '実際の結果は参考資料に記録しています。'))}</p>"
            "<h4>影響と回復</h4>"
            f"<p>{_e(_public_text(item['impact'], '影響は確認できていません。'))}<br><strong>回復:</strong> {_e(_public_text(item['recovery'], '回復したかは確認できていません。'))}</p>"
            "<h4>原因と分かっている範囲</h4>"
            f"<p><strong>{_e(_CONFIDENCE_LABELS[item['cause_confidence']])}:</strong> {_e(_public_text(item['cause_statement'], '原因は確認できていません。'))}</p>"
            "<h4>変更後の動作</h4>"
            f"<p>{_e(_public_text(item['changed_behavior'], '変更後の動作は参考資料に記録しています。'))}</p>"
            "<h4>確認範囲と根拠</h4>"
            f'<p>{_e(_public_text(item["confirmation_scope"], "確認範囲は参考資料に記録しています。"))} · {_render_evidence_links(item)}</p>'
            "</article>"
        )
    return "\n".join(rows)


def _render_check_rows(
    checks: Sequence[Mapping[str, Any]],
    sources: Mapping[str, Any],
    evidence_references: Mapping[str, Mapping[str, str]],
) -> str:
    status_names = {
        "passed": ("passed", "確認済み"),
        "failed": ("failed", "失敗"),
        "blocked": ("pending", "要確認"),
        "not_run": ("pending", "未実施"),
        "skipped": ("pending", "未実施"),
        "error": ("failed", "確認できず"),
        "deployed": ("passed", "反映済み"),
        "pending": ("pending", "保留"),
        "rolled_back": ("pending", "復旧済み・未反映"),
        "not_attempted": ("pending", "未実施"),
        "verified": ("passed", "確認済み"),
        "not_observed": ("pending", "未観測"),
        "recurred": ("failed", "再発"),
        "approved": ("passed", "承認済み"),
        "changes_requested": ("failed", "修正依頼あり"),
        "rejected": ("failed", "承認されませんでした"),
        "unavailable": ("pending", "実施できず"),
        "not_started": ("pending", "未開始"),
    }
    rows = []
    for item in checks:
        css, label = status_names[item["status"]]
        rows.append(
            "<tr>"
            f"<td>{_e(_public_text(item['stage'], '関連する工程'))}</td><td>版は参考付録を参照</td>"
            f"<td>{_e(_public_text(item['expected'], '期待した内容は参考資料に記録しています。'))}</td>"
            f"<td>{_e(_public_text(item['actual'], '実際の結果は参考資料に記録しています。'))}</td>"
            f'<td><span class="status {css}">{label}</span></td>'
            f"<td>{_render_evidence_links(item)}</td>"
            "</tr>"
        )
    tests_status = sources["tests"]["status"]
    tests_actual = {
        "passed": "保存された結果では試験に成功しています。",
        "failed": "試験で問題が見つかりました。詳しい結果は参考資料にあります。",
        "blocked": "試験結果は未確認です。確認を続けます。",
        "not_run": "試験は実施していません。試験結果の資料はありません。",
        "skipped": "試験は省略されています。試験結果の資料はありません。",
        "error": "試験結果を確認できませんでした。詳しい経緯は参考資料にあります。",
    }[tests_status]
    deployment_status = sources["deployment"]["status"]
    deployment_actual = {
        "deployed": "本番環境への反映と起動を確認しました。",
        "pending": "本番環境へ反映したか、まだ確認できていません。",
        "blocked": "本番環境への反映結果は未確認です。確認を続けます。",
        "failed": "本番環境への反映に失敗した記録があります。",
        "rolled_back": "反映後に元へ戻した記録があります。",
        "not_attempted": "本番反映は実施していません。",
    }[deployment_status]
    next_day_status = sources["next_day"]["status"]
    next_day_actual = {
        "verified": "翌日の確認を行いました。",
        "pending": "翌日の確認はまだ行っていません。",
        "not_observed": "翌日の確認結果はまだ観測できていません。",
        "recurred": "翌日の確認で同じ問題が見つかりました。",
        "blocked": "翌日の確認結果は未確認です。確認を続けます。",
    }[next_day_status]
    index_href = sources["evidence_index_href"]
    investigation = sources["investigation"]
    plan = sources["plan"]
    implementation = sources["implementation"]
    source_rows = (
        (
            "調査レビュー",
            "調査結果を別の担当者が確認する",
            {
                "approved": "調査結果は別の担当者に承認されました。",
                "changes_requested": "調査結果に修正依頼がありました。",
                "rejected": "調査結果は承認されませんでした。",
                "unavailable": "調査結果のレビューは実施できませんでした。",
                "not_started": "調査結果のレビューはまだ始まっていません。",
            }[investigation["review_status"]],
            investigation["review_status"],
            investigation["href"]
            if _source_document_available(investigation, index_href)
            else index_href,
        ),
        (
            "計画レビュー",
            "修正計画を別の担当者が確認する",
            {
                "approved": "修正計画は別の担当者に承認されました。",
                "changes_requested": "修正計画に修正依頼がありました。",
                "rejected": "修正計画は承認されませんでした。",
                "unavailable": "修正計画のレビューは実施できませんでした。",
                "not_started": "修正計画のレビューはまだ始まっていません。",
            }[plan["review_status"]],
            plan["review_status"],
            plan["href"] if _source_document_available(plan, index_href) else index_href,
        ),
        (
            "プロダクトコードレビュー",
            "変更したコードを別の担当者が確認する",
            {
                "approved": "変更したコードは別の担当者に承認されました。",
                "changes_requested": "変更したコードに修正依頼がありました。",
                "rejected": "変更したコードは承認されませんでした。",
                "unavailable": "コードレビューは実施できませんでした。",
                "not_started": "コードレビューはまだ始まっていません。",
            }[implementation["review_status"]],
            implementation["review_status"],
            index_href,
        ),
        (
            "修正後テスト",
            "修正後の動作を試験で確かめる",
            tests_actual,
            tests_status,
            sources["tests"]["evidence_href"],
        ),
        (
            "本番反映",
            "確認した変更を本番環境に反映する",
            deployment_actual,
            deployment_status,
            sources["deployment"]["evidence_href"],
        ),
        (
            "次回の日次確認",
            "翌日も同じ問題が起きるか確かめる",
            next_day_actual,
            next_day_status,
            sources["next_day"]["evidence_href"] or sources["evidence_index_href"],
        ),
    )
    for stage, expected, actual, status, href in source_rows:
        css, label = status_names[status]
        reference = evidence_references[href]
        rows.append(
            "<tr>"
            f"<td>{_e(stage)}</td><td>版は参考付録を参照</td><td>{_e(expected)}</td>"
            f'<td>{_e(actual)}</td><td><span class="status {css}">{label}</span></td>'
            f'<td><a href="#{_e(reference["anchor_id"])}">'
            f'{_e(reference["label"])}を見る</a></td></tr>'
        )
    return "\n".join(rows)


def _render_check_versions(checks: Sequence[Mapping[str, Any]]) -> str:
    if not checks:
        return "チェック記録がありません。"
    return "\n".join(
        f"・{_e(item['stage'])}: {_e(item['version'])}" for item in checks
    )


def _status_css(status: str) -> str:
    return {"complete": "passed", "pending": "pending"}[status]


def _render_source_reference(
    kind: str,
    source: Mapping[str, Any],
    evidence_index_href: str,
    evidence_references: Mapping[str, Mapping[str, str]],
) -> str:
    result_version = source.get("result_version")
    if not _source_document_available(source, evidence_index_href):
        if result_version == "not_created":
            return f"<li>{_e(kind)}の資料は作成されていません。</li>"
        return f"<li>{_e(kind)}の資料を確認できませんでした。</li>"
    href = source["href"]
    reference = evidence_references[href]
    return (
        f'<li>{_e(kind)}: <a href="#{_e(reference["anchor_id"])}">'
        f'{_e(reference["label"])}</a></li>'
    )


def _review_status_label(status: str) -> str:
    return {
        "approved": "承認済み",
        "changes_requested": "修正依頼あり",
        "rejected": "承認されませんでした",
        "unavailable": "レビューを実施できませんでした",
        "not_started": "未開始",
    }[status]


def _render_html(
    *,
    template: str,
    job: Mapping[str, Any],
    sources: Mapping[str, Any],
    response: Mapping[str, Any],
    status: str,
) -> str:
    recovery = response["recovery"]
    evidence_references = _report_evidence_index(response, sources)
    unknowns = "\n".join(
        f"・{_public_text(item, '詳しい未確認事項は参考資料に記録しています。')}"
        for item in recovery["unknowns"]
    ) or "未確認事項の記録がありません。"
    replacements = {
        "{{incident_id}}": _e(job["incident_id"]),
        "{{target_date}}": _e(job["target_date"]),
        "{{report_title}}": _e(_public_text(response["title"], "日次処理の確認結果")),
        "{{report_summary}}": _e(
            _public_text(response["summary"], "詳しい工程の状況は参考資料に記録しています。")
        ),
        "{{created_at_jst}}": _e(response["created_at_jst"]),
        "{{result_version}}": _e(response["result_version"]),
        "{{source_launch_id}}": _e(job["source_launch_id"]),
        "{{source_run_id_or_null}}": _e(job["source_run_id"] or "未取得（null）"),
        "{{status_class}}": _status_css(status),
        "{{status_label}}": _STATUS_LABELS[status],
        "{{issue_rows}}": _render_issue_rows(response["issues"]),
        "{{issue_details}}": _render_issue_details(response["issues"]),
        "{{check_rows}}": _render_check_rows(
            response["checks"], sources, evidence_references
        ),
        "{{check_versions}}": _render_check_versions(response["checks"]),
        "{{flow_tree}}": _e(response["flow_tree"]),
        "{{unknowns}}": _e(unknowns),
        "{{stop_reason}}": _e(
            _public_text(
                recovery["stop_reason"] or "記録がありません（未確認）",
                "停止理由は確認できていません。",
            )
        ),
        "{{resume_condition}}": _e(
            _public_text(recovery["resume_condition"], "次に必要な確認は参考資料に記録しています。")
        ),
        "{{side_effects}}": _e(
            _public_text(recovery["side_effects"], "実行していない操作は参考資料に記録しています。")
        ),
        "{{investigation_version}}": _e(sources["investigation"]["result_version"]),
        "{{investigation_reference}}": _render_source_reference(
            "調査結果",
            sources["investigation"],
            sources["evidence_index_href"],
            evidence_references,
        ),
        "{{plan_version}}": _e(sources["plan"]["result_version"]),
        "{{plan_reference}}": _render_source_reference(
            "修正計画",
            sources["plan"],
            sources["evidence_index_href"],
            evidence_references,
        ),
        "{{base_commit}}": _e(sources["implementation"]["base_commit"]),
        "{{result_commit_or_null}}": _e(sources["implementation"]["result_commit"]),
        "{{tests_version}}": _e(sources["tests"]["result_version"]),
        "{{deployment_status}}": _e(sources["deployment"]["status"]),
        "{{deployment_tested_commit}}": _e(
            sources["deployment"]["tested_commit"]
            if sources["deployment"]["tested_commit"] is not None
            else "null"
        ),
        "{{deployment_pushed_commit}}": _e(
            sources["deployment"]["pushed_commit"]
            if sources["deployment"]["pushed_commit"] is not None
            else "null"
        ),
        "{{deployment_active_commit}}": _e(
            sources["deployment"]["active_commit"]
            if sources["deployment"]["active_commit"] is not None
            else "null"
        ),
        "{{next_day_status}}": _e(sources["next_day"]["status"]),
        "{{next_day_observed_at}}": _e(
            sources["next_day"]["observed_at"]
            if sources["next_day"]["observed_at"] is not None
            else "null"
        ),
        "{{evidence_index_href}}": _e(
            "#" + evidence_references[sources["evidence_index_href"]]["anchor_id"]
        ),
        "{{evidence_index_label}}": _e(
            _public_text(sources["evidence_index_label"], "当日の証拠索引")
        ),
        "{{evidence_reference_rows}}": _render_evidence_reference_rows(
            evidence_references
        ),
        "{{evidence_reference_details}}": _render_evidence_reference_details(
            evidence_references
        ),
        "{{reporter_id}}": "報告担当",
        "{{reviewed_version}}": _e(sources["implementation"]["result_commit"]),
        "{{review_status}}": _e(
            _review_status_label(sources["implementation"]["review_status"])
            + "（プロダクトコード）"
        ),
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
    internal_evidence_refs: Sequence[str] | None = None,
) -> ReportStageArtifacts:
    """Render one report from available, identity-bound sources.

    The returned artifacts are ready for a separate report review. This helper
    does not persist to the repository, access production data, or publish.
    """

    try:
        job = _validate_context(job_context)
    except InvestigationContractError as exc:
        raise ReportContractError(exc.code) from exc
    validated_internal_refs = _validate_internal_evidence_refs(
        internal_evidence_refs, job=job
    )
    sources = _validate_sources(job, source_snapshot)
    report = _validate_response(
        response,
        job,
        additional_allowed_refs=validated_internal_refs,
        fallback_evidence_ref=sources["evidence_index_href"],
    )
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
    data["reviewed_at"] = _normalize_reviewed_at_jst(data["reviewed_at"])
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


def _report_blob_sha256(repo: Path, commit: str, report_path: str) -> str | None:
    """Return the exact target blob digest at a commit, if the path exists."""

    existing = _git_paths(
        repo,
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        commit,
        "--",
        f":(literal){report_path}",
    )
    if not existing:
        return None
    if existing != [report_path]:
        raise ReportContractError("report_path_conflict")
    return _git_content_sha256(repo, f"{commit}:{report_path}")


def _git_blob_from_bytes(repo: Path, content: bytes) -> str:
    """Write bytes to the object database without touching the index or worktree."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            check=False,
            capture_output=True,
            input=content,
        )
    except OSError as exc:
        raise ReportContractError("report_git_unavailable") from exc
    if completed.returncode != 0:
        raise ReportContractError("report_git_operation_failed")
    value = completed.stdout.decode("ascii", errors="strict").strip()
    if re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", value) is None:
        raise ReportContractError("report_git_blob_unavailable")
    return value


def _report_publication_commit(
    repo: Path,
    *,
    commit: str,
    report_path: str,
    expected_target_commit: str,
    expected_existing_sha256: str | None,
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
    existing_sha256 = _report_blob_sha256(repo, parent, report_path)
    if expected_existing_sha256 is None:
        if existing_sha256 is not None:
            raise ReportContractError("report_path_conflict")
    elif existing_sha256 != expected_existing_sha256:
        raise ReportContractError("report_existing_content_mismatch")
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
    expected_existing_sha256: str | None,
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
        expected_existing_sha256=expected_existing_sha256,
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
    report_bytes: bytes | None = None,
    expected_existing_sha256: str | None = None,
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
    if expected_existing_sha256 is not None and (
        not isinstance(expected_existing_sha256, str)
        or _SHA256_PATTERN.fullmatch(expected_existing_sha256) is None
    ):
        raise ReportContractError("report_existing_content_mismatch")
    if report_bytes is not None and (
        not isinstance(report_bytes, bytes) or len(report_bytes) > 8 * 1024 * 1024
    ):
        raise ReportContractError("report_file_unavailable")
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
    if report_bytes is not None:
        content = report_bytes
    else:
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
            expected_existing_sha256=expected_existing_sha256,
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
    existing_sha256 = _report_blob_sha256(repo, publication_parent, report_path)
    if expected_existing_sha256 is None:
        if existing_sha256 is not None:
            raise ReportContractError("report_path_conflict")
    elif existing_sha256 != expected_existing_sha256:
        raise ReportContractError("report_existing_content_mismatch")
    temp_index: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="inago-report-index-", delete=False) as stream:
            temp_index = Path(stream.name)
        _git_with_index(repo, temp_index, "read-tree", publication_parent)
        blob = _git_blob_from_bytes(repo, content)
        _git_with_index(
            repo, temp_index, "update-index", "--add", "--cacheinfo", "100644", blob, report_path
        )
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
            expected_existing_sha256=expected_existing_sha256,
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
