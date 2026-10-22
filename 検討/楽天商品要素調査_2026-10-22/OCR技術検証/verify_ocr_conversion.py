"""Independently compare converted product JSON files with the source OCR JSONL."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STUDY_ROOT = Path(__file__).resolve().parent
SOURCE = STUDY_ROOT / "results" / "ocr-results.jsonl"
CONVERTED_DIR = STUDY_ROOT / "converted"
REPORT = STUDY_ROOT / "conversion-verification.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_source() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with SOURCE.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"blank source JSONL line at {line_number}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"source line {line_number} is not a JSON object")
            records.append(value)
    return records


def verify() -> dict[str, Any]:
    source_records = _read_source()
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in source_records:
        by_product[record["product_directory"]].append(record)

    products: list[dict[str, Any]] = []
    all_expected_refs: list[str] = []
    all_converted_refs: list[str] = []
    all_texts_exact = True
    all_refs_exact = True
    all_statuses_exact = True
    all_payload_refs_exact = True
    output_record_count = 0

    expected_filenames = {f"{product}.json" for product in by_product}
    actual_filenames = {
        path.name for path in CONVERTED_DIR.glob("*.json") if path.is_file()
    }
    file_set_exact = actual_filenames == expected_filenames

    for product, source_rows in by_product.items():
        path = CONVERTED_DIR / f"{product}.json"
        if not path.is_file():
            raise FileNotFoundError(f"converted JSON missing: {path.name}")
        converted = json.loads(path.read_text(encoding="utf-8"))
        payload = converted["input_payload"]["product_page_ocr"]
        images = payload["images"]
        if not isinstance(images, list):
            raise ValueError(f"images must be an array in {path.name}")

        expected_refs = [
            f"ocr-{product}-{row['record_id']}" for row in source_rows
        ]
        converted_refs = [image.get("ref") for image in images]
        expected_statuses = [row["status"] for row in source_rows]
        converted_statuses = [image.get("status") for image in images]
        texts_exact = len(images) == len(source_rows) and all(
            image.get("text") == row["returned_text"]
            for image, row in zip(images, source_rows)
        )
        refs_exact = converted_refs == expected_refs
        statuses_exact = converted_statuses == expected_statuses
        payload_refs_exact = (
            converted.get("product_directory") == product
            and converted.get("input_refs") == expected_refs
            and converted.get("evidence_refs")
            == [
                f"ocr-{product}-{row['record_id']}"
                for row in source_rows
                if row["status"] == "success" and row["returned_text"].strip()
            ]
        )
        if payload.get("schema_version") != "1":
            payload_refs_exact = False
        if payload.get("source_collected_at") is not None:
            payload_refs_exact = False

        all_texts_exact = all_texts_exact and texts_exact
        all_refs_exact = all_refs_exact and refs_exact
        all_statuses_exact = all_statuses_exact and statuses_exact
        all_payload_refs_exact = all_payload_refs_exact and payload_refs_exact
        all_expected_refs.extend(expected_refs)
        all_converted_refs.extend(converted_refs)
        output_record_count += len(images)

        source_status_counts = dict(sorted(Counter(expected_statuses).items()))
        converted_status_counts = dict(sorted(Counter(converted_statuses).items()))
        products.append(
            {
                "product_directory": product,
                "source_records": len(source_rows),
                "converted_records": len(images),
                "source_returned_text_characters": sum(
                    len(row["returned_text"]) for row in source_rows
                ),
                "converted_text_characters": sum(
                    len(image.get("text", ""))
                    for image in images
                    if isinstance(image.get("text", ""), str)
                ),
                "source_status_counts": source_status_counts,
                "converted_status_counts": converted_status_counts,
                "record_refs_match_in_order": refs_exact,
                "statuses_match_in_order": statuses_exact,
                "every_returned_text_matches_exactly": texts_exact,
                "payload_refs_and_schema_match": payload_refs_exact,
                "converted_file_sha256": _sha256(path),
            }
        )

    unique_refs = len(set(all_converted_refs)) == len(all_converted_refs)
    all_source_records_once = (
        len(source_records) == output_record_count
        and len(all_expected_refs) == len(all_converted_refs)
        and sorted(all_expected_refs) == sorted(all_converted_refs)
        and unique_refs
    )
    passed = all(
        [
            file_set_exact,
            all_source_records_once,
            all_refs_exact,
            all_statuses_exact,
            all_texts_exact,
            all_payload_refs_exact,
        ]
    )
    return {
        "verification_version": "1",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_command": ".\\.venv-ocr\\Scripts\\python.exe .\\verify_ocr_conversion.py",
        "source_file": str(SOURCE.relative_to(STUDY_ROOT)).replace("\\", "/"),
        "source_file_sha256": _sha256(SOURCE),
        "converted_directory": str(CONVERTED_DIR.relative_to(STUDY_ROOT)).replace(
            "\\", "/"
        ),
        "source_records": len(source_records),
        "converted_records": output_record_count,
        "product_count": len(products),
        "checks": {
            "exactly_expected_product_files": file_set_exact,
            "every_source_record_present_once": all_source_records_once,
            "record_refs_match_in_source_order": all_refs_exact,
            "statuses_match_in_source_order": all_statuses_exact,
            "every_returned_text_matches_exactly": all_texts_exact,
            "payload_refs_and_schema_match": all_payload_refs_exact,
        },
        "passed": passed,
        "products": products,
    }


def main() -> int:
    try:
        report = verify()
        REPORT.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
