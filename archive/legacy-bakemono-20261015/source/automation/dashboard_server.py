"""Local-only dashboard server and CSRF-protected posting-state API."""

import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from automation.atomic_io import atomic_write_json
from automation.schema_validator import validate_document


JST = timezone(timedelta(hours=9))
PRODUCT_ID = re.compile(r"^P[0-9]{2,}$")


class ConflictError(ValueError):
    """A saved posting state changed after the browser loaded it."""


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _update_status(root: Path, payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"product_id", "scheduled_date", "posted", "confirm_correction", "reason", "expected_revision"}:
        raise ValueError("invalid request fields")
    product_id = payload.get("product_id")
    day = payload.get("scheduled_date")
    posted = payload.get("posted")
    if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
        raise ValueError("invalid product ID")
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("invalid scheduled date")
    if not isinstance(posted, bool):
        raise ValueError("posted must be a boolean")
    plan_path = root / "daily-plans" / f"{day}.json"
    if not plan_path.is_file():
        raise ValueError("scheduled day was not found")
    plan = _read_json(plan_path)
    validate_document("daily-plan", plan)
    if plan["target_date"] != day or product_id not in {post["product_id"] for post in plan["posts"]}:
        raise ValueError("product is not planned on this day")
    path = root / "post-status.json"
    status = _read_json(path)
    validate_document("post-status", status)
    item = status["items"].get(product_id)
    if "expected_revision" in payload:
        expected = payload["expected_revision"]
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise ValueError("invalid expected revision")
        current = len(item["correction_history"]) if item else 0
        if expected != current:
            raise ConflictError("posting state changed in another session")
    if item and item["posted"] == posted:
        return item
    if not posted:
        if not item or not item["posted"]:
            raise ValueError("there is no posted check to correct")
        reason = payload.get("reason")
        if payload.get("confirm_correction") is not True or not isinstance(reason, str) or not reason.strip():
            raise ValueError("correction confirmation and reason are required")
        note = reason.strip()
    else:
        note = "本人が投稿済みを確認"
    now = _now()
    if item is None:
        item = {"product_id": product_id, "scheduled_date": day, "posted": False,
                "ever_marked_posted": False, "posted_at": None, "updated_at": now,
                "correction_history": []}
    item["scheduled_date"] = day
    item["posted"] = posted
    item["ever_marked_posted"] = item["ever_marked_posted"] or posted
    if posted:
        item["posted_at"] = now
    item["updated_at"] = now
    item["correction_history"].append({"at": now, "reason": note})
    status["items"][product_id] = item
    validate_document("post-status", status)
    atomic_write_json(path, status)
    return item


def make_server(root: Path, port: int = 8765) -> ThreadingHTTPServer:
    root = Path(root).resolve()
    asset_dir = root / "dashboard-assets"
    token = secrets.token_urlsafe(32)
    update_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def _valid_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def _headers(self, content_type: str, length: int, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()

        def _json(self, data: dict, status: int = 200) -> None:
            content = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
            self._headers("application/json; charset=utf-8", len(content), status)
            self.wfile.write(content)

        def do_GET(self):
            if not self._valid_host():
                self._json({"error": "HOST_FORBIDDEN"}, 403)
                return
            if self.path == "/api/session":
                try:
                    status = _read_json(root / "post-status.json")
                    validate_document("post-status", status)
                except (OSError, ValueError):
                    self._json({"error": "STATUS_UNAVAILABLE"}, 503)
                    return
                self._json({"csrf_token": token, "post_status": status})
                return
            if self.path == "/api/status":
                try:
                    status = _read_json(root / "post-status.json")
                    validate_document("post-status", status)
                except (OSError, ValueError):
                    self._json({"error": "STATUS_UNAVAILABLE"}, 503)
                    return
                self._json(status)
                return
            if self.path == "/api/dashboard-errors":
                try:
                    path = root / "dashboard-error.json"
                    value = _read_json(path) if path.is_file() else {"status": "ok", "errors": []}
                    if value.get("status") not in {"ok", "error"} or not isinstance(value.get("errors"), list):
                        raise ValueError("invalid dashboard error document")
                except (OSError, ValueError):
                    self._json({"error": "DASHBOARD_ERRORS_UNAVAILABLE"}, 503)
                    return
                self._json(value)
                return
            files = {"/": (root / "posting-dashboard.html", "text/html; charset=utf-8"),
                     "/posting-dashboard.html": (root / "posting-dashboard.html", "text/html; charset=utf-8"),
                     "/dashboard-assets/app.js": (asset_dir / "app.js", "text/javascript; charset=utf-8"),
                     "/dashboard-assets/style.css": (asset_dir / "style.css", "text/css; charset=utf-8")}
            if self.path not in files:
                self._json({"error": "NOT_FOUND"}, 404)
                return
            path, content_type = files[self.path]
            try:
                content = path.read_bytes()
            except OSError:
                self._json({"error": "FILE_UNAVAILABLE"}, 503)
                return
            self._headers(content_type, len(content))
            self.wfile.write(content)

        def do_POST(self):
            if not self._valid_host() or self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}":
                self._json({"error": "ORIGIN_FORBIDDEN"}, 403)
                return
            if self.path != "/api/post-status":
                self._json({"error": "NOT_FOUND"}, 404)
                return
            if self.headers.get("X-CSRF-Token") != token:
                self._json({"error": "CSRF_INVALID"}, 403)
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._json({"error": "CONTENT_TYPE_INVALID"}, 415)
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
                if length <= 0 or length > 4096:
                    raise ValueError("invalid body length")
                payload = json.loads(self.rfile.read(length))
                with update_lock:
                    item = _update_status(root, payload)
            except ConflictError as error:
                self._json({"error": "STATE_CONFLICT", "detail": str(error)}, 409)
                return
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._json({"error": "INVALID_REQUEST", "detail": str(error)}, 400)
                return
            self._json({"status": "saved", "item": item})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
