"""Read captured product-page input for one explicit run and product identity."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


_SAFE_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_PRODUCT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class ProductPageCaptureRequest:
    """One direct public product-page capture tied to saved ROOM evidence."""

    request_id: str
    run_id: str
    attempt_id: str
    shop_id: str
    item_id: str
    item_url: str
    row_index: int


def _segment(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _run_segment(value: str) -> str:
    reserved = re.compile(
        r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.IGNORECASE
    )
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) and not reserved.fullmatch(
        value
    ) and not value.endswith("."):
        return value
    return f"encoded-{_segment(value)}"


def product_page_ref(run_id: str, shop_id: str, item_id: str) -> str:
    """Return the bounded reference used in ROOM product facts."""

    digest = hashlib.sha256(
        f"{run_id}\0{shop_id}\0{item_id}".encode("utf-8")
    ).hexdigest()[:40]
    return f"ppi:{digest}"


def load_product_page_inputs(
    data_root: Path,
    run_id: str,
    shop_id: str,
    item_id: str,
    *,
    expected_ref: str | None = None,
) -> dict[str, Any] | None:
    """Load page.json after verifying its explicit run/shop/item identity."""

    if not _SAFE_RUN.fullmatch(run_id):
        raise ValueError("product-page run identity is invalid")
    if not _SAFE_PRODUCT_ID.fullmatch(shop_id) or not _SAFE_PRODUCT_ID.fullmatch(
        item_id
    ):
        raise ValueError("product-page item identity is invalid")

    root = Path(data_root).resolve()
    product_root = root / "product-page-inputs"
    directory = (
        product_root
        / _run_segment(run_id)
        / f"shop-{_segment(shop_id)}"
        / f"item-{_segment(item_id)}"
    )
    page_file = directory / "page.json"
    if not page_file.exists():
        return None
    for path in (
        product_root,
        directory.parents[1],
        directory.parents[0],
        directory,
        page_file,
    ):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise ValueError("product-page capture contains a reparse point")
    if not page_file.resolve().is_relative_to(product_root.resolve()):
        raise ValueError("product-page capture escapes data root")
    value = json.loads(page_file.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("run_id") != run_id
        or value.get("shop_id") != shop_id
        or value.get("item_id") != item_id
        or value.get("page_ref") != product_page_ref(run_id, shop_id, item_id)
    ):
        raise ValueError("product-page capture identity differs")
    if expected_ref is not None and value.get("page_ref") != expected_ref:
        raise ValueError("product-page reference differs")
    return value


def selection_manifest(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the persisted ordered product selections in a daily checkpoint."""

    value = state.setdefault("selected_products", [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("selected product manifest is invalid")
    return value


def upsert_selection(manifest: list[dict[str, Any]], entry: Mapping[str, Any]) -> None:
    """Insert a product once by offer key, keeping its original selection order."""

    offer_key = entry.get("offer_key")
    if not isinstance(offer_key, str) or not offer_key:
        raise ValueError("selected product offer identity is invalid")
    for saved in manifest:
        if saved.get("offer_key") == offer_key:
            if saved.get("run_id") != entry.get("run_id"):
                raise ValueError("selected product run identity differs")
            return
    manifest.append(dict(entry))


def cleanup_committed_product_images(
    data_root: Path,
    run_id: str,
    offer_key: str,
    shop_id: str,
    item_id: str,
    page_ref: str,
    *,
    now: Callable[[], str],
) -> dict[str, Any]:
    """Delete only a committed product's image folder and retain a resume record."""

    if (
        not _SAFE_RUN.fullmatch(run_id)
        or not _SAFE_PRODUCT_ID.fullmatch(shop_id)
        or not _SAFE_PRODUCT_ID.fullmatch(item_id)
        or offer_key != f"{shop_id}:{item_id}"
        or not isinstance(page_ref, str)
        or page_ref != product_page_ref(run_id, shop_id, item_id)
    ):
        return {"status": "failed", "error_class": "ValueError", "deleted_file_count": 0}

    root = Path(data_root).resolve()
    product_root = root / "product-page-inputs"
    run_root = product_root / _run_segment(run_id)
    directory = (
        run_root
        / f"shop-{_segment(shop_id)}"
        / f"item-{_segment(item_id)}"
    )
    image_dir = directory / "images"
    record_file = directory / "cleanup.json"
    deleted_file_count = 0
    image_count = 0
    code_sha256 = _product_page_code_sha256()
    try:
        page = load_product_page_inputs(
            root, run_id, shop_id, item_id, expected_ref=page_ref
        )
        if page is None or not isinstance(page.get("images"), list):
            raise ValueError("product_page_input_missing")
        image_count = len(page["images"])
        if not directory.resolve().is_relative_to(run_root.resolve()):
            raise ValueError("product_image_cleanup_escapes_run_root")
        for candidate in (product_root, run_root, directory.parent, directory):
            metadata = candidate.lstat()
            if candidate.is_symlink() or getattr(
                metadata, "st_file_attributes", 0
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("product_image_cleanup_reparse_point")
        try:
            record_metadata = record_file.lstat()
        except FileNotFoundError:
            record_metadata = None
        if record_metadata is not None and (
            record_file.is_symlink()
            or getattr(record_metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError("product_image_cleanup_record_reparse_point")

        started_at = now()
        pending = {
            "schema_version": 1,
            "run_id": run_id,
            "offer_key": offer_key,
            "shop_id": shop_id,
            "item_id": item_id,
            "page_ref": page_ref,
            "status": "pending",
            "image_count": image_count,
            "deleted_file_count": 0,
            "started_at": started_at,
            "finished_at": None,
            "error_class": None,
            "code_sha256": code_sha256,
        }
        _write_cleanup_record(record_file, pending)

        try:
            image_metadata = image_dir.lstat()
        except FileNotFoundError:
            image_metadata = None
        if image_metadata is not None:
            if image_dir.is_symlink() or getattr(
                image_metadata, "st_file_attributes", 0
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("product_image_cleanup_reparse_point")
            if not image_dir.is_dir() or not image_dir.resolve().is_relative_to(
                directory.resolve()
            ):
                raise ValueError("product_image_cleanup_path_invalid")
            for current_root, directories, files in os.walk(
                image_dir, topdown=True, followlinks=False
            ):
                current = Path(current_root)
                for name in (*directories, *files):
                    child = current / name
                    metadata = child.lstat()
                    if child.is_symlink() or getattr(
                        metadata, "st_file_attributes", 0
                    ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                        raise ValueError("product_image_cleanup_reparse_point")
                    if child.is_file():
                        deleted_file_count += 1
            shutil.rmtree(image_dir)

        completed = dict(pending)
        completed.update(
            status="complete",
            deleted_file_count=deleted_file_count,
            finished_at=now(),
        )
        _write_cleanup_record(record_file, completed)
        return {
            "status": "complete",
            "image_count": image_count,
            "deleted_file_count": deleted_file_count,
            "started_at": started_at,
            "finished_at": completed["finished_at"],
            "page_ref": page_ref,
            "code_sha256": code_sha256,
        }
    except (OSError, ValueError) as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        failure_frame = frames[-1] if frames else None
        failure = {
            "schema_version": 1,
            "run_id": run_id,
            "offer_key": offer_key,
            "shop_id": shop_id,
            "item_id": item_id,
            "page_ref": page_ref,
            "status": "failed",
            "image_count": image_count,
            "deleted_file_count": deleted_file_count,
            "started_at": locals().get("started_at"),
            "finished_at": now(),
            "error_class": type(exc).__name__,
            "failure_module": "automation.inago.product_page_inputs",
            "failure_function": failure_frame.name if failure_frame else "unknown",
            "failure_line": failure_frame.lineno if failure_frame else None,
            "code_sha256": code_sha256,
        }
        try:
            _write_cleanup_record(record_file, failure)
        except OSError:
            pass
        return {
            "status": "failed",
            "image_count": image_count,
            "deleted_file_count": deleted_file_count,
            "started_at": failure["started_at"],
            "finished_at": failure["finished_at"],
            "error_class": type(exc).__name__,
            "page_ref": page_ref,
            "failure_module": failure["failure_module"],
            "failure_function": failure["failure_function"],
            "failure_line": failure["failure_line"],
            "code_sha256": code_sha256,
        }


def _write_cleanup_record(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _product_page_code_sha256() -> str | None:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None


def cleanup_unselected_product_page_folders(
    data_root: Path,
    run_id: str,
    candidates: Iterable[Mapping[str, Any]],
    keep_offer_keys: set[str],
) -> dict[str, list[dict[str, str]]]:
    """Delete only candidate folders outside the run-wide selected/committed set."""

    if not _SAFE_RUN.fullmatch(run_id):
        raise ValueError("product-page run identity is invalid")
    root = Path(data_root).resolve() / "product-page-inputs"
    run_root = root / _run_segment(run_id)
    result = {"deleted": [], "failed": []}
    for candidate in candidates:
        offer_key = candidate.get("offer_key")
        shop_id = candidate.get("shop_id")
        item_id = candidate.get("item_id")
        if not isinstance(offer_key, str) or offer_key in keep_offer_keys:
            continue
        if not isinstance(shop_id, str) or not _SAFE_PRODUCT_ID.fullmatch(shop_id):
            continue
        if not isinstance(item_id, str) or not _SAFE_PRODUCT_ID.fullmatch(item_id):
            continue
        folder = run_root / f"shop-{_segment(shop_id)}" / f"item-{_segment(item_id)}"
        if not folder.exists():
            continue
        try:
            for path in (root, run_root, folder.parent, folder):
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if path.is_symlink() or getattr(
                    metadata, "st_file_attributes", 0
                ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    raise ValueError("product-page cleanup encountered a reparse point")
            if not folder.resolve().is_relative_to(run_root.resolve()):
                raise ValueError("product-page cleanup escapes run root")
            shutil.rmtree(folder)
            result["deleted"].append({"offer_key": offer_key})
        except (OSError, ValueError) as exc:
            result["failed"].append(
                {"offer_key": offer_key, "error_class": type(exc).__name__}
            )
    return result
