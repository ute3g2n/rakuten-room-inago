"""Remove credential-shaped values before writing operational artifacts."""

import re


_PATTERNS = [
    re.compile(r"(?i)\b(access[_-]?key|password|passwd|rakuten[_-]?id)\b(\s*[=:]\s*['\"]?)([^\s,'\";}]+)"),
    re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer|basic)\s+)([^\s,'\";}]+)"),
    re.compile(r"(?i)\b(cookie\s*:\s*)([^\r\n]+)"),
]
_SENSITIVE_KEYS = {"accesskey", "access_key", "password", "passwd", "cookie", "authorization", "rakutenid", "rakuten_id"}


def redact(text: str) -> str:
    text = _PATTERNS[0].sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _PATTERNS[1].sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    return _PATTERNS[2].sub(lambda match: f"{match.group(1)}[REDACTED]", text)


def redact_data(value: object) -> object:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else redact_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value
