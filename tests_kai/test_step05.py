import json
import sys
from pathlib import Path

import pytest

from automation.adapters.node_commands import CommandError, run_json_command, run_snapshot_command
from automation.config_loader import load_config
from automation.inventory_service import build_inventory_brief, classify_candidates
from automation.observation_service import affiliate_snapshot, room_snapshot
from automation.research_collect_service import build_research_input, reclassify_snapshot, summarize_snapshot


def test_adapter_requires_fresh_artifact_and_success(tmp_path: Path):
    output = tmp_path / "result.json"
    script = tmp_path / "fake.py"
    script.write_text("import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({'ok': True}))\n", encoding="utf-8")
    result = run_json_command([sys.executable, str(script), str(output)], output, tmp_path, timeout_seconds=5)
    assert result["ok"] is True
    with pytest.raises(CommandError):
        run_json_command([sys.executable, "-c", "pass"], output, tmp_path, timeout_seconds=5)
    with pytest.raises(CommandError):
        run_json_command([sys.executable, "-c", "raise SystemExit(2)"], output, tmp_path, timeout_seconds=5)
    with pytest.raises(CommandError) as error:
        run_json_command([sys.executable, "-c", "import sys;print('accessKey=dummy-key', file=sys.stderr);raise SystemExit(2)"],
                         output, tmp_path, timeout_seconds=5)
    assert "dummy-key" not in error.value.stderr
    with pytest.raises(CommandError):
        run_json_command([sys.executable, "-c", "import time;time.sleep(2)"], output, tmp_path, timeout_seconds=0.1)


def test_adapter_keeps_partial_research_snapshot(tmp_path: Path):
    output_dir = tmp_path / "out"
    script = tmp_path / "partial.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).mkdir(exist_ok=True)\n"
        "(Path(sys.argv[1])/'snapshot.json').write_text(json.dumps({'summary': {'failedQueries': 1}}))\n"
        "raise SystemExit(1)\n", encoding="utf-8"
    )
    result = run_snapshot_command([sys.executable, str(script), str(output_dir)], output_dir,
                                  tmp_path, timeout_seconds=5, allow_partial=True)
    assert result["summary"]["failedQueries"] == 1


def test_inventory_counts_only_verified_unposted_drafts(tmp_path: Path):
    root = tmp_path / "rakuten-room-kai"
    root.mkdir()
    products = [
        {"id": "P03", "name": "posted", "url": "https://item.rakuten.co.jp/s/a/", "normalized_url": "https://item.rakuten.co.jp/s/a/", "genre": "時短・料理・調理・家電", "status": "posted", "item_code": "s/a", "model_candidate": None, "checked_at": "2026-09-24", "posted_at": "2026-09-22"},
        {"id": "P04", "name": "ready", "url": "https://item.rakuten.co.jp/s/b/", "normalized_url": "https://item.rakuten.co.jp/s/b/", "genre": "時短・料理・調理・家電", "status": "eligible", "item_code": "s/b", "model_candidate": "MODEL-B", "checked_at": "2026-09-24", "posted_at": None},
    ]
    (root / "products.json").write_text(json.dumps({"version": 1, "products": products, "next_id": 5}), encoding="utf-8")
    (root / "drafts.json").write_text(json.dumps({"version": 1, "drafts": [{"product_id": "P04", "url": products[1]["url"], "body": "本文", "status": "accepted", "checked_at": "2026-09-24"}]}), encoding="utf-8")
    (root / "post-status.json").write_text(json.dumps({"version": 1, "items": {"P03": {"product_id": "P03", "scheduled_date": None, "posted": True, "ever_marked_posted": True, "posted_at": None, "updated_at": "2026-09-24T13:00:00+09:00", "correction_history": []}}}), encoding="utf-8")
    (root / "posts-log.jsonl").write_text("", encoding="utf-8")
    brief = build_inventory_brief(root, "2026-09-25")
    assert brief["eligible_count"] == 1 and brief["posted_count"] == 1
    assert brief["shortfall_count"] == 14
    candidates = [{"itemCode": "s:a", "itemUrl": products[0]["url"]},
                  {"itemCode": "other:code", "itemUrl": "https://item.rakuten.co.jp/other/code/", "model_candidate": "MODEL-B"},
                  {"itemCode": "other:new", "itemUrl": "https://item.rakuten.co.jp/other/new/"}]
    statuses = [row["classification"] for row in classify_candidates(candidates, products, {"P03"}, set())]
    assert statuses == ["already-posted", "review-model", "new"]
    remaining_genres = [genre for genre, count in load_config()["genre_targets"].items()
                        for _ in range(count)]
    remaining_genres.remove(products[1]["genre"])
    for number, genre in zip(range(5, 19), remaining_genres):
        product = {**products[1], "id": f"P{number:02d}", "name": f"ready {number}",
                   "url": f"https://item.rakuten.co.jp/s/item{number}/",
                   "normalized_url": f"https://item.rakuten.co.jp/s/item{number}/",
                   "item_code": f"s/item{number}", "model_candidate": None, "genre": genre}
        products.append(product)
    (root / "products.json").write_text(json.dumps({"version": 1, "products": products, "next_id": 19}), encoding="utf-8")
    drafts = [{"product_id": product["id"], "url": product["url"], "body": "本文", "status": "accepted",
               "checked_at": "2026-09-24"} for product in products if product["id"] != "P03"]
    (root / "drafts.json").write_text(json.dumps({"version": 1, "drafts": drafts}), encoding="utf-8")
    full = build_inventory_brief(root, "2026-09-25")
    assert full["eligible_count"] == 15 and full["shortfall_count"] == 0
    for product in products:
        if product["status"] == "eligible":
            product["genre"] = "時短・料理・調理・家電"
    (root / "products.json").write_text(json.dumps({"version": 1, "products": products, "next_id": 19}), encoding="utf-8")
    unbalanced = build_inventory_brief(root, "2026-09-25")
    assert unbalanced["eligible_count"] == 15 and unbalanced["shortfall_count"] == 9


def test_observation_unavailable_is_not_zero():
    old = {"byDate": {"2026-09-25": {"status": "observed", "clicks": 0,
           "ordersUnconfirmed": 0, "salesJpyUnconfirmed": 0, "commissionJpyUnconfirmed": 0,
           "observedAt": "2026-09-25T04:00:00Z", "source": "report"}}, "orderDetailsByMonth": {}}
    unavailable = affiliate_snapshot(old, {"status": "unavailable"}, "2026-09-25")
    assert unavailable["status"] == "unavailable" and unavailable["clicks"] is None
    observed = affiliate_snapshot(old, {"status": "observed"}, "2026-09-25")
    assert observed["status"] == "observed" and observed["clicks"] == 0
    room = room_snapshot({"postsById": {}}, {"status": "unavailable"}, "2026-09-25", [])
    assert room["status"] == "unavailable" and room["observations"] == []
    observed_room = room_snapshot({"postsById": {"123": {"postId": "123", "itemUrl": "https://item.rakuten.co.jp/s/a/",
        "observedAt": "2026-09-25T04:00:00Z", "likes": 0, "status": "observed"}}},
        {"status": "observed"}, "2026-09-25", [{"id": "P03", "url": "https://item.rakuten.co.jp/s/a/"}])
    assert observed_room["observations"][0]["likes"] == 0
    assert observed_room["observations"][0]["product_id"] == "P03"


def test_research_summary_preserves_partial_failure():
    snapshot = {"summary": {"rawResults": 10, "uniqueResults": 8, "duplicatesRemoved": 2,
                "newCandidates": 4, "alreadyPosted": 1, "planned": 1, "alreadyInLedger": 2,
                "failedQueries": 1, "succeededQueries": 2, "totalQueries": 3},
                "items": [{"status": status} for status, count in
                          (("new", 4), ("already-posted", 1), ("planned", 1), ("in-ledger", 2))
                          for _ in range(count)]}
    result = summarize_snapshot(snapshot)
    assert result["status"] == "partial" and result["counts"]["new"] == 4
    assert result["failed_queries"] == 1


def test_reclassification_uses_new_post_status(tmp_path: Path):
    product = {"id": "P03", "name": "posted", "url": "https://item.rakuten.co.jp/s/a/",
               "normalized_url": "https://item.rakuten.co.jp/s/a/", "genre": "時短・料理・調理・家電",
               "status": "needs_review", "item_code": "s/a", "model_candidate": None,
               "checked_at": None, "posted_at": None}
    (tmp_path / "products.json").write_text(json.dumps({"version": 1, "products": [product], "next_id": 4}), encoding="utf-8")
    (tmp_path / "post-status.json").write_text(json.dumps({"version": 1, "items": {"P03": {
        "product_id": "P03", "scheduled_date": None, "posted": True, "ever_marked_posted": True, "posted_at": None,
        "updated_at": "2026-09-25T13:00:00+09:00", "correction_history": []}}}), encoding="utf-8")
    (tmp_path / "posts-log.jsonl").write_text("", encoding="utf-8")
    snapshot = {"summary": {"rawResults": 1, "uniqueResults": 1, "duplicatesRemoved": 0,
                "newCandidates": 1, "alreadyPosted": 0, "planned": 0, "alreadyInLedger": 0,
                "failedQueries": 0, "succeededQueries": 1, "totalQueries": 1},
                "items": [{"itemCode": "s:a", "itemUrl": product["url"], "status": "new"}]}
    updated = reclassify_snapshot(snapshot, tmp_path)
    assert updated["summary"]["alreadyPosted"] == 1
    assert updated["items"][0]["status"] == "already-posted"
    assert not updated["items"][0]["eligible"]


def test_research_input_uses_new_inventory_counts():
    from automation.config_loader import load_config
    config = load_config()
    brief = {"target_date": "2026-09-25", "eligible_count": 0, "draft_count": 0,
             "posted_count": 1, "shortfall_count": 15, "inventory_shortfall_count": 120,
             "genre_counts": {name: 0 for name in config["genre_targets"]},
             "needs_review_count": 5, "eligible_product_ids": []}
    plan = {"genres": [{"id": "cooking", "name": "時短・料理・調理・家電",
                        "inventoryTarget": 48, "postsPerDay": 6}]}
    result = build_research_input(brief, plan)
    assert result["totals"]["shortage"] == 48
    assert result["researchQueue"][0]["id"] == "cooking"
