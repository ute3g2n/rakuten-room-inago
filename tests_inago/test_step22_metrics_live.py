"""Local fixture contracts for the STEP22 live-source normalization boundary."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_node(source: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _metrics_port_with_fake_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: object,
):
    from automation.inago.metrics_live_port import NodeMetricsPort

    runtime = tmp_path / "runtime"
    script = runtime / "metrics_live.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture", encoding="utf-8")
    browser = tmp_path / "chrome.exe"
    browser.write_text("fixture", encoding="utf-8")
    port = NodeMetricsPort(
        node_executable="node.exe",
        browser_executable=browser,
        script_path=script,
        output_root=runtime / "artifacts" / "metrics",
        runtime_root=runtime,
        owner_room_url="https://room.rakuten.co.jp/room_fixture/items",
        affiliate_auth_state=tmp_path / "affiliate-auth.json",
        timeout_seconds=30,
    )
    monkeypatch.setattr(
        "automation.inago.metrics_live_port.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    connection = SimpleNamespace(
        execute=lambda *args, **kwargs: SimpleNamespace(
            fetchone=lambda: ("2026-10-15",)
        )
    )
    deadline = (datetime.now().astimezone() + timedelta(minutes=2)).isoformat()
    return port, connection, deadline


def test_live_affiliate_normalization_keeps_null_zero_and_empty_csv_scope() -> None:
    payload = _run_node(
        r"""
        import { normalizeAffiliateMetrics } from './automation/inago/adapters/metrics_live.mjs';
        const csv = 'date,rewards,amount,shop_name,item_name,status,link_type,item_code\n';
        const value = normalizeAffiliateMetrics({
          observedAt: '2026-10-14T04:00:00Z',
          expectedDates: ['2026-10-13', '2026-10-12'],
          dailyRows: [{ date: '2026-10-13', clicks: 0, salesCount: null,
            salesYen: 0, commissionUnconfirmedYen: null }],
          orderMonths: [
            { month: '2026-10', status: 'observed', csv },
            { month: '2026-09', status: 'unavailable', errorCode: 'timeout' },
          ],
        });
        process.stdout.write(JSON.stringify(value));
        """
    )

    assert payload["status"] == "partial"
    assert payload["daily"]["status"] == "partial"
    assert payload["daily"]["rows"][0]["clicks"] == 0
    assert payload["daily"]["rows"][0]["salesCount"] is None
    assert payload["daily"]["rows"][1]["clicks"] is None
    assert payload["orders"] == []
    assert payload["orderWindows"] == [
        {
            "window": "affiliate-orders:2026-10",
            "count": 0,
            "observedAt": "2026-10-14T04:00:00Z",
            "availability": "observed",
        },
        {
            "window": "affiliate-orders:2026-09",
            "count": None,
            "observedAt": "2026-10-14T04:00:00Z",
            "availability": "unavailable",
            "errorCode": "timeout",
        },
    ]


def test_metrics_browser_resolver_uses_the_explicit_configured_executable(
    tmp_path: Path,
) -> None:
    browser = tmp_path / "configured chrome.exe"
    browser.write_text("fixture", encoding="utf-8")
    result = _run_node(
        f"""
        import {{ chromeExecutable }} from './automation/inago/adapters/metrics_live.mjs';
        process.env.INAGO_BROWSER_EXECUTABLE = {json.dumps(str(browser))};
        console.log(JSON.stringify({{ path: chromeExecutable() }}));
        """
    )

    assert result["path"] == str(browser.resolve())


def test_metrics_browser_resolver_fails_closed_for_missing_configured_executable(
    tmp_path: Path,
) -> None:
    missing_browser = tmp_path / "missing chrome.exe"
    result = _run_node(
        f"""
        import {{ chromeExecutable }} from './automation/inago/adapters/metrics_live.mjs';
        process.env.INAGO_BROWSER_EXECUTABLE = {json.dumps(str(missing_browser))};
        try {{
          chromeExecutable();
          console.log(JSON.stringify({{ error: null }}));
        }} catch (error) {{
          console.log(JSON.stringify({{ error: error.message }}));
        }}
        """
    )

    assert result["error"] == "configured_browser_unavailable"


def test_live_room_normalization_uses_exact_30_day_window_and_keeps_null() -> None:
    payload = _run_node(
        r"""
        import { normalizeRoomMetricsFromObservations } from './automation/inago/adapters/metrics_live.mjs';
        const value = normalizeRoomMetricsFromObservations({
          observedAt: '2026-10-14T04:00:00Z',
          roomUrl: 'https://room.rakuten.co.jp/room_fixture',
          observations: [
            { postId: 'POST-IN', postUrl: 'https://room.rakuten.co.jp/room_fixture/POST-IN',
              itemUrl: 'https://item.rakuten.co.jp/shop/item/', likes: 0,
              createdAt: '2026-09-14 13:00:00' },
            { postId: 'POST-OLD', postUrl: 'https://room.rakuten.co.jp/room_fixture/POST-OLD',
              itemUrl: 'https://item.rakuten.co.jp/shop/item-old/', likes: 7,
              createdAt: '2026-09-14 12:59:59' },
            { postId: 'POST-UNKNOWN', postUrl: 'https://room.rakuten.co.jp/room_fixture/POST-UNKNOWN',
              itemUrl: 'https://item.rakuten.co.jp/shop/item-unknown/', likes: null,
              createdAt: '' },
          ],
        });
        process.stdout.write(JSON.stringify(value));
        """
    )

    assert payload["status"] == "observed"
    assert payload["windowStart"] == "2026-09-14T13:00:00+09:00"
    assert [post["postId"] for post in payload["posts"]] == [
        "POST-IN",
        "POST-UNKNOWN",
    ]
    assert payload["posts"][0]["likes"] == 0
    assert payload["posts"][1]["likes"] is None
    assert payload["posts"][1]["createdAt"] is None


def test_room_public_payload_preserves_missing_likes_as_null() -> None:
    payload = _run_node(
        r"""
        import { roomPayloadToMetricsPage } from './automation/inago/adapters/metrics_live.mjs';
        const page = roomPayloadToMetricsPage({
          status: 'success', code: 200,
          data: [{ id: 42, item: { url: 'https://item.rakuten.co.jp/shop/item/' },
            created_at: '2026-10-01 09:30:00', likes: null, is_pinned: 0 }],
          meta: { next_page: null },
        }, {
          roomUrl: 'https://room.rakuten.co.jp/room_fixture/items',
          userId: '12345', observedAt: '2026-10-14T04:00:00Z',
        });
        process.stdout.write(JSON.stringify(page));
        """
    )

    assert payload["entries"][0]["observation"]["postId"] == "42"
    assert payload["entries"][0]["observation"]["likes"] is None
    assert (
        payload["entries"][0]["observation"]["createdAt"] == "2026-10-01T09:30:00+09:00"
    )
    assert payload["entries"][0]["isPinned"] is False


def test_partial_room_normalization_keeps_the_missing_field_reason() -> None:
    payload = _run_node(
        r"""
        import { normalizeRoomMetricsFromObservations } from './automation/inago/adapters/metrics_live.mjs';
        const value = normalizeRoomMetricsFromObservations({
          status: 'partial', errorCode: 'room_fields_unavailable',
          observedAt: '2026-10-14T04:00:00Z',
          roomUrl: 'https://room.rakuten.co.jp/room_fixture',
          observations: [],
        });
        process.stdout.write(JSON.stringify(value));
        """
    )

    assert payload["status"] == "partial"
    assert payload["errorCode"] == "room_fields_unavailable"


def test_room_metrics_requires_explicit_owner_url_instead_of_legacy_account_files() -> (
    None
):
    payload = _run_node(
        r"""
        import { resolveOwnerRoomUrl } from './automation/inago/adapters/metrics_live.mjs';
        let missing;
        try { resolveOwnerRoomUrl(''); } catch (error) { missing = error.message; }
        const selected = resolveOwnerRoomUrl('https://room.rakuten.co.jp/room_fixture/items');
        process.stdout.write(JSON.stringify({ missing, selected }));
        """
    )

    assert payload == {
        "missing": "room_url_unavailable",
        "selected": "https://room.rakuten.co.jp/room_fixture/items",
    }


def test_metrics_collection_date_uses_jst_at_the_utc_day_boundary() -> None:
    from automation.inago.metrics_live_port import metrics_result_from_documents

    for observed_at in (
        "2026-10-14T23:59:00Z",
        "2026-10-15T00:00:00Z",
        "2026-10-15T00:01:00Z",
    ):
        affiliate = {
            "status": "unavailable",
            "observedAt": observed_at,
            "daily": {"rows": []},
            "orders": [],
            "orderWindows": [],
        }
        room = {"status": "unavailable", "observedAt": observed_at, "posts": []}
        result = metrics_result_from_documents(
            run_id="RUN-JST-BOUNDARY",
            target_date="2026-10-15",
            affiliate=affiliate,
            room=room,
        )

        assert (
            result.observed_at
            == datetime.fromisoformat(observed_at.replace("Z", "+00:00")).isoformat()
        )


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_metrics_adapter_rejects_non_finite_values_before_persistence(
    bad_value: float,
) -> None:
    from automation.inago.metrics_live_port import (
        MetricsAdapterError,
        metrics_result_from_documents,
    )

    observed_at = "2026-10-15T13:20:00+09:00"
    affiliate = {
        "status": "observed",
        "observedAt": observed_at,
        "daily": {"rows": [{"date": "2026-10-14", "clicks": bad_value}]},
        "orders": [],
        "orderWindows": [],
    }
    room = {"status": "unavailable", "observedAt": observed_at, "posts": []}

    with pytest.raises(MetricsAdapterError, match="affiliate_metric_value_invalid"):
        metrics_result_from_documents(
            run_id="RUN-NON-FINITE",
            target_date="2026-10-15",
            affiliate=affiliate,
            room=room,
        )


@pytest.mark.parametrize("post_id", [None, "", " \t", False, 0, 1.5])
def test_metrics_adapter_rejects_missing_or_invalid_room_post_ids(
    post_id: object,
) -> None:
    from automation.inago.metrics_live_port import (
        MetricsAdapterError,
        metrics_result_from_documents,
    )

    observed_at = "2026-10-15T13:20:00+09:00"
    affiliate = {
        "status": "observed",
        "observedAt": observed_at,
        "daily": {"rows": []},
        "orders": [],
        "orderWindows": [],
    }
    room = {
        "status": "observed",
        "observedAt": observed_at,
        "posts": [{"postId": post_id}],
    }

    with pytest.raises(MetricsAdapterError, match="room_post_record_invalid"):
        metrics_result_from_documents(
            run_id="RUN-BAD-ROOM-POST-ID",
            target_date="2026-10-15",
            affiliate=affiliate,
            room=room,
        )


def test_metrics_adapter_rejects_room_post_record_without_id() -> None:
    from automation.inago.metrics_live_port import (
        MetricsAdapterError,
        metrics_result_from_documents,
    )

    observed_at = "2026-10-15T13:20:00+09:00"
    affiliate = {
        "status": "observed",
        "observedAt": observed_at,
        "daily": {"rows": []},
        "orders": [],
        "orderWindows": [],
    }
    room = {
        "status": "observed",
        "observedAt": observed_at,
        "posts": [{}],
    }

    with pytest.raises(MetricsAdapterError, match="room_post_record_invalid"):
        metrics_result_from_documents(
            run_id="RUN-MISSING-ROOM-POST-ID",
            target_date="2026-10-15",
            affiliate=affiliate,
            room=room,
        )


def test_metrics_adapter_normalizes_positive_integer_room_post_id() -> None:
    from automation.inago.metrics_live_port import metrics_result_from_documents

    observed_at = "2026-10-15T13:20:00+09:00"
    affiliate = {
        "status": "observed",
        "observedAt": observed_at,
        "daily": {"rows": []},
        "orders": [],
        "orderWindows": [],
    }
    room = {
        "status": "observed",
        "observedAt": observed_at,
        "posts": [{"postId": 42}],
    }

    result = metrics_result_from_documents(
        run_id="RUN-NUMERIC-ROOM-POST-ID",
        target_date="2026-10-15",
        affiliate=affiliate,
        room=room,
    )

    assert result.room_posts[0].post_id == "42"


@pytest.mark.parametrize("bad_status", [[], {}])
def test_metrics_adapter_maps_unstructured_status_values_to_unavailable(
    bad_status: object,
) -> None:
    from automation.inago.metrics_live_port import metrics_result_from_documents

    observed_at = "2026-10-15T13:20:00+09:00"
    affiliate = {
        "status": "observed",
        "observedAt": observed_at,
        "daily": {"rows": []},
        "orders": [],
        "orderWindows": [],
    }
    room = {"status": bad_status, "observedAt": observed_at, "posts": []}

    result = metrics_result_from_documents(
        run_id="RUN-INVALID-STATUS",
        target_date="2026-10-15",
        affiliate=affiliate,
        room=room,
    )

    assert result.status == "partial"
    room_snapshot = next(
        snapshot for snapshot in result.snapshots if snapshot.source == "room"
    )
    assert room_snapshot.status == "unavailable"


def test_metrics_collect_rejects_naive_deadline_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from automation.inago.metrics_live_port import (
        MetricsAdapterError,
        NodeMetricsPort,
    )

    runtime = tmp_path / "runtime"
    script = runtime / "metrics_live.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture", encoding="utf-8")
    browser = tmp_path / "chrome.exe"
    browser.write_text("fixture", encoding="utf-8")
    port = NodeMetricsPort(
        node_executable="node.exe",
        browser_executable=browser,
        script_path=script,
        output_root=runtime / "artifacts" / "metrics",
        runtime_root=runtime,
        owner_room_url="https://room.rakuten.co.jp/room_fixture/items",
        affiliate_auth_state=tmp_path / "affiliate-auth.json",
    )
    connection = SimpleNamespace(
        execute=lambda *args, **kwargs: SimpleNamespace(
            fetchone=lambda: ("2026-10-15",)
        )
    )
    monkeypatch.setattr(
        "automation.inago.metrics_live_port.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail(
            "Node must not launch for a naive deadline"
        ),
    )

    with pytest.raises(MetricsAdapterError, match="metrics_deadline_invalid"):
        port.collect(
            connection,
            run_id="RUN-NAIVE-DEADLINE",
            deadline_at="2026-10-15T13:30:00",
        )

    assert not (runtime / "artifacts" / "metrics").exists()


def test_metrics_collect_rejects_empty_successful_worker_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from automation.inago.metrics_live_port import (
        MetricsAdapterError,
        NodeMetricsPort,
    )

    runtime = tmp_path / "runtime"
    script = runtime / "metrics_live.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture", encoding="utf-8")
    browser = tmp_path / "chrome.exe"
    browser.write_text("fixture", encoding="utf-8")
    port = NodeMetricsPort(
        node_executable="node.exe",
        browser_executable=browser,
        script_path=script,
        output_root=runtime / "artifacts" / "metrics",
        runtime_root=runtime,
        owner_room_url="https://room.rakuten.co.jp/room_fixture/items",
        affiliate_auth_state=tmp_path / "affiliate-auth.json",
    )
    process = SimpleNamespace(
        returncode=0,
        pid=123,
        communicate=lambda timeout: (b" \n", b""),
        poll=lambda: 0,
    )
    connection = SimpleNamespace(
        execute=lambda *args, **kwargs: SimpleNamespace(
            fetchone=lambda: ("2026-10-15",)
        )
    )
    monkeypatch.setattr(
        "automation.inago.metrics_live_port.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    deadline = (datetime.now().astimezone() + timedelta(minutes=2)).isoformat()

    with pytest.raises(MetricsAdapterError, match="metrics_output_contract_invalid"):
        port.collect(
            connection,
            run_id="RUN-EMPTY-WORKER-OUTPUT",
            deadline_at=deadline,
        )


@pytest.mark.parametrize("linked_name", ["affiliate.json", "room.json"])
def test_metrics_collect_rejects_reparse_point_source_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked_name: str
) -> None:
    from automation.inago.metrics_live_port import (
        MetricsAdapterError,
        NodeMetricsPort,
    )

    attempt = tmp_path / "runtime" / "artifacts" / "metrics" / "attempt-fixture"
    process = SimpleNamespace(
        returncode=0,
        pid=456,
        communicate=lambda timeout: (
            json.dumps({"attemptDir": str(attempt)}).encode("utf-8"),
            b"",
        ),
        poll=lambda: 0,
    )
    port, connection, deadline = _metrics_port_with_fake_process(
        tmp_path, monkeypatch, process
    )
    attempt.mkdir(parents=True)
    (attempt / "affiliate.json").write_text("{}", encoding="utf-8")
    (attempt / "room.json").write_text("{}", encoding="utf-8")
    linked_path = attempt / linked_name
    original_check = NodeMetricsPort._has_reparse_point

    def report_fixture_reparse(path: Path) -> bool:
        candidate = Path(path)
        return candidate == linked_path or original_check(candidate)

    monkeypatch.setattr(
        NodeMetricsPort,
        "_has_reparse_point",
        staticmethod(report_fixture_reparse),
    )

    with pytest.raises(MetricsAdapterError, match="metrics_output_contract_invalid"):
        port.collect(
            connection,
            run_id=f"RUN-LINKED-{linked_name}",
            deadline_at=deadline,
        )


def test_metrics_collect_keeps_process_handle_after_failed_tree_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from automation.inago.metrics_live_port import (
        MetricsAdapterError,
        NodeMetricsPort,
    )

    class TimeoutProcess:
        pid = 321
        returncode = None

        @staticmethod
        def communicate(timeout: int) -> tuple[bytes, bytes]:
            raise subprocess.TimeoutExpired(cmd="node", timeout=timeout)

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: int) -> None:
            if self.returncode is None:
                raise subprocess.TimeoutExpired(cmd="node", timeout=timeout)

    process = TimeoutProcess()
    port, connection, deadline = _metrics_port_with_fake_process(
        tmp_path, monkeypatch, process
    )
    monkeypatch.setattr(port, "_stop_process_tree", lambda _process: False)

    with pytest.raises(
        MetricsAdapterError, match="metrics_process_cleanup_unconfirmed"
    ):
        port.collect(connection, run_id="RUN-CLEANUP-RETRY", deadline_at=deadline)

    assert port._processes["RUN-CLEANUP-RETRY"] is process

    def stop_and_reap(selected: TimeoutProcess) -> bool:
        selected.returncode = 0
        return True

    monkeypatch.setattr(port, "_stop_process_tree", stop_and_reap)

    assert port.cancel_run("RUN-CLEANUP-RETRY") is True
    assert "RUN-CLEANUP-RETRY" not in port._processes


def test_metrics_cancellation_fails_when_windows_process_tree_kill_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation.inago.metrics_live_port import NodeMetricsPort

    port = NodeMetricsPort.__new__(NodeMetricsPort)
    port._guard = __import__("threading").Lock()
    process = SimpleNamespace(pid=123, poll=lambda: None)
    port._processes = {"RUN-CANCEL": process}
    monkeypatch.setattr(
        "automation.inago.metrics_live_port.os.name", "nt", raising=False
    )
    monkeypatch.setattr(
        "automation.inago.metrics_live_port.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    assert port.cancel_run("RUN-CANCEL") is False
