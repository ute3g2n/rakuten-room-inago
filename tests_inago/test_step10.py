"""Fixture contracts for Trends snapshots and observed keyword selection."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from automation.inago.contracts import IdempotencyConflict, RejectedContract
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.trends_adapter import (
    FixtureBrowserPort,
    KeywordDecision,
    TrendKeywordSession,
    TrendsContractError,
    build_key_input,
    choose_keyword,
    child_candidates,
    load_keyword_snapshot,
    save_keyword_snapshot,
    validate_key_decisions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONFIG = PROJECT_ROOT / "tests_inago" / "config" / "fixture.json"
PREPARATION_ROOT = PROJECT_ROOT / "plan" / "イナゴスキル事前準備"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
FETCHED_AT = "2026-10-12T13:01:00+09:00"


def make_word(
    word_id: str,
    raw: str,
    rank: int,
    popularity_index: int | float | None = 100,
) -> dict[str, Any]:
    return {
        "word_id": word_id,
        "raw": raw,
        "source_rank": rank,
        "popularity_index": popularity_index,
    }


def snapshot_payload(
    *,
    request_id: str = "request-01",
    snapshot_id: str = "snapshot-01",
    parent_word: str = "揚げ物",
    current_parent: str | None = None,
    refresh_complete: bool = True,
    status: str = "observed",
    fetched_at: str | None = FETCHED_AT,
    words: list[dict[str, Any]] | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "snapshot_id": snapshot_id,
        "run_id": "RUN-STEP10",
        "settings_version": SETTINGS_VERSION,
        "source": "google_trends_public_ui",
        "parent_word": parent_word,
        "current_parent": (
            parent_word
            if current_parent is None and status == "observed"
            else current_parent
        ),
        "refresh_complete": refresh_complete,
        "language": "ja",
        "geo": "JP",
        "search_type": "web",
        "range": "90d",
        "list_type": "Top",
        "status": status,
        "fetched_at": fetched_at,
        "error_code": error_code,
        "words": [] if words is None else words,
    }


def make_session(payload: dict[str, Any], parent: str = "揚げ物"):
    port = FixtureBrowserPort({"request-01": payload})
    session = TrendKeywordSession(
        port,
        run_id="RUN-STEP10",
        settings_version=SETTINGS_VERSION,
    )
    return session, port, session.fetch(parent, snapshot_id="request-01")


def seed_daily_run(connection: sqlite3.Connection) -> None:
    from automation.inago.settings import register_external_settings

    basic = json.loads((PREPARATION_ROOT / "初期設定引渡し.json").read_text(encoding="utf-8"))
    detailed = json.loads(
        (PREPARATION_ROOT / "文型詳細ルール.json").read_text(encoding="utf-8")
    )
    register_external_settings(
        connection,
        basic,
        detailed,
        created_at="2026-10-12T12:00:00+09:00",
    )
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES (?, '2026-10-12', 'daily', 'running', ?, ?, ?, ?, ?,
                0, 0, NULL, 'not_started', NULL)
        """,
        (
            "RUN-STEP10",
            SETTINGS_VERSION,
            "STEP10-SEED",
            "2026-10-12T13:00:00+09:00",
            "2026-10-12T17:45:00+09:00",
            "2026-10-12T18:00:00+09:00",
        ),
    )


def key_decisions(snapshot, *, default: str = "child") -> tuple[KeywordDecision, ...]:
    return tuple(
        KeywordDecision(word_id=word.word_id, decision=default, reason="分類定義に合致")
        for word in snapshot.words
    )


def test_snapshot_uses_explicit_japan_web_90d_top_request_and_current_parent() -> None:
    session, port, snapshot = make_session(
        snapshot_payload(words=[make_word("w-1", "唐揚げ", 1)])
    )

    assert snapshot.status == "observed"
    assert snapshot.request.parent_word == "揚げ物"
    assert snapshot.current_parent == "揚げ物"
    assert snapshot.refresh_complete is True
    assert snapshot.fetched_at == FETCHED_AT
    assert len(port.requests) == 1
    request = port.requests[0]
    assert request.language == "ja"
    assert request.geo == "JP"
    assert request.search_type == "web"
    assert request.range == "90d"
    assert request.list_type == "Top"
    assert request.parent_word == "揚げ物"
    assert session.stopped is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"current_parent": "古い親語"},
        {"refresh_complete": False},
        {"geo": "US"},
        {"language": "en"},
        {"search_type": "image"},
        {"range": "30d"},
        {"list_type": "Rising"},
        {"source": "paid_keyword_api"},
    ],
)
def test_stale_or_unrequested_trends_results_are_invalid_and_discarded(
    overrides: dict[str, Any],
) -> None:
    payload = snapshot_payload(words=[make_word("old-word", "旧結果", 1)])
    payload.update(overrides)
    session, port, snapshot = make_session(payload)

    assert snapshot.status == "invalid"
    assert snapshot.words == ()
    assert session.stopped is True
    assert len(port.requests) == 1


def test_nfkc_whitespace_duplicates_keep_raw_variants_and_original_ranks() -> None:
    _, _, snapshot = make_session(
        snapshot_payload(
            words=[
                make_word("w-late", " ＡＢＣ　ＤＥＦ ", 4, 91),
                make_word("w-early", "ABC DEF", 2, 95),
                make_word("w-other", "別の語", 1, 99),
            ]
        )
    )

    duplicate = next(word for word in snapshot.words if word.normalized == "ABC DEF")
    assert duplicate.word_id == "w-early"
    assert duplicate.source_rank == 2
    assert duplicate.popularity_index == 95
    assert {
        (item.word_id, item.raw, item.source_rank) for item in duplicate.variants
    } == {
        ("w-late", " ＡＢＣ　ＤＥＦ ", 4),
        ("w-early", "ABC DEF", 2),
    }
    payload = build_key_input(
        snapshot,
        level=1,
        parent_definition="大分類の定義",
        child_definition="親語の子分類だけを採る。レシピ・店名・属性は採らない。",
    )
    assert [item["word_id"] for item in payload["words"]] == [
        "w-other",
        "w-early",
    ]
    assert all(set(item) == {"word_id", "term"} for item in payload["words"])


def test_key_decisions_accept_only_the_observed_ids_and_exact_classification_fields() -> None:
    _, _, snapshot = make_session(
        snapshot_payload(
            words=[
                make_word("w-1", "唐揚げ", 1),
                make_word("w-2", "唐揚げ レシピ", 2),
                make_word("w-3", "和風", 3),
                make_word("w-4", "〇〇専門店", 4),
            ]
        )
    )
    key_input = build_key_input(
        snapshot,
        level=1,
        parent_definition="揚げ物の子分類",
        child_definition="味・用途・サイズだけは属性なので不採用",
    )
    decisions = validate_key_decisions(
        snapshot,
        key_input,
        [
            {"word_id": "w-1", "decision": "child", "reason": "料理分類"},
            {"word_id": "w-2", "decision": "not_child", "reason": "レシピ語"},
            {"word_id": "w-3", "decision": "unknown", "reason": "属性か分類か曖昧"},
            {"word_id": "w-4", "decision": "not_child", "reason": "店名"},
        ],
    )

    assert [item.decision for item in decisions] == [
        "child",
        "not_child",
        "unknown",
        "not_child",
    ]
    assert [item.word_id for item in decisions] == ["w-1", "w-2", "w-3", "w-4"]
    assert [
        word.word_id
        for word in child_candidates(snapshot, decisions, level=1, seed="saved-seed")
    ] == ["w-1"]


@pytest.mark.parametrize(
    "responses",
    [
        [
            {"word_id": "w-1", "decision": "child", "reason": "分類"},
            {"word_id": "invented", "decision": "child", "reason": "追加語"},
        ],
        [{"word_id": "w-1", "decision": "child", "reason": "分類"}],
        [
            {"word_id": "w-1", "decision": "child", "reason": "分類"},
            {"word_id": "w-1", "decision": "not_child", "reason": "重複"},
        ],
        [
            {"word_id": "w-1", "decision": "child", "reason": "分類", "source_rank": 1},
            {"word_id": "w-2", "decision": "child", "reason": "分類"},
        ],
    ],
)
def test_key_rejects_added_missing_duplicate_or_rank_modified_results(
    responses: list[dict[str, Any]],
) -> None:
    _, _, snapshot = make_session(
        snapshot_payload(words=[make_word("w-1", "語1", 1), make_word("w-2", "語2", 2)])
    )
    key_input = build_key_input(
        snapshot,
        level=1,
        parent_definition="親",
        child_definition="子分類だけ",
    )

    with pytest.raises(TrendsContractError):
        validate_key_decisions(snapshot, key_input, responses)


def test_top_caps_follow_observed_rank_and_use_seed_only_for_boundary_ties() -> None:
    words = [make_word(f"w-{i:02d}", f"語{i:02d}", i) for i in range(1, 20)]
    words.extend(make_word(f"tie-{i}", f"同順位{i}", 20) for i in range(5))
    words.extend(make_word(f"w-{i:02d}", f"語{i:02d}", i) for i in range(21, 25))
    _, _, snapshot = make_session(snapshot_payload(words=words))
    decisions = key_decisions(snapshot)

    selected_a = child_candidates(snapshot, decisions, level=1, seed="seed-A")
    selected_b = child_candidates(snapshot, decisions, level=1, seed="seed-A")

    assert len(selected_a) == 20
    assert [word.word_id for word in selected_a] == [
        word.word_id for word in selected_b
    ]
    assert {word.source_rank for word in selected_a} <= set(range(1, 21))
    assert sum(word.source_rank == 20 for word in selected_a) == 1
    assert all(word.source_rank < 20 for word in selected_a[:19])


def test_second_level_cap_and_seven_three_candidates_are_not_padded() -> None:
    words = [make_word(f"w-{i:02d}", f"第2語{i:02d}", i) for i in range(1, 10)]
    words.extend(make_word(f"tie-{i}", f"第2同順位{i}", 10) for i in range(4))
    _, _, snapshot = make_session(
        snapshot_payload(words=words, parent_word="第1語"), "第1語"
    )
    decisions = key_decisions(snapshot)

    capped = child_candidates(snapshot, decisions, level=2, seed="seed-B")
    assert len(capped) == 10
    assert sum(word.source_rank == 10 for word in capped) == 1

    _, _, snapshot_seven = make_session(
        snapshot_payload(
            snapshot_id="seven",
            words=[make_word(f"seven-{i}", f"語{i}", i) for i in range(1, 8)],
        )
    )
    _, _, snapshot_three = make_session(
        snapshot_payload(
            snapshot_id="three",
            parent_word="第1語",
            words=[make_word(f"three-{i}", f"第2語{i}", i) for i in range(1, 4)],
        ),
        "第1語",
    )
    seven = child_candidates(
        snapshot_seven, key_decisions(snapshot_seven), level=1, seed="seed-C"
    )
    three = child_candidates(
        snapshot_three, key_decisions(snapshot_three), level=2, seed="seed-C"
    )
    assert len(seven) == 7
    assert len(three) == 3
    assert choose_keyword(seven, seed="seed-C", context="level-1") in seven
    assert choose_keyword(three, seed="seed-C", context="level-2") in three


def test_successful_zero_is_distinct_from_failure_and_failure_stops_without_reuse() -> None:
    zero_session, zero_port, zero = make_session(snapshot_payload(words=[]))
    assert zero.status == "observed"
    assert zero.words == ()
    assert zero.error_code is None
    assert zero_session.stopped is False
    assert choose_keyword((), seed="seed", context="zero") is None
    assert (
        build_key_input(
            zero,
            level=1,
            parent_definition="大分類",
            child_definition="子分類",
        )["words"]
        == []
    )

    failure = snapshot_payload(
        snapshot_id="failed-01",
        status="unavailable",
        current_parent=None,
        refresh_complete=False,
        fetched_at=None,
        words=[],
        error_code="source_unavailable",
    )
    failure_session, failure_port, failed = make_session(failure)
    assert failed.status == "unavailable"
    assert failed.words == ()
    assert failed.error_code == "source_unavailable"
    assert failure_session.stopped is True
    with pytest.raises(RejectedContract):
        failure_session.fetch("別テーマ", snapshot_id="request-02")
    assert len(failure_port.requests) == 1
    assert len(zero_port.requests) == 1


def test_parent_scoped_indices_are_not_combined_and_same_parent_is_not_refetched() -> None:
    response_a = snapshot_payload(
        request_id="request-a",
        snapshot_id="snapshot-a",
        parent_word="テーマA",
        words=[make_word("a-1", "観測語A", 1, 100)],
    )
    response_b = snapshot_payload(
        request_id="request-b",
        snapshot_id="snapshot-b",
        parent_word="テーマB",
        words=[make_word("b-1", "観測語B", 1, 100)],
    )
    port = FixtureBrowserPort({"request-a": response_a, "request-b": response_b})
    session = TrendKeywordSession(
        port,
        run_id="RUN-STEP10",
        settings_version=SETTINGS_VERSION,
    )

    first = session.fetch("テーマA", snapshot_id="request-a")
    second = session.fetch("テーマB", snapshot_id="request-b")
    with pytest.raises(RejectedContract):
        session.fetch("テーマA", snapshot_id="request-a-retry")

    assert first.words[0].popularity_index == second.words[0].popularity_index == 100
    assert first.words[0].word_id != second.words[0].word_id
    assert [request.parent_word for request in port.requests] == ["テーマA", "テーマB"]


def test_unavailable_response_cannot_smuggle_old_words() -> None:
    payload = snapshot_payload(
        status="unavailable",
        current_parent=None,
        refresh_complete=False,
        fetched_at=None,
        words=[make_word("old", "古い語", 1)],
        error_code="source_unavailable",
    )
    _, _, snapshot = make_session(payload)

    assert snapshot.status == "invalid"
    assert snapshot.words == ()
    assert snapshot.error_code == "stale_words_on_failure"


def test_missing_popularity_index_remains_unknown_and_is_never_fabricated() -> None:
    _, _, snapshot = make_session(
        snapshot_payload(words=[make_word("unknown-index", "順位値なし", 1, None)])
    )

    assert snapshot.words[0].popularity_index is None
    assert snapshot.words[0].source_rank == 1


def test_snapshot_persistence_distinguishes_observed_zero_and_unavailable(
    tmp_path: Path,
) -> None:
    paths = resolve_runtime_paths(
        data_root=tmp_path / "step10" / "state",
        config_path=FIXTURE_CONFIG,
        fixture_mode=True,
    )
    connection = open_database(paths)
    try:
        seed_daily_run(connection)
        _, zero_port, zero = make_session(
            snapshot_payload(snapshot_id="zero", words=[])
        )
        unavailable_payload = snapshot_payload(
            snapshot_id="failed",
            parent_word="別テーマ",
            status="unavailable",
            current_parent=None,
            refresh_complete=False,
            fetched_at=None,
            words=[],
            error_code="source_unavailable",
        )
        _, unavailable_port, unavailable = make_session(unavailable_payload, "別テーマ")

        save_keyword_snapshot(connection, zero, decisions=())
        save_keyword_snapshot(connection, unavailable, decisions=())
        rows = connection.execute(
            "SELECT snapshot_id, availability, words_json, fetched_at "
            "FROM keyword_snapshot ORDER BY snapshot_id"
        ).fetchall()

        assert [(row[0], row[1], json.loads(row[2]), row[3]) for row in rows] == [
            ("failed", "unavailable", [], None),
            ("zero", "observed", [], FETCHED_AT),
        ]
        restored_zero = load_keyword_snapshot(
            connection,
            "zero",
            request=zero_port.requests[0],
        )
        restored_failure = load_keyword_snapshot(
            connection,
            "failed",
            request=unavailable_port.requests[0],
        )
        assert restored_zero is not None and restored_zero.snapshot.status == "observed"
        assert restored_zero.decisions == ()
        assert restored_failure is not None
        assert restored_failure.snapshot.status == "unavailable"
        assert restored_failure.snapshot.error_code == "source_unavailable"
    finally:
        connection.close()


def test_snapshot_persistence_allows_pending_to_classified_once_and_is_idempotent(
    tmp_path: Path,
) -> None:
    paths = resolve_runtime_paths(
        data_root=tmp_path / "step10" / "state",
        config_path=FIXTURE_CONFIG,
        fixture_mode=True,
    )
    connection = open_database(paths)
    try:
        seed_daily_run(connection)
        _, port, snapshot = make_session(
            snapshot_payload(
                snapshot_id="classified",
                words=[make_word("word-1", "唐揚げ", 1)],
            )
        )
        decisions = key_decisions(snapshot)

        assert save_keyword_snapshot(connection, snapshot) == "classified"
        pending = load_keyword_snapshot(
            connection,
            "classified",
            request=port.requests[0],
        )
        assert pending is not None
        assert pending.snapshot == snapshot
        assert pending.decisions is None
        assert (
            save_keyword_snapshot(connection, snapshot, decisions=decisions)
            == "classified"
        )
        assert (
            save_keyword_snapshot(connection, snapshot, decisions=decisions)
            == "classified"
        )
        restored = load_keyword_snapshot(
            connection,
            "classified",
            request=port.requests[0],
        )
        assert restored is not None
        assert restored.snapshot == snapshot
        assert restored.decisions == decisions
        stored = connection.execute(
            "SELECT words_json, filter_decisions_json FROM keyword_snapshot "
            "WHERE snapshot_id='classified'"
        ).fetchone()
        assert json.loads(stored[0])[0]["is_child"] is True
        assert json.loads(stored[1])[1]["decision"] == "child"
        with pytest.raises(IdempotencyConflict):
            save_keyword_snapshot(
                connection,
                snapshot,
                decisions=(KeywordDecision("word-1", "not_child", "訂正不能"),),
            )
        _, _, duplicate_parent = make_session(
            snapshot_payload(
                request_id="request-other",
                snapshot_id="snapshot-other",
                words=[make_word("word-other", "別の観測語", 1)],
            )
        )
        with pytest.raises(IdempotencyConflict):
            save_keyword_snapshot(connection, duplicate_parent)
    finally:
        connection.close()


def test_browser_contract_mjs_accepts_fixture_and_rejects_stale_parent() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to verify the fixture browser contract module")
    module_url = (
        PROJECT_ROOT / "automation" / "inago" / "adapters" / "browser_contract.mjs"
    ).as_uri()
    script = f"""
import {{ validateTrendsSnapshot }} from {json.dumps(module_url)};
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const input = JSON.parse(raw);
const expected = {{requestId: 'request-01', runId: 'RUN-STEP10', settingsVersion: {json.dumps(SETTINGS_VERSION)}, parentWord: '揚げ物'}};
const accepted = validateTrendsSnapshot(input.good, expected);
const rejected = validateTrendsSnapshot(input.stale, expected);
process.stdout.write(JSON.stringify({{accepted, rejected}}));
"""
    good = snapshot_payload(words=[make_word("w-1", "唐揚げ", 1)])
    stale = snapshot_payload(current_parent="古い親語", words=[make_word("old", "古い語", 1)])

    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        input=json.dumps({"good": good, "stale": stale}, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=PROJECT_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["accepted"]["status"] == "observed", output["accepted"]
    assert output["accepted"]["words"][0]["word_id"] == "w-1"
    assert output["rejected"]["status"] == "invalid"
    assert output["rejected"]["words"] == []
