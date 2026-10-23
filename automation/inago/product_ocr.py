"""Product-page OCR backed by an isolated, run-scoped PaddleOCR worker."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
from typing import Any, Iterator, Mapping

from automation.inago.product_page_inputs import load_product_page_inputs


_ENGINE_CONFIG = {
    "ocr_engine": "PaddleOCR",
    "paddleocr_version": "3.7.0",
    "paddlepaddle_version": "3.3.1",
    "paddlex_version": "3.7.2",
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
_SAFE_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_OCR_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_OCR_IMAGE_FRAMES = 120
_MAX_OCR_IMAGE_PIXELS = 40_000_000
_IMAGE_ERROR_CODES = frozenset(
    {
        "ocr_image_byte_limit_exceeded",
        "ocr_image_decode_failed",
        "ocr_image_frame_limit_exceeded",
        "ocr_image_pixel_limit_exceeded",
    }
)


def _segment(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _run_segment(value: str) -> str:
    reserved = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I)
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) and not reserved.fullmatch(value) and not value.endswith("."):
        return value
    return f"encoded-{_segment(value)}"


def load_product_page_ocr(
    data_root: Path,
    run_id: str,
    offer_key: str,
    shop_id: str,
    item_id: str,
    *,
    expected_page_ref: str,
    expected_ocr_ref: str,
) -> dict[str, Any] | None:
    """Load the public OCR projection for one exact selected product."""

    if (
        not _SAFE_RUN.fullmatch(run_id)
        or not _SAFE_ID.fullmatch(shop_id)
        or not _SAFE_ID.fullmatch(item_id)
        or not isinstance(offer_key, str)
        or offer_key != f"{shop_id}:{item_id}"
    ):
        raise ValueError("product OCR identity is invalid")
    identity_digest = hashlib.sha256(
        f"{run_id}\0{offer_key}\0{shop_id}\0{item_id}".encode("utf-8")
    ).hexdigest()[:40]
    expected_ref = f"ppocr:{identity_digest}"
    if expected_ocr_ref != expected_ref:
        raise ValueError("product OCR reference differs")
    root = Path(data_root).resolve()
    product_root = root / "product-page-inputs"
    product_dir = (
        product_root / _run_segment(run_id)
        / f"shop-{_segment(shop_id)}" / f"item-{_segment(item_id)}"
    )
    path = product_dir / "ocr.json"
    if not path.is_file():
        return None
    for candidate in (product_root, product_dir.parents[1], product_dir.parent, product_dir, path):
        metadata = candidate.lstat()
        if candidate.is_symlink() or getattr(metadata, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise ValueError("product OCR capture contains a reparse point")
    if not path.resolve().is_relative_to(product_root.resolve()):
        raise ValueError("product OCR capture escapes data root")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("run_id") != run_id
        or value.get("offer_key") != offer_key
        or value.get("shop_id") != shop_id
        or value.get("item_id") != item_id
        or value.get("page_ref") != expected_page_ref
        or value.get("raw_ref") != f"ppocrraw:{identity_digest}"
    ):
        raise ValueError("product OCR capture identity differs")
    payload = value.get("input_payload")
    product_ocr = payload.get("product_page_ocr") if isinstance(payload, dict) else None
    images = product_ocr.get("images") if isinstance(product_ocr, dict) else None
    if (
        not isinstance(product_ocr, dict)
        or product_ocr.get("schema_version") != 1
        or not isinstance(images, list)
    ):
        raise ValueError("product OCR payload is invalid")
    for image in images:
        if (
            not isinstance(image, dict)
            or not isinstance(image.get("ref"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", image["ref"])
            or not isinstance(image.get("status"), str)
            or image["status"] not in {"success", "empty", "error"}
            or not isinstance(image.get("text"), str)
        ):
            raise ValueError("product OCR image record is invalid")
    return value


def _json_safe(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _engine_response(result: Any) -> tuple[list[Any], str]:
    raw_pages: list[Any] = []
    text_lines: list[str] = []
    for page in result:
        raw = getattr(page, "json", None)
        if callable(raw):
            raw = raw()
        safe_raw = _json_safe(
            raw if raw is not None else getattr(page, "__dict__", repr(page))
        )
        raw_pages.append(safe_raw)
        payload = safe_raw.get("res", safe_raw) if isinstance(safe_raw, dict) else {}
        if isinstance(payload, dict):
            texts = payload.get("rec_texts", [])
            if isinstance(texts, list):
                text_lines.extend(str(text) for text in texts)
    return raw_pages, "\n".join(text_lines)


def _decode_with_pillow(
    image_bytes: bytes, cv2: Any, numpy: Any
) -> Iterator[Any]:
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as source:
            _canvas_width, _canvas_height, expected_frame_count = _validate_image_metadata(
                source.size, getattr(source, "n_frames", 1)
            )
            frame_count = 0
            total_pixels = 0
            for frame_index in range(expected_frame_count):
                if frame_count >= _MAX_OCR_IMAGE_FRAMES:
                    raise ValueError("ocr_image_frame_limit_exceeded")
                source.seek(frame_index)
                width, height = source.size
                frame_pixels = width * height
                if (
                    width <= 0
                    or height <= 0
                    or frame_pixels > _MAX_OCR_IMAGE_PIXELS
                    or total_pixels + frame_pixels > _MAX_OCR_IMAGE_PIXELS
                ):
                    raise ValueError("ocr_image_pixel_limit_exceeded")
                converted = source.convert("RGB")
                yield cv2.cvtColor(
                    numpy.asarray(converted), cv2.COLOR_RGB2BGR
                )
                frame_count += 1
                total_pixels += frame_pixels
            if frame_count == 0:
                raise ValueError("ocr_image_decode_failed")
    except ValueError as exc:
        if str(exc) in _IMAGE_ERROR_CODES:
            raise
        raise ValueError("ocr_image_decode_failed") from exc
    except Exception as exc:
        raise ValueError("ocr_image_decode_failed") from exc


def _validate_image_metadata(size: Any, frame_count: Any) -> tuple[int, int, int]:
    try:
        width, height = size
        frame_total = int(frame_count)
    except (TypeError, ValueError) as exc:
        raise ValueError("ocr_image_decode_failed") from exc
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or frame_total <= 0
    ):
        raise ValueError("ocr_image_decode_failed")
    if frame_total > _MAX_OCR_IMAGE_FRAMES:
        raise ValueError("ocr_image_frame_limit_exceeded")
    if width * height * frame_total > _MAX_OCR_IMAGE_PIXELS:
        raise ValueError("ocr_image_pixel_limit_exceeded")
    return width, height, frame_total


def _inspect_image_header(image_bytes: bytes) -> tuple[int, int, int]:
    if len(image_bytes) > _MAX_OCR_IMAGE_BYTES:
        raise ValueError("ocr_image_byte_limit_exceeded")
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as source:
            return _validate_image_metadata(
                source.size, getattr(source, "n_frames", 1)
            )
    except ValueError as exc:
        if str(exc) in _IMAGE_ERROR_CODES:
            raise
        raise ValueError("ocr_image_decode_failed") from exc
    except Exception as exc:
        if type(exc).__name__.startswith("DecompressionBomb"):
            raise ValueError("ocr_image_pixel_limit_exceeded") from exc
        raise ValueError("ocr_image_decode_failed") from exc


def _decode_image_frames(
    image_bytes: bytes, cv2: Any, numpy: Any
) -> Iterator[Any]:
    """Decode still images and preserve every frame in animated GIFs."""
    _width, _height, frame_count = _inspect_image_header(image_bytes)
    if frame_count > 1 or image_bytes.startswith((b"GIF87a", b"GIF89a")):
        yield from _decode_with_pillow(image_bytes, cv2, numpy)
        return

    try:
        image = cv2.imdecode(
            numpy.frombuffer(image_bytes, dtype=numpy.uint8), cv2.IMREAD_COLOR
        )
    except Exception:
        image = None
    if image is not None:
        yield image
        return
    yield from _decode_with_pillow(image_bytes, cv2, numpy)


def _predict_image_frames(
    engine: Any, frames: Iterator[Any]
) -> tuple[list[Any], str]:
    """OCR all decoded frames and keep frame boundaries in the raw evidence."""
    frame_results: list[dict[str, Any]] = []
    frame_texts: list[str] = []
    for frame_index, frame in enumerate(frames):
        raw_pages, text = _engine_response(engine.predict(frame))
        frame_results.append(
            {"frame_index": frame_index, "engine_response": raw_pages}
        )
        if text.strip():
            frame_texts.append(text)
    if not frame_results:
        raise ValueError("ocr_image_decode_failed")
    if len(frame_results) == 1:
        return frame_results[0]["engine_response"], frame_texts[0] if frame_texts else ""
    return frame_results, "\n\n".join(frame_texts)


def _worker_error_code(exc: Exception) -> str:
    error_code = str(exc)
    return error_code if error_code in _IMAGE_ERROR_CODES else "ocr_inference_failed"


@contextmanager
def _redirect_native_stdout_to_stderr():
    """Keep native OCR library output out of the worker's JSON-line protocol."""

    sys.stdout.flush()
    saved_stdout = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved_stdout, 1)
        os.close(saved_stdout)


class _PaddleWorker:
    """One long-lived isolated interpreter and model initialization per run."""

    def __init__(self, data_root: Path) -> None:
        asset_root = data_root / "ocr-assets"
        python = asset_root / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            raise RuntimeError("ocr_runtime_not_installed")
        asset_root.mkdir(parents=True, exist_ok=True)
        for relative in ("cache/pip", "cache/xdg", "cache/huggingface", "models/paddlex", "tmp"):
            (asset_root / relative).mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        paths = {
            "PIP_CACHE_DIR": asset_root / "cache" / "pip",
            "TEMP": asset_root / "tmp",
            "TMP": asset_root / "tmp",
            "XDG_CACHE_HOME": asset_root / "cache" / "xdg",
            "HF_HOME": asset_root / "cache" / "huggingface",
            "PADDLE_PDX_CACHE_HOME": asset_root / "models" / "paddlex",
        }
        env.update({name: str(path) for name, path in paths.items()})
        env["PYTHONIOENCODING"] = "utf-8"
        self.process = subprocess.Popen(
            [str(python), "-B", "-m", "automation.inago.product_ocr", "--worker"],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
        )
        assert self.process.stdout is not None
        import queue
        import threading

        ready_line: queue.Queue[str | None] = queue.Queue(maxsize=1)
        threading.Thread(
            target=lambda: ready_line.put(self.process.stdout.readline()),
            name="inago-ocr-worker-ready",
            daemon=True,
        ).start()
        try:
            line = ready_line.get(timeout=600)
            if line is None or not line:
                raise RuntimeError("ocr_worker_start_failed")
            ready = json.loads(line)
        except (json.JSONDecodeError, TypeError, queue.Empty, RuntimeError) as exc:
            self.close()
            raise RuntimeError("ocr_worker_start_failed") from exc
        if ready.get("ready") is not True:
            self.close()
            raise RuntimeError(str(ready.get("error_code") or "ocr_model_initialization_failed"))

    def predict(self, image_bytes: bytes) -> tuple[list[Any], str]:
        if self.process.poll() is not None or self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("ocr_worker_exited")
        request = {"image_base64": base64.b64encode(image_bytes).decode("ascii")}
        try:
            self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            response = json.loads(self.process.stdout.readline())
        except (BrokenPipeError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("ocr_worker_protocol_error") from exc
        if response.get("error_code"):
            raise RuntimeError(str(response["error_code"]))
        raw = response.get("engine_response")
        text = response.get("returned_text")
        if not isinstance(raw, list) or not isinstance(text, str):
            raise RuntimeError("ocr_worker_result_invalid")
        return raw, text

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write('{"close":true}\n')
                process.stdin.flush()
                process.stdin.close()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=5)


class ProductPageOCR:
    """Create per-product OCR JSON and expose a same-run product reference."""

    def __init__(self) -> None:
        self._run_id: str | None = None
        self._worker: _PaddleWorker | None = None
        self._worker_error: str | None = None
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            if self._worker is not None:
                self._worker.close()
            self._worker = None
            self._worker_error = None
            self._run_id = None

    def _worker_for(self, run_id: str, data_root: Path) -> _PaddleWorker:
        if self._run_id != run_id:
            self.close()
            self._run_id = run_id
            try:
                self._worker = _PaddleWorker(data_root)
            except Exception as exc:
                self._worker_error = (
                    str(exc)
                    if str(exc).startswith("ocr_")
                    else "ocr_model_initialization_failed"
                )
                raise RuntimeError(self._worker_error) from exc
        if self._worker_error:
            raise RuntimeError(self._worker_error)
        if self._worker is None:
            self._worker = _PaddleWorker(data_root)
            self._run_id = run_id
        return self._worker

    def process_product(
        self,
        connection: Any,
        *,
        run_id: str,
        offer_key: str,
        shop_id: str,
        item_id: str,
        page_ref: str | None,
        source_evidence_ref: str | None,
        data_root: Path,
    ) -> Mapping[str, Any]:
        del connection, source_evidence_ref
        if (
            not _SAFE_RUN.fullmatch(run_id)
            or not _SAFE_ID.fullmatch(shop_id)
            or not _SAFE_ID.fullmatch(item_id)
        ):
            return {"status": "failed", "images": [], "error_code": "product_identity_invalid"}
        root = Path(data_root).resolve()
        try:
            page = load_product_page_inputs(root, run_id, shop_id, item_id, expected_ref=page_ref)
            if page is None:
                return {"status": "failed", "images": [], "error_code": "product_page_input_missing"}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"status": "failed", "images": [], "error_code": "product_page_input_invalid"}

        product_dir = (
            root / "product-page-inputs" / _run_segment(run_id)
            / f"shop-{_segment(shop_id)}" / f"item-{_segment(item_id)}"
        )
        ocr_file = product_dir / "ocr.json"
        raw_file = product_dir / "ocr-raw.json"
        identity_digest = hashlib.sha256(
            f"{run_id}\0{offer_key}\0{shop_id}\0{item_id}".encode("utf-8")
        ).hexdigest()[:40]
        ocr_ref = f"ppocr:{identity_digest}"
        raw_ref = f"ppocrraw:{identity_digest}"
        images = page.get("images")
        if not isinstance(images, list):
            return {"status": "failed", "images": [], "error_code": "product_image_manifest_invalid"}
        if not product_dir.resolve().is_relative_to((root / "product-page-inputs").resolve()):
            return {"status": "failed", "images": [], "error_code": "product_path_invalid"}

        existing_payload: dict[str, Any] = {}
        existing_raw: dict[str, Any] = {}
        try:
            old = json.loads(ocr_file.read_text(encoding="utf-8"))
            old_images = (
                old.get("input_payload", {})
                .get("product_page_ocr", {})
                .get("images")
            )
            if all(old.get(key) == value for key, value in (
                ("run_id", run_id), ("offer_key", offer_key), ("shop_id", shop_id),
                ("item_id", item_id), ("page_ref", page.get("page_ref")),
            )) and old.get("raw_ref") == raw_ref and isinstance(old_images, list):
                existing_payload = {
                    entry.get("ref"): entry
                    for entry in old_images
                    if isinstance(entry, dict)
                }
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        try:
            old_raw = json.loads(raw_file.read_text(encoding="utf-8"))
            old_raw_images = old_raw.get("images")
            if all(old_raw.get(key) == value for key, value in (
                ("run_id", run_id), ("offer_key", offer_key), ("shop_id", shop_id),
                ("item_id", item_id), ("page_ref", page.get("page_ref")),
                ("raw_ref", raw_ref), ("engine", _ENGINE_CONFIG),
            )) and isinstance(old_raw_images, list):
                existing_raw = {
                    entry.get("ref"): entry
                    for entry in old_raw_images
                    if isinstance(entry, dict)
                }
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

        image_results: list[dict[str, Any]] = []
        payload_images: list[dict[str, Any]] = []
        raw_images: list[dict[str, Any]] = []
        first_error: str | None = None
        for order, item in enumerate(images):
            source_digest = hashlib.sha256(
                f"{page.get('page_ref')}\0{order}".encode("utf-8")
            ).hexdigest()[:32]
            source_ref = f"ppiimg:{source_digest}"
            image_ref = f"ocrimg:{hashlib.sha256(source_ref.encode()).hexdigest()[:32]}"
            record: dict[str, Any] = {
                "ref": image_ref,
                "status": "error",
                "text": "",
            }
            raw_record: dict[str, Any] = {
                "order": order,
                "ref": image_ref,
                "source_ref": source_ref,
                "status": "error",
                "source_sha256": None,
                "returned_text_sha256": None,
                "engine": dict(_ENGINE_CONFIG),
                "engine_response": [],
            }
            if (
                not isinstance(item, dict)
                or item.get("status") != "downloaded"
                or not isinstance(item.get("file"), str)
            ):
                raw_record["error_code"] = "image_unavailable"
                first_error = first_error or "image_unavailable"
            else:
                relative = Path(item["file"])
                image_path = product_dir / relative
                try:
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or not image_path.resolve().is_relative_to(product_dir.resolve())
                    ):
                        raise ValueError("image_path_invalid")
                    metadata = image_path.lstat()
                    if image_path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
                        raise ValueError("image_path_invalid")
                    if metadata.st_size > _MAX_OCR_IMAGE_BYTES:
                        raise ValueError("ocr_image_byte_limit_exceeded")
                    raw_bytes = image_path.read_bytes()
                    source_hash = hashlib.sha256(raw_bytes).hexdigest()
                    raw_record["source_sha256"] = source_hash
                    prior_payload = existing_payload.get(image_ref)
                    prior_raw = existing_raw.get(image_ref)
                    if (
                        prior_payload
                        and prior_raw
                        and prior_raw.get("source_sha256") == source_hash
                        and prior_raw.get("status") in {"success", "empty"}
                        and prior_payload.get("status") == prior_raw.get("status")
                        and isinstance(prior_payload.get("text"), str)
                    ):
                        record["status"] = prior_payload["status"]
                        record["text"] = prior_payload["text"]
                        raw_record.update(prior_raw)
                    else:
                        with self._lock:
                            try:
                                raw_response, text = self._worker_for(run_id, root).predict(raw_bytes)
                                record["text"] = text
                                record["status"] = "success" if text.strip() else "empty"
                                raw_record["engine_response"] = raw_response
                                raw_record["status"] = record["status"]
                                raw_record["returned_text_sha256"] = hashlib.sha256(
                                    text.encode("utf-8")
                                ).hexdigest()
                            except Exception as exc:
                                error_code = (
                                    str(exc)
                                    if str(exc).startswith("ocr_")
                                    else "ocr_inference_failed"
                                )
                                raw_record["error_code"] = error_code
                                first_error = first_error or error_code
                except (OSError, ValueError) as exc:
                    code = (
                        str(exc)
                        if str(exc) == "image_path_invalid" or str(exc) in _IMAGE_ERROR_CODES
                        else "image_read_failed"
                    )
                    raw_record["error_code"] = code
                    first_error = first_error or code
            raw_record["returned_text_sha256"] = hashlib.sha256(
                record["text"].encode("utf-8")
            ).hexdigest()
            raw_record["status"] = record["status"]
            image_results.append(
                {
                    "order": order,
                    "status": record["status"],
                    "image_ref": image_ref,
                    "source_ref": source_ref,
                    "ocr_ref": image_ref,
                    **(
                        {"error_code": raw_record["error_code"]}
                        if "error_code" in raw_record
                        else {}
                    ),
                }
            )
            payload_images.append(record)
            raw_images.append(raw_record)
            self._write_json(
                raw_file,
                self._raw_document(
                    run_id, offer_key, shop_id, item_id, page,
                    raw_ref, raw_images,
                ),
            )
            self._write_json(
                ocr_file,
                self._document(
                    run_id, offer_key, shop_id, item_id, page,
                    raw_ref, payload_images,
                ),
            )

        statuses = [record["status"] for record in payload_images]
        if not statuses:
            status = "complete"
        elif all(value == "error" for value in statuses):
            status = "failed"
        elif any(value == "error" for value in statuses):
            status = "partial"
        else:
            status = "complete"
        self._write_json(
            raw_file,
            self._raw_document(
                run_id, offer_key, shop_id, item_id, page, raw_ref, raw_images
            ),
        )
        self._write_json(
            ocr_file,
            self._document(
                run_id, offer_key, shop_id, item_id, page, raw_ref, payload_images
            ),
        )
        result: dict[str, Any] = {
            "status": status,
            "images": image_results,
            "ocr_ref": ocr_ref,
        }
        if status != "complete":
            result["error_code"] = first_error or "ocr_failed"
        return result

    @staticmethod
    def _document(
        run_id: str,
        offer_key: str,
        shop_id: str,
        item_id: str,
        page: Mapping[str, Any],
        raw_ref: str,
        images: list[dict[str, Any]],
    ) -> dict[str, Any]:
        input_refs = [
            ref
            for ref in [page.get("page_ref"), *(image.get("ref") for image in images)]
            if isinstance(ref, str)
        ]
        evidence_refs = [
            image["ref"] for image in images
            if image.get("status") == "success"
            and isinstance(image.get("text"), str)
            and image["text"].strip()
            and isinstance(image.get("ref"), str)
        ]
        return {
            "schema_version": 1,
            "run_id": run_id,
            "offer_key": offer_key,
            "shop_id": shop_id,
            "item_id": item_id,
            "page_ref": page.get("page_ref"),
            "raw_ref": raw_ref,
            "input_refs": input_refs,
            "evidence_refs": evidence_refs,
            "input_payload": {"product_page_ocr": {
                "schema_version": 1,
                "source_collected_at": page.get("captured_at"),
                "images": images,
            }},
        }

    @staticmethod
    def _raw_document(
        run_id: str,
        offer_key: str,
        shop_id: str,
        item_id: str,
        page: Mapping[str, Any],
        raw_ref: str,
        images: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "raw_ref": raw_ref,
            "run_id": run_id,
            "offer_key": offer_key,
            "shop_id": shop_id,
            "item_id": item_id,
            "page_ref": page.get("page_ref"),
            "source_evidence_ref": page.get("source_evidence_ref"),
            "source_collected_at": page.get("captured_at"),
            "engine": dict(_ENGINE_CONFIG),
            "images": images,
        }

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)


def _worker_main() -> int:
    """Private JSON-lines worker; stdout is reserved for protocol responses."""
    import contextlib
    try:
        with _redirect_native_stdout_to_stderr():
            with contextlib.redirect_stdout(sys.stderr):
                import cv2
                import numpy as np
                from paddleocr import PaddleOCR

                engine = PaddleOCR(
                    lang="japan", ocr_version="PP-OCRv6", device="cpu",
                    use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False, enable_mkldnn=False,
                    text_det_thresh=0.3, text_det_box_thresh=0.6,
                    text_rec_score_thresh=0.0,
                )
        print(json.dumps({"ready": True}), flush=True)
    except Exception:
        print(json.dumps({"ready": False, "error_code": "ocr_model_initialization_failed"}), flush=True)
        return 1
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("close") is True:
                return 0
            image_bytes = base64.b64decode(request["image_base64"], validate=True)
            frames = _decode_image_frames(image_bytes, cv2, np)
            with _redirect_native_stdout_to_stderr():
                with contextlib.redirect_stdout(sys.stderr):
                    raw, text = _predict_image_frames(engine, frames)
            print(json.dumps({"engine_response": raw, "returned_text": text}, ensure_ascii=False, separators=(",", ":")), flush=True)
        except Exception as exc:
            print(
                json.dumps({"error_code": _worker_error_code(exc)}), flush=True
            )
    return 0


if __name__ == "__main__" and "--worker" in sys.argv:
    raise SystemExit(_worker_main())
