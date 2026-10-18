"""Focused checks for the safe daily retry summary."""

import sqlite3

from automation.inago.daily_pipeline import _retry_summary_for_run


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE search_attempt (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            query TEXT NOT NULL,
            parent_refs_json TEXT NOT NULL,
            level TEXT NOT NULL,
            source_status TEXT NOT NULL,
            ended_at TEXT,
            retry_of TEXT
        );
        CREATE TABLE trends_acquisition (
            acquisition_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts_used INTEGER NOT NULL
        );
        CREATE TABLE trends_acquisition_attempt (
            request_id TEXT PRIMARY KEY,
            acquisition_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            retry_of TEXT,
            ended_at TEXT,
            status TEXT NOT NULL
        );
        """
    )
    return connection


def test_retry_summary_counts_recovered_and_unresolved_attempts() -> None:
    connection = _database()
    connection.executemany(
        "INSERT INTO search_attempt VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("room-1", "run-1", "q1", "[]", "combined", "partial", "t1", None),
            ("room-2", "run-1", "q1", "[]", "combined", "observed", "t2", "room-1"),
            ("room-3", "run-1", "q2", "[]", "parent", "unavailable", "t3", None),
            ("room-4", "run-1", "q2", "[]", "parent", "partial", "t4", "room-3"),
        ],
    )
    connection.executemany(
        "INSERT INTO trends_acquisition VALUES (?, ?, ?, ?)",
        [
            ("trend-1", "run-1", "observed", 2),
            ("trend-2", "run-1", "deadline", 2),
        ],
    )
    connection.executemany(
        "INSERT INTO trends_acquisition_attempt VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("trend-1-a1", "trend-1", 1, None, "t1", "unavailable"),
            ("trend-1-a2", "trend-1", 2, "trend-1-a1", "t2", "response_ready"),
            ("trend-2-a1", "trend-2", 1, None, "t3", "unavailable"),
            ("trend-2-a2", "trend-2", 2, "trend-2-a1", "t4", "deadline"),
        ],
    )

    assert _retry_summary_for_run(connection, "run-1") == {
        "attempt_count": 4,
        "recovered_count": 2,
        "unresolved_count": 2,
        "contract_violation_count": 0,
    }


def test_retry_summary_marks_changed_room_retry_input_as_violation() -> None:
    connection = _database()
    connection.executemany(
        "INSERT INTO search_attempt VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("room-1", "run-1", "q1", "[]", "combined", "partial", "t1", None),
            ("room-2", "run-1", "q2", "[]", "combined", "observed", "t2", "room-1"),
        ],
    )

    assert _retry_summary_for_run(connection, "run-1") == {
        "attempt_count": 1,
        "recovered_count": 1,
        "unresolved_count": 0,
        "contract_violation_count": 1,
    }
