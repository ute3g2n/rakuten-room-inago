/** Public UI only. No internal API, hidden state, credentials, CDP or stealth. */
import { chromium } from 'playwright-core';
import { pathToFileURL } from 'node:url';

export class SourceFailure extends Error {
  constructor(code, retryAfterSeconds = null) {
    super(code);
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

const REF = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const allowedPeriods = new Set(['過去90日間', '過去90日', '過去3か月間', '過去3か月', '過去3ヶ月間', '過去3ヶ月']);
const compact = value => value.normalize('NFKC').replace(/\s/g, '');
const timestamp = () => new Date().toISOString();
const processStartedAt = Date.now();
const MAX_SOURCE_TIMEOUT_SECONDS = 17100;
// A slow Rakuten marketplace redirect can take longer than the detail-click
// itself. Keep it bounded, but do not discard a route that eventually commits.
const PRODUCT_NAVIGATION_TIMEOUT_MS = 60000;
const PRODUCT_DESTINATION_TIMEOUT_MS = 30000;
const PRODUCT_DETAIL_CLICK_TIMEOUT_MS = 15000;
const PRODUCT_BUTTON_TIMEOUT_MS = 120000;
const PRODUCT_RESOLUTION_FAILURE_LIMIT = 10;
let activeTrace = {stage:'startup',row_index:null};
const browserDiagnostics = {
  pages:0,
  consoleErrors:0,
  pageErrors:0,
  failedRequests:0,
  popupNavigations:0,
  droppedGroups:0,
  groups:new Map(),
};
function trace(stage, details = {}) {
  activeTrace = {
    stage,
    row_index:Number.isInteger(details.row_index) ? details.row_index : activeTrace.row_index,
  };
  process.stderr.write(JSON.stringify({event:'public_source_stage',stage,elapsed_ms:Date.now()-processStartedAt,...details})+'\n');
}

function safeThrownErrorClass(error) {
  const allowed = new Set([
    'Error','TimeoutError','TypeError','ReferenceError','SyntaxError','RangeError',
    'URIError','EvalError','AggregateError','TargetClosedError','BrowserClosedError',
    'ProtocolError','SourceFailure',
  ]);
  return allowed.has(error?.name) ? error.name : 'other';
}

function safeNetworkCode(error) {
  if (typeof error?.message !== 'string') return null;
  const match = error.message.match(/\bnet::(ERR_[A-Z0-9_]{1,64})\b/);
  return match?.[1] ?? null;
}

function safeErrorKind(error) {
  const message = typeof error?.message === 'string' ? error.message.toLowerCase() : '';
  if (safeNetworkCode(error)) return 'network_error';
  if (error?.name === 'TimeoutError') return 'timeout';
  if (/target (?:has been )?closed|browser (?:has been )?closed/.test(message)) return 'target_closed';
  if (/page (?:has been )?closed/.test(message)) return 'page_closed';
  if (/navigation.{0,80}interrupted|interrupted.{0,80}navigation/.test(message)) return 'navigation_interrupted';
  if (/protocol error/.test(message)) return 'protocol_error';
  if (/browser.{0,40}(launch|executable)|(launch|executable).{0,40}browser/.test(message)) return 'browser_launch_error';
  return 'other_error';
}

function diagnostic(signal, fields = {}) {
  if (signal === 'console_error') browserDiagnostics.consoleErrors++;
  else if (signal === 'page_error') browserDiagnostics.pageErrors++;
  else if (signal === 'failed_request') browserDiagnostics.failedRequests++;
  else if (signal === 'popup_navigation') browserDiagnostics.popupNavigations++;
  const keyFields = {
    signal,
    stage:activeTrace.stage,
    row_index:activeTrace.row_index,
    ...fields,
  };
  const key = JSON.stringify(keyFields);
  const existing = browserDiagnostics.groups.get(key);
  if (existing) existing.count++;
  else if (browserDiagnostics.groups.size < 2048) browserDiagnostics.groups.set(key,{
    ...keyFields,
    count:1,
    first_elapsed_ms:Date.now()-processStartedAt,
  });
  else browserDiagnostics.droppedGroups++;
}

function safeHostClass(raw) {
  try {
    const host = new URL(raw).hostname;
    if (host === 'room.rakuten.co.jp') return 'room';
    if (host === 'item.rakuten.co.jp') return 'product';
    if (host.endsWith('.rakuten.co.jp') || host.endsWith('.rakuten.com')) return 'rakuten_other';
    if (/(^|\.)trends\.google\.(co\.jp|com)$/.test(host)) return 'trends';
    return 'other';
  } catch { return 'other'; }
}

function safeResourceType(raw) {
  return ['document','stylesheet','image','media','font','script','texttrack','xhr','fetch','eventsource','websocket','manifest','other'].includes(raw) ? raw : 'other';
}

function safeFailureClass(raw) {
  const value = String(raw || '').toUpperCase();
  if (value.includes('ERR_ABORTED')) return 'aborted';
  if (value.includes('ERR_BLOCKED_BY_CLIENT')) return 'blocked';
  if (value.includes('ERR_NAME_NOT_RESOLVED')) return 'dns';
  if (value.includes('ERR_CONNECTION_RESET')) return 'connection_reset';
  if (value.includes('ERR_CONNECTION_CLOSED')) return 'connection_closed';
  if (value.includes('ERR_TIMED_OUT')) return 'timed_out';
  if (value.includes('ERR_CONNECTION_REFUSED')) return 'connection_refused';
  return 'other';
}

function safeErrorClass(raw) {
  return ['Error','TypeError','ReferenceError','SyntaxError','RangeError','URIError','EvalError','AggregateError','SecurityError','NotAllowedError'].includes(raw) ? raw : 'other';
}

function installBrowserDiagnostics(context) {
  context.on('page', page => {
    const pageRole = browserDiagnostics.pages++ === 0 ? 'main' : 'popup';
    page.on('console', message => {
      if (message.type() === 'error') diagnostic('console_error',{page_role:pageRole});
    });
    page.on('pageerror', error => diagnostic('page_error',{page_role:pageRole,error_class:safeErrorClass(error?.name)}));
    page.on('requestfailed', request => diagnostic('failed_request',{
      page_role:pageRole,
      host_class:safeHostClass(request.url()),
      resource_type:safeResourceType(request.resourceType()),
      failure_class:safeFailureClass(request.failure()?.errorText),
    }));
    page.on('request', request => {
      const hostClass = safeHostClass(request.url());
      const captureNavigation = pageRole === 'popup' ||
        pageRole === 'main' && ['trends_home_navigation','trends_home_commit_observed'].includes(activeTrace.stage);
      if (captureNavigation && request.isNavigationRequest() && request.resourceType() === 'document')
        diagnostic('navigation_request',{
          page_role:pageRole,
          host_class:hostClass,
          resource_type:'document',
        });
    });
    page.on('response', response => {
      const request = response.request();
      const hostClass = safeHostClass(response.url());
      const captureNavigation = pageRole === 'popup' ||
        pageRole === 'main' && ['trends_home_navigation','trends_home_commit_observed'].includes(activeTrace.stage);
      if (captureNavigation && request.isNavigationRequest() && request.resourceType() === 'document')
        diagnostic('navigation_response',{
          page_role:pageRole,
          host_class:hostClass,
          resource_type:'document',
          status:response.status(),
        });
    });
    page.on('requestfinished', request => {
      const hostClass = safeHostClass(request.url());
      const captureNavigation = pageRole === 'popup' ||
        pageRole === 'main' && ['trends_home_navigation','trends_home_commit_observed'].includes(activeTrace.stage);
      if (captureNavigation && request.isNavigationRequest() && request.resourceType() === 'document')
        diagnostic('navigation_finished',{
          page_role:pageRole,
          host_class:hostClass,
          resource_type:'document',
        });
    });
    page.on('framenavigated', frame => {
      if (pageRole === 'popup' && frame === page.mainFrame())
        diagnostic('popup_navigation',{page_role:pageRole,url_kind:safeUrlKind(frame.url())});
    });
  });
}

function flushBrowserDiagnostics() {
  process.stderr.write(JSON.stringify({
    event:'public_source_diagnostic',signal:'summary',
    page_count:browserDiagnostics.pages,
    console_error_count:browserDiagnostics.consoleErrors,
    page_error_count:browserDiagnostics.pageErrors,
    failed_request_count:browserDiagnostics.failedRequests,
    popup_navigation_count:browserDiagnostics.popupNavigations,
    dropped_groups:browserDiagnostics.droppedGroups,
  })+'\n');
  for (const fields of browserDiagnostics.groups.values())
    process.stderr.write(JSON.stringify({event:'public_source_diagnostic',...fields})+'\n');
}

function validateInput(input) {
  const { config, request, kind } = input ?? {};
  if (!['room', 'trends'].includes(kind) || !request || !config ||
      !['fixture', 'public_ui'].includes(config.environment) ||
      typeof config.chrome_executable !== 'string' ||
      typeof config.headless !== 'boolean' ||
      !Number.isInteger(config.fixture_product_delay_ms) || config.fixture_product_delay_ms < 0 ||
      (config.environment !== 'fixture' && config.fixture_product_delay_ms !== 0) ||
      !Number.isInteger(config.fixture_browser_startup_delay_ms) || config.fixture_browser_startup_delay_ms < 0 ||
      (config.environment !== 'fixture' && config.fixture_browser_startup_delay_ms !== 0) ||
      (config.fixture_room_scroll_delay_ms !== undefined &&
        (!Number.isInteger(config.fixture_room_scroll_delay_ms) || config.fixture_room_scroll_delay_ms < 0 ||
         (config.environment !== 'fixture' && config.fixture_room_scroll_delay_ms !== 0))) ||
      (config.fixture_expire_deadline_before_room_restore !== undefined &&
        (typeof config.fixture_expire_deadline_before_room_restore !== 'boolean' ||
         (config.environment !== 'fixture' && config.fixture_expire_deadline_before_room_restore))) ||
      !Number.isFinite(config.timeout_seconds) || config.timeout_seconds < 1 || config.timeout_seconds > MAX_SOURCE_TIMEOUT_SECONDS ||
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
  let retryAfterSeconds = null;
  page.on('response', response => {
    const type = response.request().resourceType();
    if (['document', 'xhr', 'fetch'].includes(type) && new URL(response.url()).hostname === host &&
        [401, 403, 429].includes(response.status())) {
      denial = `http_${response.status()}`;
      if (response.status() === 429) {
        retryAfterSeconds = parseRetryAfter(response.headers()['retry-after']);
        if (retryAfterSeconds !== null && access)
          access.retryAfterSeconds = Math.max(access.retryAfterSeconds ?? 0, retryAfterSeconds);
      }
    }
  });
  return async () => {
    if (access?.code) throw new SourceFailure(access.code, access.retryAfterSeconds);
    if (denial) throw new SourceFailure(denial, retryAfterSeconds);
    const text = await page.locator('body').innerText({ timeout: 1000 }).catch(() => '');
    if (/captcha|unusual traffic|access denied|アクセス.*拒否|ロボットではない|Too Many Requests/i.test(text))
      throw new SourceFailure('access_denied');
  };
}

function parseRetryAfter(raw) {
  if (typeof raw !== 'string' || !raw.trim()) return null;
  if (/^\d+$/.test(raw.trim())) return Math.min(86400, Number(raw.trim()));
  const timestamp = Date.parse(raw);
  if (!Number.isFinite(timestamp)) return null;
  return Math.min(86400, Math.max(0, Math.ceil((timestamp - Date.now()) / 1000)));
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

function popupUrlMetadata(page) {
  try {
    const raw = page.url();
    return {
      popup_url_kind:safeUrlKind(raw),
      popup_url_length:typeof raw === 'string' ? raw.length : -1,
    };
  } catch {
    return {popup_url_kind:'invalid',popup_url_length:0};
  }
}

async function resolveProduct(page, card, check, deadline, access, rowIndex, config) {
  let popup;
  let sameTabNavigation = false;
  let destinationPromise;
  let cancelDestinationWait;
  const roomUrl = page.url();
  let phase = 'card_link';
  const detail = {row_index:rowIndex};
  const restoreSearchBeforeDeadline = async () => {
    let remaining = deadline - Date.now();
    if (remaining <= 0) {
      sameTabNavigation = false;
      trace('room_search_restore_skipped_deadline',detail);
      return false;
    }
    if (page.url() !== roomUrl) {
      await page.goBack({waitUntil:'domcontentloaded',timeout:Math.min(5000,remaining)});
      remaining = deadline - Date.now();
      if (remaining <= 0) {
        sameTabNavigation = false;
        trace('room_search_restore_skipped_deadline',detail);
        return false;
      }
      await page.waitForURL(url => url.href === roomUrl, {
        waitUntil:'domcontentloaded', timeout:Math.min(5000,remaining),
      });
    }
    if (Date.now() >= deadline) {
      sameTabNavigation = false;
      trace('room_search_restore_skipped_deadline',detail);
      return false;
    }
    if (page.url() !== roomUrl) throw new SourceFailure('room_search_restore_failed');
    sameTabNavigation = false;
    trace('room_search_restored',detail);
    return true;
  };
  trace('room_product_begin',detail);
  try {
    phase = 'preview_click';
    trace('room_preview_click_begin',detail);
    await check();
    const previewClickRemaining = deadline-Date.now();
    if (previewClickRemaining <= 0) throw new SourceFailure('source_timeout');
    await card.locator('.imgLink').click({ timeout: Math.min(2000, previewClickRemaining) });
    trace('room_preview_click_complete',detail);
    await check();
    phase = 'detail_button_ready';
    // ROOM exposes this market-details control as visible text, but its DOM
    // class is not stable across the public UI. Anchor to the rendered label.
    const button = page.getByText('楽天市場で詳細を見る', {exact:true}).filter({visible:true}).first();
    await button.waitFor({
      state:'visible',
      timeout:Math.min(PRODUCT_BUTTON_TIMEOUT_MS,Math.max(1,deadline-Date.now())),
    });
    trace('room_detail_button_ready',detail);
    const control = await button.evaluate(el => {
      const visible = anchor => anchor.isConnected && anchor.getClientRects().length > 0;
      const descendant = Array.from(el.querySelectorAll('a[href]')).find(visible) || null;
      const anchor = el instanceof HTMLAnchorElement && el.href ? el : el.closest('a[href]') || descendant;
      return {
        tag:el.tagName.toLowerCase(),
        href:anchor?.href || null,
        target:anchor?.target || null,
        link_scope:!anchor ? 'none' : anchor === el ? 'self' : anchor.contains(el) ? 'ancestor' : 'descendant',
      };
    });
    const directProduct = control.href ? productIds(control.href) : null;
    trace('room_detail_control_inspected',{
      ...detail,
      element_tag: ['a','button','div','span'].includes(control.tag) ? control.tag : 'other',
      has_href: Boolean(control.href),
      href_kind: control.href ? safeUrlKind(control.href) : 'none',
      link_scope: ['none','self','ancestor','descendant'].includes(control.link_scope) ? control.link_scope : 'other',
      link_target: control.target === '_blank' ? '_blank' : control.target === '_self' ? '_self' : control.target ? 'other' : 'none',
    });
    if (directProduct) trace('room_product_visible_href_observed',detail);
    destinationPromise = new Promise(resolve => {
      let settled = false;
      const finish = destination => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        page.off('popup', onPopup);
        page.off('framenavigated', onFrameNavigated);
        resolve(destination);
      };
      cancelDestinationWait = () => finish(null);
      const onPopup = productPage => finish({page:productPage,kind:'popup'});
      const onFrameNavigated = frame => {
        if (frame === page.mainFrame() && page.url() !== roomUrl) {
          sameTabNavigation = true;
          finish({page,kind:'same_tab'});
        }
      };
      const timer = setTimeout(
        () => finish(null),
        Math.min(PRODUCT_DESTINATION_TIMEOUT_MS, Math.max(1, deadline-Date.now())),
      );
      page.on('popup', onPopup);
      page.on('framenavigated', onFrameNavigated);
    });
    // Only the product-details control is clicked; no like/collect/buy action.
    phase = 'detail_button_click';
    trace('room_detail_button_click_begin',detail);
    await check();
    const detailClickRemaining = deadline-Date.now();
    if (detailClickRemaining <= 0) throw new SourceFailure('source_timeout');
    await button.click({ timeout: Math.min(PRODUCT_DETAIL_CLICK_TIMEOUT_MS, detailClickRemaining) });
    phase = 'product_destination_wait';
    trace('room_product_destination_wait_begin',detail);
    const destination = await destinationPromise;
    if (!destination) {
      trace('room_product_destination_missing',detail);
      if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
      return null;
    }
    popup = destination.kind === 'popup' ? destination.page : null;
    sameTabNavigation = destination.kind === 'same_tab';
    const productPage = destination.page;
    trace(sameTabNavigation ? 'room_same_tab_navigation_observed' : 'room_popup_received',detail);
    phase = 'product_initial_url';
    const initialProduct = productIds(productPage.url());
    if (initialProduct) trace('room_product_resolved_initial_url',detail);
    const productCheck = guardPage(productPage, 'item.rakuten.co.jp', access);
    await productCheck();
    phase = 'product_navigation';
    if (!initialProduct) {
      trace('room_product_navigation_begin',detail);
      await productPage.waitForURL(url => url.hostname === 'item.rakuten.co.jp', {
        waitUntil: 'commit', timeout: Math.min(PRODUCT_NAVIGATION_TIMEOUT_MS, Math.max(1, deadline-Date.now())),
      });
      trace('room_product_navigation_complete',{...detail,...popupUrlMetadata(productPage)});
    }
    await productCheck();
    await check();
    phase = 'product_url_parse';
    trace('room_product_page_render_wait_begin',detail);
    const productDomState = await productPage.evaluate(() => ({
      body_children:document.body?.children.length ?? 0,
      body_text_nonempty:Boolean(document.body?.innerText.trim()),
    }));
    trace('room_product_dom_state',{...detail,...productDomState});
    await waitDOM(productPage, productCheck, () => Boolean(document.body?.children.length && document.body.innerText.trim()), null, deadline);
    trace('room_product_page_rendered',detail);
    const product = productIds(productPage.url());
    trace(product ? 'room_product_resolved' : 'room_product_url_unresolved',detail);
    if (
      sameTabNavigation &&
      config.environment === 'fixture' &&
      config.fixture_expire_deadline_before_room_restore
    ) deadline = Date.now() - 1;
    if (sameTabNavigation) {
      phase = 'room_search_restore';
      if (!(await restoreSearchBeforeDeadline()))
        throw new SourceFailure('source_timeout');
      await waitDOM(page,check,() => Boolean(document.querySelector('#search-box')),null,deadline);
    }
    return product;
  } catch (error) {
    if (error instanceof SourceFailure) throw error;
    if (phase === 'detail_button_click' && error?.name === 'TimeoutError' && destinationPromise) {
      const lateDestination = await destinationPromise;
      if (lateDestination?.kind === 'popup') {
        popup = lateDestination.page;
        trace('room_popup_received',detail);
      }
      if (lateDestination?.kind === 'same_tab') {
        sameTabNavigation = true;
        trace('room_same_tab_navigation_observed',detail);
      }
      throw new SourceFailure('source_timeout');
    }
    cancelDestinationWait?.();
    const diagnosticPage = popup ?? (sameTabNavigation ? page : null);
    trace(
      error?.name === 'TimeoutError' ? 'room_product_timeout' : 'room_product_unresolved',
      {
        ...detail,
        ...(diagnosticPage ? popupUrlMetadata(diagnosticPage) : {}),
        phase,
      },
    );
    if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
    await check();
    return null; // An unresolved identity still occupies its original source slot.
  } finally {
    if (popup) await popup.close().catch(() => {});
    if (sameTabNavigation && !page.isClosed()) {
      try {
        await restoreSearchBeforeDeadline();
      } catch {
        throw new SourceFailure('room_search_restore_failed');
      }
    }
    if (Date.now() < deadline) {
      const close = page.locator('.close-button').filter({ visible: true }).first();
      if (await close.count()) await close.click({ timeout: Math.min(1000,deadline-Date.now()) }).catch(() => {});
    } else {
      trace('room_modal_close_skipped_deadline',detail);
    }
  }
}

export async function collectRoom(page, request, config, deadline, access) {
  const url = new URL(config.environment === 'fixture' ? config.fixture_url : 'https://room.rakuten.co.jp/search/item');
  for (const [key, value] of Object.entries({ keyword: request.query, colle: '', comment: '', like: '300', user_id: '', user_name: '', original_photo: '0' })) url.searchParams.set(key, value);
  const check = guardPage(page, url.hostname, access);
  if (deadline - Date.now() < 1000) throw new SourceFailure('source_timeout');
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
  let stableShortBatch = false;
  let stableNoProgressRounds = 0;
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
    if (config.environment === 'fixture' && config.fixture_room_scroll_delay_ms > 0) {
      const delayMs = Math.min(config.fixture_room_scroll_delay_ms,Math.max(0,deadline-Date.now()+1));
      await new Promise(resolve => setTimeout(resolve,delayMs));
    }
    await check();
    const scrollRemaining = deadline-Date.now();
    if (scrollRemaining <= 0) {
      trace('room_scroll_deadline_expired');
      throw new SourceFailure('source_timeout');
    }
    const scrolled = await page.evaluate(cutoff => {
      if (Date.now() >= cutoff) return false;
      window.scrollTo(0, document.documentElement.scrollHeight);
      return true;
    },deadline);
    if (!scrolled) {
      trace('room_scroll_deadline_expired');
      throw new SourceFailure('source_timeout');
    }
    const before = loaded;
    const changeDeadline = Math.min(deadline, Date.now()+2000);
    try {
      await waitDOM(page, check, n => document.querySelectorAll('.items-search > .item').length > n ||
        Array.from(document.querySelectorAll('body *')).some(e => e.children.length === 0 && !e.closest('.items-search, .item-preview') && e.getClientRects().length &&
          /^(検索結果は以上です|検索結果がありません|該当する商品がありません|検索結果はありません)$/.test(e.textContent.trim())), before, changeDeadline);
    } catch (error) {
      if (!(error instanceof SourceFailure) || error.code !== 'source_timeout') throw error;
      // A positive short batch ends only after three bounded scrolls add no rows.
    }
    await check();
    loaded = await cards.count();
    exhausted = await endVisible();
    if (loaded > before) {
      stableNoProgressRounds = 0;
    } else if (exhausted) {
      break;
    } else if (loaded > 0) {
      stableNoProgressRounds += 1;
      trace('room_list_no_progress', {
        loaded_posts: loaded,
        stable_round: stableNoProgressRounds,
      });
      if (stableNoProgressRounds >= 3) {
        stableShortBatch = true;
        trace('room_short_batch_stable', {
          loaded_posts: loaded,
          stable_round: stableNoProgressRounds,
        });
        break;
      }
    }
  }
  if (loaded < 50 && !exhausted && !stableShortBatch)
    throw new SourceFailure('incomplete_search');
  trace('room_resolve_products',{loaded_posts:loaded,accepted_posts:Math.min(50,loaded)});
  const fetched = timestamp();
  const posts = [];
  const facts = {};
  let productResolutionFailures = 0;
  for (let index = 0; index < Math.min(50, loaded); index++) {
    if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
    const card = cards.nth(index);
    const name = await card.locator('.imgLink img').first().getAttribute('alt').catch(() => null);
    const image = await card.locator('.imgLink img').first().getAttribute('src').catch(() => null);
    const ids = await resolveProduct(page, card, check, deadline, access, index+1, config);
    if (!ids) productResolutionFailures += 1;
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
  if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
  const tooManyProductResolutionFailures =
    productResolutionFailures > PRODUCT_RESOLUTION_FAILURE_LIMIT;
  return { response: { ...Object.fromEntries(['attempt_id','run_id','query','parent_refs','level','likes_min','max_posts','query_kind','keyword_snapshot_ref','selected_word_ref'].map(k => [k,request[k] ?? null])),
    current_search_id: request.attempt_id, search_loaded: true, internal_order_confirmed: true,
    status: tooManyProductResolutionFailures ? 'partial' : 'observed', fetched_at: fetched,
    end_reached: exhausted || stableShortBatch,
    error_code: tooManyProductResolutionFailures ? 'product_resolution_failures_exceeded' : null,
    posts, loaded_posts: loaded, condition_evidence_ref: `${request.attempt_id}:condition` },
    product_facts: facts, diagnostics: { source: 'public_dom', loaded_posts: loaded, accepted_posts: posts.length,
      product_resolution_failures: productResolutionFailures,
      unused_posts: Math.max(0,loaded-posts.length), condition: { query: request.query, like: 300, comment: null,
        like_display:likeDisplay.trim(),comment_display:commentDisplay.trim() },
      end_evidence: exhausted ? 'explicit_public_end_text' : stableShortBatch ? 'stable_no_progress' : 'first_50' } };
}

async function emptyEvidence(widget) {
  const matches = await widget.getByText(/^(データがありません|この検索キーワードのデータはありません)$/).all();
  for (const match of matches) {
    if (await match.isVisible() && await match.evaluate(e=>!e.closest('a.progress-label'))) return match;
  }
  return null;
}

function deadlineTimeout(deadline) {
  const remaining = deadline - Date.now();
  if (remaining < 1) throw new SourceFailure('source_timeout');
  return Math.floor(remaining);
}

function ensureBeforeDeadline(deadline) {
  deadlineTimeout(deadline);
}

async function clickBeforeDeadline(locator, deadline, options = {}) {
  await locator.click({ ...options, timeout: deadlineTimeout(deadline) });
  ensureBeforeDeadline(deadline);
}

export async function collectTrends(page, request, config, deadline, access) {
  const homeUrl = config.environment === 'fixture'
    ? new URL('/trends/', config.fixture_url)
    : new URL('https://trends.google.co.jp/trends/');
  const check = guardPage(page,homeUrl.hostname,access);
  if (deadline - Date.now() < 1000) throw new SourceFailure('source_timeout');
  trace('trends_home_navigation');
  // The home page may cancel ancillary fetches during its initial render.
  // Wait for the navigation commit here, then wait for the rendered search
  // control below instead of coupling startup to DOMContentLoaded.
  await page.goto(homeUrl.href, { waitUntil:'commit' });
  trace('trends_home_commit_observed');
  await check();
  const consent = page.getByRole('button', { name: 'OK, got it', exact: true });
  if (await consent.isVisible()) {
    await clickBeforeDeadline(consent, deadline);
  }
  const homeSearchMatches = page.getByLabel('検索',{exact:true}).filter({visible:true});
  trace('trends_home_search_wait_begin');
  await homeSearchMatches.first().waitFor({
    state:'visible',
    timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
  });
  let homeSearchDescriptors = [];
  const controlDeadline = Math.min(deadline,Date.now()+20000);
  while (Date.now() < controlDeadline) {
    homeSearchDescriptors = await homeSearchMatches.evaluateAll(elements => elements.map((element,index) => ({
      index,
      isInput:element.tagName === 'INPUT' && ['text','search'].includes(element.type),
    })));
    if (homeSearchDescriptors.some(element => element.isInput)) break;
    await page.waitForTimeout(Math.min(100,Math.max(1,controlDeadline-Date.now())));
  }
  const homeSearchIndices = homeSearchDescriptors.filter(element => element.isInput).map(element => element.index);
  const homeSearchCount = homeSearchIndices.length;
  const visibleInputCount = await page.locator('input:visible').count();
  const labelLocatorMatchCount = homeSearchDescriptors.length;
  const documentReadyState = await page.evaluate(() => document.readyState);
  trace('trends_home_search_candidates',{
    search_candidate_count:homeSearchCount,
    visible_input_count:visibleInputCount,
    label_locator_match_count:labelLocatorMatchCount,
    document_ready_state:documentReadyState,
  });
  if (homeSearchIndices.length !== 1) throw new SourceFailure('home_search_ambiguous');
  const homeSearch = homeSearchMatches.nth(homeSearchIndices[0]);
  trace('trends_home_search_visible');
  await homeSearch.fill(request.parent_word, { timeout: deadlineTimeout(deadline) });
  trace('trends_home_query_entered');
  const exploreButtons = page.getByRole('button',{name:'調べる',exact:true}).filter({visible:true});
  const exploreButtonCount = await exploreButtons.count();
  const searchButtonCount = await page.getByRole('button',{name:'検索',exact:true}).filter({visible:true}).count();
  trace('trends_home_submit_control',{
    explore_button_count:exploreButtonCount,
    search_button_count:searchButtonCount,
  });
  if (exploreButtonCount === 1) await clickBeforeDeadline(exploreButtons.first(),deadline);
  else if (exploreButtonCount === 0) await homeSearch.press('Enter', { timeout: deadlineTimeout(deadline) });
  else throw new SourceFailure('home_search_submit_ambiguous');
  ensureBeforeDeadline(deadline);
  trace('trends_home_submit_sent');
  await page.waitForURL(url => /\/trends\/explore\/?$/.test(url.pathname),
    {waitUntil:'domcontentloaded',timeout:Math.min(30000,Math.max(1,deadline-Date.now()))});
  await check();
  await waitDOM(page,check,() => !!document.querySelector('input[aria-label="検索キーワードを追加"]'),null,deadline);
  const queryInput = page.getByLabel('検索キーワードを追加',{exact:true});
  if (await queryInput.inputValue() !== request.parent_word ||
      new URL(page.url()).searchParams.get('geo') !== 'JP')
    throw new SourceFailure('query_not_reflected');
  const periodControl = page.locator('[aria-label^="Select time period"]').first();
  trace('trends_period_control_wait_begin');
  await periodControl.waitFor({state:'visible',timeout:Math.max(1,deadline-Date.now())});
  trace('trends_period_control_visible');
  let period = await periodControl.innerText();
  if (!allowedPeriods.has(compact(period))) {
    trace('trends_period_menu_open_begin');
    await clickBeforeDeadline(periodControl, deadline);
    trace('trends_period_menu_opened');
    const periodOptions = page.getByRole('option',{name:/過去\s*(?:3\s*[かヶ]月|90\s*日)/}).filter({visible:true});
    trace('trends_period_option_wait_begin');
    await periodOptions.first().waitFor({
      state:'visible',
      timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
    });
    const periodOptionCount = await periodOptions.count();
    trace('trends_period_option_visible',{period_option_count:periodOptionCount});
    if (periodOptionCount !== 1) throw new SourceFailure('period_option_ambiguous');
    const periodOption = periodOptions.first();
    await clickBeforeDeadline(periodOption, deadline);
    trace('trends_period_option_clicked');
    trace('trends_period_reflection_wait_begin');
    const periodDeadline = Math.min(deadline,Date.now()+30000);
    await waitDOM(page,check,() => {
      const control = document.querySelector('[aria-label^="Select time period"]');
      const value = control?.innerText.normalize('NFKC').replace(/\s/g,'') ?? '';
      const date = new URL(location.href).searchParams.get('date');
      return ['過去90日間','過去90日','過去3か月間','過去3か月','過去3ヶ月間','過去3ヶ月'].includes(value) &&
        ['today 3-m','today 90-d'].includes(date);
    },null,periodDeadline);
    trace('trends_period_reflected');
    period = await periodControl.innerText();
  }
  const exploreUrl = new URL(page.url());
  if (!allowedPeriods.has(compact(period)) || !['today 3-m','today 90-d'].includes(exploreUrl.searchParams.get('date')) ||
      exploreUrl.searchParams.get('geo') !== 'JP' ||
      !(await page.locator('[aria-label^="Select stories location"]').innerText()).includes('ウェブ検索') ||
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
    await clickBeforeDeadline(dropdown, deadline);
    await clickBeforeDeadline(
      page.getByRole('option',{name:'人気',exact:true}).filter({ visible:true }),
      deadline,
    );
    // A changed dropdown alone is insufficient: require changed row data and
    // visible row data and rank markers. Identical, unprovable data fails
    // closed rather than treating Rising as Top. Avoid `networkidle` here:
    // unrelated Explore traffic can stay open after the requested rows render.
    if (prior.length) {
      trace('trends_top_rows_wait_begin');
      await waitDOM(page,check, previous => {
        const widget = Array.from(document.querySelectorAll('.fe-related-queries')).find(e=>e.innerText.startsWith('関連キーワード'));
        const current = Array.from(widget?.querySelectorAll('a.progress-label') ?? []).map(e=>e.textContent);
        return JSON.stringify(current) !== JSON.stringify(previous) && current.length > 0;
      },prior,deadline);
      trace('trends_top_rows_updated');
    }
    else if (priorEmptyHandle) {
      // Rising's zero marker cannot establish Top zero. Require its removal or
      // hiding for the new view; if the UI exposes no transition, fail closed.
      trace('trends_top_empty_wait_begin');
      await page.waitForFunction(e=>!e.isConnected || e.getClientRects().length===0,
        priorEmptyHandle,{timeout:Math.max(1,deadline-Date.now())});
      trace('trends_top_empty_refreshed');
    }
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
    await clickBeforeDeadline(next, deadline);
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
  const configuredDeadline = Date.now()+config.timeout_seconds*1000;
  const requestDeadline = request.deadline_at == null ? null : Date.parse(request.deadline_at);
  if (request.deadline_at != null && !Number.isFinite(requestDeadline))
    throw new SourceFailure('invalid_request');
  const deadline = requestDeadline == null ? configuredDeadline : Math.min(configuredDeadline,requestDeadline);
  if (deadline - Date.now() < 1000) {
    trace('deadline_reached_before_browser_launch');
    throw new SourceFailure('source_timeout');
  }
  let browser = null;
  let context = null;
  const persistent = typeof config.user_data_dir === 'string' && config.user_data_dir.length > 0;
  trace(persistent ? 'browser_persistent_launch_begin' : 'browser_launch_begin');
  if (persistent) {
    context = await chromium.launchPersistentContext(config.user_data_dir, {
      executablePath:config.chrome_executable,
      headless:config.headless,
      locale:'ja-JP',
    });
    browser = context.browser();
    trace('browser_persistent_launch_complete');
  } else {
    browser = await chromium.launch({executablePath:config.chrome_executable,headless:config.headless});
    trace('browser_launch_complete');
  }
  const access = {code:null,retryAfterSeconds:null};
  try {
    if (config.environment === 'fixture' && config.fixture_browser_startup_delay_ms > 0)
      await new Promise(resolve => setTimeout(resolve,config.fixture_browser_startup_delay_ms));
    if (deadline - Date.now() < 1000)
      throw new SourceFailure('source_timeout');
    if (!context) {
      trace('browser_context_begin');
      context = await browser.newContext({locale:'ja-JP'});
      trace('browser_context_complete');
    }
    installBrowserDiagnostics(context);
    // Installed before newPage/popup: even the first popup document and its
    // redirects are monitored. No response body or internal API is queried.
    context.on('response', response => {
      const type = response.request().resourceType();
      const host = new URL(response.url()).hostname;
      const sourceHost = host === 'room.rakuten.co.jp' || /(^|\.)trends\.google\.(co\.jp|com)$/.test(host);
      if ([401,403,429].includes(response.status()) && (type === 'document' || sourceHost && ['xhr','fetch'].includes(type))) {
        access.code = `http_${response.status()}`;
        if (response.status() === 429) {
          const headers = response.headers();
          const retryAfterHeader = Object.entries(headers).find(
            ([name]) => name.toLowerCase() === 'retry-after'
          )?.[1];
          const retryAfter = parseRetryAfter(retryAfterHeader);
          trace('source_retry_after_observed',{
            retry_after_present:typeof retryAfterHeader === 'string',
            retry_after_seconds:retryAfter,
          });
          if (retryAfter !== null)
            access.retryAfterSeconds = Math.max(access.retryAfterSeconds ?? 0, retryAfter);
        }
      }
    });
    if (config.environment === 'fixture') await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin === new URL(config.fixture_url).origin) return route.continue();
      if (url.hostname === 'item.rakuten.co.jp') {
        if (config.fixture_product_delay_ms > 0)
          await new Promise(resolve => setTimeout(resolve, config.fixture_product_delay_ms));
        return route.fulfill({
          status:200,
          contentType:'text/html; charset=utf-8',
          body:'<!doctype html><html><body><main>fixture product page rendered</main></body></html>',
        });
      }
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
    if (access.code) throw new SourceFailure(access.code, access.retryAfterSeconds);
    throw error;
  } finally {
    flushBrowserDiagnostics();
    try {
      if (persistent) await context.close();
      else await browser.close();
    }
    catch (error) {
      if (access.code) throw new SourceFailure(access.code, access.retryAfterSeconds);
      throw error;
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk;
    if (raw.length > 1000000) throw new SourceFailure('request_too_large');
  }
  try { process.stdout.write(JSON.stringify(await main(JSON.parse(raw)))+'\n'); }
  catch (error) {
    const errorCode = error instanceof SourceFailure ? error.code : error?.name === 'TimeoutError' ? 'source_timeout' : 'browser_adapter_error';
    const failureEvent = {
      event:'public_source_failure',
      stage:typeof activeTrace.stage === 'string' && REF.test(activeTrace.stage) ? activeTrace.stage : 'unknown',
      error_code:errorCode,
      error_class:error instanceof SourceFailure ? 'SourceFailure' : safeThrownErrorClass(error),
      error_kind:safeErrorKind(error),
    };
    if (Number.isInteger(activeTrace.row_index) && activeTrace.row_index >= 1 && activeTrace.row_index <= 50)
      failureEvent.row_index = activeTrace.row_index;
    const networkCode = safeNetworkCode(error);
    if (networkCode) failureEvent.network_code = networkCode;
    process.stderr.write(JSON.stringify(failureEvent)+'\n');
    const result = {error_code:errorCode};
    if (error instanceof SourceFailure && Number.isInteger(error.retryAfterSeconds))
      result.retry_after_seconds = Math.max(0, Math.min(86400, error.retryAfterSeconds));
    process.stdout.write(JSON.stringify(result)+'\n');
    process.exitCode=1;
  }
}
