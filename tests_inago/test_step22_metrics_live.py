"""Local fixture contracts for the STEP22 live-source normalization boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
