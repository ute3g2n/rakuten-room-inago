/** Public UI only. No internal API, hidden state, credentials, CDP or stealth. */
import { chromium } from 'playwright-core';
import { pathToFileURL } from 'node:url';

export class SourceFailure extends Error {
  constructor(code) { super(code); this.code = code; }
}

const REF = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const allowedPeriods = new Set(['過去90日間', '過去3か月間', '過去3か月', '過去3ヶ月間', '過去3ヶ月']);
const compact = value => value.normalize('NFKC').replace(/\s/g, '');
const timestamp = () => new Date().toISOString();
const processStartedAt = Date.now();
function trace(stage, details = {}) {
  process.stderr.write(JSON.stringify({event:'public_source_stage',stage,elapsed_ms:Date.now()-processStartedAt,...details})+'\n');
}

function validateInput(input) {
  const { config, request, kind } = input ?? {};
  if (!['room', 'trends'].includes(kind) || !request || !config ||
      !['fixture', 'public_ui'].includes(config.environment) ||
      typeof config.chrome_executable !== 'string' ||
      !Number.isFinite(config.timeout_seconds) || config.timeout_seconds < 1 || config.timeout_seconds > 900 ||
      !REF.test(request.run_id) || typeof (request.query ?? request.parent_word) !== 'string' ||
      !(request.query ?? request.parent_word).trim()) throw new SourceFailure('invalid_request');
  if (config.environment === 'fixture') {
    const url = new URL(config.fixture_url);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
        url.username || url.password) throw new SourceFailure('invalid_fixture_endpoint');
  } else if (config.fixture_url != null) throw new SourceFailure('invalid_fixture_endpoint');
  if (kind === 'room') {
    if (!REF.test(request.attempt_id) || request.likes_min !== 300 || request.max_posts !== 50 ||
        !['related', 'theme_fallback'].includes(request.level) || request.query_kind !== request.level)
      throw new SourceFailure('invalid_request');
  } else if (!REF.test(request.request_id) || !REF.test(request.settings_version) ||
             request.range !== '3m' || request.geo !== 'JP' || request.language !== 'ja' ||
             request.search_type !== 'web' || request.list_type !== 'Top') throw new SourceFailure('invalid_request');
}

function guardPage(page, host, access) {
  let denial = null;
  page.on('response', response => {
    const type = response.request().resourceType();
    if (['document', 'xhr', 'fetch'].includes(type) && new URL(response.url()).hostname === host &&
        [401, 403, 429].includes(response.status())) denial = `http_${response.status()}`;
  });
  return async () => {
    if (access?.code) throw new SourceFailure(access.code);
    if (denial) throw new SourceFailure(denial);
    const text = await page.locator('body').innerText({ timeout: 1000 }).catch(() => '');
    if (/captcha|unusual traffic|access denied|アクセス.*拒否|ロボットではない|Too Many Requests/i.test(text))
      throw new SourceFailure('access_denied');
  };
}

async function waitDOM(page, check, predicate, argument, deadline) {
  while (Date.now() < deadline) {
    await check();
    if (await page.evaluate(predicate, argument)) return;
    await page.waitForTimeout(Math.min(100, Math.max(1, deadline - Date.now())));
  }
  throw new SourceFailure('source_timeout');
}

function productIds(raw) {
  try {
    const url = new URL(raw);
    const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent);
    if (url.protocol !== 'https:' || url.hostname !== 'item.rakuten.co.jp' || url.port || url.username || url.password || parts.length !== 2 ||
        !parts.every(part => REF.test(part))) return null;
    return { shop: parts[0], item: parts[1], url: `https://item.rakuten.co.jp/${parts.map(encodeURIComponent).join('/')}/` };
  } catch { return null; }
}

function safeUrlKind(raw) {
  try {
    if (typeof raw !== 'string') return 'invalid';
    if (!raw.trim()) return 'empty';
    const url = new URL(raw);
    if (url.href === 'about:blank') return 'blank';
    if (url.hostname === 'item.rakuten.co.jp') return 'rakuten_item';
    if (url.hostname.endsWith('.rakuten.co.jp') || url.hostname.endsWith('.rakuten.com')) return 'rakuten_other';
    if (url.protocol === 'https:') return 'other_https';
    return 'other_scheme';
  } catch { return 'invalid'; }
}

async function resolveProduct(page, card, check, deadline, access, rowIndex) {
  let popup;
  let phase = 'card_link';
  const detail = {row_index:rowIndex};
  trace('room_product_begin',detail);
  try {
    const directLink = card.locator('a[href^="https://item.rakuten.co.jp/"]').first();
    const direct = await directLink.count() ? await directLink.getAttribute('href', {timeout:300}) : null;
    if (direct && productIds(direct)) {
      trace('room_product_direct_link',detail);
      return productIds(direct);
    }
    phase = 'preview_click';
    trace('room_preview_click_begin',detail);
    await card.locator('.imgLink').click({ timeout: Math.min(2000, Math.max(1, deadline-Date.now())) });
    trace('room_preview_click_complete',detail);
    await check();
    phase = 'detail_button_ready';
    // ROOM exposes this market-details control as visible text, but its DOM
    // class is not stable across the public UI. Anchor to the rendered label.
    const button = page.getByText('楽天市場で詳細を見る', {exact:true}).filter({visible:true}).first();
    await button.waitFor({state:'visible',timeout:Math.min(1500,Math.max(1,deadline-Date.now()))});
    trace('room_detail_button_ready',detail);
    const control = await button.evaluate(el => ({
      tag: el.tagName.toLowerCase(),
      href: el instanceof HTMLAnchorElement ? el.href : null,
      target: el instanceof HTMLAnchorElement ? el.target : null,
    }));
    const directProduct = control.href ? productIds(control.href) : null;
    trace('room_detail_control_inspected',{
      ...detail,
      element_tag: ['a','button','div','span'].includes(control.tag) ? control.tag : 'other',
      has_href: Boolean(control.href),
      href_kind: control.href ? safeUrlKind(control.href) : 'none',
      link_target: control.target === '_blank' ? '_blank' : control.target === '_self' ? '_self' : control.target ? 'other' : 'none',
    });
    if (directProduct) {
      trace('room_product_resolved_visible_href',detail);
      return directProduct;
    }
    const popupPromise = page.waitForEvent('popup', { timeout: Math.min(5000, Math.max(1, deadline-Date.now())) }).catch(() => null);
    // Only the product-details control is clicked; no like/collect/buy action.
    phase = 'detail_button_click';
    trace('room_detail_button_click_begin',detail);
    await button.click({ timeout: Math.min(2000, Math.max(1, deadline-Date.now())) });
    phase = 'popup_wait';
    trace('room_popup_wait_begin',detail);
    popup = await popupPromise;
    if (!popup) {
      trace('room_popup_missing',detail);
      return null;
    }
    const popupUrlKind = raw => ({popup_url_kind:safeUrlKind(raw),popup_url_length:typeof raw === 'string' ? raw.length : -1});
    const popupMainFrame = popup.mainFrame();
    popup.on('framenavigated', frame => {
      if (frame === popupMainFrame) trace('room_popup_navigation_observed',{...detail,...popupUrlKind(frame.url())});
    });
    trace('room_popup_received',{...detail,...popupUrlKind(popup.url())});
    phase = 'popup_initial_url';
    const initialProduct = productIds(popup.url());
    if (initialProduct) {
      trace('room_product_resolved_initial_url',detail);
      return initialProduct;
    }
    const popupCheck = guardPage(popup, 'item.rakuten.co.jp', access);
    await popupCheck();
    phase = 'product_navigation';
    trace('room_product_navigation_begin',detail);
    await popup.waitForURL(url => url.hostname === 'item.rakuten.co.jp', {
      waitUntil: 'commit', timeout: Math.min(5000, Math.max(1, deadline-Date.now())),
    });
    trace('room_product_navigation_complete',{...detail,...popupUrlKind(popup.url())});
    await popupCheck();
    await check();
    phase = 'product_url_parse';
    const product = productIds(popup.url());
    trace(product ? 'room_product_resolved' : 'room_product_url_unresolved',detail);
    return product;
  } catch (error) {
    if (error instanceof SourceFailure) throw error;
    trace(error?.name === 'TimeoutError' ? 'room_product_timeout' : 'room_product_unresolved',{...detail,phase});
    await check();
    return null; // An unresolved identity still occupies its original source slot.
  } finally {
    if (popup) await popup.close().catch(() => {});
    const close = page.locator('.close-button').filter({ visible: true }).first();
    if (await close.count()) await close.click({ timeout: 1000 }).catch(() => {});
  }
}

export async function collectRoom(page, request, config, deadline, access) {
  const url = new URL(config.environment === 'fixture' ? config.fixture_url : 'https://room.rakuten.co.jp/search/item');
  for (const [key, value] of Object.entries({ keyword: request.query, colle: '', comment: '', like: '300', user_id: '', user_name: '', original_photo: '0' })) url.searchParams.set(key, value);
  const check = guardPage(page, url.hostname, access);
  trace('room_navigation');
  await page.goto(url.href, { waitUntil: 'domcontentloaded' });
  trace('room_document_loaded');
  await check();
  await waitDOM(page, check, () => !!document.querySelector('#search-box'), null, deadline);
  trace('room_search_box_ready');
  if (await page.locator('#search-box').inputValue() !== request.query ||
      new URL(page.url()).searchParams.get('like') !== '300' ||
      new URL(page.url()).searchParams.get('comment') !== '') throw new SourceFailure('query_not_reflected');
  trace('room_condition_controls');
  await page.locator('.search-detail-wrap').click();
  trace('room_condition_popup_opened');
  await check();
  const conditionPopup = page.locator('.itemSearch-popup').filter({visible:true});
  const likeDisplay = await conditionPopup.locator('.search-popup-project').filter({hasText:'いいね数'}).locator('.select > .select-item').first().innerText();
  const commentDisplay = await conditionPopup.locator('.search-popup-project').filter({hasText:'コメント数'}).locator('.select > .select-item').first().innerText();
  if (compact(likeDisplay) !== '300件' || compact(commentDisplay) !== 'コメント数指定なし')
    throw new SourceFailure('search_conditions_unverified');
  trace('room_conditions_verified');
  let conditionPopupClosed = false;
  trace('room_condition_toggle_close_begin');
  await page.locator('.search-detail-wrap').click({timeout:1500}).catch(() => {});
  try {
    await conditionPopup.waitFor({state:'hidden',timeout:1500});
    conditionPopupClosed = true;
  } catch {}
  if (!conditionPopupClosed) {
    trace('room_condition_escape_close_begin');
    await page.keyboard.press('Escape').catch(() => {});
    try {
      await conditionPopup.waitFor({state:'hidden',timeout:1500});
      conditionPopupClosed = true;
    } catch {}
  }
  if (!conditionPopupClosed) throw new SourceFailure('condition_popup_close_failed');
  trace('room_condition_popup_closed');
  await check();
  const cards = page.locator('.items-search > .item');
  const endVisible = () => page.evaluate(() => Array.from(document.querySelectorAll('body *')).some(e =>
    e.children.length === 0 && !e.closest('.items-search, .item-preview') && e.getClientRects().length > 0 &&
    /^(検索結果は以上です|検索結果がありません|該当する商品がありません|検索結果はありません)$/.test(e.textContent.trim())));
  let loaded = 0;
  let exhausted = false;
  let priorLoaded = -1;
  while (Date.now() < deadline) {
    await check();
    loaded = await cards.count();
    exhausted = await endVisible();
    if (loaded !== priorLoaded) {
      trace('room_list_progress',{loaded_posts:loaded});
      priorLoaded = loaded;
    }
    if (loaded >= 50 || exhausted) break;
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const before = loaded;
    const changeDeadline = Math.min(deadline, Date.now()+2000);
    try {
      await waitDOM(page, check, n => document.querySelectorAll('.items-search > .item').length > n ||
        Array.from(document.querySelectorAll('body *')).some(e => e.children.length === 0 && !e.closest('.items-search, .item-preview') && e.getClientRects().length &&
          /^(検索結果は以上です|検索結果がありません|該当する商品がありません|検索結果はありません)$/.test(e.textContent.trim())), before, changeDeadline);
    } catch (error) {
      if (!(error instanceof SourceFailure) || error.code !== 'source_timeout') throw error;
      // Stability alone never proves zero/end. The overall finite timeout remains.
    }
  }
  if (loaded < 50 && !exhausted) throw new SourceFailure('incomplete_search');
  trace('room_resolve_products',{loaded_posts:loaded,accepted_posts:Math.min(50,loaded)});
  const fetched = timestamp();
  const posts = [];
  const facts = {};
  for (let index = 0; index < Math.min(50, loaded); index++) {
    if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
    const card = cards.nth(index);
    const name = await card.locator('.imgLink img').first().getAttribute('alt').catch(() => null);
    const image = await card.locator('.imgLink img').first().getAttribute('src').catch(() => null);
    const ids = await resolveProduct(page, card, check, deadline, access, index+1);
    trace('room_product_row_complete',{row_index:index+1,accepted_posts:index+1});
    const evidenceRef = `${request.attempt_id}:row:${index+1}`;
    posts.push({ internal_index: index+1, stable_room_id: null, room_url: null,
      likes: null, comments: null, item_url: ids?.url ?? null, observed_shop_id: ids?.shop ?? null,
      observed_item_id: ids?.item ?? null, image_ref: image, actor_ref: null, published_at: null,
      evidence_ref: evidenceRef, observed_at: fetched, availability: ids ? 'observed' : 'unknown' });
    if (ids) facts[evidenceRef] = { offer_key: `${ids.shop}:${ids.item}`, name: name || null,
      item_url: ids.url, image_ref: image, evidence_ref: evidenceRef, observed_at: fetched,
      origin: 'public_product_image_label' };
  }
  await check();
  return { response: { ...Object.fromEntries(['attempt_id','run_id','query','parent_refs','level','likes_min','max_posts','query_kind','keyword_snapshot_ref','selected_word_ref'].map(k => [k,request[k] ?? null])),
    current_search_id: request.attempt_id, search_loaded: true, internal_order_confirmed: true,
    status: 'observed', fetched_at: fetched, end_reached: exhausted, error_code: null,
    posts, loaded_posts: loaded, condition_evidence_ref: `${request.attempt_id}:condition` },
    product_facts: facts, diagnostics: { source: 'public_dom', loaded_posts: loaded, accepted_posts: posts.length,
      unused_posts: Math.max(0,loaded-posts.length), condition: { query: request.query, like: 300, comment: null,
        like_display:likeDisplay.trim(),comment_display:commentDisplay.trim() }, end_evidence: exhausted ? 'explicit_public_end_text' : 'first_50' } };
}

async function emptyEvidence(widget) {
  const matches = await widget.getByText(/^(データがありません|この検索キーワードのデータはありません)$/).all();
  for (const match of matches) {
    if (await match.isVisible() && await match.evaluate(e=>!e.closest('a.progress-label'))) return match;
  }
  return null;
}

export async function collectTrends(page, request, config, deadline, access) {
  const url = new URL(config.environment === 'fixture' ? config.fixture_url : 'https://trends.google.co.jp/trends/explore');
  for (const [key,value] of Object.entries({date:'today 3-m',geo:'JP',q:request.parent_word,hl:'ja'})) url.searchParams.set(key,value);
  const check = guardPage(page,url.hostname,access);
  trace('trends_navigation');
  await page.goto(url.href, { waitUntil:'domcontentloaded' });
  await check();
  const consent = page.getByRole('button', { name: 'OK, got it', exact: true });
  if (await consent.isVisible()) await consent.click();
  await waitDOM(page,check,() => !!document.querySelector('input[aria-label="検索キーワードを追加"]'),null,deadline);
  if (await page.getByLabel('検索キーワードを追加',{exact:true}).inputValue() !== request.parent_word ||
      new URL(page.url()).searchParams.get('date') !== 'today 3-m' || new URL(page.url()).searchParams.get('geo') !== 'JP')
    throw new SourceFailure('query_not_reflected');
  const period = await page.locator('[aria-label^="Select time period"]').innerText();
  if (!allowedPeriods.has(compact(period)) || !(await page.locator('[aria-label^="Select stories location"]').innerText()).includes('ウェブ検索') ||
      !(await page.getByText('日本',{exact:true}).filter({visible:true}).count()) || !(await page.getByText('検索キーワード',{exact:true}).filter({visible:true}).count()))
    throw new SourceFailure('trends_scope_not_reflected');
  await waitDOM(page,check,() => Array.from(document.querySelectorAll('.fe-related-queries')).some(e => e.innerText.startsWith('関連キーワード')),null,deadline);
  const widget = page.locator('.fe-related-queries').filter({ hasText: '関連キーワード' }).filter({ visible: true });
  if (await widget.count() !== 1) throw new SourceFailure('query_widget_ambiguous');
  const dropdown = widget.locator('md-select.bullets-view-selector');
  const prior = await widget.locator('a.progress-label').allTextContents();
  const priorEmpty = await emptyEvidence(widget);
  const priorEmptyHandle = priorEmpty ? await priorEmpty.elementHandle() : null;
  const alreadyTop = (await dropdown.getAttribute('aria-label') ?? await dropdown.innerText()).includes('人気');
  if (!alreadyTop) {
    trace('trends_switch_top');
    await dropdown.click();
    await page.getByRole('option',{name:'人気',exact:true}).filter({ visible:true }).click();
    // A changed dropdown alone is insufficient: require changed row data and
    // completion of the UI-triggered network work. Identical, unprovable data
    // fails closed rather than treating Rising as Top.
    if (prior.length) await waitDOM(page,check, previous => {
      const widget = Array.from(document.querySelectorAll('.fe-related-queries')).find(e=>e.innerText.startsWith('関連キーワード'));
      const current = Array.from(widget?.querySelectorAll('a.progress-label') ?? []).map(e=>e.textContent);
      return JSON.stringify(current) !== JSON.stringify(previous) && current.length > 0;
    },prior,deadline);
    else if (priorEmptyHandle) {
      // Rising's zero marker cannot establish Top zero. Require its removal or
      // hiding for the new view; if the UI exposes no transition, fail closed.
      await page.waitForFunction(e=>!e.isConnected || e.getClientRects().length===0,
        priorEmptyHandle,{timeout:Math.max(1,deadline-Date.now())});
    }
    await page.waitForLoadState('networkidle',{timeout:Math.max(1,deadline-Date.now())});
  }
  await check();
  const words = [];
  while (words.length < 20) {
    const empty = words.length === 0 ? await emptyEvidence(widget) : null;
    if (empty && !(await widget.locator('a.progress-label').filter({visible:true}).count())) {
      const sameOld = !alreadyTop && priorEmptyHandle &&
        await empty.evaluate((e,old)=>e===old,priorEmptyHandle);
      if (sameOld) {
        // An old marker temporarily hidden and shown again is still Rising.
        // Keep its identity until either new rows or a replacement empty exists.
        await page.waitForFunction(old=>!old.isConnected ||
          Array.from(document.querySelectorAll('.fe-related-queries')).some(widget=>
            widget.innerText.startsWith('関連キーワード') && Array.from(widget.querySelectorAll('a.progress-label')).some(e=>e.getClientRects().length)),
          priorEmptyHandle,{timeout:Math.max(1,deadline-Date.now())});
        continue;
      }
      break;
    }
    await waitDOM(page,check, n => {
      const widget = Array.from(document.querySelectorAll('.fe-related-queries')).find(e => e.innerText.startsWith('関連キーワード'));
      const ranks = Array.from(widget?.querySelectorAll('a.progress-label .label-line-number') ?? []).map(e=>Number(e.textContent.trim()));
      return ranks.length > 0 && ranks.every((rank,i)=>rank === n+i+1);
    },words.length,deadline);
    if (!(await dropdown.innerText()).includes('人気')) throw new SourceFailure('top_not_reflected');
    const observed = await widget.locator('a.progress-label').evaluateAll(rows => rows.map(e => {
      const value = e.querySelector('.progress-value')?.textContent.trim() ?? '';
      return {raw:e.querySelector('.label-text')?.textContent.trim(),source_rank:Number(e.querySelector('.label-line-number')?.textContent.trim()),
        popularity_index:/^\d+(\.\d+)?$/.test(value) ? Number(value) : null};
    }));
    if (!observed.length) throw new SourceFailure('top_rows_unreadable');
    for (const row of observed) {
      if (words.length === 20) break;
      if (!row.raw || row.raw.length > 500 || row.source_rank !== words.length+1) throw new SourceFailure('top_rank_not_fresh');
      words.push({word_id:`${request.request_id}:w${row.source_rank}`, ...row});
    }
    if (words.length === 20) break;
    const next = widget.getByRole('button',{name:'Next',exact:true});
    if (!(await next.isEnabled())) break;
    await next.click();
    await check();
  }
  await priorEmptyHandle?.dispose();
  return { response: { request_id:request.request_id,snapshot_id:request.request_id,run_id:request.run_id,
    settings_version:request.settings_version,source:'google_trends_public_ui',parent_word:request.parent_word,current_parent:request.parent_word,
    refresh_complete:true,language:'ja',geo:'JP',search_type:'web',range:'3m',list_type:'Top',ui_period_label:period,
    status:'observed',fetched_at:timestamp(),error_code:null,words }, product_facts:{},
    diagnostics:{source:'public_dom',word_count:words.length,period_label:period,original_top:true} };
}

export async function main(input) {
  validateInput(input);
  const {config,request,kind} = input;
  const deadline = Date.now()+config.timeout_seconds*1000;
  trace('browser_launch_begin');
  const browser = await chromium.launch({executablePath:config.chrome_executable,headless:true});
  trace('browser_launch_complete');
  const access = {code:null};
  try {
    trace('browser_context_begin');
    const context = await browser.newContext({locale:'ja-JP'});
    trace('browser_context_complete');
    // Installed before newPage/popup: even the first popup document and its
    // redirects are monitored. No response body or internal API is queried.
    context.on('response', response => {
      const type = response.request().resourceType();
      const host = new URL(response.url()).hostname;
      const sourceHost = host === 'room.rakuten.co.jp' || /(^|\.)trends\.google\.(co\.jp|com)$/.test(host);
      if ([401,403,429].includes(response.status()) && (type === 'document' || sourceHost && ['xhr','fetch'].includes(type)))
        access.code = `http_${response.status()}`;
    });
    if (config.environment === 'fixture') await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === new URL(config.fixture_url).origin) return route.continue();
      if (url.hostname === 'item.rakuten.co.jp') return route.fulfill({status:200,contentType:'text/html',body:'<title>合成商品の詳細</title>'});
      return route.abort();
    });
    trace('browser_page_begin');
    const page = await context.newPage();
    trace('browser_page_complete');
    page.setDefaultTimeout(Math.min(10000,config.timeout_seconds*1000));
    page.setDefaultNavigationTimeout(Math.min(30000,config.timeout_seconds*1000));
    return await (kind === 'room' ? collectRoom(page,request,config,deadline,access) : collectTrends(page,request,config,deadline,access));
  } catch (error) {
    // A later DOM/navigation timeout must not erase an already observed denial.
    if (access.code) throw new SourceFailure(access.code);
    throw error;
  } finally {
    try { await browser.close(); }
    catch (error) { if (access.code) throw new SourceFailure(access.code); throw error; }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk;
    if (raw.length > 1000000) throw new SourceFailure('request_too_large');
  }
  try { process.stdout.write(JSON.stringify(await main(JSON.parse(raw)))+'\n'); }
  catch (error) { process.stdout.write(JSON.stringify({error_code:error instanceof SourceFailure ? error.code : error?.name === 'TimeoutError' ? 'source_timeout' : 'browser_adapter_error'})+'\n'); process.exitCode=1; }
}
