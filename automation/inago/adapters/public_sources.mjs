/** Public UI only. No internal API, hidden state, credentials, CDP or stealth. */
import { chromium } from 'playwright-core';
import { pathToFileURL } from 'node:url';
import { saveProductPageInputs } from './product_page_inputs.mjs';

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
const MAX_STAGE_TRACE_EVENTS = 2047;
// A slow Rakuten marketplace redirect can take longer than the detail-click
// itself. Keep it bounded, but do not discard a route that eventually commits.
const PRODUCT_NAVIGATION_TIMEOUT_MS = 60000;
const PRODUCT_DESTINATION_TIMEOUT_MS = 30000;
const PRODUCT_DETAIL_CLICK_TIMEOUT_MS = 15000;
const PRODUCT_BUTTON_TIMEOUT_MS = 120000;
// Applying ROOM filters can take longer than opening the condition sheet.
const ROOM_CONDITION_SEARCH_CLICK_TIMEOUT_MS = 20000;
const PRODUCT_RESOLUTION_FAILURE_LIMIT = 10;
const TRENDS_WIDGET_SELECTOR_VERSION = 'related-widget-v5';
let activeTrace = {stage:'startup',row_index:null};
let traceEventCount = 0;
let traceSuppressionWritten = false;
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
  const safeStage = typeof stage === 'string' && REF.test(stage) ? stage : 'unknown';
  activeTrace = {
    stage:safeStage,
    row_index:Number.isInteger(details.row_index) && details.row_index >= 1 && details.row_index <= 50
      ? details.row_index
      : activeTrace.row_index,
  };
  if (traceEventCount >= MAX_STAGE_TRACE_EVENTS) {
    if (!traceSuppressionWritten) {
      process.stderr.write(JSON.stringify({
        event:'public_source_trace_suppressed',
        stage:'trace_event_limit',
        suppression_marker_count:1,
      })+'\n');
      traceSuppressionWritten = true;
    }
    return;
  }
  const safe = {
    event:'public_source_stage',
    stage:safeStage,
    elapsed_ms:Math.max(0,Date.now()-processStartedAt),
  };
  const integerFields = [
    'loaded_posts','accepted_posts','row_index','body_children','stable_round',
    'row_failure_count','identified_row_count','product_page_image_count',
    'search_candidate_count','visible_input_count','label_locator_match_count',
    'search_button_count','explore_button_count','period_option_count',
    'top_table_count','top_rows_count',
    'retry_after_seconds','popup_url_length','widget_dom_count',
    'widget_visible_count','widget_match_count','widget_match_visible_count',
    'widget_wrong_visible_count','related_widget_generation',
    'generation_before_period','room_condition_close_control_count',
    'room_condition_search_control_count',
    'timeout_ms','wait_elapsed_ms',
  ];
  for (const key of integerFields) {
    const value = details[key];
    const maximum = key === 'stable_round' ? 3 : 50;
    if (Number.isInteger(value) && value >= 0 &&
        (key !== 'row_index' || value <= 50) &&
        (!['stable_round','row_failure_count','identified_row_count'].includes(key) || value <= maximum) &&
        (key !== 'product_page_image_count' || value <= 10000))
      safe[key] = value;
  }
  for (const key of [
    'body_text_nonempty','retry_after_present','has_href',
    'room_conditions_likes_verified','room_conditions_comments_verified',
    'element_visible','element_enabled','element_obscured',
  ]) {
    if (typeof details[key] === 'boolean') safe[key] = details[key];
  }
  if (details.widget_period_refresh_required === true || details.widget_period_refresh_required === false)
    safe.widget_period_refresh_required = details.widget_period_refresh_required;
  if (details.widget_selector_version === TRENDS_WIDGET_SELECTOR_VERSION)
    safe.widget_selector_version = TRENDS_WIDGET_SELECTOR_VERSION;
  const enums = {
    scope:['row','query','run'],
    row_status:['identified','unresolved'],
    page_capture_state:['captured','existing_preserved','capture_failed'],
    document_ready_state:['loading','interactive','complete'],
    phase:['card_link','preview_click','detail_button_ready','detail_button_click','product_destination_wait','popup_wait','popup_initial_url','product_navigation','product_url_parse','room_search_restore'],
    popup_url_kind:['blank','empty','rakuten_item','rakuten_other','other_https','other_scheme','invalid'],
    element_tag:['a','button','div','span','other'],
    element_role:['link','button','other'],
    handler_kind:['link','button','role','onclick','none'],
    handler_tag:['a','button','div','span','other'],
    handler_scope:['self','ancestor','none'],
    destination_kind:['popup','same_tab'],
    link_scope:['none','self','ancestor','descendant','other'],
    href_kind:['none','empty','blank','rakuten_item','rakuten_other','other_https','other_scheme','invalid'],
    link_target:['none','_blank','_self','other'],
    room_condition_close_method:['close_control','search_submit','escape','already_closed','none'],
    room_condition_popup_state:['visible','hidden','unknown'],
    error_class:['Error','TimeoutError','TypeError','ReferenceError','SyntaxError','RangeError','URIError','EvalError','AggregateError','TargetClosedError','BrowserClosedError','ProtocolError','SourceFailure','other'],
    widget_scope:['explore_related_queries'],
    widget_kind:['related_keywords','related_topics','unknown','multiple'],
    widget_empty_evidence:['none','explicit_no_data'],
    widget_outcome:['ready','deadline','ambiguous','contract_mismatch'],
    end_evidence:['explicit_no_results','explicit_end','stable_no_progress','first_50','row_limit','no_marker','unrecognized_empty'],
  };
  for (const [key, allowed] of Object.entries(enums)) {
    if (allowed.includes(details[key])) safe[key] = details[key];
  }
  if (typeof details.failed_operation === 'string' && REF.test(details.failed_operation))
    safe.failed_operation = details.failed_operation;
  if (typeof details.failure_module === 'string' &&
      /^automation\.inago\.adapters\.[A-Za-z0-9_]{1,64}$/.test(details.failure_module))
    safe.failure_module = details.failure_module;
  if (typeof details.failure_function === 'string' &&
      /^[_A-Za-z][_A-Za-z0-9.]{0,63}$/.test(details.failure_function))
    safe.failure_function = details.failure_function;
  if (Number.isInteger(details.failure_line) && details.failure_line >= 1 &&
      details.failure_line <= 2**31 - 1)
    safe.failure_line = details.failure_line;
  process.stderr.write(JSON.stringify(safe)+'\n');
  traceEventCount++;
}

function safeThrownErrorClass(error) {
  const allowed = new Set([
    'Error','TimeoutError','TypeError','ReferenceError','SyntaxError','RangeError',
    'URIError','EvalError','AggregateError','TargetClosedError','BrowserClosedError',
    'ProtocolError','SourceFailure',
  ]);
  return allowed.has(error?.name) ? error.name : 'other';
}

function safeThrownErrorLocation(error) {
  if (typeof error?.stack !== 'string') return {};
  for (const line of error.stack.split('\n').slice(0, 32)) {
    if (line.length > 2048) continue;
    const match = /(?:^|[\\/])automation[\\/]inago[\\/]adapters[\\/]([A-Za-z0-9_-]{1,64})\.mjs:(\d+):\d+/.exec(line);
    if (!match) continue;
    const failureLine = Number(match[2]);
    if (!Number.isSafeInteger(failureLine) || failureLine < 1 || failureLine > 2**31 - 1)
      continue;
    const functionMatch = /^\s*at\s+(?:async\s+)?([_A-Za-z][_A-Za-z0-9.]{0,63})\s+\(/.exec(line);
    return {
      failure_module: `automation.inago.adapters.${match[1].replace(/-/g, '_')}`,
      failure_function: functionMatch?.[1] ?? 'unknown',
      failure_line: failureLine,
    };
  }
  return {};
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
        pageRole === 'main' && ['trends_home_navigation','trends_home_commit_observed',
          'trends_explore_navigation','trends_explore_commit_observed'].includes(activeTrace.stage);
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
        pageRole === 'main' && ['trends_home_navigation','trends_home_commit_observed',
          'trends_explore_navigation','trends_explore_commit_observed'].includes(activeTrace.stage);
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
        pageRole === 'main' && ['trends_home_navigation','trends_home_commit_observed',
          'trends_explore_navigation','trends_explore_commit_observed'].includes(activeTrace.stage);
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
  if (!['room', 'trends', 'product_page'].includes(kind) || !request || !config ||
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
      (config.fixture_trends_new_explore !== undefined &&
        (typeof config.fixture_trends_new_explore !== 'boolean' ||
         (config.environment !== 'fixture' && config.fixture_trends_new_explore))) ||
      (config.fixture_expire_deadline_before_room_restore !== undefined &&
        (typeof config.fixture_expire_deadline_before_room_restore !== 'boolean' ||
         (config.environment !== 'fixture' && config.fixture_expire_deadline_before_room_restore))) ||
      ['fixture_detail_click_timeout_ms','fixture_product_destination_timeout_ms','fixture_request_deadline_ms'].some(key =>
        config[key] !== undefined &&
        (!Number.isInteger(config[key]) || config[key] < 0 || config[key] > 60000 ||
         (config.environment !== 'fixture' && config[key] !== 0))) ||
      (!(kind === 'room' && config.timeout_seconds === null) &&
        (!Number.isFinite(config.timeout_seconds) || config.timeout_seconds < 1 || config.timeout_seconds > MAX_SOURCE_TIMEOUT_SECONDS)) ||
      !REF.test(request.run_id)) throw new SourceFailure('invalid_request');
  if (config.environment === 'fixture') {
    const url = new URL(config.fixture_url);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
        url.username || url.password) throw new SourceFailure('invalid_fixture_endpoint');
  } else if (config.fixture_url != null) throw new SourceFailure('invalid_fixture_endpoint');
  if (kind === 'room') {
    if (!REF.test(request.attempt_id) || typeof request.query !== 'string' || !request.query.trim() || request.likes_min !== 300 ||
        !Number.isInteger(request.max_posts) || request.max_posts < 1 || request.max_posts > 50 ||
        !['related', 'theme_fallback'].includes(request.level) || request.query_kind !== request.level)
      throw new SourceFailure('invalid_request');
  } else if (kind === 'product_page') {
    const product = productIds(request.item_url);
    if (!REF.test(request.request_id) || !REF.test(request.attempt_id) ||
        !REF.test(request.shop_id) || !REF.test(request.item_id) ||
        !Number.isInteger(request.row_index) || request.row_index < 1 || request.row_index > 50 ||
        !product || product.shop !== request.shop_id || product.item !== request.item_id ||
        typeof config.product_page_root !== 'string' || !config.product_page_root)
      throw new SourceFailure('invalid_request');
  } else if (!REF.test(request.request_id) || !REF.test(request.settings_version) ||
             typeof (request.parent_word ?? request.query) !== 'string' ||
             !(request.parent_word ?? request.query).trim() ||
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

async function resolveProduct(page, card, check, deadline, access, rowIndex, config, request) {
  let popup;
  let sameTabNavigation = false;
  let destinationMonitor;
  let stageErrorRecorded = false;
  let clickStartedAt = null;
  let clickTimeoutMs = null;
  let destinationWaitStartedAt = null;
  let destinationWaitTimeoutMs = null;
  let control = null;
  const roomUrl = page.url();
  let phase = 'card_link';
  const detail = {row_index:rowIndex};
  const restoreSearchBeforeDeadline = async () => {
    const restoreStartedAt = Date.now();
    trace('room_search_restore_begin',{...detail,phase:'room_search_restore'});
    let remaining = deadline - Date.now();
    if (remaining <= 0) {
      sameTabNavigation = false;
      trace('room_search_restore_skipped_deadline',{
        ...detail,phase:'room_search_restore',scope:'query',
      });
      return false;
    }
    try {
      if (page.url() !== roomUrl) {
        await page.goBack({waitUntil:'domcontentloaded',timeout:Math.min(5000,remaining)});
        remaining = deadline - Date.now();
        if (remaining <= 0) {
          sameTabNavigation = false;
          trace('room_search_restore_skipped_deadline',{
            ...detail,phase:'room_search_restore',scope:'query',
          });
          return false;
        }
        await page.waitForURL(url => url.href === roomUrl, {
          waitUntil:'domcontentloaded', timeout:Math.min(5000,remaining),
        });
      }
      if (Date.now() >= deadline) {
        sameTabNavigation = false;
        trace('room_search_restore_skipped_deadline',{
          ...detail,phase:'room_search_restore',scope:'query',
        });
        return false;
      }
      if (page.url() !== roomUrl) throw new SourceFailure('room_search_restore_failed');
      sameTabNavigation = false;
      trace('room_search_restored',{
        ...detail,
        phase:'room_search_restore',
        wait_elapsed_ms:Math.max(0,Date.now()-restoreStartedAt),
      });
      return true;
    } catch (error) {
      sameTabNavigation = false;
      trace('room_search_restore_error',{
        ...detail,
        phase:'room_search_restore',
        scope:'query',
        error_class:safeThrownErrorClass(error),
        failed_operation:'room_search_restore_begin',
        wait_elapsed_ms:Math.max(0,Date.now()-restoreStartedAt),
      });
      throw new SourceFailure('room_search_restore_failed');
    }
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
    control = await button.evaluate(el => {
      const visible = anchor => anchor.isConnected && anchor.getClientRects().length > 0;
      const descendant = Array.from(el.querySelectorAll('a[href]')).find(visible) || null;
      const anchor = el instanceof HTMLAnchorElement && el.href ? el : el.closest('a[href]') || descendant;
      const tag = el.tagName.toLowerCase();
      const roleAttribute = el.getAttribute('role');
      const role = roleAttribute === 'link' || tag === 'a' ? 'link' :
        roleAttribute === 'button' || tag === 'button' ? 'button' : 'other';
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const elementVisible = el.isConnected && el.getClientRects().length > 0 &&
        style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0;
      const disabledAncestor = el.closest('[disabled],[aria-disabled="true"]');
      const elementEnabled = !disabledAncestor;
      const hit = rect.width > 0 && rect.height > 0
        ? document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
        : null;
      const elementObscured = Boolean(elementVisible && hit &&
        hit !== el && !el.contains(hit) && !hit.contains(el));
      const ancestors = [];
      for (let node = el; node && node instanceof HTMLElement; node = node.parentElement) {
        ancestors.push(node);
        if (ancestors.length >= 8) break;
      }
      const handler = ancestors.find(node => {
        const nodeTag = node.tagName.toLowerCase();
        return (nodeTag === 'a' && Boolean(node.getAttribute('href'))) ||
          nodeTag === 'button' || node.getAttribute('role') === 'button' ||
          node.hasAttribute('onclick') || typeof node.onclick === 'function';
      }) || null;
      const handlerTag = handler ? handler.tagName.toLowerCase() : 'other';
      const handlerRole = handler?.getAttribute('role');
      const handlerKind = !handler ? 'none' :
        handlerTag === 'a' ? 'link' : handlerTag === 'button' ? 'button' :
        handlerRole === 'button' ? 'role' : 'onclick';
      return {
        tag,
        role,
        visible:elementVisible,
        enabled:elementEnabled,
        obscured:elementObscured,
        handler_kind:handlerKind,
        handler_tag:['a','button','div','span'].includes(handlerTag) ? handlerTag : 'other',
        handler_scope:!handler ? 'none' : handler === el ? 'self' : 'ancestor',
        href:anchor?.href || null,
        target:anchor?.target || null,
        link_scope:!anchor ? 'none' : anchor === el ? 'self' : anchor.contains(el) ? 'ancestor' : 'descendant',
      };
    });
    const directProduct = control.href ? productIds(control.href) : null;
    trace('room_detail_control_inspected',{
      ...detail,
      element_tag:['a','button','div','span'].includes(control.tag) ? control.tag : 'other',
      element_role:control.role,
      element_visible:control.visible,
      element_enabled:control.enabled,
      element_obscured:control.obscured,
      handler_kind:control.handler_kind,
      handler_tag:control.handler_tag,
      handler_scope:control.handler_scope,
      has_href:Boolean(control.href),
      href_kind:control.href ? safeUrlKind(control.href) : 'none',
      link_scope:['none','self','ancestor','descendant'].includes(control.link_scope) ? control.link_scope : 'other',
      link_target:control.target === '_blank' ? '_blank' : control.target === '_self' ? '_self' : control.target ? 'other' : 'none',
    });
    if (directProduct) trace('room_product_visible_href_observed',detail);

    const detailClickTimeoutMs = config.fixture_detail_click_timeout_ms || PRODUCT_DETAIL_CLICK_TIMEOUT_MS;
    const destinationTimeoutMs = config.fixture_product_destination_timeout_ms || PRODUCT_DESTINATION_TIMEOUT_MS;
    let observedDestination = null;
    let destinationWaitTimer = null;
    let destinationWaitResolve = null;
    let destinationWaitStopped = false;
    const settleDestination = destination => {
      if (destinationWaitStopped || observedDestination) return;
      observedDestination = destination;
      if (destinationWaitTimer) {
        clearTimeout(destinationWaitTimer);
        destinationWaitTimer = null;
      }
      if (destinationWaitResolve) {
        const resolve = destinationWaitResolve;
        destinationWaitResolve = null;
        resolve(destination);
      }
    };
    const onPopup = productPage => settleDestination({page:productPage,kind:'popup'});
    const onFrameNavigated = frame => {
      if (frame === page.mainFrame() && page.url() !== roomUrl) {
        sameTabNavigation = true;
        settleDestination({page,kind:'same_tab'});
      }
    };
    page.on('popup', onPopup);
    page.on('framenavigated', onFrameNavigated);
    destinationMonitor = {
      getDestination:() => observedDestination,
      wait:timeoutMs => {
        if (observedDestination) return Promise.resolve(observedDestination);
        if (destinationWaitStopped) return Promise.resolve(null);
        return new Promise(resolve => {
          destinationWaitResolve = resolve;
          destinationWaitTimer = setTimeout(() => {
            destinationWaitTimer = null;
            destinationWaitResolve = null;
            resolve(null);
          }, Math.max(1,timeoutMs));
        });
      },
      cancel:() => {
        if (destinationWaitStopped) return;
        destinationWaitStopped = true;
        if (destinationWaitTimer) {
          clearTimeout(destinationWaitTimer);
          destinationWaitTimer = null;
        }
        page.off('popup', onPopup);
        page.off('framenavigated', onFrameNavigated);
        if (destinationWaitResolve) {
          const resolve = destinationWaitResolve;
          destinationWaitResolve = null;
          resolve(null);
        }
      },
    };

    // Only the product-details control is clicked; no like/collect/buy action.
    phase = 'detail_button_click';
    clickStartedAt = Date.now();
    trace('room_detail_button_click_begin',{...detail,phase:'detail_button_click'});
    await check();
    const detailClickRemaining = deadline-Date.now();
    if (detailClickRemaining <= 0) throw new SourceFailure('source_timeout');
    clickTimeoutMs = Math.min(detailClickTimeoutMs,detailClickRemaining);
    try {
      // Keep the ordinary Playwright click so its real actionability checks apply.
      await button.click({timeout:clickTimeoutMs});
    } catch (error) {
      const clickElapsedMs = Math.max(0,Date.now()-clickStartedAt);
      const destinationAtClickFailure = destinationMonitor.getDestination();
      destinationMonitor.cancel();
      trace('room_detail_button_click_error',{
        ...detail,
        phase:'detail_button_click',
        scope:error instanceof SourceFailure || Date.now() >= deadline ? 'query' : 'row',
        error_class:safeThrownErrorClass(error),
        failed_operation:'room_detail_button_click_begin',
        timeout_ms:clickTimeoutMs,
        wait_elapsed_ms:clickElapsedMs,
        element_obscured:control.obscured,
      });
      stageErrorRecorded = true;
      if (destinationAtClickFailure?.kind === 'popup') {
        popup = destinationAtClickFailure.page;
        trace('room_product_destination_discarded',{
          ...detail,
          phase:'detail_button_click',
          destination_kind:'popup',
          failed_operation:'room_detail_button_click_begin',
          error_class:safeThrownErrorClass(error),
        });
      }
      if (destinationAtClickFailure?.kind === 'same_tab') {
        sameTabNavigation = true;
        trace('room_same_tab_navigation_observed',detail);
      }
      if (error instanceof SourceFailure) throw error;
      if (error?.name === 'TimeoutError')
        throw new SourceFailure(Date.now() >= deadline ? 'source_timeout' : 'product_row_timeout');
      throw error;
    }
    trace('room_detail_button_click_complete',{
      ...detail,
      phase:'detail_button_click',
      timeout_ms:clickTimeoutMs,
      wait_elapsed_ms:Math.max(0,Date.now()-clickStartedAt),
    });

    phase = 'product_destination_wait';
    destinationWaitStartedAt = Date.now();
    const destinationWaitRemaining = deadline-Date.now();
    destinationWaitTimeoutMs = Math.max(0,Math.min(destinationTimeoutMs,destinationWaitRemaining));
    trace('room_product_destination_wait_begin',{
      ...detail,
      phase:'product_destination_wait',
      timeout_ms:destinationWaitTimeoutMs,
    });
    if (destinationWaitTimeoutMs <= 0) throw new SourceFailure('source_timeout');
    const destination = await destinationMonitor.wait(destinationWaitTimeoutMs);
    if (!destination) {
      destinationMonitor.cancel();
      trace('room_product_destination_wait_error',{
        ...detail,
        phase:'product_destination_wait',
        scope:Date.now() >= deadline ? 'query' : 'row',
        error_class:'TimeoutError',
        failed_operation:'room_product_destination_wait_begin',
        timeout_ms:destinationWaitTimeoutMs,
        wait_elapsed_ms:Math.max(0,Date.now()-destinationWaitStartedAt),
      });
      stageErrorRecorded = true;
      if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
      throw new SourceFailure('product_row_timeout');
    }
    destinationMonitor.cancel();
    trace('room_product_destination_wait_complete',{
      ...detail,
      phase:'product_destination_wait',
      destination_kind:destination.kind,
      timeout_ms:destinationWaitTimeoutMs,
      wait_elapsed_ms:Math.max(0,Date.now()-destinationWaitStartedAt),
    });
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
        waitUntil:'commit', timeout:Math.min(PRODUCT_NAVIGATION_TIMEOUT_MS,Math.max(1,deadline-Date.now())),
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
    await waitDOM(productPage,productCheck,() => Boolean(document.body?.children.length && document.body.innerText.trim()),null,deadline);
    trace('room_product_page_rendered',detail);
    const product = productIds(productPage.url());
    trace(product ? 'room_product_resolved' : 'room_product_url_unresolved',{
      ...detail,
      scope:'row',
      row_status:product ? 'identified' : 'unresolved',
    });
    if (product && config.product_page_root) {
      try {
        product.page_capture = await saveProductPageInputs({
          page: productPage, product, request, rowIndex, config,
        });
        trace('room_product_inputs_saved', {
          ...detail,
          page_capture_state: product.page_capture.state,
          product_page_image_count: product.page_capture.image_count,
        });
      } catch (error) {
        product.page_capture = { page_ref: null, state: 'capture_failed', image_count: 0 };
        trace('room_product_inputs_capture_error', {
          ...detail,
          error_class: safeThrownErrorClass(error),
          failed_operation: 'product_page_inputs_save',
          ...safeThrownErrorLocation(error),
        });
      }
    }
    if (
      sameTabNavigation &&
      config.environment === 'fixture' &&
      config.fixture_expire_deadline_before_room_restore
    ) deadline = Date.now() - 1;
    if (sameTabNavigation) {
      phase = 'room_search_restore';
      if (!(await restoreSearchBeforeDeadline()))
        throw new SourceFailure(Date.now() >= deadline ? 'source_timeout' : 'room_search_restore_failed');
      await waitDOM(page,check,() => Boolean(document.querySelector('#search-box')),null,deadline);
    }
    return product;
  } catch (error) {
    const destinationAtFailure = destinationMonitor?.getDestination();
    destinationMonitor?.cancel();
    if (destinationAtFailure?.kind === 'popup' && !popup) {
      popup = destinationAtFailure.page;
      if (phase === 'detail_button_click' && !stageErrorRecorded) {
        trace('room_product_destination_discarded',{
          ...detail,
          phase:'detail_button_click',
          destination_kind:'popup',
          failed_operation:'room_detail_button_click_begin',
          error_class:safeThrownErrorClass(error),
        });
      }
    }
    if (destinationAtFailure?.kind === 'same_tab') sameTabNavigation = true;
    const errorClass = safeThrownErrorClass(error);
    const failedOperation = phase === 'detail_button_click'
      ? 'room_detail_button_click_begin'
      : phase === 'product_destination_wait'
        ? 'room_product_destination_wait_begin'
        : phase === 'room_search_restore'
          ? 'room_search_restore_begin'
          : null;
    const rowTimeout = (error instanceof SourceFailure && error.code === 'product_row_timeout') ||
      (error?.name === 'TimeoutError' && Date.now() < deadline &&
        ['preview_click','detail_button_click','product_navigation'].includes(phase));
    const queryFailure = !rowTimeout || Date.now() >= deadline || phase === 'room_search_restore';
    if (!stageErrorRecorded && phase === 'detail_button_click') {
      trace('room_detail_button_click_error',{
        ...detail,
        phase:'detail_button_click',
        scope:queryFailure ? 'query' : 'row',
        error_class:errorClass,
        failed_operation:failedOperation,
        timeout_ms:clickTimeoutMs,
        wait_elapsed_ms:clickStartedAt == null ? 0 : Math.max(0,Date.now()-clickStartedAt),
        element_obscured:control?.obscured,
      });
      stageErrorRecorded = true;
    } else if (!stageErrorRecorded && phase === 'product_destination_wait') {
      trace('room_product_destination_wait_error',{
        ...detail,
        phase:'product_destination_wait',
        scope:queryFailure ? 'query' : 'row',
        error_class:errorClass,
        failed_operation:failedOperation,
        timeout_ms:destinationWaitTimeoutMs,
        wait_elapsed_ms:destinationWaitStartedAt == null ? 0 : Math.max(0,Date.now()-destinationWaitStartedAt),
      });
      stageErrorRecorded = true;
    }
    const diagnosticPage = popup ?? (sameTabNavigation ? page : null);
    trace(
      errorClass === 'TimeoutError' ? 'room_product_timeout' : 'room_product_unresolved',
      {
        ...detail,
        ...(diagnosticPage ? popupUrlMetadata(diagnosticPage) : {}),
        phase,
        scope:queryFailure ? 'query' : 'row',
        error_class:errorClass,
        ...(failedOperation ? {failed_operation:failedOperation} : {}),
      },
    );
    if (queryFailure) {
      if (Date.now() >= deadline &&
          (!(error instanceof SourceFailure) || error.code === 'product_row_timeout'))
        throw new SourceFailure('source_timeout');
      throw error;
    }
    await check();
    trace('room_product_row_unresolved',{
      ...detail,
      scope:'row',
      phase,
      error_class:rowTimeout ? 'TimeoutError' : errorClass,
      ...(failedOperation ? {failed_operation:failedOperation} : {}),
    });
    return null; // An unresolved identity still occupies its original source slot.
  } finally {
    destinationMonitor?.cancel();
    if (popup && !popup.isClosed()) await popup.close().catch(() => {});
    if (sameTabNavigation && !page.isClosed()) {
      try {
        if (!(await restoreSearchBeforeDeadline()))
          throw new SourceFailure(Date.now() >= deadline ? 'source_timeout' : 'room_search_restore_failed');
        await waitDOM(page,check,() => Boolean(document.querySelector('#search-box')),null,deadline);
      } catch (error) {
        if (error instanceof SourceFailure) throw error;
        throw new SourceFailure('room_search_restore_failed');
      }
    }
    if (Date.now() < deadline) {
      const close = page.locator('.close-button').filter({visible:true}).first();
      if (await close.count()) {
        trace('room_product_modal_close_begin',{...detail,scope:'row'});
        try {
          await close.click({timeout:Math.min(1000,deadline-Date.now())});
        } catch (error) {
          trace('room_product_modal_close_control_error',{
            ...detail,scope:'query',error_class:safeThrownErrorClass(error),
          });
        }
        if (await page.locator('.close-button').filter({visible:true}).count()) {
          trace('room_product_modal_escape_close_begin',{...detail,scope:'query'});
          try {
            const remaining = deadline-Date.now();
            if (remaining <= 0) throw new SourceFailure('source_timeout');
            await page.keyboard.press('Escape');
            trace('room_product_modal_escape_close_sent',{...detail,scope:'query'});
            await close.waitFor({
              state:'hidden',
              timeout:Math.min(1000,Math.max(1,deadline-Date.now())),
            });
            trace('room_product_modal_escape_close_complete',{...detail,scope:'query'});
          } catch (error) {
            trace('room_product_modal_escape_close_error',{
              ...detail,scope:'query',error_class:safeThrownErrorClass(error),
            });
          }
        }
        if (await page.locator('.close-button').filter({visible:true}).count()) {
          trace('room_product_modal_close_error',{
            ...detail,scope:'query',error_class:'Error',
          });
          throw new SourceFailure('room_search_restore_failed');
        }
        trace('room_product_modal_close_complete',{...detail,scope:'row'});
      }
    } else {
      trace('room_modal_close_skipped_deadline',{...detail,scope:'query'});
    }
  }
}
async function conditionPopupState(popup) {
  try {
    return await popup.isVisible() ? 'visible' : 'hidden';
  } catch {
    return 'unknown';
  }
}

function remainingConditionTimeout(deadline, maximumMs) {
  const remaining = deadline - Date.now();
  return remaining > 0 ? Math.max(1, Math.min(maximumMs, remaining)) : 0;
}

async function waitForConditionPopupHidden(popup, deadline) {
  const timeout = remainingConditionTimeout(deadline, 5000);
  if (!timeout) {
    trace('room_condition_close_wait_deadline', {
      room_condition_popup_state:await conditionPopupState(popup),
    });
    return await conditionPopupState(popup) === 'hidden';
  }
  trace('room_condition_close_wait_begin', {
    room_condition_popup_state:await conditionPopupState(popup),
  });
  try {
    await popup.waitFor({state:'hidden',timeout});
    trace('room_condition_close_wait_complete', {
      room_condition_popup_state:'hidden',
    });
    return true;
  } catch (error) {
    const state = await conditionPopupState(popup);
    trace('room_condition_close_wait_error', {
      error_class:safeThrownErrorClass(error),
      room_condition_popup_state:state,
    });
    return state === 'hidden';
  }
}

export async function closeConditionPopup(page, popup, deadline) {
  const initialState = await conditionPopupState(popup);
  if (initialState === 'hidden') {
    trace('room_condition_popup_already_closed', {
      room_condition_close_method:'already_closed',
      room_condition_popup_state:'hidden',
      room_condition_close_control_count:0,
      room_condition_search_control_count:0,
    });
    return {closed:true,method:'already_closed',closeControlCount:0,searchControlCount:0};
  }

  const controls = popup.locator('button, a, [role="button"], [aria-label], [title]')
    .filter({visible:true});
  const matchingIndexes = await controls.evaluateAll(elements => {
    const compact = value => (value ?? '').normalize('NFKC').replace(/\s/g,'');
    return elements.flatMap((element,index) => {
      const style = getComputedStyle(element);
      const accessibleName = element.getAttribute('aria-label') ??
        element.getAttribute('title') ?? element.innerText ?? '';
      const enabled = !element.disabled && element.getAttribute('aria-disabled') !== 'true';
      const visible = element.getClientRects().length > 0 && style.visibility !== 'hidden' &&
        style.display !== 'none' && style.pointerEvents !== 'none';
      return enabled && visible && compact(accessibleName) === '閉じる' ? [index] : [];
    });
  });
  const searchIndexes = await controls.evaluateAll(elements => {
    const compact = value => (value ?? '').normalize('NFKC').replace(/\s/g,'');
    return elements.flatMap((element,index) => {
      const style = getComputedStyle(element);
      const accessibleName = element.getAttribute('aria-label') ??
        element.getAttribute('title') ?? element.innerText ?? '';
      const enabled = !element.disabled && element.getAttribute('aria-disabled') !== 'true';
      const visible = element.getClientRects().length > 0 && style.visibility !== 'hidden' &&
        style.display !== 'none' && style.pointerEvents !== 'none';
      return enabled && visible && compact(accessibleName) === '検索' ? [index] : [];
    });
  });
  const closeControlCount = Math.min(100,matchingIndexes.length);
  const searchControlCount = Math.min(100,searchIndexes.length);
  trace('room_condition_close_control_inspected', {
    room_condition_close_control_count:closeControlCount,
    room_condition_search_control_count:searchControlCount,
    room_condition_popup_state:initialState,
  });

  if (matchingIndexes.length === 1) {
    trace('room_condition_close_control_begin', {
      room_condition_close_control_count:closeControlCount,
      room_condition_search_control_count:searchControlCount,
      room_condition_popup_state:initialState,
    });
    try {
      const clickTimeout = remainingConditionTimeout(deadline,2500);
      if (!clickTimeout) throw new SourceFailure('condition_popup_close_failed');
      await controls.nth(matchingIndexes[0]).click({timeout:clickTimeout});
      trace('room_condition_close_control_clicked', {
        room_condition_close_control_count:closeControlCount,
        room_condition_search_control_count:searchControlCount,
        room_condition_popup_state:await conditionPopupState(popup),
      });
      if (await waitForConditionPopupHidden(popup,deadline)) {
        trace('room_condition_close_control_complete', {
          room_condition_close_method:'close_control',
          room_condition_close_control_count:closeControlCount,
          room_condition_search_control_count:searchControlCount,
          room_condition_popup_state:'hidden',
        });
        return {closed:true,method:'close_control',closeControlCount,searchControlCount};
      }
    } catch (error) {
      trace('room_condition_close_control_error', {
        error_class:safeThrownErrorClass(error),
        room_condition_close_control_count:closeControlCount,
        room_condition_search_control_count:searchControlCount,
        room_condition_popup_state:await conditionPopupState(popup),
      });
    }
  } else {
    trace(matchingIndexes.length ? 'room_condition_close_control_ambiguous' :
      'room_condition_close_control_missing', {
        room_condition_close_control_count:closeControlCount,
        room_condition_search_control_count:searchControlCount,
        room_condition_popup_state:initialState,
      });
  }

  if (await conditionPopupState(popup) === 'hidden') {
    trace('room_condition_popup_already_closed', {
      room_condition_close_method:matchingIndexes.length === 1 ? 'close_control' : 'already_closed',
      room_condition_close_control_count:closeControlCount,
      room_condition_search_control_count:searchControlCount,
      room_condition_popup_state:'hidden',
    });
    return {
      closed:true,
      method:matchingIndexes.length === 1 ? 'close_control' : 'already_closed',
      closeControlCount,
      searchControlCount,
    };
  }

  // Some public ROOM layouts expose the verified conditions as a sheet with
  // no dismiss control. Its unique search action applies those conditions and
  // reveals results; it is not a toggle or an item action.
  if (matchingIndexes.length !== 1 && searchIndexes.length === 1) {
    trace('room_condition_search_submit_begin', {
      room_condition_close_control_count:closeControlCount,
      room_condition_search_control_count:searchControlCount,
      room_condition_popup_state:initialState,
    });
    try {
      const clickTimeout = remainingConditionTimeout(deadline,ROOM_CONDITION_SEARCH_CLICK_TIMEOUT_MS);
      if (!clickTimeout) throw new SourceFailure('condition_popup_close_failed');
      await controls.nth(searchIndexes[0]).click({timeout:clickTimeout});
      trace('room_condition_search_submit_clicked', {
        room_condition_close_control_count:closeControlCount,
        room_condition_search_control_count:searchControlCount,
        room_condition_popup_state:await conditionPopupState(popup),
      });
      if (await waitForConditionPopupHidden(popup,deadline)) {
        trace('room_condition_search_submit_complete', {
          room_condition_close_method:'search_submit',
          room_condition_close_control_count:closeControlCount,
          room_condition_search_control_count:searchControlCount,
          room_condition_popup_state:'hidden',
        });
        return {closed:true,method:'search_submit',closeControlCount,searchControlCount};
      }
    } catch (error) {
      trace('room_condition_search_submit_error', {
        error_class:safeThrownErrorClass(error),
        room_condition_close_control_count:closeControlCount,
        room_condition_search_control_count:searchControlCount,
        room_condition_popup_state:await conditionPopupState(popup),
      });
    }
  }

  trace('room_condition_escape_close_begin', {
    room_condition_close_control_count:closeControlCount,
    room_condition_search_control_count:searchControlCount,
    room_condition_popup_state:await conditionPopupState(popup),
  });
  try {
    if (deadline - Date.now() <= 0) throw new SourceFailure('condition_popup_close_failed');
    await page.keyboard.press('Escape');
    trace('room_condition_escape_close_sent', {
      room_condition_close_control_count:closeControlCount,
      room_condition_search_control_count:searchControlCount,
      room_condition_popup_state:await conditionPopupState(popup),
    });
    if (await waitForConditionPopupHidden(popup,deadline)) {
      trace('room_condition_escape_close_complete', {
        room_condition_close_method:'escape',
        room_condition_close_control_count:closeControlCount,
        room_condition_search_control_count:searchControlCount,
        room_condition_popup_state:'hidden',
      });
      return {closed:true,method:'escape',closeControlCount,searchControlCount};
    }
  } catch (error) {
    trace('room_condition_escape_close_error', {
      error_class:safeThrownErrorClass(error),
      room_condition_close_control_count:closeControlCount,
      room_condition_search_control_count:searchControlCount,
      room_condition_popup_state:await conditionPopupState(popup),
    });
  }
  const finalState = await conditionPopupState(popup);
  trace('room_condition_popup_close_failed', {
    scope:'query',
    room_condition_close_method:'none',
    room_condition_close_control_count:closeControlCount,
    room_condition_search_control_count:searchControlCount,
    room_condition_popup_state:finalState,
  });
  return {closed:false,method:'none',closeControlCount,searchControlCount,state:finalState};
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
  trace('room_condition_popup_open_begin');
  await page.locator('.search-detail-wrap').click({
    timeout:remainingConditionTimeout(deadline,2500),
  });
  const conditionPopups = page.locator('.itemSearch-popup');
  const conditionPopup = conditionPopups.filter({visible:true});
  await conditionPopup.first().waitFor({
    state:'visible',timeout:remainingConditionTimeout(deadline,5000),
  });
  trace('room_condition_popup_opened', {
    room_condition_popup_state:'visible',
  });
  await check();
  const likeDisplay = await conditionPopup.locator('.search-popup-project').filter({hasText:'いいね数'}).locator('.select > .select-item').first().innerText();
  const commentDisplay = await conditionPopup.locator('.search-popup-project').filter({hasText:'コメント数'}).locator('.select > .select-item').first().innerText();
  const likesVerified = compact(likeDisplay) === '300件';
  const commentsVerified = compact(commentDisplay) === 'コメント数指定なし';
  if (!likesVerified || !commentsVerified)
    throw new SourceFailure('search_conditions_unverified');
  trace('room_conditions_verified', {
    room_conditions_likes_verified:likesVerified,
    room_conditions_comments_verified:commentsVerified,
    room_condition_popup_state:'visible',
  });
  const closeResult = await closeConditionPopup(page,conditionPopup.first(),deadline);
  if (!closeResult.closed) throw new SourceFailure('condition_popup_close_failed');
  await check();
  const reflectedUrl = new URL(page.url());
  const queryPreserved = await page.locator('#search-box').inputValue() === request.query;
  const conditionsPreserved = reflectedUrl.searchParams.get('like') === '300' &&
    reflectedUrl.searchParams.get('comment') === '';
  const popupState = await conditionPopupState(conditionPopup.first());
  if (!queryPreserved || !conditionsPreserved || popupState !== 'hidden')
    throw new SourceFailure('search_conditions_unverified');
  trace('room_condition_popup_closed', {
    room_condition_close_method:closeResult.method,
    room_condition_close_control_count:closeResult.closeControlCount,
    room_condition_popup_state:popupState,
    room_conditions_likes_verified:likesVerified,
    room_conditions_comments_verified:commentsVerified,
  });
  trace('room_results_scan_begin', {
    scope:'query',
    room_condition_popup_state:popupState,
    room_condition_close_method:closeResult.method,
  });
  const cards = page.locator('.items-search > .item');
  const roomEndMarkers = [
    '検索結果は以上です','検索結果がありません','該当する商品がありません',
    '検索結果はありません','ご指定の検索条件に該当する商品はありません。',
  ];
  const roomNoResultMarkers = [
    '検索結果がありません','該当する商品がありません','検索結果はありません',
    'ご指定の検索条件に該当する商品はありません。',
  ];
  const readEndMarker = () => page.evaluate(({markers,noResultMarkers,loadedPosts}) => {
    const normalize = value => String(value ?? '').replace(/\s+/gu,'');
    const productCardSelector = '.items-search > .item, .item-preview';
    const markerByNormalizedText = new Map(markers.map(marker => [normalize(marker),marker]));
    const noResultText = new Set(noResultMarkers.map(normalize));
    for (const element of document.querySelectorAll('body *')) {
      const marker = markerByNormalizedText.get(normalize(element.innerText ?? element.textContent));
      const emptyStatePreview = loadedPosts === 0 && Boolean(element.closest('.item-preview')) &&
        noResultText.has(normalize(marker));
      if ((element.closest(productCardSelector) && !emptyStatePreview) ||
          !element.getClientRects().length)
        continue;
      if (marker && (emptyStatePreview || !element.querySelector(productCardSelector)))
        return marker;
    }
    return null;
  },{markers:roomEndMarkers,noResultMarkers:roomNoResultMarkers,loadedPosts:loaded});
  let loaded = 0;
  let exhausted = false;
  let endMarker = null;
  let stableShortBatch = false;
  let stableNoProgressRounds = 0;
  let priorLoaded = -1;
  while (Date.now() < deadline) {
    await check();
    loaded = await cards.count();
    endMarker = await readEndMarker();
    exhausted = endMarker !== null;
    if (loaded !== priorLoaded) {
      trace('room_list_progress',{scope:'query',loaded_posts:loaded});
      priorLoaded = loaded;
    }
    if (loaded >= request.max_posts || exhausted) break;
    if (config.environment === 'fixture' && config.fixture_room_scroll_delay_ms > 0) {
      const delayMs = Math.min(config.fixture_room_scroll_delay_ms,Math.max(0,deadline-Date.now()+1));
      await new Promise(resolve => setTimeout(resolve,delayMs));
    }
    await check();
    const scrollRemaining = deadline-Date.now();
    if (scrollRemaining <= 0) {
      trace('room_scroll_deadline_expired',{scope:'query'});
      throw new SourceFailure('source_timeout');
    }
    const scrolled = await page.evaluate(cutoff => {
      if (Date.now() >= cutoff) return false;
      window.scrollTo(0, document.documentElement.scrollHeight);
      return true;
    },deadline);
    if (!scrolled) {
      trace('room_scroll_deadline_expired',{scope:'query'});
      throw new SourceFailure('source_timeout');
    }
    const before = loaded;
    const changeDeadline = Math.min(deadline, Date.now()+2000);
    try {
      await waitDOM(page, check, state => {
        const [minimumLoaded, markers, noResultMarkers] = state;
        const normalize = value => String(value ?? '').replace(/\s+/gu,'');
        const productCardSelector = '.items-search > .item, .item-preview';
        const markerTexts = new Set(markers.map(normalize));
        const noResultText = new Set(noResultMarkers.map(normalize));
        return document.querySelectorAll('.items-search > .item').length > minimumLoaded ||
          Array.from(document.querySelectorAll('body *')).some(element => {
            const normalizedText = normalize(element.innerText ?? element.textContent);
            const emptyStatePreview = minimumLoaded === 0 &&
              Boolean(element.closest('.item-preview')) && noResultText.has(normalizedText);
            if ((element.closest(productCardSelector) && !emptyStatePreview) ||
                !element.getClientRects().length) return false;
            return markerTexts.has(normalizedText) &&
              (emptyStatePreview || !element.querySelector(productCardSelector));
          });
      }, [before,roomEndMarkers,roomNoResultMarkers], changeDeadline);
    } catch (error) {
      if (!(error instanceof SourceFailure) || error.code !== 'source_timeout') throw error;
      // A positive short batch ends only after three bounded scrolls add no rows.
    }
    await check();
    loaded = await cards.count();
    endMarker = await readEndMarker();
    exhausted = endMarker !== null;
    if (loaded > before) {
      stableNoProgressRounds = 0;
    } else if (exhausted) {
      break;
    } else {
      stableNoProgressRounds += 1;
      trace('room_list_no_progress', {
        scope:'query',
        loaded_posts: loaded,
        stable_round: stableNoProgressRounds,
      });
      // An unchanged empty list can still be loading after filters are applied.
      // Only a positive short batch can finish without an explicit end marker.
      if (loaded > 0 && stableNoProgressRounds >= 3) {
        stableShortBatch = true;
        trace('room_short_batch_stable', {
          scope:'query',
          loaded_posts: loaded,
          stable_round: stableNoProgressRounds,
        });
        break;
      }
    }
  }
  const explicitNoResult = roomNoResultMarkers.includes(endMarker);
  if (loaded === 0 && !explicitNoResult && !stableShortBatch) {
    trace('room_search_incomplete',{
      scope:'query',
      loaded_posts:loaded,
      end_evidence:endMarker === null ? 'no_marker' : 'unrecognized_empty',
    });
    throw new SourceFailure('incomplete_search');
  }
  if (loaded < request.max_posts && !exhausted && !stableShortBatch)
    throw new SourceFailure('incomplete_search');
  const endEvidence = loaded === 0 ?
    explicitNoResult ? 'explicit_no_results' : 'stable_no_progress' :
    exhausted ? 'explicit_end' : stableShortBatch ? 'stable_no_progress' : 'row_limit';
  trace('room_results_complete',{
    scope:'query',
    loaded_posts:loaded,
    accepted_posts:Math.min(request.max_posts,loaded),
    end_evidence:endEvidence,
  });
  trace('room_resolve_products',{
    scope:'query',loaded_posts:loaded,accepted_posts:Math.min(request.max_posts,loaded),
  });
  const fetched = timestamp();
  const posts = [];
  const facts = {};
  let productResolutionFailures = 0;
  for (let index = 0; index < Math.min(request.max_posts, loaded); index++) {
    if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
    const card = cards.nth(index);
    const name = await card.locator('.imgLink img').first().getAttribute('alt').catch(() => null);
    const image = await card.locator('.imgLink img').first().getAttribute('src').catch(() => null);
    const ids = await resolveProduct(page, card, check, deadline, access, index+1, config, request);
    if (!ids) productResolutionFailures += 1;
    trace('room_product_row_complete',{
      scope:'row',
      row_index:index+1,
      accepted_posts:index+1,
      row_status:ids ? 'identified' : 'unresolved',
    });
    const evidenceRef = `${request.attempt_id}:row:${index+1}`;
    posts.push({ internal_index: index+1, stable_room_id: null, room_url: null,
      likes: null, comments: null, item_url: ids?.url ?? null, observed_shop_id: ids?.shop ?? null,
      observed_item_id: ids?.item ?? null, image_ref: image, actor_ref: null, published_at: null,
      evidence_ref: evidenceRef, observed_at: fetched, availability: ids ? 'observed' : 'unknown' });
    if (ids) facts[evidenceRef] = { offer_key: `${ids.shop}:${ids.item}`, name: name || null,
      item_url: ids.url, image_ref: image, evidence_ref: evidenceRef, observed_at: fetched,
      origin: 'public_product_image_label',
      ...(ids.page_capture ? {
        product_page_ref: ids.page_capture.page_ref,
        product_page_state: ids.page_capture.state,
        product_page_image_count: ids.page_capture.image_count,
        product_page_identity: { run_id: request.run_id, shop_id: ids.shop, item_id: ids.item },
      } : {}) };
  }
  await check();
  if (Date.now() >= deadline) throw new SourceFailure('source_timeout');
  trace('room_product_resolution_summary',{
    scope:'query',
    loaded_posts:loaded,
    accepted_posts:posts.length,
    row_failure_count:productResolutionFailures,
    identified_row_count:posts.length-productResolutionFailures,
  });
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
      end_evidence: endEvidence } };
}

export async function collectProductPage(page, request, config, deadline, access) {
  const product = productIds(request.item_url);
  if (!product || product.shop !== request.shop_id || product.item !== request.item_id)
    throw new SourceFailure('invalid_request');
  const check = guardPage(page, 'item.rakuten.co.jp', access);
  trace('product_page_capture_navigation_begin', {
    scope:'row', row_index:request.row_index,
  });
  await page.goto(request.item_url, {
    waitUntil:'domcontentloaded',
    timeout:Math.min(30000, Math.max(1, deadline-Date.now())),
  });
  await check();
  const landed = productIds(page.url());
  if (!landed || landed.shop !== request.shop_id || landed.item !== request.item_id)
    throw new SourceFailure('product_page_identity_mismatch');
  await waitDOM(page, check, () => Boolean(document.body?.children.length && document.body.innerText.trim()), null, deadline);
  trace('product_page_capture_rendered', {
    scope:'row', row_index:request.row_index,
  });
  const captured = await saveProductPageInputs({
    page,
    product:{...product, url:request.item_url},
    request,
    rowIndex:request.row_index,
    config,
  });
  trace('product_page_capture_saved', {
    scope:'row', row_index:request.row_index,
    page_capture_state:captured.state,
    product_page_image_count:captured.image_count,
  });
  return {
    request_id:request.request_id,
    run_id:request.run_id,
    shop_id:request.shop_id,
    item_id:request.item_id,
    page_ref:captured.page_ref,
    state:captured.state,
    image_count:captured.image_count,
  };
}

async function emptyEvidence(widget) {
  const matches = await widget.getByText(/^(データがありません|この検索キーワードのデータはありません|ここに表示するデータはありません。)$/).all();
  for (const match of matches) {
    if (await match.isVisible() && await match.evaluate(e=>!e.closest('a.progress-label'))) return match;
  }
  return null;
}

async function installTrendsWidgetObserver(page) {
  await page.addInitScript(() => {
    const kindFor = widget => {
      const visible = element => {
        if (!element.isConnected) return false;
        const style = getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden' &&
          style.opacity !== '0' && element.getClientRects().length > 0;
      };
      const candidates = Array.from(widget.querySelectorAll(
        'h1,h2,h3,h4,h5,h6,[role="heading"],[class*="title"],[class*="heading"]',
      ));
      if (widget.hasAttribute('aria-label')) candidates.unshift(widget);
      const labelledBy = widget.getAttribute('aria-labelledby');
      if (labelledBy) {
        for (const id of labelledBy.trim().split(/\s+/)) {
          const label = document.getElementById(id);
          if (label && visible(label)) candidates.unshift(label);
        }
      }
      const kinds = new Set();
      for (const candidate of new Set(candidates)) {
        const text = candidate.getAttribute('aria-label') || candidate.textContent || '';
        const label = text.normalize('NFKC').replace(/\s/g,'').toLocaleLowerCase('ja');
        const topics = label.includes('トピック') || /topics?/i.test(label);
        const keywords = /キーワード|検索|クエリ/.test(label) ||
          /keywords?|queries|search(?:es)?/i.test(label);
        if (topics !== keywords) kinds.add(topics ? 'related_topics' : 'related_keywords');
      }
      if (kinds.size === 1) return kinds.values().next().value;
      return 'unknown';
    };
    window.__inagoTrendsWidgetKind = kindFor;
    const widgetFor = node => {
      const element = node instanceof Element ? node : node?.parentElement;
      if (!element) return null;
      if (element.matches('.fe-related-queries')) return element;
      return element.closest('.fe-related-queries');
    };
    const isRelatedKeywordNode = node => {
      const widget = widgetFor(node);
      return widget !== null && kindFor(widget) === 'related_keywords';
    };
    const containsRelatedKeyword = node => {
      if (!(node instanceof Element)) return false;
      return isRelatedKeywordNode(node) ||
        Array.from(node.querySelectorAll('.fe-related-queries')).some(widget =>
          kindFor(widget) === 'related_keywords');
    };
    window.__inagoTrendsWidgetGeneration = 0;
    const observer = new MutationObserver(records => {
      // Bind by the rendered heading, rather than an index that can change
      // when the region/map widget is inserted or Angular replaces a panel.
      for (const widget of document.querySelectorAll('.fe-related-queries')) {
        const kind = kindFor(widget);
        if (widget.getAttribute('data-inago-trends-kind') !== kind)
          widget.setAttribute('data-inago-trends-kind',kind);
      }
      if (records.some(record => isRelatedKeywordNode(record.target) ||
          Array.from(record.addedNodes).some(containsRelatedKeyword) ||
          Array.from(record.removedNodes).some(containsRelatedKeyword)))
        window.__inagoTrendsWidgetGeneration += 1;
    });
    observer.observe(document, {
      subtree:true,
      childList:true,
      characterData:true,
      attributes:true,
      attributeFilter:['class','style','aria-hidden'],
    });
  });
}

async function relatedWidgetState(page) {
  return await page.evaluate(() => {
    const kindFor = widget => window.__inagoTrendsWidgetKind?.(widget) ?? 'unknown';
    const isVisible = element => {
      if (!element.isConnected) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' &&
        rect.width > 0 && rect.height > 0;
    };
    const widgets = Array.from(document.querySelectorAll('.fe-related-queries'));
    const records = widgets.map(element => {
      const kind = kindFor(element);
      const visible = isVisible(element);
      const empty = visible && Array.from(element.querySelectorAll('*')).some(node =>
        isVisible(node) && !node.closest('a.progress-label') &&
        ['データがありません','この検索キーワードのデータはありません','ここに表示するデータはありません。'].includes(node.textContent.trim()));
      return {kind,visible,empty};
    });
    const keyword = records.filter(item => item.kind === 'related_keywords');
    const visibleKeyword = keyword.filter(item => item.visible);
    return {
      widget_dom_count:records.length,
      widget_visible_count:records.filter(item => item.visible).length,
      widget_match_count:keyword.length,
      widget_match_visible_count:visibleKeyword.length,
      widget_wrong_visible_count:records.filter(item => item.visible && item.kind !== 'related_keywords').length,
      related_widget_generation:Number.isInteger(window.__inagoTrendsWidgetGeneration)
        ? window.__inagoTrendsWidgetGeneration : 0,
      widget_kind:visibleKeyword.length > 1 ? 'multiple' :
        visibleKeyword.length === 1 ? 'related_keywords' :
        records.some(item => item.visible && item.kind === 'related_topics') ? 'related_topics' : 'unknown',
      widget_empty_evidence:visibleKeyword.length === 1 && visibleKeyword[0].empty
        ? 'explicit_no_data' : 'none',
    };
  });
}

async function waitForRelatedWidget(page, check, deadline, generationBeforePeriod) {
  let state = await relatedWidgetState(page);
  const periodRefreshRequired = generationBeforePeriod !== null;
  const details = () => ({
    widget_scope:'explore_related_queries',
    widget_selector_version:TRENDS_WIDGET_SELECTOR_VERSION,
    ...state,
    widget_period_refresh_required:periodRefreshRequired,
    ...(generationBeforePeriod === null ? {} : {generation_before_period:generationBeforePeriod}),
  });
  trace('trends_related_widget_wait_begin',details());
  while (Date.now() < deadline) {
    await check();
    state = await relatedWidgetState(page);
    if (state.widget_match_visible_count > 1) {
      trace('trends_related_widget_contract_error',{
        ...details(),widget_outcome:'ambiguous',
      });
      throw new SourceFailure('query_widget_ambiguous');
    }
    const periodUpdated = generationBeforePeriod === null ||
      state.related_widget_generation > generationBeforePeriod;
    if (state.widget_match_visible_count === 1 && periodUpdated) {
      const visibleWidgets = page.locator('.fe-related-queries').filter({visible:true});
      const matchingIndexes = [];
      for (let index = 0; index < await visibleWidgets.count(); index += 1) {
        if (await visibleWidgets.nth(index).evaluate(
          element => window.__inagoTrendsWidgetKind?.(element) === 'related_keywords',
        )) matchingIndexes.push(index);
      }
      if (matchingIndexes.length !== 1) {
        // Angular can replace or lay out the widget between the state read and
        // locator resolution. Reobserve within the existing deadline.
        await page.waitForTimeout(Math.min(100,Math.max(1,deadline-Date.now())));
        continue;
      }
      trace('trends_related_widget_wait_ready',{
        ...details(),widget_outcome:'ready',
      });
      return page.locator('.fe-related-queries[data-inago-trends-kind="related_keywords"]')
        .filter({visible:true});
    }
    await page.waitForTimeout(Math.min(100,Math.max(1,deadline-Date.now())));
  }
  state = await relatedWidgetState(page);
  if (state.widget_match_count === 0 && state.widget_wrong_visible_count > 0) {
    trace('trends_related_widget_contract_error',{
      ...details(),widget_outcome:'contract_mismatch',
    });
    throw new SourceFailure('query_widget_contract_mismatch');
  }
  trace('trends_related_widget_wait_timeout',{
    ...details(),widget_outcome:'deadline',
  });
  throw new SourceFailure('source_timeout');
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

async function waitForTrendsActionReady(page, locator, deadline, check) {
  await check();
  await locator.click({trial:true,timeout:deadlineTimeout(deadline)});
  if (deadline - Date.now() < 1000) throw new SourceFailure('source_timeout');
  await page.waitForTimeout(1000);
  ensureBeforeDeadline(deadline);
  await check();
}

async function clickTrendsAfterReady(page, locator, deadline, check) {
  await waitForTrendsActionReady(page,locator,deadline,check);
  await locator.click({timeout:deadlineTimeout(deadline)});
  ensureBeforeDeadline(deadline);
  await check();
}

async function fillTrendsAfterReady(page, locator, value, deadline, check) {
  await waitForTrendsActionReady(page,locator,deadline,check);
  await locator.fill(value,{timeout:deadlineTimeout(deadline)});
  ensureBeforeDeadline(deadline);
  await check();
}

function decodedTrendsQuery(url) {
  let value = url.searchParams.get('q') ?? '';
  for (let index=0; index<2 && /%[0-9a-f]{2}/i.test(value); index++) {
    try { value = decodeURIComponent(value); }
    catch { return null; }
  }
  return value;
}

async function collectExploreTrends(page, request, deadline, check) {
  trace('trends_explore_query_wait_begin');
  const search = page.getByRole('combobox',{name:'検索キーワードを追加',exact:true})
    .filter({visible:true});
  await search.waitFor({state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now()))});
  if (await search.count() !== 1) throw new SourceFailure('query_widget_ambiguous');
  trace('trends_explore_query_ready');
  await fillTrendsAfterReady(page,search.first(),request.parent_word,deadline,check);
  trace('trends_explore_query_entered');

  const searchKeywordOption = page.getByRole('option',{
    name:`${request.parent_word} 検索キーワード`,exact:true,
  }).filter({visible:true});
  await searchKeywordOption.waitFor({
    state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
  });
  const optionCount = await searchKeywordOption.count();
  if (optionCount !== 1) throw new SourceFailure('query_widget_ambiguous');
  trace('trends_explore_query_option_visible');
  await clickTrendsAfterReady(page,searchKeywordOption.first(),deadline,check);
  trace('trends_explore_query_selected');
  await check();
  const searchKeywordChip = page.getByRole('button',{
    name:`${request.parent_word} 検索キーワード`,exact:true,
  }).filter({visible:true});
  await searchKeywordChip.waitFor({
    state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
  });
  await waitDOM(page,check,query => {
    const input = Array.from(document.querySelectorAll('input[role="combobox"]')).find(
      element => element.disabled && element.getAttribute('aria-label') === query,
    );
    let value = new URL(location.href).searchParams.get('q') ?? '';
    for (let index=0; index<2 && /%[0-9a-f]{2}/i.test(value); index++) {
      try { value = decodeURIComponent(value); }
      catch { return false; }
    }
    const chip = Array.from(document.querySelectorAll('button')).some(element =>
      element.getAttribute('aria-label') === `${query} 検索キーワード`);
    return Boolean(input && chip && value === query);
  },request.parent_word,deadline);

  const japanButton = page.getByRole('button',{
    name:'日本、地域を選択',exact:true,
  }).filter({visible:true});
  if (await japanButton.count() !== 1) {
    const allCountries = page.getByRole('button',{
      name:'すべての国、地域を選択',exact:true,
    }).filter({visible:true});
    if (await allCountries.count() !== 1)
      throw new SourceFailure('trends_scope_not_reflected');
    await clickTrendsAfterReady(page,allCountries.first(),deadline,check);
    const japanOption = page.getByRole('menuitemradio',{
      name:'日本',exact:true,
    }).filter({visible:true});
    await japanOption.waitFor({
      state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
    });
    if (await japanOption.count() !== 1) throw new SourceFailure('query_widget_ambiguous');
    await clickTrendsAfterReady(page,japanOption.first(),deadline,check);
    const allRegions = page.getByRole('menuitem',{
      name:'すべての地域',exact:true,
    }).filter({visible:true});
    await allRegions.waitFor({
      state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
    });
    if (await allRegions.count() !== 1) throw new SourceFailure('query_widget_ambiguous');
    await clickTrendsAfterReady(page,allRegions.first(),deadline,check);
  }
  trace('trends_explore_country_selected');

  const periodControl = page.getByRole('button',{name:/期間を選択$/}).filter({visible:true});
  await periodControl.waitFor({
    state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
  });
  if (await periodControl.count() !== 1) throw new SourceFailure('trends_scope_not_reflected');
  let periodControlLabel = await periodControl.getAttribute('aria-label') ??
    await periodControl.innerText();
  let period = compact(periodControlLabel).replace(/、?期間を選択$/,'');
  if (!allowedPeriods.has(period)) {
    trace('trends_period_menu_open_begin');
    await clickTrendsAfterReady(page,periodControl.first(),deadline,check);
    trace('trends_period_menu_opened');
    const periodOptions = page.getByRole('menuitemradio',{
      name:/^過去\s*3\s*[かヶ]月間$/,
    }).filter({visible:true});
    trace('trends_period_option_wait_begin');
    await periodOptions.first().waitFor({
      state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
    });
    const periodOptionCount = await periodOptions.count();
    trace('trends_period_option_visible',{period_option_count:periodOptionCount});
    if (periodOptionCount !== 1) throw new SourceFailure('query_widget_ambiguous');
    await clickTrendsAfterReady(page,periodOptions.first(),deadline,check);
    trace('trends_period_option_clicked');
    trace('trends_period_reflection_wait_begin');
    await waitDOM(page,check,() => {
      const control = document.querySelector('button[aria-label*="期間を選択"]');
      const label = (control?.getAttribute('aria-label') ?? '')
        .normalize('NFKC').replace(/\s/g,'').replace(/、?期間を選択$/,'');
      const date = new URL(location.href).searchParams.get('date');
      return ['過去90日間','過去90日','過去3か月間','過去3か月','過去3ヶ月間','過去3ヶ月'].includes(label) &&
        ['today 3-m','today 90-d'].includes(date);
    },null,deadline);
    trace('trends_period_reflected');
    periodControlLabel = await periodControl.getAttribute('aria-label') ??
      await periodControl.innerText();
    period = compact(periodControlLabel).replace(/、?期間を選択$/,'');
  }

  const propertyControl = page.getByRole('button',{
    name:/プロパティを選択$/,
  }).filter({visible:true});
  await propertyControl.waitFor({
    state:'visible',timeout:Math.min(20000,Math.max(1,deadline-Date.now())),
  });
  if (await propertyControl.count() !== 1) throw new SourceFailure('trends_scope_not_reflected');
  let propertyLabel = await propertyControl.getAttribute('aria-label') ??
    await propertyControl.innerText();
  if (!propertyLabel.includes('ウェブ検索')) {
    await clickTrendsAfterReady(page,propertyControl.first(),deadline,check);
    const webRadio = page.getByRole('menuitemradio',{
      name:'ウェブ検索',exact:true,
    }).filter({visible:true});
    const webMenuItem = page.getByRole('menuitem',{
      name:'ウェブ検索',exact:true,
    }).filter({visible:true});
    await waitDOM(page,check,() => {
      const matches = Array.from(document.querySelectorAll('[role="menuitemradio"],[role="menuitem"]'))
        .filter(element => element.getClientRects().length &&
          element.textContent.normalize('NFKC').trim() === 'ウェブ検索');
      return matches.length > 0;
    },null,Math.min(deadline,Date.now()+10000));
    const radioCount = await webRadio.count();
    const itemCount = await webMenuItem.count();
    if (radioCount + itemCount !== 1) throw new SourceFailure('query_widget_ambiguous');
    await clickTrendsAfterReady(page,radioCount ? webRadio.first() : webMenuItem.first(),deadline,check);
    propertyLabel = await propertyControl.getAttribute('aria-label') ??
      await propertyControl.innerText();
  }

  await check();
  const exploreUrl = new URL(page.url());
  const countryLabel = await japanButton.getAttribute('aria-label');
  if (!allowedPeriods.has(period) || !['today 3-m','today 90-d'].includes(exploreUrl.searchParams.get('date')) ||
      exploreUrl.searchParams.get('geo') !== 'JP' || decodedTrendsQuery(exploreUrl) !== request.parent_word ||
      countryLabel !== '日本、地域を選択' || !propertyLabel.includes('ウェブ検索') ||
      await searchKeywordChip.count() !== 1)
    throw new SourceFailure('trends_scope_not_reflected');
  trace('trends_explore_scope_reflected');

  // A full page navigation discards any result rows left behind while the
  // Explore controls changed. Google serializes the selected query and scope
  // into this URL, so reload the page once all controls have been verified.
  trace('trends_explore_scope_reload_begin');
  await page.reload({
    waitUntil:'domcontentloaded',
    timeout:deadlineTimeout(deadline),
  });
  ensureBeforeDeadline(deadline);
  trace('trends_explore_scope_reload_complete');
  await waitDOM(page,check,query => {
    const controls = Array.from(document.querySelectorAll('button'));
    const country = controls.some(button => button.getAttribute('aria-label') === '日本、地域を選択');
    const period = controls.find(button => (button.getAttribute('aria-label') ?? '').includes('期間を選択'));
    const periodLabel = (period?.getAttribute('aria-label') ?? '')
      .normalize('NFKC').replace(/\s/g,'');
    const property = controls.some(button =>
      (button.getAttribute('aria-label') ?? '').includes('ウェブ検索') &&
      (button.getAttribute('aria-label') ?? '').includes('プロパティを選択'));
    const chip = controls.some(button => button.getAttribute('aria-label') === `${query} 検索キーワード`);
    const input = Array.from(document.querySelectorAll('input[role="combobox"]')).some(element =>
      element.disabled && element.getAttribute('aria-label') === query);
    let urlQuery = new URL(location.href).searchParams.get('q') ?? '';
    for (let index=0; index<2 && /%[0-9a-f]{2}/i.test(urlQuery); index++) {
      try { urlQuery = decodeURIComponent(urlQuery); }
      catch { return false; }
    }
    const url = new URL(location.href);
    const selectedPeriod = periodLabel.replace(/、?期間を選択$/,'');
    const validPeriod = ['過去90日間','過去90日','過去3か月間','過去3か月','過去3ヶ月間','過去3ヶ月'].includes(selectedPeriod);
    return country && validPeriod && property && chip && input &&
      url.searchParams.get('geo') === 'JP' &&
      ['today 3-m','today 90-d'].includes(url.searchParams.get('date')) &&
      urlQuery === query;
  },request.parent_word,Math.min(deadline,Date.now()+30000));
  trace('trends_explore_scope_reload_reflected');

  const topTable = page.getByRole('table',{name:'上位の語句',exact:true}).filter({visible:true});
  trace('trends_explore_top_rows_wait_begin');
  trace('trends_explore_top_refresh_wait_begin');
  await waitDOM(page,check,() => {
    const table = document.querySelector('table[aria-label="上位の語句"]');
    if (!table || !table.getClientRects().length) return false;
    let ancestor = table;
    while (ancestor) {
      if (ancestor.getAttribute('aria-busy') === 'true') return false;
      ancestor = ancestor.parentElement;
    }
    return true;
  },null,Math.min(deadline,Date.now()+30000));
  const topTableCount = await topTable.count();
  trace('trends_explore_top_table_ready',{top_table_count:topTableCount});
  if (topTableCount !== 1) throw new SourceFailure('query_widget_ambiguous');

  const words = [];
  while (words.length < 20) {
    const expectedRank = words.length + 1;
    await waitDOM(page,check,startRank => {
      const table = document.querySelector('table[aria-label="上位の語句"]');
      const ranks = Array.from(table?.querySelectorAll('tbody tr') ?? []).map(row => {
        const value = row.querySelector('td')?.innerText.trim() ?? '';
        return /^\d+$/.test(value) ? Number(value) : null;
      }).filter(Number.isInteger);
      return ranks.length > 0 && ranks[0] === startRank &&
        ranks.every((rank,index) => rank === startRank + index);
    },expectedRank,Math.min(deadline,Date.now()+30000));
    const observed = await topTable.first().evaluate(table =>
      Array.from(table.querySelectorAll('tbody tr')).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const rankText = cells[0]?.innerText.trim() ?? '';
        const raw = (cells[1]?.innerText ?? '').replace(/[\u200e\u200f]/g,'').trim();
        const interestLabel = cells[2]?.querySelector('[role="img"]')?.getAttribute('aria-label') ?? '';
        const interest = interestLabel.match(/検索インタレスト\s*[:：]\s*(\d+(?:\.\d+)?)/);
        return {raw,source_rank:/^\d+$/.test(rankText) ? Number(rankText) : null,
          popularity_index:interest ? Number(interest[1]) : null};
      }).filter(row => Number.isInteger(row.source_rank))
    );
    trace('trends_explore_top_rows_updated',{top_rows_count:observed.length});
    if (!observed.length) throw new SourceFailure('top_rows_unreadable');
    for (const row of observed) {
      if (words.length === 20) break;
      if (!row.raw || row.raw.length > 500 || row.source_rank !== words.length + 1)
        throw new SourceFailure('top_rank_not_fresh');
      words.push({word_id:`${request.request_id}:w${row.source_rank}`,...row});
    }
    if (words.length === 20) break;
    const container = topTable.first().locator('xpath=../..');
    const next = container.getByRole('button',{name:'次のページに移動',exact:true});
    if (await next.count() !== 1 || !(await next.isEnabled())) break;
    await clickTrendsAfterReady(page,next,deadline,check);
    await check();
    trace('trends_explore_page_changed');
  }

  return {response:{request_id:request.request_id,snapshot_id:request.request_id,run_id:request.run_id,
    settings_version:request.settings_version,source:'google_trends_public_ui',parent_word:request.parent_word,
    current_parent:request.parent_word,refresh_complete:true,language:'ja',geo:'JP',search_type:'web',
    range:'3m',list_type:'Top',ui_period_label:period,status:'observed',fetched_at:timestamp(),
    error_code:null,words},product_facts:{},diagnostics:{source:'public_dom',
      selector_version:'explore-table-v1',word_count:words.length,period_label:period,original_top:true}};
}

function legacyTrendsResult(request, period, words) {
  return {response:{request_id:request.request_id,snapshot_id:request.request_id,run_id:request.run_id,
    settings_version:request.settings_version,source:'google_trends_public_ui',parent_word:request.parent_word,current_parent:request.parent_word,
    refresh_complete:true,language:'ja',geo:'JP',search_type:'web',range:'3m',list_type:'Top',ui_period_label:period,
    status:'observed',fetched_at:timestamp(),error_code:null,words},product_facts:{},
    diagnostics:{source:'public_dom',selector_version:TRENDS_WIDGET_SELECTOR_VERSION,
      word_count:words.length,period_label:period,original_top:true}};
}

async function collectLegacyTrends(page, request, config, deadline, check) {
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
  let generationBeforePeriod = null;
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
    generationBeforePeriod = await page.evaluate(() =>
      Number.isInteger(window.__inagoTrendsWidgetGeneration)
        ? window.__inagoTrendsWidgetGeneration : 0);
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
  // This stage spans initial widget readiness through verified Top row refresh.
  const widget = await waitForRelatedWidget(
    page,check,deadline,generationBeforePeriod,
  );
  return await collectLegacyRelatedTop(page,request,period,widget,deadline,check,false);
}

async function collectLegacyRelatedTop(page,request,period,widget,deadline,check,emptyReloaded) {
  trace('trends_list_control_wait_begin');
  await waitDOM(page,check,() => {
    const widgets = Array.from(document.querySelectorAll(
      '.fe-related-queries[data-inago-trends-kind="related_keywords"]',
    )).filter(element => element.getClientRects().length);
    if (widgets.length !== 1) return false;
    const widget = widgets[0];
    for (let node=widget; node; node=node.parentElement) {
      if (node.getAttribute('aria-busy') === 'true') return false;
    }
    const empty = Array.from(widget.querySelectorAll('*')).some(element =>
      element.getClientRects().length && !element.closest('a.progress-label') &&
      ['データがありません','この検索キーワードのデータはありません','ここに表示するデータはありません。'].includes(element.textContent.trim()));
    return Boolean(widget.querySelector('md-select.bullets-view-selector') || empty);
  },null,Math.min(deadline,Date.now()+20000));
  const dropdown = widget.locator('md-select.bullets-view-selector');
  const dropdownCount = await dropdown.count();
  if (dropdownCount !== 1) {
    const empty = await emptyEvidence(widget);
    // Current Google renders a whole-panel no-data message without a sort
    // control. A generic Rising-only zero cannot establish Top zero.
    if (dropdownCount === 0 && empty &&
        (await empty.innerText()).trim() === 'ここに表示するデータはありません。' &&
        await widget.locator('a.progress-label').count() === 0) {
      await check();
      if (!emptyReloaded) {
        // The period control can update before the old no-data panel changes.
        // Reload its verified public URL before adopting a whole-panel zero.
        trace('trends_legacy_zero_reload_begin');
        await page.reload({waitUntil:'domcontentloaded',timeout:deadlineTimeout(deadline)});
        await waitDOM(page,check,query => {
          const url = new URL(location.href);
          const input = document.querySelector('input[aria-label="検索キーワードを追加"]');
          const period = document.querySelector('[aria-label^="Select time period"]');
          const label = (period?.innerText ?? '').normalize('NFKC').replace(/\s/g,'');
          const property = document.querySelector('[aria-label^="Select stories location"]');
          return input?.value === query && url.searchParams.get('q') === query &&
            url.searchParams.get('geo') === 'JP' &&
            ['today 3-m','today 90-d'].includes(url.searchParams.get('date')) &&
            ['過去90日間','過去90日','過去3か月間','過去3か月','過去3ヶ月間','過去3ヶ月'].includes(label) &&
            property?.innerText.includes('ウェブ検索');
        },request.parent_word,Math.min(deadline,Date.now()+20000));
        const refreshedWidget = await waitForRelatedWidget(page,check,deadline,null);
        trace('trends_legacy_zero_reload_reflected');
        return await collectLegacyRelatedTop(page,request,period,refreshedWidget,deadline,check,true);
      }
      trace('trends_related_widget_normal_zero',{
        top_rows_count:0,widget_empty_evidence:'explicit_no_data',
      });
      return legacyTrendsResult(request,period,[]);
    }
    trace('trends_list_control_contract_error');
    throw new SourceFailure('query_widget_contract_mismatch');
  }
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
        const widget = Array.from(document.querySelectorAll('.fe-related-queries')).find(e=>
          window.__inagoTrendsWidgetKind?.(e) === 'related_keywords' &&
          getComputedStyle(e).display !== 'none' && e.getClientRects().length > 0);
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
            window.__inagoTrendsWidgetKind?.(widget) === 'related_keywords' &&
            getComputedStyle(widget).display !== 'none' &&
            Array.from(widget.querySelectorAll('a.progress-label')).some(e=>e.getClientRects().length)),
          priorEmptyHandle,{timeout:Math.max(1,deadline-Date.now())});
        continue;
      }
      break;
    }
    await waitDOM(page,check, n => {
      const widget = Array.from(document.querySelectorAll('.fe-related-queries')).find(e =>
        window.__inagoTrendsWidgetKind?.(e) === 'related_keywords' &&
        getComputedStyle(e).display !== 'none' && e.getClientRects().length > 0);
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
  return legacyTrendsResult(request,period,words);
}

export async function collectTrends(page, request, config, deadline, access) {
  const useExplore = config.environment === 'public_ui' || config.fixture_trends_new_explore;
  const exploreUrl = config.environment === 'fixture'
    ? new URL(useExplore ? '/explore?hl=ja' : '/trends/',config.fixture_url)
    : new URL('https://trends.google.co.jp/explore?hl=ja');
  const exploreCheck = guardPage(page,exploreUrl.hostname,access);
  if (deadline - Date.now() < 1000) throw new SourceFailure('source_timeout');
  if (!useExplore) await installTrendsWidgetObserver(page);
  trace(useExplore ? 'trends_explore_navigation' : 'trends_home_navigation');
  await page.goto(exploreUrl.href,{waitUntil:'commit'});
  trace(useExplore ? 'trends_explore_commit_observed' : 'trends_home_commit_observed');
  await exploreCheck();

  if (useExplore) {
    const search = page.locator('input[role="combobox"][aria-label="検索キーワードを追加"]');
    let modernExplore = false;
    try {
      await search.waitFor({state:'visible',timeout:Math.min(15000,Math.max(1,deadline-Date.now()))});
      modernExplore = true;
    } catch (error) {
      if (error?.name !== 'TimeoutError') throw error;
    }
    await exploreCheck();
    if (modernExplore) {
      trace('trends_explore_page_ready');
      return await collectExploreTrends(page,request,deadline,exploreCheck);
    }
    if (config.environment !== 'public_ui')
      throw new SourceFailure('query_widget_contract_mismatch');
    await installTrendsWidgetObserver(page);
    const homeUrl = new URL('https://trends.google.co.jp/trends/');
    trace('trends_home_navigation');
    await page.goto(homeUrl.href,{waitUntil:'commit'});
    trace('trends_home_commit_observed');
    const homeCheck = guardPage(page,homeUrl.hostname,access);
    await homeCheck();
    return await collectLegacyTrends(page,request,config,deadline,homeCheck);
  }

  return await collectLegacyTrends(page,request,config,deadline,exploreCheck);
}

export async function main(input) {
  validateInput(input);
  const {config,request,kind} = input;
  const timeoutMs = config.timeout_seconds === null ? Infinity : config.timeout_seconds*1000;
  const fixtureDeadline = config.fixture_request_deadline_ms || timeoutMs;
  const configuredDeadline = Date.now()+Math.min(timeoutMs,fixtureDeadline);
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
    page.setDefaultTimeout(Math.min(10000,timeoutMs));
    page.setDefaultNavigationTimeout(Math.min(30000,timeoutMs));
    if (kind === 'room') return await collectRoom(page,request,config,deadline,access);
    if (kind === 'product_page') {
      const response = await collectProductPage(page,request,config,deadline,access);
      return {response,product_facts:{},diagnostics:{source:'public_dom'}};
    }
    return await collectTrends(page,request,config,deadline,access);
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
      elapsed_ms:Math.max(0,Date.now()-processStartedAt),
      error_code:errorCode,
      error_class:error instanceof SourceFailure ? 'SourceFailure' : safeThrownErrorClass(error),
      error_kind:safeErrorKind(error),
      scope:'query',
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
