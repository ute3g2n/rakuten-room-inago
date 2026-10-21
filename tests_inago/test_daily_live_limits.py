"""Daily row budgets and lossless migration of historical fifty-row searches."""

from dataclasses import replace

import pytest

from automation.inago import repository
from automation.inago.contracts import UnsupportedSchemaVersion
from tests_inago.test_step13 import RUN_ID, _make_ports, _run, runtime_case
from tests_inago.test_step21_browser import room_html, room_request, source, ui_server
from tests_inago.test_step21_schema import legacy_v2


def test_new_daily_searches_take_twenty_five_rows(runtime_case):
    bundle = _make_ports(runtime_case, posts_per_search=30)

    result = _run(runtime_case, bundle)

    assert result.status == "complete" and result.committed_count == 15
    assert len(bundle.room.requests) == 3
    assert all(request.max_posts == 25 for request in bundle.room.requests)
    counts = runtime_case.connection.execute(
        "SELECT max_posts,observed_posts,loaded_posts FROM search_attempt WHERE run_id=?",
        (RUN_ID,),
    ).fetchall()
    assert [tuple(row) for row in counts] == [(25, 25, 30)] * 3


def test_browser_stops_product_resolution_at_twenty_five(tmp_path, ui_server):
    ui_server["html"] = room_html(80, initial=30)
    port = source(tmp_path, ui_server)
    request = replace(room_request(), max_posts=25, deadline_at=None)

    result = port.search(request)

    assert result["status"] == "observed", result.get("error_code")
    assert result["max_posts"] == 25
    assert len(result["posts"]) == 25
    assert result["loaded_posts"] == 30
    assert result["end_reached"] is False
    assert len(port._facts) == 25


def _v6(connection, monkeypatch):
    with monkeypatch.context() as context:
        context.setattr(repository, "CURRENT_SCHEMA_VERSION", 6)
        assert repository.initialize_database(connection) == 6
    connection.execute("UPDATE search_attempt SET retry_of='A1' WHERE attempt_id='A2'")


def _rows(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "AND name != 'schema_migration' ORDER BY name"
    )]
    return {name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"')]
            for name in tables}


def test_v7_preserves_v6_searches_and_references(legacy_v2, monkeypatch):
    _v6(legacy_v2, monkeypatch)
    before = _rows(legacy_v2)

    assert repository.initialize_database(legacy_v2) == 7

    assert _rows(legacy_v2) == before
    assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert legacy_v2.execute("PRAGMA foreign_key_check").fetchall() == []
    assert legacy_v2.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    legacy_v2.execute(
        "INSERT INTO search_attempt(attempt_id,run_id,query,parent_refs_json,level,attempt_no,"
        "started_at,source_status,source_order,max_posts) "
        "SELECT 'A25',run_id,query,parent_refs_json,level,10,started_at,'invalid','internal',25 "
        "FROM search_attempt WHERE attempt_id='A1'"
    )
    assert legacy_v2.execute("SELECT max_posts FROM search_attempt WHERE attempt_id='A1'").fetchone()[0] == 50
    assert legacy_v2.execute("SELECT max_posts FROM search_attempt WHERE attempt_id='A25'").fetchone()[0] == 25
    assert repository.initialize_database(legacy_v2) == 7


def test_v7_rejects_unreviewed_custom_objects_without_losing_data(legacy_v2, monkeypatch):
    _v6(legacy_v2, monkeypatch)
    legacy_v2.execute("CREATE INDEX custom_search_query ON search_attempt(query)")
    before = _rows(legacy_v2)

    with pytest.raises(UnsupportedSchemaVersion):
        repository.initialize_database(legacy_v2)

    assert legacy_v2.execute("PRAGMA user_version").fetchone()[0] == 6
    assert _rows(legacy_v2) == before
    assert legacy_v2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert legacy_v2.execute("SELECT name FROM sqlite_master WHERE name='custom_search_query'").fetchone()
