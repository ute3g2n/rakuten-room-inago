"""Every versioned Schema has generated valid and invalid contract fixtures."""

import copy
import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from automation.schema_validator import SCHEMA_DIR, SchemaValidationError, validate_document
from automation.schema_validator import validate_json_file, validate_product_store
from automation.config_loader import load_config, load_model_policy, load_workflow
from automation.inventory_service import classify_candidates, normalize_code
from automation.inventory_service import build_inventory_brief
from automation.dashboard_server import _update_status, make_server
from automation.dashboard_service import DashboardError, _validate_plan
from automation.secret_scan import scan_paths


NAMES = sorted(path.stem.removesuffix(".schema") for path in SCHEMA_DIR.glob("*.schema.json"))


def sample(schema):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return sample(next(item for item in schema["anyOf"] if item.get("type") != "null"))
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next(item for item in kind if item != "null")
    if kind == "object":
        return {key: sample(schema["properties"][key]) for key in schema.get("required", [])}
    if kind == "array":
        return []
    if kind == "integer":
        return max(schema.get("minimum", 0), 0)
    if kind == "number":
        return max(schema.get("minimum", 0), 0)
    if kind == "boolean":
        return False
    if kind == "string":
        pattern = schema.get("pattern", "")
        if "P[0-9]" in pattern or "P\\d" in pattern:
            return "P01"
        if "{8}" in pattern and "daily" in pattern:
            return "20260925-130000-daily-abcdef"
        if pattern.startswith("^https://") or schema.get("format") == "uri":
            return "https://item.rakuten.co.jp/shop/item/"
        if "09:00" in pattern or schema.get("format") == "date-time":
            return "2026-09-25T13:00:00+09:00"
        if schema.get("format") == "date" or "{4}" in pattern:
            return "2026-09-25"
        if "[0-5]" in pattern:
            return "13:00"
        return "x"
    if kind == "null":
        return None
    raise AssertionError(f"unsupported schema shape: {schema}")


@pytest.mark.parametrize("name", NAMES)
def test_schema_valid_required_missing_and_extra(name):
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    valid = sample(schema)
    validate_document(name, valid)
    missing = copy.deepcopy(valid)
    missing.pop(schema["required"][0])
    with pytest.raises(SchemaValidationError):
        validate_document(name, missing)
    extra = {**valid, "unexpected_field": True}
    with pytest.raises(SchemaValidationError):
        validate_document(name, extra)


@pytest.mark.parametrize("name,field,bad", [
    ("daily-plan", "target_date", "2026-99-99"),
    ("daily-report", "status", "unknown"),
    ("automation-config", "daily_schedule", "25:99"),
    ("job-envelope", "run_id", "bad-id"),
    ("products", "version", 2),
])
def test_schema_invalid_formats_and_enums(name, field, bad):
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    invalid = sample(schema)
    invalid[field] = bad
    with pytest.raises(SchemaValidationError):
        validate_document(name, invalid)


def test_all_acceptance_ids_have_traceability_rows():
    root = SCHEMA_DIR.parents[1] / "implementation"
    criteria = (root / "acceptance-criteria.md").read_text(encoding="utf-8")
    matrix = (root / "test-traceability-matrix.md").read_text(encoding="utf-8")
    import re
    ids = set(re.findall(r"AC-\d{3}", criteria))
    rows = set(re.findall(r"^\| (AC-\d{3}) \|", matrix, re.MULTILINE))
    assert len(ids) == 60 and rows == ids


def test_nested_product_id_url_and_date_rejections():
    schema = json.loads((SCHEMA_DIR / "products.schema.json").read_text(encoding="utf-8"))
    entry_schema = schema["properties"]["products"]["items"]
    valid = sample(schema)
    valid["products"] = [sample(entry_schema)]
    validate_document("products", valid)
    for field, bad in (("id", "not-an-id"), ("url", "http://example.com/item"),
                       ("checked_at", "2026-13-40")):
        invalid = copy.deepcopy(valid)
        invalid["products"][0][field] = bad
        with pytest.raises(SchemaValidationError):
            validate_document("products", invalid)


def test_config_cross_field_guards(tmp_path):
    source = SCHEMA_DIR.parent
    for name in ("automation-config.json", "model-policy.json", "workflow.json"):
        shutil.copy2(source / name, tmp_path / name)
    config_path = tmp_path / "automation-config.json"
    original = json.loads(config_path.read_text(encoding="utf-8"))
    bad = copy.deepcopy(original)
    bad["daily_target"] += 1
    config_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="genre targets"):
        load_config(tmp_path)
    bad = copy.deepcopy(original)
    bad["operation_start_date"] = bad["preparation_date"]
    config_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="operation start"):
        load_config(tmp_path)
    config_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    model_path = tmp_path / "model-policy.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    bad = copy.deepcopy(model)
    bad["astra_exception_only"] = False
    model_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="exception only"):
        load_model_policy(tmp_path)
    bad = copy.deepcopy(model)
    bad["jobs"]["triage"]["normal_model"] = "gpt-6-astra"
    model_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Astra in normal"):
        load_model_policy(tmp_path)
    workflow_path = tmp_path / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["flows"]["status"]["stages"][0]["depends_on"] = ["missing_stage"]
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="workflow DAG"):
        load_workflow(tmp_path)


def test_product_store_duplicate_keys_and_file_validation(tmp_path):
    root = SCHEMA_DIR.parents[1]
    source = json.loads((root / "products.json").read_text(encoding="utf-8"))
    first = copy.deepcopy(source["products"][0])
    second = copy.deepcopy(first)
    second.update(id="P99", url="https://item.rakuten.co.jp/other/other/",
                  normalized_url="https://item.rakuten.co.jp/other/other/",
                  item_code="other/other", model_candidate="OTHER")
    first["model_candidate"] = "FIRST"
    base = {"version": 1, "products": [first, second], "next_id": 100}
    validate_product_store(base)
    for field in ("normalized_url", "item_code", "model_candidate"):
        bad = copy.deepcopy(base)
        bad["products"][1][field] = bad["products"][0][field]
        with pytest.raises(SchemaValidationError):
            validate_product_store(bad)
    bad = copy.deepcopy(base)
    bad["next_id"] = 99
    with pytest.raises(SchemaValidationError):
        validate_product_store(bad)
    path = tmp_path / "products.json"
    path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    assert validate_json_file("product-ledger", path)["next_id"] == 100
    path.write_text(json.dumps({"version": 1, "drafts": []}), encoding="utf-8")
    assert validate_json_file("drafts", path)["drafts"] == []
    with pytest.raises(SchemaValidationError):
        validate_document("not-a-schema", {})


def test_inventory_candidate_all_classifications():
    assert normalize_code(None) is None
    url = lambda slug: f"https://item.rakuten.co.jp/shop/{slug}/"
    products = [{"id": f"P{i:02}", "normalized_url": url(f"item{i}"),
                 "item_code": f"shop:item{i}", "model_candidate": "MODEL-X" if i == 4 else None}
                for i in range(1, 5)]
    candidates = [{"itemUrl": url("item1"), "itemCode": "shop:item1"},
                  {"itemUrl": url("item2"), "itemCode": "shop:item2"},
                  {"itemUrl": url("item3"), "itemCode": "shop:item3"},
                  {"itemUrl": url("new"), "itemCode": None, "model_candidate": "model-x"},
                  {"itemUrl": url("new2"), "itemCode": None},
                  {"itemUrl": url("new2"), "itemCode": None}]
    result = classify_candidates(candidates, products, {"P01"}, {"P02"})
    assert [row["classification"] for row in result] == ["already-posted", "planned", "in-ledger",
                                                          "review-model", "new", "duplicate"]


def test_dashboard_rejects_invalid_status_requests(tmp_path):
    root = tmp_path
    (root / "daily-plans").mkdir()
    (root / "daily-plans/2026-09-25.json").write_text(json.dumps({"target_date": "2026-09-25",
        "posts": [{"product_id": "P01", "name": "商品", "url": "https://item.rakuten.co.jp/shop/item/",
                   "genre": "家事ラク用品", "body": "本文", "scheduled_at": "2026-09-25T13:00:00+09:00",
                   "role": "紹介"}], "shortfall": [], "applied_learning": []}, ensure_ascii=False), encoding="utf-8")
    (root / "post-status.json").write_text(json.dumps({"version": 1, "items": {}}), encoding="utf-8")
    base = {"product_id": "P01", "scheduled_date": "2026-09-25", "posted": True}
    for change in ({"extra": True}, {"product_id": "bad"}, {"scheduled_date": "wrong"},
                   {"posted": 1}, {"product_id": "P02"}, {"scheduled_date": "2026-09-26"}):
        with pytest.raises(ValueError):
            _update_status(root, {**base, **change})
    with pytest.raises(ValueError):
        _update_status(root, {**base, "posted": False})


def test_dashboard_plan_boundaries(tmp_path):
    from tests_kai.test_step09 import fixture_project
    project = fixture_project(tmp_path, 1)
    root = project / "rakuten-room-kai"
    post = {"product_id": "P01", "name": "商品1", "url": "https://item.rakuten.co.jp/shop/item-1/",
            "genre": "時短・料理・調理・家電", "body": "商品1の紹介文",
            "scheduled_at": "2026-09-25T13:00:00+09:00", "role": "紹介"}
    plan = {"target_date": "2026-09-25", "posts": [post], "shortfall": [], "applied_learning": []}
    _validate_plan(root, plan, set())
    bad = copy.deepcopy(plan)
    bad["posts"].append(copy.deepcopy(post))
    with pytest.raises(DashboardError, match="duplicate"):
        _validate_plan(root, bad, set())
    for key, value in (("body", "別の商品文"), ("scheduled_at", "2026-09-26T13:00:00+09:00"),
                       ("name", "別の商品")):
        bad = copy.deepcopy(plan)
        bad["posts"][0][key] = value
        with pytest.raises(DashboardError):
            _validate_plan(root, bad, set())
    drafts_path = root / "drafts.json"
    drafts = json.loads(drafts_path.read_text(encoding="utf-8"))
    drafts["drafts"].append(copy.deepcopy(drafts["drafts"][0]))
    drafts_path.write_text(json.dumps(drafts, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DashboardError, match="duplicate draft"):
        _validate_plan(root, plan, set())
    drafts["drafts"].pop()
    drafts_path.write_text(json.dumps(drafts, ensure_ascii=False), encoding="utf-8")
    products_path = root / "products.json"
    products = json.loads(products_path.read_text(encoding="utf-8"))
    products["products"][0]["checked_at"] = "2026-09-10"
    products_path.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DashboardError, match="stale"):
        _validate_plan(root, plan, set())
    products["products"][0]["checked_at"] = "2026-09-25"
    products_path.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    status_path = root / "post-status.json"
    status = {"version": 1, "items": {"P01": {"product_id": "P01", "scheduled_date": "2026-09-25",
              "posted": False, "ever_marked_posted": True, "posted_at": None,
              "updated_at": "2026-09-25T13:00:00+09:00", "correction_history": []}}}
    status_path.write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(DashboardError, match="previously posted"):
        _validate_plan(root, plan, set())


def test_inventory_planned_posted_and_stale_drafts(tmp_path):
    from tests_kai.test_step09 import fixture_project
    project = fixture_project(tmp_path, 3)
    root = project / "rakuten-room-kai"
    (root / "posts-log.jsonl").write_text('\n'.join((
        json.dumps({"date": "2026-09-25", "product_id": "P01", "status": "posted", "source": "fixture",
                    "posted_at": None, "reaction": None, "rating": None}),
        json.dumps({"date": "2026-09-25", "product_id": "P02", "status": "planned", "source": "fixture",
                    "posted_at": None, "reaction": None, "rating": None}))) + '\n', encoding="utf-8")
    products_path = root / "products.json"
    products = json.loads(products_path.read_text(encoding="utf-8"))
    products["products"][2]["checked_at"] = "2026-09-10"
    products_path.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    brief = build_inventory_brief(root, "2026-09-25")
    assert brief["eligible_count"] == 0 and brief["posted_count"] == 1


def test_dashboard_get_routes_and_missing_status(tmp_path):
    root = tmp_path
    (root / "dashboard-assets").mkdir()
    (root / "posting-dashboard.html").write_text("<h1>test</h1>", encoding="utf-8")
    (root / "dashboard-assets/app.js").write_text("const test = 1;", encoding="utf-8")
    (root / "dashboard-assets/style.css").write_text("body{}", encoding="utf-8")
    (root / "post-status.json").write_text(json.dumps({"version": 1, "items": {}}), encoding="utf-8")
    server = make_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for route in ("/", "/posting-dashboard.html", "/dashboard-assets/app.js", "/dashboard-assets/style.css", "/api/status"):
            with urllib.request.urlopen(base + route) as response:
                assert response.status == 200
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/../../secret")
        assert error.value.code == 404
        hostile = urllib.request.Request(base + "/", headers={"Host": "example.com"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(hostile)
        assert error.value.code == 403
        (root / "post-status.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/api/session")
        assert error.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_skill_links_and_secret_scanner(tmp_path):
    root = SCHEMA_DIR.parents[2]
    skills = [root / ".agents/skills/rakuten-room-bakemono-kai/SKILL.md",
              *sorted((root / ".agents/skills").glob("rr-kai-*/SKILL.md"))]
    assert len(skills) == 9
    import re
    for skill in skills:
        content = skill.read_text(encoding="utf-8")
        assert f"name: {skill.parent.name}" in content
        assert not re.search(r"\b(?:TODO|FIXME)\b", content)
        for relative in re.findall(r"\]\(([^)#]+)(?:#[^)]+)?\)", content):
            assert (skill.parent / relative).is_file(), (skill, relative)
    safe = tmp_path / "safe.json"
    safe.write_text('{"message":"password=[REDACTED]"}', encoding="utf-8")
    assert not scan_paths([safe])
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text('{"password":"dummy-value"}', encoding="utf-8")
    assert scan_paths([unsafe])
