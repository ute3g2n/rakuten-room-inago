"""Local contracts for the isolated STEP 02 runtime foundation."""

from __future__ import annotations

import importlib
import json
from copy import deepcopy
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from jsonschema.exceptions import ValidationError


JST = ZoneInfo("Asia/Tokyo")


def test_fixture_contract_is_synthetic_and_external_access_is_disabled(
    fixture_document: dict[str, object],
) -> None:
    assert fixture_document["environment"] == "fixture"
    assert fixture_document["network_allowed"] is False
    assert fixture_document["settings_version"] == "confirmed-0.3-step46-all132"
    assert fixture_document["detailed_writing_rules"] == {
        "version": "detailed-writing-rules-candidate-0.1-step68",
        "adopted": False,
        "enabled": False,
    }
    for field in ("offer", "word", "report", "metric"):
        assert fixture_document[field]["test_only"] is True
    assert fixture_document["metric"]["revenue_yen"] is None


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    (
        (("target_date",), "2026-13-40"),
        (("report", "reported_at"), "not-a-date-time"),
    ),
)
def test_fixture_schema_rejects_invalid_date_formats(
    fixture_document: dict[str, object], fixture_validator, field_path, invalid_value
) -> None:
    malformed = deepcopy(fixture_document)
    target = malformed
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = invalid_value

    with pytest.raises(ValidationError):
        fixture_validator(malformed)


def test_path_contract_requires_explicit_values_and_confines_fixture_writes(
    project_root: Path,
    fixture_config_path: Path,
) -> None:
    paths = importlib.import_module("automation.inago.paths")
    safe_base = paths.SAFE_FIXTURE_BASE
    data_root = safe_base / "step-02" / "case-path" / "state"

    resolved = paths.resolve_runtime_paths(
        data_root=data_root,
        config_path=fixture_config_path,
        fixture_mode=True,
    )

    assert resolved.data_root == data_root.resolve()
    assert resolved.config_path == fixture_config_path.resolve()
    assert resolved.workspace_root == project_root.resolve()
    assert not data_root.exists()

    for keyword in ("data_root", "config_path"):
        values = {
            "data_root": data_root,
            "config_path": fixture_config_path,
            "fixture_mode": True,
        }
        values[keyword] = None
        with pytest.raises(paths.PathContractError):
            paths.resolve_runtime_paths(**values)

    escaped_roots = (
        project_root / "rakuten-room-inago",
        project_root / "rakuten-room-kai",
        safe_base / ".." / ".." / "rakuten-room-kai",
        project_root.parent / "outside-step-test-area",
    )
    for escaped_root in escaped_roots:
        with pytest.raises(paths.UnsafePathError):
            paths.resolve_runtime_paths(
                data_root=escaped_root,
                config_path=fixture_config_path,
                fixture_mode=True,
            )
    assert not data_root.exists()


def test_fake_clock_crosses_jst_midnight_and_uses_no_real_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock_module = importlib.import_module("automation.inago.clock")
    fake = clock_module.FakeClock(datetime(2026, 10, 12, 23, 59, tzinfo=JST))
    deadline = fake.monotonic() + 60

    monkeypatch.setattr(
        clock_module.time,
        "sleep",
        lambda _seconds: pytest.fail("FakeClock must not sleep in real time"),
    )
    assert fake.now_jst() == datetime(2026, 10, 12, 23, 59, tzinfo=JST)
    assert fake.deadline_remaining(deadline) == 60

    fake.wait(60)

    assert fake.now_jst() == datetime(2026, 10, 13, 0, 0, tzinfo=JST)
    assert fake.monotonic() == 60
    assert fake.deadline_remaining(deadline) == 0


def test_fake_clock_controls_1320_1745_and_1800_without_wall_clock_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock_module = importlib.import_module("automation.inago.clock")
    fake = clock_module.FakeClock(datetime(2026, 10, 12, 13, 20, tzinfo=JST))
    cutoff_deadline = fake.monotonic() + timedelta(hours=4, minutes=25).total_seconds()
    metrics_deadline = fake.monotonic() + timedelta(hours=4, minutes=40).total_seconds()

    monkeypatch.setattr(
        clock_module.time,
        "sleep",
        lambda _seconds: pytest.fail("FakeClock must not sleep in real time"),
    )
    assert fake.deadline_remaining(cutoff_deadline) == 4 * 60 * 60 + 25 * 60
    assert fake.deadline_remaining(metrics_deadline) == 4 * 60 * 60 + 40 * 60

    fake.wait(4 * 60 * 60 + 25 * 60)
    assert fake.now_jst() == datetime(2026, 10, 12, 17, 45, tzinfo=JST)
    assert fake.deadline_remaining(cutoff_deadline) == 0
    assert fake.deadline_remaining(metrics_deadline) == 15 * 60

    fake.wait(15 * 60)
    assert fake.now_jst() == datetime(2026, 10, 12, 18, 0, tzinfo=JST)
    assert fake.deadline_remaining(metrics_deadline) == 0


@pytest.mark.parametrize(
    "mode",
    (
        "setup",
        "daily",
        "status",
        "recovery",
        "config-change",
        "export",
        "serve",
        "migrate",
        "backup",
        "restore-check",
        "acceptance",
    ),
)
def test_cli_modes_are_explicit_stubs_without_side_effects(
    mode: str,
    external_call_spy,
    fixture_config_path: Path,
    tmp_path: Path,
) -> None:
    cli = importlib.import_module("automation.inago.cli")
    data_root = tmp_path / mode / "state"
    stdout = StringIO()

    exit_code = cli.main(
        [
            mode,
            "--config",
            str(fixture_config_path),
            "--data-root",
            str(data_root),
        ],
        ports=external_call_spy,
        stdout=stdout,
    )

    result = json.loads(stdout.getvalue())
    assert exit_code == cli.EXIT_NOT_IMPLEMENTED
    assert result == {"status": "not_implemented", "mode": mode}
    assert external_call_spy.calls == {
        "database": 0,
        "browser": 0,
        "ai": 0,
        "metrics": 0,
        "html": 0,
    }
    assert not data_root.exists()


def test_cli_requires_explicit_config_and_data_root() -> None:
    cli = importlib.import_module("automation.inago.cli")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["daily"])

    assert exc_info.value.code == 2
