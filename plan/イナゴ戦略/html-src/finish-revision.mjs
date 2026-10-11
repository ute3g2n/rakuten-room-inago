import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { loadDocument, serializeDocument, base, htmlName } from './document-source.mjs';
async function edit(name, fn) { const p = new URL(name, import.meta.url); await writeFile(p, fn(await readFile(p, 'utf8')), 'utf8'); }
await edit('build.mjs', s => s
  .replace("'事前の分類・文型の採用、随時の設定変更は本人が判断する。'", "'実装者は別計画の確定結果を受け取る。本人は運用中の設定変更を判断する。'")
  .replace(/^  \['決定済みのルールと、実装時.*$/m, "  ['事前分析は実装前に別計画で行い、その確定結果を実装の前提にする。', ['実装者はジャンル・30テーマ・文型・長さとタグ目安を受け取る。', '実装者は日次の公開UI取得と成果接続を、実装後のテストで確認する。']],")
  .replace(/^  \['キーワードなし・コメント10以上.*$/m, "  ['実装者は別計画で確定した分類と文章の方針を、実装前に受け取る。', ['本システムは事前分析を実行しない。専用スクリプト・AI・skillを作成しない。', '必要な結果が不足する場合、実装者は不足を示して別計画側の確定を待つ。']],")
  .replace(/^  \['事前標本から5ジャンル.*$/m, "  ['実装者は外部で確定した5ジャンルと各6テーマ、合計30テーマを登録する。', ['実装者は受け取った名前・定義・所属をそのまま初期設定へ反映する。', '商品選択プログラムは主テーマと補助テーマを記録し、商品重複を除く。']],")
  .replace(/^  \['人気投稿から文章の組み立て方.*$/m, "  ['本文作成AIは外部で確定した文章の型・長さ・タグ目安を使って新しい本文を作る。', ['本システムは人気投稿から型や目安を抽出・提案しない。', '本文作成AIは目安を超えた商品を保留しない。本人は実投稿後の記録で効果を確かめる。']],")
  .replaceAll('現在版1.1は業務要件1.0と、本人の追加依頼・時刻訂正を統合したもの。', '現在版1.2は追加依頼・時刻訂正・説明改訂・事前分析の対象外化を統合したもの。')
  .replaceAll('U-20261003-01〜03', 'U-20261003-01〜05')
  .replaceAll('11タスクを12の図', '10タスクを11の図')
  .replaceAll('8つのAI役割と8skills', '7つのAI役割と7skills')
  .replaceAll('分類・語の意味・本文・説明', '語の意味・本文・説明')
  .replace(/figure\(5, [\s\S]*?(?=\nfigure\(6,)/, `figure(5, '別計画の確定結果を、実装の前提として受け取る', '事前分析は実装前の別計画で完了する。実装者はその結果を受け取り、初期設定へ登録する。本システムは事前分析の手順や担当を持たない。', 240, id =>
  box(15, 30, 240, '実装者は確定結果を', ['別計画から受け取ります。', '分類・文章の型・目安が', '実装の前提になります。'], 'human', 130) + box(300, 30, 240, '実装者は受領結果を', ['初期設定へ登録します。', '結果が不足する場合は、', '別計画の確定を待ちます。'], 'system', 130) + box(585, 30, 240, '日次処理は登録設定を', ['商品選びと文章作りに', '使います。事前分析を', '実行しません。'], 'store', 130) + arrow(id, 255, 95, 300, 95) + arrow(id, 540, 95, 585, 95) + textAt(420, 207, '実装者は事前分析用スクリプト・AI・skillを作りません。', 'small'));
`)
  .replaceAll('名前は事前分析後に本人が確定する。', '実装者は実装前に別計画で確定した名前と定義を受け取る。')
  .replaceAll('各テーマ：定義・所属・根拠・不足・本人確認・設定版', '実装者は各テーマの定義・所属・確定参照・版を登録します。')
  .replace(/figure\(7, [\s\S]*?(?=\nfigure\(8,)/, `figure(7, '本文作成AIは、確定済みの型と目安を使う', '実装者は別計画の確定結果を初期設定へ登録する。本文作成AIはその型と目安を参考に新しい文章を作る。本人は実投稿後の記録で効果を確かめる。', 238, id =>
  box(15, 28, 240, '実装者は文章の型と', ['長さ・タグの目安を', '確定結果から登録します。'], 'system', 118) + box(300, 28, 240, '本文作成AIは設定を', ['読み、新しい紹介文を', '商品ごとに一案作ります。'], 'system', 118) + box(585, 28, 240, '本人は投稿後の記録を', ['読み、必要に応じて', '型や目安を変えます。'], 'human', 118) + arrow(id, 255, 87, 300, 87) + arrow(id, 540, 87, 585, 87) + textAt(420, 193, '本文作成AIは、目安超過だけでは商品を保留しません。', 'label'));
`));
await edit('finalize-html.mjs', s => s
  .replaceAll('辞書・追跡表は1.1追記を含みます。その他は経緯を示す過去資料です。旧資料中の17:45固定開始・旧提案値と現在要件が異なる場合は、本HTMLの20・22・28章とU-20261003を優先します。', '辞書・追跡表は1.2へ更新済みです。その他は過去の回答や検討経緯を示す資料で、現在の処理設計ではありません。旧資料の事前分析設計・17:45固定開始・旧提案値は現在要件へ適用しません。実装者は本HTMLの5～7・20・22・28章とU-20261003を優先します。')
  .replaceAll(' — 1.1追記を含む', ' — 1.2更新済み'));
await edit('readable-chapter.mjs', s => s
  .replace("  ['run','その日の一連の作業記録'", "  ['search_attempt／candidate_decision','検索を一回始めた記録／候補を選んだ、または外した理由の記録','検索プログラムは、失敗を含む検索開始を一回として記録します。選択プログラムは、候補ごとに採否の理由を保存します。'],\n  ['keyword_snapshot／theme_selection','当日に取得した人気語の記録／テーマと語を選んだ記録','人気語取得プログラムは、実際に得た語と順位を保存します。選択プログラムは、抽選結果と選択回数の加算を記録します。'],\n  ['generation_attempt／publication_event／human_action','本文生成を試した記録／本人の実投稿の記録／本人が行った操作の履歴','AI起動プログラムは、生成回数と結果を記録します。保存プログラムは、本人の投稿報告と見送り・訂正・本文修正を区別して記録します。'],\n  ['exclusion_key／metric_observation／revenue_order','投稿済み商品の除外番号／成果の観測記録／公式注文明細の記録','保存プログラムは、投稿済み商品と成果の範囲を別々に記録します。集計プログラムは、注文明細を根拠なく入口投稿へ割り当てません。'],\n  ['recovery／backup／restore_check','復旧作業／予備コピーの作成／予備コピーの読み戻し確認','復旧担当は、停止原因を解消して残りだけを再開します。保全担当プログラムは、予備コピーを作り、別の検査用領域で読み戻せるかを確かめます。'],\n  ['run','その日の一連の作業記録'")
  .replace('### 28.1.5 図番号F00～F11', '### 28.1.5 図番号F00およびF02～F11')
  .replace("gloss('28.1.5 図番号F00～F11", "gloss('28.1.5 図番号F00およびF02～F11")
  .replace('各対応表を開くと、', '各対応表は、')
  .replace('普通の日本語で確認できます。', '普通の日本語で説明します。読者は必要に応じて表を閉じられます。'));
// Explanations are visible on first reading; readers may collapse them later.
await edit('build.mjs', s => s.replace('<details class="glossary-group">', '<details class="glossary-group" open>'));
const data = await loadDocument();
const { readableChapter28 } = await import('./readable-chapter.mjs');
data.source = data.source.replace(/^## 28\. [\s\S]*$/m, readableChapter28());
// Preserve historical decisions while making current scope unambiguous.
data.source = data.source.replace('本人の追加依頼、', '本人の追加依頼、事前分析の対象外化、');
data.source = data.source.replace(/分析前未確定目安\/長文/g, '外部結果未受領/長文');
data.source = data.source.replace(/分析提案→本人確定/g, '外部確定結果を受領。不足は表示し分析を起動しない');
const dictionaryUrl = new URL('要件データ辞書.md', base);
let dictionary = await readFile(dictionaryUrl, 'utf8');
dictionary = dictionary
  .replaceAll('material_count:integer!,material_kind:enum!', 'material_count:integer?,material_kind:string?')
  .replaceAll('確認前confirmed_at=null、材料数0でも架空根拠を付けない', '初期設定は別計画で本人確定した結果を登録。材料数・種別は提供された場合だけ保存し、未提供ならnull')
  .replaceAll('source_kind=baseline/supplement/daily/metrics/generation', 'source_kind=external_analysis_result/daily/metrics/generation')
  .replace('## 版1.1：AIジョブ契約（IR-028）', '## 版1.2：AIジョブ契約（IR-028）');
const descriptions = [
  'AI起動プログラムは、受け渡しと回答形式の版を別々に記録します。保存プログラムは、未対応の版の回答を処理エラーにします。',
  '日次制御プログラムは、28章で許可した作業・AI・指示書の組合せを渡します。個別AI作業番号は日次番号とは別に付けます。',
  'AI起動プログラムは、同じ作業の生成回数を記録します。本文の初回と再試行2回を上限とし、再開でも回数を戻しません。',
  'AI起動プログラムは、実在する資料の参照と入力の照合値を渡します。保存プログラムは、AIが創作した参照を受理しません。',
  '日次制御プログラムは、前工程の終了直後に成果取得を始めます。17:45は前工程の例外的な打切り、18:00は成果取得の上限です。',
  'AI起動プログラムは、実際のモデル・推論設定・認証の種類を記録します。鍵やCookieの値を保存しません。',
  'AI起動プログラムは、正常・空・エラー・打切りを区別します。失敗理由から秘密情報を除きます。',
  '保存プログラムは、回答ファイルの参照と採用時刻を記録します。AIの生成成功だけでは完成件数を増やしません。',
  '保存プログラムは、未採用・採用・形式不一致・期限後到着・競合を記録します。期限後の本文を新しい完成本文へ採用しません。',
];
let i = 0;
dictionary = dictionary.replace(/(## 版1\.2：AIジョブ契約[\s\S]*)/, tail => tail.replace(/^\| ([^|]+) \| ([^|]+) \| ([^|]+) \|$/gm, (line, name, type) => name === '項目' ? line : `| ${name} | ${type} | ${descriptions[i++]} |`));
dictionary += '\n## 版1.2：事前分析との境界\n\n実装者は、実装前の別計画で確定した分類・文型・本文長さ・タグ目安を初期設定として受け取る。システムは事前分析を実行しない。実装者はその専用スクリプト・AI・skillを作成しない。settings_versionは外部結果の登録先であり、artifactのexternal_analysis_resultは受領済み成果資料を示す。いずれも事前分析の実行機能ではない。\n';
await writeFile(dictionaryUrl, dictionary);
const traceUrl = new URL('要件追跡表.md', base);
let trace = await readFile(traceUrl, 'utf8');
trace = trace.replace('V-001/002 | IR-005/010/011', 'V-001/002 | IR-010/011')
  .replace('初期事前分析→本人確定。材料不足なら不足表示/本人判断', '実装者は別計画の本人確定結果を実装前に受領する。不足時は別計画側の確定を待ち、システム内で分析しない');
trace = trace.replace(/## 2026-10-03の本人追加依頼[\s\S]*$/, `## 2026-10-03の本人追加依頼（版1.2）

| 追跡ID | 本人が何を依頼したか | 誰が、どの要件へどう反映するか |
|---|---|---|
| U-20261003-01 | 本人は、タスク図・機械処理とAIの分担・AI部品仕様の追加を依頼した。 | 実装者は、IR-028のT02～T11、F00とF02～F11、AI-Oと6専用AI・7skillsを実装し、AT-028とAT-A01～10で確認する。 |
| U-20261003-02 | 本人は、前工程終了直後に成果を始め、17:45を超過時だけの打切りにするよう訂正した。 | 日次制御プログラムは、IR-020・022・028に従って直ちに成果へ進む。実装者はAT-S16とAT-A05・06で時刻を検証する。 |
| U-20261003-03 | 本人は、HTMLを正本とし、全情報の集約と元MDの削除を依頼した。 | 文書編集者は、HTMLに要件・図・辞書・追跡・根拠全文・編集用原文を収録した。元の要件MDは全文移行の検証後に削除済みである。 |
| U-20261003-04 | 本人は、各ノードと追加表を主語・述語・目的語・必要な理由を持つ平易な文章へ直し、全独自用語の対応表をフロー前に置くよう依頼した。 | 文書編集者は、28章の先頭に対応表を置き、各手順の担当・目的・対象・方法・条件を全文で示す。 |
| U-20261003-05 | 本人は、事前分析を実装前の別計画とし、その専用スクリプト等を作成せず、システム設計を本書から削除するよう依頼した。 | 文書編集者は、事前分析のタスク・図・専用AI・skill・標本収集手順を削除する。実装者はIR-005～007で別計画の確定結果だけを受け取る。 |

旧回答と検討資料の事前分析設計や「17:45で成果へ」という表現は当時の記録である。実装者は、現在のU-20261003-02と05および要件HTMLを優先し、事前分析を実装しない。13:20等の例は所要時間の実測ではない。この文書編集は、スキル実装や日次有効化を含まない。
`);
await writeFile(traceUrl, trace);
for (const name of ['要件データ辞書.md', '要件追跡表.md']) data.references[name] = (await readFile(new URL(name, base))).toString('base64');
await mkdir(new URL('html-review-1.2/', base), {recursive:true});
await writeFile(new URL('html-review-1.2/要件改訂内容.txt', base), data.source.split(/^## /m).filter(s => /^(?:1|2|3|26|27)\. /.test(s)).join('\n## '));
const p = new URL(htmlName, base);
await writeFile(p, (await readFile(p, 'utf8')).replace(/(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/, (_,a,b)=>a+serializeDocument(data)+b));
// Delete only the obsolete documentation diagram; no operational code is touched.
await rm(new URL('図解/F01.svg', base), {force:true});
console.log('Current documentation, glossary, dictionary, trace and diagrams updated.');
