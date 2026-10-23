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


def test_three_selectors_preserve_all_rendered_outer_html(tmp_path: Path) -> None:
    result = _capture(tmp_path, "captured")

    assert result["page"]["html_state"] == "captured"
    selectors = result["page"]["selectors"]
    assert [entry["selector"] for entry in selectors] == [
        "span.sale_desc",
        "span.normal_reserve_item_name",
        "span.normal_reserve_catch_copy",
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
    assert [item["status"] for item in selectors] == ["not_found", "empty", "captured"]

    failed = _capture(tmp_path, "evaluation_error")["page"]
    assert failed["html_state"] == "capture_failed"
    assert [item["status"] for item in failed["selectors"]] == [
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
