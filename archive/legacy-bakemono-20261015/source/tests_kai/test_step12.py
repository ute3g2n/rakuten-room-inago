"""Offline path integration with temporary stores and deterministic external fakes."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.kai_orchestrator import KaiOrchestrator
from automation.live_services import LiveServices
from tests_kai.research_fixtures import complete_b_score
from automation.observation_service import affiliate_snapshot, room_snapshot
from automation.performance_service import build_performance_input
from automation.pattern_ledger import apply_analysis
from automation.schema_validator import validate_document
from tests_kai.test_step09 import FakeServices, GENRES, ROOT, TARGET, fixture_project
from tests_kai.test_step07 import empty_analysis, input_snapshot, order
from tests_kai.test_step04 import _run as fake_model_run


SCENARIOS = json.loads((ROOT / "rakuten-room-kai/implementation/fixtures/integration/scenarios.json").read_text(encoding="utf-8"))


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _assert_run(context, flow, source):
    manifest = _json(context.manifest_path)
    assert manifest == context.manifest
    assert manifest["flow"] == flow and manifest["source"] == source
    assert manifest["status"] in {"success", "partial", "action_required", "failed"}
    assert manifest["finished_at"]
    validate_document("manifest", manifest)
    events = [_json_line for line in context.events_path.read_text(encoding="utf-8").splitlines()
              if (_json_line := json.loads(line))]
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_completed"
    assert all(event["run_id"] == context.run_id for event in events)
    for stage in manifest["stages"]:
        if stage["status"] == "success":
            assert all((context.logger.run_dir / artifact).is_file() or
                       (context.logger.run_dir.parent / manifest["parent_run_id"] / artifact).is_file()
                       for artifact in stage["artifacts"])
    assert any(context.run_id in p.read_text(encoding="utf-8") for p in
               (context.root / "history").rglob("*.jsonl"))


@pytest.mark.parametrize("source", SCENARIOS["sources"])
def test_daily_sufficient_path_and_sources(tmp_path, source):
    project = fixture_project(tmp_path, 15)
    fake = FakeServices()
    context = KaiOrchestrator(project, fake).run("daily", date.fromisoformat(TARGET), source=source)
    _assert_run(context, "daily", source)
    assert context.manifest["status"] == "success"
    assert [s["id"] for s in context.manifest["stages"]] == SCENARIOS["expected_daily_stages"]
    assert _json(context.logger.run_dir / "outputs/research-result.json")["status"] == "skipped"
    assert fake.calls == ["affiliate", "room", "performance", "daily_plan"] or (
        sorted(fake.calls[:2]) == ["affiliate", "room"] and fake.calls[2:] == ["performance", "daily_plan"])
    assert len(_json(project / "rakuten-room-kai/daily-plans" / f"{TARGET}.json")["posts"]) == 15


def test_daily_shortage_real_research_chain_partial_and_idempotent(tmp_path, monkeypatch):
    project = fixture_project(tmp_path, 0)
    data = project / "rakuten-room-kai"
    services = LiveServices.__new__(LiveServices)
    services.project_root = ROOT
    raw = {"items": [{"itemUrl": "https://item.rakuten.co.jp/shop/new-item/?tracking=1",
                      "itemName": "家事用テスト商品", "itemCode": "shop:new-item",
                      "sourceQueries": [{"genreName": "家事ラク用品"}]}], "summary": {}}
    calls = []
    services.node = SimpleNamespace(node="fake-node", research_collect=lambda *a, **kw: raw)
    base = FakeServices()
    services.affiliate = base.affiliate
    services.room = base.room

    def model(context, name, payload):
        calls.append(name)
        common = {"run_id": context.run_id, "target_date": TARGET}
        url = raw["items"][0]["itemUrl"]
        if name in {"performance", "daily_plan"}:
            return base.model(context, name, payload)
        if name == "research_plan":
            return {"target_date": TARGET, "genre_priorities": [],
                    "search_queries": [{"genre": g, "query": g} for g in GENRES],
                    "winning_conditions": [], "deprioritized_conditions": [], "research_requests": []}
        if name == "triage":
            return {**common, "selected": [{"product_id": "P01", "reason": "確認対象"}]
                    if payload["api_candidates"] else [],
                    "excluded": [], "uncertain": []}
        if name == "product_verify":
            return {**common, "verified": [{"product_id": "P01", "url": url,
                    "checked_at": "2026-09-25T13:00:00+09:00", "evidence": ["page"]}]
                    if payload["selected_candidates"] else [],
                    "rejected": [], "unavailable": []}
        if name == "copywriter":
            return {**common, "drafts": [{"product_id": "P01", "url": url,
                    "body": "家事用テスト商品の紹介文", "status": "accepted", "checked_at": TARGET}]
                    if payload["verified_products"] else [],
                    "unsupported_claims": []}
        if name == "quality_gate":
            return {**common, "accepted": ["P01"] if payload["verified_products"] else [],
                    "rejected": [], "required_edits": []}
        raise AssertionError(name)

    services.model = model
    monkeypatch.setattr("automation.live_services.run_json_command", lambda *a, **kw: {
        "schema_version": 1, "observed_at": "2026-09-25T13:00:00+09:00", "requested": 1,
        "observed": 1, "unavailable": 0,
        "snapshots": [{"candidate_id": "P01", "status": "observed", "availability": "in_stock",
                       "name": "家事用テスト商品", "price_yen": 1200, "observed_at": "2026-09-25T13:00:00+09:00"}],
        "heat_scores": [complete_b_score("P01", raw["items"][0]["itemUrl"]) ]})
    engine = KaiOrchestrator(project, services)
    first = engine.run("daily", date.fromisoformat(TARGET))
    _assert_run(first, "daily", "manual")
    assert first.manifest["status"] == "partial", first.manifest["errors"]
    assert calls == ["performance", *SCENARIOS["expected_research_jobs"], "daily_plan"]
    assert len(_json(data / "products.json")["products"]) == 1
    assert len(_json(data / "drafts.json")["drafts"]) == 1
    assert _json(first.logger.run_dir / "outputs/research-result.json")["accepted_product_ids"] == ["P01"]
    assert _json(first.logger.run_dir / "outputs/daily-report.json")["planned_count"] == 1
    second = engine.run("daily", date.fromisoformat(TARGET))
    assert second.manifest["status"] == "partial"
    assert len(_json(data / "products.json")["products"]) == 1
    assert len(_json(data / "drafts.json")["drafts"]) == 1
    assert len(_json(data / "daily-plans" / f"{TARGET}.json")["posts"]) == 1


def test_research_rechecks_initial_triage_reserve_after_selected_exhausted(tmp_path, monkeypatch):
    project = fixture_project(tmp_path, 0)
    services = LiveServices.__new__(LiveServices)
    services.project_root = ROOT
    raw = {"items": [
        {"itemUrl": f"https://item.rakuten.co.jp/shop/item-{index}/",
         "itemName": f"商品{index}", "itemCode": f"shop:item-{index}",
         "sourceQueries": [{"genreName": "家事ラク用品"}]}
        for index in range(1, 4)], "summary": {}}
    services.node = SimpleNamespace(node="fake-node", research_collect=lambda *a, **kw: raw)
    base = FakeServices()
    services.affiliate, services.room = base.affiliate, base.room
    page_batches = []

    def model(context, name, payload):
        common = {"run_id": context.run_id, "target_date": TARGET}
        if name in {"performance", "daily_plan"}:
            return base.model(context, name, payload)
        if name == "research_plan":
            return {"target_date": TARGET, "genre_priorities": [],
                    "search_queries": [{"genre": genre, "query": genre} for genre in GENRES],
                    "winning_conditions": [], "deprioritized_conditions": [], "research_requests": []}
        if name == "triage":
            return {**common, "selected": [{"product_id": "P01", "reason": "代表"}],
                    "uncertain": [{"product_id": "P02", "reason": "代替"}],
                    "excluded": [{"product_id": "P03", "reason": "代替"}]}
        if name == "product_verify":
            return {**common, "verified": [
                {"product_id": item["product_id"], "url": item["url"],
                 "checked_at": f"{TARGET}T13:00:00+09:00", "evidence": ["page"]}
                for item in payload["selected_candidates"]], "rejected": [], "unavailable": []}
        if name == "copywriter":
            return {**common, "drafts": [
                {"product_id": item["product_id"], "url": item["url"],
                 "body": f"{item['name']}の紹介文", "status": "accepted", "checked_at": TARGET}
                for item in payload["product_context"]], "unsupported_claims": []}
        if name == "quality_gate":
            return {**common, "accepted": [item["product_id"] for item in payload["product_context"]],
                    "rejected": [], "required_edits": []}
        raise AssertionError(name)

    def page_command(command, output_path, project_root, timeout, allowed):
        candidate_ids = [item["id"] for item in _json(command[command.index("--input") + 1])["candidates"]]
        page_batches.append(candidate_ids)
        return {"schema_version": 1, "observed_at": f"{TARGET}T13:00:00+09:00",
                "requested": len(candidate_ids), "observed": len(candidate_ids), "unavailable": 0,
                "snapshots": [
                    {"candidate_id": product_id, "status": "observed", "availability": "in_stock",
                     "name": f"商品{int(product_id[1:])}", "price_yen": 1200,
                     "observed_at": f"{TARGET}T13:00:00+09:00"}
                    for product_id in candidate_ids],
                "heat_scores": [complete_b_score(product_id, raw["items"][int(product_id[1:]) - 1]["itemUrl"])
                                for product_id in candidate_ids if product_id != "P01"]}

    services.model = model
    monkeypatch.setattr("automation.live_services.run_json_command", page_command)
    run = KaiOrchestrator(project, services).run("daily", date.fromisoformat(TARGET))
    result = _json(run.logger.run_dir / "outputs/research-result.json")

    assert page_batches == [["P01"], ["P02", "P03"]]
    assert result["triage_selected_count"] == 1
    assert result["fallback_page_request_count"] == 2
    assert result["page_request_count"] == 3
    assert result["page_check_count"] == 3
    assert result["qualified_count"] == 2
    assert result["accepted_product_ids"] == ["P02", "P03"]
    assert result["stop_reason"] == "triaged_candidates_exhausted"


@pytest.mark.parametrize("flow", SCENARIOS["flows"])
@pytest.mark.parametrize("source", SCENARIOS["sources"])
def test_all_flow_sources_manifest_and_artifacts(tmp_path, flow, source):
    project = fixture_project(tmp_path, 0)
    fake = FakeServices()
    engine = KaiOrchestrator(project, fake)
    kwargs = {"dry_run": True}
    if flow == "config-change":
        kwargs["proposal"] = {"dashboard_port": 8766}
    if flow == "recovery":
        failed = engine.run("daily", date(2026, 9, 24), source=source)
        kwargs.update(failed_run_id=failed.run_id, failed_step="load_state")
    context = engine.run(flow, date.fromisoformat(TARGET), source=source, **kwargs)
    _assert_run(context, flow, source)
    assert context.manifest["status"] in {"success", "partial", "action_required"}, context.manifest["errors"]
    assert [s["id"] for s in context.manifest["stages"]] == [s["id"] for s in engine.workflow["flows"][flow]["stages"]]


def test_observation_zero_order_match_unmatched_and_unavailable(tmp_path):
    project = fixture_project(tmp_path, 1)
    data = project / "rakuten-room-kai"
    product = _json(data / "products.json")["products"][0]
    product["status"] = "posted"
    product["posted_at"] = "2026-09-24"
    store = _json(data / "products.json")
    store["products"][0] = product
    (data / "products.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    status = _json(data / "post-status.json")
    status["items"]["P01"] = {"posted": True, "ever_marked_posted": True,
                                "posted_at": "2026-09-24T13:00:00+09:00"}
    (data / "post-status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    observed = {"byDate": {"2026-09-24": {"status": "observed", "observedAt": "2026-09-25T13:00:00+09:00",
                "clicks": 0, "ordersUnconfirmed": 0, "salesJpyUnconfirmed": 0, "commissionJpyUnconfirmed": 0}},
                "orderDetailsByMonth": {"2026-09": {"status": "observed", "orders": []}}}
    zero = affiliate_snapshot(observed, {"status": "observed"}, "2026-09-24")
    assert zero["status"] == "observed" and zero["sales_count"] == 0
    assert affiliate_snapshot({}, {"status": "unavailable"}, "2026-09-24")["sales_count"] is None
    observed["orderDetailsByMonth"]["2026-09"]["orders"] = [
        {"productName": "商品1", "salesJpy": 1000, "status": "confirmed"},
        {"productName": "別の商品", "salesJpy": 2000, "status": "confirmed"}]
    sale = affiliate_snapshot(observed, {"status": "observed"}, "2026-09-24")
    room = room_snapshot({"postsById": {"r1": {"status": "observed", "postId": "r1",
        "itemUrl": product["url"], "likes": 0, "observedAt": "2026-09-25T13:00:00+09:00"},
        "r2": {"status": "observed", "itemUrl": "https://item.rakuten.co.jp/other/unknown/",
        "likes": 3, "observedAt": "2026-09-25T13:00:00+09:00"}}}, {"status": "observed"}, TARGET, [product])
    assert [r["status"] for r in room["observations"]] == ["observed", "unmatched"]
    assert room["observations"][0]["likes"] == 0
    assert room_snapshot({}, {"status": "unavailable"}, TARGET, [product])["status"] == "unavailable"
    performance = build_performance_input(data, TARGET, sale, room)
    assert performance["post_observations"][0]["reaction_label"] == "公開反応なし"
    assert performance["post_observations"][0]["mature"]
    assert len(performance["purchased_items"]) == 2
    assert sum(item["product_id"] is not None for item in performance["purchased_items"]) == 1
    assert len(performance["missing_or_unattributed_data"]) == 1
    assert not build_performance_input(data, "2026-09-24", zero, room)["post_observations"][0]["mature"]


def test_resume_and_setup_no_duplicate(tmp_path):
    project = fixture_project(tmp_path, 15)
    fake = FakeServices()
    engine = KaiOrchestrator(project, fake)
    setup1 = engine.run("setup", date.fromisoformat(TARGET))
    setup2 = engine.run("setup", date.fromisoformat(TARGET))
    assert setup1.manifest["status"] == setup2.manifest["status"] == "action_required"
    first = engine.run("daily", date.fromisoformat(TARGET))
    second = engine.run("daily", date.fromisoformat(TARGET), resume_manifest=first.manifest_path)
    assert second.manifest["status"] == "success"
    assert second.manifest["parent_run_id"] == first.run_id
    assert len(_json(project / "rakuten-room-kai/daily-plans" / f"{TARGET}.json")["posts"]) == 15
    assert fake.calls.count("daily_plan") == 1


def test_posted_product_never_reenters_daily_plan(tmp_path):
    project = fixture_project(tmp_path, 15)
    data = project / "rakuten-room-kai"
    store = _json(data / "products.json")
    store["products"][0]["status"] = "posted"
    store["products"][0]["posted_at"] = "2026-09-24"
    (data / "products.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    fake = FakeServices()
    run = KaiOrchestrator(project, fake).run("daily", date.fromisoformat(TARGET))
    assert run.manifest["status"] == "partial", run.manifest["errors"]
    planned = _json(data / "daily-plans" / f"{TARGET}.json")["posts"]
    assert len(planned) == 14
    assert "P01" not in {item["product_id"] for item in planned}


def test_learning_a_b_c_hold_and_single_change(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "bc").mkdir()
    sale = input_snapshot(tmp_path / "a", [order()], likes=2)
    a = empty_analysis()
    a["post_decisions"] = [{"product_id": "P01", "rating": "A",
                            "attribution": "post_confirmed_by_order_product_match", "evidence": "order-1"}]
    a["winning_conditions"] = [{"condition": "家事短縮", "evidence": "order-1"}]
    a["research_requests"] = [{"condition": "家事短縮", "evidence": "別商品を確認"}]
    result = apply_analysis(tmp_path / "a", "run-a", sale, a)
    assert result["counts"]["A"] == 1
    assert result["research_queue"]["requests"][0]["requested_count"] == 3
    assert result["research_queue"]["requests"][0]["exclude_product_ids"] == ["P01"]

    reaction_b = input_snapshot(tmp_path / "bc", likes=1)
    b = empty_analysis()
    b["post_decisions"] = [{"product_id": "P01", "rating": "B",
                            "attribution": "post_confirmed", "evidence": "投稿の公開反応を確認"}]
    b["single_change_experiments"] = [{"variable": "冒頭", "change": "短く", "evidence": "反応"}]
    assert apply_analysis(tmp_path / "bc", "run-b", reaction_b, b)["counts"]["B"] == 1
    reaction = input_snapshot(tmp_path / "bc", likes=0)
    c = empty_analysis()
    c["post_decisions"] = [{"product_id": "P01", "rating": "C",
                            "attribution": "post_confirmed", "evidence": "公開反応0"}]
    c["deprioritized_conditions"] = [{"condition": "長い冒頭", "evidence": "公開反応0"}]
    assert apply_analysis(tmp_path / "bc", "run-c", reaction, c)["counts"]["C"] == 1
    held = empty_analysis()
    held["post_decisions"] = [{"product_id": "P01", "rating": "hold",
                               "attribution": "unattributed", "evidence": "判断材料不足"}]
    result = apply_analysis(tmp_path / "bc", "run-h", reaction, held)
    assert result["counts"]["hold"] == 1
    assert len(result["pattern_ledger"]["deprioritized_conditions"]) == 1


def test_fake_cli_retry_escalation_and_auth_ban(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    for name in ("retry", "escalated", "banned"):
        (tmp_path / name).mkdir()
    normal, retry, _ = fake_model_run(tmp_path / "retry", mode="fail_once")
    assert retry.success and [x["model"] for x in retry.attempts] == ["gpt-6-luna"] * 2
    assert len(normal.manifest["model_attempts"]) == 2
    _, escalated, _ = fake_model_run(tmp_path / "escalated", mode="escalate", job="product_verify",
                                     reason="conflicting_product_information")
    assert escalated.success and [x["model"] for x in escalated.attempts] == [
        "gpt-6-sol", "gpt-6-sol", "gpt-6-astra"]
    _, banned, _ = fake_model_run(tmp_path / "banned", mode="auth", job="product_verify",
                                  reason="conflicting_product_information")
    assert not banned.success and banned.error_code == "AUTH_REQUIRED"
    assert len(banned.attempts) == 1
