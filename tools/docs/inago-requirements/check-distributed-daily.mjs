import {chromium} from 'playwright-core';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const evidence=new URL('../../../worklogs/projects/requirements/reviews/html-review-1.3/',import.meta.url);
await mkdir(evidence,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const url=encodeURI('file:///G:/マイドライブ/イナゴスキル要件定義書.html#task-F02');
 await page.goto(url);await page.evaluate(()=>document.fonts.ready);
 assert((await page.title()).includes('1.3'));
 assert.equal(await page.locator('#source-28 figure').first().getAttribute('id'),'task-F02');
 assert.equal(await page.locator('#task-F02 [data-node]').count(),6);
 assert.equal(await page.locator('#implementation-test-flow').evaluate(e=>e.open),false);
 assert.equal(await page.locator('#task-F11').isVisible(),false);
 await page.screenshot({path:fileURLToPath(new URL('配布先-日次全体図.png',evidence))});
 await writeFile(new URL('配布先表示確認.json',evidence),JSON.stringify({version:'1.3',firstDiagram:'日次フロー全体図',dailySteps:6,implementationReferenceInitiallyHidden:true,url},null,2));
 console.log('Gドライブ配布先：版1.3・日次全体図先頭・6手順・テスト初期非表示を確認。');
}finally{await browser.close();}
