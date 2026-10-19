"""Reconcile one maintenance invocation into a durable, truthful terminal."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import tempfile
from typing import Any

from automation.inago.maintenance.failure_journal import append_failure_event
from automation.inago.maintenance.store import (
    LeaseToken,
    MaintenanceStore,
    MaintenanceStoreError,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_STAGES = (
    "check_incident",
    "evidence",
    "job",
    "investigation",
    "planning",
    "repair",
    "deployment",
    "report",
)


def _read_json(path: Path) -> tuple[dict[str, Any] | None, bytes | None]:
    """Read one durable JSON artifact without following a final symlink."""

    if path.is_symlink() or not path.is_file():
        return None, None
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    return (value, raw) if isinstance(value, dict) else (None, raw)


def _durable_stage_handoffs(
    data_root: Path,
    *,
    job_id: str,
    invocation_id: str | None,
    identity: dict[str, object],
) -> tuple[list[str], list[str]]:
    """Check that every stage left its own content-bearing handoff on disk."""

    missing: list[str] = []
    refs: list[str] = []
    if invocation_id is None:
        return list(_STAGES), refs
    folder = data_root / "workflow-handoffs" / job_id / invocation_id
    if folder.is_symlink():
        return list(_STAGES), refs
    for stage in _STAGES:
        path = folder / f"{stage}.json"
        payload, raw = _read_json(path)
        if (
            payload is None
            or raw is None
            or payload.get("schema_version") != "maintenance-stage-handoff-v1"
            or payload.get("stage") != stage
            or payload.get("identity") != {
                key: identity.get(key)
                for key in (
                    "job_id", "incident_id", "source_launch_id", "source_run_id", "target_date"
                )
            }
            or not isinstance(payload.get("content_result"), dict)
            or not payload["content_result"].get("summary")
            or not isinstance(payload["content_result"].get("next_actions"), list)
            or not payload["content_result"]["next_actions"]
        ):
            missing.append(stage)
            continue
        refs.append(path.relative_to(data_root).as_posix())
    return missing, refs


def _published_report_readback(
    store: MaintenanceStore,
    *,
    data_root: Path,
    job_id: str,
    identity: dict[str, object],
) -> tuple[dict[str, object] | None, str | None]:
    """Verify report attempt bytes and the public branch's current report bytes."""

    try:
        attempts = store.list_step_attempts(job_id=job_id)
    except Exception as exc:
        append_failure_event(
            data_root,
            phase="failed",
            operation="workflow.terminal.report_attempt_lookup",
            correlation_id=str(identity.get("source_launch_id") or "workflow-terminal"),
            job_id=job_id,
            incident_id=str(identity.get("incident_id") or ""),
            source_launch_id=str(identity.get("source_launch_id") or ""),
            source_run_id=identity.get("source_run_id"),
            reason_code="report_attempt_lookup_failed",
            error=exc,
            capture_location="workflow_reconciliation._published_report_readback",
            primary_sink="maintenance_store",
        )
        return None, "report_attempt_lookup_failed"
    report_attempt = max(
        (
            item
            for item in attempts
            if item.step_key == "maintenance_report" and item.sequence == 4
        ),
        key=lambda item: item.attempt_no,
        default=None,
    )
    if (
        report_attempt is None
        or report_attempt.status != "complete"
        or report_attempt.job_id != identity.get("job_id")
        or report_attempt.source_run_id != identity.get("source_run_id")
        or report_attempt.output_path != f"{job_id}/{report_attempt.attempt_id}/result.json"
        or not isinstance(report_attempt.output_sha256, str)
        or not isinstance(report_attempt.result_version, str)
    ):
        return None, "report_attempt_or_output_missing"
    attempts_root = store.database_path.parent / "attempts"
    try:
        result_bytes = store.read_attempt_artifact(
            attempts_root=attempts_root,
            relative_path=report_attempt.output_path,
            expected_sha256=report_attempt.output_sha256,
        )
        result = json.loads(result_bytes)
    except Exception:
        return None, "report_result_readback_failed"
    output = result.get("output") if isinstance(result, dict) else None
    output_path = output.get("path") if isinstance(output, dict) else None
    output_parts = PurePosixPath(output_path).parts if isinstance(output_path, str) else ()
    safe_html_path = (
        len(output_parts) == 3
        and output_parts[:2] == (job_id, report_attempt.attempt_id)
        and re.fullmatch(r"report(?:-fallback-[a-f0-9]{20})?\.html", output_parts[2]) is not None
    )
    result_identity = result.get("identity") if isinstance(result, dict) else None
    identity_matches = result_identity == identity
    fallback_input_matches = True
    if (
        not identity_matches
        and isinstance(result_identity, dict)
        and result.get("report_origin") == "controller_fallback"
    ):
        # The visible report input keeps the incomplete supplied manifest identity.
        # Extra result identity fields are allowed only when they match known
        # values; their sources are the immutable job row and current launch.
        identity_matches = all(
            result_identity.get(key) == value
            for key, value in identity.items()
            if key == "source_run_id" or value not in (None, "")
        ) and result_identity.get("job_id") == job_id
    if isinstance(result, dict) and result.get("report_origin") == "controller_fallback":
        input_ref = result.get("report_input")
        input_parts = (
            PurePosixPath(input_ref.get("path")).parts
            if isinstance(input_ref, dict) and isinstance(input_ref.get("path"), str)
            else ()
        )
        if (
            not isinstance(input_ref, dict)
            or set(input_ref) != {"path", "sha256"}
            or len(input_parts) != 3
            or input_parts[:2] != (job_id, report_attempt.attempt_id)
            or re.fullmatch(
                r"report-input-fallback-[a-f0-9]{20}\.json", input_parts[2]
            ) is None
            or not isinstance(input_ref.get("sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", input_ref["sha256"]) is None
        ):
            fallback_input_matches = False
        else:
            try:
                raw_fallback_input = store.read_attempt_artifact(
                    attempts_root=attempts_root,
                    relative_path=input_ref["path"],
                    expected_sha256=input_ref["sha256"],
                )
                fallback_input = json.loads(raw_fallback_input)
                fallback_input_matches = (
                    isinstance(fallback_input, dict)
                    and fallback_input.get("schema_version")
                    == "maintenance-report-fallback-input-v1"
                    and fallback_input.get("identity") == identity
                    and fallback_input.get("storage_identity") == result_identity
                    and fallback_input.get("attempt_id") == report_attempt.attempt_id
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                fallback_input_matches = False
    expected_html_path = output_path if safe_html_path else ""
    if (
        not isinstance(result, dict)
        or not identity_matches
        or not fallback_input_matches
        or result.get("attempt_id") != report_attempt.attempt_id
        or result.get("result_version") != report_attempt.result_version
        or result.get("report_status") not in {"complete", "pending"}
        or result.get("stage_status") not in {"reviewed", "review_unavailable"}
        or not isinstance(result.get("review"), dict)
        or (
            result.get("stage_status") == "reviewed"
            and (
                not (
                    result.get("report_status") == "complete"
                    and result["review"].get("status") == "approved"
                )
                and not (
                    result.get("report_status") == "pending"
                    and result["review"].get("status") in {"changes_requested", "rejected"}
                )
            )
        )
        or (
            result.get("stage_status") == "review_unavailable"
            and (
                result.get("report_status") != "pending"
                or result["review"].get("status") != "unavailable"
            )
        )
        or not isinstance(output, dict)
        or not safe_html_path
        or not isinstance(output.get("sha256"), str)
    ):
        return None, "report_result_identity_or_status_mismatch"
    try:
        html_bytes = store.read_attempt_artifact(
            attempts_root=attempts_root,
            relative_path=expected_html_path,
            expected_sha256=output["sha256"],
        )
    except Exception:
        return None, "final_html_readback_failed"
    html_sha256 = hashlib.sha256(html_bytes).hexdigest()
    publication_path = attempts_root / job_id / report_attempt.attempt_id / "report-publication.json"
    publication, publication_bytes = _read_json(publication_path)
    if publication is None or publication_bytes is None:
        return None, "report_publication_artifact_missing"
    if (
        publication.get("schema_version") != "maintenance-report-publication-v1"
        or publication.get("identity") != identity
        or publication.get("attempt_id") != report_attempt.attempt_id
        or publication.get("result_version") != report_attempt.result_version
        or publication.get("result_sha256") != report_attempt.output_sha256
        or publication.get("report_path")
        != (
            f"reports/incidents/{identity.get('target_date')}/job-"
            f"{hashlib.sha256(job_id.encode('utf-8')).hexdigest()[:20]}.html"
        )
        or publication.get("report_sha256") != html_sha256
        or not isinstance(publication.get("remote_name"), str)
        or not isinstance(publication.get("target_branch"), str)
        or not isinstance(publication.get("publication_commit"), str)
    ):
        return None, "report_publication_binding_mismatch"

    from automation.inago.maintenance.reporting import (
        ReportContractError,
        _git_content_sha256,
        _is_ancestor,
        _remote_report_head,
    )

    repository = Path(__file__).resolve().parents[3]
    try:
        remote_head = _remote_report_head(
            repository, publication["remote_name"], publication["target_branch"]
        )
        publication_commit = publication["publication_commit"]
        if (
            remote_head is None
            or not _is_ancestor(repository, publication_commit, remote_head)
            or _git_content_sha256(
                repository, f"{remote_head}:{publication['report_path']}"
            )
            != html_sha256
        ):
            return None, "public_report_target_readback_mismatch"
    except (OSError, RuntimeError, ValueError, ReportContractError) as exc:
        append_failure_event(
            data_root,
            phase="failed",
            operation="workflow.terminal.public_report_readback",
            correlation_id=str(identity.get("source_launch_id") or "workflow-terminal"),
            job_id=job_id,
            incident_id=str(identity.get("incident_id") or ""),
            source_launch_id=str(identity.get("source_launch_id") or ""),
            source_run_id=identity.get("source_run_id"),
            reason_code="public_report_target_readback_failed",
            error=exc,
            capture_location="workflow_reconciliation._published_report_readback",
            input_refs=(str(publication.get("report_path") or ""),),
            primary_sink="git_remote_readback",
        )
        return None, "public_report_target_readback_failed"
    return {
        "attempt_id": report_attempt.attempt_id,
        "result_path": report_attempt.output_path,
        "result_sha256": report_attempt.output_sha256,
        "html_path": expected_html_path,
        "html_sha256": html_sha256,
        "publication_path": publication_path.relative_to(data_root).as_posix(),
        "publication_sha256": hashlib.sha256(publication_bytes).hexdigest(),
        "publication_commit": publication_commit,
        "remote_name": publication["remote_name"],
        "target_branch": publication["target_branch"],
        "remote_head_readback": remote_head,
        "public_html_sha256": html_sha256,
    }, None


def _attempt_and_deployment_outputs(
    store: MaintenanceStore, *, job_id: str, identity: dict[str, object]
) -> tuple[list[dict[str, object]], list[str], dict[str, str] | None]:
    """Read back formal investigation/plan/repair outputs and deployment result."""

    try:
        attempts = store.list_step_attempts(job_id=job_id)
    except Exception as exc:
        append_failure_event(
            store.database_path.parent.parent,
            phase="failed",
            operation="workflow.terminal.step_attempt_lookup",
            correlation_id=str(identity.get("source_launch_id") or "workflow-terminal"),
            job_id=job_id,
            incident_id=str(identity.get("incident_id") or ""),
            source_launch_id=str(identity.get("source_launch_id") or ""),
            source_run_id=identity.get("source_run_id"),
            reason_code="terminal_attempt_lookup_failed",
            error=exc,
            capture_location="workflow_reconciliation._attempt_and_deployment_outputs",
            primary_sink="maintenance_store",
        )
        return [], ["step_attempt_lookup_failed"], None
    attempts_root = store.database_path.parent / "attempts"
    refs: list[dict[str, object]] = []
    gaps: list[str] = []
    for sequence, step_key in (
        (1, "maintenance_investigation"),
        (2, "maintenance_planning"),
        (3, "maintenance_repair"),
    ):
        candidate = max(
            (
                item for item in attempts
                if item.sequence == sequence and item.step_key == step_key
            ),
            key=lambda item: item.attempt_no,
            default=None,
        )
        if candidate is None:
            gaps.append(f"{step_key}:attempt_missing")
            continue
        if (
            candidate.job_id != job_id
            or candidate.source_run_id != identity.get("source_run_id")
            or not isinstance(candidate.output_path, str)
            or not isinstance(candidate.output_sha256, str)
        ):
            gaps.append(f"{step_key}:output_reference_missing_or_identity_mismatch")
            continue
        try:
            content = store.read_attempt_artifact(
                attempts_root=attempts_root,
                relative_path=candidate.output_path,
                expected_sha256=candidate.output_sha256,
            )
        except Exception:
            gaps.append(f"{step_key}:output_readback_failed")
            continue
        refs.append(
            {
                "step_key": step_key,
                "attempt_id": candidate.attempt_id,
                "status": candidate.status,
                "result_version": candidate.result_version,
                "output_path": candidate.output_path,
                "output_sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    repair = max(
        (
            item for item in attempts
            if item.step_key == "maintenance_repair" and item.sequence == 3
        ),
        key=lambda item: item.attempt_no,
        default=None,
    )
    deployment_ref: dict[str, str] | None = None
    if repair is None:
        gaps.append("deployment:repair_attempt_missing")
    else:
        folder = attempts_root / job_id / repair.attempt_id
        for filename in ("deployment-result.json", "deployment-not-performed.json"):
            path = folder / filename
            payload, raw = _read_json(path)
            if payload is None or raw is None:
                continue
            if filename == "deployment-not-performed.json" and payload.get("identity") != identity:
                gaps.append("deployment:output_identity_mismatch")
                break
            if filename == "deployment-result.json" and not all(
                isinstance(payload.get(key), str)
                for key in ("deployment_id", "release_id", "status", "source_commit", "tested_commit")
            ):
                gaps.append("deployment:output_fields_invalid")
                break
            deployment_ref = {
                "path": path.relative_to(attempts_root).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            break
        if deployment_ref is None and not any(
            item.startswith("deployment:") for item in gaps
        ):
            gaps.append("deployment:output_missing")
    return refs, gaps, deployment_ref


def reconcile_workflow_terminal(
    store: MaintenanceStore,
    *,
    job_id: str,
    lease: LeaseToken,
    expected_identity: dict[str, object],
) -> dict[str, object]:
    """Persist current invocation observations and separate flow from incident state."""

    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise MaintenanceStoreError("workflow_terminal_job_invalid")
    manifest = store.get_job_input_manifest(
        job_id=job_id, expected_identity=expected_identity
    )
    identity = manifest.identity()
    observations = store.list_workflow_stage_observations(job_id=job_id)
    current_invocation = max(
        observations,
        key=lambda row: str(row.get("observed_at") or ""),
        default=None,
    )
    invocation_id = (
        str(current_invocation["invocation_id"])
        if current_invocation is not None
        else None
    )
    current_rows = [
        row for row in observations if row.get("invocation_id") == invocation_id
    ] if invocation_id else []
    by_stage = {str(row["stage_name"]): row for row in current_rows}
    missing_stages = [name for name in _STAGES if name not in by_stage]
    identity_mismatches = [
        str(row.get("stage_name"))
        for row in current_rows
        if row.get("source_launch_id") != identity.get("source_launch_id")
        or row.get("source_run_id") != identity.get("source_run_id")
        or row.get("target_date") != identity.get("target_date")
    ]
    report_row = by_stage.get("report")
    report_complete = (
        isinstance(report_row, dict) and report_row.get("status") == "complete"
    )
    data_root = store.database_path.parent.parent
    handoff_gaps, handoff_refs = _durable_stage_handoffs(
        data_root,
        job_id=job_id,
        invocation_id=invocation_id,
        identity=identity,
    )
    report_evidence, report_evidence_reason = _published_report_readback(
        store, data_root=data_root, job_id=job_id, identity=identity
    )
    attempt_refs, attempt_gaps, deployment_ref = _attempt_and_deployment_outputs(
        store, job_id=job_id, identity=identity
    )
    # The publication runner records bytes only. This reconciler is the sole
    # terminal writer, after every durable handoff, formal output, deployment
    # record, report sidecar, and remote public readback has been verified.
    terminal_verification = {
        "all_stage_observations_present": not missing_stages,
        "stage_observation_identity_matches": not identity_mismatches,
        "all_content_handoffs_read_back": not handoff_gaps,
        "formal_attempt_outputs_read_back": not attempt_gaps,
        "deployment_output_read_back": deployment_ref is not None,
        "report_complete_observation": report_complete,
        "report_result_html_publication_sidecar_and_remote_readback": (
            report_evidence is not None
        ),
    }
    workflow_complete = all(terminal_verification.values())
    final_status = "complete" if workflow_complete else "failed"
    reason = None if workflow_complete else "workflow_terminal_observations_incomplete"
    terminal = {
        "schema_version": "maintenance-workflow-terminal-v1",
        "identity": identity,
        "invocation_id": invocation_id,
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "workflow_completion": "complete" if workflow_complete else "incomplete",
        "incident_resolution": "unknown",
        "job_terminal_status": final_status,
        "reason_code": reason,
        "stage_observations": [by_stage[name] for name in _STAGES if name in by_stage],
        "missing_stages": missing_stages,
        "identity_mismatch_stages": identity_mismatches,
        "missing_stage_handoffs": handoff_gaps,
        "stage_handoff_refs": handoff_refs,
        "attempt_output_refs": attempt_refs,
        "missing_attempt_outputs": attempt_gaps,
        "deployment_output_ref": deployment_ref,
        "report_publication_readback": report_evidence,
        "report_publication_readback_reason": report_evidence_reason,
        "terminal_verification": terminal_verification,
        "next_actions": (
            []
            if workflow_complete
            else ["同じjobの耐久記録から未完了工程と最終報告を再開する。"]
        ),
    }
    # First leave a durable non-complete checkpoint. A crash before the DB
    # terminal write can never leave a file claiming the flow was completed.
    desired_completion = terminal["workflow_completion"]
    desired_status = terminal["job_terminal_status"]
    desired_reason = terminal["reason_code"]
    terminal["workflow_completion"] = "incomplete"
    terminal["job_terminal_status"] = "failed"
    terminal["reason_code"] = "workflow_terminal_finalize_pending"
    terminal["next_actions"] = ["同じjobの終端登録と公開記録を再照合する。"]
    artifact_ref = _save_terminal_artifact(data_root, terminal)
    terminal["artifact_ref"] = artifact_ref
    try:
        store.finalize_job(
            job_id=job_id,
            final_status=desired_status,
            lease=lease,
            finalized_at=datetime.now(timezone.utc),
            reason_code=desired_reason,
            # A completed maintenance flow does not prove the original incident
            # was fixed. Keep it open until a separate verified observation says so.
            incident_status="in_progress",
        )
    except Exception as exc:
        append_failure_event(
            data_root,
            phase="failed",
            operation="workflow.terminal.persist",
            correlation_id=invocation_id or "workflow-terminal-unknown",
            parent_operation=invocation_id,
            job_id=job_id,
            incident_id=str(identity.get("incident_id") or ""),
            source_launch_id=str(identity.get("source_launch_id") or ""),
            source_run_id=identity.get("source_run_id"),
            reason_code="workflow_terminal_persist_failed",
            error=exc,
            capture_location="workflow_reconciliation.reconcile_workflow_terminal",
            artifact_refs=(artifact_ref,) if artifact_ref else (),
            primary_sink="maintenance_job",
        )
        terminal["workflow_completion"] = "unknown"
        terminal["job_terminal_status"] = "unknown"
        terminal["reason_code"] = "workflow_terminal_persist_failed"
    else:
        terminal["workflow_completion"] = desired_completion
        terminal["job_terminal_status"] = desired_status
        terminal["reason_code"] = desired_reason
        terminal["next_actions"] = (
            []
            if desired_completion == "complete"
            else ["同じjobの耐久記録から未完了工程と最終報告を再開する。"]
        )
    final_artifact_ref = _save_terminal_artifact(data_root, terminal)
    if final_artifact_ref is None and terminal["workflow_completion"] == "complete":
        terminal["workflow_completion"] = "incomplete"
        terminal["job_terminal_status"] = "failed"
        terminal["reason_code"] = "workflow_terminal_artifact_save_failed"
        terminal["next_actions"] = ["終端成果を保存してから同じjobの登録を再開する。"]
        try:
            store.finalize_job(
                job_id=job_id,
                final_status="failed",
                lease=lease,
                finalized_at=datetime.now(timezone.utc),
                reason_code="workflow_terminal_artifact_save_failed",
                incident_status="in_progress",
            )
        except Exception as exc:
            append_failure_event(
                data_root,
                phase="failed",
                operation="workflow.terminal.artifact_failure_finalize",
                correlation_id=invocation_id or "workflow-terminal-unknown",
                parent_operation=invocation_id,
                job_id=job_id,
                incident_id=str(identity.get("incident_id") or ""),
                source_launch_id=str(identity.get("source_launch_id") or ""),
                source_run_id=identity.get("source_run_id"),
                reason_code="workflow_terminal_failure_finalize_failed",
                error=exc,
                capture_location="workflow_reconciliation.reconcile_workflow_terminal",
                primary_sink="maintenance_job",
            )
        final_artifact_ref = _save_terminal_artifact(data_root, terminal)
    terminal["artifact_ref"] = final_artifact_ref or artifact_ref
    return terminal


def _save_terminal_artifact(data_root: Path, document: dict[str, Any]) -> str | None:
    job_id = document["identity"].get("job_id")
    invocation_id = document.get("invocation_id")
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        return None
    folder = data_root / "workflow-terminal" / job_id
    filename = (
        invocation_id if isinstance(invocation_id, str) and _ID.fullmatch(invocation_id)
        else "invocation-unknown"
    )
    final_path = folder / f"{filename}.json"
    temporary: str | None = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=folder, prefix=".terminal-", suffix=".tmp", delete=False
        ) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, final_path)
        temporary = None
        return final_path.relative_to(data_root).as_posix()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        append_failure_event(
            data_root,
            phase="fallback",
            operation="workflow.terminal.artifact_save",
            correlation_id=str(document.get("invocation_id") or "workflow-terminal-unknown"),
            parent_operation=document.get("invocation_id"),
            job_id=job_id,
            incident_id=str(document["identity"].get("incident_id") or ""),
            source_launch_id=str(document["identity"].get("source_launch_id") or ""),
            source_run_id=document["identity"].get("source_run_id"),
            reason_code="workflow_terminal_artifact_save_failed",
            error=exc,
            capture_location="workflow_reconciliation._save_terminal_artifact",
            primary_sink="workflow_terminal_file",
        )
        return None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
