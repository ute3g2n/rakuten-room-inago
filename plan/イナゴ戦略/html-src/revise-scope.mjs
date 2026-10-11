// Documentation migration only: no operational analysis scripts or skills are created.
import { readFile, writeFile } from 'node:fs/promises';
import { loadDocument, serializeDocument, base, htmlName } from './document-source.mjs';

async function edit(name, transform) {
  const url = new URL(name, import.meta.url);
  await writeFile(url, transform(await readFile(url, 'utf8')), 'utf8');
}
await edit('flow-descriptions.mjs', s => s
  .replace(/  F01: \[[\s\S]*?\n  \],\n/, '')
  .replace(/^  F01: .*\n/m, '')
  .replaceAll('AG-SETUP／AG-KEY／AG-COPYなど', 'AG-KEY／AG-COPYなど')
  .replaceAll('「準備する」', '「設定を変更する」'));
await edit('flow-renderer.mjs', s => s.replace(/^  if \(id === 'F01'\).*\n/m, ''));
await edit('task-flows.mjs', s => s.replace(/  \{\n    "id": "F01",[\s\S]*?\n  \},\n/, ''));
await edit('readable-chapter.mjs', s => s
  .replace(/^  \['AG-SETUP'.*\n/m, '').replace(/^  \['T01'.*\n/m, '')
  .replaceAll('T01～T11', 'T02～T11').replaceAll('T01等', 'T02等')
  .replaceAll("'初期資料から設定を決める順番'", 'null')
  .replace('flowTitles.map((name,i)=>[', 'flowTitles.map((name,i)=>name ? [')
  .replace('文章と矢印で説明します。`]);', '文章と矢印で説明します。`] : null).filter(Boolean);')
  .replaceAll('人気投稿、検索結果、本文・実投稿・反応・取得できた売上の記録', '日次検索結果、本文・実投稿・反応・取得できた売上の記録')
  .replaceAll('文字数・タグ数・商品の集まり方・取得範囲ごとの成果を数えて表にします', '日次の商品数・保存済み本文数・実投稿数・取得範囲ごとの成果を数えて表にします')
  .replaceAll('準備AIは5分野に各6テーマを提案します。商品には主テーマ一つと補助テーマを付けられますが、同じ商品を二重に数えません。', '実装者は、別計画で確定した5分野と各6テーマを初期設定に登録します。商品選択プログラムは、商品に主テーマ一つと補助テーマを記録し、同じ商品を二重に数えません。')
  .replaceAll('準備AIは人気投稿の実測から目安を提案し、本人が採用します。本文作成AIは目安を参考にしますが、超えた商品を保留しません。', '実装者は、実装前に別計画で確定した文章の型と目安を受け取ります。本文作成AIは、その目安を参考にしますが、目安を超えた商品を保留しません。')
  .replaceAll('setup／daily／status／recovery／config-change', 'daily／status／recovery／config-change')
  .replaceAll('準備／日次／状態確認／復旧／設定変更という起動モード', '日次／状態確認／復旧／設定変更という起動モード')
  .replaceAll('分類案・本文・説明を作ります', '検索語の分類・本文・説明を作ります')
  .replace('### 28.2 各作業の目的と担当', '事前分析は、システムを実装する前に別計画で行います。実装者は、その計画で本人が確定した結果を初期設定として受け取ります。本システムは、事前分析の標本収集・分類案作成・文型分析を実行しません。実装者は、それらの専用スクリプト・AI・skillを作成しません。\n\n### 28.2 各作業の目的と担当')
  .replace('04は図と表の説明', '04は図と表の説明')
  .replace('対応表を先に置く依頼です。', '対応表を先に置く依頼です。05は事前分析を実装前の別計画へ分け、専用の処理設計を本システムから削除する依頼です。')
  .replace('現在1.2は説明の改訂であり、', '現在1.2は説明の改訂と事前分析の対象外化を反映し、')
  .replace('商品の意味や文章の表現を考える作業は専用AIへ渡します。', 'このプログラムは、商品の意味や文章の表現を考える作業を専用AIへ渡します。')
);

const { readableChapter28 } = await import('./readable-chapter.mjs');
const data = await loadDocument();
const replaceChapter = (number, text) => {
  const re = new RegExp(`^## ${number}\\. [\\s\\S]*?(?=^## \\d+\\. |$(?![\\s\\S]))`, 'm');
  if (!re.test(data.source)) throw new Error(`Missing chapter ${number}`);
  data.source = data.source.replace(re, text.trim() + '\n\n');
};
data.source = data.source
  .replace('対象は事前分析、', '事前分析は、システム実装前に別計画で実施し、本システムの対象外とする。実装者は、その確定結果を初期設定として受け取る。対象は')
  .replaceAll('操作を事前準備/日次/', '操作を日次/')
  .replace(/事前準備は基礎\/補足標本から[^\n]+/, '実装者は、別計画で本人が確定した分類・文型・本文長さ・タグ目安を実装の前提情報として受け取る。本システムは、事前分析を実行する機能を持たない。本人は、運用中の設定変更・投稿・見送り・訂正を行う。')
  .replace(/分類名\/30テーマ\/本文長さ\/タグ目安は事前分析後[^\n]+/, '実装者は、実装を始める前に、別計画で本人が確定した分類名・30テーマ・文型・本文長さ・タグ目安を受け取る。結果が不足する場合は、実装者が不足項目を示して別計画側の確定を待つ。本システム内で標本収集や再分析を行わない。日次の取得方法は、実装後のテストで別途確認する。')
  .replaceAll('分析前未確定目安/長文', '外部結果未受領/長文')
  .replaceAll('分析提案→本人確定', '外部の確定結果を受領。不足時は不足表示し分析を起動しない')
  .replaceAll('本文長さ/タグ/分類名は事前分析と本人確認後に確定', '本文長さ/タグ/分類名は、実装前の別計画で本人が確定した結果を受け取る');
replaceChapter(5, `## 5. 実装前に受け取る事前分析結果

IR-005：事前分析は、システムを実装する前に、別計画で実施する。実装者は、その計画で本人が確定した結果を、実装の前提情報として受け取る。本システムは事前分析を行わない。AT-005：実装者は、受け取った確定結果を初期設定へ反映でき、事前分析の収集・分析ジョブを起動する機能が存在しないことを確認する。

実装者が受け取る情報は、5ジャンルと各6テーマの名前・定義、文章の型、本文長さとタグの目安、および本人が採用した結果を特定できる資料である。実装者は、提供された結果の版と参照を記録し、日次処理がその設定を使えるようにする。

実装者は、事前分析のためのスクリプト、AIエージェント、専用skill、標本取得モードを作成しない。本書は、その別計画の標本の集め方、補足検索、分析手順、担当構成を設計しない。実装者は、必要な確定結果が届いていなければ不足項目を示し、別計画側での確定を待つ。本システムは、不足を埋めるために事前分析を開始しない。`);
replaceChapter(6, `## 6. 外部で確定した5ジャンル・30テーマの登録と利用

IR-006：実装者は、別計画で本人が確定した5ジャンルと各6テーマ、合計30テーマを初期設定へ登録する。本システムは、初期ジャンルやテーマを人気投稿から生成しない。AT-006：実装者は、登録したジャンル数が5、各ジャンルの有効テーマ数が6、合計が30であり、名前と定義が受け取った結果に一致することを確認する。

実装者は、各ジャンルとテーマに識別番号を付け、名前・定義・所属ジャンル・本人の確定を示す参照・設定の版を保存する。別計画の結果に含まれる分類例や根拠資料は、その参照とともに取り込める。実装者は、提供されていない材料数や根拠を創作しない。

商品選択プログラムは、テーマの意味の重なりを許容し、商品に主テーマ一つと補助テーマを記録する。同じ商品が複数テーマに当てはまっても、商品選択プログラムは店舗と商品IDの組合せで重複を除き、一商品として数える。

本人は、運用中に必要だと判断した場合、23章と28章の設定変更作業でテーマを変更できる。初期分類を作る事前分析と、運用中の本人による設定変更は、別の作業として扱う。`);
replaceChapter(7, `## 7. 外部で確定した文型・本文長さ・タグ目安の利用

IR-007：実装者は、実装前の別計画で本人が確定した文章の型、本文長さとタグの目安を初期設定へ登録する。本文作成AIは、その設定を使って各商品の新しい紹介文を作る。AT-007：実装者は、本文作成AIへ渡す型と目安が受領結果に一致し、本システムが人気投稿から型や目安を分析・提案する機能を持たないことを確認する。

実装者は、文章の型の識別番号、組み立て方、長さとタグの目安、結果の参照、本人の確定と設定の版を保存する。実装者は、未確定の200～300字やタグ3～5個などを、採用済みの値として登録しない。必要な結果が未受領なら、実装者は不足項目を示す。

本文作成AIは、採用済みの型と目安を参考にし、他者の投稿を丸写しせずに新しい本文を一案作る。システムは、長さやタグ数の目安を超えたことだけを理由に商品を保留せず、空でない本文の生成と確定保存が成功すれば完成に数える。

本人は、文章の型を収益効果が証明された事実ではなく、運用で確かめる仮説として使う。システムは、実投稿後の反応と取得できた収益を記録し、本人は必要に応じて型や目安を変更する。`);
replaceChapter(28, readableChapter28());
data.version = '1.2';
data.updated = '2026-10-03';
data.source = data.source.replace('版：1.1', '版：1.2');
const url = new URL(htmlName, base);
const html = await readFile(url, 'utf8');
await writeFile(url, html.replace(/(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/, (_,a,b)=>a+serializeDocument(data)+b));
console.log('External pre-analysis scope applied to editable HTML source and diagram templates.');
