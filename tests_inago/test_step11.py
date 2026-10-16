"""Fixture-only contracts for STEP 11 ROOM search observations."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from automation.inago.clock import FakeClock
from automation.inago.contracts import RejectedContract
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.room_adapter import (
    FixtureRoomSearchPort,
    RoomSearchSession,
    RoomUnknownScreen,
    validate_room_observations,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONFIG = ROOT / "tests_inago" / "config" / "fixture.json"
PREPARATION_ROOT = ROOT / "plan" / "イナゴスキル事前準備"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
NOW = "2026-10-12T13:01:00+09:00"
DEADLINE = "2026-10-12T17:45:00+09:00"


def _seed_daily_run(connection: Any, *, attempt_used: int = 0) -> None:
    from automation.inago.settings import register_external_settings

    basic = json.loads((PREPARATION_ROOT / "初期設定引渡し.json").read_text(encoding="utf-8"))
    detailed = json.loads(
        (PREPARATION_ROOT / "文型詳細ルール.json").read_text(encoding="utf-8")
    )
    register_external_settings(
        connection, basic, detailed, created_at="2026-10-12T12:00:00+09:00"
    )
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('RUN-STEP11', '2026-10-12', 'daily', 'running', ?, 'STEP11-SEED',
                ?, ?, '2026-10-12T18:00:00+09:00', ?, 0, NULL, 'not_started', NULL)
        """,
        (SETTINGS_VERSION, NOW, DEADLINE, attempt_used),
    )


def _database(tmp_path: Path, *, attempt_used: int = 0):
    paths = resolve_runtime_paths(
        data_root=tmp_path / "state",
        config_path=FIXTURE_CONFIG,
        fixture_mode=True,
    )
    connection = open_database(paths)
    _seed_daily_run(connection, attempt_used=attempt_used)
    return connection


def _post(
    index: int,
    *,
    room_id: str | None = None,
    likes: int | None = 310,
    shop_id: str | None = "shop-a",
    item_id: str | None = None,
    item_url: str | None = None,
) -> dict[str, Any]:
    resolved_item = item_id or f"item-{index}"
    return {
        "internal_index": index,
        "stable_room_id": room_id or f"room-{index}",
        "room_url": f"https://room.rakuten.co.jp/user/item/{index}",
        "likes": likes,
        "comments": None,
        "item_url": (
            item_url
            if item_url is not None
            else (
                None
                if shop_id is None
                else f"https://item.rakuten.co.jp/{shop_id}/{resolved_item}/"
            )
        ),
        "observed_shop_id": shop_id,
        "observed_item_id": resolved_item if shop_id is not None else None,
        "image_ref": f"fixture-image-{index}",
        "actor_ref": None,
        "published_at": None,
        "evidence_ref": f"fixture-evidence-{index}",
        "observed_at": NOW,
        "availability": "observed",
    }


def _response(
    attempt_id: str,
    *,
    query: str = "揚げ物",
    parent_refs: list[str] | None = None,
    posts: list[dict[str, Any]] | None = None,
    end_reached: bool = True,
    current_search_id: str | None = None,
    status: str = "observed",
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "run_id": "RUN-STEP11",
        "query": query,
        "parent_refs": parent_refs or ["theme-01"],
        "level": "parent",
        "likes_min": 300,
        "max_posts": 50,
        "current_search_id": (
            current_search_id
            if current_search_id is not None
            else (attempt_id if status != "unavailable" else None)
        ),
        "search_loaded": status != "unavailable",
        "internal_order_confirmed": status != "unavailable",
        "status": status,
        "fetched_at": NOW if status != "unavailable" else None,
        "end_reached": end_reached if status != "unavailable" else False,
        "error_code": error_code,
        "posts": posts or [],
    }


def _session(connection: Any, responses: dict[str, dict[str, Any]]):
    port = FixtureRoomSearchPort(responses)
    clock = FakeClock(datetime.fromisoformat(NOW))
    session = RoomSearchSession(connection, port, clock=clock)
    return session, port


def test_attempt_is_reserved_before_search_and_successful_zero_is_not_failure(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        payload = _response("attempt-1")

        class InspectingPort(FixtureRoomSearchPort):
            def search(self, request):
                assert request.query == "揚げ物"
                assert request.likes_min == 300
                assert request.max_posts == 50
                assert request.deadline_at == DEADLINE
                assert (
                    connection.execute(
                        "SELECT attempt_used FROM run WHERE run_id = ?",
                        (request.run_id,),
                    ).fetchone()[0]
                    == request.attempt_no
                )
                row = connection.execute(
                    "SELECT attempt_no, source_status, ended_at FROM search_attempt WHERE attempt_id = ?",
                    (request.attempt_id,),
                ).fetchone()
                assert tuple(row) == (request.attempt_no, "invalid", None)
                return super().search(request)

        port = InspectingPort({"attempt-1": payload})
        result = RoomSearchSession(
            connection, port, clock=FakeClock(datetime.fromisoformat(NOW))
        ).search(
            attempt_id="attempt-1",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )

        assert result.status == "observed"
        assert result.posts == ()
        assert result.error_code is None
        row = connection.execute(
            "SELECT attempt_no, source_status, max_posts, source_order FROM search_attempt"
        ).fetchone()
        assert tuple(row) == (1, "observed", 50, "internal")
        assert (
            connection.execute(
                "SELECT attempt_used FROM run WHERE run_id = 'RUN-STEP11'"
            ).fetchone()[0]
            == 1
        )
        port._responses["attempt-partial"] = _response(
            "attempt-partial",
            posts=[_post(1)],
            status="partial",
            end_reached=False,
        )
        partial = RoomSearchSession(
            connection, port, clock=FakeClock(datetime.fromisoformat(NOW))
        ).search(
            attempt_id="attempt-partial",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert partial.status == "partial"
        assert partial.error_code == "search_not_terminal"
        assert len(partial.posts) == 1
    finally:
        connection.close()


def test_unapplied_query_likes_or_old_search_is_invalid_and_persists_no_candidates(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        cases = {
            "attempt-stale-id": _response(
                "attempt-stale-id",
                current_search_id="old-search",
                posts=[_post(1)],
            ),
            "attempt-stale-query": _response(
                "attempt-stale-query", query="別の検索", posts=[_post(2)]
            ),
            "attempt-stale-likes": {
                **_response("attempt-stale-likes", posts=[_post(3)]),
                "likes_min": 299,
            },
        }
        session, _ = _session(connection, cases)
        for attempt_id in cases:
            result = session.search(
                attempt_id=attempt_id,
                run_id="RUN-STEP11",
                query="揚げ物",
                parent_refs=["theme-01"],
                level="parent",
            )
            assert result.status == "invalid"
            assert result.posts == ()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM search_attempt WHERE source_status = 'invalid'"
            ).fetchone()[0]
            == 3
        )
        assert connection.execute("SELECT COUNT(*) FROM source_post").fetchone()[0] == 0
    finally:
        connection.close()


def test_internal_order_deduplicates_same_permanent_id_and_caps_at_50(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        exact_response = _response(
            "attempt-exact-50", posts=[_post(index) for index in range(1, 51)]
        )
        exact_session, _ = _session(connection, {"attempt-exact-50": exact_response})
        exact_result = exact_session.search(
            attempt_id="attempt-exact-50",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert exact_result.status == "observed"
        assert len(exact_result.posts) == 50
        assert exact_result.end_reached is True
        assert (
            connection.execute("SELECT COUNT(*) FROM source_post").fetchone()[0] == 50
        )
    finally:
        connection.close()

    connection = _database(tmp_path / "over-limit")
    try:
        response = _response(
            "attempt-cap",
            posts=[_post(index) for index in range(1, 52)],
            end_reached=True,
        )
        session, _ = _session(connection, {"attempt-cap": response})
        result = session.search(
            attempt_id="attempt-cap",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert result.status == "partial"
        assert len(result.posts) == 50
        assert [post.order_index for post in result.posts] == list(range(1, 51))
        assert [post.stable_room_id for post in result.posts[:3]] == [
            "room-1",
            "room-2",
            "room-3",
        ]
        assert (
            connection.execute("SELECT COUNT(*) FROM source_post").fetchone()[0] == 50
        )
        assert (
            connection.execute("SELECT MAX(order_index) FROM source_post").fetchone()[0]
            == 50
        )
    finally:
        connection.close()

    connection = _database(tmp_path / "duplicates")
    try:
        duplicate_rows = [
            _post(1, room_id="same-room"),
            _post(2, room_id="other-room"),
            _post(3, room_id="same-room"),
        ]
        session, _ = _session(
            connection,
            {"attempt-dups": _response("attempt-dups", posts=duplicate_rows)},
        )
        result = session.search(
            attempt_id="attempt-dups",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert [post.stable_room_id for post in result.posts] == [
            "same-room",
            "other-room",
        ]
        assert [post.order_index for post in result.posts] == [1, 2]
        assert connection.execute("SELECT COUNT(*) FROM source_post").fetchone()[0] == 2
    finally:
        connection.close()

    connection = _database(tmp_path / "duplicate-does-not-use-slot")
    try:
        rows = [_post(index, room_id=f"room-{index}") for index in range(1, 49)]
        rows.extend(
            [
                _post(49, room_id="room-48"),
                _post(50, room_id="room-48"),
                _post(51, room_id="room-49"),
                _post(52, room_id="room-50"),
            ]
        )
        session, _ = _session(
            connection,
            {"attempt-duplicate-slot": _response("attempt-duplicate-slot", posts=rows)},
        )
        result = session.search(
            attempt_id="attempt-duplicate-slot",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert result.status == "observed"
        assert len(result.posts) == 50
        assert result.posts[-1].stable_room_id == "room-50"
        assert (
            connection.execute("SELECT COUNT(*) FROM source_post").fetchone()[0] == 50
        )
    finally:
        connection.close()


def test_likes_keep_null_and_product_key_requires_matching_observed_ids(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        rows = [
            _post(1, likes=299),
            _post(2, likes=300),
            _post(3, likes=None),
            _post(
                4,
                shop_id="other-shop",
                item_id="expected-item",
                item_url="https://item.rakuten.co.jp/shop-a/actual-item/",
            ),
            _post(
                5,
                shop_id=None,
                item_url="https://item.rakuten.co.jp/shop-from-url/item-from-url/",
            ),
            _post(6),
        ]
        rows[5]["stable_room_id"] = None
        same_image_first = _post(7)
        same_image_second = _post(8)
        same_image_first["image_ref"] = "same-image-ref"
        same_image_second["image_ref"] = "same-image-ref"
        rows.extend([same_image_first, same_image_second])
        session, _ = _session(
            connection, {"attempt-likes": _response("attempt-likes", posts=rows)}
        )
        result = session.search(
            attempt_id="attempt-likes",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert [post.likes for post in result.posts] == [
            299,
            300,
            None,
            310,
            310,
            310,
            310,
            310,
        ]
        assert [post.offer_key for post in result.posts] == [
            "shop-a:item-1",
            "shop-a:item-2",
            "shop-a:item-3",
            None,
            "shop-from-url:item-from-url",
            "shop-a:item-6",
            "shop-a:item-7",
            "shop-a:item-8",
        ]
        assert result.posts[5].stable_room_id is None
        assert result.posts[5].source_key == (
            "attempt-likes:url:https://room.rakuten.co.jp/user/item/6:position:6"
        )
        stored = connection.execute(
            "SELECT likes FROM source_post ORDER BY order_index"
        ).fetchall()
        assert [row[0] for row in stored] == [
            299,
            300,
            None,
            310,
            310,
            310,
            310,
            310,
        ]
        same_images = connection.execute(
            "SELECT image_ref, COUNT(DISTINCT item_url) FROM source_post "
            "WHERE order_index IN (7, 8) GROUP BY image_ref"
        ).fetchone()
        assert tuple(same_images) == ("same-image-ref", 2)
    finally:
        connection.close()


def test_transport_failure_is_unavailable_and_retries_use_new_attempt_rows(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:

        class FailingPort:
            def __init__(self) -> None:
                self.requests = []

            def search(self, request):
                self.requests.append(request)
                raise RuntimeError("fixture source failure")

        failing_port = FailingPort()
        session = RoomSearchSession(
            connection,
            failing_port,
            clock=FakeClock(datetime.fromisoformat(NOW)),
        )
        first = session.search(
            attempt_id="attempt-fail-1",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert first.status == "unavailable"
        assert first.posts == ()
        assert first.error_code == "source_adapter_error"
        assert len(failing_port.requests) == 1

        response = _response("attempt-fail-2", posts=[_post(1)])
        fixture_port = FixtureRoomSearchPort({"attempt-fail-2": response})
        second = RoomSearchSession(
            connection,
            fixture_port,
            clock=FakeClock(datetime.fromisoformat(NOW)),
        ).search(
            attempt_id="attempt-fail-2",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
            retry_of="attempt-fail-1",
        )
        assert second.status == "observed"
        assert len(fixture_port.requests) == 1
        attempts = connection.execute(
            "SELECT attempt_no, retry_of FROM search_attempt ORDER BY attempt_no"
        ).fetchall()
        assert [tuple(row) for row in attempts] == [
            (1, None),
            (2, "attempt-fail-1"),
        ]
    finally:
        connection.close()


def test_search_at_old_draft_cutoff_is_still_reserved_and_called(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        port = FixtureRoomSearchPort({})
        session = RoomSearchSession(
            connection,
            port,
            clock=FakeClock(datetime.fromisoformat(DEADLINE)),
        )
        result = session.search(
            attempt_id="attempt-after-deadline",
            run_id="RUN-STEP11",
            query="揚げ物",
            parent_refs=["theme-01"],
            level="parent",
        )
        assert result.status == "unavailable"
        assert [request.attempt_id for request in port.requests] == [
            "attempt-after-deadline"
        ]
        assert (
            connection.execute("SELECT COUNT(*) FROM search_attempt").fetchone()[0] == 1
        )
        assert (
            connection.execute(
                "SELECT attempt_used FROM run WHERE run_id = 'RUN-STEP11'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_search_attempts_are_unbounded_but_retries_remain_bounded(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        responses = {
            f"attempt-{n}": _response(f"attempt-{n}", query=f"query-{n}")
            for n in range(1, 11)
        }
        session, port = _session(connection, responses)
        for number in range(1, 11):
            session.search(
                attempt_id=f"attempt-{number}",
                run_id="RUN-STEP11",
                query=f"query-{number}",
                parent_refs=["theme-01"],
                level="parent",
            )
        assert len(port.requests) == 10
        assert (
            connection.execute("SELECT COUNT(*) FROM search_attempt").fetchone()[0]
            == 10
        )
        assert (
            connection.execute(
                "SELECT attempt_used FROM run WHERE run_id = 'RUN-STEP11'"
            ).fetchone()[0]
            == 10
        )

    finally:
        connection.close()

    connection = _database(tmp_path / "retry-budget")
    try:
        responses = {
            f"retry-{n}": _response(
                f"retry-{n}",
                query="retry-query",
                status="unavailable",
                error_code="source_error",
            )
            for n in range(1, 5)
        }
        session, port = _session(connection, responses)
        previous = None
        for number in range(1, 4):
            result = session.search(
                attempt_id=f"retry-{number}",
                run_id="RUN-STEP11",
                query="retry-query",
                parent_refs=["theme-01"],
                level="parent",
                retry_of=previous,
            )
            assert result.status == "unavailable"
            previous = f"retry-{number}"
        with pytest.raises(RejectedContract, match="initial attempt plus two retries"):
            session.search(
                attempt_id="retry-4",
                run_id="RUN-STEP11",
                query="retry-query",
                parent_refs=["theme-01"],
                level="parent",
                retry_of=previous,
            )
        assert len(port.requests) == 3
        assert (
            connection.execute("SELECT COUNT(*) FROM search_attempt").fetchone()[0] == 3
        )
    finally:
        connection.close()


def test_only_allowlisted_observation_fields_on_known_screen_are_accepted() -> None:
    observation = {
        "field": "query",
        "value": "揚げ物",
        "position": "search-box",
        "ref": "snapshot-1",
        "availability": "observed",
        "unknown_reason": None,
    }
    result = validate_room_observations(
        [observation], snapshot_id="snapshot-1", known_screen=True
    )
    assert result == (observation,)

    with pytest.raises(RejectedContract, match="not allowlisted"):
        validate_room_observations(
            [{**observation, "field": "posting_state"}],
            snapshot_id="snapshot-1",
            known_screen=True,
        )
    with pytest.raises(RoomUnknownScreen, match="unknown ROOM screen"):
        validate_room_observations(
            [observation], snapshot_id="snapshot-1", known_screen=False
        )


def test_fixture_browser_contract_strips_stale_results() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "Node.js is not installed; browser-contract syntax is checked separately"
        )
    module = ROOT / "automation" / "inago" / "adapters" / "browser_contract.mjs"
    script = f"""
      import {{ validateRoomSnapshot }} from {json.dumps(module.as_uri())};
      const response = {json.dumps(_response("attempt-js", posts=[_post(1)]), ensure_ascii=False)};
      const expected = {{attemptId:"attempt-js", runId:"RUN-STEP11", query:"揚げ物", parentRefs:["theme-01"], level:"parent"}};
      const current = validateRoomSnapshot(response, expected);
      if (current.status !== "observed" || current.posts.length !== 1) process.exit(2);
      response.current_search_id = "old-search";
      const stale = validateRoomSnapshot(response, expected);
      if (stale.status !== "invalid" || stale.posts.length !== 0) process.exit(3);
    """
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
