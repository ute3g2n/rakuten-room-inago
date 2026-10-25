import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import {
  base,
  htmlName,
  loadDocument,
} from '../../../../../tools/docs/inago-requirements/document-source.mjs';

const data = await loadDocument();
const html = await readFile(new URL(htmlName, base), 'utf8');
const source = data.source;
const required = [
  'span.item_desc',
  'page.json',
  'ocr.json',
  'AT-ITEM-DESC-01',
  'sale_desc/item_desc',
  '通常run resume',
  '本文作成AIと独立レビュー担当',
  'rr-inago-copy-reviewer',
  '27.21 2026-10-25',
];

for (const value of required) {
  assert.ok(source.includes(value), `canonical source is missing: ${value}`);
}
assert.equal(data.version, '2.62');
assert.equal(data.updated, '2026-10-25');
assert.equal((source.match(/^## (?:[1-9]|1\d|2[0-8])\. /gm) || []).length, 28);
assert.ok(html.includes('span.item_desc'));
assert.ok(html.includes('AT-ITEM-DESC-01'));

for (const name of ['要件データ辞書.md', '要件追跡表.md']) {
  const embedded = Buffer.from(data.references[name], 'base64');
  const current = await readFile(new URL(name, base));
  assert.deepEqual(embedded, current, `embedded reference differs: ${name}`);
  assert.ok(current.toString('utf8').includes('現在版2.62補足'));
}
assert.equal(Object.keys(data.references).length, 26);

console.log(JSON.stringify({
  status: 'PASS',
  version: data.version,
  main_chapters: 28,
  required_checks: required.length,
  embedded_references: Object.keys(data.references).length,
  html_sha256: createHash('sha256').update(html).digest('hex'),
  html_bytes: Buffer.byteLength(html),
}, null, 2));
