from __future__ import annotations

import json
from pathlib import Path

from automation.inago.orchestrator import _trace_product_stage
from automation.inago.product_page_inputs import (
    cleanup_unselected_product_page_folders,
    load_product_page_inputs,
    product_page_ref,
)
from tests_inago.product_page_test_support import product_dir, write_page


def test_shop_run_and_encoded_item_identity_do_not_collide(tmp_path: Path) -> None:
    first = write_page(tmp_path, "RUN-1", "same-shop", "same-item")
    second = write_page(tmp_path, "RUN-1", "other-shop", "same-item")
    third = write_page(tmp_path, "RUN-2", "same-shop", "same-item")

    loaded_first = load_product_page_inputs(
        tmp_path, "RUN-1", "same-shop", "same-item", expected_ref=first["page_ref"]
    )
    loaded_second = load_product_page_inputs(
        tmp_path, "RUN-1", "other-shop", "same-item", expected_ref=second["page_ref"]
    )
    loaded_third = load_product_page_inputs(
        tmp_path, "RUN-2", "same-shop", "same-item", expected_ref=third["page_ref"]
    )

    assert loaded_first["shop_id"] == "same-shop"
    assert loaded_second["shop_id"] == "other-shop"
    assert loaded_third["run_id"] == "RUN-2"
    assert len({first["page_ref"], second["page_ref"], third["page_ref"]}) == 3
    assert all(len(value["page_ref"]) <= 128 for value in (first, second, third))
    assert product_dir(tmp_path, "RUN-1", "same-shop", "same-item") != product_dir(
        tmp_path, "RUN-1", "other-shop", "same-item"
    )


def test_unselected_cleanup_uses_whole_run_keep_set(tmp_path: Path) -> None:
    selected = write_page(tmp_path, "RUN-CLEAN", "shop", "selected")
    committed = write_page(tmp_path, "RUN-CLEAN", "shop", "committed")
    rejected = write_page(tmp_path, "RUN-CLEAN", "shop", "rejected")
    other_run = write_page(tmp_path, "RUN-OTHER", "shop", "rejected")
    candidates = [
        {"offer_key": f"shop:{key}", "shop_id": "shop", "item_id": key}
        for key in ("selected", "committed", "rejected")
    ]

    result = cleanup_unselected_product_page_folders(
        tmp_path,
        "RUN-CLEAN",
        candidates,
        {"shop:selected", "shop:committed"},
    )

    assert result == {"deleted": [{"offer_key": "shop:rejected"}], "failed": []}
    assert load_product_page_inputs(
        tmp_path, "RUN-CLEAN", "shop", "selected", expected_ref=selected["page_ref"]
    ) is not None
    assert load_product_page_inputs(
        tmp_path, "RUN-CLEAN", "shop", "committed", expected_ref=committed["page_ref"]
    ) is not None
    assert not product_dir(tmp_path, "RUN-CLEAN", "shop", "rejected").exists()
    assert product_dir(tmp_path, "RUN-OTHER", "shop", "rejected").exists()
    assert other_run["run_id"] == "RUN-OTHER"
    assert not rejected["page_ref"] == other_run["page_ref"]


def test_capture_ocr_and_cleanup_errors_keep_correlated_safe_events(tmp_path: Path) -> None:
    run_id = "TRACE-PRODUCT-1"
    offer_key = "shop:item"
    page_ref = product_page_ref(run_id, "shop", "item")
    _trace_product_stage(
        tmp_path,
        run_id=run_id,
        target_date="2026-10-23",
        offer_key=offer_key,
        page_ref=page_ref,
        operation="product_ai_input",
        phase="status",
        clock=None,
        status="ready",
        image_count=2,
        input_ref_count=5,
        error_code=None,
    )
    _trace_product_stage(
        tmp_path,
        run_id=run_id,
        target_date="2026-10-23",
        offer_key=offer_key,
        page_ref=page_ref,
        operation="product_image_cleanup",
        phase="error",
        clock=None,
        status="failed",
        image_count=2,
        deleted_file_count=0,
        error_code="product_image_cleanup_failed",
        error_class="OSError",
        failure_module="automation.inago.product_page_inputs",
        failure_function="cleanup_committed_product_images",
        failure_line=10,
    )

    path = tmp_path / "logs" / "traces" / "2026-10-23" / f"{run_id}.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2
    assert all(event["component"] == "product_page" for event in events)
    assert all(event["request_id"] == page_ref for event in events)
    assert all(event["task_id"] == offer_key for event in events)
    assert events[0]["input_ref_count"] == 5
    assert events[1]["deleted_file_count"] == 0
    assert "outer_html" not in path.read_text(encoding="utf-8")
    assert "fixture product text" not in path.read_text(encoding="utf-8")
