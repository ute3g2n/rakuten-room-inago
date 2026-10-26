"""Export all daily-run drafts as a date-navigable standalone HTML file."""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from dataclasses import dataclass
from html import escape as _html_escape
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from automation.inago.dashboard_export import (
    DashboardExportError,
    _atomic_write,
    _json_for_html,
    _validated_destination,
)
from automation.inago.export import safe_product_url


_RUN_STATUS_LABELS = {
    "created": "未開始",
    "running": "実行中",
    "complete": "完了",
    "partial": "一部完了",
    "failed": "失敗",
    "shortfall": "件数不足",
    "missed": "未実行",
}
_DRAFT_STATUS_LABELS = {
    "committed": "保存済み下書き",
    "human_edited": "本人編集済み",
    "posted": "投稿反映済み",
    "skipped": "見送り反映済み",
    "do_not_post_already_posted": "既投稿のため後続版は投稿対象外",
}
_REPORT_ACTIONS = ("posted", "skipped", "correction", "edit")
_REPORT_APPLY_STATUSES = ("pending", "applied", "rejected")
_REPORT_ACTION_LABELS = {
    "posted": "投稿",
    "skipped": "見送り",
    "correction": "訂正",
    "edit": "編集",
}
_HISTORY_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "rakuten-room-inago"
    / "dashboard-assets"
    / "history.js"
)
_HTML_TEMPLATE = """<!doctype html>
<html lang="ja" data-api-origin="%%API_ORIGIN%%" data-history-transport="%%HISTORY_TRANSPORT%%">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>イナゴスキル 日次投稿履歴</title>
  <style nonce="%%CSP_NONCE%%">
    :root { color-scheme: light; font-family: "Yu Gothic UI", "Meiryo", sans-serif; color: #1b2522; background: #f4f7f5; }
    * { box-sizing: border-box; }
    body { margin: 0; min-width: 320px; line-height: 1.6; }
    header, main, footer { width: min(100% - 2rem, 72rem); margin-inline: auto; }
    header { padding: 1.6rem 0 1rem; }
    h1 { margin: 0; font-size: clamp(1.55rem, 4vw, 2.2rem); }
    .eyebrow { margin: 0 0 .25rem; color: #23674b; font-size: .82rem; font-weight: 700; }
    main { display: grid; grid-template-columns: minmax(12rem, 16rem) minmax(0, 1fr); gap: 1rem; align-items: start; padding-bottom: 2rem; }
    #history-sidebar, .summary, .draft-card { border: 1px solid #d5dfd9; border-radius: .8rem; background: #fff; box-shadow: 0 .15rem .6rem rgb(25 47 35 / 5%); }
    #history-sidebar { padding: .8rem; position: sticky; top: .75rem; }
    #history-sidebar h2 { margin: 0 0 .5rem; font-size: 1rem; }
    #date-navigation { display: grid; gap: .25rem; max-height: 70vh; overflow: auto; }
    #date-navigation button { width: 100%; border: 1px solid transparent; border-radius: .4rem; background: transparent; color: inherit; cursor: pointer; padding: .45rem .55rem; text-align: left; font: inherit; }
    #date-navigation button:hover { background: #f2f7f4; }
    #date-navigation button[aria-pressed="true"] { border-color: #8bb6a0; background: #eaf4ee; font-weight: 700; }
    #history-content { min-width: 0; display: grid; gap: 1rem; }
    .summary { padding: 1rem; }
    .summary p { margin: .15rem 0; }
    #selected-date { font-size: 1.35rem; }
    .summary-count { color: #52665b; }
    #draft-list { display: grid; gap: 1rem; }
    .draft-card { display: grid; gap: .65rem; padding: 1rem; min-width: 0; }
    .draft-card h3 { margin: 0; font-size: 1.08rem; overflow-wrap: anywhere; }
    .draft-card p { margin: 0; }
    .meta { color: #65756d; font-size: .88rem; overflow-wrap: anywhere; }
    .status { color: #245b42; font-weight: 700; }
    .draft-card a { width: fit-content; color: #075bba; overflow-wrap: anywhere; }
    .body-text { width: 100%; min-height: 8rem; resize: vertical; margin: 0; padding: .8rem; border: 1px solid #d5dfd9; border-radius: .5rem; background: #f8faf9; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
    .copy-actions { display: flex; flex-wrap: wrap; align-items: center; column-gap: 1rem; row-gap: .6rem; }
    .copy-button { justify-self: start; border: 0; border-radius: .45rem; background: #23674b; color: #fff; cursor: pointer; padding: .55rem .8rem; font: inherit; }
    .copy-button:disabled { cursor: not-allowed; opacity: .65; }
    .combined-action-button { background: #1b58a6; }
    .combined-action-button:hover:not(:disabled) { background: #174a8a; }
    .report-controls { display: grid; gap: .55rem; border-top: 1px solid #e3e9e5; padding-top: .65rem; }
    .posted-control { display: flex; gap: .5rem; align-items: center; font-weight: 700; }
    .posted-checkbox { width: 1.1rem; height: 1.1rem; }
    .report-button { justify-self: start; border: 1px solid #aab9b0; border-radius: .4rem; background: #fff; cursor: pointer; padding: .35rem .6rem; font: inherit; }
    .report-button:disabled, .posted-checkbox:disabled { cursor: not-allowed; opacity: .65; }
    .auth-controls { display: flex; flex-wrap: wrap; align-items: end; gap: .5rem; margin-top: .5rem; }
    .auth-controls[hidden] { display: none; }
    .auth-controls label { display: grid; gap: .15rem; font-size: .9rem; }
    .auth-controls input { min-width: min(24rem, 75vw); max-width: 100%; font: inherit; }
    .report-counts { width: 100%; margin-top: .6rem; border-collapse: collapse; font-size: .9rem; }
    .report-counts th, .report-counts td { border: 1px solid #d5dfd9; padding: .3rem; text-align: right; }
    .report-counts th:first-child { text-align: left; }
    .correction-controls { display: grid; gap: .45rem; }
    .correction-controls select, .correction-controls textarea, .actual-posted-at { max-width: 100%; font: inherit; }
    .offline-note, .report-status { margin: 0; color: #52665b; font-size: .9rem; }
    .copy-button:focus-visible, #date-navigation button:focus-visible { outline: 3px solid #f1aa38; outline-offset: 2px; }
    #history-message { min-height: 1.6em; margin: 0; color: #52665b; }
    footer { padding: 0 0 2rem; color: #65756d; font-size: .85rem; }
    @media (max-width: 720px) {
      main { grid-template-columns: minmax(0, 1fr); }
      #history-sidebar { position: static; }
      #date-navigation { max-height: 12rem; }
    }
    @media print {
      body { background: #fff; }
      header, main, footer { width: 100%; }
      #history-sidebar, .copy-actions, .copy-button, .report-controls { display: none !important; }
      main { display: block; }
      .summary, .draft-card { box-shadow: none; break-inside: avoid; }
      .draft-card { margin-bottom: .7rem; }
      .body-text { min-height: 0; border: 0; padding: 0; }
    }
  </style>
</head>
<body>
  <header>
    <p class="eyebrow">楽天ROOM・日次履歴</p>
    <h1>日付別の投稿内容</h1>
    <p>サイドバーの日付を選ぶと、その日の日次runと保存された本文を表示します。</p>
      <p id="history-connection" data-history-connection="connecting" role="status" aria-live="polite">バックグラウンドのSQLite保存APIへ接続しています。</p>
      <div id="history-auth" class="auth-controls" role="group" aria-label="SQLite保存APIの本人確認" hidden>
        <label for="history-api-token">Human API token（この画面内だけで使用）
          <input id="history-api-token" type="password" autocomplete="off" spellcheck="false">
        </label>
        <button id="history-auth-submit" class="report-button" type="button">本人確認して接続</button>
      </div>
      <button id="history-reconnect" class="report-button" type="button" hidden>接続を再試行</button>
  </header>
  <main>
    <aside id="history-sidebar" aria-label="日次履歴">
      <h2>日付</h2>
      <nav id="date-navigation" aria-label="日付を選択"></nav>
    </aside>
    <section id="history-content" aria-live="polite">
      <div class="summary" aria-label="選択日の状態">
        <h2 id="selected-date"></h2>
        <p>日次run: <strong id="run-status"></strong></p>
        <p class="summary-count">表示本文: <strong id="daily-count"></strong>件</p>
        <p class="summary-count">run保存件数: <strong id="committed-count"></strong>件</p>
        <p class="summary-count">投稿済みチェック: <strong id="posted-count" aria-live="polite">0</strong>件</p>
        <p class="summary-count">SQLite受付待ち報告: <strong id="sqlite-pending-count" aria-live="polite">0</strong>件</p>
        <p class="summary-count">SQLite管理反映済み報告: <strong id="sqlite-applied-count" aria-live="polite">0</strong>件</p>
        <p class="summary-count">SQLite要確認報告: <strong id="sqlite-rejected-count" aria-live="polite">0</strong>件</p>
        <p class="summary-count">この日のブラウザー保留: <strong id="browser-outbox-day-count" aria-live="polite">0</strong>件</p>
        <table id="report-action-counts" class="report-counts">
          <caption>操作別・SQLite受付状態（選択日）</caption>
          <thead><tr><th scope="col">操作</th><th scope="col">受付待ち</th><th scope="col">管理反映済み</th><th scope="col">要確認</th></tr></thead>
          <tbody>%%REPORT_COUNT_ROWS%%</tbody>
        </table>
      </div>
      <p id="history-message" role="status"></p>
      <p id="outbox-count" class="offline-note" aria-live="polite"></p>
      <button id="export-outbox" class="report-button" type="button" hidden>未送信報告をJSONに書き出す</button>
      <section id="draft-list" aria-label="選択日の投稿本文"></section>
    </section>
  </main>
  <footer><p>投稿済みチェックはSQLiteへ送信されます。接続できない場合はブラウザー内に保留し、JSONを書き出して翌日の日次処理へ取り込めます。</p></footer>
  <script id="dashboard-history-data" type="application/json">%%HISTORY_DATA%%</script>
  <script nonce="%%CSP_NONCE%%">%%HISTORY_SCRIPT%%</script>
</body>
</html>
"""


class DashboardHistoryExportError(RuntimeError):
    """The date-navigable history could not be safely exported."""


@dataclass(frozen=True, slots=True)
class DashboardHistoryExportResult:
    path: Path
    run_count: int
    draft_count: int


def _report_count_rows() -> str:
    rows: list[str] = []
    for action in _REPORT_ACTIONS:
        cells = "".join(
            '<td><strong id="report-count-{}-{}" aria-live="polite">0</strong>件</td>'.format(
                _html_escape(action, quote=True),
                _html_escape(apply_status, quote=True),
            )
            for apply_status in _REPORT_APPLY_STATUSES
        )
        rows.append(
            '<tr data-report-action="{}"><th scope="row">{}</th>{}</tr>'.format(
                _html_escape(action, quote=True),
                _html_escape(_REPORT_ACTION_LABELS[action]),
                cells,
            )
        )
    return "".join(rows)


def _iso_date(value: Any) -> str:
    if not isinstance(value, str):
        raise DashboardHistoryExportError("history target date is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DashboardHistoryExportError("history target date is invalid") from exc
    if parsed.isoformat() != value:
        raise DashboardHistoryExportError("history target date is not canonical")
    return value


def _status_label(value: Any, labels: Mapping[str, str], field: str) -> str:
    if not isinstance(value, str):
        raise DashboardHistoryExportError(f"{field} is invalid")
    label = labels.get(value)
    if label is None and value in labels.values():
        label = value
    if label is None:
        raise DashboardHistoryExportError(f"{field} is unknown")
    return label


def _history_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    raw_days = state.get("days")
    if not isinstance(raw_days, list):
        raise DashboardHistoryExportError("history snapshot has no day list")
    raw_report_counts = state.get("report_counts_by_date", [])
    if not isinstance(raw_report_counts, list):
        raise DashboardHistoryExportError("history report counts are invalid")
    days: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for raw_day in raw_days:
        if not isinstance(raw_day, Mapping):
            raise DashboardHistoryExportError("history snapshot contains an invalid day")
        target_date = _iso_date(raw_day.get("target_date"))
        if target_date in seen_dates:
            raise DashboardHistoryExportError("history snapshot contains a duplicate date")
        seen_dates.add(target_date)
        run_status = _status_label(raw_day.get("run_status"), _RUN_STATUS_LABELS, "run status")
        raw_items = raw_day.get("items")
        if not isinstance(raw_items, list):
            raise DashboardHistoryExportError("history day has no item list")
        try:
            committed_count = raw_day.get("committed_count", len(raw_items))
            if isinstance(committed_count, bool) or not isinstance(committed_count, int):
                raise ValueError
            if not 0 <= committed_count <= 15:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise DashboardHistoryExportError("history committed count is invalid") from exc

        items: list[dict[str, Any]] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise DashboardHistoryExportError("history day contains an invalid item")
            draft_id = raw_item.get("draft_id")
            product_name = raw_item.get("product_name")
            body = raw_item.get("body")
            if (
                not isinstance(draft_id, str)
                or not draft_id.strip()
                or not isinstance(product_name, str)
                or not isinstance(body, str)
            ):
                raise DashboardHistoryExportError("history draft fields are invalid")
            draft_version = raw_item.get("draft_version")
            if (
                isinstance(draft_version, bool)
                or not isinstance(draft_version, int)
                or draft_version < 1
            ):
                raise DashboardHistoryExportError("history draft version is invalid")
            item_date = _iso_date(raw_item.get("target_date", target_date))
            if item_date != target_date:
                raise DashboardHistoryExportError("draft date does not match its daily run")
            offer_key = raw_item.get("offer_key")
            if offer_key is not None and (
                not isinstance(offer_key, str)
                or not offer_key.strip()
                or offer_key != offer_key.strip()
            ):
                raise DashboardHistoryExportError("history offer key is invalid")
            receipt_revision = raw_item.get("receipt_revision", 0)
            if (
                isinstance(receipt_revision, bool)
                or not isinstance(receipt_revision, int)
                or receipt_revision < 0
            ):
                raise DashboardHistoryExportError("history receipt revision is invalid")
            raw_events = raw_item.get("events", [])
            if not isinstance(raw_events, list):
                raise DashboardHistoryExportError("history event list is invalid")
            events: list[dict[str, Any]] = []
            for raw_event in raw_events:
                if not isinstance(raw_event, Mapping):
                    raise DashboardHistoryExportError("history event is invalid")
                event_id = raw_event.get("event_id")
                event_revision = raw_event.get("revision", 0)
                if (
                    not isinstance(event_id, str)
                    or not event_id.strip()
                    or isinstance(event_revision, bool)
                    or not isinstance(event_revision, int)
                    or event_revision < 0
                ):
                    raise DashboardHistoryExportError("history event fields are invalid")
                events.append(
                    {
                        "event_id": event_id,
                        "actual_posted_at": raw_event.get("actual_posted_at"),
                        "revision": event_revision,
                    }
                )
            items.append(
                {
                    "draft_id": draft_id,
                    "draft_version": draft_version,
                    "offer_key": offer_key,
                    "receipt_revision": receipt_revision,
                    "events": events,
                    "product_name": product_name,
                    "product_url": safe_product_url(raw_item.get("product_url")),
                    "body": body,
                    "status": _status_label(
                        raw_item.get("status"), _DRAFT_STATUS_LABELS, "draft status"
                    ),
                    "target_date": target_date,
                }
            )
        days.append(
            {
                "target_date": target_date,
                "run_status": run_status,
                "committed_count": committed_count,
                "item_count": len(items),
                "items": items,
            }
        )
    counts_by_date: dict[str, dict[str, Any]] = {}
    for raw_counts in raw_report_counts:
        if not isinstance(raw_counts, Mapping):
            raise DashboardHistoryExportError("history report count row is invalid")
        target_date = _iso_date(raw_counts.get("target_date"))
        if target_date not in seen_dates or target_date in counts_by_date:
            raise DashboardHistoryExportError("history report count date is invalid")
        raw_actions = raw_counts.get("action_counts", {})
        if not isinstance(raw_actions, Mapping):
            raise DashboardHistoryExportError("history report action counts are invalid")
        action_counts: dict[str, dict[str, int]] = {}
        for action in _REPORT_ACTIONS:
            raw_statuses = raw_actions.get(action, {})
            if not isinstance(raw_statuses, Mapping):
                raise DashboardHistoryExportError("history report action counts are invalid")
            action_counts[action] = {}
            for apply_status in _REPORT_APPLY_STATUSES:
                count = raw_statuses.get(apply_status, 0)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise DashboardHistoryExportError("history report count is invalid")
                action_counts[action][apply_status] = count
        parsed_counts: dict[str, Any] = {
            "target_date": target_date,
            "action_counts": action_counts,
        }
        for apply_status in _REPORT_APPLY_STATUSES:
            parsed_counts[f"{apply_status}_report_count"] = sum(
                counts[apply_status] for counts in action_counts.values()
            )
        counts_by_date[target_date] = parsed_counts
    return {
        "days": days,
        "report_counts_by_date": [
            counts_by_date.get(
                day["target_date"],
                {
                    "target_date": day["target_date"],
                    "action_counts": {
                        action: {status: 0 for status in _REPORT_APPLY_STATUSES}
                        for action in _REPORT_ACTIONS
                    },
                    "pending_report_count": 0,
                    "applied_report_count": 0,
                    "rejected_report_count": 0,
                },
            )
            for day in days
        ],
    }


def _read_history_state(connection: sqlite3.Connection) -> dict[str, Any]:
    if connection.in_transaction:
        raise DashboardHistoryExportError("history export requires an idle connection")
    try:
        connection.execute("BEGIN")
        days_by_run: dict[str, dict[str, Any]] = {}
        rows = connection.execute(
            """
            SELECT r.run_id, r.target_date, r.status AS run_status,
                   r.committed_count, d.draft_id, d.version AS draft_version,
                   d.offer_key,
                   d.body, d.status AS draft_status, d.committed_at,
                   o.name AS product_name, o.url AS product_url,
                   (SELECT COALESCE(MAX(h.receipt_revision), 0)
                      FROM human_action AS h WHERE h.offer_key=d.offer_key)
                       AS receipt_revision
              FROM run AS r
              LEFT JOIN draft AS d ON d.run_id=r.run_id
              LEFT JOIN offer AS o ON o.offer_key=d.offer_key
             WHERE r.mode='daily'
             ORDER BY r.target_date DESC, r.started_at DESC,
                      d.committed_at DESC, d.draft_id
            """
        )
        for row in rows:
            run_id = str(row["run_id"])
            target_date = _iso_date(row["target_date"])
            run_status = _status_label(row["run_status"], _RUN_STATUS_LABELS, "run status")
            committed_count = row["committed_count"]
            if (
                isinstance(committed_count, bool)
                or not isinstance(committed_count, int)
                or not 0 <= committed_count <= 15
            ):
                raise DashboardHistoryExportError("history committed count is invalid")
            day = days_by_run.get(run_id)
            if day is None:
                day = {
                    "target_date": target_date,
                    "run_status": run_status,
                    "committed_count": committed_count,
                    "items": [],
                }
                days_by_run[run_id] = day
            elif day["target_date"] != target_date:
                raise DashboardHistoryExportError("daily run date changed during snapshot")

            if row["draft_id"] is None:
                continue
            if row["product_name"] is None or row["body"] is None:
                raise DashboardHistoryExportError("history draft is missing its offer")
            status = _status_label(row["draft_status"], _DRAFT_STATUS_LABELS, "draft status")
            day["items"].append(
                {
                    "draft_id": str(row["draft_id"]),
                    "draft_version": row["draft_version"],
                    "offer_key": str(row["offer_key"]),
                    "receipt_revision": int(row["receipt_revision"]),
                    "events": [],
                    "product_name": str(row["product_name"]),
                    "product_url": row["product_url"],
                    "body": str(row["body"]),
                    "status": status,
                    "target_date": target_date,
                }
            )

        events_by_draft: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        for event in connection.execute(
            """
            SELECT event_id, offer_key, draft_id, draft_version,
                   actual_posted_at, revision
              FROM publication_event
             WHERE valid=1 AND draft_id IS NOT NULL AND draft_version IS NOT NULL
             ORDER BY reported_at DESC, revision DESC, event_id DESC
            """
        ):
            key = (
                str(event["offer_key"]),
                str(event["draft_id"]),
                int(event["draft_version"]),
            )
            events_by_draft.setdefault(key, []).append(
                {
                    "event_id": str(event["event_id"]),
                    "actual_posted_at": event["actual_posted_at"],
                    "revision": int(event["revision"]),
                }
            )
        for day in days_by_run.values():
            for item in day["items"]:
                item["events"] = events_by_draft.get(
                    (item["offer_key"], item["draft_id"], int(item["draft_version"])),
                    [],
                )
        report_counts_map: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            """
            SELECT run.target_date, h.action, h.apply_status, COUNT(*) AS report_count
              FROM human_action AS h
              JOIN draft AS d ON d.offer_key=h.offer_key
                             AND d.draft_id=h.draft_id
                             AND d.version=h.draft_version
              JOIN run ON run.run_id=d.run_id
             WHERE run.mode='daily'
             GROUP BY run.target_date, h.action, h.apply_status
             ORDER BY run.target_date DESC, h.action, h.apply_status
            """
        ):
            target_date = str(row["target_date"])
            report_counts = report_counts_map.setdefault(
                target_date,
                {
                    "target_date": target_date,
                    "action_counts": {
                        action: {status: 0 for status in _REPORT_APPLY_STATUSES}
                        for action in _REPORT_ACTIONS
                    },
                },
            )
            action = str(row["action"])
            apply_status = str(row["apply_status"])
            if action not in _REPORT_ACTIONS or apply_status not in _REPORT_APPLY_STATUSES:
                raise DashboardHistoryExportError("history report action status is invalid")
            report_counts["action_counts"][action][apply_status] = int(
                row["report_count"]
            )
        report_counts_by_date = list(report_counts_map.values())
        connection.execute("ROLLBACK")
        days = sorted(
            days_by_run.values(), key=lambda day: day["target_date"], reverse=True
        )
        return _history_payload(
            {"days": days, "report_counts_by_date": report_counts_by_date}
        )
    except Exception as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        if isinstance(exc, DashboardHistoryExportError):
            raise
        if isinstance(exc, (sqlite3.Error, DashboardExportError)):
            raise DashboardHistoryExportError("daily history could not be read") from exc
        raise


def render_dashboard_history_html(
    state: Mapping[str, Any],
    *,
    api_origin: str | None = None,
    http_mode: bool = False,
    csp_nonce: str = "",
) -> str:
    """Render a self-contained date navigator from an already-read snapshot."""

    try:
        payload = _history_payload(state)
    except DashboardHistoryExportError:
        raise
    except (TypeError, ValueError) as exc:
        raise DashboardHistoryExportError("history snapshot is invalid") from exc
    origin = ""
    if api_origin is not None:
        try:
            parsed_origin = urlsplit(api_origin)
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.hostname != "127.0.0.1"
                or parsed_origin.port is None
                or not 1 <= parsed_origin.port <= 65535
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
                or parsed_origin.username
                or parsed_origin.password
            ):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise DashboardHistoryExportError("history API origin is invalid") from exc
        origin = api_origin
    try:
        history_script = _HISTORY_SCRIPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DashboardHistoryExportError("history browser script is unavailable") from exc
    safe_script = history_script.replace("</script", "<\\/script")
    html = _HTML_TEMPLATE.replace("%%HISTORY_DATA%%", _json_for_html(payload))
    html = html.replace("%%API_ORIGIN%%", _html_escape(origin, quote=True))
    html = html.replace("%%HISTORY_SCRIPT%%", safe_script)
    html = html.replace("%%HISTORY_TRANSPORT%%", "http" if http_mode else "file")
    html = html.replace("%%CSP_NONCE%%", _html_escape(csp_nonce, quote=True))
    html = html.replace("%%REPORT_COUNT_ROWS%%", _report_count_rows())
    if "%%HISTORY_" in html or "%%API_ORIGIN%%" in html or "%%REPORT_" in html:
        raise DashboardHistoryExportError("history HTML template is incomplete")
    return html


def export_dashboard_history_html(
    connection: sqlite3.Connection,
    *,
    output_path: str | Path,
    runtime_root: str | Path,
    api_origin: str | None = None,
) -> DashboardHistoryExportResult:
    """Atomically write one consistent history snapshot and report its counts."""

    try:
        destination, resolved_root = _validated_destination(output_path, runtime_root)
    except DashboardExportError as exc:
        raise DashboardHistoryExportError(str(exc)) from exc
    state = _read_history_state(connection)
    html = render_dashboard_history_html(state, api_origin=api_origin)
    try:
        checked_destination, checked_root = _validated_destination(
            destination, resolved_root
        )
        if checked_destination != destination or checked_root != resolved_root:
            raise DashboardHistoryExportError("runtime output path changed during export")
        _atomic_write(destination, html)
    except DashboardExportError as exc:
        raise DashboardHistoryExportError(str(exc)) from exc
    return DashboardHistoryExportResult(
        path=destination,
        run_count=len(state["days"]),
        draft_count=sum(day["item_count"] for day in state["days"]),
    )


def write_dashboard_history_html(
    connection: sqlite3.Connection,
    *,
    output_path: str | Path,
    runtime_root: str | Path,
    api_origin: str | None = None,
) -> Path:
    """Atomically write a standalone view of every persisted daily run."""

    return export_dashboard_history_html(
        connection,
        output_path=output_path,
        runtime_root=runtime_root,
        api_origin=api_origin,
    ).path
