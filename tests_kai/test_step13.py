import json
from datetime import date

import pytest

from automation.dashboard_server import ConflictError, _update_status
from automation.kai_orchestrator import KaiOrchestrator
from tests_kai.test_step09 import FakeServices, TARGET, fixture_project


def test_stale_posting_revision_returns_conflict_without_overwrite(tmp_path):
    project = fixture_project(tmp_path, 15)
    data = project / "rakuten-room-kai"
    first = KaiOrchestrator(project, FakeServices()).run("daily", date.fromisoformat(TARGET))
    assert first.manifest["status"] == "success"
    posted = _update_status(data, {"product_id": "P01", "scheduled_date": TARGET,
                                   "posted": True, "expected_revision": 0})
    assert posted["posted"] and len(posted["correction_history"]) == 1
    with pytest.raises(ConflictError):
        _update_status(data, {"product_id": "P01", "scheduled_date": TARGET,
                              "posted": False, "expected_revision": 0,
                              "confirm_correction": True, "reason": "stale browser"})
    stored = json.loads((data / "post-status.json").read_text(encoding="utf-8"))["items"]["P01"]
    assert stored["posted"] and len(stored["correction_history"]) == 1
    corrected = _update_status(data, {"product_id": "P01", "scheduled_date": TARGET,
                                      "posted": False, "expected_revision": 1,
                                      "confirm_correction": True, "reason": "誤操作"})
    assert corrected["ever_marked_posted"] and not corrected["posted"]


def test_invalid_daily_plan_shows_error_and_writes_failed_daily_result(tmp_path):
    project = fixture_project(tmp_path, 15)
    data = project / "rakuten-room-kai"
    engine = KaiOrchestrator(project, FakeServices())
    first = engine.run("daily", date.fromisoformat(TARGET))
    assert first.manifest["status"] == "success"
    original_html = (data / "posting-dashboard.html").read_bytes()
    drafts_path = data / "drafts.json"
    drafts = json.loads(drafts_path.read_text(encoding="utf-8"))
    drafts["drafts"] = [item for item in drafts["drafts"] if item["product_id"] != "P01"]
    drafts_path.write_text(json.dumps(drafts, ensure_ascii=False), encoding="utf-8")
    failed = engine.run("daily", date.fromisoformat(TARGET))
    assert failed.manifest["status"] == "failed"
    assert failed.manifest["errors"][0]["code"] == "DATA_MISMATCH"
    report = json.loads((failed.logger.run_dir / "outputs/daily-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["planned_count"] == 0
    assert report["shortfall"][0]["code"] == "DATA_MISMATCH"
    error = json.loads((data / "dashboard-error.json").read_text(encoding="utf-8"))
    assert error["status"] == "error" and error["errors"]
    assert (data / "posting-dashboard.html").read_bytes() == original_html


def test_duplicate_model_plan_reports_error_without_creating_dashboard(tmp_path):
    project = fixture_project(tmp_path, 15)
    data = project / "rakuten-room-kai"

    class DuplicatePlan(FakeServices):
        def model(self, context, name, payload):
            value = super().model(context, name, payload)
            if name == "daily_plan":
                value["posts"][1] = dict(value["posts"][0])
            return value

    failed = KaiOrchestrator(project, DuplicatePlan()).run("daily", date.fromisoformat(TARGET))
    assert failed.manifest["status"] == "failed"
    assert not (data / "posting-dashboard.html").exists()
    report = json.loads((failed.logger.run_dir / "outputs/daily-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["shortfall"][0]["code"] == "DATA_MISMATCH"
    error = json.loads((data / "dashboard-error.json").read_text(encoding="utf-8"))
    assert error["status"] == "error"
