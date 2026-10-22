"""Convert OCR JSONL records into the proposed per-product input JSON format."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


STUDY_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = STUDY_ROOT / "results" / "ocr-results.jsonl"
DEFAULT_OUTPUT = STUDY_ROOT / "converted"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALLOWED_STATUSES = {"success", "empty", "error"}


def _inside_study(path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(STUDY_ROOT)
    except ValueError as exc:
        raise ValueError(f"{label} must be inside {STUDY_ROOT}") from exc
    return resolved


def _load_records(source: Path) -> list[dict[str, Any]]:
    source = _inside_study(source, "input file")
    if not source.is_file():
        raise FileNotFoundError(f"OCR JSONL not found: {source}")

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()

    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"blank JSONL line at {line_number}")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number} must contain a JSON object")

            record_id = record.get("record_id")
            product = record.get("product_directory")
            source_path = record.get("source_relative_path")
            status = record.get("status")
            text = record.get("returned_text")
            if not isinstance(record_id, str) or not SAFE_NAME.fullmatch(record_id):
                raise ValueError(f"invalid record_id at line {line_number}")
            if not isinstance(product, str) or not SAFE_NAME.fullmatch(product):
                raise ValueError(f"invalid product_directory at line {line_number}")
            if not isinstance(source_path, str):
                raise ValueError(f"missing source_relative_path at line {line_number}")
            parsed_path = PurePosixPath(source_path.replace("\\", "/"))
            if (
                parsed_path.is_absolute()
                or ".." in parsed_path.parts
                or len(parsed_path.parts) < 3
                or parsed_path.parts[0] != product
                or parsed_path.parts[1] != "images"
            ):
                raise ValueError(f"unsafe source_relative_path at line {line_number}")
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"invalid status at line {line_number}: {status!r}")
            if not isinstance(text, str):
                raise ValueError(f"returned_text must be a string at line {line_number}")
            if record_id in seen_ids:
                raise ValueError(f"duplicate record_id: {record_id}")
            if source_path in seen_paths:
                raise ValueError(f"duplicate source_relative_path: {source_path}")

            seen_ids.add(record_id)
            seen_paths.add(source_path)
            records.append(record)

    if not records:
        raise ValueError("OCR JSONL contains no records")
    return records


def _product_payload(product: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    images: list[dict[str, str]] = []
    input_refs: list[str] = []
    evidence_refs: list[str] = []

    for record in records:
        ref = f"ocr-{product}-{record['record_id']}"
        image = {
            "ref": ref,
            "status": record["status"],
            "text": record["returned_text"],
        }
        images.append(image)
        input_refs.append(ref)
        if record["status"] == "success" and record["returned_text"].strip():
            evidence_refs.append(ref)

    return {
        "product_directory": product,
        "input_refs": input_refs,
        "evidence_refs": evidence_refs,
        "input_payload": {
            "product_page_ocr": {
                "schema_version": "1",
                "source_collected_at": None,
                "images": images,
            }
        },
    }


def _verify_written(
    records: list[dict[str, Any]], output_dir: Path, expected_products: list[str]
) -> dict[str, int]:
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_product[record["product_directory"]].append(record)

    actual_files = {path.name for path in output_dir.glob("*.json") if path.is_file()}
    expected_files = {f"{product}.json" for product in expected_products}
    if actual_files != expected_files:
        raise ValueError(
            "converted JSON file set mismatch: "
            f"expected {sorted(expected_files)}, got {sorted(actual_files)}"
        )

    counts: dict[str, int] = {}
    for product in expected_products:
        converted_path = output_dir / f"{product}.json"
        converted = json.loads(converted_path.read_text(encoding="utf-8"))
        expected = _product_payload(product, by_product[product])
        if converted != expected:
            raise ValueError(f"converted content mismatch: {converted_path.name}")
        counts[product] = len(expected["input_payload"]["product_page_ocr"]["images"])
    return counts


def convert(source: Path, output_dir: Path, force: bool) -> dict[str, int]:
    source = _inside_study(source, "input file")
    output_dir = _inside_study(output_dir, "output directory")
    if output_dir == STUDY_ROOT or output_dir == source.parent:
        raise ValueError("output directory must be a dedicated directory inside the study")

    records = _load_records(source)
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_product[record["product_directory"]].append(record)
    products = list(by_product)

    output_dir.mkdir(parents=True, exist_ok=True)
    destinations = [output_dir / f"{product}.json" for product in products]
    existing = [path for path in destinations if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "output exists; pass --force to replace generated product files: "
            + ", ".join(path.name for path in existing)
        )

    unexpected = {
        path.name
        for path in output_dir.glob("*.json")
        if path.is_file() and path not in destinations
    }
    if unexpected:
        raise ValueError(f"unexpected JSON files in output directory: {sorted(unexpected)}")

    for product, destination in zip(products, destinations):
        payload = _product_payload(product, by_product[product])
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8", newline="\n")
        temporary.replace(destination)

    return _verify_written(records, output_dir, products)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert OCR JSONL records into the proposed per-product JSON format."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace existing product JSON files generated by this converter",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        counts = convert(args.input, args.output_dir, args.force)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {"verified": True, "products": counts, "total_images": sum(counts.values())},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
