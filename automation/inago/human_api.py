"""Loopback-only HTTP receipt API for night reports from the owner."""

from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from automation.inago.clock import Clock, SystemClock
from automation.inago.contracts import (
    IdempotencyConflict,
    RejectedContract,
)
from automation.inago.export import build_dashboard_state
from automation.inago.repository import (
    ReceiptRevisionConflict,
    record_pending_human_action,
)
from automation.jsonschema_formats import rfc3339_format_checker


MAX_REQUEST_BYTES = 64 * 1024
_REQUEST_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "human-action.schema.json"
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_FILES = {
    "/": (
        _PROJECT_ROOT / "automation" / "inago" / "templates" / "posting-dashboard.html",
        "text/html; charset=utf-8",
    ),
    "/dashboard-assets/app.js": (
        _PROJECT_ROOT / "rakuten-room-inago" / "dashboard-assets" / "app.js",
        "text/javascript; charset=utf-8",
    ),
    "/dashboard-assets/style.css": (
        _PROJECT_ROOT / "rakuten-room-inago" / "dashboard-assets" / "style.css",
        "text/css; charset=utf-8",
    ),
}
_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


@lru_cache(maxsize=1)
def _request_validator() -> Draft202012Validator:
    try:
        schema = json.loads(_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError("human report request schema cannot be loaded") from exc
    return Draft202012Validator(schema, format_checker=rfc3339_format_checker())


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_report(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RejectedContract("report body must be strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise RejectedContract("report body must be a JSON object")
    try:
        _request_validator().validate(payload)
    except ValidationError as exc:
        raise RejectedContract("human report request schema violation") from exc
    return payload


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class _LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class _HumanAPIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        # Avoid writing report text or local identifiers into an ambient log.
        return

    @property
    def _api_server(self) -> Any:
        return self.server

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def _authentication_is_valid(self) -> bool:
        expected = self._api_server.auth_token
        if expected is None:
            return True
        scheme, separator, supplied = self.headers.get("Authorization", "").partition(
            " "
        )
        return (
            separator == " "
            and scheme.casefold() == "bearer"
            and bool(supplied)
            and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
        )

    def _require_authentication(self) -> bool:
        if self._authentication_is_valid():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication_required"})
        return False

    def _send_static_file(self, path: Path, content_type: str) -> None:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(_PROJECT_ROOT)
            body = resolved.read_bytes()
        except (OSError, RuntimeError, ValueError):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def _origin_is_valid(self, *, require_origin: bool = True) -> bool:
        if self.client_address[0] != "127.0.0.1":
            return False
        host = self.headers.get("Host")
        expected = urlsplit(self._api_server.origin)
        if host is None or host.lower() != expected.netloc.lower():
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return not require_origin
        try:
            parsed = urlsplit(origin)
            return (
                parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
                and parsed.port == expected.port
                and parsed.netloc.lower() == host.lower()
                and not parsed.username
                and not parsed.password
                and parsed.path == ""
                and parsed.query == ""
                and parsed.fragment == ""
            )
        except ValueError:
            return False

    def do_GET(self) -> None:
        if self.path.startswith("/api/") and not self._require_authentication():
            return
        if not self._origin_is_valid(require_origin=False):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "origin_rejected"},
            )
            return
        static_file = _DASHBOARD_FILES.get(self.path)
        if static_file is not None:
            self._send_static_file(*static_file)
            return
        if self.path != "/api/state":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = self._api_server.connection_factory()
            state = build_dashboard_state(connection)
            state["csrf_token"] = self._api_server.csrf_token
            self._send_json(HTTPStatus.OK, state)
        except (sqlite3.Error, OSError):
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "storage_unavailable"},
            )
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"}
            )
        finally:
            if connection is not None:
                connection.close()

    def _csrf_is_valid(self) -> bool:
        supplied = self.headers.get("X-CSRF-Token", "")
        expected = self._api_server.csrf_token
        if not supplied or len(supplied) > 256:
            return False
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))

    def _read_report_body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"saved": False, "error": "transfer_encoding_rejected"},
            )
            return None
        if self.headers.get_content_type().lower() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"saved": False, "error": "content_type_rejected"},
            )
            return None
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(
                HTTPStatus.LENGTH_REQUIRED,
                {"saved": False, "error": "content_length_required"},
            )
            return None
        if length > MAX_REQUEST_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"saved": False, "error": "body_too_large"},
            )
            return None
        try:
            raw = self.rfile.read(length)
        except OSError:
            raw = b""
        if len(raw) != length:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"saved": False, "error": "body_incomplete"},
            )
            return None
        return raw

    def do_POST(self) -> None:
        if not self._require_authentication():
            return
        if not self._origin_is_valid():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"saved": False, "error": "origin_rejected"},
            )
            return
        if not self._csrf_is_valid():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"saved": False, "error": "csrf_rejected"},
            )
            return
        if self.path != "/api/reports":
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"saved": False, "error": "not_found"},
            )
            return
        raw = self._read_report_body()
        if raw is None:
            return
        try:
            payload = _parse_report(raw)
        except RejectedContract:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"saved": False, "error": "invalid_report"},
            )
            return

        connection: sqlite3.Connection | None = None
        try:
            connection = self._api_server.connection_factory()
            receipt = record_pending_human_action(
                connection,
                payload,
                clock=self._api_server.clock,
            )
            self._send_json(HTTPStatus.CREATED, receipt)
        except ReceiptRevisionConflict as exc:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "saved": False,
                    "error": "stale_revision",
                    "receipt_revision": exc.current_revision,
                    "reload_required": True,
                },
            )
        except IdempotencyConflict:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"saved": False, "error": "action_id_conflict"},
            )
        except RejectedContract:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"saved": False, "error": "invalid_report"},
            )
        except (sqlite3.Error, OSError):
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"saved": False, "error": "storage_unavailable"},
            )
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"saved": False, "error": "internal_error"},
            )
        finally:
            if connection is not None:
                connection.close()


def create_server(
    *,
    connection_factory: Callable[[], sqlite3.Connection],
    clock: Clock | None = None,
    bind_host: str = "127.0.0.1",
    port: int = 0,
    auth_token: str | None = None,
) -> ThreadingHTTPServer:
    """Create the explicit local report server; it cannot bind off-loopback."""

    if bind_host != "127.0.0.1":
        raise ValueError("human report API must bind only to 127.0.0.1")
    if not callable(connection_factory):
        raise TypeError("connection_factory must be callable")
    if auth_token is not None and (
        not isinstance(auth_token, str) or len(auth_token) < 32
    ):
        raise ValueError("human report API auth token must be at least 32 characters")
    csrf_token = secrets.token_urlsafe(32)
    server = _LoopbackHTTPServer((bind_host, port), _HumanAPIHandler)
    actual_port = int(server.server_address[1])
    server.origin = f"http://127.0.0.1:{actual_port}"
    server.csrf_token = csrf_token
    server.auth_token = auth_token
    server.clock = clock or SystemClock()
    server.connection_factory = connection_factory
    return server
