"""R11 integration nodes for the shared daily execution pipeline."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from automation.inago import production_runtime
from automation.inago.clock import FakeClock
from automation.inago.daily_probe import (
    PROBE_TEST_ROOT,
    load_daily_probe_config,
    open_probe_database,
    run_daily_probe,
)
from automation.inago.paths import SAFE_FIXTURE_BASE
from automation.inago.settings import register_external_settings
from tests_inago.test_step23_12_daily_e2e import (
    DETAILS_PATH,
    SETTINGS_PATH,
    SETTINGS_VERSION,
    _FixtureAIRoot,
    _FixtureMetrics,
    _FixturePublicSource,
)


NOW = "2026-10-15T13:15:00+09:00"
RUN_ID = "RUN-R11-N22"


def _probe_fixture(tmp_path: Path):
    config_path = tmp_path / "probe-config.json"
    probe_root = tmp_path / "probe"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "isolated_probe",
                "network_allowed": False,
                "test_only": True,
                "probe_id": "R11-N22",
                "probe_root": str(probe_root.resolve()),
            }
        ),
        encoding="utf-8",
    )
    config = load_daily_probe_config(config_path.resolve())
    connection = open_probe_database(config)
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    detailed_rules = json.loads(DETAILS_PATH.read_text(encoding="utf-8"))
    settings_runtime = register_external_settings(
        connection,
        settings,
        detailed_rules,
        created_at=NOW,
    )
    runtime_config = SimpleNamespace(
        adopted_settings=settings,
        detailed_rules=detailed_rules,
    )
    copy_spec_factory = production_runtime._make_copy_spec_factory(
        runtime_config, settings_runtime
    )
    copy_spec_factory.fixture_only = True
    clock = FakeClock(datetime.fromisoformat(NOW))
    source = _FixturePublicSource(config.data_root, clock)
    ai_root = _FixtureAIRoot()
    metrics = _FixtureMetrics()

    def cancel_metrics(_run_id: str) -> bool:
        return True

    cancel_metrics.fixture_only = True
    return (
        config,
        connection,
        clock,
        source,
        ai_root,
        metrics,
        copy_spec_factory,
        cancel_metrics,
    )


def test_n22_probe_executes_daily_html_backup_and_restore_as_one_pipeline(
    tmp_path: Path,
) -> None:
    """The isolated production composition must run all post-daily stages."""

    assert tmp_path.resolve().is_relative_to(PROBE_TEST_ROOT.resolve())
    (
        config,
        connection,
        clock,
        source,
        ai_root,
        metrics,
        copy_spec_factory,
        cancel_metrics,
    ) = _probe_fixture(tmp_path)

    result = run_daily_probe(
        connection,
        config=config,
        source=source,
        ai_root=ai_root,
        copy_spec_factory=copy_spec_factory,
        metrics_port=metrics,
        cancel_metrics_job=cancel_metrics,
        clock=clock,
        run_id_factory=lambda _target: RUN_ID,
        seed_factory=lambda: "R11-N22-SEED",
    )

    assert isinstance(result, dict), (
        "isolated daily probe must return the shared whole-pipeline result, "
        "including dashboard export and end-of-day preservation"
    )
    assert result["status"] == "complete"
    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 15
    assert result["export"]["status"] == "ready"
    assert result["export"]["draft_count"] == 15
    assert result["preservation"]["status"] == "ready"
    assert result["preservation"]["end_backup"]["status"] == "ready"
    assert result["preservation"]["monthly_restore"]["status"] == "ready"
    assert len(source.search_requests) == 3
    assert len(ai_root.copy_runner.requests) == 15
    assert len(metrics.calls) == 1

    backup_database = (
        Path(result["preservation"]["end_backup"]["path"]) / "state.sqlite"
    )
    import sqlite3

    backup = sqlite3.connect(backup_database)
    try:
        assert backup.execute(
            "SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0] == 15
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        backup.close()

    assert (config.data_root / "posting-history.html").is_file()
    assert (config.backup_root / "restore-check" / "2026-10" / "report.json").is_file()


def test_n22b_production_entry_uses_shared_core_after_production_guards(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Production keeps its gates and supplies adapters to the shared core."""

    from automation.inago import daily_pipeline
    from tests_inago.test_step23_12_daily_e2e import (
        _database_connection,
        _make_daily_fixture,
    )

    config, database_path, _fixture_clock, _source, _ai_root, _metrics = (
        _make_daily_fixture(monkeypatch, tmp_path)
    )
    readiness_calls = 0
    root_guard_calls = 0
    original_readiness = production_runtime.require_production_ready
    original_root_guard = production_runtime._assert_production_roots
    original_core = daily_pipeline.run_daily_execution_core
    observed_ports = []

    def checked_readiness(selected_config):
        nonlocal readiness_calls
        readiness_calls += 1
        return original_readiness(selected_config)

    def checked_root_guard(selected_config):
        nonlocal root_guard_calls
        root_guard_calls += 1
        return original_root_guard(selected_config)

    def observed_core(ports, *, clock):
        observed_ports.append(ports)
        return original_core(ports, clock=clock)

    monkeypatch.setattr(production_runtime, "require_production_ready", checked_readiness)
    monkeypatch.setattr(production_runtime, "_assert_production_roots", checked_root_guard)
    monkeypatch.setattr(daily_pipeline, "run_daily_execution_core", observed_core)

    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )

    assert len(observed_ports) == 1
    assert observed_ports[0].data_root == config.runtime_root
    assert readiness_calls == 1
    assert root_guard_calls >= 3
    assert (result["status"], result["daily_status"], result["committed_count"]) == (
        "complete",
        "complete",
        15,
    )
    assert result["phase_results"]["preflight"]["status"] == "ready"
    assert result["phase_results"]["database_open"]["status"] == "ready"
    assert result["phase_results"]["database_close"]["status"] == "closed"
    assert result["export"]["status"] == "ready"
    assert result["preservation"]["status"] == "ready"
    assert result["pipeline_trace"]["status"] == "healthy"

    connection = _database_connection(database_path)
    try:
        row = connection.execute(
            "SELECT status, committed_count FROM run WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
        assert tuple(row) == ("complete", 15)
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 15
    finally:
        connection.close()


def test_manual_production_daily_runs_before_scheduled_hour(monkeypatch, tmp_path):
    from tests_inago.test_step23_12_daily_e2e import _make_daily_fixture

    config, *_ = _make_daily_fixture(monkeypatch, tmp_path)
    clock = FakeClock(datetime.fromisoformat("2026-10-15T09:15:00+09:00"))
    scheduled = production_runtime.run_production_daily(config, clock=clock)
    assert scheduled["status"] == "not_due"
    result = production_runtime.run_production_daily(config, clock=clock, manual=True)
    assert (result["status"], result["committed_count"]) == ("complete", 15)
    assert result["metrics_status"] == "observed"
    assert result["export"]["status"] == "ready"
    assert result["preservation"]["status"] == "ready"


def test_manual_daily_resumes_corrected_widget_before_scheduled_hour(monkeypatch, tmp_path):
    from tests_inago.test_step23_12_daily_e2e import _make_daily_fixture

    config, _db, _clock, source, *_ = _make_daily_fixture(monkeypatch, tmp_path)
    clock = FakeClock(datetime.fromisoformat("2026-10-15T09:15:00+09:00"))
    original_fetch = source.fetch

    def broken_widget(request):
        response = original_fetch(request)
        response.update(status="unavailable", words=[], current_parent=None, refresh_complete=False,
                        fetched_at=None, error_code="query_widget_contract_mismatch")
        response.pop("ui_period_label", None)
        return response

    monkeypatch.setattr(source, "fetch", broken_widget)
    failed = production_runtime.run_production_daily(config, clock=clock, manual=True)
    assert failed["status"] == "failed"
    monkeypatch.setattr(source, "fetch", original_fetch)
    result = production_runtime.run_production_daily(
        config, clock=clock, manual=True, resume_confirmed=True, cause_resolved=True,
    )
    assert result["run_id"] == failed["run_id"]
    assert (result["status"], result["committed_count"]) == ("complete", 15)


def test_n23a_transient_search_and_row_failures_reach_fifteen_supplementally(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A recovered query and three unresolved rows allow the 12-to-15 refill."""

    from tests_inago.test_step23_12_daily_e2e import (
        _database_connection,
        _make_daily_fixture,
    )
    config, database_path, _clock, source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    original_search = source.search
    first_query: str | None = None
    unresolved_rows_by_call: dict[int, list[str]] = {}

    def day_recovery_search(request):
        nonlocal first_query
        result = original_search(request)
        call_number = len(source.search_requests)
        if call_number == 1:
            first_query = request.query
            return {
                **result,
                "current_search_id": None,
                "search_loaded": False,
                "internal_order_confirmed": False,
                "status": "unavailable",
                "fetched_at": None,
                "error_code": "fixture_transient_room_failure",
                "end_reached": False,
                "posts": [],
                "loaded_posts": 0,
                "condition_evidence_ref": None,
            }
        if 2 <= call_number <= 7:
            unresolved_rows_by_call[call_number] = [
                post["stable_room_id"] for post in result["posts"][2:]
            ]
            return {
                **result,
                "posts": result["posts"][:2],
                "loaded_posts": 2,
            }
        return result

    source.search = day_recovery_search
    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )

    assert len(unresolved_rows_by_call) == 6
    assert all(len(rows) == 3 for rows in unresolved_rows_by_call.values())
    assert len(source.search_requests) == 8
    assert source.search_requests[0].query == source.search_requests[1].query
    assert result["status"] == "complete"
    assert result["committed_count"] == 15
    assert result["source_failures"] == 1
    assert result["error_count"] == 0
    assert result["supplemental_allocation_count"] == 3
    assert result["metrics_status"] == "observed"
    assert result["export"]["status"] == "ready"
    assert result["preservation"]["status"] == "ready"

    connection = _database_connection(database_path)
    try:
        attempts = connection.execute(
            "SELECT attempt_id, retry_of, query, source_status "
            "FROM search_attempt WHERE run_id=? ORDER BY rowid",
            (result["run_id"],),
        ).fetchall()
        assert len(attempts) == 8
        assert attempts[0]["source_status"] == "unavailable"
        assert attempts[1]["source_status"] == "observed"
        assert attempts[1]["query"] == attempts[0]["query"]
        assert attempts[1]["retry_of"] == attempts[0]["attempt_id"]
        assert connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (result["run_id"],)
        ).fetchone()[0] == 15
        assert connection.execute(
            "SELECT COUNT(*) FROM reservation JOIN draft USING (offer_key) "
            "WHERE draft.run_id=? AND reservation.status='active'",
            (result["run_id"],),
        ).fetchone()[0] == 15
    finally:
        connection.close()


def test_n23b_wrong_input_hash_stops_before_any_daily_adoption(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """An invalid AI input hash must stop the run and preserve the bad evidence."""

    from tests_inago.test_step23_12_daily_e2e import (
        _database_connection,
        _make_daily_fixture,
    )
    from tests_inago.test_step08 import _root_response, _runner

    config, database_path, _clock, source, ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    original_run = ai_root.run
    rejected_hash = "f" * 64
    copy_calls = 0
    rejected_results = []

    def wrong_hash_copy(request):
        nonlocal copy_calls
        if not request.copy_targets:
            return original_run(request)
        copy_calls += 1
        response = _root_response(
            request,
            delegations=[],
            input_hash=rejected_hash,
        )
        validator_root = tmp_path / f"invalid-hash-{copy_calls}"
        validator_root.mkdir()
        runner, _, _ = _runner(
            validator_root,
            response=json.dumps(response, ensure_ascii=False).encode("utf-8"),
            clock=FakeClock(datetime.fromisoformat(NOW)),
        )
        runner.data_root = config.runtime_root
        result = runner.run(request)
        rejected_results.append(result)
        return result

    ai_root.run = wrong_hash_copy
    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )

    assert result["status"] == "failed"
    assert result["daily_status"] == "failed"
    assert result["committed_count"] == 0
    assert result["daily_reason"] == "copy_runner_contract_rejected"
    assert result["export"]["status"] == "ready"
    assert result["preservation"]["status"] == "ready"
    assert copy_calls == 1
    assert rejected_results[0].adoption_status == "rejected_contract"
    assert rejected_results[0].error == {"code": "input_hash"}
    assert rejected_results[0].artifact is not None
    assert json.loads(
        rejected_results[0].artifact.path.read_text(encoding="utf-8")
    )["input_hash"] == rejected_hash
    assert len(source.search_requests) == 1

    connection = _database_connection(database_path)
    try:
        run = connection.execute(
            "SELECT status, committed_count, checkpoint FROM run WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
        assert run["status"] == "failed"
        assert run["committed_count"] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (result["run_id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM reservation JOIN draft USING (offer_key) "
            "WHERE draft.run_id=? AND reservation.status='active'",
            (result["run_id"],),
        ).fetchone()[0] == 0
        bad_job = connection.execute(
            "SELECT input_hash, error_json, adoption_status, artifact_ref FROM ai_job "
            "WHERE run_id=? AND agent_id='AI-O'",
            (result["run_id"],),
        ).fetchone()
        assert bad_job["input_hash"] == rejected_results[0].input_hash
        assert bad_job["adoption_status"] == "rejected_contract"
        assert json.loads(bad_job["error_json"]) == {
            "code": "copy_runner_contract_rejected",
            "cause_code": "input_hash",
            "artifact_ref": bad_job["artifact_ref"],
        }
        assert bad_job["artifact_ref"] == rejected_results[0].artifact.artifact_ref
        assert connection.execute(
            "SELECT COUNT(*) FROM ai_job WHERE run_id=? AND agent_id!='AI-O'",
            (result["run_id"],),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def _seed_partial_fourteen(database_path: Path, *, run_id: str) -> None:
    from automation.inago.orchestrator import _daily_state

    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        checkpoint, state = _daily_state(None)
        state["initial_theme_ids"] = []
        state["initial_plan_index"] = 0
        state["tasks"] = []
        state["task_cursor"] = 0
        connection.execute(
            "INSERT INTO run "
            "(run_id,target_date,mode,status,settings_version,seed,started_at,"
            "draft_deadline,end_deadline,attempt_used,committed_count,checkpoint,metrics_status) "
            "VALUES (?, '2026-10-15', 'daily', 'partial', ?, ?, ?, NULL, NULL, 0, 14, ?, 'not_started')",
            (
                run_id,
                SETTINGS_VERSION,
                f"{run_id}-SEED",
                NOW,
                json.dumps(checkpoint, ensure_ascii=False, sort_keys=True),
            ),
        )
        for number in range(1, 15):
            shop_id = "n24shop"
            item_id = f"prior{number:02d}"
            offer_key = f"{shop_id}:{item_id}"
            draft_id = f"D-{run_id}-{number:02d}"
            connection.execute(
                "INSERT INTO offer "
                "(offer_key,shop_id,item_id,name,url,image_refs_json,main_theme_id,"
                "aux_theme_ids_json,first_seen,last_seen,facts_json) "
                "VALUES (?, ?, ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')",
                (
                    offer_key,
                    shop_id,
                    item_id,
                    f"prior item {number}",
                    f"https://item.rakuten.co.jp/{shop_id}/{item_id}/",
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO draft "
                "(draft_id,offer_key,run_id,version,body,generated_at,committed_at,"
                "generator_version,pattern_version,status,artifact_ref,pattern_id,evidence_refs_json) "
                "VALUES (?, ?, ?, 1, 'prior committed body', ?, ?, 'fixture', ?, "
                "'committed', NULL, 'P01', '[]')",
                (draft_id, offer_key, run_id, NOW, NOW, SETTINGS_VERSION),
            )
            connection.execute(
                "INSERT INTO reservation "
                "(reservation_id,offer_key,draft_id,status,created_at,released_at,release_reason) "
                "VALUES (?, ?, ?, 'active', ?, NULL, NULL)",
                (f"RES-{run_id}-{number:02d}", offer_key, draft_id, NOW),
            )
    finally:
        connection.close()


def test_n24a_report_apply_restarts_same_fourteen_run_across_old_cutoffs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Eight reports apply atomically across restart before exploration resumes."""

    from automation.inago import orchestrator, report_apply
    from tests_inago.test_step23_12_daily_e2e import (
        _database_connection,
        _make_daily_fixture,
    )

    config, database_path, clock, source, ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    run_id = "RUN-R11-N24"
    _seed_partial_fourteen(database_path, run_id=run_id)

    inbox = config.runtime_root / "incoming-human-reports"
    inbox.mkdir()
    reports = []
    for number in range(1, 9):
        queued_at = datetime.fromisoformat("2026-10-14T21:00:00+09:00").replace(
            minute=number
        )
        reports.append(
            {
                "queued_at": queued_at.isoformat(),
                "report": {
                    "action_id": f"ACT-{run_id}-{number:02d}",
                    "offer_key": f"n24shop:prior{number:02d}",
                    "draft_id": f"D-{run_id}-{number:02d}",
                    "draft_version": 1,
                    "expected_receipt_revision": 0,
                    "action": "posted",
                    "reason": None,
                    "correction_ref": None,
                    "actual_posted_at": None,
                },
            }
        )
    (inbox / "human-reports-2026-10-14.json").write_text(
        json.dumps({"schema_version": 1, "reports": reports}, ensure_ascii=False),
        encoding="utf-8",
    )

    original_apply_action = report_apply._apply_action_in_transaction
    applied_this_call = 0

    def crash_after_three(connection, action, current_run_id, applied_at):
        nonlocal applied_this_call
        applied_this_call += 1
        if applied_this_call == 4:
            raise report_apply.ReportApplyError("fixture_apply_crash")
        return original_apply_action(connection, action, current_run_id, applied_at)

    monkeypatch.setattr(report_apply, "_apply_action_in_transaction", crash_after_three)
    first = production_runtime.run_production_daily(
        config,
        clock=clock,
        resume_confirmed=True,
        cause_resolved=True,
    )

    assert first["status"] == "failed"
    assert first["daily_status"] == "partial"
    assert first["run_id"] == run_id
    assert first["committed_count"] == 14
    assert source.search_requests == []
    assert source.trends_requests == []
    assert not ai_root.copy_runner.requests
    connection = _database_connection(database_path)
    try:
        statuses = connection.execute(
            "SELECT apply_status, COUNT(*) AS count FROM human_action GROUP BY apply_status"
        ).fetchall()
        assert {row["apply_status"]: row["count"] for row in statuses} == {
            "applied": 3,
            "pending": 5,
        }
        run = connection.execute(
            "SELECT seed, committed_count, checkpoint FROM run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        first_checkpoint = json.loads(run["checkpoint"])
        report_state = first_checkpoint["daily_orchestration"]["report_apply"]
        assert run["seed"] == f"{run_id}-SEED"
        assert run["committed_count"] == 14
        assert report_state == {
            "applied_count": 3,
            "pending_count": 5,
            "exploration_allowed": False,
            "error": "fixture_apply_crash",
        }
        assert len(first_checkpoint["report_apply"]["action_ids"]) == 8
        assert connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 14
    finally:
        connection.close()


    monkeypatch.setattr(
        report_apply,
        "_apply_action_in_transaction",
        original_apply_action,
    )
    original_apply_pending = orchestrator.apply_pending_reports
    clock.current = datetime.fromisoformat("2026-10-15T17:44:59+09:00")

    def apply_then_cross_cutoffs(connection, *, run_id, clock, data_root=None):
        result = original_apply_pending(
            connection, run_id=run_id, clock=clock, data_root=data_root
        )
        if result.exploration_allowed:
            clock.current = datetime.fromisoformat("2026-10-15T18:00:01+09:00")
        return result

    monkeypatch.setattr(orchestrator, "apply_pending_reports", apply_then_cross_cutoffs)
    original_search = source.search

    def verify_reports_before_search(request):
        connection = _database_connection(database_path)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status='applied'"
            ).fetchone()[0] == 8
        finally:
            connection.close()
        return original_search(request)

    source.search = verify_reports_before_search
    resumed = production_runtime.run_production_daily(
        config,
        clock=clock,
        resume_confirmed=True,
        cause_resolved=True,
    )

    assert clock.now_jst().isoformat() == "2026-10-15T18:00:01+09:00"
    assert resumed["run_id"] == first["run_id"]
    assert resumed["resumed"] is True
    assert resumed["status"] == "complete"
    assert resumed["committed_count"] == 15
    assert resumed["export"]["status"] == "ready"
    assert resumed["preservation"]["status"] == "ready"
    assert len(source.search_requests) >= 1
    assert len(ai_root.copy_runner.requests) == 1

    connection = _database_connection(database_path)
    try:
        run = connection.execute(
            "SELECT status, seed, committed_count, checkpoint FROM run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        state = json.loads(run["checkpoint"])["daily_orchestration"]
        assert tuple(run[:3]) == ("complete", f"{run_id}-SEED", 15)
        assert connection.execute(
            "SELECT COUNT(*) FROM human_action WHERE apply_status='applied'"
        ).fetchone()[0] == 8
        assert connection.execute(
            "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
        ).fetchone()[0] == 0
        assert state["report_apply"]["pending_count"] == 0
        assert state["report_apply"]["exploration_allowed"] is True
        assert connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 15
    finally:
        connection.close()


def test_n24b_late_ai_response_is_discarded_and_next_offer_continues(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A timed-out copy is not adopted; confirmed request cancel permits progress."""

    from tests_inago.test_step23_12_daily_e2e import (
        _database_connection,
        _make_daily_fixture,
    )

    config, database_path, clock, _source, ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    run_id = "RUN-R11-N24B"
    _seed_partial_fourteen(database_path, run_id=run_id)
    clock.current = datetime.fromisoformat("2026-10-15T17:44:59+09:00")
    original_run = ai_root.run
    late_job_id: str | None = None
    late_offer_key: str | None = None
    copy_calls = 0
    cancelled_jobs: list[str] = []

    def cancel_active_request(_run_id: str, request_id: str) -> bool:
        cancelled_jobs.append(request_id)
        return True

    ai_root.cancel_active_request = cancel_active_request

    def delayed_first_copy(request):
        nonlocal copy_calls, late_job_id, late_offer_key
        result = original_run(request)
        if request.copy_targets:
            copy_calls += 1
            if copy_calls == 1:
                late_job_id = request.job_id
                late_offer_key = request.copy_targets[0].offer_key
                clock.advance(902)
        return result

    ai_root.run = delayed_first_copy
    result = production_runtime.run_production_daily(
        config,
        clock=clock,
        resume_confirmed=True,
        cause_resolved=True,
    )

    assert clock.now_jst().isoformat() == "2026-10-15T18:00:01+09:00"
    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 15
    assert result["status"] == "failed"
    assert result["error_count"] == 1
    assert result["primary_error"] == {
        "phase": "copy",
        "code": "ai_request_timeout",
    }
    assert copy_calls == 2
    assert late_job_id is not None
    assert late_offer_key is not None
    assert cancelled_jobs == [late_job_id]

    connection = _database_connection(database_path)
    try:
        late_job = connection.execute(
            "SELECT result_status, adoption_status, error_json FROM ai_job "
            "WHERE run_id=? AND job_id=? AND agent_id='AI-O'",
            (run_id, late_job_id),
        ).fetchone()
        assert late_job["result_status"] == "timed_out"
        assert json.loads(late_job["error_json"]) == {
            "code": "timed_out",
            "cause_code": "ai_request_timeout",
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=? AND offer_key=?",
            (run_id, late_offer_key),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM reservation WHERE offer_key=? AND status='active'",
            (late_offer_key,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 15
        assert connection.execute(
            "SELECT status, committed_count FROM run WHERE run_id=?", (run_id,)
        ).fetchone()[:] == ("complete", 15)
    finally:
        connection.close()


def test_n25_partial_metrics_and_full_retention_preserve_only_supported_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Partial metrics stay distinct while backups retain protected state."""

    from automation.inago import backup, restore_check
    from automation.inago.artifact_io import write_temp_response
    from automation.inago.metrics import (
        DailyMetricObservation,
        MetricsCollectionResult,
        MetricsSnapshot,
        OrderItemObservation,
        OrderWindowObservation,
        RoomPostObservation,
    )
    from automation.inago.repository import record_pending_human_action
    from automation.inago.statistics import summarize_metrics
    from tests_inago.test_step23_12_daily_e2e import _database_connection, _make_daily_fixture

    config, database_path, clock, _source, _ai_root, metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    assert tmp_path.resolve().is_relative_to(SAFE_FIXTURE_BASE.resolve())
    observed_at = clock.now_jst().isoformat()

    connection = _database_connection(database_path)
    try:
        connection.execute(
            "INSERT INTO run "
            "(run_id,target_date,mode,status,settings_version,seed,started_at,"
            "draft_deadline,end_deadline,attempt_used,committed_count,checkpoint,"
            "metrics_status,finished_at) "
            "VALUES ('RUN-R11-N25-HISTORY','2026-10-14','daily','partial',?,"
            "'N25-HISTORY-SEED','2026-10-14T13:00:00+09:00',NULL,NULL,1,1,NULL,"
            "'not_started','2026-10-14T13:15:00+09:00')",
            (SETTINGS_VERSION,),
        )
        connection.execute(
            "INSERT INTO offer "
            "(offer_key,shop_id,item_id,name,url,image_refs_json,main_theme_id,"
            "aux_theme_ids_json,first_seen,last_seen,facts_json) "
            "VALUES ('n25shop:prior01','n25shop','prior01','N25 fixture item',"
            "'https://item.rakuten.co.jp/n25shop/prior01/','[]','G01-T01','[]',?,?, '[]')",
            ("2026-10-14T13:00:00+09:00", "2026-10-14T13:00:00+09:00"),
        )
        connection.execute(
            "INSERT INTO draft "
            "(draft_id,offer_key,run_id,version,body,generated_at,committed_at,"
            "generator_version,pattern_version,status,artifact_ref,pattern_id,evidence_refs_json) "
            "VALUES ('D-N25-HISTORY','n25shop:prior01','RUN-R11-N25-HISTORY',1,"
            "'N25 historical posted draft','2026-10-14T13:05:00+09:00',"
            "'2026-10-14T13:05:00+09:00','fixture','fixture','posted',NULL,'P01','[]')"
        )
        connection.execute(
            "INSERT INTO reservation "
            "(reservation_id,offer_key,draft_id,status,created_at,released_at,release_reason) "
            "VALUES ('RES-N25-HISTORY','n25shop:prior01','D-N25-HISTORY','released',"
            "'2026-10-14T13:05:00+09:00','2026-10-14T13:10:00+09:00','posted')"
        )
        connection.execute(
            "INSERT INTO publication_event "
            "(event_id,offer_key,draft_id,draft_version,reported_at,actual_posted_at,"
            "room_url,evidence_kind,valid,revision) "
            "VALUES ('POST-N25-HISTORY','n25shop:prior01','D-N25-HISTORY',1,"
            "'2026-10-14T13:10:00+09:00','2026-10-14T13:10:00+09:00',"
            "'https://room.rakuten.co.jp/room_demo/items/POST-N25-HISTORY',"
            "'human_statement',1,0)"
        )
        connection.execute(
            "INSERT INTO exclusion_key (offer_key,reason,evidence_refs_json,effective,updated_at) "
            "VALUES ('n25shop:prior01','actual_posted','[\"POST-N25-HISTORY\"]',1,?)",
            ("2026-10-14T13:10:00+09:00",),
        )

        expired_artifact = write_temp_response(
            data_root=config.runtime_root,
            artifact_ref="N25-EXPIRED-RAW",
            payload={"fixture": "expired private raw"},
            source_kind="daily",
            created_at="2026-10-15T12:00:00+09:00",
            retention_expires_at="2026-10-15T13:14:59+09:00",
        )
        connection.execute(
            "INSERT INTO artifact "
            "(artifact_ref,path,hash,created_at,source_kind,retention_expires_at,status) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                expired_artifact.artifact_ref,
                str(expired_artifact.path),
                expired_artifact.content_hash,
                expired_artifact.created_at,
                expired_artifact.source_kind,
                expired_artifact.retention_expires_at,
                expired_artifact.status,
            ),
        )

        for day in range(1, 7):
            backup.create_daily_backup(
                connection,
                data_root=config.runtime_root,
                backup_root=config.backup_root,
                now=datetime.fromisoformat(f"2026-10-{day:02d}T13:00:00+09:00"),
                fixture_mode=True,
                generation_stage="final",
            )
        connection.commit()
    finally:
        connection.close()

    def collect_partial_metrics(connection, *, run_id: str, deadline_at: str):
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        return MetricsCollectionResult(
            run_id=run_id,
            status="partial",
            observed_at=observed_at,
            snapshots=(
                MetricsSnapshot(
                    source_key="affiliate",
                    source="affiliate",
                    status="partial",
                    observed_at=observed_at,
                    payload={"error_code": "authentication_required"},
                ),
                MetricsSnapshot(
                    source_key="room",
                    source="room",
                    status="observed",
                    observed_at=observed_at,
                    payload={"fixture": True, "window": "room-recent-30d"},
                ),
            ),
            daily_observations=(
                DailyMetricObservation(
                    source_key="affiliate",
                    ref="2026-10-14",
                    metric="clicks",
                    value=0,
                    unit="count",
                    observed_at=observed_at,
                    window="affiliate-daily:2026-10-14",
                    availability="observed",
                    provisional=False,
                ),
                DailyMetricObservation(
                    source_key="affiliate",
                    ref="2026-10-14",
                    metric="sales_yen",
                    value=None,
                    unit="JPY",
                    observed_at=observed_at,
                    window="affiliate-daily:2026-10-14",
                    availability="unknown",
                    provisional=True,
                ),
            ),
            orders=(
                OrderItemObservation(
                    source_key="affiliate",
                    order_ref="ORDER-N25",
                    is_temporary_ref=False,
                    occurred_at="2026-10-14T10:00:00+09:00",
                    shop_name="N25 fixture shop",
                    product_name="N25 fixture item",
                    item_code="n25shop:prior01",
                    product_url="https://item.rakuten.co.jp/n25shop/prior01/",
                    sales_yen=0,
                    commission_yen=None,
                    order_status="未確定",
                    link_type="商品リンク",
                    window="affiliate-orders:2026-10",
                    observed_at=observed_at,
                    entry_post_ref=None,
                ),
            ),
            order_windows=(
                OrderWindowObservation(
                    source_key="affiliate",
                    window="affiliate-orders:2026-10",
                    count=0,
                    observed_at=observed_at,
                    availability="observed",
                ),
                OrderWindowObservation(
                    source_key="affiliate",
                    window="affiliate-orders:2026-09",
                    count=None,
                    observed_at=observed_at,
                    availability="unavailable",
                ),
            ),
            room_posts=(
                RoomPostObservation(
                    source_key="room",
                    post_id="ROOM-N25",
                    room_post_url="https://room.rakuten.co.jp/room_demo/items/POST-N25-HISTORY",
                    item_url="https://item.rakuten.co.jp/n25shop/prior01/",
                    likes=None,
                    observed_at=observed_at,
                    window="room-recent-30d:2026-09-15/2026-10-15",
                    post_created_at="2026-10-14T13:10:00+09:00",
                ),
            ),
        )

    metrics.collect = collect_partial_metrics
    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 15
    assert result["metrics_status"] == "partial"
    assert result["status"] == "metrics_failed"
    assert result["export"]["status"] == "ready"
    assert result["start_backup"]["status"] == "ready"
    assert result["preservation"]["status"] == "ready"
    assert result["preservation"]["monthly_restore"]["status"] == "ready"
    start_manifest = json.loads(
        (Path(result["start_backup"]["path"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    first_end_manifest = json.loads(
        (Path(result["preservation"]["end_backup"]["path"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert "N25-EXPIRED-RAW" not in {
        item["artifact_ref"] for item in start_manifest["artifacts"]
    }
    assert "N25-EXPIRED-RAW" not in {
        item["artifact_ref"] for item in first_end_manifest["artifacts"]
    }

    connection = _database_connection(database_path)
    try:
        expired = connection.execute(
            "SELECT path,status FROM artifact WHERE artifact_ref='N25-EXPIRED-RAW'"
        ).fetchone()
        assert expired["status"] == "expired"
        assert not Path(expired["path"]).exists()
        values = connection.execute(
            "SELECT scope,ref,metric,value_json,availability,window "
            "FROM metric_observation WHERE "
            "(scope='day_aggregate' AND ref='2026-10-14') OR "
            "(scope='order_item' AND (ref='ORDER-N25' OR ref LIKE 'order-window:%')) OR "
            "(scope='room_post' AND ref='POST-N25-HISTORY')"
        ).fetchall()
        records = {
            (row["scope"], row["ref"], row["metric"]): row for row in values
        }
        clicks = records[("day_aggregate", "2026-10-14", "clicks")]
        assert (clicks["value_json"], clicks["availability"]) == ("0", "observed")
        daily_sales = records[("day_aggregate", "2026-10-14", "sales_yen")]
        assert daily_sales["value_json"] in (None, "null")
        assert daily_sales["availability"] == "unknown"
        assert daily_sales["window"] == "affiliate-daily:2026-10-14"
        order_sales = records[("order_item", "ORDER-N25", "sales_yen")]
        assert (order_sales["value_json"], order_sales["availability"]) == (
            "0",
            "observed",
        )
        order_commission = records[
            ("order_item", "ORDER-N25", "commission_unconfirmed_yen")
        ]
        assert order_commission["value_json"] in (None, "null")
        assert order_commission["availability"] == "unknown"
        empty_window = records[
            (
                "order_item",
                "order-window:affiliate-orders:2026-10",
                "sales_count",
            )
        ]
        unavailable_window = records[
            (
                "order_item",
                "order-window:affiliate-orders:2026-09",
                "sales_count",
            )
        ]
        assert (empty_window["value_json"], empty_window["availability"]) == (
            "0",
            "observed",
        )
        assert unavailable_window["value_json"] in (None, "null")
        assert unavailable_window["availability"] == "unavailable"
        room_likes = records[("room_post", "POST-N25-HISTORY", "likes")]
        assert room_likes["value_json"] in (None, "null")
        assert room_likes["availability"] == "unknown"
        assert json.loads(room_likes["window"])["post_age_seconds"] > 0
        order = connection.execute(
            "SELECT match_kind,matched_offer_key,entry_post_ref "
            "FROM revenue_order WHERE order_ref='ORDER-N25'"
        ).fetchone()
        assert tuple(order) == ("purchased_item_match", "n25shop:prior01", None)
        summary = summarize_metrics(connection)
        assert summary.affiliate_daily["clicks"].known_total == 0
        assert summary.affiliate_daily["sales_yen"].known_total is None
        assert summary.order_count.known_total == 0
        assert summary.order_count.missing_count == 1
    finally:
        connection.close()

    target_draft = None
    connection = _database_connection(database_path)
    try:
        target_draft = connection.execute(
            "SELECT offer_key,draft_id,version FROM draft WHERE run_id=? LIMIT 1",
            (result["run_id"],),
        ).fetchone()
        assert target_draft is not None
        clock.current = datetime.fromisoformat("2026-10-15T21:30:00+09:00")
        record_pending_human_action(
            connection,
            {
                "action_id": "ACT-R11-N25-POSTED",
                "offer_key": target_draft["offer_key"],
                "draft_id": target_draft["draft_id"],
                "draft_version": target_draft["version"],
                "expected_receipt_revision": 0,
                "action": "posted",
                "reason": None,
                "correction_ref": None,
                "actual_posted_at": "2026-10-15T21:20:00+09:00",
            },
            clock=clock,
        )
        record_pending_human_action(
            connection,
            {
                "action_id": "ACT-R11-N25-CORRECTION",
                "offer_key": target_draft["offer_key"],
                "draft_id": target_draft["draft_id"],
                "draft_version": target_draft["version"],
                "expected_receipt_revision": 1,
                "action": "correction",
                "reason": "fixture correction",
                "correction_ref": "ACT-R11-N25-POSTED",
                "actual_posted_at": "2026-10-15T21:21:00+09:00",
            },
            clock=clock,
        )
    finally:
        connection.close()

    clock.current = datetime.fromisoformat("2026-11-01T13:00:00+09:00")
    preservation = production_runtime.run_production_end_of_day_preservation(
        config,
        now=clock.now_jst(),
        run_id=result["run_id"],
    )
    assert preservation["status"] == "ready"
    assert preservation["monthly_restore"]["status"] == "ready"

    retained = backup.list_daily_backups(config.backup_root, generation_stage="final")
    assert len(retained) == 7
    retained_created = [
        json.loads((path / "manifest.json").read_text(encoding="utf-8"))["created_at"]
        for path in retained
    ]
    assert all(not created.startswith("2026-10-01") for created in retained_created)
    assert any(created.startswith("2026-11-01") for created in retained_created)

    restore_report = json.loads(
        (Path(preservation["monthly_restore"]["path"]) / "report.json").read_text(
            encoding="utf-8"
        )
    )
    assert restore_report["integrity_check"] == "ok"
    assert restore_report["foreign_key_errors"] == 0
    assert restore_report["table_counts_match"] is True
    assert restore_report["selection_allowed"] is False
    assert restore_report["requires_supplementation"] is True
    assert restore_report["active_reservations"] == 15
    assert restore_report["pending_reports"] == 2
    assert restore_report["correction_count"] == 1
    assert restore_report["posted_count"] == 1

    restored = _database_connection(
        Path(preservation["monthly_restore"]["path"])
        / "restored-data"
        / "state.sqlite"
    )
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("PRAGMA foreign_key_check").fetchall() == []
        assert restored.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=? AND status='committed'",
            (result["run_id"],),
        ).fetchone()[0] == 15
        assert restored.execute(
            "SELECT COUNT(*) FROM reservation WHERE status='active'"
        ).fetchone()[0] == 15
        assert restored.execute(
            "SELECT COUNT(*) FROM human_action WHERE apply_status='pending'"
        ).fetchone()[0] == 2
        assert restored.execute(
            "SELECT COUNT(*) FROM human_action WHERE action='correction' "
            "AND apply_status='pending'"
        ).fetchone()[0] == 1
        assert restored.execute(
            "SELECT effective FROM exclusion_key WHERE offer_key='n25shop:prior01'"
        ).fetchone()[0] == 1
        restored_expired = restored.execute(
            "SELECT path,status FROM artifact WHERE artifact_ref='N25-EXPIRED-RAW'"
        ).fetchone()
        assert restored_expired["status"] == "expired"
        assert "expired" in Path(restored_expired["path"]).parts
        assert not Path(restored_expired["path"]).exists()
    finally:
        restored.close()

def test_n22c_monitor_start_failure_survives_successful_stop_status(
    tmp_path: Path,
) -> None:
    """Cleanup success must not erase the operations monitor start failure."""

    from automation.inago.daily_pipeline import (
        DailyExecutionPorts,
        run_daily_execution_core,
    )

    clock = FakeClock(datetime.fromisoformat(NOW))
    monitor_calls: list[str] = []
    daily_calls = 0

    class Monitor:
        def start(self) -> None:
            monitor_calls.append("start")
            raise RuntimeError("monitor_start_failed")

        def stop(self) -> None:
            monitor_calls.append("stop")

    def run_daily(_connection, _context, _on_draft_commit):
        nonlocal daily_calls
        daily_calls += 1
        return {"status": "complete", "committed_count": 15}

    ports = DailyExecutionPorts(
        data_root=tmp_path,
        preflight=lambda _now: {},
        open_database=lambda: sqlite3.connect(":memory:"),
        prepare=lambda _connection, _now, _preflight: {},
        start_backup=lambda _connection, _context, _now: {"status": "ready"},
        operations_monitor=lambda _connection, _context, _target: Monitor(),
        run_daily=run_daily,
        export_dashboard=lambda _context: {"status": "ready"},
        preserve_end_of_day=lambda _context, _now, _run_id: {
            "status": "ready",
            "end_backup": {"status": "ready"},
            "monthly_restore": {"status": "already_checked", "already_checked": True},
        },
    )

    result = run_daily_execution_core(ports, clock=clock)

    assert monitor_calls == ["start", "stop"]
    assert daily_calls == 0
    assert result["status"] == "failed"
    assert result["phase_results"]["operations_monitor"]["status"] == "failed"
    assert result["phase_results"]["database_close"]["status"] == "closed"
