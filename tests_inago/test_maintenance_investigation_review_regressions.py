"""Store-level regressions for the U12 investigation handoff gate."""

import ctypes
import json
import os
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago.maintenance.controller import (
    InvestigationAttemptRequest,
    MaintenanceController,
)
from automation.inago.maintenance.store import (
    LeaseOwnershipError,
    MaintenanceStoreError,
)
from tests_inago.maintenance_support import (
    BASE_TIME,
    create_incident,
    create_ready_release,
    make_store,
)
from tests_inago.maintenance_investigation_support import (
    make_context,
    make_investigation_response,
    make_review_response,
)
from automation.inago.maintenance.investigation import InvestigationContractError
from automation.inago.maintenance import safe_file


def _start_attempt(tmp_path):
    store = make_store(tmp_path / "store")
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=5101,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=6),
        ttl_seconds=300,
    )
    attempt = store.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=7),
    )
    return store, job.job_id, attempt.attempt_id, lease


def test_u12_investigation_path_cannot_bypass_handoff_gate(tmp_path):
    store, job_id, attempt_id, lease = _start_attempt(tmp_path)
    arbitrary_output = f"{job_id}/{attempt_id}/other.json"

    with pytest.raises(
        MaintenanceStoreError, match="investigation_output_path_required"
    ):
        store.complete_step_attempt_with_review(
            attempt_id=attempt_id,
            lease=lease,
            result_version="investigation-v1",
            completed_at=BASE_TIME + timedelta(seconds=10),
            output_path=arbitrary_output,
            output_sha256="a" * 64,
            reviewer_id="independent-reviewer",
            reviewed_version="investigation-v1",
            review_status="approved",
            reviewed_at=BASE_TIME + timedelta(seconds=11),
        )

    with pytest.raises(
        MaintenanceStoreError, match="investigation_output_path_required"
    ):
        store.complete_step_attempt(
            attempt_id=attempt_id,
            lease=lease,
            result_version="investigation-v1",
            output_path=arbitrary_output,
            output_sha256="a" * 64,
            completed_at=BASE_TIME + timedelta(seconds=12),
        )

    store.complete_step_attempt(
        attempt_id=attempt_id,
        lease=lease,
        result_version="investigation-v1",
        output_path=f"{job_id}/{attempt_id}/result.json",
        output_sha256="a" * 64,
        completed_at=BASE_TIME + timedelta(seconds=12),
    )
    store.record_step_review(
        attempt_id=attempt_id,
        reviewer_id="independent-reviewer",
        reviewed_version="investigation-v1",
        status="approved",
        reviewed_at=BASE_TIME + timedelta(seconds=13),
    )
    with pytest.raises(
        MaintenanceStoreError, match="step_predecessor_handoff_not_accepted"
    ):
        store.begin_step_attempt(
            job_id=job_id,
            step_key="change_planning",
            sequence=2,
            lease=lease,
            started_at=BASE_TIME + timedelta(seconds=14),
        )


def _start_controller_attempt(tmp_path):
    store = make_store(tmp_path / "store")
    controller = MaintenanceController(store)
    incident_id = create_incident(store)
    job = create_ready_release(store, incident_id=incident_id)
    lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-a",
        owner_pid=5201,
        owner_start_time=BASE_TIME.isoformat(),
        now=BASE_TIME + timedelta(seconds=6),
        ttl_seconds=300,
    )
    controller.bind_job_input_manifest(
        incident_id=incident_id,
        source_launch_id="launch-1",
        source_run_id=None,
        target_date="2026-10-17",
        job_id=job.job_id,
        created_at=BASE_TIME + timedelta(seconds=6),
    )
    attempt = controller.begin_step_attempt(
        job_id=job.job_id,
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=7),
    )
    context = make_context(
        incident_id=incident_id,
        source_launch_id="launch-1",
        source_run_id=None,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
    )
    attempts_root = tmp_path / "attempts"
    output_root = attempts_root / job.job_id / attempt.attempt_id
    output_root.mkdir(parents=True)
    return store, controller, lease, context, attempts_root, output_root


def _complete_attempt(
    controller,
    *,
    context,
    attempts_root,
    output_root,
    lease,
    version,
    review,
    review_round,
    reviewer_calls,
    completed_at=10,
    accepted_at=12,
):
    return controller.complete_investigation_attempt(
        InvestigationAttemptRequest(
            context=context,
            response=make_investigation_response(context),
            attempts_root=attempts_root,
            output_root=output_root,
            result_version=version,
            created_at=BASE_TIME + timedelta(seconds=8),
            completed_at=BASE_TIME + timedelta(seconds=completed_at),
            review_started_at=BASE_TIME + timedelta(seconds=9),
            reviewed_at=BASE_TIME + timedelta(seconds=11),
            accepted_at=BASE_TIME + timedelta(seconds=accepted_at),
            lease=lease,
            author_id="implementation-agent",
            author_execution_id=f"investigator-{version}",
            reviewer_id="independent-reviewer",
            reviewer_execution_id="review-turn-001",
            review_runner=lambda metadata: (reviewer_calls.append(metadata) or review),
            review_round=review_round,
            handoff_root=attempts_root,
        )
    )


def test_u12_review_round_must_match_store_history(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)
    first_review = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="changes_requested",
        findings=["Revise the synthetic report."],
    )
    _complete_attempt(
        controller,
        context=context,
        attempts_root=attempts_root,
        output_root=output_root,
        lease=lease,
        version="investigation-v1",
        review=first_review,
        review_round=1,
        reviewer_calls=[],
    )

    retry = controller.begin_step_attempt(
        job_id=context["job_id"],
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=13),
    )
    retry_context = make_context(
        incident_id=context["incident_id"],
        source_launch_id=context["source_launch_id"],
        source_run_id=context["source_run_id"],
        job_id=context["job_id"],
        attempt_id=retry.attempt_id,
    )
    retry_root = attempts_root / context["job_id"] / retry.attempt_id
    retry_root.mkdir(parents=True)
    approved = make_review_response(
        retry_context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v2",
        status="approved",
    )
    reviewer_calls = []
    with pytest.raises(MaintenanceStoreError, match="review_round_mismatch"):
        _complete_attempt(
            controller,
            context=retry_context,
            attempts_root=attempts_root,
            output_root=retry_root,
            lease=lease,
            version="investigation-v2",
            review=approved,
            review_round=1,
            reviewer_calls=reviewer_calls,
        )
    assert reviewer_calls == []
    assert list(retry_root.iterdir()) == []


def test_u12_lease_expiry_after_review_prevents_result_write(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)
    approved = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="approved",
    )
    reviewer_calls = []
    with pytest.raises(LeaseOwnershipError, match="lease_token_invalid_or_expired"):
        _complete_attempt(
            controller,
            context=context,
            attempts_root=attempts_root,
            output_root=output_root,
            lease=lease,
            version="investigation-v1",
            review=approved,
            review_round=1,
            reviewer_calls=reviewer_calls,
            completed_at=400,
        )
    assert len(reviewer_calls) == 1
    assert not (output_root / "result.json").exists()
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "running"


def test_u12_reviewer_execution_id_must_be_fresh_for_each_round(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)
    changes_requested = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="changes_requested",
        findings=["Revise the synthetic report."],
    )
    _complete_attempt(
        controller,
        context=context,
        attempts_root=attempts_root,
        output_root=output_root,
        lease=lease,
        version="investigation-v1",
        review=changes_requested,
        review_round=1,
        reviewer_calls=[],
    )
    retry = controller.begin_step_attempt(
        job_id=context["job_id"],
        step_key="maintenance_investigation",
        sequence=1,
        lease=lease,
        started_at=BASE_TIME + timedelta(seconds=13),
    )
    retry_context = make_context(
        incident_id=context["incident_id"],
        source_launch_id=context["source_launch_id"],
        source_run_id=context["source_run_id"],
        job_id=context["job_id"],
        attempt_id=retry.attempt_id,
    )
    retry_root = attempts_root / context["job_id"] / retry.attempt_id
    retry_root.mkdir(parents=True)
    approved = make_review_response(
        retry_context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v2",
        status="approved",
    )
    reviewer_calls = []
    with pytest.raises(MaintenanceStoreError, match="review_execution_id_reused"):
        _complete_attempt(
            controller,
            context=retry_context,
            attempts_root=attempts_root,
            output_root=retry_root,
            lease=lease,
            version="investigation-v2",
            review=approved,
            review_round=2,
            reviewer_calls=reviewer_calls,
        )
    assert reviewer_calls == []
    assert list(retry_root.iterdir()) == []


def test_u12_unapproved_attempt_cannot_mutate_during_handoff_resume(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)
    changes_requested = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="changes_requested",
        findings=["Revise the synthetic report."],
    )
    _complete_attempt(
        controller,
        context=context,
        attempts_root=attempts_root,
        output_root=output_root,
        lease=lease,
        version="investigation-v1",
        review=changes_requested,
        review_round=1,
        reviewer_calls=[],
    )

    with pytest.raises(
        InvestigationContractError, match="investigation_handoff_resume_invalid"
    ):
        controller.resume_investigation_handoff(
            attempt_id=context["attempt_id"],
            attempts_root=attempts_root,
            handoff_root=attempts_root,
            lease=lease,
            accepted_at=BASE_TIME + timedelta(seconds=13),
        )

    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM step_attempt WHERE job_id = ?",
                (context["job_id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM step_attempt WHERE job_id = ? AND status = 'running'",
                (context["job_id"],),
            ).fetchone()[0]
            == 0
        )


def test_u12_failed_artifact_write_removes_partial_file(tmp_path, monkeypatch):
    from automation.inago.maintenance import investigation

    (
        store,
        _controller,
        _lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)

    def fail_fsync(_descriptor):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(investigation.os, "fsync", fail_fsync)
    with pytest.raises(
        InvestigationContractError, match="investigation_artifact_write_failed"
    ):
        investigation.save_attempt_artifact(
            output_root,
            attempts_root,
            context,
            filename="facts.json",
            content=b"partial synthetic artifact",
        )

    assert not (output_root / "facts.json").exists()


def test_u12_partial_artifact_cleanup_failure_is_recorded(tmp_path, monkeypatch):
    from automation.inago.maintenance import investigation

    (
        _store,
        _controller,
        _lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)

    def fail_fsync(_descriptor):
        raise OSError("synthetic fsync failure")

    def fail_unlink(_root, _relative_path):
        raise OSError("synthetic unlink failure")

    monkeypatch.setattr(investigation.os, "fsync", fail_fsync)
    monkeypatch.setattr(investigation, "unlink_file_beneath", fail_unlink)

    with pytest.raises(
        InvestigationContractError, match="investigation_artifact_write_failed"
    ) as raised:
        investigation.save_attempt_artifact(
            output_root,
            attempts_root,
            context,
            filename="facts.json",
            content=b"synthetic artifact",
        )

    assert isinstance(raised.value.__cause__, OSError)
    assert "synthetic fsync failure" in str(raised.value.__cause__)
    assert any(
        "synthetic unlink failure" in note for note in raised.value.__cause__.__notes__
    )
    assert (output_root / "facts.json").exists()


def test_u12_contract_error_records_partial_artifact_cleanup_failure(
    tmp_path, monkeypatch
):
    from automation.inago.maintenance import investigation

    (
        _store,
        _controller,
        _lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)

    def fail_fsync(_descriptor):
        raise InvestigationContractError("synthetic artifact contract failure")

    def fail_unlink(_root, _relative_path):
        raise OSError("synthetic unlink failure")

    monkeypatch.setattr(investigation.os, "fsync", fail_fsync)
    monkeypatch.setattr(investigation, "unlink_file_beneath", fail_unlink)

    with pytest.raises(
        InvestigationContractError, match="synthetic artifact contract failure"
    ) as raised:
        investigation.save_attempt_artifact(
            output_root,
            attempts_root,
            context,
            filename="facts.json",
            content=b"synthetic artifact",
        )

    assert any("synthetic unlink failure" in note for note in raised.value.__notes__)
    assert (output_root / "facts.json").exists()


def test_u12_completed_approved_handoff_can_be_retried(tmp_path):
    (
        store,
        controller,
        lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)
    approved = make_review_response(
        context,
        reviewer_id="independent-reviewer",
        review_execution_id="review-turn-001",
        reviewed_version="investigation-v1",
        status="approved",
    )
    with pytest.raises(LeaseOwnershipError, match="lease_token_invalid_or_expired"):
        _complete_attempt(
            controller,
            context=context,
            attempts_root=attempts_root,
            output_root=output_root,
            lease=lease,
            version="investigation-v1",
            review=approved,
            review_round=1,
            reviewer_calls=[],
            accepted_at=400,
        )
    assert store.get_step_attempt(attempt_id=context["attempt_id"]).status == "complete"
    retry_lease = store.acquire_lease(
        lease_key="maintenance-worker",
        owner_id="worker-b",
        owner_pid=5202,
        owner_start_time=BASE_TIME + timedelta(seconds=400),
        now=BASE_TIME + timedelta(seconds=400),
        ttl_seconds=300,
        prior_owner_observation="dead",
    )
    resumed = controller.resume_investigation_handoff(
        attempt_id=context["attempt_id"],
        attempts_root=attempts_root,
        handoff_root=attempts_root,
        lease=retry_lease,
        accepted_at=BASE_TIME + timedelta(seconds=401),
    )
    assert resumed.accepted is True
    assert (
        store.get_step_attempt(attempt_id=context["attempt_id"]).status
        == "handoff_accepted"
    )
    retried = controller.resume_investigation_handoff(
        attempt_id=context["attempt_id"],
        attempts_root=attempts_root,
        handoff_root=attempts_root,
        lease=retry_lease,
        accepted_at=BASE_TIME + timedelta(seconds=402),
    )
    assert retried == resumed
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM handoff_artifact WHERE attempt_id = ?",
                (context["attempt_id"],),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_create_directory_handle_closes_when_attribute_check_fails(monkeypatch):
    handle = 101
    closed = []
    monkeypatch.setattr(safe_file, "_CreateFileW", lambda *_args: handle)
    monkeypatch.setattr(
        safe_file, "_CloseHandle", lambda value: closed.append(value) or True
    )

    def fail_attributes(_handle):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(safe_file, "_attributes", fail_attributes)

    with pytest.raises(OSError, match="synthetic metadata failure"):
        safe_file._create_directory_handle("C:\\")

    assert closed == [handle]


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_relative_handle_closes_when_attribute_check_fails(monkeypatch):
    handle = 102
    closed = []

    def create_relative(handle_pointer, *_args):
        ctypes.cast(
            handle_pointer, ctypes.POINTER(safe_file.wintypes.HANDLE)
        ).contents.value = handle
        return 0

    monkeypatch.setattr(safe_file, "_NtCreateFile", create_relative)
    monkeypatch.setattr(
        safe_file, "_CloseHandle", lambda value: closed.append(value) or True
    )

    def fail_attributes(_handle):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(safe_file, "_attributes", fail_attributes)

    with pytest.raises(OSError, match="synthetic metadata failure"):
        safe_file._open_relative_handle(77, "child", mode="rb")

    assert closed == [handle]


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_created_relative_handle_is_deleted_when_attribute_check_fails(
    monkeypatch,
):
    handle = 104
    closed = []
    delete_requests = []
    access_masks = []
    fallback_cleanup_calls = []

    def create_relative(handle_pointer, desired_access, *_args):
        access_masks.append(desired_access)
        ctypes.cast(
            handle_pointer, ctypes.POINTER(safe_file.wintypes.HANDLE)
        ).contents.value = handle
        return 0

    monkeypatch.setattr(safe_file, "_NtCreateFile", create_relative)
    monkeypatch.setattr(
        safe_file,
        "_NtSetInformationFile",
        lambda file_handle, *_args: delete_requests.append(file_handle) or 0,
    )
    monkeypatch.setattr(
        safe_file, "_CloseHandle", lambda value: closed.append(value) or True
    )

    def fail_attributes(_handle):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(safe_file, "_attributes", fail_attributes)

    with pytest.raises(OSError, match="synthetic metadata failure"):
        safe_file._open_relative_handle(
            77,
            "new.json",
            mode="xb",
            created_file_cleanup=lambda: fallback_cleanup_calls.append(True),
        )

    assert access_masks[0] & safe_file._DELETE
    assert delete_requests == [handle]
    assert closed == [handle]
    assert fallback_cleanup_calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_failed_delete_disposition_uses_path_cleanup(monkeypatch):
    handle = 105
    closed = []
    cleanup_calls = []

    def create_relative(handle_pointer, *_args):
        ctypes.cast(
            handle_pointer, ctypes.POINTER(safe_file.wintypes.HANDLE)
        ).contents.value = handle
        return 0

    monkeypatch.setattr(safe_file, "_NtCreateFile", create_relative)
    monkeypatch.setattr(safe_file, "_NtSetInformationFile", lambda *_args: -1)
    monkeypatch.setattr(
        safe_file, "_CloseHandle", lambda value: closed.append(value) or True
    )

    def fail_attributes(_handle):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(safe_file, "_attributes", fail_attributes)

    with pytest.raises(OSError, match="synthetic metadata failure"):
        safe_file._open_relative_handle(
            77,
            "new.json",
            mode="xb",
            created_file_cleanup=lambda: cleanup_calls.append(True),
        )

    assert closed == [handle]
    assert cleanup_calls == [True]


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_closehandle_false_uses_path_cleanup(monkeypatch):
    handle = 107
    cleanup_calls = []

    def create_relative(handle_pointer, *_args):
        ctypes.cast(
            handle_pointer, ctypes.POINTER(safe_file.wintypes.HANDLE)
        ).contents.value = handle
        return 0

    monkeypatch.setattr(safe_file, "_NtCreateFile", create_relative)
    monkeypatch.setattr(safe_file, "_NtSetInformationFile", lambda *_args: 0)
    monkeypatch.setattr(safe_file, "_CloseHandle", lambda _value: False)
    monkeypatch.setattr(
        safe_file, "_win_error", lambda operation: OSError(f"{operation} failed")
    )

    def fail_attributes(_handle):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(safe_file, "_attributes", fail_attributes)

    with pytest.raises(OSError, match="synthetic metadata failure"):
        safe_file._open_relative_handle(
            77,
            "new.json",
            mode="xb",
            created_file_cleanup=lambda: cleanup_calls.append(True),
        )

    assert cleanup_calls == [True]


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_failed_handle_cleanup_attempts_remaining_closes_and_preserves_error():
    attempted = []
    primary_error = RuntimeError("synthetic primary failure")

    def fail_first_close():
        raise OSError("synthetic CloseHandle failure")

    def close_remaining_handle():
        attempted.append("remaining")

    safe_file._run_cleanups([fail_first_close, close_remaining_handle], primary_error)

    assert attempted == ["remaining"]
    assert "synthetic primary failure" in str(primary_error)
    assert any(
        "synthetic CloseHandle failure" in note for note in primary_error.__notes__
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_fstat_failure_closes_descriptor_and_removes_new_artifact(
    tmp_path, monkeypatch
):
    from automation.inago.maintenance import investigation

    (
        _store,
        _controller,
        _lease,
        context,
        attempts_root,
        output_root,
    ) = _start_controller_attempt(tmp_path)
    descriptor = 103
    closed_descriptors = []
    closed_handles = []

    def fail_fstat(_descriptor):
        raise OSError("synthetic fstat failure")

    monkeypatch.setattr(safe_file, "_create_directory_handle", lambda _path: 77)

    def open_relative(_parent, name, *, mode, **_kwargs):
        if mode == "xb":
            assert name == "facts.json"
            (output_root / name).write_bytes(b"")
            return 78
        return 77

    monkeypatch.setattr(safe_file, "_open_relative_handle", open_relative)
    monkeypatch.setattr(safe_file, "_verify_handle_path", lambda *_args: None)
    monkeypatch.setattr(
        safe_file,
        "_CloseHandle",
        lambda handle: closed_handles.append(handle),
    )
    monkeypatch.setattr(
        safe_file.msvcrt,
        "open_osfhandle",
        lambda handle, _flags: descriptor
        if handle == 78
        else pytest.fail("wrong handle"),
    )
    monkeypatch.setattr(
        safe_file,
        "_unlink_windows_beneath",
        lambda root, parts: Path(root).joinpath(*parts).unlink(),
    )
    monkeypatch.setattr(
        safe_file,
        "os",
        SimpleNamespace(
            name="nt",
            path=os.path,
            O_BINARY=0x8000,
            O_RDONLY=0,
            O_WRONLY=1,
            fstat=fail_fstat,
            close=closed_descriptors.append,
            fdopen=lambda *_args: pytest.fail("fdopen must not follow failed fstat"),
        ),
    )

    with pytest.raises(
        InvestigationContractError, match="investigation_artifact_write_failed"
    ):
        investigation.save_attempt_artifact(
            output_root,
            attempts_root,
            context,
            filename="facts.json",
            content=b"synthetic artifact",
        )

    assert closed_descriptors == [descriptor]
    assert 77 in closed_handles
    assert not (output_root / "facts.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native handle cleanup")
def test_c30_stream_entry_failure_closes_and_removes_new_artifact(monkeypatch):
    descriptor = 106
    closed_descriptors = []
    cleanup_calls = []
    stream_close_calls = []
    cleanup_order = []

    class FailingStream:
        def __enter__(self):
            try:
                self.close()
            except OSError:
                pass
            raise OSError("synthetic stream entry failure")

        def __exit__(self, *_args):
            return False

        def close(self):
            stream_close_calls.append(True)
            cleanup_order.append("stream_close")
            if len(stream_close_calls) == 1:
                raise OSError("synthetic initial stream close failure")

    monkeypatch.setattr(safe_file, "_create_directory_handle", lambda _path: 77)
    monkeypatch.setattr(
        safe_file,
        "_open_relative_handle",
        lambda *_args, **_kwargs: 78,
    )
    monkeypatch.setattr(safe_file, "_verify_handle_path", lambda *_args: None)
    monkeypatch.setattr(safe_file, "_CloseHandle", lambda *_args: True)
    monkeypatch.setattr(
        safe_file.msvcrt,
        "open_osfhandle",
        lambda handle, _flags: descriptor
        if handle == 78
        else pytest.fail("wrong handle"),
    )
    monkeypatch.setattr(
        safe_file,
        "_unlink_windows_beneath",
        lambda root, parts: (
            cleanup_order.append("unlink"),
            cleanup_calls.append((root, parts)),
        ),
    )
    monkeypatch.setattr(
        safe_file,
        "os",
        SimpleNamespace(
            name="nt",
            path=os.path,
            O_BINARY=0x8000,
            O_RDONLY=0,
            O_WRONLY=1,
            fstat=lambda _descriptor: SimpleNamespace(st_mode=stat.S_IFREG | 0o600),
            close=closed_descriptors.append,
            fdopen=lambda *_args: FailingStream(),
        ),
    )

    with pytest.raises(OSError, match="synthetic stream entry failure"):
        with safe_file._open_windows_beneath("C:\\", ("result.json",), "xb"):
            pytest.fail("open should fail before yielding")

    assert stream_close_calls == [True, True]
    assert cleanup_order == ["stream_close", "stream_close", "unlink"]
    assert cleanup_calls == [("C:\\", ("result.json",))]
    assert closed_descriptors == []


def test_c30_posix_fstat_failure_closes_descriptor_and_removes_new_artifact(
    monkeypatch,
):
    closed_descriptors = []
    unlink_calls = []
    opened_paths = []

    def open_path(path, _flags, *_args, dir_fd=None):
        opened_paths.append((path, dir_fd))
        return len(opened_paths) + 10

    def fail_fstat(_descriptor):
        raise OSError("synthetic fstat failure")

    fake_os = SimpleNamespace(
        name="posix",
        path=SimpleNamespace(abspath=lambda path: str(path)),
        O_RDONLY=0,
        O_WRONLY=1,
        O_CREAT=0x40,
        O_EXCL=0x80,
        O_DIRECTORY=0x10000,
        O_NOFOLLOW=0x20000,
        O_NONBLOCK=0x800,
        open=open_path,
        close=closed_descriptors.append,
        fstat=fail_fstat,
        fdopen=lambda *_args: pytest.fail("fdopen must not follow failed fstat"),
        unlink=lambda path, *, dir_fd: unlink_calls.append((path, dir_fd)),
    )
    monkeypatch.setattr(safe_file, "Path", safe_file.PurePosixPath)
    monkeypatch.setattr(safe_file, "os", fake_os)

    with pytest.raises(OSError, match="synthetic fstat failure"):
        with safe_file._open_posix_beneath(
            safe_file.PurePosixPath("/virtual/root"), ("artifact.json",), "xb"
        ):
            pytest.fail("open should fail before yielding")

    assert opened_paths == [
        ("/", None),
        ("virtual", 11),
        ("root", 12),
        ("artifact.json", 13),
    ]
    assert 14 in closed_descriptors
    assert unlink_calls == [("artifact.json", 13)]
