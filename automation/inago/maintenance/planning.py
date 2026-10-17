"""U13 planning-stage contracts and safe document rendering."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import html
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
_PROMPT_LABELS = (
    "作業ルート:",
    "計画文書:",
    "対象STEP:",
    "入力・版:",
    "開始条件:",
    "作業内容:",
    "所有ファイル:",
    "変更禁止:",
    "検証:",
    "合格条件:",
    "停止・再開:",
    "証拠:",
    "Git:",
    "次工程:",
)
_STEP_FIELDS = {
    "identity",
    "purpose",
    "requirements",
    "predecessors",
    "inputs",
    "ownership",
    "procedure",
    "outputs",
    "constraints",
    "review",
    "acceptance_evidence",
    "failure_recovery",
    "handoff",
    "full_prompt",
}
_CASE_FIELDS = {
    "case_id",
    "node",
    "requirement_step",
    "purpose",
    "kind_method",
    "preconditions",
    "inputs_operations",
    "execution_configuration",
    "expected_output",
    "expected_state_logs",
    "forbidden_side_effects",
    "pass_criteria",
    "evidence",
    "cleanup_retry",
}
_LOG_FIELD_SOURCES = {
    "source_run_id": "automation/inago/maintenance/daily_launcher.py::source_run_id",
    "overall_status": "automation/inago/maintenance/daily_launcher.py::overall_status",
    "daily_status": "automation/inago/maintenance/daily_launcher.py::daily_status",
    "reason_code": "automation/inago/maintenance/daily_launcher.py::reason_code",
    "failure_phase": "automation/inago/maintenance/daily_launcher.py::failure_phase",
    "trace_status": "automation/inago/maintenance/daily_launcher.py::trace_status",
    "missing_reason": "automation/inago/maintenance/planning.py::expected_missing_reason",
}


class PlanningContractError(ValueError):
    """A planning result, independent review, or input binding is invalid."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=2)
def _schema_validator(name: str) -> Draft202012Validator:
    if name not in {
        "maintenance-planning-response.schema.json",
        "maintenance-planning-review.schema.json",
    }:
        raise PlanningContractError("planning_schema_unknown")
    path = Path(__file__).resolve().parents[1] / "schemas" / name
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise PlanningContractError("planning_schema_unavailable") from exc


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_list(value: object, code: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not _nonempty_text(item) for item in value)
    ):
        raise PlanningContractError(code)
    return list(value)


def _text_values(value: object, code: str) -> list[str]:
    if _nonempty_text(value):
        return [value]
    return _text_list(value, code)


def _validate_planning_context(
    context: object, investigation_facts: object
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(context, Mapping):
        raise PlanningContractError("planning_context_invalid")
    data = dict(context)
    required = {
        "incident_id",
        "source_launch_id",
        "source_run_id",
        "target_date",
        "job_id",
        "attempt_id",
        "investigation_attempt_id",
        "investigation_result_version",
        "base_commit",
        "input_refs",
        "evidence_refs",
        "requirements",
        "case_manifest",
        "work_root",
        "plan_document",
    }
    if not required.issubset(data):
        raise PlanningContractError("planning_context_incomplete")
    _validate_context_identity(data)
    _normalize_context_refs(data)
    _normalize_context_requirements(data)
    _validate_context_location(data)
    data["case_manifest"] = _normalize_case_manifest(data["case_manifest"])
    facts = _validate_investigation_facts(investigation_facts, data["evidence_refs"])
    return data, facts


def _validate_context_identity(data: Mapping[str, Any]) -> None:
    for key in (
        "incident_id",
        "source_launch_id",
        "job_id",
        "attempt_id",
        "investigation_attempt_id",
        "investigation_result_version",
    ):
        if not isinstance(data[key], str) or _ID_PATTERN.fullmatch(data[key]) is None:
            raise PlanningContractError("planning_context_identity_invalid")
    if data["source_run_id"] is not None and (
        not isinstance(data["source_run_id"], str)
        or _ID_PATTERN.fullmatch(data["source_run_id"]) is None
    ):
        raise PlanningContractError("planning_context_identity_invalid")
    if not isinstance(data["base_commit"], str) or not re.fullmatch(
        r"[0-9a-fA-F]{7,64}", data["base_commit"]
    ):
        raise PlanningContractError("planning_context_version_invalid")


def _normalize_context_refs(data: dict[str, Any]) -> None:
    for field in ("input_refs", "evidence_refs"):
        refs = data[field]
        if (
            not isinstance(refs, (list, tuple))
            or not refs
            or any(
                not isinstance(ref, str) or not _REF_PATTERN.fullmatch(ref)
                for ref in refs
            )
            or len(set(refs)) != len(refs)
        ):
            raise PlanningContractError("planning_context_refs_invalid")
        data[field] = list(refs)


def _normalize_context_requirements(data: dict[str, Any]) -> None:
    requirements = data["requirements"]
    if (
        not isinstance(requirements, (list, tuple))
        or not requirements
        or any(
            not isinstance(item, str) or _ID_PATTERN.fullmatch(item) is None
            for item in requirements
        )
        or len(set(requirements)) != len(requirements)
    ):
        raise PlanningContractError("planning_requirements_invalid")
    data["requirements"] = list(requirements)


def _validate_context_location(data: Mapping[str, Any]) -> None:
    if not isinstance(data["target_date"], str):
        raise PlanningContractError("planning_context_identity_invalid")
    if not _nonempty_text(data["work_root"]) or not _nonempty_text(
        data["plan_document"]
    ):
        raise PlanningContractError("planning_context_location_invalid")


def _normalize_case_manifest(case_manifest: object) -> list[dict[str, str]]:
    if not isinstance(case_manifest, list) or not case_manifest:
        raise PlanningContractError("planning_case_manifest_invalid")
    normalized_cases: list[dict[str, str]] = []
    for item in case_manifest:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"case_id", "node"}
            or not _nonempty_text(item["case_id"])
            or not _nonempty_text(item["node"])
        ):
            raise PlanningContractError("planning_case_manifest_invalid")
        normalized_cases.append({"case_id": item["case_id"], "node": item["node"]})
    if len({item["case_id"] for item in normalized_cases}) != len(normalized_cases):
        raise PlanningContractError("planning_case_manifest_invalid")
    return normalized_cases


def _validate_investigation_facts(
    investigation_facts: object, allowed_refs: list[str]
) -> list[dict[str, Any]]:
    if not isinstance(investigation_facts, list) or not investigation_facts:
        raise PlanningContractError("planning_investigation_facts_invalid")
    facts: list[dict[str, Any]] = []
    seen_facts: set[str] = set()
    for fact in investigation_facts:
        if not isinstance(fact, Mapping):
            raise PlanningContractError("planning_investigation_facts_invalid")
        fact_id = fact.get("fact_id")
        confidence = fact.get("cause_confidence")
        refs = fact.get("evidence_refs")
        if (
            not isinstance(fact_id, str)
            or _ID_PATTERN.fullmatch(fact_id) is None
            or fact_id in seen_facts
            or not isinstance(confidence, str)
            or confidence not in {"confirmed", "limited", "unknown"}
            or not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) for ref in refs)
            or any(ref not in allowed_refs for ref in refs)
        ):
            raise PlanningContractError("planning_investigation_facts_invalid")
        facts.append(dict(fact))
        seen_facts.add(fact_id)
    return facts


def _validate_sections(sections: object) -> None:
    if not isinstance(sections, list) or len(sections) != 15:
        raise PlanningContractError("planning_sections_incomplete")
    for expected, section in enumerate(sections):
        if (
            not isinstance(section, Mapping)
            or set(section) != {"number", "title", "content"}
            or isinstance(section["number"], bool)
            or not isinstance(section["number"], int)
            or section["number"] != expected
            or not _nonempty_text(section["title"])
            or not _nonempty_text(section["content"])
        ):
            raise PlanningContractError("planning_sections_incomplete")


def _validate_step_document(
    step: object, job: Mapping[str, Any], facts: list[dict[str, Any]]
) -> tuple[int, set[str]]:
    if not isinstance(step, Mapping) or set(step) != _STEP_FIELDS:
        raise PlanningContractError("planning_step_fields_invalid")
    identity = step["identity"]
    if (
        not isinstance(identity, Mapping)
        or set(identity) != {"number", "name", "status"}
        or isinstance(identity["number"], bool)
        or not isinstance(identity["number"], int)
        or identity["number"] < 1
        or not _nonempty_text(identity["name"])
        or identity["status"] not in {"planned", "blocked", "complete"}
    ):
        raise PlanningContractError("planning_step_fields_invalid")
    requirements = set(_text_list(step["requirements"], "planning_step_fields_invalid"))
    if not requirements.issubset(job["requirements"]):
        raise PlanningContractError("planning_step_requirement_unknown")
    for field in ("predecessors", "inputs", "ownership", "outputs"):
        _text_list(step[field], "planning_step_fields_invalid")
    for field in (
        "purpose",
        "procedure",
        "constraints",
        "review",
        "acceptance_evidence",
        "failure_recovery",
        "handoff",
    ):
        _text_values(step[field], "planning_step_fields_invalid")
    _validate_full_prompt(step["full_prompt"], job, facts)
    return identity["number"], requirements


def _validate_full_prompt(
    prompt: object, job: Mapping[str, Any], facts: list[dict[str, Any]]
) -> None:
    if (
        not _nonempty_text(prompt)
        or len(prompt) < 300
        or any(label not in prompt for label in _PROMPT_LABELS)
        or any(marker in prompt for marker in ("...", "上記を参照", "省略"))
        or job["work_root"] not in prompt
        or job["plan_document"] not in prompt
        or job["base_commit"] not in prompt
        or job["investigation_result_version"] not in prompt
        or any(fact["fact_id"] not in prompt for fact in facts)
        or any(ref not in prompt for ref in job["input_refs"] + job["evidence_refs"])
    ):
        raise PlanningContractError("planning_prompt_incomplete")


def _validate_steps(
    steps: object, job: Mapping[str, Any], facts: list[dict[str, Any]]
) -> dict[int, set[str]]:
    if not isinstance(steps, list) or not steps:
        raise PlanningContractError("planning_step_fields_invalid")
    step_requirements: dict[int, set[str]] = {}
    for step in steps:
        number, requirements = _validate_step_document(step, job, facts)
        if number in step_requirements:
            raise PlanningContractError("planning_step_fields_invalid")
        step_requirements[number] = requirements
    return step_requirements


def _validate_cases(
    cases: object,
    case_manifest: Mapping[str, str],
    requirements: set[str],
    step_numbers: set[int],
) -> dict[str, set[tuple[str, int]]]:
    if not isinstance(cases, list) or len(cases) != len(case_manifest):
        raise PlanningContractError("planning_case_manifest_mismatch")
    case_edges: dict[str, set[tuple[str, int]]] = {}
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != _CASE_FIELDS:
            raise PlanningContractError("planning_case_fields_invalid")
        case_id = case["case_id"]
        if (
            not isinstance(case_id, str)
            or case_id not in case_manifest
            or case["node"] != case_manifest[case_id]
            or case_id in case_edges
        ):
            raise PlanningContractError("planning_case_manifest_mismatch")
        raw_edges = case["requirement_step"]
        if not isinstance(raw_edges, list) or not raw_edges:
            raise PlanningContractError("planning_case_fields_invalid")
        edges: set[tuple[str, int]] = set()
        for edge in raw_edges:
            if (
                not isinstance(edge, Mapping)
                or set(edge) != {"requirement_id", "step_number"}
                or not isinstance(edge["requirement_id"], str)
                or edge["requirement_id"] not in requirements
                or isinstance(edge["step_number"], bool)
                or not isinstance(edge["step_number"], int)
                or edge["step_number"] not in step_numbers
            ):
                raise PlanningContractError("planning_case_fields_invalid")
            pair = (edge["requirement_id"], edge["step_number"])
            if pair in edges:
                raise PlanningContractError("planning_case_fields_invalid")
            edges.add(pair)
        case_edges[case_id] = edges
        for field in _CASE_FIELDS - {"case_id", "node", "requirement_step"}:
            _text_values(case[field], "planning_case_fields_invalid")
    return case_edges


def _validate_coverage_row(
    row: object,
    *,
    job: Mapping[str, Any],
    facts: list[dict[str, Any]],
    step_requirements: Mapping[int, set[str]],
    case_edges: Mapping[str, set[tuple[str, int]]],
    covered_requirements: set[str],
) -> tuple[str, list[str], list[str], list[int], list[str]]:
    fields = {
        "requirement_id",
        "fact_ids",
        "evidence_refs",
        "step_numbers",
        "case_ids",
    }
    if not isinstance(row, Mapping) or set(row) != fields:
        raise PlanningContractError("planning_coverage_incomplete")
    requirement_id = row["requirement_id"]
    fact_values = row["fact_ids"]
    evidence_values = row["evidence_refs"]
    step_values = row["step_numbers"]
    case_values = row["case_ids"]
    if not _coverage_values_are_valid(
        requirement_id,
        fact_values,
        evidence_values,
        step_values,
        case_values,
        job=job,
        facts=facts,
        step_requirements=step_requirements,
        case_edges=case_edges,
        covered_requirements=covered_requirements,
    ):
        raise PlanningContractError("planning_coverage_incomplete")
    return requirement_id, fact_values, evidence_values, step_values, case_values


def _coverage_values_are_valid(
    requirement_id: object,
    fact_values: object,
    evidence_values: object,
    step_values: object,
    case_values: object,
    *,
    job: Mapping[str, Any],
    facts: list[dict[str, Any]],
    step_requirements: Mapping[int, set[str]],
    case_edges: Mapping[str, set[tuple[str, int]]],
    covered_requirements: set[str],
) -> bool:
    fact_ids = {fact["fact_id"] for fact in facts}
    return (
        isinstance(requirement_id, str)
        and requirement_id in job["requirements"]
        and requirement_id not in covered_requirements
        and _coverage_fact_evidence_valid(
            fact_values, evidence_values, facts=facts, fact_ids=fact_ids
        )
        and _coverage_step_case_valid(
            step_values,
            case_values,
            step_requirements=step_requirements,
            case_edges=case_edges,
        )
    )


def _coverage_fact_evidence_valid(
    fact_values: object,
    evidence_values: object,
    *,
    facts: list[dict[str, Any]],
    fact_ids: set[str],
) -> bool:
    allowed_evidence = (
        {
            ref
            for fact in facts
            if fact["fact_id"] in fact_values
            for ref in fact["evidence_refs"]
        }
        if isinstance(fact_values, list)
        and all(isinstance(value, str) for value in fact_values)
        else set()
    )
    return (
        isinstance(fact_values, list)
        and bool(fact_values)
        and all(isinstance(value, str) for value in fact_values)
        and len(set(fact_values)) == len(fact_values)
        and set(fact_values).issubset(fact_ids)
        and isinstance(evidence_values, list)
        and bool(evidence_values)
        and all(isinstance(value, str) for value in evidence_values)
        and len(set(evidence_values)) == len(evidence_values)
        and set(evidence_values).issubset(allowed_evidence)
    )


def _coverage_step_case_valid(
    step_values: object,
    case_values: object,
    *,
    step_requirements: Mapping[int, set[str]],
    case_edges: Mapping[str, set[tuple[str, int]]],
) -> bool:
    return (
        isinstance(step_values, list)
        and bool(step_values)
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in step_values
        )
        and len(set(step_values)) == len(step_values)
        and set(step_values).issubset(step_requirements)
        and isinstance(case_values, list)
        and bool(case_values)
        and all(isinstance(value, str) for value in case_values)
        and len(set(case_values)) == len(case_values)
        and set(case_values).issubset(case_edges)
    )


def _record_coverage_row(
    row: tuple[str, list[str], list[str], list[int], list[str]],
    *,
    state: dict[str, Any],
    step_requirements: Mapping[int, set[str]],
    case_edges: Mapping[str, set[tuple[str, int]]],
) -> None:
    requirement_id, fact_values, evidence_values, step_values, case_values = row
    for fact_id in fact_values:
        state["fact_refs"][fact_id].update(evidence_values)
    for step_number in step_values:
        if requirement_id not in step_requirements[step_number]:
            raise PlanningContractError("planning_coverage_incomplete")
        state["step_edges"].add((requirement_id, step_number))
        for case_id in case_values:
            if (requirement_id, step_number) not in case_edges[case_id]:
                raise PlanningContractError("planning_coverage_incomplete")
            state["case_edges"].add((requirement_id, step_number, case_id))
    state["requirements"].add(requirement_id)
    state["facts"].update(fact_values)
    state["steps"].update(step_values)
    state["cases"].update(case_values)


def _validate_coverage_complete(
    state: Mapping[str, Any],
    *,
    job: Mapping[str, Any],
    facts: list[dict[str, Any]],
    step_requirements: Mapping[int, set[str]],
    case_edges: Mapping[str, set[tuple[str, int]]],
) -> None:
    fact_ids = {fact["fact_id"] for fact in facts}
    declared_step_edges = {
        (requirement_id, step_number)
        for step_number, requirement_ids in step_requirements.items()
        for requirement_id in requirement_ids
    }
    declared_case_edges = {
        (requirement_id, step_number, case_id)
        for case_id, edges in case_edges.items()
        for requirement_id, step_number in edges
    }
    if (
        state["requirements"] != set(job["requirements"])
        or state["facts"] != fact_ids
        or state["steps"] != set(step_requirements)
        or state["cases"] != set(case_edges)
        or state["step_edges"] != declared_step_edges
        or state["case_edges"] != declared_case_edges
        or any(
            not set(fact["evidence_refs"]).issubset(state["fact_refs"][fact["fact_id"]])
            for fact in facts
        )
    ):
        raise PlanningContractError("planning_coverage_incomplete")


def _validate_coverage(
    coverage: object,
    *,
    job: Mapping[str, Any],
    facts: list[dict[str, Any]],
    step_requirements: Mapping[int, set[str]],
    case_edges: Mapping[str, set[tuple[str, int]]],
) -> None:
    if not isinstance(coverage, list) or len(coverage) != len(job["requirements"]):
        raise PlanningContractError("planning_coverage_incomplete")
    fact_ids = {fact["fact_id"] for fact in facts}
    state = {
        "requirements": set(),
        "facts": set(),
        "steps": set(),
        "cases": set(),
        "step_edges": set(),
        "case_edges": set(),
        "fact_refs": {fact_id: set() for fact_id in fact_ids},
    }
    for row in coverage:
        normalized = _validate_coverage_row(
            row,
            job=job,
            facts=facts,
            step_requirements=step_requirements,
            case_edges=case_edges,
            covered_requirements=state["requirements"],
        )
        _record_coverage_row(
            normalized,
            state=state,
            step_requirements=step_requirements,
            case_edges=case_edges,
        )
    _validate_coverage_complete(
        state,
        job=job,
        facts=facts,
        step_requirements=step_requirements,
        case_edges=case_edges,
    )


def _validate_cause_actions(actions: object, facts: list[dict[str, Any]]) -> None:
    fact_ids = {fact["fact_id"] for fact in facts}
    unknown_fact_ids = {
        fact["fact_id"] for fact in facts if fact["cause_confidence"] == "unknown"
    }
    if not isinstance(actions, list):
        raise PlanningContractError("planning_cause_actions_invalid")
    action_map: dict[str, str] = {}
    for action in actions:
        if not isinstance(action, Mapping) or set(action) != {
            "fact_id",
            "action",
            "basis",
        }:
            raise PlanningContractError("planning_cause_actions_invalid")
        fact_id = action["fact_id"]
        action_name = action["action"]
        if (
            not isinstance(fact_id, str)
            or fact_id not in fact_ids
            or fact_id in action_map
            or not isinstance(action_name, str)
            or action_name not in {"verify_only", "hold", "repair_verified"}
            or not _nonempty_text(action["basis"])
        ):
            raise PlanningContractError("planning_cause_actions_invalid")
        action_map[fact_id] = action_name
    if not unknown_fact_ids.issubset(action_map):
        raise PlanningContractError("planning_cause_actions_invalid")
    if any(
        action_map[fact_id] not in {"verify_only", "hold"}
        for fact_id in unknown_fact_ids
    ):
        raise PlanningContractError("planning_unknown_cause_assumed")


def expected_missing_reason(daily_completion: Mapping[str, Any]) -> str:
    if daily_completion.get("source_run_id") is None:
        return "source_run_not_saved"
    trace_status = daily_completion.get("trace_status")
    if trace_status == "not_written":
        return "trace_not_written"
    if trace_status == "unavailable":
        return "trace_unavailable"
    if trace_status == "degraded":
        return "trace_degraded"
    return "none"


def _validate_logging_bindings(logging: Mapping[str, Any]) -> None:
    bindings = logging["field_bindings"]
    if not isinstance(bindings, list) or len(bindings) != len(_LOG_FIELD_SOURCES):
        raise PlanningContractError("planning_logging_binding_invalid")
    binding_map: dict[str, Mapping[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, Mapping) or set(binding) != {
            "field",
            "schema_ref",
            "source_ref",
            "case_id",
        }:
            raise PlanningContractError("planning_logging_binding_invalid")
        field = binding["field"]
        if (
            not isinstance(field, str)
            or field not in _LOG_FIELD_SOURCES
            or field in binding_map
        ):
            raise PlanningContractError("planning_logging_binding_invalid")
        binding_map[field] = binding
    if set(binding_map) != set(_LOG_FIELD_SOURCES) or any(
        binding_map[field]["schema_ref"]
        != f"#/properties/logging_contract/properties/{field}"
        or binding_map[field]["source_ref"] != _LOG_FIELD_SOURCES[field]
        or binding_map[field]["case_id"] != "C32"
        for field in _LOG_FIELD_SOURCES
    ):
        raise PlanningContractError("planning_logging_binding_invalid")


def _validate_logging_contract(
    logging: object,
    *,
    job: Mapping[str, Any],
    daily_completion: Mapping[str, Any],
) -> None:
    if not isinstance(logging, Mapping):
        raise PlanningContractError("planning_logging_value_mismatch")
    if logging.get("source_run_id") != job["source_run_id"]:
        raise PlanningContractError("planning_source_run_id_mismatch")
    expected = {
        "source_run_id": daily_completion["source_run_id"],
        "overall_status": daily_completion["overall_status"],
        "daily_status": daily_completion["daily_status"],
        "reason_code": daily_completion["reason_code"],
        "failure_phase": daily_completion["failure_phase"],
        "trace_status": daily_completion["trace_status"],
        "missing_reason": expected_missing_reason(daily_completion),
    }
    if any(logging.get(field) != value for field, value in expected.items()):
        raise PlanningContractError("planning_logging_value_mismatch")
    _validate_logging_bindings(logging)


def validate_planning_response(
    response: object,
    context: Mapping[str, Any],
    investigation_facts: list[dict[str, Any]],
    *,
    daily_completion: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the plan, every traceability edge, and C32 store-bound values."""

    job, facts = _validate_planning_context(context, investigation_facts)
    if not isinstance(response, Mapping):
        raise PlanningContractError("planning_response_invalid")
    data = dict(response)
    _validate_sections(data.get("sections"))
    try:
        _schema_validator("maintenance-planning-response.schema.json").validate(data)
    except ValidationError as exc:
        raise PlanningContractError("planning_response_invalid") from exc
    if data["stage"] != "planning" or data["review_required"] is not True:
        raise PlanningContractError("planning_response_invalid")
    step_requirements = _validate_steps(data["steps"], job, facts)
    case_manifest = {item["case_id"]: item["node"] for item in job["case_manifest"]}
    case_edges = _validate_cases(
        data["cases"],
        case_manifest,
        set(job["requirements"]),
        set(step_requirements),
    )
    _validate_coverage(
        data["coverage"],
        job=job,
        facts=facts,
        step_requirements=step_requirements,
        case_edges=case_edges,
    )
    _validate_cause_actions(data["cause_actions"], facts)
    _validate_logging_contract(
        data["logging_contract"], job=job, daily_completion=daily_completion
    )
    if data["status"] != "ready" and (
        not data["hold_conditions"] or not _nonempty_text(data["final_explanation"])
    ):
        raise PlanningContractError("planning_hold_explanation_required")
    return data


def validate_planning_review(
    review: object,
    *,
    result_version: str,
    author_id: str,
    author_execution_id: str,
    reviewer_id: str,
    reviewer_execution_id: str,
) -> dict[str, Any]:
    """Require an independent reviewer and the exact plan version."""

    if not isinstance(review, Mapping):
        raise PlanningContractError("planning_review_invalid")
    data = dict(review)
    try:
        _schema_validator("maintenance-planning-review.schema.json").validate(data)
    except ValidationError as exc:
        raise PlanningContractError("planning_review_invalid") from exc
    if (
        data["reviewer_id"] != reviewer_id
        or data["review_execution_id"] != reviewer_execution_id
        or reviewer_id == author_id
        or reviewer_execution_id == author_execution_id
    ):
        raise PlanningContractError("planning_review_not_independent")
    if any(
        not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None
        for value in (
            result_version,
            author_id,
            author_execution_id,
            reviewer_id,
            reviewer_execution_id,
        )
    ):
        raise PlanningContractError("planning_review_identity_invalid")
    if data["reviewed_version"] != result_version:
        raise PlanningContractError("planning_review_version_mismatch")
    if data["status"] == "changes_requested" and not data["findings"]:
        raise PlanningContractError("planning_review_findings_required")
    return data


def render_plan_markdown(response: Mapping[str, Any]) -> str:
    """Render a fixed Markdown report from a validated plan response."""

    lines = ["# Maintenance repair plan", "", f"Status: `{response['status']}`", ""]
    for section in response["sections"]:
        lines.extend(
            [
                f"## {section['number']}. {_markdown(section['title'])}",
                "",
                _markdown(section["content"]),
                "",
            ]
        )
    lines.extend(["# STEP details", ""])
    for step in response["steps"]:
        identity = step["identity"]
        lines.extend(
            [
                f"## STEP {identity['number']}: {_markdown(identity['name'])}",
                "",
                f"1. Identity/status: `{identity['status']}`",
            ]
        )
        for index, field in enumerate(
            (
                "purpose",
                "requirements",
                "predecessors",
                "inputs",
                "ownership",
                "procedure",
                "outputs",
                "constraints",
                "review",
                "acceptance_evidence",
                "failure_recovery",
                "handoff",
            ),
            2,
        ):
            values = _text_values(step[field], "planning_step_fields_invalid")
            rendered = " / ".join(_markdown(value) for value in values)
            lines.append(f"{index}. **{field}**: {rendered}")
        lines.extend(
            ["14. **Full standalone prompt**:", "", _markdown(step["full_prompt"]), ""]
        )
    lines.extend(["# Test cases", ""])
    for case in response["cases"]:
        lines.extend([f"## {case['case_id']} — `{_markdown(case['node'])}`", ""])
        for field in (
            "requirement_step",
            "purpose",
            "kind_method",
            "preconditions",
            "inputs_operations",
            "execution_configuration",
            "expected_output",
            "expected_state_logs",
            "forbidden_side_effects",
            "pass_criteria",
            "evidence",
            "cleanup_retry",
        ):
            if field == "requirement_step":
                case_values = [
                    f"{item['requirement_id']} → STEP {item['step_number']}"
                    for item in case[field]
                ]
            else:
                case_values = _text_values(case[field], "planning_case_fields_invalid")
            lines.append(
                f"- **{field}**: {' / '.join(_markdown(item) for item in case_values)}"
            )
        lines.append("")
    lines.extend(
        [
            "# Unknown-cause handling",
            "",
            "Root causes remain unconfirmed until their evidence is verified. A cause-dependent fix is not planned while confidence is unknown.",
            "",
            _markdown(response["final_explanation"]),
            "",
        ]
    )
    return "\n".join(lines)


def build_requirement_step_case_map(
    response: Mapping[str, Any], *, result_version: str, investigation_version: str
) -> dict[str, Any]:
    return {
        "schema_version": "maintenance-requirement-step-case-map-v1",
        "result_version": result_version,
        "investigation_result_version": investigation_version,
        "coverage": response["coverage"],
        "cause_actions": response["cause_actions"],
        "case_nodes": [
            {
                "case_id": case["case_id"],
                "node": case["node"],
                "requirement_step": case["requirement_step"],
            }
            for case in response["cases"]
        ],
    }


def render_planning_review_markdown(
    review: Mapping[str, Any], *, gate_status: str
) -> str:
    lines = [
        "# Independent planning review",
        "",
        f"- Reviewer: `{_markdown(review['reviewer_id'])}`",
        f"- Execution: `{_markdown(review['review_execution_id'])}`",
        f"- Reviewed version: `{_markdown(review['reviewed_version'])}`",
        f"- Review status: `{_markdown(review['status'])}`",
        f"- Store gate: `{_markdown(gate_status)}`",
        "",
        "## Findings",
        "",
    ]
    lines.extend(f"- {_markdown(item)}" for item in review["findings"])
    if not review["findings"]:
        lines.append("- No findings.")
    return "\n".join(lines) + "\n"


def _markdown(value: object) -> str:
    text = html.escape(str(value), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "|", "#", ">"):
        text = text.replace(character, f"&#{ord(character)};")
    return text
