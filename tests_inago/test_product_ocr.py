from __future__ import annotations

import json
import shutil
from collections import defaultdict
from types import ModuleType
from pathlib import Path
from typing import Any

import pytest

from automation.inago import product_ocr
from automation.inago.product_ocr import ProductPageOCR, load_product_page_ocr
from tests_inago.product_page_test_support import product_dir, write_ocr, write_page


ROOT = Path(__file__).resolve().parents[1]
STUDY_ROOT = ROOT / "検討" / "楽天商品要素調査_2026-10-22"
OCR_STUDY = STUDY_ROOT / "OCR技術検証"
RUNTIME_ROOT = ROOT / "rakuten-room-inago" / "runtime"


class _FixtureWorker:
    def __init__(self, texts: list[str]) -> None:
        self.texts = iter(texts)

    def predict(self, _image_bytes: bytes) -> tuple[list[Any], str]:
        return ([{"fixture": True}], next(self.texts))

    def close(self) -> None:
        pass


def _image_records(count: int) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "url": f"https://images.test/{index}.png",
            "occurrences": [{"order": index, "tag": "img", "attribute": "src"}],
            "status": "downloaded",
            "file": f"images/{index + 1:04d}.png",
            "content_type": "image/png",
            "byte_length": 12,
        }
        for index in range(count)
    ]


def test_animated_gif_frames_are_all_decoded_and_sent_to_ocr(monkeypatch) -> None:
    decoded_frames = 0

    class _Frame:
        size = (2, 2)

        def __init__(self, marker: str) -> None:
            self.marker = marker

        def convert(self, mode: str) -> str:
            assert mode == "RGB"
            return self.marker

    class _Gif:
        size = (2, 2)
        n_frames = 2
        frames = [_Frame("frame-one"), _Frame("frame-two")]
        current_frame = None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def seek(self, index: int) -> None:
            self.current_frame = self.frames[index]

        def convert(self, mode: str) -> str:
            return self.current_frame.convert(mode)

    fake_pil = ModuleType("PIL")
    fake_image = ModuleType("PIL.Image")
    fake_image.open = lambda _stream: _Gif()
    fake_pil.Image = fake_image
    monkeypatch.setitem(__import__("sys").modules, "PIL", fake_pil)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", fake_image)

    class _Cv2:
        IMREAD_COLOR = 1
        COLOR_RGB2BGR = 2

        @staticmethod
        def imdecode(_encoded: bytes, _mode: int):
            raise AssertionError("GIF frames must be decoded through Pillow")

        @staticmethod
        def cvtColor(frame: str, _conversion: int) -> str:
            nonlocal decoded_frames
            decoded_frames += 1
            return frame

    class _Numpy:
        uint8 = "uint8"

        @staticmethod
        def frombuffer(image_bytes: bytes, dtype: str) -> bytes:
            assert dtype == "uint8"
            return image_bytes

        @staticmethod
        def asarray(frame: str) -> str:
            return frame

    class _Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def json(self) -> dict[str, Any]:
            return {"res": {"rec_texts": [self.text]}}

    class _Engine:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        def predict(self, frame: str) -> list[_Page]:
            assert decoded_frames == len(self.inputs) + 1
            self.inputs.append(frame)
            return [_Page(f"text-{frame}")]

    frames = product_ocr._decode_image_frames(b"GIF89a-fixture", _Cv2, _Numpy)
    assert decoded_frames == 0
    engine = _Engine()
    raw, text = product_ocr._predict_image_frames(engine, frames)

    assert engine.inputs == ["frame-one", "frame-two"]
    assert text == "text-frame-one\n\ntext-frame-two"
    assert [frame["frame_index"] for frame in raw] == [0, 1]
    assert [frame["engine_response"][0]["res"]["rec_texts"] for frame in raw] == [
        ["text-frame-one"],
        ["text-frame-two"],
    ]


@pytest.mark.parametrize(
    ("limit_name", "limit", "error_code"),
    [
        ("_MAX_OCR_IMAGE_FRAMES", 1, "ocr_image_frame_limit_exceeded"),
        ("_MAX_OCR_IMAGE_PIXELS", 3, "ocr_image_pixel_limit_exceeded"),
        ("_MAX_OCR_IMAGE_BYTES", 5, "ocr_image_byte_limit_exceeded"),
    ],
)
def test_animated_gif_limits_return_explicit_error_codes(
    monkeypatch, limit_name: str, limit: int, error_code: str
) -> None:
    monkeypatch.setattr(product_ocr, limit_name, limit, raising=False)

    class _Frame:
        size = (2, 2)

        def __init__(self, marker: str) -> None:
            self.marker = marker

        def convert(self, _mode: str) -> str:
            return self.marker

    image_open_count = 0
    frame_seek_count = 0

    class _Gif:
        size = (2, 2)
        n_frames = 2
        frames = [_Frame("frame-one"), _Frame("frame-two")]
        current_frame = None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def seek(self, index: int) -> None:
            nonlocal frame_seek_count
            frame_seek_count += 1
            self.current_frame = self.frames[index]

        def convert(self, mode: str) -> str:
            return self.current_frame.convert(mode)

    fake_pil = ModuleType("PIL")
    fake_image = ModuleType("PIL.Image")

    def _open_image(_stream):
        nonlocal image_open_count
        image_open_count += 1
        return _Gif()

    fake_image.open = _open_image
    fake_pil.Image = fake_image
    monkeypatch.setitem(__import__("sys").modules, "PIL", fake_pil)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", fake_image)

    class _Cv2:
        COLOR_RGB2BGR = 2

        @staticmethod
        def cvtColor(frame: str, _conversion: int) -> str:
            return frame

    class _Numpy:
        @staticmethod
        def asarray(frame: str) -> str:
            return frame

    frames = product_ocr._decode_image_frames(b"GIF89a-fixture", _Cv2, _Numpy)
    try:
        next(frames)
    except ValueError as exc:
        assert str(exc) == error_code
    else:
        raise AssertionError(f"expected {error_code}")
    assert frame_seek_count == 0
    assert image_open_count == (0 if limit_name == "_MAX_OCR_IMAGE_BYTES" else 1)


def test_static_image_pixel_limit_is_checked_before_opencv_decode(monkeypatch) -> None:
    monkeypatch.setattr(product_ocr, "_MAX_OCR_IMAGE_PIXELS", 5, raising=False)

    class _Image:
        size = (3, 2)
        n_frames = 1

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    fake_pil = ModuleType("PIL")
    fake_image = ModuleType("PIL.Image")
    fake_image.open = lambda _stream: _Image()
    fake_sequence = ModuleType("PIL.ImageSequence")
    fake_pil.Image = fake_image
    fake_pil.ImageSequence = fake_sequence
    monkeypatch.setitem(__import__("sys").modules, "PIL", fake_pil)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", fake_image)
    monkeypatch.setitem(__import__("sys").modules, "PIL.ImageSequence", fake_sequence)

    class _Cv2:
        IMREAD_COLOR = 1

        @staticmethod
        def imdecode(_encoded: bytes, _mode: int):
            raise AssertionError("oversized image must be rejected before imdecode")

    class _Numpy:
        uint8 = "uint8"

        @staticmethod
        def frombuffer(image_bytes: bytes, dtype: str) -> bytes:
            return image_bytes

    frames = product_ocr._decode_image_frames(b"PNG-fixture", _Cv2, _Numpy)
    with pytest.raises(ValueError, match="^ocr_image_pixel_limit_exceeded$"):
        next(frames)


def test_worker_preserves_only_known_image_error_codes() -> None:
    assert (
        product_ocr._worker_error_code(ValueError("ocr_image_decode_failed"))
        == "ocr_image_decode_failed"
    )
    assert (
        product_ocr._worker_error_code(ValueError("unexpected detail"))
        == "ocr_inference_failed"
    )


def test_unreadable_gif_returns_explicit_decode_error(monkeypatch) -> None:
    fake_pil = ModuleType("PIL")
    fake_image = ModuleType("PIL.Image")

    def _raise_decode_error(_stream):
        raise OSError("decode failed")

    fake_image.open = _raise_decode_error
    fake_sequence = ModuleType("PIL.ImageSequence")
    fake_pil.Image = fake_image
    fake_pil.ImageSequence = fake_sequence
    monkeypatch.setitem(__import__("sys").modules, "PIL", fake_pil)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", fake_image)
    monkeypatch.setitem(__import__("sys").modules, "PIL.ImageSequence", fake_sequence)

    frames = product_ocr._decode_image_frames(b"GIF89a-fixture", object(), object())
    with pytest.raises(ValueError, match="^ocr_image_decode_failed$"):
        next(frames)


def test_real_engine_uses_runtime_assets_and_handles_japanese_image_path(
    tmp_path: Path, monkeypatch
) -> None:
    assert (RUNTIME_ROOT / "ocr-assets" / ".venv" / "Scripts" / "python.exe").is_file()
    source = STUDY_ROOT / "01_meiji-ya_129" / "images" / "image_001.jpg"
    assert source.is_file()
    page = write_page(
        tmp_path,
        "OCR-REAL-01",
        "ocr-shop",
        "ocr-item",
        images=[
            {
                "index": 0,
                "url": "https://images.test/real.jpg",
                "occurrences": [],
                "status": "downloaded",
                "file": "images/画像-日本語.jpg",
                "content_type": "image/jpeg",
                "byte_length": source.stat().st_size,
            }
        ],
    )
    image_path = product_dir(tmp_path, "OCR-REAL-01", "ocr-shop", "ocr-item") / "images" / "画像-日本語.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, image_path)

    worker_factory = product_ocr._PaddleWorker
    monkeypatch.setattr(
        product_ocr,
        "_PaddleWorker",
        lambda _data_root: worker_factory(RUNTIME_ROOT),
    )
    engine = ProductPageOCR()
    try:
        result = engine.process_product(
            None,
            run_id="OCR-REAL-01",
            offer_key="ocr-shop:ocr-item",
            shop_id="ocr-shop",
            item_id="ocr-item",
            page_ref=page["page_ref"],
            source_evidence_ref=page["source_evidence_ref"],
            data_root=tmp_path,
        )
    finally:
        engine.close()

    assert result["status"] == "complete"
    assert len(result["images"]) == 1
    assert result["images"][0]["status"] == "success"
    saved = load_product_page_ocr(
        tmp_path,
        "OCR-REAL-01",
        "ocr-shop:ocr-item",
        "ocr-shop",
        "ocr-item",
        expected_page_ref=page["page_ref"],
        expected_ocr_ref=result["ocr_ref"],
    )
    assert isinstance(saved, dict)
    text = saved["input_payload"]["product_page_ocr"]["images"][0]["text"]
    assert isinstance(text, str) and bool(text.strip())


def test_every_image_and_exact_returned_text_survive_conversion(
    tmp_path: Path,
) -> None:
    run_id = "OCR-CONVERT-01"
    images = _image_records(3)
    images[2]["status"] = "http_error"
    images[2]["file"] = None
    page = write_page(tmp_path, run_id, "shop", "item", images=images)
    folder = product_dir(tmp_path, run_id, "shop", "item")
    for index in range(2):
        image_path = folder / images[index]["file"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(f"fixture-image-{index}".encode())
    expected = ["first line\nsecond line", "English\nword", ""]
    engine = ProductPageOCR()
    fixture_worker = _FixtureWorker(expected[:2])
    engine._worker_for = lambda _run_id, _data_root: fixture_worker

    result = engine.process_product(
        None,
        run_id=run_id,
        offer_key="shop:item",
        shop_id="shop",
        item_id="item",
        page_ref=page["page_ref"],
        source_evidence_ref=page["source_evidence_ref"],
        data_root=tmp_path,
    )

    assert result["status"] == "partial"
    saved = load_product_page_ocr(
        tmp_path,
        run_id,
        "shop:item",
        "shop",
        "item",
        expected_page_ref=page["page_ref"],
        expected_ocr_ref=result["ocr_ref"],
    )
    assert saved is not None
    actual = saved["input_payload"]["product_page_ocr"]["images"]
    assert len(actual) == 3
    assert [(item["status"], item["text"]) for item in actual] == [
        ("success", expected[0]),
        ("success", expected[1]),
        ("error", ""),
    ]
    assert len(saved["input_refs"]) == 4
    assert saved["evidence_refs"] == [actual[0]["ref"], actual[1]["ref"]]


def test_sale_and_item_desc_images_all_flow_to_ordered_ocr_records(
    tmp_path: Path,
) -> None:
    run_id = "OCR-DESC-MIXED-01"
    images = _image_records(4)
    for index, image in enumerate(images):
        selector = "span.sale_desc" if index % 2 == 0 else "span.item_desc"
        image["occurrences"] = [
            {
                "key": "sale_desc" if index % 2 == 0 else "item_desc",
                "selector": selector,
                "match_index": 0,
                "dom_position": index + 1,
                "tag": "img",
                "attribute": "src",
                "order": index,
            }
        ]
    images[2]["status"] = "http_error"
    images[2]["file"] = None
    page = write_page(
        tmp_path,
        run_id,
        "shop",
        "item",
        selectors=[
            {"key": "sale_desc", "selector": "span.sale_desc", "status": "captured", "outer_html": ["<span class=\"sale_desc\">sale</span>"]},
            {"key": "item_desc", "selector": "span.item_desc", "status": "captured", "outer_html": ["<span class=\"item_desc\">item</span>"]},
        ],
        images=images,
    )
    folder = product_dir(tmp_path, run_id, "shop", "item")
    for index in (0, 1, 3):
        image_path = folder / images[index]["file"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(f"fixture-image-{index}".encode())

    texts = ["sale first line\nsale second line", "item English\nitem 日本語", "last image\nfull text"]
    engine = ProductPageOCR()
    fixture_worker = _FixtureWorker(texts)
    engine._worker_for = lambda _run_id, _data_root: fixture_worker
    result = engine.process_product(
        None,
        run_id=run_id,
        offer_key="shop:item",
        shop_id="shop",
        item_id="item",
        page_ref=page["page_ref"],
        source_evidence_ref=page["source_evidence_ref"],
        data_root=tmp_path,
    )

    assert result["status"] == "partial"
    assert [entry["order"] for entry in result["images"]] == [0, 1, 2, 3]
    saved = load_product_page_ocr(
        tmp_path,
        run_id,
        "shop:item",
        "shop",
        "item",
        expected_page_ref=page["page_ref"],
        expected_ocr_ref=result["ocr_ref"],
    )
    assert saved is not None
    actual = saved["input_payload"]["product_page_ocr"]["images"]
    assert len(actual) == len(page["images"]) == 4
    assert [(entry["status"], entry["text"]) for entry in actual] == [
        ("success", texts[0]),
        ("success", texts[1]),
        ("error", ""),
        ("success", texts[2]),
    ]
    assert [item["image_ref"] for item in result["images"]] == [item["ref"] for item in actual]
    assert saved["input_refs"] == [page["page_ref"], *[item["ref"] for item in actual]]
    assert saved["evidence_refs"] == [actual[0]["ref"], actual[1]["ref"], actual[3]["ref"]]
    raw = json.loads((folder / "ocr-raw.json").read_text(encoding="utf-8"))
    assert [item["order"] for item in raw["images"]] == [0, 1, 2, 3]
    assert raw["images"][1]["status"] == "success"
    assert raw["images"][1]["source_sha256"]


def test_description_images_ocr_preserves_text_and_resumes(
    tmp_path: Path,
) -> None:
    run_id = "OCR-EXPLICIT-RETRY-01"
    images = _image_records(2)
    page = write_page(tmp_path, run_id, "shop", "item", images=images)
    folder = product_dir(tmp_path, run_id, "shop", "item")
    image_bytes = [b"first-stable-image", b"second-retry-image"]
    for image, value in zip(images, image_bytes, strict=True):
        image_path = folder / image["file"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(value)

    class _RetryWorker:
        def __init__(self) -> None:
            self.responses: list[Any] = [
                "kept first\nfull text",
                RuntimeError("fixture temporary inference failure"),
                "retried second\nfull text",
            ]
            self.inputs: list[bytes] = []

        def predict(self, value: bytes) -> tuple[list[Any], str]:
            self.inputs.append(value)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return ([{"fixture": True}], response)

        def close(self) -> None:
            pass

    engine = ProductPageOCR()
    worker = _RetryWorker()
    engine._worker_for = lambda _run_id, _data_root: worker
    first = engine.process_product(
        None,
        run_id=run_id,
        offer_key="shop:item",
        shop_id="shop",
        item_id="item",
        page_ref=page["page_ref"],
        source_evidence_ref=page["source_evidence_ref"],
        data_root=tmp_path,
    )
    first_raw = json.loads((folder / "ocr-raw.json").read_text(encoding="utf-8"))
    second = engine.process_product(
        None,
        run_id=run_id,
        offer_key="shop:item",
        shop_id="shop",
        item_id="item",
        page_ref=page["page_ref"],
        source_evidence_ref=page["source_evidence_ref"],
        data_root=tmp_path,
    )
    second_raw = json.loads((folder / "ocr-raw.json").read_text(encoding="utf-8"))
    saved = load_product_page_ocr(
        tmp_path,
        run_id,
        "shop:item",
        "shop",
        "item",
        expected_page_ref=page["page_ref"],
        expected_ocr_ref=second["ocr_ref"],
    )

    assert first["status"] == "partial"
    assert second["status"] == "complete"
    assert len(worker.inputs) == 3
    assert worker.inputs == [image_bytes[0], image_bytes[1], image_bytes[1]]
    assert first_raw["images"][0]["source_sha256"] == second_raw["images"][0]["source_sha256"]
    assert first_raw["images"][0]["returned_text_sha256"] == second_raw["images"][0]["returned_text_sha256"]
    assert saved is not None
    payload_images = saved["input_payload"]["product_page_ocr"]["images"]
    assert [(entry["status"], entry["text"]) for entry in payload_images] == [
        ("success", "kept first\nfull text"),
        ("success", "retried second\nfull text"),
    ]


def test_legacy_v1_page_and_ocr_payload_still_load_without_new_image_fields(
    tmp_path: Path,
) -> None:
    run_id = "OCR-LEGACY-V1-01"
    page = write_page(
        tmp_path,
        run_id,
        "shop",
        "item",
        images=[
            {
                "index": 0,
                "url": "https://images.test/legacy.png",
                "occurrences": [{"order": 0, "tag": "img", "attribute": "src"}],
                "status": "downloaded",
                "file": "images/0001-legacy.png",
                "content_type": "image/png",
                "byte_length": 8,
            }
        ],
    )
    legacy_payload, ocr_ref = write_ocr(
        tmp_path,
        page,
        images=[
            {"ref": "ocrimg:legacy-success", "status": "success", "text": "legacy\nfull text"},
            {"ref": "ocrimg:legacy-error", "status": "error", "text": ""},
        ],
    )

    loaded = load_product_page_ocr(
        tmp_path,
        run_id,
        "shop:item",
        "shop",
        "item",
        expected_page_ref=page["page_ref"],
        expected_ocr_ref=ocr_ref,
    )

    assert page["schema_version"] == legacy_payload["schema_version"] == 1
    assert "source_kind" not in page["images"][0]
    assert loaded is not None
    assert [entry["text"] for entry in loaded["input_payload"]["product_page_ocr"]["images"]] == [
        "legacy\nfull text",
        "",
    ]


def test_oversized_image_is_rejected_before_reading_or_worker_send(
    tmp_path: Path, monkeypatch
) -> None:
    run_id = "OCR-BYTE-LIMIT-01"
    images = _image_records(1)
    page = write_page(tmp_path, run_id, "shop", "item", images=images)
    image_path = product_dir(tmp_path, run_id, "shop", "item") / images[0]["file"]
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"oversized")
    monkeypatch.setattr(product_ocr, "_MAX_OCR_IMAGE_BYTES", 4)

    original_read_bytes = Path.read_bytes
    def guarded_read_bytes(path: Path) -> bytes:
        if path == image_path:
            raise AssertionError("oversized image must be rejected before read_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    engine = ProductPageOCR()
    engine._worker_for = lambda *_args: pytest.fail("oversized image reached OCR worker")

    result = engine.process_product(
        None,
        run_id=run_id,
        offer_key="shop:item",
        shop_id="shop",
        item_id="item",
        page_ref=page["page_ref"],
        source_evidence_ref=page["source_evidence_ref"],
        data_root=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "ocr_image_byte_limit_exceeded"
    assert result["images"][0]["error_code"] == "ocr_image_byte_limit_exceeded"
    raw_file = image_path.parent.parent / "ocr-raw.json"
    raw = json.loads(raw_file.read_text(encoding="utf-8"))
    assert raw["images"][0]["error_code"] == "ocr_image_byte_limit_exceeded"


def test_study_fifty_nine_images_round_trip_through_product_format(
    tmp_path: Path,
) -> None:
    raw_path = OCR_STUDY / "results" / "ocr-results.jsonl"
    records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 59
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record["product_directory"])].append(record)
    assert len(groups) == 5

    compared = 0
    for product_index, (product_name, product_records) in enumerate(sorted(groups.items()), 1):
        run_id = f"OCR-STUDY-{product_index}"
        shop_id = f"study-shop-{product_index}"
        item_id = f"study-item-{product_index}"
        image_specs: list[dict[str, Any]] = []
        expected_text: list[str] = []
        for index, record in enumerate(product_records):
            source = STUDY_ROOT / Path(str(record["source_relative_path"]))
            assert source.is_file()
            relative = f"images/{index + 1:04d}{source.suffix.lower()}"
            image_specs.append(
                {
                    "index": index,
                    "url": f"https://images.test/{product_index}/{index}",
                    "occurrences": [],
                    "status": "downloaded",
                    "file": relative,
                    "content_type": "image/jpeg",
                    "byte_length": source.stat().st_size,
                }
            )
            destination = product_dir(tmp_path, run_id, shop_id, item_id) / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            expected_text.append(str(record["returned_text"]))
        page = write_page(tmp_path, run_id, shop_id, item_id, images=image_specs)
        engine = ProductPageOCR()
        fixture_worker = _FixtureWorker(expected_text)
        engine._worker_for = lambda _run_id, _data_root: fixture_worker
        result = engine.process_product(
            None,
            run_id=run_id,
            offer_key=f"{shop_id}:{item_id}",
            shop_id=shop_id,
            item_id=item_id,
            page_ref=page["page_ref"],
            source_evidence_ref=page["source_evidence_ref"],
            data_root=tmp_path,
        )
        assert result["status"] == "complete"
        saved = load_product_page_ocr(
            tmp_path,
            run_id,
            f"{shop_id}:{item_id}",
            shop_id,
            item_id,
            expected_page_ref=page["page_ref"],
            expected_ocr_ref=result["ocr_ref"],
        )
        actual = saved["input_payload"]["product_page_ocr"]["images"]
        assert len(actual) == len(expected_text)
        assert all(
            item["status"] == "success" and item["text"] == expected_text[index]
            for index, item in enumerate(actual)
        )
        compared += len(actual)

    assert compared == 59
