from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "automation" / "inago" / "adapters" / "product_page_inputs.mjs"


_RUNNER = r"""
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const [modulePath, root, mode] = process.argv.slice(1);
const { saveProductPageInputs } = await import(pathToFileURL(modulePath).href);
const node = (tagName, outerHTML, innerHTML = '', attrs = {}, children = []) => ({
  tagName: tagName.toUpperCase(), outerHTML, innerHTML, children,
  currentSrc: attrs.currentSrc || '',
  scrollIntoView() {},
  getAttribute(name) { return Object.hasOwn(attrs, name) ? attrs[name] : null; },
  querySelectorAll(selector) {
    const descendants = [];
    const walk = item => { for (const child of item.children) { descendants.push(child); walk(child); } };
    walk(this);
    return selector === '*' ? descendants : descendants.filter(item => selector.toLowerCase() === item.tagName.toLowerCase());
  },
});
const imageA = node('img', '<img src="https://images.test/a.png">', '', { src: 'https://images.test/a.png', currentSrc: 'https://images.test/a.png' });
const imageB = node('img', '<img src="https://images.test/b.png">', '', { src: 'https://images.test/b.png', currentSrc: 'https://images.test/b.png' });
const imageFailed = node('img', '<img src="https://images.test/failed.png">', '', { src: 'https://images.test/failed.png', currentSrc: 'https://images.test/failed.png' });
let sale, reserve, catchCopy;
if (mode === 'states') {
  sale = null;
  reserve = node('span', '<span class="normal_reserve_item_name"> </span>', '   ', { class: 'normal_reserve_item_name' });
  catchCopy = node('span', '<span class="normal_reserve_catch_copy">fixture catch copy</span>', 'fixture catch copy', { class: 'normal_reserve_catch_copy' });
} else if (mode === 'no_images') {
  sale = node('span', '<span class="sale_desc">fixture</span>', 'fixture', { class: 'sale_desc' });
  reserve = node('span', '<span class="normal_reserve_item_name">fixture name</span>', 'fixture name', { class: 'normal_reserve_item_name' });
  catchCopy = node('span', '<span class="normal_reserve_catch_copy">fixture catch</span>', 'fixture catch', { class: 'normal_reserve_catch_copy' });
} else {
  const children = mode === 'images' ? [imageA, imageB] : mode === 'download_failure' ? [imageFailed] : [imageA];
  sale = node('span', '<span class="sale_desc"><p>full synthetic description</p></span>', 'full synthetic description', { class: 'sale_desc', style: mode === 'images' ? 'background-image:url(https://images.test/a.png)' : '' }, children);
  reserve = node('span', '<span class="normal_reserve_item_name">fixture name</span>', 'fixture name', { class: 'normal_reserve_item_name' });
  catchCopy = node('span', '<span class="normal_reserve_catch_copy">fixture catch</span>', 'fixture catch', { class: 'normal_reserve_catch_copy' });
}
globalThis.document = { querySelectorAll(selector) {
  if (selector === 'span.sale_desc') return sale ? [sale] : [];
  if (selector === 'span.normal_reserve_item_name') return reserve ? [reserve] : [];
  if (selector === 'span.normal_reserve_catch_copy') return catchCopy ? [catchCopy] : [];
  return [];
} };
globalThis.location = { href: 'https://item.rakuten.co.jp/fixture-shop/fixture-item/' };
globalThis.requestAnimationFrame = callback => callback();
globalThis.getComputedStyle = element => ({ backgroundImage: element.getAttribute('style') || 'none' });
let evaluationCount = 0;
const page = {
  async evaluate(fn, arg) {
    if (mode === 'evaluation_error' || mode === 'retry_capture_failure' && evaluationCount++ === 0)
      throw new Error('fixture-evaluate-failure');
    return fn(arg);
  },
  request: { async get(url) { return { ok: () => !url.includes('/failed.'), status: () => url.includes('/failed.') ? 503 : 200, headers: () => ({ 'content-type': 'image/png' }), body: async () => Buffer.from('fixture-image') }; } },
};
const input = { page, product: { shop: 'fixture-shop', item: `fixture-${mode}`, url: location.href }, request: { run_id: `CAP-${mode}`, attempt_id: `CAP-${mode}-ATTEMPT` }, rowIndex: 1, config: { product_page_root: root }, resolveImageHost: async () => [{ address: '93.184.216.34' }] };
const first = await saveProductPageInputs(input);
const result = mode === 'retry_capture_failure' ? await saveProductPageInputs(input) : first;
const pagePath = `${root}/CAP-${mode}/shop-Zml4dHVyZS1zaG9w/item-${Buffer.from(`fixture-${mode}`).toString('base64url')}/page.json`;
console.log(JSON.stringify({ first, result, page: JSON.parse(await readFile(pagePath, 'utf8')) }));
"""


_ITEM_DESC_RUNNER = r"""
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [modulePath, root, mode] = process.argv.slice(1);
const { saveProductPageInputs } = await import(pathToFileURL(modulePath).href);
const node = (tagName, outerHTML, innerHTML = '', attrs = {}, children = []) => ({
  tagName: tagName.toUpperCase(), outerHTML, innerHTML, children, currentSrc: attrs.currentSrc || '',
  scrollIntoView() {
    if (attrs['data-on-scroll']) {
      attrs.src = attrs['data-on-scroll'];
      this.currentSrc = attrs['data-on-scroll'];
    }
  },
  getAttribute(name) { return Object.hasOwn(attrs, name) ? attrs[name] : null; },
  querySelectorAll(selector) {
    const descendants = [];
    const walk = item => { for (const child of item.children) { descendants.push(child); walk(child); } };
    walk(this);
    return selector === '*' ? descendants : descendants.filter(item => selector.toLowerCase() === item.tagName.toLowerCase());
  },
});
const image = (url, extra = {}) => node('img', `<img src="${url}">`, '', { src: url, currentSrc: url, ...extra });
const saleShared = image('https://images.test/shared.png');
const sale = node('span', '<span class="sale_desc"><p>sale full text</p></span>', 'sale full text', {
  class: 'sale_desc', style: 'background-image:url(https://images.test/shared.png)',
}, [saleShared, node('source', '<source srcset="https://images.test/one.png 1x, https://images.test/two.png 2x">', '', {
  srcset: 'https://images.test/one.png 1x, https://images.test/two.png 2x',
})]);
const dataUrl = 'data:image/png;base64,ZGF0YQ==';
const percentDataUrl = 'data:image/png,%89PNG%0D%0A';
const blobUrl = 'blob:https://item.rakuten.co.jp/fixture-blob';
const itemImages = [
  image('', { src: '', currentSrc: '', 'data-src': 'https://images.test/lazy.png', 'data-on-scroll': 'https://images.test/lazy.png' }),
  image(dataUrl),
  image(percentDataUrl),
  image(blobUrl),
];
const item = node('span', '<span class="item_desc"><p>item full text</p></span>', 'item full text', { class: 'item_desc' }, itemImages);
let saleMatches = [];
let itemMatches = [];
if (mode === 'item_only') itemMatches = [item];
else if (mode === 'item_empty') itemMatches = [node('span', '<span class="item_desc">   </span>', '   ', { class: 'item_desc' })];
else if (mode === 'both_multiple') {
  saleMatches = [sale, node('span', '<span class="sale_desc">second sale</span>', 'second sale', { class: 'sale_desc' })];
  itemMatches = [item, node('span', '<span class="item_desc">second item</span>', 'second item', { class: 'item_desc' })];
} else if (mode === 'images') { saleMatches = [sale]; itemMatches = [item]; }
else if (mode === 'retry' || mode === 'cleanup') {
  saleMatches = [node('span', '<span class="sale_desc">retry fixture</span>', 'retry fixture', { class: 'sale_desc' }, [
    image('https://images.test/success.png'), image('https://images.test/retry.png'),
  ])];
} else if (mode === 'inline_retry') {
  itemMatches = [node('span', '<span class="item_desc"><img></span>', 'inline retry', { class: 'item_desc' }, [image(blobUrl)])];
} else if (mode === 'invalid_sources') {
  itemMatches = [node('span', '<span class="item_desc">invalid source fixtures</span>', 'invalid source fixtures', { class: 'item_desc' }, [
    image('ftp://images.test/not-supported.png'),
    image('http://['),
    image('data:image/png;base64,%%%'),
  ])];
} else if (mode === 'lazy_timeout') {
  itemMatches = [node('span', '<span class="item_desc"><img data-src="data:image/png;base64,ZGF0YQ=="></span>', 'lazy timeout fixture', { class: 'item_desc' }, [
    image('', { src: '', currentSrc: '', 'data-src': dataUrl }),
  ])];
} else if (mode === 'inline_retry_evaluation_error') {
  itemMatches = [node('span', '<span class="item_desc"><img></span>', 'inline retry fixture', { class: 'item_desc' }, [image(blobUrl)])];
} else if (mode === 'invalid_percent_data') {
  itemMatches = [node('span', '<span class="item_desc"><img src="data:image/png,%ZZ"></span>', 'invalid percent fixture', { class: 'item_desc' }, [
    image('data:image/png,%ZZ'),
  ])];
} else if (mode === 'uppercase_data_scheme') {
  itemMatches = [node('span', '<span class="item_desc"><img src="DATA:image/png;base64,ZGF0YQ=="></span>', 'uppercase data scheme fixture', { class: 'item_desc' }, [
    image('DATA:image/png;base64,ZGF0YQ=='),
  ])];
} else if (mode === 'raf_timeout') {
  itemMatches = [node('span', '<span class="item_desc"><img data-src="data:image/png;base64,ZGF0YQ=="></span>', 'raf timeout fixture', { class: 'item_desc' }, [
    image('', { src: '', currentSrc: '', 'data-src': dataUrl }),
  ])];
}
globalThis.document = { querySelectorAll(selector) {
  if (selector === 'span.sale_desc') return saleMatches;
  if (selector === 'span.item_desc') return itemMatches;
  if (selector === 'span.normal_reserve_item_name' || selector === 'span.normal_reserve_catch_copy') return [];
  return [];
} };
globalThis.location = { href: 'https://item.rakuten.co.jp/fixture-shop/fixture-item/' };
globalThis.requestAnimationFrame = mode === 'raf_timeout' ? () => {} : callback => callback();
globalThis.getComputedStyle = element => ({ backgroundImage: element.getAttribute('style') || 'none' });
let inlineFetchCount = 0;
globalThis.fetch = async () => {
  inlineFetchCount++;
  const failed = ['inline_retry', 'inline_retry_evaluation_error'].includes(mode) && inlineFetchCount === 1;
  return { ok: !failed, headers: { get: () => 'image/svg+xml' }, arrayBuffer: async () => Buffer.from('blob-bytes') };
};
let evaluateCount = 0;
const requestCounts = new Map();
const page = {
  async evaluate(fn, arg) {
    evaluateCount++;
    if (mode === 'inline_retry_evaluation_error' && evaluateCount > 1)
      throw new TypeError('fixture-inline-recapture-failure');
    return fn(arg);
  },
  request: { async get(url) {
    const count = (requestCounts.get(url) || 0) + 1;
    requestCounts.set(url, count);
    const failed = url.endsWith('/retry.png') && count === 1;
    return {
      ok: () => !failed, status: () => failed ? 503 : 200,
      headers: () => ({ 'content-type': 'image/png' }),
      body: async () => Buffer.from(`bytes:${url}`),
    };
  } },
};
const product = { shop: 'fixture-shop', item: `fixture-${mode}`, url: location.href };
const request = { run_id: `CAP-${mode}`, attempt_id: `CAP-${mode}-ATTEMPT` };
const config = { product_page_root: root };
const input = { page, product, request, rowIndex: 1, config, resolveImageHost: async () => [{ address: '93.184.216.34' }] };
const folder = path.join(root, `CAP-${mode}`, `shop-${Buffer.from(product.shop).toString('base64url')}`, `item-${Buffer.from(product.item).toString('base64url')}`);
if (mode === 'legacy') {
  await mkdir(folder, { recursive: true });
  await writeFile(path.join(folder, 'page.json'), JSON.stringify({
    schema_version: 1, page_ref: 'legacy-page-ref', run_id: request.run_id, shop_id: product.shop,
    item_id: product.item, item_url: product.url, html_state: 'captured',
    selectors: [{ key: 'sale_desc', selector: 'span.sale_desc', status: 'captured', outer_html: ['<span class="sale_desc">legacy</span>'] }],
    image_state: 'captured', images: [{ index: 0, url: 'https://images.test/retry.png', status: 'http_error', file: null }],
  }));
}
const first = await saveProductPageInputs(input);
let normal = null;
let retried = null;
let pageBeforeRetry = null;
if (mode === 'retry' || mode === 'inline_retry' || mode === 'inline_retry_evaluation_error') {
  normal = await saveProductPageInputs(input);
  if (mode === 'inline_retry_evaluation_error')
    pageBeforeRetry = JSON.parse(await readFile(path.join(folder, 'page.json'), 'utf8'));
  retried = await saveProductPageInputs({ ...input, retryFailedImages: true });
} else if (mode === 'cleanup') {
  await writeFile(path.join(folder, 'cleanup.json'), JSON.stringify({ status: 'complete' }));
  retried = await saveProductPageInputs({ ...input, retryFailedImages: true });
} else if (mode === 'legacy') {
  normal = await saveProductPageInputs(input);
}
const pageValue = JSON.parse(await readFile(path.join(folder, 'page.json'), 'utf8'));
const bytesByFile = {};
for (const record of pageValue.images || []) {
  if (record.file) bytesByFile[record.file] = (await readFile(path.join(folder, record.file))).toString('base64');
}
console.log(JSON.stringify({ first, normal, retried, pageBeforeRetry, page: pageValue, evaluateCount,
  requestCounts: Object.fromEntries(requestCounts), inlineFetchCount, bytesByFile }));
"""


def _capture(tmp_path: Path, mode: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for product page capture tests")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", _RUNNER, str(MODULE), str(tmp_path), mode],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _capture_item_desc(tmp_path: Path, mode: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for product page capture tests")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", _ITEM_DESC_RUNNER, str(MODULE), str(tmp_path), mode],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_existing_selectors_preserve_order_and_rendered_outer_html(tmp_path: Path) -> None:
    result = _capture(tmp_path, "captured")

    assert result["page"]["html_state"] == "captured"
    selectors = result["page"]["selectors"]
    assert [entry["selector"] for entry in selectors] == [
        "span.sale_desc",
        "span.normal_reserve_item_name",
        "span.normal_reserve_catch_copy",
        "span.item_desc",
    ]
    assert selectors[0]["outer_html"] == [
        '<span class="sale_desc"><p>full synthetic description</p></span>'
    ]
    assert "full synthetic description" in selectors[0]["outer_html"][0]
    assert selectors[1]["outer_html"] == [
        '<span class="normal_reserve_item_name">fixture name</span>'
    ]


def test_missing_empty_and_failed_selector_states_are_distinct(tmp_path: Path) -> None:
    result = _capture(tmp_path, "states")
    selectors = result["page"]["selectors"]
    assert [item["status"] for item in selectors] == [
        "not_found",
        "empty",
        "captured",
        "not_found",
    ]

    failed = _capture(tmp_path, "evaluation_error")["page"]
    assert failed["html_state"] == "capture_failed"
    assert [item["status"] for item in failed["selectors"]] == [
        "unavailable",
        "unavailable",
        "unavailable",
        "unavailable",
    ]


def test_all_image_candidates_are_captured_in_occurrence_order(tmp_path: Path) -> None:
    result = _capture(tmp_path, "images")["page"]

    assert len(result["images"]) == 2
    assert result["images"][0]["url"] == "https://images.test/a.png"
    assert result["images"][1]["url"] == "https://images.test/b.png"
    assert len(result["images"][0]["occurrences"]) >= 2
    assert result["images"][0]["occurrences"][0]["order"] < result["images"][1]["occurrences"][0]["order"]
    assert all(item["status"] == "downloaded" for item in result["images"])


def test_download_failure_and_zero_images_preserve_capture_states(tmp_path: Path) -> None:
    failed = _capture(tmp_path, "download_failure")["page"]
    assert failed["image_state"] == "captured"
    assert len(failed["images"]) == 1
    assert failed["images"][0]["status"] == "http_error"

    empty = _capture(tmp_path, "no_images")["page"]
    assert empty["image_state"] == "no_images"
    assert empty["images"] == []


def test_failed_capture_can_be_retried_and_replaced_by_a_complete_page(
    tmp_path: Path,
) -> None:
    result = _capture(tmp_path, "retry_capture_failure")

    assert result["first"]["state"] == "capture_failed"
    assert result["result"]["state"] == "captured"
    assert result["page"]["html_state"] == "captured"


def test_description_selectors_preserve_all_html_and_states(
    tmp_path: Path,
) -> None:
    only_item = _capture_item_desc(tmp_path / "item-only", "item_only")["page"]
    selectors = only_item["selectors"]
    assert [entry["selector"] for entry in selectors] == [
        "span.sale_desc",
        "span.normal_reserve_item_name",
        "span.normal_reserve_catch_copy",
        "span.item_desc",
    ]
    assert selectors[-1] == {
        "key": "item_desc",
        "selector": "span.item_desc",
        "status": "captured",
        "outer_html": ['<span class="item_desc"><p>item full text</p></span>'],
    }

    both = _capture_item_desc(tmp_path / "both", "both_multiple")["page"]
    assert both["selectors"][0]["outer_html"] == [
        '<span class="sale_desc"><p>sale full text</p></span>',
        '<span class="sale_desc">second sale</span>',
    ]
    assert both["selectors"][-1]["outer_html"] == [
        '<span class="item_desc"><p>item full text</p></span>',
        '<span class="item_desc">second item</span>',
    ]
    empty = _capture_item_desc(tmp_path / "empty", "item_empty")["page"]
    assert empty["selectors"][-1]["status"] == "empty"
    assert empty["selectors"][-1]["outer_html"] == ['<span class="item_desc">   </span>']


def test_description_images_keep_all_candidates_and_origins(
    tmp_path: Path,
) -> None:
    result = _capture_item_desc(tmp_path, "images")
    page = result["page"]
    assert len(page["images"]) == 7
    assert [entry["status"] for entry in page["images"]] == ["downloaded"] * 7

    shared = next(image for image in page["images"] if image["url"] == "https://images.test/shared.png")
    shared_origins = shared["occurrences"]
    assert {origin["selector"] for origin in shared_origins} == {"span.sale_desc"}
    assert {origin["attribute"] for origin in shared_origins} >= {"style", "currentSrc", "src"}
    assert all({"match_index", "dom_position", "order", "tag"} <= origin.keys() for origin in shared_origins)

    lazy = next(image for image in page["images"] if image["url"] == "https://images.test/lazy.png")
    assert {origin["selector"] for origin in lazy["occurrences"]} == {"span.item_desc"}
    assert {origin["attribute"] for origin in lazy["occurrences"]} >= {"currentSrc", "src", "data-src"}

    data_image = next(image for image in page["images"] if image.get("source_kind") == "data")
    blob_image = next(image for image in page["images"] if image.get("source_kind") == "blob")
    assert data_image["url"].startswith("data:sha256:")
    assert blob_image["url"].startswith("blob:sha256:")
    assert data_image["byte_length"] == 4
    assert blob_image["byte_length"] == len(b"blob-bytes")
    assert result["bytesByFile"][data_image["file"]] == "ZGF0YQ=="
    assert result["bytesByFile"][blob_image["file"]] == "YmxvYi1ieXRlcw=="
    percent_data = next(image for image in page["images"] if image.get("source_kind") == "data" and image["byte_length"] == 6)
    assert result["bytesByFile"][percent_data["file"]] == "iVBORw0K"


def test_description_image_retry_preserves_manifest_and_legacy_capture(
    tmp_path: Path,
) -> None:
    result = _capture_item_desc(tmp_path, "retry")
    assert result["first"]["state"] == "captured"
    assert result["normal"]["state"] == "existing_preserved"
    assert result["retried"]["state"] == "images_retried"
    assert result["evaluateCount"] == 1
    assert result["requestCounts"] == {
        "https://images.test/success.png": 1,
        "https://images.test/retry.png": 2,
    }
    assert [image["status"] for image in result["page"]["images"]] == [
        "downloaded",
        "downloaded",
    ]
    assert result["page"]["selectors"][0]["outer_html"] == [
        '<span class="sale_desc">retry fixture</span>'
    ]

    inline = _capture_item_desc(tmp_path / "inline", "inline_retry")
    assert inline["first"]["state"] == "captured"
    assert inline["normal"]["state"] == "existing_preserved"
    assert inline["retried"]["state"] == "images_retried"
    assert inline["evaluateCount"] == 2
    assert inline["inlineFetchCount"] == 2
    assert inline["requestCounts"] == {}
    assert inline["page"]["images"][0]["status"] == "downloaded"
    assert inline["bytesByFile"][inline["page"]["images"][0]["file"]] == "YmxvYi1ieXRlcw=="


def test_invalid_image_sources_are_recorded_without_being_fetched(
    tmp_path: Path,
) -> None:
    result = _capture_item_desc(tmp_path, "invalid_sources")
    images = result["page"]["images"]
    assert [image["status"] for image in images] == [
        "source_type_error",
        "invalid_source",
        "inline_decode_error",
    ]
    assert all(image["file"] is None for image in images)
    assert all(image["byte_length"] is None for image in images)
    assert all(image["occurrences"][0]["selector"] == "span.item_desc" for image in images)
    assert all(len(image["source_url_sha256"]) == 64 for image in images)
    assert result["requestCounts"] == {}


def test_unresolved_lazy_image_is_marked_incomplete_after_finite_wait(
    tmp_path: Path,
) -> None:
    result = _capture_item_desc(tmp_path, "lazy_timeout")
    assert result["page"]["lazy_load_state"] == "timed_out"
    assert result["page"]["image_state"] == "lazy_load_incomplete"
    assert result["page"]["lazy_load_unresolved"] == [
        {
            "key": "item_desc",
            "selector": "span.item_desc",
            "match_index": 0,
            "dom_position": 1,
            "attribute": "data-src",
            "tag": "img",
        }
    ]


def test_inline_retry_reports_dom_recapture_exception_and_preserves_capture(
    tmp_path: Path,
) -> None:
    result = _capture_item_desc(tmp_path, "inline_retry_evaluation_error")
    assert result["retried"]["state"] == "existing_preserved"
    assert result["retried"]["retry_diagnostic"] == {
        "status": "failed",
        "reason": "dom_recapture_failed",
        "error_class": "TypeError",
    }
    assert result["page"] == result["pageBeforeRetry"]
    assert result["page"]["images"][0]["status"] == "inline_fetch_error"


def test_invalid_percent_escape_in_data_uri_is_not_saved_as_downloaded(
    tmp_path: Path,
) -> None:
    result = _capture_item_desc(tmp_path, "invalid_percent_data")
    image = result["page"]["images"][0]
    assert image["status"] == "inline_decode_error"
    assert image["file"] is None
    assert image["byte_length"] is None


def test_uppercase_data_scheme_is_decoded_case_insensitively(tmp_path: Path) -> None:
    result = _capture_item_desc(tmp_path, "uppercase_data_scheme")
    image = result["page"]["images"][0]
    assert image["source_kind"] == "data"
    assert image["status"] == "downloaded"
    assert image["file"] is not None
    assert result["bytesByFile"][image["file"]] == "ZGF0YQ=="


def test_animation_frame_waits_share_the_finite_observation_deadline(
    tmp_path: Path,
) -> None:
    result = _capture_item_desc(tmp_path, "raf_timeout")
    assert result["page"]["lazy_load_state"] == "timed_out"
    assert result["page"]["lazy_load_reason"] == "observation_timeout"
    assert result["page"]["image_state"] == "lazy_load_incomplete"
    assert result["page"]["lazy_load_observed_ms"] <= 2550


def test_cleanup_and_legacy_captures_are_never_refetched(tmp_path: Path) -> None:
    cleanup = _capture_item_desc(tmp_path / "cleanup", "cleanup")
    assert cleanup["retried"]["state"] == "existing_preserved"
    assert cleanup["requestCounts"]["https://images.test/retry.png"] == 1

    legacy = _capture_item_desc(tmp_path / "legacy", "legacy")
    assert legacy["first"]["state"] == "existing_preserved"
    assert legacy["normal"]["state"] == "existing_preserved"
    assert legacy["evaluateCount"] == 0
    assert legacy["requestCounts"] == {}
    assert legacy["page"]["selectors"] == [
        {
            "key": "sale_desc",
            "selector": "span.sale_desc",
            "status": "captured",
            "outer_html": ['<span class="sale_desc">legacy</span>'],
        }
    ]
