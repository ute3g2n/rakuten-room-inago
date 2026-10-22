from __future__ import annotations
import json
import os
import sys
from pathlib import Path

root = Path(os.environ.get("OCR_STUDY_ROOT", Path(__file__).resolve().parent))
for name, relative in {
    "PIP_CACHE_DIR": "cache/pip",
    "TEMP": "tmp",
    "TMP": "tmp",
    "XDG_CACHE_HOME": "cache/xdg",
    "HF_HOME": "cache/huggingface",
    "PADDLE_PDX_CACHE_HOME": "models/paddlex",
}.items():
    target = root / relative
    target.mkdir(parents=True, exist_ok=True)
    os.environ[name] = str(target)

from paddleocr import PaddleOCR

image = Path(sys.argv[1]).resolve()
ocr = PaddleOCR(
    lang="japan",
    device="cpu",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    enable_mkldnn=False,
)
results = ocr.predict(str(image))
payloads = []
for result in results:
    data = getattr(result, "json", None)
    if callable(data):
        data = data()
    if data is None:
        data = getattr(result, "__dict__", {"repr": repr(result)})
    payloads.append(data)
(root / "results" / "smoke-001.json").write_text(
    json.dumps(payloads, ensure_ascii=False, default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value), indent=2),
    encoding="utf-8",
)
texts = []
for payload in payloads:
    if isinstance(payload, dict):
        data = payload.get("res", payload)
        if isinstance(data, dict):
            values = data.get("rec_texts", [])
            if isinstance(values, list):
                texts.extend(str(value) for value in values)
print(json.dumps({
    "image": image.name,
    "result_pages": len(payloads),
    "recognized_text_regions": len(texts),
    "nonempty_text_regions": sum(bool(value.strip()) for value in texts),
    "result_file": "results/smoke-001.json",
}, ensure_ascii=False))
