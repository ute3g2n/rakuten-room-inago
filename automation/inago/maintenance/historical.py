"""Idempotent intake for a verified historical daily-launch observation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import TYPE_CHECKING, Any, Mapping

from automation.inago.maintenance.investigation import _assert_no_reparse_components
from automation.inago.maintenance.safe_file import (
    SafeFileError,
    ensure_directory_beneath,
    open_file_beneath,
    unlink_file_beneath,
)
from automation.inago.maintenance.store import (
    DailyCheckRecord,
    HistoricalJobReference,
    IdempotencyConflictError,
    JobInputManifest,
    MaintenanceJobRecord,
    MaintenanceStore,
    MaintenanceStoreError,
)

if TYPE_CHECKING:
    from automation.inago.maintenance.controller import MaintenanceController


_OBSERVATION_KEYS = {
    "schema_version",
    "original_source_launch_id",
    "source_run_id",
    "target_date",
    "launch_status",
    "daily_status",
    "exit_code",
    "reason_code",
    "evidence_ref",
    "evidence_sha256",
    "observed_at",
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_OBSERVATION_BYTES = 64 * 1024
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_MAX_HISTORICAL_SOURCE_FILES = 8
_MAX_HISTORICAL_SOURCE_FILE_BYTES = 16 * 1024
_MAX_HISTORICAL_SOURCE_TOTAL_BYTES = 64 * 1024
_HISTORICAL_SOURCE_FILE_NAMES = frozenset(
    {
        "e0-manifest.json",
        "production-cli.filtered.json",
        "scheduler.filtered.json",
        "read-aggregate.json",
    }
)
_INTAKE_RECORD_KEYS = {
    "schema_version",
    "original_source_launch_id",
    "maintenance_source_launch_id",
    "source_run_id",
    "target_date",
    "launch_status",
    "daily_status",
    "exit_code",
    "reason_code",
    "evidence_ref",
    "evidence_sha256",
    "observation_sha256",
    "release_id",
    "check_id",
    "incident_id",
    "job_id",
    "manifest_sha256",
    "enqueued_at",
}


@dataclass(frozen=True, slots=True)
class HistoricalSourceDocument:
    """One allowlisted, hash-bound JSON source copied into historical intake."""

    relative_ref: str
    sha256: str
    document: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class HistoricalWorkItem:
    """Verified file and store bindings for one run-null maintenance job."""

    source_launch_id: str
    target_date: str
    check: DailyCheckRecord
    job: MaintenanceJobRecord
    manifest: JobInputManifest
    intake_record: Mapping[str, Any]
    intake_record_bytes: bytes
    observation: Mapping[str, Any]
    observation_ref: str
    observation_bytes: bytes
    evidence_ref: str
    evidence_bytes: bytes
    evidence_document: Mapping[str, Any]
    intake_ref: str
    source_documents: tuple[HistoricalSourceDocument, ...] = ()


class HistoricalIntakeError(ValueError):
    """A safe error code for invalid historical intake data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise HistoricalIntakeError("historical_evidence_ref_invalid")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise HistoricalIntakeError("historical_evidence_ref_invalid")
    return relative.as_posix()


def validate_historical_observation(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _OBSERVATION_KEYS:
        raise HistoricalIntakeError("historical_observation_schema_invalid")
    document = dict(value)
    if document["schema_version"] != "historical-daily-intake-v1":
        raise HistoricalIntakeError("historical_observation_schema_invalid")
    if not isinstance(document["original_source_launch_id"], str) or not _ID.fullmatch(
        document["original_source_launch_id"]
    ):
        raise HistoricalIntakeError("historical_source_launch_id_invalid")
    if document["source_run_id"] is not None:
        raise HistoricalIntakeError("historical_source_run_must_be_null")
    target = document["target_date"]
    if not isinstance(target, str):
        raise HistoricalIntakeError("historical_target_date_invalid")
    try:
        from datetime import date

        if date.fromisoformat(target).isoformat() != target:
            raise ValueError
    except ValueError as exc:
        raise HistoricalIntakeError("historical_target_date_invalid") from exc
    launch_status = document["launch_status"]
    if not isinstance(launch_status, str) or launch_status not in {
        "failed",
        "blocked",
        "unknown",
    }:
        raise HistoricalIntakeError("historical_launch_status_invalid")
    daily_status = document["daily_status"]
    if not isinstance(daily_status, str) or daily_status not in {
        "failed",
        "blocked",
        "unknown",
    }:
        raise HistoricalIntakeError("historical_daily_status_invalid")
    exit_code = document["exit_code"]
    if (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or not 1 <= exit_code <= 255
    ):
        raise HistoricalIntakeError("historical_exit_code_invalid")
    if not isinstance(document["reason_code"], str) or not _CODE.fullmatch(
        document["reason_code"]
    ):
        raise HistoricalIntakeError("historical_reason_code_invalid")
    document["evidence_ref"] = _safe_relative_path(document["evidence_ref"])
    if not isinstance(document["evidence_sha256"], str) or not _SHA256.fullmatch(
        document["evidence_sha256"]
    ):
        raise HistoricalIntakeError("historical_evidence_hash_invalid")
    observed_at = document["observed_at"]
    if not isinstance(observed_at, str):
        raise HistoricalIntakeError("historical_observed_at_invalid")
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalIntakeError("historical_observed_at_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalIntakeError("historical_observed_at_invalid")
    return document


def _read_observation(
    data_root: Path, relative_path: str
) -> tuple[dict[str, Any], bytes]:
    try:
        with open_file_beneath(data_root, relative_path, "rb") as stream:
            encoded = stream.read(_MAX_OBSERVATION_BYTES + 1)
    except (OSError, SafeFileError) as exc:
        raise HistoricalIntakeError("historical_observation_unavailable") from exc
    if len(encoded) > _MAX_OBSERVATION_BYTES:
        raise HistoricalIntakeError("historical_observation_too_large")
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalIntakeError("historical_observation_json_invalid") from exc
    return validate_historical_observation(value), encoded


def _write_intake_record(data_root: Path, relative_path: str, payload: bytes) -> bool:
    try:
        relative_parent = PurePosixPath(relative_path).parent.as_posix()
        if relative_parent != ".":
            ensure_directory_beneath(data_root, relative_parent)
        with open_file_beneath(data_root, relative_path, "xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except FileExistsError:
        try:
            with open_file_beneath(data_root, relative_path, "rb") as stream:
                existing = stream.read(_MAX_OBSERVATION_BYTES + 1)
        except (OSError, SafeFileError) as exc:
            raise HistoricalIntakeError("historical_intake_record_unavailable") from exc
        if existing != payload:
            raise IdempotencyConflictError("historical_intake_record_conflict")
        return False
    except (OSError, SafeFileError, RuntimeError, ValueError) as exc:
        try:
            unlink_file_beneath(data_root, relative_path)
        except (OSError, SafeFileError):
            pass
        if isinstance(exc, HistoricalIntakeError):
            raise
        raise HistoricalIntakeError("historical_intake_record_write_failed") from exc


def enqueue_historical_observation(
    controller: MaintenanceController,
    *,
    data_root: str | Path,
    release_id: str,
    observation_ref: str,
    enqueued_at: str,
) -> dict[str, object]:
    """Create one run-null check/incident/job from a verified failed launch."""

    root = Path(data_root).expanduser().resolve(strict=True)
    _assert_no_reparse_components(root)
    relative_ref = _safe_relative_path(observation_ref)
    observation, source_bytes = _read_observation(root, relative_ref)
    if not any(
        observation[key] == "failed" for key in ("launch_status", "daily_status")
    ):
        raise HistoricalIntakeError("historical_failure_not_confirmed")
    try:
        with open_file_beneath(root, observation["evidence_ref"], "rb") as stream:
            evidence_bytes = stream.read(_MAX_EVIDENCE_BYTES + 1)
    except (OSError, SafeFileError) as exc:
        raise HistoricalIntakeError("historical_evidence_unavailable") from exc
    if len(evidence_bytes) > _MAX_EVIDENCE_BYTES:
        raise HistoricalIntakeError("historical_evidence_too_large")
    if hashlib.sha256(evidence_bytes).hexdigest() != observation["evidence_sha256"]:
        raise HistoricalIntakeError("historical_evidence_hash_mismatch")
    original_id = str(observation["original_source_launch_id"])
    target_date = str(observation["target_date"])
    source_digest = hashlib.sha256(
        f"{target_date}\0{original_id}".encode("utf-8")
    ).hexdigest()[:32]
    source_launch_id = f"historical-{source_digest}"
    incident_digest = hashlib.sha256(
        f"historical-incident\0{target_date}\0{original_id}".encode("utf-8")
    ).hexdigest()[:32]
    incident = {
        "dedupe_key": f"historical-{incident_digest}",
        "component": "daily_pipeline",
        "event_code": "historical_daily_failure",
    }
    check = controller.record_daily_check(
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        check_status="abnormal",
        observed_at=str(observation["observed_at"]),
        reason_code="historical_daily_failure_observed",
        incident=incident,
    )
    if check.incident_id is None:
        raise HistoricalIntakeError("historical_incident_unavailable")
    job_id = f"job-historical-{source_digest}"
    job = controller.start_fix_job(
        incident_id=check.incident_id,
        release_id=release_id,
        job_id=job_id,
        started_at=enqueued_at,
        source_run_id=None,
    )
    if not job.started and job.job_id != job_id:
        return {
            "status": "blocked",
            "reason_code": (
                job.reason_codes[0] if job.reason_codes else "job_unavailable"
            ),
            "release_id": release_id,
            "gate_status": job.gate_status,
            "check_id": check.check_id,
            "incident_id": check.incident_id,
        }
    manifest = controller.bind_job_input_manifest(
        incident_id=check.incident_id,
        source_launch_id=source_launch_id,
        source_run_id=None,
        target_date=target_date,
        job_id=job_id,
        created_at=enqueued_at,
    )
    intake_record = {
        "schema_version": "historical-intake-record-v1",
        "original_source_launch_id": original_id,
        "maintenance_source_launch_id": source_launch_id,
        "source_run_id": None,
        "target_date": target_date,
        "launch_status": observation["launch_status"],
        "daily_status": observation["daily_status"],
        "exit_code": observation["exit_code"],
        "reason_code": observation["reason_code"],
        "evidence_ref": observation["evidence_ref"],
        "evidence_sha256": observation["evidence_sha256"],
        "observation_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "release_id": release_id,
        "check_id": check.check_id,
        "incident_id": check.incident_id,
        "job_id": job_id,
        "manifest_sha256": manifest.manifest_sha256,
        "enqueued_at": enqueued_at,
    }
    payload = (
        json.dumps(
            intake_record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    intake_ref = f"historical-intake/{target_date}/{source_launch_id}.json"
    record_created = _write_intake_record(root, intake_ref, payload)
    return {
        "status": "queued",
        "source_launch_id": source_launch_id,
        "original_source_launch_id": original_id,
        "source_run_id": None,
        "target_date": target_date,
        "release_id": release_id,
        "gate_status": job.gate_status,
        "check_id": check.check_id,
        "incident_id": check.incident_id,
        "job_id": job_id,
        "job_started": job.started,
        "evidence_ref": intake_ref,
        "evidence_created": record_created,
        "duplicate": not check.created or not job.started,
    }


def _read_historical_json(
    data_root: Path,
    relative_path: str,
    *,
    max_bytes: int,
    unavailable_code: str,
    size_code: str,
    invalid_code: str,
) -> tuple[dict[str, Any], bytes]:
    relative = _safe_relative_path(relative_path)
    try:
        with open_file_beneath(data_root, relative, "rb") as stream:
            encoded = stream.read(max_bytes + 1)
    except (OSError, SafeFileError) as exc:
        raise HistoricalIntakeError(unavailable_code) from exc
    if len(encoded) > max_bytes:
        raise HistoricalIntakeError(size_code)
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalIntakeError(invalid_code) from exc
    if not isinstance(value, dict):
        raise HistoricalIntakeError(invalid_code)
    return value, encoded


def _read_historical_source_documents(
    data_root: Path,
    *,
    target_date: str,
    evidence_document: Mapping[str, Any],
) -> tuple[HistoricalSourceDocument, ...]:
    descriptors = evidence_document.get("copied_source_files", [])
    if (
        not isinstance(descriptors, list)
        or len(descriptors) > _MAX_HISTORICAL_SOURCE_FILES
    ):
        raise HistoricalIntakeError("historical_source_file_manifest_invalid")

    documents: list[HistoricalSourceDocument] = []
    seen_refs: set[str] = set()
    total_bytes = 0
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping) or set(descriptor) != {
            "bytes",
            "relative_ref",
            "sha256",
        }:
            raise HistoricalIntakeError("historical_source_file_manifest_invalid")
        relative_ref = _safe_relative_path(descriptor["relative_ref"])
        parts = PurePosixPath(relative_ref).parts
        if (
            len(parts) != 3
            or parts[:2] != ("historical-input", target_date)
            or parts[2] not in _HISTORICAL_SOURCE_FILE_NAMES
            or relative_ref in seen_refs
        ):
            raise HistoricalIntakeError("historical_source_file_ref_invalid")
        expected_bytes = descriptor["bytes"]
        expected_sha256 = descriptor["sha256"]
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or not 0 <= expected_bytes <= _MAX_HISTORICAL_SOURCE_FILE_BYTES
            or not isinstance(expected_sha256, str)
            or not _SHA256.fullmatch(expected_sha256)
        ):
            raise HistoricalIntakeError("historical_source_file_manifest_invalid")
        total_bytes += expected_bytes
        if total_bytes > _MAX_HISTORICAL_SOURCE_TOTAL_BYTES:
            raise HistoricalIntakeError("historical_source_files_too_large")
        try:
            with open_file_beneath(data_root, relative_ref, "rb") as stream:
                encoded = stream.read(_MAX_HISTORICAL_SOURCE_FILE_BYTES + 1)
        except (OSError, SafeFileError) as exc:
            raise HistoricalIntakeError("historical_source_file_unavailable") from exc
        if len(encoded) > _MAX_HISTORICAL_SOURCE_FILE_BYTES:
            raise HistoricalIntakeError("historical_source_file_too_large")
        if (
            len(encoded) != expected_bytes
            or hashlib.sha256(encoded).hexdigest() != expected_sha256
        ):
            raise HistoricalIntakeError("historical_source_file_hash_mismatch")
        try:
            document = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoricalIntakeError("historical_source_file_invalid") from exc
        if not isinstance(document, dict):
            raise HistoricalIntakeError("historical_source_file_invalid")
        documents.append(
            HistoricalSourceDocument(
                relative_ref=relative_ref,
                sha256=expected_sha256,
                document=document,
            )
        )
        seen_refs.add(relative_ref)
    return tuple(documents)


def load_historical_work_item(
    store: MaintenanceStore,
    *,
    data_root: str | Path,
    source_launch_id: str,
    job_id: str,
    allow_blocked: bool = False,
) -> HistoricalWorkItem:
    """Verify a run-null job and its immutable inputs for dispatch or resume."""

    if not isinstance(allow_blocked, bool):
        raise HistoricalIntakeError("historical_allow_blocked_invalid")
    if not isinstance(source_launch_id, str) or not _ID.fullmatch(source_launch_id):
        raise HistoricalIntakeError("historical_source_launch_id_invalid")
    if not source_launch_id.startswith("historical-"):
        raise HistoricalIntakeError("historical_source_launch_id_invalid")
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise HistoricalIntakeError("historical_job_id_invalid")
    root = Path(data_root).expanduser().resolve(strict=True)
    _assert_no_reparse_components(root)

    try:
        references = store.list_historical_job_references(
            source_launch_id=source_launch_id,
            job_id=job_id,
            limit=1,
            include_blocked=allow_blocked,
        )
        check = store.get_daily_check_by_launch(source_launch_id)
        if check is None:
            raise HistoricalIntakeError("historical_check_missing")
        jobs = store.list_workflow_jobs_for_launch(
            source_launch_id=source_launch_id,
            source_run_id=None,
            target_date=check.target_date,
            job_id=job_id,
        )
    except HistoricalIntakeError:
        raise
    except Exception as exc:
        raise HistoricalIntakeError("historical_job_identity_unavailable") from exc

    if not references or not jobs:
        raise HistoricalIntakeError("historical_job_identity_missing")
    reference: HistoricalJobReference = references[0]
    job = jobs[0]
    if (
        reference.source_launch_id != source_launch_id
        or reference.source_run_id is not None
        or check.source_launch_id != source_launch_id
        or check.source_run_id is not None
        or check.target_date != reference.target_date
        or check.check_status != "abnormal"
        or check.reason_code != "historical_daily_failure_observed"
        or reference.job_id != job_id
        or job.job_id != job_id
        or job.incident_id != reference.incident_id
        or job.release_id != reference.release_id
        or job.status != reference.status
        or job.source_run_id is not None
        or job.status
        not in (
            {"queued", "running", "blocked"} if allow_blocked else {"queued", "running"}
        )
        or store.get_daily_launch(source_launch_id) is not None
        or store.get_worker_queue(source_launch_id) is not None
    ):
        raise HistoricalIntakeError("historical_job_identity_mismatch")

    expected_identity = {
        "incident_id": job.incident_id,
        "source_launch_id": source_launch_id,
        "source_run_id": None,
        "target_date": check.target_date,
        "job_id": job.job_id,
    }
    try:
        manifest = store.get_job_input_manifest(
            job_id=job.job_id,
            expected_identity=expected_identity,
        )
    except (MaintenanceStoreError, OSError, ValueError) as exc:
        raise HistoricalIntakeError("historical_manifest_invalid") from exc

    intake_ref = f"historical-intake/{check.target_date}/{source_launch_id}.json"
    intake_record, intake_record_bytes = _read_historical_json(
        root,
        intake_ref,
        max_bytes=_MAX_OBSERVATION_BYTES,
        unavailable_code="historical_intake_record_unavailable",
        size_code="historical_intake_record_too_large",
        invalid_code="historical_intake_record_invalid",
    )
    if set(intake_record) != _INTAKE_RECORD_KEYS:
        raise HistoricalIntakeError("historical_intake_record_invalid")

    observation_ref = f"historical-input/{check.target_date}/observation.json"
    observation_value, observation_bytes = _read_historical_json(
        root,
        observation_ref,
        max_bytes=_MAX_OBSERVATION_BYTES,
        unavailable_code="historical_observation_unavailable",
        size_code="historical_observation_too_large",
        invalid_code="historical_observation_json_invalid",
    )
    try:
        observation = validate_historical_observation(observation_value)
    except HistoricalIntakeError:
        raise
    if not any(
        observation[key] == "failed" for key in ("launch_status", "daily_status")
    ):
        raise HistoricalIntakeError("historical_failure_not_confirmed")

    evidence_ref = _safe_relative_path(observation["evidence_ref"])
    try:
        with open_file_beneath(root, evidence_ref, "rb") as stream:
            evidence_bytes = stream.read(_MAX_EVIDENCE_BYTES + 1)
    except (OSError, SafeFileError) as exc:
        raise HistoricalIntakeError("historical_evidence_unavailable") from exc
    if len(evidence_bytes) > _MAX_EVIDENCE_BYTES:
        raise HistoricalIntakeError("historical_evidence_too_large")
    try:
        evidence_document = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalIntakeError("historical_evidence_invalid") from exc
    if (
        not isinstance(evidence_document, dict)
        or evidence_document.get("schema_version") != "historical-source-evidence-v1"
        or evidence_document.get("target_date") != check.target_date
    ):
        raise HistoricalIntakeError("historical_evidence_invalid")
    source_documents = _read_historical_source_documents(
        root,
        target_date=check.target_date,
        evidence_document=evidence_document,
    )

    observation_sha256 = hashlib.sha256(observation_bytes).hexdigest()
    evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
    if (
        intake_record.get("schema_version") != "historical-intake-record-v1"
        or intake_record.get("maintenance_source_launch_id") != source_launch_id
        or intake_record.get("source_run_id") is not None
        or intake_record.get("target_date") != check.target_date
        or intake_record.get("original_source_launch_id")
        != observation.get("original_source_launch_id")
        or intake_record.get("launch_status") != observation.get("launch_status")
        or intake_record.get("daily_status") != observation.get("daily_status")
        or intake_record.get("exit_code") != observation.get("exit_code")
        or intake_record.get("reason_code") != observation.get("reason_code")
        or intake_record.get("evidence_ref") != evidence_ref
        or intake_record.get("evidence_sha256") != evidence_sha256
        or observation.get("evidence_sha256") != evidence_sha256
        or intake_record.get("observation_sha256") != observation_sha256
        or intake_record.get("release_id") != job.release_id
        or intake_record.get("check_id") != check.check_id
        or intake_record.get("incident_id") != job.incident_id
        or intake_record.get("job_id") != job.job_id
        or intake_record.get("manifest_sha256") != manifest.manifest_sha256
    ):
        raise HistoricalIntakeError("historical_intake_binding_mismatch")

    return HistoricalWorkItem(
        source_launch_id=source_launch_id,
        target_date=check.target_date,
        check=check,
        job=job,
        manifest=manifest,
        intake_record=intake_record,
        intake_record_bytes=intake_record_bytes,
        observation=observation,
        observation_ref=observation_ref,
        observation_bytes=observation_bytes,
        evidence_ref=evidence_ref,
        evidence_bytes=evidence_bytes,
        evidence_document=evidence_document,
        intake_ref=intake_ref,
        source_documents=source_documents,
    )
