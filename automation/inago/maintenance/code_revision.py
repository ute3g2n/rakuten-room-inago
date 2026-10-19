"""Immutable invocation-start fingerprint for maintenance source and contracts."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class InvocationCodeRevision:
    code_revision: str
    reason_code: str | None = None
    failed_ref: str | None = None
    error: BaseException | None = None
    source_count: int = 0
    manifest_sha256: str | None = None


_CURRENT_REVISION: ContextVar[InvocationCodeRevision | None] = ContextVar(
    "maintenance_invocation_code_revision", default=None
)


@contextmanager
def bind_invocation_code_revision(
    revision: InvocationCodeRevision,
) -> Iterator[None]:
    token: Token[InvocationCodeRevision | None] = _CURRENT_REVISION.set(revision)
    try:
        yield
    finally:
        _CURRENT_REVISION.reset(token)


def current_invocation_code_revision() -> InvocationCodeRevision | None:
    return _CURRENT_REVISION.get()


def current_code_revision() -> str:
    revision = current_invocation_code_revision()
    return revision.code_revision if revision is not None else "unknown"


def current_code_revision_reason() -> str:
    revision = current_invocation_code_revision()
    if revision is None:
        return "invocation_revision_not_bound"
    return revision.reason_code or ""


def capture_invocation_code_revision() -> InvocationCodeRevision:
    """Hash the maintenance implementation set once, before stage execution."""

    failed_ref: str | None = None
    failure_reason = "code_revision_source_enumeration_failed"
    try:
        source_file = Path(__file__).resolve(strict=True)
        root = source_file.parents[3]
        maintenance_root = root / "automation" / "inago" / "maintenance"
        schema_root = root / "automation" / "inago" / "schemas"
        fixed_sources = (
            root / "automation" / "inago" / "daily_pipeline.py",
            root / "automation" / "inago" / "orchestrator.py",
            root / "automation" / "inago" / "trace.py",
        )

        def enumerate_sources() -> tuple[Path, ...]:
            candidates = (
                *maintenance_root.rglob("*.py"),
                *maintenance_root.rglob("*.sql"),
                *schema_root.glob("maintenance-*.schema.json"),
                *fixed_sources,
            )
            normalized: dict[str, Path] = {}
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
                normalized[resolved.relative_to(root).as_posix()] = resolved
            return tuple(normalized[key] for key in sorted(normalized))

        source_paths = enumerate_sources()
        if not source_paths:
            raise RuntimeError("maintenance source set is empty")
        manifest_sources: list[dict[str, str]] = []
        source_bytes: dict[str, bytes] = {}
        failure_reason = "code_revision_source_read_failed"
        for path in source_paths:
            relative = path.relative_to(root).as_posix()
            failed_ref = relative
            before = path.stat()
            content = path.read_bytes()
            after = path.stat()
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ino != after.st_ino
                or len(content) != after.st_size
            ):
                failure_reason = "code_revision_source_changed_during_capture"
                raise RuntimeError("source changed during fingerprint capture")
            source_bytes[relative] = content
            manifest_sources.append(
                {"path": relative, "sha256": hashlib.sha256(content).hexdigest()}
            )
        if enumerate_sources() != source_paths:
            failure_reason = "code_revision_source_set_changed_during_capture"
            raise RuntimeError("source set changed during fingerprint capture")

        worker_ref = "automation/inago/maintenance/worker.py"
        workflow_ref = "automation/inago/maintenance/workflow_provider.py"
        failed_ref = worker_ref
        failure_reason = "code_revision_python_source_parse_failed"
        worker_tree = ast.parse(source_bytes[worker_ref].decode("utf-8"))
        failed_ref = workflow_ref
        workflow_tree = ast.parse(source_bytes[workflow_ref].decode("utf-8"))
        failure_reason = "code_revision_worker_contract_invalid"
        worker_exclusions = _literal_constant(worker_tree, "WORKER_DISPATCH_EXCLUDED_DATES")
        if (
            isinstance(worker_exclusions, ast.Call)
            and isinstance(worker_exclusions.func, ast.Name)
            and worker_exclusions.func.id == "frozenset"
            and len(worker_exclusions.args) == 1
        ):
            worker_exclusions = ast.literal_eval(worker_exclusions.args[0])
        workflow_stages = _literal_constant(workflow_tree, "WORKFLOW_STAGE_ORDER")
        worker_versions = sorted(
            {
                node.value
                for node in ast.walk(worker_tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("maintenance-worker-result-v")
            }
        )
        failure_reason = "code_revision_workflow_contract_invalid"
        schema_versions: dict[str, list[str | int]] = {}
        for relative, content in source_bytes.items():
            if not relative.startswith("automation/inago/schemas/maintenance-"):
                continue
            failed_ref = relative
            failure_reason = "code_revision_schema_parse_failed"
            document = json.loads(content.decode("utf-8"))
            schema_versions[relative] = sorted(
                _schema_version_values(document), key=lambda value: (type(value).__name__, str(value))
            )

        metadata = {
            "fingerprint_schema_version": 1,
            "worker": {
                "source": worker_ref,
                "dispatch_excluded_dates": sorted(worker_exclusions),
                "result_schema_versions": worker_versions,
            },
            "workflow": {
                "source": workflow_ref,
                "stage_order": list(ast.literal_eval(workflow_stages)),
            },
            "schema_versions": schema_versions,
            "sources": manifest_sources,
        }
        encoded = json.dumps(
            metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        failure_reason = "code_revision_manifest_hash_failed"
        digest = hashlib.sha256(encoded).hexdigest()
        return InvocationCodeRevision(
            code_revision=f"maintenance-source-v1-{digest}",
            source_count=len(manifest_sources),
            manifest_sha256=digest,
        )
    except Exception as exc:
        return InvocationCodeRevision(
            code_revision="unknown",
            reason_code=failure_reason,
            failed_ref=failed_ref,
            error=exc,
        )


def _literal_constant(tree: ast.AST, name: str) -> object:
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return node.value
    raise ValueError("source contract constant is missing")


def _schema_version_values(document: object) -> set[str | int]:
    values: set[str | int] = set()
    if isinstance(document, dict):
        for key, value in document.items():
            if key == "schema_version" and isinstance(value, dict):
                const = value.get("const")
                if type(const) in (str, int):
                    values.add(const)
            values.update(_schema_version_values(value))
    elif isinstance(document, list):
        for value in document:
            values.update(_schema_version_values(value))
    return values
