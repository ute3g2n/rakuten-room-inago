import {readFile,writeFile} from 'node:fs/promises';
import {loadDocument,serializeDocument,base,review12,htmlName} from './document-source.mjs';
async function edit(name,fn){const p=new URL(name,import.meta.url);await writeFile(p,fn(await readFile(p,'utf8')));}
await edit('build.mjs',s=>s
 .replaceAll('29個の重要経路','31個の重要経路').replaceAll('AT-S01〜29','AT-S01〜31').replaceAll('U-20261003-01〜06','U-20261003-01〜07')
 .replaceAll('本人の報告まで予約する', '本人の報告を翌日日次で反映するまで予約する')
 .replaceAll('本人報告まで予約する', '本人報告の翌日日次反映まで予約する')
 .replaceAll('見送りは予約解除、未チェックだけでは解除しない。','見送り報告の翌日反映で予約を解除し、未チェックだけでは解除しない。')
 .replaceAll('本人操作はローカルサーバー経由でSQLiteへ保存する。','受付プログラムは夜の報告を保存し、翌日の日次が管理情報へ反映する。')
 .replaceAll('見送り後・過去日の実投稿報告も保存。同商品の後発本文には「既に投稿済み・投稿しない」を表示する。','過去本文の報告も受付保存し、翌日反映で同商品の後発本文を投稿禁止にする。')
 .replaceAll('本人の実投稿イベントを生成履歴と混ぜない。','夜の報告受付と翌日の管理反映を分け、本人投稿を生成履歴と混ぜない。')
 .replaceAll('操作保存', '報告受付保存')
 .replaceAll('本人が安定と判断後', '本人が安定と判断後')
 .replace(/sequence\(2, [\s\S]*?\n\]\);/, `figure(2, '自動処理・夜の本人投稿・翌日の管理反映を分ける', '自動処理は本人投稿を待たずに終了する。本人は21:30などに独立して投稿し、受付プログラムはその結果を未反映として保存する。翌日の日次は前日の報告を回収・反映してから今日の商品を探す。', 430, id =>
  textAt(420, 25, '日次制御プログラムは、今日13:00に自動処理を始めます。', 'label') + box(15, 48, 240, 'F00は前日の報告を', ['回収し、管理情報へ', '反映してから次へ進みます。'], 'system', 120) + box(300, 48, 240, 'F02は今日の商品を探し', ['本文を保存します。', '直後に成果を取得します。'], 'system', 120) + box(585, 48, 240, '日次制御は成果終了後', ['本人投稿を待たずに', '自動処理を終了します。'], 'store', 120) + arrow(id, 255, 106, 300, 106) + arrow(id, 540, 106, 585, 106) + textAt(420, 215, '本人は、自動処理と独立して21:30などに投稿します。', 'label') + box(15, 244, 240, '本人はROOMへ投稿し', ['HTMLで投稿結果を', 'チェックして報告します。'], 'human', 120) + box(300, 244, 240, '受付は報告を保存し', ['管理反映待ちと示します。', 'この時点では管理未更新。'], 'store', 120) + box(585, 244, 240, '翌日のF00は報告を', ['回収・反映し、F02の', '手順2へ進めます。'], 'system', 120) + arrow(id, 255, 301, 300, 301) + arrow(id, 540, 301, 585, 301, '', true) + textAt(420, 407, '上段と下段に開始待ちの矢印はありません。下段の破線は翌日の回収です。', 'small'));
`)
 .replace(/figure\(15, [\s\S]*?(?=\nsequence\(15,)/, `figure(15, '夜は報告を保存し、翌日の日次が管理情報へ反映する', '受付プログラムは、夜の投稿・見送り・訂正を未反映の報告として保存する。翌日の日次は報告を照合し、投稿済み・除外・予約と反映状態を同時確定してから商品探索を始める。', 420, id =>
  box(15, 28, 240, '本人は夜にROOMへ投稿し', ['HTMLで結果を報告します。', '見送り・訂正も報告します。'], 'human', 110) + box(300, 28, 240, '受付は報告を保存します', ['管理反映待ちと表示します。', '予約はまだ解除しません。'], 'store', 110) + box(585, 28, 240, '翌日の日次は報告を', ['回収し、元の本文の版と', '訂正の順を確認します。'], 'system', 110) + arrow(id, 255, 83, 300, 83) + arrow(id, 540, 83, 585, 83, '', true) + box(15, 212, 240, '保存担当は管理情報と', ['反映済みの印を同時に', '確定します。'], 'store', 110) + box(300, 212, 240, '日次制御は反映完了後に', ['F02手順2から今日の', '商品探索を始めます。'], 'system', 110) + box(585, 212, 240, '反映失敗時は日次制御が', ['商品探索を停止し、', '未反映の報告を残します。'], 'warning', 110) + route(id, 'M 705 138 V 181 H 135 V 212') + arrow(id, 255, 267, 300, 267) + textAt(420, 381, '受付保存の成功と管理反映の成功は、別の状態として表示します。', 'label'));
`)
 .replace(/sequence\(15, [\s\S]*?\n\]\);/, `sequence(15, '古い本文の報告も、翌日に元の版へ反映する', '本人は過去の本文で投稿した結果を夜に報告できる。受付は元の本文版を記録し、翌日の日次が実投稿と後発予約解除を反映する。過去の生成件数を減らさない。', [{ name: '本人', kind: 'human' }, { name: '報告受付', kind: 'system' }, { name: '翌日の日次', kind: 'store' }], [
  { from: 0, to: 1, label: '本人は古い本文の投稿を報告する' }, { from: 1, to: 0, label: '受付は管理反映待ちを表示する' }, { from: 1, to: 2, label: '翌日日次は未反映報告を回収する' }, { from: 2, to: 1, label: '日次は元版へ投稿と除外を確定する' }, { from: 2, to: 0, label: '画面は後発本文を投稿禁止と示す' },
]);
`)
 .replaceAll('観測→商品→本文→本人イベント', '観測→商品→本文→報告受付→翌日反映')
 .replaceAll('13:00に開始し、語句更新・検索・本文生成・保存が終わったら', '13:00に前日の報告を回収・管理反映し、語句更新・検索・本文生成・保存が終わったら')
 .replaceAll("['語句更新・検索・本文生成・保存']", "['前日報告の反映後に', '語句・検索・本文作成を実行']")
 .replaceAll('実投稿除外・未解決予約・訂正履歴は運用中保持', '実投稿除外・未解決予約・未反映報告・訂正履歴は運用中保持')
 .replaceAll('27章 / ', '28章 / ')
 .replaceAll('確定版 1.0 · 2026.09.30', 'HTML正本 1.2 · 2026.10.03')
 .replace('本人が投稿・見送りを報告するまで予約として残ります。', '本人の報告を翌日の日次で管理情報へ反映するまで予約として残ります。夜の報告受付と翌日の管理反映を分けます。')
);
await edit('finalize-html.mjs',s=>s.replace('旧資料の事前分析設計・17:45固定開始・旧提案値は現在要件へ適用しません。','旧資料の事前分析設計・チェック直後の管理更新・17:45固定開始・旧提案値は現在要件へ適用しません。'));
await edit('flow-renderer.mjs',s=>s.replace("  if (id === 'F03')", `  if (id === 'F00') {
    const lines = wrapText('日次制御プログラムは、前日の管理反映が完了した作業をF02の手順2へ渡し、今日のテーマ選択と語句更新を始めます。', 978, 20);
    content += arrow(id, 'M 556 '+(y-44)+' V '+(y-9));
    content += '<rect class="flow-transition" x="42" y="'+y+'" width="1028" height="'+(lines.length*29+42)+'" rx="10" fill="#e6edf8" stroke="#59649b"/>';
    content += paragraph(67,y+29,lines,20); y+=lines.length*29+68;
  }
  if (id === 'F03')`));
const d=await loadDocument();
d.source=d.source.replace('|\n\n| AT-S30','|\n| AT-S30')
 .replace('保存本文15、本人チェック後だけposted', '保存本文15、夜は報告受付、翌日日次反映後だけ管理posted')
 .replace('本日が今日', '本日が今日')
 .replace('現在版1.2は2026-10-03の本人依頼', '現在版1.2は2026-10-03の本人依頼')
 .replace('自動開始と本人投稿のフローを分離したもの。','自動開始と本人投稿のフローを分離し、夜の報告受付と翌日の日次での管理反映を分けたもの。');
const p=new URL(htmlName,base);await writeFile(p,(await readFile(p,'utf8')).replace(/(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+serializeDocument(d)+b));
await writeFile(new URL('現在要件全文.txt',review12),d.source);
console.log('Current source, summaries and original diagrams synchronized with next-day reconciliation.');
