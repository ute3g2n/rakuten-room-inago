"""Fault injection on temporary stores: failures cannot become success or observed zero."""

import http.client
import json
import threading
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation import dashboard_service
from automation.adapters.node_commands import CommandError
from automation.dashboard_server import make_server
from automation.dashboard_service import record_dashboard_error, upsert_dashboard
from automation.execution_logger import ExecutionLogger
from automation.health_service import evaluate_daily_health
from automation.kai_orchestrator import KaiOrchestrator
from automation.live_services import LiveServices
from tests_kai.research_fixtures import complete_b_score
from automation.observation_service import room_snapshot
from automation.run_context import RunContext
from automation.status_service import query_status
from automation import show_status
from tests_kai.test_step06 import _fixture as dashboard_fixture
from tests_kai.test_step09 import FakeServices, TARGET, fixture_project
from tests_kai.test_step09 import GENRES, ROOT
from tests_kai.test_step04 import _run as fake_model_run


JST = timezone(timedelta(hours=9))


@pytest.mark.parametrize("stderr,expected", [
    ("AUTH_REQUIRED", "AUTH_REQUIRED"), ("CAPTCHA_REQUIRED", "CAPTCHA_REQUIRED")])
def test_affiliate_auth_failure_requires_action(tmp_path, stderr, expected):
    services = LiveServices.__new__(LiveServices)
    services.node = SimpleNamespace(affiliate_check=lambda: (_ for _ in ()).throw(
        CommandError("NONZERO_EXIT", 1, stderr=stderr)))
    with pytest.raises(Exception) as error:
        services.affiliate(TARGET, False)
    assert error.value.code == expected
    assert "password" not in str(error.value).lower()


@pytest.mark.parametrize("code", ["TIMEOUT", "INVALID_JSON", "NONZERO_EXIT"])
def test_affiliate_non_auth_failure_is_unavailable_not_zero(code):
    services = LiveServices.__new__(LiveServices)
    services.node = SimpleNamespace(affiliate_check=lambda: (_ for _ in ()).throw(CommandError(code)))
    result = services.affiliate(TARGET, False)
    assert result["status"] == "unavailable"
    assert result["sales_count"] is None and result["clicks"] is None


@pytest.mark.parametrize("code", ["TIMEOUT", "INVALID_JSON", "NONZERO_EXIT"])
def test_room_fetch_failure_is_unavailable_not_zero(tmp_path, code):
    services = LiveServices.__new__(LiveServices)
    services.node = SimpleNamespace(room_likes=lambda *_: (_ for _ in ()).throw(CommandError(code)))
    result = services.room(TARGET, [], False, tmp_path)
    assert result["status"] == "unavailable" and result["observations"] == []
    assert room_snapshot({"postsById": {}}, {"status": "observed"}, TARGET, [])["status"] == "observed"


def test_room_structure_change_is_unavailable(tmp_path):
    services = LiveServices.__new__(LiveServices)
    services.node = SimpleNamespace(room_likes=lambda *_: {"postsById": {
        "broken": {"status": "observed", "likes": 1, "itemUrl": "https://item.rakuten.co.jp/shop/item/"}}})
    result = services.room(TARGET, [], False, tmp_path)
    assert result["status"] == "unavailable" and result["observations"] == []


def test_dashboard_write_failure_leaves_no_partial_plan(tmp_path, monkeypatch):
    root = tmp_path / "dashboard"
    plan = dashboard_fixture(root)
    def fail_html(path, value):
        if Path(path).name == "posting-dashboard.html":
            raise OSError("simulated disk full")
        from automation.atomic_io import atomic_write_text
        return atomic_write_text(path, value)
    monkeypatch.setattr("automation.dashboard_service.atomic_write_text", fail_html)
    with pytest.raises(OSError, match="disk full"):
        upsert_dashboard(root, plan)
    assert not (root / "daily-plans" / f"{TARGET}.json").exists()
    assert not (root / "posting-dashboard.html").exists()


def test_dashboard_disk_failure_sets_failed_exit_and_write_error(tmp_path, monkeypatch):
    project = fixture_project(tmp_path, 15)
    data = project / "rakuten-room-kai"
    original = dashboard_service.atomic_write_text
    def fail_html(path, value):
        if Path(path).name == "posting-dashboard.html":
            raise OSError("simulated disk full")
        return original(path, value)
    monkeypatch.setattr("automation.dashboard_service.atomic_write_text", fail_html)
    result = KaiOrchestrator(project, FakeServices()).run("daily", date.fromisoformat(TARGET))
    assert result.manifest["status"] == "failed" and result.exit_code == 1
    assert result.manifest["errors"][-1]["code"] == "OUTPUT_WRITE_FAILED"
    assert not (data / "daily-plans" / f"{TARGET}.json").exists()


def test_server_rejects_traversal_and_oversize_json(tmp_path):
    root = tmp_path / "dashboard"
    upsert_dashboard(root, dashboard_fixture(root))
    server = make_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/../products.json", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/api/session", headers={"Host": f"127.0.0.1:{port}"})
        token = json.loads(connection.getresponse().read())["csrf_token"]
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("POST", "/api/post-status", body="x" * 4097,
            headers={"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
                     "Content-Type": "application/json", "X-CSRF-Token": token})
        assert connection.getresponse().status == 400
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_missed_stale_and_failed_only_status(tmp_path, monkeypatch, capsys):
    project = fixture_project(tmp_path, 0)
    runtime = project / "rakuten-room-kai/orchestrator"
    late = datetime(2026, 9, 25, 14, 1, tzinfo=JST)
    missed = evaluate_daily_health(runtime, date.fromisoformat(TARGET), late)
    assert missed["status"] == "missed" and missed["errors"][0]["code"] == "MISSED_SCHEDULE"
    assert json.loads((runtime / "status/health.json").read_text(encoding="utf-8"))["status"] == "missed"
    run = RunContext.create(runtime, "daily", date.fromisoformat(TARGET), "scheduler")
    run.manifest["last_heartbeat_at"] = datetime(2026, 9, 25, 13, 0, tzinfo=JST).isoformat()
    run.logger.write_manifest(run.manifest)
    stale = evaluate_daily_health(runtime, date.fromisoformat(TARGET), late)
    assert stale["status"] == "stale_heartbeat" and stale["errors"][0]["code"] == "STALE_HEARTBEAT"
    run.manifest["errors"].append({"code": "AUTH_REQUIRED", "detail": "re-login"})
    run.logger.write_manifest(run.manifest)
    run.finish("action_required")
    rows = query_status(runtime, failed_only=True, days=365)
    assert len(rows) == 1 and rows[0]["run_id"] == run.run_id
    assert rows[0]["actions"] == ["本人がブラウザで楽天アフィリエイトへ再ログインする"]
    monkeypatch.setattr(show_status, "query_status", lambda _root, **kwargs: query_status(runtime, **kwargs))
    assert show_status.main(["--failed-only", "--days", "365", "--json"]) == 0
    displayed = json.loads(capsys.readouterr().out)
    assert len(displayed) == 1 and displayed[0]["run_id"] == run.run_id


def test_forced_termination_resume_creates_one_plan(tmp_path):
    project = fixture_project(tmp_path, 15)
    runtime = project / "rakuten-room-kai/orchestrator"
    interrupted = RunContext.create(runtime, "daily", date.fromisoformat(TARGET), "scheduler")
    interrupted.step_started("load_state")
    interrupted.manifest["last_heartbeat_at"] = datetime(2026, 9, 25, 13, 0, tzinfo=JST).isoformat()
    interrupted.logger.write_manifest(interrupted.manifest)
    health = evaluate_daily_health(runtime, date.fromisoformat(TARGET),
                                   datetime(2026, 9, 25, 14, 1, tzinfo=JST), False)
    assert health["status"] == "stale_heartbeat"
    resumed = KaiOrchestrator(project, FakeServices()).run("daily", date.fromisoformat(TARGET),
                                                           resume_manifest=interrupted.manifest_path)
    assert resumed.manifest["status"] == "success" and resumed.manifest["attempt"] == 2
    assert resumed.manifest["parent_run_id"] == interrupted.run_id
    plan = json.loads((project / "rakuten-room-kai/daily-plans" / f"{TARGET}.json").read_text(encoding="utf-8"))
    assert len(plan["posts"]) == 15 and len({post["product_id"] for post in plan["posts"]}) == 15


def test_model_failure_does_not_commit_plan_or_mark_success(tmp_path):
    project = fixture_project(tmp_path, 15)
    class FailPlan(FakeServices):
        def model(self, context, name, payload):
            if name == "daily_plan":
                from automation.kai_orchestrator import FlowError
                raise FlowError("CLI_TIMEOUT", "injected timeout")
            return super().model(context, name, payload)
    context = KaiOrchestrator(project, FailPlan()).run("daily", date.fromisoformat(TARGET))
    assert context.manifest["status"] == "failed" and context.exit_code == 1
    assert context.manifest["errors"][-1]["code"] == "CLI_TIMEOUT"
    assert not (project / "rakuten-room-kai/daily-plans" / f"{TARGET}.json").exists()


def test_log_write_failure_propagates_without_fake_success(tmp_path, monkeypatch):
    logger = ExecutionLogger(tmp_path, TARGET, "injected-run")
    def disk_full(_fd):
        raise OSError("simulated disk full")
    monkeypatch.setattr("automation.execution_logger.os.fsync", disk_full)
    with pytest.raises(OSError, match="disk full"):
        logger.append_output("stderr", "safe diagnostic")
    assert not (tmp_path / "status/latest.json").exists()


def test_dashboard_error_artifact_redacts_dummy_credential(tmp_path):
    record_dashboard_error(tmp_path, "accessKey=dummy-secret")
    serialized = (tmp_path / "dashboard-error.json").read_text(encoding="utf-8")
    assert "dummy-secret" not in serialized and "[REDACTED]" in serialized


def test_escalation_failure_has_bounded_attempts(tmp_path):
    context, result, _ = fake_model_run(tmp_path, mode="escalate_fail", job="product_verify",
                                        reason="conflicting_product_information")
    assert not result.success and result.error_code == "CLI_NONZERO_EXIT"
    assert [item["model"] for item in result.attempts] == ["gpt-6-sol", "gpt-6-sol", "gpt-6-astra"]
    assert context.manifest["model_attempts"][-1]["status"] == "CLI_NONZERO_EXIT"


def test_two_quality_rejections_never_commit_unverified_stock(tmp_path, monkeypatch):
    project = fixture_project(tmp_path, 0)
    data = project / "rakuten-room-kai"
    services = LiveServices.__new__(LiveServices)
    services.project_root = ROOT
    url = "https://item.rakuten.co.jp/shop/rejected/"
    services.node = SimpleNamespace(node="fake-node", research_collect=lambda *a, **kw: {
        "items": [{"itemUrl": url, "itemName": "未採用商品", "itemCode": "shop:rejected",
                   "sourceQueries": [{"genreName": "家事ラク用品"}]}], "summary": {}})
    base = FakeServices()
    services.affiliate = base.affiliate
    services.room = base.room
    calls = []

    def model(context, name, payload):
        calls.append(name)
        common = {"run_id": context.run_id, "target_date": TARGET}
        if name in {"performance", "daily_plan"}:
            return base.model(context, name, payload)
        if name == "research_plan":
            return {"target_date": TARGET, "genre_priorities": [],
                    "search_queries": [{"genre": g, "query": g} for g in GENRES],
                    "winning_conditions": [], "deprioritized_conditions": [], "research_requests": []}
        if name == "triage":
            return {**common, "selected": [{"product_id": "P01", "reason": "確認"}],
                    "excluded": [], "uncertain": []}
        if name == "product_verify":
            return {**common, "verified": [{"product_id": "P01", "url": url,
                    "checked_at": "2026-09-25T13:00:00+09:00", "evidence": ["page"]}],
                    "rejected": [], "unavailable": []}
        if name == "copywriter":
            return {**common, "drafts": [{"product_id": "P01", "url": url, "body": "未採用商品の文面",
                    "status": "accepted", "checked_at": TARGET}], "unsupported_claims": []}
        if name == "quality_gate":
            return {**common, "accepted": [], "rejected": [],
                    "required_edits": [{"code": "REWRITE", "detail": "根拠不足"}]}
        raise AssertionError(name)

    services.model = model
    monkeypatch.setattr("automation.live_services.run_json_command", lambda *a, **kw: {
        "schema_version": 1, "observed_at": "2026-09-25T13:00:00+09:00", "requested": 1,
        "observed": 1, "unavailable": 0,
        "snapshots": [{"candidate_id": "P01", "status": "observed", "availability": "in_stock",
                       "name": "未採用商品", "price_yen": 1200, "observed_at": "2026-09-25T13:00:00+09:00"}],
        "heat_scores": [complete_b_score("P01", url)]})
    result = KaiOrchestrator(project, services).run("daily", date.fromisoformat(TARGET))
    assert result.manifest["status"] == "partial", result.manifest["errors"]
    assert calls.count("quality_gate") == 2 and calls.count("copywriter") == 2
    assert json.loads((data / "products.json").read_text(encoding="utf-8"))["products"] == []
    assert json.loads((data / "drafts.json").read_text(encoding="utf-8"))["drafts"] == []
    research = json.loads((result.logger.run_dir / "outputs/research-result.json").read_text(encoding="utf-8"))
    assert research["status"] == "partial" and research["unresolved_quality_edits"]
    assert research["target_reached"] is False and research["stop_reason"] == "triaged_candidates_exhausted"
    assert json.loads((result.logger.run_dir / "outputs/daily-report.json").read_text(encoding="utf-8"))["planned_count"] == 0
