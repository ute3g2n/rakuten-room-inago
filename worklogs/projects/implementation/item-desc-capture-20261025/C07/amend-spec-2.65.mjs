import { readFile, writeFile } from 'node:fs/promises';
import { loadDocument, serializeDocument, base, htmlName } from '../../../../../tools/docs/inago-requirements/document-source.mjs';

const data = await loadDocument();
if (data.version !== '2.64') throw new Error(`Expected specification 2.64, received ${data.version}`);

const title = '版：2.64（画像source観測全体の有限deadlineと不正percent escapeを明示）。最終更新：2026-10-25 JST.';
if (!data.source.includes(title)) throw new Error('Specification title anchor is missing');
data.source = data.source.replace(title,
  '版：2.65（data URI schemeの大文字小文字を区別しない）。最終更新：2026-10-25 JST.');

const previous = 'data URIのbase64は文字集合・padding・復号結果を検証し、不正または0 byteならinline_decode_errorとしてdownloadedにしない。';
if (!data.source.includes(previous)) throw new Error('Data URI validation anchor is missing');
data.source = data.source.replace(previous,
  'data URIのscheme marker `data:`は大文字小文字を区別せず認識する。base64は文字集合・padding・復号結果を検証し、不正または0 byteならinline_decode_errorとしてdownloadedにしない。');
data.source = data.source.replace('版2.64：画像候補URL', '版2.65：画像候補URL');
data.version = '2.65';
data.updated = '2026-10-25';

const url = new URL(htmlName, base);
const html = await readFile(url, 'utf8');
const updatedHtml = html.replace(
  /(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/,
  (_, open, close) => open + serializeDocument(data) + close,
);
if (updatedHtml === html) throw new Error('Canonical requirements-source snapshot was not replaced');
await writeFile(url, updatedHtml, 'utf8');

const dictionaryPath = new URL('../../../../../docs/specifications/inago/要件データ辞書.md', import.meta.url);
const dictionary = await readFile(dictionaryPath, 'utf8');
let updatedDictionary = dictionary
  .replace('現在版2.64補足・2026-10-25 JST：正本要件HTML2.64。', '現在版2.65補足・2026-10-25 JST：正本要件HTML2.65。')
  .replace('data URIはbase64の文字・padding・復号後byte数と非base64 payloadのpercent escapeを検証し、不正/空byteは', '`data:` scheme markerは大文字小文字を区別せず認識する。data URIはbase64の文字・padding・復号後byte数と非base64 payloadのpercent escapeを検証し、不正/空byteは');
if (updatedDictionary === dictionary) throw new Error('Dictionary version/data URI anchors are missing');
updatedDictionary = updatedDictionary.replace('## 版2.64：商品ページ説明HTML・画像・OCR入力', '## 版2.65：商品ページ説明HTML・画像・OCR入力');
await writeFile(dictionaryPath, updatedDictionary, 'utf8');

const traceabilityPath = new URL('../../../../../docs/specifications/inago/要件追跡表.md', import.meta.url);
const traceability = await readFile(traceabilityPath, 'utf8');
const updatedTraceability = traceability
  .replace('現在版2.64補足・2026-10-25 JST：要件正本HTML2.64', '現在版2.65補足・2026-10-25 JST：要件正本HTML2.65')
  .replace('不正base64/percent escape・scrollとanimation frame待機を含む全体2500ms deadline', '大文字小文字を区別しないdata URI schemeと不正base64/percent escape・scrollとanimation frame待機を含む全体2500ms deadline');
if (updatedTraceability === traceability) throw new Error('Traceability version/data URI anchors are missing');
await writeFile(traceabilityPath, updatedTraceability, 'utf8');

console.log('Canonical requirements source, dictionary, and traceability updated to 2.65.');
