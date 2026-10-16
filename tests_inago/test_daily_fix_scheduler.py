"""R13 acceptance for an isolated daily probe under the normal task identity."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any
from types import SimpleNamespace

import pytest

from automation.inago import production_runtime
from automation.inago.clock import FakeClock
from automation.inago.daily_probe import (
    PROBE_TEST_ROOT,
    load_daily_probe_config,
    open_probe_database,
    run_daily_probe,
)
from automation.inago.settings import register_external_settings
from tests_inago.test_step23_12_daily_e2e import (
    DETAILS_PATH,
    SETTINGS_PATH,
    _FixtureAIRoot,
    _FixtureMetrics,
    _FixturePublicSource,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = PROJECT_ROOT / "rakuten-room-inago" / "runtime"
NOW = "2026-10-17T13:15:00+09:00"
RUN_ID = "RUN-R13-N30-20261017"


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element:
    for candidate in element.iter():
        if candidate is not element and _local_name(candidate) == name:
            return candidate
    raise AssertionError(f"task XML is missing {name}")


def _text(element: ET.Element, name: str) -> str:
    value = _child(element, name).text
    assert value is not None
    return value.strip()


def _task_xml(task_name: str) -> ET.Element:
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    script = r"""
$ErrorActionPreference = 'Stop'
$xml = Export-ScheduledTask -TaskName $env:INAGO_R13_QUERY_TASK_NAME -TaskPath '\' -ErrorAction Stop
[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes([string]$xml))
"""
    completed = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="ascii",
        timeout=15,
        env={**os.environ, "INAGO_R13_QUERY_TASK_NAME": task_name},
    )
    assert completed.returncode == 0, "Task Scheduler XML query failed"
    xml_text = base64.b64decode(completed.stdout.strip()).decode("utf-16le")
    return ET.fromstring(xml_text)


def _task_metadata(task_name: str) -> dict[str, Any]:
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    script = r"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $env:INAGO_R13_QUERY_TASK_NAME -TaskPath '\' -ErrorAction Stop
$principalId = [string]$task.Principal.UserId
if ($principalId -match '^S-1-') {
    $principalSid = ([Security.Principal.SecurityIdentifier]::new($principalId)).Value
} else {
    $principalSid = ([Security.Principal.NTAccount]::new($principalId)).Translate([Security.Principal.SecurityIdentifier]).Value
}
$action = $task.Actions[0]
$json = [pscustomobject]@{
    State = $task.State.ToString()
    PrincipalSid = $principalSid
    LogonType = $task.Principal.LogonType.ToString()
    RunLevel = $task.Principal.RunLevel.ToString()
    ExecutionTimeLimit = [string]$task.Settings.ExecutionTimeLimit
    RestartCount = [string]$task.Settings.RestartCount
    RestartInterval = [string]$task.Settings.RestartInterval
    Command = [string]$action.Execute
    Arguments = [string]$action.Arguments
    WorkingDirectory = [string]$action.WorkingDirectory
} | ConvertTo-Json -Compress
[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes([string]$json))
"""
    completed = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="ascii",
        timeout=15,
        env={**os.environ, "INAGO_R13_QUERY_TASK_NAME": task_name},
    )
    assert completed.returncode == 0, "Task Scheduler API query failed"
    metadata_json = base64.b64decode(completed.stdout.strip()).decode("utf-16le")
    result = json.loads(metadata_json)
    assert isinstance(result, dict)
    return result


def _current_sid() -> str:
    whoami = Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe"
    completed = subprocess.run(
        [str(whoami), "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        encoding="mbcs",
        timeout=15,
    )
    match = re.search(r"S-1-\d+(?:-\d+)+", completed.stdout)
    assert match is not None, "current Windows SID could not be read"
    return match.group(0)


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def test_n30(tmp_path: Path) -> None:
    """Check production startup read-only and run the shared core in a task context."""

    context_path_value = os.environ.get("INAGO_R13_CONTEXT_FILE")
    if not context_path_value:
        pytest.skip("N30 is executed only by its temporary normal-principal task")
    assert os.name == "nt", "N30 scheduler context requires Windows"
    context_path = Path(context_path_value).resolve(strict=True)
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert isinstance(context, dict)
    assert tmp_path.resolve().is_relative_to(PROBE_TEST_ROOT.resolve())

    project_root = Path(context["project_root"]).resolve(strict=True)
    expected_python = Path(context["python_executable"]).resolve(strict=True)
    expected_profile = Path(context["user_profile"]).resolve(strict=True)
    expected_local_app_data = Path(context["local_app_data"]).resolve(strict=True)
    assert project_root == PROJECT_ROOT.resolve()
    assert Path(sys.executable).resolve() == expected_python
    assert Path.cwd().resolve() == project_root
    assert _current_sid() == context["principal_sid"]
    assert Path(os.environ["USERPROFILE"]).resolve() == expected_profile
    assert Path(os.environ["LOCALAPPDATA"]).resolve() == expected_local_app_data

    temporary_task = _task_xml(context["temporary_task_name"])
    temporary_metadata = _task_metadata(context["temporary_task_name"])
    assert _text(temporary_task, "UserId") == context["principal_sid"]
    assert _text(temporary_task, "LogonType") == "InteractiveToken"
    assert _text(temporary_task, "ExecutionTimeLimit") == "PT0S"
    assert _text(temporary_task, "Command") == context["temporary_action"]["command"]
    assert (
        _text(temporary_task, "Arguments") == context["temporary_action"]["arguments"]
    )
    assert _text(temporary_task, "WorkingDirectory") == str(project_root)
    assert temporary_metadata["PrincipalSid"] == context["principal_sid"]
    assert temporary_metadata["LogonType"] == "Interactive"
    assert temporary_metadata["RunLevel"] == "Limited"
    assert temporary_metadata["ExecutionTimeLimit"] in {"PT0S", "00:00:00"}
    assert temporary_metadata["State"] == "Running"
    assert temporary_metadata["Command"] == context["temporary_action"]["command"]
    assert temporary_metadata["Arguments"] == context["temporary_action"]["arguments"]
    assert temporary_metadata["WorkingDirectory"] == str(project_root)

    daily = _task_xml("RakutenRoomInagoDaily")
    human_api = _task_xml("RakutenRoomInagoHumanApi")
    daily_metadata = _task_metadata("RakutenRoomInagoDaily")
    human_metadata = _task_metadata("RakutenRoomInagoHumanApi")
    daily_task_context = context["daily_task"]
    assert _text(daily, "UserId") == context["principal_sid"]
    assert _text(daily, "LogonType") == "InteractiveToken"
    assert _text(daily, "ExecutionTimeLimit") == "PT0S"
    assert _text(daily, "Command") == daily_task_context["command"]
    assert _text(daily, "Arguments") == daily_task_context["arguments"]
    assert _text(daily, "WorkingDirectory") == str(project_root)
    assert _child(daily, "CalendarTrigger") is not None
    assert _text(daily, "DaysInterval") == "1"
    assert daily_metadata["PrincipalSid"] == context["principal_sid"]
    assert daily_metadata["LogonType"] == "Interactive"
    assert daily_metadata["RunLevel"] == "Limited"
    assert daily_metadata["ExecutionTimeLimit"] in {"PT0S", "00:00:00"}
    assert daily_metadata["State"] == "Ready"

    assert _text(human_api, "UserId") == context["principal_sid"]
    assert _text(human_api, "LogonType") == "InteractiveToken"
    assert _text(human_api, "ExecutionTimeLimit") == "PT0S"
    assert _text(human_api, "Command") == context["human_api_task"]["command"]
    assert _text(human_api, "Arguments") == context["human_api_task"]["arguments"]
    assert _text(human_api, "WorkingDirectory") == str(project_root)
    assert _child(human_api, "LogonTrigger") is not None
    assert human_metadata["PrincipalSid"] == context["principal_sid"]
    assert human_metadata["LogonType"] == "Interactive"
    assert human_metadata["RunLevel"] == "Limited"
    assert human_metadata["ExecutionTimeLimit"] in {"PT0S", "00:00:00"}
    restart = _child(human_api, "RestartOnFailure")
    assert _text(restart, "Interval") == "PT1M"
    assert _text(restart, "Count") == "3"

    registration_source = (
        project_root / "automation" / "inago" / "register_tasks.ps1"
    ).read_text(encoding="utf-8")
    assert "$executionTimeLimitMatches" in registration_source
    assert "-not $executionTimeLimitMatches" in registration_source

    production_config = Path(daily_task_context["config_path"]).resolve(strict=True)
    production_data_root = Path(daily_task_context["data_root"]).resolve(strict=True)
    production_database = production_data_root / "state.sqlite"
    assert production_config == (PRODUCTION_ROOT / "config" / "settings.json").resolve()
    assert production_data_root == PRODUCTION_ROOT.resolve()
    assert Path(daily_task_context["command"]).resolve() == expected_python
    expected_daily_arguments = (
        "-B -m automation.inago.cli daily --environment production "
        f'--config "{production_config}" --data-root "{production_data_root}" '
        f'--user-profile "{expected_profile}" '
        f'--local-app-data "{expected_local_app_data}"'
    )
    assert daily_task_context["arguments"] == expected_daily_arguments
    assert Path(context["human_api_task"]["command"]).resolve() == expected_python
    expected_human_arguments = expected_daily_arguments.replace(
        "cli daily", "cli serve", 1
    )
    assert context["human_api_task"]["arguments"] == expected_human_arguments
    assert production_database.is_file()
    config_before = _file_signature(production_config)
    database_before = _file_signature(production_database)
    preflight = production_runtime.run_production_startup_preflight(
        production_config,
        data_root=production_data_root,
        user_profile=expected_profile,
        local_app_data=expected_local_app_data,
    )
    assert preflight["status"] == "ready"
    assert preflight["failed_checks"] == []
    assert all(value == "ready" for value in preflight["checks"].values())
    assert all(value is False for value in preflight["side_effects"].values())
    assert _file_signature(production_config) == config_before
    assert _file_signature(production_database) == database_before

    probe_root = tmp_path / "probe-root"
    config_path = tmp_path / "probe-config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "isolated_probe",
                "network_allowed": False,
                "test_only": True,
                "probe_id": "R13-N30",
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
        seed_factory=lambda: "R13-N30-SEED",
    )

    assert result["status"] == "complete"
    assert result["daily_status"] == "complete"
    assert result["committed_count"] == 15
    assert result["metrics_status"] == "observed"
    assert result["export"]["status"] == "ready"
    assert result["export"]["draft_count"] == 15
    assert result["preservation"]["status"] == "ready"
    assert result["preservation"]["end_backup"]["status"] == "ready"
    assert result["preservation"]["monthly_restore"]["status"] == "ready"
    assert result["pipeline_trace"]["status"] == "healthy"
    assert result["pipeline_trace"]["event_count"] > 0
    assert len(source.search_requests) == 3
    assert len(ai_root.copy_runner.requests) == 15
    assert len(metrics.calls) == 1

    history_html = config.data_root / "posting-history.html"
    assert history_html.is_file() and history_html.stat().st_size > 0
    trace_path = config.data_root / result["pipeline_trace"]["reference"]
    trace_events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pipeline_events = [
        event for event in trace_events if event.get("component") == "daily_pipeline"
    ]
    assert len(pipeline_events) == result["pipeline_trace"]["event_count"]
    assert len(trace_events) >= len(pipeline_events)

    probe_uri = f"{config.database_path.resolve().as_uri()}?mode=ro"
    probe_database = sqlite3.connect(probe_uri, uri=True)
    try:
        probe_database.execute("PRAGMA query_only = ON")
        assert probe_database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert probe_database.execute("PRAGMA foreign_key_check").fetchall() == []
        run_row = probe_database.execute(
            "SELECT status, committed_count FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()
        assert run_row == ("complete", 15)
        assert (
            probe_database.execute(
                "SELECT COUNT(*) FROM draft WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
            == 15
        )
        assert (
            probe_database.execute("SELECT COUNT(*) FROM publication_event").fetchone()[
                0
            ]
            == 0
        )
    finally:
        probe_database.close()

    backup_database_path = (
        Path(result["preservation"]["end_backup"]["path"]) / "state.sqlite"
    )
    backup_database = sqlite3.connect(backup_database_path)
    try:
        assert (
            backup_database.execute(
                "SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
            == 15
        )
        assert backup_database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup_database.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        backup_database.close()
    assert (config.restore_root / "2026-10" / "report.json").is_file()
    assert _file_signature(production_config) == config_before
    assert _file_signature(production_database) == database_before
