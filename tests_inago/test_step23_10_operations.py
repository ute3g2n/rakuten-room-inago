from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from automation.inago.clock import JST
from automation.inago.operations import (
    DailyOperationsMonitor,
    read_operations_observation,
)
from automation.inago.repository import initialize_database
from automation.inago.run_lock import LockOwner, observe_process_tree


NOW = datetime(2026, 10, 15, 13, 30, tzinfo=JST)
RUN_ID = "RUN-OPS-20261015"
SETTINGS_VERSION = "operations-fixture-v1"
OWNER_START = "2026-10-15T04:29:00+00:00"
SEED = "private-deterministic-seed-ops"


def _database(runtime_root: Path, *, status: str = "running") -> Path:
    runtime_root.mkdir(parents=True, exist_ok=True)
    database_path = runtime_root / "state.sqlite"
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        initialize_database(connection)
        connection.execute(
            """
            INSERT INTO settings_version
                (version, confirmed_at, genres_json, themes_json,
                 pattern_refs_json, change_reason, created_at)
            VALUES (?, NULL, '[]', '[]', '["P01","P02","P03","P04","P05"]',
                    NULL, ?)
            """,
            (SETTINGS_VERSION, NOW.isoformat()),
        )
        from automation.inago.orchestrator import _daily_state

        checkpoint_root, _ = _daily_state(None)
        checkpoint = json.dumps(
            checkpoint_root,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, checkpoint, metrics_status, finished_at)
            VALUES (?, '2026-10-15', 'daily', ?, ?, ?, ?, ?, ?, 2, 0, ?,
                    'pending', NULL)
            """,
            (
                RUN_ID,
                status,
                SETTINGS_VERSION,
                SEED,
                NOW.isoformat(),
                "2026-10-15T17:45:00+09:00",
                "2026-10-15T18:00:00+09:00",
                checkpoint,
            ),
        )
    finally:
        connection.close()
    return database_path


def _publish_owner(runtime_root: Path, *, run_id: str = RUN_ID) -> bytes:
    runtime_root.mkdir(parents=True, exist_ok=True)
    owner_path = runtime_root / ".daily-run-owner.json"
    owner_path.write_text(
        json.dumps(
            LockOwner(os.getpid(), OWNER_START, run_id).to_dict(),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (runtime_root / ".daily-run.lock").write_bytes(b"\0")
    return owner_path.read_bytes()


def _config(runtime_root: Path):
    backup_root = runtime_root.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(runtime_root=runtime_root, backup_root=backup_root)


def test_operations_monitor_tracks_checkpoint_progress_without_saving_checkpoint_text(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    database_path = _database(runtime_root)
    _publish_owner(runtime_root)
    observation_path = runtime_root / "state" / "daily-operations.json"
    clock = [NOW]
    monitor = DailyOperationsMonitor(
        database_path,
        observation_path,
        target_date="2026-10-15",
        now=lambda: clock[0],
    )

    assert monitor.observe_once() is True
    first = json.loads(observation_path.read_text(encoding="utf-8"))
    assert first["phase"] == "initial"
    assert first["progress_at"] == first["heartbeat_at"]
    assert "checkpoint" not in first

    clock[0] += timedelta(seconds=30)
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute(
            "UPDATE run SET checkpoint=? WHERE run_id=?",
            (
                json.dumps(
                    {
                        "daily_orchestration": {"phase": "additional", "errors": []},
                        "product_text": "fixture-only-sensitive-copy-text",
                    },
                    separators=(",", ":"),
                ),
                RUN_ID,
            ),
        )
    finally:
        connection.close()

    assert monitor.observe_once() is True
    second = json.loads(observation_path.read_text(encoding="utf-8"))
    assert second["phase"] == "additional"
    assert second["heartbeat_at"] > first["heartbeat_at"]
    assert second["progress_at"] > first["progress_at"]
    assert "fixture-only-sensitive-copy-text" not in observation_path.read_text(
        encoding="utf-8"
    )
    assert read_operations_observation(observation_path)["run_id"] == RUN_ID


def test_production_status_is_read_only_and_fails_closed_on_stale_progress(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime"
    database_path = _database(runtime_root)
    owner_bytes = _publish_owner(runtime_root)
    observation_path = runtime_root / "state" / "daily-operations.json"
    monitor = DailyOperationsMonitor(
        database_path,
        observation_path,
        target_date="2026-10-15",
        now=lambda: NOW,
    )
    assert monitor.observe_once() is True
    config = _config(runtime_root)
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    before_db = database_path.read_bytes()
    before_observation = observation_path.read_bytes()
    before_files = sorted(path.name for path in runtime_root.iterdir())

    healthy = production_runtime.run_production_status(
        config,
        run_id=RUN_ID,
        now=NOW,
        process_start_time_reader=lambda _pid: OWNER_START,
    )

    assert healthy["status"] == "healthy"
    assert healthy["fields"]["process"]["status"] == "alive"
    assert healthy["fields"]["monitor"]["status"] == "current"
    assert healthy["permissions"] == {
        "kill_process": False,
        "release_lock": False,
        "restore_database": False,
        "send_notification": False,
    }
    assert database_path.read_bytes() == before_db
    assert observation_path.read_bytes() == before_observation
    assert (runtime_root / ".daily-run-owner.json").read_bytes() == owner_bytes
    assert sorted(path.name for path in runtime_root.iterdir()) == before_files

    stale = json.loads(observation_path.read_text(encoding="utf-8"))
    stale["heartbeat_at"] = (NOW - timedelta(minutes=3)).isoformat()
    stale["progress_at"] = (NOW - timedelta(minutes=11)).isoformat()
    observation_path.write_text(json.dumps(stale), encoding="utf-8")
    stalled = production_runtime.run_production_status(
        config,
        run_id=RUN_ID,
        now=NOW,
        process_start_time_reader=lambda _pid: OWNER_START,
    )
    assert stalled["status"] == "stalled"
    assert "heartbeat_stale" in stalled["reason_codes"]
    assert stalled["fields"]["progress"]["status"] == "stalled"


def test_recovery_requires_stopped_owner_and_preserves_same_run_without_executing(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime"
    database_path = _database(runtime_root)
    owner_bytes = _publish_owner(runtime_root)
    config = _config(runtime_root)
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    before_db = database_path.read_bytes()

    active = production_runtime.run_production_recovery(
        config,
        run_id=RUN_ID,
        now=NOW,
        owner_confirmed=True,
        cause_resolved=True,
        process_start_time_reader=lambda _pid: OWNER_START,
    )
    assert active["status"] == "blocked"
    assert active["decision"]["action"] == "wait_for_daily_owner"
    assert active["decision"]["execution_requested"] is False
    assert active["decision"]["allowed"]["kill_process"] is False
    assert SEED not in json.dumps(active)

    def stopped_process(_pid: int) -> str:
        raise ProcessLookupError("fixture process stopped")

    stopped = production_runtime.run_production_recovery(
        config,
        run_id=RUN_ID,
        now=NOW,
        owner_confirmed=True,
        cause_resolved=True,
        process_start_time_reader=stopped_process,
    )
    decision = stopped["decision"]
    assert stopped["owner_state"] == "stopped"
    assert decision["action"] == "resume_remaining"
    assert decision["execution_requested"] is False
    assert stopped["resume_entrypoint"] == "daily"
    assert decision["preserved"] == {
        "run_id": RUN_ID,
        "target_date": "2026-10-15",
        "settings_version": SETTINGS_VERSION,
        "search_attempts": 0,
        "unfinished_generation_attempts": {},
        "committed_count": 0,
        "run_status": "running",
        "metrics_pending": True,
        "seed_preserved": True,
        "draft_deadline": "2026-10-15T17:45:00+09:00",
        "end_deadline": "2026-10-15T18:00:00+09:00",
    }
    assert SEED not in json.dumps(stopped)
    assert database_path.read_bytes() == before_db
    assert (runtime_root / ".daily-run-owner.json").read_bytes() == owner_bytes


def test_production_cli_forwards_read_only_status_and_recovery_options(
    monkeypatch,
) -> None:
    import io

    from automation.inago import cli, production_runtime

    config = SimpleNamespace(runtime_root=Path("fixture-runtime"))
    monkeypatch.setattr(
        production_runtime, "load_human_api_config", lambda *_args, **_kwargs: config
    )
    monkeypatch.setattr(
        cli,
        "_append_production_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("read-only mode must not write a lifecycle log")
        ),
    )
    status_calls: list[dict[str, object]] = []
    recovery_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        production_runtime,
        "run_production_status",
        lambda _config, **kwargs: status_calls.append(kwargs) or {"status": "healthy"},
    )
    monkeypatch.setattr(
        production_runtime,
        "run_production_recovery",
        lambda _config, **kwargs: recovery_calls.append(kwargs)
        or {"status": "advisory", "execution_requested": False},
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    assert (
        cli.main(
            [
                "status",
                "--config",
                "unused.json",
                "--data-root",
                "unused-runtime",
                "--environment",
                "production",
                "--run-id",
                RUN_ID,
            ],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert json.loads(stdout.getvalue()) == {"status": "healthy"}
    assert status_calls == [{"run_id": RUN_ID}]

    stdout = io.StringIO()
    assert (
        cli.main(
            [
                "recovery",
                "--config",
                "unused.json",
                "--data-root",
                "unused-runtime",
                "--environment",
                "production",
                "--run-id",
                RUN_ID,
                "--owner-confirmed",
                "--cause-resolved",
                "--export-only-failed",
            ],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert json.loads(stdout.getvalue()) == {
        "status": "advisory",
        "execution_requested": False,
    }
    assert recovery_calls == [
        {
            "run_id": RUN_ID,
            "owner_confirmed": True,
            "cause_resolved": True,
            "export_only_failed": True,
        }
    ]


def test_recovery_waits_for_orphaned_child_process_after_owner_stops(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime"
    database_path = _database(runtime_root)
    owner_path = runtime_root / ".daily-run-owner.json"
    owner_path.write_text(
        json.dumps(LockOwner(876543, OWNER_START, RUN_ID).to_dict()),
        encoding="utf-8",
    )
    (runtime_root / ".daily-run.lock").write_bytes(b"\0")
    config = _config(runtime_root)
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )

    def stopped_process(_pid: int) -> str:
        raise ProcessLookupError("fixture parent exited")

    result = production_runtime.run_production_recovery(
        config,
        run_id=RUN_ID,
        now=NOW,
        owner_confirmed=True,
        cause_resolved=True,
        process_start_time_reader=stopped_process,
        process_tree_reader=lambda _pid: (876544,),
    )

    assert result["status"] == "blocked"
    assert result["owner_state"] == "children_running"
    assert result["decision"]["action"] == "wait_for_daily_children"
    assert result["decision"]["reason_code"] == "daily_child_processes_still_active"
    assert result["decision"]["execution_requested"] is False
    assert result["child_processes"]["pids"] == [876544]
    assert result["decision"]["allowed"]["search"] is False
    assert result["decision"]["allowed"]["kill_process"] is False
    assert SEED not in json.dumps(result)


def test_status_reports_allowlisted_auth_wait_and_database_save_error(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime
    from automation.inago.orchestrator import _daily_state

    runtime_root = tmp_path / "runtime"
    database_path = _database(runtime_root)
    _publish_owner(runtime_root)
    config = _config(runtime_root)
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    observation_path = runtime_root / "state" / "daily-operations.json"
    monitor = DailyOperationsMonitor(
        database_path,
        observation_path,
        target_date="2026-10-15",
        now=lambda: NOW,
    )
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        for code, expected in (
            ("auth_wait", "auth_wait"),
            ("save_error", "save_error"),
        ):
            _, state = _daily_state(None)
            state["errors"] = [{"phase": "safe", "code": code}]
            connection.execute(
                "UPDATE run SET checkpoint=? WHERE run_id=?",
                (
                    json.dumps(
                        {"daily_orchestration": state},
                        separators=(",", ":"),
                    ),
                    RUN_ID,
                ),
            )
            assert monitor.observe_once() is True
            status = production_runtime.run_production_status(
                config,
                run_id=RUN_ID,
                now=NOW,
                process_start_time_reader=lambda _pid: OWNER_START,
                process_tree_reader=lambda _pid: (),
            )
            assert status["status"] == expected
    finally:
        connection.close()


def test_orchestrator_persists_safe_authentication_and_database_error_codes(
    tmp_path: Path,
) -> None:
    import sqlite3

    from automation.inago.clock import FakeClock
    from automation.inago.metrics import MetricsCollectionResult, MetricsSnapshot
    from automation.inago import orchestrator

    runtime_root = tmp_path / "runtime"
    database_path = _database(runtime_root)
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("UPDATE run SET checkpoint=NULL WHERE run_id=?", (RUN_ID,))
    connection.execute(
        "UPDATE run SET metrics_status='not_started' WHERE run_id=?", (RUN_ID,)
    )
    _, state = orchestrator._daily_state(None)
    response = MetricsCollectionResult(
        run_id=RUN_ID,
        status="unavailable",
        observed_at=NOW.isoformat(),
        snapshots=(
            MetricsSnapshot(
                "affiliate",
                "affiliate",
                "unavailable",
                NOW.isoformat(),
                {"error_code": "authentication_required"},
            ),
            MetricsSnapshot(
                "room",
                "room",
                "unavailable",
                NOW.isoformat(),
                {"error_code": "authentication_required"},
            ),
        ),
    )

    class MetricsPort:
        def collect(self, *_args, **_kwargs):
            return response

    try:
        result = orchestrator._collect_metrics(
            connection,
            run_id=RUN_ID,
            target_date="2026-10-15",
            state=state,
            ports=SimpleNamespace(
                metrics_port=MetricsPort(),
                cancel_metrics_job=lambda _run_id: True,
            ),
            clock=FakeClock(NOW),
            data_root=runtime_root,
        )
        assert result == "unavailable"
        stored = connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()["checkpoint"]
        stored_state = json.loads(stored)["daily_orchestration"]
        assert stored_state["errors"] == [
            {"phase": "authentication", "code": "auth_wait"}
        ]

        failed_state: dict[str, object] = {"errors": []}
        orchestrator._record_orchestration_error(
            failed_state,
            sqlite3.OperationalError("private database path and payload"),
        )
        assert failed_state["errors"] == [{"phase": "save", "code": "save_error"}]
    finally:
        connection.close()


def test_windows_process_tree_observer_sees_and_does_not_own_fixture_child() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        observation = None
        while time.monotonic() < deadline:
            observation = observe_process_tree(os.getpid())
            if observation.status == "unknown":
                raise AssertionError(observation.reason)
            if child.pid in observation.child_pids:
                break
            time.sleep(0.05)
        assert observation is not None
        assert child.pid in observation.child_pids
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)


def test_status_rejects_invalid_checkpoint_and_recovery_treats_deadlines_as_history(
    tmp_path: Path, monkeypatch
) -> None:
    import sqlite3

    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime"
    database_path = _database(runtime_root)
    config = _config(runtime_root)
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute(
            "UPDATE run SET checkpoint=? WHERE run_id=?", ("{broken", RUN_ID)
        )
    finally:
        connection.close()
    invalid_status = production_runtime.run_production_status(
        config, run_id=RUN_ID, now=NOW
    )
    invalid_recovery = production_runtime.run_production_recovery(
        config, run_id=RUN_ID, now=NOW
    )
    assert invalid_status["status"] == "unknown"
    assert invalid_status["reason_codes"] == ["daily_checkpoint_invalid"]
    assert invalid_recovery["status"] == "blocked"
    assert invalid_recovery["reason_code"] == "daily_checkpoint_invalid"
    assert invalid_recovery["execution_requested"] is False

    second_root = tmp_path / "runtime-deadline"
    second_database = _database(second_root)
    second_config = _config(second_root)
    connection = sqlite3.connect(second_database, isolation_level=None)
    try:
        connection.execute(
            "UPDATE run SET end_deadline=? WHERE run_id=?",
            ("2026-10-15T18:01:00+09:00", RUN_ID),
        )
    finally:
        connection.close()
    historical_deadline = production_runtime.run_production_recovery(
        second_config, run_id=RUN_ID, now=NOW
    )
    assert historical_deadline["status"] == "blocked"
    assert (
        historical_deadline["decision"]["reason_code"]
        == "daily_lock_file_unavailable"
    )
    assert historical_deadline["decision"]["preserved"]["end_deadline"] == (
        "2026-10-15T18:01:00+09:00"
    )


def test_production_daily_requires_explicit_confirmation_to_resume_same_run(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime, trigger
    from automation.inago.clock import FakeClock
    from automation.inago.orchestrator import DailyRunOutcome
    from tests_inago.test_step23_05_export_flow import (
        SETTINGS_VERSION as EXPORT_SETTINGS_VERSION,
        _insert_run,
        _open_database,
        _runtime_fixture,
    )

    config, database_path = _runtime_fixture(monkeypatch, tmp_path)
    connection = _open_database(database_path)
    _insert_run(connection, run_id="RUN-EXPORT-20261015")
    connection.close()
    owner_path = config.runtime_root / ".daily-run-owner.json"
    owner_path.write_text(
        json.dumps(
            LockOwner(876543, OWNER_START, "RUN-EXPORT-20261015").to_dict(),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (config.runtime_root / ".daily-run.lock").write_bytes(b"\0")
    calls: list[dict[str, object]] = []

    def stopped_process(_pid: int) -> str:
        raise ProcessLookupError("fixture previous owner stopped")

    def fake_daily(connection, **kwargs):
        row = connection.execute(
            """
            SELECT run_id, target_date, settings_version, seed, attempt_used,
                   committed_count, draft_deadline, end_deadline
              FROM run WHERE run_id='RUN-EXPORT-20261015'
            """
        ).fetchone()
        calls.append({key: row[key] for key in row.keys()})
        return DailyRunOutcome(
            status="partial",
            run_id=str(row["run_id"]),
            target_date=str(row["target_date"]),
            committed_count=int(row["committed_count"]),
            attempt_used=int(row["attempt_used"]),
            metrics_status="unavailable",
            resumed=True,
        )

    monkeypatch.setattr(trigger, "run_public_scheduled_daily", fake_daily)
    clock = FakeClock(datetime(2026, 10, 15, 13, 15, tzinfo=JST))
    blocked = production_runtime.run_production_daily(
        config,
        clock=clock,
        process_start_time_reader=stopped_process,
        process_tree_reader=lambda _pid: (),
    )
    assert blocked["status"] == "recovery_confirmation_required"
    assert blocked["reason"] == "explicit_resume_confirmation_required"
    assert blocked["export"]["status"] == "not_run"
    assert blocked["preservation"]["status"] == "not_run"
    assert calls == []

    resumed = production_runtime.run_production_daily(
        config,
        clock=clock,
        resume_confirmed=True,
        cause_resolved=True,
        process_start_time_reader=stopped_process,
        process_tree_reader=lambda _pid: (),
    )
    assert resumed["status"] == "metrics_failed"
    assert resumed["daily_status"] == "partial"
    assert resumed["run_id"] == "RUN-EXPORT-20261015"
    assert resumed["resumed"] is True
    assert calls == [
        {
            "run_id": "RUN-EXPORT-20261015",
            "target_date": "2026-10-15",
            "settings_version": EXPORT_SETTINGS_VERSION,
            "seed": "seed-RUN-EXPORT-20261015",
            "attempt_used": 0,
            "committed_count": 0,
            "draft_deadline": "2026-10-15T17:45:00+09:00",
            "end_deadline": "2026-10-15T18:00:00+09:00",
        }
    ]


def test_daily_cli_parser_requires_both_explicit_same_run_confirmation_flags() -> None:
    from automation.inago.cli import _parser

    parsed = _parser().parse_args(
        [
            "daily",
            "--config",
            "settings.json",
            "--data-root",
            "runtime",
            "--environment",
            "production",
            "--resume-confirmed",
            "--cause-resolved",
        ]
    )
    assert parsed.resume_confirmed is True
    assert parsed.cause_resolved is True


def test_status_and_recovery_do_not_create_sidecars_for_checkpointed_wal_database(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime-wal"
    database_path = _database(runtime_root)
    journal_connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        assert (
            journal_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()
            == "wal"
        )
    finally:
        journal_connection.close()
    sidecars = tuple(Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm"))
    assert all(not path.exists() for path in sidecars)
    before_database = database_path.read_bytes()
    config = _config(runtime_root)
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )

    status = production_runtime.run_production_status(
        config,
        run_id=RUN_ID,
        now=NOW,
        process_tree_reader=lambda _pid: (),
    )
    recovery = production_runtime.run_production_recovery(
        config,
        run_id=RUN_ID,
        now=NOW,
        process_tree_reader=lambda _pid: (),
    )

    assert status["mode"] == "status"
    assert recovery["mode"] == "recovery"
    assert all(not path.exists() for path in sidecars)
    assert database_path.read_bytes() == before_database


def test_readonly_operations_opener_does_not_create_missing_wal_sidecars(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime-wal-opener"
    database_path = _database(runtime_root)
    journal_connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        assert (
            journal_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()
            == "wal"
        )
    finally:
        journal_connection.close()
    sidecars = tuple(Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm"))
    assert all(not path.exists() for path in sidecars)
    before_database = database_path.read_bytes()
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )

    connection, integrity = production_runtime._open_readonly_operations_database(
        _config(runtime_root)
    )
    try:
        assert integrity == "ok"
    finally:
        connection.close()

    assert all(not path.exists() for path in sidecars)
    assert database_path.read_bytes() == before_database


def test_recovery_does_not_recommend_resuming_finished_failed_or_shortfall_runs(
    tmp_path: Path, monkeypatch
) -> None:
    import sqlite3

    from automation.inago import production_runtime
    from automation.inago.orchestrator import _daily_state

    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    for index, terminal_status in enumerate(("failed", "shortfall")):
        runtime_root = tmp_path / f"runtime-finished-{index}"
        database_path = _database(runtime_root)
        root, state = _daily_state(None)
        state["phase"] = "finished"
        state["terminal_status"] = terminal_status
        state["last_reason"] = "fixture_terminal_result"
        connection = sqlite3.connect(database_path, isolation_level=None)
        try:
            connection.execute(
                "UPDATE run SET status=?, checkpoint=?, finished_at=? WHERE run_id=?",
                (
                    terminal_status,
                    json.dumps(root, separators=(",", ":")),
                    NOW.isoformat(),
                    RUN_ID,
                ),
            )
        finally:
            connection.close()

        recovery = production_runtime.run_production_recovery(
            _config(runtime_root),
            run_id=RUN_ID,
            now=NOW,
            owner_confirmed=True,
            cause_resolved=True,
            process_tree_reader=lambda _pid: (),
        )

        decision = recovery["decision"]
        assert decision["action"] == "terminal_result_saved"
        assert decision["allowed"]["search"] is False
        assert decision["allowed"]["generation"] is False
        assert recovery["resume_entrypoint"] is None


def test_status_and_recovery_block_missing_checkpoint_after_persisted_work(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    cases = (
        ("null-checkpoint-with-attempt", None, 1, 0),
        ("empty-checkpoint-with-commit", "{}", 0, 1),
    )
    for name, checkpoint, attempt_used, committed_count in cases:
        runtime_root = tmp_path / name
        database_path = _database(runtime_root)
        connection = sqlite3.connect(database_path, isolation_level=None)
        try:
            connection.execute(
                """
                UPDATE run
                   SET checkpoint=?, attempt_used=?, committed_count=?
                 WHERE run_id=?
                """,
                (checkpoint, attempt_used, committed_count, RUN_ID),
            )
        finally:
            connection.close()

        config = _config(runtime_root)
        status = production_runtime.run_production_status(
            config, run_id=RUN_ID, now=NOW
        )
        recovery = production_runtime.run_production_recovery(
            config, run_id=RUN_ID, now=NOW
        )

        assert status["status"] == "unknown"
        assert status["reason_codes"] == ["daily_checkpoint_invalid"]
        assert recovery["status"] == "blocked"
        assert recovery["reason_code"] == "daily_checkpoint_invalid"
        assert recovery["execution_requested"] is False

    initial_root = tmp_path / "initial-run-without-checkpoint"
    initial_database = _database(initial_root)
    connection = sqlite3.connect(initial_database, isolation_level=None)
    try:
        connection.execute(
            """
            UPDATE run
               SET checkpoint=NULL, attempt_used=0, metrics_status='not_started'
             WHERE run_id=?
            """,
            (RUN_ID,),
        )
    finally:
        connection.close()
    initial_config = _config(initial_root)
    initial_status = production_runtime.run_production_status(
        initial_config, run_id=RUN_ID, now=NOW
    )
    initial_recovery = production_runtime.run_production_recovery(
        initial_config, run_id=RUN_ID, now=NOW
    )
    assert initial_status.get("reason_codes") != ["daily_checkpoint_invalid"]
    assert initial_recovery.get("reason_code") != "daily_checkpoint_invalid"


def test_status_returns_unknown_when_wal_writer_starts_during_read(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime-wal-race"
    database_path = _database(runtime_root)
    setup = sqlite3.connect(database_path, isolation_level=None)
    try:
        assert setup.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    finally:
        setup.close()
    config = _config(runtime_root)
    original_open = production_runtime._open_readonly_operations_database
    writer_connections: list[sqlite3.Connection] = []

    def open_then_start_writer(selected_config):
        connection, integrity = original_open(selected_config)
        writer = sqlite3.connect(database_path, isolation_level=None)
        writer.execute("UPDATE run SET committed_count=1 WHERE run_id=?", (RUN_ID,))
        writer_connections.append(writer)
        return connection, integrity

    monkeypatch.setattr(
        production_runtime,
        "_assert_production_roots",
        lambda _config: None,
    )
    monkeypatch.setattr(
        production_runtime,
        "_open_readonly_operations_database",
        open_then_start_writer,
    )
    try:
        status = production_runtime.run_production_status(
            config, run_id=RUN_ID, now=NOW
        )
        assert status["status"] == "unknown"
        assert status["reason_codes"] == ["daily_state_changed_during_observation"]
        assert status["previous_snapshot_reused"] is False
    finally:
        for connection in writer_connections:
            connection.close()


def test_status_returns_unknown_if_run_appears_during_immutable_read(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime-wal-no-run-race"
    database_path = _database(runtime_root)
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        assert (
            connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        )
        saved_run = dict(
            connection.execute("SELECT * FROM run WHERE run_id=?", (RUN_ID,)).fetchone()
        )
        connection.execute("DELETE FROM run WHERE run_id=?", (RUN_ID,))
    finally:
        connection.close()
    config = _config(runtime_root)
    original_open = production_runtime._open_readonly_operations_database
    writer_connections: list[sqlite3.Connection] = []

    def open_then_insert_run(selected_config):
        readonly, integrity = original_open(selected_config)
        writer = sqlite3.connect(database_path, isolation_level=None)
        writer.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, checkpoint, metrics_status, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(
                saved_run[column]
                for column in (
                    "run_id",
                    "target_date",
                    "mode",
                    "status",
                    "settings_version",
                    "seed",
                    "started_at",
                    "draft_deadline",
                    "end_deadline",
                    "attempt_used",
                    "committed_count",
                    "checkpoint",
                    "metrics_status",
                    "finished_at",
                )
            ),
        )
        writer_connections.append(writer)
        return readonly, integrity

    monkeypatch.setattr(
        production_runtime,
        "_assert_production_roots",
        lambda _config: None,
    )
    monkeypatch.setattr(
        production_runtime,
        "_open_readonly_operations_database",
        open_then_insert_run,
    )
    try:
        status = production_runtime.run_production_status(config, run_id=None, now=NOW)
        assert status["status"] == "unknown"
        assert status["reason_codes"] == ["daily_state_changed_during_observation"]
    finally:
        for writer in writer_connections:
            writer.close()


def test_uninspectable_operations_snapshot_is_never_reported_stable(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    snapshot = production_runtime._OperationsSourceSnapshot(
        file_signatures=(("state.sqlite-wal", "unavailable"),),
        owner_status="missing",
        owner=None,
        owner_reason="owner_file_missing",
    )
    monkeypatch.setattr(
        production_runtime,
        "_operations_source_snapshot",
        lambda _config: snapshot,
    )

    assert (
        production_runtime._operations_source_is_stable(
            _config(tmp_path / "runtime-uninspectable"), snapshot
        )
        is False
    )


def test_status_fails_closed_while_lock_owner_metadata_is_being_published(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime, run_lock

    runtime_root = tmp_path / "runtime-owner-pending"
    database_path = _database(runtime_root)
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute("DELETE FROM run WHERE run_id=?", (RUN_ID,))
    finally:
        connection.close()
    config = _config(runtime_root)
    original_publish = run_lock._write_owner_atomically
    observations: list[dict[str, object]] = []

    def inspect_pending_before_owner_publish(path: Path, owner: LockOwner) -> None:
        markers = tuple(runtime_root.glob(".daily-run-owner.json.pending.*"))
        assert len(markers) == 1
        observations.append(production_runtime.run_production_status(config, now=NOW))
        original_publish(path, owner)

    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    monkeypatch.setattr(
        run_lock, "_write_owner_atomically", inspect_pending_before_owner_publish
    )
    lock = run_lock.RunLock.acquire(
        runtime_root / ".daily-run.lock",
        runtime_root / ".daily-run-owner.json",
        run_id=RUN_ID,
        pid=os.getpid(),
        started_at=OWNER_START,
    )
    try:
        assert len(observations) == 1
        assert observations[0]["status"] == "unknown"
        assert observations[0]["reason_codes"] == [
            "daily_state_changed_during_observation"
        ]
    finally:
        lock.release()
    assert not tuple(runtime_root.glob(".daily-run-owner.json.pending.*"))


def test_recovery_fails_closed_while_lock_owner_metadata_is_being_published(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime, run_lock

    runtime_root = tmp_path / "runtime-recovery-owner-pending"
    database_path = _database(runtime_root)
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute("DELETE FROM run WHERE run_id=?", (RUN_ID,))
    finally:
        connection.close()
    config = _config(runtime_root)
    original_publish = run_lock._write_owner_atomically
    observations: list[dict[str, object]] = []

    def inspect_pending_before_owner_publish(path: Path, owner: LockOwner) -> None:
        markers = tuple(runtime_root.glob(".daily-run-owner.json.pending.*"))
        assert len(markers) == 1
        observations.append(production_runtime.run_production_recovery(config, now=NOW))
        original_publish(path, owner)

    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    monkeypatch.setattr(
        run_lock, "_write_owner_atomically", inspect_pending_before_owner_publish
    )
    lock = run_lock.RunLock.acquire(
        runtime_root / ".daily-run.lock",
        runtime_root / ".daily-run-owner.json",
        run_id=RUN_ID,
        pid=os.getpid(),
        started_at=OWNER_START,
    )
    try:
        assert len(observations) == 1
        assert observations[0]["status"] == "blocked"
        assert observations[0]["reason_code"] == (
            "daily_state_changed_during_observation"
        )
        assert observations[0]["execution_requested"] is False
    finally:
        lock.release()
    assert not tuple(runtime_root.glob(".daily-run-owner.json.pending.*"))


def test_status_treats_pending_marker_directory_scan_failure_as_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime-unlistable-owner-markers"
    _database(runtime_root)
    config = _config(runtime_root)
    original_scandir = production_runtime.os.scandir

    def denied_scandir(path):
        if Path(path) == runtime_root:
            raise PermissionError("fixture directory listing denied")
        return original_scandir(path)

    monkeypatch.setattr(production_runtime.os, "scandir", denied_scandir)
    snapshot = production_runtime._operations_source_snapshot(config)

    assert snapshot.pending_owner_markers == (("pending_owner_marker", "unavailable"),)
    assert production_runtime._operations_source_is_stable(config, snapshot) is False


def test_run_lock_releases_owner_and_byte_lock_when_pending_marker_close_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from automation.inago import run_lock

    runtime_root = tmp_path / "runtime-marker-close-failure"
    runtime_root.mkdir(parents=True)
    lock_path = runtime_root / ".daily-run.lock"
    owner_path = runtime_root / ".daily-run-owner.json"
    original_factory = run_lock.tempfile.NamedTemporaryFile
    fail_pending_close = [True]

    class CloseFailsOnce:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def close(self):
            self._wrapped.close()
            if fail_pending_close[0]:
                fail_pending_close[0] = False
                raise OSError("fixture pending marker close failure")

    def named_temporary_file(*args, **kwargs):
        temporary = original_factory(*args, **kwargs)
        if kwargs.get("prefix") == f"{owner_path.name}.pending.":
            return CloseFailsOnce(temporary)
        return temporary

    monkeypatch.setattr(run_lock.tempfile, "NamedTemporaryFile", named_temporary_file)
    with pytest.raises(run_lock.RunLockError, match="pending daily lock marker"):
        run_lock.RunLock.acquire(
            lock_path,
            owner_path,
            run_id="RUN-OPS-MARKER-CLOSE-FAILURE",
            pid=os.getpid(),
            started_at=OWNER_START,
        )

    assert LockOwner.from_dict(json.loads(owner_path.read_text(encoding="utf-8"))) == (
        LockOwner(os.getpid(), OWNER_START, "RUN-OPS-MARKER-CLOSE-FAILURE")
    )
    assert not tuple(runtime_root.glob(f"{owner_path.name}.pending.*"))

    lock = run_lock.RunLock.acquire(
        lock_path,
        owner_path,
        run_id="RUN-OPS-MARKER-CLOSE-RECOVERY",
        pid=os.getpid(),
        started_at=OWNER_START,
    )
    lock.release()


def test_marker_close_failure_owner_prevents_false_no_run_status_and_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from automation.inago import production_runtime, run_lock

    runtime_root = tmp_path / "runtime-marker-close-owner-guard"
    _database(runtime_root)
    connection = sqlite3.connect(runtime_root / "state.sqlite", isolation_level=None)
    try:
        connection.execute("DELETE FROM run WHERE run_id=?", (RUN_ID,))
    finally:
        connection.close()
    config = _config(runtime_root)
    owner_path = runtime_root / ".daily-run-owner.json"
    original_factory = run_lock.tempfile.NamedTemporaryFile
    fail_pending_close = [True]

    class CloseFailsOnce:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def close(self):
            self._wrapped.close()
            if fail_pending_close[0]:
                fail_pending_close[0] = False
                raise OSError("fixture pending marker close failure")

    def named_temporary_file(*args, **kwargs):
        temporary = original_factory(*args, **kwargs)
        if kwargs.get("prefix") == f"{owner_path.name}.pending.":
            return CloseFailsOnce(temporary)
        return temporary

    monkeypatch.setattr(run_lock.tempfile, "NamedTemporaryFile", named_temporary_file)
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    with pytest.raises(run_lock.RunLockError, match="pending daily lock marker"):
        run_lock.RunLock.acquire(
            runtime_root / ".daily-run.lock",
            owner_path,
            run_id="RUN-OPS-MARKER-CLOSE-GUARD",
            pid=os.getpid(),
            started_at=OWNER_START,
        )

    assert owner_path.is_file()
    status = production_runtime.run_production_status(config, now=NOW)
    recovery = production_runtime.run_production_recovery(config, now=NOW)

    assert status["status"] == "unknown"
    assert status["reason_codes"] == ["daily_owner_without_run_record"]
    assert recovery["status"] == "blocked"
    assert recovery["reason_code"] == "daily_owner_without_run_record"
    assert recovery["execution_requested"] is False
