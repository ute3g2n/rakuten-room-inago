import { readFile, writeFile } from 'node:fs/promises';
import { loadDocument, serializeDocument, base, htmlName } from '../../../../../tools/docs/inago-requirements/document-source.mjs';

const data = await loadDocument();
if (data.version !== '2.63') throw new Error(`Expected specification 2.63, received ${data.version}`);

const title = '版：2.63（画像候補の未取得状態と遅延読込の有限待機を明示）。最終更新：2026-10-25 JST.';
if (!data.source.includes(title)) throw new Error('Specification title anchor is missing');
data.source = data.source.replace(title,
  '版：2.64（画像source観測全体の有限deadlineと不正percent escapeを明示）。最終更新：2026-10-25 JST.');

const previous = '版2.63：画像候補URLの解析失敗や未対応schemeも無言で破棄せず、全occurrenceとsource hashを保ってinvalid_sourceまたはsource_type_errorとしてpage.jsonへ記録する。安全確認に合格しないURLは取得しない。data URIのbase64は文字集合・padding・復号結果を検証し、不正または0 byteならinline_decode_errorとしてdownloadedにしない。説明領域をscrollした後はDOMと画像sourceの変化を最大2500ms観測し、遅延sourceが解決しないか安定を確認できない場合はlazy_load_state=timed_out、未解決occurrence、image_state=lazy_load_incompleteを追加記録する。有限待機を超えた部分を完全取得済みと扱わない。局所的なdata/blob画像再試行でDOM再取得が失敗した時はsafe error classをretry診断として戻し、既存HTML・画像manifest・bytesは保持する。これらはschema_version=1の追加項目で、通常run resume・DB schema・画像URL安全条件は変えない。';
const revised = '版2.64：画像候補URLの解析失敗や未対応schemeも無言で破棄せず、全occurrenceとsource hashを保ってinvalid_sourceまたはsource_type_errorとしてpage.jsonへ記録する。安全確認に合格しないURLは取得しない。data URIのbase64は文字集合・padding・復号結果を検証し、不正または0 byteならinline_decode_errorとしてdownloadedにしない。非base64 data URIのpercent escapeも検証し、不正なescapeがあればinline_decode_errorとしてdownloadedにしない。説明領域の観測開始からDOM探索、scroll、animation frame待機、遅延source・DOM安定待機までを合わせて最大2500msとし、各待機に同じdeadlineを適用する。期限に達した場合は取得できた範囲を記録し、lazy_load_state=timed_out、lazy_load_reason=observation_timeout、未解決occurrence、image_state=lazy_load_incompleteを追加記録する。その他、未解決sourceやDOM安定未確認も理由とoccurrenceを記録し、有限待機を超えた部分を完全取得済みと扱わない。局所的なdata/blob画像再試行でDOM再取得が失敗した時はsafe error classをretry診断として戻し、既存HTML・画像manifest・bytesは保持する。これらはschema_version=1の追加項目で、通常run resume・DB schema・画像URL安全条件は変えない。';
if (!data.source.includes(previous)) throw new Error('Version 2.63 requirement anchor is missing');
data.source = data.source.replace(previous, revised);
data.version = '2.64';
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
  .replace('現在版2.63補足・2026-10-25 JST：正本要件HTML2.63。', '現在版2.64補足・2026-10-25 JST：正本要件HTML2.64。')
  .replace('base64 data URIは文字・padding・復号後byte数を検証し、無効/空byteは`inline_decode_error`とする。説明領域scroll後のDOM/source観測には最大2500msの有限上限を設け、未解決候補が残る場合は', 'data URIはbase64の文字・padding・復号後byte数と非base64 payloadのpercent escapeを検証し、不正/空byteは`inline_decode_error`とする。説明領域観測開始からDOM探索・scroll・animation frame待機・DOM/source安定待機までを合わせて最大2500msとし、待機全体に同じdeadlineを適用する。期限到達時は`lazy_load_state=timed_out`と`lazy_load_reason=observation_timeout`、未解決候補、`image_state=lazy_load_incomplete`を記録し、未解決候補や未安定も理由付きで残す。未解決候補が残る場合は');
if (updatedDictionary === dictionary) throw new Error('Dictionary version/contract anchors are missing');
updatedDictionary = updatedDictionary.replace('## 版2.63：商品ページ説明HTML・画像・OCR入力', '## 版2.64：商品ページ説明HTML・画像・OCR入力');
await writeFile(dictionaryPath, updatedDictionary, 'utf8');

const traceabilityPath = new URL('../../../../../docs/specifications/inago/要件追跡表.md', import.meta.url);
const traceability = await readFile(traceabilityPath, 'utf8');
const updatedTraceability = traceability
  .replace('現在版2.63補足・2026-10-25 JST：要件正本HTML2.63', '現在版2.64補足・2026-10-25 JST：要件正本HTML2.64')
  .replace('不正URL/未対応scheme・不正base64・遅延読込の有限timeout・局所data/blob再試行のDOM例外', '不正URL/未対応scheme・不正base64/percent escape・scrollとanimation frame待機を含む全体2500ms deadline・局所data/blob再試行のDOM例外');
if (updatedTraceability === traceability) throw new Error('Traceability version/contract anchors are missing');
await writeFile(traceabilityPath, updatedTraceability, 'utf8');

console.log('Canonical requirements source, dictionary, and traceability updated to 2.64.');
