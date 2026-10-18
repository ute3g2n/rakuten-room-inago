"""Local Git fixtures for the maintenance deployment contract."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.inago.maintenance.deployment import (
    DeploymentCoordinator,
    DeploymentError,
    ManagedWorktreeManager,
    PendingSnapshot,
    commit_owned_paths,
    push_candidate_commit,
    stage_owned_paths,
)
from automation.inago.maintenance.store import MaintenanceStore


def _git(path: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if check and result.returncode:
        raise AssertionError(f"git fixture command failed: {args!r}")
    return result.stdout.strip()


class _Runtime:
    def __init__(self, base: str) -> None:
        self.active = base
        self.unhealthy_commit: str | None = None
        self.switches: list[str] = []

    def current_commit(self) -> str:
        return self.active

    def activate(self, commit: str, checkout: Path | None = None) -> None:
        self.active = commit
        self.switches.append(commit)
        if checkout is not None:
            assert _git(checkout, "rev-parse", "HEAD") == commit

    def healthy(self, expected_commit: str) -> bool:
        return (
            self.active == expected_commit and expected_commit != self.unhealthy_commit
        )


class _Guard:
    def __init__(self) -> None:
        self.held = False

    def acquire(self) -> bool:
        if self.held:
            return False
        self.held = True
        return True

    def release(self) -> None:
        self.held = False


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "--all")
    _git(path, "commit", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _case(tmp_path: Path, *, changed_path: str = "src/fix.py") -> dict[str, object]:
    repo = tmp_path / "candidate"
    repo.mkdir(parents=True)
    _git(tmp_path, "init", "--initial-branch=main", str(repo))
    _git(repo, "config", "user.name", "Fixture Author")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    base = _commit(repo, "base")

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "--set-upstream", "origin", "main")

    active = tmp_path / "active"
    _git(tmp_path, "clone", "--branch", "main", str(remote), str(active))
    _git(active, "config", "user.name", "Fixture Deployer")
    _git(active, "config", "user.email", "deployer@example.invalid")

    _git(repo, "checkout", "-b", "codex/repair")
    changed = repo / Path(changed_path)
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("fixed\n", encoding="utf-8")
    result = _commit(repo, "approved repair")
    _git(repo, "push", "origin", f"{result}:refs/heads/codex/repair")

    runtime_root = tmp_path / "runtime"
    store = MaintenanceStore(
        runtime_root,
        daily_database_path=tmp_path / "daily" / "state.sqlite",
    )
    store.initialize(applied_at="2026-10-18T01:00:00+00:00")
    store.create_release(
        release_id="release-fixture",
        author_id="fixture-author",
        base_commit=base,
        result_commit=result,
        result_version="release-v1",
        config_version="config-v1",
        required_case_ids=("case-fixture",),
        created_at="2026-10-18T01:00:01+00:00",
    )
    store.record_test_result(
        release_id="release-fixture",
        case_id="case-fixture",
        attempt=1,
        result_version="release-v1",
        status="PASS",
        recorded_at="2026-10-18T01:00:02+00:00",
    )
    store.record_release_review(
        release_id="release-fixture",
        reviewer_id="fixture-reviewer",
        reviewed_version="release-v1",
        status="approved",
        reviewed_at="2026-10-18T01:00:03+00:00",
    )
    pending = PendingSnapshot(2, "pending-revision-1")
    daily = {"active": False}
    runtime = _Runtime(base)
    coordinator = DeploymentCoordinator(
        store=store,
        repository=repo,
        worktree_root=tmp_path / "managed-worktrees",
        daily_active_probe=lambda: daily["active"],
        pending_probe=lambda: pending,
        runtime_switcher=runtime,
        exclusion_guard=_Guard(),
    )
    return {
        "repo": repo,
        "remote": remote,
        "active": active,
        "base": base,
        "result": result,
        "store": store,
        "pending": pending,
        "daily": daily,
        "runtime": runtime,
        "coordinator": coordinator,
        "changed_path": changed_path,
    }


def _deploy(case: dict[str, object]):
    return case["coordinator"].deploy(
        release_id="release-fixture",
        tested_version="release-v1",
        reviewed_version="release-v1",
        remote_name="origin",
        target_branch="main",
        active_checkout=case["active"],
        expected_target_commit=case["base"],
        owner_id="fixture-deployer",
    )


def test_c38_contract(tmp_path: Path) -> None:
    case = _case(tmp_path)
    repo = case["repo"]
    source_user_change = repo / "README.md"
    source_user_change.write_text("user change stays here\n", encoding="utf-8")
    manager = ManagedWorktreeManager(repo, tmp_path / "managed")

    worktree = manager.open(release_id="release-fixture", commit=case["result"])
    reused = manager.open(release_id="release-fixture", commit=case["result"])

    assert reused == worktree
    assert _git(worktree, "rev-parse", "HEAD") == case["result"]
    assert not _git(worktree, "status", "--porcelain")
    assert source_user_change.read_text(encoding="utf-8") == "user change stays here\n"
    other = manager.open(release_id="another-release", commit=case["base"])
    assert other != worktree

    (worktree / "local-note.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(DeploymentError, match="managed_worktree_dirty"):
        manager.open(release_id="release-fixture", commit=case["result"])
    assert (worktree / "local-note.txt").read_text(encoding="utf-8") == "keep"


def test_c39_contract(tmp_path: Path) -> None:
    case = _case(tmp_path)
    guard = case["coordinator"].exclusion_guard
    case["coordinator"].exclusion_guard = None
    with pytest.raises(DeploymentError, match="deployment_exclusion_unavailable"):
        _deploy(case)
    case["coordinator"].exclusion_guard = guard
    case["daily"]["active"] = True

    waiting = _deploy(case)

    assert waiting.status == "blocked"
    assert waiting.reason_code == "daily_owner_active_or_unknown"
    assert _git(case["remote"], "rev-parse", "refs/heads/main") == case["base"]
    assert case["pending"] == PendingSnapshot(2, "pending-revision-1")
    assert case["runtime"].active == case["base"]

    case["daily"]["active"] = False
    completed = _deploy(case)

    assert completed.status == "deployed"
    assert completed.pushed_commit == completed.active_commit == case["result"]
    assert case["pending"] == PendingSnapshot(2, "pending-revision-1")


def test_c40_contract(tmp_path: Path) -> None:
    nonintersecting = _case(tmp_path / "nonintersecting")
    active_notes = nonintersecting["active"] / "README.md"
    active_notes.write_text(
        "user deletion/change is represented here\n", encoding="utf-8"
    )
    before = _git(nonintersecting["active"], "diff", "--binary", "HEAD")

    result = _deploy(nonintersecting)

    assert result.status == "deployed"
    assert _git(nonintersecting["active"], "diff", "--binary", "HEAD") == before
    assert (
        active_notes.read_text(encoding="utf-8")
        == "user deletion/change is represented here\n"
    )

    intersecting = _case(tmp_path / "intersecting")
    collision = intersecting["active"] / Path(intersecting["changed_path"])
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_text("user-owned conflicting file\n", encoding="utf-8")
    before = collision.read_bytes()

    held = _deploy(intersecting)

    assert held.status == "blocked"
    assert held.reason_code == "user_change_intersection"
    assert collision.read_bytes() == before
    assert (
        _git(intersecting["remote"], "rev-parse", "refs/heads/main")
        == intersecting["base"]
    )


def test_c41_contract(tmp_path: Path) -> None:
    case = _case(tmp_path)
    racer = tmp_path / "racer"
    _git(tmp_path, "clone", "--branch", "main", str(case["remote"]), str(racer))
    _git(racer, "config", "user.name", "Remote Racer")
    _git(racer, "config", "user.email", "racer@example.invalid")
    (racer / "remote-change.txt").write_text("new remote head\n", encoding="utf-8")
    raced = _commit(racer, "remote advanced")
    _git(racer, "push", "origin", "main")

    result = _deploy(case)

    assert result.status == "blocked"
    assert result.reason_code == "remote_target_changed"
    assert _git(case["remote"], "rev-parse", "refs/heads/main") == raced
    assert case["runtime"].active == case["base"]

    push_failed = _case(tmp_path / "push-failed")
    _git(
        push_failed["repo"],
        "remote",
        "set-url",
        "--push",
        "origin",
        str(tmp_path / "unavailable-push-target.git"),
    )
    result = _deploy(push_failed)
    assert result.status == "blocked"
    assert result.reason_code == "git_push_failed_or_raced"
    assert (
        _git(push_failed["remote"], "rev-parse", "refs/heads/main")
        == push_failed["base"]
    )


def test_c42_contract(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(DeploymentError, match="candidate_commit_not_approved"):
        push_candidate_commit(
            case["repo"],
            remote_name="origin",
            branch="codex/other",
            commit=case["base"],
            store=case["store"],
            release_id="release-fixture",
        )
    candidate = push_candidate_commit(
        case["repo"],
        remote_name="origin",
        branch="codex/repair",
        commit=case["result"],
        store=case["store"],
        release_id="release-fixture",
    )

    assert candidate.status == "pushed_candidate"
    assert candidate.active is False
    assert _git(case["remote"], "rev-parse", "refs/heads/main") == case["base"]
    assert case["store"].counts()["deployment"] == 0

    result = _deploy(case)

    assert result.status == "deployed"
    assert result.source_commit == result.tested_commit == result.pushed_commit
    assert result.active_commit == result.tested_commit == case["result"]
    record = case["store"].get_deployment(deployment_id=result.deployment_id)
    assert record is not None
    assert record.source_commit == record.tested_commit == record.pushed_commit
    assert record.active_commit == record.result_commit
    gate = case["store"].evaluate_release_gate(
        release_id="release-fixture", evaluated_at="2026-10-18T01:01:00+00:00"
    )
    assert gate.status == "ready"


def test_c43_contract(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case["runtime"].unhealthy_commit = case["result"]

    result = _deploy(case)

    assert result.status == "rolled_back"
    assert result.reason_code == "health_check_failed"
    assert result.pushed_commit == case["result"]
    assert result.active_commit == case["base"]
    assert case["runtime"].active == case["base"]
    assert case["runtime"].switches[-2:] == [case["result"], case["base"]]
    assert case["pending"] == PendingSnapshot(2, "pending-revision-1")
    record = case["store"].get_deployment(deployment_id=result.deployment_id)
    assert record is not None and record.status == "rolled_back"


def test_c57_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(tmp_path, "init", "--initial-branch=main", str(repo))
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "owned.py").write_text("before = True\n", encoding="utf-8")
    (repo / "user.md").write_text("untouched\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "owned.py").write_text("after = True\n", encoding="utf-8")
    (repo / "user.md").write_text("user edit\n", encoding="utf-8")

    staged = stage_owned_paths(repo, ("owned.py",))

    assert staged == ("owned.py",)
    assert _git(repo, "diff", "--cached", "--name-only") == "owned.py"
    assert _git(repo, "diff", "--name-only") == "user.md"
    _git(repo, "commit", "-m", "owned change")
    assert (repo / "user.md").read_text(encoding="utf-8") == "user edit\n"

    (repo / "owned.py").write_text("release = True\n", encoding="utf-8")
    assert commit_owned_paths(repo, ("owned.py",), message="owned change 2")
    (repo / "owned.py").write_text("secret = 'private-canary-7f33'\n", encoding="utf-8")
    with pytest.raises(DeploymentError, match="staged_secret_detected"):
        stage_owned_paths(
            repo,
            ("owned.py",),
            secret_canaries=("private-canary-7f33",),
        )
    assert not _git(repo, "diff", "--cached", "--name-only")
    assert _git(repo, "log", "-1", "--format=%s") == "owned change 2"
