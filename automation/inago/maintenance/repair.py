"""Host-side guards for isolated maintenance repair and verification campaigns."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator, ValidationError


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_TEST_STATUSES = {"PASS", "FAIL", "NOT_RUN", "SKIP", "XFAIL", "ERROR", "UNVERIFIED"}
_ELIGIBLE_STATUSES = {"FAIL", "NOT_RUN"}
_INVALID_CAMPAIGN_STATUSES = {"SKIP", "XFAIL", "ERROR", "UNVERIFIED"}
_C34_NODE = "tests_inago/test_maintenance_repair.py::test_c34_contract"
_ATTESTATION_SOURCE = "host_collected"


class RepairContractError(ValueError):
    """A repair-stage input violates its host-side contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PatchAssessment:
    """Whether a proposed patch respects file ownership and test assertions."""

    accepted: bool
    reason_codes: tuple[str, ...]
    changed_paths: tuple[str, ...]
    assertion_snapshot_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class TestRunEvidence:
    """One host-observed synthetic test run, without raw console output."""

    node: str
    outcome: str
    exit_code: int
    collected: int
    assertion_failures: int
    errors: int
    code_sha256: str
    semantic_case_id: str
    invocation_parent_id: str | None = None
    collection_errors: int = 0
    execution_kind: str = "in_process_callable"


@dataclass(frozen=True, slots=True)
class RepairReview:
    """A review claim bound to the tested code and result versions."""

    author_id: str
    reviewer_id: str
    tested_result_version: str
    reviewed_result_version: str
    unresolved_critical_findings: tuple[str, ...] = ()
    tested_code_sha256: str | None = None
    reviewed_code_sha256: str | None = None
    status: str = "approved"

    def __post_init__(self) -> None:
        if not isinstance(self.unresolved_critical_findings, tuple):
            raise RepairContractError("repair_review_findings_invalid")


@dataclass(frozen=True, slots=True)
class RepairReviewContext:
    """Exact host-built artifact identity sent to an independent reviewer."""

    job_id: str
    attempt_id: str
    result_version: str
    author_id: str
    reviewer_id: str
    reviewed_output_sha256: str
    code_sha256: str
    patch_sha256: str
    test_ledger_sha256: str


@dataclass(frozen=True, slots=True)
class RepairReviewEvidence:
    """Host-issued reviewer execution evidence, never accepted from a request."""

    host_receipt_id: str
    attempt_id: str
    result_version: str
    author_id: str
    reviewer_id: str
    reviewed_output_sha256: str
    code_sha256: str
    patch_sha256: str
    test_ledger_sha256: str
    approved: bool
    unresolved_critical_findings: tuple[str, ...]
    reviewer_session_key: str | None = None
    reviewer_turn_id: str | None = None
    reviewer_output_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.unresolved_critical_findings, tuple):
            raise RepairContractError("repair_review_findings_invalid")


@dataclass(frozen=True, slots=True)
class TestResultRecord:
    """An immutable outcome recorded for one semantic case."""

    semantic_case_id: str
    outcome: str
    code_sha256: str
    receipt_id: str | None = None
    receipt_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class FullRegressionRecord:
    """The complete, code-hash-bound result set from one suite run."""

    run_id: str
    token: str
    code_sha256: str
    cases: tuple[tuple[str, str], ...]
    results: Mapping[str, str]
    exit_code: int
    collected: int
    errors: int
    skips: int
    collection_errors: int
    invocation_parent_id: str | None
    receipt_id: str | None = None
    receipt_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class FullRegressionEvidence:
    """Result details for one top-level local pytest run."""

    run_id: str
    token: str
    receipt_id: str | None
    code_sha256: str
    cases: tuple[tuple[str, str], ...]
    results: Mapping[str, str]
    exit_code: int
    collected: int
    errors: int
    skips: int
    collection_errors: int
    invocation_parent_id: str | None
    receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.cases, tuple):
            raise RepairContractError("full_regression_cases_invalid")
        if not isinstance(self.results, Mapping):
            raise RepairContractError("full_regression_results_invalid")
        if self.receipt_id is not None:
            _safe_identifier(self.receipt_id, "full_regression_receipt_id_invalid")
        object.__setattr__(self, "results", MappingProxyType(dict(self.results)))


def _freeze(value: object) -> object:
    """Recursively copy host context into read-only containers."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    """Convert frozen context back to JSON-compatible containers."""

    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class RepairHostEvidenceContext:
    """Store-verified predecessor and attempt identity for the host resolver."""

    job_id: str
    attempt_id: str
    base_commit: str
    approved_plan: Mapping[str, Any]
    attempts_root: str | Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.approved_plan, Mapping):
            raise RepairContractError("repair_approved_plan_invalid")
        object.__setattr__(self, "approved_plan", _freeze(self.approved_plan))


@dataclass(frozen=True, slots=True)
class RepairHostEvidence:
    """Trusted checkout, ownership, assertion, hash, and runner evidence."""

    changed_paths: tuple[str, ...]
    owned_paths: tuple[str, ...]
    baseline_assertions: Mapping[str, Mapping[str, str]]
    candidate_assertions: Mapping[str, Mapping[str, str]]
    test_ledger: RepairTestLedger
    current_code_sha256: str
    test_evidence: tuple[TestRunEvidence, ...]
    approved_cases: Mapping[str, str] | None = None
    job_id: str | None = None
    attempt_id: str | None = None
    base_commit: str | None = None
    plan_attempt_id: str | None = None
    plan_result_version: str | None = None
    plan_output_sha256: str | None = None
    checkout_root: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.changed_paths, tuple) or not isinstance(
            self.owned_paths, tuple
        ):
            raise RepairContractError("repair_host_paths_invalid")
        if not isinstance(self.baseline_assertions, Mapping) or not isinstance(
            self.candidate_assertions, Mapping
        ):
            raise RepairContractError("assertion_snapshot_invalid")
        if not isinstance(self.test_evidence, tuple):
            raise RepairContractError("test_evidence_invalid")
        object.__setattr__(
            self, "baseline_assertions", _freeze(self.baseline_assertions)
        )
        object.__setattr__(
            self, "candidate_assertions", _freeze(self.candidate_assertions)
        )
        if self.approved_cases is not None:
            if not isinstance(self.approved_cases, Mapping) or not self.approved_cases:
                raise RepairContractError("repair_approved_cases_invalid")
            object.__setattr__(self, "approved_cases", _freeze(self.approved_cases))


@dataclass(frozen=True, slots=True)
class _FrozenCase:
    semantic_case_id: str
    node: str
    semantic_signature: str


@dataclass(frozen=True, slots=True)
class RepairAttemptRequest:
    """Caller inputs for one sequence-3 attempt, excluding trusted host evidence."""

    context: Mapping[str, Any]
    attempts_root: str | Path
    output_root: str | Path
    lease: Any
    result_version: str
    completed_at: Any
    reviewed_at: Any
    review: RepairReview


@dataclass(frozen=True, slots=True)
class RepairAttemptOutcome:
    """Persisted artifacts and the final gate for one repair attempt."""

    attempt_id: str
    result_version: str
    code_sha256: str
    review_status: str
    output_path: str
    output_sha256: str
    artifacts: Mapping[str, tuple[str, str]]


@lru_cache(maxsize=8)
def _schema_validator(name: str) -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RepairContractError("repair_schema_unavailable") from exc
    return Draft202012Validator(schema)


def _validate_schema(name: str, value: Mapping[str, Any], code: str) -> None:
    try:
        _schema_validator(name).validate(dict(value))
    except (ValidationError, TypeError, ValueError) as exc:
        raise RepairContractError(code) from exc


def _safe_identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise RepairContractError(code)
    return value


def _safe_sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RepairContractError(code)
    return value


def _normalize_repo_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RepairContractError("patch_path_invalid")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
        posix = PurePosixPath(normalized)
    if (
        not normalized
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise RepairContractError("patch_path_invalid")
    return posix.as_posix()


def _path_is_owned(path: str, owner: str) -> bool:
    return path == owner or path.startswith(owner.rstrip("/") + "/")


def _is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    filename = parts[-1].lower() if parts else ""
    return (
        filename.startswith("test_")
        or filename.endswith("_test.py")
        or any(part in {"tests", "tests_inago"} for part in parts[:-1])
    )


def _normalized_expression(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairContractError("assertion_expression_invalid")
    try:
        tree = ast.parse(value, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise RepairContractError("assertion_expression_invalid") from exc
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def _normalize_assertion_snapshot(
    value: object,
) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping):
        raise RepairContractError("assertion_snapshot_invalid")
    normalized: dict[str, dict[str, str]] = {}
    for raw_path, raw_assertions in value.items():
        path = _normalize_repo_path(raw_path)
        if not isinstance(raw_assertions, Mapping):
            raise RepairContractError("assertion_snapshot_invalid")
        assertions: dict[str, str] = {}
        for raw_id, expression in raw_assertions.items():
            assertion_id = _safe_identifier(raw_id, "assertion_id_invalid")
            if assertion_id in assertions:
                raise RepairContractError("assertion_id_duplicate")
            assertions[assertion_id] = _normalized_expression(expression)
        if path in normalized:
            raise RepairContractError("assertion_path_duplicate")
        normalized[path] = assertions
    return normalized


def _assertion_snapshot_hash(
    baseline: Mapping[str, Mapping[str, str]],
    candidate: Mapping[str, Mapping[str, str]],
) -> str:
    encoded = json.dumps(
        {"baseline": baseline, "candidate": candidate},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_node(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RepairContractError("test_node_invalid")
    path_part, separator, test_part = value.partition("::")
    if not separator or not test_part:
        raise RepairContractError("test_node_invalid")
    path = _normalize_repo_path(path_part)
    if not path.lower().endswith(".py") or any(
        character.isspace() for character in path
    ):
        raise RepairContractError("test_node_invalid")
    if any(character in test_part for character in "\r\n"):
        raise RepairContractError("test_node_invalid")
    return f"{path}::{test_part}"


class RepairController:
    """Validate ownership, test evidence, and independent repair reviews."""

    def __init__(
        self,
        *,
        identity_resolver: Callable[[str], object] | None = None,
    ) -> None:
        self._identity_resolver = identity_resolver

    def assess_patch(
        self,
        *,
        changed_paths: tuple[str, ...],
        owned_paths: tuple[str, ...],
        baseline_assertions: Mapping[str, Mapping[str, str]],
        candidate_assertions: Mapping[str, Mapping[str, str]],
    ) -> PatchAssessment:
        """Reject unowned paths and any removed or altered assertion contract."""

        try:
            changed = tuple(_normalize_repo_path(path) for path in changed_paths)
            owners = tuple(_normalize_repo_path(path) for path in owned_paths)
            if len(changed) != len(set(changed)) or not owners:
                raise RepairContractError("patch_path_set_invalid")
            before = _normalize_assertion_snapshot(baseline_assertions)
            after = _normalize_assertion_snapshot(candidate_assertions)
            _validate_schema(
                "maintenance-repair-patch.schema.json",
                {
                    "changed_paths": list(changed),
                    "owned_paths": list(owners),
                    "baseline_assertions": before,
                    "candidate_assertions": after,
                },
                "patch_assessment_invalid",
            )
        except RepairContractError as exc:
            return PatchAssessment(False, (exc.code,), ())
        except (TypeError, ValueError):
            return PatchAssessment(False, ("patch_assessment_invalid",), ())

        reasons: list[str] = []
        if any(
            not any(_path_is_owned(path, owner) for owner in owners) for path in changed
        ):
            reasons.append("patch_contains_unowned_path")

        changed_test_paths = {path for path in changed if _is_test_path(path)}
        if any(path not in before or path not in after for path in changed_test_paths):
            reasons.append("assertion_evidence_missing")

        for path, assertions in before.items():
            candidate = after.get(path, {})
            for assertion_id, expression in assertions.items():
                candidate_expression = candidate.get(assertion_id)
                if candidate_expression is None:
                    reasons.append("assertion_removed")
                elif candidate_expression != expression:
                    reasons.append("assertion_expression_weakened_or_changed")
            has_execution_baseline = "execution-module" in assertions
            for execution_id, expression in assertions.items():
                if not execution_id.startswith("execution-"):
                    continue
                if candidate.get(execution_id) != expression:
                    reasons.append("test_execution_control_weakened_or_changed")
            if has_execution_baseline:
                for execution_id in candidate:
                    if (
                        execution_id.startswith("execution-")
                        and execution_id not in assertions
                        and not _is_new_test_execution_key(execution_id)
                    ):
                        reasons.append("test_execution_scope_added")
            before_skips = Counter(
                value for value in assertions.values() if _is_skip_signature(value)
            )
            candidate_skips = Counter(
                value for value in candidate.values() if _is_skip_signature(value)
            )
            if candidate_skips - before_skips:
                reasons.append("test_skip_added")
            before_exception_expectations = Counter(
                value
                for value in assertions.values()
                if _is_exception_expectation_signature(value)
            )
            candidate_exception_expectations = Counter(
                value
                for value in candidate.values()
                if _is_exception_expectation_signature(value)
            )
            if candidate_exception_expectations - before_exception_expectations:
                reasons.append("test_exception_expectation_added")

        return PatchAssessment(
            accepted=not reasons,
            reason_codes=tuple(sorted(set(reasons))),
            changed_paths=changed,
            assertion_snapshot_sha256=_assertion_snapshot_hash(before, after),
        )

    def classify_test_evidence(self, evidence: TestRunEvidence) -> str:
        """Classify only the exact C34 assertion failure as meaningful RED."""

        if not isinstance(evidence, TestRunEvidence):
            return "environment_error"
        payload = {
            "node": evidence.node,
            "outcome": evidence.outcome,
            "exit_code": evidence.exit_code,
            "collected": evidence.collected,
            "assertion_failures": evidence.assertion_failures,
            "errors": evidence.errors,
            "code_sha256": evidence.code_sha256,
            "semantic_case_id": evidence.semantic_case_id,
            "invocation_parent_id": evidence.invocation_parent_id,
            "collection_errors": evidence.collection_errors,
            "execution_kind": evidence.execution_kind,
        }
        try:
            _validate_schema(
                "maintenance-repair-test-evidence.schema.json",
                payload,
                "test_evidence_invalid",
            )
            node = _normalize_node(evidence.node)
        except RepairContractError:
            return "environment_error"
        if (
            node != _C34_NODE
            or evidence.semantic_case_id != "C34"
            or evidence.invocation_parent_id is not None
            or evidence.execution_kind != "in_process_callable"
            or evidence.collected != 1
            or evidence.errors != 0
            or evidence.collection_errors != 0
        ):
            return "environment_error"
        if (
            evidence.outcome == "FAIL"
            and evidence.exit_code == 1
            and evidence.assertion_failures == 1
        ):
            return "meaningful_red"
        if (
            evidence.outcome == "PASS"
            and evidence.exit_code == 0
            and evidence.assertion_failures == 0
        ):
            return "passed"
        return "environment_error"

    def validate_review(self, review: RepairReview) -> bool:
        """Require host-verified read-only independence and exact version binding."""

        if not isinstance(review, RepairReview) or self._identity_resolver is None:
            return False
        payload = {
            "author_id": review.author_id,
            "reviewer_id": review.reviewer_id,
            "tested_result_version": review.tested_result_version,
            "reviewed_result_version": review.reviewed_result_version,
            "unresolved_critical_findings": list(review.unresolved_critical_findings),
            "tested_code_sha256": review.tested_code_sha256,
            "reviewed_code_sha256": review.reviewed_code_sha256,
            "status": review.status,
        }
        try:
            _validate_schema(
                "maintenance-repair-review-claim.schema.json",
                payload,
                "repair_review_invalid",
            )
            author_role = self._identity_resolver(review.author_id)
            reviewer_role = self._identity_resolver(review.reviewer_id)
        except Exception:
            return False
        return bool(
            review.author_id != review.reviewer_id
            and author_role == "implementer"
            and reviewer_role == "read_only_reviewer"
            and review.tested_result_version == review.reviewed_result_version
            and (
                review.tested_code_sha256 is None
                and review.reviewed_code_sha256 is None
                or review.tested_code_sha256 == review.reviewed_code_sha256
                and _SHA256.fullmatch(review.tested_code_sha256 or "") is not None
            )
            and review.status
            in {"approved", "changes_requested", "rejected", "unavailable"}
        )


class RepairTestLedger:
    """In-memory, immutable-identity ledger for one isolated repair campaign."""

    __slots__ = (
        "_campaign_id",
        "_required_cases",
        "_frozen_cases",
        "_frozen_receipts",
        "_code_sha256",
        "_invocation_parent_id",
        "_semantic_signatures",
        "_results",
        "_full_regression_started",
        "_full_regression_token",
        "_full_regression_snapshot",
        "_full_regression_record",
        "_store",
        "_job_id",
        "_attempt_id",
        "_created_at",
        "_pending_nonce",
        "_lock",
    )

    def __init__(
        self,
        *,
        campaign_id: str,
        required_cases: Mapping[str, str],
        code_sha256: str,
        invocation_parent_id: str | None,
        semantic_signatures: Mapping[str, str] | None = None,
        _store: Any = None,
        _job_id: str | None = None,
        _attempt_id: str | None = None,
        _created_at: datetime | str | None = None,
    ) -> None:
        self._campaign_id = _safe_identifier(campaign_id, "campaign_id_invalid")
        self._code_sha256 = _safe_sha256(code_sha256, "campaign_code_hash_invalid")
        if invocation_parent_id is not None:
            _safe_identifier(invocation_parent_id, "invocation_parent_id_invalid")
        self._invocation_parent_id = invocation_parent_id
        if not isinstance(required_cases, Mapping) or not required_cases:
            raise RepairContractError("required_case_set_invalid")
        cases: dict[str, str] = {}
        for raw_case_id, raw_node in required_cases.items():
            case_id = _safe_identifier(raw_case_id, "semantic_case_id_invalid")
            if case_id in cases:
                raise RepairContractError("semantic_case_id_duplicate")
            cases[case_id] = _normalize_node(raw_node)
        if len(set(cases.values())) != len(cases):
            raise RepairContractError("semantic_case_node_duplicate")
        if not isinstance(semantic_signatures, Mapping):
            raise RepairContractError("semantic_signatures_required")
        if set(semantic_signatures) != set(cases):
            raise RepairContractError("semantic_signature_case_set_invalid")
        signatures: dict[str, str] = {}
        for case_id, signature in semantic_signatures.items():
            if case_id not in cases:
                raise RepairContractError("semantic_signature_case_unknown")
            if not isinstance(signature, str) or not signature.strip():
                raise RepairContractError("semantic_signature_invalid")
            signatures[case_id] = " ".join(signature.split())
        if len(set(signatures.values())) != len(signatures):
            raise RepairContractError("semantic_signature_duplicate")

        self._required_cases = cases
        self._frozen_cases: dict[str, _FrozenCase] = {}
        self._frozen_receipts: dict[str, Mapping[str, object]] = {}
        self._semantic_signatures = signatures
        self._results: dict[str, list[TestResultRecord]] = {
            case_id: [] for case_id in cases
        }
        self._full_regression_started = False
        self._full_regression_token: str | None = None
        self._full_regression_snapshot: tuple[tuple[str, str], ...] | None = None
        self._full_regression_record: FullRegressionRecord | None = None
        self._store = _store
        self._job_id = _job_id
        self._attempt_id = _attempt_id
        self._created_at = _created_at
        self._pending_nonce: str | None = None
        self._lock = threading.RLock()

    @classmethod
    def create_in_store(
        cls,
        *,
        store: Any,
        campaign_id: str,
        job_id: str,
        attempt_id: str,
        code_sha256: str,
        required_cases: Mapping[str, str],
        semantic_signatures: Mapping[str, str],
        invocation_parent_id: str | None,
        created_at: datetime | str,
    ) -> RepairTestLedger:
        """Create or recover a campaign in Store and return its ledger view."""

        provisional = cls(
            campaign_id=campaign_id,
            required_cases=required_cases,
            semantic_signatures=semantic_signatures,
            code_sha256=code_sha256,
            invocation_parent_id=invocation_parent_id,
            _store=store,
            _job_id=job_id,
            _attempt_id=attempt_id,
            _created_at=created_at,
        )
        try:
            store.create_or_load_repair_campaign(
                campaign_id=provisional.campaign_id,
                job_id=job_id,
                attempt_id=attempt_id,
                code_sha256=provisional.code_sha256,
                invocation_parent_id=invocation_parent_id,
                required_cases=dict(provisional.required_cases),
                semantic_signatures=dict(provisional.semantic_signatures),
                created_at=created_at,
            )
            return cls.from_store(store=store, campaign_id=campaign_id)
        except Exception as exc:
            raise RepairContractError(
                getattr(exc, "code", "repair_campaign_persistence_failed")
            ) from exc

    @classmethod
    def from_store(cls, *, store: Any, campaign_id: str) -> RepairTestLedger:
        """Rebuild the complete campaign state from durable Store records."""

        try:
            snapshot = store.load_repair_campaign(campaign_id=campaign_id)
        except Exception as exc:
            raise RepairContractError(
                getattr(exc, "code", "repair_campaign_unavailable")
            ) from exc
        case_rows = tuple(snapshot["cases"])
        frozen_proofs = snapshot.get("frozen_proofs")
        if not isinstance(frozen_proofs, Mapping):
            raise RepairContractError("repair_campaign_frozen_proofs_invalid")
        proof_data = snapshot.get("full_regression")
        proof_ids = (
            {case_id for case_id, _node in proof_data["cases"]}
            if str(snapshot["state"]) == "full_regression_recorded"
            and isinstance(proof_data, Mapping)
            else set()
        )
        active_rows = [
            row
            for row in case_rows
            if not row["frozen"] or row["semantic_case_id"] in proof_ids
        ]
        required = {row["semantic_case_id"]: row["node"] for row in active_rows}
        if not required:
            # A recorded full run may have frozen every case. The constructor
            # still requires a non-empty active set, so retain one frozen case
            # internally; retry and completion APIs filter it by frozen state.
            if not case_rows:
                raise RepairContractError("repair_campaign_cases_invalid")
            required = {case_rows[0]["semantic_case_id"]: case_rows[0]["node"]}
        signatures = {
            row["semantic_case_id"]: row["semantic_signature"] for row in active_rows
        }
        if not signatures:
            signatures = {
                required_id: next(
                    row["semantic_signature"]
                    for row in case_rows
                    if row["semantic_case_id"] == required_id
                )
                for required_id in required
            }
        ledger = cls(
            campaign_id=str(snapshot["campaign_id"]),
            required_cases=required,
            semantic_signatures=signatures,
            code_sha256=str(snapshot["code_sha256"]),
            invocation_parent_id=snapshot["invocation_parent_id"],
            _store=store,
            _job_id=str(snapshot["job_id"]),
            _attempt_id=str(snapshot["attempt_id"]),
        )
        for row in case_rows:
            if row["frozen"]:
                ledger._frozen_cases[row["semantic_case_id"]] = _FrozenCase(
                    row["semantic_case_id"],
                    row["node"],
                    row["semantic_signature"],
                )
                frozen_proof = frozen_proofs.get(row["semantic_case_id"])
                if not isinstance(frozen_proof, Mapping):
                    raise RepairContractError("repair_campaign_frozen_proof_missing")
                if (
                    frozen_proof.get("semantic_case_id") != row["semantic_case_id"]
                    or frozen_proof.get("node") != row["node"]
                    or frozen_proof.get("semantic_signature")
                    != row["semantic_signature"]
                    or frozen_proof.get("outcome") != "PASS"
                    or _SHA256.fullmatch(str(frozen_proof.get("code_sha256", "")))
                    is None
                    or _SHA256.fullmatch(str(frozen_proof.get("receipt_sha256", "")))
                    is None
                ):
                    raise RepairContractError("repair_campaign_frozen_proof_corrupt")
                ledger._frozen_receipts[row["semantic_case_id"]] = MappingProxyType(
                    dict(frozen_proof)
                )
                if row["semantic_case_id"] not in proof_ids:
                    ledger._required_cases.pop(row["semantic_case_id"], None)
                    ledger._semantic_signatures.pop(row["semantic_case_id"], None)
                    ledger._results.pop(row["semantic_case_id"], None)
            elif row["semantic_case_id"] not in ledger._required_cases:
                ledger._required_cases[row["semantic_case_id"]] = row["node"]
                ledger._semantic_signatures[row["semantic_case_id"]] = row[
                    "semantic_signature"
                ]
                ledger._results[row["semantic_case_id"]] = []
        receipt_rows = {
            str(receipt["receipt_id"]): receipt
            for receipt in snapshot.get("run_receipts", ())
        }
        for result in snapshot["results"]:
            case_id = str(result["semantic_case_id"])
            if case_id in ledger._results:
                receipt = receipt_rows.get(str(result["receipt_id"]))
                if (
                    receipt is None
                    or receipt["campaign_id"] != ledger._campaign_id
                    or receipt["attempt_id"] != ledger._attempt_id
                    or receipt["code_sha256"] != result["code_sha256"]
                    or receipt["results"].get(case_id) != result["outcome"]
                    or not receipt["consumed"]
                ):
                    raise RepairContractError("repair_campaign_receipt_corrupt")
                ledger._results[case_id].append(
                    TestResultRecord(
                        case_id,
                        str(result["outcome"]),
                        str(result["code_sha256"]),
                        receipt_id=str(result["receipt_id"]),
                        receipt_sha256=str(receipt["receipt_sha256"]),
                    )
                )
        state = str(snapshot["state"])
        ledger._full_regression_started = state != "active"
        if snapshot.get("full_snapshot") is not None:
            ledger._full_regression_snapshot = tuple(snapshot["full_snapshot"])
        if snapshot.get("full_regression_token_hash") is not None:
            ledger._full_regression_token = str(snapshot["full_regression_token_hash"])
        if state == "full_regression_recorded" and isinstance(proof_data, Mapping):
            cases = tuple(proof_data["cases"])
            results = dict(proof_data["results"])
            if (
                proof_data.get("receipt_id") != snapshot.get("full_run_receipt_id")
                or proof_data.get("token_hash")
                != snapshot.get("full_regression_token_hash")
                or proof_data.get("campaign_id") != ledger._campaign_id
                or proof_data.get("attempt_id") != ledger._attempt_id
                or proof_data.get("code_sha256") != ledger._code_sha256
                or cases != ledger._full_regression_snapshot
                or set(results) != {case_id for case_id, _node in cases}
                or any(
                    value
                    not in {"PASS", "FAIL", "ERROR", "SKIP", "NOT_RUN", "UNVERIFIED"}
                    for value in results.values()
                )
                or not isinstance(proof_data.get("exit_code"), int)
                or not isinstance(proof_data.get("collected"), int)
                or not isinstance(proof_data.get("errors"), int)
                or not isinstance(proof_data.get("skips"), int)
                or not isinstance(proof_data.get("collection_errors"), int)
                or proof_data.get("invocation_parent_id") is not None
                or not proof_data.get("consumed")
            ):
                raise RepairContractError("repair_campaign_receipt_corrupt")
            ledger._full_regression_snapshot = cases
            ledger._full_regression_record = FullRegressionRecord(
                run_id=str(proof_data["receipt_id"]),
                token=str(snapshot.get("full_regression_token_hash") or ""),
                code_sha256=str(proof_data["code_sha256"]),
                cases=cases,
                results=MappingProxyType(results),
                exit_code=int(proof_data["exit_code"]),
                collected=int(proof_data["collected"]),
                errors=int(proof_data["errors"]),
                skips=int(proof_data["skips"]),
                collection_errors=int(proof_data["collection_errors"]),
                invocation_parent_id=proof_data["invocation_parent_id"],
                receipt_id=str(proof_data["receipt_id"]),
                receipt_sha256=str(proof_data["receipt_sha256"]),
            )
        return ledger

    @property
    def campaign_id(self) -> str:
        return self._campaign_id

    @property
    def job_id(self) -> str | None:
        return self._job_id

    @property
    def attempt_id(self) -> str | None:
        return self._attempt_id

    @property
    def required_cases(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._required_cases))

    @property
    def semantic_signatures(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._semantic_signatures))

    @property
    def frozen_cases(self) -> Mapping[str, _FrozenCase]:
        return MappingProxyType(dict(self._frozen_cases))

    @property
    def frozen_pass_receipts(self) -> Mapping[str, Mapping[str, object]]:
        return MappingProxyType(dict(self._frozen_receipts))

    @property
    def full_regression_cases(self) -> tuple[tuple[str, str], ...] | None:
        return self._full_regression_snapshot

    @property
    def code_sha256(self) -> str:
        return self._code_sha256

    @property
    def invocation_parent_id(self) -> str | None:
        """Immutable host-supplied ancestry; nested full-suite calls are rejected."""

        return self._invocation_parent_id

    @property
    def full_regression_record(self) -> FullRegressionRecord | None:
        return self._full_regression_record

    def record_result(
        self,
        semantic_case_id: str,
        *,
        outcome: str,
        code_sha256: str,
    ) -> TestResultRecord:
        case_id = _safe_identifier(semantic_case_id, "semantic_case_id_invalid")
        code_hash = _safe_sha256(code_sha256, "test_result_code_hash_invalid")
        if not isinstance(outcome, str) or not outcome.strip():
            raise RepairContractError("test_result_status_invalid")
        with self._lock:
            if case_id not in self._required_cases:
                raise RepairContractError("test_case_not_registered")
            if code_hash != self._code_sha256:
                raise RepairContractError("test_result_code_hash_mismatch")
            if self._full_regression_started:
                raise RepairContractError("test_result_after_full_regression_started")
            history = self._results[case_id]
            if history and history[-1].outcome == "PASS":
                raise RepairContractError("test_pass_frozen")
            record = TestResultRecord(case_id, outcome, code_hash)
            history.append(record)
            if outcome == "PASS":
                self._frozen_cases[case_id] = _FrozenCase(
                    case_id,
                    self._required_cases[case_id],
                    self._semantic_signatures[case_id],
                )
            return record

    def record_case_receipt(self, receipt: Any) -> TestResultRecord:
        """Consume one persisted host-runner receipt into this campaign."""

        if self._store is None:
            raise RepairContractError("host_run_receipt_required")
        if receipt is None or getattr(receipt, "run_kind", None) != "case":
            raise RepairContractError("host_run_receipt_unavailable")
        case_id = getattr(receipt, "semantic_case_id", None)
        try:
            case_id = _safe_identifier(case_id, "semantic_case_id_invalid")
            if (
                case_id not in self._required_cases
                or receipt.code_sha256 != self._code_sha256
                or self._full_regression_started
            ):
                raise RepairContractError("host_run_receipt_mismatch")
            result = receipt.results[case_id]
            self._store.consume_repair_case_receipt(
                receipt_id=receipt.receipt_id,
                expected={
                    "campaign_id": self._campaign_id,
                    "attempt_id": self._attempt_id,
                    "semantic_case_id": case_id,
                    "invocation_id": receipt.invocation_id,
                    "code_sha256": receipt.code_sha256,
                    "cases": receipt.cases,
                    "results": dict(receipt.results),
                    "exit_code": receipt.exit_code,
                    "collected": receipt.collected,
                    "assertion_failures": receipt.assertion_failures,
                    "errors": receipt.errors,
                    "skips": receipt.skips,
                    "collection_errors": receipt.collection_errors,
                    "invocation_parent_id": receipt.invocation_parent_id,
                    "receipt_sha256": receipt.receipt_sha256,
                },
                consumed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            code = getattr(exc, "code", "host_run_receipt_mismatch")
            if code == "host_run_receipt_mismatch":
                code = "full_regression_receipt_mismatch"
            raise RepairContractError(code) from exc
        record = TestResultRecord(
            case_id,
            result,
            receipt.code_sha256,
            receipt_id=receipt.receipt_id,
            receipt_sha256=receipt.receipt_sha256,
        )
        self._results[case_id].append(record)
        if result == "PASS":
            self._frozen_cases[case_id] = _FrozenCase(
                case_id,
                self._required_cases[case_id],
                self._semantic_signatures[case_id],
            )
            self._frozen_receipts[case_id] = MappingProxyType(
                {
                    "semantic_case_id": case_id,
                    "node": self._required_cases[case_id],
                    "semantic_signature": self._semantic_signatures[case_id],
                    "campaign_id": self._campaign_id,
                    "attempt_id": self._attempt_id,
                    "code_sha256": receipt.code_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.receipt_sha256,
                    "outcome": "PASS",
                }
            )
        return record

    def select_retry_nodes(self) -> tuple[str, ...]:
        """Return only new, NOT_RUN, and FAIL nodes; invalid statuses stop closed."""

        with self._lock:
            for history in self._results.values():
                if any(
                    record.outcome not in _TEST_STATUSES
                    or record.outcome in _INVALID_CAMPAIGN_STATUSES
                    for record in history
                ):
                    raise RepairContractError("test_ledger_status_not_actionable")
            selected: list[str] = []
            regression_results = (
                self._full_regression_record.results
                if self._full_regression_record is not None
                else {}
            )
            for case_id, node in self._required_cases.items():
                if case_id in self._frozen_cases:
                    continue
                history = self._results[case_id]
                # A case that passed the consumed full regression stays frozen
                # even if its proof's code hash later differs from the candidate.
                outcome = regression_results.get(case_id)
                if outcome is None and history:
                    outcome = history[-1].outcome
                if outcome is None or outcome in _ELIGIBLE_STATUSES:
                    selected.append(node)
            return tuple(selected)

    def begin_full_regression(self) -> str:
        """Consume the campaign's single top-level full-regression permission."""

        with self._lock:
            if self._invocation_parent_id is not None:
                raise RepairContractError("nested_full_regression_rejected")
            if self._full_regression_started:
                raise RepairContractError("full_regression_token_already_used")
            # A bad or unrecognized status must never be hidden by a full-suite run.
            for history in self._results.values():
                if any(
                    record.outcome not in {"PASS", "FAIL", "NOT_RUN"}
                    for record in history
                ):
                    raise RepairContractError("test_ledger_status_not_actionable")
            if self._store is not None:
                try:
                    started = self._store.begin_repair_full_regression(
                        campaign_id=self._campaign_id,
                        code_sha256=self._code_sha256,
                        invocation_parent_id=self._invocation_parent_id,
                        started_at=datetime.now(timezone.utc),
                    )
                except Exception as exc:
                    raise RepairContractError(
                        getattr(exc, "code", "full_regression_token_invalid")
                    ) from exc
                token = str(started["token"])
                self._pending_nonce = (
                    None if started["nonce"] is None else str(started["nonce"])
                )
                self._full_regression_started = True
                self._full_regression_token = token
                self._full_regression_snapshot = tuple(started["cases"])
                return token
            token = uuid.uuid4().hex
            # The final regression validates every registered case against the
            # current code, including cases with an earlier individual PASS.
            snapshot = tuple(self._all_campaign_cases().items())
            if not snapshot:
                raise RepairContractError("repair_campaign_cases_invalid")
            self._full_regression_started = True
            self._full_regression_token = token
            self._full_regression_snapshot = snapshot
            return token

    def record_full_regression(
        self,
        evidence: FullRegressionEvidence,
    ) -> FullRegressionRecord:
        """Bind a host-observed run to its one-use token and exact campaign."""

        if not isinstance(evidence, FullRegressionEvidence):
            raise RepairContractError("full_regression_evidence_invalid")
        with self._lock:
            if (
                not self._full_regression_started
                or self._full_regression_token is None
                or self._full_regression_snapshot is None
                or (self._full_regression_record is not None and self._store is None)
            ):
                raise RepairContractError("full_regression_token_invalid")
            run_id = _safe_identifier(evidence.run_id, "full_regression_run_id_invalid")
            token = _safe_identifier(evidence.token, "full_regression_token_invalid")
            code_hash = _safe_sha256(
                evidence.code_sha256, "regression_code_hash_invalid"
            )
            if (
                self._full_regression_token is not None
                and token != self._full_regression_token
            ):
                raise RepairContractError(
                    "full_regression_receipt_mismatch"
                    if self._store is not None
                    else "full_regression_token_invalid"
                )
            if code_hash != self._code_sha256:
                raise RepairContractError(
                    "full_regression_receipt_mismatch"
                    if self._store is not None
                    else "full_regression_code_hash_mismatch"
                )
            if evidence.cases != self._full_regression_snapshot:
                raise RepairContractError(
                    "full_regression_receipt_mismatch"
                    if self._store is not None
                    else "full_regression_case_set_mismatch"
                )
            if evidence.invocation_parent_id is not None:
                raise RepairContractError("nested_full_regression_rejected")
            if (
                type(evidence.exit_code) is not int
                or not 0 <= evidence.exit_code <= 255
                or type(evidence.collected) is not int
                or evidence.collected < 0
                or type(evidence.errors) is not int
                or evidence.errors < 0
                or type(evidence.skips) is not int
                or evidence.skips < 0
                or type(evidence.collection_errors) is not int
                or evidence.collection_errors < 0
            ):
                raise RepairContractError("full_regression_result_invalid")
            expected_ids = {case_id for case_id, _ in self._full_regression_snapshot}
            if not expected_ids:
                raise RepairContractError("repair_campaign_cases_invalid")
            results = dict(evidence.results)
            if set(results) != expected_ids or any(
                outcome
                not in {"PASS", "FAIL", "ERROR", "SKIP", "NOT_RUN", "UNVERIFIED"}
                for outcome in results.values()
            ):
                raise RepairContractError("full_regression_results_invalid")
            if self._store is not None:
                if evidence.receipt_id is None:
                    raise RepairContractError("host_run_receipt_unavailable")
                try:
                    self._store.consume_repair_run_receipt(
                        receipt_id=evidence.receipt_id,
                        expected={
                            "receipt_id": evidence.receipt_id,
                            "run_id": evidence.run_id,
                            "token": evidence.token,
                            "campaign_id": self._campaign_id,
                            "attempt_id": self._attempt_id,
                            "code_sha256": evidence.code_sha256,
                            "cases": evidence.cases,
                            "results": dict(evidence.results),
                            "exit_code": evidence.exit_code,
                            "collected": evidence.collected,
                            "assertion_failures": sum(
                                outcome == "FAIL" for outcome in results.values()
                            ),
                            "errors": evidence.errors,
                            "skips": evidence.skips,
                            "collection_errors": evidence.collection_errors,
                            "invocation_parent_id": evidence.invocation_parent_id,
                            "receipt_sha256": evidence.receipt_sha256,
                        },
                        consumed_at=datetime.now(timezone.utc),
                    )
                except Exception as exc:
                    code = getattr(exc, "code", "host_run_receipt_mismatch")
                    if code == "host_run_receipt_mismatch":
                        code = "full_regression_receipt_mismatch"
                    raise RepairContractError(code) from exc
            record = FullRegressionRecord(
                run_id=run_id,
                token=token,
                code_sha256=code_hash,
                cases=self._full_regression_snapshot,
                results=MappingProxyType(results),
                exit_code=evidence.exit_code,
                collected=evidence.collected,
                errors=evidence.errors,
                skips=evidence.skips,
                collection_errors=evidence.collection_errors,
                invocation_parent_id=evidence.invocation_parent_id,
                receipt_id=evidence.receipt_id,
                receipt_sha256=evidence.receipt_sha256,
            )
            self._full_regression_record = record
            for case_id, node in self._full_regression_snapshot:
                if results[case_id] == "PASS":
                    self._frozen_cases[case_id] = _FrozenCase(
                        case_id, node, self._semantic_signatures[case_id]
                    )
                if (
                    evidence.receipt_id is not None
                    and evidence.results[case_id] == "PASS"
                ):
                    self._frozen_receipts[case_id] = MappingProxyType(
                        {
                            "semantic_case_id": case_id,
                            "node": node,
                            "semantic_signature": self._semantic_signatures[case_id],
                            "campaign_id": self._campaign_id,
                            "attempt_id": self._attempt_id,
                            "code_sha256": code_hash,
                            "receipt_id": evidence.receipt_id,
                            "receipt_sha256": evidence.receipt_sha256,
                            "outcome": "PASS",
                        }
                    )
            return record

    def add_case(
        self,
        semantic_case_id: str,
        node: str,
        *,
        semantic_signature: str | None = None,
    ) -> None:
        """Register a genuinely new semantic case without reusing a frozen ID."""

        case_id = _safe_identifier(semantic_case_id, "semantic_case_id_invalid")
        normalized_node = _normalize_node(node)
        if not isinstance(semantic_signature, str) or not semantic_signature.strip():
            raise RepairContractError("semantic_signature_invalid")
        signature = " ".join(semantic_signature.split())
        with self._lock:
            if self._full_regression_started:
                raise RepairContractError("case_added_after_full_regression_started")
            if case_id in self._required_cases or case_id in self._frozen_cases:
                raise RepairContractError("semantic_case_id_already_registered")
            if normalized_node in self._required_cases.values() or any(
                item.node == normalized_node for item in self._frozen_cases.values()
            ):
                raise RepairContractError("semantic_case_node_already_registered")
            if signature in self._semantic_signatures.values() or any(
                item.semantic_signature == signature
                for item in self._frozen_cases.values()
            ):
                raise RepairContractError("semantic_signature_already_registered")
            if self._store is not None:
                try:
                    self._store.add_repair_campaign_case(
                        campaign_id=self._campaign_id,
                        semantic_case_id=case_id,
                        node=normalized_node,
                        semantic_signature=signature,
                    )
                except Exception as exc:
                    raise RepairContractError(
                        getattr(exc, "code", "repair_campaign_case_invalid")
                    ) from exc
            self._required_cases[case_id] = normalized_node
            self._results[case_id] = []
            self._semantic_signatures[case_id] = signature

    def fork_campaign_with_case(
        self,
        *,
        campaign_id: str,
        code_sha256: str,
        semantic_case_id: str,
        node: str,
        semantic_signature: str,
    ) -> RepairTestLedger:
        """Start a code-changed campaign and rerun every inherited case."""

        new_campaign_id = _safe_identifier(campaign_id, "campaign_id_invalid")
        new_code_hash = _safe_sha256(code_sha256, "campaign_code_hash_invalid")
        new_case_id = _safe_identifier(semantic_case_id, "semantic_case_id_invalid")
        new_node = _normalize_node(node)
        if not isinstance(semantic_signature, str) or not semantic_signature.strip():
            raise RepairContractError("semantic_signature_invalid")
        new_signature = " ".join(semantic_signature.split())

        with self._lock:
            if self._store is not None:
                try:
                    self._store.fork_repair_campaign_with_case(
                        parent_campaign_id=self._campaign_id,
                        campaign_id=new_campaign_id,
                        code_sha256=new_code_hash,
                        semantic_case_id=new_case_id,
                        node=new_node,
                        semantic_signature=new_signature,
                        created_at=datetime.now(timezone.utc),
                    )
                    return type(self).from_store(
                        store=self._store, campaign_id=new_campaign_id
                    )
                except Exception as exc:
                    raise RepairContractError(
                        getattr(exc, "code", "repair_campaign_fork_failed")
                    ) from exc
            proof = self._full_regression_record
            if (
                proof is None
                or not self._full_regression_started
                or self._full_regression_token is None
            ):
                raise RepairContractError("full_regression_not_consumed")
            if new_campaign_id == self._campaign_id:
                raise RepairContractError("campaign_id_already_registered")
            if new_code_hash == proof.code_sha256:
                raise RepairContractError("campaign_code_hash_not_changed")
            if new_case_id in self._required_cases or new_case_id in self._frozen_cases:
                raise RepairContractError("semantic_case_id_already_registered")
            if new_node in self._required_cases.values() or any(
                item.node == new_node for item in self._frozen_cases.values()
            ):
                raise RepairContractError("semantic_case_node_already_registered")
            all_signatures = {
                **self._semantic_signatures,
                **{
                    case_id: item.semantic_signature
                    for case_id, item in self._frozen_cases.items()
                },
            }
            if new_signature in all_signatures.values():
                raise RepairContractError("semantic_signature_already_registered")

            # A code hash change invalidates every prior PASS for this new
            # campaign. Carry the case definitions, but require fresh results
            # against the candidate hash instead of carrying frozen receipts.
            retry_cases = self._all_campaign_cases()
            retry_signatures = {
                **self._semantic_signatures,
                **{
                    case_id: item.semantic_signature
                    for case_id, item in self._frozen_cases.items()
                },
            }
            retry_cases[new_case_id] = new_node
            retry_signatures[new_case_id] = new_signature
            child = RepairTestLedger(
                campaign_id=new_campaign_id,
                required_cases=retry_cases,
                semantic_signatures=retry_signatures,
                code_sha256=new_code_hash,
                invocation_parent_id=None,
            )
            return child

    def fork_after_failed_regression(
        self, *, campaign_id: str, code_sha256: str
    ) -> RepairTestLedger:
        """Resume a failed one-use full run in a fresh durable campaign."""

        if self._store is None:
            raise RepairContractError("repair_campaign_store_required")
        new_campaign_id = _safe_identifier(campaign_id, "campaign_id_invalid")
        new_code_hash = _safe_sha256(code_sha256, "campaign_code_hash_invalid")
        try:
            self._store.fork_failed_repair_campaign(
                parent_campaign_id=self._campaign_id,
                campaign_id=new_campaign_id,
                code_sha256=new_code_hash,
                created_at=datetime.now(timezone.utc),
            )
            return type(self).from_store(store=self._store, campaign_id=new_campaign_id)
        except Exception as exc:
            raise RepairContractError(
                getattr(exc, "code", "repair_campaign_fork_failed")
            ) from exc

    def abandon_interrupted_regression(self) -> None:
        """Close an expired campaign only after Store proves its runner is dead."""

        if self._store is None:
            raise RepairContractError("repair_campaign_store_required")
        try:
            self._store.abandon_interrupted_repair_full_regression(
                campaign_id=self._campaign_id,
                capability=self._store._repair_runner_capability,
                abandoned_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            raise RepairContractError(
                getattr(exc, "code", "full_regression_not_abandonable")
            ) from exc

    def completion_status(self, *, current_code_sha256: str) -> str:
        """Return ready only when the full-regression proof covers current code."""

        try:
            current_hash = _safe_sha256(
                current_code_sha256, "current_code_hash_invalid"
            )
        except RepairContractError:
            return "hold"
        with self._lock:
            if not self._frozen_receipts_match_code_hash_locked(current_hash):
                return "hold"
            if any(
                record.outcome not in {"PASS", "FAIL", "NOT_RUN"}
                for history in self._results.values()
                for record in history
            ):
                return "hold"
            proof = self._full_regression_record
            if (
                proof is None
                or proof.code_sha256 != current_hash
                or self._full_regression_snapshot is None
                or proof.cases != self._full_regression_snapshot
            ):
                return "hold"
            current_cases = self._all_campaign_cases()
            proof_cases = dict(proof.cases)
            if len(proof_cases) != len(proof.cases):
                return "hold"
            if proof_cases != {
                case_id: current_cases[case_id]
                for case_id in proof_cases
                if case_id in current_cases
            }:
                return "hold"
            if set(proof.results) != set(proof_cases) or any(
                status != "PASS" for status in proof.results.values()
            ):
                return "hold"
            frozen_ids = set(current_cases) - set(proof_cases)
            if not frozen_ids.issubset(self._frozen_receipts):
                return "hold"
            for case_id in frozen_ids:
                frozen = self._frozen_receipts[case_id]
                if (
                    frozen.get("semantic_case_id") != case_id
                    or frozen.get("node") != current_cases[case_id]
                    or frozen.get("outcome") != "PASS"
                    or frozen.get("code_sha256") != current_hash
                    or not frozen.get("receipt_id")
                    or _SHA256.fullmatch(str(frozen.get("receipt_sha256", ""))) is None
                ):
                    return "hold"
            if set(frozen_ids) | set(proof_cases) != set(current_cases):
                return "hold"
            return "ready"

    def frozen_receipts_match_code_hash(self, current_code_sha256: str) -> bool:
        """Check inherited PASS receipts against the current candidate hash."""

        try:
            current_hash = _safe_sha256(
                current_code_sha256, "current_code_hash_invalid"
            )
        except RepairContractError:
            return False
        with self._lock:
            return self._frozen_receipts_match_code_hash_locked(current_hash)

    def _frozen_receipts_match_code_hash_locked(self, current_hash: str) -> bool:
        return all(
            receipt.get("code_sha256") == current_hash
            for receipt in self._frozen_receipts.values()
        )

    def _all_campaign_cases(self) -> dict[str, str]:
        """Return every registered case, including frozen PASS cases, in stable order."""

        cases = dict(self._required_cases)
        for case_id, frozen in self._frozen_cases.items():
            node = frozen.node
            prior = cases.get(case_id)
            if prior is not None and prior != node:
                raise RepairContractError("repair_campaign_case_identity_conflict")
            cases[case_id] = node
        return cases

    def _non_frozen_campaign_cases(self) -> dict[str, str]:
        """Return the exact set eligible for this campaign's single full run."""

        return {
            case_id: node
            for case_id, node in self._all_campaign_cases().items()
            if case_id not in self._frozen_cases
        }

    def to_document(
        self,
        *,
        attempt_id: str,
        result_version: str,
        tested_code_sha256: str,
        identity: Mapping[str, object],
        test_evidence: tuple[TestRunEvidence, ...] = (),
    ) -> dict[str, Any]:
        """Return a schema-checked, content-free campaign evidence snapshot."""

        attempt = _safe_identifier(attempt_id, "attempt_id_invalid")
        version = _safe_identifier(result_version, "result_version_invalid")
        tested_code_hash = _safe_sha256(tested_code_sha256, "tested_code_hash_invalid")
        if not isinstance(identity, Mapping):
            raise RepairContractError("repair_identity_invalid")
        if any(not isinstance(item, TestRunEvidence) for item in test_evidence):
            raise RepairContractError("test_evidence_invalid")
        with self._lock:
            full_regression = self._full_regression_record
            document: dict[str, Any] = {
                "schema_version": "maintenance-repair-test-ledger-v1",
                "attestation_source": (
                    "local_pytest" if self._store is None else _ATTESTATION_SOURCE
                ),
                "identity": dict(identity),
                "attempt_id": attempt,
                "result_version": version,
                "campaign_id": self._campaign_id,
                "code_sha256": self._code_sha256,
                "tested_code_sha256": tested_code_hash,
                "invocation_parent_id": self._invocation_parent_id,
                "semantic_signatures": [
                    {
                        "semantic_case_id": case_id,
                        "signature": signature,
                    }
                    for case_id, signature in self._semantic_signatures.items()
                ],
                "frozen_cases": [
                    {
                        "semantic_case_id": item.semantic_case_id,
                        "node": item.node,
                        "semantic_signature": item.semantic_signature,
                    }
                    for item in self._frozen_cases.values()
                ],
                "frozen_pass_receipts": [
                    {
                        "semantic_case_id": item["semantic_case_id"],
                        "node": item["node"],
                        "semantic_signature": item["semantic_signature"],
                        "code_sha256": item["code_sha256"],
                        "receipt_id": item["receipt_id"],
                        "receipt_sha256": item["receipt_sha256"],
                        "outcome": item["outcome"],
                    }
                    for item in self._frozen_receipts.values()
                ],
                "required_cases": [
                    {"semantic_case_id": case_id, "node": node}
                    for case_id, node in self._required_cases.items()
                ],
                "results": [
                    {
                        "semantic_case_id": case_id,
                        "runs": [
                            {
                                "outcome": result.outcome,
                                "code_sha256": result.code_sha256,
                                "receipt_id": result.receipt_id,
                                "receipt_sha256": result.receipt_sha256,
                            }
                            for result in history
                        ],
                    }
                    for case_id, history in self._results.items()
                ],
                "full_regression": (
                    None
                    if full_regression is None
                    else {
                        "run_id": full_regression.run_id,
                        "receipt_id": full_regression.receipt_id,
                        "receipt_sha256": full_regression.receipt_sha256,
                        "token": full_regression.token,
                        "code_sha256": full_regression.code_sha256,
                        "cases": [
                            {"semantic_case_id": case_id, "node": node}
                            for case_id, node in full_regression.cases
                        ],
                        "results": [
                            {"semantic_case_id": case_id, "outcome": outcome}
                            for case_id, outcome in full_regression.results.items()
                        ],
                        "exit_code": full_regression.exit_code,
                        "collected": full_regression.collected,
                        "errors": full_regression.errors,
                        "skips": full_regression.skips,
                        "collection_errors": full_regression.collection_errors,
                        "invocation_parent_id": full_regression.invocation_parent_id,
                    }
                ),
                "c34_evidence": [
                    {
                        "node": item.node,
                        "outcome": item.outcome,
                        "exit_code": item.exit_code,
                        "collected": item.collected,
                        "assertion_failures": item.assertion_failures,
                        "errors": item.errors,
                        "code_sha256": item.code_sha256,
                        "semantic_case_id": item.semantic_case_id,
                        "invocation_parent_id": item.invocation_parent_id,
                        "collection_errors": item.collection_errors,
                        "execution_kind": item.execution_kind,
                    }
                    for item in test_evidence
                ],
            }
        _validate_schema(
            "maintenance-repair-test-ledger.schema.json",
            document,
            "test_ledger_document_invalid",
        )
        return document


def _run_git(root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepairContractError("repair_git_evidence_unavailable") from exc
    return completed.stdout


def _verified_checkout_root(checkout_root: str | Path | None = None) -> Path:
    """Resolve a checkout and confirm its actual Git top-level directory."""

    try:
        requested = (
            Path(checkout_root).expanduser()
            if checkout_root is not None
            else Path(__file__).resolve(strict=True).parents[3]
        )
        requested = requested.resolve(strict=True)
        if not requested.is_dir() or requested.is_symlink():
            raise RepairContractError("repair_checkout_invalid")
        output = _run_git(requested, "rev-parse", "--show-toplevel")
        top = Path(os.fsdecode(output.rstrip(b"\r\n"))).resolve(strict=True)
        if top != requested:
            raise RepairContractError("repair_checkout_root_mismatch")
        _assert_no_checkout_reparse(top)
        return top
    except RepairContractError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepairContractError("repair_checkout_invalid") from exc


def _assert_no_checkout_reparse(path: Path) -> None:
    """Reject symlinks/reparse points along a checkout path."""

    try:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            if current.is_symlink():
                raise RepairContractError("repair_checkout_reparse_point")
            try:
                attributes = current.stat(follow_symlinks=False).st_file_attributes
            except FileNotFoundError:
                continue
            except AttributeError:
                attributes = 0
            if attributes & 0x400:
                raise RepairContractError("repair_checkout_reparse_point")
    except RepairContractError:
        raise
    except OSError as exc:
        raise RepairContractError("repair_checkout_invalid") from exc


def _git_file_paths(root: Path) -> tuple[str, ...]:
    tracked = _run_git(root, "ls-files", "--cached", "-z")
    untracked = _run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    values: set[str] = set()
    for raw in (tracked + untracked).split(b"\x00"):
        if not raw:
            continue
        try:
            value = os.fsdecode(raw).replace("\\", "/")
            normalized = _normalize_repo_path(value)
        except (UnicodeDecodeError, RepairContractError) as exc:
            raise RepairContractError("repair_checkout_path_invalid") from exc
        if any(
            part in {".git", "__pycache__", ".pytest_cache"}
            for part in PurePosixPath(normalized).parts
        ) or normalized.endswith((".pyc", ".pyo")):
            continue
        values.add(normalized)
    return tuple(sorted(values))


def _git_index_file_modes(root: Path) -> dict[str, int]:
    """Return unambiguous stage-0 Git index modes for every indexed path."""

    output = _run_git(root, "ls-files", "--stage", "-z")
    modes: dict[str, int] = {}
    for record in output.split(b"\x00"):
        if not record:
            continue
        header, separator, raw_path = record.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            raise RepairContractError("repair_checkout_index_mode_invalid")
        raw_mode, object_id, raw_stage = fields
        if (
            raw_mode not in {b"100644", b"100755"}
            or len(object_id) not in {40, 64}
            or any(byte not in b"0123456789abcdefABCDEF" for byte in object_id)
            or raw_stage != b"0"
        ):
            raise RepairContractError("repair_checkout_index_mode_invalid")
        try:
            path = _normalize_repo_path(os.fsdecode(raw_path).replace("\\", "/"))
        except (UnicodeDecodeError, RepairContractError) as exc:
            raise RepairContractError("repair_checkout_index_mode_invalid") from exc
        if path in modes:
            raise RepairContractError("repair_checkout_index_mode_invalid")
        modes[path] = int(raw_mode, 8)
    return modes


def _regular_checkout_file(root: Path, relative: str) -> Path:
    normalized = _normalize_repo_path(relative)
    target = root.joinpath(*PurePosixPath(normalized).parts)
    try:
        _assert_no_checkout_reparse(target)
        resolved_parent = target.parent.resolve(strict=True)
        resolved_parent.relative_to(root)
        if target.exists():
            resolved = target.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                raise RepairContractError("repair_checkout_file_invalid")
            return resolved
        return target
    except RepairContractError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepairContractError("repair_checkout_path_outside_root") from exc


def repository_code_sha256(checkout_root: str | Path | None = None) -> str:
    """Hash paths, index/worktree executable modes, and file bytes."""

    root = _verified_checkout_root(checkout_root)
    digest = hashlib.sha256()
    index_modes = _git_index_file_modes(root)
    for relative in _git_file_paths(root):
        path = _regular_checkout_file(root, relative)
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        index_mode = index_modes.get(relative)
        digest.update(b"I" if index_mode is not None else b"U")
        digest.update((index_mode or 0).to_bytes(4, "big"))
        if path.exists():
            content = path.read_bytes()
            digest.update(b"F")
            digest.update(_execution_mode_bits(path).to_bytes(2, "big"))
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        else:
            digest.update(b"D")
            digest.update((0).to_bytes(2, "big"))
            digest.update((0).to_bytes(8, "big"))
    return digest.hexdigest()


def _execution_mode_bits(path: Path) -> int:
    """Return executable permission bits that can affect how a file runs."""

    return stat.S_IMODE(path.stat().st_mode) & 0o111


def _changed_paths_since(root: Path, base_commit: str) -> tuple[str, ...]:
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise RepairContractError("repair_base_commit_invalid")
    _run_git(root, "cat-file", "-e", f"{base_commit}^{{commit}}")
    changed = _run_git(
        root, "diff", "--name-only", "--no-renames", "-z", base_commit, "--"
    )
    untracked = _run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    values: set[str] = set()
    for raw in (changed + untracked).split(b"\x00"):
        if not raw:
            continue
        try:
            path = _normalize_repo_path(os.fsdecode(raw).replace("\\", "/"))
        except (UnicodeDecodeError, RepairContractError) as exc:
            raise RepairContractError("repair_changed_path_invalid") from exc
        _regular_checkout_file(root, path)
        values.add(path)
    return tuple(sorted(values))


def _assertions_from_source(source: bytes, path: str) -> dict[str, str]:
    try:
        tree = ast.parse(source.decode("utf-8"), filename=path)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RepairContractError("assertion_snapshot_invalid") from exc
    pytest_aliases: set[str] = {"pytest"}
    unittest_aliases: set[str] = {"unittest"}
    pytest_imports: dict[str, str] = {}
    unittest_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest":
                    pytest_aliases.add(alias.asname or "pytest")
                elif alias.name == "unittest":
                    unittest_aliases.add(alias.asname or "unittest")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "pytest":
                pytest_imports.update(
                    (alias.asname or alias.name, alias.name) for alias in node.names
                )
            elif node.module and node.module.startswith("unittest"):
                unittest_imports.update(
                    alias.asname or alias.name for alias in node.names
                )

    def call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else None
        return None

    def is_pytest_api(name: str | None, api: str) -> bool:
        if name is None:
            return False
        return any(name == f"{alias}.{api}" for alias in pytest_aliases) or (
            pytest_imports.get(name) == api
        )

    def is_skip_control(name: str | None) -> bool:
        if name is None:
            return False
        parts = name.split(".")
        leaf = parts[-1]
        if leaf not in {
            "skip",
            "skipif",
            "skipIf",
            "skipUnless",
            "skipTest",
            "expectedFailure",
            "xfail",
            "importorskip",
        }:
            return False
        if name in pytest_imports:
            return pytest_imports[name] in {
                "skip",
                "skipif",
                "skipIf",
                "skipUnless",
                "xfail",
                "importorskip",
            }
        if name in unittest_imports:
            return True
        if leaf == "skipTest" and "." in name:
            return True
        return any(name.startswith(f"{alias}.") for alias in pytest_aliases) or any(
            name.startswith(f"{alias}.") for alias in unittest_aliases
        )

    def signature(kind: str, node: ast.AST) -> str:
        encoded = ast.Tuple(
            elts=[
                ast.Constant(value=kind),
                ast.Constant(
                    value=ast.dump(node, annotate_fields=True, include_attributes=False)
                ),
            ],
            ctx=ast.Load(),
        )
        return ast.dump(encoded, annotate_fields=True, include_attributes=False)

    bare_skip_decorators: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                target = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                if is_skip_control(call_name(target)):
                    bare_skip_decorators.add(id(decorator))

    assertions: dict[str, str] = {}
    ordinal = 0
    for node in ast.walk(tree):
        kind: str | None = None
        observed: ast.AST = node
        if id(node) in bare_skip_decorators:
            kind = "skip-control"
        elif isinstance(node, ast.Assert):
            kind = "assert"
            observed = node.test
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            if any(
                isinstance(item.context_expr, ast.Call)
                and is_pytest_api(call_name(item.context_expr.func), "raises")
                for item in node.items
            ):
                kind = "pytest.raises"
            elif any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr.startswith("assertRaises")
                for item in node.items
            ):
                kind = "unittest.assertRaises"
        elif isinstance(node, ast.Call):
            name = call_name(node.func)
            if is_skip_control(name):
                kind = "skip-control"
            elif isinstance(node.func, ast.Attribute) and node.func.attr.startswith(
                "assertRaises"
            ):
                kind = "exception-expectation"
            elif isinstance(node.func, ast.Attribute) and (
                node.func.attr.startswith("assert") or node.func.attr.startswith("fail")
            ):
                kind = "unittest-assertion"
            elif is_pytest_api(name, "raises"):
                kind = "pytest.raises-call"
        if kind is not None:
            assertions[f"assertion-{ordinal:04d}"] = signature(kind, observed)
            ordinal += 1

    module_scope = ast.Module(
        body=[
            node
            for node in tree.body
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        ],
        type_ignores=tree.type_ignores,
    )
    assertions["execution-module"] = signature("execution-module", module_scope)

    def collect_execution_units(body: list[ast.stmt], scope: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = f"{scope}.{node.name}" if scope else node.name
                execution_id = f"execution-function:{qualified_name}"
                assertions[execution_id] = signature("execution-function", node)
                collect_execution_units(node.body, qualified_name)
            elif isinstance(node, ast.ClassDef):
                qualified_name = f"{scope}.{node.name}" if scope else node.name
                execution_id = f"execution-class:{qualified_name}"
                class_scope = ast.ClassDef(
                    name=node.name,
                    bases=node.bases,
                    keywords=node.keywords,
                    body=[
                        child
                        for child in node.body
                        if not isinstance(
                            child,
                            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                        )
                    ],
                    decorator_list=node.decorator_list,
                )
                assertions[execution_id] = signature("execution-class", class_scope)
                collect_execution_units(node.body, qualified_name)

    collect_execution_units(tree.body, "")
    return assertions


def _is_new_test_execution_key(value: str) -> bool:
    prefix, separator, qualified_name = value.partition(":")
    if not separator:
        return False
    parts = qualified_name.split(".")
    leaf = parts[-1]
    if any(part.startswith("test") or part.endswith("_test") for part in parts[:-1]):
        return True
    if prefix == "execution-class":
        return leaf.startswith("Test")
    if prefix == "execution-function":
        return leaf.startswith("test") or leaf.endswith("_test")
    return False


def _is_skip_signature(value: str) -> bool:
    return _signature_kind(value) == "skip-control"


def _is_exception_expectation_signature(value: str) -> bool:
    return _signature_kind(value) in {
        "pytest.raises",
        "pytest.raises-call",
        "unittest.assertRaises",
        "exception-expectation",
    }


def _signature_kind(value: str) -> str | None:
    try:
        tree = ast.parse(value, mode="eval")
    except (SyntaxError, ValueError):
        return None
    if (
        isinstance(tree.body, ast.Tuple)
        and len(tree.body.elts) == 2
        and isinstance(tree.body.elts[0], ast.Constant)
        and isinstance(tree.body.elts[0].value, str)
    ):
        return tree.body.elts[0].value
    return None


def _assertion_snapshots(
    root: Path, base_commit: str, changed_paths: tuple[str, ...]
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    baseline: dict[str, dict[str, str]] = {}
    candidate: dict[str, dict[str, str]] = {}
    for path in changed_paths:
        if not _is_test_path(path):
            continue
        baseline[path] = {}
        try:
            before = _run_git(root, "show", f"{base_commit}:{path}")
        except RepairContractError as exc:
            if exc.code != "repair_git_evidence_unavailable":
                raise
        else:
            baseline[path] = _assertions_from_source(before, path)
        target = _regular_checkout_file(root, path)
        candidate[path] = (
            _assertions_from_source(target.read_bytes(), path)
            if target.exists()
            else {}
        )
    return baseline, candidate


def _junit_results(
    junit_path: Path, expected_nodes: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RepairContractError("host_pytest_junit_invalid") from exc
    expected_identities: dict[str, tuple[str, str]] = {}
    for raw_node in expected_nodes:
        try:
            node = _normalize_node(raw_node)
        except RepairContractError:
            continue
        path, *components = node.split("::")
        if not components:
            continue
        module_name = path[:-3].replace("/", ".")
        class_components = components[:-1]
        classname = ".".join((module_name, *class_components))
        expected_identities[node] = (classname, components[-1])

    cases: dict[str, str] = {}
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for case in suite.findall("testcase"):
            name = case.attrib.get("name", "")
            classname = case.attrib.get("classname", "")
            matches = [
                node
                for node, identity in expected_identities.items()
                if identity == (classname, name)
            ]
            if len(matches) != 1:
                continue
            node = matches[0]
            if case.find("failure") is not None:
                status = "FAIL"
            elif case.find("error") is not None:
                status = "ERROR"
            elif case.find("skipped") is not None:
                status = "SKIP"
            else:
                status = "PASS"
            if node in cases:
                cases[node] = "ERROR"
            else:
                cases[node] = status
    return tuple((node, cases.get(node, "NOT_RUN")) for node in expected_nodes)


def _raise_full_regression_close_failure(
    original_error: Exception, closure_error: Exception
) -> None:
    """Keep the runner error as primary while retaining the Store failure too."""

    error = RepairContractError(
        getattr(original_error, "code", "host_run_receipt_invalid")
    )
    error.original_error = original_error
    error.closure_error = closure_error
    error.add_note(f"full regression failure closure also failed: {closure_error!r}")
    raise error from ExceptionGroup(
        "Full regression failed and durable failure closure also failed",
        [original_error, closure_error],
    )


def _run_local_pytest(
    argv: list[str], *, cwd: Path, env: Mapping[str, str], timeout: int
) -> subprocess.CompletedProcess[bytes]:
    """Run the selected local pytest nodes with the current Python environment."""

    return subprocess.run(
        argv,
        cwd=cwd,
        env=dict(env),
        timeout=timeout,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class LocalPytestRunner:
    """Run the approved local pytest nodes and return their ordinary results."""

    def __init__(
        self,
        *,
        store: Any,
        checkout_root: str | Path | None = None,
    ) -> None:
        self._store = store
        self._checkout_root = _verified_checkout_root(checkout_root)

    def _run_exact_nodes(
        self, nodes: tuple[str, ...], *, expected_code_sha256: str
    ) -> tuple[tuple[tuple[str, str], ...], int, int, int, int, int, int]:
        if not nodes or len(nodes) != len(set(nodes)):
            raise RepairContractError("host_pytest_node_set_invalid")
        before_hash = repository_code_sha256(self._checkout_root)
        if before_hash != expected_code_sha256:
            raise RepairContractError("repair_code_hash_drift")
        with tempfile.TemporaryDirectory(prefix="inago-repair-run-") as tmp:
            junit_path = Path(tmp) / "junit.xml"
            environment = dict(os.environ)
            environment.pop("PYTEST_CURRENT_TEST", None)
            environment["PYTEST_ADDOPTS"] = ""
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONOPTIMIZE"] = "0"
            environment["INAGO_REPAIR_RUNNER_ACTIVE"] = uuid.uuid4().hex
            argv = [
                sys.executable,
                "-c",
                (
                    "import sys; assert sys.flags.optimize == 0; "
                    "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
                ),
                "-q",
                "--disable-warnings",
                "--maxfail=0",
                "-o",
                "addopts=",
                "--junitxml",
                str(junit_path),
                *nodes,
            ]
            try:
                completed = _run_local_pytest(
                    argv,
                    cwd=self._checkout_root,
                    env=environment,
                    timeout=300,
                )
            except subprocess.TimeoutExpired:
                completed = subprocess.CompletedProcess(
                    argv, 124, b"", b"pytest timeout"
                )
            except OSError as exc:
                completed = subprocess.CompletedProcess(
                    argv, 127, b"", str(exc).encode("utf-8", errors="replace")
                )
            after_hash = repository_code_sha256(self._checkout_root)
            if before_hash != after_hash or after_hash != expected_code_sha256:
                raise RepairContractError("repair_code_hash_drift")
            try:
                results = _junit_results(junit_path, nodes)
            except RepairContractError:
                results = tuple((node, "NOT_RUN") for node in nodes)
            result_map = dict(results)
            try:
                output = junit_path.read_text(encoding="utf-8")
                import xml.etree.ElementTree as ET

                xml_root = ET.fromstring(output)
                suite_nodes = (
                    [xml_root]
                    if xml_root.tag == "testsuite"
                    else list(xml_root.iter("testsuite"))
                )
                errors = sum(int(s.attrib.get("errors", "0")) for s in suite_nodes)
                skips = sum(int(s.attrib.get("skipped", "0")) for s in suite_nodes)
                collection_errors = sum(
                    1 for s in suite_nodes for child in s.findall("error")
                )
                assertion_failures = sum(
                    1
                    for s in suite_nodes
                    for case in s.findall("testcase")
                    if case.find("failure") is not None
                )
            except (OSError, ValueError, ET.ParseError):
                errors = max(errors, 1)
                collection_errors = max(collection_errors, 1)
                assertion_failures = 0
                skips = 0
            collected = sum(status != "NOT_RUN" for _node, status in results)
            return (
                tuple((node, result_map[node]) for node in nodes),
                completed.returncode,
                collected,
                assertion_failures,
                errors,
                skips,
                collection_errors,
            )

    def run_case(self, ledger: RepairTestLedger, semantic_case_id: str) -> Any:
        if not isinstance(ledger, RepairTestLedger) or (
            ledger._store is not None and ledger._store is not self._store
        ):
            raise RepairContractError("host_runner_ledger_invalid")
        if ledger.invocation_parent_id is not None or os.environ.get(
            "INAGO_REPAIR_RUNNER_ACTIVE"
        ):
            raise RepairContractError("nested_test_invocation_rejected")
        start: Mapping[str, object] | None = None
        try:
            start = self._store._begin_repair_case_run(
                campaign_id=ledger.campaign_id,
                semantic_case_id=semantic_case_id,
                capability=self._store._repair_runner_capability,
                started_at=datetime.now(timezone.utc),
            )
            if repository_code_sha256(self._checkout_root) != start["code_sha256"]:
                raise RepairContractError("repair_code_hash_drift")
            (
                results,
                exit_code,
                collected,
                failures,
                errors,
                skips,
                collection_errors,
            ) = self._run_exact_nodes(
                (start["node"],), expected_code_sha256=start["code_sha256"]
            )
            if len(results) != 1 or results[0][0] != start["node"]:
                raise RepairContractError("host_pytest_node_set_mismatch")
            outcome = results[0][1]
            if (outcome == "PASS" and exit_code != 0) or (
                outcome == "FAIL" and exit_code != 1
            ):
                raise RepairContractError("host_pytest_exit_status_mismatch")
            return self._store._record_repair_case_receipt(
                capability=self._store._repair_runner_capability,
                invocation_id=start["invocation_id"],
                nonce=start["nonce"],
                outcome=outcome,
                exit_code=exit_code,
                collected=collected,
                assertion_failures=failures,
                errors=errors,
                skips=skips,
                collection_errors=collection_errors,
                created_at=datetime.now(timezone.utc),
            )
        except RepairContractError as exc:
            if start is not None:
                try:
                    self._store._fail_repair_case_run(
                        invocation_id=str(start["invocation_id"]),
                        failure_code=exc.code,
                        capability=self._store._repair_runner_capability,
                        failed_at=datetime.now(timezone.utc),
                    )
                except Exception as failure:
                    raise RepairContractError(
                        getattr(
                            failure, "code", "repair_case_run_failure_persist_failed"
                        )
                    ) from exc
            raise
        except Exception as exc:
            wrapped = RepairContractError(
                getattr(exc, "code", "host_run_receipt_invalid")
            )
            if start is not None:
                try:
                    self._store._fail_repair_case_run(
                        invocation_id=str(start["invocation_id"]),
                        failure_code=wrapped.code,
                        capability=self._store._repair_runner_capability,
                        failed_at=datetime.now(timezone.utc),
                    )
                except Exception as failure:
                    raise RepairContractError(
                        getattr(
                            failure, "code", "repair_case_run_failure_persist_failed"
                        )
                    ) from exc
            raise RepairContractError(wrapped.code) from exc

    def run_full_regression(self, ledger: RepairTestLedger) -> FullRegressionEvidence:
        if not isinstance(ledger, RepairTestLedger) or (
            ledger._store is not None and ledger._store is not self._store
        ):
            raise RepairContractError("host_runner_ledger_invalid")
        if ledger.invocation_parent_id is not None or os.environ.get(
            "INAGO_REPAIR_RUNNER_ACTIVE"
        ):
            raise RepairContractError("nested_test_invocation_rejected")
        claimed = False
        attempt_id: str | None = None
        try:
            if ledger._store is None:
                attempt_id = ledger._attempt_id
                code_hash = ledger.code_sha256
                cases = ledger._full_regression_snapshot
                token = ledger._full_regression_token
                nonce = None
            else:
                (
                    attempt_id,
                    code_hash,
                    cases,
                    token,
                    nonce,
                ) = self._store._claim_repair_runner_nonce(
                    campaign_id=ledger.campaign_id,
                    capability=self._store._repair_runner_capability,
                    token=ledger._full_regression_token,
                    nonce=ledger._pending_nonce,
                )
                claimed = True
            if (
                token is None
                or cases is None
                or code_hash != ledger.code_sha256
                or cases != ledger._full_regression_snapshot
            ):
                raise RepairContractError("full_regression_snapshot_mismatch")
            if repository_code_sha256(self._checkout_root) != code_hash:
                raise RepairContractError("repair_code_hash_drift")
            nodes = tuple(node for _case_id, node in cases)
            (
                run_results,
                exit_code,
                collected,
                failures,
                errors,
                skips,
                collection_errors,
            ) = self._run_exact_nodes(nodes, expected_code_sha256=code_hash)
            result_map = {
                case_id: status
                for (case_id, _node), (run_node, status) in zip(cases, run_results)
                if run_node == _node
            }
            if len(result_map) != len(cases) or set(result_map) != {
                case_id for case_id, _node in cases
            }:
                raise RepairContractError("host_pytest_result_set_invalid")
            receipt = None
            if ledger._store is not None:
                if attempt_id is None:
                    raise RepairContractError("repair_attempt_identity_invalid")
                receipt = self._store._record_repair_run_receipt(
                    capability=self._store._repair_runner_capability,
                    campaign_id=ledger.campaign_id,
                    attempt_id=attempt_id,
                    token=token,
                    nonce=nonce,
                    code_sha256=code_hash,
                    cases=cases,
                    results=result_map,
                    exit_code=exit_code,
                    collected=collected,
                    assertion_failures=failures,
                    errors=errors,
                    skips=skips,
                    collection_errors=collection_errors,
                    invocation_parent_id=None,
                    created_at=datetime.now(timezone.utc),
                )
            return FullRegressionEvidence(
                run_id=uuid.uuid4().hex if receipt is None else receipt.receipt_id,
                token=token,
                receipt_id=None if receipt is None else receipt.receipt_id,
                code_sha256=code_hash,
                cases=cases,
                results=MappingProxyType(result_map),
                exit_code=exit_code,
                collected=collected,
                errors=errors,
                skips=skips,
                collection_errors=collection_errors,
                invocation_parent_id=None,
                receipt_sha256=None if receipt is None else receipt.receipt_sha256,
            )
        except RepairContractError as exc:
            if claimed and attempt_id is not None:
                try:
                    self._store._fail_repair_full_regression(
                        campaign_id=ledger.campaign_id,
                        attempt_id=attempt_id,
                        failure_code=exc.code,
                        capability=self._store._repair_runner_capability,
                        failed_at=datetime.now(timezone.utc),
                    )
                except Exception as close_exc:
                    _raise_full_regression_close_failure(exc, close_exc)
            raise
        except Exception as exc:
            if claimed and attempt_id is not None:
                try:
                    self._store._fail_repair_full_regression(
                        campaign_id=ledger.campaign_id,
                        attempt_id=attempt_id,
                        failure_code=getattr(exc, "code", "host_run_receipt_invalid"),
                        capability=self._store._repair_runner_capability,
                        failed_at=datetime.now(timezone.utc),
                    )
                except Exception as close_exc:
                    _raise_full_regression_close_failure(exc, close_exc)
            raise RepairContractError(
                getattr(exc, "code", "host_run_receipt_invalid")
            ) from exc


# Compatibility name for existing callers of the previous runner class.
HostPytestRunner = LocalPytestRunner


class RepositoryRepairEvidenceResolver:
    """Collect change scope, assertions, and persisted cases from trusted host state."""

    def __init__(
        self,
        *,
        store: Any,
        checkout_root: str | Path | None = None,
        attempts_root: str | Path | None = None,
        runner: LocalPytestRunner | None = None,
    ) -> None:
        self._store = store
        self._checkout_root = _verified_checkout_root(checkout_root)
        self._attempts_root = (
            Path(attempts_root).expanduser().resolve(strict=True)
            if attempts_root is not None
            else None
        )
        self._runner = runner or LocalPytestRunner(
            store=store, checkout_root=self._checkout_root
        )

    def _attempts_root_for(self, context: RepairHostEvidenceContext) -> Path:
        if context.attempts_root is not None:
            return Path(context.attempts_root).expanduser().resolve(strict=True)
        if self._attempts_root is not None:
            return self._attempts_root
        # The standard isolated layout keeps attempts/ beside the store's data
        # directory. Production controller calls always pass the configured root.
        try:
            return self._store.database_path.parents[2] / "attempts"
        except (AttributeError, IndexError) as exc:
            raise RepairContractError("repair_attempts_root_unavailable") from exc

    @property
    def runner(self) -> LocalPytestRunner:
        """Expose the local pytest runner to the repair-stage controller."""

        return self._runner

    def __call__(self, context: RepairHostEvidenceContext) -> RepairHostEvidence:
        if not isinstance(context, RepairHostEvidenceContext):
            raise RepairContractError("repair_host_context_invalid")
        root = _verified_checkout_root(self._checkout_root)
        try:
            repair_attempt = self._store.get_step_attempt(attempt_id=context.attempt_id)
            pin = self._store.get_repair_step_predecessor(attempt_id=context.attempt_id)
            plan_attempt = self._store.get_step_attempt(attempt_id=pin["attempt_id"])
            manifest = self._store.get_job_input_manifest(job_id=context.job_id)
            expected_identity = manifest.identity()
            attempts_root = self._attempts_root_for(context)
            plan_bytes = self._store.read_attempt_artifact(
                attempts_root=attempts_root,
                relative_path=plan_attempt.output_path,
                expected_sha256=pin["output_sha256"],
            )
            approved_plan = json.loads(plan_bytes)
            supplied_plan = _thaw(context.approved_plan)
            if (
                repair_attempt.job_id != context.job_id
                or repair_attempt.step_key != "maintenance_repair"
                or repair_attempt.sequence != 3
                or repair_attempt.status != "running"
                or pin["attempt_id"] != plan_attempt.attempt_id
                or plan_attempt.job_id != context.job_id
                or plan_attempt.step_key != "maintenance_planning"
                or plan_attempt.sequence != 2
                or plan_attempt.result_version != pin["result_version"]
                or plan_attempt.output_path is None
                or plan_attempt.output_sha256 != pin["output_sha256"]
                or not isinstance(approved_plan, dict)
                or approved_plan != supplied_plan
                or approved_plan.get("schema_version")
                != "maintenance-planning-result-v1"
                or approved_plan.get("identity") != expected_identity
                or approved_plan.get("attempt_id") != plan_attempt.attempt_id
                or pin["result_version"] != approved_plan.get("result_version")
                or approved_plan.get("base_commit")
                != self._store.get_job_base_commit(job_id=context.job_id)
            ):
                raise RepairContractError("repair_plan_unavailable")
            scope = approved_plan.get("repair_scope")
            output = approved_plan.get("output")
            scope_ref = (
                output.get("repair_scope") if isinstance(output, Mapping) else None
            )
            map_ref = (
                output.get("requirement_step_case_map")
                if isinstance(output, Mapping)
                else None
            )
            if (
                not isinstance(scope, Mapping)
                or set(scope) != {"owned_paths"}
                or not isinstance(scope.get("owned_paths"), (list, tuple))
                or not isinstance(scope_ref, Mapping)
                or set(scope_ref) != {"path", "sha256"}
                or not isinstance(map_ref, Mapping)
                or set(map_ref) != {"path", "sha256"}
            ):
                raise RepairContractError("repair_scope_invalid")
            owned_paths = tuple(
                _normalize_repo_path(path) for path in scope["owned_paths"]
            )
            if (
                not owned_paths
                or list(owned_paths) != list(scope["owned_paths"])
                or len(owned_paths) != len(set(owned_paths))
                or tuple(sorted(owned_paths)) != owned_paths
            ):
                raise RepairContractError("repair_scope_invalid")
            scope_bytes = self._store.read_attempt_artifact(
                attempts_root=attempts_root,
                relative_path=scope_ref["path"],
                expected_sha256=scope_ref["sha256"],
            )
            scope_artifact = json.loads(scope_bytes)
            if scope_artifact != {"repair_scope": {"owned_paths": list(owned_paths)}}:
                raise RepairContractError("repair_scope_invalid")
            map_bytes = self._store.read_attempt_artifact(
                attempts_root=attempts_root,
                relative_path=map_ref["path"],
                expected_sha256=map_ref["sha256"],
            )
            case_map = json.loads(map_bytes)
            if (
                not isinstance(case_map, dict)
                or case_map.get("schema_version")
                != "maintenance-requirement-step-case-map-v1"
                or case_map.get("result_version") != approved_plan.get("result_version")
                or not isinstance(case_map.get("case_nodes"), list)
                or not case_map["case_nodes"]
            ):
                raise RepairContractError("repair_case_map_invalid")
            required: dict[str, str] = {}
            signatures: dict[str, str] = {}
            for item in case_map["case_nodes"]:
                if not isinstance(item, Mapping) or set(item) != {
                    "case_id",
                    "node",
                    "requirement_step",
                }:
                    raise RepairContractError("repair_case_map_invalid")
                case_id = _safe_identifier(item["case_id"], "repair_case_map_invalid")
                node = _normalize_node(item["node"])
                required[case_id] = node
                signature_payload = json.dumps(
                    dict(item), ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                signatures[case_id] = hashlib.sha256(
                    signature_payload.encode("ascii")
                ).hexdigest()
            current_hash = repository_code_sha256(root)
            changed_paths = _changed_paths_since(root, context.base_commit)
            if any(
                not any(_path_is_owned(path, owner) for owner in owned_paths)
                for path in changed_paths
            ):
                raise RepairContractError("repair_changed_path_unowned")
            baseline, candidate = _assertion_snapshots(
                root, context.base_commit, changed_paths
            )
            campaign_id = f"repair-{context.attempt_id}-{current_hash[:12]}"
            ledger = RepairTestLedger(
                campaign_id=campaign_id,
                code_sha256=current_hash,
                required_cases=required,
                semantic_signatures=signatures,
                invocation_parent_id=None,
                _job_id=context.job_id,
                _attempt_id=context.attempt_id,
            )
            return RepairHostEvidence(
                changed_paths=changed_paths,
                owned_paths=owned_paths,
                baseline_assertions=baseline,
                candidate_assertions=candidate,
                test_ledger=ledger,
                current_code_sha256=current_hash,
                test_evidence=(),
                approved_cases=required,
                job_id=context.job_id,
                attempt_id=context.attempt_id,
                base_commit=context.base_commit,
                plan_attempt_id=plan_attempt.attempt_id,
                plan_result_version=str(plan_attempt.result_version),
                plan_output_sha256=str(pin["output_sha256"]),
                checkout_root=str(root),
            )
        except RepairContractError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "repair_host_evidence_invalid")
            if code == "repair_changed_path_unowned":
                raise RepairContractError(code) from exc
            raise RepairContractError(code) from exc
