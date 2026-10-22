from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPT_ROOT = Path(__file__).resolve().parent


def study_root() -> Path:
    configured = os.environ.get("OCR_STUDY_ROOT")
    root = Path(configured) if configured else Path(__file__).absolute().parent
    if root.resolve() != SCRIPT_ROOT:
        print("Refusing OCR_STUDY_ROOT outside the runner's folder.", file=sys.stderr)
        raise SystemExit(2)
    return root


ROOT = study_root()
PATH_SETTINGS = {
    "PIP_CACHE_DIR": "cache/pip",
    "TEMP": "tmp",
    "TMP": "tmp",
    "XDG_CACHE_HOME": "cache/xdg",
    "HF_HOME": "cache/huggingface",
    "PADDLE_PDX_CACHE_HOME": "models/paddlex",
}
for variable, relative_path in PATH_SETTINGS.items():
    local_path = ROOT / relative_path
    local_path.mkdir(parents=True, exist_ok=True)
    os.environ[variable] = str(local_path)

from paddleocr import PaddleOCR  # noqa: E402
from importlib.metadata import version  # noqa: E402


ENGINE_CONFIG = {
    "ocr_engine": "PaddleOCR",
    "paddleocr_version": version("paddleocr"),
    "paddlepaddle_version": version("paddlepaddle"),
    "paddlex_version": version("paddlex"),
    "ocr_version": "PP-OCRv6",
    "detection_model": "PP-OCRv6_medium_det",
    "recognition_model": "PP-OCRv6_medium_rec",
    "language": "japan",
    "device": "cpu",
    "use_doc_orientation_classify": False,
    "use_doc_unwarping": False,
    "use_textline_orientation": False,
    "enable_mkldnn": False,
    "text_det_thresh": 0.3,
    "text_det_box_thresh": 0.6,
    "text_rec_score_thresh": 0.0,
}


def now_jst() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_safe(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def exception_record(exc: BaseException) -> dict[str, str]:
    message = str(exc)
    user_profile = str(Path.home())
    if user_profile:
        message = message.replace(user_profile, "<USERPROFILE>")
    return {"class": type(exc).__name__, "message": message[:2000]}


def read_expected_hashes(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    if not path.is_file():
        return expected
    with path.open("r", encoding="utf-8-sig") as stream:
        for line in stream:
            parts = line.rstrip("\r\n").split("  ", 1)
            if len(parts) == 2:
                expected[parts[1].replace("\\", "/")] = parts[0].lower()
    return expected


def build_inputs(index_path: Path, limit: int | None) -> tuple[list[dict], dict]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    source_root = index_path.resolve().parent
    products = index.get("products", [])
    inputs: list[dict] = []
    inventory: list[dict] = []
    for product in products:
        directory = str(product["directory"])
        image_dir = (source_root / directory / "images").resolve()
        if not image_dir.is_relative_to(source_root):
            raise ValueError(f"Product image directory escapes the collection folder: {directory}")
        images = sorted(
            (path for path in image_dir.iterdir() if path.is_file()),
            key=lambda path: path.name.casefold(),
        ) if image_dir.is_dir() else []
        expected_count = int(product.get("imageCount", len(images)))
        inventory.append(
            {
                "directory": directory,
                "expected_image_count": expected_count,
                "found_image_count": len(images),
                "image_directory_exists": image_dir.is_dir(),
            }
        )
        for image in images:
            if not image.resolve().is_relative_to(source_root):
                raise ValueError(f"Product image escapes the collection folder: {image.name}")
            relative = image.relative_to(source_root).as_posix()
            inputs.append(
                {
                    "source_relative_path": relative,
                    "source_path": image,
                    "product_directory": directory,
                }
            )
    inputs.sort(key=lambda item: item["source_relative_path"].casefold())
    if limit is not None:
        inputs = inputs[:limit]
    return inputs, {
        "index_path": index_path.as_posix(),
        "product_count": len(products),
        "inventory": inventory,
        "found_image_count": sum(item["found_image_count"] for item in inventory),
        "selected_input_count": len(inputs),
    }


def engine_response(result) -> tuple[list, str]:
    pages = []
    text_lines: list[str] = []
    for page in result:
        raw = getattr(page, "json", None)
        if callable(raw):
            raw = raw()
        safe_raw = json_safe(raw if raw is not None else getattr(page, "__dict__", repr(page)))
        pages.append(safe_raw)
        payload = safe_raw.get("res", safe_raw) if isinstance(safe_raw, dict) else {}
        if isinstance(payload, dict):
            texts = payload.get("rec_texts", [])
            if isinstance(texts, list):
                text_lines.extend(str(text) for text in texts)
    return pages, "\n".join(text_lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-off OCR runner for the 59 previously collected Rakuten product images."
    )
    parser.add_argument("--index", required=True, help="Path to the existing collection index.json")
    parser.add_argument(
        "--output",
        default=str(ROOT / "results" / "ocr-results.jsonl"),
        help="JSONL output path under the study folder",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional small smoke-run limit")
    args = parser.parse_args()

    index_path = Path(args.index)
    if not index_path.is_absolute():
        index_path = (ROOT / index_path).absolute()
    expected_index_path = (SCRIPT_ROOT.parent / "index.json").resolve()
    if index_path.resolve() != expected_index_path:
        print(
            f"Refusing index outside the expected collection index path: {index_path}",
            file=sys.stderr,
        )
        return 2
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    resolved_root = ROOT.resolve()
    resolved_output = output_path.resolve()
    if not resolved_output.is_relative_to(resolved_root):
        print("Refusing to write output outside the OCR study folder.", file=sys.stderr)
        return 2
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        print(f"Refusing to overwrite existing output: {output_path}", file=sys.stderr)
        return 2

    inputs, inventory_summary = build_inputs(index_path, args.limit)
    expected_hashes = read_expected_hashes(ROOT / "input-sha256.txt")
    run_started = now_jst()
    engine = None
    initialization_error = None
    try:
        engine = PaddleOCR(
            lang=ENGINE_CONFIG["language"],
            ocr_version=ENGINE_CONFIG["ocr_version"],
            device=ENGINE_CONFIG["device"],
            use_doc_orientation_classify=ENGINE_CONFIG["use_doc_orientation_classify"],
            use_doc_unwarping=ENGINE_CONFIG["use_doc_unwarping"],
            use_textline_orientation=ENGINE_CONFIG["use_textline_orientation"],
            enable_mkldnn=ENGINE_CONFIG["enable_mkldnn"],
            text_det_thresh=ENGINE_CONFIG["text_det_thresh"],
            text_det_box_thresh=ENGINE_CONFIG["text_det_box_thresh"],
            text_rec_score_thresh=ENGINE_CONFIG["text_rec_score_thresh"],
        )
    except Exception as exc:
        initialization_error = exception_record(exc)

    counts = {"success": 0, "empty": 0, "error": 0}
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        for ordinal, item in enumerate(inputs, start=1):
            image_path: Path = item["source_path"]
            record_started = now_jst()
            start_clock = time.perf_counter()
            before_hash = None
            after_hash = None
            raw_response = []
            returned_text = ""
            error = initialization_error
            if image_path.is_file():
                try:
                    before_hash = sha256(image_path)
                except Exception as exc:
                    error = exception_record(exc)
            else:
                error = {"class": "FileNotFoundError", "message": "Input image is missing."}

            expected_hash = expected_hashes.get(item["source_relative_path"])
            if error is None and engine is not None:
                try:
                    raw_response, returned_text = engine_response(engine.predict(str(image_path)))
                except Exception as exc:
                    error = exception_record(exc)

            if image_path.is_file():
                try:
                    after_hash = sha256(image_path)
                except Exception:
                    after_hash = None

            if error is not None:
                status = "error"
            elif returned_text.strip():
                status = "success"
            else:
                status = "empty"
            counts[status] += 1
            record = {
                "record_id": f"IMG-{ordinal:03d}",
                "source_relative_path": item["source_relative_path"],
                "product_directory": item["product_directory"],
                "sha256_before": before_hash,
                "sha256_expected": expected_hash,
                "sha256_after": after_hash,
                "hash_matches_manifest_before": (
                    before_hash == expected_hash if before_hash is not None and expected_hash else None
                ),
                "hash_unchanged_during_ocr": (
                    before_hash == after_hash if before_hash is not None and after_hash is not None else None
                ),
                "status": status,
                "engine": ENGINE_CONFIG,
                "started_at_jst": record_started,
                "elapsed_seconds": round(time.perf_counter() - start_clock, 3),
                "returned_text": returned_text,
                "engine_response": raw_response,
                "error": error,
            }
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            print(
                json.dumps(
                    {
                        "record_id": record["record_id"],
                        "status": status,
                        "path": item["source_relative_path"],
                        "elapsed_seconds": record["elapsed_seconds"],
                    },
                    ensure_ascii=False,
                )
            )

    run_summary = {
        "started_at_jst": run_started,
        "finished_at_jst": now_jst(),
        "engine": ENGINE_CONFIG,
        "inventory": inventory_summary,
        "counts": counts,
        "output": output_path.as_posix(),
        "return_code": 0,
    }
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(run_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(run_summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
