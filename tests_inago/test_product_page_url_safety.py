from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "automation" / "inago" / "adapters" / "product_page_inputs.mjs"


_NODE_RUNNER = r"""
import { pathToFileURL } from 'node:url';
const [modulePath, mode] = process.argv.slice(1);
const { fetchPublicImage, isSafePublicImageUrl } = await import(pathToFileURL(modulePath).href);
const publicResolver = async () => [{ address: '8.8.8.8', family: 4 }];
if (mode === 'private_hosts') {
  const privateResolver = async () => [{ address: '192.168.1.20', family: 4 }];
  const cases = await Promise.all([
    isSafePublicImageUrl('https://images.example/item.png', { resolveHost: privateResolver }),
    isSafePublicImageUrl('http://127.0.0.1/admin', { resolveHost: publicResolver }),
    isSafePublicImageUrl('http://localhost/admin', { resolveHost: publicResolver }),
    isSafePublicImageUrl('https://images.example/item.png', { resolveHost: async () => [
      { address: '8.8.8.8', family: 4 }, { address: '10.0.0.2', family: 4 },
    ] }),
  ]);
  console.log(JSON.stringify({ cases }));
} else if (mode === 'private_redirect') {
  const requests = [];
  const request = { async get(url, options) {
    requests.push({ url, maxRedirects: options.maxRedirects });
    return {
      ok: () => false,
      status: () => 302,
      headers: () => ({ location: 'http://127.0.0.1/admin' }),
      body: async () => Buffer.from('unused'),
    };
  } };
  const result = await fetchPublicImage(request, 'https://images.example/item.png', {
    resolveHost: publicResolver,
  });
  console.log(JSON.stringify({ status: result.status, requests }));
} else if (mode === 'html_response') {
  const requests = [];
  const request = { async get(url, options) {
    requests.push({ url, maxRedirects: options.maxRedirects });
    return {
      ok: () => true,
      status: () => 200,
      headers: () => ({ 'content-type': 'text/html; charset=utf-8' }),
      body: async () => Buffer.from('unused'),
    };
  } };
  const result = await fetchPublicImage(request, 'https://images.example/item.png', {
    resolveHost: publicResolver,
  });
  console.log(JSON.stringify({ status: result.status, requests }));
}
"""


def _node_json(mode: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for product page URL safety tests")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", _NODE_RUNNER, str(MODULE), mode],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_private_or_mixed_dns_answers_are_rejected() -> None:
    result = _node_json("private_hosts")

    assert result["cases"] == [False, False, False, False]


def test_private_redirect_is_not_requested() -> None:
    result = _node_json("private_redirect")

    assert result["status"] == "url_rejected"
    assert len(result["requests"]) == 1
    assert result["requests"][0]["url"] == "https://images.example/item.png"
    assert result["requests"][0]["maxRedirects"] == 0


def test_non_image_response_is_not_accepted_as_a_download() -> None:
    result = _node_json("html_response")

    assert result["status"] == "content_type_error"
    assert len(result["requests"]) == 1
    assert result["requests"][0]["maxRedirects"] == 0
