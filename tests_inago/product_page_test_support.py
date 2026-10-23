from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any


def _segment(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _run_segment(value: str) -> str:
    if value and all(char.isalnum() or char in "._-" for char in value):
        return value
    return f"encoded-{_segment(value)}"


def product_page_ref(run_id: str, shop_id: str, item_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{shop_id}\0{item_id}".encode()).hexdigest()[:40]
    return f"ppi:{digest}"


def product_ocr_ref(run_id: str, offer_key: str, shop_id: str, item_id: str) -> str:
    digest = hashlib.sha256(
        f"{run_id}\0{offer_key}\0{shop_id}\0{item_id}".encode()
    ).hexdigest()[:40]
    return f"ppocr:{digest}"


def product_dir(data_root: Path, run_id: str, shop_id: str, item_id: str) -> Path:
    return (
        data_root
        / "product-page-inputs"
        / _run_segment(run_id)
        / f"shop-{_segment(shop_id)}"
        / f"item-{_segment(item_id)}"
    )


def write_page(
    data_root: Path,
    run_id: str,
    shop_id: str,
    item_id: str,
    *,
    selectors: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
    captured_at: str = "2026-10-23T13:00:00+09:00",
    source_evidence_ref: str = "fixture:evidence:01",
) -> dict[str, Any]:
    folder = product_dir(data_root, run_id, shop_id, item_id)
    folder.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1,
        "page_ref": product_page_ref(run_id, shop_id, item_id),
        "run_id": run_id,
        "shop_id": shop_id,
        "item_id": item_id,
        "item_url": f"https://item.rakuten.co.jp/{shop_id}/{item_id}/",
        "source_evidence_ref": source_evidence_ref,
        "captured_at": captured_at,
        "html_state": "captured",
        "selectors": selectors
        if selectors is not None
        else [
            {
                "key": "sale_desc",
                "selector": "span.sale_desc",
                "status": "captured",
                "outer_html": ["<span class=\"sale_desc\">fixture product text</span>"],
            }
        ],
        "images": images or [],
        "image_state": "captured" if images else "no_images",
    }
    (folder / "page.json").write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8"
    )
    return value


def write_ocr(
    data_root: Path,
    page: dict[str, Any],
    *,
    images: list[dict[str, str]],
) -> tuple[dict[str, Any], str]:
    run_id = page["run_id"]
    shop_id = page["shop_id"]
    item_id = page["item_id"]
    offer_key = f"{shop_id}:{item_id}"
    digest = hashlib.sha256(
        f"{run_id}\0{offer_key}\0{shop_id}\0{item_id}".encode()
    ).hexdigest()[:40]
    ocr_ref = f"ppocr:{digest}"
    raw_ref = f"ppocrraw:{digest}"
    value = {
        "schema_version": 1,
        "run_id": run_id,
        "offer_key": offer_key,
        "shop_id": shop_id,
        "item_id": item_id,
        "page_ref": page["page_ref"],
        "raw_ref": raw_ref,
        "input_refs": [page["page_ref"], *[image["ref"] for image in images]],
        "evidence_refs": [
            image["ref"]
            for image in images
            if image["status"] == "success" and image["text"].strip()
        ],
        "input_payload": {
            "product_page_ocr": {
                "schema_version": 1,
                "source_collected_at": page["captured_at"],
                "images": images,
            }
        },
    }
    path = product_dir(data_root, run_id, shop_id, item_id) / "ocr.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value, ocr_ref
