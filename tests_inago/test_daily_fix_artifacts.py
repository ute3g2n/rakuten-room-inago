"""Failure observation artifacts remain private, addressable, and expirable."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

import pytest

from automation.atomic_io import atomic_write_json
from automation.inago import repository, trends_adapter as trends
from automation.inago.clock import FakeClock
from automation.inago.live_sources import PublicBrowserSource, PublicSourceConfig
from automation.inago.retention import (
    ArtifactExpiredError,
    RetentionError,
    RetentionResult,
    apply_retention,
)
from tests_inago.test_step13 import runtime_case


NOW = "2026-10-14T13:00:00+09:00"
RUN_ID = "N05-RUN"
SETTINGS_VERSION = "N05-SETTINGS-1"
THEME_ID = "T01"
THEME_WORD = "evidence-query-sentinel"
ACQUISITION_ID = "N05-RUN:initial:01:T01"
FAILURE_CODE = "source_response_invalid"


def _open_case(data_root: Path) -> sqlite3.Connection:
    data_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        data_root / "state.sqlite", isolation_level=None, timeout=0
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=0")
    repository.initialize_database(connection)
    connection.execute(
        "INSERT INTO settings_version (version,confirmed_at,genres_json,themes_json,"
        "pattern_refs_json,change_reason,created_at) VALUES (?, ?, '[]','[]',"
        "'[\"P01\"]',NULL,?)",
        (SETTINGS_VERSION, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO run (run_id,target_date,mode,status,settings_version,seed,"
        "started_at,draft_deadline,end_deadline,attempt_used,committed_count,"
        "checkpoint,metrics_status,finished_at) VALUES (?, '2026-10-14','daily',"
        "'running', ?, 'N05-SEED', ?, '2026-10-14T17:45:00+09:00',"
        "'2026-10-14T18:00:00+09:00',0,0,NULL,'pending',NULL)",
        (RUN_ID, SETTINGS_VERSION, NOW),
    )
    selection_id = f"theme-selection:{RUN_ID}:initial:01"
    connection.execute(
        "INSERT INTO theme_selection (selection_id,run_id,theme_id,event_kind,"
        "count_event_id,seed,result_word_refs_json,selected_at) VALUES (?, ?, ?,'initial',"
        "?, 'N05-SEED', '[]', ?)",
        (selection_id, RUN_ID, THEME_ID, selection_id, NOW),
    )
    return connection


def _failure_response(request: trends.TrendsRequest) -> dict[str, object]:
    return {
        "request_id": request.request_id,
        "snapshot_id": request.request_id,
        "run_id": request.run_id,
        "settings_version": request.settings_version,
        "source": "google_trends_public_ui",
        "parent_word": request.parent_word,
        "current_parent": None,
        "refresh_complete": False,
        "language": "ja",
        "geo": "JP",
        "search_type": "web",
        "range": "3m",
        "list_type": "Top",
        "ui_period_label": None,
        "status": "unavailable",
        "fetched_at": None,
        "error_code": FAILURE_CODE,
        "words": [],
    }


class _SavedFailureSource(PublicBrowserSource):
    """Fixture port that writes the same private observation record as production."""

    def __init__(self, evidence_root: Path, *, lock_connection=None, make_request=True):
        config = PublicSourceConfig(
            chrome_executable=Path(sys.executable),
            evidence_root=evidence_root,
            environment="fixture",
            fixture_url="http://127.0.0.1:9",
            node_executable="node",
        )
        super().__init__(config)
        self.requests: list[trends.TrendsRequest] = []
        self.lock_connection = lock_connection
        self.make_request = make_request

    def fetch(self, request: trends.TrendsRequest) -> dict[str, object]:
        self.requests.append(request)
        response = _failure_response(request)
        if self.make_request:
            recorded_at = (
                self._clock.now_jst().isoformat() if hasattr(self, "_clock") else NOW
            )
            record = {
                "kind": "trends",
                "request": asdict(request),
                "environment": "fixture",
                "recorded_at": recorded_at,
                "retention_expires_at": (
                    datetime.fromisoformat(recorded_at) + timedelta(days=90)
                ).isoformat(),
                "response": response,
                "node_events": [
                    {
                        "event": "public_source_failure",
                        "stage": "trends_top_rows_wait",
                        "error_code": FAILURE_CODE,
                        "elapsed_ms": 1250,
                        "message": "response-body-sentinel credential-sentinel",
                    }
                ],
                "process_returncode": 0,
                "trace_state": "unavailable",
                "trace_ref": None,
            }
            path = self._evidence / (
                f"trends-{quote(request.request_id, safe='')}-observation-fixture.json"
            )
            atomic_write_json(path, record)
        if self.lock_connection is not None:
            self.lock_connection.execute("BEGIN IMMEDIATE")
        return response


def _session(connection, clock, source):
    return trends.DurableGroupKeywordSession(
        connection,
        source,
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed="N05-SEED",
        deadline_at="2026-10-14T17:45:00+09:00",
        acquisition_id=ACQUISITION_ID,
    )


def test_n05(tmp_path: Path, monkeypatch) -> None:
    """Commit an artifact link only after its atomic file exists; never resurrect expired evidence."""

    data_root = tmp_path / "locked-case"
    connection = _open_case(data_root)
    lock_connection = sqlite3.connect(
        data_root / "state.sqlite", isolation_level=None, timeout=0
    )
    lock_connection.execute("PRAGMA busy_timeout=0")
    clock = FakeClock(datetime.fromisoformat(NOW))
    evidence_root = data_root / "observations"
    source = _SavedFailureSource(evidence_root, lock_connection=lock_connection)
    session = _session(connection, clock, source)

    with pytest.raises(sqlite3.OperationalError):
        session.fetch(THEME_WORD)

    request_id = f"{ACQUISITION_ID}-a1"
    source_files = list(evidence_root.glob("trends-*-observation-*.json"))
    pending_artifacts = list((data_root / "artifacts" / "daily").glob("TFAIL-*.json"))
    assert len(source_files) == len(pending_artifacts) == 1
    pending = json.loads(pending_artifacts[0].read_text(encoding="utf-8"))
    assert pending["request_id"] == request_id
    assert pending["run_id"] == RUN_ID
    assert pending["settings_version"] == SETTINGS_VERSION
    assert pending["attempt_no"] == 1
    assert pending["failure_error_code"] == FAILURE_CODE
    assert pending["source_status"] == "unavailable"
    assert "snapshot_id" not in pending
    assert THEME_WORD not in json.dumps(pending, ensure_ascii=False)
    assert "response-body-sentinel" not in json.dumps(pending, ensure_ascii=False)
    assert "credential-sentinel" not in json.dumps(pending, ensure_ascii=False)
    row = connection.execute(
        "SELECT status,error_code,evidence_ref FROM trends_acquisition_attempt WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert tuple(row) == ("started", None, None)
    assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0] == 0

    lock_connection.rollback()
    resumed_source = _SavedFailureSource(evidence_root, make_request=False)
    with pytest.raises(trends.TrendsContractError):
        _session(connection, clock, resumed_source).fetch(THEME_WORD)

    assert len(source.requests) == 1
    assert resumed_source.requests == []
    attempt = connection.execute(
        "SELECT status,error_code,evidence_ref,response_json FROM trends_acquisition_attempt "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert attempt[0:3] == ("processing_error", FAILURE_CODE, pending_artifacts[0].stem)
    assert json.loads(attempt[3])["_evidence_capture"]["status"] == "available"
    acquisition = connection.execute(
        "SELECT snapshot_id FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()
    assert acquisition[0] is None
    assert (
        connection.execute("SELECT COUNT(*) FROM keyword_snapshot").fetchone()[0] == 0
    )

    artifact = connection.execute(
        "SELECT path,hash,source_kind,created_at,retention_expires_at,status FROM artifact "
        "WHERE artifact_ref=?",
        (attempt[2],),
    ).fetchone()
    assert artifact is not None
    artifact_path = Path(artifact[0])
    assert artifact_path.is_file()
    assert artifact[2] == "daily"
    assert artifact[5] == "available"
    retention = RetentionResult(
        now=datetime.fromisoformat(NOW),
        data_root=data_root,
        expired_artifact_refs=(),
        expired_aggregate_refs=(),
        expired_detail_observations=0,
        expired_source_posts=0,
        expired_keyword_snapshots=0,
        expired_trends_responses=0,
        expired_source_observation_files=0,
        source_observation_deletion_failures=0,
        deleted_revenue_orders=0,
        file_deletion_failures=(),
    )
    assert retention.open_artifact(connection, attempt[2], now=NOW) == artifact_path

    connection.execute(
        "INSERT INTO artifact (artifact_ref,path,hash,created_at,source_kind,"
        "retention_expires_at,status) VALUES ('N05-PATH-ESCAPE','../outside.json',"
        "'not-a-managed-file-hash','2026-10-14T13:00:00+09:00','daily',"
        "'2027-01-13T13:00:00+09:00','available')"
    )
    with pytest.raises(RetentionError):
        retention.open_artifact(connection, "N05-PATH-ESCAPE", now=NOW)

    expiry = datetime.fromisoformat(artifact[4])
    with pytest.raises(ArtifactExpiredError):
        retention.open_artifact(connection, attempt[2], now=expiry)
    result = apply_retention(connection, data_root=data_root, now=expiry)
    assert attempt[2] in result.expired_artifact_refs
    assert not artifact_path.exists()
    assert (
        connection.execute(
            "SELECT status FROM artifact WHERE artifact_ref=?", (attempt[2],)
        ).fetchone()[0]
        == "expired"
    )
    assert (
        connection.execute(
            "SELECT evidence_ref FROM trends_acquisition_attempt WHERE request_id=?",
            (request_id,),
        ).fetchone()[0]
        == attempt[2]
    )

    permission_root = tmp_path / "permission-case"
    permission_connection = _open_case(permission_root)
    permission_clock = FakeClock(datetime.fromisoformat(NOW))
    permission_source = _SavedFailureSource(permission_root / "observations")

    def fail_artifact_write(**_kwargs):
        raise PermissionError("private-path=credential-sentinel")

    monkeypatch.setattr(
        "automation.inago.live_sources.write_temp_response", fail_artifact_write
    )
    with pytest.raises(trends.TrendsContractError):
        _session(permission_connection, permission_clock, permission_source).fetch(
            THEME_WORD
        )
    failed_capture = permission_connection.execute(
        "SELECT status,error_code,evidence_ref,response_json FROM trends_acquisition_attempt "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert failed_capture[0:3] == ("processing_error", FAILURE_CODE, None)
    capture_state = json.loads(failed_capture[3])["_evidence_capture"]
    assert capture_state == {
        "status": "write_failed",
        "error_code": "artifact_write_failed",
    }
    assert (
        permission_connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
        == 0
    )

    permission_connection.close()
    connection.close()
    lock_connection.close()
    monkeypatch.undo()

    missing_root = tmp_path / "missing-observation-case"
    missing_connection = _open_case(missing_root)
    missing_clock = FakeClock(datetime.fromisoformat(NOW))
    missing_source = _SavedFailureSource(
        missing_root / "observations", make_request=False
    )
    with pytest.raises(trends.TrendsContractError):
        _session(missing_connection, missing_clock, missing_source).fetch(THEME_WORD)
    missing_attempt = missing_connection.execute(
        "SELECT status,error_code,evidence_ref FROM trends_acquisition_attempt "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    assert missing_attempt[0:2] == ("processing_error", FAILURE_CODE)
    assert missing_attempt[2] is not None
    missing_payload = json.loads(
        (missing_root / "artifacts" / "daily" / f"{missing_attempt[2]}.json").read_text(
            encoding="utf-8"
        )
    )
    assert missing_payload["source_observation_state"] == "missing"
    missing_connection.close()


def test_n05b_rejects_tampered_pending_artifact(tmp_path: Path) -> None:
    """Reopening a pending artifact must reject extra top-level and nested fields."""

    data_root = tmp_path / "tampered-artifact-case"
    source = _SavedFailureSource(data_root / "observations")
    request = trends.TrendsRequest(
        f"{ACQUISITION_ID}-a1",
        RUN_ID,
        SETTINGS_VERSION,
        THEME_WORD,
        range="3m",
    )
    source.fetch(request)
    first_capture = source.capture_failure_observation(
        request, _failure_response(request), FAILURE_CODE
    )
    assert first_capture.status == "available"
    assert first_capture.artifact is not None

    artifact_path = (
        data_root
        / "artifacts"
        / "daily"
        / f"{first_capture.artifact.artifact_ref}.json"
    )
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["private_message"] = "credential-sentinel"
    payload["node_events"][0]["private_message"] = "response-body-sentinel"
    with artifact_path.open("w", encoding="utf-8", newline="\n") as artifact_file:
        artifact_file.write(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        )

    resumed_capture = source.capture_failure_observation(
        request, _failure_response(request), FAILURE_CODE
    )

    assert resumed_capture.status == "invalid"
    assert resumed_capture.error_code == "failure_artifact_payload_invalid"
    assert resumed_capture.artifact is None
    persisted_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert persisted_payload["private_message"] == "credential-sentinel"
    assert (
        persisted_payload["node_events"][0]["private_message"]
        == "response-body-sentinel"
    )


def test_n05d_normalizes_observation_retention_timestamps(tmp_path: Path) -> None:
    """A microsecond drift in separately sampled source timestamps must not reject capture."""

    data_root = tmp_path / "timestamp-drift-case"
    source = _SavedFailureSource(data_root / "observations")
    request = trends.TrendsRequest(
        f"{ACQUISITION_ID}-a1",
        RUN_ID,
        SETTINGS_VERSION,
        THEME_WORD,
        range="3m",
    )
    source.fetch(request)
    observation_path = next(source._evidence.glob("trends-*-observation-*.json"))
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    recorded_at = datetime.fromisoformat(observation["recorded_at"])
    observation["retention_expires_at"] = (
        recorded_at + timedelta(days=90, microseconds=1)
    ).isoformat()
    atomic_write_json(observation_path, observation)

    capture = source.capture_failure_observation(
        request, _failure_response(request), FAILURE_CODE
    )

    assert capture.status == "available"
    assert capture.artifact is not None
    artifact_path = (
        data_root / "artifacts" / "daily" / f"{capture.artifact.artifact_ref}.json"
    )
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["recorded_at"] == observation["recorded_at"]
    assert (
        payload["retention_expires_at"]
        == (recorded_at + timedelta(days=90)).isoformat()
    )


def test_n05e_reports_and_stops_on_unregistered_artifact_cleanup_failure(
    runtime_case, monkeypatch, caplog
) -> None:
    """An undeletable expired orphan must be logged and stop daily processing."""

    from automation.inago import orchestrator as daily
    from tests_inago.test_step13 import _make_ports

    source = _SavedFailureSource(runtime_case.data_root / "observations")
    request = trends.TrendsRequest(
        f"{ACQUISITION_ID}-a1",
        RUN_ID,
        SETTINGS_VERSION,
        THEME_WORD,
        range="3m",
    )
    source.fetch(request)
    capture = source.capture_failure_observation(
        request, _failure_response(request), FAILURE_CODE
    )
    assert capture.status == "available"
    assert capture.artifact is not None
    artifact_path = (
        runtime_case.data_root
        / "artifacts"
        / "daily"
        / f"{capture.artifact.artifact_ref}.json"
    )
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    recorded_at = datetime.fromisoformat("2026-07-16T13:00:00+09:00")
    payload["recorded_at"] = recorded_at.isoformat()
    payload["retention_expires_at"] = (recorded_at + timedelta(days=90)).isoformat()
    atomic_write_json(artifact_path, payload)

    original_unlink = Path.unlink

    def fail_orphan_removal(path: Path, *args, **kwargs):
        if path == artifact_path:
            raise PermissionError("fixture denied expired orphan removal")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_orphan_removal)
    runtime_case.clock.current = datetime.fromisoformat("2026-10-14T18:00:00+09:00")
    bundle = _make_ports(runtime_case)
    caplog.set_level(logging.INFO)

    with pytest.raises(daily.DailyOrchestrationError, match="retention_cleanup_failed"):
        daily.run_daily(
            runtime_case.connection,
            data_root=runtime_case.data_root,
            ports=bundle.ports,
            clock=runtime_case.clock,
            run_id_factory=lambda _date: "N05e-RUN",
            seed_factory=lambda: "N05e-SEED",
        )

    retention_log = next(
        record
        for record in caplog.records
        if record.getMessage() == "daily_retention_applied"
    )
    assert retention_log.expired_unregistered_failure_artifacts == 0
    assert retention_log.unregistered_failure_artifact_cleanup_failures == 1
    assert retention_log.trace_state == "degraded"
    assert bundle.room.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM run WHERE target_date='2026-10-14'"
        ).fetchone()[0]
        == 0
    )


def test_n05c_expires_unregistered_missing_observation_artifact(
    tmp_path: Path,
) -> None:
    """Unregistered missing-observation evidence remains resumable until its expiry."""

    data_root = tmp_path / "orphan-artifact-case"
    connection = _open_case(data_root)
    lock_connection = sqlite3.connect(
        data_root / "state.sqlite", isolation_level=None, timeout=0
    )
    lock_connection.execute("PRAGMA busy_timeout=0")
    source = _SavedFailureSource(
        data_root / "observations",
        lock_connection=lock_connection,
        make_request=False,
    )
    try:
        with pytest.raises(sqlite3.OperationalError):
            _session(
                connection,
                FakeClock(datetime.fromisoformat(NOW)),
                source,
            ).fetch(THEME_WORD)

        lock_connection.rollback()
        request_id = f"{ACQUISITION_ID}-a1"
        attempt = connection.execute(
            "SELECT status,error_code,evidence_ref FROM trends_acquisition_attempt "
            "WHERE request_id=?",
            (request_id,),
        ).fetchone()
        assert tuple(attempt) == ("started", None, None)
        artifact_files = list((data_root / "artifacts" / "daily").glob("TFAIL-*.json"))
        assert len(artifact_files) == 1
        artifact_path = artifact_files[0]
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert payload["source_observation_state"] == "missing"
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0] == 0
        expiry = datetime.fromisoformat(payload["retention_expires_at"])

        apply_retention(
            connection,
            data_root=data_root,
            now=expiry - timedelta(seconds=1),
        )
        assert artifact_path.is_file()

        expired = apply_retention(connection, data_root=data_root, now=expiry)
        assert not artifact_path.exists()
        assert expired.expired_unregistered_failure_artifacts == 1
        assert (
            connection.execute(
                "SELECT snapshot_id FROM trends_acquisition WHERE acquisition_id=?",
                (ACQUISITION_ID,),
            ).fetchone()[0]
            is None
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM keyword_snapshot").fetchone()[0]
            == 0
        )
    finally:
        connection.close()
        lock_connection.close()
