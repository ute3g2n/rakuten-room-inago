from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from automation.inago import product_page_inputs
from automation.inago.clock import FakeClock
from automation.inago.orchestrator import run_daily
from automation.inago.paths import SAFE_FIXTURE_BASE, resolve_runtime_paths
from automation.inago.product_page_inputs import (
    cleanup_unselected_product_page_folders,
)
from automation.inago.repository import open_database
from automation.inago.settings import register_external_settings
from tests_inago.product_page_test_support import product_dir, write_page
from tests_inago.test_product_page_selection import (
    _FixtureOCR,
    _enable_product_page_pipeline,
    _run,
    _checkpoint,
)
from tests_inago.test_step13 import (
    BASIC_SETTINGS_PATH,
    DETAIL_RULES_PATH,
    NOW,
    RuntimeCase,
    _make_ports,
)


@pytest.fixture
def runtime_case(tmp_path: Path, fixture_config_path: Path) -> RuntimeCase:
    private_root = SAFE_FIXTURE_BASE / "product-page-cleanup" / tmp_path.name
    paths = resolve_runtime_paths(
        data_root=private_root / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    register_external_settings(
        connection,
        json.loads(BASIC_SETTINGS_PATH.read_text(encoding="utf-8")),
        json.loads(DETAIL_RULES_PATH.read_text(encoding="utf-8")),
        created_at=NOW,
    )
    case = RuntimeCase(
        connection=connection,
        data_root=paths.data_root,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )
    try:
        yield case
    finally:
        connection.close()
        if private_root.resolve().is_relative_to(SAFE_FIXTURE_BASE.resolve()):
            shutil.rmtree(private_root, ignore_errors=True)


def test_rejected_folder_is_removed_and_failed_selected_folder_is_kept(
    tmp_path: Path,
) -> None:
    run_id = "PRODUCT-CLEANUP-REJECTED"
    rejected = write_page(tmp_path, run_id, "rejected-shop", "item")["page_ref"]
    selected = write_page(tmp_path, run_id, "selected-shop", "item")["page_ref"]
    rejected_dir = product_dir(tmp_path, run_id, "rejected-shop", "item")
    selected_dir = product_dir(tmp_path, run_id, "selected-shop", "item")
    (rejected_dir / "raw.json").write_text("fixture", encoding="utf-8")
    (selected_dir / "raw.json").write_text("fixture", encoding="utf-8")

    result = cleanup_unselected_product_page_folders(
        tmp_path,
        run_id,
        [
            {
                "offer_key": "rejected-shop:item",
                "shop_id": "rejected-shop",
                "item_id": "item",
            },
            {
                "offer_key": "selected-shop:item",
                "shop_id": "selected-shop",
                "item_id": "item",
            },
        ],
        {"selected-shop:item"},
    )

    assert rejected and selected
    assert result == {
        "deleted": [{"offer_key": "rejected-shop:item"}],
        "failed": [],
    }
    assert not rejected_dir.exists()
    assert selected_dir.is_dir()
    assert (selected_dir / "page.json").is_file()


def test_images_are_deleted_only_after_each_draft_commit(
    runtime_case: RuntimeCase, monkeypatch
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case, image_count=1)
    ocr = _FixtureOCR()
    original_cleanup = product_page_inputs.cleanup_committed_product_images
    cleanup_order: list[str] = []

    def verify_committed_before_cleanup(
        data_root: Path,
        run_id: str,
        offer_key: str,
        shop_id: str,
        item_id: str,
        page_ref: str,
        *,
        now,
    ):
        assert runtime_case.connection.execute(
            "SELECT 1 FROM draft WHERE run_id=? AND offer_key=? LIMIT 1",
            (run_id, offer_key),
        ).fetchone() is not None
        cleanup_order.append(offer_key)
        return original_cleanup(
            data_root,
            run_id,
            offer_key,
            shop_id,
            item_id,
            page_ref,
            now=now,
        )

    monkeypatch.setattr(
        product_page_inputs,
        "cleanup_committed_product_images",
        verify_committed_before_cleanup,
    )
    outcome = _run(runtime_case, bundle, ocr, run_id="PRODUCT-CLEANUP-AFTER-COMMIT")

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert len(cleanup_order) == 15
    state = _checkpoint(runtime_case.connection, "PRODUCT-CLEANUP-AFTER-COMMIT")
    selected = state["selected_products"]
    assert len(selected) == 15
    for entry in selected:
        folder = product_dir(
            runtime_case.data_root,
            "PRODUCT-CLEANUP-AFTER-COMMIT",
            entry["shop_id"],
            entry["item_id"],
        )
        assert (folder / "page.json").is_file()
        assert (folder / "ocr.json").is_file()
        assert (folder / "cleanup.json").is_file()
        assert not (folder / "images").exists()


def test_resume_cleans_committed_images_without_regenerating_copy(
    runtime_case: RuntimeCase, monkeypatch
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case, image_count=1)
    ocr = _FixtureOCR()
    original_cleanup = product_page_inputs.cleanup_committed_product_images
    failed_offers: set[str] = set()

    def fail_one_cleanup(
        data_root: Path,
        run_id: str,
        offer_key: str,
        shop_id: str,
        item_id: str,
        page_ref: str,
        *,
        now,
    ):
        if not failed_offers:
            failed_offers.add(offer_key)
            return {
                "status": "failed",
                "image_count": 1,
                "deleted_file_count": 0,
                "error_class": "OSError",
                "page_ref": page_ref,
            }
        return original_cleanup(
            data_root,
            run_id,
            offer_key,
            shop_id,
            item_id,
            page_ref,
            now=now,
        )

    monkeypatch.setattr(
        product_page_inputs, "cleanup_committed_product_images", fail_one_cleanup
    )
    run_id = "PRODUCT-CLEANUP-RESUME"
    first = _run(runtime_case, bundle, ocr, run_id=run_id)
    assert first.status == "partial"
    assert first.committed_count == 15
    assert first.error_count == 1
    assert len(failed_offers) == 1
    saved = _checkpoint(runtime_case.connection, run_id)
    failed_entry = next(
        item for item in saved["selected_products"] if item["offer_key"] in failed_offers
    )
    failed_dir = product_dir(
        runtime_case.data_root,
        run_id,
        failed_entry["shop_id"],
        failed_entry["item_id"],
    )
    assert (failed_dir / "images").is_dir()

    monkeypatch.setattr(
        product_page_inputs,
        "cleanup_committed_product_images",
        original_cleanup,
    )
    second = _run(runtime_case, bundle, ocr, run_id=run_id)

    assert second.status == "complete"
    assert second.resumed is True
    assert second.committed_count == 15
    assert second.error_count == 0
    assert len(ocr.calls) == 15
    assert len(bundle.runner.requests) == 15
    final_state = _checkpoint(runtime_case.connection, run_id)
    for entry in final_state["selected_products"]:
        folder = product_dir(
            runtime_case.data_root, run_id, entry["shop_id"], entry["item_id"]
        )
        assert (folder / "page.json").is_file()
        assert (folder / "ocr.json").is_file()
        assert not (folder / "images").exists()
