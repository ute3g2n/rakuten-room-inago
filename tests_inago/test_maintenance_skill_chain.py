"""Focused synthetic tests for the approved eight-role maintenance runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from automation.inago.maintenance import skill_chain


JOB_ID = "job-historical-9e7f0c175cc4c874c955a5dab29c4427"
LAUNCH_ID = "historical-9e7f0c175cc4c874c955a5dab29c4427"
TARGET_DATE = "2026-10-17"
IDENTITY = {
    "job_id": JOB_ID,
    "source_launch_id": LAUNCH_ID,
    "target_date": TARGET_DATE,
    "source_run_id": None,
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    plan_relative = Path("plans/projects/maintenance/保守フローskill直列化と10月17日実動完遂_実行計画_2026-10-20.md")
    target_plan = root / plan_relative
    target_plan.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / plan_relative, target_plan)
    job = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID
    job.mkdir(parents=True)
    (job / "incident.md").write_text(
        "- 対象job: `" + JOB_ID + "`\n"
        "- 対象起動: `" + LAUNCH_ID + "`\n"
        "- 対象日: " + TARGET_DATE + "\n"
        "- 日次run: `null` のまま。\n",
        encoding="utf-8",
    )
    return root, job


class _Turn:
    def __init__(self, final_text: str, thread_id: str, turn_id: str, events=()):
        self.final_text = final_text
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.events = tuple(events)
        self.observed_model = "synthetic"
        self.observed_effort = "high"
        self.observed_service_tier = "priority"


class _SyntheticSession:
    def __init__(self, *, role_name: str, role_number: int, job: Path, repository_root: Path, call_number: int, invalid_first: bool = False, read_mode: str = "success", reporter_behavior: str | None = None, reporter_recovery_behavior: str = "success", reporter_instance: int = 1, call_trace: list[int] | None = None):
        self.role_name = role_name
        self.role_number = role_number
        self.job = job
        self.repository_root = repository_root
        self.call_number = call_number
        self.invalid_first = invalid_first
        self.read_mode = read_mode
        self.reporter_behavior = reporter_behavior
        self.reporter_recovery_behavior = reporter_recovery_behavior
        self.reporter_instance = reporter_instance
        self.call_trace = call_trace if call_trace is not None else []
        self.calls = 0
        self.turn_events = []
        self.log_write_failed = False
        self.capability_probe_observations = {"model": {"status": "unavailable", "code": "probe_offline"}}

    def run_turn(self, prompt, _schema, **_kwargs):
        self.calls += 1
        self.call_trace.append(self.role_number)
        if self.role_number == 6:
            behavior = (
                self.reporter_behavior
                if self.reporter_instance == 1 and self.calls == 1
                else self.reporter_recovery_behavior
            )
            if behavior == "transport_failure":
                raise RuntimeError("synthetic reporter transport failure")
            if self.reporter_instance > 1 and behavior == "failure":
                return _Turn("{}", f"thread-{self.call_number}", f"turn-{self.call_number}-recovery-invalid")
            if self.reporter_instance == 1 and self.reporter_behavior == "invalid_handoff" and self.calls <= 2:
                return _Turn("{}", f"thread-{self.call_number}", f"turn-{self.call_number}-invalid")
        if self.invalid_first and self.calls == 1:
            return _Turn("{}", f"thread-{self.call_number}", f"turn-{self.call_number}-invalid")
        index = self.role_number
        predecessor = "incident.md" if index == 1 else skill_chain.ROLE_CALLS[index - 2].outputs[0]
        predecessor_bytes = (self.job / predecessor).read_bytes()
        predecessor_hash = _sha(predecessor_bytes)
        if index == 1:
            summary = "調査の合成結果"
        else:
            if self.calls == 1:
                assert str(self.job / predecessor) in prompt
                assert predecessor_hash in prompt
            summary = f"前担当の値:{predecessor_hash}"
        ack = f"\n\n## 前担当Markdown実読証跡\n- {predecessor}\n- SHA-256: {predecessor_hash}\n- 読み取った事実: {summary}\n"

        if index in (1, 2, 3, 4, 5):
            filename = skill_chain.ROLE_CALLS[index - 1].outputs[0]
            (self.job / filename).write_text(f"# {filename}\n{summary}{ack}", encoding="utf-8")
        elif index == 6 and self.reporter_instance == 1 and self.calls == 1 and self.reporter_behavior == "omit_report_html":
            (self.job / "report.md").write_text(
                f"# レポート\n{summary}{ack}\n", encoding="utf-8"
            )
        elif index == 6 and (self.reporter_instance > 1 or self.calls > 1) and self.reporter_recovery_behavior == "success":
            report = self.job / "report.html"
            report.write_text(
                "<!doctype html><html lang=\"ja\"><body>recovered_by_synthetic_author</body></html>",
                encoding="utf-8",
            )
            html_hash = _sha(report.read_bytes())
            (self.job / "report.md").write_text(
                f"# レポート\nagent recovery{ack}\nfinal_html_sha256: {html_hash}\n",
                encoding="utf-8",
            )
        elif index == 6 and self.calls == 1:
            report = self.job / "report.html"
            report.write_text(
                "<!doctype html><html lang=\"ja\"><body><h1>確認結果</h1>"
                "<p>原因は未確認です。</p><p>実施した変更は未確認です。</p>"
                "<p>次回の調査記録を追加しました。</p></body></html>",
                encoding="utf-8",
            )
            html_hash = _sha(report.read_bytes())
            (self.job / "report.md").write_text(
                f"# レポート\n{summary}{ack}\nfinal_html_sha256: {html_hash}\n",
                encoding="utf-8",
            )
        elif index == 6:
            report = self.job / "report.html"
            html_hash = _sha(report.read_bytes())
            (self.job / "report.md").write_text(
                f"# レポート\n{summary}{ack}\nfinal_html_sha256: {html_hash}\n",
                encoding="utf-8",
            )
        elif index == 7:
            report_path = self.job / "report.html"
            report_hash = _sha(report_path.read_bytes()) if report_path.is_file() else "0" * 64
            round_text = "\nreview_round: final_after_author" if "new/current" in prompt else ""
            report_md_hash = _sha((self.job / "report.md").read_bytes())
            (self.job / "html-review.md").write_text(
                f"# HTML確認\n4項目を確認しました。\n前担当report.md SHA-256: {report_md_hash}\n"
                f"前担当から読み取った事実: 本文の4項目を確認する\nreviewed_html_sha256: {report_hash}\n"
                f"review_result: approved{round_text}\n",
                encoding="utf-8",
            )
        else:
            report_path = self.job / "report.html"
            report_hash = _sha(report_path.read_bytes()) if report_path.is_file() else "0" * 64
            public = self.repository_root / skill_chain._PUBLIC_REPORT
            public.parent.mkdir(parents=True, exist_ok=True)
            public.write_bytes((self.job / "report.html").read_bytes())
            (self.job / "publication.md").write_text(
                f"# 公開記録\n{summary}{ack}\npublication_result: succeeded\n"
                f"reviewed_html_sha256: {report_hash}\npublished_html_sha256: {report_hash}\n"
                f"remote_html_sha256: {report_hash}\n",
                encoding="utf-8",
            )
        next_role = skill_chain.ROLE_CALLS[index].name if index < len(skill_chain.ROLE_CALLS) else "none"
        handoff = json.dumps({"担当": next_role, "入力": predecessor, "依頼": summary}, ensure_ascii=False)
        predecessor_path = self.job / predecessor
        command_path = predecessor_path if self.read_mode != "mismatched" else self.job / "wrong-predecessor.md"
        read_events = []
        if self.read_mode != "missing":
            read_events.append({
                "method": "item/completed",
                "params": {
                    "threadId": f"thread-{self.call_number}",
                    "turnId": f"turn-{self.call_number}-{self.calls}",
                    "item": {
                        "type": "commandExecution",
                        "command": f'Get-Content -Raw -LiteralPath "{command_path}"',
                        "status": "failed" if self.read_mode == "failed" else "completed",
                        "exitCode": 1 if self.read_mode == "failed" else 0,
                    },
                },
            })
        self.turn_events.append(tuple(read_events))
        return _Turn(handoff, f"thread-{self.call_number}", f"turn-{self.call_number}-{self.calls}", read_events)

    def close(self):
        pass


class _SessionList(list):
    def __init__(self):
        super().__init__()
        self.call_trace: list[int] = []


def _factory(root: Path, job: Path, *, fail_roles: set[int] | None = None, invalid_roles: set[int] | None = None, read_modes: dict[int, str] | None = None, reporter_behavior: str | None = None, reporter_recovery_behavior: str = "success"):
    sessions = _SessionList()
    fail_roles = fail_roles or set()
    invalid_roles = invalid_roles or set()
    read_modes = read_modes or {}
    name_counts: dict[str, int] = {}

    def factory(**kwargs):
        role_name = kwargs["role_name"]
        instance = name_counts.get(role_name, 0) + 1
        name_counts[role_name] = instance
        if role_name == "rr-inago-maintenance-delivery":
            role_number = 5 if instance == 1 else 8
        else:
            role_number = next(i for i, role in enumerate(skill_chain.ROLE_CALLS, start=1) if role.name == role_name)
        session = _SyntheticSession(
            role_name=role_name,
            role_number=role_number,
            job=job,
            repository_root=root,
            call_number=len(sessions) + 1,
            invalid_first=role_number in invalid_roles,
            read_mode=read_modes.get(role_number, "success"),
            reporter_behavior=reporter_behavior if role_number == 6 else None,
            reporter_recovery_behavior=reporter_recovery_behavior,
            reporter_instance=instance if role_number == 6 else 1,
            call_trace=sessions.call_trace,
        )
        if role_number in fail_roles:
            def failed(*_args, **_kwargs):
                raise RuntimeError("synthetic failure with sensitive-looking text")
            session.run_turn = failed
        sessions.append(session)
        return session

    return factory, sessions


def _run(root: Path, factory):
    return skill_chain.run_skill_chain(
        repository_root=root,
        **IDENTITY,
        session_factory=factory,
    )


def test_eight_roles_receive_exact_markdown(tmp_path: Path, monkeypatch):
    root, job = _workspace(tmp_path)
    monkeypatch.setattr(skill_chain, "_executable", lambda: "synthetic-codex")
    factory, sessions = _factory(root, job)
    result = _run(root, factory)
    events = [json.loads(line) for line in (job / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    finished = [event for event in events if event.get("event") == "role_call_finished"]

    assert [event["role_call"] for event in finished] == list(range(1, 9))
    assert len(sessions) == 8
    assert result["role_calls_attempted"] == 8
    assert all(event["previous_read_acknowledged"] for event in finished)
    for prior, current in zip(finished, finished[1:]):
        prior_md = prior["output_refs"][0]
        if prior["role_call"] == 7:
            rereview = next(event for event in events if event.get("event") == "html_rereview_finished")
            prior_md = rereview["output_refs"][0]
        current_input = next(
            ref for ref in current["input_refs"]
            if Path(ref["path"]).name == Path(prior_md["path"]).name
        )
        assert current_input["sha256"] == prior_md["sha256"]
        current_markdown = next(ref for ref in current["output_refs"] if ref["path"].endswith(".md"))
        saved = (root / current_markdown["path"]).read_bytes()
        if current["role_call"] == 7:
            # The required final author revision and independent re-review
            # replace role 7's first Markdown file. Preserve the immutable
            # role-call input evidence as the assertion for that read.
            assert any(
                evidence["path"] == current_input["path"]
                and evidence["sha256"] == current_input["sha256"]
                and evidence["method"] == "item/completed"
                and evidence["item_type"] == "commandExecution"
                and evidence["status"] == "completed"
                and evidence["exit_code"] == 0
                for evidence in current["previous_read_evidence"]
            )
            continue
        assert current_input["sha256"].encode() in saved
    assert result["terminal"]["status"] == "complete", result["terminal"]
    assert (root / skill_chain._PUBLIC_REPORT).read_bytes() == (job / "report.html").read_bytes()


def test_non_gating_status_and_early_end_continue_remaining_roles(tmp_path: Path, monkeypatch):
    root, job = _workspace(tmp_path)
    monkeypatch.setattr(skill_chain, "_executable", lambda: "synthetic-codex")
    factory, sessions = _factory(root, job, fail_roles={1, 3})
    result = _run(root, factory)
    events = [json.loads(line) for line in (job / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    finished = [event for event in events if event.get("event") == "role_call_finished"]

    assert [event["role_call"] for event in finished] == list(range(1, 9))
    assert len(sessions) == 8
    assert finished[0]["turn_status"] == "failed"
    assert all((job / name).is_file() and (job / name).stat().st_size for name in skill_chain._WORKFLOW_OUTPUTS)
    assert (job / "publication.md").is_file()
    assert result["status"] == "incomplete"
    assert "synthetic failure with sensitive-looking text" not in (job / "calls.jsonl").read_text(encoding="utf-8")
    assert "RuntimeError" in (job / "calls.jsonl").read_text(encoding="utf-8")


def test_operation_failures_keep_raw_and_continue(tmp_path: Path, monkeypatch):
    root, job = _workspace(tmp_path)
    monkeypatch.setattr(skill_chain, "_executable", lambda: "synthetic-codex")
    factory, sessions = _factory(root, job, fail_roles={1}, invalid_roles={2})
    _run(root, factory)
    events = [json.loads(line) for line in (job / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    finished = [event for event in events if event.get("event") == "role_call_finished"]

    assert [event["role_call"] for event in finished] == list(range(1, 9))
    assert sessions[1].calls == 2
    assert finished[0]["turn_status"] == "failed"
    assert finished[1]["turn_status"] == "completed"
    assert finished[1]["turn_number"] == 2
    safe_ledger = (job / "calls.jsonl").read_text(encoding="utf-8")
    assert "synthetic failure with sensitive-looking text" not in safe_ledger
    assert "RuntimeError" in safe_ledger


def test_resume_same_job_without_predecessor_pin(tmp_path: Path, monkeypatch):
    root, job = _workspace(tmp_path)
    monkeypatch.setattr(skill_chain, "_executable", lambda: "synthetic-codex")
    broken_factory, first_sessions = _factory(root, job, fail_roles={1})
    first = _run(root, broken_factory)
    assert first["status"] == "incomplete"
    initial_job_count = len(list((root / "rakuten-room-inago/runtime/maintenance-flow").iterdir()))
    resumed_factory, second_sessions = _factory(root, job)
    resumed = _run(root, resumed_factory)
    events = [json.loads(line) for line in (job / "calls.jsonl").read_text(encoding="utf-8").splitlines()]

    assert resumed["resumed"] is True
    assert resumed["resume_from_role"] == 1
    assert resumed["source_run_id"] is None
    assert len(first_sessions) == 8 and len(second_sessions) == 8
    assert len(list((root / "rakuten-room-inago/runtime/maintenance-flow").iterdir())) == initial_job_count == 1
    assert any(event.get("event") == "chain_resumed" and event.get("resume_from_role") == 1 for event in events)
    assert sum(event.get("event") == "chain_started" for event in events) == 1


def test_resume_after_final_review_reuses_author_and_reviewer_outputs(tmp_path: Path, monkeypatch):
    root, job = _workspace(tmp_path)
    monkeypatch.setattr(skill_chain, "_executable", lambda: "synthetic-codex")
    factory, _sessions = _factory(root, job)
    result = _run(root, factory)
    assert result["status"] == "complete", result["terminal"]

    report_before = (job / "report.html").read_bytes()
    review_before = (job / "html-review.md").read_bytes()
    (root / skill_chain._PUBLIC_REPORT).write_bytes(b"public readback mismatch")
    events = skill_chain._read_events(
        job / "calls.jsonl", result["chain_started_at"]
    )

    start_role, reason = skill_chain._resume_start_role(
        job,
        root,
        events,
        IDENTITY,
        result["chain_started_at"],
    )

    assert (start_role, reason) == (8, "skill_chain_publication_bytes_mismatch")
    assert (job / "report.html").read_bytes() == report_before
    assert (job / "html-review.md").read_bytes() == review_before

    report_markdown = (job / "report.md").read_bytes()
    (job / "report.md").write_bytes(
        report_markdown + b"\n# changed after final author revision\n"
    )
    changed_start_role, changed_reason = skill_chain._resume_start_role(
        job,
        root,
        events,
        IDENTITY,
        result["chain_started_at"],
    )
    assert (changed_start_role, changed_reason) == (6, "role_call_incomplete")


def test_appserver_best_effort_logging_and_advisory_probe(tmp_path: Path, monkeypatch):
    from automation.inago.app_server import AppServerError, AppServerSession

    session = AppServerSession(
        command=("unused",), cwd=tmp_path, log_path=tmp_path / "log.jsonl",
        log_root=tmp_path, model="m", effort="high", service_tier="priority",
        fast_mode=True, role_name="test", developer_instructions="test",
        advisory_capability_probes=True, best_effort_logging=True,
    )
    session._run_capability_probe(
        "model", lambda: (_ for _ in ()).throw(AppServerError("probe_offline"))
    )
    assert session.capability_probe_observations["model"]["status"] == "unavailable"
    original_open = Path.open
    def failed_open(_path, *_args, **_kwargs):
        raise OSError("private raw failure")
    monkeypatch.setattr(Path, "open", failed_open)
    session._log("turn_finished", role_name="test")
    assert session.log_write_failed is True
    monkeypatch.setattr(Path, "open", original_open)


def test_scope_preserves_excluded_date_and_daily_data(tmp_path: Path):
    from automation.inago.maintenance.worker import WORKER_DISPATCH_EXCLUDED_DATES

    assert WORKER_DISPATCH_EXCLUDED_DATES == frozenset({"2026-10-19"})
    class Store:
        def __init__(self):
            self.calls = []
        def list_workflow_jobs_for_launch(self, **kwargs):
            self.calls.append(kwargs)
            return []

    store = Store()
    callback = skill_chain.build_skill_chain_workflow_provider(store, repository_root=tmp_path)
    check = type("Check", (), {"check_id": "synthetic-check"})()
    arguments = {"check": check, "queue": object(), "lease": object(), "cancel_event": object()}
    excluded = type("Launch", (), {"target_date": "2026-10-19", "source_launch_id": "old", "source_run_id": None})()
    excluded_result = callback(excluded, **arguments)
    assert excluded_result.reason_code == "worker_dispatch_date_excluded"
    assert store.calls == []

    assigned = type("Launch", (), {"target_date": TARGET_DATE, "source_launch_id": LAUNCH_ID, "source_run_id": None})()
    assigned_result = callback(assigned, **arguments)
    assert assigned_result.reason_code == "existing_job_missing_or_ambiguous"
    assert store.calls == [{"source_launch_id": LAUNCH_ID, "source_run_id": None, "target_date": TARGET_DATE}]


def test_production_route_is_separate_from_legacy_controller():
    import inspect
    source = inspect.getsource(skill_chain.build_skill_chain_workflow_provider)
    assert "MaintenanceController" not in source
    assert "workflow_reconciliation" not in source
    assert "source_run_id" in source
    assert "worker_dispatch_date_excluded" in source


def test_contract_roles_and_generated_flow_are_synchronized(tmp_path: Path):
    root, _job = _workspace(tmp_path)
    prompts = skill_chain._extract_role_prompts(
        root / "plans/projects/maintenance/保守フローskill直列化と10月17日実動完遂_実行計画_2026-10-20.md"
    )
    assert len(prompts) == len(skill_chain.ROLE_CALLS) == 8
    assert [role.number for role in skill_chain.ROLE_CALLS] == list(range(1, 9))
    assert all(prompt.strip() for prompt in prompts.values())
    config_names = {role.config_name for role in skill_chain.ROLE_CALLS}
    assert "rr-inago-maintenance-delivery.toml" in config_names
    generated = Path(__file__).resolve().parents[1] / "docs/specifications/inago/イナゴスキル要件定義書.html"
    html = generated.read_text(encoding="utf-8")
    assert "2.59" in html
    assert "F12.svg" in html


@pytest.mark.parametrize("remote_mode", ["available", "unavailable"])
def test_git_failure_preserves_scope_and_resumes(tmp_path: Path, remote_mode: str):
    bare = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Synthetic Tester"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "tester@example.invalid"], check=True, capture_output=True)
    (repo / "task.txt").write_text("baseline\n", encoding="utf-8")
    (repo / "user.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", "task.txt", "user.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "baseline"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main"], check=True, capture_output=True)
    (repo / "task.txt").write_text("task change\n", encoding="utf-8")
    (repo / "user.txt").write_text("user change\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", "task.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "task change"], check=True, capture_output=True)
    if remote_mode == "unavailable":
        subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", str(tmp_path / "missing.git")], check=True, capture_output=True)
    pushed = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True)

    status = subprocess.run(["git", "-C", str(repo), "status", "--short"], check=True, capture_output=True, text=True).stdout
    assert " M user.txt" in status
    assert "task.txt" not in status
    if remote_mode == "available":
        assert pushed.returncode == 0
        remote_task = subprocess.run(["git", "--git-dir", str(bare), "show", "main:task.txt"], check=True, capture_output=True, text=True).stdout
        assert remote_task == "task change\n"
    else:
        assert pushed.returncode != 0
        assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout == " M user.txt\n"


@pytest.mark.parametrize("primary_failure", ["transport_failure", "invalid_handoff", "omit_report_html"])
@pytest.mark.parametrize("recovery_outcome", ["success", "failure"])
def test_reporter_failure_gets_same_author_recovery_then_later_roles(
    tmp_path: Path,
    monkeypatch,
    primary_failure: str,
    recovery_outcome: str,
):
    root, job = _workspace(tmp_path)
    monkeypatch.setattr(skill_chain, "_executable", lambda: "synthetic-codex")
    factory, sessions = _factory(
        root,
        job,
        reporter_behavior=primary_failure,
        reporter_recovery_behavior=recovery_outcome,
    )
    result = _run(root, factory)
    role_names = [session.role_name for session in sessions]
    reporter_calls = [session for session in sessions if session.role_number == 6]

    assert any(session.role_number == 7 for session in sessions)
    assert any(session.role_number == 8 for session in sessions)
    assert reporter_calls
    first_reviewer_call = sessions.call_trace.index(7)
    reporter_attempts_before_review = sessions.call_trace[:first_reviewer_call].count(6)
    assert reporter_attempts_before_review >= (3 if primary_failure == "invalid_handoff" else 2)
    if recovery_outcome == "success":
        assert b"recovered_by_synthetic_author" in (job / "report.html").read_bytes()
        assert result["status"] == "complete"
    else:
        assert result["status"] == "incomplete"
        assert not (job / "report.html").is_file() or b"recovered_by_synthetic_author" not in (job / "report.html").read_bytes()


@pytest.mark.parametrize("read_mode", ["success", "missing", "failed", "mismatched"])
def test_previous_read_acknowledgement_requires_successful_exact_path_event(
    tmp_path: Path,
    monkeypatch,
    read_mode: str,
):
    root, job = _workspace(tmp_path)
    monkeypatch.setattr(skill_chain, "_executable", lambda: "synthetic-codex")
    factory, sessions = _factory(root, job, read_modes={2: read_mode})
    _run(root, factory)
    events = [json.loads(line) for line in (job / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    role_two = next(event for event in events if event.get("event") == "role_call_finished" and event.get("role_call") == 2)

    assert role_two["previous_read_acknowledged"] is (read_mode == "success")
    if read_mode == "success":
        read_event = sessions[1].turn_events[0][0]
        assert read_event["method"] == "item/completed"
        assert read_event["params"]["item"]["type"] == "commandExecution"
        assert read_event["params"]["item"]["status"] == "completed"
        assert read_event["params"]["item"]["exitCode"] == 0
        assert str(job / "investigation.md") in read_event["params"]["item"]["command"]
