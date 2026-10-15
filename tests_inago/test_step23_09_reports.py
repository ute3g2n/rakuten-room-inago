from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from automation.inago.dashboard_history import export_dashboard_history_html
from automation.inago.report_apply import apply_pending_reports
from tests_inago.test_step05 import (
    DatabaseFixture,
    FakeClock,
    JST,
    _json_request,
    _report,
    _request,
    _running_server,
    seeded_database,
)


def _create_next_day_run(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('R2', '2026-10-13', 'daily', 'running', ?, 'seed-r2',
                '2026-10-13T13:00:00+09:00', '2026-10-13T17:45:00+09:00',
                '2026-10-13T18:00:00+09:00', 0, 0, NULL, 'not_started', NULL)
        """,
        ("confirmed-0.3-step46-all132",),
    )


def test_authenticated_report_applies_next_day_and_updates_history_html(
    seeded_database: DatabaseFixture,
    tmp_path: Path,
) -> None:
    token = "isolated-human-api-token-" + ("x" * 40)
    clock = FakeClock(datetime(2026, 10, 12, 21, 30, tzinfo=JST))

    with _running_server(
        seeded_database.connect, clock, auth_token=token
    ) as server:
        assert server.server_address[0] == "127.0.0.1"
        health_status, health, _ = _json_request(server, "GET", path="/health")
        assert (health_status, health) == (200, {"status": "ready"})

        page_status, _, page_body = _request(server, "GET", path="/")
        assert page_status == 200
        assert token.encode("utf-8") not in page_body

        unauthorized_status, unauthorized, _ = _json_request(
            server, "GET", path="/api/state"
        )
        assert unauthorized_status == 401
        assert unauthorized == {"error": "authentication_required"}

        headers = {"Authorization": f"Bearer {token}"}
        state_status, state, _ = _json_request(
            server, "GET", headers=headers, path="/api/state"
        )
        assert state_status == 200
        assert state is not None
        assert token not in json.dumps(state)
        item = next(
            value
            for value in state["items"]
            if value["offer_key"] == "shop_test:item_A01"
        )
        payload = _report(
            number=1,
            action_id="ACT-23-09-POSTED",
            actual_posted_at="2026-10-12T21:10:00+09:00",
        )
        payload.update(
            {
                "draft_id": item["draft_id"],
                "draft_version": item["draft_version"],
                "expected_receipt_revision": state["receipt_revisions"][
                    item["offer_key"]
                ],
            }
        )
        post_status, receipt, _ = _json_request(
            server,
            "POST",
            payload=payload,
            csrf_token=state["csrf_token"],
            headers=headers,
        )
        assert post_status == 201
        assert receipt == {
            "action_id": "ACT-23-09-POSTED",
            "receipt_revision": 1,
            "apply_status": "pending",
            "saved": True,
        }

        pending_status, pending_state, _ = _json_request(
            server, "GET", headers=headers, path="/api/state"
        )
        assert pending_status == 200
        assert pending_state is not None
        assert pending_state["summary"]["generated_count_total"] == 15
        assert pending_state["summary"]["reported_posted_count"] == 0
        assert pending_state["summary"]["pending_report_count"] == 1
        assert pending_state["pending_reports"][0]["apply_status"] == "pending"

        connection = seeded_database.connect()
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM publication_event"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0] == 15
            assert connection.execute(
                "SELECT committed_count FROM run WHERE run_id='R1'"
            ).fetchone()[0] == 15

            _create_next_day_run(connection)
            apply_clock = FakeClock(datetime(2026, 10, 13, 13, 0, tzinfo=JST))
            applied = apply_pending_reports(connection, run_id="R2", clock=apply_clock)
            assert applied.applied_action_ids == ("ACT-23-09-POSTED",)
            assert applied.pending_action_ids == ()
            assert applied.exploration_allowed is True

            html_path = tmp_path / "history.html"
            export = export_dashboard_history_html(
                connection,
                output_path=html_path,
                runtime_root=tmp_path,
            )
            assert export.run_count == 2
            assert export.draft_count == 15
            assert connection.execute(
                "SELECT COUNT(*) FROM publication_event WHERE valid=1"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0] == 14
            assert connection.execute(
                "SELECT committed_count FROM run WHERE run_id='R1'"
            ).fetchone()[0] == 15
        finally:
            connection.close()

        health_after_apply, ready_after_apply, _ = _json_request(
            server, "GET", path="/health"
        )
        assert (health_after_apply, ready_after_apply) == (
            200,
            {"status": "ready"},
        )

    html = html_path.read_text(encoding="utf-8")
    embedded = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert embedded is not None
    history = json.loads(embedded.group(1))
    previous_day = next(
        value for value in history["days"] if value["target_date"] == "2026-10-12"
    )
    posted_item = next(
        value
        for value in previous_day["items"]
        if value["draft_id"] == "D-A01"
    )
    assert posted_item["status"] == "投稿反映済み"
    assert 'copy.className = "copy-button"' in html
    assert '<table id="report-action-counts"' in html
    assert 'data-report-action="posted"' in html

    task_path = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "inago"
        / "task-xml"
        / "RakutenRoomInagoHumanApi.xml"
    )
    task_xml = task_path.read_text(encoding="utf-8")
    assert "<LogonTrigger>" in task_xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in task_xml
    assert "automation.inago.cli serve" in task_xml
