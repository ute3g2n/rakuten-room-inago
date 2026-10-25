from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago.ai_runner import AIJobRunner, compute_input_hash
from automation.inago.contracts import AIJobRequest, CopyTarget
from automation.inago.copy_service import _check_spec
from automation.inago.product_ocr import ProductPageOCR, load_product_page_ocr
from automation.inago.production_runtime import _make_copy_spec_factory
from automation.inago.paths import SAFE_FIXTURE_BASE, resolve_runtime_paths
from automation.inago.prompt_builder import build_prompt_context, render_prompt
from automation.inago.repository import open_database
from automation.inago.settings import register_external_settings
from tests_inago.product_page_test_support import write_ocr, write_page
from tests_inago.test_step09 import BASIC_SETTINGS_PATH, DETAIL_RULES_PATH


ROOT = Path(__file__).resolve().parents[1]
FULL_GUIDANCE = (
    "OCRでの抽出テキスト内の改行は画像のレイアウトを反映しています。"
    "改行を単語や文の区切りと決めつけず、前後の行も文脈に含めて読んでください。"
    "英数字が改行をまたぐ場合も、自然につながると判断できるときだけ一語として解釈し、"
    "不確かな箇所は推測で補わないでください。"
)


_C07_CAPTURE_RUNNER = r"""
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [modulePath, root] = process.argv.slice(1);
const { saveProductPageInputs } = await import(pathToFileURL(modulePath).href);
const node = (tagName, outerHTML, innerHTML = '', attrs = {}, children = []) => ({
  tagName: tagName.toUpperCase(), outerHTML, innerHTML, children,
  currentSrc: attrs.currentSrc || '', scrollIntoView() {},
  getAttribute(name) { return Object.hasOwn(attrs, name) ? attrs[name] : null; },
  querySelectorAll(selector) {
    const descendants = [];
    const walk = item => { for (const child of item.children) { descendants.push(child); walk(child); } };
    walk(this);
    return selector === '*' ? descendants : descendants.filter(item => selector.toLowerCase() === item.tagName.toLowerCase());
  },
});
const saleImage = node('img', '<img src="https://images.test/sale.png">', '', {
  src: 'https://images.test/sale.png', currentSrc: 'https://images.test/sale.png',
});
const itemImage = node('img', '<img src="https://images.test/item.png">', '', {
  src: 'https://images.test/item.png', currentSrc: 'https://images.test/item.png',
});
const sale = node('span', '<span class="sale_desc"><p>Sale description fixture</p><img src="https://images.test/sale.png"></span>',
  'Sale description fixture', { class: 'sale_desc' }, [saleImage]);
const item = node('span', '<span class="item_desc"><p>Item description complete HTML fixture</p><img src="https://images.test/item.png"></span>',
  'Item description complete HTML fixture', { class: 'item_desc' }, [itemImage]);
globalThis.document = { querySelectorAll(selector) {
  if (selector === 'span.sale_desc') return [sale];
  if (selector === 'span.item_desc') return [item];
  return [];
} };
globalThis.location = { href: 'https://item.rakuten.co.jp/c07-shop/c07-item/' };
globalThis.requestAnimationFrame = callback => callback();
globalThis.getComputedStyle = () => ({ backgroundImage: 'none' });
const page = {
  async evaluate(fn, arg) { return fn(arg); },
  request: { async get(url) {
    return { ok: () => true, status: () => 200, headers: () => ({ 'content-type': 'image/png' }), body: async () => Buffer.from(`capture-bytes:${url}`) };
  } },
};
const runId = 'CAP-C07-01';
const product = { shop: 'c07-shop', item: 'c07-item', url: location.href };
const request = { run_id: runId, attempt_id: `${runId}-ATTEMPT` };
const productPageRoot = path.join(root, 'product-page-inputs');
const result = await saveProductPageInputs({ page, product, request, rowIndex: 1,
  config: { product_page_root: productPageRoot },
  resolveImageHost: async () => [{ address: '93.184.216.34' }],
});
const folder = path.join(productPageRoot, runId, `shop-${Buffer.from(product.shop).toString('base64url')}`,
  `item-${Buffer.from(product.item).toString('base64url')}`);
const pageValue = JSON.parse(await readFile(path.join(folder, 'page.json'), 'utf8'));
console.log(JSON.stringify({ result, page: pageValue }));
"""


@pytest.fixture
def settings_runtime(tmp_path: Path, fixture_config_path: Path):
    private_root = SAFE_FIXTURE_BASE / "product-page-copy" / tmp_path.name
    paths = resolve_runtime_paths(
        data_root=private_root / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    try:
        return register_external_settings(
            connection,
            json.loads(BASIC_SETTINGS_PATH.read_text(encoding="utf-8")),
            json.loads(DETAIL_RULES_PATH.read_text(encoding="utf-8")),
            created_at="2026-10-23T13:00:00+09:00",
        )
    finally:
        connection.close()
        if private_root.resolve().is_relative_to(SAFE_FIXTURE_BASE.resolve()):
            shutil.rmtree(private_root, ignore_errors=True)


def _copy_spec_fixture(
    tmp_path: Path,
    settings_runtime,
    *,
    page_override: dict | None = None,
    ocr_override: dict | None = None,
    ocr_ref_override: str | None = None,
):
    run_id = page_override["run_id"] if page_override is not None else "COPY-PRODUCT-01"
    shop_id = page_override["shop_id"] if page_override is not None else "copy-shop"
    item_id = page_override["item_id"] if page_override is not None else "copy-item"
    offer_key = f"{shop_id}:{item_id}"
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE offer (
            offer_key TEXT PRIMARY KEY, shop_id TEXT, item_id TEXT,
            facts_json TEXT NOT NULL, image_refs_json TEXT NOT NULL,
            name TEXT NOT NULL, url TEXT NOT NULL
        );
        CREATE TABLE draft (run_id TEXT, offer_key TEXT, version INTEGER);
        CREATE TABLE run (run_id TEXT PRIMARY KEY, checkpoint TEXT NOT NULL);
        """
    )
    facts = [
        {"source_ref": "fact:fixture:01", "field": "material", "value": "observed"}
    ]
    connection.execute(
        "INSERT INTO offer VALUES (?,?,?,?,?,?,?)",
        (
            offer_key,
            shop_id,
            item_id,
            json.dumps(facts),
            "[]",
            "Fixture product name",
            f"https://item.rakuten.co.jp/{shop_id}/{item_id}/",
        ),
    )
    page = page_override or write_page(
        tmp_path,
        run_id,
        shop_id,
        item_id,
        selectors=[
            {
                "key": "sale_desc",
                "selector": "span.sale_desc",
                "status": "captured",
                "outer_html": ['<span class="sale_desc" data-value="kept">complete HTML fixture</span>'],
            },
            {
                "key": "normal_reserve_item_name",
                "selector": "span.normal_reserve_item_name",
                "status": "captured",
                "outer_html": ['<span class="normal_reserve_item_name">Fixture product name</span>'],
            },
            {
                "key": "normal_reserve_catch_copy",
                "selector": "span.normal_reserve_catch_copy",
                "status": "captured",
                "outer_html": ['<span class="normal_reserve_catch_copy">Fixture catch copy</span>'],
            },
            {
                "key": "item_desc",
                "selector": "span.item_desc",
                "status": "captured",
                "outer_html": ['<span class="item_desc" data-source="fixture">complete item_desc HTML fixture</span>'],
            },
        ],
        images=[
            {
                "index": 0,
                "url": "https://images.test/sale.png",
                "occurrences": [{"key": "sale_desc", "selector": "span.sale_desc", "order": 0}],
                "status": "downloaded",
                "file": "images/0001.png",
            },
            {
                "index": 1,
                "url": "https://images.test/item.png",
                "occurrences": [{"key": "item_desc", "selector": "span.item_desc", "order": 1}],
                "status": "downloaded",
                "file": "images/0002.png",
            },
        ],
    )
    if page_override is not None:
        if ocr_override is None or ocr_ref_override is None:
            raise ValueError("captured page requires its OCR document and reference")
        ocr = ocr_override
        ocr_ref = ocr_ref_override
    else:
        ocr, ocr_ref = write_ocr(
            tmp_path,
            page,
            images=[
                {"ref": "ocrimg:fixture:01", "status": "success", "text": "sale image OCR\nfull text"},
                {"ref": "ocrimg:fixture:02", "status": "success", "text": "item_desc image OCR\ncomplete text"},
            ],
        )
    connection.execute(
        "INSERT INTO run VALUES (?,?)",
        (
            run_id,
            json.dumps(
                {
                    "daily_orchestration": {
                        "selected_products": [
                            {
                                "offer_key": offer_key,
                                "run_id": run_id,
                                "shop_id": shop_id,
                                "item_id": item_id,
                                "page_ref": page["page_ref"],
                                "ocr_ref": ocr_ref,
                            }
                        ]
                    }
                }
            ),
        ),
    )
    runtime_config = SimpleNamespace(runtime_root=tmp_path)
    factory = _make_copy_spec_factory(runtime_config, settings_runtime)
    spec = factory(
        connection,
        run_id=run_id,
        offer_key=offer_key,
        theme_id=settings_runtime.themes[0]["theme_id"],
        target_date="2026-10-23",
        settings_version=settings_runtime.active_basic_version,
        deadline_at="2026-10-23T13:10:00+09:00",
        deadline_monotonic=1000.0,
    )
    return connection, page, ocr, ocr_ref, spec


def test_copy_spec_contains_exact_html_ocr_and_all_input_refs(
    tmp_path: Path, settings_runtime
) -> None:
    connection, page, ocr, ocr_ref, spec = _copy_spec_fixture(tmp_path, settings_runtime)
    assert spec is not None
    _check_spec(spec)

    payload = spec.input_payload
    html_selectors = payload["product_page_html"]["selectors"]
    ocr_images = payload["product_page_ocr"]["images"]
    assert [item["selector"] for item in html_selectors] == [
        "span.sale_desc",
        "span.normal_reserve_item_name",
        "span.normal_reserve_catch_copy",
        "span.item_desc",
    ]
    expected_html = [
        html
        for record in page["selectors"]
        for html in record["outer_html"]
    ]
    actual_html = [
        element["outer_html"]
        for record in html_selectors
        for element in record["elements"]
    ]
    expected_ocr = ocr["input_payload"]["product_page_ocr"]["images"]
    assert actual_html == expected_html
    assert ocr_images == expected_ocr
    item_selector = next(item for item in html_selectors if item["selector"] == "span.item_desc")
    assert item_selector["elements"][0]["outer_html"] == '<span class="item_desc" data-source="fixture">complete item_desc HTML fixture</span>'
    assert [item["text"] for item in ocr_images] == [
        "sale image OCR\nfull text",
        "item_desc image OCR\ncomplete text",
    ]
    assert ocr_ref in spec.input_refs
    assert page["page_ref"] in spec.input_refs
    assert all(item["ref"] in spec.input_refs for item in ocr_images)
    assert set(item["ref"] for item in ocr_images if item["text"]) <= set(spec.evidence_refs)
    assert "engine_response" not in json.dumps(payload, ensure_ascii=False)
    connection.close()


def _job_request(
    payload: dict,
    input_refs: tuple[str, ...],
    *,
    run_id: str = "COPY-PRODUCT-01",
    offer_key: str = "copy-shop:copy-item",
) -> AIJobRequest:
    return AIJobRequest(
        job_id="JOB-PRODUCT-01",
        run_id=run_id,
        attempt=1,
        task_id="T02",
        target_date="2026-10-23",
        settings_version="settings-fixture-v1",
        input_refs=input_refs,
        evidence_refs=tuple(ref for ref in input_refs if ref.startswith("html:") or ref.startswith("ocrimg:")),
        deadline_at="2026-10-23T13:10:00+09:00",
        deadline_monotonic=1000.0,
        input_payload=payload,
        trigger="scheduled_daily",
        explicit_user_request=False,
        source_kind="fixture",
        copy_targets=(CopyTarget(offer_key, 1),),
    )


def _capture_c07_page(data_root: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for the capture-to-copy integration fixture")
    module_path = ROOT / "automation" / "inago" / "adapters" / "product_page_inputs.mjs"
    completed = subprocess.run(
        [node, "--input-type=module", "-e", _C07_CAPTURE_RUNNER, str(module_path), str(data_root)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_item_desc_full_inputs_and_guidance_reach_both_specialists(
    tmp_path: Path,
) -> None:
    html = '<span class="sale_desc">complete synthetic HTML</span>'
    item_html = '<span class="item_desc">complete item_desc synthetic HTML</span>'
    ocr_text = "English\nword\nfixture OCR text"
    payload = {
        "offer": {"offer_key": "copy-shop:copy-item", "draft_version": 1},
        "product_page_html": {"selectors": [
            {"key": "sale_desc", "selector": "span.sale_desc", "elements": [{"ref": "html:fixture:01", "outer_html": html}]},
            {"key": "item_desc", "selector": "span.item_desc", "elements": [{"ref": "html:fixture:02", "outer_html": item_html}]},
        ]},
        "product_page_ocr": {"images": [
            {"ref": "ocrimg:fixture:01", "status": "success", "text": ocr_text},
            {"ref": "ocrimg:fixture:02", "status": "success", "text": "item_desc OCR full text"},
        ]},
    }
    request = _job_request(payload, (
        "offer:copy-shop:copy-item", "html:fixture:01", "html:fixture:02",
        "ocrimg:fixture:01", "ocrimg:fixture:02",
    ))
    runner = AIJobRunner(
        data_root=tmp_path,
        skill_root=ROOT / ".agents" / "skills",
        agent_config_root=ROOT / ".codex" / "agents",
    )
    job_dir, _schema, _last_message, prompt = runner._stage_job(
        request, compute_input_hash(payload)
    )
    staged = json.loads((job_dir / "job-input.json").read_text(encoding="utf-8"))
    staged_skill = (
        job_dir
        / ".agents"
        / "skills"
        / "rr-inago-copywriter"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    staged_reviewer_skill = (
        job_dir
        / ".agents"
        / "skills"
        / "rr-inago-copy-reviewer"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    staged_writer_contract = (
        job_dir
        / ".agents"
        / "skills"
        / "rr-inago-copywriter"
        / "references"
        / "contracts.md"
    ).read_text(encoding="utf-8")
    staged_reviewer_contract = (
        job_dir
        / ".agents"
        / "skills"
        / "rr-inago-copy-reviewer"
        / "references"
        / "contracts.md"
    ).read_text(encoding="utf-8")
    staged_writing_rules = (
        job_dir
        / ".agents"
        / "skills"
        / "rr-inago-copywriter"
        / "references"
        / "文型ルール確定.md"
    ).read_text(encoding="utf-8")
    transport = prompt.decode("utf-8")

    assert staged["input_payload"] == payload
    transported_payload = json.loads(
        (job_dir / "job-input.json").read_text(encoding="utf-8")
    )["input_payload"]
    assert transported_payload == payload
    assert transported_payload["product_page_ocr"]["images"][0]["text"] == ocr_text
    assert transported_payload["product_page_html"]["selectors"][1]["elements"][0]["outer_html"] == item_html
    assert transported_payload["product_page_ocr"]["images"][1]["text"] == "item_desc OCR full text"
    assert "including text extracted from images within span.item_desc" in transport
    assert "`span.item_desc`内の画像から抽出されたOCRテキストも商品説明の参考" in staged_skill
    assert "span.item_desc" in staged_reviewer_skill and "画像から抽出されたOCR" in staged_reviewer_skill
    assert "`span.item_desc`内の画像OCR" in staged_writer_contract
    assert "span.item_desc内画像からのOCR" in staged_reviewer_contract
    assert "span.item_desc内画像からの抽出も含む" in staged_writing_rules
    assert "complete one-product input" in transport
    assert "join alphanumeric text across lines only when the word is clear" in transport
    assert FULL_GUIDANCE in staged_skill
    assert "資料データとして扱い" in staged_skill
    assert "used_input_refs" in staged_skill


def test_large_payload_is_never_silently_truncated(tmp_path: Path) -> None:
    large_text = "商品情報fixture0123456789\n" * 15000
    payload = {
        "offer": {"offer_key": "copy-shop:copy-item", "draft_version": 1},
        "product_page_html": {"selectors": [
            {"key": "sale_desc", "selector": "span.sale_desc", "elements": [{"ref": "html:large:01", "outer_html": "sale fixture"}]},
            {"key": "item_desc", "selector": "span.item_desc", "elements": [{"ref": "html:large:02", "outer_html": large_text}]},
        ]},
        "product_page_ocr": {"images": [
            {"ref": "ocrimg:large:01", "status": "success", "text": "sale OCR fixture"},
            {"ref": "ocrimg:large:02", "status": "success", "text": large_text},
        ]},
    }
    request = _job_request(payload, (
        "offer:copy-shop:copy-item", "html:large:01", "html:large:02",
        "ocrimg:large:01", "ocrimg:large:02",
    ))
    runner = AIJobRunner(
        data_root=tmp_path,
        skill_root=ROOT / ".agents" / "skills",
        agent_config_root=ROOT / ".codex" / "agents",
    )
    job_dir, _schema, _last_message, prompt = runner._stage_job(
        request, compute_input_hash(payload)
    )
    job_bytes = (job_dir / "job-input.json").read_bytes()

    assert len(large_text) > 200_000
    assert len(job_bytes) > 400_000
    staged = json.loads(job_bytes)
    assert staged["input_payload"] == payload
    assert len(staged["input_payload"]["product_page_html"]["selectors"][1]["elements"][0]["outer_html"]) == len(large_text)
    assert len(staged["input_payload"]["product_page_ocr"]["images"][1]["text"]) == len(large_text)
    assert len(prompt) > len(job_bytes)


def test_prompt_builder_keeps_product_and_newline_guidance(settings_runtime) -> None:
    from tests_inago.test_step09 import _build

    html = '<span class="sale_desc">complete fixture</span>'
    item_html = '<span class="item_desc">complete item_desc fixture</span>'
    ocr_text = "Latin\nword"
    context = _build(
        settings_runtime,
        input_payload={
            "offer": {"offer_key": "shop_fixture:item_1", "draft_version": 1},
            "product_page_html": {"selectors": [
                {"selector": "span.sale_desc", "elements": [{"ref": "html:fixture:01", "outer_html": html}]},
                {"selector": "span.item_desc", "elements": [{"ref": "html:fixture:02", "outer_html": item_html}]},
            ]},
            "product_page_ocr": {"images": [
                {"ref": "ocrimg:fixture:01", "status": "success", "text": ocr_text},
                {"ref": "ocrimg:fixture:02", "status": "success", "text": "item_desc OCR text"},
            ]},
        },
    )
    prompt = render_prompt(context)
    input_payload = json.loads(context["input_json"])["input_payload"]
    assert input_payload["product_page_html"]["selectors"][0]["elements"][0]["outer_html"] == html
    assert input_payload["product_page_ocr"]["images"][0]["text"] == ocr_text
    assert input_payload["product_page_html"]["selectors"][1]["elements"][0]["outer_html"] == item_html
    assert input_payload["product_page_ocr"]["images"][1]["text"] == "item_desc OCR text"
    assert "span.item_desc内の画像から抽出されたものを含む" in prompt
    assert FULL_GUIDANCE in prompt


def test_item_desc_capture_to_ocr_to_copy_inputs(
    tmp_path: Path,
    settings_runtime,
) -> None:
    capture = _capture_c07_page(tmp_path)
    page = capture["page"]
    assert capture["result"]["state"] == "captured"
    assert [image["status"] for image in page["images"]] == ["downloaded", "downloaded"]
    assert page["images"][1]["occurrences"][0]["selector"] == "span.item_desc"
    assert page["selectors"][-1]["selector"] == "span.item_desc"
    assert "Item description complete HTML fixture" in page["selectors"][-1]["outer_html"][0]

    class _Worker:
        def __init__(self) -> None:
            self.inputs: list[bytes] = []
            self.texts = iter(("sale OCR\ncomplete line", "item_desc OCR\ncomplete line"))

        def predict(self, value: bytes):
            self.inputs.append(value)
            return ([{"fixture": True}], next(self.texts))

        def close(self) -> None:
            pass

    worker = _Worker()
    ocr_engine = ProductPageOCR()
    ocr_engine._worker_for = lambda _run_id, _data_root: worker
    ocr_result = ocr_engine.process_product(
        None,
        run_id=page["run_id"],
        offer_key=f"{page['shop_id']}:{page['item_id']}",
        shop_id=page["shop_id"],
        item_id=page["item_id"],
        page_ref=page["page_ref"],
        source_evidence_ref=page["source_evidence_ref"],
        data_root=tmp_path,
    )
    expected_image_bytes = [
        b"capture-bytes:https://images.test/sale.png",
        b"capture-bytes:https://images.test/item.png",
    ]
    assert ocr_result["status"] == "complete"
    assert worker.inputs == expected_image_bytes
    ocr = load_product_page_ocr(
        tmp_path,
        page["run_id"],
        f"{page['shop_id']}:{page['item_id']}",
        page["shop_id"],
        page["item_id"],
        expected_page_ref=page["page_ref"],
        expected_ocr_ref=ocr_result["ocr_ref"],
    )
    assert ocr is not None
    assert [image["text"] for image in ocr["input_payload"]["product_page_ocr"]["images"]] == [
        "sale OCR\ncomplete line",
        "item_desc OCR\ncomplete line",
    ]

    connection, _page, _ocr, ocr_ref, spec = _copy_spec_fixture(
        tmp_path,
        settings_runtime,
        page_override=page,
        ocr_override=ocr,
        ocr_ref_override=ocr_result["ocr_ref"],
    )
    try:
        assert spec is not None
        _check_spec(spec)
        request = _job_request(
            spec.input_payload,
            spec.input_refs,
            run_id=page["run_id"],
            offer_key=f"{page['shop_id']}:{page['item_id']}",
        )
        runner = AIJobRunner(
            data_root=tmp_path,
            skill_root=ROOT / ".agents" / "skills",
            agent_config_root=ROOT / ".codex" / "agents",
        )
        job_dir, _schema, _last_message, prompt = runner._stage_job(
            request, compute_input_hash(spec.input_payload)
        )
        staged = json.loads((job_dir / "job-input.json").read_text(encoding="utf-8"))
        staged_html = staged["input_payload"]["product_page_html"]["selectors"]
        staged_ocr = staged["input_payload"]["product_page_ocr"]["images"]
        assert [entry["selector"] for entry in staged_html] == [
            "span.sale_desc",
            "span.normal_reserve_item_name",
            "span.normal_reserve_catch_copy",
            "span.item_desc",
        ]
        assert staged_html[-1]["elements"][0]["outer_html"] == page["selectors"][-1]["outer_html"][0]
        assert [entry["text"] for entry in staged_ocr] == [
            "sale OCR\ncomplete line",
            "item_desc OCR\ncomplete line",
        ]
        assert all(ref in staged["input_refs"] for ref in [page["page_ref"], ocr_ref, *(entry["ref"] for entry in staged_ocr)])
        assert "including text extracted from images within span.item_desc" in prompt.decode("utf-8")
    finally:
        connection.close()
