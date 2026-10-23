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
from tests_inago.product_page_test_support import product_dir, write_page


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
        frames = [_Frame("frame-one"), _Frame("frame-two")]

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    fake_pil = ModuleType("PIL")
    fake_image = ModuleType("PIL.Image")
    fake_image.open = lambda _stream: _Gif()
    fake_sequence = ModuleType("PIL.ImageSequence")
    fake_sequence.Iterator = lambda source: iter(source.frames)
    fake_pil.Image = fake_image
    fake_pil.ImageSequence = fake_sequence
    monkeypatch.setitem(__import__("sys").modules, "PIL", fake_pil)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", fake_image)
    monkeypatch.setitem(__import__("sys").modules, "PIL.ImageSequence", fake_sequence)

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

    class _Gif:
        frames = [_Frame("frame-one"), _Frame("frame-two")]

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    fake_pil = ModuleType("PIL")
    fake_image = ModuleType("PIL.Image")
    fake_image.open = lambda _stream: _Gif()
    fake_sequence = ModuleType("PIL.ImageSequence")
    fake_sequence.Iterator = lambda source: iter(source.frames)
    fake_pil.Image = fake_image
    fake_pil.ImageSequence = fake_sequence
    monkeypatch.setitem(__import__("sys").modules, "PIL", fake_pil)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", fake_image)
    monkeypatch.setitem(__import__("sys").modules, "PIL.ImageSequence", fake_sequence)

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
        next(frames)
    except ValueError as exc:
        assert str(exc) == error_code
    else:
        raise AssertionError(f"expected {error_code}")


def test_worker_preserves_only_known_image_error_codes() -> None:
    assert (
        product_ocr._worker_error_code(ValueError("ocr_image_decode_failed"))
        == "ocr_image_decode_failed"
    )
    assert (
        product_ocr._worker_error_code(ValueError("unexpected detail"))
        == "ocr_inference_failed"
    )


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
