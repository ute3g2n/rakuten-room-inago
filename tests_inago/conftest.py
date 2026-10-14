"""Shared isolated fixtures for the Inago implementation test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import validate

from automation.jsonschema_formats import rfc3339_format_checker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONFIG = Path(__file__).resolve().parent / "config" / "fixture.json"


def pytest_configure(config: pytest.Config) -> None:
    """Register STEP 19's two explicit offline acceptance phases."""

    config.addinivalue_line(
        "markers",
        "fixture_contract: isolated offline fixture acceptance checks",
    )
    config.addinivalue_line(
        "markers",
        "document_contract: source-document and decision-trace checks",
    )


FIXTURE_CONFIG_SCHEMA = {
    "type": "object",
    "required": [
        "schema_version",
        "environment",
        "network_allowed",
        "target_date",
        "started_at_jst",
        "run_id",
        "seed",
        "settings_version",
        "detailed_writing_rules",
        "offer",
        "word",
        "report",
        "metric",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "environment": {"const": "fixture"},
        "network_allowed": {"const": False},
        "target_date": {"type": "string", "format": "date"},
        "started_at_jst": {"type": "string"},
        "run_id": {"type": "string"},
        "seed": {"type": "string"},
        "settings_version": {"const": "confirmed-0.3-step46-all132"},
        "detailed_writing_rules": {
            "type": "object",
            "required": ["version", "adopted", "enabled"],
            "properties": {
                "version": {"const": "detailed-writing-rules-candidate-0.1-step68"},
                "adopted": {"const": False},
                "enabled": {"const": False},
            },
            "additionalProperties": False,
        },
        "offer": {
            "type": "object",
            "required": ["offer_key", "shop_id", "item_id", "like_count", "test_only"],
            "properties": {
                "offer_key": {"type": "string"},
                "shop_id": {"type": "string"},
                "item_id": {"type": "string"},
                "like_count": {"type": "integer", "minimum": 0},
                "test_only": {"const": True},
            },
            "additionalProperties": False,
        },
        "word": {
            "type": "object",
            "required": ["word_id", "text", "source_rank", "test_only"],
            "properties": {
                "word_id": {"type": "string"},
                "text": {"type": "string"},
                "source_rank": {"type": "integer", "minimum": 1},
                "test_only": {"const": True},
            },
            "additionalProperties": False,
        },
        "report": {
            "type": "object",
            "required": ["action_id", "draft_id", "action", "reported_at", "test_only"],
            "properties": {
                "action_id": {"type": "string"},
                "draft_id": {"type": "string"},
                "action": {"const": "posted"},
                "reported_at": {"type": "string", "format": "date-time"},
                "test_only": {"const": True},
            },
            "additionalProperties": False,
        },
        "metric": {
            "type": "object",
            "required": ["scope", "revenue_yen", "observed_at", "test_only"],
            "properties": {
                "scope": {"const": "overall"},
                "revenue_yen": {"type": ["integer", "null"]},
                "observed_at": {"type": "string", "format": "date-time"},
                "test_only": {"const": True},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


class ExternalCallSpy:
    """Count calls that fixture-mode code must never make."""

    PORT_NAMES = ("database", "browser", "ai", "metrics", "html")

    def __init__(self) -> None:
        self.calls = dict.fromkeys(self.PORT_NAMES, 0)

    def record(self, port_name: str) -> None:
        if port_name not in self.calls:
            raise ValueError(f"unknown external port: {port_name}")
        self.calls[port_name] += 1


@pytest.fixture(scope="session")
def fixture_document() -> dict[str, object]:
    document = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    validate_fixture_document(document)
    return document


def validate_fixture_document(document: dict[str, object]) -> None:
    """Validate one fixture configuration using the shared schema."""
    validate(
        instance=document,
        schema=FIXTURE_CONFIG_SCHEMA,
        format_checker=rfc3339_format_checker(),
    )


@pytest.fixture
def fixture_validator():
    return validate_fixture_document


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def fixture_config_path() -> Path:
    return FIXTURE_CONFIG


@pytest.fixture
def external_call_spy() -> ExternalCallSpy:
    return ExternalCallSpy()
