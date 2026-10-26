(() => {
  const API_ORIGIN = document.documentElement.dataset.apiOrigin || "";
  const HTTP_HISTORY = document.documentElement.dataset.historyTransport === "http";
  let data = JSON.parse(document.getElementById("dashboard-history-data").textContent);
  const navigation = document.getElementById("date-navigation");
  const selectedDate = document.getElementById("selected-date");
  const runStatus = document.getElementById("run-status");
  const dailyCount = document.getElementById("daily-count");
  const committedCount = document.getElementById("committed-count");
  const postedCount = document.getElementById("posted-count");
  const sqlitePendingCount = document.getElementById("sqlite-pending-count");
  const sqliteAppliedCount = document.getElementById("sqlite-applied-count");
  const sqliteRejectedCount = document.getElementById("sqlite-rejected-count");
  const dayOutboxCount = document.getElementById("browser-outbox-day-count");
  const list = document.getElementById("draft-list");
  const message = document.getElementById("history-message");
  const connectionStatus = document.getElementById("history-connection");
  const authForm = document.getElementById("history-auth");
  const authTokenInput = document.getElementById("history-api-token");
  const authSubmitButton = document.getElementById("history-auth-submit");
  const reconnectButton = document.getElementById("history-reconnect");
  const outboxButton = document.getElementById("export-outbox");
  const outboxCount = document.getElementById("outbox-count");
  const RECONNECT_INTERVAL_MS = 5000;
  let activeDate = null;
  let apiState = null;
  let authToken = null;
  let authRevision = 0;
  let authRequired = false;
  let outboxDb = null;
  let outboxEntries = [];
  let reconnectTimer = null;
  let refreshInFlight = null;
  let reportSubmissionsInFlight = 0;
  let historyEventSource = null;
  let historyUpdatePending = false;
  let historyUpdateRunning = false;
  let outboxFlushInFlight = null;
  const acknowledgedReports = new Map();
  const combinedActionsInFlight = new Set();

  function updatePostedCount() {
    postedCount.textContent = String(list.querySelectorAll(".posted-checkbox:checked").length);
  }

  function updateDayReportCounts(day) {
    const reportCounts = (data.report_counts_by_date || []).find(
      (entry) => entry.target_date === day.target_date
    ) || {};
    sqlitePendingCount.textContent = String(reportCounts.pending_report_count || 0);
    sqliteAppliedCount.textContent = String(reportCounts.applied_report_count || 0);
    sqliteRejectedCount.textContent = String(reportCounts.rejected_report_count || 0);
    dayOutboxCount.textContent = String(outboxEntries.filter((entry) => (
      day.items.some((item) => (
        entry.report.offer_key === item.offer_key
        && entry.report.draft_id === item.draft_id
        && entry.report.draft_version === item.draft_version
      ))
    )).length);
    for (const action of ["posted", "skipped", "correction", "edit"]) {
      for (const status of ["pending", "applied", "rejected"]) {
        const cell = document.getElementById(`report-count-${action}-${status}`);
        cell.textContent = String(reportCounts.action_counts?.[action]?.[status] || 0);
      }
    }
  }

  const apiErrorMessage = (code) => ({
    authentication_required: "Human API tokenを確認してください。",
    origin_rejected: "保存元を確認できませんでした。許可された履歴HTMLから開き直してください。",
    csrf_rejected: "画面の確認情報が古くなりました。再読み込みしてください。",
    stale_revision: "別の報告が先に受付されました。最新状態を読み直してください。",
    action_id_conflict: "報告IDが競合しました。最新状態を読み直してください。",
    storage_unavailable: "SQLiteへ保存できませんでした。接続状態を確認してください。",
    invalid_report: "報告を保存できませんでした。入力内容を確認してください。",
  }[code] || "SQLite保存APIに接続できません。");

  function setConnection(state, text) {
    connectionStatus.dataset.historyConnection = state;
    connectionStatus.textContent = text;
    reconnectButton.hidden = !["unavailable", "outbox"].includes(state) || !API_ORIGIN;
  }

  function requireAuthentication() {
    authRequired = true;
    authToken = null;
    authTokenInput.value = "";
    authForm.hidden = false;
    setConnection("auth-required", "Human APIの本人確認が必要です。tokenを入力してください。");
  }

  function openOutbox() {
    return new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) {
        reject(new Error("このブラウザーはIndexedDBを利用できません。"));
        return;
      }
      const request = indexedDB.open("inago-history-outbox-v1", 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains("browser-outbox")) {
          request.result.createObjectStore("browser-outbox", { keyPath: "action_id" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDBを開けません。"));
      request.onblocked = () => reject(new Error("別の履歴画面が開いています。"));
    });
  }

  function outboxTransaction(mode, operation) {
    return new Promise((resolve, reject) => {
      if (!outboxDb) {
        reject(new Error("ブラウザー内の保留領域を利用できません。"));
        return;
      }
      const transaction = outboxDb.transaction("browser-outbox", mode);
      const store = transaction.objectStore("browser-outbox");
      let result;
      try {
        result = operation(store);
      } catch (error) {
        reject(error);
        return;
      }
      transaction.oncomplete = () => resolve(result?.result);
      transaction.onerror = () => reject(transaction.error || new Error("保留報告を保存できません。"));
      transaction.onabort = () => reject(transaction.error || new Error("保留報告の保存を取り消しました。"));
    });
  }

  async function reloadOutbox() {
    if (outboxDb) {
      outboxEntries = (await outboxTransaction("readonly", (store) => store.getAll())) || [];
    }
    outboxEntries.sort((left, right) => (
      left.report.offer_key.localeCompare(right.report.offer_key)
      || left.report.expected_receipt_revision - right.report.expected_receipt_revision
      || left.queued_at.localeCompare(right.queued_at)
      || left.action_id.localeCompare(right.action_id)
    ));
    outboxButton.hidden = !outboxEntries.some((entry) => !entry.rejection_code);
    const needsReview = outboxEntries.filter((entry) => entry.rejection_code).length;
    outboxCount.textContent = outboxEntries.length
      ? `ブラウザー保留 ${outboxEntries.length}件（SQLite未受付${needsReview ? `・要確認 ${needsReview}件` : ""}）`
      : "";
    return outboxEntries;
  }

  function reportsFor(item) {
    const savedState = apiState || data;
    const saved = [...(savedState.pending_reports || []), ...(savedState.recent_reports || [])];
    const browserQueued = outboxEntries.filter((entry) => !entry.rejection_code).map((entry) => ({
      ...entry.report,
      receipt_revision: entry.report.expected_receipt_revision + 1,
      apply_status: "pending",
      browser_outbox: true,
    }));
    return [...saved, ...browserQueued]
      .filter((report) => report.offer_key === item.offer_key
        && report.draft_id === item.draft_id
        && report.draft_version === item.draft_version)
      .sort((left, right) => right.receipt_revision - left.receipt_revision);
  }

  function revisionFor(item) {
    const serverRevision = (apiState || data).receipt_revisions?.[item.offer_key];
    const base = Number.isInteger(serverRevision) ? serverRevision : item.receipt_revision || 0;
    return outboxEntries.reduce((revision, entry) => (
      !entry.rejection_code && entry.report.offer_key === item.offer_key
        ? Math.max(revision, entry.report.expected_receipt_revision + 1)
        : revision
    ), base);
  }

  function activeEvents(item) {
    return Array.isArray(item.events) ? item.events.filter((event) => event?.event_id) : [];
  }

  function setControls(card, item) {
    const posted = card.querySelector("input[data-report-posted-draft-id]");
    const skip = card.querySelector('button[data-report-action="skipped"]');
    const correctionButton = card.querySelector('button[data-report-action="correction"]');
    const reportStatus = card.querySelector("[data-report-status]");
    const reports = reportsFor(item);
    const latest = reports[0] || null;
    const pending = latest?.apply_status === "pending";
    const events = activeEvents(item);
    const appliedSkipped = item.status === "見送り反映済み";
    const pendingPosted = pending && latest.action === "posted";
    const pendingCorrection = pending && latest.action === "correction";
    const appliedPosted = events.length > 0
      || (!latest && item.status === "投稿反映済み")
      || (latest?.action === "posted" && latest.apply_status === "applied");
    const canReport = Boolean(outboxDb || (API_ORIGIN && apiState)) && !pending;
    const canQueuePosted = Boolean(outboxDb || (API_ORIGIN && apiState));
    const canPost = canQueuePosted && (!pending || pendingCorrection)
      && (!appliedPosted || pendingCorrection) && !appliedSkipped
      && item.status !== "既投稿のため後続版は投稿対象外";
    const canSkip = canReport && !appliedPosted && !appliedSkipped
      && item.status !== "既投稿のため後続版は投稿対象外";
    const canUndo = Boolean(API_ORIGIN && (apiState || outboxDb))
      && (pendingPosted || (!pending && appliedPosted));
    const canCorrectPendingPost = pendingPosted && Boolean(API_ORIGIN && apiState);
    posted.checked = pending ? pendingPosted : appliedPosted;
    posted.disabled = !(canPost || canUndo);
    const combined = card.querySelector("button[data-copy-check-open-draft-id]");
    if (combined) {
      combined.disabled = !item.product_url || !(posted.checked || canPost)
        || combinedActionsInFlight.has(`${item.draft_id}:${item.draft_version}`);
    }
    skip.disabled = !canSkip;
    if (correctionButton) {
      correctionButton.disabled = !(canReport && events.length > 0) && !canCorrectPendingPost;
    }
    for (const control of card.querySelectorAll(
      "[data-correction-ref], [data-correction-reason], [data-actual-posted-at]"
    )) {
      control.disabled = !(canReport || canCorrectPendingPost) || appliedSkipped;
    }
    if (pending) {
      const actionName = latest.action === "posted" ? "投稿" : latest.action === "skipped" ? "見送り" : "訂正";
      reportStatus.textContent = latest.browser_outbox
        ? `ブラウザー保留・SQLite未受付（${actionName}）`
        : `SQLite受付済み・翌日反映待ち（${actionName}）`;
      if (pendingPosted) reportStatus.textContent += "。チェックを外すと理由入力なしで訂正を送信します。";
      if (pendingCorrection) reportStatus.textContent += "。チェックすると訂正後の投稿報告を追加できます。";
    } else if (appliedPosted) {
      reportStatus.textContent = "SQLite管理反映済みの投稿。チェックを外すと訂正を送信します（理由入力不要）。";
    } else if (appliedSkipped) {
      reportStatus.textContent = "SQLite管理反映済みの見送り";
    } else if (latest?.action === "correction" && latest.apply_status === "applied") {
      reportStatus.textContent = "訂正はSQLite管理へ反映済み";
    } else {
      reportStatus.textContent = apiState
        ? "投稿後にチェックするとSQLiteへ即時保存します。"
        : "接続できない場合はブラウザーに保留し、JSONから翌日取り込めます。";
    }
    if (outboxEntries.some((entry) => entry.rejection_code
      && entry.report.offer_key === item.offer_key && entry.report.draft_id === item.draft_id
      && entry.report.draft_version === item.draft_version)) {
      reportStatus.textContent += " SQLite未受付・要確認の報告があります。別の報告との競合等で拒否されました。DBの状態を確認して操作し直してください。";
    }

    const correctionSelect = card.querySelector("[data-correction-ref]");
    if (correctionSelect) {
      const selected = correctionSelect.value;
      correctionSelect.replaceChildren();
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "投稿記録を選択";
      correctionSelect.append(emptyOption);
      for (const event of events) {
        const option = document.createElement("option");
        option.value = event.event_id;
        option.textContent = `${event.event_id}・${event.actual_posted_at || "時刻不明"}`;
        correctionSelect.append(option);
      }
      for (const report of reports.filter((entry) => entry.action === "posted")) {
        const option = document.createElement("option");
        option.value = report.action_id;
        option.textContent = `${report.action_id}・受付済み投稿`;
        correctionSelect.append(option);
      }
      if ([...correctionSelect.options].some((option) => option.value === selected)) {
        correctionSelect.value = selected;
      }
    }
  }

  async function apiFetch(path, options = {}) {
    if (!API_ORIGIN) throw new Error("このHTMLにSQLite保存APIの接続先がありません。");
    const requestAuthRevision = authRevision;
    const send = (token) => fetch(`${API_ORIGIN}${path}`, {
      ...options,
      mode: "cors",
      credentials: HTTP_HISTORY ? "same-origin" : "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      headers: {
        ...(options.headers || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
    const response = await send(authToken);
    if (response.status === 401 && requestAuthRevision === authRevision) {
      requireAuthentication();
    }
    return response;
  }

  async function queueOfflineReport(report) {
    const entry = { action_id: report.action_id, queued_at: new Date().toISOString(), report };
    await outboxTransaction("readwrite", (store) => store.put(entry));
    await reloadOutbox();
    return entry;
  }

  async function acknowledgeOutbox(actionId) {
    if (!outboxDb) return;
    await outboxTransaction("readwrite", (store) => store.delete(actionId));
    await reloadOutbox();
  }

  async function sendReport(report) {
    if (!apiState) throw new TypeError("SQLite保存APIの接続状態を確認できません。");
    return apiFetch("/api/reports", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": apiState.csrf_token,
      },
      body: JSON.stringify(report),
    });
  }

  function rememberReceipt(report, receipt) {
    acknowledgedReports.set(report.action_id, receipt);
    const received = { ...report, receipt_revision: receipt.receipt_revision, apply_status: receipt.apply_status };
    for (const state of new Set([data, apiState].filter(Boolean))) {
      const key = receipt.apply_status === "pending" ? "pending_reports" : "recent_reports";
      state[key] = [...(state[key] || []).filter((entry) => entry.action_id !== report.action_id), received];
      state.receipt_revisions ||= {};
      state.receipt_revisions[report.offer_key] = Math.max(
        state.receipt_revisions[report.offer_key] || 0, receipt.receipt_revision
      );
    }
  }

  async function flushOutboxInternal() {
    if (!apiState || outboxEntries.length === 0) return 0;
    let saved = 0;
    while (outboxEntries.some((entry) => !entry.rejection_code)) {
      const entry = outboxEntries.find((candidate) => !candidate.rejection_code);
      const response = await sendReport(entry.report);
      const receipt = await response.json().catch(() => ({}));
      if (!response.ok || receipt.saved !== true
        || !["pending", "applied"].includes(receipt.apply_status)) {
        const error = new Error(apiErrorMessage(receipt.error));
        error.apiCode = receipt.error;
        error.status = response.status;
        error.actionId = entry.action_id;
        if (["stale_revision", "action_id_conflict", "invalid_report"].includes(receipt.error)) {
          // Retain rejected intent for review, without blocking other products.
          await outboxTransaction("readwrite", (store) => store.put({ ...entry, rejection_code: receipt.error }));
          await reloadOutbox();
          continue;
        }
        throw error;
      }
      rememberReceipt(entry.report, receipt);
      await acknowledgeOutbox(entry.action_id);
      saved += 1;
    }
    return saved;
  }

  function flushOutbox() {
    if (outboxFlushInFlight) return outboxFlushInFlight;
    const current = flushOutboxInternal().finally(() => {
      if (outboxFlushInFlight === current) outboxFlushInFlight = null;
    });
    outboxFlushInFlight = current;
    return current;
  }

  function selectedCorrectionRef(item) {
    const reports = reportsFor(item);
    const latest = reports[0];
    if (latest?.action === "posted" && latest.apply_status === "pending") {
      return latest.action_id;
    }
    const selected = list.querySelector(
      `article[data-draft-id="${CSS.escape(item.draft_id)}"] [data-correction-ref]`
    )?.value;
    if (selected) return selected;
    return activeEvents(item)[0]?.event_id || null;
  }

  function createActionId() {
    return `ACT-${globalThis.crypto?.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  }

  async function submitReport(item, action, card, posted, options = {}) {
    if (!item.offer_key || (!API_ORIGIN && !outboxDb)) {
      posted.checked = Boolean(options.previousChecked);
      updatePostedCount();
      message.textContent = "この履歴には報告先が設定されていません。";
      return;
    }
    const reason = options.reason
      || card.querySelector("[data-correction-reason]")?.value?.trim()
      || null;
    const correctionRef = options.correctionRef
      || card.querySelector("[data-correction-ref]")?.value
      || null;
    if (action === "correction" && (!correctionRef || (options.requireReason && !reason))) {
      posted.checked = Boolean(options.previousChecked);
      updatePostedCount();
      message.textContent = options.requireReason
        ? "訂正理由と対象の投稿記録を入力してください。"
        : "訂正対象の投稿記録が見つかりません。状態を確認してください。";
      return;
    }
    const localPostedAt = card.querySelector("[data-actual-posted-at]")?.value || "";
    const parsedPostedAt = localPostedAt ? new Date(localPostedAt) : null;
    if (parsedPostedAt && Number.isNaN(parsedPostedAt.getTime())) {
      posted.checked = Boolean(options.previousChecked);
      updatePostedCount();
      message.textContent = "投稿時刻の形式を確認してください。";
      return;
    }
    const report = {
      action_id: createActionId(),
      offer_key: item.offer_key,
      draft_id: item.draft_id,
      draft_version: item.draft_version,
      expected_receipt_revision: revisionFor(item),
      action,
      reason: action === "correction" ? reason : null,
      correction_ref: action === "correction" ? correctionRef : null,
      actual_posted_at: action === "posted" && parsedPostedAt ? parsedPostedAt.toISOString() : null,
    };
    const reportButtons = card.querySelectorAll("button[data-report-action]");
    reportSubmissionsInFlight += 1;
    posted.disabled = true;
    for (const button of reportButtons) button.disabled = true;
    const actionLabel = action === "posted" ? "投稿" : action === "skipped" ? "見送り" : "訂正";
    message.textContent = `${actionLabel}報告を送信しています。SQLiteの受付を待っています。`;
    let queued = false;
    let accepted = false;
    try {
      // Persist before starting a request that can be interrupted by reload.
      if (outboxDb) {
        try {
          await queueOfflineReport(report);
          queued = true;
        } catch (_) {
          // A working API can still durably accept the report without IndexedDB.
        }
      }
      if (queued || outboxEntries.length) {
        await flushOutbox();
        // Include an entry queued as an earlier flush was completing.
        if (!acknowledgedReports.has(report.action_id)) await flushOutbox();
      }
      let receipt = acknowledgedReports.get(report.action_id);
      const rejected = outboxEntries.find((entry) => entry.action_id === report.action_id && entry.rejection_code);
      if (rejected) {
        const error = new Error(apiErrorMessage(rejected.rejection_code));
        error.apiCode = rejected.rejection_code;
        error.actionId = report.action_id;
        throw error;
      }
      if (!receipt) {
        // IndexedDB may be unavailable, or another tab may have ACKed the entry.
        const response = await sendReport(report);
        receipt = await response.json().catch(() => ({}));
        if (!response.ok || receipt.saved !== true
          || !["pending", "applied"].includes(receipt.apply_status)) {
          const error = new Error(apiErrorMessage(receipt.error));
          error.apiCode = receipt.error;
          error.status = response.status;
          error.actionId = report.action_id;
          throw error;
        }
      }
      accepted = true;
      rememberReceipt(report, receipt);
      await acknowledgeOutbox(report.action_id);
      posted.checked = action === "posted";
      updatePostedCount();
      // Do not reuse a GET that began before this POST committed.
      if (refreshInFlight) await refreshInFlight.catch(() => {});
      await refreshConnectedState();
      message.textContent = "SQLite受付済み・翌日の日次管理反映待ち";
    } catch (error) {
      if (accepted || acknowledgedReports.has(report.action_id)) {
        setConnection("unavailable", "SQLite受付済み。最新表示は再接続後に読み直します。");
        renderDays();
        message.textContent = "SQLiteには保存済みです。再接続を待っています。";
        return;
      }
      const transientServerFailure = Number.isInteger(error?.status)
        && error.status >= 500 && error.status <= 599;
      const storageUnavailable = error?.apiCode === "storage_unavailable";
      const sessionRefreshNeeded = ["authentication_required", "csrf_rejected"].includes(error?.apiCode);
      const earlierReportFailed = queued && error?.actionId && error.actionId !== report.action_id;
      if ((error instanceof TypeError || transientServerFailure || storageUnavailable
        || sessionRefreshNeeded || earlierReportFailed) && outboxDb) {
        try {
          if (!queued) await queueOfflineReport(report);
          posted.checked = action === "posted";
          updatePostedCount();
          setConnection("outbox", "報告をブラウザー内に保留しました。SQLiteの受付を確認でき次第、順番に再送します。");
          renderDays();
          message.textContent = "ブラウザー保留に保存しました。JSONを書き出し、翌日の日次取込フォルダーへ置いてください。";
          return;
        } catch (queueError) {
          message.textContent = queueError instanceof Error
            ? queueError.message
            : "ブラウザー保留にも保存できませんでした。";
        }
      } else {
        if (queued && !outboxEntries.some((entry) => entry.action_id === report.action_id && entry.rejection_code)) {
          await acknowledgeOutbox(report.action_id);
        }
        message.textContent = error instanceof Error
          ? error.message
          : "報告を保存できませんでした。状態を確認してください。";
      }
      posted.checked = Boolean(options.previousChecked);
      updatePostedCount();
      const latest = reportsFor(item)[0] || null;
      setControls(card, item, latest);
    } finally {
      reportSubmissionsInFlight = Math.max(0, reportSubmissionsInFlight - 1);
      void drainHistoryUpdates();
    }
  }

  function appendReportControls(card, item) {
    const controls = document.createElement("section");
    controls.className = "report-controls";
    controls.setAttribute("aria-label", "投稿報告");
    const postedLabel = document.createElement("label");
    postedLabel.className = "posted-control";
    const posted = document.createElement("input");
    posted.type = "checkbox";
    posted.className = "posted-checkbox";
    posted.dataset.reportPostedDraftId = item.draft_id;
    posted.setAttribute("aria-label", `${item.draft_id} ${item.draft_version}版の投稿済み状態`);
    posted.disabled = true;
    const postedText = document.createElement("span");
    postedText.textContent = "ROOMに投稿済み";
    postedLabel.append(posted, postedText);
    controls.append(postedLabel);

    const actualPostedLabel = document.createElement("label");
    actualPostedLabel.className = "meta";
    actualPostedLabel.textContent = "本人が申告する投稿時刻（任意）";
    const actualPostedAt = document.createElement("input");
    actualPostedAt.type = "datetime-local";
    actualPostedAt.className = "actual-posted-at";
    actualPostedAt.dataset.actualPostedAt = "true";
    actualPostedAt.disabled = true;
    actualPostedLabel.append(actualPostedAt);
    controls.append(actualPostedLabel);

    const skip = document.createElement("button");
    skip.type = "button";
    skip.className = "report-button";
    skip.dataset.reportAction = "skipped";
    skip.dataset.reportDraftId = item.draft_id;
    skip.textContent = "見送りを報告";
    skip.disabled = true;
    controls.append(skip);

    const details = document.createElement("details");
    details.className = "correction-controls";
    details.addEventListener("toggle", () => {
      if (!details.open) {
        historyUpdatePending = true;
        void drainHistoryUpdates();
      }
    });
    const summary = document.createElement("summary");
    summary.textContent = "投稿報告を訂正する";
    details.append(summary);
    const eventLabel = document.createElement("label");
    eventLabel.textContent = "訂正対象の投稿記録";
    const eventSelect = document.createElement("select");
    eventSelect.dataset.correctionRef = "true";
    eventSelect.disabled = true;
    eventLabel.append(eventSelect);
    details.append(eventLabel);
    const reasonLabel = document.createElement("label");
    reasonLabel.textContent = "訂正理由";
    const reason = document.createElement("textarea");
    reason.rows = 3;
    reason.dataset.correctionReason = "true";
    reason.disabled = true;
    reasonLabel.append(reason);
    details.append(reasonLabel);
    const correct = document.createElement("button");
    correct.type = "button";
    correct.className = "report-button";
    correct.dataset.reportAction = "correction";
    correct.dataset.reportDraftId = item.draft_id;
    correct.textContent = "訂正報告を送信";
    correct.disabled = true;
    details.append(correct);
    controls.append(details);

    const reportStatus = document.createElement("p");
    reportStatus.className = "report-status";
    reportStatus.dataset.reportStatus = "true";
    controls.append(reportStatus);
    card.append(controls);

    setControls(card, item);
    posted.addEventListener("change", () => {
      updatePostedCount();
      if (posted.checked) {
        submitReport(item, "posted", card, posted, { previousChecked: false });
        return;
      }
      const correctionRef = selectedCorrectionRef(item);
      if (!correctionRef) {
        posted.checked = true;
        updatePostedCount();
        message.textContent = "訂正対象の投稿記録が見つかりません。チェック状態を戻しました。";
        return;
      }
      submitReport(item, "correction", card, posted, {
        correctionRef,
        reason: "投稿済みチェック解除（本人操作）",
        previousChecked: true,
      });
    });
    skip.addEventListener("click", () => submitReport(item, "skipped", card, posted));
    correct.addEventListener("click", () => submitReport(item, "correction", card, posted, {
      requireReason: true,
      previousChecked: posted.checked,
    }));
  }

  function captureCorrectionInputState() {
    const states = new Map();
    for (const card of Array.from(list.children)) {
      const details = card.querySelector("details.correction-controls");
      const correctionRef = card.querySelector("[data-correction-ref]");
      const reason = card.querySelector("[data-correction-reason]");
      const actualPostedAt = card.querySelector("[data-actual-posted-at]");
      if (!details || (!details.open && !correctionRef?.value && !reason?.value
        && !actualPostedAt?.value)) continue;
      states.set(`${card.dataset.draftId}:${card.dataset.draftVersion}`, {
        open: details.open,
        correctionRef: correctionRef?.value || "",
        reason: reason?.value || "",
        actualPostedAt: actualPostedAt?.value || "",
      });
    }
    return states;
  }

  function restoreCorrectionInputState(card, state) {
    if (!state) return;
    const posted = card.querySelector("input[data-report-posted-draft-id]");
    if (!posted?.checked) return;
    const details = card.querySelector("details.correction-controls");
    const correctionRef = card.querySelector("[data-correction-ref]");
    const reason = card.querySelector("[data-correction-reason]");
    const actualPostedAt = card.querySelector("[data-actual-posted-at]");
    if (!details || !reason) return;
    if (correctionRef && state.correctionRef
      && Array.from(correctionRef.options).some((option) => option.value === state.correctionRef)) {
      correctionRef.value = state.correctionRef;
    }
    reason.value = state.reason;
    if (actualPostedAt) actualPostedAt.value = state.actualPostedAt;
    details.open = state.open;
  }

  function selectDraftBody(item, body) {
    const current = list.querySelector(
      `article[data-draft-id="${CSS.escape(item.draft_id)}"] textarea.body-text`
    ) || body;
    current.focus();
    current.select();
  }

  async function copyDraftBody(item, body) {
    try {
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
        throw new Error("clipboard unavailable");
      }
      await navigator.clipboard.writeText(item.body);
      return true;
    } catch {
      selectDraftBody(item, body);
      return false;
    }
  }

  async function copyCheckAndOpen(item, card, body, productLink) {
    const key = `${item.draft_id}:${item.draft_version}`;
    const posted = card.querySelector("input[data-report-posted-draft-id]");
    if (!productLink || !posted || (!posted.checked && posted.disabled)
      || combinedActionsInFlight.has(key)) return;
    const alreadyChecked = posted.checked;
    combinedActionsInFlight.add(key);
    setControls(card, item);
    try {
      // Start clipboard access while focused; keep navigation in the user gesture.
      const copying = copyDraftBody(item, body);
      let navigationError = false;
      try {
        productLink.click();
      } catch {
        navigationError = true;
      }
      let reporting = Promise.resolve();
      if (!alreadyChecked) {
        posted.checked = true;
        updatePostedCount();
        reporting = submitReport(item, "posted", card, posted, { previousChecked: false });
      }
      const [copyResult, reportResult] = await Promise.allSettled([copying, reporting]);
      const copied = copyResult.status === "fulfilled" && copyResult.value;
      if (!copied) selectDraftBody(item, body);
      const parts = [copied
        ? `${item.draft_version}版の本文をコピーしました。`
        : "コピーできません。選択中の本文を Ctrl+C でコピーしてください。"];
      parts.push(alreadyChecked ? "投稿済みチェックはONのままです。"
        : reportResult.status === "rejected" ? "投稿済みチェックの保存に失敗しました。"
          : message.textContent);
      if (navigationError) parts.push("商品ページを開けませんでした。");
      message.textContent = parts.filter(Boolean).join(" ");
    } finally {
      combinedActionsInFlight.delete(key);
      const currentCard = list.querySelector(`article[data-draft-id="${CSS.escape(item.draft_id)}"]`);
      const currentItem = data.days.flatMap((day) => day.items).find((entry) => (
        entry.draft_id === item.draft_id && entry.draft_version === item.draft_version
      ));
      if (currentCard && currentItem) setControls(currentCard, currentItem);
    }
  }

  function renderDay(day) {
    const focusedBody = document.activeElement;
    const bodySelection = activeDate === day.target_date && focusedBody?.className === "body-text"
      ? {
        draftId: focusedBody.parentElement.dataset.draftId,
        version: focusedBody.parentElement.dataset.draftVersion,
        start: focusedBody.selectionStart,
        end: focusedBody.selectionEnd,
        direction: focusedBody.selectionDirection,
      } : null;
    const correctionInputStates = activeDate === day.target_date
      ? captureCorrectionInputState()
      : new Map();
    if (activeDate !== day.target_date) message.textContent = "";
    activeDate = day.target_date;
    selectedDate.textContent = `${day.target_date}の投稿内容`;
    runStatus.textContent = day.run_status;
    dailyCount.textContent = String(day.items.length);
    committedCount.textContent = String(day.committed_count);
    list.replaceChildren();
    updatePostedCount();
    for (const item of day.items) {
      const card = document.createElement("article");
      card.className = "draft-card";
      card.dataset.draftId = item.draft_id;
      card.dataset.draftVersion = String(item.draft_version);
      const status = document.createElement("p");
      status.className = "status";
      status.textContent = item.status;
      card.append(status);
      const title = document.createElement("h3");
      title.textContent = item.product_name;
      card.append(title);
      const meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = `下書きID ${item.draft_id}・${item.draft_version}版・${item.target_date}`;
      card.append(meta);
      let productLink = null;
      if (item.product_url) {
        const link = document.createElement("a");
        link.href = item.product_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "商品ページを開く";
        productLink = link;
        card.append(link);
      } else {
        const unavailable = document.createElement("p");
        unavailable.className = "meta";
        unavailable.textContent = "商品URLを安全に表示できないため、リンクを省略しました。";
        card.append(unavailable);
      }
      const body = document.createElement("textarea");
      body.className = "body-text";
      body.readOnly = true;
      body.setAttribute("aria-label", `${item.draft_id} ${item.draft_version}版の投稿本文`);
      body.value = item.body;
      card.append(body);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "copy-button";
      copy.dataset.copyDraftId = item.draft_id;
      copy.dataset.copyVersion = String(item.draft_version);
      copy.textContent = `${item.draft_version}版の本文をコピー`;
      copy.addEventListener("click", async () => {
        const copied = await copyDraftBody(item, body);
        message.textContent = copied
          ? `${item.draft_id} ${item.draft_version}版の本文をコピーしました。`
          : "クリップボードを使えません。選択中の本文を Ctrl+C でコピーしてください。";
      });
      const combined = document.createElement("button");
      combined.type = "button";
      combined.className = "copy-button combined-action-button";
      combined.dataset.copyCheckOpenDraftId = item.draft_id;
      combined.textContent = "コピー+チェック+遷移";
      combined.disabled = true;
      if (!productLink) combined.title = "安全な商品URLがないため実行できません。";
      combined.addEventListener("click", () => { void copyCheckAndOpen(item, card, body, productLink); });
      const copyActions = document.createElement("div");
      copyActions.className = "copy-actions";
      copyActions.append(copy, combined);
      card.append(copyActions);
      appendReportControls(card, item);
      const correctionInputState = correctionInputStates.get(
        `${item.draft_id}:${item.draft_version}`
      );
      restoreCorrectionInputState(card, correctionInputState);
      list.append(card);
    }
    if (bodySelection) {
      const restored = list.querySelector(
        `article[data-draft-id="${CSS.escape(bodySelection.draftId)}"] textarea.body-text`
      );
      if (restored && restored.parentElement.dataset.draftVersion === bodySelection.version) {
        restored.focus({ preventScroll: true });
        restored.setSelectionRange(bodySelection.start, bodySelection.end, bodySelection.direction);
      }
    }
    updatePostedCount();
    updateDayReportCounts(day);
    if (day.items.length === 0 && !message.textContent) {
      message.textContent = "この日は保存された投稿本文はありません。";
    }
    for (const button of navigation.querySelectorAll("button[data-target-date]")) {
      button.setAttribute("aria-pressed", String(button.dataset.targetDate === activeDate));
    }
  }

  function renderDays() {
    navigation.replaceChildren();
    for (const day of data.days) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.targetDate = day.target_date;
      button.textContent = `${day.target_date}　${day.run_status}　${day.items.length}件`;
      button.setAttribute("aria-label", `${day.target_date}の日次履歴を表示`);
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => renderDay(day));
      navigation.append(button);
    }
    const selected = data.days.find((day) => day.target_date === activeDate) || data.days[0];
    if (selected) {
      renderDay(selected);
    } else {
      activeDate = null;
      selectedDate.textContent = "日次履歴はありません";
      runStatus.textContent = "";
      dailyCount.textContent = "0";
      committedCount.textContent = "0";
      postedCount.textContent = "0";
      updateDayReportCounts({ target_date: null, items: [] });
      message.textContent = "表示できる日次runはありません。";
    }
  }

  async function refreshConnectedStateInternal() {
    const response = await apiFetch("/api/history-state", {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    const next = await response.json().catch(() => ({}));
    if (!response.ok || !Array.isArray(next.days) || !next.receipt_revisions) {
      const error = new Error(apiErrorMessage(next.error));
      error.apiCode = next.error;
      throw error;
    }
    apiState = next;
    data = next;
    await reloadOutbox();
    let flushed = 0;
    try {
      flushed = await flushOutbox();
    } catch (error) {
      renderDays();
      throw error;
    }
    if (flushed) {
      const updated = await apiFetch("/api/history-state", {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      const updatedState = await updated.json().catch(() => ({}));
      if (!updated.ok || !Array.isArray(updatedState.days)) {
        const error = new Error(apiErrorMessage(updatedState.error));
        error.apiCode = updatedState.error;
        throw error;
      }
      apiState = updatedState;
      data = updatedState;
      await reloadOutbox();
    }
    authRequired = false;
    authForm.hidden = true;
    authTokenInput.value = "";
    setConnection("ready", "バックグラウンドAPIへ接続中。チェック操作はSQLiteへ即時保存され、翌日の日次処理で管理へ反映されます。");
    renderDays();
  }

  function refreshConnectedState() {
    if (refreshInFlight) return refreshInFlight;
    const pending = refreshConnectedStateInternal().finally(() => {
      if (refreshInFlight === pending) refreshInFlight = null;
    });
    refreshInFlight = pending;
    return pending;
  }

  function hasOpenCorrectionForm() {
    return Array.from(list.querySelectorAll("details.correction-controls"))
      .some((details) => details.open);
  }

  async function attemptApiReconnect() {
    if (!API_ORIGIN || reportSubmissionsInFlight > 0 || hasOpenCorrectionForm()
      || (authRequired && !authToken && !HTTP_HISTORY)) return;
    const requestAuthRevision = authRevision;
    try {
      await refreshConnectedState();
      if (requestAuthRevision !== authRevision) return;
    } catch (error) {
      if (requestAuthRevision !== authRevision) return;
      apiState = null;
      await reloadOutbox().catch(() => {});
      if (requestAuthRevision !== authRevision) return;
      if (error?.apiCode === "authentication_required") {
        requireAuthentication();
        renderDays();
        return;
      }
      setConnection(
        "unavailable",
        `${error instanceof Error ? error.message : "APIに接続できません。"}。報告はブラウザー内に保留し、接続復旧後に再送します。`
      );
      renderDays();
    }
  }

  function startReconnectLoop() {
    if (reconnectTimer !== null || !API_ORIGIN) return;
    reconnectTimer = window.setInterval(() => {
      if (document.visibilityState === "hidden" || reportSubmissionsInFlight > 0
        || (authRequired && !authToken && !HTTP_HISTORY)) return;
      void attemptApiReconnect();
    }, RECONNECT_INTERVAL_MS);
    window.addEventListener("online", () => {
      if (reportSubmissionsInFlight === 0 && !(authRequired && !authToken && !HTTP_HISTORY)) {
        void attemptApiReconnect();
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "hidden" && reportSubmissionsInFlight === 0
        && !(authRequired && !authToken && !HTTP_HISTORY)) {
        void attemptApiReconnect();
      }
    });
    authSubmitButton.addEventListener("click", () => {
      const entered = authTokenInput.value.trim();
      authTokenInput.value = "";
      if (!entered) {
        authForm.hidden = false;
        setConnection("auth-required", "Human API tokenを入力してください。");
        authTokenInput.focus();
        return;
      }
      authRevision += 1;
      authToken = entered;
      authRequired = false;
      authForm.hidden = true;
      setConnection("connecting", "Human APIの本人確認をしています。");
      void attemptApiReconnect();
    });
    reconnectButton.addEventListener("click", () => {
      setConnection("connecting", "SQLite保存APIへの接続を再試行しています。");
      void attemptApiReconnect();
    });
  }

  async function bootstrapHistorySession() {
    if (!HTTP_HISTORY) return;
    const current = new URL(window.location.href);
    const ticket = new URLSearchParams(current.hash.slice(1)).get("launch");
    if (!ticket) return;
    window.history.replaceState(null, "", current.pathname + current.search);
    const response = await fetch(`${API_ORIGIN}/api/history-session`, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket }),
    });
    if (!response.ok) throw new Error("起動受付の有効期限が切れました。起動バッチを開き直してください。");
  }

  async function drainHistoryUpdates() {
    if (!HTTP_HISTORY || historyUpdateRunning || !historyUpdatePending
      || reportSubmissionsInFlight > 0 || hasOpenCorrectionForm()) return;
    historyUpdateRunning = true;
    try {
      while (historyUpdatePending && reportSubmissionsInFlight === 0 && !hasOpenCorrectionForm()) {
        historyUpdatePending = false;
        if (refreshInFlight) await refreshInFlight.catch(() => {});
        await attemptApiReconnect();
      }
    } finally {
      historyUpdateRunning = false;
    }
  }

  function startHistoryEvents() {
    if (!HTTP_HISTORY || historyEventSource || !globalThis.EventSource) return;
    historyEventSource = new EventSource(`${API_ORIGIN}/api/history-events`);
    historyEventSource.addEventListener("history-changed", () => {
      historyUpdatePending = true;
      void drainHistoryUpdates();
    });
    historyEventSource.addEventListener("error", () => { void attemptApiReconnect(); });
    window.addEventListener("pagehide", () => historyEventSource.close());
  }

  async function exportOutbox() {
    const entries = (await reloadOutbox()).filter((entry) => !entry.rejection_code);
    if (!entries.length) return;
    const batch = {
      schema_version: 1,
      reports: entries.map((entry) => ({ queued_at: entry.queued_at, report: entry.report })),
    };
    const json = JSON.stringify(batch, null, 2);
    const name = `human-reports-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    if (typeof window.showSaveFilePicker === "function") {
      const handle = await window.showSaveFilePicker({
        suggestedName: name,
        types: [{ description: "JSON", accept: { "application/json": [".json"] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(json);
      await writable.close();
    } else {
      const blobUrl = URL.createObjectURL(new Blob([json], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = name;
      link.click();
      URL.revokeObjectURL(blobUrl);
    }
    message.textContent = "JSONを書き出しました。runtime\\incoming-human-reports に置くと翌日の日次処理がSQLiteへ取り込みます。";
  }

  outboxButton.addEventListener("click", () => {
    exportOutbox().catch((error) => {
      message.textContent = error instanceof Error ? error.message : "JSONを書き出せませんでした。";
    });
  });
  renderDays();
  if (API_ORIGIN) {
    setConnection("connecting", "バックグラウンドのSQLite保存APIへ接続しています。");
    openOutbox().then(async (database) => {
      outboxDb = database;
      await reloadOutbox();
      renderDays();
      startReconnectLoop();
      await bootstrapHistorySession();
      await attemptApiReconnect();
      startHistoryEvents();
    }).catch(async (error) => {
      outboxDb = null;
      setConnection(
        "unavailable",
        `${error instanceof Error ? error.message : "ブラウザー保留領域を利用できません。"}。SQLite APIへの接続を試します。`
      );
      startReconnectLoop();
      await bootstrapHistorySession().catch((sessionError) => {
        message.textContent = sessionError.message;
      });
      await attemptApiReconnect();
      startHistoryEvents();
    });
  } else {
    setConnection("unavailable", "SQLite API接続先がありません。報告はブラウザーに保留し、JSONから翌日取り込めます。");
    openOutbox().then(async (database) => {
      outboxDb = database;
      await reloadOutbox();
      renderDays();
    }).catch(() => {
      outboxDb = null;
    });
  }
})();
