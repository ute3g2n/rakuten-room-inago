"""Release candidate dry-run adapters keep real reads inside a run artifact directory."""

from pathlib import Path
from types import SimpleNamespace

from automation.live_services import LiveServices
from automation.kai_orchestrator import KaiOrchestrator
from automation.codex_cli_runner import CodexCliRunner
from automation.adapters.node_commands import NodeAdapters
from datetime import date
from tests_kai.test_step09 import FakeServices, TARGET, fixture_project


def test_live_dry_run_observation_paths_are_run_local(tmp_path):
    calls = []
    prior = "2026-09-24"
    affiliate = {"byDate": {prior: {"status": "observed", "observedAt": "2026-09-25T13:00:00+09:00",
                 "clicks": 0, "ordersUnconfirmed": 0, "salesJpyUnconfirmed": 0,
                 "commissionJpyUnconfirmed": 0}},
                 "orderDetailsByMonth": {"2026-09": {"status": "observed", "orders": []}}}
    def affiliate_check(*, output_dir):
        calls.append(("affiliate", Path(output_dir)))
        return affiliate
    def room_likes(output_dir):
        calls.append(("room", Path(output_dir)))
        return {"postsById": {}}
    services = LiveServices.__new__(LiveServices)
    services.node = SimpleNamespace(affiliate_check=affiliate_check, room_likes=room_likes)
    run_dir = tmp_path / "runs" / TARGET / "example-dry-run"
    first = services.affiliate(TARGET, True, run_dir)
    second = services.room(TARGET, [], True, run_dir)
    assert first["status"] == "observed" and first["sales_count"] == 0
    assert second["status"] == "observed" and second["observations"] == []
    assert calls == [("affiliate", run_dir / "affiliate-report"),
                     ("room", run_dir / "room-metrics")]


def test_performance_unavailable_observation_is_partial(tmp_path):
    project = fixture_project(tmp_path, 0)

    class MissingRoom(FakeServices):
        def room(self, target, products, dry_run, run_dir):
            return {"target_date": target, "status": "unavailable", "observations": [],
                    "errors": [{"code": "ROOM_FETCH_FAILED", "detail": "test fetch failed"}]}

    context = KaiOrchestrator(project, MissingRoom()).run("performance", date.fromisoformat(TARGET), dry_run=True)
    assert context.manifest["status"] == "partial" and context.exit_code == 2


def test_scheduler_without_session_path_finds_desktop_codex_and_system_node(tmp_path, monkeypatch):
    desktop = tmp_path / "OpenAI" / "Codex" / "bin" / "build" / "codex.exe"
    desktop.parent.mkdir(parents=True)
    desktop.touch()
    node = tmp_path / "nodejs" / "node.exe"
    node.parent.mkdir(parents=True)
    node.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr("automation.codex_cli_runner.shutil.which", lambda _: None)
    monkeypatch.setattr("automation.adapters.node_commands.shutil.which", lambda _: None)
    assert CodexCliRunner(tmp_path).executable == desktop
    assert NodeAdapters(tmp_path).node == str(node)
