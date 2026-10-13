"""Q1.4 Top20, semantic groups, raw draws and acquisition-order products."""

from __future__ import annotations

import sqlite3
import json
import subprocess
import shutil
from pathlib import Path
from dataclasses import replace

import pytest

from automation.inago import trends_adapter as trends
from automation.inago.repository import initialize_database
from automation.inago.selection import OfferHistory, SourcePost, evaluate_sources
from tests_inago.test_step21_schema import legacy_v2  # noqa: F401


NOW = "2026-10-13T13:01:00+09:00"
SNAPSHOT = "KW-V14"


def request():
    return trends.TrendsRequest(SNAPSHOT, "R1", "S1", "電子レンジ", range="3m")


def top_response(count=25):
    raw = [
        "電子レンジ",
        "レンジ",
        "電子",
        "レンジ 調理 器具",
        "電子 調理 器具",
        "電子 レンジ 調理 器具",
    ]
    raw += [f"用途 {number}" for number in range(7, count + 1)]
    return {
        "request_id": SNAPSHOT,
        "snapshot_id": SNAPSHOT,
        "run_id": "R1",
        "settings_version": "S1",
        "source": "google_trends_public_ui",
        "parent_word": "電子レンジ",
        "current_parent": "電子レンジ",
        "refresh_complete": True,
        "language": "ja",
        "geo": "JP",
        "search_type": "web",
        "range": "3m",
        "list_type": "Top",
        "ui_period_label": "過去3か月間",
        "status": "observed",
        "fetched_at": NOW,
        "error_code": None,
        "words": [
            {
                "word_id": f"W{index}",
                "raw": word,
                "source_rank": index,
                "popularity_index": None if index == 8 else 100 - index,
            }
            for index, word in enumerate(raw[:count], 1)
        ],
    }


def snapshot(count=25):
    return trends.parse_grouped_top_response(top_response(count), request())


def grouped_response(data, *, only_three=False):
    decisions = []
    for word in data.words:
        number = int(word.word_id[1:])
        if number <= 3:
            kind, group = "query_same", None
        elif number <= 6:
            kind, group = ("candidate" if number == 4 else "duplicate_member"), "W4"
        elif only_three:
            group = "W7" if number < 11 else "W11"
            kind = "candidate" if word.word_id == group else "duplicate_member"
        else:
            kind, group = "candidate", word.word_id
        decisions.append(
            {
                "word_id": word.word_id,
                "decision": kind,
                "group_ref": group,
                "reason": "固定fixtureの意味判断",
            }
        )
    return {
        "snapshot_id": data.snapshot_id,
        "query_word": data.request.parent_word,
        "decisions": decisions,
    }


def test_raw_top_twenty_is_cut_before_normalization_or_semantic_exclusion():
    payload = top_response()
    payload["words"][18]["raw"] = "用途 7"
    payload["words"][19]["raw"] = "用途　7"
    data = trends.parse_grouped_top_response(payload, request())
    assert data.status == "observed"
    assert len(data.words) == 20
    assert [word.word_id for word in data.words] == [f"W{i}" for i in range(1, 21)]
    assert data.words[18].raw == "用途 7" and data.words[19].raw == "用途　7"
    assert data.words[7].popularity_index is None


def test_three_semantic_groups_use_actual_count_and_one_unmodified_variant():
    data = snapshot()
    response = grouped_response(data, only_three=True)
    decisions = trends.validate_grouped_key_response(data, response)
    first = trends.draw_room_keywords(data, decisions, seed="SAVED-SEED")
    resumed = trends.draw_room_keywords(data, decisions, seed="SAVED-SEED")
    assert first == resumed
    assert len(first) == 3
    assert {item.group_ref for item in first} == {"W4", "W7", "W11"}
    lookup = {word.word_id: word.raw for word in data.words}
    assert all(item.raw == lookup[item.word_id] for item in first)
    assert not any(item.word_id in {"W1", "W2", "W3", "W21"} for item in first)
    assert next(item for item in first if item.group_ref == "W4").raw in {
        "レンジ 調理 器具",
        "電子 調理 器具",
        "電子 レンジ 調理 器具",
    }
    prepared = trends.grouped_key_input(data)
    assert prepared["snapshot_id"] == SNAPSHOT
    assert prepared["words"][5]["raw"] == "電子 レンジ 調理 器具"


def test_maximum_five_distinct_groups_and_group_members_are_seeded():
    data = snapshot()
    decisions = trends.validate_grouped_key_response(data, grouped_response(data))
    draws = [
        trends.draw_room_keywords(data, decisions, seed=f"SEED-{n}") for n in range(30)
    ]
    assert all(
        len(draw) == 5 and len({item.group_ref for item in draw}) == 5 for draw in draws
    )
    assert all(
        trends.draw_room_keywords(data, decisions, seed=f"SEED-{n}") == draw
        for n, draw in enumerate(draws)
    )
    assert len({tuple(item.word_id for item in draw) for draw in draws}) > 1


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_snapshot",
        "new_word",
        "missing_word",
        "duplicate_word",
        "bad_group",
        "query_retained",
    ],
)
def test_group_contract_rejects_mismatched_ids_and_invented_groups(mutation):
    data = snapshot()
    response = grouped_response(data)
    if mutation == "foreign_snapshot":
        response["snapshot_id"] = "OTHER"
    elif mutation == "new_word":
        response["decisions"][0]["word_id"] = "NEW"
    elif mutation == "missing_word":
        response["decisions"].pop()
    elif mutation == "duplicate_word":
        response["decisions"][1] = response["decisions"][0]
    elif mutation == "bad_group":
        response["decisions"][4]["group_ref"] = "NEW-GROUP"
    else:
        response["decisions"][0].update(decision="candidate", group_ref="W1")
    with pytest.raises(trends.TrendsContractError):
        trends.validate_grouped_key_response(data, response)


def test_unknown_semantic_relationship_remains_a_distinct_candidate():
    data = snapshot()
    response = grouped_response(data, only_three=True)
    response["decisions"][7].update(
        decision="unknown", group_ref="W8", reason="同義と判断する根拠不足"
    )
    decisions = trends.validate_grouped_key_response(data, response)
    draw = trends.draw_room_keywords(data, decisions, seed="SAVED-SEED")
    assert len(draw) == 4 and "W8" in {word.group_ref for word in draw}


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_parent", "古いテーマ"),
        ("refresh_complete", False),
        ("list_type", "Rising"),
        ("range", "7d"),
        ("ui_period_label", "過去7日間"),
    ],
)
def test_stale_or_wrong_scope_is_invalid_and_has_no_words(field, value):
    response = top_response()
    response[field] = value
    result = trends.parse_grouped_top_response(response, request())
    assert result.status == "invalid" and result.words == ()


def test_successful_zero_and_acquisition_failure_are_distinct():
    result = trends.parse_grouped_top_response(top_response(0), request())
    assert result.status == "observed" and result.words == ()
    assert trends.draw_room_keywords(result, (), seed="SAVED-SEED") == ()
    response = top_response(0)
    response.update(
        status="unavailable",
        error_code="http_429",
        fetched_at=None,
        current_parent=None,
        refresh_complete=False,
        ui_period_label=None,
    )
    result = trends.parse_grouped_top_response(response, request())
    assert result.status == "unavailable" and result.words == ()
    with pytest.raises(trends.KeywordAcquisitionStopped):
        trends.draw_room_keywords(result, (), seed="SAVED-SEED")


def test_raw_and_grouped_snapshot_save_resume_without_redraw(legacy_v2):
    initialize_database(legacy_v2)
    data = snapshot()
    assert trends.save_grouped_snapshot(legacy_v2, data) == SNAPSHOT
    decisions = trends.validate_grouped_key_response(
        data, grouped_response(data, only_three=True)
    )
    drawn = trends.draw_room_keywords(data, decisions, seed="SAVED-SEED")
    assert (
        trends.save_grouped_snapshot(
            legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
        )
        == SNAPSHOT
    )
    saved = trends.load_grouped_snapshot(legacy_v2, SNAPSHOT, request=request())
    assert (
        saved.snapshot == data
        and saved.decisions == decisions
        and saved.selected == drawn
    )
    trends.save_grouped_snapshot(
        legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
    )
    with pytest.raises(trends.IdempotencyConflict):
        trends.save_grouped_snapshot(
            legacy_v2, data, decisions=decisions, seed="NEW-SEED"
        )
    assert legacy_v2.execute(
        "SELECT range,ui_period_label FROM keyword_snapshot WHERE snapshot_id=?",
        (SNAPSHOT,),
    ).fetchone()[:] == ("3m", "過去3か月間")


def test_snapshot_upgrade_save_failure_rolls_back_to_pending_without_new_fetch(
    legacy_v2, monkeypatch
):
    initialize_database(legacy_v2)
    data = snapshot()
    trends.save_grouped_snapshot(legacy_v2, data)
    before = legacy_v2.execute(
        "SELECT words_json,filter_decisions_json FROM keyword_snapshot WHERE snapshot_id=?",
        (SNAPSHOT,),
    ).fetchone()[:]
    decisions = trends.validate_grouped_key_response(data, grouped_response(data))
    legacy_v2.execute(
        "CREATE TRIGGER stop_group_save BEFORE UPDATE ON keyword_snapshot BEGIN SELECT RAISE(ABORT,'save failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="save failure"):
        trends.save_grouped_snapshot(
            legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
        )
    assert (
        legacy_v2.execute(
            "SELECT words_json,filter_decisions_json FROM keyword_snapshot WHERE snapshot_id=?",
            (SNAPSHOT,),
        ).fetchone()[:]
        == before
    )
    legacy_v2.execute("DROP TRIGGER stop_group_save")
    trends.save_grouped_snapshot(
        legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
    )
    assert (
        len(
            trends.load_grouped_snapshot(
                legacy_v2, SNAPSHOT, request=request()
            ).selected
        )
        == 5
    )


def test_products_with_null_likes_use_first_post_and_acquisition_order():
    posts = [
        SourcePost("A-first", "shop:A", None, None, True, 1),
        SourcePost("B", "shop:B", 900, None, True, 2),
        SourcePost("A-later", "shop:A", 5000, None, True, 3),
    ]
    result = evaluate_sources(posts, seed="SAVED-SEED", history=OfferHistory())
    assert [item.offer_key for item in result.eligible_representatives] == [
        "shop:A",
        "shop:B",
    ]
    assert result.representatives[0].source_key == "A-first"
    assert result.primary_reason_counts == {
        "same_search_offer_duplicate": 1,
        "eligible": 2,
    }


def test_posted_pending_and_reserved_exclusions_survive_numeric_likes_removal():
    posts = [
        SourcePost(str(i), f"shop:{letter}", None, None, True, i)
        for i, letter in enumerate("ABCD", 1)
    ]
    history = OfferHistory(
        permanently_posted=frozenset({"shop:A"}),
        pending_posted=frozenset({"shop:B"}),
        active_reservations=frozenset({"shop:C"}),
    )
    result = evaluate_sources(posts, seed="SAVED-SEED", history=history)
    assert [item.offer_key for item in result.eligible_representatives] == ["shop:D"]


def test_resume_and_idempotent_save_perform_no_new_draw(legacy_v2, monkeypatch):
    initialize_database(legacy_v2)
    data = snapshot()
    decisions = trends.validate_grouped_key_response(data, grouped_response(data))
    trends.save_grouped_snapshot(
        legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
    )
    saved = trends.load_grouped_snapshot(legacy_v2, SNAPSHOT, request=request())

    def unexpected_draw(*args, **kwargs):
        raise AssertionError("resume performed a new draw")

    monkeypatch.setattr(trends, "draw_room_keywords", unexpected_draw)
    trends.save_grouped_snapshot(
        legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
    )
    assert trends.load_grouped_snapshot(legacy_v2, SNAPSHOT, request=request()) == saved


def test_group_session_uses_current_range_and_does_not_refetch_saved_query():
    class Port:
        def __init__(self):
            self.requests = []

        def fetch(self, value):
            self.requests.append(value)
            return top_response()

    port = Port()
    session = trends.GroupKeywordSession(port, run_id="R1", settings_version="S1")
    assert session.fetch("電子レンジ", snapshot_id=SNAPSHOT).status == "observed"
    assert port.requests[0].range == "3m"
    with pytest.raises(trends.IdempotencyConflict):
        session.fetch("電子レンジ", snapshot_id="NEW")
    assert len(port.requests) == 1


def test_group_session_source_failure_stops_other_themes_without_fallback():
    class Port:
        def __init__(self):
            self.calls = 0

        def fetch(self, request):
            self.calls += 1
            raise TimeoutError("synthetic")

    port = Port()
    session = trends.GroupKeywordSession(port, run_id="R1", settings_version="S1")
    with pytest.raises(trends.KeywordAcquisitionStopped):
        session.fetch("電子レンジ", snapshot_id=SNAPSHOT)
    with pytest.raises(trends.KeywordAcquisitionStopped):
        session.fetch("他テーマ", snapshot_id="NEW")
    assert port.calls == 1


@pytest.mark.parametrize("fault", ["word_type", "period", "saved_seed"])
def test_corrupt_snapshot_cannot_be_treated_as_a_resumable_draw(legacy_v2, fault):
    initialize_database(legacy_v2)
    data = snapshot()
    if fault == "word_type":
        with pytest.raises(trends.TrendsContractError):
            trends.grouped_key_input(replace(data, words=(None,)))
    elif fault == "period":
        with pytest.raises(trends.TrendsContractError):
            trends.grouped_key_input(replace(data, ui_period_label="過去7日間"))
    else:
        decisions = trends.validate_grouped_key_response(data, grouped_response(data))
        trends.save_grouped_snapshot(
            legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
        )
        meta = json.loads(
            legacy_v2.execute(
                "SELECT filter_decisions_json FROM keyword_snapshot WHERE snapshot_id=?",
                (SNAPSHOT,),
            ).fetchone()[0]
        )
        meta[0]["seed"] = "WRONG"
        legacy_v2.execute(
            "UPDATE keyword_snapshot SET filter_decisions_json=? WHERE snapshot_id=?",
            (json.dumps(meta), SNAPSHOT),
        )
        with pytest.raises(trends.TrendsContractError):
            trends.load_grouped_snapshot(legacy_v2, SNAPSHOT, request=request())


def test_group_save_in_callers_transaction_preserves_other_work_on_failure(legacy_v2):
    initialize_database(legacy_v2)
    data = snapshot()
    decisions = trends.validate_grouped_key_response(data, grouped_response(data))
    legacy_v2.execute("BEGIN IMMEDIATE")
    legacy_v2.execute("UPDATE run SET checkpoint='CALLER' WHERE run_id='R1'")
    trends.save_grouped_snapshot(legacy_v2, data)
    assert legacy_v2.in_transaction
    legacy_v2.execute(
        "CREATE TRIGGER stop_nested BEFORE UPDATE ON keyword_snapshot BEGIN SELECT RAISE(ABORT,'nested save failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        trends.save_grouped_snapshot(
            legacy_v2, data, decisions=decisions, seed="SAVED-SEED"
        )
    assert legacy_v2.in_transaction
    assert (
        legacy_v2.execute("SELECT checkpoint FROM run WHERE run_id='R1'").fetchone()[0]
        == "CALLER"
    )
    assert (
        trends.load_grouped_snapshot(legacy_v2, SNAPSHOT, request=request()).decisions
        is None
    )
    legacy_v2.rollback()
    assert trends.load_grouped_snapshot(legacy_v2, SNAPSHOT, request=request()) is None


@pytest.mark.parametrize(
    "fault",
    [
        "new_snapshot",
        "changed_raw",
        "inactive_run",
        "wrong_seed",
        "foreign_settings",
        "bad_payload",
        "unknown_merge",
        "late_anchor",
    ],
)
def test_saved_grouping_refuses_replacement_or_invalid_adoption(legacy_v2, fault):
    initialize_database(legacy_v2)
    data = snapshot()
    trends.save_grouped_snapshot(legacy_v2, data)
    output = grouped_response(data)
    if fault == "bad_payload":
        output["decisions"][0].pop("reason")
    elif fault == "unknown_merge":
        output["decisions"][3]["decision"] = "unknown"
    elif fault == "late_anchor":
        for item in output["decisions"][3:6]:
            item.update(
                group_ref="W6",
                decision="candidate" if item["word_id"] == "W6" else "duplicate_member",
            )
    if fault in {"bad_payload", "unknown_merge", "late_anchor"}:
        with pytest.raises(trends.TrendsContractError):
            trends.validate_grouped_key_response(data, output)
        return
    decisions = trends.validate_grouped_key_response(data, output)
    if fault == "new_snapshot":
        other = replace(
            data,
            snapshot_id="KW-OTHER",
            request=replace(request(), request_id="KW-OTHER"),
        )
        with pytest.raises(trends.IdempotencyConflict):
            trends.save_grouped_snapshot(legacy_v2, other)
    elif fault == "changed_raw":
        other_response = top_response()
        other_response["words"][4]["raw"] = "変更された語"
        other = trends.parse_grouped_top_response(other_response, request())
        with pytest.raises(trends.IdempotencyConflict):
            trends.save_grouped_snapshot(legacy_v2, other)
    else:
        if fault == "inactive_run":
            legacy_v2.execute("UPDATE run SET status='complete' WHERE run_id='R1'")
        elif fault == "foreign_settings":
            data = replace(data, request=replace(request(), settings_version="OTHER"))
        with pytest.raises(trends.TrendsContractError):
            trends.save_grouped_snapshot(
                legacy_v2,
                data,
                decisions=decisions,
                seed="WRONG" if fault == "wrong_seed" else "SAVED-SEED",
            )


@pytest.mark.parametrize(
    "fault",
    ["input_id", "duplicate_id", "unhashable_id", "snapshot", "query", "decision_id"],
)
def test_native_key_contract_binds_batch_input_and_output(fault):
    from automation.inago.ai_runner import AIJobRunner
    from tests_inago.test_step08 import _base_request

    job = _base_request()
    data = {
        "snapshot_id": "KEYWORDS-01",
        "query_word": "テーマ",
        "decisions": [
            {
                "word_id": "WORD-01",
                "decision": "candidate",
                "group_ref": "WORD-01",
                "reason": "fixture",
            }
        ],
    }
    if fault == "input_id":
        job.input_payload["keywords"]["words"][0]["word_id"] = "OTHER"
    elif fault == "duplicate_id":
        job.input_payload["word_ids"] = ["WORD-01", "WORD-01"]
    elif fault == "unhashable_id":
        job.input_payload["word_ids"] = [["WORD-01"]]
    elif fault == "snapshot":
        data["snapshot_id"] = "OTHER"
    elif fault == "query":
        data["query_word"] = "OTHER"
    else:
        data["decisions"][0]["word_id"] = "OTHER"
    assert not AIJobRunner._validate_role_payload(job, "AG-KEY", data, set())


@pytest.mark.parametrize("scope", ["3m", "90d", "7d"])
def test_node_current_top_contract_preserves_original_twenty(scope):
    payload = top_response(25)
    payload["range"] = scope
    module = (
        Path(__file__).resolve().parents[1]
        / "automation/inago/adapters/browser_contract.mjs"
    ).as_uri()
    script = f"import {{validateTrendsGroupedSnapshot}} from {json.dumps(module)}; let text=''; for await (const part of process.stdin) text+=part; console.log(JSON.stringify(validateTrendsGroupedSnapshot(JSON.parse(text), {{requestId:'{SNAPSHOT}',runId:'R1',settingsVersion:'S1',parentWord:'電子レンジ'}})));"
    node = shutil.which("node")
    assert node, "required Node runtime is absent"
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == ("observed" if scope == "3m" else "invalid")
    assert len(output["words"]) == (20 if scope == "3m" else 0)
