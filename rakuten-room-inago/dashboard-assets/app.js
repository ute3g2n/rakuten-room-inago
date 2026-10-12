const API_STATE = '/api/state';
const API_REPORTS = '/api/reports';

export function safeProductUrl(value) {
  if (typeof value !== 'string' || !value || value.trim() !== value
    || /[\u0000-\u001f\u007f]/u.test(value)) return null;
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase().replace(/\.$/u, '');
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password
      || (parsed.port && parsed.port !== '443')
      || host === 'localhost' || host.endsWith('.localhost')
      || host.endsWith('.local') || /^\[|^(?:\d{1,3}\.){3}\d{1,3}$/u.test(host)) {
      return null;
    }
    parsed.hash = '';
    return parsed.href;
  } catch {
    return null;
  }
}

export function messageForApiError(code) {
  const messages = {
    origin_rejected: '保存元を確認できませんでした。公式のローカル画面から開き直してください。',
    csrf_rejected: '画面の確認情報が古くなりました。再読み込みしてください。',
    stale_revision: '別の報告が先に受け付けられました。状態を再読み込みして確認してください。',
    action_id_conflict: '同じ報告IDで異なる内容が見つかりました。再読み込みしてください。',
    storage_unavailable: '報告を保存できませんでした。保存状態を確認してから再試行してください。',
    invalid_report: '入力を保存できませんでした。選択内容と必須項目を確認してください。',
    not_found: '保存先が見つかりません。画面を再読み込みしてください。',
    connection_unavailable: '保存サーバーに接続できません。報告操作は無効です。',
  };
  return messages[code] || '保存できませんでした。状態を確認してから再試行してください。';
}

export function createClickHandler({ getState, clipboard, onReport, setMessage }) {
  return async (event) => {
    const button = event.target?.closest?.('button[data-copy], button[data-report-action]');
    if (!button) return;
    const state = getState();
    if (button.dataset.copy) {
      const item = state?.items?.find((entry) => entry.offer_key === button.dataset.copy);
      if (!item) {
        setMessage('コピー対象の本文を読み込めません。画面を再読み込みしてください。');
        return;
      }
      try {
        await clipboard.writeText(item.body);
        setMessage('本文をコピーしました。投稿報告は送信していません。');
      } catch {
        setMessage('コピーできませんでした。本文を手動で選択してください。投稿報告は送信していません。');
      }
      return;
    }
    if (button.dataset.reportAction && button.dataset.offerKey) {
      await onReport(button.dataset.reportAction, button.dataset.offerKey, button);
    }
  };
}

function reportPayload(item, action, article, revision) {
  const reason = article.querySelector('[data-reason]')?.value?.trim() || null;
  const correctionRef = article.querySelector('[data-correction-ref]')?.value || null;
  const localPostedAt = article.querySelector('[data-actual-posted-at]')?.value || '';
  if (action === 'correction' && (!reason || !correctionRef)) {
    throw new Error('訂正理由と対象の投稿記録を選んでください。');
  }
  const parsedPostedAt = localPostedAt ? new Date(localPostedAt) : null;
  if (parsedPostedAt && Number.isNaN(parsedPostedAt.getTime())) {
    throw new Error('投稿時刻の形式を確認してください。');
  }
  const actualPostedAt = parsedPostedAt ? parsedPostedAt.toISOString() : null;
  const actionId = globalThis.crypto?.randomUUID
    ? `ACT-${globalThis.crypto.randomUUID()}`
    : `ACT-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return {
    action_id: actionId,
    offer_key: item.offer_key,
    draft_id: item.draft_id,
    draft_version: item.draft_version,
    expected_receipt_revision: revision,
    action,
    reason: action === 'correction' ? reason : null,
    correction_ref: action === 'correction' ? correctionRef : null,
    actual_posted_at: action === 'posted' ? actualPostedAt : null,
  };
}

async function postReport(payload, csrfToken, fetchImpl) {
  let response;
  try {
    response = await fetchImpl(API_REPORTS, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error(messageForApiError('connection_unavailable'));
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.saved !== true) {
    throw new Error(messageForApiError(result.error));
  }
  return result;
}

function addTextElement(documentRef, parent, tag, text, className = '') {
  const element = documentRef.createElement(tag);
  element.textContent = text == null ? '' : String(text);
  if (className) element.className = className;
  parent.append(element);
  return element;
}

function clear(element) {
  while (element.firstChild) element.removeChild(element.firstChild);
}

function renderReportList(documentRef, list, reports) {
  clear(list);
  if (!reports.length) {
    addTextElement(documentRef, list, 'li', '記録はありません。', 'muted');
    return;
  }
  for (const report of reports) {
    const item = documentRef.createElement('li');
    item.className = 'report-entry';
    const title = `${report.product_name}・下書き ${report.draft_version}・${report.action_label}`;
    addTextElement(documentRef, item, 'strong', title);
    addTextElement(documentRef, item, 'span', report.apply_status_label, 'status-label');
    addTextElement(documentRef, item, 'span', `受付 ${report.reported_at}`, 'muted');
    if (report.applied_at) addTextElement(documentRef, item, 'span', `反映 ${report.applied_at}`, 'muted');
    if (report.actual_posted_at) addTextElement(documentRef, item, 'span', `本人申告の投稿時刻 ${report.actual_posted_at}`, 'muted');
    if (report.reason) addTextElement(documentRef, item, 'p', report.reason, 'report-reason');
    if (report.apply_error) addTextElement(documentRef, item, 'span', `反映エラー: ${report.apply_error}`, 'error-text');
    list.append(item);
  }
}

function renderItem(documentRef, item, state) {
  const article = documentRef.createElement('article');
  article.className = 'product-card';
  article.dataset.offerKey = item.offer_key;
  addTextElement(documentRef, article, 'p', item.draft_status_label, 'status-label');
  addTextElement(documentRef, article, 'h3', item.product_name);
  addTextElement(documentRef, article, 'p', `下書き ${item.draft_version}・型 ${item.pattern_id}（${item.pattern_version}）・設定 ${item.settings_version}`, 'muted');
  const safeUrl = safeProductUrl(item.product_url);
  if (safeUrl) {
    const link = documentRef.createElement('a');
    link.href = safeUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = '商品ページを開く';
    article.append(link);
  } else {
    addTextElement(documentRef, article, 'p', '商品URLを安全に確認できないため、リンクを表示していません。', 'muted');
  }
  const body = documentRef.createElement('textarea');
  body.readOnly = true;
  body.rows = Math.min(12, Math.max(4, Math.ceil(item.body.length / 44)));
  body.setAttribute('aria-label', `${item.product_name}の保存済み本文`);
  body.value = item.body;
  article.append(body);
  const controls = documentRef.createElement('div');
  controls.className = 'action-row';
  const copy = documentRef.createElement('button');
  copy.type = 'button';
  copy.dataset.copy = item.offer_key;
  copy.textContent = '本文をコピー';
  controls.append(copy);
  const pending = state.pending_reports.find((report) => report.offer_key === item.offer_key);
  if (pending) {
    addTextElement(documentRef, controls, 'span', '報告受付済み・翌日管理反映待ち', 'status-label');
  } else {
    for (const [action, label] of [['posted', '投稿したと報告'], ['skipped', '見送ったと報告']]) {
      const button = documentRef.createElement('button');
      button.type = 'button';
      button.dataset.reportAction = action;
      button.dataset.offerKey = item.offer_key;
      button.textContent = label;
      controls.append(button);
    }
  }
  article.append(controls);

  const timeLabel = addTextElement(documentRef, article, 'label', '本人が申告する投稿時刻（任意）');
  const postedAt = documentRef.createElement('input');
  postedAt.type = 'datetime-local';
  postedAt.dataset.actualPostedAt = 'true';
  timeLabel.append(postedAt);
  const revision = state.receipt_revisions[item.offer_key] ?? 0;
  article.dataset.receiptRevision = String(revision);

  if (item.events.length) {
    const details = documentRef.createElement('details');
    const summary = addTextElement(documentRef, details, 'summary', '既存の投稿記録を訂正する');
    summary.className = 'correction-summary';
    addTextElement(documentRef, details, 'label', '訂正する投稿記録');
    const select = documentRef.createElement('select');
    select.dataset.correctionRef = 'true';
    const empty = documentRef.createElement('option');
    empty.value = '';
    empty.textContent = '投稿記録を選択';
    select.append(empty);
    for (const event of item.events) {
      const option = documentRef.createElement('option');
      option.value = event.event_id;
      option.textContent = `${event.event_id}・${event.actual_posted_at || '時刻不明'}`;
      select.append(option);
    }
    details.append(select);
    addTextElement(documentRef, details, 'label', '訂正理由');
    const reason = documentRef.createElement('textarea');
    reason.rows = 3;
    reason.dataset.reason = 'true';
    details.append(reason);
    const correct = documentRef.createElement('button');
    correct.type = 'button';
    correct.dataset.reportAction = 'correction';
    correct.dataset.offerKey = item.offer_key;
    correct.textContent = '訂正報告を受付';
    details.append(correct);
    article.append(details);
  }
  return article;
}

export function renderDashboardState(root, state) {
  const documentRef = root.ownerDocument;
  root.querySelector('[data-available-count]').textContent = String(state.summary.available_count);
  root.querySelector('[data-generated-count]').textContent = String(state.summary.generated_count_total);
  root.querySelector('[data-posted-count]').textContent = String(state.summary.reported_posted_count);
  root.querySelector('[data-pending-count]').textContent = String(state.summary.pending_report_count);
  root.querySelector('[data-settings-version]').textContent = state.latest_run
    ? `日次設定版 ${state.latest_run.settings_version}`
    : '日次実行記録はありません。';
  const productList = root.querySelector('#product-list');
  clear(productList);
  if (!state.items.length) addTextElement(documentRef, productList, 'p', '現在使える下書きはありません。', 'muted');
  for (const item of state.items) productList.append(renderItem(documentRef, item, state));
  const excludedList = root.querySelector('#do-not-post-list');
  clear(excludedList);
  if (!state.do_not_post_drafts.length) {
    addTextElement(documentRef, excludedList, 'li', '既投稿により対象外となった後続版はありません。', 'muted');
  }
  for (const draft of state.do_not_post_drafts) {
    const entry = documentRef.createElement('li');
    entry.className = 'report-entry';
    addTextElement(documentRef, entry, 'strong', `${draft.product_name}・下書き ${draft.draft_version}`);
    addTextElement(documentRef, entry, 'span', draft.draft_status_label, 'status-label');
    addTextElement(documentRef, entry, 'span', `設定 ${draft.settings_version}・日次 ${draft.target_date}`, 'muted');
    addTextElement(documentRef, entry, 'span', '投稿・コピー・報告操作の対象外です。', 'muted');
    excludedList.append(entry);
  }
  renderReportList(documentRef, root.querySelector('#pending-reports'), state.pending_reports);
  renderReportList(documentRef, root.querySelector('#recent-reports'), state.recent_reports);
  const metrics = state.metrics;
  const formatTotal = (value, unit) => {
    if (value.known_total === null) return `未取得（欠測 ${value.missing_count}件）`;
    return `${value.known_total}${unit}（観測 ${value.observed_count}件・欠測 ${value.missing_count}件）`;
  };
  const daily = metrics.affiliate_daily;
  const lines = [
    `アフィリエイト日別（暫定）: クリック ${daily.clicks ? formatTotal(daily.clicks, '回') : '未取得'}、売上件数 ${daily.sales_count ? formatTotal(daily.sales_count, '件') : '未取得'}、売上金額 ${daily.sales_yen ? formatTotal(daily.sales_yen, '円') : '未取得'}、未確定手数料 ${daily.commission_unconfirmed_yen ? formatTotal(daily.commission_unconfirmed_yen, '円') : '未取得'}`,
    `公式注文CSV: 注文件数 ${formatTotal(metrics.order_count, '件')}、現行注文売上 ${metrics.orders_current_sales_yen === null ? '未取得または不明' : `${metrics.orders_current_sales_yen}円`}、未確定手数料 ${metrics.orders_current_commission_unconfirmed_yen === null ? '未取得または不明' : `${metrics.orders_current_commission_unconfirmed_yen}円`}、暫定参照 ${metrics.order_unresolved_count}件`,
    `本人ROOM投稿: 反応取得済み ${metrics.room_post_count}件、いいね合計 ${formatTotal(metrics.room_likes, '件')}、欠測 ${metrics.room_missing_count}件`,
  ];
  root.querySelector('#metrics-summary').textContent = lines.join('\n');
}

export function setDashboardOffline(root, message) {
  root.querySelector('#connection-status').textContent = message;
  for (const button of root.querySelectorAll('button[data-copy], button[data-report-action]')) {
    button.disabled = true;
  }
}

export async function startDashboard(root, { fetchImpl = fetch, clipboard = navigator.clipboard } = {}) {
  const connectionStatus = root.querySelector('#connection-status');
  const actionMessage = root.querySelector('#action-message');
  let state = null;
  const setMessage = (message) => { actionMessage.textContent = message; };
  const refresh = async () => {
    let response;
    try {
      response = await fetchImpl(API_STATE, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
    } catch {
      throw new Error(messageForApiError('connection_unavailable'));
    }
    const next = await response.json().catch(() => ({}));
    if (!response.ok || !Array.isArray(next.items) || !next.summary) {
      throw new Error(messageForApiError(next.error));
    }
    state = next;
    renderDashboardState(root, state);
    connectionStatus.textContent = '保存サーバーに接続しました。報告は受付後、翌日の日次処理で管理へ反映されます。';
  };
  const onReport = async (action, offerKey, button) => {
    const item = state?.items?.find((entry) => entry.offer_key === offerKey);
    if (!item) {
      setMessage('下書きの状態を確認できません。画面を再読み込みしてください。');
      return;
    }
    const article = button.closest('article[data-offer-key]');
    try {
      const payload = reportPayload(item, action, article, state.receipt_revisions[offerKey] ?? 0);
      await postReport(payload, state.csrf_token, fetchImpl);
      setMessage('報告を受け付けました。管理への反映は翌日の日次処理で行います。');
      try {
        await refresh();
      } catch {
        setMessage('報告は受付済みです。最新状態を読み込めませんでした。再読み込みして確認してください。');
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '報告を保存できませんでした。');
      if (String(error?.message).includes('再読み込み')) {
        await refresh().catch(() => {});
      }
    }
  };
  root.addEventListener('click', createClickHandler({
    getState: () => state,
    clipboard,
    onReport,
    setMessage,
  }));
  try {
    await refresh();
  } catch (error) {
    const message = error instanceof Error ? error.message : '保存サーバーに接続できません。';
    setDashboardOffline(root, `${message} 報告操作は無効です。ブラウザー保存は使用しません。`);
  }
}

if (typeof document !== 'undefined') {
  const root = document.querySelector('[data-dashboard-root]');
  if (root) startDashboard(root);
}
