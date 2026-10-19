"""U16 report contracts over local, synthetic source records."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from automation.inago.maintenance.detector import preclassify_daily_outcome
from automation.inago.maintenance.reporting import (
    ReportContractError,
    finalize_report_review,
    prepare_incident_report,
    publish_reviewed_report,
    validate_report_review,
)
from tests_inago.maintenance_detector_support import (
    CHECKED_AT,
    LAUNCH_ID,
    TARGET_DATE,
    clone_case,
)


_IDENTITY = {
    "incident_id": "INC-20261017-001",
    "source_launch_id": "launch-20261017-001",
    "source_run_id": None,
    "target_date": "2026-10-17",
    "job_id": "job-20261017-001",
}
_RESULT_COMMIT = "b" * 40
_BASE_COMMIT = "a" * 40
_EVIDENCE = "evidence/U16/source.json"


def _job_context() -> dict[str, object]:
    return {
        **_IDENTITY,
        "attempt_id": "attempt-U16-001",
        "base_commit": _BASE_COMMIT,
        "input_refs": ["evidence/U16/input.json"],
        "evidence_refs": [
            _EVIDENCE,
            "evidence/U16/investigation.md",
            "evidence/U16/plan.md",
            "evidence/U16/tests.xml",
            "evidence/U16/deployment.json",
            "evidence/U16/index.md",
        ],
        "allowed_outputs": [
            "job-20261017-001/attempt-U16-001/report.html",
            "job-20261017-001/attempt-U16-001/result.json",
        ],
    }


def _source_snapshot() -> dict[str, object]:
    return {
        "identity": deepcopy(_IDENTITY),
        "investigation": {
            "result_version": "investigation-v1",
            "review_status": "approved",
            "href": "evidence/U16/investigation.md",
        },
        "plan": {
            "result_version": "plan-v1",
            "review_status": "approved",
            "href": "evidence/U16/plan.md",
        },
        "implementation": {
            "base_commit": _BASE_COMMIT,
            "result_commit": _RESULT_COMMIT,
            "review_status": "approved",
        },
        "tests": {
            "status": "passed",
            "result_version": "tests-v1",
            "summary": "8件PASS、失敗・未実施なし",
            "evidence_href": "evidence/U16/tests.xml",
        },
        "deployment": {
            "status": "not_attempted",
            "tested_commit": None,
            "pushed_commit": None,
            "active_commit": None,
            "evidence_href": "evidence/U16/deployment.json",
        },
        "next_day": {
            "status": "pending",
            "observed_at": None,
            "evidence_href": None,
        },
        "evidence_index_href": "evidence/U16/index.md",
        "evidence_index_label": "U16 evidence index",
    }


def _report_response() -> dict[str, object]:
    return {
        "schema_version": "maintenance-report-response-v1",
        "identity": deepcopy(_IDENTITY),
        "result_version": "report-v1",
        "created_at_jst": "2026-10-18T11:00:00+09:00",
        "title": "日次処理の結果確認と修正状況",
        "summary": "fixtureでの確認結果です。本番反映と次回日次確認はまだ行っていません。",
        "issues": [
            {
                "event_class": "observed",
                "phase": "結果の保存",
                "title": "日次処理の結果を保存できない",
                "operation": "日次処理の結果を書き込む操作を行いました。",
                "expected": "当日分の結果が保存されること。",
                "actual": "当日分の確定は0件で、処理はfailedでした。",
                "impact": "当日の結果を確定できませんでした。",
                "recovery": "再試行による回復は確認していません。",
                "cause_confidence": "limited",
                "cause_statement": "記録された失敗位置までは確認しました。根本原因は未確定です。",
                "changed_behavior": "同じ状況では詳細な確認結果を記録して停止します。",
                "confirmation_scope": "ローカルfixtureでの通常pytestのみを確認しました。",
                "evidence_ref": _EVIDENCE,
            }
        ],
        "flow_tree": "開始\n├─ 結果保存: 失敗を記録\n└─ 本番反映: 未実施",
        "checks": [
            {
                "stage": "ローカルfixture",
                "version": "tests-v1",
                "expected": "契約に沿って未確認を保留する",
                "actual": "期待どおり未確認を保留しました。",
                "status": "passed",
                "evidence_ref": "evidence/U16/tests.xml",
            }
        ],
        "recovery": {
            "unknowns": ["本番反映後の動作", "次回日次の再発有無"],
            "stop_reason": None,
            "resume_condition": "本番反映と次回の日次結果が確認できた時点で更新します。",
            "side_effects": "本番日次、投稿、稼働DBは変更していません。",
        },
    }


def _prepared_report(response: dict[str, object] | None = None):
    root = Path(__file__).resolve().parents[1]
    return prepare_incident_report(
        _job_context(),
        _source_snapshot(),
        response or _report_response(),
        template_path=root / "docs/templates/incidents/日次不具合対応レポート_ひな形.html",
    )


def test_c55_contract():
    completion, daily_result, manifest = clone_case(status="failed", run_id=None)
    daily_result["phase_results"]["trace"].update(
        {"status": "not_written", "event_count": 0, "reference": None}
    )
    manifest["correlation"].pop("source_run_id", None)

    # This U11 case covers the current-run detector only. U16 owns the report
    # view that compares today's result with prior dashboard totals.
    result = preclassify_daily_outcome(
        expected_target_date=TARGET_DATE,
        expected_source_launch_id=LAUNCH_ID,
        completion=completion,
        daily_result=daily_result,
        evidence_manifest=manifest,
        checked_at=CHECKED_AT,
    )

    assert result["preclassification"] == "needs_investigation"
    assert result["check_status"] == "abnormal"
    assert result["source_run_id"] is None
    assert result["counts"]["committed_count"] == 0
    assert result["counts"]["error_count"] == 1
    assert result["phase_results"]["export"] == "ready"
    assert "previous_dashboard_count" not in result
    assert result["routing"]["steps_started"] == []

    report_response = _report_response()
    report_response["issues"][0]["actual"] = f"本日: failed、確定0件。前日画面: 12件。"
    report = _prepared_report(report_response)
    assert "本日: failed、確定0件" in report.html
    assert "前日画面: 12件" in report.html
    assert "本番反映: 未実施" in report.html
    assert report.report_status == "pending"


def test_c56_report_marks_recurrent_observation_blocked():
    sources = _source_snapshot()
    sources["next_day"] = {
        "status": "recurred",
        "observed_at": "2026-10-18T13:10:00+09:00",
        "evidence_href": _EVIDENCE,
    }
    report = prepare_incident_report(
        _job_context(),
        sources,
        _report_response(),
        template_path=(
            Path(__file__).resolve().parents[1]
            / "docs/templates/incidents/日次不具合対応レポート_ひな形.html"
        ),
    )
    assert report.report_status == "blocked"
    assert "recurred" in report.result_json


def test_c44_contract():
    report = _prepared_report()
    assert report.report_status == "pending"
    assert "操作・期待・実結果" in report.html
    assert "当日分の結果が保存されること。" in report.html
    assert "当日分の確定は0件で、処理はfailedでした。" in report.html
    assert "影響と回復" in report.html
    assert "変更後の動作" in report.html
    assert "本番反映と次回日次確認はまだ行っていません" in report.html
    assert "{{" not in report.html
    assert '"stage_status":"ready_for_review"' in report.result_json


def test_c45_contract():
    report = _prepared_report()
    assert (
        ".tree { white-space: pre; overflow-wrap: normal; overflow-x: auto;"
        in report.html
    )
    assert "code, pre { font-family:" in report.html
    assert (
        'code, pre { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }'
        in report.html
    )
    assert "@media (max-width: 520px)" in report.html
    assert "pre, code { white-space: pre-wrap; }" in report.html
    assert ".tree, pre, code { white-space: pre-wrap; }" not in report.html
    assert "@media print" in report.html
    assert "├─ 結果保存" in report.html
    ids = set(re.findall(r'\bid="([^"]+)"', report.html))
    anchors = re.findall(r'href="#([^"]+)"', report.html)
    assert anchors
    assert set(anchors).issubset(ids)
    assert 'href="javascript:' not in report.html


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_c46_contract(tmp_path: Path):
    bare = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)], check=True, capture_output=True
    )
    _git(repo, "config", "user.name", "Report Fixture")
    _git(repo, "config", "user.email", "report-fixture@example.invalid")
    (repo / "code.py").write_text("base = True\n", encoding="utf-8")
    _git(repo, "add", "code.py")
    _git(repo, "commit", "-m", "base")
    old_base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "main")
    (repo / "code.py").write_text("base = True\nfix = True\n", encoding="utf-8")
    _git(repo, "add", "code.py")
    _git(repo, "commit", "-m", "reviewed fix")
    code_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "push", "origin", "main")

    report_output_path = "reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート.html"
    report_file = repo.joinpath(*report_output_path.split("/"))
    report_file.parent.mkdir(parents=True)
    report_file.write_bytes(b"<html>approved</html>\n")
    digest = hashlib.sha256(report_file.read_bytes()).hexdigest()
    review_document = {
        "schema_version": "maintenance-report-review-v1",
        "reviewer_id": "report-reviewer",
        "reviewed_version": "report-v1",
        "reviewed_sha256": digest,
        "status": "approved",
        "reviewed_at": "2026-10-18T11:05:00+09:00",
        "findings": [],
    }
    published = publish_reviewed_report(
        repo,
        report_path=report_output_path,
        expected_target_commit=old_base,
        source_code_commit=code_commit,
        result_version="report-v1",
        review_document=review_document,
        author_id="report-writer",
        remote_name="origin",
        target_branch="main",
        commit_message="docs: publish reviewed report",
    )
    resumed = publish_reviewed_report(
        repo,
        report_path=report_output_path,
        expected_target_commit=old_base,
        source_code_commit=code_commit,
        result_version="report-v1",
        review_document=review_document,
        author_id="report-writer",
        remote_name="origin",
        target_branch="main",
        commit_message="docs: publish reviewed report",
    )
    assert resumed == published
    detached_retry = tmp_path / "detached-report-retry"
    _git(repo, "worktree", "add", "--detach", str(detached_retry), old_base)
    detached_report = detached_retry.joinpath(*report_output_path.split("/"))
    detached_report.parent.mkdir(parents=True)
    detached_report.write_bytes(b"<html>approved</html>\n")
    detached_resumed = publish_reviewed_report(
        detached_retry,
        report_path=report_output_path,
        expected_target_commit=old_base,
        source_code_commit=code_commit,
        result_version="report-v1",
        review_document=review_document,
        author_id="report-writer",
        remote_name="origin",
        target_branch="main",
        commit_message="docs: publish reviewed report",
    )
    assert detached_resumed == published
    assert published.base_commit == code_commit
    assert published.code_commit == code_commit
    assert published.commit != code_commit
    assert (
        _git(
            repo,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-z",
            "-r",
            published.commit,
        ).split("\x00")[0]
        == report_output_path
    )
    assert (
        _git(repo, "ls-remote", "--heads", "origin", "refs/heads/main").split()[0]
        == published.commit
    )

    with pytest.raises(ReportContractError, match="report_review_invalid"):
        publish_reviewed_report(
            repo,
            report_path=report_output_path,
            expected_target_commit=published.base_commit,
            source_code_commit=code_commit,
            result_version="report-v1",
            review_document={**review_document, "reviewer_id": "report-writer"},
            author_id="report-writer",
            remote_name="origin",
            target_branch="main",
            commit_message="docs: invalid self review",
        )


def test_report_review_must_bind_author_version_and_hash():
    report = _prepared_report()
    html_hash = report.html_sha256
    review = {
        "schema_version": "maintenance-report-review-v1",
        "reviewer_id": "reviewer-1",
        "reviewed_version": "report-v1",
        "reviewed_sha256": html_hash,
        "status": "approved",
        "reviewed_at": "2026-10-18T11:05:00+09:00",
        "findings": [],
    }
    validated = validate_report_review(
        review,
        author_id="writer-1",
        result_version="report-v1",
        output_sha256=html_hash,
    )
    assert validated["status"] == "approved"
    finalized = finalize_report_review(report, review, author_id="writer-1")
    assert '"stage_status":"reviewed"' in finalized.result_json
    assert '"next_action":"publish"' in finalized.result_json
    assert finalized.html_sha256 == report.html_sha256
    with pytest.raises(ReportContractError, match="report_review_not_independent"):
        validate_report_review(
            review,
            author_id="reviewer-1",
            result_version="report-v1",
            output_sha256=html_hash,
        )
    with pytest.raises(ReportContractError, match="report_review_version_mismatch"):
        validate_report_review(
            review,
            author_id="writer-1",
            result_version="report-v2",
            output_sha256=html_hash,
        )


def test_report_accepts_unapproved_status_but_rejects_unsafe_links_and_identity_drift():
    sources = _source_snapshot()
    sources["plan"]["review_status"] = "changes_requested"
    prepare_incident_report(
        _job_context(),
        sources,
        _report_response(),
        template_path=Path(__file__).resolve().parents[1]
        / "docs/templates/incidents/日次不具合対応レポート_ひな形.html",
    )
    response = _report_response()
    response["issues"][0]["evidence_ref"] = "javascript:alert(1)"
    with pytest.raises(ReportContractError, match="report_link_invalid"):
        _prepared_report(response)
    response = _report_response()
    response["identity"]["source_run_id"] = "guessed-run"
    with pytest.raises(ReportContractError, match="report_identity_mismatch"):
        _prepared_report(response)


def test_verified_next_day_requires_jst_date_evidence_and_complete_template(
    tmp_path: Path,
):
    root = Path(__file__).resolve().parents[1]
    template_path = root / "docs/templates/incidents/日次不具合対応レポート_ひな形.html"
    job = _job_context()
    next_day_ref = "evidence/U16/next-day.json"
    job["evidence_refs"].append(next_day_ref)
    response = _report_response()
    sources = _source_snapshot()
    sources["tests"]["status"] = "passed"
    sources["deployment"].update(
        {
            "status": "deployed",
            "tested_commit": _RESULT_COMMIT,
            "pushed_commit": _RESULT_COMMIT,
            "active_commit": _RESULT_COMMIT,
        }
    )

    for observed_at, evidence_href, error_code in (
        ("", next_day_ref, "report_next_day_observation_missing"),
        (
            "2026-10-17T12:00:00+09:00",
            next_day_ref,
            "report_next_day_observation_date_mismatch",
        ),
        ("2026-10-18T12:00:00+09:00", None, "report_next_day_evidence_missing"),
    ):
        invalid_sources = deepcopy(sources)
        invalid_sources["next_day"] = {
            "status": "verified",
            "observed_at": observed_at,
            "evidence_href": evidence_href,
        }
        with pytest.raises(ReportContractError, match=error_code):
            prepare_incident_report(
                job, invalid_sources, response, template_path=template_path
            )

    sources["next_day"] = {
        "status": "verified",
        "observed_at": "2026-10-18T12:00:00+09:00",
        "evidence_href": next_day_ref,
    }
    completed = prepare_incident_report(
        job, sources, response, template_path=template_path
    )
    assert '"report_status":"complete"' in completed.result_json
    assert f'href="{next_day_ref}"' in completed.html

    incomplete_template = tmp_path / "incomplete.html"
    incomplete_template.write_text(
        template_path.read_text(encoding="utf-8").replace("{{flow_tree}}", ""),
        encoding="utf-8",
    )
    with pytest.raises(ReportContractError, match="report_template_invalid"):
        prepare_incident_report(
            job, sources, response, template_path=incomplete_template
        )
