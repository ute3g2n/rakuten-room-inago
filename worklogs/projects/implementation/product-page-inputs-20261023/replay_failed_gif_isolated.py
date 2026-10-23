from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from automation.inago import product_ocr
from automation.inago.product_page_inputs import load_product_page_inputs


RUN_ID = "INAGO-2026-10-23"
EXPECTED_SHA256 = "ad2f7249afe35992739580a20942d666de4d58eb51f999eeea2c6e47230271a4"


def _decode_segment(value: str) -> str:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def main() -> int:
    root = Path.cwd()
    runtime = root / "rakuten-room-inago" / "runtime"
    product_root = runtime / "product-page-inputs" / RUN_ID
    matches = [
        path
        for path in product_root.rglob("*")
        if path.is_file()
        and path.parent.name == "images"
        and hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED_SHA256
    ]
    if len(matches) != 1:
        print(json.dumps({"status": "error", "reason": "image_match_count"}))
        return 1

    source_image = matches[0]
    item_dir = source_image.parents[1]
    shop_dir = item_dir.parent
    shop_id = _decode_segment(shop_dir.name.removeprefix("shop-"))
    item_id = _decode_segment(item_dir.name.removeprefix("item-"))

    with tempfile.TemporaryDirectory(prefix="inago-ocr-replay-") as temp_name:
        data_root = Path(temp_name)
        target_dir = (
            data_root
            / "product-page-inputs"
            / RUN_ID
            / shop_dir.name
            / item_dir.name
        )
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(item_dir, target_dir)
        page = load_product_page_inputs(data_root, RUN_ID, shop_id, item_id)
        if not page:
            print(json.dumps({"status": "error", "reason": "isolated_inputs_missing"}))
            return 1

        worker_factory = product_ocr._PaddleWorker
        product_ocr._PaddleWorker = lambda _data_root: worker_factory(runtime)
        engine = product_ocr.ProductPageOCR()
        try:
            result = engine.process_product(
                None,
                run_id=RUN_ID,
                offer_key=f"{shop_id}:{item_id}",
                shop_id=shop_id,
                item_id=item_id,
                page_ref=page["page_ref"],
                source_evidence_ref=page["source_evidence_ref"],
                data_root=data_root,
            )
        finally:
            engine.close()

        raw = json.loads((target_dir / "ocr-raw.json").read_text(encoding="utf-8"))
        matching_raw = [
            entry
            for entry in raw["images"]
            if entry.get("source_sha256") == EXPECTED_SHA256
        ]
        if len(matching_raw) != 1:
            print(json.dumps({"status": "error", "reason": "raw_image_match_count"}))
            return 1

        image_record = matching_raw[0]
        response = image_record.get("engine_response", [])
        frames = [
            entry
            for entry in response
            if isinstance(entry, dict) and isinstance(entry.get("frame_index"), int)
        ]
        summary = {
            "status": result.get("status"),
            "error_code": result.get("error_code"),
            "source_sha256_matches": image_record.get("source_sha256") == EXPECTED_SHA256,
            "source_bytes": source_image.stat().st_size,
            "frame_count": len(frames),
            "frame_indices_complete": [entry["frame_index"] for entry in frames]
            == list(range(len(frames))),
            "ocr_text_nonempty": image_record.get("returned_text_sha256")
            != hashlib.sha256(b"").hexdigest(),
        }
        print(json.dumps(summary, ensure_ascii=False))
        return int(
            not (
                result.get("status") == "complete"
                and image_record.get("status") in {"success", "empty"}
                and len(frames) == 38
                and summary["frame_indices_complete"]
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
