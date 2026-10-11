import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from automation.config_loader import load_config, load_model_policy, load_workflow
from automation.migrate_legacy_data import migrate_legacy_data
from automation.schema_validator import SCHEMA_DIR, SchemaValidationError, validate_document, validate_product_store


def test_configuration_and_all_schemas_are_valid():
    config = load_config()
    assert config["operation_start_date"] == "2026-09-25"
    assert sum(config["genre_targets"].values()) == 15
    assert load_model_policy()["astra_exception_only"]
    assert "daily" in load_workflow()["flows"]
    for path in SCHEMA_DIR.glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_schema_rejects_missing_and_extra_fields():
    valid = {"version": 1, "products": [], "next_id": 1}
    validate_document("product-ledger", valid)
    with pytest.raises(SchemaValidationError):
        validate_document("product-ledger", {"version": 1, "products": []})
    with pytest.raises(SchemaValidationError):
        validate_document("product-ledger", valid | {"unexpected": True})
    with pytest.raises(SchemaValidationError):
        validate_document("health-status", {
            "target_date": "2026-09-25", "status": "healthy", "checked_at": "2026-09-25T13:00:00Z",
            "operation_start_date": "2026-09-25", "run_id": None, "errors": []
        })


def test_duplicate_product_id_is_rejected():
    product = {
        "id": "P03", "name": "商品", "url": "https://item.rakuten.co.jp/shop/code/",
        "normalized_url": "https://item.rakuten.co.jp/shop/code/", "genre": "時短・料理・調理・家電",
        "status": "needs_review", "item_code": "shop/code", "model_candidate": None,
        "checked_at": None, "posted_at": None,
    }
    with pytest.raises(SchemaValidationError):
        validate_product_store({"version": 1, "products": [product, product], "next_id": 4})


def test_migration_is_idempotent_and_keeps_p03_posted(tmp_path: Path):
    old = tmp_path / "rakuten-room"
    old.mkdir()
    (old / "products.md").write_text(
        "## 候補一覧\n"
        "|ID|優先度|商品名|URL|税込価格・容量|刺さる悩み|確定役割|加熱スコア（①〜⑥）|加熱根拠|確認日|最終投稿日|\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
        "|P03|A|商品A|https://item.rakuten.co.jp/shop/a/|1,000円|悩み|入口|8/10|根拠|2026-09-21|2026-09-22|\n"
        "|P04|B|商品B|https://item.rakuten.co.jp/shop/b/|2,000円|悩み|関連|6/10|根拠|2026-09-21||\n"
        "|broken|line|\n", encoding="utf-8"
    )
    (old / "post-drafts.md").write_text(
        "## 商品A\n- ID：P03 ／ 役割：入口\n- 商品：https://item.rakuten.co.jp/shop/a/\n"
        "```text\n本文A\n```\n", encoding="utf-8"
    )
    (old / "posts-log.md").write_text(
        "## 直近の投稿\n"
        "|日付|商品|役割|投稿文の要旨（冒頭型）|予定/実施時刻|実施|反応メモ|判定|学び|\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "|2026-09-22|P03 商品A|入口|要旨|21:30|実施済み|未確認||保留|\n", encoding="utf-8"
    )
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    destination = tmp_path / "rakuten-room-kai"
    first = migrate_legacy_data(old, destination)
    assert first["migrated_products"] == 2
    assert first["posted_ids"] == ["P03"]
    assert any(row["reason"] == "unparsed product row" for row in first["needs_review"])
    products = json.loads((destination / "products.json").read_text(encoding="utf-8"))
    assert next(p for p in products["products"] if p["id"] == "P03")["status"] == "posted"
    snapshot = {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()}
    second = migrate_legacy_data(old, destination)
    assert second["migrated_products"] == 0
    assert second["skipped_existing"]
    assert snapshot == {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()}
    assert before == {p.name: p.read_bytes() for p in old.iterdir()}
