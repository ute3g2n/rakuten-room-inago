from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from automation.inago.copy_service import _check_spec
from automation.inago.production_runtime import _make_copy_spec_factory


def test_copy_spec_factory_includes_every_fact_evidence_reference(tmp_path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE offer (
            offer_key TEXT PRIMARY KEY,
            shop_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            image_refs_json TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL
        );
        CREATE TABLE draft (
            run_id TEXT NOT NULL,
            offer_key TEXT NOT NULL,
            version INTEGER NOT NULL
        );
        """
    )
    offer_key = "shop:item-1"
    facts = [
        {"source_ref": "fact:observed:01", "value": "fixture value"},
        {"source_ref": "fact:observed:01", "value": "duplicate reference"},
        {"source_ref": "fact:observed:02", "value": "another fixture value"},
    ]
    connection.execute(
        """
        INSERT INTO offer (offer_key, shop_id, item_id, facts_json, image_refs_json, name, url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            offer_key,
            "shop",
            "item-1",
            json.dumps(facts),
            "[]",
            "Fixture offer",
            "https://example.test/item",
        ),
    )
    settings_runtime = SimpleNamespace(
        active_basic_version="confirmed-0.3-step46-all132",
        themes=({"theme_id": "G01-T01", "name": "fixture theme"},),
    )
    make_spec = _make_copy_spec_factory(
        SimpleNamespace(runtime_root=tmp_path), settings_runtime
    )

    spec = make_spec(
        connection,
        run_id="INAGO-TEST-01",
        offer_key=offer_key,
        theme_id="G01-T01",
        target_date="2026-10-15",
        settings_version="confirmed-0.3-step46-all132",
        deadline_at="2026-10-15T17:45:00+09:00",
        deadline_monotonic=123.0,
    )

    assert spec is not None
    _check_spec(spec)
    assert spec.input_refs == (
        "offer:shop:item-1",
        "fact:observed:01",
        "fact:observed:02",
    )
    assert spec.evidence_refs == ("fact:observed:01", "fact:observed:02")
