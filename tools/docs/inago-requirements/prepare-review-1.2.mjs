import { readFile, writeFile } from 'node:fs/promises';
import { loadDocument, serializeDocument, base, review12, htmlName } from './document-source.mjs';
const p = new URL('review.mjs', import.meta.url);
let s = await readFile(p, 'utf8');
s = s.replaceAll('html-review-1.1/', 'html-review-1.2/').replaceAll('HTML正本 1.1', 'HTML正本 1.2')
 .replaceAll('39', '38').replaceAll("'AI-O','AG-SETUP',", "'AI-O',")
 .replaceAll("'rakuten-room-inago','rr-inago-setup',", "'rakuten-room-inago',")
 .replace('for (let n = 1; n <= 11;', 'for (let n = 2; n <= 11;')
 .replace("assert.equal(await page.locator('#source-28 figure').count(), 12)", "assert.equal(await page.locator('#source-28 figure').count(), 11)")
 .replace('tasks: 11, aiRoles: 8, skills: 8, flows: 12', 'tasks: 10, aiRoles: 7, skills: 7, flows: 11')
 .replace("assert(diagram.includes('完了直後')); assert(diagram.includes('待たない'));", "assert(diagram.includes('終わった直後')); assert(diagram.includes('待たず'));")
 .replace("const body = element.querySelector('.md').cloneNode(true);", "const body = element.querySelector('.md').cloneNode(true); body.querySelectorAll('[data-ui-only]').forEach(e => e.remove());")
 .replace("const body = element.querySelector('.reference-text, .md');", "const body = element.querySelector('.reference-text, .md').cloneNode(true); body.querySelectorAll('[data-ui-only]').forEach(e => e.remove());")
 .replace("import { loadDocument }", "import { expandedFlows } from './flow-descriptions.mjs';\nimport { loadDocument }");
const extra = `
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
    const body = await page.locator('#source-28 .md').textContent();
    const scriptIds = [...new Set(body.match(/\\b(?:S-[A-Z-]+|ORCH-S)\\b/g))];
    const agentIds = [...new Set(body.match(/\\b(?:AI-O|AG-[A-Z]+)\\b/g))];
    const skills = [...new Set(body.match(/\\b(?:rakuten-room-inago|rr-inago-[a-z-]+)\\b/g))];
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
`;
s = s.replace("  await record('ブラウザ実行エラー・外部通信'", extra+"\n  await record('ブラウザ実行エラー・外部通信'");
await writeFile(p,s);
const d=await loadDocument();
d.source=d.source.replace('図・表の説明と用語の対応表を改善したもの。','図・表の説明と用語の対応表を改善し、事前分析の実行設計を削除したもの。')
 .replace('本文長さ/タグ/分類名、UI50/恒久ID/無料Trends継続、現行成果の新出力接続は3章/11の検証責任・期限・不成立時に従う。','実装者は、本文長さ・タグ・分類名を実装前の別計画の確定結果として受け取る。日次のUI50・恒久ID・無料Trends継続・現行成果の新出力接続は、実装後のテストで確認する。旧11の事前分析手順は現在の実装対象に含めない。');
const htmlP=new URL(htmlName,base);
await writeFile(htmlP,(await readFile(htmlP,'utf8')).replace(/(<script id="requirements-source" type="application\/json">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+serializeDocument(d)+b));
await writeFile(new URL('現在要件全文.txt',review12),d.source);
