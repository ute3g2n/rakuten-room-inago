from jsonschema import Draft202012Validator
import pytest

from automation.jsonschema_formats import rfc3339_format_checker


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-12T21:30:00+09:00", True),
        ("2026-10-12T12:30:00Z", True),
        ("2026-10-12T12:30:00.123Z", True),
        ("not-a-date-time", False),
        ("2026-10-12T21:30:00", False),
        ("2026-99-99T25:00:00+09:00", False),
        ("2026-10-12T21:30:00+0900", False),
    ],
)
def test_rfc3339_date_time_format(value, expected):
    validator = Draft202012Validator(
        {"type": "string", "format": "date-time"},
        format_checker=rfc3339_format_checker(),
    )

    assert validator.is_valid(value) is expected


def test_nullable_date_time_accepts_null_and_still_checks_string_values():
    validator = Draft202012Validator(
        {"type": ["string", "null"], "format": "date-time"},
        format_checker=rfc3339_format_checker(),
    )

    assert validator.is_valid(None)
    assert validator.is_valid("2026-10-12T21:30:00+09:00")
    assert not validator.is_valid("not-a-date-time")
