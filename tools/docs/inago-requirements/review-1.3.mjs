import { chromium } from 'playwright-core';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { expandedFlows } from './flow-descriptions.mjs';
import { base, review13, loadDocument, readReferenceDocument } from './document-source.mjs';

const artifactDir = review13;
await mkdir(artifactDir, { recursive: true });
const documentData = await loadDocument();
const source = documentData.source;
const sourceHash = createHash('sha256').update(source).digest('hex');
const htmlPath = fileURLToPath(new URL('イナゴスキル要件定義書.html', base));
const html = await readFile(htmlPath, 'utf8');
const tests = [];
const record = async (name, fn) => {
  try { const evidence = await fn(); tests.push({ name, status: 'PASS', evidence: evidence ?? null }); }
  catch (error) { tests.push({ name, status: 'FAIL', error: error.message }); }
};
const canonical = text => text.replace(/\s/g, '');
const sourceText = markdown => canonical(markdown
  .replace(/!\[([^\]]+)\]\([^)]+\)/g, '$1')
  .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  .replace(/^\|[\s:|-]+\|$/gm, '')
  .replace(/^\d+\. /gm, '').replace(/^- /gm, '')
  .replace(/^#{1,4} /gm, '').replace(/[`|]/g, '').replace(/\*\*/g, ''));
const errors = [];
const remoteRequests = [];
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/^https?:/.test(request.url())) remoteRequests.push(request.url()); });
  await page.goto(pathToFileURL(htmlPath).href);
  await page.evaluate(() => document.fonts.ready);

  await record('日次全体図を先頭に置き、各サブフローを手順へ対応',async()=>{
    const ids=await page.locator('#source-28 figure').evaluateAll(es=>es.map(e=>e.id));
    assert.deepEqual(ids,['task-F02','task-F00','task-F03','task-F04','task-F06','task-F09','task-F05','task-F07','task-F08','task-F10','task-F11']);
    assert.equal(await page.locator('#task-F02 [data-node]').count(),6);
    const text=await page.locator('#source-28 .md').textContent();
    for(const n of [1,2,3,4,5,6]) assert(text.includes('日次全体の手順'+n) || text.includes('日次全体の手順2・3'));
    const titles=await page.locator('#source-28 svg title').allTextContents();
    for(const title of titles)assert(!/^Fd/.test(title),title);
    for(const id of ['F00','F03','F04','F06','F09']) assert.equal(await page.locator('a[href="#task-'+id+'"]').count(),id==='F03'?2:1);
    return {dailySteps:6,subflowDiagrams:5,orderedFigures:ids};
  });
  await record('動作テスト図は初期非表示、開閉・検索・印刷で閲覧',async()=>{
    assert.equal(await page.locator('#implementation-test-flow').evaluate(e=>e.open),false);
    assert.equal(await page.locator('#task-F11').isVisible(),false);
    await page.locator('#implementation-test-flow > summary').click();assert(await page.locator('#task-F11').isVisible());
    await page.locator('#implementation-test-flow > summary').click();assert.equal(await page.locator('#task-F11').isVisible(),false);
    await page.locator('#document-search').fill('動作テストから本番');assert(await page.locator('#task-F11').isVisible());
    await page.locator('#clear-search').click();assert.equal(await page.locator('#implementation-test-flow').evaluate(e=>e.open),false);
    await page.evaluate(()=>window.dispatchEvent(new Event('beforeprint')));assert(await page.locator('#task-F11').isVisible());
    await page.evaluate(()=>window.dispatchEvent(new Event('afterprint')));assert.equal(await page.locator('#implementation-test-flow').evaluate(e=>e.open),false);
    return '初期は閉じる。本人の開閉・検索・印刷に対応し、解除後は閉状態へ戻る。';
  });
  await record('対応表リンクからサブフロー全文へ直接移動',async()=>{
    for(const id of ['F00','F03','F04','F06','F09']){
      await page.locator('a[href="#task-'+id+'"]').first().click();assert.equal(new URL(page.url()).hash,'#task-'+id);assert(await page.locator('#task-'+id).isVisible());
    }
    await page.goto(pathToFileURL(htmlPath).href);
    return '5サブフローへ移動。用語・全体図・説明を欠落させない。';
  });
  await record('前夜の報告受付と翌日管理反映・人間投稿の独立',async()=>{
    const intake=await page.locator('#task-F05').textContent();assert(intake.includes('未反映'));assert(intake.includes('管理情報'));assert(intake.includes('翌日'));
    const overview=await page.locator('#task-F02').textContent();assert(overview.includes('前日'));assert(overview.includes('本人の夜の投稿を待たず'));
    const start=await page.locator('#task-F00').textContent();assert(start.includes('反映が完了したら日次フロー全体図の手順2'));
    for(const id of ['AT-S18','AT-S19','AT-S20','AT-S29','AT-S30','AT-S31'])assert((await page.locator('#source-26').textContent()).includes(id));
    return '夜の受付・翌日管理反映・反映後の商品探索・未反映からの再開を保持。';
  });

  await record('HTML内部sourceハッシュと表示版の一致', () => { assert(html.includes(sourceHash)); assert(html.includes('HTML正本 1.3')); return sourceHash; });
  await record('全28章の全文照合（省略・書換えなし）', async () => {
    const sourceSections = [...source.matchAll(/^## (\d+)\. (.+)\n([\s\S]*?)(?=^## \d+\. |$(?![\s\S]))/gm)];
    const rendered = await page.locator('.chapter').evaluateAll(elements => elements.map(element => {
      const body = element.querySelector('.md').cloneNode(true); body.querySelectorAll('[data-ui-only]').forEach(e => e.remove());
      body.querySelectorAll('figure[data-source-alt]').forEach(figure => figure.replaceWith(document.createTextNode(figure.dataset.sourceAlt)));
      return { title: element.querySelector('h3').textContent, body: body.textContent };
    }));
    assert.equal(rendered.length, 28);
    sourceSections.forEach((match, index) => { assert.equal(rendered[index].title, match[2]); assert.equal(canonical(rendered[index].body), sourceText(match[3]), `第${index + 1}章の全文不一致`); });
    const preamble = source.slice(0, source.indexOf('## 1.')).trim().replace(/^# .+\n+/, '');
    assert.equal(canonical(await page.locator('#source-common .md').textContent()), sourceText(preamble));
    return '28章＋版情報・共通契約の全テキスト一致。Markdownの表示記法と空白だけ除外。';
  });
  await record('IR28・AT28・重要経路31の収録', async () => {
    const fullText = await page.locator('.chapter .md').allTextContents();
    const joined = fullText.join('\n');
    for (let n = 1; n <= 28; n += 1) { assert(joined.includes(`IR-${String(n).padStart(3, '0')}：`)); assert(joined.includes(`AT-${String(n).padStart(3, '0')}：`)); }
    for (let n = 1; n <= 31; n += 1) assert(joined.includes(`AT-S${String(n).padStart(2, '0')}`));
    return { IR: 28, AT: 28, scenarios: 31 };
  });
  await record('HTMLのID一意・見出し・アンカー・SVG説明', async () => {
    const result = await page.evaluate(() => {
      const ids = [...document.querySelectorAll('[id]')].map(element => element.id);
      const brokenAnchors = [...document.querySelectorAll('a[href^="#"]')].filter(link => !document.getElementById(decodeURIComponent(link.hash.slice(1)))).map(link => link.href);
      const figures = [...document.querySelectorAll('figure svg')];
      return { ids: ids.length, uniqueIds: new Set(ids).size, brokenAnchors, figures: figures.length, missingDescriptions: figures.filter(svg => !svg.querySelector('title') || !svg.querySelector('desc') || svg.getAttribute('role') !== 'img').length, h1: document.querySelectorAll('h1').length };
    });
    assert.equal(result.ids, result.uniqueIds); assert.equal(result.h1, 1); assert.equal(result.figures, 38); assert.equal(result.missingDescriptions, 0); assert.deepEqual(result.brokenAnchors, []); return result;
  });
  await record('参考MDの同梱・元ファイル一致・外部依存なし', async () => {
    const links = await page.locator('a:not([href^="#"])').evaluateAll(elements => elements.map(element => ({ name: element.getAttribute('download'), href: element.getAttribute('href') })));
    const names = new Set();
    for (const link of links) {
      if (/^https:\/\//.test(link.href)) { assert(link.href.startsWith('https://learn.chatgpt.com/docs/')); continue; }
      assert(link.name && link.name.endsWith('.md')); assert(link.href.startsWith('data:text/markdown;charset=utf-8;base64,'));
      const embedded = Buffer.from(link.href.split(',')[1], 'base64');
      const original = await readReferenceDocument(link.name, documentData); assert(embedded.equals(original), `${link.name}同梱内容不一致`); names.add(link.name);
    }
    assert.equal(names.size, Object.keys(documentData.references).length); assert.deepEqual(remoteRequests, []); return { referenceDocuments: names.size, networkRequests: remoteRequests.length };
  });
  await record('同梱したMDをブラウザからダウンロード', async () => {
    const downloadPromise = page.waitForEvent('download');
    const link = page.locator('a[download="要件データ辞書.md"]');
    await link.evaluate(element => { element.closest('details').open = true; });
    await link.click();
    const download = await downloadPromise;
    assert.equal(download.suggestedFilename(), '要件データ辞書.md'); assert.equal(await download.failure(), null);
    const downloadedPath = fileURLToPath(new URL('同梱MD取得確認.md', artifactDir));
    await download.saveAs(downloadedPath);
    assert((await readFile(downloadedPath)).equals(await readReferenceDocument('要件データ辞書.md', documentData)));
    return 'HTML内のdata URLから根拠のデータ辞書を取得。原ファイルとバイト一致。';
  });
  await record('SVGの全テキスト：図面境界と各ノード内の収まり', async () => {
    const overflows = await page.evaluate(() => {
      const problems = [];
      for (const svg of document.querySelectorAll('figure svg')) {
        const view = svg.viewBox.baseVal;
        for (const text of svg.querySelectorAll('text')) {
          const bounds = text.getBBox();
          if (bounds.x < -1 || bounds.y < -1 || bounds.x + bounds.width > view.width + 1 || bounds.y + bounds.height > view.height + 1) problems.push({ figure: svg.closest('figure').id, kind: 'viewBox', text: text.textContent, bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } });
          const node = text.closest('.diagram-node');
          if (node) {
            const rect = node.querySelector('rect').getBBox();
            if (bounds.x < rect.x + 3 || bounds.x + bounds.width > rect.x + rect.width - 3 || bounds.y < rect.y || bounds.y + bounds.height > rect.y + rect.height - 2) problems.push({ figure: svg.closest('figure').id, kind: 'node', text: text.textContent });
          }
        }
      }
      return problems;
    });
    assert.deepEqual(overflows, []); return '38 SVGの全テキストをgetBBoxで検証';
  });
  await record('全文の展開・折りたたみ', async () => {
    await page.locator('#expand-all').click(); assert.equal(await page.locator('.normative[open]').count(), 29);
    await page.locator('#collapse-all').click(); assert.equal(await page.locator('.normative[open]').count(), 0);
    return '28章＋共通契約：29全文ブロックを操作';
  });
  await record('本文・図・全文の文字コントラスト', async () => {
    await page.locator('#expand-all').click();
    const result = await page.evaluate(() => {
      const rgb = color => (color.match(/[\d.]+/g) ?? []).map(Number).slice(0, 3);
      const luminance = color => rgb(color).map(value => { const s = value / 255; return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4; }).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
      const background = element => {
        const node = element.closest('.diagram-node');
        if (node) return getComputedStyle(node.querySelector('rect')).fill;
        for (let current = element; current; current = current.parentElement) {
          const color = getComputedStyle(current).backgroundColor;
          if (color !== 'rgba(0, 0, 0, 0)' && color !== 'transparent') return color;
        }
        return 'rgb(255,255,255)';
      };
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node; let checked = 0; let minimum = Infinity; const failures = [];
      while ((node = walker.nextNode())) {
        if (!node.textContent.trim()) continue;
        const element = node.parentElement;
        if (!element || element.closest('script,style,title,desc') || !element.getClientRects().length || !element.getBoundingClientRect().height) continue;
        const style = getComputedStyle(element);
        const foregroundColor = element instanceof SVGElement ? style.fill : style.color;
        const alpha = Number(style.opacity);
        const back = rgb(background(element));
        const fore = rgb(foregroundColor).map((value, i) => value * alpha + back[i] * (1 - alpha));
        const a = luminance(`rgb(${fore.join(',')})`); const b = luminance(background(element));
        const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
        const size = Number.parseFloat(style.fontSize); const bold = Number.parseInt(style.fontWeight, 10) >= 700;
        const threshold = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
        checked += 1; minimum = Math.min(minimum, ratio);
        if (ratio < threshold) failures.push({ text: node.textContent.trim().slice(0, 50), ratio: Number(ratio.toFixed(2)), threshold });
      }
      return { checked, minimumRatio: Number(minimum.toFixed(2)), failures };
    });
    await page.locator('#collapse-all').click();
    assert.deepEqual(result.failures, []); return result;
  });
  await record('全文検索・複数語・0件・解除・展開状態復元', async () => {
    await page.locator('#document-search').fill('AT-S29'); assert.equal(await page.locator('.chapter:visible').count(), 1); assert(await page.locator('#chapter-26 details').getAttribute('open') !== null);
    await page.locator('#document-search').fill('予約 17:45');
    const matched = await page.locator('.chapter:visible').evaluateAll(elements => elements.map(element => element.textContent));
    assert(matched.length > 0); matched.forEach(text => { assert(text.includes('予約') && text.includes('17:45')); });
    await page.locator('#document-search').fill('存在しない検索語zzzz'); assert.equal(await page.locator('.chapter:visible').count(), 0); assert(await page.locator('#no-results').isVisible());
    await page.locator('#clear-search').click(); assert.equal(await page.locator('.chapter:visible').count(), 28); assert.equal(await page.locator('.normative[open]').count(), 0); assert(await page.locator('#document-search').evaluate(element => element === document.activeElement));
    return 'IR/ATを含む全文対象・AND検索・解除後の28開閉状態を復元';
  });
  await record('目次・原文アンカーへの直接移動', async () => {
    await page.locator('.rail a[data-chapter="chapter-15"]').click(); assert.equal(new URL(page.url()).hash, '#chapter-15');
    await page.evaluate(() => { location.hash = '#source-15'; });
    await page.waitForFunction(() => document.getElementById('source-15').open);
    await page.locator('#collapse-all').click(); await page.evaluate(() => { location.hash = '#overview'; });
    return '第15章と閉じた全文へのディープリンク';
  });
  await record('キーボード・入力ラベル・スクロール領域', async () => {
    await page.locator('#document-search').focus(); await page.keyboard.press('Tab'); assert(await page.locator('#clear-search').evaluate(element => element === document.activeElement));
    await page.keyboard.press('Tab'); assert(await page.locator('#expand-all').evaluate(element => element === document.activeElement));
    await page.keyboard.press('Enter'); assert.equal(await page.locator('.normative[open]').count(), 29);
    await page.keyboard.press('Tab'); await page.keyboard.press('Enter'); assert.equal(await page.locator('.normative[open]').count(), 0);
    assert.equal(await page.locator('label[for="document-search"]').count(), 1);
    assert.equal(await page.locator('.diagram-scroll[tabindex="0"]').count(), 38);
    return 'Tab・Enterで全文操作可能。SVGと表の横スクロール領域をフォーカス可能。';
  });
  await record('PC・タブレット・スマートフォンのページ横はみ出し', async () => {
    const evidence = [];
    for (const width of [1920, 1440, 1024, 768, 380, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      const bounds = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
      assert(bounds.scroll <= bounds.client + 1, `${width}px: ${JSON.stringify(bounds)}`);
      evidence.push({ width, ...bounds });
    }
    return evidence;
  });
  await record('SVGと表のモバイル横スクロール', async () => {
    await page.setViewportSize({ width: 380, height: 844 });
    const svgScroll = await page.locator('.diagram-scroll').first().evaluate(element => { const maximum = element.scrollWidth - element.clientWidth; element.scrollLeft = 120; return { maximum, scrollLeft: element.scrollLeft }; });
    assert(svgScroll.maximum > 0 && svgScroll.scrollLeft > 0);
    await page.locator('#source-26').evaluate(element => { element.open = true; });
    const tableScroll = await page.locator('#source-26 .table-scroll').evaluate(element => { element.scrollLeft = 160; return { maximum: element.scrollWidth - element.clientWidth, scrollLeft: element.scrollLeft }; });
    assert(tableScroll.maximum > 0 && tableScroll.scrollLeft > 0);
    await page.locator('#source-26').evaluate(element => { element.open = false; }); return { svgScroll, tableScroll };
  });
  await record('モバイル用目次：全28章の展開と移動', async () => {
    await page.locator('.mobile-toc summary').click(); assert.equal(await page.locator('.mobile-toc a[data-chapter]:visible').count(), 28);
    await page.locator('.mobile-toc a[data-chapter="chapter-22"]').click(); assert.equal(new URL(page.url()).hash, '#chapter-22');
    await page.locator('.mobile-toc').evaluate(element => { element.open = false; });
    return '380pxの画面でも、全章へ目次から移動できる';
  });
  await record('印刷：検索中も全章全文・印刷後の状態復元', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#document-search').fill('AT-S29');
    const before = await page.locator('.normative[open]').count();
    await page.emulateMedia({ media: 'print' });
    await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
    assert.equal(await page.locator('.normative[open]').count(), 29);
    assert.equal(await page.locator('.chapter:visible').count(), 28);
    assert.equal(await page.locator('.rail:visible').count(), 0);
    await page.pdf({ path: fileURLToPath(new URL('全文印刷確認.pdf', artifactDir)), format: 'A4', printBackground: true, preferCSSPageSize: true });
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    assert.equal(await page.locator('.normative[open]').count(), before);
    await page.emulateMedia({ media: 'screen' });
    await page.locator('#clear-search').click(); return '全28章・29全文が印刷対象。印刷後は検索と開閉状態が戻る。';
  });
  await record('表示証拠：PC・モバイル・図解一覧', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 }); await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: fileURLToPath(new URL('pc-先頭.png', artifactDir)) });
    await page.locator('#chapter-04').screenshot({ path: fileURLToPath(new URL('pc-構成図.png', artifactDir)) });
    await page.locator('#chapter-15').screenshot({ path: fileURLToPath(new URL('pc-本人操作.png', artifactDir)) });
    await page.setViewportSize({ width: 380, height: 844 }); await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: fileURLToPath(new URL('mobile-先頭.png', artifactDir)) });
    await page.locator('#chapter-13').screenshot({ path: fileURLToPath(new URL('mobile-補充フロー.png', artifactDir)) });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#source-28').evaluate(element => { element.open = true; });
    await page.locator('.implementation-reference').evaluate(e => {e.open=true;});
    for (let index = 0; index < 38; index += 1) await page.locator('figure').nth(index).screenshot({ path: fileURLToPath(new URL(`fig-${String(index + 1).padStart(2, '0')}.png`, artifactDir)) });
    return 'PC・スマートフォンと全38図のPNGを保存';
  });
  await record('JavaScript無効時：本文・図・全文閲覧', async () => {
    const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 380, height: 844 } });
    try {
      const noScriptPage = await context.newPage(); await noScriptPage.goto(pathToFileURL(htmlPath).href);
      assert.equal(await noScriptPage.locator('.chapter:visible').count(), 28); assert.equal(await noScriptPage.locator('figure svg').count(), 38);
      assert.equal(await noScriptPage.locator('.toolbar-box:visible').count(), 0);
      await noScriptPage.locator('#source-01 summary').click(); assert(await noScriptPage.locator('#source-01 .md').isVisible());
      assert(await noScriptPage.locator('noscript').isVisible()); return '依存なし・native detailsで全文閲覧。検索と印刷の自動展開はJSが必要。';
    } finally { await context.close(); }
  });
  await record('22根拠資料の全文収録・元資料一致', async () => {
    const records = await page.locator('.reference-document').evaluateAll(elements => elements.map(element => {
      const body = element.querySelector('.reference-text, .md').cloneNode(true); body.querySelectorAll('[data-ui-only]').forEach(e => e.remove());
      return { name: element.dataset.referenceName, text: body.textContent, pre: body.tagName === 'PRE' };
    }));
    assert.equal(records.length, Object.keys(documentData.references).length);
    for (const record of records) {
      const original = Buffer.from(documentData.references[record.name], 'base64').toString('utf8').replace(/\r\n/g, '\n');
      assert.equal(record.pre ? record.text : canonical(record.text), record.pre ? original : sourceText(original), `${record.name}全文不一致`);
    }
    return { visibleFullTextDocuments: records.length };
  });
  await record('タスク・AI・skills・追加試験の網羅', async () => {
    const text = await page.locator('#source-28 .md').textContent();
    for (let n = 2; n <= 11; n += 1) assert(text.includes(`T${String(n).padStart(2, '0')}`));
    for (const id of ['AI-O','AG-OBS','AG-KEY','AG-COPY','AG-INSIGHT','AG-CONFIG','AG-RECOVERY']) assert(text.includes(id));
    for (const name of ['rakuten-room-inago','rr-inago-observation','rr-inago-keywords','rr-inago-copywriter','rr-inago-insight','rr-inago-config','rr-inago-recovery']) assert(text.includes(name));
    for (let n = 1; n <= 10; n += 1) assert(text.includes(`AT-A${String(n).padStart(2, '0')}`));
    assert.equal(await page.locator('#source-28 figure').count(), 11);
    return { tasks: 10, aiRoles: 7, skills: 7, flows: 11, aiContractTests: 10 };
  });
  await record('通常完了直後と17:45例外の表現照合', async () => {
    for (const number of ['20', '22', '28']) {
      const text = await page.locator(`#chapter-${number}`).textContent();
      assert(['完了直後','終了直後','終わった直後'].some(expression => text.includes(expression))); assert(text.includes('17:45')); assert(text.includes('待')); assert(text.includes('打切') || text.includes('打ち切'));
    }
    const diagram = await page.locator('#task-F02 svg').textContent();
    assert(diagram.includes('終わった直後')); assert(diagram.includes('待たず'));
    assert(!html.includes('15達成なら早めに成果へ'));
    return '20・22・28章とF02：完了直後の通常経路、長時間化だけ17:45打切り。';
  });
  await record('根拠全文検索と文書内リンク', async () => {
    await page.locator('#reference-search').fill('U-20261003');
    assert((await page.locator('.reference-document:visible').count()) > 0);
    await page.locator('#reference-search').fill('存在しない検索語zzzz');
    assert.equal(await page.locator('.reference-document:visible').count(), 0);
    await page.locator('#reference-search').fill('');
    assert.equal(await page.locator('.reference-document:visible').count(), 22);
    const link = page.locator('#references a').filter({ hasText: '要件データ辞書' });
    const target = await link.getAttribute('href');
    await link.click();
    await page.waitForFunction(id => document.querySelector(id).open, target);
    return '22資料の全文検索と、辞書全文への内部移動。';
  });
  await record('単一HTMLを別領域へ置いた閲覧', async () => {
    const standalone = new URL('単体配布確認.html', artifactDir);
    await writeFile(standalone, html);
    const isolated = await browser.newContext();
    try {
      const tab = await isolated.newPage();
      const requests = [];
      tab.on('request', request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
      await tab.goto(standalone.href);
      assert.equal(await tab.locator('figure svg').count(), 38);
      assert.equal(await tab.locator('.reference-document').count(), 22);
      assert.equal(await tab.locator('#requirements-source').count(), 1);
      assert.deepEqual(requests, []);
      return 'HTML1ファイルで28章・38SVG・22根拠全文・編集sourceを閲覧。';
    } finally { await isolated.close(); }
  });

  await record('事前分析の実行設計を削除・外部結果だけ受入', async () => {
    const text = (await page.locator('.chapter').allTextContents()).join('\n');
    for (const forbidden of ['AG-SETUP', 'rr-inago-setup', 'T01 事前分析', '補足標本：', '基礎標本：', '標本を分析']) assert(!text.includes(forbidden), forbidden);
    assert.equal(await page.locator('#task-F01').count(), 0);
    const sourceText = await page.locator('#source-05').textContent();
    assert(sourceText.includes('別計画')); assert(sourceText.includes('実装する前'));
    assert(sourceText.includes('専用skill')); assert(sourceText.includes('作成しない'));
    for (const n of ['05','06','07']) assert((await page.locator('#source-'+n).textContent()).includes('確定'));
    return 'T01/F01/準備専用AI/skill/標本収集設計なし。5～7章は外部確定結果の受領と利用。';
  });
  await record('全担当名の対応表をフロー前に表示', async () => {
    const defs = await page.locator('#source-28 .glossary-group').allTextContents();
    const glossary = defs.join('\n');
    const body = await page.locator('#source-28 .md').evaluate(e => [...e.querySelectorAll('td,p,h4,svg text')].map(x => x.textContent).join('\n'));
    const scriptIds = [...new Set(body.match(/\b(?:S-[A-Z-]+|ORCH-S)\b/g))];
    const agentIds = [...new Set(body.match(/\b(?:AI-O|AG-[A-Z]+)\b/g))];
    const skills = [...new Set(body.match(/\b(?:rakuten-room-inago|rr-inago-[a-z-]+)\b/g))];
    for (const code of [...scriptIds,...agentIds,...skills]) assert(glossary.includes(code),code);
    assert.equal(defs.length,7); assert.equal(scriptIds.length,16); assert.equal(agentIds.length,7); assert.equal(skills.length,7);
    assert(await page.locator('#source-28').evaluate(e => [...e.querySelectorAll('.glossary-group')].every(g => Boolean(g.compareDocumentPosition(e.querySelector('figure')) & Node.DOCUMENT_POSITION_FOLLOWING))));
    return {programs:scriptIds.length,aiRoles:agentIds.length,skills:skills.length,definitionTables:defs.length};
  });
  await record('全追加ノードの主語・目的・対象・動作と省略なし', async () => {
    let count=0;
    for(const [id,nodes] of Object.entries(expandedFlows)) for(let i=0;i<nodes.length;i++) {
      const n=nodes[i];
      for(const key of ['actor','purpose','target','method','sentence','condition']) assert(n[key]?.length,key);
      assert(n.sentence.includes(n.actor+'は、')); assert(n.sentence.includes(n.purpose+'ために、')); assert(n.sentence.includes(n.target+'を'));
      const nodeText=await page.locator('[data-node="'+id+'-'+(i+1)+'"]').textContent();
      assert(canonical(nodeText).includes(canonical(n.sentence)),id+'-'+i+'本文省略');
      assert(canonical(nodeText).includes(canonical(n.condition)),id+'-'+i+'条件省略');
      count++;
    }
    assert.equal(await page.locator('.flow-transcript').count(),11);
    return {fullyExplainedNodes:count,flows:11,responsiveTranscripts:11};
  });

  await record('ブラウザ実行エラー・外部通信', () => { assert.deepEqual(errors, []); assert.deepEqual(remoteRequests, []); return { errors, remoteRequests }; });
} finally { await browser.close(); }
const report = { reviewedAt: new Date().toISOString(), browser: 'Microsoft Edge / Chromium / headless', sourceHash, total: tests.length, passed: tests.filter(test => test.status === 'PASS').length, failed: tests.filter(test => test.status === 'FAIL').length, tests };
await writeFile(new URL('検証結果.json', artifactDir), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
console.log(JSON.stringify(report, null, 2));
if (report.failed) process.exitCode = 1;
