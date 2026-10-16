"""R02-only checks for the isolated probe and run-history boundaries."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from automation.inago.daily_probe import (
    DailyProbeConfigError,
    PROBE_TEST_ROOT,
    PRODUCTION_RUNTIME_ROOT,
    assert_probe_database,
    compose_daily_probe_ports,
    load_daily_probe_config,
    open_probe_database,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_TEST_ROOT = PROBE_TEST_ROOT / "daily-fix-20261016"
REPLAY_PATH = TASK_TEST_ROOT / "R02" / "N02" / "replay.json"
REPLAY_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "daily_fix_replay.schema.json"
)


class _FixtureSource:
    fixture_only = True

    def __init__(self, evidence_root: Path) -> None:
        self.evidence_root = evidence_root
        self.calls = 0

    def search(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("probe composition must not call the public source")

    def fetch(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("probe composition must not fetch")

    def product_fact(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("probe composition must not fetch product facts")

    def cancel_owned_jobs(self, *_args, **_kwargs):
        self.calls += 1
        return True


class _FixtureAIRoot:
    fixture_only = True

    def __init__(self) -> None:
        self.calls = 0

    def run(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("probe composition must not invoke AI")

    def cancel_owned_jobs(self, *_args, **_kwargs):
        self.calls += 1
        return True


class _FixtureMetrics:
    fixture_only = True

    def __init__(self) -> None:
        self.calls = 0

    def collect(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("probe composition must not collect metrics")


def _write_config(path: Path, *, probe_root: Path, **changes) -> None:
    document = {
        "schema_version": 1,
        "environment": "isolated_probe",
        "network_allowed": False,
        "test_only": True,
        "probe_id": "R02-N02",
        "probe_root": str(probe_root),
    }
    document.update(changes)
    path.write_text(json.dumps(document), encoding="utf-8")


def _contains_path(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return path.resolve() != parent.resolve()


def test_n02(tmp_path: Path) -> None:
    """A new N02 node covers replay integrity, loader refusal, and safe composition."""

    replay = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
    schema = json.loads(REPLAY_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(replay)

    run_id = replay["fixture_ids"]["run_id"]
    trends_ids = replay["fixture_ids"]["trends_attempt_ids"]
    room_ids = replay["fixture_ids"]["room_attempt_ids"]
    all_ids = [run_id, *trends_ids, *room_ids, replay["fixture_ids"]["copy_attempt_id"]]
    assert len(all_ids) == len(set(all_ids))
    assert len(trends_ids) == replay["entity_counts"]["trends_attempt"] == 20
    assert len(room_ids) == replay["entity_counts"]["room_attempt"] == 7
    assert replay["foreign_key_counts"] == {
        "trends_to_run": 20,
        "room_to_run": 7,
        "copy_to_run": 1,
    }
    assert replay["trends"]["reason_counts"]["source_timeout"] + replay[
        "trends"
    ]["reason_counts"]["query_widget_ambiguous"] == len(trends_ids)
    assert replay["room"]["successes"] + replay["room"]["condition_popup"][
        "failed_close_attempts"
    ] + replay["room"]["product_detail_click_timeouts"] == len(room_ids)
    assert replay["copy"]["expected_input_hash"] != replay["copy"]["observed_input_hash"]
    assert replay["cli"]["daily_target"] - replay["cli"]["committed_count"] == replay[
        "cli"
    ]["shortfall"]

    config_path = tmp_path / "daily-probe.json"
    probe_root = tmp_path / "probe"
    _write_config(config_path, probe_root=probe_root)
    config = load_daily_probe_config(config_path)
    assert config.environment == "isolated_probe"
    assert config.network_allowed is False and config.test_only is True
    assert _contains_path(config.probe_root, PROBE_TEST_ROOT)
    assert all(_contains_path(path, config.probe_root) for path in config.write_roots)
    assert not config.probe_root.exists(), "config loading must not create a write root"

    for changes in (
        {"environment": "production"},
        {"network_allowed": True},
        {"test_only": False},
    ):
        invalid_path = tmp_path / f"invalid-{len(changes)}-{len(changes.values())}.json"
        _write_config(invalid_path, probe_root=probe_root, **changes)
        with pytest.raises(DailyProbeConfigError):
            load_daily_probe_config(invalid_path)

    production_root_config = tmp_path / "production-root.json"
    _write_config(production_root_config, probe_root=PRODUCTION_RUNTIME_ROOT)
    with pytest.raises(DailyProbeConfigError):
        load_daily_probe_config(production_root_config)
    test_root_config = tmp_path / "test-root.json"
    _write_config(test_root_config, probe_root=PROBE_TEST_ROOT)
    with pytest.raises(DailyProbeConfigError):
        load_daily_probe_config(test_root_config)
    with pytest.raises(DailyProbeConfigError):
        load_daily_probe_config(PROJECT_ROOT / "tests_inago" / "config" / "fixture.json")

    connection = open_probe_database(config)
    try:
        database_path = assert_probe_database(connection, config)
        assert database_path == config.database_path.resolve()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        for table in ("run", "search_attempt", "draft"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()

    memory_connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(DailyProbeConfigError):
            assert_probe_database(memory_connection, config)
    finally:
        memory_connection.close()

    source = _FixtureSource(config.observations_root)
    ai_root = _FixtureAIRoot()
    metrics = _FixtureMetrics()

    def copy_spec_factory(*_args, **_kwargs):
        raise AssertionError("probe composition must not invoke copy generation")

    copy_spec_factory.fixture_only = True

    def cancel_metrics_job(_job_id: str) -> bool:
        raise AssertionError("probe composition must not cancel a metrics job")

    cancel_metrics_job.fixture_only = True
    ports = compose_daily_probe_ports(
        config=config,
        source=source,
        ai_root=ai_root,
        copy_spec_factory=copy_spec_factory,
        metrics_port=metrics,
        cancel_metrics_job=cancel_metrics_job,
    )
    assert ports.fixture_only is True
    assert (source.calls, ai_root.calls, metrics.calls) == (0, 0, 0)

    non_fixture_source = _FixtureSource(config.observations_root)
    non_fixture_source.fixture_only = False
    with pytest.raises(DailyProbeConfigError):
        compose_daily_probe_ports(
            config=config,
            source=non_fixture_source,
            ai_root=ai_root,
            copy_spec_factory=copy_spec_factory,
            metrics_port=metrics,
            cancel_metrics_job=cancel_metrics_job,
        )

    acceptance_test = (PROJECT_ROOT / "tests_inago" / "test_acceptance.py").read_text(
        encoding="utf-8"
    )
    acceptance_runner = (PROJECT_ROOT / "automation" / "inago" / "acceptance.py").read_text(
        encoding="utf-8"
    )
    probe_source = (PROJECT_ROOT / "automation" / "inago" / "daily_probe.py").read_text(
        encoding="utf-8"
    )
    assert "fixture_subset_result" in acceptance_test
    assert "subprocess.run(" in acceptance_runner
    assert "load_production_config" not in probe_source
    assert "production_runtime" not in probe_source
