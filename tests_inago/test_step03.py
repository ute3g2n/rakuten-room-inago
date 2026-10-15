"""SQLite contracts and atomic draft persistence for STEP 03."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator
from automation.inago.artifact_io import write_temp_response
from automation.inago.contracts import (
    DraftCommitRequest,
    IdempotencyConflict,
    InjectedFailure,
    PersistenceContractError,
    RejectedContract,
    UnsupportedSchemaVersion,
)
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import (
    commit_draft,
    open_database,
    schema_version,
    validate_entity,
)

JST = ZoneInfo("Asia/Tokyo")
RUN_ID = "TEST-R1"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
INPUT_REFS = ("INPUT-A1",)
INPUT_HASH = "BUSINESS-X"
ARTIFACT_ID = "ART-A1"


@dataclass
class RuntimeCase:
    connection: sqlite3.Connection
    data_root: Path
    clock: Any


@pytest.fixture
def runtime_case(tmp_path: Path, fixture_config_path: Path) -> RuntimeCase:
    from automation.inago.clock import FakeClock

    paths = resolve_runtime_paths(
        data_root=tmp_path / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    _seed_settings_run_offer(connection)
    clock = FakeClock(datetime(2026, 10, 12, 13, 1, tzinfo=JST))
    case = RuntimeCase(connection, paths.data_root, clock)
    try:
        yield case
    finally:
        connection.close()


def _seed_settings_run_offer(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO settings_version
            (version, confirmed_at, genres_json, themes_json, pattern_refs_json,
             change_reason, created_at)
        VALUES (?, NULL, '[]', '[]', '["P01","P02","P03","P04","P05"]',
                NULL, ?)
        """,
        (SETTINGS_VERSION, "2026-10-12T13:00:00+09:00"),
    )
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, '2026-10-12', 'daily', 'running', ?, 'TEST-SEED-01',
                '2026-10-12T13:00:00+09:00', '2026-10-12T17:45:00+09:00',
                '2026-10-12T18:00:00+09:00', 1, 0, NULL, 'not_started', NULL)
        """,
        (RUN_ID, SETTINGS_VERSION),
    )
    _seed_offer(connection, "A1")
    _seed_successful_copy_job(connection, _response_payload())


def _seed_offer(connection: sqlite3.Connection, suffix: str) -> None:
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, 'shop_test', ?, ?, ?, '[]', 'G01-T01', '[]', ?, ?, '[]')
        """,
        (
            f"shop_test:item_{suffix}",
            f"item_{suffix}",
            f"架空品{suffix}",
            f"https://example.invalid/items/{suffix}",
            "2026-10-12T13:00:00+09:00",
            "2026-10-12T13:00:00+09:00",
        ),
    )


def _response_payload(
    *,
    offer_key: str = "shop_test:item_A1",
    draft_version: int = 1,
    body: str = "架空品A1の説明です。",
) -> dict[str, object]:
    return {
        "offer_key": offer_key,
        "draft_version": draft_version,
        "body": body,
        "pattern_id": "P01",
        "evidence_refs": ["INPUT-A1"],
    }


def _seed_successful_copy_job(
    connection: sqlite3.Connection,
    payload: dict[str, object],
    *,
    job_id: str = "J-A1",
    offer_key: str = "shop_test:item_A1",
    artifact_ref: str = ARTIFACT_ID,
    generation_attempt_id: str = "GA-A1-1",
) -> None:
    connection.execute(
        """
        INSERT INTO ai_job
            (job_id, run_id, protocol_version, schema_version, task_id, agent_id,
             skill_name, attempt, target_date, settings_version, input_refs_json,
             evidence_refs_json, input_hash, deadline_at, started_at, finished_at,
             resolved_model, reasoning_setting, auth_mode, result_status,
             payload_json, error_json, artifact_ref, accepted_at, adoption_status)
        VALUES (?, ?, 'fixture-protocol-1', 'copy-response-1', 'T02',
                'AG-COPY', 'rr-inago-copywriter', 1, '2026-10-12', ?, ?,
                '["INPUT-A1"]', ?, '2026-10-12T17:45:00+09:00',
                '2026-10-12T13:00:00+09:00', '2026-10-12T13:01:00+09:00',
                'fixture-model', 'fixture', 'fixture', 'success', ?, NULL,
                ?, NULL, 'pending')
        """,
        (
            job_id,
            RUN_ID,
            SETTINGS_VERSION,
            json.dumps(list(INPUT_REFS)),
            INPUT_HASH,
            json.dumps(payload, ensure_ascii=False),
            artifact_ref,
        ),
    )
    connection.execute(
        """
        INSERT INTO generation_attempt
            (generation_attempt_id, run_id, offer_key, draft_id, attempt_no,
             outcome, artifact_ref, error_code, started_at, ended_at)
        VALUES (?, ?, ?, NULL, 1, 'success', ?, NULL,
                '2026-10-12T13:00:00+09:00', '2026-10-12T13:01:00+09:00')
        """,
        (generation_attempt_id, RUN_ID, offer_key, artifact_ref),
    )


def _make_artifact(
    case: RuntimeCase,
    *,
    payload: dict[str, object] | None = None,
    artifact_ref: str = ARTIFACT_ID,
):
    return write_temp_response(
        data_root=case.data_root,
        artifact_ref=artifact_ref,
        payload=payload or _response_payload(),
        source_kind="generation",
        created_at="2026-10-12T13:01:00+09:00",
    )


def _make_request(
    case: RuntimeCase,
    *,
    payload: dict[str, object] | None = None,
    artifact_ref: str = ARTIFACT_ID,
    job_id: str = "J-A1",
) -> DraftCommitRequest:
    response = payload or _response_payload()
    return DraftCommitRequest(
        run_id=RUN_ID,
        job_id=job_id,
        settings_version=SETTINGS_VERSION,
        input_refs=INPUT_REFS,
        input_hash=INPUT_HASH,
        attempt_no=1,
        draft_id="D-A1",
        generator_version="copy-generator-1",
        pattern_version=SETTINGS_VERSION,
        generated_at="2026-10-12T13:01:00+09:00",
        committed_at="2026-10-12T13:01:00+09:00",
        response=response,
        artifact=_make_artifact(case, payload=response, artifact_ref=artifact_ref),
    )


def _counts(
    connection: sqlite3.Connection,
    *,
    run_id: str = RUN_ID,
    offer_key: str = "shop_test:item_A1",
) -> tuple[int, int, int, int]:
    drafts = connection.execute(
        "SELECT COUNT(*) FROM draft WHERE run_id = ? AND offer_key = ?",
        (run_id, offer_key),
    ).fetchone()[0]
    reservations = connection.execute(
        "SELECT COUNT(*) FROM reservation WHERE offer_key = ? AND status = 'active'",
        (offer_key,),
    ).fetchone()[0]
    committed = connection.execute(
        "SELECT committed_count FROM run WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    artifacts = connection.execute(
        "SELECT COUNT(*) FROM artifact WHERE artifact_ref = ?", (ARTIFACT_ID,)
    ).fetchone()[0]
    return drafts, reservations, committed, artifacts


def _export_stub(
    connection: sqlite3.Connection,
    draft_id: str,
    calls: list[str],
    *,
    failpoint: str | None = None,
) -> None:
    if failpoint not in (None, "export"):
        raise ValueError("unsupported export failpoint")
    row = connection.execute(
        "SELECT status FROM draft WHERE draft_id = ?", (draft_id,)
    ).fetchone()
    if row is None:
        raise RejectedContract("export stub requires a committed draft")
    calls.append(draft_id)
    if failpoint == "export":
        raise InjectedFailure("export")


def test_artifact_ref_collision_preserves_the_first_response(
    runtime_case: RuntimeCase,
) -> None:
    original = _make_artifact(runtime_case)
    original_bytes = original.path.read_bytes()

    retry = _make_artifact(runtime_case)
    assert retry.path == original.path
    assert retry.content_hash == original.content_hash
    with pytest.raises(IdempotencyConflict):
        _make_artifact(
            runtime_case,
            payload=_response_payload(body="別の本文です。"),
        )

    assert original.path.read_bytes() == original_bytes


def test_artifact_symlink_escape_is_rejected_before_outside_creation(
    runtime_case: RuntimeCase,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    artifact_root = runtime_case.data_root / "artifacts"
    try:
        artifact_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this Windows environment cannot create directory symlinks: {exc}")

    with pytest.raises(PersistenceContractError, match="escaped"):
        write_temp_response(
            data_root=runtime_case.data_root,
            artifact_ref="ART-ESCAPE",
            payload=_response_payload(),
            source_kind="generation",
            created_at="2026-10-12T13:01:00+09:00",
        )

    assert not (outside / "generation").exists()


def test_initial_schema_maps_entities_and_sqlite_state_contract(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "inago"
        / "schemas"
        / "entities.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    expected_entities = {
        "settings_version",
        "run",
        "search_attempt",
        "source_post",
        "offer",
        "candidate_decision",
        "keyword_snapshot",
        "theme_selection",
        "draft",
        "generation_attempt",
        "reservation",
        "publication_event",
        "human_action",
        "exclusion_key",
        "metric_observation",
        "revenue_order",
        "artifact",
        "recovery",
        "backup",
        "restore_check",
        "ai_job",
    }
    assert expected_entities <= set(schema["$defs"])
    assert {"draft_version", "receipt_revision"} <= set(
        schema["$defs"]["human_action"]["required"]
    )
    assert validate_entity(
        "human_action",
        {
            "action_id": "ACTION-1",
            "action": "posted",
            "offer_key": "shop_test:item_A1",
            "draft_id": "D-A1",
            "draft_version": 1,
            "prior_revision": 0,
            "receipt_revision": 1,
            "reason": None,
            "reported_at": "2026-10-12T20:05:00+09:00",
            "actual_posted_at": None,
            "old_event_ref": None,
            "correction_action_ref": None,
            "new_body": None,
            "apply_status": "pending",
            "applied_at": None,
            "applied_run_id": None,
            "apply_error": None,
        },
    )

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert expected_entities <= tables
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 5


def test_schema_version_rejects_an_unknown_reader_version(
    runtime_case: RuntimeCase,
) -> None:
    runtime_case.connection.execute("PRAGMA user_version = 99")
    with pytest.raises(UnsupportedSchemaVersion):
        schema_version(runtime_case.connection)


def test_schema_rejects_human_action_without_explicit_draft_and_receipt_versions() -> (
    None
):
    invalid_action = {
        "action_id": "ACTION-1",
        "action": "posted",
        "offer_key": "shop_test:item_A1",
        "draft_id": "D-A1",
        "prior_revision": 0,
        "reason": None,
        "reported_at": "2026-10-12T20:05:00+09:00",
        "old_event_ref": None,
        "new_body": None,
        "apply_status": "pending",
        "applied_at": None,
        "applied_run_id": None,
        "apply_error": None,
    }

    with pytest.raises(RejectedContract):
        validate_entity("human_action", invalid_action)


def test_entity_contracts_preserve_null_zero_and_dictionary_bounds() -> None:
    metric = {
        "observation_id": "METRIC-1",
        "scope": "entry_post",
        "ref": "POST-1",
        "metric": "likes",
        "value": 0,
        "unit": "count",
        "observed_at": "2026-10-12T18:00:00+09:00",
        "window": None,
        "availability": "observed",
        "source_ref": None,
        "provisional": False,
    }
    assert validate_entity("metric_observation", metric)
    assert validate_entity(
        "metric_observation", {**metric, "value": None, "availability": "unknown"}
    )
    with pytest.raises(RejectedContract):
        validate_entity("metric_observation", {**metric, "value": -1})

    released_reservation = {
        "reservation_id": "RES-1",
        "offer_key": "shop_test:item_A1",
        "draft_id": "D-A1",
        "status": "released",
        "created_at": "2026-10-12T13:01:00+09:00",
        "released_at": "2026-10-13T13:00:00+09:00",
        "release_reason": "posted",
    }
    assert validate_entity("reservation", released_reservation)
    with pytest.raises(RejectedContract):
        validate_entity("reservation", {**released_reservation, "released_at": None})

    event = {
        "event_id": "PUB-1",
        "offer_key": "shop_test:item_A1",
        "draft_id": "D-A1",
        "draft_version": 0,
        "reported_at": "2026-10-12T20:00:00+09:00",
        "actual_posted_at": None,
        "room_url": None,
        "evidence_kind": "human_statement",
        "valid": True,
        "revision": 1,
    }
    with pytest.raises(RejectedContract):
        validate_entity("publication_event", event)


def test_daily_run_and_attempt_keys_enforce_uniqueness_and_foreign_keys(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, metrics_status)
            VALUES ('TEST-R2', '2026-10-12', 'daily', 'created', ?, 'SEED-2',
                    '2026-10-12T13:00:00+09:00', '2026-10-12T17:45:00+09:00',
                    '2026-10-12T18:00:00+09:00', 0, 0, 'not_started')
            """,
            (SETTINGS_VERSION,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO search_attempt
                (attempt_id, run_id, query, parent_refs_json, level, attempt_no,
                 started_at, ended_at, source_status, source_order, max_posts,
                 retry_of, error_code)
            VALUES ('SEARCH-1', 'MISSING-RUN', 'test', '[]', 'combined', 1,
                    '2026-10-12T13:00:00+09:00', NULL, 'unavailable', 'internal',
                    50, NULL, 'fixture_failure')
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO settings_version
                (version, genres_json, themes_json, pattern_refs_json, created_at)
            VALUES (NULL, '[]', '[]', '[]', '2026-10-12T13:00:00+09:00')
            """
        )


def test_before_commit_rolls_back_all_rows_and_preserves_normal_response_artifact(
    runtime_case: RuntimeCase,
) -> None:
    request = _make_request(runtime_case)
    with pytest.raises(InjectedFailure, match="before_commit"):
        commit_draft(
            runtime_case.connection,
            request,
            clock=runtime_case.clock,
            failpoint="before_commit",
        )

    assert _counts(runtime_case.connection) == (0, 0, 0, 0)
    assert request.artifact.path.is_file()
    assert (
        json.loads(request.artifact.path.read_text(encoding="utf-8"))
        == request.response
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ? AND attempt_no = 1",
            (RUN_ID,),
        ).fetchone()[0]
        == 1
    )


def test_after_commit_crash_reopens_as_one_draft_one_reservation_and_one_count(
    runtime_case: RuntimeCase,
) -> None:
    request = _make_request(runtime_case)
    with pytest.raises(InjectedFailure, match="after_commit"):
        commit_draft(
            runtime_case.connection,
            request,
            clock=runtime_case.clock,
            failpoint="after_commit",
        )

    assert _counts(runtime_case.connection) == (1, 1, 1, 1)
    stored = runtime_case.connection.execute(
        "SELECT draft_id, version, body, status FROM draft WHERE offer_key = ?",
        ("shop_test:item_A1",),
    ).fetchone()
    assert tuple(stored) == ("D-A1", 1, "架空品A1の説明です。", "committed")


def test_export_failure_and_resume_do_not_recommit_or_regenerate(
    runtime_case: RuntimeCase,
) -> None:
    request = _make_request(runtime_case)
    commit_draft(runtime_case.connection, request, clock=runtime_case.clock)
    calls: list[str] = []

    with pytest.raises(InjectedFailure, match="export"):
        _export_stub(
            runtime_case.connection,
            request.draft_id,
            calls,
            failpoint="export",
        )
    assert _counts(runtime_case.connection) == (1, 1, 1, 1)

    replay = commit_draft(runtime_case.connection, request, clock=runtime_case.clock)
    _export_stub(runtime_case.connection, request.draft_id, calls)
    assert replay.already_committed is True
    assert calls == ["D-A1", "D-A1"]
    assert _counts(runtime_case.connection) == (1, 1, 1, 1)
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ? AND offer_key = ?",
            (RUN_ID, "shop_test:item_A1"),
        ).fetchone()[0]
        == 1
    )


def test_same_committed_request_is_idempotent_and_never_regenerates(
    runtime_case: RuntimeCase,
) -> None:
    request = _make_request(runtime_case)
    first = commit_draft(runtime_case.connection, request, clock=runtime_case.clock)
    second = commit_draft(runtime_case.connection, request, clock=runtime_case.clock)

    assert first.already_committed is False
    assert second.already_committed is True
    assert _counts(runtime_case.connection) == (1, 1, 1, 1)
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ? AND offer_key = ?",
            (RUN_ID, "shop_test:item_A1"),
        ).fetchone()[0]
        == 1
    )
    assert (
        runtime_case.connection.execute(
            "SELECT adoption_status FROM ai_job WHERE job_id = 'J-A1'"
        ).fetchone()[0]
        == "accepted"
    )

    runtime_case.connection.execute(
        """
        UPDATE reservation
           SET status = 'released', released_at = '2026-10-13T13:00:00+09:00',
               release_reason = 'posted'
         WHERE offer_key = 'shop_test:item_A1' AND draft_id = 'D-A1'
        """
    )
    replay_after_release = commit_draft(
        runtime_case.connection, request, clock=runtime_case.clock
    )
    assert replay_after_release.already_committed is True
    assert _counts(runtime_case.connection) == (1, 0, 1, 1)


@pytest.mark.parametrize(
    "mutation", ("settings_version", "input_refs", "input_hash", "job_id")
)
def test_committed_request_rejects_a_different_job_or_input(
    runtime_case: RuntimeCase, mutation: str
) -> None:
    request = _make_request(runtime_case)
    commit_draft(runtime_case.connection, request, clock=runtime_case.clock)

    if mutation == "settings_version":
        retry = replace(request, settings_version="settings-unexpected-2")
    elif mutation == "input_refs":
        retry = replace(request, input_refs=("INPUT-A1-OTHER",))
    elif mutation == "input_hash":
        retry = replace(request, input_hash="BUSINESS-Y")
    else:
        retry = replace(request, job_id="J-OTHER")

    with pytest.raises(IdempotencyConflict):
        commit_draft(runtime_case.connection, retry, clock=runtime_case.clock)

    assert _counts(runtime_case.connection) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    "mutation",
    (
        "offer_key",
        "draft_version",
        "settings_version",
        "input_refs",
        "input_hash",
    ),
)
def test_contract_mismatch_is_rejected_without_state_delta(
    runtime_case: RuntimeCase, mutation: str
) -> None:
    request = _make_request(runtime_case)
    if mutation == "offer_key":
        payload = _response_payload(offer_key="shop_test:item_B")
        request = _make_request(
            runtime_case, payload=payload, artifact_ref="ART-MISMATCH"
        )
    elif mutation == "draft_version":
        payload = _response_payload(draft_version=2)
        request = _make_request(
            runtime_case, payload=payload, artifact_ref="ART-MISMATCH"
        )
    elif mutation == "settings_version":
        request = replace(request, settings_version="settings-unexpected-2")
    elif mutation == "input_refs":
        request = replace(request, input_refs=("INPUT-A1-Y",))
    elif mutation == "input_hash":
        request = replace(request, input_hash="BUSINESS-Y")

    with pytest.raises(RejectedContract):
        commit_draft(runtime_case.connection, request, clock=runtime_case.clock)

    assert _counts(runtime_case.connection) == (0, 0, 0, 0)


def test_draft_commit_requires_a_daily_run_and_matching_job_target_date(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    request = _make_request(runtime_case)
    connection.execute("UPDATE run SET mode = 'setup' WHERE run_id = ?", (RUN_ID,))
    with pytest.raises(RejectedContract, match="daily run"):
        commit_draft(connection, request, clock=runtime_case.clock)
    assert _counts(connection) == (0, 0, 0, 0)

    connection.execute("UPDATE run SET mode = 'daily' WHERE run_id = ?", (RUN_ID,))
    connection.execute(
        "UPDATE ai_job SET target_date = '2026-10-13' WHERE job_id = 'J-A1'"
    )
    with pytest.raises(RejectedContract):
        commit_draft(connection, request, clock=runtime_case.clock)
    assert _counts(connection) == (0, 0, 0, 0)


def test_malformed_or_blank_copy_response_is_rejected_without_retry(
    runtime_case: RuntimeCase,
) -> None:
    malformed = {"offer_key": "shop_test:item_A1", "draft_version": 1, "body": "  "}
    artifact = _make_artifact(runtime_case, payload=malformed)
    request = _make_request(runtime_case, payload=malformed)

    with pytest.raises(RejectedContract):
        commit_draft(
            runtime_case.connection,
            replace(request, artifact=artifact),
            clock=runtime_case.clock,
        )

    assert _counts(runtime_case.connection) == (0, 0, 0, 0)
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM generation_attempt WHERE run_id = ? AND offer_key = ?",
            (RUN_ID, "shop_test:item_A1"),
        ).fetchone()[0]
        == 1
    )


def test_successful_commit_writes_body_reservation_and_count_together(
    runtime_case: RuntimeCase,
) -> None:
    request = _make_request(runtime_case)
    outcome = commit_draft(runtime_case.connection, request, clock=runtime_case.clock)

    assert outcome.already_committed is False
    assert _counts(runtime_case.connection) == (1, 1, 1, 1)
    assert (
        runtime_case.connection.execute(
            "SELECT draft_id FROM reservation WHERE offer_key = ? AND status = 'active'",
            ("shop_test:item_A1",),
        ).fetchone()[0]
        == "D-A1"
    )


def test_transaction_refuses_the_sixteenth_daily_completion(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    for number in range(1, 16):
        suffix = f"A{number:02d}"
        _seed_offer(connection, suffix)
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES (?, ?, ?, 1, ?, '2026-10-12T13:01:00+09:00',
                    '2026-10-12T13:01:00+09:00', 'fixture', ?, 'committed',
                    NULL, 'P01', '["INPUT-A1"]')
            """,
            (
                f"D-{suffix}",
                f"shop_test:item_{suffix}",
                RUN_ID,
                f"架空品{suffix}の説明です。",
                SETTINGS_VERSION,
            ),
        )
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES (?, ?, ?, 'active', '2026-10-12T13:01:00+09:00', NULL, NULL)
            """,
            (f"RES-{suffix}", f"shop_test:item_{suffix}", f"D-{suffix}"),
        )
    connection.execute(
        "UPDATE run SET committed_count = 15 WHERE run_id = ?", (RUN_ID,)
    )
    _seed_offer(connection, "A16")
    payload = _response_payload(offer_key="shop_test:item_A16")
    _seed_successful_copy_job(
        connection,
        payload,
        job_id="J-A16",
        offer_key="shop_test:item_A16",
        artifact_ref="ART-A16",
        generation_attempt_id="GA-A16-1",
    )
    request = replace(
        _make_request(
            runtime_case,
            payload=payload,
            artifact_ref="ART-A16",
            job_id="J-A16",
        ),
        draft_id="D-A16",
    )

    with pytest.raises(RejectedContract, match="15"):
        commit_draft(connection, request, clock=runtime_case.clock)

    assert (
        connection.execute(
            "SELECT committed_count FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 15
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        == 15
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM reservation WHERE status = 'active'"
        ).fetchone()[0]
        == 15
    )


def test_foreign_keys_and_active_reservation_uniqueness_are_enforced(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    request = _make_request(runtime_case)
    commit_draft(connection, request, clock=runtime_case.clock)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES ('RES-DUP', 'shop_test:item_A1', 'D-A1', 'active',
                    '2026-10-12T13:02:00+09:00', NULL, NULL)
            """
        )
    _seed_offer(connection, "B")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES ('RES-WRONG-OFFER', 'shop_test:item_B', 'D-A1', 'active',
                    '2026-10-12T13:02:00+09:00', NULL, NULL)
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO publication_event
                (event_id, offer_key, draft_id, draft_version, reported_at,
                 actual_posted_at, room_url, evidence_kind, valid, revision)
            VALUES ('PUB-WRONG-OFFER', 'shop_test:item_B', 'D-A1', 1,
                    '2026-10-12T20:00:00+09:00', NULL, NULL,
                    'human_statement', 1, 1)
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO human_action
                (action_id, action, offer_key, draft_id, draft_version,
                 prior_revision, receipt_revision, reason, reported_at,
                 old_event_ref, new_body, apply_status, applied_at,
                 applied_run_id, apply_error)
            VALUES ('ACTION-WRONG-OFFER', 'posted', 'shop_test:item_B',
                    'D-A1', 1, 0, 1, NULL, '2026-10-12T20:05:00+09:00',
                    NULL, NULL, 'pending', NULL, NULL, NULL)
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO publication_event
                (event_id, offer_key, draft_id, draft_version, reported_at,
                 actual_posted_at, room_url, evidence_kind, valid, revision)
            VALUES ('PUB-BAD', 'missing:item', 'D-A1', 1,
                    '2026-10-12T20:00:00+09:00', NULL, NULL,
                    'human_statement', 1, 1)
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE reservation SET status = 'released' WHERE reservation_id = 'RES-D-A1'"
        )


def test_sql_rejects_negative_observed_metrics_and_order_amounts(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    request = _make_request(runtime_case)
    commit_draft(connection, request, clock=runtime_case.clock)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO metric_observation
                (observation_id, scope, ref, metric, value_json, unit,
                 observed_at, window, availability, source_ref, provisional)
            VALUES ('METRIC-NEG', 'entry_post', 'POST-1', 'likes', '-1', 'count',
                    '2026-10-12T18:00:00+09:00', NULL, 'observed', NULL, 0)
            """
        )
    for bad_json, observation_id in (
        ("true", "METRIC-BOOL"),
        ("[]", "METRIC-ARRAY"),
        ("{}", "METRIC-OBJECT"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO metric_observation
                    (observation_id, scope, ref, metric, value_json, unit,
                     observed_at, window, availability, source_ref, provisional)
                VALUES (?, 'entry_post', 'POST-1', 'likes', ?, 'count',
                        '2026-10-12T18:00:00+09:00', NULL, 'observed', NULL, 0)
                """,
                (observation_id, bad_json),
            )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO revenue_order
                (order_ref, is_temporary_ref, occurred_at, shop_name, product_name,
                 item_code, product_url, sales_yen, commission_yen, order_status,
                 link_type, source_ref, match_kind, matched_offer_key, entry_post_ref)
            VALUES ('ORDER-NEG', 0, NULL, NULL, NULL, NULL, NULL, -1, NULL, NULL,
                    NULL, 'ART-A1', 'unmatched', NULL, NULL)
            """
        )
    for order_ref, sales_yen, commission_yen in (
        ("ORDER-TEXT-SALES", "not-a-number", None),
        ("ORDER-TEXT-COMMISSION", None, "not-a-number"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO revenue_order
                    (order_ref, is_temporary_ref, occurred_at, shop_name, product_name,
                     item_code, product_url, sales_yen, commission_yen, order_status,
                     link_type, source_ref, match_kind, matched_offer_key, entry_post_ref)
                VALUES (?, 0, NULL, NULL, NULL, NULL, NULL, ?, ?, NULL, NULL,
                        'ART-A1', 'unmatched', NULL, NULL)
                """,
                (order_ref, sales_yen, commission_yen),
            )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE ai_job SET payload_json = '[]' WHERE job_id = 'J-A1'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE ai_job SET error_json = '[]' WHERE job_id = 'J-A1'")


def test_draft_is_rejected_when_pending_posted_report_or_deadline_wins(
    runtime_case: RuntimeCase,
) -> None:
    request = _make_request(runtime_case)
    runtime_case.connection.execute(
        """
        INSERT INTO human_action
            (action_id, action, offer_key, draft_id, draft_version,
             prior_revision, receipt_revision, reason, reported_at,
             old_event_ref, new_body, apply_status, applied_at,
             applied_run_id, apply_error)
        VALUES ('ACTION-A1', 'posted', 'shop_test:item_A1', NULL, 1,
                0, 1, NULL, '2026-10-12T20:05:00+09:00', NULL, NULL,
                'pending', NULL, NULL, NULL)
        """
    )
    with pytest.raises(RejectedContract, match="pending"):
        commit_draft(runtime_case.connection, request, clock=runtime_case.clock)
    assert _counts(runtime_case.connection) == (0, 0, 0, 0)

    runtime_case.connection.execute(
        "DELETE FROM human_action WHERE action_id = 'ACTION-A1'"
    )
    from automation.inago.clock import FakeClock

    past_deadline = FakeClock(datetime(2026, 10, 12, 17, 45, tzinfo=JST))
    with pytest.raises(RejectedContract, match="deadline"):
        commit_draft(runtime_case.connection, request, clock=past_deadline)
    assert _counts(runtime_case.connection) == (0, 0, 0, 0)
