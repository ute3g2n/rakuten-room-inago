"""Run real Codex sessions against a disposable, synthetic maintenance job."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from automation.inago.maintenance.ai_adapter import (
    PARENT_ROLES,
    PARENT_SETTINGS,
    MaintenanceAIAdapter,
    MaintenanceAIBlockedError,
)
from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.maintenance.paths import _has_reparse_point
from automation.inago.maintenance.settings import ensure_settings_file
from automation.inago.maintenance.store import (
    LeaseToken,
    MaintenanceStore,
    MaintenanceStoreError,
)


_PROBE_SUBDIRS = (
    "common-execution-planning",
    "common-review",
    "common-implementation",
    "common-testing",
    "rr-inago-incident-investigation",
    "rr-inago-incident-report",
)
_PROFILE_ROLES = {
    "investigation": ("A", PARENT_ROLES["A"]),
    "repair": ("B", PARENT_ROLES["B"]),
}
_RESPONSE_SCHEMA_VERSION = "maintenance-ai-response-v1"
_PROBE_TEST_COMMAND_ARGS = (
    sys.executable,
    "-B",
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    "--basetemp",
    ".probe-basetemp/PROBE-C01",
    "test_probe.py::test_normal_classification",
)
_PROBE_TEST_PYTHON = "'" + sys.executable.replace("'", "''") + "'"
_PROBE_TEST_COMMAND = f"& {_PROBE_TEST_PYTHON} " + " ".join(
    _PROBE_TEST_COMMAND_ARGS[1:]
)
_PROBE_REVIEW_ROLES = frozenset(
    {
        "rr-inago-incident-investigation-reviewer",
        "common-execution-plan-reviewer",
        "common-reviewer",
        "rr-inago-incident-report-reviewer",
    }
)
_PROBE_ROLE_STAGES = {
    "rr-inago-incident-investigator": "investigation",
    "rr-inago-incident-investigation-reviewer": "investigation_review",
    "common-execution-planner": "planning",
    "common-execution-plan-reviewer": "planning_review",
    "common-implementer": "implementation",
    "common-tester": "testing",
    "common-reviewer": "implementation_review",
    "rr-inago-incident-reporter": "reporting",
    "rr-inago-incident-report-reviewer": "report_review",
}
_PROBE_ROLE_OUTPUTS = {
    "rr-inago-incident-investigator": (
        "facts.json",
        "investigation.md",
        "result.json",
    ),
    "rr-inago-incident-investigation-reviewer": ("review.json", "review.md"),
    "common-execution-planner": (
        "plan.md",
        "requirement-step-case-map.json",
        "result.json",
    ),
    "common-execution-plan-reviewer": ("review.json", "review.md"),
    "common-implementer": ("src/repair_target.py",),
    "common-tester": ("test-result.json",),
    "common-reviewer": ("review.json", "review.md"),
    "rr-inago-incident-reporter": ("report.html", "result.json"),
    "rr-inago-incident-report-reviewer": ("review.json", "review.md"),
}
_PROBE_PLAN_SECTIONS = list(range(15))
_PROBE_TEST_CASE_FIELDS = [
    "requirement_and_step",
    "purpose",
    "method",
    "preconditions",
    "inputs_and_operations",
    "execution_command",
    "expected_output",
    "expected_state_and_evidence",
    "prohibited_side_effects",
    "acceptance",
    "saved_evidence",
    "cleanup_and_rerun",
]


class MaintenanceProbeError(RuntimeError):
    """A sanitized failure while preparing or running an isolated probe."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _assert_no_reparse_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if _has_reparse_point(current):
            raise MaintenanceProbeError("probe_path_reparse_point")


def validate_ai_probe_target(data_root: str | Path, *, profile: str) -> Path:
    if profile not in _PROFILE_ROLES:
        raise MaintenanceProbeError("probe_profile_invalid")
    root = Path(os.path.abspath(Path(data_root).expanduser()))
    repository_root = _repository_root()
    expected_parent = (
        repository_root
        / "非公開作業データ"
        / "イナゴ実装試験"
        / "maintenance-20261017"
        / "U18"
        / ("LC01" if profile == "investigation" else "LC02")
    )
    try:
        relative = root.relative_to(expected_parent)
    except ValueError as exc:
        raise MaintenanceProbeError("probe_data_root_out_of_scope") from exc
    if (
        len(relative.parts) != 2
        or not relative.parts[0].startswith("attempt-")
        or relative.parts[1] != "runtime"
    ):
        raise MaintenanceProbeError("probe_data_root_out_of_scope")
    _assert_no_reparse_components(expected_parent)
    _assert_no_reparse_components(root)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise MaintenanceProbeError("probe_data_root_not_empty")
    return root


def _write_atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    encoded = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _git(path: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=path,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MaintenanceProbeError("probe_toy_git_failed") from exc
    return completed.stdout.strip()


def _copy_probe_skills(repository_root: Path, target_root: Path) -> None:
    source_root = repository_root / ".agents" / "skills"
    target_skills = target_root / ".agents" / "skills"
    target_skills.mkdir(parents=True, exist_ok=True)
    for name in _PROBE_SUBDIRS:
        source = source_root / name
        target = target_skills / name
        if not source.is_dir() or target.exists():
            raise MaintenanceProbeError("probe_skill_snapshot_unavailable")
        shutil.copytree(source, target)


def _create_toy_repository(data_root: Path) -> tuple[Path, str]:
    project_root = data_root / "toy-project"
    remote_root = data_root / "toy-remote.git"
    worktree_root = data_root / "worktrees"
    project_root.mkdir(parents=True)
    worktree_root.mkdir()
    source = project_root
    (source / "src").mkdir()
    (source / ".agents").mkdir()
    (source / ".gitignore").write_text(
        ".agents/skills/\n"
        "__pycache__/\n"
        "*.py[cod]\n"
        ".pytest_cache/\n"
        ".probe-basetemp/\n",
        encoding="utf-8",
        newline="\n",
    )
    (source / "README.md").write_text(
        "Synthetic maintenance AI probe. Contains no production inputs.\n",
        encoding="utf-8",
        newline="\n",
    )
    (source / "src" / "repair_target.py").write_text(
        "def classify(value: str) -> str:\n" '    return "hold"\n',
        encoding="utf-8",
        newline="\n",
    )
    (source / "test_probe.py").write_text(
        "from src.repair_target import classify\n\n"
        "def test_normal_classification():\n"
        "    assert classify('normal') == 'retry'\n",
        encoding="utf-8",
        newline="\n",
    )
    (source / "probe-requirements.md").write_text(
        "# Synthetic probe requirement\n\n"
        "- Requirement: PROBE-R01. `classify('normal')` returns `retry`.\n"
        "- Allowed product change: `src/repair_target.py` only.\n"
        f"- Exact case: `{_PROBE_TEST_COMMAND}`.\n"
        "- Production data, databases, settings, Scheduler, and posts are out of scope.\n",
        encoding="utf-8",
        newline="\n",
    )
    (source / "probe-case.json").write_text(
        json.dumps(
            {
                "case_id": "PROBE-C01",
                "node": "test_probe.py::test_normal_classification",
                "command": _PROBE_TEST_COMMAND,
                "expected": "1 passed, exit code 0",
                "attempt_policy": "run once; retry only if the case fails",
                "ledger_status": "NOT_RUN",
                "initial_data": {
                    "fixture": "fresh synthetic repository",
                    "observed": "classify('normal') returns hold",
                },
                "clock_jst": "2026-10-18T00:00:00+09:00",
                "basetemp": ".probe-basetemp/PROBE-C01",
                "basetemp_initial_state": "absent",
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (source / "test-ledger.json").write_text(
        json.dumps(
            {
                "case_id": "PROBE-C01",
                "node": "test_probe.py::test_normal_classification",
                "status": "NOT_RUN",
                "prior_attempts": [],
                "initial_data": {
                    "fixture": "fresh synthetic repository",
                    "observed": "classify('normal') returns hold",
                },
                "clock_jst": "2026-10-18T00:00:00+09:00",
                "basetemp": ".probe-basetemp/PROBE-C01",
                "basetemp_initial_state": "absent",
                "command": _PROBE_TEST_COMMAND,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    template_root = source / "docs" / "templates" / "incidents"
    template_root.mkdir(parents=True)
    source_templates = _repository_root() / "docs" / "templates" / "incidents"
    for name in (
        "日次不具合調査レポート_ひな形.md",
        "日次不具合修正計画_ひな形.md",
        "日次不具合対応レポート_ひな形.html",
    ):
        source_template = source_templates / name
        if not source_template.is_file():
            raise MaintenanceProbeError("probe_template_unavailable")
        shutil.copyfile(source_template, template_root / name)
    _git(source, "init", "--initial-branch=main")
    _git(source, "config", "user.name", "Inago Probe")
    _git(source, "config", "user.email", "inago-probe@example.invalid")
    _git(
        source,
        "add",
        ".gitignore",
        "README.md",
        "docs/templates/incidents/日次不具合調査レポート_ひな形.md",
        "docs/templates/incidents/日次不具合修正計画_ひな形.md",
        "docs/templates/incidents/日次不具合対応レポート_ひな形.html",
        "probe-case.json",
        "probe-requirements.md",
        "test-ledger.json",
        "src/repair_target.py",
        "test_probe.py",
    )
    _git(source, "commit", "-m", "create synthetic maintenance probe")
    commit = _git(source, "rev-parse", "HEAD")

    try:
        subprocess.run(
            ["git", "init", "--bare", str(remote_root)],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MaintenanceProbeError("probe_toy_git_failed") from exc
    _git(source, "remote", "add", "origin", str(remote_root))
    _git(source, "push", "--set-upstream", "origin", "main")
    return project_root, commit


def _create_toy_worktree(
    *,
    repository_root: Path,
    project_root: Path,
    worktrees_root: Path,
    job_id: str,
) -> Path:
    worktree = worktrees_root / job_id
    try:
        subprocess.run(
            ["git", "clone", "--no-hardlinks", str(project_root), str(worktree)],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MaintenanceProbeError("probe_toy_git_failed") from exc
    _copy_probe_skills(repository_root, project_root)
    _copy_probe_skills(repository_root, worktree)
    return worktree


def _seed_probe_job(
    *,
    data_root: Path,
    store: MaintenanceStore,
    controller: MaintenanceController,
    commit: str,
    job_id: str,
) -> tuple[dict[str, object], str, str, LeaseToken]:
    now = datetime.now(timezone.utc)
    target_date = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    probe_id = uuid4().hex
    incident_id = f"probe-incident-{probe_id}"
    launch_id = f"probe-launch-{probe_id}"
    release_id = f"probe-release-{probe_id}"
    daily = controller.record_daily_check(
        source_launch_id=launch_id,
        source_run_id=None,
        target_date=target_date,
        check_status="abnormal",
        observed_at=now,
        reason_code="probe_synthetic_incident",
        incident={
            "dedupe_key": f"probe:{probe_id}",
            "component": "probe",
            "event_code": "probe_synthetic",
        },
    )
    if daily.incident_id is None:
        raise MaintenanceProbeError("probe_incident_creation_failed")
    incident_id = daily.incident_id

    result_version = f"probe-{probe_id}"
    config_version = "isolated-probe-v1"
    store.create_release(
        release_id=release_id,
        author_id="probe-author",
        base_commit=commit,
        result_commit=commit,
        result_version=result_version,
        config_version=config_version,
        required_case_ids=("probe-smoke",),
        created_at=now,
    )
    store.record_test_result(
        release_id=release_id,
        case_id="probe-smoke",
        attempt=1,
        result_version=result_version,
        status="PASS",
        recorded_at=now,
        evidence_ref="probe/probe-smoke.json",
    )
    store.record_release_review(
        release_id=release_id,
        reviewer_id="probe-independent-reviewer",
        reviewed_version=result_version,
        status="approved",
        reviewed_at=now,
    )
    deployment_id = store.begin_deployment(
        release_id=release_id,
        source_commit=commit,
        base_commit=commit,
        tested_commit=commit,
        remote_name="origin",
        target_branch="main",
        started_at=now,
    )
    remote_commit = _git(data_root / "toy-project", "rev-parse", "origin/main")
    if remote_commit != commit:
        raise MaintenanceProbeError("probe_toy_push_unverified")
    store.finish_deployment(
        deployment_id=deployment_id,
        status="deployed",
        pushed_commit=remote_commit,
        active_commit=commit,
        reason_code=None,
        finished_at=now,
        capability=store._deployment_capability,
    )
    lease = store.acquire_lease(
        lease_key=f"probe-lease-{probe_id}",
        owner_id=f"probe-owner-{probe_id}",
        owner_pid=os.getpid(),
        owner_start_time=now.isoformat(),
        now=now,
        ttl_seconds=300,
    )
    job_started = False
    try:
        job = controller.start_fix_job(
            incident_id=incident_id,
            release_id=release_id,
            job_id=job_id,
            started_at=now,
        )
        if not job.started:
            raise MaintenanceProbeError("probe_job_start_blocked")
        job_started = True
        attempt = controller.begin_step_attempt(
            job_id=job_id,
            step_key="maintenance_investigation",
            sequence=1,
            lease=lease,
            started_at=now,
        )
        identity: dict[str, object] = {
            "incident_id": incident_id,
            "source_launch_id": launch_id,
            "source_run_id": None,
            "target_date": target_date,
            "job_id": job_id,
        }
        controller.bind_job_input_manifest(
            **identity,
            created_at=now,
        )
        (data_root / "attempts" / job_id / attempt.attempt_id).mkdir(parents=True)
        return identity, job_id, attempt.attempt_id, lease
    except Exception as exc:
        if job_started:
            try:
                controller.finalize_job(
                    job_id=job_id,
                    final_status="cancelled",
                    lease=lease,
                    finalized_at=datetime.now(timezone.utc),
                    reason_code="probe_setup_failed",
                )
            except (MaintenanceStoreError, TypeError) as cleanup_error:
                exc.add_note(f"probe job cleanup failed: {cleanup_error!r}")
        try:
            current_lease = store.get_lease(lease.lease_key)
            if current_lease is not None and current_lease.state == "held":
                store.release_lease(lease=lease, released_at=datetime.now(timezone.utc))
        except MaintenanceStoreError as cleanup_error:
            exc.add_note(f"probe lease cleanup failed: {cleanup_error!r}")
        raise


def _role_result_version(role_name: str, attempt_id: str) -> str:
    if role_name not in _PROBE_ROLE_STAGES:
        raise MaintenanceProbeError("probe_role_invalid")
    return f"{_PROBE_ROLE_STAGES[role_name]}:{attempt_id}:v1"


def _server_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Build a provider schema while retaining exact host-side validation."""

    def scalar_schema(value: Any) -> dict[str, Any]:
        if value is None:
            return {"type": ["string", "null"], "enum": [None]}
        if isinstance(value, bool):
            value_type = "boolean"
        elif isinstance(value, int):
            value_type = "integer"
        elif isinstance(value, float):
            value_type = "number"
        elif isinstance(value, str):
            value_type = "string"
        else:
            raise MaintenanceProbeError("probe_output_schema_invalid")
        return {"type": value_type, "enum": [value]}

    def array_item_schema(values: list[Any]) -> dict[str, Any]:
        if not values:
            return {"type": "string"}
        if all(
            value is None or isinstance(value, (str, bool, int, float))
            for value in values
        ):
            value_types = {
                (
                    "null"
                    if value is None
                    else (
                        "boolean"
                        if isinstance(value, bool)
                        else (
                            "integer"
                            if isinstance(value, int)
                            else "number"
                            if isinstance(value, float)
                            else "string"
                        )
                    )
                )
                for value in values
            }
            if len(value_types) != 1:
                raise MaintenanceProbeError("probe_output_schema_invalid")
            value_type = next(iter(value_types))
            return {
                "type": ["string", "null"] if value_type == "null" else value_type,
                "enum": values,
            }
        item_schema = values[0]
        if isinstance(item_schema, Mapping):
            return schema_for_value(item_schema)
        raise MaintenanceProbeError("probe_output_schema_invalid")

    def schema_for_value(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return {
                "type": "object",
                "properties": {
                    str(key): schema_for_value(item) for key, item in value.items()
                },
                "required": [str(key) for key in value],
                "additionalProperties": False,
            }
        if isinstance(value, list):
            return {
                "type": "array",
                "items": array_item_schema(value),
                "minItems": len(value),
                "maxItems": len(value),
            }
        return scalar_schema(value)

    def reduce(value: Mapping[str, Any]) -> dict[str, Any]:
        constant = value.get("const")
        schema_type = value.get("type")
        if schema_type == "object" or isinstance(constant, Mapping):
            properties = value.get("properties")
            if not isinstance(properties, Mapping):
                if not isinstance(constant, Mapping):
                    raise MaintenanceProbeError("probe_output_schema_invalid")
                properties = {
                    str(key): schema_for_value(item) for key, item in constant.items()
                }
            reduced_properties = {
                str(key): reduce(item)
                for key, item in properties.items()
                if isinstance(item, Mapping)
            }
            if len(reduced_properties) != len(properties):
                raise MaintenanceProbeError("probe_output_schema_invalid")
            return {
                "type": "object",
                "properties": reduced_properties,
                "required": list(value.get("required", reduced_properties)),
                "additionalProperties": False,
            }
        if schema_type == "array" or isinstance(constant, list):
            item_schema = value.get("items")
            if isinstance(item_schema, Mapping):
                reduced_item = reduce(item_schema)
            elif isinstance(constant, list):
                reduced_item = array_item_schema(constant)
            else:
                reduced_item = {"type": "string"}
            result = {"type": "array", "items": reduced_item}
            if isinstance(constant, list):
                result["minItems"] = len(constant)
                result["maxItems"] = len(constant)
            else:
                for key in ("minItems", "maxItems"):
                    if key in value:
                        result[key] = value[key]
            return result
        if "const" in value:
            return scalar_schema(constant)
        if isinstance(schema_type, list):
            result = {"type": schema_type}
            if "enum" in value:
                result["enum"] = value["enum"]
            return result
        if schema_type in {"string", "integer", "number", "boolean"}:
            result = {"type": schema_type}
            if "enum" in value:
                result["enum"] = value["enum"]
            return result
        if schema_type == "null":
            return {"type": ["string", "null"]}
        raise MaintenanceProbeError("probe_output_schema_invalid")

    return reduce(schema)


def _response_schema(
    identity: Mapping[str, object],
    role_name: str,
    *,
    attempt_id: str,
    role_names: tuple[str, ...],
    workflow_binding: Mapping[str, object],
    result_version: str,
) -> dict[str, object]:
    string = {"type": "string", "minLength": 1}
    long_string = {"type": "string", "minLength": 100}
    nonempty_strings = {
        "type": "array",
        "minItems": 1,
        "items": string,
    }
    exact_target = {"const": ["src/repair_target.py"], "type": "array"}
    investigation_ref = _role_result_version(
        "rr-inago-incident-investigator", attempt_id
    )
    plan_ref = _role_result_version("common-execution-planner", attempt_id)
    implementation_ref = _role_result_version("common-implementer", attempt_id)
    test_ref = _role_result_version("common-tester", attempt_id)
    reviewer_ref = _role_result_version("common-reviewer", attempt_id)
    reporter_ref = _role_result_version("rr-inago-incident-reporter", attempt_id)
    fact_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "fact_id",
            "event_class",
            "stage",
            "expected",
            "actual",
            "impact",
            "recovery",
            "cause_confidence",
            "evidence_refs",
        ],
        "properties": {
            "fact_id": {"const": "probe-F001", "type": "string"},
            "event_class": {"const": "synthetic_defect", "type": "string"},
            "stage": {"const": "investigation", "type": "string"},
            "expected": {"const": "retry", "type": "string"},
            "actual": {"const": "hold", "type": "string"},
            "impact": string,
            "recovery": string,
            "cause_confidence": {
                "const": "source_verified_synthetic",
                "type": "string",
            },
            "evidence_refs": {"const": workflow_binding["evidence_refs"]},
        },
    }
    case_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": _PROBE_TEST_CASE_FIELDS,
        "properties": {field: string for field in _PROBE_TEST_CASE_FIELDS},
    }
    step_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["number", "items", "full_prompt"],
        "properties": {
            "number": {"const": 1, "type": "integer"},
            "items": {
                "type": "array",
                "minItems": 14,
                "maxItems": 14,
                "items": string,
            },
            "full_prompt": {"type": "string", "minLength": 50},
        },
    }
    details_by_role: dict[str, dict[str, object]] = {
        "rr-inago-incident-investigator": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "finding",
                "observed",
                "expected",
                "evidence_files",
                "facts",
                "investigation_markdown",
                "artifact_refs",
            ],
            "properties": {
                "finding": {
                    "const": "normal input returns hold instead of retry",
                    "type": "string",
                },
                "observed": {"const": "hold", "type": "string"},
                "expected": {"const": "retry", "type": "string"},
                "evidence_files": {
                    "const": ["src/repair_target.py", "probe-requirements.md"],
                    "type": "array",
                },
                "facts": {"type": "array", "minItems": 1, "items": fact_schema},
                "investigation_markdown": long_string,
                "artifact_refs": {
                    "const": list(_PROBE_ROLE_OUTPUTS[role_name]),
                    "type": "array",
                },
            },
        },
        "rr-inago-incident-investigation-reviewer": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "decision",
                "reviewed_version",
                "verified_fact_ids",
                "verified_chapters",
                "review_markdown",
            ],
            "properties": {
                "decision": {"enum": ["approved", "rejected"], "type": "string"},
                "reviewed_version": {"const": investigation_ref, "type": "string"},
                "verified_fact_ids": {
                    "const": ["probe-F001"],
                    "type": "array",
                },
                "verified_chapters": {
                    "const": list(range(1, 10)),
                    "type": "array",
                },
                "review_markdown": long_string,
            },
        },
        "common-execution-planner": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "plan_markdown",
                "section_numbers",
                "step",
                "case",
                "mapping",
                "allowed_files",
                "test_command",
            ],
            "properties": {
                "plan_markdown": {"type": "string", "minLength": 500},
                "section_numbers": {
                    "const": _PROBE_PLAN_SECTIONS,
                    "type": "array",
                },
                "step": step_schema,
                "case": case_schema,
                "mapping": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "requirement_id",
                            "fact_ids",
                            "step_numbers",
                            "case_ids",
                        ],
                        "properties": {
                            "requirement_id": {
                                "const": "PROBE-R01",
                                "type": "string",
                            },
                            "fact_ids": {"const": ["probe-F001"], "type": "array"},
                            "step_numbers": {"const": [1], "type": "array"},
                            "case_ids": {"const": ["PROBE-C01"], "type": "array"},
                        },
                    },
                },
                "allowed_files": exact_target,
                "test_command": {
                    "const": _PROBE_TEST_COMMAND,
                    "type": "string",
                },
            },
        },
        "common-execution-plan-reviewer": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "decision",
                "reviewed_version",
                "verified_sections",
                "verified_step_item_count",
                "verified_case_fields",
                "review_markdown",
            ],
            "properties": {
                "decision": {"enum": ["approved", "rejected"], "type": "string"},
                "reviewed_version": {"const": plan_ref, "type": "string"},
                "verified_sections": {
                    "const": _PROBE_PLAN_SECTIONS,
                    "type": "array",
                },
                "verified_step_item_count": {"const": 14, "type": "integer"},
                "verified_case_fields": {
                    "const": _PROBE_TEST_CASE_FIELDS,
                    "type": "array",
                },
                "review_markdown": long_string,
            },
        },
        "common-implementer": {
            "type": "object",
            "additionalProperties": False,
            "required": ["outcome", "changed_files", "change_summary"],
            "properties": {
                "outcome": {"const": "implemented", "type": "string"},
                "changed_files": exact_target,
                "change_summary": string,
            },
        },
        "common-tester": {
            "type": "object",
            "additionalProperties": False,
            "required": ["command", "result", "exit_code", "observed_output"],
            "properties": {
                "command": {"const": _PROBE_TEST_COMMAND, "type": "string"},
                "result": {"const": "passed", "type": "string"},
                "exit_code": {"const": 0, "type": "integer"},
                "observed_output": string,
            },
        },
        "common-reviewer": {
            "type": "object",
            "additionalProperties": False,
            "required": ["decision", "reviewed_files", "findings"],
            "properties": {
                "decision": {"enum": ["approved", "rejected"], "type": "string"},
                "reviewed_files": exact_target,
                "findings": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
            },
        },
        "rr-inago-incident-reporter": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "report_status",
                "report_html",
                "changed_file",
                "test_status",
                "review_status",
                "deployment_status",
                "correlated_roles",
            ],
            "properties": {
                "report_status": {"const": "complete", "type": "string"},
                "report_html": {"type": "string", "minLength": 300},
                "changed_file": {
                    "const": "src/repair_target.py",
                    "type": "string",
                },
                "test_status": {"const": "passed", "type": "string"},
                "review_status": {"const": "approved", "type": "string"},
                "deployment_status": {
                    "const": "not_performed_isolated_probe",
                    "type": "string",
                },
                "correlated_roles": {
                    "const": list(role_names[:3]),
                    "type": "array",
                },
            },
        },
        "rr-inago-incident-report-reviewer": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "decision",
                "reviewed_version",
                "verified_job_id",
                "verified_attempt_id",
                "verified_roles",
                "verified_evidence",
                "review_markdown",
            ],
            "properties": {
                "decision": {"enum": ["approved", "rejected"], "type": "string"},
                "reviewed_version": {"const": reporter_ref, "type": "string"},
                "verified_job_id": {"const": identity["job_id"], "type": "string"},
                "verified_attempt_id": {"const": attempt_id, "type": "string"},
                "verified_roles": {"const": list(role_names), "type": "array"},
                "verified_evidence": nonempty_strings,
                "review_markdown": long_string,
            },
        },
    }
    stage = _PROBE_ROLE_STAGES.get(role_name)
    details_schema = details_by_role.get(role_name)
    if stage is None or details_schema is None:
        raise MaintenanceProbeError("probe_role_invalid")
    properties = {
        "incident_id": {"const": identity["incident_id"], "type": "string"},
        "source_launch_id": {
            "const": identity["source_launch_id"],
            "type": "string",
        },
        "source_run_id": {"const": None, "type": "null"},
        "target_date": {"const": identity["target_date"], "type": "string"},
        "job_id": {"const": identity["job_id"], "type": "string"},
        "probe_status": {"const": "ok", "type": "string"},
        "role_name": {"const": role_name, "type": "string"},
        "workflow_stage": {"const": stage, "type": "string"},
        "workflow_binding": {"const": dict(workflow_binding), "type": "object"},
        "result_version": {"const": result_version, "type": "string"},
        "summary": string,
        "details": details_schema,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _numbered_section_headings(markdown: str) -> list[int]:
    return [
        int(match.group(1))
        for match in re.finditer(r"(?m)^## (\d+)\.\s+\S.*$", markdown)
    ]


def _review_role_approved(role_name: str, details: Mapping[str, Any]) -> bool:
    if role_name not in _PROBE_REVIEW_ROLES:
        return True
    if details.get("decision") != "approved":
        return False
    findings = details.get("findings")
    return role_name != "common-reviewer" or findings == []


def _validate_role_workflow_response(
    *, role_name: str, response: Mapping[str, Any]
) -> None:
    summary = response.get("summary")
    details = response.get("details")
    if not isinstance(summary, str) or not isinstance(details, Mapping):
        raise MaintenanceProbeError("probe_role_artifact_invalid")
    lowered = summary.casefold()
    if any(
        phrase in lowered
        for phrase in (
            "blocked:",
            "review unavailable",
            "approval is unavailable",
            "no approval granted",
            "no approval was granted",
            "cannot proceed",
            "source verification did not proceed",
        )
    ):
        raise MaintenanceProbeError("probe_role_reported_blocked")
    if role_name == "rr-inago-incident-investigator":
        report = details.get("investigation_markdown")
        if not isinstance(report, str) or _numbered_section_headings(report) != list(
            range(1, 10)
        ):
            raise MaintenanceProbeError("probe_investigation_artifact_incomplete")
    elif role_name == "common-execution-planner":
        report = details.get("plan_markdown")
        if not isinstance(report, str) or _numbered_section_headings(report) != list(
            range(15)
        ):
            raise MaintenanceProbeError("probe_plan_artifact_incomplete")
    elif role_name == "rr-inago-incident-reporter":
        report = details.get("report_html")
        if not isinstance(report, str) or not all(
            marker in report.casefold()
            for marker in ("<html", "<body", "<h1", "</html>")
        ):
            raise MaintenanceProbeError("probe_report_artifact_incomplete")


def _probe_prompt(
    identity: Mapping[str, object],
    role_name: str,
    *,
    profile: str,
    attempt_id: str,
    workflow_binding: Mapping[str, object],
    prior_outputs: list[Mapping[str, object]],
    observed_diff: str | None = None,
) -> str:
    identity_json = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    binding_json = json.dumps(workflow_binding, ensure_ascii=False, sort_keys=True)
    prior_json = json.dumps(prior_outputs, ensure_ascii=False, sort_keys=True)
    workflow = (
        "This is a complete host-validated synthetic handoff. Its immutable job "
        "identity, attempt, stage, base commit, input_refs, evidence_refs, and "
        "allowed_outputs are supplied below; do not treat the probe as missing "
        "production incident data. Use only those supplied references. The accepted "
        "fact is that classify('normal') must return 'retry', while the baseline "
        "product file returns 'hold'. The defect is deliberately seeded in the toy "
        "repository. Read test expectations only as the case contract; no test-code "
        "review is requested. The host captures Git versions and validates the diff, "
        "so do not invoke Git commands."
    )
    if profile == "investigation":
        tasks = {
            "rr-inago-incident-investigator": (
                "Perform the investigation using the supplied synthetic facts, "
                "requirements, source files, and investigation template. Return one "
                "fact ledger entry plus a completed nine-chapter investigation "
                "Markdown with headings exactly `## 1.` through `## 9.` in order, "
                "result version, and the exact allowed artifact references. Include "
                "each numbered heading once. "
                "Distinguish expected/reported/observed values and cause confidence. "
                "Do not edit files."
            ),
            "rr-inago-incident-investigation-reviewer": (
                "Independently review the complete prior investigation artifact and "
                "its version against the supplied refs. Verify all nine chapters, "
                "fact/evidence binding, and the source-confirmed synthetic behavior. "
                "Review the product finding, not test-code quality. Return a "
                "version-bound review and verified chapter/fact IDs. Approve only "
                "when every check passes; otherwise reject and explain the issue. "
                "Do not edit files."
            ),
            "common-execution-planner": (
                "Create a complete plan from the approved prior investigation and "
                "the supplied requirements, case manifest, code version, and plan "
                "template. Format sections exactly `## 0.` through `## 14.` in "
                "order. Include sections 0 through 14, one STEP with all 14 "
                "fields and a standalone full prompt, one case with all 12 fields, "
                "and a fact/requirement/STEP/case map. Restrict the product change "
                "to src/repair_target.py and use this exact case command: "
                f"{_PROBE_TEST_COMMAND}. Do not edit files or run the command."
            ),
            "common-execution-plan-reviewer": (
                "Independently review the full prior plan, its exact version, the "
                "approved investigation, source refs, requirements, case manifest, "
                "and allowed outputs. Check 0-14 sections, all 14 STEP fields, the "
                "standalone prompt, all 12 case fields, and the complete mapping. "
                "Approve only if the product scope is src/repair_target.py and the "
                f"command is {_PROBE_TEST_COMMAND}. Return a version-bound review; "
                "otherwise reject and explain the issue. Do not edit files."
            ),
        }
    else:
        workflow = (
            "This is an isolated repair assignment with host-supplied approved "
            "synthetic investigation and plan versions. The prior outputs include "
            "their IDs, facts, evidence refs, and the exact allowed files. Contract: "
            "classify('normal') must return 'retry'. Only src/repair_target.py may "
            f"change. The approved case command is {_PROBE_TEST_COMMAND}. Test "
            "code belongs to the fixture and must not be read as review subject or "
            "edited. Production deployment is explicitly not performed by this probe."
        )
        tasks = {
            "common-implementer": (
                "Implement the approved versioned plan now. Fix only "
                "src/repair_target.py so classify('normal') returns 'retry'. Do not "
                "edit tests or run tests. The host will bind the exact source version "
                "and inspect the product diff. Return implementation version and "
                "changed-file summary."
            ),
            "common-tester": (
                "Execute only the exact approved case in the supplied case manifest: "
                f"{_PROBE_TEST_COMMAND}. The case and test-ledger record status "
                "NOT_RUN, fixed initial fixture/clock, and an absent basetemp "
                "`.probe-basetemp/PROBE-C01`; verify those inputs before running. "
                "For Git ownership checks, use `git -c safe.directory=<exact managed "
                "worktree root from this attempt>` on that command only. Never use "
                "global safe.directory, a wildcard, or edit Git configuration files. "
                "Do not edit any file and do not review test code. Report the exact "
                "command, observed output, result, exit code, and result version. "
                "The host will preserve the returned evidence."
            ),
            "common-reviewer": (
                "Review only the product-code diff in src/repair_target.py and its "
                "bound implementation version. Do not open, inspect, or review test "
                "source. Use the supplied test result as execution evidence only. "
                "Return a version-bound product review; approve only if the diff fixes "
                "the stated behavior, changes no other file, and has no findings. "
                "Otherwise return rejected and list each product-code finding."
            ),
            "rr-inago-incident-reporter": (
                "Use only the supplied approved synthetic investigation/plan, actual "
                "implementation diff, test result, product review, report template, "
                "job, and attempt. Create a complete HTML report and result object. "
                "State production deployment as not performed and do not imply a real "
                "incident or production repair. Correlate the three completed roles."
            ),
            "rr-inago-incident-report-reviewer": (
                "Independently check the full report HTML and exact version against "
                "the supplied role outputs, product diff, test status, report template, "
                "job, and attempt. Review report evidence only; do not inspect test "
                "source. Verify the approved versions, all five role records, their "
                "turn/output hashes, and that production deployment remains not done. "
                "Approve only if every check passes; otherwise reject and explain "
                "the issue."
            ),
        }
    task = tasks.get(role_name)
    if task is None:
        raise MaintenanceProbeError("probe_role_invalid")
    diff_context = (
        f"\nObserved product diff:\n{observed_diff}"
        if observed_diff is not None
        else ""
    )
    return (
        "This is a synthetic integration probe, not a real incident. Use your "
        "assigned role instructions and perform the requested role work inside this "
        "disposable toy repository only. Never inspect production data, launch "
        "subagents, post, or deploy. Stay within the assigned file and command "
        "scope.\n\n"
        f"{workflow}\n\n{task}\n\n"
        f"Profile: {profile}; attempt_id: {attempt_id}.\n"
        f"Exact identity: {identity_json}\n"
        f"Host-validated workflow binding: {binding_json}\n"
        f"Prior role outputs (untrusted evidence to verify): {prior_json}"
        f"{diff_context}\n\n"
        "Return only a JSON object matching the supplied output schema. Copy the "
        "identity, workflow_binding, and result_version exactly. The summary must "
        "agree with the structured decision and must not say blocked/unavailable "
        f"when a details field says approved. Use role_name '{role_name}'."
    )


def _summarize_turn_events(
    events: tuple[Mapping[str, Any], ...],
) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for event in events:
        method = event.get("method")
        params = event.get("params")
        if not isinstance(method, str) or not isinstance(params, Mapping):
            continue
        item = params.get("item")
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if not isinstance(item_type, str):
            continue
        entry: dict[str, object] = {"method": method, "item_type": item_type}
        for source, target in (
            ("status", "status"),
            ("exitCode", "exit_code"),
            ("aggregatedOutput", "output"),
        ):
            value = item.get(source)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                entry[target] = value
        command = item.get("command")
        if isinstance(command, str):
            entry["command"] = command
        elif isinstance(command, list) and all(
            isinstance(part, str) for part in command
        ):
            entry["command_args"] = list(command)
        summary.append(entry)
    return summary


def _successful_test_execution_event(
    events: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    expected_args = list(_PROBE_TEST_COMMAND_ARGS)

    def wrapped_command_runs_probe(command: str) -> bool:
        try:
            command_parts = shlex.split(command, posix=True)
        except ValueError:
            return False
        if not command_parts:
            return False
        shell_path = command_parts[0].replace("\\", "/").casefold()
        if not shell_path.endswith(("/pwsh.exe", "/powershell.exe")):
            return False
        if len(command_parts) == 3 and command_parts[1].casefold() == "-command":
            script = command_parts[2]
        elif (
            len(command_parts) == 4
            and command_parts[1].casefold() == "-noprofile"
            and command_parts[2].casefold() == "-command"
        ):
            script = command_parts[3]
        else:
            return False
        if script == _PROBE_TEST_COMMAND:
            return True

        expected_invocation = f"& {_PROBE_TEST_PYTHON} " + " ".join(
            _PROBE_TEST_COMMAND_ARGS[1:]
        )
        wrappers = (
            (
                r"\$start=\(Get-Date\)\.ToString\('yyyy-MM-ddTHH:mm:ss\.fffzzz'\);"
                r'\s*Write-Output "START_JST=\$start";\s*',
                r";\s*\$testExit=\$LASTEXITCODE;"
                r"\s*\$end=\(Get-Date\)\.ToString\('yyyy-MM-ddTHH:mm:ss\.fffzzz'\);"
                r'\s*Write-Output "END_JST=\$end";'
                r'\s*Write-Output "EXIT_CODE=\$testExit";'
                r"\s*exit\s+\$testExit\s*",
            ),
            (
                r"\$tz=\[TimeZoneInfo\]::FindSystemTimeZoneById\('Tokyo Standard Time'\);"
                r"\s*\$start=\[DateTimeOffset\]::UtcNow;"
                r"\s*\$startJst=\[TimeZoneInfo\]::ConvertTime\(\$start,\$tz\)\.ToString\('yyyy-MM-dd HH:mm:ss\.fff zzz'\);"
                r'\s*Write-Output "START_JST=\$startJst";\s*',
                r";\s*\$testExit=\$LASTEXITCODE;"
                r"\s*\$end=\[DateTimeOffset\]::UtcNow;"
                r"\s*\$endJst=\[TimeZoneInfo\]::ConvertTime\(\$end,\$tz\)\.ToString\('yyyy-MM-dd HH:mm:ss\.fff zzz'\);"
                r'\s*Write-Output "END_JST=\$endJst";'
                r'\s*Write-Output "TEST_EXIT_CODE=\$testExit";'
                r"\s*exit\s+\$testExit\s*",
            ),
        )
        for prefix_pattern, suffix_pattern in wrappers:
            start_prefix = re.match(prefix_pattern, script, re.IGNORECASE)
            if start_prefix is None:
                continue
            if not script.startswith(expected_invocation, start_prefix.end()):
                continue
            command_suffix = script[start_prefix.end() + len(expected_invocation) :]
            if re.fullmatch(suffix_pattern, command_suffix, re.IGNORECASE):
                return True
        return False

    for event in events:
        command = event.get("command")
        if (
            event.get("method") == "item/completed"
            and event.get("item_type") == "commandExecution"
            and (
                event.get("command_args") == expected_args
                or command == _PROBE_TEST_COMMAND
                or (isinstance(command, str) and wrapped_command_runs_probe(command))
            )
            and event.get("status") == "completed"
            and event.get("exit_code") == 0
        ):
            return {**event, "command_args": expected_args}
    return None


def run_ai_probe(
    *,
    data_root: str | Path,
    profile: str,
    environment: str,
    repository_root: str | Path | None = None,
    codex_executable: str | None = None,
    timeout_seconds: int = 300,
    validated_data_root: Path | None = None,
) -> tuple[dict[str, object], int]:
    """Run all profile roles against a new synthetic job and preserve evidence."""

    if environment != "isolated":
        raise MaintenanceProbeError("probe_environment_not_isolated")
    if isinstance(timeout_seconds, bool) or not 30 <= timeout_seconds <= 1800:
        raise MaintenanceProbeError("probe_timeout_invalid")
    if validated_data_root is None:
        data_path = validate_ai_probe_target(data_root, profile=profile)
    else:
        data_path = validate_ai_probe_target_identity(
            validated_data_root, profile=profile
        )
    repo_root = Path(repository_root or _repository_root()).resolve(strict=True)
    executable = codex_executable or shutil.which("codex")
    if not executable:
        raise MaintenanceProbeError("codex_cli_unavailable")

    data_path.mkdir(parents=True, exist_ok=True)
    attempts_root = data_path / "attempts"
    worktrees_root = data_path / "worktrees"
    attempts_root.mkdir()
    now = datetime.now(timezone.utc)
    ensure_settings_file(data_path / "maintenance" / "settings.json")
    store = MaintenanceStore(
        data_path,
        daily_database_path=data_path / "state.sqlite",
    )
    store.initialize(applied_at=now)
    controller = MaintenanceController(store, diagnostic_trace=None)
    project_root, toy_commit = _create_toy_repository(data_path)
    seeded_job_id = f"probe-job-{uuid4().hex}"
    parent_key, role_names = _PROFILE_ROLES[profile]
    input_refs = [
        "probe-requirements.md",
        "probe-case.json",
        "test-ledger.json",
        "src/repair_target.py",
        "docs/templates/incidents/日次不具合調査レポート_ひな形.md",
        "docs/templates/incidents/日次不具合修正計画_ひな形.md",
        "docs/templates/incidents/日次不具合対応レポート_ひな形.html",
    ]
    input_refs.extend(
        f".agents/skills/{name}/{skill_file}"
        for name in _PROBE_SUBDIRS
        for skill_file in (
            "SKILL.md",
            "references/contracts.md",
        )
        if (project_root / ".agents" / "skills" / name / skill_file).is_file()
    )
    evidence_refs = []
    for relative in (
        "probe-requirements.md",
        "probe-case.json",
        "test-ledger.json",
        "src/repair_target.py",
    ):
        digest = hashlib.sha256((project_root / relative).read_bytes()).hexdigest()
        evidence_refs.append(f"{relative}#sha256={digest}")
    expected_model, expected_effort, expected_tier, expected_fast = PARENT_SETTINGS[
        parent_key
    ]
    # Finish all filesystem-backed probe setup before acquiring a job lease.
    identity, job_id, attempt_id, lease = _seed_probe_job(
        data_root=data_path,
        store=store,
        controller=controller,
        commit=toy_commit,
        job_id=seeded_job_id,
    )
    evidence: dict[str, object] = {
        "schema_version": _RESPONSE_SCHEMA_VERSION,
        "status": "running",
        "environment": "isolated",
        "profile": profile,
        "parent_key": parent_key,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "identity": identity,
        "toy_commit": toy_commit,
        "requested": {
            "model": expected_model,
            "effort": expected_effort,
            "service_tier": expected_tier,
            "fast_mode": expected_fast,
        },
        "observation_semantics": {
            "thread_configuration_source": "app_server_thread_start_response",
            "per_turn_source": "app_server_turn_notifications",
            "service_tier_limitation": (
                "installed app-server protocol does not report applied per-turn "
                "service tier"
            ),
            "completion_rule": (
                "require a completed turn, a confirmed thread model, and no "
                "reported runtime mismatch; record effort and service tier as "
                "unreported when the app-server does not expose their per-turn "
                "values; thread effort is the default and does not override the "
                "per-turn request"
            ),
        },
        "roles": [],
        "started_at": now.isoformat(),
    }
    result_path = data_path / "probe-result.json"
    exit_code = 3
    runtime_model_unverified = False
    runtime_settings_mismatch = False
    runtime_telemetry_unreported = False
    heartbeat_stop = threading.Event()
    heartbeat_errors: list[str] = []
    heartbeat_thread: threading.Thread | None = None
    prior_outputs: list[Mapping[str, object]] = []
    if profile == "repair":
        prior_outputs.append(
            {
                "artifact_type": "approved_synthetic_investigation",
                "role_name": "synthetic-controller-handoff",
                "result_version": f"investigation:{attempt_id}:approved-fixture",
                "status": "approved_fixture_input",
                "fact_id": "probe-F001",
                "expected": "retry",
                "actual": "hold",
                "evidence_refs": list(evidence_refs),
            }
        )
        prior_outputs.append(
            {
                "artifact_type": "approved_synthetic_plan",
                "role_name": "synthetic-controller-handoff",
                "result_version": f"planning:{attempt_id}:approved-fixture",
                "status": "approved_fixture_input",
                "allowed_files": ["src/repair_target.py"],
                "verification_command": _PROBE_TEST_COMMAND,
                "acceptance": "classify('normal') returns 'retry'",
            }
        )

    def heartbeat_lease() -> None:
        while not heartbeat_stop.wait(60):
            try:
                store.heartbeat_lease(
                    lease=lease,
                    now=datetime.now(timezone.utc),
                    ttl_seconds=180,
                )
            except Exception:
                heartbeat_errors.append("probe_lease_heartbeat_failed")
                return

    try:
        heartbeat_thread = threading.Thread(
            target=heartbeat_lease,
            name="inago-probe-lease-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        _write_atomic(result_path, evidence)
        worktree_root = _create_toy_worktree(
            repository_root=repo_root,
            project_root=project_root,
            worktrees_root=worktrees_root,
            job_id=job_id,
        )
        adapter = MaintenanceAIAdapter(
            controller,
            base_command=(executable, "app-server"),
            project_root=project_root,
            attempts_root=attempts_root,
            worktrees_root=worktrees_root,
            agent_config_dir=repo_root / ".codex" / "agents",
        )
        for role_name in role_names:
            cwd = project_root if parent_key == "A" else worktree_root
            result_version = _role_result_version(role_name, attempt_id)
            allowed_outputs = _PROBE_ROLE_OUTPUTS[role_name]
            source_versions = [
                item.get("result_version")
                for item in prior_outputs
                if isinstance(item.get("result_version"), str)
            ]
            workflow_binding: dict[str, object] = {
                **identity,
                "attempt_id": attempt_id,
                "attempt_number": 1,
                "stage": _PROBE_ROLE_STAGES[role_name],
                "base_commit": toy_commit,
                "input_refs": input_refs,
                "evidence_refs": list(evidence_refs)
                + [
                    f"role-output:{item['role_name']}#sha256={item['output_sha256']}"
                    for item in prior_outputs
                    if isinstance(item.get("role_name"), str)
                    and isinstance(item.get("output_sha256"), str)
                ],
                "source_result_versions": source_versions,
                "allowed_outputs": list(allowed_outputs),
            }
            plan = adapter.plan_session(
                parent_key=parent_key,
                role_name=role_name,
                job_id=job_id,
                attempt_id=attempt_id,
                identity=identity,
                cwd=cwd,
                attempt_output_root=attempts_root / job_id / attempt_id,
                worktree_root=worktree_root,
            )
            session = adapter.create_session(plan)
            try:
                response_schema = _response_schema(
                    identity,
                    role_name,
                    attempt_id=attempt_id,
                    role_names=role_names,
                    workflow_binding=workflow_binding,
                    result_version=result_version,
                )
                turn = session.run_turn(
                    _probe_prompt(
                        identity,
                        role_name,
                        profile=profile,
                        attempt_id=attempt_id,
                        workflow_binding=workflow_binding,
                        prior_outputs=prior_outputs,
                        observed_diff=(
                            evidence.get("product_diff")
                            if isinstance(evidence.get("product_diff"), str)
                            else None
                        ),
                    ),
                    response_schema,
                    allowed_outputs=allowed_outputs,
                    server_output_schema=_server_output_schema(response_schema),
                    deadline=time.monotonic() + timeout_seconds,
                )
            finally:
                session.close()
            workflow_response = dict(turn.response)
            _validate_role_workflow_response(
                role_name=role_name,
                response=workflow_response,
            )
            role_result = {
                "role_name": role_name,
                "workflow_stage": _PROBE_ROLE_STAGES[role_name],
                "result_version": result_version,
                "workflow_binding": workflow_binding,
                "role_config_sha256": plan.role_config_sha256,
                "thread_id": turn.turn.thread_id,
                "turn_id": turn.turn.turn_id,
                "turn_result": "completed",
                "observation_status": turn.observation_status,
                "requested_model": plan.requested_model,
                "requested_effort": plan.requested_effort,
                "requested_service_tier": plan.requested_service_tier,
                "fast_mode_configured": plan.requested_fast_mode,
                "fast_mode_capability_check": (
                    "passed" if plan.requested_fast_mode else "not_required"
                ),
                "thread_configured_model": turn.turn.thread_configured_model,
                "thread_configured_effort": turn.turn.thread_configured_effort,
                "thread_model_status": (
                    "confirmed"
                    if turn.turn.thread_configured_model == expected_model
                    else "unverified"
                ),
                "thread_default_effort": turn.turn.thread_configured_effort,
                "thread_default_effort_scope": "thread_default_not_turn_override",
                "runtime_settings_status": turn.observation_status,
                "turn_start_request_status": "accepted_and_turn_completed",
                "per_turn_observed_model": turn.turn.observed_model,
                "per_turn_model_status": (
                    "unreported"
                    if turn.turn.observed_model is None
                    else (
                        "confirmed"
                        if turn.turn.observed_model == expected_model
                        else "mismatch"
                    )
                ),
                "per_turn_observed_effort": turn.turn.observed_effort,
                "per_turn_effort_status": (
                    "unreported"
                    if turn.turn.observed_effort is None
                    else (
                        "confirmed"
                        if turn.turn.observed_effort == expected_effort
                        else "mismatch"
                    )
                ),
                "per_turn_observed_service_tier": (turn.turn.observed_service_tier),
                "per_turn_service_tier_status": (
                    "unreported"
                    if turn.turn.observed_service_tier is None
                    else (
                        "confirmed"
                        if turn.turn.observed_service_tier == expected_tier
                        else "mismatch"
                    )
                ),
                "model_reroute_event_seen": any(
                    event.get("method") == "model/rerouted"
                    for event in turn.turn.events
                ),
                "workflow_response": workflow_response,
                "turn_events": _summarize_turn_events(turn.turn.events),
                "output_sha256": turn.output_sha256,
            }
            roles = evidence["roles"]
            assert isinstance(roles, list)
            roles.append(role_result)
            prior_outputs.append(
                {
                    "role_name": role_name,
                    "result_version": result_version,
                    "thread_id": turn.turn.thread_id,
                    "turn_id": turn.turn.turn_id,
                    "output_sha256": turn.output_sha256,
                    "workflow_response": workflow_response,
                }
            )
            if role_name == "common-implementer":
                changed_paths = _git(
                    worktree_root, "diff", "--name-only", "HEAD"
                ).splitlines()
                status_paths = _git(
                    worktree_root,
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ).splitlines()
                if changed_paths != ["src/repair_target.py"]:
                    raise MaintenanceProbeError("probe_implementation_scope_mismatch")
                if len(status_paths) != 1 or not status_paths[0].endswith(
                    "src/repair_target.py"
                ):
                    raise MaintenanceProbeError("probe_implementation_scope_mismatch")
                evidence["product_diff"] = _git(
                    worktree_root, "diff", "--", "src/repair_target.py"
                )
                evidence["product_diff_sha256"] = hashlib.sha256(
                    str(evidence["product_diff"]).encode("utf-8")
                ).hexdigest()
                evidence["product_changed_paths"] = changed_paths
            if role_name == "common-tester":
                details = workflow_response.get("details")
                if not isinstance(details, Mapping):
                    raise MaintenanceProbeError("probe_test_result_missing")
                turn_events = role_result["turn_events"]
                host_test_event = (
                    _successful_test_execution_event(turn_events)
                    if isinstance(turn_events, list)
                    else None
                )
                if host_test_event is None:
                    raise MaintenanceProbeError("probe_test_execution_unobserved")
                if (
                    details.get("command") != _PROBE_TEST_COMMAND
                    or details.get("result") != "passed"
                    or details.get("exit_code") != 0
                    or not isinstance(details.get("observed_output"), str)
                    or "1 passed" not in details.get("observed_output", "")
                ):
                    raise MaintenanceProbeError("probe_test_result_invalid")
                changed_paths = _git(
                    worktree_root, "diff", "--name-only", "HEAD"
                ).splitlines()
                status_paths = _git(
                    worktree_root,
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ).splitlines()
                if (
                    changed_paths != ["src/repair_target.py"]
                    or len(status_paths) != 1
                    or not status_paths[0].endswith("src/repair_target.py")
                ):
                    raise MaintenanceProbeError("probe_test_scope_mismatch")
                product_diff_after_test = _git(
                    worktree_root, "diff", "--", "src/repair_target.py"
                )
                product_diff_hash_after_test = hashlib.sha256(
                    product_diff_after_test.encode("utf-8")
                ).hexdigest()
                if product_diff_hash_after_test != evidence.get("product_diff_sha256"):
                    raise MaintenanceProbeError("probe_tester_modified_product")
                evidence["test_execution_observation"] = {
                    "command": details["command"],
                    "reported_result": details["result"],
                    "reported_exit_code": details["exit_code"],
                    "reported_output": details["observed_output"],
                    "host_command_args": host_test_event["command_args"],
                    "host_status": host_test_event["status"],
                    "host_exit_code": host_test_event["exit_code"],
                    "event_evidence": turn_events,
                }
                prior_outputs[-1] = {
                    **prior_outputs[-1],
                    "execution_observation": evidence["test_execution_observation"],
                }
            if role_name == "common-reviewer":
                evidence["product_review"] = workflow_response.get("details")
            if role_name == "rr-inago-incident-reporter":
                evidence["synthetic_report"] = workflow_response.get("details")
            _write_atomic(result_path, evidence)
            if not _review_role_approved(role_name, workflow_response["details"]):
                evidence["status"] = "failed"
                evidence["reason_code"] = "probe_review_rejected"
                _write_atomic(result_path, evidence)
                raise MaintenanceProbeError("probe_review_rejected")
            if role_result["thread_model_status"] != "confirmed":
                runtime_model_unverified = True
            if role_result["runtime_settings_status"] == "blocked":
                runtime_settings_mismatch = True
            if any(
                role_result[key] == "unreported"
                for key in (
                    "per_turn_model_status",
                    "per_turn_effort_status",
                    "per_turn_service_tier_status",
                )
            ):
                runtime_telemetry_unreported = True
            if heartbeat_errors:
                raise MaintenanceProbeError("probe_lease_heartbeat_failed")
        if heartbeat_errors:
            raise MaintenanceProbeError("probe_lease_heartbeat_failed")
        evidence["workflow_status"] = (
            "investigation_and_plan_complete"
            if profile == "investigation"
            else "repair_test_review_and_report_complete"
        )
        if runtime_settings_mismatch:
            evidence["status"] = "failed"
            evidence["reason_code"] = "runtime_settings_mismatch"
            exit_code = 3
        elif runtime_model_unverified:
            evidence["status"] = "unverified"
            evidence["reason_code"] = "thread_model_unverified"
            exit_code = 3
        else:
            evidence["status"] = "complete"
            exit_code = 0
            evidence["runtime_telemetry_status"] = (
                "unreported" if runtime_telemetry_unreported else "verified"
            )
    except (
        MaintenanceAIBlockedError,
        MaintenanceStoreError,
        MaintenanceProbeError,
    ) as exc:
        evidence["status"] = "failed"
        evidence["reason_code"] = getattr(
            exc, "reason_code", getattr(exc, "code", "probe_failed")
        )
        exit_code = 3
    except Exception:
        evidence["status"] = "failed"
        evidence["reason_code"] = "probe_execution_failed"
        exit_code = 3
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)
            if heartbeat_thread.is_alive():
                heartbeat_errors.append("probe_lease_heartbeat_shutdown_failed")
        if heartbeat_errors:
            evidence["status"] = "failed"
            evidence["reason_code"] = "probe_lease_heartbeat_failed"
            exit_code = 3
        try:
            controller.finalize_job(
                job_id=job_id,
                final_status="cancelled",
                lease=lease,
                finalized_at=datetime.now(timezone.utc),
                reason_code=("probe_completed" if exit_code == 0 else "probe_failed"),
            )
            current_lease = store.get_lease(lease.lease_key)
            if current_lease is not None:
                store.release_lease(
                    lease=LeaseToken(
                        current_lease.lease_key,
                        current_lease.owner_id,
                        current_lease.generation,
                    ),
                    released_at=datetime.now(timezone.utc),
                )
        except (MaintenanceStoreError, TypeError):
            evidence["cleanup_status"] = "needs_reconciliation"
            evidence["status"] = "failed"
            if exit_code == 0:
                exit_code = 3

    evidence["status"] = (
        "complete" if exit_code == 0 else evidence.get("status", "failed")
    )
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    evidence["counts"] = store.counts()
    evidence["settings"] = {
        "model": expected_model,
        "effort": expected_effort,
        "service_tier": expected_tier,
        "fast_mode": expected_fast,
    }
    _write_atomic(result_path, evidence)
    return evidence, exit_code


def validate_ai_probe_target_identity(data_root: Path, *, profile: str) -> Path:
    """Recheck a root previously checked for emptiness before CLI tracing."""

    if profile not in _PROFILE_ROLES:
        raise MaintenanceProbeError("probe_profile_invalid")
    root = Path(os.path.abspath(data_root))
    expected_parent = (
        _repository_root()
        / "非公開作業データ"
        / "イナゴ実装試験"
        / "maintenance-20261017"
        / "U18"
        / ("LC01" if profile == "investigation" else "LC02")
    )
    try:
        relative = root.relative_to(expected_parent)
    except ValueError as exc:
        raise MaintenanceProbeError("probe_data_root_out_of_scope") from exc
    if (
        len(relative.parts) != 2
        or not relative.parts[0].startswith("attempt-")
        or relative.parts[1] != "runtime"
    ):
        raise MaintenanceProbeError("probe_data_root_out_of_scope")
    _assert_no_reparse_components(expected_parent)
    _assert_no_reparse_components(root)
    return root
