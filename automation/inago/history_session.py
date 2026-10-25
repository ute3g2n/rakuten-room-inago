"""Short-lived launch tickets and loopback browser sessions for history."""

from __future__ import annotations

import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import os
from pathlib import Path
import secrets
import threading
import time


HISTORY_SERVICE = "inago-history-v1"
SESSION_COOKIE = "inago_history_session"
SESSION_SECONDS = 24 * 60 * 60
TICKET_SECONDS = 60


def history_runtime_id(root: Path | None) -> str:
    """Identify the configured runtime without disclosing its filesystem path."""
    value = os.path.normcase(str(root.resolve())) if root is not None else "fixture"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class HistorySessions:
    """Keep tickets single-use; sign cookies with the existing private API key."""

    def __init__(self, key: str | None):
        self._key = key.encode("utf-8") if key else secrets.token_bytes(32)
        self._tickets: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue_ticket(self) -> str:
        now = time.monotonic()
        value = secrets.token_urlsafe(32)
        with self._lock:
            self._tickets = {
                k: expiry for k, expiry in self._tickets.items() if expiry > now
            }
            self._tickets[value] = now + TICKET_SECONDS
        return value

    def redeem(self, ticket: object) -> str | None:
        if not isinstance(ticket, str) or len(ticket) > 128:
            return None
        with self._lock:
            expiry = self._tickets.pop(ticket, 0)
        if expiry <= time.monotonic():
            return None
        value = f"{int(time.time()) + SESSION_SECONDS}.{secrets.token_urlsafe(24)}"
        signature = hmac.new(
            self._key, value.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return f"{SESSION_COOKIE}={value}.{signature}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict"

    def valid_cookie(self, header: str) -> bool:
        if not header or len(header) > 8192:
            return False
        try:
            cookies = SimpleCookie()
            cookies.load(header)
            morsel = cookies.get(SESSION_COOKIE)
            if morsel is None:
                return False
            expiry, nonce, signature = morsel.value.split(".")
            if int(expiry) <= time.time() or not nonce:
                return False
            value = f"{expiry}.{nonce}"
            expected = hmac.new(
                self._key, value.encode("ascii"), hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(signature, expected)
        except (CookieError, ValueError, UnicodeError, TypeError):
            return False
