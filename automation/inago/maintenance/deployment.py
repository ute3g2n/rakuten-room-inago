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
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol, Sequence

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


class FileDeploymentExclusionGuard:
    """A process-wide OS lock shared by deployment and runtime mutators."""

    def __init__(self, lock_path: str | Path) -> None:
        self.lock_path = Path(lock_path).expanduser()
        self._handle = None
        self._state_lock = threading.Lock()

    def acquire(self) -> bool:
        if self._handle is not None or not self._state_lock.acquire(blocking=False):
            return False
        handle = None
        lock_attempted = False
        try:
            if _has_reparse_point(self.lock_path):
                raise DeploymentError("deployment_exclusion_path_invalid")
            handle = self.lock_path.open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            lock_attempted = True
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            elif os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise DeploymentError("deployment_exclusion_unavailable")
        except OSError as exc:
            try:
                if handle is not None:
                    handle.close()
            except OSError:
                pass
            self._state_lock.release()
            if lock_attempted:
                return False
            raise DeploymentError("deployment_exclusion_unavailable") from exc
        except (ImportError, DeploymentError) as exc:
            try:
                if handle is not None:
                    handle.close()
            except OSError:
                pass
            self._state_lock.release()
            if isinstance(exc, DeploymentError):
                raise
            raise DeploymentError("deployment_exclusion_unavailable") from exc
        self._handle = handle
        return True

    def __enter__(self) -> FileDeploymentExclusionGuard:
        if self._handle is not None:
            raise DeploymentError("deployment_exclusion_reentrant")
        while not self.acquire():
            time.sleep(0.1)
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                handle.close()
            finally:
                self._state_lock.release()


def observe_daily_owner_active(runtime_root: str | Path) -> bool | None:
    """Return a read-only active-run observation; uncertainty remains unknown."""

    from automation.inago.production_runtime import _has_reparse_point
    from automation.inago.run_lock import observe_process_owner, read_lock_owner

    root = Path(runtime_root).expanduser()
    owner_path = root / ".daily-run-owner.json"
    lock_path = root / ".daily-run.lock"
    try:
        if (
            _has_reparse_point(root)
            or _has_reparse_point(owner_path)
            or _has_reparse_point(lock_path)
            or not root.is_dir()
        ):
            return None
        owner_observation = read_lock_owner(owner_path)
        with os.scandir(root) as entries:
            pending_owner_markers = tuple(
                entry.name
                for entry in entries
                if entry.name.startswith(f"{owner_path.name}.pending.")
            )
        if pending_owner_markers:
            return None
        if (
            owner_observation.status == "present"
            and owner_observation.owner is not None
        ):
            process = observe_process_owner(owner_observation.owner)
            if process.status == "alive":
                return True
            if process.status not in {"dead"}:
                return None
        elif owner_observation.status != "missing":
            return None
        if not lock_path.exists():
            return False
        if _has_reparse_point(lock_path):
            return None
        if os.name != "nt":
            return None
        try:
            handle = lock_path.open("r+b")
        except FileNotFoundError:
            return False
        try:
            handle.seek(0)
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        except OSError:
            return None
        finally:
            handle.close()
    except (OSError, RuntimeError, ValueError, DeploymentError):
        return None


def observe_pending_action_snapshot(runtime_root: str | Path) -> PendingSnapshot | None:
    """Hash the pending human-action set from one read-only SQLite snapshot."""

    from automation.inago.operations import open_readonly_operations_connection

    database_path = Path(runtime_root).expanduser() / "state.sqlite"
    try:
        connection = open_readonly_operations_connection(database_path)
        try:
            rows = connection.execute(
                "SELECT action_id, action, offer_key, receipt_revision "
                "FROM human_action WHERE apply_status = 'pending' ORDER BY action_id"
            ).fetchall()
            payload = [
                [
                    str(row["action_id"]),
                    str(row["action"]),
                    str(row["offer_key"]),
                    int(row["receipt_revision"]),
                ]
                for row in rows
            ]
        finally:
            connection.close()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return None
    except Exception:
        return None
    revision = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return PendingSnapshot(len(payload), revision)


class RuntimeSwitcher(Protocol):
    """Host-owned code switcher. It must switch code only, never restore a DB."""

    def current_commit(self) -> str | None:
        ...

    def activate(self, commit: str, checkout: Path | None = None) -> None:
        ...

    def healthy(self, expected_commit: str) -> bool:
        ...

    def confirm_active(self, commit: str) -> None:
        """Finalize switch bookkeeping after the deployment row is durable."""
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


def prepare_repair_candidate_commit(
    repository: str | Path,
    source_worktree: str | Path,
    *,
    base_commit: str,
    product_paths: Sequence[str],
    product_file_sha256: Mapping[str, str],
    expected_product_diff_sha256: str,
    candidate_ref: str,
    commit_timestamp: datetime,
) -> str:
    """Create or reuse a local candidate commit without changing its source tree."""

    repo = _repository_root(repository)
    source = _repository_root(source_worktree)
    base = _commit(repo, base_commit)
    if (
        _commit(source, _text(_git(source, "rev-parse", "HEAD"))) != base
        or not isinstance(candidate_ref, str)
        or re.fullmatch(r"refs/maintenance/candidates/[a-f0-9]{64}", candidate_ref)
        is None
        or not isinstance(expected_product_diff_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_product_diff_sha256) is None
        or not isinstance(commit_timestamp, datetime)
        or commit_timestamp.tzinfo is None
    ):
        raise DeploymentError("repair_candidate_identity_invalid")
    try:
        repo_common = Path(
            _text(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        ).resolve(strict=True)
        source_common = Path(
            _text(
                _git(source, "rev-parse", "--path-format=absolute", "--git-common-dir")
            )
        ).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DeploymentError("repair_candidate_repository_unavailable") from exc
    if repo_common != source_common:
        raise DeploymentError("repair_candidate_repository_mismatch")

    paths: list[str] = []
    for value in product_paths:
        if (
            not isinstance(value, str)
            or not value
            or "\\" in value
            or any(character in value for character in "*?[]:")
        ):
            raise DeploymentError("repair_candidate_path_invalid")
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.as_posix() != value
        ):
            raise DeploymentError("repair_candidate_path_invalid")
        current = source
        for part in relative.parts:
            current = current / part
            if _has_reparse_point(current):
                raise DeploymentError("repair_candidate_path_reparse_point")
        paths.append(value)
    if not paths or len(paths) != len(set(paths)):
        raise DeploymentError("repair_candidate_path_set_invalid")
    if set(product_file_sha256) - set(paths) or any(
        not isinstance(path, str)
        or not isinstance(value, str)
        or re.fullmatch(r"[a-f0-9]{64}", value) is None
        for path, value in product_file_sha256.items()
    ):
        raise DeploymentError("repair_candidate_file_hash_invalid")
    pathspecs = [f":(literal){path}" for path in sorted(paths)]

    def checked_diff(commit: str) -> bytes:
        return _git(repo, "diff", "--binary", base, commit, "--", *pathspecs)

    def verify_candidate(commit: str) -> str:
        exact = _commit(repo, commit)
        parent = _text(_git(repo, "rev-parse", f"{exact}^"))
        changed = _changed_paths(repo, base, exact)
        diff = checked_diff(exact)
        observed_hash = hashlib.sha256(
            diff
            + json.dumps(
                dict(sorted(product_file_sha256.items())),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        if (
            parent != base
            or changed != set(paths)
            or observed_hash != expected_product_diff_sha256
        ):
            raise DeploymentError("repair_candidate_commit_conflict")
        return exact

    source_status = _status(source)
    source_diff = _git(source, "diff", "--binary", base, "--", *pathspecs)
    source_diff_hash = hashlib.sha256(
        source_diff
        + json.dumps(
            dict(sorted(product_file_sha256.items())),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    if source_diff_hash != expected_product_diff_sha256:
        raise DeploymentError("repair_candidate_source_diff_mismatch")

    existing = _git(
        repo,
        "rev-parse",
        "--verify",
        "--quiet",
        candidate_ref,
        allowed_returncodes=(0, 1),
    )
    if _text(existing):
        candidate = verify_candidate(_text(existing))
        if _status(source) != source_status:
            raise DeploymentError("repair_candidate_source_changed")
        return candidate

    timestamp = commit_timestamp.astimezone(timezone.utc)
    git_date = timestamp.strftime("%a, %d %b %Y %H:%M:%S +0000")
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_INDEX_FILE": "",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Inago Maintenance",
            "GIT_AUTHOR_EMAIL": "maintenance@localhost",
            "GIT_COMMITTER_NAME": "Inago Maintenance",
            "GIT_COMMITTER_EMAIL": "maintenance@localhost",
            "GIT_AUTHOR_DATE": git_date,
            "GIT_COMMITTER_DATE": git_date,
        }
    )
    try:
        with tempfile.TemporaryDirectory(prefix="inago-candidate-") as temporary:
            environment["GIT_INDEX_FILE"] = str(Path(temporary) / "index")

            def run(arguments: Sequence[str], failure_code: str) -> bytes:
                try:
                    result = subprocess.run(
                        ["git", "-C", str(source), *arguments],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=30,
                        check=False,
                        env=environment,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise DeploymentError(failure_code) from exc
                if result.returncode != 0:
                    raise DeploymentError(failure_code)
                return result.stdout

            run(["read-tree", base], "repair_candidate_index_failed")
            run(
                ["--literal-pathspecs", "add", "--all", "--", *sorted(paths)],
                "repair_candidate_paths_stage_failed",
            )
            tree = _text(run(["write-tree"], "repair_candidate_tree_failed"))
            if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", tree):
                raise DeploymentError("repair_candidate_tree_invalid")
            commit_message = (
                f"maintenance repair candidate {candidate_ref.rsplit('/', 1)[-1][:16]}"
            )
            commit = _text(
                run(
                    ["commit-tree", tree, "-p", base, "-m", commit_message],
                    "repair_candidate_commit_failed",
                )
            )
    except DeploymentError:
        raise
    except OSError as exc:
        raise DeploymentError("repair_candidate_temporary_index_failed") from exc
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit):
        raise DeploymentError("repair_candidate_commit_invalid")

    zero = "0" * len(commit)
    try:
        _git(repo, "update-ref", candidate_ref, commit, zero)
    except DeploymentError:
        concurrent = _git(
            repo,
            "rev-parse",
            "--verify",
            "--quiet",
            candidate_ref,
            allowed_returncodes=(0, 1),
        )
        if not _text(concurrent):
            raise
        commit = _text(concurrent)
    candidate = verify_candidate(commit)
    if (
        _status(source) != source_status
        or _commit(source, _text(_git(source, "rev-parse", "HEAD"))) != base
    ):
        raise DeploymentError("repair_candidate_source_changed")
    return candidate


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
        resume_deployment_id: str | None = None,
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

        resume_record = None
        completed_record = None
        if resume_deployment_id is not None:
            try:
                resume_record = self.store.get_deployment(
                    deployment_id=resume_deployment_id
                )
            except MaintenanceStoreError as exc:
                raise DeploymentError(
                    getattr(exc, "code", "deployment_resume_unavailable")
                ) from exc
            if (
                resume_record is None
                or resume_record.status not in {"pending", "deployed"}
                or resume_record.release_id != release_id
                or resume_record.source_commit != tested
                or resume_record.base_commit != base
                or resume_record.tested_commit != tested
                or resume_record.remote_name != remote_name
                or resume_record.target_branch != target_branch
            ):
                raise DeploymentError("deployment_resume_identity_mismatch")

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

        if resume_record is not None:
            try:
                current_record = self.store.get_deployment(
                    deployment_id=resume_record.deployment_id
                )
            except MaintenanceStoreError as exc:
                try:
                    self.store.release_lease(lease=lease, released_at=self._now())
                except MaintenanceStoreError:
                    pass
                raise DeploymentError(
                    getattr(exc, "code", "deployment_resume_unavailable")
                ) from exc
            if (
                current_record is None
                or current_record.release_id != release_id
                or current_record.source_commit != tested
                or current_record.base_commit != base
                or current_record.tested_commit != tested
                or current_record.remote_name != remote_name
                or current_record.target_branch != target_branch
            ):
                try:
                    self.store.release_lease(lease=lease, released_at=self._now())
                except MaintenanceStoreError:
                    pass
                raise DeploymentError("deployment_resume_identity_mismatch")
            if current_record.status == "deployed":
                completed_record = current_record
            elif current_record.status != "pending":
                try:
                    self.store.release_lease(lease=lease, released_at=self._now())
                except MaintenanceStoreError:
                    pass
                return self._result(current_record)
            else:
                resume_record = current_record

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
        production_checkout_advanced = False
        deployment_finalized = False

        def block_or_keep_pending(
            *,
            deployment_id: str,
            pushed: str | None,
            active: str | None,
            reason: str,
        ):
            if production_checkout_advanced:
                raise DeploymentError("deployment_finalize_pending")
            return self._blocked(
                deployment_id=deployment_id,
                pushed=pushed,
                active=active,
                reason=reason,
            )

        try:
            if completed_record is not None:
                if not self._daily_idle():
                    raise DeploymentError("daily_owner_active_or_unknown")
                confirm_active = getattr(self.runtime_switcher, "confirm_active", None)
                if callable(confirm_active):
                    try:
                        confirm_active(tested)
                    except Exception as exc:
                        raise DeploymentError("runtime_task_release_pending") from exc
                return self._result(completed_record)

            deployment_id = (
                resume_record.deployment_id
                if resume_record is not None
                else self.store.begin_deployment(
                    release_id=release_id,
                    source_commit=tested,
                    base_commit=base,
                    tested_commit=tested,
                    remote_name=remote_name,
                    target_branch=target_branch,
                    started_at=self._now(),
                )
            )
            active_root = _repository_root(active_checkout)
            active_head = _commit(
                active_root, _text(_git(active_root, "rev-parse", "HEAD"))
            )
            production_checkout_advanced = base != tested and active_head == tested
            if not self._daily_idle():
                return block_or_keep_pending(
                    deployment_id=deployment_id,
                    pushed=None,
                    active=None,
                    reason="daily_owner_active_or_unknown",
                )
            pending_before = self._pending()
            if pending_before is None:
                return block_or_keep_pending(
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
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="managed_worktree_not_clean_at_release",
                    )
                if (
                    _text(_git(active_root, "branch", "--show-current"))
                    != target_branch
                ):
                    return block_or_keep_pending(
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
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="active_tracking_target_mismatch",
                    )
                active_head = _commit(
                    active_root, _text(_git(active_root, "rev-parse", "HEAD"))
                )
                if active_head != base and not (
                    resume_record is not None and active_head == tested
                ):
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="active_checkout_base_changed",
                    )
                change_paths = _changed_paths(self.repository, base, tested)
                user_paths = _working_paths(active_root)
                if change_paths & user_paths:
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="user_change_intersection",
                    )
                user_status_before = _status(active_root)
                remote_head = _remote_head(self.repository, remote_name, target_branch)
                if remote_head not in {base, tested}:
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="remote_target_changed",
                    )
                if not _is_ancestor(self.repository, base, tested):
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="approved_commit_not_fast_forward",
                    )
                try:
                    observed_active = self.runtime_switcher.current_commit()
                except Exception:
                    observed_active = None
                if observed_active is None:
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=None,
                        reason="active_commit_unavailable",
                    )
                observed_active = _commit(self.repository, observed_active)
                previous_active = (
                    resume_record.previous_active_commit
                    if resume_record is not None
                    else observed_active
                )
                if previous_active is None:
                    if observed_active != base:
                        return block_or_keep_pending(
                            deployment_id=deployment_id,
                            pushed=None,
                            active=observed_active,
                            reason="active_commit_base_mismatch",
                        )
                    try:
                        bound = self.store.bind_deployment_previous_active(
                            deployment_id=deployment_id,
                            lease=lease,
                            previous_active_commit=observed_active,
                            recorded_at=self._now(),
                        )
                    except MaintenanceStoreError as exc:
                        return block_or_keep_pending(
                            deployment_id=deployment_id,
                            pushed=None,
                            active=observed_active,
                            reason=getattr(
                                exc,
                                "code",
                                "deployment_previous_active_unavailable",
                            ),
                        )
                    previous_active = bound.previous_active_commit
                elif previous_active != base or observed_active not in {
                    previous_active,
                    tested,
                }:
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=None,
                        active=observed_active,
                        reason="deployment_resume_runtime_mismatch",
                    )
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
                        return block_or_keep_pending(
                            deployment_id=deployment_id,
                            pushed=None,
                            active=previous_active,
                            reason="git_push_failed_or_raced",
                        )
                pushed = _remote_head(self.repository, remote_name, target_branch)
                if pushed != tested:
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active,
                        reason="pushed_commit_unverified",
                    )
                if not self._daily_idle():
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active,
                        reason="daily_owner_started",
                    )
                if self._pending() != pending_before:
                    return block_or_keep_pending(
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
                    if production_checkout_advanced:
                        runtime_switch_attempted = False
                        raise DeploymentError("deployment_finalize_pending")
                    restored = self._restore_runtime(previous_active)
                    runtime_switch_attempted = False
                    result = self.store.finish_deployment(
                        deployment_id=deployment_id,
                        status="rolled_back" if restored else "blocked",
                        pushed_commit=pushed,
                        active_commit=previous_active if restored else None,
                        reason_code=(
                            "health_check_failed"
                            if restored
                            else "runtime_restore_failed"
                        ),
                        finished_at=self._now(),
                    )
                    return self._result(result)
                if not self._daily_idle() or self._pending() != pending_before:
                    if production_checkout_advanced:
                        runtime_switch_attempted = False
                        raise DeploymentError("deployment_finalize_pending")
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
                    if production_checkout_advanced:
                        runtime_switch_attempted = False
                        raise DeploymentError("deployment_finalize_pending") from exc
                    restored = self._restore_runtime(previous_active)
                    runtime_switch_attempted = False
                    reason = (
                        getattr(exc, "code", "active_checkout_observation_failed")
                        if restored
                        else "runtime_restore_failed"
                    )
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active if restored else None,
                        reason=reason,
                    )
                if active_status_changed:
                    if production_checkout_advanced:
                        runtime_switch_attempted = False
                        raise DeploymentError("deployment_finalize_pending")
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
                active_head = _commit(
                    active_root, _text(_git(active_root, "rev-parse", "HEAD"))
                )
                production_checkout_advanced = production_checkout_advanced or (
                    base != tested and active_head == tested
                )
                if active_head == base:
                    try:
                        _git(
                            active_root,
                            "fetch",
                            "--no-tags",
                            remote_name,
                            f"refs/heads/{target_branch}",
                            failure_code="active_checkout_remote_update_failed",
                        )
                        _git(
                            active_root,
                            "merge",
                            "--ff-only",
                            tested,
                            failure_code="active_checkout_fast_forward_failed",
                        )
                        production_checkout_advanced = True
                        active_head = _commit(
                            active_root,
                            _text(_git(active_root, "rev-parse", "HEAD")),
                        )
                        if active_head != tested:
                            raise DeploymentError(
                                "active_checkout_fast_forward_unverified"
                            )
                    except DeploymentError as exc:
                        if production_checkout_advanced:
                            runtime_switch_attempted = False
                            raise DeploymentError(
                                "deployment_finalize_pending"
                            ) from exc
                        restored = self._restore_runtime(previous_active)
                        runtime_switch_attempted = False
                        return block_or_keep_pending(
                            deployment_id=deployment_id,
                            pushed=pushed,
                            active=previous_active if restored else None,
                            reason=(exc.code if restored else "runtime_restore_failed"),
                        )
                elif resume_record is None or active_head != tested:
                    if production_checkout_advanced:
                        runtime_switch_attempted = False
                        raise DeploymentError("deployment_finalize_pending")
                    restored = self._restore_runtime(previous_active)
                    runtime_switch_attempted = False
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active if restored else None,
                        reason=(
                            "active_checkout_fast_forward_unexpected"
                            if restored
                            else "runtime_restore_failed"
                        ),
                    )
                result = self.store.finish_deployment(
                    deployment_id=deployment_id,
                    status="deployed",
                    pushed_commit=pushed,
                    active_commit=tested,
                    reason_code=None,
                    finished_at=self._now(),
                    capability=self.store._deployment_capability,
                )
                deployment_finalized = True
                runtime_switch_attempted = False
                confirm_active = getattr(self.runtime_switcher, "confirm_active", None)
                if callable(confirm_active):
                    confirm_error: Exception | None = None
                    for _ in range(2):
                        try:
                            confirm_active(tested)
                            confirm_error = None
                            break
                        except Exception as exc:
                            confirm_error = exc
                    if confirm_error is not None:
                        raise DeploymentError(
                            "runtime_task_release_pending"
                        ) from confirm_error
                return self._result(result)
            except DeploymentError as exc:
                if deployment_finalized or completed_record is not None:
                    raise
                if production_checkout_advanced:
                    # Keep scheduler tasks fenced and the deployment pending so
                    # a later run can finish from the already advanced checkout.
                    runtime_switch_attempted = False
                    raise DeploymentError("deployment_finalize_pending") from exc
                if runtime_switch_attempted:
                    restored = previous_active is not None and self._restore_runtime(
                        previous_active
                    )
                    runtime_switch_attempted = False
                    return block_or_keep_pending(
                        deployment_id=deployment_id,
                        pushed=pushed,
                        active=previous_active if restored else None,
                        reason=(exc.code if restored else "runtime_restore_failed"),
                    )
                return block_or_keep_pending(
                    deployment_id=deployment_id,
                    pushed=pushed,
                    active=previous_active,
                    reason=exc.code,
                )
            except Exception as exc:
                if not runtime_switch_attempted:
                    raise
                if production_checkout_advanced:
                    # Do not try to restore base from a checkout already at tested.
                    # The pending deployment and runtime fence are resume state.
                    runtime_switch_attempted = False
                    raise DeploymentError("deployment_finalize_pending") from exc
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
                return block_or_keep_pending(
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
            if switcher.current_commit() != commit:
                return False
            confirm_active = getattr(switcher, "confirm_active", None)
            if callable(confirm_active):
                confirm_active(commit)
            elif not switcher.healthy(commit):
                return False
            return switcher.current_commit() == commit
        except Exception:
            return False
