import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from automation.inago.maintenance.deployment import prepare_repair_candidate_commit
from automation.inago.maintenance.store import MaintenanceStore


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def test_repair_candidate_commit_preserves_source_and_reuses_exact_ref(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "candidate-repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "Test Owner")
    _git(repo, "config", "user.email", "test@example.invalid")
    product = repo / "automation.py"
    product.write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "automation.py")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    product.write_text("value = 2\n", encoding="utf-8")
    test_source = repo / "tests_inago" / "test_candidate.py"
    test_source.parent.mkdir()
    test_source.write_text("def test_candidate():\n    assert True\n", encoding="utf-8")
    source_status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    product_hashes = {"automation.py": hashlib.sha256(product.read_bytes()).hexdigest()}
    source_diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary", base, "--", "automation.py"],
        capture_output=True,
        check=True,
    ).stdout
    expected_diff_hash = hashlib.sha256(
        source_diff
        + json.dumps(
            product_hashes,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    ref = "refs/maintenance/candidates/" + hashlib.sha256(b"candidate").hexdigest()

    candidate = prepare_repair_candidate_commit(
        repo,
        repo,
        base_commit=base,
        product_paths=("automation.py",),
        product_file_sha256=product_hashes,
        expected_product_diff_sha256=expected_diff_hash,
        candidate_ref=ref,
        commit_timestamp=datetime(2026, 10, 19, tzinfo=timezone.utc),
    )

    assert _git(repo, "rev-parse", "HEAD") == base
    assert (
        _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == source_status
    )

    store = MaintenanceStore(tmp_path / "maintenance-data")
    when = datetime(2026, 10, 19, tzinfo=timezone.utc)
    store.initialize(applied_at=when)
    store.create_release(
        release_id="base-release",
        author_id="base-author",
        base_commit=base,
        result_commit=base,
        result_version="base-version",
        config_version="config-v1",
        required_case_ids=("base-case",),
        created_at=when,
    )
    store.record_test_result(
        release_id="base-release",
        case_id="base-case",
        attempt=1,
        result_version="base-version",
        status="PASS",
        evidence_ref="base/test-ledger.json",
        recorded_at=when,
    )
    store.record_release_review(
        release_id="base-release",
        reviewer_id="base-reviewer",
        reviewed_version="base-version",
        status="approved",
        reviewed_at=when,
    )
    candidate_release = {
        "release_id": "candidate-release",
        "author_id": "repair-author",
        "reviewer_id": "repair-reviewer",
        "base_commit": base,
        "result_commit": candidate,
        "result_version": "repair-version",
        "config_version": "config-v1",
        "required_case_ids": ("repair-case",),
        "evidence_ref": "job/attempt/test-ledger.json",
        "recorded_at": when,
    }
    first = store.register_repair_candidate_release(**candidate_release)
    resumed_release = store.register_repair_candidate_release(**candidate_release)
    basis = store.get_release_deployment_basis(release_id="candidate-release")
    assert first == resumed_release
    assert basis["base_commit"] == base
    assert basis["result_commit"] == candidate
    assert basis["result_version"] == "repair-version"
    assert basis["author_id"] == "repair-author"
    assert _git(repo, "rev-parse", f"{candidate}^") == base
    assert _git(repo, "diff", "--name-only", base, candidate) == "automation.py"
    assert _git(repo, "show", f"{candidate}:automation.py") == "value = 2"
    candidate_paths = _git(repo, "ls-tree", "-r", "--name-only", candidate)
    assert "tests_inago/test_candidate.py" not in candidate_paths.splitlines()

    resumed = prepare_repair_candidate_commit(
        repo,
        repo,
        base_commit=base,
        product_paths=("automation.py",),
        product_file_sha256=product_hashes,
        expected_product_diff_sha256=expected_diff_hash,
        candidate_ref=ref,
        commit_timestamp=datetime(2026, 10, 19, tzinfo=timezone.utc),
    )
    assert resumed == candidate
    assert (
        _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == source_status
    )
