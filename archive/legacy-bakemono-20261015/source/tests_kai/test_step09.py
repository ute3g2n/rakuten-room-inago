import json
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from automation.kai_orchestrator import KaiOrchestrator
from automation.kai_orchestrator import FlowError
from automation.run_lock import RunLock
from automation.live_services import LiveServices
from tests_kai.research_fixtures import complete_b_score


ROOT = Path(__file__).resolve().parents[1]
TARGET = "2026-09-25"
GENRES = json.loads((ROOT / "rakuten-room-kai/orchestrator/automation-config.json").read_text(encoding="utf-8"))["genre_targets"]


def fixture_project(tmp_path, count=0):
    data = tmp_path / "rakuten-room-kai"
    data.mkdir(parents=True)
    shutil.copytree(ROOT / "rakuten-room-kai/orchestrator", data / "orchestrator", ignore=shutil.ignore_patterns("runs", "history", "status", "locks", "__pycache__"))
    products, drafts = [], []
    genres = [g for g, quantity in GENRES.items() for _ in range(quantity)]
    for index in range(count):
        pid = f"P{index+1:02}"
        url = f"https://item.rakuten.co.jp/shop/item-{index+1}/"
        products.append({"id": pid, "name": f"商品{index+1}", "url": url, "normalized_url": url,
                         "genre": genres[index], "status": "eligible", "item_code": f"shop/item-{index+1}",
                         "model_candidate": None, "checked_at": TARGET, "posted_at": None})
        drafts.append({"product_id": pid, "url": url, "body": f"商品{index+1}の紹介文", "status": "accepted", "checked_at": TARGET})
    (data / "products.json").write_text(json.dumps({"version": 1, "products": products, "next_id": count+1}, ensure_ascii=False), encoding="utf-8")
    (data / "drafts.json").write_text(json.dumps({"version": 1, "drafts": drafts}, ensure_ascii=False), encoding="utf-8")
    (data / "post-status.json").write_text(json.dumps({"version": 1, "items": {}}, ensure_ascii=False), encoding="utf-8")
    (data / "posts-log.jsonl").write_text("", encoding="utf-8")
    (data / "dashboard-days.json").write_text(json.dumps({"version": 1, "days": []}), encoding="utf-8")
    return tmp_path


class FakeServices:
    def __init__(self):
        self.calls = []
        self.performance_payload = None
        self.daily_payload = None
        self.research_calls = 0

    def affiliate(self, target, dry_run, run_dir=None):
        self.calls.append("affiliate")
        return {"target_date": "2026-09-24", "status": "observed", "observed_at": "2026-09-25T13:00:00+09:00",
                "clicks": 0, "sales_count": 0, "sales_yen": 0, "unconfirmed_commission_yen": 0,
                "orders": [], "errors": []}

    def room(self, target, products, dry_run, run_dir):
        self.calls.append("room")
        return {"target_date": target, "status": "observed", "observations": [], "errors": []}

    def model(self, context, name, payload):
        self.calls.append(name)
        if name == "performance":
            self.performance_payload = payload
            return {"analysis_date": TARGET, "post_decisions": [], "cohort_insights": [],
                    "winning_conditions": [], "single_change_experiments": [],
                    "deprioritized_conditions": [], "research_requests": [],
                    "daily_plan_constraints": [], "held_items": [], "missing_or_unattributed_data": []}
        if name == "daily_plan":
            self.daily_payload = payload
            posts = [{"product_id": item["id"], "name": item["name"], "url": item["url"],
                      "genre": item["genre"], "body": item["body"], "scheduled_at":
                      f"{TARGET}T{8+index//2:02}:00:00+09:00", "role": "紹介"}
                     for index, item in enumerate(payload["eligible_products"])]
            return {"target_date": TARGET, "posts": posts,
                    "shortfall": [] if len(posts) == 15 else [{"code": "INVENTORY_SHORT", "detail": "確認済み在庫不足"}],
                    "applied_learning": []}
        if name == "recovery":
            return {"failed_run_id": payload["failed_run_id"], "failed_step": payload["failed_step"],
                    "cause": "input failed", "retryable": False, "proposed_action": "入力確認",
                    "requires_user_action": True}
        raise AssertionError(name)

    def research(self, context, root, inventory, performance, dry_run, target_count=None, target_by_genre=None):
        self.calls.append("research")
        self.research_calls += 1
        assert "winning_conditions" in performance and "research_requests" in performance
        for name in ("research-plan", "api-candidates", "triage-output", "heat-scores", "product-verification", "copy-output", "quality-gate"):
            path = context.logger.run_dir / "outputs" / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == "heat-scores":
                output = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                          "scoring_method": "legacy-heat-6-factor-v1", "scores": []}
            else:
                output = {"test": name}
            path.write_text(json.dumps(output), encoding="utf-8")
        return {"status": "success", "accepted_product_ids": [], "candidate_count": 0}


def test_daily_sufficient_inventory_skips_research_and_rerun(tmp_path):
    project = fixture_project(tmp_path, 15)
    fake = FakeServices()
    engine = KaiOrchestrator(project, fake)
    first = engine.run("daily", date.fromisoformat(TARGET), dry_run=False)
    assert first.manifest["status"] == "success", first.manifest["errors"]
    assert fake.research_calls == 0
    assert fake.calls.index("performance") < fake.calls.index("daily_plan")
    assert {"winning_conditions", "single_change_experiments", "daily_plan_constraints"} <= fake.daily_payload.keys()
    assert (first.logger.run_dir / "inputs/performance-input.json").is_file()
    assert [s["id"] for s in first.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"]["daily"]["stages"]]
    plan = json.loads((project / "rakuten-room-kai/daily-plans" / f"{TARGET}.json").read_text(encoding="utf-8"))
    assert len(plan["posts"]) == 15
    second = engine.run("daily", date.fromisoformat(TARGET), dry_run=False)
    assert second.manifest["status"] == "success"
    assert len(json.loads((project / "rakuten-room-kai/daily-plans" / f"{TARGET}.json").read_text(encoding="utf-8"))["posts"]) == 15


def test_daily_shortage_partial_and_dry_run_isolated(tmp_path):
    project = fixture_project(tmp_path, 0)
    fake = FakeServices()
    engine = KaiOrchestrator(project, fake)
    context = engine.run("daily", date.fromisoformat(TARGET), dry_run=True)
    assert context.manifest["status"] == "partial", context.manifest["errors"]
    assert fake.research_calls == 1
    assert not (project / "rakuten-room-kai/daily-plans").exists()
    assert (context.logger.run_dir / "sandbox-data/posting-dashboard.html").is_file()
    report = json.loads((context.logger.run_dir / "outputs/daily-report.json").read_text(encoding="utf-8"))
    assert report["planned_count"] == 0 and report["shortfall"]


def test_prestart_production_rejected_and_setup_status_do_not_call_model(tmp_path):
    project = fixture_project(tmp_path, 0)
    fake = FakeServices()
    engine = KaiOrchestrator(project, fake)
    early = engine.run("daily", date(2026, 9, 24), dry_run=False)
    assert early.manifest["status"] == "failed"
    assert early.manifest["errors"][0]["code"] == "INPUT_INVALID"
    assert not (project / "rakuten-room-kai/daily-plans").exists()
    setup = engine.run("setup", date.fromisoformat(TARGET))
    status = engine.run("status", date.fromisoformat(TARGET))
    assert setup.manifest["status"] == "action_required"
    assert status.manifest["status"] == "success"
    assert [s["id"] for s in setup.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"]["setup"]["stages"]]
    assert [s["id"] for s in status.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"]["status"]["stages"]]
    assert not fake.calls


def test_research_performance_config_and_recovery_flows(tmp_path):
    project = fixture_project(tmp_path, 0)
    fake = FakeServices()
    engine = KaiOrchestrator(project, fake)
    research = engine.run("research", date.fromisoformat(TARGET), dry_run=True)
    assert research.manifest["status"] == "success", research.manifest["errors"]
    assert [s["id"] for s in research.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"]["research"]["stages"]]
    perf = engine.run("performance", date.fromisoformat(TARGET), dry_run=True)
    assert perf.manifest["status"] == "success", perf.manifest["errors"]
    assert [s["id"] for s in perf.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"]["performance"]["stages"]]
    config_path = project / "rakuten-room-kai/orchestrator/automation-config.json"
    before = config_path.read_bytes()
    config = engine.run("config-change", date.fromisoformat(TARGET), dry_run=True,
                        proposal={"dashboard_port": 8766})
    assert config.manifest["status"] == "success"
    assert [s["id"] for s in config.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"]["config-change"]["stages"]]
    assert config_path.read_bytes() == before
    invalid = engine.run("config-change", date.fromisoformat(TARGET), dry_run=True,
                         proposal={"genre_targets": {"家事ラク用品": 15}})
    assert invalid.manifest["status"] == "failed"
    early = engine.run("daily", date(2026, 9, 24), dry_run=False)
    recovery = engine.run("recovery", date.fromisoformat(TARGET), failed_run_id=early.run_id,
                          failed_step="load_state")
    assert recovery.manifest["status"] == "action_required", recovery.manifest["errors"]
    assert [s["id"] for s in recovery.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"]["recovery"]["stages"]]


def test_auth_schema_failure_resume_and_lock(tmp_path):
    project = fixture_project(tmp_path, 15)

    class FailingFake(FakeServices):
        def __init__(self):
            super().__init__()
            self.failure = "auth"

        def model(self, context, name, payload):
            if name == "daily_plan" and self.failure == "auth":
                self.calls.append("daily_plan_auth")
                self.failure = "none"
                raise FlowError("AUTH_REQUIRED", "login needed")
            if name == "daily_plan" and self.failure == "schema":
                self.calls.append("daily_plan_schema")
                return {}
            return super().model(context, name, payload)

    fake = FailingFake()
    engine = KaiOrchestrator(project, fake)
    first = engine.run("daily", date.fromisoformat(TARGET))
    assert first.manifest["status"] == "action_required"
    assert first.manifest["errors"][0]["code"] == "AUTH_REQUIRED"
    observed_calls = fake.calls.count("affiliate") + fake.calls.count("room")
    performance_calls = fake.calls.count("performance")
    second = engine.run("daily", date.fromisoformat(TARGET), resume_manifest=first.manifest_path)
    assert second.manifest["status"] == "success", second.manifest["errors"]
    assert fake.calls.count("affiliate") + fake.calls.count("room") == observed_calls
    assert fake.calls.count("performance") == performance_calls
    fake.failure = "schema"
    third = engine.run("daily", date.fromisoformat(TARGET))
    # The existing full same-day plan is reused, so a schema failure must be tested on a fresh day fixture.
    assert third.manifest["status"] == "success"

    other = fixture_project(tmp_path / "other", 15)
    schema_fake = FailingFake()
    schema_fake.failure = "schema"
    schema_run = KaiOrchestrator(other, schema_fake).run("daily", date.fromisoformat(TARGET))
    assert schema_run.manifest["status"] == "failed"
    assert schema_run.manifest["errors"][0]["code"] == "SCHEMA_INVALID"

    lock_path = project / "rakuten-room-kai/orchestrator/locks/process.lock"
    with RunLock(lock_path, "held-by-test"):
        locked = engine.run("status", date.fromisoformat(TARGET))
    assert locked.manifest["status"] == "failed"
    assert locked.manifest["errors"][0]["code"] == "LOCKED"


def test_research_pipeline_verifies_then_commits_and_rechecks_quality(tmp_path, monkeypatch):
    project = fixture_project(tmp_path, 0)
    data = project / "rakuten-room-kai"
    services = LiveServices.__new__(LiveServices)
    services.project_root = ROOT
    raw = {"items": [{"itemUrl": "https://item.rakuten.co.jp/shop/new-item/?tracking=1",
                      "itemName": "家事用テスト商品", "itemCode": "shop:new-item",
                      "sourceQueries": [{"genreName": "家事ラク用品"}]}], "summary": {},
           "queries": [{"genreName": "家事ラク用品", "keyword": f"調査語{i}"} for i in range(5)]}
    services.node = SimpleNamespace(node="fake-node", research_collect=lambda *a, **kw: raw)
    calls = []
    quality_count = 0
    def model(context, name, payload):
        nonlocal quality_count
        calls.append(name)
        common = {"run_id": "test", "target_date": TARGET}
        url = raw["items"][0]["itemUrl"]
        if name == "research_plan":
            return {"target_date": TARGET, "genre_priorities": [], "search_queries": [],
                    "winning_conditions": [], "deprioritized_conditions": [], "research_requests": []}
        if name == "triage":
            return {**common, "selected": [{"product_id": "P01", "reason": "確認対象"}],
                    "excluded": [], "uncertain": []}
        if name == "product_verify":
            assert payload["selected_candidates"][0]["search_queries"] == [f"調査語{i}" for i in range(5)]
            assert payload["heat_scores"][0]["status"] == "complete"
            assert payload["heat_scores"][0]["grade"] == "B"
            return {**common, "verified": [{"product_id": "P01", "url": url,
                    "checked_at": "2026-09-25T13:00:00+09:00", "evidence": ["page"]}],
                    "rejected": [], "unavailable": []}
        if name == "copywriter":
            return {**common, "drafts": [{"product_id": "P01", "url": url,
                    "body": "確認済みの紹介文", "status": "accepted", "checked_at": TARGET}],
                    "unsupported_claims": []}
        if name == "quality_gate":
            quality_count += 1
            return {**common, "accepted": ["P01"] if quality_count == 2 else [],
                    "rejected": [], "required_edits": [] if quality_count == 2 else
                    [{"code": "REWRITE", "detail": "表現を確認"}]}
        raise AssertionError(name)
    services.model = model
    def fake_page_check(command, output_path, project_root, timeout_seconds, accepted_exit_codes):
        candidate_input = json.loads(Path(command[command.index("--input") + 1]).read_text(encoding="utf-8"))
        page_candidates = candidate_input["candidates"]
        return {"schema_version": 1, "observed_at": "2026-09-25T13:00:00+09:00",
                "requested": len(page_candidates), "observed": len(page_candidates), "unavailable": 0,
                "snapshots": [{"candidate_id": item["id"], "status": "observed", "availability": "in_stock",
                               "name": "家事用テスト商品", "price_yen": 1200, "observed_at": "2026-09-25T13:00:00+09:00"}
                              for item in page_candidates],
                "heat_scores": [complete_b_score(item["id"], item["url"]) for item in page_candidates]}
    monkeypatch.setattr("automation.live_services.run_json_command", fake_page_check)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = SimpleNamespace(run_id="test", logger=SimpleNamespace(run_dir=run_dir), manifest={"target_date": TARGET})
    inventory = {"target_date": TARGET, "eligible_count": 0, "draft_count": 0, "posted_count": 0,
                 "shortfall_count": 1, "inventory_shortfall_count": 120,
                 "genre_counts": {genre: 0 for genre in GENRES}, "needs_review_count": 0,
                 "eligible_product_ids": []}
    result = services.research(context, data, inventory, {"winning_conditions": [],
                               "deprioritized_conditions": [], "research_requests": [],
                               "single_change_experiments": []}, False, target_count=1,
                               target_by_genre={"時短・料理・調理・家電": 0, "家事ラク用品": 1,
                                                "住まい・収納・インテリア": 0, "子育て・キッズ用品": 0,
                                                "親の美容・セルフケア": 0})
    assert result["accepted_product_ids"] == ["P01"]
    assert calls == ["research_plan", "triage", "product_verify", "copywriter", "quality_gate",
                     "copywriter", "quality_gate"]
    assert result["target_reached"] and result["page_check_count"] == 1 and result["checked_batches"] == 1
    assert json.loads((data / "products.json").read_text(encoding="utf-8"))["products"][0]["status"] == "eligible"
    assert json.loads((data / "drafts.json").read_text(encoding="utf-8"))["drafts"][0]["body"] == "確認済みの紹介文"
    assert (run_dir / "outputs/research-result.json").is_file()
    assert json.loads((run_dir / "inputs/page-candidates-batch-01.json").read_text(encoding="utf-8"))["candidates"][0]["search_queries"] == [f"調査語{i}" for i in range(5)]
    heat_scores = json.loads((run_dir / "outputs/heat-scores.json").read_text(encoding="utf-8"))
    assert heat_scores["scores"][0]["grade"] == "B" and heat_scores["scores"][0]["score"] == 6


def test_research_pipeline_checks_ten_at_a_time_until_target_is_filled(tmp_path, monkeypatch):
    project = fixture_project(tmp_path, 0)
    data = project / "rakuten-room-kai"
    services = LiveServices.__new__(LiveServices)
    services.project_root = ROOT
    candidate_genres = [genre for genre, count in GENRES.items() for _ in range(count)] + [next(iter(GENRES))] * 5
    items = [{"itemUrl": f"https://item.rakuten.co.jp/shop/item-{index:02}/",
              "itemName": f"商品{index:02}", "itemCode": f"shop:item-{index:02}",
              "sourceQueries": [{"genreName": candidate_genres[index - 1], "keyword": "調査語"}]}
             for index in range(1, 21)]
    raw = {"items": items, "summary": {},
           "queries": [{"genreName": genre, "keyword": f"{genre}調査語{i}"}
                       for genre in GENRES for i in range(5)]}
    services.node = SimpleNamespace(node="fake-node", research_collect=lambda *a, **kw: raw)
    model_calls = []

    def model(context, name, payload):
        model_calls.append((name, len(payload.get("selected_candidates", []))))
        common = {"run_id": "test-batches", "target_date": TARGET}
        if name == "research_plan":
            return {"target_date": TARGET, "genre_priorities": [], "search_queries": [],
                    "winning_conditions": [], "deprioritized_conditions": [], "research_requests": []}
        if name == "triage":
            return {**common,
                    "selected": [{"product_id": item["product_id"], "reason": "確認対象"}
                                 for item in payload["api_candidates"]],
                    "excluded": [], "uncertain": []}
        if name == "product_verify":
            return {**common, "verified": [{"product_id": item["product_id"], "url": item["url"],
                    "checked_at": f"{TARGET}T13:00:00+09:00", "evidence": ["在庫・商品名・価格を確認"]}
                    for item in payload["selected_candidates"]], "rejected": [], "unavailable": []}
        if name == "copywriter":
            return {**common, "drafts": [{"product_id": item["product_id"], "url": item["url"],
                    "body": "確認済みの商品紹介", "status": "accepted", "checked_at": TARGET}
                    for item in payload["verified_products"]], "unsupported_claims": []}
        if name == "quality_gate":
            return {**common, "accepted": [item["product_id"] for item in payload["copy_output"]["drafts"]],
                    "rejected": [], "required_edits": []}
        raise AssertionError(name)

    services.model = model
    batch_sizes = []

    def fake_page_check(command, output_path, project_root, timeout_seconds, accepted_exit_codes):
        candidate_input = json.loads(Path(command[command.index("--input") + 1]).read_text(encoding="utf-8"))
        page_candidates = candidate_input["candidates"]
        batch_sizes.append(len(page_candidates))
        snapshots = [{"candidate_id": item["id"], "requested_url": item["url"], "page_url": item["url"],
                      "status": "observed", "availability": "in_stock", "name": "確認済み商品",
                      "price_yen": 1200, "observed_at": "2026-09-25T13:00:00+09:00"}
                     for item in page_candidates]
        return {"schema_version": 1, "observed_at": "2026-09-25T13:00:00+09:00",
                "requested": len(page_candidates), "observed": len(page_candidates), "unavailable": 0,
                "snapshots": snapshots,
                "heat_scores": [complete_b_score(item["id"], item["url"]) for item in page_candidates]}

    monkeypatch.setattr("automation.live_services.run_json_command", fake_page_check)
    run_dir = tmp_path / "run-batches"
    run_dir.mkdir()
    context = SimpleNamespace(run_id="test-batches", logger=SimpleNamespace(run_dir=run_dir),
                              manifest={"target_date": TARGET})
    inventory = {"target_date": TARGET, "eligible_count": 0, "draft_count": 0, "posted_count": 0,
                 "shortfall_count": 15, "inventory_shortfall_count": 120,
                 "genre_counts": {genre: 0 for genre in GENRES}, "needs_review_count": 0,
                 "eligible_product_ids": []}
    target_by_genre = {genre: GENRES[genre] for genre in GENRES}
    result = services.research(context, data, inventory, {"winning_conditions": [],
                               "deprioritized_conditions": [], "research_requests": [],
                               "single_change_experiments": []}, False, target_count=15,
                               target_by_genre=target_by_genre)

    assert result["target_reached"] is True
    assert result["page_check_count"] == 20
    assert result["checked_batches"] == 2
    assert batch_sizes == [10, 10]
    assert model_calls.count(("product_verify", 10)) == 2
    assert len(result["accepted_product_ids"]) == 20
    assert len(json.loads((data / "products.json").read_text(encoding="utf-8"))["products"]) == 20
    assert len(json.loads((data / "drafts.json").read_text(encoding="utf-8"))["drafts"]) == 20
    snapshots = json.loads((run_dir / "outputs/page-snapshots.json").read_text(encoding="utf-8"))
    assert len(snapshots["snapshots"]) == 20


def test_recovery_retries_only_failed_stage_and_downstream(tmp_path):
    project = fixture_project(tmp_path, 15)

    class RetryFake(FakeServices):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def model(self, context, name, payload):
            if name == "daily_plan" and self.fail_once:
                self.fail_once = False
                self.calls.append("daily_plan_timeout")
                raise FlowError("CLI_TIMEOUT", "temporary timeout")
            if name == "recovery":
                return {"failed_run_id": payload["failed_run_id"], "failed_step": payload["failed_step"],
                        "cause": "temporary timeout", "retryable": True,
                        "proposed_action": "daily_plan工程だけ再開", "requires_user_action": False}
            return super().model(context, name, payload)

    fake = RetryFake()
    engine = KaiOrchestrator(project, fake)
    first = engine.run("daily", date.fromisoformat(TARGET))
    assert first.manifest["status"] == "failed"
    before_observe = (fake.calls.count("affiliate"), fake.calls.count("room"), fake.calls.count("performance"))
    recovery = engine.run("recovery", date.fromisoformat(TARGET), failed_run_id=first.run_id,
                          failed_step="daily_plan")
    assert recovery.manifest["status"] == "success", recovery.manifest["errors"]
    assert (fake.calls.count("affiliate"), fake.calls.count("room"), fake.calls.count("performance")) == before_observe
    outcome = json.loads((recovery.logger.run_dir / "outputs/recovery-result.json").read_text(encoding="utf-8"))
    assert outcome["automatic_retry"] and outcome["resumed_status"] == "success"
