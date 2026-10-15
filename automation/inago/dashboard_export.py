"""Generate a self-contained HTML view of confirmed saved drafts."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from automation.inago.export import build_dashboard_state, safe_product_url


_STATUS_LABELS = {
    "committed": "保存済み下書き",
    "human_edited": "本人編集済み",
}

_HTML_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>イナゴスキル 投稿下書き</title>
  <style>
    :root { color-scheme: light; font-family: "Yu Gothic UI", "Meiryo", sans-serif; color: #1b2522; background: #f4f7f5; }
    * { box-sizing: border-box; }
    body { margin: 0; min-width: 320px; line-height: 1.6; }
    header, main, footer { width: min(100% - 2rem, 70rem); margin-inline: auto; }
    header { padding: 2rem 0 1rem; }
    h1 { margin: 0; font-size: clamp(1.7rem, 4vw, 2.4rem); }
    main { display: grid; gap: 1rem; padding-bottom: 2rem; }
    .eyebrow { margin: 0 0 .25rem; color: #23674b; font-size: .82rem; font-weight: 700; }
    .summary, .draft-card { border: 1px solid #d5dfd9; border-radius: .8rem; background: #fff; box-shadow: 0 .15rem .6rem rgb(25 47 35 / 5%); }
    .summary { padding: 1rem; }
    .summary p { margin: .2rem 0; }
    #draft-count { font-size: 1.8rem; }
    #draft-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    .draft-card { display: grid; gap: .65rem; padding: 1rem; min-width: 0; }
    .draft-card h2 { margin: 0; font-size: 1.1rem; overflow-wrap: anywhere; }
    .draft-card p { margin: 0; }
    .meta { color: #65756d; font-size: .88rem; overflow-wrap: anywhere; }
    .status { color: #245b42; font-weight: 700; }
    .draft-card a { width: fit-content; color: #075bba; overflow-wrap: anywhere; }
    .body-text { margin: 0; padding: .8rem; border: 1px solid #d5dfd9; border-radius: .5rem; background: #f8faf9; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
    footer { padding: 0 0 2rem; color: #65756d; font-size: .85rem; }
    @media (max-width: 760px) { #draft-list { grid-template-columns: 1fr; } }
    @media print {
      body { background: #fff; }
      header, main, footer { width: 100%; }
      .summary, .draft-card { box-shadow: none; break-inside: avoid; }
      #draft-list { display: block; }
      .draft-card { margin-bottom: .7rem; }
    }
  </style>
</head>
<body>
  <header>
    <p class="eyebrow">楽天ROOM・本人操作</p>
    <h1>保存済み投稿下書き</h1>
    <p>このファイルだけで本文を閲覧できます。閲覧は投稿報告になりません。</p>
  </header>
  <main>
    <section class="summary" aria-label="出力状態">
      <p id="run-date"></p>
      <p>表示する保存済み本文: <strong id="draft-count">%%DASHBOARD_COUNT%%</strong>件</p>
    </section>
    <section id="draft-list" aria-label="保存済み本文" aria-live="polite"></section>
  </main>
  <footer><p>投稿するかどうかは本人が判断します。システムはROOMへ投稿しません。</p></footer>
  <script id="dashboard-data" type="application/json">%%DASHBOARD_DATA%%</script>
  <script>
    (() => {
      const data = JSON.parse(document.getElementById("dashboard-data").textContent);
      const runDate = document.getElementById("run-date");
      if (data.run_date) runDate.textContent = `最終日次run日: ${data.run_date}`;
      const list = document.getElementById("draft-list");
      if (data.items.length === 0) {
        const empty = document.createElement("p");
        empty.textContent = "保存済みの本文はありません。";
        list.append(empty);
        return;
      }
      for (const item of data.items) {
        const card = document.createElement("article");
        card.className = "draft-card";
        const status = document.createElement("p");
        status.className = "status";
        status.textContent = item.status;
        card.append(status);
        const title = document.createElement("h2");
        title.textContent = item.product_name;
        card.append(title);
        const meta = document.createElement("p");
        meta.className = "meta";
        meta.textContent = `${item.target_date}・下書き ${item.draft_version}版`;
        card.append(meta);
        if (item.product_url) {
          const link = document.createElement("a");
          link.href = item.product_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "商品ページを開く";
          card.append(link);
        } else {
          const unavailable = document.createElement("p");
          unavailable.className = "meta";
          unavailable.textContent = "商品URLを安全に表示できないため、リンクを省略しました。";
          card.append(unavailable);
        }
        const body = document.createElement("pre");
        body.className = "body-text";
        body.textContent = item.body;
        card.append(body);
        list.append(card);
      }
    })();
  </script>
</body>
</html>
"""


class DashboardExportError(RuntimeError):
    """The standalone dashboard could not be safely or atomically written."""


def _has_reparse_point(path: Path) -> bool:
    cursor = path
    while True:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            return True
        else:
            attributes = getattr(info, "st_file_attributes", 0)
            if cursor.is_symlink() or attributes & 0x400:
                return True
        if cursor.parent == cursor:
            return False
        cursor = cursor.parent


def _iso_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise DashboardExportError(f"{field} is not a date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DashboardExportError(f"{field} is not a date") from exc
    if parsed.isoformat() != value:
        raise DashboardExportError(f"{field} is not a canonical date")
    return value


def _dashboard_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    raw_items = state.get("items")
    if not isinstance(raw_items, list):
        raise DashboardExportError("dashboard snapshot has no item list")

    latest_run = state.get("latest_run")
    run_date = (
        _iso_date(latest_run.get("target_date"), "run target_date")
        if isinstance(latest_run, Mapping)
        else None
    )
    items: list[dict[str, Any]] = []
    for row in raw_items:
        if not isinstance(row, Mapping):
            raise DashboardExportError("dashboard snapshot contains an invalid row")
        status_code = row.get("draft_status")
        status_label = _STATUS_LABELS.get(status_code)
        if status_label is None:
            continue
        product_name = row.get("product_name")
        body = row.get("body")
        if not isinstance(product_name, str) or not isinstance(body, str):
            raise DashboardExportError("confirmed draft text is invalid")
        draft_version = row.get("draft_version")
        if (
            isinstance(draft_version, bool)
            or not isinstance(draft_version, int)
            or draft_version < 1
        ):
            raise DashboardExportError("confirmed draft version is invalid")
        items.append(
            {
                "product_name": product_name,
                "product_url": safe_product_url(row.get("product_url")),
                "body": body,
                "status": status_label,
                "draft_version": draft_version,
                "target_date": _iso_date(row.get("target_date"), "target_date"),
            }
        )
    return {"run_date": run_date, "items": items}


def _json_for_html(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _render_html(state: Mapping[str, Any]) -> str:
    payload = _dashboard_payload(state)
    data = _json_for_html(payload)
    return _HTML_TEMPLATE.replace(
        "%%DASHBOARD_COUNT%%", str(len(payload["items"]))
    ).replace("%%DASHBOARD_DATA%%", data)


def render_standalone_dashboard_html(state: Mapping[str, Any]) -> str:
    """Render the standalone page from an already-read dashboard snapshot."""

    return _render_html(state)


def _validated_destination(
    output_path: str | Path,
    runtime_root: str | Path,
) -> tuple[Path, Path]:
    try:
        root = Path(runtime_root).expanduser().absolute()
        destination = Path(output_path).expanduser()
        if not destination.is_absolute():
            destination = root / destination
        destination = destination.absolute()
        if _has_reparse_point(root) or _has_reparse_point(destination):
            raise DashboardExportError("runtime output path contains a reparse point")
        resolved_root = root.resolve(strict=True)
        resolved_destination = destination.resolve(strict=False)
        resolved_destination.relative_to(resolved_root)
    except DashboardExportError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise DashboardExportError("runtime output path is invalid") from exc

    if not resolved_root.is_dir():
        raise DashboardExportError("runtime root is not a directory")
    if not resolved_destination.parent.is_dir():
        raise DashboardExportError("runtime output directory does not exist")
    if resolved_destination == resolved_root or resolved_destination.exists() and not resolved_destination.is_file():
        raise DashboardExportError("runtime output target is not a file")
    return resolved_destination, resolved_root


def _atomic_write(destination: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    except OSError as exc:
        raise DashboardExportError("standalone HTML save failed") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def write_standalone_dashboard_html(
    connection: sqlite3.Connection,
    *,
    output_path: str | Path,
    runtime_root: str | Path,
) -> Path:
    """Write one self-contained view from a consistent persisted DB snapshot."""

    destination, resolved_root = _validated_destination(output_path, runtime_root)
    state = build_dashboard_state(connection)
    html = render_standalone_dashboard_html(state)
    # Recheck the root boundary immediately before creating a file beside the target.
    checked_destination, checked_root = _validated_destination(destination, resolved_root)
    if checked_destination != destination or checked_root != resolved_root:
        raise DashboardExportError("runtime output path changed during export")
    _atomic_write(destination, html)
    return destination
