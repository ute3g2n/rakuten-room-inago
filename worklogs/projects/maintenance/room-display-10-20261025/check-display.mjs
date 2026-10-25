// Local display-only verification: use the real collector, stop before details.
import { chromium } from 'playwright-core';
import { collectRoom } from '../../../../automation/inago/adapters/public_sources.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1'));
const inputs = JSON.parse(fs.readFileSync(path.join(root,'queries.json'),'utf8'));
const selected = process.argv.slice(2).map(Number);
const cases = selected.length ? inputs.filter(item=>selected.includes(item.no)) : inputs;
const evidence = path.join(root,'private');
fs.mkdirSync(evidence,{recursive:true});
const sourceHash = crypto.createHash('sha256').update(fs.readFileSync(
  path.resolve(root,'../../../../automation/inago/adapters/public_sources.mjs'))).digest('hex');
const browser = await chromium.launch({
  executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,
});
const results = [];
class DisplayOnlyStop extends Error {}
const originalStderr = process.stderr.write.bind(process.stderr);
let eventFile = null;
process.stderr.write = (chunk,...rest) => {
  if (eventFile) { fs.appendFileSync(eventFile,chunk); return true; }
  return originalStderr(chunk,...rest);
};

try {
  for (const input of cases) {
    const id = String(input.no).padStart(2,'0');
    const folder = path.join(evidence,id);
    fs.mkdirSync(folder,{recursive:true});
    eventFile = path.join(folder,'events.jsonl');
    const started = Date.now();
    const result = {
      no:input.no,query:input.query,original_attempt_id:input.attempt_id,
      original_observed_posts:input.observed_posts,
      attempt_id:`LOCAL-ROOM-DISPLAY-20261025-${id}`,
      started_at:new Date().toISOString(),source_sha256:sourceHash,
      expectation:'multiple_visible_posts_or_explicit_no_results',
      product_detail_opened:false,display_only_stop:false,
    };
    let context;
    let page;
    try {
      context = await browser.newContext({locale:'ja-JP',
        viewport:{width:1440,height:1000},recordVideo:{dir:path.join(folder,'video')},
      });
      await context.tracing.start({screenshots:true,snapshots:true,sources:true});
      page = await context.newPage();
      page.setDefaultTimeout(10000);
      page.setDefaultNavigationTimeout(30000);
      await page.screenshot({path:path.join(folder,'before.png')});
      const originalLocator = page.locator.bind(page);
      page.locator = (...args) => {
        const locator = originalLocator(...args);
        if (args[0] === '.items-search > .item') {
          // The collector calls nth only after completing result display checks.
          // Stop before any preview, product-detail click, or item-page retrieval.
          locator.nth = () => { throw new DisplayOnlyStop(); };
        }
        return locator;
      };
      try {
        const collected = await collectRoom(page,{
          ...input,attempt_id:result.attempt_id,run_id:'LOCAL-ROOM-DISPLAY-20261025',
          attempt_no:1,retry_of:null,likes_min:300,max_posts:2,deadline_at:null,
        },{environment:'public_ui'},Infinity,{code:null,retryAfterSeconds:null});
        result.collector_diagnostics = collected.diagnostics;
        result.collector_status = collected.response.status;
      } catch (error) {
        if (!(error instanceof DisplayOnlyStop)) throw error;
        result.display_only_stop = true;
      }
      await page.evaluate(()=>window.scrollTo(0,0));
      result.screen = await page.evaluate(()=>{
        const cards = [...document.querySelectorAll('.items-search > .item')];
        const visible = element => {
          const rect=element.getBoundingClientRect();
          const style=getComputedStyle(element);
          return rect.width>0 && rect.height>0 && rect.top<innerHeight && rect.bottom>0 &&
            style.visibility!=='hidden' && style.display!=='none';
        };
        const url=new URL(location.href);
        return {query:document.querySelector('#search-box')?.value,
          keyword:url.searchParams.get('keyword'),like:url.searchParams.get('like'),
          comment:url.searchParams.get('comment'),loaded_posts:cards.length,
          viewport_visible_posts:cards.filter(visible).length};
      });
      await page.screenshot({path:path.join(folder,'results.png')});
      await page.locator('.search-detail-wrap').click({timeout:5000});
      const popup=page.locator('.itemSearch-popup').filter({visible:true}).first();
      await popup.waitFor({state:'visible',timeout:5000});
      result.conditions={
        likes:await popup.locator('.search-popup-project').filter({hasText:'いいね数'})
          .locator('.select > .select-item').first().innerText(),
        comments:await popup.locator('.search-popup-project').filter({hasText:'コメント数'})
          .locator('.select > .select-item').first().innerText(),
      };
      await page.screenshot({path:path.join(folder,'conditions.png')});
      const confirmed=result.screen.query===input.query && result.screen.keyword===input.query &&
        result.screen.like==='300' && result.screen.comment==='' &&
        result.conditions.likes.trim()==='300件' &&
        result.conditions.comments.trim()==='コメント数指定なし';
      result.outcome=!confirmed ? 'conditions_unverified' :
        result.screen.viewport_visible_posts>=2 ? 'multiple_hits' :
        result.screen.loaded_posts===0 &&
          result.collector_diagnostics?.end_evidence==='explicit_no_results' ?
          'explicit_no_results' : 'not_confirmed';
      result.pass=['multiple_hits','explicit_no_results'].includes(result.outcome);
    } catch (error) {
      result.pass=false;
      result.outcome='operation_failed';
      result.error={name:error.name,code:error.code??null};
      if (page) await page.screenshot({path:path.join(folder,'failure.png')}).catch(()=>{});
    } finally {
      result.elapsed_seconds=Number(((Date.now()-started)/1000).toFixed(2));
      result.finished_at=new Date().toISOString();
      if (context) {
        await context.tracing.stop({path:path.join(folder,'trace.zip')}).catch(error=>{
          result.trace_error=error.name;result.pass=false;
        });
        await context.close().catch(error=>{result.close_error=error.name;result.pass=false;});
      }
      const events=fs.existsSync(eventFile) ? fs.readFileSync(eventFile,'utf8').split('\n')
        .filter(Boolean).map(line=>{try{return JSON.parse(line);}catch{return {};}}) : [];
      result.stage_observations=events.filter(event=>[
        'room_results_scan_begin','room_list_no_progress','room_list_progress',
        'room_results_complete','room_resolve_products','room_product_begin',
      ].includes(event.stage));
      result.product_detail_opened=events.some(event=>event.stage==='room_product_begin');
      if (result.product_detail_opened) result.pass=false;
      result.trace_saved=fs.existsSync(path.join(folder,'trace.zip'));
      result.video_files=fs.existsSync(path.join(folder,'video')) ?
        fs.readdirSync(path.join(folder,'video')).filter(name=>name.endsWith('.webm')) : [];
      fs.writeFileSync(path.join(folder,'result.json'),JSON.stringify(result,null,2)+'\n');
      results.push(result);
      fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify(results,null,2)+'\n');
      console.log(JSON.stringify({no:result.no,query:result.query,outcome:result.outcome,
        loaded_posts:result.screen?.loaded_posts,visible_posts:result.screen?.viewport_visible_posts,
        elapsed_seconds:result.elapsed_seconds,pass:result.pass}));
      eventFile=null;
    }
  }
} finally {
  process.stderr.write=originalStderr;
  await browser.close();
}
process.exitCode=results.length===cases.length && results.every(result=>result.pass) ? 0 : 1;
