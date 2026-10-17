import {readFile,writeFile} from 'node:fs/promises';
const p=new URL('review.mjs',import.meta.url);
let s=await readFile(p,'utf8');
s=s.replaceAll('html-review-1.2/','html-review-1.3/').replaceAll('HTML正本 1.2','HTML正本 1.3')
 .replaceAll('IR28・AT28・重要経路29','IR28・AT28・重要経路31')
 .replace('for (let n = 1; n <= 29;','for (let n = 1; n <= 31;')
 .replace('scenarios: 29','scenarios: 31')
 .replace("assert(diagram.includes('終わった直後'));", "assert(diagram.includes('終わった直後'));")
 .replace("    for (let index = 0; index < 38;", "    await page.locator('.implementation-reference').evaluate(e => {e.open=true;});\n    for (let index = 0; index < 38;");
const extra=String.raw`
  await record('日次全体図を先頭に置き、各サブフローを手順へ対応',async()=>{
    const ids=await page.locator('#source-28 figure').evaluateAll(es=>es.map(e=>e.id));
    assert.deepEqual(ids,['task-F02','task-F00','task-F03','task-F04','task-F06','task-F09','task-F05','task-F07','task-F08','task-F10','task-F11']);
    assert.equal(await page.locator('#task-F02 [data-node]').count(),6);
    const text=await page.locator('#source-28 .md').textContent();
    for(const n of [1,2,3,4,5,6]) assert(text.includes('日次全体の手順'+n) || text.includes('日次全体の手順2・3'));
    const titles=await page.locator('#source-28 svg title').allTextContents();
    for(const title of titles)assert(!/^Fd/.test(title),title);
    for(const id of ['F00','F03','F04','F06','F09']) assert.equal(await page.locator('a[href="#task-'+id+'"]').count(),1);
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
      await page.locator('a[href="#task-'+id+'"]').click();assert.equal(new URL(page.url()).hash,'#task-'+id);assert(await page.locator('#task-'+id).isVisible());
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
`;
s=s.replace("  await record('HTML内部sourceハッシュと表示版の一致'",extra+"\n  await record('HTML内部sourceハッシュと表示版の一致'");
await writeFile(new URL('review-1.3.mjs',import.meta.url),s);
