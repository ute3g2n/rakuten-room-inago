import http.client
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from automation.dashboard_server import make_server
from automation.dashboard_service import DashboardError, upsert_dashboard
from automation.inventory_service import build_inventory_brief


def _fixture(root: Path, ids=("P04", "P06")) -> dict:
    root.mkdir()
    products = []
    drafts = []
    posts = []
    for product_id in ids:
        url = f"https://item.rakuten.co.jp/shop/{product_id.lower()}/"
        products.append({"id": product_id, "name": f"商品{product_id}", "url": url,
                         "normalized_url": url, "genre": "時短・料理・調理・家電",
                         "status": "eligible", "item_code": f"shop/{product_id.lower()}",
                         "model_candidate": None, "checked_at": "2026-09-24", "posted_at": None})
        drafts.append({"product_id": product_id, "url": url, "body": f"本文{product_id}",
                       "status": "accepted", "checked_at": "2026-09-24"})
        posts.append({"product_id": product_id, "name": f"商品{product_id}", "url": url,
                      "genre": "時短・料理・調理・家電", "body": f"本文{product_id}",
                      "scheduled_at": "2026-09-25T21:00:00+09:00", "role": "入口"})
    (root / "products.json").write_text(json.dumps({"version": 1, "products": products, "next_id": 7}), encoding="utf-8")
    (root / "drafts.json").write_text(json.dumps({"version": 1, "drafts": drafts}), encoding="utf-8")
    (root / "post-status.json").write_text(json.dumps({"version": 1, "items": {}}), encoding="utf-8")
    (root / "dashboard-days.json").write_text(json.dumps({"version": 1, "days": []}), encoding="utf-8")
    (root / "posts-log.jsonl").write_text("", encoding="utf-8")
    return {"target_date": "2026-09-25", "posts": posts, "shortfall": [], "applied_learning": []}


def _request(port: int, method: str, path: str, body=None, token=None, origin=None,
             host=None, content_type="application/json"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if body is not None:
        headers.update({"Content-Type": content_type, "Origin": origin or f"http://127.0.0.1:{port}",
                        "X-CSRF-Token": token or ""})
    connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data


def test_latest_day_first_and_same_day_upsert(tmp_path: Path):
    root = tmp_path / "room"
    plan = _fixture(root)
    upsert_dashboard(root, plan)
    newer = {**plan, "target_date": "2026-09-26", "posts": []}
    upsert_dashboard(root, newer)
    upsert_dashboard(root, newer)
    html = (root / "posting-dashboard.html").read_text(encoding="utf-8")
    assert html.count('id="day-2026-09-26"') == 1
    assert html.index('id="day-2026-09-26"') < html.index('id="day-2026-09-25"')
    assert '<details id="day-2026-09-26" open' in html
    assert '<details id="day-2026-09-25"' in html
    assert 'id="body-2026-09-25-P04"' in html
    script = (root / "dashboard-assets/app.js").read_text(encoding="utf-8") if (root / "dashboard-assets/app.js").exists() else (Path(__file__).parents[1] / "rakuten-room-kai/dashboard-assets/app.js").read_text(encoding="utf-8")
    assert 'location.protocol !== "http:"' in script
    assert 'type="checkbox"' in html and 'disabled' in html
    repeated = {**plan, "target_date": "2026-09-27", "posts": [
        {**post, "scheduled_at": "2026-09-27T21:00:00+09:00"} for post in plan["posts"]]}
    with pytest.raises(DashboardError, match="DATA_MISMATCH"):
        upsert_dashboard(root, repeated)


def test_shortfall_reason_is_visible_and_escaped_on_dashboard(tmp_path: Path):
    root = tmp_path / "room"
    plan = _fixture(root, ids=())
    plan["shortfall"] = [{"code": "INVENTORY_SHORT", "detail": "確認済み商品が0件 <不足>"}]
    upsert_dashboard(root, plan)
    html = (root / "posting-dashboard.html").read_text(encoding="utf-8")
    assert html.count('id="day-2026-09-25"') == 1
    assert "予定0件" in html
    assert "確認済み商品が0件 &lt;不足&gt;" in html
    assert "確認済み商品が0件 <不足>" not in html


def test_mismatch_duplicate_and_xss_are_rejected_or_escaped(tmp_path: Path):
    root = tmp_path / "room"
    plan = _fixture(root)
    plan["posts"][0]["body"] = '</script><img src=x onerror=alert(1)>'
    with pytest.raises(DashboardError, match="DATA_MISMATCH"):
        upsert_dashboard(root, plan)
    plan["posts"][0]["body"] = "本文P04"
    plan["posts"].append(plan["posts"][0])
    with pytest.raises(DashboardError, match="DATA_MISMATCH"):
        upsert_dashboard(root, plan)
    plan["posts"] = plan["posts"][:2]
    products = json.loads((root / "products.json").read_text(encoding="utf-8"))
    attack = '</script><img src=x onerror=alert(1)>'
    products["products"][0]["name"] = attack
    plan["posts"][0]["name"] = products["products"][0]["name"]
    (root / "products.json").write_text(json.dumps(products), encoding="utf-8")
    drafts = json.loads((root / "drafts.json").read_text(encoding="utf-8"))
    drafts["drafts"][0]["body"] = attack
    plan["posts"][0]["body"] = attack
    (root / "drafts.json").write_text(json.dumps(drafts), encoding="utf-8")
    upsert_dashboard(root, plan)
    html = (root / "posting-dashboard.html").read_text(encoding="utf-8")
    assert '</script><img src=x onerror=alert(1)>' not in html
    assert '&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;' in html
    body = re.search(r'id="body-2026-09-25-P04"[^>]*>(.*?)</div>', html).group(1)
    assert body == '&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;'


def test_server_persists_and_correction_does_not_restore_inventory(tmp_path: Path):
    root = tmp_path / "room"
    upsert_dashboard(root, _fixture(root))
    server = make_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        status, session = _request(port, "GET", "/api/session")
        assert status == 200
        token = session["csrf_token"]
        payload = {"product_id": "P04", "scheduled_date": "2026-09-25", "posted": True}
        status, _ = _request(port, "POST", "/api/post-status", payload, token)
        assert status == 200
        status, current = _request(port, "GET", "/api/status")
        assert current["items"]["P04"]["posted"]
        assert status == 200
        upsert_dashboard(root, _fixture_plan_after_save(root))
        assert '1/2 投稿済み' in (root / "posting-dashboard.html").read_text(encoding="utf-8")
        status, _ = _request(port, "POST", "/api/post-status", {**payload, "posted": False}, token)
        assert status == 400
        status, _ = _request(port, "POST", "/api/post-status", {**payload, "posted": False,
                                   "confirm_correction": True, "reason": "誤操作を訂正"}, token)
        assert status == 200
        persisted = json.loads((root / "post-status.json").read_text(encoding="utf-8"))
        assert not persisted["items"]["P04"]["posted"]
        assert persisted["items"]["P04"]["ever_marked_posted"]
        assert persisted["items"]["P04"]["correction_history"]
        assert build_inventory_brief(root, "2026-09-25")["eligible_count"] == 1
        assert _request(port, "POST", "/api/post-status", payload, "wrong")[0] == 403
        assert _request(port, "POST", "/api/post-status", payload, token, "http://evil.example")[0] == 403
        assert _request(port, "POST", "/api/post-status", payload, token,
                        host="evil.example")[0] == 403
        assert _request(port, "POST", "/api/post-status", payload, token,
                        content_type="text/plain")[0] == 415
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _fixture_plan_after_save(root: Path) -> dict:
    return json.loads((root / "daily-plans/2026-09-25.json").read_text(encoding="utf-8"))


def test_concurrent_updates_keep_both_ids(tmp_path: Path):
    root = tmp_path / "room"
    upsert_dashboard(root, _fixture(root))
    server = make_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        token = _request(port, "GET", "/api/session")[1]["csrf_token"]
        def update(product_id):
            return _request(port, "POST", "/api/post-status", {
                "product_id": product_id, "scheduled_date": "2026-09-25", "posted": True}, token)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(update, ("P04", "P06"))) == [200, 200]
        stored = json.loads((root / "post-status.json").read_text(encoding="utf-8"))
        assert set(stored["items"]) == {"P04", "P06"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
