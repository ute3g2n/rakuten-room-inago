"""Strict JSON Schema format checkers shared by the Inago runtime."""

from __future__ import annotations

from datetime import datetime
import re

from jsonschema import FormatChecker


_RFC3339_DATE_TIME = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z"
)


def _is_rfc3339_date_time(value: object) -> bool:
    # JSON Schema's format annotation applies to strings. A schema may still
    # permit null (for example, an unavailable fetched_at timestamp).
    if not isinstance(value, str):
        return True
    if not _RFC3339_DATE_TIME.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def rfc3339_format_checker() -> FormatChecker:
    """Return a checker that validates RFC3339 strings and leaves other types alone."""

    checker = FormatChecker()
    checker.checks("date-time")(_is_rfc3339_date_time)
    return checker
