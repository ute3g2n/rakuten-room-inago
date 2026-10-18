"""Git and code-switching stages for reviewed maintenance releases.

This module contains no production-service wiring. Callers must provide the
host's daily-owner, pending-action, and runtime-switch observations; missing or
uncertain observations hold the deployment. Tests use only temporary Git repos
and local bare remotes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol, Sequence

from automation.inago.maintenance.diagnostic_trace import (
    MaintenanceDiagnosticTrace,
    audit_maintenance_stage,
)
from automation.inago.maintenance.paths import _has_reparse_point
from automation.inago.maintenance.process_identity import current_process_start_time
from automation.inago.maintenance.store import (
    DeploymentRecord,
    LeaseBusyError,
    LeaseToken,
    MaintenanceStore,
    MaintenanceStoreError,
)


class DeploymentError(RuntimeError):
    """A safe deployment error code with no Git stderr or credentials."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PendingSnapshot:
    """Count and revision of pending user actions; the queue is never modified."""

    count: int
    revision: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 0
        ):
            raise ValueError("deployment_pending_count_invalid")
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("deployment_pending_revision_invalid")


class RuntimeSwitcher(Protocol):
    """Host-owned code switcher. It must switch code only, never restore a DB."""

    def current_commit(self) -> str | None:
        ...

    def activate(self, commit: str, checkout: Path | None = None) -> None:
        ...

    def healthy(self, expected_commit: str) -> bool:
        ...


class DeploymentExclusionGuard(Protocol):
    """Shared host lock used by daily, API-apply, and deployment entry points."""

    def acquire(self) -> bool:
        ...

    def release(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class CandidatePushResult:
    status: str
    commit: str
    remote_name: str
    branch: str
    active: bool = False


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    deployment_id: str | None
    release_id: str
    status: str
    reason_code: str | None
    source_commit: str
    base_commit: str
    tested_commit: str
    pushed_commit: str | None
    active_commit: str | None
    remote_name: str
    target_branch: str

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": "maintenance-deployment-result-v1", **asdict(self)}


_COMMIT_RE = re.compile(r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
_REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
)
_WORKTREE_OWNER_SCHEMA = "maintenance-worktree-owner-v1"


def _git(
    repo: Path,
    *arguments: str,
    failure_code: str = "git_command_failed",
    allowed_returncodes: tuple[int, ...] = (0,),
) -> bytes:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(failure_code) from exc
    if result.returncode not in allowed_returncodes:
        raise DeploymentError(failure_code)
    return result.stdout


def _text(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").strip()


def _repository_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if _has_reparse_point(candidate):
        raise DeploymentError("repository_path_reparse_point")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DeploymentError("repository_path_unavailable") from exc
    root = Path(_text(_git(resolved, "rev-parse", "--show-toplevel")))
    if not root.is_absolute():
        root = (resolved / root).resolve(strict=True)
    else:
        root = root.resolve(strict=True)
    return root


def _commit(repo: Path, value: str) -> str:
    if not isinstance(value, str) or not _COMMIT_RE.fullmatch(value):
        raise DeploymentError("commit_id_invalid")
    resolved = _text(_git(repo, "rev-parse", "--verify", f"{value}^{{commit}}"))
    if resolved.lower() != value.lower():
        raise DeploymentError("commit_id_not_exact")
    return resolved.lower()


def _ref(remote: str, branch: str) -> str:
    if not isinstance(remote, str) or not _REMOTE_RE.fullmatch(remote):
        raise DeploymentError("remote_name_invalid")
    if (
        not isinstance(branch, str)
        or not _BRANCH_RE.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.endswith(("/", ".", ".lock"))
        or "@{" in branch
    ):
        raise DeploymentError("target_branch_invalid")
    return f"refs/heads/{branch}"


def _remote_head(repo: Path, remote: str, branch: str) -> str | None:
    ref = _ref(remote, branch)
    raw = _git(
        repo,
        "ls-remote",
        "--heads",
        remote,
        ref,
        failure_code="git_remote_unavailable",
    )
    rows = [line for line in _text(raw).splitlines() if line]
    if not rows:
        return None
    if len(rows) != 1:
        raise DeploymentError("git_remote_ref_ambiguous")
    parts = rows[0].split("\t")
    if len(parts) != 2 or parts[1] != ref or not _COMMIT_RE.fullmatch(parts[0]):
        raise DeploymentError("git_remote_ref_invalid")
    return parts[0].lower()


def _is_ancestor(repo: Path, base: str, commit: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, commit],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise DeploymentError("git_ancestry_check_failed")
    return result.returncode == 0


def _nul_paths(raw: bytes) -> set[str]:
    return {os.fsdecode(item) for item in raw.split(b"\0") if item}


def _changed_paths(repo: Path, base: str, commit: str) -> set[str]:
    return _nul_paths(_git(repo, "diff", "--name-only", "-z", base, commit, "--"))


def _working_paths(repo: Path) -> set[str]:
    tracked = _nul_paths(_git(repo, "diff", "--name-only", "-z", "HEAD", "--"))
    untracked = _nul_paths(
        _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    )
    return tracked | untracked


def _status(repo: Path) -> bytes:
    return _git(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        failure_code="git_status_unavailable",
    )


class ManagedWorktreeManager:
    """Create/reuse only worktrees recorded as owned by the given release."""

    def __init__(self, repository: str | Path, worktree_root: str | Path) -> None:
        self.repository = _repository_root(repository)
        root_input = Path(worktree_root).expanduser()
        if _has_reparse_point(root_input):
            raise DeploymentError("worktree_root_reparse_point")
        self.worktree_root = root_input.resolve(strict=False)
        try:
            self.worktree_root.relative_to(self.repository)
        except ValueError:
            pass
        else:
            raise DeploymentError("worktree_root_inside_repository")

    def open(self, *, release_id: str, commit: str) -> Path:
        if not isinstance(release_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", release_id
        ):
            raise DeploymentError("release_id_invalid")
        pinned_commit = _commit(self.repository, commit)
        key = hashlib.sha256(release_id.encode("utf-8")).hexdigest()
        path = self.worktree_root / key
        registry_root = self.worktree_root / "owners"
        manifest = registry_root / f"{key}.json"
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        registry_root.mkdir(parents=True, exist_ok=True)
        if _has_reparse_point(self.worktree_root) or _has_reparse_point(registry_root):
            raise DeploymentError("worktree_root_reparse_point")
        if manifest.exists():
            owner = self._read_owner(manifest)
            expected = {
                "schema_version": _WORKTREE_OWNER_SCHEMA,
                "release_id": release_id,
                "repository": str(self.repository),
                "path": str(path),
                "commit": pinned_commit,
            }
            if owner != expected:
                raise DeploymentError("worktree_owner_mismatch")
            if not path.is_dir():
                raise DeploymentError("managed_worktree_missing")
            actual_root = _repository_root(path)
            if actual_root != path.resolve(strict=True):
                raise DeploymentError("managed_worktree_path_mismatch")
            actual_commit = _commit(
                actual_root, _text(_git(actual_root, "rev-parse", "HEAD"))
            )
            if actual_commit != pinned_commit:
                raise DeploymentError("managed_worktree_commit_mismatch")
            if _status(actual_root):
                raise DeploymentError("managed_worktree_dirty")
            return actual_root
        if path.exists():
            raise DeploymentError("worktree_ownership_unverified")
        if manifest.exists():
            raise DeploymentError("worktree_registry_conflict")
        _git(
            self.repository,
            "worktree",
            "add",
            "--detach",
            str(path),
            pinned_commit,
            failure_code="managed_worktree_create_failed",
        )
        owner = {
            "schema_version": _WORKTREE_OWNER_SCHEMA,
            "release_id": release_id,
            "repository": str(self.repository),
            "path": str(path),
            "commit": pinned_commit,
        }
        try:
            with manifest.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(owner, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
        except OSError as exc:
            raise DeploymentError("worktree_owner_record_failed") from exc
        return path.resolve(strict=True)

    @staticmethod
    def _read_owner(path: Path) -> dict[str, object]:
        if _has_reparse_point(path):
            raise DeploymentError("worktree_owner_reparse_point")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeploymentError("worktree_owner_unreadable") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "release_id",
            "repository",
            "path",
            "commit",
        }:
            raise DeploymentError("worktree_owner_invalid")
        return value


def stage_owned_paths(
    repository: str | Path,
    paths: Sequence[str],
    *,
    secret_canaries: Sequence[str] = (),
) -> tuple[str, ...]:
    """Stage explicit files only; preserve all unstaged and out-of-scope edits."""

    repo = _repository_root(repository)
    if not paths or len(paths) != len(set(paths)):
        raise DeploymentError("owned_paths_invalid")
    normalized: list[str] = []
    for value in paths:
        if not isinstance(value, str) or "\\" in value:
            raise DeploymentError("owned_path_invalid")
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or any(character in value for character in "*?[]:")
        ):
            raise DeploymentError("owned_path_invalid")
        candidate = repo.joinpath(*relative.parts)
        current = repo
        for part in relative.parts:
            current = current / part
            if _has_reparse_point(current):
                raise DeploymentError("owned_path_reparse_point")
        try:
            candidate.resolve(strict=False).relative_to(repo)
        except (OSError, RuntimeError, ValueError) as exc:
            raise DeploymentError("owned_path_outside_repository") from exc
        if candidate.exists() and not candidate.is_file():
            raise DeploymentError("owned_path_must_be_file")
        tracked = (
            subprocess.run(
                ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", value],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            ).returncode
            == 0
        )
        if not candidate.is_file() and not tracked:
            raise DeploymentError("owned_path_unavailable")
        normalized.append(relative.as_posix())
    staged_before = _nul_paths(_git(repo, "diff", "--cached", "--name-only", "-z"))
    if staged_before:
        raise DeploymentError("preexisting_staged_changes")
    pathspecs = [f":(literal){value}" for value in normalized]
    try:
        _git(repo, "add", "--", *pathspecs, failure_code="owned_paths_stage_failed")
        staged = _nul_paths(_git(repo, "diff", "--cached", "--name-only", "-z"))
        if not staged or not staged.issubset(set(normalized)):
            raise DeploymentError("staged_paths_outside_owned_scope")
        canaries = tuple(value.encode("utf-8") for value in secret_canaries)
        for value in staged:
            blob = _git(
                repo, "show", f":{value}", failure_code="staged_blob_unavailable"
            )
            if any(canary and canary in blob for canary in canaries) or any(
                pattern.search(blob) for pattern in _SECRET_PATTERNS
            ):
                raise DeploymentError("staged_secret_detected")
        _git(
            repo,
            "diff",
            "--cached",
            "--check",
            failure_code="staged_diff_check_failed",
        )
        return tuple(sorted(staged))
    except DeploymentError:
        _git(
            repo,
            "restore",
            "--staged",
            "--",
            *pathspecs,
            failure_code="owned_paths_unstage_failed",
        )
        raise


def commit_owned_paths(
    repository: str | Path,
    paths: Sequence[str],
    *,
    message: str,
    secret_canaries: Sequence[str] = (),
) -> str:
    repo = _repository_root(repository)
    if not isinstance(message, str) or not message.strip() or len(message) > 200:
        raise DeploymentError("commit_message_invalid")
    stage_owned_paths(repo, paths, secret_canaries=secret_canaries)
    _git(repo, "commit", "-m", message, failure_code="git_commit_failed")
    return _commit(repo, _text(_git(repo, "rev-parse", "HEAD")))


def push_candidate_commit(
    repository: str | Path,
    *,
    remote_name: str,
    branch: str,
    commit: str,
    store: MaintenanceStore,
    release_id: str,
) -> CandidatePushResult:
    """Push to a work branch; this never marks a release as deployed."""

    repo = _repository_root(repository)
    basis = store.get_release_deployment_basis(release_id=release_id)
    pinned = _commit(repo, commit)
    if pinned != basis["result_commit"]:
        raise DeploymentError("candidate_commit_not_approved")
    ref = _ref(remote_name, branch)
    _git(
        repo,
        "push",
        remote_name,
        f"{pinned}:{ref}",
        failure_code="git_push_failed",
    )
    pushed = _remote_head(repo, remote_name, branch)
    if pushed != pinned:
        raise DeploymentError("candidate_push_unverified")
    return CandidatePushResult("pushed_candidate", pinned, remote_name, branch)


class DeploymentCoordinator:
    """Integrate and activate one exact reviewed commit under a lease."""

    def __init__(
        self,
        *,
        store: MaintenanceStore,
        repository: str | Path,
        worktree_root: str | Path,
        daily_active_probe: Callable[[], bool | None],
        pending_probe: Callable[[], PendingSnapshot | None],
        runtime_switcher: RuntimeSwitcher | None,
        exclusion_guard: DeploymentExclusionGuard | None,
        clock: Callable[[], datetime] | None = None,
        diagnostic_trace: MaintenanceDiagnosticTrace | None = None,
    ) -> None:
        self.store = store
        self.repository = _repository_root(repository)
        self.worktrees = ManagedWorktreeManager(self.repository, worktree_root)
        self.daily_active_probe = daily_active_probe
        self.pending_probe = pending_probe
        self.runtime_switcher = runtime_switcher
        self.exclusion_guard = exclusion_guard
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._diagnostic_trace = diagnostic_trace or MaintenanceDiagnosticTrace(
            store.database_path.parent.parent
        )

    def _now(self) -> str:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise DeploymentError("deployment_clock_invalid")
        return value.isoformat()

    def _daily_idle(self) -> bool:
        try:
            return self.daily_active_probe() is False
        except Exception:
            return False

    def _pending(self) -> PendingSnapshot | None:
        try:
            state = self.pending_probe()
        except Exception:
            return None
        return state if isinstance(state, PendingSnapshot) else None

    def _blocked(
        self,
        *,
        deployment_id: str,
        pushed: str | None,
        active: str | None,
        reason: str,
    ) -> DeploymentResult:
        record = self.store.finish_deployment(
            deployment_id=deployment_id,
            status="blocked",
            pushed_commit=pushed,
            active_commit=active,
            reason_code=reason,
            finished_at=self._now(),
        )
        return self._result(record)

    @staticmethod
    def _result(record: DeploymentRecord) -> DeploymentResult:
        return DeploymentResult(
            deployment_id=record.deployment_id,
            release_id=record.release_id,
            status=record.status,
            reason_code=record.reason_code,
            source_commit=record.source_commit or record.result_commit,
            base_commit=record.base_commit or "",
            tested_commit=record.tested_commit or record.result_commit,
            pushed_commit=record.pushed_commit,
            active_commit=record.active_commit,
            remote_name=record.remote_name or "",
            target_branch=record.target_branch or "",
        )

    @audit_maintenance_stage("deployment", finish_event="deployment_result")
    def deploy(
        self,
        *,
        release_id: str,
        tested_version: str,
        reviewed_version: str,
        remote_name: str,
        target_branch: str,
        active_checkout: str | Path,
        expected_target_commit: str,
        owner_id: str,
    ) -> DeploymentResult:
        """Promote only the stored reviewed release and preserve pending actions."""

        try:
            basis = self.store.get_release_deployment_basis(release_id=release_id)
        except MaintenanceStoreError as exc:
            raise DeploymentError(
                getattr(exc, "code", "release_not_authorized")
            ) from exc
        tested = str(basis["result_commit"])
        base = str(basis["base_commit"])
        if (
            tested_version != basis["result_version"]
            or reviewed_version != basis["result_version"]
        ):
            raise DeploymentError("deployment_review_test_version_mismatch")
        if (
            _commit(self.repository, tested) != tested
            or _commit(self.repository, base) != base
        ):
            raise DeploymentError("deployment_commit_unavailable")
        expected = _commit(self.repository, expected_target_commit)
        if expected != base:
            raise DeploymentError("deployment_target_base_mismatch")
        _ref(remote_name, target_branch)
        if self.runtime_switcher is None:
            raise DeploymentError("runtime_switcher_unavailable")
        if self.exclusion_guard is None:
            raise DeploymentError("deployment_exclusion_unavailable")

        owner_start = current_process_start_time()
        if owner_start is None:
            raise DeploymentError("deployment_owner_identity_unavailable")
        try:
            lease = self.store.acquire_lease(
                lease_key="maintenance:deployment",
                owner_id=owner_id,
                owner_pid=os.getpid(),
                owner_start_time=owner_start,
                now=self._now(),
                ttl_seconds=300,
            )
        except (LeaseBusyError, MaintenanceStoreError) as exc:
            raise DeploymentError(
                getattr(exc, "code", "deployment_lease_busy")
            ) from exc

        try:
            guard_acquired = self.exclusion_guard.acquire()
        except Exception as exc:
            try:
                self.store.release_lease(lease=lease, released_at=self._now())
            except MaintenanceStoreError:
                pass
            raise DeploymentError("deployment_exclusion_unavailable") from exc
        if not guard_acquired:
            try:
                self.store.release_lease(lease=lease, released_at=self._now())
            except MaintenanceStoreError:
                pass
            raise DeploymentError("deployment_exclusion_busy")

        deployment_id: str | None = None
        pushed: str | None = None
        previous_active: str | None = None
        runtime_switch_attempted = False
        try:
            deployment_id = self.store.begin_deployment(
                release_id=release_id,
                source_commit=tested,
                base_commit=base,
                tested_commit=tested,
                remote_name=remote_name,
                target_branch=target_branch,
                started_at=self._now(),
            )
            if not self._daily_idle():
                return self._blocked(
                    deployment_id=deployment_id,
                    pushed=None,
                    active=None,
                    reason="daily_owner_active_or_unknown",
                )
            pending_before = self._pending()
            if pending_before is None:
                return self._blocked(
                    deployment_id=deployment_id,
                    pushed=None,
                    active=None,
                    reason="pending_state_unavailable",
                )
            try:
                candidate_path = self.worktrees.open(
                    release_id=release_id, commit=tested
                )
                if _commit(
                    candidate_path, _text(_git(candidate_path, "rev-parse", "HEAD"))
                ) != tested or _status(candidate_path):
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="managed_worktree_not_clean_at_release",
                    )
                active_root = _repository_root(active_checkout)
                if (
                    _text(_git(active_root, "branch", "--show-current"))
                    != target_branch
                ):
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="active_checkout_branch_mismatch",
                    )
                source_remote = _text(
                    _git(self.repository, "remote", "get-url", remote_name)
                )
                active_remote = _text(
                    _git(active_root, "remote", "get-url", remote_name)
                )
                upstream = _text(
                    _git(
                        active_root,
                        "rev-parse",
                        "--abbrev-ref",
                        "--symbolic-full-name",
                        "@{u}",
                    )
                )
                if (
                    source_remote != active_remote
                    or upstream != f"{remote_name}/{target_branch}"
                ):
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="active_tracking_target_mismatch",
                    )
                active_head = _commit(
                    active_root, _text(_git(active_root, "rev-parse", "HEAD"))
                )
                if active_head != base:
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="active_checkout_base_changed",
                    )
                change_paths = _changed_paths(self.repository, base, tested)
                user_paths = _working_paths(active_root)
                if change_paths & user_paths:
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="user_change_intersection",
                    )
                user_status_before = _status(active_root)
                remote_head = _remote_head(self.repository, remote_name, target_branch)
                if remote_head not in {base, tested}:
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="remote_target_changed",
                    )
                if not _is_ancestor(self.repository, base, tested):
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="approved_commit_not_fast_forward",
                    )
                try:
                    previous_active = self.runtime_switcher.current_commit()
                except Exception:
                    previous_active = None
                if previous_active is None:
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="active_commit_unavailable",
                    )
                previous_active = _commit(self.repository, previous_active)
                if remote_head == base:
                    self.store.heartbeat_lease(
                        lease=lease, now=self._now(), ttl_seconds=300
                    )
                    try:
                        _git(
                            self.repository,
                            "push",
                            remote_name,
                            f"{tested}:refs/heads/{target_branch}",
                            failure_code="git_push_failed",
                        )
                    except DeploymentError:
                        return self._blocked(
                            deployment_id=deployment_id,
                            pushed=None,
                            active=previous_active,
                            reason="git_push_failed_or_raced",
                        )
                pushed = _remote_head(self.repository, remote_name, target_branch)
                if pushed != tested:
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active,
                        reason="pushed_commit_unverified",
                    )
                if not self._daily_idle():
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active,
                        reason="daily_owner_started",
                    )
                if self._pending() != pending_before:
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active,
                        reason="pending_state_changed",
                    )
                self.store.heartbeat_lease(
                    lease=lease, now=self._now(), ttl_seconds=300
                )
                try:
                    runtime_switch_attempted = True
                    self.runtime_switcher.activate(tested, candidate_path)
                    switched = self.runtime_switcher.current_commit() == tested
                    healthy = switched and self.runtime_switcher.healthy(tested)
                except Exception:
                    healthy = False
                if not healthy:
                    restored = self._restore_runtime(previous_active)
                    runtime_switch_attempted = False
                    result = self.store.finish_deployment(
                        deployment_id=deployment_id,
                        status="rolled_back" if restored else "blocked",
                        pushed_commit=pushed,
                        active_commit=previous_active if restored else None,
                        reason_code="health_check_failed"
                        if restored
                        else "runtime_restore_failed",
                        finished_at=self._now(),
                    )
                    return self._result(result)
                if not self._daily_idle() or self._pending() != pending_before:
                    restored = self._restore_runtime(previous_active)
                    runtime_switch_attempted = False
                    result = self._blocked(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active if restored else None,
                        reason=(
                            "deployment_exclusion_observation_changed"
                            if restored
                            else "runtime_restore_failed"
                        ),
                    )
                    return result
                try:
                    active_status_changed = _status(active_root) != user_status_before
                except Exception as exc:
                    restored = self._restore_runtime(previous_active)
                    runtime_switch_attempted = False
                    reason = (
                        getattr(exc, "code", "active_checkout_observation_failed")
                        if restored
                        else "runtime_restore_failed"
                    )
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active if restored else None,
                        reason=reason,
                    )
                if active_status_changed:
                    restored = self._restore_runtime(previous_active)
                    runtime_switch_attempted = False
                    result = self._blocked(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active if restored else None,
                        reason=(
                            "active_checkout_changed_during_switch"
                            if restored
                            else "runtime_restore_failed"
                        ),
                    )
                    return result
                result = self.store.finish_deployment(
                    deployment_id=deployment_id,
                    status="deployed",
                    pushed_commit=pushed,
                    active_commit=tested,
                    reason_code=None,
                    finished_at=self._now(),
                    capability=self.store._deployment_capability,
                )
                runtime_switch_attempted = False
                return self._result(result)
            except DeploymentError as exc:
                if runtime_switch_attempted:
                    restored = previous_active is not None and self._restore_runtime(
                        previous_active
                    )
                    runtime_switch_attempted = False
                    return self._blocked(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active if restored else None,
                        reason=(exc.code if restored else "runtime_restore_failed"),
                    )
                return self._blocked(
                    deployment_id=deployment_id,
                    pushed=pushed,
                    active=previous_active,
                    reason=exc.code,
                )
            except Exception as exc:
                if not runtime_switch_attempted:
                    raise
                restored = previous_active is not None and self._restore_runtime(
                    previous_active
                )
                runtime_switch_attempted = False
                raw_reason = getattr(exc, "code", "deployment_runtime_failure")
                reason = (
                    raw_reason
                    if isinstance(raw_reason, str)
                    and _CODE_PATTERN.fullmatch(raw_reason)
                    else "deployment_runtime_failure"
                )
                return self._blocked(
                    deployment_id=deployment_id,
                    pushed=pushed,
                    active=previous_active if restored else None,
                    reason=reason if restored else "runtime_restore_failed",
                )
        finally:
            try:
                self.exclusion_guard.release()
            except Exception:
                pass
            try:
                self.store.release_lease(lease=lease, released_at=self._now())
            except MaintenanceStoreError:
                pass

    def _restore_runtime(self, commit: str) -> bool:
        switcher = self.runtime_switcher
        if switcher is None:
            return False
        try:
            switcher.activate(commit, None)
            return switcher.current_commit() == commit and switcher.healthy(commit)
        except Exception:
            return False
