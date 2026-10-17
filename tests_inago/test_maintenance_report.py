"""U11 C55 covers current-day classification; report presentation is U16."""

from __future__ import annotations

from automation.inago.maintenance.detector import preclassify_daily_outcome
from tests_inago.maintenance_detector_support import (
    CHECKED_AT,
    LAUNCH_ID,
    TARGET_DATE,
    clone_case,
)


def test_c55_contract():
    completion, daily_result, manifest = clone_case(status="failed", run_id=None)
    daily_result["phase_results"]["trace"].update(
        {"status": "not_written", "event_count": 0, "reference": None}
    )
    manifest["correlation"].pop("source_run_id", None)

    # This U11 case covers the current-run detector only. U16 owns the report
    # view that compares today's result with prior dashboard totals.
    result = preclassify_daily_outcome(
        expected_target_date=TARGET_DATE,
        expected_source_launch_id=LAUNCH_ID,
        completion=completion,
        daily_result=daily_result,
        evidence_manifest=manifest,
        checked_at=CHECKED_AT,
    )

    assert result["preclassification"] == "needs_investigation"
    assert result["check_status"] == "abnormal"
    assert result["source_run_id"] is None
    assert result["counts"]["committed_count"] == 0
    assert result["counts"]["error_count"] == 1
    assert result["phase_results"]["export"] == "ready"
    assert "previous_dashboard_count" not in result
    assert result["routing"]["steps_started"] == []
