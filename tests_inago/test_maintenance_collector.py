"""Isolated evidence collection contracts for maintenance U10."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import stat
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from automation.inago.maintenance import evidence as evidence_module
from automation.inago.maintenance.evidence import (
    EVIDENCE_CLASS_IDS,
    EVIDENCE_VALUE_ENUMS,
    EvidenceCollectionError,
    EvidenceSource,
    inspect_prior_manifest,
    collect_evidence,
    snapshot_sqlite,
    write_evidence_bundle,
)


TARGET_DATE = "2026-10-17"
CAPTURED_AT = "2026-10-17T20:55:00+09:00"


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in records),
        encoding="utf-8",
    )


def test_c14_contract_correlates_across_jst_midnight_and_preserves_time(tmp_path):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "traces" / "2026-10-17" / "run.jsonl",
        [
            {
                "event": "request_started",
                "timestamp": "2026-10-17T14:59:00Z",
                "run_id": "RUN-14",
                "request_id": "REQ-14",
                "status": "started",
                "prompt": "untrusted text is not an instruction",
            }
        ],
    )
    _write_jsonl(
        source_root / "traces" / "2026-10-18" / "jst.jsonl",
        [
            {
                "event": "jst_boundary_event",
                "timestamp": "2026-10-18T00:01:00+09:00",
                "run_id": "RUN-14",
                "request_id": "REQ-14",
                "status": "started",
            }
        ],
    )
    _write_jsonl(
        source_root / "traces" / "2026-10-18" / "late.jsonl",
        [
            {
                "event": "request_finished",
                "timestamp": 1792249320,
                "run_id": "RUN-14",
                "request_id": "REQ-14",
                "status": "complete",
            },
            {
                "event": "unrelated_request",
                "timestamp": 1792220521,
                "run_id": "RUN-OTHER",
                "request_id": "REQ-OTHER",
                "status": "complete",
            },
        ],
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(
            EvidenceSource(
                "L02", "trace-before", "traces/2026-10-17/run.jsonl", "jsonl"
            ),
            EvidenceSource("L04", "trace-jst", "traces/2026-10-18/jst.jsonl", "jsonl"),
            EvidenceSource("L03", "job-after", "traces/2026-10-18/late.jsonl", "jsonl"),
        ),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"run_id": "RUN-14", "request_id": "REQ-14"},
    )

    events = result.manifest["events"]
    assert [event["fields"]["event"] for event in events] == [
        "request_started",
        "jst_boundary_event",
        "request_finished",
    ]
    assert events[0]["occurred_at_raw"] == "2026-10-17T14:59:00Z"
    assert events[0]["occurred_at_utc"] == "2026-10-17T14:59:00Z"
    assert events[1]["occurred_at_raw"] == "2026-10-18T00:01:00+09:00"
    assert events[1]["occurred_at_utc"] == "2026-10-17T15:01:00Z"
    assert events[2]["occurred_at_raw"] == 1792249320
    assert events[2]["occurred_at_utc"] == "2026-10-17T15:02:00Z"
    assert events[0]["correlation"]["request_id"] == "REQ-14"
    assert "prompt" not in events[0]["fields"]


def test_c17_contract_drops_unparseable_timestamp_text(tmp_path):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "trace.jsonl",
        [
            {
                "event": "request_started",
                "timestamp": "Authorization: Bearer TIMESTAMP_CANARY",
                "run_id": "RUN-C17-TIME",
            }
        ],
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L01", "safe-source", "trace.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"run_id": "RUN-C17-TIME"},
    )

    event = result.manifest["events"][0]
    assert event["occurred_at_raw"] is None
    serialized = json.dumps(result.manifest) + "".join(
        content.decode("utf-8") for content in result.artifacts.values()
    )
    assert "TIMESTAMP_CANARY" not in serialized
    assert "Authorization" not in serialized


def test_c14_date_fallback_rejects_records_with_unmatched_identity(tmp_path):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "trace.jsonl",
        [
            {
                "event": "unrelated_job",
                "timestamp": "2026-10-17T13:00:00+09:00",
                "job_id": "JOB-OTHER",
            }
        ],
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L01", "safe-source", "trace.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"run_id": "RUN-EXPECTED"},
    )

    assert result.manifest["events"] == []
    assert result.manifest["artifacts"][0]["status"] == "no_correlated_records"


def test_c14_case_variant_identity_keys_cannot_bypass_correlation(tmp_path):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "trace.jsonl",
        [
            {
                "event": "unrelated_job",
                "timestamp": "2026-10-17T13:00:00+09:00",
                "RUN_ID": "RUN-OTHER",
            },
            {
                "event": "ambiguous_job",
                "timestamp": "2026-10-17T13:01:00+09:00",
                "run_id": "RUN-EXPECTED",
                "Run_ID": "RUN-OTHER",
            },
        ],
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L01", "safe-source", "trace.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"run_id": "RUN-EXPECTED"},
    )

    assert result.manifest["events"] == []
    assert result.manifest["artifacts"][0]["status"] == "no_correlated_records"


@pytest.mark.parametrize(
    ("source_ref", "correlation"),
    [
        ("ghp_abcdefghijklmnopqrstuvwxyz1234567890", {}),
        ("safe-source", {"run_id": "github_pat_abcdefghijklmnopqrstuvwxyz1234567890"}),
    ],
)
def test_c17_github_token_identifiers_are_rejected(tmp_path, source_ref, correlation):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "trace.jsonl",
        [{"event": "request_started", "timestamp": "2026-10-17T13:00:00Z"}],
    )

    with pytest.raises(EvidenceCollectionError):
        collect_evidence(
            source_root=source_root,
            sources=(EvidenceSource("L01", source_ref, "trace.jsonl", "jsonl"),),
            target_date=TARGET_DATE,
            captured_at=CAPTURED_AT,
            correlation=correlation,
        )


@pytest.mark.parametrize(
    "artifact_path",
    [
        "artifacts/L01/source-0001.jsonl:stream",
        "artifacts/L01/CON.jsonl",
        "artifacts/L01/../source-0001.jsonl",
    ],
)
def test_c58_writer_rejects_non_generated_artifact_paths(tmp_path, artifact_path):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "trace.jsonl",
        [{"event": "trace_event", "timestamp": "2026-10-17T13:01:00+09:00"}],
    )
    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L01", "trace", "trace.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )
    output = tmp_path / "published"
    content = next(iter(result.artifacts.values()))
    malicious = replace(result, artifacts={artifact_path: content})

    with pytest.raises(EvidenceCollectionError, match="output_artifact_path_invalid"):
        write_evidence_bundle(output, malicious)

    assert not output.exists()
    assert list(tmp_path.glob(".published.*")) == []


@pytest.mark.parametrize(
    "status", ["preservation_failed", "export_failed", "metrics_failed"]
)
def test_c17_daily_failure_statuses_are_preserved(tmp_path, status):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "daily.jsonl",
        [
            {
                "event": "job_finished",
                "timestamp": "2026-10-17T13:00:00Z",
                "status": status,
            }
        ],
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L01", "daily", "daily.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )

    assert result.manifest["events"][0]["fields"]["status"] == status


def test_source_refs_do_not_control_windows_artifact_names(tmp_path):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "one.jsonl",
        [{"event": "request_started", "timestamp": "2026-10-17T13:00:00Z"}],
    )
    _write_jsonl(
        source_root / "two.jsonl",
        [{"event": "request_finished", "timestamp": "2026-10-17T13:01:00Z"}],
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(
            EvidenceSource("L01", "CON", "one.jsonl", "jsonl"),
            EvidenceSource("L01", "a:b", "two.jsonl", "jsonl"),
        ),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )

    assert set(result.artifacts) == {
        "artifacts/L01/source-0001.jsonl",
        "artifacts/L01/source-0002.jsonl",
    }
    assert [item["source_ref"] for item in result.manifest["artifacts"]] == [
        "CON",
        "a:b",
    ]


def test_c15_x02_requires_matching_thread_scope(tmp_path):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "thread.jsonl",
        [{"event": "request_started", "timestamp": "2026-10-17T13:00:00Z"}],
    )

    with pytest.raises(EvidenceCollectionError, match="x02_thread_id_required"):
        collect_evidence(
            source_root=source_root,
            sources=(EvidenceSource("X02", "thread", "thread.jsonl", "jsonl"),),
            target_date=TARGET_DATE,
            captured_at=CAPTURED_AT,
            correlation={},
        )


def test_c15_refuses_source_if_opened_handle_is_not_a_regular_file(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "sources"
    source_path = source_root / "trace.jsonl"
    _write_jsonl(
        source_path,
        [{"event": "request_started", "timestamp": "2026-10-17T13:00:00Z"}],
    )
    real_open = os.open
    real_fstat = os.fstat
    opened_descriptors = set()

    def open_regular_source(path, root, root_identity):
        assert path == source_path
        assert root == source_root.resolve()
        root_metadata = root.stat()
        assert root_identity == (root_metadata.st_dev, root_metadata.st_ino)
        descriptor = real_open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        opened_descriptors.add(descriptor)
        return descriptor

    def fake_fstat(descriptor):
        if descriptor in opened_descriptors:
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0)
        return real_fstat(descriptor)

    monkeypatch.setattr(
        "automation.inago.maintenance.evidence._open_source_descriptor",
        open_regular_source,
    )
    monkeypatch.setattr(
        "automation.inago.maintenance.evidence.os.fstat",
        fake_fstat,
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L01", "safe-source", "trace.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )

    assert result.manifest["events"] == []
    assert result.manifest["artifacts"][0]["status"] == "unavailable"


def test_c15_safe_open_rejects_changed_source_root_identity(tmp_path):
    source_root = tmp_path / "sources"
    source_path = source_root / "trace.jsonl"
    _write_jsonl(
        source_path,
        [{"event": "request_started", "timestamp": "2026-10-17T13:00:00Z"}],
    )
    root = source_root.resolve()
    root_metadata = root.stat()
    wrong_identity = (root_metadata.st_dev, root_metadata.st_ino + 1)

    with pytest.raises(OSError, match="source_root_identity_changed"):
        evidence_module._open_source_descriptor(source_path, root, wrong_identity)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse handle behavior")
def test_c15_windows_safe_open_rejects_final_reparse_point(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    outside_source = tmp_path / "outside.jsonl"
    outside_source.write_text('{"status":"outside"}\n', encoding="utf-8")
    reparse_source = source_root / "source.jsonl"
    try:
        reparse_source.symlink_to(outside_source)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation unavailable: {type(exc).__name__}")

    root = source_root.absolute()
    root_metadata = root.stat()
    root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    with pytest.raises(OSError, match="source_reparse_point"):
        evidence_module._open_source_descriptor(reparse_source, root, root_identity)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse handle behavior")
def test_c15_windows_safe_open_rejects_intermediate_reparse_point(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "source.jsonl").write_text(
        '{"status":"outside"}\n', encoding="utf-8"
    )
    reparse_directory = source_root / "nested"
    try:
        reparse_directory.symlink_to(outside_directory, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation unavailable: {type(exc).__name__}")

    root = source_root.absolute()
    root_metadata = root.stat()
    root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    with pytest.raises(OSError, match="source_reparse_point"):
        evidence_module._open_source_descriptor(
            reparse_directory / "source.jsonl", root, root_identity
        )


def test_c15_collection_does_not_probe_paths_before_safe_open(tmp_path, monkeypatch):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    outside_source = outside_directory / "source.jsonl"
    outside_source.write_text(
        '{"event":"outside-canary","timestamp":"2026-10-17T13:00:00Z"}\n',
        encoding="utf-8",
    )
    reparse_directory = source_root / "nested"
    try:
        reparse_directory.symlink_to(outside_directory, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {type(exc).__name__}")

    original_exists = Path.exists
    original_lstat = Path.lstat

    def reject_source_path_probe(path):
        try:
            path.relative_to(source_root)
        except ValueError:
            return False
        raise AssertionError("source paths must be inspected only through safe handles")

    def guarded_exists(path):
        if reject_source_path_probe(path):
            return False
        return original_exists(path)

    def guarded_lstat(path):
        if reject_source_path_probe(path):
            return None
        return original_lstat(path)

    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(Path, "lstat", guarded_lstat)
    result = collect_evidence(
        source_root=source_root,
        sources=(
            EvidenceSource("L01", "linked-source", "nested/source.jsonl", "jsonl"),
        ),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )

    assert result.manifest["artifacts"][0]["status"] == "unavailable"
    assert "outside-canary" not in json.dumps(result.manifest)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse handle behavior")
def test_c15_windows_safe_open_rejects_reparse_source_root(tmp_path):
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    reparse_root = tmp_path / "sources"
    try:
        reparse_root.symlink_to(outside_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation unavailable: {type(exc).__name__}")

    with pytest.raises(OSError):
        evidence_module._open_source_root_descriptor(reparse_root.absolute())


def test_c15_contract_has_26_statuses_and_distinguishes_empty_missing_expired(
    tmp_path,
):
    source_root = tmp_path / "sources"
    formats = {
        "L01": "jsonl",
        "L02": "jsonl",
        "L03": "jsonl",
        "L04": "jsonl",
        "L05": "json",
        "O01": "json",
        "O02": "json",
        "O03": "json",
        "A01": "json",
        "A02": "response",
        "A03": "json",
        "M01": "jsonl",
        "M02": "response",
        "M03": "json",
        "S01": "sqlite",
        "S02": "json",
        "S03": "json",
        "W01": "json",
        "V01": "html_snapshot",
        "H01": "binary",
        "H02": "json",
        "B01": "sqlite",
        "B02": "sqlite",
        "X01": "text",
        "X02": "jsonl",
        "X03": "json",
    }
    sources = []
    for class_id, source_format in formats.items():
        extension = {
            "jsonl": ".jsonl",
            "json": ".json",
            "response": ".response",
            "sqlite": ".sqlite",
            "html_snapshot": ".html",
            "binary": ".bin",
            "text": ".txt",
        }[source_format]
        relative_path = f"fixture/{class_id}{extension}"
        source_path = source_root / relative_path
        if class_id not in {"V01", "W01"}:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            if class_id == "A02":
                source_path.write_bytes(b"")
            elif source_format == "jsonl":
                _write_jsonl(
                    source_path,
                    [
                        {
                            "event": "synthetic_event",
                            "timestamp": "2026-10-17T13:01:00+09:00",
                            **(
                                {"thread_id": "THREAD-C15"} if class_id == "X02" else {}
                            ),
                        }
                    ],
                )
            elif source_format in {"json", "response"}:
                source_path.write_text(
                    json.dumps(
                        {
                            "event": "synthetic_event",
                            "timestamp": "2026-10-17T13:01:00+09:00",
                            **(
                                {"thread_id": "THREAD-C15"} if class_id == "X02" else {}
                            ),
                        }
                    ),
                    encoding="utf-8",
                )
            elif source_format == "sqlite":
                connection = sqlite3.connect(source_path)
                connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
                connection.execute("INSERT INTO sample VALUES (1)")
                connection.commit()
                connection.close()
            else:
                source_path.write_bytes(b"synthetic source content")
        sources.append(
            EvidenceSource(
                class_id,
                class_id.lower(),
                relative_path,
                source_format,
                "expired" if class_id == "V01" else "not_saved",
                retention_status=("known" if class_id == "L01" else "unknown"),
                expires_at=("2026-10-18T00:00:00+09:00" if class_id == "L01" else None),
            )
        )

    result = collect_evidence(
        source_root=source_root,
        sources=tuple(sources),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"thread_id": "THREAD-C15"},
    )

    classes = {entry["class_id"]: entry for entry in result.manifest["classes"]}
    assert set(classes) == set(EVIDENCE_CLASS_IDS)
    assert classes["A02"]["status"] == "empty_response"
    assert classes["V01"]["status"] == "expired"
    assert classes["W01"]["status"] == "not_saved"
    assert classes["H01"]["status"] == "content_omitted"
    assert all(entry["artifact_count"] == 1 for entry in classes.values())
    assert len(result.manifest["artifacts"]) == 26
    cli_artifact = next(
        artifact
        for artifact in result.manifest["artifacts"]
        if artifact["class_id"] == "L01"
    )
    assert cli_artifact["source_format"] == "jsonl"
    assert cli_artifact["retention_status"] == "known"
    assert cli_artifact["source_expires_at_raw"] == "2026-10-18T00:00:00+09:00"
    assert cli_artifact["source_expires_at_utc"] == "2026-10-17T15:00:00Z"


def test_c15_contract_records_a_temp_file_disappearing_after_discovery(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "job.json"
    source.write_text('{"status":"complete"}', encoding="utf-8")
    original_safe_open = evidence_module._open_source_descriptor

    def disappear_on_open(path, root, root_identity):
        if path == source:
            path.unlink()
            raise FileNotFoundError(path)
        return original_safe_open(path, root, root_identity)

    monkeypatch.setattr(
        "automation.inago.maintenance.evidence._open_source_descriptor",
        disappear_on_open,
    )
    result = collect_evidence(
        source_root=source_root,
        sources=(
            EvidenceSource(
                "W01",
                "temp-job",
                "job.json",
                "json",
                present_at_discovery=True,
            ),
        ),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )

    assert result.manifest["artifacts"][0]["status"] == "disappeared"
    assert result.manifest["classes"][-1]["class_id"] == "X03"


def test_c16_contract_snapshots_only_committed_wal_state_read_only(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.sqlite"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    connection.execute("INSERT INTO sample VALUES ('committed')")
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("INSERT INTO sample VALUES ('uncommitted')")
    destination = tmp_path / "snapshot.sqlite"

    def reject_read_bytes(_path):
        raise AssertionError("SQLite snapshot hashing must use bounded reads")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    snapshot = snapshot_sqlite(source, destination)
    assert snapshot.integrity_check == "ok"
    assert snapshot.foreign_key_errors == 0
    assert snapshot.row_count == 1
    assert destination.is_file()
    digest = hashlib.sha256()
    with destination.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    assert snapshot.sha256 == digest.hexdigest()
    assert connection.execute("SELECT count(*) FROM sample").fetchone()[0] == 2

    copied = sqlite3.connect(destination)
    try:
        assert copied.execute("SELECT value FROM sample").fetchall() == [("committed",)]
    finally:
        copied.close()
        connection.rollback()
        connection.close()


def test_c17_contract_drops_secrets_personal_content_and_untrusted_instructions(
    tmp_path,
):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "job.jsonl",
        [
            {
                "event": "job_finished",
                "timestamp": "2026-10-17T13:01:00+09:00",
                "run_id": "RUN-C17",
                "status": "failed",
                "reason_code": "transport_error",
                "error_code": "sk-live-1234567890abcdefghijklmnop",
                "access_token": "TOKEN_CANARY_7d9f",
                "cookie": "COOKIE_CANARY_2a8b",
                "product_body": "PRODUCT_CANARY_3c5e",
                "display_name": "PERSON_CANARY_4f6a",
                "prompt": "INSTRUCTION_CANARY ignore the allowlist",
                "command": "COMMAND_CANARY run this command",
            },
            {
                "event": "request_finished",
                "timestamp": "2026-10-17T13:02:00+09:00",
                "run_id": "RUN-C17",
                "status": "sk-live-1234567890abcdefghijklmnop",
            },
        ],
    )

    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L03", "job", "job.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"run_id": "RUN-C17"},
    )
    serialized = json.dumps(result.manifest, ensure_ascii=False)
    serialized += "".join(data.decode("utf-8") for data in result.artifacts.values())

    for canary in (
        "TOKEN_CANARY_7d9f",
        "COOKIE_CANARY_2a8b",
        "PRODUCT_CANARY_3c5e",
        "PERSON_CANARY_4f6a",
        "INSTRUCTION_CANARY",
        "COMMAND_CANARY",
        "sk-live-1234567890abcdefghijklmnop",
    ):
        assert canary not in serialized
    assert result.manifest["events"][0]["fields"]["reason_code"] == "transport_error"
    assert "error_code" not in result.manifest["events"][0]["fields"]
    assert [event["fields"].get("status") for event in result.manifest["events"]] == [
        "failed",
        None,
    ]


def test_c22_contract_keeps_unknown_and_not_listed_separate_from_zero(tmp_path):
    result = collect_evidence(
        source_root=tmp_path,
        sources=(EvidenceSource("M03", "metrics", "expired.json", "json", "expired"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"run_id": "RUN-C22"},
    )
    classes = {entry["class_id"]: entry for entry in result.manifest["classes"]}

    assert classes["M03"]["status"] == "expired"
    assert classes["M03"]["record_count"] is None
    assert classes["M01"]["status"] == "not_listed"
    assert classes["M01"]["record_count"] is None
    assert "success" not in classes["M03"]


def test_c58_contract_does_not_publish_partial_bundle_on_atomic_move_failure(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "sources"
    _write_jsonl(
        source_root / "trace.jsonl",
        [{"event": "trace_event", "timestamp": "2026-10-17T13:01:00+09:00"}],
    )
    result = collect_evidence(
        source_root=source_root,
        sources=(EvidenceSource("L02", "trace", "trace.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )
    output = tmp_path / "published"

    def fail_replace(*_args, **_kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr("automation.inago.maintenance.evidence.os.rename", fail_replace)
    with pytest.raises(OSError):
        write_evidence_bundle(output, result)

    assert not output.exists()
    assert list(tmp_path.glob(".published.*")) == []


def test_prior_e0_manifest_validation_returns_only_safe_summary(tmp_path):
    manifest = {
        "manifest_version": "1.0",
        "captured_at_jst": CAPTURED_AT,
        "target_date": TARGET_DATE,
        "incident_id": "INC-TEST",
        "collection_mode": "read_only",
        "redaction": "filtered",
        "evidence": [
            {
                "path": "private/evidence/cli.filtered.json",
                "source": "rakuten-room-inago/runtime/logs/production-cli.jsonl",
                "records": 1,
                "capture": "sha256=not-exported",
            },
            {
                "path": "private/evidence/backup.sqlite",
                "source": "rakuten-room-inago/backups/daily-20261017/state.sqlite",
                "bytes": 128,
                "last_write_time": CAPTURED_AT,
                "capture": "sha256=not-exported",
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    summary = inspect_prior_manifest(path)

    assert summary == {
        "manifest_version": "1.0",
        "target_date": TARGET_DATE,
        "evidence_count": 2,
        "manifested_class_ids": ["B01", "L01"],
    }
    assert "path" not in summary
    assert "incident_id" not in summary


def test_schema_allowlist_matches_the_26_class_collector_contract(tmp_path):
    schema_path = Path("automation/inago/schemas/maintenance-evidence.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$defs"]["classId"]["enum"] == list(EVIDENCE_CLASS_IDS)

    _write_jsonl(
        tmp_path / "trace.jsonl",
        [{"event": "request_started", "timestamp": 1792195800}],
    )
    result = collect_evidence(
        source_root=tmp_path,
        sources=(EvidenceSource("L01", "trace", "trace.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={},
    )
    Draft202012Validator(schema).validate(result.manifest)
    field_properties = schema["$defs"]["event"]["properties"]["fields"]["properties"]
    assert {
        field: set(field_properties[field]["enum"]) for field in EVIDENCE_VALUE_ENUMS
    } == {field: set(values) for field, values in EVIDENCE_VALUE_ENUMS.items()}

    _write_jsonl(
        tmp_path / "thread.jsonl",
        [
            {
                "event": "request_started",
                "timestamp": "2026-10-17T13:00:00Z",
                "thread_id": "THREAD-X02",
            }
        ],
    )
    x02_result = collect_evidence(
        source_root=tmp_path,
        sources=(EvidenceSource("X02", "thread", "thread.jsonl", "jsonl"),),
        target_date=TARGET_DATE,
        captured_at=CAPTURED_AT,
        correlation={"thread_id": "THREAD-X02"},
    )
    validator = Draft202012Validator(schema)
    validator.validate(x02_result.manifest)
    missing_thread_scope = deepcopy(x02_result.manifest)
    missing_thread_scope["correlation"].pop("thread_id")
    with pytest.raises(ValidationError):
        validator.validate(missing_thread_scope)
    wrong_x02_format = deepcopy(x02_result.manifest)
    wrong_x02_format["artifacts"][0]["source_format"] = "sqlite"
    with pytest.raises(ValidationError):
        validator.validate(wrong_x02_format)

    duplicate_class = deepcopy(result.manifest)
    duplicate_class["classes"][1]["class_id"] = duplicate_class["classes"][0][
        "class_id"
    ]
    assert list(Draft202012Validator(schema).iter_errors(duplicate_class))

    class_only_artifact_status = deepcopy(result.manifest)
    class_only_artifact_status["artifacts"][0]["status"] = "not_listed"
    assert list(Draft202012Validator(schema).iter_errors(class_only_artifact_status))
