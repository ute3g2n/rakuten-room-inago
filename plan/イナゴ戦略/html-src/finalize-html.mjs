import { serializeDocument, legacyName } from './document-source.mjs';

const escape = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

export function finalizeHtml(html, data, { markdown, figureCount }) {
  const count = 28;
  html = html.replaceAll('全27章', `全${count}章`).replaceAll('27章から選ぶ', `${count}章から選ぶ`)
    .replaceAll('27章・', `${count}章・`).replaceAll('27のSVG図解', `${figureCount}のSVG図解`)
    .replace('REQUIREMENTS / 1.0', `REQUIREMENTS / ${data.version}`)
    .replace('図解版 1.0</title>', `正本 ${data.version}</title>`)
    .replace('確定版1.0のイナゴスキル要件を', `正本${data.version}のイナゴスキル要件を`)
    .replace('要件確定 1.0</span>', `HTML正本 ${data.version}</span>`)
    .replace('2026.09.30 JST</span>', `${data.updated.replaceAll('-', '.')} JST</span>`)
    .replace('本文17:45まで、成果18:00まで。', '前工程完了直後に成果へ。17:45は例外の打切り。')
    .replace('>時間割</a>', '>通常と例外</a>')
    .replace('>確定MD</a>', '>正本の版情報</a>')
    .replace('>確定要件定義書（MD）</a>', '>正本の先頭</a>')
    .replace('図と要点は説明用、正式な条件・例外は各章の全文と', '旧確定履歴と本セッションの最終回答を統合。追加決定は27.1へ。正式な条件・例外は各章の全文と')
    .replace('Q-FINAL / D-060で確定した要件の読解版。', '本HTMLが要件の正本です。')
    .replace('各章は「要点 → 図解 → 要件全文」の順。', '各章は「要点 → 図解 → 要件全文」の順。28章は最初に独自名称の対応表を置き、その後に文章で詳しく説明した図と担当表を初期表示します。<a href="#source-28">最初に用語と担当名を読む</a>。')
    .replace('図解生成元：イナゴスキル要件定義書.md', '正本内部の編集用source snapshot')
    .replace('関連MDはこのHTMLに同梱してあります。本文中・上記の資料リンクを押すとダウンロードできます。別フォルダへHTMLだけをコピーしても、図と全文の閲覧・参考MDの取得ができます。', '辞書・追跡表・根拠資料の全文を、この下に収録しています。資料名を押すと全文へ移動します。HTMLだけをコピーしても全情報を読めます。元の要件定義書MDは削除済みです。')
    .replace('>受入試験</a></div></div>', '>受入試験</a> → <a href="#chapter-28">タスク・AI仕様</a></div></div>');
  html = html.replace('id="source-28"', 'id="source-28" open');

  const names = Object.keys(data.references);
  const ids = new Map(names.map((name, index) => [name, `reference-${String(index + 1).padStart(2, '0')}`]));
  let referenceHtml = '<section class="reference-library" aria-labelledby="library-heading"><h2 id="library-heading">辞書・追跡・根拠資料の全文</h2><p class="callout amber">辞書・追跡表は1.4へ更新済みです。意思決定台帳とヒアリング記録には本セッションの指示と最終回答を追記しています。その他は過去の検討経緯です。旧階層20/10・最大いいね代表・日次9検索/450投稿・対話専用AI-Oを現在の規則へ再適用しません。1検索先頭50投稿は最終回答で確定しています。本HTMLとU-20261013、27.1の追加決定を優先します。</p><label for="reference-search">資料名・全文から検索</label><input id="reference-search" type="search" placeholder="例：成果 ／ D-052 ／ データ辞書"><p id="reference-count" role="status" aria-live="polite">全' + names.length + '資料</p>';
  for (const [name, id] of ids) {
    const bytes = Buffer.from(data.references[name], 'base64');
    const value = bytes.toString('utf8').replace(/\r\n/g, '\n');
    const current = ['要件データ辞書.md', '要件追跡表.md'].includes(name);
    referenceHtml += `<details class="reference-document" id="${id}" data-reference-name="${escape(name)}"><summary>${escape(name.replace(/\.md$/, ''))}${current ? ' — 1.4更新済み' : ' — 経緯・根拠'}</summary><p><a href="data:text/markdown;charset=utf-8;base64,${data.references[name]}" download="${escape(name)}">この根拠資料を保存</a></p>${current ? markdown(value) : `<pre class="reference-text"><span>${escape(value)}</span></pre>`}</details>`;
  }
  referenceHtml += '</section>';
  html = html.replace('<div class="top-link">', referenceHtml + '<div class="top-link">');
  // Resolve all links to documents inside the canonical HTML. Downloads remain optional.
  html = html.replaceAll(`<a href="${legacyName}">`, '<a href="#overview">');
  for (const [name, id] of ids) html = html.replaceAll(`<a href="${escape(name)}">`, `<a href="#${id}">`);
  const snapshot = `<script id="requirements-source" type="application/json">${serializeDocument(data)}</script>`;
  html = html.replace('</body>', `${snapshot}</body>`);
  return html;
}
