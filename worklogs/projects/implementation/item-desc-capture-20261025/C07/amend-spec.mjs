import { readFile, writeFile } from 'node:fs/promises';
import { loadDocument, serializeDocument, base, htmlName } from '../../../../../tools/docs/inago-requirements/document-source.mjs';

const data = await loadDocument();
if (data.version !== '2.62') throw new Error(`Expected specification 2.62, received ${data.version}`);

const title = '版：2.62（ROOM商品ページのitem_desc全文・全画像OCRを本文入力へ追加）。最終更新：2026-10-25 JST。';
if (!data.source.includes(title)) throw new Error('Specification title anchor is missing');
data.source = data.source.replace(title,
  '版：2.63（画像候補の未取得状態と遅延読込の有限待機を明示）。最終更新：2026-10-25 JST.');

const addition = '版2.63：画像候補URLの解析失敗や未対応schemeも無言で破棄せず、全occurrenceとsource hashを保ってinvalid_sourceまたはsource_type_errorとしてpage.jsonへ記録する。安全確認に合格しないURLは取得しない。data URIのbase64は文字集合・padding・復号結果を検証し、不正または0 byteならinline_decode_errorとしてdownloadedにしない。説明領域をscrollした後はDOMと画像sourceの変化を最大2500ms観測し、遅延sourceが解決しないか安定を確認できない場合はlazy_load_state=timed_out、未解決occurrence、image_state=lazy_load_incompleteを追加記録する。有限待機を超えた部分を完全取得済みと扱わない。局所的なdata/blob画像再試行でDOM再取得が失敗した時はsafe error classをretry診断として戻し、既存HTML・画像manifest・bytesは保持する。これらはschema_version=1の追加項目で、通常run resume・DB schema・画像URL安全条件は変えない。';
const anchor = 'capture/OCRの局所再呼出しと通常run resumeを区別し、後者の既存挙動は変更・保証範囲へ加えない。要件はIR-010/011/014/017/018/026/028、AT-ITEM-DESC-01、要件データ辞書と要件追跡表へ対応付ける。これは仕様の追加であり、実装・試験が完了した証拠ではない。';
if (!data.source.includes(anchor)) throw new Error('Item description requirement anchor is missing');
data.source = data.source.replace(anchor,
  'capture/OCRの局所再呼出しと通常run resumeを区別し、後者の既存挙動は変更・保証範囲へ加えない。要件はIR-010/011/014/017/018/026/028、AT-ITEM-DESC-01、要件データ辞書と要件追跡表へ対応付ける。これは仕様の追加であり、実装・試験が完了した証拠ではない。\n\n' + addition);
data.version = '2.63';
data.updated = '2026-10-25';

const url = new URL(htmlName, base);
const html = await readFile(url, 'utf8');
const updatedHtml = html.replace(
  /(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/,
  (_, open, close) => open + serializeDocument(data) + close,
);
if (updatedHtml === html) throw new Error('Canonical requirements-source snapshot was not replaced');
await writeFile(url, updatedHtml, 'utf8');
console.log('Canonical requirements source updated to 2.63 for STEP5 review follow-up.');
