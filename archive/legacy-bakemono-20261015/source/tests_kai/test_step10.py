import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from automation import cli
from automation.health_service import evaluate_daily_health
from automation.kai_orchestrator import KaiOrchestrator
from automation.run_context import RunContext
from automation.status_service import query_status


ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))


def workspace(tmp_path):
    data = tmp_path / "rakuten-room-kai"
    data.mkdir()
    shutil.copytree(ROOT / "rakuten-room-kai/orchestrator", data / "orchestrator",
                    ignore=shutil.ignore_patterns("runs", "history", "status", "locks", "__pycache__"))
    for name in ("products.json", "drafts.json", "post-status.json", "posts-log.jsonl", "migration-report.json"):
        shutil.copy2(ROOT / "rakuten-room-kai" / name, data / name)
    return tmp_path


def test_cli_common_source_json_and_invalid_combination(tmp_path, monkeypatch, capsys):
    project = workspace(tmp_path)
    monkeypatch.setattr(cli, "KaiOrchestrator", lambda _path: KaiOrchestrator(project, services=object()))
    result = cli.main_for_flow("setup", ["--date", "2026-09-25", "--source", "chat", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert result == 3 and data["source"] == "chat" and data["status"] == "action_required"
    manifest = json.loads(Path(data["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source"] == "chat"
    invalid = cli.main_for_flow("recovery", ["--date", "2026-09-25", "--source", "scheduler", "--json"])
    bad = json.loads(capsys.readouterr().out)
    assert invalid == 1 and bad["errors"][0]["code"] == "INPUT_INVALID"
    assert Path(bad["manifest_path"]).is_file()


def test_status_filters_and_no_model(tmp_path):
    project = workspace(tmp_path)
    runtime = project / "rakuten-room-kai/orchestrator"
    failed = RunContext.create(runtime, "daily", date(2026, 9, 25), "scheduler")
    failed.manifest["errors"].append({"code": "AUTH_REQUIRED", "detail": "login"})
    failed.logger.write_manifest(failed.manifest)
    failed.finish("action_required")
    ready = RunContext.create(runtime, "setup", date(2026, 9, 25), "chat")
    ready.finish("success")
    assert query_status(runtime, run_id=failed.run_id)[0]["actions"] == ["本人がブラウザで楽天アフィリエイトへ再ログインする"]
    assert len(query_status(runtime, flow="daily", target_date="2026-09-25")) == 1
    assert len(query_status(runtime, failed_only=True, days=365)) == 1
    assert query_status(runtime, latest=True)[0]["run_id"] in {ready.run_id, failed.run_id}
    with pytest.raises(ValueError):
        query_status(runtime, run_id="../secret")


def test_health_start_boundary_missed_stale_and_action_required(tmp_path):
    project = workspace(tmp_path)
    runtime = project / "rakuten-room-kai/orchestrator"
    at = lambda day, hour, minute: datetime(2026, 9, day, hour, minute, tzinfo=JST)
    assert evaluate_daily_health(runtime, date(2026, 9, 24), at(24, 15, 0), False)["status"] == "scheduled"
    assert evaluate_daily_health(runtime, date(2026, 9, 25), at(25, 13, 30), False)["status"] == "scheduled"
    assert evaluate_daily_health(runtime, date(2026, 9, 25), at(25, 14, 1), False)["status"] == "missed"
    run = RunContext.create(runtime, "daily", date(2026, 9, 25), "scheduler")
    run.manifest["last_heartbeat_at"] = at(25, 13, 30).isoformat()
    run.logger.write_manifest(run.manifest)
    assert evaluate_daily_health(runtime, date(2026, 9, 25), at(25, 14, 1), False)["status"] == "stale_heartbeat"
    run.manifest["last_heartbeat_at"] = at(25, 14, 0).isoformat()
    run.logger.write_manifest(run.manifest)
    assert evaluate_daily_health(runtime, date(2026, 9, 25), at(25, 14, 1), False)["status"] == "healthy"
    run.finish("partial")
    assert evaluate_daily_health(runtime, date(2026, 9, 25), at(25, 14, 2), True)["status"] == "action_required"
    assert (runtime / "status/health.json").is_file()
    assert (runtime / "history/health-2026-09.jsonl").is_file()


def test_exported_task_xml_has_safe_schedule_and_no_registration(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell
    query = [shell, "-NoProfile", "-Command",
             "@(Get-ScheduledTask -TaskName 'RakutenRoomBakemonoKai*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName) | ConvertTo-Json -Compress"]
    before = subprocess.run(query, capture_output=True, text=True, check=True).stdout.strip()
    output = tmp_path / "export"
    result = subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "automation/register_kai_daily_task.ps1"),
                             "-ExportOnly", "-OutputDir", str(output)], capture_output=True, text=True, check=True)
    assert "InitialRunPolicy=" in result.stdout
    expected = {"RakutenRoomBakemonoKaiDaily": "2026-09-25T13:00:00",
                "RakutenRoomBakemonoKaiHealthCheck": "2026-09-25T14:00:00"}
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    for name, start in expected.items():
        tree = ET.parse(output / f"{name}.xml")
        find = lambda path: tree.findtext(path, namespaces=ns)
        assert find(".//t:StartBoundary") == start
        assert find(".//t:WakeToRun") == "true"
        assert find(".//t:StartWhenAvailable") == "true"
        assert find(".//t:MultipleInstancesPolicy") == "IgnoreNew"
        assert find(".//t:WorkingDirectory") == str(ROOT)
        assert find(".//t:Command").endswith(".venv-kai\\Scripts\\python.exe")
    after = subprocess.run(query, capture_output=True, text=True, check=True).stdout.strip()
    assert before == after
