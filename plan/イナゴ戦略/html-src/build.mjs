import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { loadDocument } from './document-source.mjs';
import { flows, renderFlow, writeFlowAssets } from './task-flows.mjs';
import { finalizeHtml } from './finalize-html.mjs';
import { renderFlowTranscript } from './flow-renderer.mjs';

const base = new URL('../', import.meta.url);
const sourceName = 'イナゴスキル要件定義書.md';
const outputName = 'イナゴスキル要件定義書.html';
const documentData = await loadDocument({ importMarkdown: process.argv.includes('--import-markdown') });
const source = documentData.source;
await writeFlowAssets();
const flowMap = new Map(flows.map(flow => [`図解/${flow.id}.svg`, flow]));
const [baseCss, script, referenceCss] = await Promise.all([
  readFile(new URL('./styles.css', import.meta.url), 'utf8'),
  readFile(new URL('./interactions.js', import.meta.url), 'utf8'),
  readFile(new URL('./reference-styles.css', import.meta.url), 'utf8'),
]);
const css = `${baseCss}\n${referenceCss}`;
const sourceHash = createHash('sha256').update(source).digest('hex');
const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const inline = text => {
  const tokens = [];
  const placeholder = html => `\u0000${tokens.push(html) - 1}\u0000`;
  const protectedText = text.replace(/`([^`]+)`/g, (_, value) => placeholder(`<code>${escape(value)}</code>`))
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
      if (!/^(?:https?:\/\/|#[A-Za-z0-9_-]+|[^:/?#]+\.md(?:#[^\s]*)?$)/i.test(href)) throw new Error(`Unsupported source link: ${href}`);
      return placeholder(`<a href="${escape(href)}">${escape(label)}</a>`);
    });
  return escape(protectedText).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\u0000(\d+)\u0000/g, (_, index) => tokens[Number(index)]);
};

// A deliberately small renderer for this source's paragraphs, tables and lists.
// Unsupported block syntax fails the build rather than silently dropping text.
function markdown(text) {
  const lines = text.trim().split('\n');
  const html = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const image = line.match(/^!\[([^\]]+)\]\(([^)]+)\)$/);
    if (image) {
      const flow = flowMap.get(image[2]);
      if (!flow) throw new Error(`Unsupported source image: ${image[2]}`);
      const start = flow.id === 'F11' ? '<details class="implementation-reference" id="implementation-test-flow"><summary data-ui-only>参考：動作テストの図を開く</summary>' : '';
      const end = flow.id === 'F11' ? '</details>' : '';
      html.push(`${start}<figure id="task-${flow.id}" data-source-alt="${escape(image[1])}"><div class="diagram-scroll" tabindex="0" role="region" aria-label="${escape(flow.title)}（画面幅に合わせて表示）">${renderFlow(flow)}</div><figcaption><b>${escape(image[1])}</b><br>${escape(flow.desc)}</figcaption>${renderFlowTranscript(flow)}</figure>${end}`);
      index += 1; continue;
    }
    if (line.startsWith('|')) {
      const rows = [];
      while (index < lines.length && lines[index].startsWith('|')) rows.push(lines[index++]);
      if (rows.length < 2 || !/^\|[\s:|-]+\|$/.test(rows[1])) throw new Error('Unsupported Markdown table');
      const cells = row => row.trim().slice(1, -1).split('|').map(cell => inline(cell.trim()));
      const headers = cells(rows[0]);
      const labelText = value => value.replace(/<[^>]*>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      const labels = headers.map(labelText);
      const bodyRows = rows.slice(2).map(row => {
        const rowCells = cells(row);
        if (rowCells.length !== headers.length) throw new Error(`Table cell count mismatch: ${row}`);
        return `<tr>${rowCells.map((cell, column) => `<td data-label="${escape(labels[column])}">${cell}</td>`).join('')}</tr>`;
      });
      const renderedTable = `<div class="table-scroll" tabindex="0" role="region" aria-label="要件表"><table><thead><tr>${headers.map(cell => `<th scope="col">${cell}</th>`).join('')}</tr></thead><tbody>${bodyRows.join('')}</tbody></table></div>`;
      html.push(headers[0] === '記号・名称' ? `<details class="glossary-group" open><summary data-ui-only>用語の対応表を開く（${bodyRows.length}項目）</summary>${renderedTable}</details>` : renderedTable);
      continue;
    }
    if (/^\d+\. /.test(line) || /^- /.test(line)) {
      const ordered = /^\d+\. /.test(line);
      const matcher = ordered ? /^\d+\. / : /^- /;
      const items = [];
      while (index < lines.length && matcher.test(lines[index])) items.push(`<li>${inline(lines[index++].replace(matcher, ''))}</li>`);
      html.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    if (/^#{1,4} /.test(line)) {
      html.push(`<h4>${inline(line.replace(/^#+ /, ''))}</h4>`); index += 1; continue;
    }
    if (/^(?:```|>|\s{4}\S)/.test(line)) throw new Error(`Unsupported Markdown block: ${line}`);
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !/^(?:\||#{1,4} |\d+\. |- |!\[)/.test(lines[index])) paragraph.push(lines[index++]);
    html.push(`<p${paragraph[0].startsWith('IR-') ? ' class="contract"' : ''}>${paragraph.map(inline).join('<br>')}</p>`);
  }
  return `<div class="md">${html.join('\n')}</div>`;
}

const sections = [...source.matchAll(/^## (\d+)\. (.+)\n([\s\S]*?)(?=^## \d+\. |$(?![\s\S]))/gm)].map(match => ({ number: Number(match[1]), title: match[2], body: match[3].trim() }));
if (sections.length !== 28) throw new Error(`Expected 28 source chapters; found ${sections.length}`);
const preamble = source.slice(0, source.indexOf('## 1.')).trim().replace(/^# .+\n+/, '');

const summaries = [
  ['収益につながる商品を見つけ、毎日15件の本文を作る。投稿は本人が行い、下書き・実投稿・反応・収益を別々に測る。', ['<b>完成下書き</b>＝店舗商品に紐づく空でない本文が、確定保存されたもの。', '<b>15件は目標。</b>候補不足や停止時は、保存できた実数と不足理由を表示する。']],
  ['システムは候補収集と本文作成、本人は採用・投稿・見送り・訂正を担当する。', ['コピーや商品URLの閲覧だけでは、投稿済みにならない。', '実装者は別計画の確定結果を受け取る。本人は運用中の設定変更を判断する。']],
  ['事前分析は実装前に別計画で行い、その確定結果を実装の前提にする。', ['実装者はジャンル・30テーマ・文型・長さとタグ目安を受け取る。', '実装者は日次の公開UI取得と成果接続を、実装後のテストで確認する。']],
  ['新スキルを現在のWindows PCに分離して構成する。取得・生成・保存の役割を分け、SQLiteを状態の正本にする。', ['新しい名前空間から旧商品台帳・下書き・タスク状態を書き換えない。', 'HTMLは閲覧と本人操作の窓口。状態の確定はSQLiteが担う。']],
  ['実装者は別計画で確定した分類と文章の方針を、実装前に受け取る。', ['本システムは事前分析を実行しない。専用スクリプト・AI・skillを作成しない。', '必要な結果が不足する場合、実装者は不足を示して別計画側の確定を待つ。']],
  ['実装者は外部で確定した5ジャンルと各6テーマ、合計30テーマを登録する。', ['実装者は受け取った名前・定義・所属をそのまま初期設定へ反映する。', '商品選択プログラムは主テーマと補助テーマを記録し、商品重複を除く。']],
  ['本文作成AIは外部で確定した文章の型・長さ・タグ目安を使って新しい本文を作る。', ['本システムは人気投稿から型や目安を抽出・提案しない。', '本文作成AIは目安を超えた商品を保留しない。本人は実投稿後の記録で効果を確かめる。']],
  ['無料Google Trendsの関連Topから、子分類に当たる語だけを残す。日本・日本語語句・Web検索・過去90日で毎日更新する。', ['第1階層は最大20語、第2階層は最大10語。取得が7語・3語なら、その実数から選ぶ。', '<b>子分類0語</b>は別語・別テーマへ。<b>取得失敗</b>は当日の語句選びを停止。古い候補を自動流用しない。']],
  ['選択回数の少ないテーマを優先し、同点は抽選。語句は取得候補から単純抽選する。', ['最初の3テーマ、語句、乱数seed、結果を保存。同じrunの再開で抽選し直さない。', '選択回数は成功本文数ではなく選択イベント数。追加テーマも数え、再開で二重加算しない。']],
  ['1検索につき内部の並び順で最大50投稿を取得し、店舗＋商品IDでまとめる。最大いいねの1投稿を代表にして順位を付ける。', ['50は重複をまとめる前の<b>投稿数</b>。画面の見た目の順位とは異なることを記録。', 'いいね300以上。合計いいねで順位を上げない。ID不明を別商品と断定して採用しない。']],
  ['すべての検索・日付で「店舗＋商品ID」を共通キーにする。実投稿済みは恒久除外、未投稿下書きは本人の報告を翌日日次で反映するまで予約する。', ['別店舗・別商品IDは別商品。同じID内の色や容量は1商品。', '同画像でも別IDと確認できれば両方許容。見送り報告の翌日反映で予約を解除し、未チェックだけでは解除しない。']],
  ['価格・在庫・販売期間・評価などの商品事実を、完成下書きの条件にしない。再確認処理も設けない。', ['任意情報がunknownでも、識別済み商品で本文の生成・保存が成功すれば完成に数える。', 'unknownを0や在庫なしに変換しない。商品ID・本文版・保存の整合は引き続き必要。']],
  ['まず3テーマ各5件を目指す。不足したら検索を広げ、当日の新規15本文を合計で目指す。', ['第1＋第2 → 足りなければ第1のみ。初期3テーマの後は未試行の別語句 → 追加テーマ。', '<b>最大9検索試行／日</b>には再試行も含む。15件・9試行・17:45・取得不能による停止のいずれかで探索終了。']],
  ['1商品につき、そのまま貼り付けられる日本語本文1案を新しく生成する。空本文・生成エラーだけを最大2回再試行する。', ['初回＋再試行2回＝最大3回。失敗が続けば別候補へ。確定済み本文は残す。', '商品確認・再確認・別品質承認を完成条件にしない。丸写しや架空体験の偽装を生成機能として採用しない。']],
  ['商品URL・本文・投稿チェックに加え、見送りと理由付き訂正をHTMLに設ける。受付プログラムは夜の報告を保存し、翌日の日次が管理情報へ反映する。', ['単体ファイルは閲覧可能。保存接続がなければ状態操作を無効にし、「保存できない」と表示。', '過去本文の報告も受付保存し、翌日反映で同商品の後発本文を投稿禁止にする。']],
  ['SQLiteが唯一の状態正本。観測・商品・本文・予約・本人投稿・成果を参照でつなぐ。', ['ROOM投稿ID、楽天商品ID、内部番号を混ぜない。独立商品として数えるには店舗商品キーが必要。', '詳細な型・必須性・未取得時の扱いは、同版の要件データ辞書と一緒に実装へ渡す。']],
  ['本文・予約・runの完成件数を、商品ごとに1トランザクションで確定する。HTMLはcommitした後に出力する。', ['生成しただけでは完成0。commit直後なら本文・予約・カウンタがすべて存在する。', 'チェック・見送り・訂正は履歴として保存。夜の報告受付と翌日の管理反映を分け、本人投稿を生成履歴と混ぜない。']],
  ['途中停止後は同じrunの確定分、設定版、seed、試行回数を保ち、残りだけ再開する。', ['14件保存後に止まっても14件を作り直さない。17:45以降の新検索・新本文、18:00以降の新成果取得は禁止。', '画面変更・整合違反・認証切れは停止。PIDだけでロック解除せず、他のプロセスを終了しない。']],
  ['取得枠・投稿数・商品数・生成数・実投稿数を、段階ごとに数える。未達の理由も追えるようにする。', ['同商品10投稿でも商品1・本文1。検索代表の延べ数と、全日の独立商品数も別。', '一次理由は各候補1つ。副理由を除外総数へ足さない。前日の本文を当日の15件に加算しない。']],
  ['13:00開始。語句更新・検索・本文生成・保存の完了直後に成果取得へ進む。17:45は長時間化した場合だけの強制打切り上限。', ['通常は17:45まで待たない。成果取得も終われば日次完了。18:00は成果取得の終了上限。', '追加サービス・API費用0円。過去日追生成なし。日次バックアップ・月次復元確認。']],
  ['通知は使わず、本人がHTMLと状態一覧を見る。生存・進捗・プロセス・ロック・監視の更新を分けて表示する。', ['初期実装値はheartbeat30秒、2分未更新／10分進捗なしで要確認。調整可能で、自動killや解除の条件ではない。', '状態取得が失敗したら「状態確認不能」。古い正常表示を最新の成功として見せない。']],
  ['前工程終了直後、同じ日次タスクで現行「化け物・改」と同じ方式の成果取得を開始する。例えば13:20完了なら、その時点から成果へ。', ['17:45は前工程が長引いた場合だけ打切り→成果。取得範囲は前日から最大7日・当月/前月CSV・ROOM直近30日。', '未取得はnull。未確定報酬と確定報酬、購入商品一致と入口投稿の帰属を区別し、日別売上を商品に按分しない。']],
  ['テーマ・語句・文型の入替は、本人が必要に応じて随時判断する。システムは記録と比較条件を示す。', ['自動変更は行わない。取得不能を低反応として扱わない。', '変更は新設定版へ。進行中runは開始時の版を維持し、切戻しでも投稿除外・予約を巻き戻さない。']],
  ['使える旧部品だけを新設定・SQLiteへ分離して接続する。旧履歴は証拠のある実投稿と本人申告を読み取り移行する。', ['旧予定・旧下書きを投稿済みに変換しない。不明な店舗商品キーは本人補完へ。', '旧資材は本人が安定と判断するまで保持。退避は版と復元方法を付け、旧タスク停止と分けて扱う。']],
  ['実装後のテストを行い、その後は最初から本番で運用し、必要に応じてデバッグする。', ['必須経路と取得・保存の成立を検証。固定7日／30日の試験や3→9→15の段階導入は要件にしない。', '停止・復元時も本文と本人履歴を保全。バックアップ後の投稿を補完するまで新選出を再開しない。']],
  ['28個の章別受入条件、31個の重要経路、10個のAI契約試験を実装後に検証する。期待と実観測、証拠と合否を残す。', ['正常・重複・生成失敗・停止・二重起動・早期成果開始・期限・本人操作・成果欠測・復元・遅延投稿までを対象とする。', '本HTMLのレビューは文書と表示の検証。将来の実装テスト合格や、市場で15件揃う保証を意味しない。']],
  ['決定・回答・草案・監査から現在の要件へ辿る。現在版1.3は最新の本人指示を統合し、日次全体図と対応するサブフローへ読み順を整理したもの。', ['60決定・41基本質問・11補助質問・最終確認と、U-20261003-01〜08を追跡。', 'HTMLが正本。部品名やパスは初期仕様で、実装済みを意味しない。']],
  ['10タスクを11の図で追い、スクリプト・AI・本人の担当と、オーケストレータ・専用AI・skillsの仕様を読む。', ['<b>機械処理：</b>時刻・上限・抽選・重複・保存。<b>AI：</b>語の意味・本文・説明。<b>本人：</b>採用・投稿・訂正。', '7つのAI役割と7skills、入出力・起動・停止・失敗・再開の契約を明記。']],
];

let figureNumber = 0;
const diagrams = new Map();
const palette = { system: '#e2f1e8', human: '#fcf0d0', store: '#e6edf8', neutral: '#f1f5f2', warning: '#faeadf' };
const textAt = (x, y, content, className = 'label', anchor = 'middle') => `<text x="${x}" y="${y}" class="${className}" text-anchor="${anchor}">${escape(content)}</text>`;
const box = (x, y, width, title, subtitles = [], kind = 'system', height = 86) => `<g class="diagram-node"><rect x="${x}" y="${y}" width="${width}" height="${height}" rx="10" fill="${palette[kind]}" stroke="#bacfc6"/>${textAt(x + width / 2, y + 28, title, 'box-title')}${subtitles.map((sub, i) => textAt(x + width / 2, y + 51 + i * 19, sub, 'box-sub')).join('')}</g>`;
const arrow = (id, x1, y1, x2, y2, label = '', dash = false) => `<path d="M ${x1} ${y1} L ${x2} ${y2}" class="line${dash ? ' dashed' : ''}" marker-end="url(#${id}-arrow)"/>${label ? textAt((x1 + x2) / 2, (y1 + y2) / 2 - 10, label, 'small') : ''}`;
const route = (id, path, label = '', x = 0, y = 0) => `<path d="${path}" class="line" marker-end="url(#${id}-arrow)"/>${label ? textAt(x, y, label, 'small') : ''}`;
function figure(chapter, title, caption, height, draw) {
  figureNumber += 1;
  const id = `fig-${String(figureNumber).padStart(2, '0')}`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 840 ${height}" role="img" aria-labelledby="${id}-title ${id}-desc"><title id="${id}-title">${escape(title)}</title><desc id="${id}-desc">${escape(caption)}</desc><defs><marker id="${id}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 1 L 9 5 L 0 9 z" fill="#688f82"/></marker></defs>${draw(id)}</svg>`;
  const html = `<figure id="${id}"><div class="figure-head"><span>FIG ${String(figureNumber).padStart(2, '0')}</span>${escape(title)}</div><div class="diagram-scroll" tabindex="0" role="region" aria-label="${escape(title)}。図は画面幅に合わせて表示します">${svg}</div><figcaption>${escape(caption)}</figcaption></figure>`;
  diagrams.set(chapter, (diagrams.get(chapter) ?? '') + html);
}
function sequence(chapter, title, caption, actors, events) {
  const height = 115 + events.length * 62;
  figure(chapter, title, caption, height, id => {
    const positions = actors.map((_, i) => 110 + i * (620 / (actors.length - 1)));
    let result = actors.map((actor, i) => box(positions[i] - 77, 12, 154, actor.name, [], actor.kind, 48) + `<path d="M ${positions[i]} 61 V ${height - 15}" class="line dashed"/>`).join('');
    events.forEach((event, i) => {
      const y = 105 + i * 62;
      if (event.from === event.to) {
        result += `<circle cx="${positions[event.from]}" cy="${y}" r="5" fill="#126b5c"/>${textAt(positions[event.from] + 18, y - 9, event.label, 'label', 'start')}`;
      } else {
        result += arrow(id, positions[event.from], y, positions[event.to], y);
        result += textAt((positions[event.from] + positions[event.to]) / 2, y - 10, `${i + 1}. ${event.label}`, 'label');
      }
    });
    return result;
  });
}

figure(1, '下書き・実投稿・成果は、それぞれ別の数', '説明例：15本文を保存し、本人が8件投稿した日は、未投稿7件が予約として残る。売上・報酬は実観測の集計単位で記録し、15下書きから推定しない。', 255, id =>
  box(20, 38, 220, '15 完成下書き', ['本文を保存した数'], 'system') + box(310, 38, 220, '8 実投稿', ['本人が投稿を確認'], 'human') + box(600, 38, 220, '成果の観測値', ['反応・売上・報酬'], 'store') + arrow(id, 240, 80, 310, 80) + arrow(id, 530, 80, 600, 80) + box(310, 156, 220, '7 未投稿・予約中', ['本人の投稿／見送りまで'], 'neutral') + route(id, 'M 130 124 V 199 H 310') + textAt(710, 173, '未取得 ≠ 0', 'box-title') + textAt(710, 201, '下書き数 ≠ 売上', 'small'));

figure(2, '自動処理・夜の本人投稿・翌日の管理反映を分ける', '自動処理は本人投稿を待たずに終了する。本人は21:30などに独立して投稿し、受付プログラムはその結果を未反映として保存する。翌日の日次は前日の報告を回収・反映してから今日の商品を探す。', 430, id =>
  textAt(420, 25, '日次制御プログラムは、今日13:00に自動処理を始めます。', 'label') + box(15, 48, 240, 'F00は前日の報告を', ['回収し、管理情報へ', '反映してから次へ進みます。'], 'system', 120) + box(300, 48, 240, 'F02は今日の商品を探し', ['本文を保存します。', '直後に成果を取得します。'], 'system', 120) + box(585, 48, 240, '日次制御は成果終了後', ['本人投稿を待たずに', '自動処理を終了します。'], 'store', 120) + arrow(id, 255, 106, 300, 106) + arrow(id, 540, 106, 585, 106) + textAt(420, 215, '本人は、自動処理と独立して21:30などに投稿します。', 'label') + box(15, 244, 240, '本人はROOMへ投稿し', ['HTMLで投稿結果を', 'チェックして報告します。'], 'human', 120) + box(300, 244, 240, '受付は報告を保存し', ['管理反映待ちと示します。', 'この時点では管理未更新。'], 'store', 120) + box(585, 244, 240, '翌日のF00は報告を', ['回収・反映し、F02の', '手順2へ進めます。'], 'system', 120) + arrow(id, 255, 301, 300, 301) + arrow(id, 540, 301, 585, 301, '', true) + textAt(420, 407, '上段と下段に開始待ちの矢印はありません。下段の破線は翌日の回収です。', 'small'));


figure(4, '新スキルの構成とデータの流れ', '公開情報の取得、本文生成、SQLiteでの保存、HTMLでの閲覧・本人操作、本文後の成果取得を分ける。下図の位置は初期設計であり、旧台帳への書き込みは行わない。', 418, id =>
  box(20, 28, 200, '新スキル／起動', ['Windows・手動／日次']) + box(320, 28, 200, '実行管理', ['順序・期限・試行回数']) + box(620, 28, 200, '状態／復旧', ['保存境界・ロック確認']) + arrow(id, 220, 71, 320, 71) + box(20, 165, 200, 'ROOM／Trends', ['公開UI・候補観測']) + box(320, 165, 200, '本文生成', ['文型版 → 本文1案']) + box(620, 165, 200, 'SQLite 正本', ['本文・予約・履歴'], 'store') + route(id, 'M 420 114 V 142 H 120 V 165') + arrow(id, 220, 208, 320, 208) + arrow(id, 520, 208, 620, 208) + box(320, 305, 200, 'HTML／local server', ['閲覧・コピー・本人操作'], 'human') + box(620, 305, 200, '成果取得', ['本文後・現行方式']) + route(id, 'M 690 251 V 281 H 420 V 305', 'commit後に出力', 490, 274) + route(id, 'M 520 360 H 563 V 230 H 620', '報告受付保存', 562, 399) + arrow(id, 720, 305, 720, 251) + textAt(779, 283, '成果保存', 'small') + arrow(id, 720, 165, 720, 114) + textAt(120, 337, '新しい名前空間で保存', 'label') + textAt(120, 367, '旧台帳と旧タスクは分離', 'small'));

figure(5, '別計画の確定結果を、実装の前提として受け取る', '事前分析は実装前の別計画で完了する。実装者はその結果を受け取り、初期設定へ登録する。本システムは事前分析の手順や担当を持たない。', 240, id =>
  box(15, 30, 240, '実装者は確定結果を', ['別計画から受け取ります。', '分類・文章の型・目安が', '実装の前提になります。'], 'human', 130) + box(300, 30, 240, '実装者は受領結果を', ['初期設定へ登録します。', '結果が不足する場合は、', '別計画の確定を待ちます。'], 'system', 130) + box(585, 30, 240, '日次処理は登録設定を', ['商品選びと文章作りに', '使います。事前分析を', '実行しません。'], 'store', 130) + arrow(id, 255, 95, 300, 95) + arrow(id, 540, 95, 585, 95) + textAt(420, 207, '実装者は事前分析用スクリプト・AI・skillを作りません。', 'small'));

figure(6, '5ジャンル × 各6テーマ ＝ 30テーマ', '実装者は実装前に別計画で確定した名前と定義を受け取る。下図のA〜E・番号は構造を示す仮の表示。テーマの意味は重なってもよいが、商品の件数は店舗商品キーで一意に数える。', 325, () => {
  let result = '';
  for (let genre = 0; genre < 5; genre += 1) {
    const x = 22 + genre * 162;
    result += box(x, 15, 148, `ジャンル ${'ABCDE'[genre]}`, [], 'system', 47);
    for (let theme = 0; theme < 6; theme += 1) {
      result += `<rect x="${x}" y="${78 + theme * 36}" width="148" height="29" rx="5" fill="#f0f5f1"/>${textAt(x + 74, 98 + theme * 36, `テーマ ${genre * 6 + theme + 1}`, 'label')}`;
    }
  }
  return result + textAt(420, 318, '実装者は各テーマの定義・所属・確定参照・版を登録します。', 'small');
});

figure(7, '本文作成AIは、確定済みの型と目安を使う', '実装者は別計画の確定結果を初期設定へ登録する。本文作成AIはその型と目安を参考に新しい文章を作る。本人は実投稿後の記録で効果を確かめる。', 238, id =>
  box(15, 28, 240, '実装者は文章の型と', ['長さ・タグの目安を', '確定結果から登録します。'], 'system', 118) + box(300, 28, 240, '本文作成AIは設定を', ['読み、新しい紹介文を', '商品ごとに一案作ります。'], 'system', 118) + box(585, 28, 240, '本人は投稿後の記録を', ['読み、必要に応じて', '型や目安を変えます。'], 'human', 118) + arrow(id, 255, 87, 300, 87) + arrow(id, 540, 87, 585, 87) + textAt(420, 193, '本文作成AIは、目安超過だけでは商品を保留しません。', 'label'));

figure(8, '検索語の階層と「0語」「取得失敗」の分岐', '両階層とも子分類だけを使う。表示例「揚げ物 → 唐揚げ → 鶏の唐揚げ」は階層の説明であり、観測済み人気語の確定案ではない。正常0語なら他候補へ、取得失敗なら当日の語句選びを停止する。', 440, id =>
  box(20, 25, 240, 'テーマ', ['例：揚げ物'], 'neutral') + box(300, 25, 240, '第1階層：最大20語', ['例：唐揚げ']) + box(580, 25, 240, '第2階層：最大10語', ['例：鶏の唐揚げ']) + arrow(id, 260, 68, 300, 68) + arrow(id, 540, 68, 580, 68) + textAt(420, 145, '関連Top・日本・Web・過去90日・毎日更新', 'label') + box(20, 185, 240, '子分類の候補あり', ['実数から単純抽選']) + box(300, 185, 240, '取得成功・子分類0', ['他の第1語／テーマへ'], 'neutral') + box(580, 185, 240, '取得失敗', ['当日の語句選び停止'], 'warning') + box(20, 325, 240, '第1＋第2で検索', ['不足時は第1のみへ']) + box(580, 325, 240, '部分成果を保持', ['本文後の成果工程へ'], 'store') + arrow(id, 140, 271, 140, 325) + arrow(id, 700, 271, 700, 325) + textAt(419, 356, '0語をAI人気語で埋めない', 'small') + textAt(419, 385, '取得失敗で古い候補を使わない', 'small'));

figure(9, '選択回数でテーマを選び、結果を保存する', '最小の選択回数を優先する。図は説明例で、同点テーマの順は保存乱数で決まる。選択イベントは成功本文数と別で、同じrunの再開では二重加算しない。', 260, id =>
  box(18, 30, 210, '選択履歴', ['A：2回　B：0回', 'C：1回　D：0回'], 'store', 110) + box(310, 30, 220, '低回数を優先', ['B・D → C', '同点B／Dは抽選'], 'system', 110) + box(612, 30, 210, '3テーマを保存', ['設定版・seed・結果', '選択イベントを確定'], 'store', 110) + arrow(id, 228, 85, 310, 85) + arrow(id, 530, 85, 612, 85) + route(id, 'M 717 140 V 198 H 420 V 140', '再開は保存結果を読む・抽選し直さない', 550, 229));

figure(10, '50投稿を、商品単位の順位へ変換する', '50は取得投稿数の上限。商品の紹介投稿が10件あっても1商品として扱い、その商品の最大いいね投稿を代表にする。商品順位の同点は保存乱数で決める。', 310, id =>
  box(20, 24, 240, '内部順 最大50投稿', ['条件：いいね ≥ 300', '取得順・投稿キーを保存'], 'neutral', 100) + box(300, 24, 240, '店舗＋商品IDで集約', ['Aが10投稿 → 商品A 1つ', 'Bが1投稿 → 商品B 1つ'], 'system', 100) + box(580, 24, 240, '代表の最大いいね順', ['A：最大500（合算しない）', 'B：600 → この例ではBが先'], 'system', 100) + arrow(id, 260, 74, 300, 74) + arrow(id, 540, 74, 580, 74) + box(300, 196, 240, '選出して本文へ', ['実投稿済み／予約／同日採用を除外'], 'system', 94) + route(id, 'M 700 124 V 242 H 540') + textAt(139, 228, '取得できないlikesは', 'label') + textAt(139, 252, '0と見なさない', 'label'));

figure(11, '商品が同じかどうかは「店舗＋商品ID」で決まる', '商品IDが同じなら画像や色が違っても1商品。IDが異なると確認できれば同じ画像でも別商品。実投稿済みキーは恒久除外、未投稿キーは本人報告の翌日日次反映まで予約する。', 335, () => {
  const rows = [
    ['店舗A＋ID001', '店舗A＋ID001', '同じ商品：1件', '同ID内の色・容量／画像違い'],
    ['店舗A＋ID001', '店舗A＋ID002', '別商品：各1件', '別IDと確認できれば同画像も許容'],
    ['店舗A＋ID001', '店舗B＋ID001', '別商品：各1件', '店舗が異なる'],
  ];
  return rows.map((row, i) => {
    const y = 15 + i * 96;
    return box(20, y, 210, row[0], [], 'neutral', 55) + textAt(270, y + 34, 'vs.', 'label') + box(310, y, 210, row[1], [], 'neutral', 55) + textAt(680, y + 25, row[2], 'box-title') + textAt(680, y + 50, row[3], 'small');
  }).join('') + textAt(420, 321, '同日・別検索・翌日も、この商品キーで照合', 'label');
});

figure(13, '15件を目指す補充フロー', '各初期テーマは第1＋第2を先に検索し、5件未満なら第1のみで補充する。初期3テーマを処理した後に未試行の別語句、次に追加テーマへ。すべての検索で重複除外と日次上限を共有する。', 428, id =>
  box(20, 24, 230, '初期3テーマを保存', ['まず各5件を目標']) + box(305, 24, 230, '第1＋第2で検索', ['1試行・最大50投稿']) + box(590, 24, 230, '順位順に本文作成', ['そのテーマ5件まで']) + arrow(id, 250, 67, 305, 67) + arrow(id, 535, 67, 590, 67) + box(590, 170, 230, '5件未満なら第1のみ', ['検索・全検索間の重複除外']) + arrow(id, 705, 110, 705, 170, '不足時') + box(305, 170, 230, '初期3テーマを処理', ['満了／上限到達を確認']) + route(id, 'M 590 95 H 565 V 153 H 420 V 170', '5件揃ったら', 445, 145) + arrow(id, 590, 213, 535, 213) + box(20, 170, 230, '15未満なら追加探索', ['未試行別語句 → 追加テーマ']) + arrow(id, 305, 213, 250, 213) + box(20, 316, 365, '追加も第1＋第2 → 第1のみ', ['試した組合せは再利用しない']) + arrow(id, 135, 256, 135, 316) + box(455, 316, 365, '停止して成果工程へ', ['15件／9試行／17:45／取得不能'], 'store') + arrow(id, 385, 359, 455, 359) + textAt(420, 293, 'どの工程でも停止条件を優先 ／ 再試行も9試行枠に含む', 'small'));

figure(14, '本文生成の再試行は、空・エラーだけ', '最大3回は初回＋再試行2回。商品情報がunknownや本文が目安より長いことを理由に再試行・交換しない。本文の保存失敗は生成失敗と区別する。', 275, id =>
  box(20, 20, 220, '初回生成', ['本文1案']) + box(310, 20, 220, '最大2回の再試行', ['空本文／生成エラーだけ']) + box(600, 20, 220, '別候補へ交換', ['最大3回でも失敗'], 'warning') + arrow(id, 240, 63, 310, 63, '空・エラー') + arrow(id, 530, 63, 600, 63, '失敗継続') + box(20, 175, 510, '正常本文は一時保存 → SQLiteへcommit', ['commit成功で完成1。保存失敗は一時本文を保全。'], 'store') + arrow(id, 130, 106, 130, 175, '成功') + arrow(id, 420, 106, 420, 175, '成功') + textAt(709, 207, '確定済み本文は残す', 'label') + textAt(709, 236, '再開でも回数を継承', 'small'));

figure(15, '夜は報告を保存し、翌日の日次が管理情報へ反映する', '受付プログラムは、夜の投稿・見送り・訂正を未反映の報告として保存する。翌日の日次は報告を照合し、投稿済み・除外・予約と反映状態を同時確定してから商品探索を始める。', 420, id =>
  box(15, 28, 240, '本人は夜にROOMへ投稿し', ['HTMLで結果を報告します。', '見送り・訂正も報告します。'], 'human', 110) + box(300, 28, 240, '受付は報告を保存します', ['管理反映待ちと表示します。', '予約はまだ解除しません。'], 'store', 110) + box(585, 28, 240, '翌日の日次は報告を', ['回収し、元の本文の版と', '訂正の順を確認します。'], 'system', 110) + arrow(id, 255, 83, 300, 83) + arrow(id, 540, 83, 585, 83, '', true) + box(15, 212, 240, '保存担当は管理情報と', ['反映済みの印を同時に', '確定します。'], 'store', 110) + box(300, 212, 240, '日次制御は反映完了後に', ['F02手順2から今日の', '商品探索を始めます。'], 'system', 110) + box(585, 212, 240, '反映失敗時は日次制御が', ['商品探索を停止し、', '未反映の報告を残します。'], 'warning', 110) + route(id, 'M 705 138 V 181 H 135 V 212') + arrow(id, 255, 267, 300, 267) + textAt(420, 381, '受付保存の成功と管理反映の成功は、別の状態として表示します。', 'label'));

sequence(15, '古い本文の報告も、翌日に元の版へ反映する', '本人は過去の本文で投稿した結果を夜に報告できる。受付は元の本文版を記録し、翌日の日次が実投稿と後発予約解除を反映する。過去の生成件数を減らさない。', [{ name: '本人', kind: 'human' }, { name: '報告受付', kind: 'system' }, { name: '翌日の日次', kind: 'store' }], [
  { from: 0, to: 1, label: '本人は古い本文の投稿を報告する' }, { from: 1, to: 0, label: '受付は管理反映待ちを表示する' }, { from: 1, to: 2, label: '翌日日次は未反映報告を回収する' }, { from: 2, to: 1, label: '日次は元版へ投稿と除外を確定する' }, { from: 2, to: 0, label: '画面は後発本文を投稿禁止と示す' },
]);


figure(16, 'SQLiteの主な情報と参照関係', '概念図：runは設定版・seed・期限を固定し、観測→商品→本文→報告受付→翌日反映を参照で接続する。成果観測は取得できたscopeで保持する。実際の列・型・制約は章の全文と要件データ辞書に従う。', 340, id =>
  box(20, 20, 220, 'settings／run', ['設定版・日付・seed・期限'], 'store') + box(310, 20, 220, 'search／source_post', ['試行・原投稿・観測値'], 'store') + box(600, 20, 220, 'offer', ['店舗＋商品ID'], 'store') + arrow(id, 240, 63, 310, 63) + arrow(id, 530, 63, 600, 63) + box(600, 174, 220, 'draft／reservation', ['本文版・未投稿予約'], 'store') + arrow(id, 710, 106, 710, 174) + box(310, 174, 220, 'publication／action', ['投稿・見送り・訂正履歴'], 'human') + arrow(id, 600, 217, 530, 217) + box(20, 174, 220, 'metric／order', ['観測scope・null・出典'], 'neutral') + arrow(id, 310, 217, 240, 217, '照合参照') + textAt(420, 310, 'JSON・HTML・snapshotは参照artifact。状態正本はSQLiteだけ。', 'label'));

sequence(17, '商品単位の保存シーケンス', 'SQLiteで本文・active予約・run完成件数を同時にcommitし、その後にHTMLを出力する。commit前の停止は未確定、commit後の停止は再出力だけで復旧でき、本文を作り直さない。', [{ name: '本文生成', kind: 'system' }, { name: 'SQLite 正本', kind: 'store' }, { name: 'HTML出力', kind: 'system' }], [
  { from: 0, to: 1, label: '一時本文・商品キー・本文版' }, { from: 0, to: 1, label: '有効な投稿除外／予約を再照合' }, { from: 0, to: 1, label: '本文＋予約＋run件数を1 transactionでcommit' }, { from: 1, to: 2, label: '確定済みデータを渡す' }, { from: 2, to: 0, label: 'HTML出力結果（失敗時は再export）' },
]);

figure(18, '再開と停止の判断', '14件確定後の停止なら確定14件を保持し、締切内だけ未確定の残りを再開する。画面変更・認証切れ・整合違反は自動再試行せず本人／保守確認へ。Trends取得失敗は当日の語句選び停止という別ルール。', 282, id =>
  box(20, 20, 240, 'checkpointを読む', ['同run・版・seed・回数', '確定14本文は維持'], 'store', 105) + box(300, 20, 240, '条件を確認', ['ロック所有者・実プロセス', 'DB整合・残り時間'], 'system', 105) + box(580, 20, 240, '締切内だけ残りを再開', ['未確定分／残工程', '確定済みは再生成しない'], 'system', 105) + arrow(id, 260, 72, 300, 72) + arrow(id, 540, 72, 580, 72) + box(300, 180, 520, '異常なら停止して本人／保守確認へ', ['画面変更・認証切れ・整合違反・ロック要確認'], 'warning') + arrow(id, 420, 125, 420, 180) + textAt(134, 215, '予算はリセットしない', 'label') + textAt(134, 242, '過去日分を追生成しない', 'small'));

figure(19, '段階ごとの数を混ぜない', '説明例：取得367投稿、検索代表は延べ42、全日独立商品38、確定本文11。再出現した同日採用済み4は独立商品38や完成11へ二重加算しない。数は19章の例であり、見込み値ではない。', 285, id =>
  box(15, 20, 178, '取得投稿', ['367 投稿', '上限枠は450'], 'neutral', 112) + box(226, 20, 178, '検索代表・延べ', ['42 商品代表', '検索をまたぐ再出現あり'], 'system', 112) + box(437, 20, 178, '全日の独立商品', ['38 店舗商品', '全検索のキーの和集合'], 'system', 112) + box(648, 20, 178, '確定本文', ['11 本文', '目標15・不足4'], 'store', 112) + arrow(id, 193, 76, 226, 76) + arrow(id, 404, 76, 437, 76) + arrow(id, 615, 76, 648, 76) + textAt(420, 182, '延べ42 ＝ 既投稿20 ＋ 予約7 ＋ 同日採用の再出現4 ＋ 完成11', 'label') + textAt(420, 218, '独立38 ＝ 既投稿20 ＋ 予約7 ＋ 完成11', 'label') + textAt(420, 254, '実投稿数は、本人の報告に基づき別に数える', 'small'));

figure(20, '通常は完了直後に成果へ。17:45は例外の打切り', '13:00に前日の報告を回収・管理反映し、語句更新・検索・本文生成・保存が終わったら直ちに成果取得へ。17:45まで待機しない。前工程が長引いた場合だけ17:45で打ち切って成果へ切替。18:00は成果の終了上限。', 400, id =>
  textAt(420, 25, '通常経路：処理の完了が次工程の開始条件', 'label') + box(20, 50, 245, '13:00開始 → 前工程', ['前日報告の反映後に', '語句・検索・本文作成を実行']) + box(297, 50, 245, '完了直後に成果取得', ['例えば13:20なら即開始'], 'store') + box(574, 50, 245, '成果完了 → 日次終了', ['17:45／18:00まで待機しない']) + arrow(id, 265, 92, 297, 92) + arrow(id, 542, 92, 574, 92) + textAt(420, 173, '例外経路：通常は想定しない長時間化', 'label') + box(20, 196, 245, '17:45まで前工程継続', ['前工程を強制打切り'], 'warning') + box(297, 196, 245, '確定分を保持 → 成果へ', ['遅延本文の応答待ちはしない'], 'store') + box(574, 196, 245, '18:00で成果取得終了', ['不足項目は欠測として表示'], 'warning') + arrow(id, 265, 239, 297, 239) + arrow(id, 542, 239, 574, 239) + textAt(420, 333, '遅い起動：17:45〜18:00は成果のみ／18:00以降は新日次なし', 'small') + textAt(420, 372, '過去日追生成なし。確定本文の再出力・本人報告保存は時刻制限なし。', 'small'));

figure(20, '保存期間とバックアップの境界', '詳細記録90日、成果集計1年。実投稿除外・未解決予約・未反映報告・訂正履歴は運用中保持し、詳細削除で失わない。初期設計は同PCのプロジェクト外へ日次7世代、月1回別フォルダで復元確認。', 286, () =>
  box(20, 20, 245, '90日', ['原投稿本文・snapshot・raw', '期限後は参照expired'], 'neutral', 114) + box(297, 20, 245, '1年', ['成果集計', '欠測と観測0を区別'], 'neutral', 114) + box(574, 20, 245, '運用中保持', ['実投稿除外・未解決予約', '訂正・必要な本文版／参照'], 'store', 114) + `<rect x="20" y="177" width="799" height="89" rx="9" fill="#fcf0d0"/>` + textAt(420, 206, '日次：整合DBバックアップ ／ 月次：別作業領域へ復元して確認', 'box-title') + textAt(420, 236, '稼働DBへ上書きしない・期限切れrawを復活させない・同PC故障には備えられない', 'small'));

figure(21, '「動いている」と「進んでいる」を分けて確認', '5項目を別表示し、状態観測不能をhealthyにしない。2分／10分は調整可能な専門側初期値で、停止やロック解除を自動で行う条件ではない。外部通知は使わない。', 256, () => {
  const items = [['生存記録', 'heartbeat 30秒'], ['工程進捗', '最後の前進時刻'], ['プロセス', 'PID＋開始時刻'], ['OSロック', '所有者＋run ID'], ['監視の更新', '観測成功／不能']];
  return items.map((item, i) => box(16 + i * 166, 20, 150, item[0], [item[1]], 'neutral', 100)).join('') + textAt(420, 166, '2分の生存記録未更新／10分の進捗なし → 要確認', 'box-title') + textAt(420, 205, '自動kill・ロック解除なし ／ 本人がHTML・状態一覧を見る', 'label');
});

figure(22, '成果は、取得できた集計単位のまま保存', '日別集計、購入商品の注文、自分のROOM投稿反応は異なる単位。注文の商品一致だけでは入口投稿の証拠にならない。日別売上・報酬を商品や文型へ推定配賦しない。', 383, id =>
  box(20, 20, 250, 'アフィリエイト日別', ['前日から最大7日', 'クリック・件数・売上・未確定報酬'], 'store', 109) + box(295, 20, 250, '公式注文CSV', ['当月／前月', '購入商品・金額・ステータス'], 'store', 109) + box(570, 20, 250, '自分のROOM反応', ['直近30日の公開反応', '本人実投稿が評価対象'], 'store', 109) + box(20, 198, 250, '日集計として表示', ['確定報酬は現行未取得', '未取得はnull ≠ 観測0'], 'neutral', 105) + box(295, 198, 250, '照合種別を区別', ['購入商品一致 ≠ 入口投稿確証', '入口確証は直接根拠がある時だけ'], 'neutral', 105) + box(570, 198, 250, '観測条件を保持', ['投稿対応・経過時間・欠測', '未投稿を反応0にしない'], 'neutral', 105) + arrow(id, 145, 129, 145, 198) + arrow(id, 420, 129, 420, 198) + arrow(id, 695, 129, 695, 198) + textAt(420, 352, '本文を先に保存 ／ 成果失敗でも本文を保持 ／ 18:00までの残時間を共有', 'label'));

figure(23, '本人の変更は新設定版へ、投稿履歴は維持', '本人が変更した時だけ新設定版を作る。進行中runは開始時の版を使い続け、次のrunで有効版を参照する。設定を切り戻しても実投稿除外と未解決予約は巻き戻さない。', 255, id =>
  box(20, 25, 230, '運用記録を示す', ['供給・反応・収益・不足'], 'store') + box(305, 25, 230, '本人が入替を判断', ['理由・根拠・新しい設定版'], 'human') + box(590, 25, 230, '次runで新設定版', ['5ジャンル各6テーマ維持'], 'system') + arrow(id, 250, 68, 305, 68) + arrow(id, 535, 68, 590, 68) + box(20, 166, 375, '進行中run：開始時の版を固定', ['途中で黙って版を差し替えない'], 'neutral') + box(445, 166, 375, '実投稿除外・予約：維持', ['設定の切戻しと独立した運用履歴'], 'store'));

figure(24, '旧系から引き継ぐもの・移行時に分けるもの', '旧ファイルは読み取り、実投稿証拠と本人申告だけを新イベント・恒久キーへ移行する。旧予定・下書き・不明履歴を実投稿に変えない。再利用部品は新設定と正本へ分離接続する。', 322, id =>
  box(20, 25, 240, '再利用する部品', ['URL処理・HTML操作', '成果取得方式・安全な保存'], 'neutral', 104) + box(300, 25, 240, '新設定・新SQLiteへ', ['依存を分離・移植テスト', '旧台帳へ書き込まない'], 'system', 104) + arrow(id, 260, 77, 300, 77) + box(580, 25, 240, '旧資材は保持', ['本人が安定と判断後', '版付き退避・復元方法'], 'neutral', 104) + box(20, 194, 240, '旧履歴を読み取る', ['証拠／申告／予定／不明'], 'neutral', 104) + box(300, 194, 240, '店舗商品キーを照合', ['実投稿の証拠と本人申告', '不明は本人補完'], 'human', 104) + box(580, 194, 240, '新恒久キーへ登録', ['元記録参照を一意に', '再取込みで二重履歴なし'], 'store', 104) + arrow(id, 260, 246, 300, 246) + arrow(id, 540, 246, 580, 246) + textAt(420, 163, '旧スコア・旧確認ゲート・旧JSON直接書込みは引き継がない', 'small'));

figure(25, '実装テスト → 本番運用 → 随時デバッグ', '実装後の必須経路と取得・保存の成立を検証し、最初から本番へ移る。固定期間の試験運用は要求しない。停止・復元時には本人の最新投稿キーを補完してから新選出を再開する。', 258, id =>
  box(20, 25, 225, '実装と取得実証', ['初使用前に継続取得等を検証'], 'system') + box(309, 25, 225, '実装テスト', ['整合・保存・期限・旧系保全'], 'system') + box(598, 25, 225, '最初から本番', ['日次15目標・本人が投稿'], 'human') + arrow(id, 245, 68, 309, 68) + arrow(id, 534, 68, 598, 68) + box(309, 166, 514, '状態と確定本文を残して随時診断・修正', ['影響する受入条件を再確認。上限を黙って緩和しない。'], 'neutral') + arrow(id, 710, 111, 710, 166) + textAt(138, 202, '本文・本人履歴・予約を保全', 'small') + textAt(138, 229, '旧タスクを勝手に再開しない', 'small'));

figure(26, '受入試験は、正常と境界の両方を見る', '28章のAT-001〜028に加え、AT-S01〜31、AT-A01〜10を実装後に試す。分類は読みやすくするための整理であり、試験を省略する優先順位ではない。', 308, () =>
  box(20, 20, 250, '候補と本文', ['正常・重複・ID・商品事実', '空／エラー・本文目安'], 'system', 110) + box(295, 20, 250, '保存と実行制御', ['commit前後・14件後停止', '二重起動・時刻・9試行'], 'store', 110) + box(570, 20, 250, '本人操作', ['8投稿・見送り・訂正', '同時操作・過去本文の投稿'], 'human', 110) + box(20, 171, 250, '情報源と供給', ['語句0・取得失敗・季節', '低供給・ソース命令'], 'neutral', 110) + box(295, 171, 250, '成果と帰属', ['欠測・暫定値の変更', '購入商品一致・入口未確定'], 'neutral', 110) + box(570, 171, 250, '保全と移行', ['期限削除・復元・旧履歴', '切戻し・backup後の投稿'], 'neutral', 110));

figure(27, '決定から実装時の試験へ辿る', '業務要件1.0と2026-10-03の追加依頼・時刻訂正を版1.3へ統合。各章の要件全文、辞書、追跡、根拠資料は本HTMLの内部で読める。', 211, id =>
  box(20, 30, 225, '回答・決定・追加依頼', ['D-001〜060／Q／R', 'U-20261003-01〜08'], 'neutral', 110) + box(309, 30, 225, '現要件 IR-001〜028', ['各章全文・データ辞書', '追跡表で参照'], 'system', 110) + box(598, 30, 225, '受入条件と重要経路', ['AT-001〜028', 'AT-S01〜31／AT-A01〜10'], 'store', 110) + arrow(id, 245, 85, 309, 85) + arrow(id, 534, 85, 598, 85) + textAt(420, 190, 'HTML正本1.3 ／ 業務要件1.0の確定履歴を保持', 'label'));

const groups = [
  { from: 1, to: 4, title: '目的と全体像', label: '01 / OVERVIEW' },
  { from: 5, to: 9, title: '始める前に決めること', label: '02 / PREPARATION' },
  { from: 10, to: 14, title: '商品を選び、本文を作る', label: '03 / DAILY CREATION' },
  { from: 15, to: 19, title: '投稿操作と、失わない保存', label: '04 / STATE & ACTIONS' },
  { from: 20, to: 22, title: '日次運用と成果の見方', label: '05 / OPERATION' },
  { from: 23, to: 27, title: '変更・移行・試験・追跡', label: '06 / HANDOVER' },
  { from: 28, to: 28, title: '全タスクとAI部品の仕様', label: '07 / TASKS & AGENTS' },
];
const chapterHtml = sections.map(section => {
  const group = groups.find(group => section.number >= group.from && section.number <= group.to);
  const [lede, points] = summaries[section.number - 1];
  const number = String(section.number).padStart(2, '0');
  const id = String(section.number).padStart(3, '0');
  return `${section.number === group.from ? `<div class="part" data-part="${group.from}"><small>${group.label}</small><h2>${group.title}</h2></div>` : ''}<section class="chapter" id="chapter-${number}" data-part="${group.from}" aria-labelledby="chapter-title-${number}"><div class="chapter-head"><div class="chapter-num" aria-hidden="true">${number}</div><div><h3 id="chapter-title-${number}">${escape(section.title)}</h3><span class="ref">IR-${id} · AT-${id}　／　原文 第${section.number}章</span></div></div><p class="lede">${escape(lede)}</p><ul class="key-points">${points.map(point => `<li>${point}</li>`).join('')}</ul>${diagrams.get(section.number) ?? ''}<details class="normative" id="source-${number}"><summary>この章の要件全文を読む<span>根拠・例外・受入条件を含む確定版の全文</span></summary>${markdown(section.body)}</details></section>`;
}).join('\n');
const toc = groups.map(group => `<div class="nav-group">${group.label}</div>${sections.filter(section => section.number >= group.from && section.number <= group.to).map(section => { const number = String(section.number).padStart(2, '0'); return `<a data-chapter="chapter-${number}" href="#chapter-${number}"><span class="nav-num">${number}</span>${escape(section.title)}</a>`; }).join('')}`).join('');

let html = `<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><meta name="description" content="確定版1.0のイナゴスキル要件を、SVG構成図・フロー図・シーケンス図で読むHTMLドキュメント。全27章の要件全文を収録。"><title>イナゴスキル要件定義書 — 図解版 1.0</title><style>${css}</style></head>
<body><a class="skip-link" href="#main">本文へ移動</a>
<aside class="rail" aria-label="文書の目次"><div class="brand">INAGO<span>REQUIREMENTS / 1.0</span></div><nav aria-label="全27章の目次"><a href="#overview">全体をつかむ</a>${toc}<a href="#references">根拠資料・原文</a></nav><div class="rail-foot">HTML正本 1.3 · 2026.10.03<br>28章 / ${figureNumber}図 / 全文収録</div></aside>
<main id="main"><header class="hero" id="overview"><svg class="hero-art" viewBox="0 0 360 360" aria-hidden="true"><g fill="none" stroke="#c3eddb" stroke-width="1"><circle cx="180" cy="180" r="170"/><circle cx="180" cy="180" r="125"/><circle cx="180" cy="180" r="80"/><path d="M 10 180 H 350 M 180 10 V 350 M 60 60 L 300 300 M 60 300 L 300 60"/></g><g fill="#c3eddb"><circle cx="180" cy="55" r="8"/><circle cx="305" cy="180" r="8"/><circle cx="180" cy="260" r="8"/><circle cx="100" cy="180" r="8"/></g></svg><div class="eyebrow">INAGO STRATEGY · VISUAL REQUIREMENTS</div><h1>イナゴスキル<br>要件定義書</h1><p>人気商品の発見から、毎日15件の本文作成へ。<br>誰が、何を、いつ行い、どこへ保存するかを図で読み解く。</p><div class="hero-meta"><span class="pill">要件確定 1.0</span><span class="pill">2026.09.30 JST</span><span class="pill">27章・${figureNumber}のSVG図解</span><span class="pill">本文全文を収録</span></div><p class="hero-foot">Q-FINAL / D-060で確定した要件の読解版。図と要点は説明用、正式な条件・例外は各章の全文と<a href="${sourceName}">確定MD</a>で確認できます。</p></header>
<div class="stats" aria-label="日次運用の主要条件"><div class="stat"><small>毎日の新規作成目標</small><strong>15<span> 本文</span></strong><p>初期3テーマ×5。足りなければ補充。</p></div><div class="stat"><small>ROOM検索の上限</small><strong>9<span> 試行</span></strong><p>再試行を含む。1検索は最大50投稿。</p></div><div class="stat"><small>日次の時間枠 / JST</small><strong>13–18<span> 時</span></strong><p>本文17:45まで、成果18:00まで。</p></div><div class="stat"><small>追加サービス・API費用</small><strong>0<span> 円</span></strong><p>既存契約のモデル利用量は別に記録。</p></div></div>
<details class="mobile-toc"><summary>目次：27章から選ぶ</summary><nav aria-label="モバイル用・全27章の目次">${toc}</nav></details>
<section class="intro" aria-labelledby="read-guide"><h2 id="read-guide">読み方は、知りたいことから。</h2><div class="reading-path"><div class="path"><b>まず全体を知る</b><a href="#chapter-01">目的</a> → <a href="#chapter-04">構成</a> → <a href="#chapter-20">時間割</a></div><div class="path"><b>毎日の流れを知る</b><a href="#chapter-08">語句</a> → <a href="#chapter-13">補充</a> → <a href="#chapter-15">本人操作</a></div><div class="path"><b>実装・レビューに使う</b><a href="#chapter-17">保存境界</a> → <a href="#chapter-18">再開</a> → <a href="#chapter-26">受入試験</a></div></div><div class="legend"><span><i class="system"></i>システム処理</span><span><i class="human"></i>本人の操作・判断</span><span><i class="store"></i>正本・保存データ</span><span>矢印＝処理順または図内で説明した参照・データの流れ</span></div><div class="callout"><p><b>投稿するのは本人。</b>本文コピーやリンク閲覧は投稿済みになりません。未投稿の下書きは、本人の報告を翌日の日次で管理情報へ反映するまで予約として残ります。夜の報告受付と翌日の管理反映を分けます。</p></div><p class="source-meta">各章は「要点 → 図解 → 要件全文」の順。スマートフォンでも図と表を横にスクロールせず読めます。</p></section>
<section class="intro common" aria-labelledby="common-heading"><h2 id="common-heading">全章に共通する約束</h2><p>JSTの日付・時刻を使い、観測・生成・報告・実投稿の時刻を分けます。ID／型／版の不整合は処理エラーとして扱い、値0・未確認・取得失敗を混ぜません。</p><div class="callout amber"><p><b>SQLite commit → HTML出力。</b>画面に本文が見えても、DBで未確定なら完成に数えません。商品事実の確認や再確認は完成条件にしません。</p></div><details class="normative" id="source-common"><summary>版情報・根拠・共通契約の全文を読む</summary>${markdown(preamble)}</details></section>
<div class="toolbar-box control"><div class="toolbar"><div class="search-wrap"><label for="document-search">要件全文を含めて、章を検索</label><input id="document-search" type="search" placeholder="例：予約 ／ AT-S29 ／ 17:45" autocomplete="off"></div><div class="tool-buttons"><button id="clear-search" type="button">検索を解除</button><button id="expand-all" type="button">全文をすべて開く</button><button id="collapse-all" type="button">全文をすべて閉じる</button><button id="print-document" type="button">全文を印刷</button></div></div><p class="result-count" id="result-count" role="status" aria-live="polite">全27章を表示。IR / AT番号や「予約」「17:45」でも検索できます。</p><p class="source-meta">印刷時は検索状態にかかわらず、全章・要件全文を出力します。</p></div><noscript><div class="callout amber">JavaScriptが無効でも図と全文を読めます。印刷するときは、各章の「要件全文」を手動で開いてください。</div></noscript><p class="empty" id="no-results" hidden>一致する章はありません。検索語を短くするか、「検索を解除」を押してください。</p>
${chapterHtml}
<footer class="end-note" id="references"><h2>正式な条件と根拠へ、すぐ戻れる。</h2><p><a href="${sourceName}">確定要件定義書（MD）</a> · <a href="要件データ辞書.md">要件データ辞書</a> · <a href="要件追跡表.md">要件追跡表</a> · <a href="意思決定台帳.md">意思決定台帳</a> · <a href="ヒアリング記録.md">ヒアリング記録</a></p><p>関連MDはこのHTMLに同梱してあります。本文中・上記の資料リンクを押すとダウンロードできます。別フォルダへHTMLだけをコピーしても、図と全文の閲覧・参考MDの取得ができます。</p><p>本書は要件の図解ドキュメントです。スキル実装・旧資材退避・日次有効化の実行を表すものではありません。図中の説明例と初期設計値は、観測結果や本人の確定値と区別しています。</p><p class="source-hash">図解生成元：${sourceName} ／ SHA-256（LF正規化）：${sourceHash}<br>再生成：node plan/イナゴ戦略/html-src/build.mjs</p></footer><div class="top-link"><a href="#overview">文書の先頭へ ↑</a></div>
</main><script>${script}</script></body></html>`;
html = finalizeHtml(html, documentData, { markdown, figureCount: figureNumber + flows.length, sourceHash });
// Any unresolved source link is a broken canonical reference, not an external dependency.
const attachmentNames = [...new Set([...html.matchAll(/<a href="([^"<>]+\.md)">/g)].map(match => match[1]))];
if (attachmentNames.length) throw new Error(`Unresolved canonical references: ${attachmentNames.join(', ')}`);
await writeFile(new URL(outputName, base), html, 'utf8');
console.log(JSON.stringify({ output: fileURLToPath(new URL(outputName, base)), version: documentData.version, chapters: sections.length, figures: figureNumber + flows.length, referenceDocuments: Object.keys(documentData.references).length, canonical: 'HTML', bytes: Buffer.byteLength(html), sourceHash }, null, 2));
