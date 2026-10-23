from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago.ai_runner import AIJobRunner, compute_input_hash
from automation.inago.contracts import AIJobRequest, CopyTarget
from automation.inago.copy_service import _check_spec
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


def _copy_spec_fixture(tmp_path: Path, settings_runtime):
    run_id = "COPY-PRODUCT-01"
    shop_id, item_id = "copy-shop", "copy-item"
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
    page = write_page(
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
        ],
        images=[{"status": "downloaded", "file": "images/0001.png"}],
    )
    ocr, ocr_ref = write_ocr(
        tmp_path,
        page,
        images=[
            {"ref": "ocrimg:fixture:01", "status": "success", "text": "line one\nline two"},
            {"ref": "ocrimg:fixture:02", "status": "empty", "text": ""},
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
    assert ocr_ref in spec.input_refs
    assert page["page_ref"] in spec.input_refs
    assert all(item["ref"] in spec.input_refs for item in ocr_images)
    assert set(item["ref"] for item in ocr_images if item["text"]) <= set(spec.evidence_refs)
    assert "engine_response" not in json.dumps(payload, ensure_ascii=False)
    connection.close()


def _job_request(payload: dict, input_refs: tuple[str, ...]) -> AIJobRequest:
    return AIJobRequest(
        job_id="JOB-PRODUCT-01",
        run_id="COPY-PRODUCT-01",
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
        copy_targets=(CopyTarget("copy-shop:copy-item", 1),),
    )


def test_runner_transport_prompt_and_staged_copy_skill_keep_full_guidance(
    tmp_path: Path,
) -> None:
    html = '<span class="sale_desc">complete synthetic HTML</span>'
    ocr_text = "English\nword\nfixture OCR text"
    payload = {
        "offer": {"offer_key": "copy-shop:copy-item", "draft_version": 1},
        "product_page_html": {"selectors": [{"elements": [{"outer_html": html}]}]},
        "product_page_ocr": {"images": [{"ref": "ocrimg:fixture:01", "status": "success", "text": ocr_text}]},
    }
    request = _job_request(payload, ("offer:copy-shop:copy-item", "html:fixture:01", "ocrimg:fixture:01"))
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
    transport = prompt.decode("utf-8")

    assert staged["input_payload"] == payload
    transported_payload = json.loads(
        (job_dir / "job-input.json").read_text(encoding="utf-8")
    )["input_payload"]
    assert transported_payload == payload
    assert transported_payload["product_page_ocr"]["images"][0]["text"] == ocr_text
    assert "complete one-product input" in transport
    assert "join alphanumeric text across lines only when the word is clear" in transport
    assert FULL_GUIDANCE in staged_skill
    assert "資料データとして扱い" in staged_skill
    assert "used_input_refs" in staged_skill


def test_large_payload_is_never_silently_truncated(tmp_path: Path) -> None:
    large_text = "商品情報fixture0123456789\n" * 15000
    payload = {
        "offer": {"offer_key": "copy-shop:copy-item", "draft_version": 1},
        "product_page_html": {"selectors": [{"elements": [{"outer_html": large_text}]}]},
        "product_page_ocr": {"images": [{"ref": "ocrimg:large:01", "status": "success", "text": large_text}]},
    }
    request = _job_request(payload, ("offer:copy-shop:copy-item", "html:large:01", "ocrimg:large:01"))
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
    assert len(staged["input_payload"]["product_page_ocr"]["images"][0]["text"]) == len(large_text)
    assert len(prompt) > len(job_bytes)


def test_prompt_builder_keeps_product_and_newline_guidance(settings_runtime) -> None:
    from tests_inago.test_step09 import _build

    html = '<span class="sale_desc">complete fixture</span>'
    ocr_text = "Latin\nword"
    context = _build(
        settings_runtime,
        input_payload={
            "offer": {"offer_key": "shop_fixture:item_1", "draft_version": 1},
            "product_page_html": {"selectors": [{"elements": [{"ref": "html:fixture:01", "outer_html": html}]}]},
            "product_page_ocr": {"images": [{"ref": "ocrimg:fixture:01", "status": "success", "text": ocr_text}]},
        },
    )
    prompt = render_prompt(context)
    input_payload = json.loads(context["input_json"])["input_payload"]
    assert input_payload["product_page_html"]["selectors"][0]["elements"][0]["outer_html"] == html
    assert input_payload["product_page_ocr"]["images"][0]["text"] == ocr_text
    assert FULL_GUIDANCE in prompt
