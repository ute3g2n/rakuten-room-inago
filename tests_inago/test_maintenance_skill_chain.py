"""Focused tests for the seven-role maintenance runner migration."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

from automation.inago.maintenance import skill_chain

JOB_ID = "job-fixture"
LAUNCH_ID = "launch-fixture"
TARGET_DATE = "2026-10-20"
IDENTITY = {
    "job_id": JOB_ID,
    "source_launch_id": LAUNCH_ID,
    "source_run_id": None,
    "target_date": TARGET_DATE,
}


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID).mkdir(parents=True)
    return root


class _Process:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.returncode = 0
        return self.returncode


class _Turn:
    def __init__(self, name: str, response: str = "完了しました"):
        self.final_text = response
        self.thread_id = f"thread-{name}"
        self.turn_id = f"turn-{name}"
        self.events = ({"method": "turn/completed", "params": {"turn": {"status": "completed"}}},)


class _Session:
    def __init__(self, *, role_name: str, cwd: Path, close_error=False, response="完了しました", **kwargs):
        self.role_name = role_name
        self.cwd = Path(cwd)
        self.close_error = close_error
        self.response = response
        self.process_id = 1234
        self.process = _Process(0 if not close_error else None)
        self.safe_stderr_output = ["Bearer abc.def.ghi", "token=canary-secret"]
        self.closed = False
        self.prompts = []
        self.thread_id = None
        self.rollout_path = None

    def start(self, *, deadline):
        del deadline
        self.thread_id = f"thread-{self.role_name}"
        codex_home = Path(os.environ.get("CODEX_HOME", self.cwd / "codex-home"))
        sessions = codex_home / "sessions" / "2026" / "10" / "20"
        sessions.mkdir(parents=True, exist_ok=True)
        self.rollout_path = sessions / f"rollout-2026-10-20T00-00-00-{self.thread_id}.jsonl"
        self.rollout_path.write_bytes(
            json.dumps(
                {"type": "session_meta", "payload": {"id": self.thread_id}},
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        return self.thread_id

    def run_turn(self, prompt, schema=None, **kwargs):
        self.prompts.append(prompt)
        assert '"source_run_id": null' in prompt
        assert "reasoning effortはhigh" in prompt
        role_folder = {
            "rr-inago-incident-investigator": "investigator",
            "common-implementer": "implementer",
            "common-tester": "tester",
            "common-reviewer": "product-reviewer",
            "rr-inago-maintenance-delivery": "delivery",
            "rr-inago-incident-reporter": "reporter",
            "rr-inago-incident-report-reviewer": "html-reviewer",
        }[self.role_name]
        live_copy = (
            self.cwd
            / "rakuten-room-inago"
            / "runtime"
            / "conversation-logs"
            / "maintenance"
            / f"{TARGET_DATE}__{JOB_ID}"
            / role_folder
            / f"main__{self.thread_id}.jsonl"
        )
        assert self.thread_id is not None
        assert live_copy.is_file(), "the watcher should sync after thread start, before the turn"
        index = next(i for i, role in enumerate(skill_chain.ROLE_CALLS, 1) if role.name == self.role_name)
        job_root = self.cwd / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID
        artifact = skill_chain.ROLE_CALLS[index - 1].artifact
        if index == 6:
            report = self.cwd / "reports" / "incidents" / TARGET_DATE / f"{JOB_ID}.html"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("<!doctype html><html lang=ja><body>本文</body></html>", encoding="utf-8")
        elif index == 7:
            (job_root / "html-review.md").write_text("指摘: なし\n", encoding="utf-8")
        else:
            (job_root / artifact).write_text(f"{self.role_name} result\n", encoding="utf-8")
        response = self.response
        if response == "完了しました":
            reported_path = (
                Path("reports") / "incidents" / TARGET_DATE / f"{JOB_ID}.html"
                if index == 6
                else Path("rakuten-room-inago") / "runtime" / "maintenance-flow" / JOB_ID / artifact
            )
            response = f"完了しました。保存先: {reported_path.as_posix()}"
        assert self.rollout_path is not None
        with self.rollout_path.open("ab") as stream:
            stream.write(b"MAINTENANCE_CONVERSATION\n")
        return _Turn(self.role_name, response)

    def close(self):
        self.closed = True
        if self.close_error:
            raise OSError("close failed")
        self.process.wait()


def _factory(root: Path, *, fail: set[str] | None = None, close_error: set[str] | None = None, response="完了しました"):
    made = []
    fail = fail or set()
    close_error = close_error or set()

    def create(**kwargs):
        session = _Session(
            **kwargs,
            close_error=kwargs["role_name"] in close_error,
            response=response,
        )
        if session.role_name in fail:
            def fail_turn(*_args, **_kwargs):
                raise RuntimeError("synthetic failure token=private-value")
            session.run_turn = fail_turn
        made.append(session)
        return session

    return create, made


def _run(root: Path, factory, **kwargs):
    prior_codex_home = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(root / "codex-home")
    try:
        return skill_chain.run_skill_chain(
            repository_root=root,
            **IDENTITY,
            session_factory=factory,
            timeout_seconds=1,
            **kwargs,
        )
    finally:
        if prior_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = prior_codex_home


def test_seven_roles_receive_markdown_and_run_in_order(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    factory, sessions = _factory(root)
    result = _run(root, factory)
    ledger = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID / "calls.jsonl"
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    worker_events = [event for event in events if event.get("event") == "worker_finished"]
    assert [role.name for role in skill_chain.ROLE_CALLS] == [
        "rr-inago-incident-investigator", "common-implementer", "common-tester",
        "common-reviewer", "rr-inago-maintenance-delivery", "rr-inago-incident-reporter",
        "rr-inago-incident-report-reviewer",
    ]
    expected_roles = [role.name for role in skill_chain.ROLE_CALLS]
    expected_roles.extend(expected_roles[5:])
    assert [event["role"] for event in worker_events] == expected_roles
    assert [event["stage"] for event in worker_events] == (
        [role.stage for role in skill_chain.ROLE_CALLS]
        + [role.stage for role in skill_chain.ROLE_CALLS[5:]]
    )
    assert len(sessions) == result["roles_attempted"] == 9
    assert all(s.closed for s in sessions)
    assert all(role["turn_status"] == "completed" for role in result["roles"]), result["roles"]
    assert result["source_run_id"] is None
    assert result["status"] == "complete"
    assert result["reason_code"] is None
    assert (root / "reports" / "incidents" / TARGET_DATE / f"{JOB_ID}.html").is_file()
    terminal = skill_chain.inspect_skill_chain_terminal(
        job_root=ledger.parent, repository_root=root, identity=IDENTITY
    )
    assert terminal["status"] == "complete"
    assert terminal["reason_code"] is None
    assert terminal["recorded_roles"] == 9
    assert terminal["identity"] == IDENTITY
    assert terminal["all_role_save_paths_reported"] is True
    assert terminal["artifact_contents_reinspected"] is False
    assert result["html_review_author_recheck_attempted"] is True
    conversation_root = (
        root
        / "rakuten-room-inago"
        / "runtime"
        / "conversation-logs"
        / "maintenance"
        / f"{TARGET_DATE}__{JOB_ID}"
    )
    investigation_logs = conversation_root / "investigator"
    assert (
        investigation_logs / "main__thread-rr-inago-incident-investigator.jsonl"
    ).read_bytes().endswith(b"MAINTENANCE_CONVERSATION\n")
    assert "thread_id=thread-rr-inago-incident-investigator" in (
        investigation_logs / "status.txt"
    ).read_text(encoding="utf-8")
    assert not (conversation_root / "role-that-was-not-run").exists()
    assert "html-review.md" in sessions[-2].prompts[0]
    assert "定型markerの有無で分岐せず" in sessions[-2].prompts[0]
    assert all(
        all(event.get(key) == value for key, value in IDENTITY.items())
        for event in worker_events
    )


def test_non_gating_status_and_early_end_continue_remaining_roles(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    factory, sessions = _factory(root, fail={skill_chain.ROLE_CALLS[0].name, skill_chain.ROLE_CALLS[2].name})
    result = _run(root, factory)
    assert len(sessions) == result["roles_attempted"] == 9
    expected_roles = [role.name for role in skill_chain.ROLE_CALLS]
    assert [role["role"] for role in result["roles"]] == expected_roles + expected_roles[5:]
    assert result["roles"][0]["error_class"] == "RuntimeError"
    assert result["roles"][1]["turn_status"] == "completed"
    assert result["status"] == "incomplete"
    assert result["reason_code"] == "skill_chain_terminal_incomplete"


def test_operation_failures_keep_safe_record_and_continue(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    factory, sessions = _factory(root, fail={skill_chain.ROLE_CALLS[0].name})
    result = _run(root, factory)
    ledger = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID / "calls.jsonl"
    content = ledger.read_text(encoding="utf-8")
    assert len(sessions) == 9
    assert result["roles"][0]["error_class"] == "RuntimeError"
    assert "private-value" not in content
    assert "RuntimeError" in content
    assert result["roles"][-1]["role"] == skill_chain.ROLE_CALLS[-1].name


@pytest.mark.parametrize("mode", ["empty_ledger", "missing_role", "empty_response"])
def test_empty_or_incomplete_worker_ledger_is_not_terminal_complete(
    tmp_path: Path, mode: str
):
    root = _root(tmp_path)
    job_root = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID
    ledger = job_root / "calls.jsonl"
    if mode != "empty_ledger":
        roles = skill_chain.ROLE_CALLS[:-1] if mode == "missing_role" else skill_chain.ROLE_CALLS
        records = []
        for role in roles:
            records.append(
                {
                    "event": "worker_finished",
                    **IDENTITY,
                    "stage": role.stage,
                    "role": role.name,
                    "turn_status": "completed",
                    "process_returncode": 0,
                    "error_class": None,
                    "close_error_class": None,
                    "response": "" if mode == "empty_response" and role is roles[-1] else "記録あり",
                }
            )
        ledger.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
    terminal = skill_chain.inspect_skill_chain_terminal(
        job_root=job_root, repository_root=root, identity=IDENTITY
    )

    assert terminal["status"] == "incomplete"
    assert terminal["identity"] == IDENTITY
    assert terminal["recorded_roles"] == {
        "empty_ledger": 0,
        "missing_role": 6,
        "empty_response": 7,
    }[mode]

def test_owned_worker_exit_is_reaped_after_close_error(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    factory, sessions = _factory(root, close_error={skill_chain.ROLE_CALLS[0].name})
    result = _run(root, factory, start_role=1)
    first = result["roles"][0]
    assert sessions[0].closed
    assert sessions[0].process.wait_calls == 1
    assert first["process_returncode"] == 0
    assert first["close_error_class"] == "OSError"


def test_owned_worker_timeout_terminates_kills_and_records_exit(
    tmp_path: Path, monkeypatch
):
    root = _root(tmp_path)
    monkeypatch.setattr(
        "automation.inago.maintenance.skill_chain_runtime._codex",
        lambda: "codex-fixture",
    )

    class TimedProcess:
        def __init__(self):
            self.returncode = None
            self.calls = []

        def wait(self, timeout=None):
            self.calls.append(("wait", timeout))
            if self.returncode is not None:
                return self.returncode
            raise subprocess.TimeoutExpired("codex-fixture", timeout)

        def terminate(self):
            self.calls.append(("terminate", None))

        def kill(self):
            self.calls.append(("kill", None))
            self.returncode = 137

    processes = []
    sessions = []
    normal_factory, _normal_sessions = _factory(root)

    class TimedSession:
        def __init__(self, **kwargs):
            self.role_name = kwargs["role_name"]
            self.process_id = 9911
            self.process = TimedProcess()
            self.safe_stderr_output = []
            self.closed = False
            processes.append(self.process)
            sessions.append(self)

        def run_turn(self, *_args, **_kwargs):
            raise TimeoutError("synthetic timeout")

        def close(self):
            self.closed = True
            raise OSError("synthetic close failure")

    first_role = skill_chain.ROLE_CALLS[0].name

    def factory(**kwargs):
        if kwargs["role_name"] == first_role:
            return TimedSession(**kwargs)
        session = normal_factory(**kwargs)
        sessions.append(session)
        return session

    result = _run(root, factory)

    assert len(sessions) == result["roles_attempted"] == 9
    first = result["roles"][0]
    assert first["error_class"] == "TimeoutError"
    assert first["close_error_class"] == "OSError"
    assert first["process_returncode"] == 137
    assert sessions[0].closed
    operations = [name for name, _timeout in processes[0].calls]
    assert "wait" in operations and "terminate" in operations and "kill" in operations
    assert operations.index("wait") < operations.index("terminate") < operations.index("kill")
    ledger = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID / "calls.jsonl"
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    reaped = [record for record in records if record.get("event") == "process_reaped_after_close_error"]
    assert reaped and reaped[-1]["process_returncode"] == 137

def test_resume_same_job_without_predecessor_pin(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    job_root = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID
    prior_events = [
        {
            "event": "worker_finished",
            **IDENTITY,
            "stage": role.stage,
            "role": role.name,
            "turn_status": "completed",
            "process_returncode": 0,
            "error_class": None,
            "close_error_class": None,
            "response": f"{role.name} completed in prior invocation",
            "reported_save_path": f"{job_root.relative_to(root).as_posix()}/{role.artifact}",
        }
        for role in skill_chain.ROLE_CALLS[:2]
    ]
    ledger = job_root / "calls.jsonl"
    ledger.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in prior_events),
        encoding="utf-8",
    )
    factory, sessions = _factory(root)
    result = _run(root, factory, start_role=3)
    assert len(sessions) == result["roles_attempted"] == 7
    resumed_roles = [role.name for role in skill_chain.ROLE_CALLS[2:]]
    assert [role["role"] for role in result["roles"]] == resumed_roles + resumed_roles[-2:]
    assert [session.role_name for session in sessions] == resumed_roles + resumed_roles[-2:]
    assert result["source_run_id"] is None
    assert result["status"] == "complete"


def test_resume_after_final_review_reuses_author_and_reviewer_outputs(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    factory, sessions = _factory(root)
    result = _run(root, factory, start_role=6)
    assert [session.role_name for session in sessions] == [role.name for role in skill_chain.ROLE_CALLS[5:]] * 2
    assert result["roles_attempted"] == 4


def test_maintenance_chain_does_not_generate_content_hashes(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    factory, _sessions = _factory(root)
    _run(root, factory)
    ledger = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID / "calls.jsonl"
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    forbidden = {"sha256", "content_hash", "fingerprint", "bytes", "mtime", "marker"}
    assert all(not forbidden.intersection(record) for record in records)
    assert "hashlib" not in inspect.getsource(skill_chain.run_skill_chain)


def test_appserver_best_effort_logging_and_advisory_probe(tmp_path: Path, monkeypatch):
    from automation.inago.maintenance.skill_chain_runtime import _session
    captured = {}
    def factory(**kwargs):
        captured.update(kwargs)
        return object()
    role = skill_chain.ROLE_CALLS[0]
    _session(tmp_path, tmp_path / "job", role, factory)
    assert captured["model"] == "gpt-6-luna"
    assert captured["effort"] == "high"
    assert captured["advisory_capability_probes"] is True
    assert captured["best_effort_logging"] is True


def test_scope_preserves_excluded_date_and_daily_data(tmp_path: Path):
    from automation.inago.maintenance.worker import WORKER_DISPATCH_EXCLUDED_DATES
    assert WORKER_DISPATCH_EXCLUDED_DATES == frozenset({"2026-10-19"})
    source = inspect.getsource(skill_chain.build_skill_chain_workflow_provider)
    assert "ensure_skill_chain_job_for_launch" in source
    assert "launch.source_run_id" in source


def test_production_route_is_separate_from_legacy_controller():
    source = inspect.getsource(skill_chain.build_skill_chain_workflow_provider)
    assert "MaintenanceController" not in source
    assert "source_launch_id=launch.source_launch_id" in source
    assert "source_run_id=launch.source_run_id" in source
    assert "target_date=launch.target_date" in source


def test_workflow_provider_records_finalization_after_job_is_finalized(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from automation.inago.maintenance import skill_chain_runtime
    from automation.inago.maintenance.worker import WorkerWorkflowOutcome

    root = tmp_path / "repo"
    job = SimpleNamespace(job_id=JOB_ID, created_at="2026-10-21T01:00:00Z")
    run_calls = []

    class Store:
        database_path = root / "rakuten-room-inago" / "runtime" / "maintenance" / "state.sqlite"

        def ensure_skill_chain_job_for_launch(self, **_kwargs):
            return job

        def finalize_job(self, **_kwargs):
            return SimpleNamespace(status="complete")

    monkeypatch.setattr(
        skill_chain_runtime,
        "run_skill_chain",
        lambda **kwargs: run_calls.append(kwargs) or {},
    )
    monkeypatch.setattr(
        skill_chain_runtime,
        "inspect_skill_chain_terminal",
        lambda **_kwargs: {"status": "complete", "reason_code": None},
    )
    provider = skill_chain_runtime.build_skill_chain_workflow_provider(
        Store(), repository_root=root
    )
    launch = SimpleNamespace(
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        target_date=TARGET_DATE,
        status="failed",
        exit_code=4,
    )
    check = SimpleNamespace(check_id="check-fixture")
    queue = SimpleNamespace(queue_id="queue-fixture")

    outcome = provider(launch, check, queue, None)

    assert run_calls[0]["job_created_at"] == job.created_at

    ledger = (
        root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID / "calls.jsonl"
    )
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    finalization = next(
        record for record in records if record.get("event") == "job_finalization_observed"
    )
    assert isinstance(outcome, WorkerWorkflowOutcome)
    assert outcome.status == "complete"
    assert finalization["finalization_status"] == "complete"
    assert {key: finalization[key] for key in IDENTITY} == IDENTITY


def _create_failed_launch(store, tmp_path: Path):
    from datetime import datetime, timezone

    from automation.inago.maintenance.daily_launcher import (
        DailyProcessResult,
        run_daily_launch,
    )

    def failed_daily_process(_command, _cwd, on_started):
        on_started(9501)
        return DailyProcessResult(
            returncode=4,
            stdout=b'{"status":"runtime_error","reason":"SyntheticError"}',
            stderr=b"",
            pid=9501,
        )

    return run_daily_launch(
        store,
        data_root=tmp_path / "runtime",
        config_path=tmp_path / "config.json",
        now=datetime(2026, 10, 20, 4, tzinfo=timezone.utc),
        process_runner=failed_daily_process,
        command=("synthetic-daily-child",),
        cwd=tmp_path,
    )


def test_worker_stage_failure_completes_queue_without_lease(tmp_path: Path):
    from datetime import datetime, timezone

    from automation.inago.maintenance.process_identity import ProcessObservation
    from automation.inago.maintenance.store import MaintenanceStore
    from automation.inago.maintenance.worker import (
        WorkerWorkflowOutcome,
        run_worker_once,
    )

    runtime_root = tmp_path / "runtime"
    store = MaintenanceStore(runtime_root, daily_database_path=tmp_path / "daily" / "state.sqlite")
    store.initialize()
    launch = _create_failed_launch(store, tmp_path)
    seen = {}

    def failed_workflow(received_launch, check, queue, _cancel_event):
        seen.update(launch=received_launch, check=check, queue=queue)
        return WorkerWorkflowOutcome(
            status="blocked",
            check_id=check.check_id,
            reason_code="investigation_inputs_incomplete",
        )

    result = run_worker_once(
        store,
        now=datetime(2026, 10, 21, 0, tzinfo=timezone.utc),
        source_launch_id=launch.launch_id,
        workflow_runner=failed_workflow,
        process_probe=lambda _pid: ProcessObservation("stopped", None),
    )

    queue = store.get_worker_queue(launch.launch_id)
    assert result.status == "maintenance_incomplete"
    assert result.maintenance_resolution_status == "blocked"
    assert result.reason_code == "investigation_inputs_incomplete"
    assert seen["launch"].source_run_id is None
    assert seen["launch"].target_date == "2026-10-20"
    assert queue is not None and queue.status == "complete"
    assert queue.queue_id == seen["queue"].queue_id
    assert store.counts()["lease"] == 0


def test_worker_outer_failure_writes_readable_fallback_with_identity(tmp_path: Path):
    from types import SimpleNamespace

    from automation.inago.maintenance.worker import _record_outer_workflow_failure

    store = SimpleNamespace(
        database_path=tmp_path / "runtime" / "maintenance" / "state.sqlite"
    )
    launch = SimpleNamespace(
        source_launch_id=LAUNCH_ID,
        source_run_id=None,
        target_date=TARGET_DATE,
    )
    check = SimpleNamespace(check_id="check-fixture")
    _record_outer_workflow_failure(
        store,
        job_id=JOB_ID,
        launch=launch,
        check=check,
        error=RuntimeError("synthetic runner boundary error"),
        reason_code="maintenance_workflow_failed",
    )

    fallback = (
        tmp_path / "runtime" / "workflow-handoffs" / JOB_ID / "workflow-fallback.md"
    )
    content = fallback.read_text(encoding="utf-8")
    assert "job: job-fixture" in content
    assert "日次起動: launch-fixture" in content
    assert "日次run: 未生成（null）" in content
    assert "対象日: 2026-10-20" in content
    assert "check: check-fixture" in content
    assert "調査: 未確認" in content
    assert "独立HTMLレビュー: 未確認" in content
    assert "実施済みとは扱いません" in content


def test_worker_cli_admits_target_launch_without_lease(tmp_path: Path, capsys, monkeypatch):
    from datetime import datetime, timezone

    from automation.inago.maintenance.cli import main as maintenance_cli_main
    from automation.inago.maintenance.settings import ensure_settings_file
    from automation.inago.maintenance.store import MaintenanceStore

    monkeypatch.setattr(
        "automation.inago.maintenance.skill_chain_runtime._codex",
        lambda: None,
    )
    monkeypatch.setattr(
        "automation.inago.maintenance.skill_chain_runtime.run_skill_chain",
        lambda **_kwargs: {"status": "incomplete"},
    )
    data_root = tmp_path / "runtime"
    daily_database = tmp_path / "daily" / "state.sqlite"
    ensure_settings_file(data_root / "maintenance" / "settings.json")
    store = MaintenanceStore(data_root, daily_database_path=daily_database)
    store.initialize()
    launch = _create_failed_launch(store, tmp_path)

    exit_code = maintenance_cli_main(
        [
            "--data-root", str(data_root),
            "--daily-database", str(daily_database),
            "worker", "--once", "--source-launch-id", launch.launch_id,
        ]
    )
    output = json.loads(capsys.readouterr().out)
    queue = store.get_worker_queue(launch.launch_id)
    check = store.get_daily_check_by_launch(launch.launch_id)
    jobs = store.list_workflow_jobs_for_launch(
        source_launch_id=launch.launch_id,
        source_run_id=None,
        target_date="2026-10-20",
    )
    (tmp_path / "worker-result.json").write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    assert queue is not None
    first_admission = store.ensure_skill_chain_job_for_launch(
        source_launch_id=launch.launch_id,
        source_run_id=None,
        target_date="2026-10-20",
        queue_id=queue.queue_id,
        observed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    repeated_admission = store.ensure_skill_chain_job_for_launch(
        source_launch_id=launch.launch_id,
        source_run_id=None,
        target_date="2026-10-20",
        queue_id=queue.queue_id,
        observed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
    )

    assert exit_code == 0, output
    assert output["status"] == "maintenance_incomplete"
    assert output["maintenance_resolution_status"] == "unknown"
    assert check is not None and check.check_status == "incomplete"
    assert queue is not None and queue.status == "complete"
    assert len(jobs) == 1
    assert jobs[0].job_id != queue.worker_job_id
    assert first_admission.job_id == repeated_admission.job_id == jobs[0].job_id
    assert jobs[0].source_run_id is None
    assert queue.target_date == "2026-10-20"
    assert check is not None and check.target_date == "2026-10-20"
    assert jobs[0].status == "failed"
    assert output["reason_code"] == "skill_chain_log_missing"
    assert jobs[0].terminal_reason_code == "skill_chain_log_missing"
    assert store.counts()["lease"] == 0


def test_contract_roles_and_generated_flow_are_synchronized():
    role_names = [role.name for role in skill_chain.ROLE_CALLS]
    assert len(role_names) == 7
    assert [role.stage for role in skill_chain.ROLE_CALLS] == [
        "調査", "実装", "試験", "独立製品レビュー", "本番反映", "HTML作成", "独立HTMLレビュー"
    ]
    for role in skill_chain.ROLE_CALLS:
        assert (Path(".codex/agents") / role.config).is_file()
        assert role.artifact.endswith((".md", ".html"))


def test_safe_stdout_stderr_and_response_exclude_secrets(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr("automation.inago.maintenance.skill_chain_runtime._codex", lambda: "codex-fixture")
    factory, _sessions = _factory(root, response="Bearer abc.def.ghi token=canary-secret secret=private")
    _run(root, factory, start_role=1)
    ledger = root / "rakuten-room-inago" / "runtime" / "maintenance-flow" / JOB_ID / "calls.jsonl"
    content = ledger.read_text(encoding="utf-8")
    assert "abc.def.ghi" not in content
    assert "canary-secret" not in content
    assert "private" not in content
    assert "[redacted]" in content


def test_daily_gap_emits_useful_safe_diagnostics(tmp_path: Path, monkeypatch):
    """Run the real daily-gap fixtures under the plan-mapped complete node."""
    from tests_inago import test_daily_pipeline_safe_diagnostics as daily_tests
    daily_tests.test_callback_secondary_exception_preserves_primary_failure_and_records_safe_diagnostic(
        tmp_path / "callback"
    )
    for index, operation in enumerate((
        "retry_summary_room_attempts", "retry_summary_trend_acquisitions", "retry_summary_trend_attempts",
    )):
        with monkeypatch.context() as isolated_patch:
            daily_tests.test_each_retry_summary_query_failure_keeps_result_and_null_fallbacks(
                tmp_path / f"query-{index}", isolated_patch, operation, operation
            )
    daily_tests.test_commit_then_export_exception_preserves_commit_and_draft_count(
        tmp_path / "post-commit-export"
    )
    daily_tests.test_secondary_errors_reach_isolated_cli_l01_record_payload(
        tmp_path / "safe-trace"
    )
