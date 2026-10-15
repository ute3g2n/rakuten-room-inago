"""Build a read-only, version-aware view for the local posting dashboard."""

from __future__ import annotations

import ipaddress
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from automation.inago.statistics import summarize_metrics


_ACTION_LABELS = {
    "posted": "投稿した報告",
    "skipped": "見送り報告",
    "correction": "訂正報告",
    "edit": "本文編集報告",
}
_DRAFT_LABELS = {
    "committed": "保存済み下書き",
    "human_edited": "本人編集済み",
    "posted": "投稿反映済み",
    "skipped": "見送り反映済み",
    "do_not_post_already_posted": "既投稿のため後続版は投稿対象外",
}
_REPORT_LABELS = {
    "pending": "受付済み・翌日管理反映待ち",
    "applied": "管理反映済み",
    "rejected": "管理反映されていません",
}


def safe_product_url(value: Any) -> str | None:
    """Return a safe HTTPS link, or None when the stored URL is unsafe."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or value != value.strip()
    ):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme.casefold() != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or (port is not None and port != 443)
    ):
        return None
    hostname = host.casefold().rstrip(".")
    if (
        not hostname
        or any(character.isspace() for character in hostname)
        or hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
    ):
        return None
    if re.fullmatch(
        r"(?i)(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}",
        hostname,
    ):
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        return None
    if parts.netloc.startswith("["):
        return None
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    path = parts.path or "/"
    return urlunsplit(("https", netloc, path, parts.query, ""))


def _metrics_json(connection: sqlite3.Connection) -> dict[str, Any]:
    summary = summarize_metrics(connection)

    def total(value: Any) -> dict[str, Any]:
        return {
            "known_total": value.known_total,
            "observed_count": value.observed_count,
            "missing_count": value.missing_count,
        }

    return {
        "affiliate_daily": {
            name: total(value) for name, value in summary.affiliate_daily.items()
        },
        "order_count": total(summary.order_count),
        "order_windows": {
            name: total(value) for name, value in summary.order_windows.items()
        },
        "order_unresolved_count": summary.order_unresolved_count,
        "orders_current_sales_yen": summary.orders_current_sales_yen,
        "orders_current_commission_unconfirmed_yen": (
            summary.orders_current_commission_unconfirmed_yen
        ),
        "room_post_count": summary.room_post_count,
        "room_missing_count": summary.room_missing_count,
        "room_likes": total(summary.room_likes),
    }


def _report_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "action_id": str(row["action_id"]),
        "action": str(row["action"]),
        "action_label": _ACTION_LABELS.get(str(row["action"]), "報告"),
        "offer_key": str(row["offer_key"]),
        "product_name": str(row["product_name"]),
        "product_url": safe_product_url(row["product_url"]),
        "draft_id": str(row["draft_id"]),
        "draft_version": int(row["draft_version"]),
        "body": str(row["body"]),
        "pattern_id": str(row["pattern_id"]),
        "pattern_version": str(row["pattern_version"]),
        "settings_version": str(row["settings_version"]),
        "receipt_revision": int(row["receipt_revision"]),
        "reason": row["reason"],
        "correction_ref": row["old_event_ref"] or row["correction_action_ref"],
        "actual_posted_at": row["actual_posted_at"],
        "reported_at": str(row["reported_at"]),
        "apply_status": str(row["apply_status"]),
        "apply_status_label": _REPORT_LABELS.get(str(row["apply_status"]), "状態不明"),
        "applied_at": row["applied_at"],
        "apply_error": row["apply_error"],
    }


def build_dashboard_state(connection: sqlite3.Connection) -> dict[str, Any]:
    """Read all dashboard values from one consistent SQLite snapshot."""

    if connection.in_transaction:
        raise RuntimeError("dashboard export requires an idle connection")
    connection.execute("BEGIN")
    try:
        runs = [
            dict(row)
            for row in connection.execute(
                """
                SELECT run_id, target_date, status, settings_version,
                       committed_count, metrics_status, finished_at
                  FROM run WHERE mode='daily'
                 ORDER BY target_date DESC, started_at DESC, run_id DESC
                 LIMIT 30
                """
            )
        ]
        latest_run = runs[0] if runs else None
        drafts = [
            {
                "offer_key": str(row["offer_key"]),
                "draft_id": str(row["draft_id"]),
                "draft_version": int(row["version"]),
                "status": str(row["status"]),
            }
            for row in connection.execute(
                """
                SELECT offer_key, draft_id, version, status
                  FROM draft
                 ORDER BY offer_key, version DESC, draft_id
                """
            )
        ]
        reservations = connection.execute(
            """
            SELECT r.offer_key, r.reservation_id, r.draft_id,
                   o.name AS product_name, o.url AS product_url,
                   d.version AS draft_version, d.body, d.status AS draft_status,
                   d.pattern_id, d.pattern_version, d.generated_at, d.run_id,
                   run.target_date, run.settings_version AS run_settings_version,
                   run.committed_count
              FROM reservation AS r
              JOIN offer AS o ON o.offer_key=r.offer_key
              JOIN draft AS d ON d.offer_key=r.offer_key AND d.draft_id=r.draft_id
              JOIN run ON run.run_id=d.run_id
             WHERE r.status='active'
             ORDER BY run.target_date DESC, d.committed_at DESC,
                      r.offer_key, d.version DESC
            """
        ).fetchall()
        do_not_post_drafts = [
            {
                "offer_key": str(row["offer_key"]),
                "product_name": str(row["product_name"]),
                "draft_id": str(row["draft_id"]),
                "draft_version": int(row["version"]),
                "draft_status": str(row["status"]),
                "draft_status_label": _DRAFT_LABELS[str(row["status"])],
                "pattern_id": str(row["pattern_id"]),
                "pattern_version": str(row["pattern_version"]),
                "settings_version": str(row["settings_version"]),
                "target_date": str(row["target_date"]),
            }
            for row in connection.execute(
                """
                SELECT d.offer_key, o.name AS product_name, d.draft_id, d.version,
                       d.status, d.pattern_id, d.pattern_version,
                       run.settings_version, run.target_date
                  FROM draft AS d
                  JOIN offer AS o ON o.offer_key=d.offer_key
                  JOIN run ON run.run_id=d.run_id
                 WHERE d.status='do_not_post_already_posted'
                 ORDER BY run.target_date DESC, d.committed_at DESC,
                          d.offer_key, d.version DESC
                """
            )
        ]
        revisions = {
            str(row["offer_key"]): int(row["receipt_revision"])
            for row in connection.execute(
                """
                SELECT o.offer_key, COALESCE(MAX(h.receipt_revision), 0) AS receipt_revision
                  FROM offer AS o
                  LEFT JOIN human_action AS h ON h.offer_key=o.offer_key
                 GROUP BY o.offer_key ORDER BY o.offer_key
                """
            )
        }
        events_by_offer: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            """
            SELECT event_id, offer_key, actual_posted_at, room_url, revision
              FROM publication_event WHERE valid=1
             ORDER BY reported_at DESC, revision DESC, event_id DESC
            """
        ):
            events_by_offer.setdefault(str(row["offer_key"]), []).append(
                {
                    "event_id": str(row["event_id"]),
                    "actual_posted_at": row["actual_posted_at"],
                    "room_url": safe_product_url(row["room_url"]),
                    "revision": int(row["revision"]),
                }
            )
        pending = [
            _report_row(row)
            for row in connection.execute(
                """
                SELECT h.action_id, h.action, h.offer_key, o.name AS product_name,
                       o.url AS product_url, h.draft_id, h.draft_version,
                       d.body, d.pattern_id, d.pattern_version,
                       run.settings_version AS settings_version,
                       h.receipt_revision, h.reason, h.old_event_ref,
                       h.correction_action_ref,
                       h.actual_posted_at, h.reported_at, h.apply_status,
                       h.applied_at, h.apply_error
                  FROM human_action AS h
                  JOIN offer AS o ON o.offer_key=h.offer_key
                  JOIN draft AS d ON d.offer_key=h.offer_key
                                AND d.draft_id=h.draft_id
                                AND d.version=h.draft_version
                  JOIN run ON run.run_id=d.run_id
                 WHERE h.apply_status='pending'
                 ORDER BY h.reported_at, h.receipt_revision, h.action_id
                """
            )
        ]
        recent = [
            _report_row(row)
            for row in connection.execute(
                """
                SELECT h.action_id, h.action, h.offer_key, o.name AS product_name,
                       o.url AS product_url, h.draft_id, h.draft_version,
                       d.body, d.pattern_id, d.pattern_version,
                       run.settings_version AS settings_version,
                       h.receipt_revision, h.reason, h.old_event_ref,
                       h.correction_action_ref,
                       h.actual_posted_at, h.reported_at, h.apply_status,
                       h.applied_at, h.apply_error
                  FROM human_action AS h
                  JOIN offer AS o ON o.offer_key=h.offer_key
                  JOIN draft AS d ON d.offer_key=h.offer_key
                                AND d.draft_id=h.draft_id
                                AND d.version=h.draft_version
                  JOIN run ON run.run_id=d.run_id
                 WHERE h.apply_status!='pending'
                 ORDER BY COALESCE(h.applied_at, h.reported_at) DESC,
                          h.receipt_revision DESC, h.action_id DESC
                 LIMIT 40
                """
            )
        ]
        items: list[dict[str, Any]] = []
        invalid_active_count = 0
        for row in reservations:
            if row["draft_status"] not in {"committed", "human_edited"}:
                invalid_active_count += 1
                continue
            offer_key = str(row["offer_key"])
            items.append(
                {
                    "offer_key": offer_key,
                    "product_name": str(row["product_name"]),
                    "product_url": safe_product_url(row["product_url"]),
                    "draft_id": str(row["draft_id"]),
                    "draft_version": int(row["draft_version"]),
                    "body": str(row["body"]),
                    "draft_status": str(row["draft_status"]),
                    "draft_status_label": _DRAFT_LABELS[str(row["draft_status"])],
                    "pattern_id": str(row["pattern_id"]),
                    "pattern_version": str(row["pattern_version"]),
                    "settings_version": str(row["run_settings_version"]),
                    "run_id": str(row["run_id"]),
                    "target_date": str(row["target_date"]),
                    "receipt_revision": revisions.get(offer_key, 0),
                    "events": events_by_offer.get(offer_key, []),
                    "generated_at": str(row["generated_at"]),
                }
            )
        generated_total = int(
            connection.execute(
                "SELECT COALESCE(SUM(committed_count), 0) FROM run WHERE mode='daily'"
            ).fetchone()[0]
        )
        reported_posted_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE valid=1"
            ).fetchone()[0]
        )
        applied_report_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM human_action WHERE apply_status='applied'"
            ).fetchone()[0]
        )
        state = {
            "schema_version": 1,
            "latest_run": latest_run,
            "runs": runs,
            "drafts": drafts,
            "summary": {
                "daily_target": 15,
                "available_count": len(items),
                "reserved_count": len(reservations),
                "invalid_active_reservation_count": invalid_active_count,
                "do_not_post_count": len(do_not_post_drafts),
                "generated_count_total": generated_total,
                "latest_run_generated_count": (
                    int(latest_run["committed_count"]) if latest_run else None
                ),
                "reported_posted_count": reported_posted_count,
                "pending_report_count": len(pending),
                "applied_report_count": applied_report_count,
            },
            "items": items,
            "do_not_post_drafts": do_not_post_drafts,
            "receipt_revisions": revisions,
            "pending_reports": pending,
            "recent_reports": recent,
            "metrics": _metrics_json(connection),
        }
        connection.commit()
        return state
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
