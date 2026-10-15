"""Focused recovery contracts for file-origin reports and the HAPI task."""

from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_TASK_XML = _ROOT / "automation/inago/task-xml/RakutenRoomInagoHumanApi.xml"
_TASK_UPDATER = _ROOT / "automation/inago/configure_human_api_task.ps1"
_HISTORY_JS = _ROOT / "rakuten-room-inago/dashboard-assets/history.js"
_HISTORY_TEMPLATE = _ROOT / "automation/inago/dashboard_history.py"
_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_human_api_task_xml_sets_bounded_restart_policy() -> None:
    task = ET.parse(_TASK_XML).getroot()
    restart = task.find("t:Settings/t:RestartOnFailure", _NS)

    assert restart is not None
    assert restart.findtext("t:Interval", namespaces=_NS) == "PT1M"
    assert restart.findtext("t:Count", namespaces=_NS) == "3"
    assert task.find("t:Triggers/t:LogonTrigger", _NS) is not None
    assert task.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=_NS) == "PT0S"


def test_human_api_task_update_path_is_scoped_and_validates_registration() -> None:
    source = _TASK_UPDATER.read_text(encoding="utf-8")

    assert "RakutenRoomInagoHumanApi" in source
    assert "RakutenRoomInagoDaily" not in source
    assert "Get-ScheduledTask" in source
    assert "Register-ScheduledTask" in source
    assert "Interactive" in source
    assert "LeastPrivilege" in source or "Limited" in source
    assert "RestartOnFailure" in source
    assert "-Force" in source


def test_file_history_retries_and_flushes_persisted_outbox_after_api_recovers() -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the focused history-script behavior test"

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.INAGO_HISTORY_JS, 'utf8');

function element() {
  return {
    dataset: {}, textContent: '', hidden: false, children: [],
    replaceChildren(...children) { this.children = children; },
    querySelectorAll() { return []; },
    listeners: new Map(),
    addEventListener(name, callback) { this.listeners.set(name, callback); },
    setAttribute() {},
  };
}
const ids = new Map();
function getElement(id) {
  if (!ids.has(id)) ids.set(id, element());
  return ids.get(id);
}
getElement('dashboard-history-data').textContent = JSON.stringify({
  days: [], report_counts_by_date: [],
});
getElement('history-auth').hidden = true;
getElement('history-api-token').type = 'password';
const documentListeners = new Map();
const windowListeners = new Map();
let intervalCallback = null;
let intervalDelay = null;
let promptCount = 0;
let online = false;
let outboxRows = [{
  action_id: 'ACT-DURABLE-001',
  queued_at: '2026-10-16T00:00:00.000Z',
  report: {
    action_id: 'ACT-DURABLE-001', offer_key: 'shop_test:item_A01',
    draft_id: 'D-A01', draft_version: 1, expected_receipt_revision: 0,
    action: 'posted', reason: null, correction_ref: null, actual_posted_at: null,
  },
}];
const db = {
  objectStoreNames: { contains: () => true },
  transaction() {
    const tx = {
      objectStore() {
        return {
          getAll() { return { result: outboxRows.map((row) => structuredClone(row)) }; },
          put(row) {
            outboxRows = outboxRows.filter((entry) => entry.action_id !== row.action_id);
            outboxRows.push(structuredClone(row));
            return { result: row };
          },
          delete(id) {
            outboxRows = outboxRows.filter((entry) => entry.action_id !== id);
            return { result: undefined };
          },
        };
      },
    };
    setTimeout(() => tx.oncomplete?.(), 0);
    return tx;
  },
};
const indexedDB = {
  open() {
    const request = {};
    setTimeout(() => {
      request.result = db;
      request.onsuccess?.();
    }, 0);
    return request;
  },
};
let releaseFirstAuthenticatedGet;
let heldAuthenticatedGet = false;
const firstAuthenticatedGet = new Promise((resolve) => {
  releaseFirstAuthenticatedGet = resolve;
});
let authenticatedGetCount = 0;
const postIds = [];
function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}
async function fetch(url, options = {}) {
  const method = options.method || 'GET';
  if (!online) throw new TypeError('Failed to fetch');
  const authorization = options.headers?.Authorization;
  if (!authorization) return response(401, { error: 'authentication_required' });
  if (url.endsWith('/api/reports')) {
    const payload = JSON.parse(options.body);
    postIds.push(payload.action_id);
    return response(201, {
      saved: true, apply_status: 'pending', receipt_revision: 1,
    });
  }
  if (method === 'GET' && url.endsWith('/api/history-state')) {
    authenticatedGetCount += 1;
    if (!heldAuthenticatedGet) {
      heldAuthenticatedGet = true;
      await firstAuthenticatedGet;
    }
    return response(200, {
      days: [], report_counts_by_date: [], receipt_revisions: {},
      pending_reports: [], recent_reports: [], csrf_token: 'fixture-csrf',
    });
  }
  throw new Error(`Unexpected request: ${method} ${url}`);
}
const document = {
  documentElement: { dataset: { apiOrigin: 'http://127.0.0.1:40123' } },
  visibilityState: 'visible',
  getElementById: getElement,
  addEventListener(name, callback) { documentListeners.set(name, callback); },
};
const window = {
  prompt() { promptCount += 1; return 'fixture-bearer-token'; },
  setInterval(callback, delay) {
    assert.equal(intervalCallback, null, 'only one reconnect timer is allowed');
    intervalCallback = callback;
    intervalDelay = delay;
    return 17;
  },
  addEventListener(name, callback) { windowListeners.set(name, callback); },
};
const context = {
  document, window, indexedDB, fetch, setTimeout, clearTimeout,
  navigator: {}, URL, Blob, Date, Promise, TypeError, JSON, Math, String, Number,
  console, structuredClone,
};
vm.runInNewContext(source, context, { filename: 'history.js' });

const waitFor = async (predicate) => {
  const end = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > end) throw new Error('timed out waiting for history state');
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
};

(async () => {
  await waitFor(() => intervalCallback
    && getElement('outbox-count').textContent.includes('1件')
    && getElement('history-connection').dataset.historyConnection === 'unavailable');
  assert.equal(intervalDelay, 5000);
  assert.equal(getElement('history-connection').dataset.historyConnection, 'unavailable');

  online = true;
  const firstRetry = intervalCallback();
  const authForm = getElement('history-auth');
  const tokenInput = getElement('history-api-token');
  await waitFor(() => !authForm.hidden
    && getElement('history-connection').dataset.historyConnection === 'auth-required');
  await firstRetry;
  assert.equal(authenticatedGetCount, 0, 'unauthenticated requests do not load history state');
  assert.equal(promptCount, 0, 'native prompt() must not be required for authentication');
  tokenInput.value = 'fixture-bearer-token';
  getElement('history-auth-submit').listeners.get('click')();
  await waitFor(() => authenticatedGetCount === 1);
  const overlappingRetry = intervalCallback();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(authenticatedGetCount, 1, 'overlapping retry must share the in-flight refresh');
  releaseFirstAuthenticatedGet();
  await overlappingRetry;
  await waitFor(() => getElement('history-connection').dataset.historyConnection === 'ready');

  assert.equal(promptCount, 0);
  assert.deepEqual(postIds, ['ACT-DURABLE-001']);
  assert.deepEqual(outboxRows, []);
  assert.equal(getElement('outbox-count').textContent, '');
  assert.equal(windowListeners.has('online'), true);
  assert.equal(documentListeners.has('visibilitychange'), true);

  await intervalCallback();
  assert.deepEqual(postIds, ['ACT-DURABLE-001'], 'a confirmed action must not be posted twice');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    env = os.environ.copy()
    env["INAGO_HISTORY_JS"] = str(_HISTORY_JS)
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", script],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_file_history_persists_checkbox_report_without_api_origin() -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the focused history-script behavior test"

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.INAGO_HISTORY_JS, 'utf8');

class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName.toLowerCase();
    this.dataset = {};
    this.textContent = '';
    this.hidden = false;
    this.children = [];
    this.listeners = new Map();
    this.attributes = {};
    this.checked = false;
    this.disabled = false;
    this.value = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  get options() { return this.children; }
  focus() {}
  select() {}
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (selector === '.posted-checkbox:checked') return this.className === 'posted-checkbox' && this.checked;
    if (selector === 'input[data-report-posted-draft-id]') return this.tagName === 'input' && this.dataset.reportPostedDraftId !== undefined;
    if (selector === 'button[data-report-action="skipped"]') return this.tagName === 'button' && this.dataset.reportAction === 'skipped';
    if (selector === 'button[data-report-action="correction"]') return this.tagName === 'button' && this.dataset.reportAction === 'correction';
    if (selector === '[data-report-status]') return this.dataset.reportStatus !== undefined;
    if (selector === '[data-correction-ref]') return this.dataset.correctionRef !== undefined;
    if (selector === '[data-correction-reason]') return this.dataset.correctionReason !== undefined;
    if (selector === '[data-actual-posted-at]') return this.dataset.actualPostedAt !== undefined;
    if (selector.startsWith('article[data-draft-id=')) return this.tagName === 'article';
    return false;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((entry) => entry.trim());
    return this.descendants().filter((child) => selectors.some((entry) => child.matches(entry)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const ids = new Map();
function getElement(id) {
  if (!ids.has(id)) ids.set(id, new Element());
  return ids.get(id);
}
getElement('dashboard-history-data').textContent = JSON.stringify({
  days: [{
    target_date: '2026-10-16', run_status: 'complete', committed_count: 1,
    report_counts_by_date: [],
    items: [{
      status: '本文確定', target_date: '2026-10-16', draft_id: 'D-A01',
      draft_version: 1, offer_key: 'shop_test:item_A01', product_name: '試験商品',
      product_url: null, body: '試験投稿本文', receipt_revision: 0, events: [],
    }],
  }],
  report_counts_by_date: [],
});
const rows = [];
const db = {
  objectStoreNames: { contains: () => true },
  transaction() {
    const tx = {
      objectStore() {
        return {
          getAll() { return { result: structuredClone(rows) }; },
          put(row) {
            const index = rows.findIndex((entry) => entry.action_id === row.action_id);
            if (index >= 0) rows.splice(index, 1);
            rows.push(structuredClone(row));
            return { result: row };
          },
          delete(id) {
            const index = rows.findIndex((entry) => entry.action_id === id);
            if (index >= 0) rows.splice(index, 1);
            return { result: undefined };
          },
        };
      },
    };
    setTimeout(() => tx.oncomplete?.(), 0);
    return tx;
  },
};
const indexedDB = {
  open() {
    const request = {};
    setTimeout(() => { request.result = db; request.onsuccess?.(); }, 0);
    return request;
  },
};
const document = {
  documentElement: { dataset: { apiOrigin: '' } },
  visibilityState: 'visible',
  getElementById: getElement,
  createElement(tagName) { return new Element(tagName); },
  addEventListener() {},
};
const window = { prompt() { throw new Error('No prompt is expected for a post checkbox.'); } };
const context = {
  document, window, indexedDB, setTimeout, clearTimeout, navigator: {},
  URL, Blob, Date, Promise, TypeError, JSON, Math, String, Number,
  CSS: { escape: (value) => value }, structuredClone,
};
vm.runInNewContext(source, context, { filename: 'history.js' });

const waitFor = async (predicate) => {
  const end = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > end) throw new Error('timed out waiting for browser outbox');
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
};

(async () => {
  const list = getElement('draft-list');
  await waitFor(() => {
    const posted = list.querySelector('input[data-report-posted-draft-id]');
    return posted && !posted.disabled;
  });
  let checkbox = list.querySelector('input[data-report-posted-draft-id]');
  assert.equal(checkbox.disabled, false, 'a file page can report with browser storage alone');
  checkbox.checked = true;
  checkbox.listeners.get('change')();
  await waitFor(() => rows.length === 1 && getElement('outbox-count').textContent.includes('1件'));

  assert.equal(rows[0].report.action, 'posted');
  assert.equal(rows[0].report.offer_key, 'shop_test:item_A01');
  assert.equal(rows[0].report.draft_id, 'D-A01');
  assert.equal(rows[0].report.draft_version, 1);
  assert.equal(getElement('history-connection').dataset.historyConnection, 'outbox');
  checkbox = list.querySelector('input[data-report-posted-draft-id]');
  assert.equal(checkbox.checked, true, 'the checked state is retained while SQLite has not accepted it');
  assert.equal(checkbox.disabled, true, 'an unacknowledged action cannot be overwritten');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    env = os.environ.copy()
    env["INAGO_HISTORY_JS"] = str(_HISTORY_JS)
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", script],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_history_authentication_uses_an_ephemeral_inline_token_form() -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the focused history-script behavior test"

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.INAGO_HISTORY_JS, 'utf8');
function element() {
  return {
    dataset: {}, textContent: '', hidden: false, value: '', type: '',
    children: [], listeners: new Map(),
    replaceChildren(...children) { this.children = children; },
    querySelectorAll() { return []; },
    addEventListener(name, callback) { this.listeners.set(name, callback); },
    setAttribute() {}, focus() {},
  };
}
const ids = new Map();
function getElement(id) {
  if (!ids.has(id)) ids.set(id, element());
  return ids.get(id);
}
getElement('dashboard-history-data').textContent = JSON.stringify({
  days: [], report_counts_by_date: [],
});
getElement('history-auth').hidden = true;
getElement('history-api-token').type = 'password';
const outboxRows = [];
const db = {
  objectStoreNames: { contains: () => true },
  transaction() {
    const tx = {
      objectStore() {
        return {
          getAll() { return { result: structuredClone(outboxRows) }; },
          put(row) { outboxRows.push(structuredClone(row)); return { result: row }; },
          delete() { return { result: undefined }; },
        };
      },
    };
    setTimeout(() => tx.oncomplete?.(), 0);
    return tx;
  },
};
const indexedDB = {
  open() {
    const request = {};
    setTimeout(() => { request.result = db; request.onsuccess?.(); }, 0);
    return request;
  },
};
let promptCount = 0;
let authenticatedRequests = 0;
function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}
async function fetch(url, options = {}) {
  if (!url.endsWith('/api/history-state')) throw new Error(`Unexpected request: ${url}`);
  if (options.headers?.Authorization === 'Bearer fixture-token') {
    authenticatedRequests += 1;
    return response(200, {
      days: [], report_counts_by_date: [], receipt_revisions: {},
      pending_reports: [], recent_reports: [], csrf_token: 'fixture-csrf',
    });
  }
  return response(401, { error: 'authentication_required' });
}
const documentListeners = new Map();
const document = {
  documentElement: { dataset: { apiOrigin: 'http://127.0.0.1:40123' } },
  visibilityState: 'visible', getElementById: getElement,
  createElement() { return element(); },
  addEventListener(name, callback) { documentListeners.set(name, callback); },
};
const windowListeners = new Map();
const window = {
  prompt() { promptCount += 1; return 'fixture-token'; },
  setInterval() { return 17; },
  addEventListener(name, callback) { windowListeners.set(name, callback); },
};
const context = {
  document, window, indexedDB, fetch, setTimeout, clearTimeout,
  navigator: {}, URL, Blob, Date, Promise, TypeError, JSON, Math, String, Number,
  console, structuredClone,
};
vm.runInNewContext(source, context, { filename: 'history.js' });
const waitFor = async (predicate) => {
  const end = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > end) throw new Error('timed out waiting for authentication UI');
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
};
(async () => {
  const authForm = getElement('history-auth');
  const tokenInput = getElement('history-api-token');
  const connection = getElement('history-connection');
  await waitFor(() => !authForm.hidden || connection.dataset.historyConnection === 'ready');
  assert.equal(promptCount, 0, 'the page must not depend on native prompt()');
  assert.equal(authForm.hidden, false, '401 must expose the in-page token form');
  assert.equal(tokenInput.type, 'password');
  tokenInput.value = 'bad-token';
  await getElement('history-auth-submit').listeners.get('click')?.();
  await waitFor(() => authForm.hidden === false
    && connection.dataset.historyConnection === 'auth-required');
  assert.equal(tokenInput.value, '', 'a rejected token must be cleared from the form');
  tokenInput.value = 'fixture-token';
  await getElement('history-auth-submit').listeners.get('click')?.();
  await waitFor(() => connection.dataset.historyConnection === 'ready');
  assert.equal(authenticatedRequests, 1);
  assert.equal(tokenInput.value, '', 'the form must clear the token after submission');
  assert.deepEqual(outboxRows, [], 'authentication data must not enter browser storage');
  assert.equal(promptCount, 0);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    env = os.environ.copy()
    env["INAGO_HISTORY_JS"] = str(_HISTORY_JS)
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", script],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    template = _HISTORY_TEMPLATE.read_text(encoding="utf-8")
    assert 'id="history-auth"' in template
    assert 'id="history-api-token" type="password"' in template
    assert 'id="history-auth-submit"' in template
    assert ".auth-controls[hidden] { display: none; }" in template


def test_history_checkbox_off_posts_correction_without_reason_input() -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the focused history-script behavior test"

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.INAGO_HISTORY_JS, 'utf8');
class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName.toLowerCase(); this.dataset = {}; this.textContent = '';
    this.hidden = false; this.children = []; this.listeners = new Map();
    this.attributes = {}; this.checked = false; this.disabled = false;
    this.value = ''; this.open = false; this.className = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  get options() { return this.children; }
  focus() {}
  select() {}
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (selector === '.posted-checkbox:checked') return this.className === 'posted-checkbox' && this.checked;
    if (selector === 'input[data-report-posted-draft-id]') return this.tagName === 'input' && this.dataset.reportPostedDraftId !== undefined;
    if (selector === 'button[data-report-action="skipped"]') return this.tagName === 'button' && this.dataset.reportAction === 'skipped';
    if (selector === 'button[data-report-action="correction"]') return this.tagName === 'button' && this.dataset.reportAction === 'correction';
    if (selector === 'button[data-report-action]') return this.tagName === 'button' && this.dataset.reportAction !== undefined;
    if (selector === 'button[data-target-date]') return this.tagName === 'button' && this.dataset.targetDate !== undefined;
    if (selector === '[data-report-status]') return this.dataset.reportStatus !== undefined;
    if (selector === '[data-correction-ref]') return this.dataset.correctionRef !== undefined;
    if (selector === '[data-correction-reason]') return this.dataset.correctionReason !== undefined;
    if (selector === '[data-actual-posted-at]') return this.dataset.actualPostedAt !== undefined;
    if (selector === 'details.correction-controls') return this.tagName === 'details' && this.className === 'correction-controls';
    if (selector.startsWith('article[data-draft-id=')) return this.tagName === 'article';
    return false;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((entry) => entry.trim());
    return this.descendants().filter((child) => selectors.some((entry) => child.matches(entry)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const ids = new Map();
function getElement(id) {
  if (!ids.has(id)) ids.set(id, new Element());
  return ids.get(id);
}
const offerKey = 'shop_test:item_A01';
const postedAction = {
  action_id: 'ACT-POSTED-001', offer_key: offerKey, draft_id: 'D-A01',
  draft_version: 1, expected_receipt_revision: 0, receipt_revision: 1,
  action: 'posted', reason: null, correction_ref: null, actual_posted_at: null,
  apply_status: 'pending',
};
const item = {
  status: '本文確定', target_date: '2026-10-16', draft_id: 'D-A01',
  draft_version: 1, offer_key: offerKey, product_name: '匿名fixture商品',
  product_url: 'https://example.invalid/items/A01', body: '匿名fixture本文',
  receipt_revision: 1, events: [],
};
const day = {
  target_date: '2026-10-16', run_status: 'complete', committed_count: 1,
  items: [item],
};
getElement('dashboard-history-data').textContent = JSON.stringify({
  days: [day], report_counts_by_date: [],
});
const outboxRows = [];
const db = {
  objectStoreNames: { contains: () => true },
  transaction() {
    const tx = {
      objectStore() {
        return {
          getAll() { return { result: structuredClone(outboxRows) }; },
          put(row) { outboxRows.push(structuredClone(row)); return { result: row }; },
          delete(id) {
            const index = outboxRows.findIndex((row) => row.action_id === id);
            if (index >= 0) outboxRows.splice(index, 1);
            return { result: undefined };
          },
        };
      },
    };
    setTimeout(() => tx.oncomplete?.(), 0);
    return tx;
  },
};
const indexedDB = {
  open() {
    const request = {};
    setTimeout(() => { request.result = db; request.onsuccess?.(); }, 0);
    return request;
  },
};
let promptCount = 0;
const reports = [postedAction];
const correctionPosts = [];
function historyState() {
  return {
    days: [day], report_counts_by_date: [],
    receipt_revisions: { [offerKey]: reports.length },
    pending_reports: reports, recent_reports: [], csrf_token: 'fixture-csrf',
  };
}
function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}
async function fetch(url, options = {}) {
  if (url.endsWith('/api/history-state')) return response(200, historyState());
  if (url.endsWith('/api/reports')) {
    const report = JSON.parse(options.body);
    correctionPosts.push(report);
    reports.push({ ...report, receipt_revision: reports.length + 1, apply_status: 'pending' });
    return response(201, { saved: true, apply_status: 'pending', receipt_revision: reports.length });
  }
  throw new Error(`Unexpected request: ${url}`);
}
const document = {
  documentElement: { dataset: { apiOrigin: 'http://127.0.0.1:40123' } },
  visibilityState: 'visible', getElementById: getElement,
  createElement(tagName) { return new Element(tagName); },
  addEventListener() {},
};
const window = {
  prompt() { promptCount += 1; return null; },
  setInterval() { return 17; }, addEventListener() {},
};
const context = {
  document, window, indexedDB, fetch, setTimeout, clearTimeout,
  navigator: {}, URL, Blob, Date, Promise, TypeError, JSON, Math, String, Number,
  console, CSS: { escape: (value) => value }, structuredClone,
};
vm.runInNewContext(source, context, { filename: 'history.js' });
const waitFor = async (predicate) => {
  const end = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > end) throw new Error('timed out waiting for correction state');
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
};
(async () => {
  const list = getElement('draft-list');
  await waitFor(() => {
    const checkbox = list.querySelector('input[data-report-posted-draft-id]');
    return checkbox && !checkbox.disabled
      && getElement('history-connection').dataset.historyConnection === 'ready';
  });
  let checkbox = list.querySelector('input[data-report-posted-draft-id]');
  assert.equal(checkbox.checked, true);
  checkbox.checked = false;
  checkbox.listeners.get('change')();
  assert.equal(promptCount, 0, 'checkbox correction must not depend on native prompt()');
  await waitFor(() => correctionPosts.length === 1
    && getElement('history-message').textContent === 'SQLite受付済み・翌日の日次管理反映待ち'
    && list.querySelector('input[data-report-posted-draft-id]')?.checked === false);
  const card = list.children[0];
  const details = card.querySelector('details.correction-controls');
  assert.equal(promptCount, 0);
  assert.equal(details.open, false, 'OFF sends directly without opening the reason form');
  assert.equal(correctionPosts[0].action, 'correction');
  assert.equal(correctionPosts[0].reason, '投稿済みチェック解除（本人操作）');
  assert.equal(correctionPosts[0].correction_ref, 'ACT-POSTED-001');
  assert.equal(correctionPosts[0].expected_receipt_revision, 1);
  assert.equal(getElement('posted-count').textContent, '0');
  assert.equal(getElement('history-message').textContent, 'SQLite受付済み・翌日の日次管理反映待ち');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    env = os.environ.copy()
    env["INAGO_HISTORY_JS"] = str(_HISTORY_JS)
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", script],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_history_refresh_preserves_open_correction_form_input() -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the focused history-script behavior test"

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.INAGO_HISTORY_JS, 'utf8');
class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName.toLowerCase(); this.dataset = {}; this.textContent = '';
    this.hidden = false; this.children = []; this.listeners = new Map();
    this.attributes = {}; this.checked = false; this.disabled = false;
    this.value = ''; this.open = false; this.className = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  get options() { return this.children; }
  focus() {}
  select() {}
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (selector === '.posted-checkbox:checked') return this.className === 'posted-checkbox' && this.checked;
    if (selector === 'input[data-report-posted-draft-id]') return this.tagName === 'input' && this.dataset.reportPostedDraftId !== undefined;
    if (selector === 'button[data-report-action="skipped"]') return this.tagName === 'button' && this.dataset.reportAction === 'skipped';
    if (selector === 'button[data-report-action="correction"]') return this.tagName === 'button' && this.dataset.reportAction === 'correction';
    if (selector === 'button[data-report-action]') return this.tagName === 'button' && this.dataset.reportAction !== undefined;
    if (selector === 'button[data-target-date]') return this.tagName === 'button' && this.dataset.targetDate !== undefined;
    if (selector === '[data-report-status]') return this.dataset.reportStatus !== undefined;
    if (selector === '[data-correction-ref]') return this.dataset.correctionRef !== undefined;
    if (selector === '[data-correction-reason]') return this.dataset.correctionReason !== undefined;
    if (selector === '[data-actual-posted-at]') return this.dataset.actualPostedAt !== undefined;
    if (selector === 'details.correction-controls') return this.tagName === 'details' && this.className === 'correction-controls';
    if (selector.startsWith('article[data-draft-id=')) return this.tagName === 'article';
    return false;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((entry) => entry.trim());
    return this.descendants().filter((child) => selectors.some((entry) => child.matches(entry)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const ids = new Map();
function getElement(id) {
  if (!ids.has(id)) ids.set(id, new Element());
  return ids.get(id);
}
const offerKey = 'shop_test:item_A01';
const postedAction = {
  action_id: 'ACT-POSTED-001', offer_key: offerKey, draft_id: 'D-A01',
  draft_version: 1, expected_receipt_revision: 0, receipt_revision: 1,
  action: 'posted', reason: null, correction_ref: null, actual_posted_at: null,
  apply_status: 'pending',
};
const day = {
  target_date: '2026-10-16', run_status: 'complete', committed_count: 1,
  items: [{
    status: '本文確定', target_date: '2026-10-16', draft_id: 'D-A01',
    draft_version: 1, offer_key: offerKey, product_name: '匿名fixture商品',
    product_url: 'https://example.invalid/items/A01', body: '匿名fixture本文',
    receipt_revision: 1, events: [],
  }],
};
getElement('dashboard-history-data').textContent = JSON.stringify({
  days: [day], report_counts_by_date: [],
});
const outboxRows = [];
const db = {
  objectStoreNames: { contains: () => true },
  transaction() {
    const tx = {
      objectStore() {
        return {
          getAll() { return { result: structuredClone(outboxRows) }; },
          put(row) { outboxRows.push(structuredClone(row)); return { result: row }; },
          delete() { return { result: undefined }; },
        };
      },
    };
    setTimeout(() => tx.oncomplete?.(), 0);
    return tx;
  },
};
const indexedDB = {
  open() {
    const request = {};
    setTimeout(() => { request.result = db; request.onsuccess?.(); }, 0);
    return request;
  },
};
let intervalCallback = null;
let stateReads = 0;
let reportPosts = 0;
let currentReports = [postedAction];
function historyState() {
  return {
    days: [day], report_counts_by_date: [],
    receipt_revisions: { [offerKey]: currentReports[0]?.receipt_revision || 1 },
    pending_reports: currentReports,
    recent_reports: [], csrf_token: 'fixture-csrf',
  };
}
function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}
async function fetch(url) {
  if (url.endsWith('/api/history-state')) {
    stateReads += 1;
    return response(200, historyState());
  }
  if (url.endsWith('/api/reports')) {
    reportPosts += 1;
    return response(201, { saved: true, apply_status: 'pending', receipt_revision: 2 });
  }
  throw new Error(`Unexpected request: ${url}`);
}
const document = {
  documentElement: { dataset: { apiOrigin: 'http://127.0.0.1:40123' } },
  visibilityState: 'visible', getElementById: getElement,
  createElement(tagName) { return new Element(tagName); }, addEventListener() {},
};
const window = {
  prompt() { throw new Error('native prompt() must not be called'); },
  setInterval(callback) { intervalCallback = callback; return 17; },
  addEventListener() {},
};
const context = {
  document, window, indexedDB, fetch, setTimeout, clearTimeout,
  navigator: {}, URL, Blob, Date, Promise, TypeError, JSON, Math, String, Number,
  console, CSS: { escape: (value) => value }, structuredClone,
};
vm.runInNewContext(source, context, { filename: 'history.js' });
const waitFor = async (predicate) => {
  const end = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > end) throw new Error('timed out waiting for history refresh');
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
};
(async () => {
  const list = getElement('draft-list');
  await waitFor(() => intervalCallback && stateReads === 1
    && getElement('history-connection').dataset.historyConnection === 'ready');
  let card = list.children[0];
  let checkbox = card.querySelector('input[data-report-posted-draft-id]');
  assert.equal(checkbox.checked, true);
  let details = card.querySelector('details.correction-controls');
  let correctionRef = card.querySelector('[data-correction-ref]');
  let reason = card.querySelector('[data-correction-reason]');
  let actualPostedAt = card.querySelector('[data-actual-posted-at]');
  details.open = true;
  assert.equal(details.open, true);
  correctionRef.value = 'ACT-POSTED-001';
  reason.value = 'poll中も消えてはいけない理由';
  actualPostedAt.value = '2026-10-16T21:30';

  intervalCallback();
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.equal(stateReads, 1, 'poll must pause while the correction form is open');
  assert.equal(list.children[0], card, 'the active card must not be re-rendered while editing');
  details.open = false;
  intervalCallback();
  await waitFor(() => stateReads === 2);
  await new Promise((resolve) => setTimeout(resolve, 15));
  card = list.children[0];
  checkbox = card.querySelector('input[data-report-posted-draft-id]');
  details = card.querySelector('details.correction-controls');
  correctionRef = card.querySelector('[data-correction-ref]');
  reason = card.querySelector('[data-correction-reason]');
  actualPostedAt = card.querySelector('[data-actual-posted-at]');
  assert.equal(details.open, false, 'poll must preserve the user-closed state');
  assert.equal(correctionRef.value, 'ACT-POSTED-001');
  assert.equal(reason.value, 'poll中も消えてはいけない理由');
  assert.equal(actualPostedAt.value, '2026-10-16T21:30');
  assert.equal(checkbox.checked, true, 'server posted state remains authoritative before receipt');
  assert.equal(getElement('posted-count').textContent, '1');
  assert.equal(reportPosts, 0, 'a refresh must never submit the unfinished correction');

  currentReports = [{
    action_id: 'ACT-CORRECTION-002', offer_key: offerKey, draft_id: 'D-A01',
    draft_version: 1, expected_receipt_revision: 1, receipt_revision: 2,
    action: 'correction', reason: 'fixture correction',
    correction_ref: 'ACT-POSTED-001', actual_posted_at: null,
    apply_status: 'pending',
  }, postedAction];
  intervalCallback();
  await waitFor(() => stateReads === 3);
  await new Promise((resolve) => setTimeout(resolve, 15));
  card = list.children[0];
  checkbox = card.querySelector('input[data-report-posted-draft-id]');
  details = card.querySelector('details.correction-controls');
  reason = card.querySelector('[data-correction-reason]');
  assert.equal(checkbox.checked, false, 'new server receipt overrides the old posted checkbox');
  assert.equal(getElement('posted-count').textContent, '0');
  assert.equal(details.open, false);
  assert.equal(reason.value, '', 'stale correction text is not restored after server state changes');
  assert.equal(reportPosts, 0);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    env = os.environ.copy()
    env["INAGO_HISTORY_JS"] = str(_HISTORY_JS)
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", script],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
