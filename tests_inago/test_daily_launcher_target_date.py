from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from automation.inago.clock import FakeClock
from automation.inago.maintenance.daily_launcher import (
    DailyProcessResult,
    _parser as launcher_parser,
    run_daily_launch,
)
from automation.inago.paths import SAFE_FIXTURE_BASE, resolve_runtime_paths
from automation.inago.recovery import plan_resume
from automation.inago.repository import open_database
from automation.inago.settings import register_external_settings
from tests_inago.maintenance_support import make_store
from tests_inago.test_product_page_selection import (
    _FixtureOCR,
    _enable_product_page_pipeline,
    _run,
    _checkpoint,
)
from tests_inago.test_step13 import (
    BASIC_SETTINGS_PATH,
    DETAIL_RULES_PATH,
    NOW,
    RuntimeCase,
    _make_ports,
)


JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "rakuten-room-inago" / "runtime"


@pytest.fixture
def runtime_case(tmp_path: Path, fixture_config_path: Path) -> RuntimeCase:
    private_root = SAFE_FIXTURE_BASE / "product-page-target-date" / tmp_path.name
    paths = resolve_runtime_paths(
        data_root=private_root / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    register_external_settings(
        connection,
        json.loads(BASIC_SETTINGS_PATH.read_text(encoding="utf-8")),
        json.loads(DETAIL_RULES_PATH.read_text(encoding="utf-8")),
        created_at=NOW,
    )
    case = RuntimeCase(
        connection=connection,
        data_root=paths.data_root,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )
    try:
        yield case
    finally:
        connection.close()
        if private_root.resolve().is_relative_to(SAFE_FIXTURE_BASE.resolve()):
            shutil.rmtree(private_root, ignore_errors=True)


def test_skip_once_preserves_next_daily_trigger_and_manual_launch(
    tmp_path: Path,
) -> None:
    scheduler_evidence = json.loads(
        (
            RUNTIME_ROOT
            / "artifacts/product-page-rollout-20261023/scheduler/step01-post-1300.json"
        ).read_text(encoding="utf-8")
    )
    assert scheduler_evidence["task_name"] == "RakutenRoomInagoDaily"
    assert scheduler_evidence["state"] == "Ready"
    assert scheduler_evidence["enabled"] is True
    assert scheduler_evidence["last_run_time"].startswith("2026-10-22")
    assert scheduler_evidence["next_run_time"] == "2026-10-24T13:00:00.0000000+09:00"
    assert scheduler_evidence["number_of_missed_runs"] == 0
    observed_at = datetime.fromisoformat(
        scheduler_evidence["observed_at"].replace("Z", "+00:00")
    ).astimezone(JST)
    assert (observed_at.date().isoformat(), observed_at.hour) == ("2026-10-23", 13)

    parsed = launcher_parser().parse_args(
        [
            "--config",
            str(tmp_path / "settings.json"),
            "--data-root",
            str(tmp_path / "runtime"),
            "--target-date",
            "2026-10-23",
            "--manual",
            "--resume-confirmed",
        ]
    )
    assert (parsed.target_date, parsed.manual, parsed.resume_confirmed) == (
        "2026-10-23",
        True,
        True,
    )
    assert parsed.cause_resolved is False

    store = make_store(tmp_path / "launch-store")
    commands: list[tuple[str, ...]] = []

    def child(command, _cwd, on_started):
        commands.append(tuple(command))
        on_started(8123)
        return DailyProcessResult(
            returncode=4,
            stdout=json.dumps(
                {"status": "failed", "target_date": "2026-10-23"}
            ).encode("utf-8"),
            stderr=b"",
            pid=8123,
        )

    run_daily_launch(
        store,
        data_root=store.database_path.parent.parent,
        config_path=tmp_path / "settings.json",
        now=datetime.fromisoformat("2026-10-24T09:00:00+09:00"),
        process_runner=child,
        cwd=ROOT,
        target_date="2026-10-23",
        manual=True,
        resume_confirmed=True,
    )
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("--target-date") + 1] == "2026-10-23"
    assert "--manual" in command
    assert "--resume-confirmed" in command
    assert "--cause-resolved" not in command


def test_explicit_current_target_date_reaches_official_daily_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests_inago.test_step23_12_daily_e2e import _database_connection, _make_daily_fixture
    from automation.inago import production_runtime

    now = "2026-10-23T13:00:00+09:00"
    config, database_path, clock, _source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path, now=now
    )
    result = production_runtime.run_production_daily(
        config,
        clock=clock,
        target_date="2026-10-23",
        manual=True,
    )

    assert result["target_date"] == "2026-10-23"
    assert result["status"] == "complete", {
        key: result.get(key)
        for key in (
            "status",
            "daily_status",
            "daily_reason",
            "reason",
            "committed_count",
            "error_count",
            "primary_error",
            "phase_results",
        )
    }
    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 15
    assert result["pipeline_trace"]["status"] == "healthy"
    connection = _database_connection(database_path)
    try:
        row = connection.execute(
            "SELECT target_date, started_at, status, committed_count "
            "FROM run WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
        assert tuple(row) == (
            "2026-10-23",
            "2026-10-23T13:00:00+09:00",
            "complete",
            15,
        )
    finally:
        connection.close()
    operations = json.loads(
        (config.runtime_root / "state/daily-operations.json").read_text(
            encoding="utf-8"
        )
    )
    assert operations["target_date"] == "2026-10-23"
    assert operations["run_id"] == result["run_id"]


@pytest.mark.parametrize(
    ("resume_confirmed", "expected_reason"),
    [
        (False, "previous_day_resume_confirmation_required"),
        (True, "previous_day_run_not_found"),
    ],
)
def test_past_target_date_cannot_create_a_new_run_through_production_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resume_confirmed: bool,
    expected_reason: str,
) -> None:
    from tests_inago.test_step23_12_daily_e2e import _database_connection, _make_daily_fixture
    from automation.inago import production_runtime

    config, database_path, clock, _source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path, now="2026-10-24T09:00:00+09:00"
    )
    result = production_runtime.run_production_daily(
        config,
        clock=clock,
        target_date="2026-10-23",
        manual=True,
        resume_confirmed=resume_confirmed,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == expected_reason, {
        "reason": result.get("reason"),
        "secondary_errors": result.get("secondary_errors"),
        "phase_results": result.get("phase_results"),
    }
    connection = _database_connection(database_path)
    try:
        count = connection.execute(
            "SELECT count(*) FROM run WHERE mode='daily' AND target_date=?",
            ("2026-10-23",),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0


def test_authorized_resume_continues_same_incomplete_run_without_false_cause_resolution(
    runtime_case: RuntimeCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case)
    ocr = _FixtureOCR()
    run_id = "PRODUCT-CROSS-DATE-RESUME"
    runtime_case.clock = FakeClock(
        datetime.fromisoformat("2026-10-23T13:00:00+09:00")
    )
    original_generate = __import__(
        "automation.inago.orchestrator", fromlist=["generate_and_commit_copy"]
    ).generate_and_commit_copy
    calls = 0

    def interrupt_after_four_commits(connection, spec, runner, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("fixture interruption")
        return original_generate(connection, spec, runner, **kwargs)

    from automation.inago import orchestrator

    monkeypatch.setattr(
        orchestrator, "generate_and_commit_copy", interrupt_after_four_commits
    )
    first = _run(runtime_case, bundle, ocr, run_id=run_id)
    assert first.status == "partial"
    assert first.committed_count == 4
    assert len(ocr.calls) == 15

    recovery = plan_resume(
        {
            "run_id": run_id,
            "target_date": "2026-10-23",
            "settings_version": "fixture-settings",
            "seed": f"{run_id}-SEED",
            "search_attempts": 1,
            "unfinished_generation_attempts": {"fixture-shop:item": 3},
            "committed_count": 4,
            "run_status": "shortfall",
            "metrics_pending": False,
        },
        now=datetime.fromisoformat("2026-10-24T09:00:00+09:00"),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=False,
        manual=True,
    )
    assert recovery["action"] == "resume_remaining"
    assert recovery["cause_resolved_observed"] is False
    assert recovery["allowed"]["generation"] is True

    saved = _checkpoint(runtime_case.connection, run_id)
    selected_before = [item["offer_key"] for item in saved["selected_products"]]
    runtime_case.clock = FakeClock(
        datetime.fromisoformat("2026-10-24T09:00:00+09:00")
    )
    monkeypatch.setattr(
        orchestrator, "generate_and_commit_copy", original_generate
    )
    second = _run(
        runtime_case,
        bundle,
        ocr,
        run_id=run_id,
        manual=True,
        target_date="2026-10-23",
    )

    assert second.status == "complete"
    assert second.resumed is True
    assert second.target_date == "2026-10-23"
    assert second.committed_count == 15
    assert second.error_count == 0
    assert len(ocr.calls) == 15
    assert len(bundle.runner.requests) == 15
    final_state = _checkpoint(runtime_case.connection, run_id)
    assert [item["offer_key"] for item in final_state["selected_products"][: len(selected_before)]] == selected_before
    assert not final_state.get("cause_resolution_claims")
    assert final_state["resolved_error_history"][-1]["resolution"] == (
        "same_run_target_reached_after_resume"
    )
