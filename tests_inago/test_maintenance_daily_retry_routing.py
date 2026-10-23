import json
from datetime import datetime

import pytest

from automation.inago.maintenance.daily_launcher import (
    DailyProcessResult,
    JST,
    _completion_is_consistent,
    _extract_safe_failure_diagnostic,
    _summarize_result,
)
from automation.inago.maintenance.store import MaintenanceStore


@pytest.mark.parametrize(
    ("retry_summary", "expected_reason"),
    [
        (
            {
                "attempt_count": 1,
                "recovered_count": 0,
                "unresolved_count": 1,
                "contract_violation_count": 0,
            },
            "daily_retry_unresolved",
        ),
        (
            {
                "attempt_count": 1,
                "recovered_count": 1,
                "unresolved_count": 1,
                "contract_violation_count": 0,
            },
            "daily_retry_contract_violation",
        ),
        (
            {
                "attempt_count": 1,
                "recovered_count": 0,
                "unresolved_count": 0,
                "contract_violation_count": 1,
            },
            "daily_retry_contract_violation",
        ),
        (
            {"attempt_count": 1, "recovered_count": 0},
            "daily_retry_observation_incomplete",
        ),
    ],
)
def test_completed_daily_retry_issue_is_queued_for_review(
    retry_summary, expected_reason
):
    payload = {
        "status": "complete",
        "overall_status": "complete",
        "daily_status": "complete",
        "target_date": "2026-10-18",
        "run_id": "run-1",
        "retry_summary": retry_summary,
    }
    process_result = DailyProcessResult(
        returncode=0,
        stdout=(json.dumps(payload) + "\n").encode("utf-8"),
        stderr=b"",
        pid=1234,
    )

    summary, _stderr, observation = _summarize_result(
        launch_id="launch-1",
        target_date="2026-10-18",
        process_result=process_result,
        completed_at=datetime(2026, 10, 18, tzinfo=JST),
    )

    assert summary["status"] == "complete"
    assert summary["_check_status"] == "incomplete"
    assert summary["reason_code"] == expected_reason
    assert observation is not None
    assert observation["retry_summary"]["unresolved_count"] == retry_summary.get(
        "unresolved_count"
    )
    assert _completion_is_consistent(summary, "incomplete")


def test_completed_daily_with_clean_retry_summary_stays_normal():
    payload = {
        "status": "complete",
        "overall_status": "complete",
        "daily_status": "complete",
        "target_date": "2026-10-18",
        "run_id": "run-1",
        "retry_summary": {
            "attempt_count": 1,
            "recovered_count": 1,
            "unresolved_count": 0,
            "contract_violation_count": 0,
        },
    }
    process_result = DailyProcessResult(
        returncode=0,
        stdout=(json.dumps(payload) + "\n").encode("utf-8"),
        stderr=b"",
        pid=1234,
    )

    summary, _stderr, _observation = _summarize_result(
        launch_id="launch-1",
        target_date="2026-10-18",
        process_result=process_result,
        completed_at=datetime(2026, 10, 18, tzinfo=JST),
    )

    assert summary["_check_status"] == "normal"
    assert summary["reason_code"] is None
    assert _completion_is_consistent(summary, "normal")


def test_retry_issue_is_persisted_as_incomplete_and_queued(tmp_path):
    payload = {
        "status": "complete",
        "overall_status": "complete",
        "daily_status": "complete",
        "target_date": "2026-10-18",
        "run_id": "run-1",
        "retry_summary": {
            "attempt_count": 1,
            "recovered_count": 0,
            "unresolved_count": 1,
            "contract_violation_count": 0,
        },
    }
    process_result = DailyProcessResult(
        returncode=0,
        stdout=(json.dumps(payload) + "\n").encode("utf-8"),
        stderr=b"",
        pid=1234,
    )
    completed_at = datetime(2026, 10, 18, tzinfo=JST)
    summary, _stderr, observation = _summarize_result(
        launch_id="launch-1",
        target_date="2026-10-18",
        process_result=process_result,
        completed_at=completed_at,
    )
    check_status = str(summary.pop("_check_status"))
    summary.pop("completed_at")
    data_root = tmp_path / "data"
    store = MaintenanceStore(
        data_root,
        daily_database_path=data_root / "state.sqlite",
    )
    store.initialize(applied_at=completed_at)
    store.record_daily_launch_start(
        source_launch_id="launch-1",
        target_date="2026-10-18",
        owner_pid=1234,
        owner_start_time=completed_at,
        started_at=completed_at,
    )

    launch = store.record_daily_launch_completion(
        source_launch_id="launch-1",
        target_date="2026-10-18",
        child_pid=1234,
        source_run_id="run-1",
        launch_status="complete",
        exit_code=0,
        completed_at=completed_at,
        check_status=check_status,
        reason_code="daily_retry_unresolved",
        result_summary=summary,
        daily_result_observation=observation,
    )

    check = store.get_daily_check_by_launch("launch-1")
    queue = store.get_worker_queue("launch-1")
    stored_observation = store.get_daily_result_observation("launch-1")
    assert launch.status == "complete"
    assert check is not None and check.check_status == "incomplete"
    assert check.reason_code == "daily_retry_unresolved"
    assert queue is not None and queue.status == "queued"
    assert stored_observation is not None
    assert stored_observation.observation["retry_summary"]["unresolved_count"] == 1


def test_failure_diagnostic_keeps_only_safe_repository_frames():
    payload = {
        "status": "failed",
        "overall_status": "failed",
        "daily_status": "failed",
        "reason": "daily_runtime_error",
        "target_date": "2026-10-23",
        "run_id": "INAGO-2026-10-23",
        "primary_error": {
            "phase": "orchestration",
            "code": "DailyOrchestrationError",
            "diagnostic": {
                "exception_class": "DailyOrchestrationError",
                "exception_chain": [
                    {
                        "relation": "raised",
                        "exception_class": "DailyOrchestrationError",
                    }
                ],
                "exception_chain_truncated": False,
                "exception_frames": [],
                "exception_frames_truncated": False,
                "message": "secret exception message",
            },
            "daily_primary_failure_diagnostic": {
                "exception_class": "RoomContractError",
                "exception_chain": [
                    {"relation": "raised", "exception_class": "RoomContractError"}
                ],
                "exception_chain_truncated": False,
                "exception_frames": [
                    {
                        "exception_class": "RoomContractError",
                        "relation": "raised",
                        "path": "automation/inago/room_adapter.py",
                        "module": "automation.inago.room_adapter",
                        "function": "_reserve_search_attempt",
                        "line": 492,
                    }
                ],
                "exception_frames_truncated": False,
            },
        },
        "secondary_errors": [
            {
                "operation": "daily_orchestration",
                "exception_class": "RoomContractError",
                "exception_chain": [
                    {"relation": "raised", "exception_class": "RoomContractError"},
                    {"relation": ["raised"], "exception_class": "RoomContractError"},
                ],
                "exception_chain_truncated": False,
                "exception_frames": [
                    {
                        "exception_class": "RoomContractError",
                        "relation": "raised",
                        "path": "automation/inago/room_adapter.py",
                        "module": "automation.inago.room_adapter",
                        "function": "_reserve_search_attempt",
                        "line": 492,
                    },
                    {
                        "exception_class": "RoomContractError",
                        "relation": "raised",
                        "path": "C:/Users/private/source.py",
                        "module": "private.module",
                        "function": "private_function",
                        "line": 1,
                    },
                    {
                        "exception_class": "RoomContractError",
                        "relation": "raised",
                        "path": "../../private/source.py",
                        "module": "private.module",
                        "function": "private_function",
                        "line": 1,
                    },
                    {
                        "exception_class": "RoomContractError",
                        "relation": ["raised"],
                        "path": "automation/inago/room_adapter.py",
                        "module": "automation.inago.room_adapter",
                        "function": "_reserve_search_attempt",
                        "line": 492,
                    },
                ],
                "exception_frames_truncated": False,
                "message": "secret secondary message",
            }
        ],
    }
    process_result = DailyProcessResult(
        returncode=4,
        stdout=(json.dumps(payload) + "\n").encode("utf-8"),
        stderr=b"",
        pid=1234,
    )

    _summary, stderr_summary, _observation = _summarize_result(
        launch_id="launch-diagnostic",
        target_date="2026-10-23",
        process_result=process_result,
        completed_at=datetime(2026, 10, 23, tzinfo=JST),
    )

    safe = json.loads(stderr_summary)
    assert safe["primary_error_class"] == "DailyOrchestrationError"
    assert safe["failure_diagnostic"]["primary"][0]["exception_class"] == (
        "DailyOrchestrationError"
    )
    assert safe["failure_diagnostic"]["secondary"][0]["operation"] == (
        "daily_orchestration"
    )
    assert safe["failure_diagnostic"]["primary"][1]["exception_frames"] == [
        {
            "exception_class": "RoomContractError",
            "relation": "raised",
            "path": "automation/inago/room_adapter.py",
            "module": "automation.inago.room_adapter",
            "function": "_reserve_search_attempt",
            "line": 492,
        }
    ]
    assert safe["failure_diagnostic"]["secondary"][0]["exception_frames"] == [
        {
            "exception_class": "RoomContractError",
            "relation": "raised",
            "path": "automation/inago/room_adapter.py",
            "module": "automation.inago.room_adapter",
            "function": "_reserve_search_attempt",
            "line": 492,
        }
    ]
    assert "secret exception message" not in stderr_summary
    assert "secret secondary message" not in stderr_summary
    assert "C:/Users/private" not in stderr_summary
    assert "../../private" not in stderr_summary


def test_failure_diagnostic_marks_launcher_frame_truncation():
    frame = {
        "exception_class": "RuntimeError",
        "relation": "raised",
        "path": "automation/inago/orchestrator.py",
        "module": "automation.inago.orchestrator",
        "function": "run_daily",
        "line": 123,
    }
    diagnostic = _extract_safe_failure_diagnostic(
        {
            "primary_error": {
                "diagnostic": {
                    "exception_class": "RuntimeError",
                    "exception_frames": [frame] * 97,
                    "exception_frames_truncated": False,
                }
            }
        }
    )

    assert diagnostic is not None
    primary = diagnostic["primary"][0]
    assert len(primary["exception_frames"]) == 96
    assert primary["exception_frames_truncated"] is True
