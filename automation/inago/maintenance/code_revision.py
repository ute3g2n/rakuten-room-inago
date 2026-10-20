"""Descriptive maintenance invocation context without a source fingerprint."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True, slots=True)
class InvocationCodeRevision:
    code_revision: str
    reason_code: str | None = None
    failed_ref: str | None = None
    error: BaseException | None = None
    source_count: int = 0


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
    """Provide a descriptive marker; source inspection never gates a stage."""

    return InvocationCodeRevision(code_revision="maintenance-source-current")
