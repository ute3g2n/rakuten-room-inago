"""One owned app-server process/root with bounded, isolated structured turns."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
import threading

import pytest

from automation.inago.app_server import AppServerSession, AppServerError
from automation.inago.daily_ai import DailyAIRootRunner
from automation.inago.repository import initialize_database
from automation.inago.clock import FakeClock
from tests_inago.test_step21_schema import legacy_v2  # noqa: F401
from tests_inago.test_step13 import runtime_case  # noqa: F401


FAKE = r"""
import sys, json, time
mode = sys.argv[1]
def send(value):
 print(json.dumps(value),flush=True)
turn = 0
for line in sys.stdin:
 p=json.loads(line); method=p.get('method'); params=p.get('params',{})
 if method=='initialize': send({'id':p['id'],'result':{'userAgent':'fixture'}})
 elif method=='initialized': pass
 elif method=='account/read': send({'id':p['id'],'result':{'account':{'type':'apiKey' if mode=='auth' else 'chatgpt','email':'PRIVATE-EMAIL'}}})
 elif method in ('thread/start','thread/resume'):
  if method=='thread/resume' and mode=='missing_rollout':
   send({'id':p['id'],'error':{'code':-32600,'message':'no rollout found for thread id '+params['threadId']}});continue
  send({'id':p['id'],'result':{'thread':{'id':'OTHER' if mode=='bad_resume' else params.get('threadId','ROOT-ONE')}}})
  if mode=='stop_reading': time.sleep(1)
 elif method=='thread/inject_items': send({'id':p['id'],'result':{}})
 elif method=='turn/start':
  turn+=1; tid='TURN-'+str(turn)
  send({'id':p['id'],'result':{'turn':{'id':tid,'status':'inProgress','items':[]}}})
  if mode=='hang': continue
  if mode=='malformed': print('INVALID JSON',flush=True); continue
  if mode=='bad_turn': send({'method':'turn/completed','params':{'threadId':'ROOT-ONE','turn':{'id':tid,'status':'completed'}}}); continue
  if mode=='rpc_error': send({'method':'turn/completed','params':{'threadId':'ROOT-ONE','turn':{'id':tid,'status':'failed','error':{'message':'PRIVATE-ERROR'}}}}); continue
  # A late result from an old turn must never be selected for the new job.
  send({'method':'item/completed','params':{'threadId':'ROOT-ONE','turnId':'OLD','item':{'type':'agentMessage','phase':'final_answer','text':'{"old":true}'}}})
  if mode=='foreign':
   send({'method':'item/completed','params':{'threadId':'OTHER','turnId':tid,'item':{'type':'agentMessage','phase':'final_answer','text':'{"foreign":true}'}}})
  else:
   send({'method':'item/completed','params':{'threadId':'ROOT-ONE','turnId':tid,'item':{'type':'agentMessage','phase':'final_answer','text':json.dumps({'value':params['input'][0]['text']})}}})
   if mode=='duplicate_final': send({'method':'item/completed','params':{'threadId':'ROOT-ONE','turnId':tid,'item':{'type':'agentMessage','phase':'final_answer','text':'{"duplicate":true}'}}})
  send({'method':'turn/completed','params':{'threadId':'ROOT-ONE','turn':{'id':tid,'status':'completed'}}})
 elif method=='turn/interrupt': send({'id':p['id'],'result':{}})
"""


def session(tmp_path: Path, mode="normal", *, resume=None):
    script = tmp_path / "fake_app_server.py"
    script.write_text(FAKE, encoding="utf-8")
    return AppServerSession(
        command=(sys.executable, "-X", "utf8", str(script), mode),
        cwd=tmp_path,
        log_path=tmp_path / "metadata.jsonl",
        resume_thread_id=resume,
    )


def deadline():
    return time.monotonic() + 5


def test_ai_raw_response_artifact_has_raw_retention_deadline(tmp_path):
    from datetime import datetime

    from automation.inago.ai_runner import AIJobRunner
    from automation.inago.retention import retention_expiry
    from tests_inago.test_step08 import _base_request

    now = datetime.fromisoformat("2026-10-14T13:00:00+09:00")
    clock = FakeClock(now)
    data_root = tmp_path / "runtime"
    data_root.mkdir()
    message = tmp_path / "last-message.json"
    message.write_text('{"ok":true}', encoding="utf-8")
    runner = AIJobRunner(data_root=data_root, clock=clock)

    artifact = runner._save_raw_response(_base_request(), message)

    assert artifact is not None
    assert artifact.source_kind == "daily"
    assert (
        artifact.retention_expires_at
        == retention_expiry("raw", artifact.created_at).isoformat()
    )


def test_ai_job_runner_recovers_valid_success_after_return_before_db_index(tmp_path):
    from datetime import datetime

    from automation.inago.ai_runner import AIJobRunner
    from automation.inago.artifact_io import write_temp_response_bytes
    from automation.inago.clock import FakeClock
    from automation.inago.retention import retention_expiry
    from tests_inago.test_step08 import _base_request, _delegation, _root_response

    request = _base_request()
    key_payload = {
        "snapshot_id": "KEYWORDS-01",
        "query_word": "テーマ",
        "decisions": [
            {
                "word_id": "WORD-01",
                "decision": "candidate",
                "group_ref": "WORD-01",
                "reason": "fixture",
            }
        ],
    }
    response = _root_response(
        request,
        delegations=[_delegation("AG-KEY", "T02", "rr-inago-keywords", key_payload)],
    )
    now = datetime.fromisoformat("2026-10-14T13:00:00+09:00")
    data_root = tmp_path / "runtime"
    data_root.mkdir()
    runner = AIJobRunner(data_root=data_root, clock=FakeClock(now))
    artifact_ref = f"{request.job_id}-a{request.attempt}-response"
    raw = json.dumps(response, ensure_ascii=False).encode("utf-8")
    artifact = write_temp_response_bytes(
        data_root=data_root,
        artifact_ref=artifact_ref,
        response=raw,
        source_kind="daily",
        created_at=now.isoformat(),
        retention_expires_at=retention_expiry("raw", now.isoformat()).isoformat(),
    )
    child_trace = {
        "agent_id": "AG-KEY",
        "task_id": "T02",
        "skill_name": "rr-inago-keywords",
        "result_status": "success",
        "resolved_model": "gpt-6.1-sol",
        "reasoning_setting": "xhigh",
        "auth_mode": "chatgpt",
    }
    observed_child = {
        "agent_id": "AG-KEY",
        "resolved_model": "gpt-6.1-sol",
        "reasoning_setting": "xhigh",
        "auth_mode": "chatgpt",
    }
    runner._trace_event(request, "job_started")
    runner._trace_event(request, "auth_observed", auth_mode="chatgpt")
    runner._trace_event(
        request, "cli_finished", process_status="completed", process_returncode=0
    )
    runner._trace_event(request, "artifact_saved", artifact_ref=artifact_ref)
    runner._trace_event(
        request,
        "session_trace_observed",
        resolved_model="gpt-6.1-sol",
        reasoning_setting="xhigh",
        child_agents=[observed_child],
        spawn_count=1,
    )
    runner._trace_event(
        request,
        "job_finished",
        result_status="success",
        adoption_status="pending",
        auth_mode="chatgpt",
        resolved_model="gpt-6.1-sol",
        reasoning_setting="xhigh",
        artifact_ref=artifact_ref,
        process_returncode=0,
        duration_ms=1200,
        child_agents=[child_trace],
    )
    runner._trace_event(request, "job_started")
    runner._trace_event(request, "auth_observed", auth_mode="chatgpt")
    runner._trace_event(
        request,
        "job_finished",
        result_status="error",
        adoption_status="rejected_contract",
        error_code="final_response_not_saved",
        auth_mode="chatgpt",
        resolved_model="gpt-6.1-sol",
        reasoning_setting="xhigh",
        artifact_ref=None,
        process_returncode=0,
        duration_ms=15,
        child_agents=[],
    )

    recovered = runner.recover_successful_result(request)

    assert recovered is not None
    assert recovered.result_status == "success"
    assert recovered.adoption_status == "pending"
    assert recovered.artifact == artifact
    assert recovered.subjobs[0].agent_id == "AG-KEY"
    assert recovered.subjobs[0].payload == key_payload
    assert recovered.resolved_model == "gpt-6.1-sol"


def test_ai_job_runner_does_not_recover_response_without_terminal_success_trace(
    tmp_path,
):
    from datetime import datetime

    from automation.inago.ai_runner import AIJobRunner
    from automation.inago.artifact_io import write_temp_response_bytes
    from automation.inago.clock import FakeClock
    from automation.inago.retention import retention_expiry
    from tests_inago.test_step08 import _base_request, _root_response

    request = _base_request()
    now = datetime.fromisoformat("2026-10-14T13:00:00+09:00")
    data_root = tmp_path / "runtime"
    data_root.mkdir()
    runner = AIJobRunner(data_root=data_root, clock=FakeClock(now))
    artifact_ref = f"{request.job_id}-a{request.attempt}-response"
    write_temp_response_bytes(
        data_root=data_root,
        artifact_ref=artifact_ref,
        response=json.dumps(_root_response(request)).encode("utf-8"),
        source_kind="daily",
        created_at=now.isoformat(),
        retention_expires_at=retention_expiry("raw", now.isoformat()).isoformat(),
    )
    runner._trace_event(request, "job_started")
    runner._trace_event(request, "artifact_saved", artifact_ref=artifact_ref)

    assert runner.recover_successful_result(request) is None


def test_ai_job_runner_rejects_dangling_artifact_symlink_before_exists_check(
    tmp_path,
):
    from datetime import datetime

    from automation.inago.ai_runner import AIJobRunner
    from automation.inago.contracts import PersistenceContractError
    from automation.inago.clock import FakeClock
    from tests_inago.test_step08 import _base_request

    request = _base_request()
    data_root = tmp_path / "runtime"
    data_root.mkdir()
    artifact_path = (
        data_root
        / "artifacts"
        / request.source_kind
        / f"{request.job_id}-a{request.attempt}-response.response"
    )
    artifact_path.parent.mkdir(parents=True)
    try:
        artifact_path.symlink_to(tmp_path / "missing-outside-artifact.response")
    except OSError as exc:
        pytest.skip(f"cannot create a dangling symlink fixture: {exc}")
    current = datetime.fromisoformat("2026-10-14T13:00:00+09:00")
    runner = AIJobRunner(data_root=data_root, clock=FakeClock(current))

    with pytest.raises(PersistenceContractError, match="symbolic link"):
        runner.recover_successful_result(request)


def test_ai_trace_append_never_follows_dangling_trace_symlink(tmp_path):
    from datetime import datetime

    from automation.inago.ai_runner import AIJobRunner
    from automation.inago.clock import FakeClock
    from tests_inago.test_step08 import _base_request

    request = _base_request()
    data_root = tmp_path / "runtime"
    data_root.mkdir()
    outside_trace = tmp_path / "outside-trace.jsonl"
    trace_path = (
        data_root
        / "logs"
        / request.target_date
        / f"{request.run_id}--{request.job_id}-a{request.attempt}.jsonl"
    )
    trace_path.parent.mkdir(parents=True)
    try:
        trace_path.symlink_to(outside_trace)
    except OSError as exc:
        pytest.skip(f"cannot create a dangling symlink fixture: {exc}")
    current = datetime.fromisoformat("2026-10-14T13:00:00+09:00")
    runner = AIJobRunner(data_root=data_root, clock=FakeClock(current))

    runner._trace_event(request, "job_started")

    assert trace_path.is_symlink()
    assert not outside_trace.exists()


def test_ai_job_runner_rejects_symlinked_data_root_before_resolving_it(tmp_path):
    from automation.inago.ai_runner import AIJobRunner
    from automation.inago.contracts import PersistenceContractError

    target = tmp_path / "real-runtime"
    target.mkdir()
    linked_root = tmp_path / "runtime-link"
    try:
        linked_root.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot create a data-root symlink fixture: {exc}")

    with pytest.raises(PersistenceContractError, match="data root"):
        AIJobRunner(data_root=linked_root)


def test_two_jobs_share_one_process_and_root_but_have_distinct_turns(tmp_path):
    root = session(tmp_path)
    with root:
        root.start(deadline=deadline())
        pid = root.process_id
        first = root.run_turn("first", {}, cwd=tmp_path, deadline=deadline())
        second = root.run_turn("second", {}, cwd=tmp_path, deadline=deadline())
        assert first.thread_id == second.thread_id == "ROOT-ONE"
        assert first.turn_id != second.turn_id
        assert root.process_id == pid and root.starts == 1
        assert json.loads(first.final_text) == {"value": "first"}
        assert json.loads(second.final_text) == {"value": "second"}
        assert first.process_returncode is None and second.process_returncode is None
    assert root.is_closed
    metadata = (tmp_path / "metadata.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE-EMAIL" not in metadata and "first" not in metadata


def test_resume_uses_saved_root_and_never_starts_a_new_thread(tmp_path):
    with session(tmp_path, resume="ROOT-ONE") as root:
        root.start(deadline=deadline())
        result = root.run_turn("resume", {}, cwd=tmp_path, deadline=deadline())
        assert result.thread_id == "ROOT-ONE"
    events = [
        json.loads(line)
        for line in (tmp_path / "metadata.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(event.get("method") == "thread/resume" for event in events)
    assert not any(event.get("method") == "thread/start" for event in events)


@pytest.mark.parametrize("mode", ["malformed", "rpc_error", "foreign"])
def test_invalid_failed_or_foreign_turn_is_not_a_completed_job(tmp_path, mode):
    with session(tmp_path, mode) as root:
        root.start(deadline=deadline())
        with pytest.raises(AppServerError):
            root.run_turn("request", {}, cwd=tmp_path, deadline=deadline())
    assert root.is_closed
    assert "PRIVATE-ERROR" not in (tmp_path / "metadata.jsonl").read_text(
        encoding="utf-8"
    )


def test_timeout_interrupts_and_closes_only_owned_server(tmp_path):
    with session(tmp_path, "hang") as root:
        root.start(deadline=deadline())
        with pytest.raises(AppServerError, match="timed_out"):
            root.run_turn("request", {}, cwd=tmp_path, deadline=time.monotonic() + 0.05)
    assert root.is_closed


def test_cancelled_job_does_not_receive_a_late_answer(tmp_path):
    cancel = threading.Event()
    with session(tmp_path, "hang") as root:
        root.start(deadline=deadline())
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        try:
            with pytest.raises(AppServerError, match="cancelled"):
                root.run_turn(
                    "request",
                    {},
                    cwd=tmp_path,
                    deadline=deadline(),
                    cancel_event=cancel,
                )
        finally:
            timer.join()
    assert root.is_closed


def test_daily_runner_reuses_root_and_preserves_checkpoint_without_fake_exitcode(
    tmp_path, legacy_v2
):
    from dataclasses import replace
    from datetime import datetime
    from types import SimpleNamespace
    from tests_inago.test_step08 import (
        _base_request,
        _root_response,
        _delegation,
        _spawn_event,
    )

    initialize_database(legacy_v2)
    legacy_v2.execute("UPDATE run SET checkpoint='{}' WHERE run_id='R1'")
    jobs = {}
    instances = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.starts = 1
            self.process_id = 1234
            self.is_closed = False
            self.calls = []
            instances.append(self)

        def start(self, **kwargs):
            return "ROOT-ONE"

        def run_turn(self, prompt, schema, *, cwd, **kwargs):
            doc = json.loads((cwd / "job-input.json").read_text(encoding="utf-8"))
            job = jobs[doc["job_id"]]
            payload = {
                "snapshot_id": "KEYWORDS-01",
                "query_word": "テーマ",
                "decisions": [
                    {
                        "word_id": "WORD-01",
                        "decision": "candidate",
                        "group_ref": "WORD-01",
                        "reason": "fixture",
                    }
                ],
            }
            response = _root_response(
                job,
                delegations=[
                    _delegation("AG-KEY", "T02", "rr-inago-keywords", payload)
                ],
            )
            self.calls.append(cwd)
            event = _spawn_event("rr-inago-keywords")
            return SimpleNamespace(
                thread_id="ROOT-ONE",
                turn_id=f"TURN-{len(self.calls)}",
                final_text=json.dumps(response),
                process_returncode=None,
                events=(
                    {"method": "item/completed", "params": {"item": event["item"]}},
                ),
            )

        def close(self):
            self.is_closed = True

    runner = DailyAIRootRunner(
        data_root=tmp_path / "runtime",
        session_factory=FakeSession,
        auth_mode_provider=lambda: "chatgpt",
        require_observed_metadata=False,
        clock=FakeClock(datetime.fromisoformat("2026-10-13T13:01:00+09:00")),
    )
    runner.prepare_daily_run(legacy_v2, "R1")
    runner.start_daily_root(deadline_monotonic=100)
    for number in (1, 2):
        job = replace(
            _base_request(),
            job_id=f"J{number}",
            run_id="R1",
            target_date="2026-10-13",
            settings_version="S1",
            deadline_at="2026-10-13T17:45:00+09:00",
            deadline_monotonic=100,
        )
        jobs[job.job_id] = job
        result = runner.run(job)
        assert result.result_status == "success", result.error
        assert result.process_returncode is None
        runner.sync_daily_checkpoint(legacy_v2)
    assert len(instances) == 1 and runner.process_starts == 1
    assert all(path.is_dir() for path in instances[0].calls)
    metadata = json.loads(
        legacy_v2.execute("SELECT checkpoint FROM run WHERE run_id='R1'").fetchone()[0]
    )["ai_daily_root"]
    assert metadata["root_thread_id"] == "ROOT-ONE" and metadata["seed"] == "SAVED-SEED"
    runner.close_daily_run()
    assert all(not path.exists() for path in instances[0].calls)
    runner.sync_daily_checkpoint(legacy_v2)
    assert runner.root_metadata()["closed"] is True


def test_rollout_window_excludes_previous_turn_evidence():
    from datetime import datetime
    from automation.inago.ai_runner import _rollout_in_window

    events = (
        {"timestamp": "2026-10-13T13:00:00+09:00", "type": "response_item"},
        {"timestamp": "2026-10-13T13:01:00+09:00", "type": "turn_context"},
        {"type": "response_item"},
    )
    selected = _rollout_in_window(
        events,
        datetime.fromisoformat("2026-10-13T13:00:30+09:00"),
        datetime.fromisoformat("2026-10-13T13:02:00+09:00"),
    )
    assert selected == (events[1],)


def test_daily_control_begins_and_closes_one_root_on_program_thread(runtime_case):
    from dataclasses import replace
    from tests_inago.test_step21_daily import current_bundle
    from tests_inago.test_step13 import _run

    bundle = current_bundle(runtime_case)
    owner = threading.get_ident()
    operations = []
    base = bundle.runner

    class RootHooks:
        fixture_only = True

        def prepare_daily_run(self, connection, run_id):
            assert threading.get_ident() == owner
            operations.append("prepare")

        def start_daily_root(self, **kwargs):
            assert threading.get_ident() == owner
            operations.append("start")

        def sync_daily_checkpoint(self, connection):
            assert threading.get_ident() == owner
            operations.append("sync")

        def close_daily_run(self):
            assert threading.get_ident() == owner
            operations.append("close")

        def run(self, request):
            return base.run(request)

    bundle.ports = replace(bundle.ports, copy_runner=RootHooks())
    outcome = _run(runtime_case, bundle)
    assert outcome.committed_count == 15
    assert operations == ["prepare", "start", "sync", "close", "sync"]
    assert len(base.requests) == 15


@pytest.mark.parametrize(
    "fault", ["foreign_run", "foreign_seed", "bad_thread", "nonobject_checkpoint"]
)
def test_root_resume_identity_errors_do_not_start_processes(tmp_path, legacy_v2, fault):
    initialize_database(legacy_v2)
    metadata = {
        "schema_version": 1,
        "run_id": "R1",
        "target_date": "2026-10-13",
        "settings_version": "S1",
        "seed": "SAVED-SEED",
        "root_thread_id": "ROOT-ONE",
    }
    if fault == "foreign_run":
        metadata["run_id"] = "OTHER"
    elif fault == "foreign_seed":
        metadata["seed"] = "OTHER"
    elif fault == "bad_thread":
        metadata["root_thread_id"] = True
    value = [] if fault == "nonobject_checkpoint" else {"ai_daily_root": metadata}
    legacy_v2.execute(
        "UPDATE run SET checkpoint=? WHERE run_id='R1'", (json.dumps(value),)
    )
    runner = DailyAIRootRunner(data_root=tmp_path / "runtime")
    with pytest.raises(AppServerError):
        runner.prepare_daily_run(legacy_v2, "R1")
    assert runner.session is None and runner.process_starts == 0


@pytest.mark.parametrize("mode", ["auth", "bad_resume"])
def test_startup_failure_never_falls_back_to_new_root_or_billing_mode(tmp_path, mode):
    with session(tmp_path, mode, resume="ROOT-ONE") as root:
        with pytest.raises(AppServerError):
            root.start(deadline=deadline())
        assert root.thread_id is None
    assert root.is_closed


@pytest.mark.parametrize("mode", ["bad_turn", "duplicate_final"])
def test_missing_or_duplicate_final_answer_is_a_contract_error(tmp_path, mode):
    with session(tmp_path, mode) as root:
        root.start(deadline=deadline())
        with pytest.raises(AppServerError):
            root.run_turn("job", {}, cwd=tmp_path, deadline=deadline())
    assert root.is_closed


def test_concurrent_turn_is_rejected_without_releasing_other_job_lock(tmp_path):
    with session(tmp_path) as root:
        root._job_lock.acquire()
        try:
            with pytest.raises(AppServerError, match="concurrent"):
                root.run_turn("job", {}, cwd=tmp_path, deadline=deadline())
            assert root._job_lock.locked() and root.process_id is None
        finally:
            root._job_lock.release()


def test_idle_session_start_is_idempotent_and_closed_root_cannot_restart(tmp_path):
    root = session(tmp_path)
    root.start(deadline=deadline())
    assert root.start(deadline=deadline()) == "ROOT-ONE" and root.starts == 1
    root.close()
    root.close()
    with pytest.raises(AppServerError, match="closed"):
        root.start(deadline=deadline())


def test_daily_root_initial_context_is_persisted_without_inference(tmp_path):
    root = session(tmp_path)
    root.persist_initial_context = True
    try:
        root.start(deadline=deadline())
    finally:
        root.close()
    events = [json.loads(line) for line in root.log_path.read_text().splitlines()]
    methods = [event.get("method") for event in events if event["event"] == "rpc_sent"]
    assert "thread/inject_items" in methods
    assert "turn/start" not in methods


def test_unused_missing_root_is_replaced_with_preserved_provenance(tmp_path, legacy_v2):
    runner = bound_runner(tmp_path, legacy_v2)
    runner.context["root_thread_id"] = "UNUSED-ROOT"
    runner.sync_daily_checkpoint(legacy_v2)
    replacement = DailyAIRootRunner(data_root=tmp_path / "runtime")
    replacement.prepare_daily_run(legacy_v2, "R1")
    script = tmp_path / "missing_rollout_server.py"
    script.write_text(FAKE, encoding="utf-8")

    def factory(**kwargs):
        kwargs["command"] = (sys.executable, "-X", "utf8", str(script), "missing_rollout")
        return AppServerSession(**kwargs)

    replacement.session_factory = factory
    try:
        replacement.start_daily_root(deadline_monotonic=time.monotonic() + 5)
        replacement.sync_daily_checkpoint(legacy_v2)
        saved = json.loads(legacy_v2.execute("SELECT checkpoint FROM run WHERE run_id='R1'").fetchone()[0])["ai_daily_root"]
        assert saved["root_thread_id"] == "ROOT-ONE"
        assert saved["replaced_unused_root_thread_id"] == "UNUSED-ROOT"
        assert saved["process_starts"] == 2
    finally:
        replacement.close_daily_run()


def bound_runner(tmp_path, connection):
    initialize_database(connection)
    connection.execute("UPDATE run SET checkpoint='{}' WHERE run_id='R1'")
    runner = DailyAIRootRunner(data_root=tmp_path / "runtime")
    runner.prepare_daily_run(connection, "R1")
    return runner


def test_root_checkpoint_update_is_atomic_and_rejects_caller_transaction(
    tmp_path, legacy_v2
):
    runner = bound_runner(tmp_path, legacy_v2)
    legacy_v2.execute("BEGIN IMMEDIATE")
    with pytest.raises(AppServerError, match="idle"):
        runner.sync_daily_checkpoint(legacy_v2)
    assert legacy_v2.in_transaction
    legacy_v2.rollback()
    legacy_v2.execute("UPDATE run SET seed='OTHER' WHERE run_id='R1'")
    with pytest.raises(AppServerError, match="conflict"):
        runner.sync_daily_checkpoint(legacy_v2)
    assert not legacy_v2.in_transaction
    assert (
        legacy_v2.execute("SELECT checkpoint FROM run WHERE run_id='R1'").fetchone()[0]
        == "{}"
    )


def test_existing_runner_cannot_rebind_to_changed_daily_identity(tmp_path, legacy_v2):
    runner = bound_runner(tmp_path, legacy_v2)
    legacy_v2.execute("UPDATE run SET seed='OTHER' WHERE run_id='R1'")
    with pytest.raises(AppServerError, match="identity"):
        runner.prepare_daily_run(legacy_v2, "R1")
    assert runner.context["seed"] == "SAVED-SEED" and runner.session is None


def test_staging_cleanup_refuses_path_outside_owned_jobs(tmp_path, legacy_v2):
    runner = bound_runner(tmp_path, legacy_v2)
    outside = tmp_path / "keep"
    outside.mkdir()
    runner._retained_dirs.add(outside)
    with pytest.raises(AppServerError, match="path"):
        runner.close_daily_run()
    assert outside.is_dir()


def test_failed_bootstrap_auth_keeps_actual_start_count_and_does_not_start_inference(
    tmp_path, legacy_v2
):
    runner = bound_runner(tmp_path, legacy_v2)
    script = tmp_path / "auth_server.py"
    script.write_text(FAKE, encoding="utf-8")

    def factory(**kwargs):
        kwargs["command"] = (sys.executable, "-X", "utf8", str(script), "auth")
        return AppServerSession(**kwargs)

    runner.session_factory = factory
    with pytest.raises(AppServerError, match="auth"):
        runner.start_daily_root(deadline_monotonic=time.monotonic() + 5)
    assert runner.process_starts == 1 and runner.context["root_thread_id"] is None
    assert runner.session.is_closed
    runner.close_daily_run()


def test_foreign_job_and_foreign_cancel_do_not_touch_daily_root(tmp_path, legacy_v2):
    from tests_inago.test_step08 import _base_request

    runner = bound_runner(tmp_path, legacy_v2)
    with pytest.raises(AppServerError, match="bound"):
        runner.run(_base_request())
    assert not runner.cancel_owned_jobs("OTHER")
    assert runner.cancel_owned_jobs("R1")
    assert runner.session is None


def test_full_stdin_pipe_cannot_make_job_deadline_unbounded(tmp_path):
    with session(tmp_path, "stop_reading") as root:
        root.start(deadline=deadline())
        started = time.monotonic()
        with pytest.raises(AppServerError, match="timed_out"):
            root.run_turn("x" * 1_000_000, {}, cwd=tmp_path, deadline=started + 0.05)
        assert time.monotonic() - started < 0.8
