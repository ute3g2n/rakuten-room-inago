import json
import multiprocessing
from datetime import date
from pathlib import Path

import pytest

from automation.atomic_io import atomic_write_json
from automation.redaction import redact
from automation.run_context import RunContext, daily_date_status
from automation.run_lock import LockBusy, RunLock
from automation.schema_validator import validate_document


def _try_lock(path: str, start, release, results) -> None:
    start.wait()
    try:
        with RunLock(Path(path), "test-run"):
            results.put("acquired")
            release.wait(2)
    except LockBusy:
        results.put("busy")


@pytest.mark.parametrize("state,code", [("success", 0), ("partial", 2), ("action_required", 3), ("failed", 1)])
def test_run_context_closes_each_state(tmp_path: Path, state: str, code: int):
    context = RunContext.create(tmp_path, "daily", date(2026, 9, 25), "manual")
    assert context.step_started("load_state")
    context.heartbeat()
    context.step_completed("load_state", ["inputs/state.json"])
    context.finish(state)
    manifest = json.loads(context.manifest_path.read_text(encoding="utf-8"))
    validate_document("manifest", manifest)
    assert manifest["status"] == state
    assert context.exit_code == code
    assert (tmp_path / "status/latest.json").is_file()
    assert (tmp_path / "status/latest-daily.json").is_file()
    assert (tmp_path / "status/latest-daily-status.txt").is_file()
    events = [json.loads(line) for line in context.events_path.read_text(encoding="utf-8").splitlines()]
    assert [row["type"] for row in events] == ["run_started", "step_started", "heartbeat", "step_completed", "run_completed"]


def test_exception_closes_manifest_as_failed(tmp_path: Path):
    context = RunContext.create(tmp_path, "daily", date(2026, 9, 25), "manual")
    with pytest.raises(RuntimeError):
        with context.run_step("explode"):
            raise RuntimeError("password=dummy-secret")
    manifest = json.loads(context.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["finished_at"]
    assert manifest["stages"][-1]["status"] == "failed"
    assert "dummy-secret" not in context.manifest_path.read_text(encoding="utf-8")


def test_atomic_replace_keeps_old_file_on_failure(tmp_path: Path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"value": 1}\n', encoding="utf-8")
    def fail_replace(_source, _destination):
        raise OSError("replace failed")
    monkeypatch.setattr("automation.atomic_io.os.replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_json(path, {"value": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1}
    assert len(list(tmp_path.iterdir())) == 1


def test_two_processes_cannot_hold_same_lock(tmp_path: Path):
    ctx = multiprocessing.get_context("spawn")
    start, release = ctx.Event(), ctx.Event()
    results = ctx.Queue()
    path = str(tmp_path / "daily-2026-09-25.lock")
    processes = [ctx.Process(target=_try_lock, args=(path, start, release, results)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    values = sorted(results.get(timeout=10) for _ in processes)
    release.set()
    for process in processes:
        process.join(timeout=10)
    assert values == ["acquired", "busy"]
    assert all(process.exitcode == 0 for process in processes)


def test_stale_metadata_is_reported_without_deleting_file(tmp_path: Path):
    path = tmp_path / "old.lock"
    path.write_text(json.dumps({"pid": 99999999, "heartbeat_at": "2020-01-01T00:00:00+09:00"}), encoding="utf-8")
    assert RunLock.inspect_stale(path, stale_minutes=5)
    assert path.exists()


def test_resume_skips_completed_step_and_links_parent(tmp_path: Path):
    first = RunContext.create(tmp_path, "daily", date(2026, 9, 25), "manual")
    first.step_started("load_state")
    artifact = first.logger.run_dir / "inputs/state.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    first.step_completed("load_state", ["inputs/state.json"])
    first.step_started("fetch")
    first.step_failed("fetch", "ROOM_FETCH_FAILED", "unavailable")
    first.finish("partial")
    resumed = RunContext.resume(tmp_path, first.manifest_path, "manual")
    assert resumed.parent_run_id == first.run_id
    assert resumed.attempt == 2
    assert resumed.resume_info()["artifacts"] == ["inputs/state.json"]
    assert resumed.resume_info()["failed_steps"] == ["fetch"]
    assert not resumed.step_started("load_state")
    assert resumed.step_started("fetch")
    events = [json.loads(line) for line in resumed.events_path.read_text(encoding="utf-8").splitlines()]
    assert not any(row["type"] == "step_started" and row["stage"] == "load_state" for row in events)


def test_resume_restarts_step_if_required_artifact_is_missing(tmp_path: Path):
    first = RunContext.create(tmp_path, "daily", date(2026, 9, 25), "manual")
    first.step_started("load_state")
    first.step_completed("load_state", ["inputs/missing.json"])
    first.finish("partial")
    resumed = RunContext.resume(tmp_path, first.manifest_path, "manual")
    assert resumed.step_started("load_state")


def test_redaction_and_event_deduplication(tmp_path: Path):
    sample = "accessKey=dummy-key Cookie: session=dummy-cookie Authorization: Bearer dummy-bearer password=dummy-password RakutenID=dummy-id"
    masked = redact(sample)
    for value in ("dummy-key", "dummy-cookie", "dummy-bearer", "dummy-password", "dummy-id"):
        assert value not in masked
    context = RunContext.create(tmp_path, "status", date(2026, 9, 25), "manual")
    event = {"event_id": "same-event", "run_id": context.run_id, "at": context.manifest["started_at"], "type": "heartbeat", "stage": None, "message": sample}
    assert context.logger.append_event(event)
    assert not context.logger.append_event(event)
    context.logger.append_output("stdout", sample)
    data = context.events_path.read_text(encoding="utf-8")
    assert data.count("same-event") == 1
    written = data + (context.logger.run_dir / "stdout.log").read_text(encoding="utf-8")
    assert not any(value in written for value in ("dummy-key", "dummy-cookie", "dummy-bearer", "dummy-password", "dummy-id"))


def test_before_operation_start_is_not_missed():
    assert daily_date_status(date(2026, 9, 24), "2026-09-25") == "before_operation_start"
    assert daily_date_status(date(2026, 9, 25), "2026-09-25") == "eligible"
