"""Sequential theme search, unlimited numeric count and Q1.4 resume contracts."""

from dataclasses import replace
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from automation.inago import orchestrator as daily
from automation.inago import trends_adapter as trends
from automation.inago.retention import RetentionError
from tests_inago.test_step13 import (
    runtime_case,
    _make_ports,
    _run,
    FakeRoomPort,
    RUN_ID,
)


from tests_inago.fixtures_q14 import CurrentPlanner


def test_scheduled_daily_applies_raw_retention_before_processing(runtime_case):
    observation_root = runtime_case.data_root / "observations"
    observation_root.mkdir(parents=True)
    expired = observation_root / "room-old-observation-1.json"
    expired.write_text(
        json.dumps(
            {
                "kind": "room",
                "retention_expires_at": "2026-10-12T12:59:59+09:00",
            }
        ),
        encoding="utf-8",
    )
    stop_record = observation_root / "access-stop-old.json"
    stop_record.write_text('{"run_id":"OLD","error_code":"http_403"}', encoding="utf-8")

    bundle = current_bundle(runtime_case)
    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete"
    assert not expired.exists()
    assert stop_record.is_file()


def test_scheduled_daily_stops_before_search_when_retention_cannot_clean(runtime_case):
    observation_root = runtime_case.data_root / "observations"
    broken_capture = observation_root / "trends-broken-observation-1.json"
    broken_capture.mkdir(parents=True)
    bundle = current_bundle(runtime_case)

    with pytest.raises(daily.DailyOrchestrationError, match="retention_cleanup_failed"):
        _run(runtime_case, bundle)

    assert bundle.room.requests == []
    assert (
        runtime_case.connection.execute(
            "SELECT 1 FROM run WHERE target_date=?", ("2026-10-12",)
        ).fetchone()
        is None
    )


class ObservedTrendsPort:
    fixture_only = True

    def __init__(self, clock, *, theme_by_word=None, failures=False):
        self.clock = clock
        self.theme_by_word = dict(theme_by_word or {})
        self.failures = failures
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        if self.failures:
            return {
                "request_id": request.request_id,
                "snapshot_id": request.request_id,
                "run_id": request.run_id,
                "settings_version": request.settings_version,
                "source": "google_trends_public_ui",
                "parent_word": request.parent_word,
                "current_parent": None,
                "refresh_complete": False,
                "language": "ja",
                "geo": "JP",
                "search_type": "web",
                "range": "3m",
                "list_type": "Top",
                "ui_period_label": None,
                "status": "unavailable",
                "fetched_at": None,
                "error_code": "source_timeout",
                "words": [],
            }
        return {
            "request_id": request.request_id,
            "snapshot_id": request.request_id,
            "run_id": request.run_id,
            "settings_version": request.settings_version,
            "source": "google_trends_public_ui",
            "parent_word": request.parent_word,
            "current_parent": request.parent_word,
            "refresh_complete": True,
            "language": "ja",
            "geo": "JP",
            "search_type": "web",
            "range": "3m",
            "list_type": "Top",
            "ui_period_label": "過去3か月間",
            "status": "observed",
            "fetched_at": self.clock.now_jst().isoformat(),
            "error_code": None,
            "words": [
                {
                    "word_id": f"{request.request_id}:w{rank}",
                    "raw": (
                        f"related-{rank}::{self.theme_by_word[request.parent_word]}"
                    ),
                    "source_rank": rank,
                    "popularity_index": 100 - rank,
                }
                for rank in range(1, 21)
            ],
        }


class FixtureKeywordRoot:
    fixture_only = True

    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        keywords = request.input_payload["keywords"]
        payload = {
            "snapshot_id": keywords["snapshot_id"],
            "query_word": keywords["query_word"],
            "decisions": [
                {
                    "word_id": word["word_id"],
                    "decision": "candidate",
                    "group_ref": word["word_id"],
                    "reason": "distinct fixture keyword",
                }
                for word in keywords["words"]
            ],
        }
        child = SimpleNamespace(
            agent_id="AG-KEY",
            task_id="T02",
            result_status="success",
            payload=payload,
        )
        return SimpleNamespace(
            result_status="success",
            adoption_status="accepted",
            subjobs=(child,),
        )


class PendingArtifactKeywordRoot:
    """Model the production runner's successful-but-not-yet-adopted result."""

    def __init__(self, data_root, clock):
        self.data_root = data_root
        self.clock = clock
        self.requests = []
        self.results = []

    def run(self, request):
        from automation.inago.ai_runner import compute_input_hash
        from automation.inago.artifact_io import write_temp_response_bytes
        from automation.inago.contracts import AIJobResult, AISubjobResult
        from automation.inago.retention import retention_expiry

        self.requests.append(request)
        key_result = FixtureKeywordRoot().run(request).subjobs[0]
        now = self.clock.now_jst().isoformat()
        artifact = write_temp_response_bytes(
            data_root=self.data_root,
            artifact_ref=f"{request.job_id}-a{request.attempt}-response",
            response=json.dumps({"result": "fixture"}).encode("utf-8"),
            source_kind="daily",
            created_at=now,
            retention_expires_at=retention_expiry("raw", now).isoformat(),
        )
        root_payload = {
            "summary": "Keyword filtering completed.",
            "details": [],
            "references": list(request.input_refs),
        }
        root_input_hash = compute_input_hash(request.input_payload)
        child_input_hash = compute_input_hash(
            {
                "parent_input_hash": root_input_hash,
                "run_id": request.run_id,
                "target_date": request.target_date,
                "settings_version": request.settings_version,
                "deadline_at": request.deadline_at,
                "attempt": request.attempt,
                "task_id": "T02",
                "agent_id": "AG-KEY",
                "skill_name": "rr-inago-keywords",
                "input_refs": list(request.input_refs),
                "evidence_refs": [],
                "target": {"snapshot_id": key_result.payload["snapshot_id"]},
            }
        )
        child = AISubjobResult(
            job_id=f"{request.job_id}-a{request.attempt}-child-01",
            parent_job_id=request.job_id,
            run_id=request.run_id,
            attempt=request.attempt,
            task_id="T02",
            agent_id="AG-KEY",
            skill_name="rr-inago-keywords",
            target_date=request.target_date,
            settings_version=request.settings_version,
            input_refs=request.input_refs,
            evidence_refs=(),
            input_hash=child_input_hash,
            deadline_at=request.deadline_at,
            result_status="success",
            payload=dict(key_result.payload),
            resolved_model="fixture-model",
            reasoning_setting="xhigh",
            auth_mode="chatgpt",
        )
        result = AIJobResult(
            job_id=request.job_id,
            run_id=request.run_id,
            attempt=request.attempt,
            task_id=request.task_id,
            agent_id="AI-O",
            skill_name="rakuten-room-inago",
            target_date=request.target_date,
            settings_version=request.settings_version,
            input_refs=request.input_refs,
            evidence_refs=request.evidence_refs,
            input_hash=root_input_hash,
            deadline_at=request.deadline_at,
            result_status="success",
            adoption_status="pending",
            payload=root_payload,
            error=None,
            artifact=artifact,
            subjobs=(child,),
            resolved_model="fixture-root-model",
            reasoning_setting="xhigh",
            auth_mode="chatgpt",
            process_returncode=0,
            started_at=now,
            finished_at=now,
        )
        self.results.append(result)
        return result


class LateFixtureKeywordRoot(FixtureKeywordRoot):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def run(self, request):
        result = super().run(request)
        if len(self.requests) == 1:
            self.clock.advance(5 * 60 * 60)
        return result


class CurrentRoom(FakeRoomPort):
    def __init__(self, clock, *, counts=(5,), fail=False, failed_product_rows=0):
        super().__init__(clock)
        self.counts = counts
        self.per_theme = {}
        self.fail = fail
        self.failed_product_rows = failed_product_rows

    def search(self, request):
        theme = request.parent_refs[0]
        index = self.per_theme.get(theme, 0)
        self.per_theme[theme] = index + 1
        self.posts_per_search = self.counts[index % len(self.counts)]
        self.status = "unavailable" if self.fail else "observed"
        result = super().search(request)
        result.update(
            query_kind=request.query_kind,
            keyword_snapshot_ref=request.keyword_snapshot_ref,
            selected_word_ref=request.selected_word_ref,
            loaded_posts=len(result["posts"]),
            condition_evidence_ref=(
                f"COND-{request.attempt_no}" if not self.fail else None
            ),
        )
        for number, item in enumerate(result["posts"], index * 10 + 1):
            item_id = f"item-{number:02}"
            item.update(
                likes=None,
                item_url=f"https://item.rakuten.co.jp/shop-{theme.lower()}/{item_id}/",
                observed_item_id=item_id,
            )
        for item in result["posts"][: self.failed_product_rows]:
            item.update(
                item_url=None,
                observed_shop_id=None,
                observed_item_id=None,
                availability="unknown",
            )
        return result


class QueryAgnosticCurrentRoom(CurrentRoom):
    """Exercise the daily planner with arbitrary real keyword expressions."""

    def search(self, request):
        theme_id = request.parent_refs[0]
        if self.before_search is not None:
            self.before_search(request)
        callback = self.before_search
        self.before_search = None
        try:
            result = super().search(replace(request, query=f"fixture::{theme_id}"))
        finally:
            self.before_search = callback
        self.requests[-1] = request
        result["query"] = request.query
        return result


def current_bundle(
    case,
    *,
    words=5,
    counts=(5,),
    keyword_fail_at=None,
    room_fail=False,
    failed_product_rows=0,
):
    bundle = _make_ports(case)
    planner = CurrentPlanner(case, word_count=words, fail_at=keyword_fail_at)
    room = CurrentRoom(
        case.clock,
        counts=counts,
        fail=room_fail,
        failed_product_rows=failed_product_rows,
    )
    bundle.planner = planner
    bundle.room = room
    bundle.ports = replace(bundle.ports, theme_planner=planner, room_port=room)
    return bundle


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_scheduled_daily_validates_lexical_data_root_before_resolving(
    runtime_case, tmp_path
):
    bundle = current_bundle(runtime_case)
    configured_root = tmp_path / "configured-data"
    junction = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(configured_root),
            str(runtime_case.data_root),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if junction.returncode != 0:
        pytest.skip(f"could not create a fixture junction: {junction.stderr}")

    try:
        with pytest.raises(RetentionError, match="data root contains"):
            daily.run_daily(
                runtime_case.connection,
                data_root=configured_root,
                ports=bundle.ports,
                clock=runtime_case.clock,
            )
        assert bundle.room.requests == []
    finally:
        configured_root.rmdir()


def _theme_words_for_bundle(case, bundle):
    version = bundle.ports.current_settings_version(case.connection)
    themes = case.connection.execute(
        "SELECT themes_json FROM settings_version WHERE version=?", (version,)
    ).fetchone()[0]
    return {item["name"]: item["theme_id"] for item in json.loads(themes)}


def test_first_related_word_fills_each_theme_and_does_not_prefetch_next_theme(
    runtime_case,
):
    bundle = current_bundle(runtime_case)

    def before_search(request):
        assert len(bundle.planner.calls) == len(bundle.room.requests)

    bundle.room.before_search = before_search
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "complete" and outcome.committed_count == 15
    assert outcome.attempt_used == 3 and len(bundle.room.requests) == 3
    assert all(item.query_kind == "related" for item in bundle.room.requests)
    assert len(bundle.planner.calls) == 3
    assert bundle.metrics.started_at == ["2026-10-12T13:00:00+09:00"]


def test_short_room_batches_and_ten_unresolved_products_continue_daily_search(
    runtime_case,
):
    bundle = current_bundle(runtime_case, counts=(15,), failed_product_rows=10)

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete" and outcome.committed_count == 15
    assert len(bundle.room.requests) == 3
    assert len({request.parent_refs[0] for request in bundle.room.requests}) == 3
    attempts = runtime_case.connection.execute(
        "SELECT source_status,observed_posts,loaded_posts,error_code "
        "FROM search_attempt WHERE run_id=? ORDER BY attempt_no",
        (RUN_ID,),
    ).fetchall()
    assert [tuple(row) for row in attempts] == [
        ("observed", 15, 15, None),
        ("observed", 15, 15, None),
        ("observed", 15, 15, None),
    ]


def test_durable_trends_and_same_root_key_job_feed_the_daily_theme_plan(runtime_case):
    from automation.inago.daily_theme_planner import DurableTrendsThemePlanner

    bundle = current_bundle(runtime_case)
    trends_port = ObservedTrendsPort(
        runtime_case.clock,
        theme_by_word=_theme_words_for_bundle(runtime_case, bundle),
    )
    key_root = FixtureKeywordRoot()
    planner = DurableTrendsThemePlanner(trends_port, key_root, runtime_case.clock)
    room = QueryAgnosticCurrentRoom(runtime_case.clock)
    bundle.room = room
    bundle.ports = replace(bundle.ports, theme_planner=planner, room_port=room)

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete" and outcome.committed_count == 15
    assert len(trends_port.requests) == 3
    assert len(key_root.requests) == 3
    assert all(request.task_id == "T02" for request in key_root.requests)
    assert all(request.trigger == "scheduled_daily" for request in key_root.requests)
    acquisitions = runtime_case.connection.execute(
        "SELECT status,attempts_used,snapshot_id FROM trends_acquisition "
        "WHERE run_id=? ORDER BY event_kind,ordinal",
        (RUN_ID,),
    ).fetchall()
    assert len(acquisitions) == 3
    assert all(row[0] == "observed" and row[1] == 1 and row[2] for row in acquisitions)
    attempts = runtime_case.connection.execute(
        "SELECT status FROM trends_acquisition_attempt WHERE acquisition_id IN "
        "(SELECT acquisition_id FROM trends_acquisition WHERE run_id=?)",
        (RUN_ID,),
    ).fetchall()
    assert len(attempts) == 3 and all(row[0] == "response_ready" for row in attempts)
    assert len(bundle.room.requests) == 3
    assert all(request.query_kind == "related" for request in bundle.room.requests)


def test_pending_successful_key_result_is_atomically_adopted_with_its_snapshot(
    runtime_case,
):
    from automation.inago.daily_theme_planner import DurableTrendsThemePlanner

    bundle = current_bundle(runtime_case)
    trends_port = ObservedTrendsPort(
        runtime_case.clock,
        theme_by_word=_theme_words_for_bundle(runtime_case, bundle),
    )
    key_root = PendingArtifactKeywordRoot(runtime_case.data_root, runtime_case.clock)
    from automation.inago.artifact_io import write_temp_response_bytes
    from automation.inago.retention import retention_expiry

    old_response_created_at = runtime_case.clock.now_jst().isoformat()
    write_temp_response_bytes(
        data_root=runtime_case.data_root,
        artifact_ref="KEY-20261012-I-01-a1-response",
        response=b'{"previous":"response"}',
        source_kind="daily",
        created_at=old_response_created_at,
        retention_expires_at=retention_expiry(
            "raw", old_response_created_at
        ).isoformat(),
    )
    planner = DurableTrendsThemePlanner(trends_port, key_root, runtime_case.clock)
    room = QueryAgnosticCurrentRoom(runtime_case.clock)
    bundle.room = room
    bundle.ports = replace(bundle.ports, theme_planner=planner, room_port=room)

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete" and outcome.committed_count == 15
    assert len(key_root.requests) == 3
    assert key_root.requests[0].attempt == 2
    assert key_root.requests[0].job_id == "KEY-20261012-I-01-r02"
    jobs = runtime_case.connection.execute(
        "SELECT task_id,agent_id,result_status,adoption_status,accepted_at,artifact_ref "
        "FROM ai_job WHERE run_id=? AND job_id LIKE 'KEY-%' ORDER BY agent_id,job_id",
        (RUN_ID,),
    ).fetchall()
    assert len(jobs) == 6
    assert all(row[0] == "T02" for row in jobs)
    assert all(row[2:4] == ("success", "accepted") for row in jobs)
    assert all(row[4] is not None and row[5] for row in jobs)
    assert sum(row[1] == "AI-O" for row in jobs) == 3
    assert sum(row[1] == "AG-KEY" for row in jobs) == 3
    assert (
        runtime_case.connection.execute(
            "SELECT count(*) FROM artifact WHERE artifact_ref LIKE 'KEY-%' "
            "AND source_kind='daily'"
        ).fetchone()[0]
        == 3
    )
    snapshots = runtime_case.connection.execute(
        "SELECT snapshot_id,filter_decisions_json FROM keyword_snapshot "
        "WHERE filter_decisions_json LIKE ?",
        (f"%{RUN_ID}%",),
    ).fetchall()
    assert len(snapshots) == 3
    assert all('"classification_complete":true' in row[1] for row in snapshots)


def test_key_result_persistence_failure_rolls_back_grouping_and_job_acceptance(
    runtime_case, monkeypatch
):
    from automation.inago.daily_theme_planner import DurableTrendsThemePlanner
    from automation.inago import daily_theme_planner as planner_module

    bundle = current_bundle(runtime_case)
    trends_port = ObservedTrendsPort(
        runtime_case.clock,
        theme_by_word=_theme_words_for_bundle(runtime_case, bundle),
    )
    key_root = PendingArtifactKeywordRoot(runtime_case.data_root, runtime_case.clock)
    planner = DurableTrendsThemePlanner(trends_port, key_root, runtime_case.clock)
    room = QueryAgnosticCurrentRoom(runtime_case.clock)
    bundle.room = room
    bundle.ports = replace(bundle.ports, theme_planner=planner, room_port=room)

    def reject_adoption(*_args, **_kwargs):
        raise RuntimeError("injected KEY adoption failure")

    monkeypatch.setattr(
        planner_module, "accept_pending_key_job_result", reject_adoption
    )
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "failed"

    snapshot_id, decisions_json = runtime_case.connection.execute(
        "SELECT snapshot_id,filter_decisions_json FROM keyword_snapshot "
        "WHERE filter_decisions_json LIKE ?",
        (f"%{RUN_ID}%",),
    ).fetchone()
    request = trends_port.requests[0]
    stored = trends.load_grouped_snapshot(
        runtime_case.connection,
        snapshot_id,
        request=trends.TrendsRequest(
            snapshot_id,
            RUN_ID,
            bundle.ports.current_settings_version(runtime_case.connection),
            request.parent_word,
            range="3m",
        ),
    )
    assert stored is not None and stored.decisions is None and stored.seed is None
    assert '"classification_complete":false' in decisions_json
    assert (
        runtime_case.connection.execute(
            "SELECT count(*) FROM ai_job WHERE job_id LIKE 'KEY-%' "
            "AND adoption_status='pending'"
        ).fetchone()[0]
        == 2
    )
    assert (
        runtime_case.connection.execute(
            "SELECT count(*) FROM ai_job WHERE job_id LIKE 'KEY-%' "
            "AND adoption_status='accepted'"
        ).fetchone()[0]
        == 0
    )
    assert (
        runtime_case.connection.execute(
            "SELECT count(*) FROM artifact WHERE artifact_ref LIKE 'KEY-%'"
        ).fetchone()[0]
        == 1
    )


def test_pending_key_result_is_reused_after_daily_process_interruption(
    runtime_case, monkeypatch
):
    from automation.inago.daily_theme_planner import DurableTrendsThemePlanner

    first_bundle = current_bundle(runtime_case)
    trends_port = ObservedTrendsPort(
        runtime_case.clock,
        theme_by_word=_theme_words_for_bundle(runtime_case, first_bundle),
    )
    first_root = PendingArtifactKeywordRoot(runtime_case.data_root, runtime_case.clock)
    first_planner = DurableTrendsThemePlanner(
        trends_port, first_root, runtime_case.clock
    )
    room = QueryAgnosticCurrentRoom(runtime_case.clock)
    first_bundle.room = room
    first_bundle.ports = replace(
        first_bundle.ports, theme_planner=first_planner, room_port=room
    )

    def interrupt_after_pending(*_args, **_kwargs):
        raise RuntimeError("injected interruption after pending save")

    monkeypatch.setattr(
        first_planner, "_save_grouping_with_key_result", interrupt_after_pending
    )
    first_outcome = _run(runtime_case, first_bundle)

    assert first_outcome.status == "failed"
    pending_rows = runtime_case.connection.execute(
        "SELECT count(*) FROM ai_job WHERE run_id=? AND job_id LIKE 'KEY-%' "
        "AND adoption_status='pending'",
        (RUN_ID,),
    ).fetchone()[0]
    assert pending_rows == 2

    class ResumePendingThenRunOtherThemes:
        fixture_only = False

        def __init__(self, data_root, clock):
            self.data_root = data_root
            self.delegate = PendingArtifactKeywordRoot(data_root, clock)

        @property
        def requests(self):
            return self.delegate.requests

        def run(self, request):
            return self.delegate.run(request)

    second_root = ResumePendingThenRunOtherThemes(
        runtime_case.data_root, runtime_case.clock
    )
    second_planner = DurableTrendsThemePlanner(
        trends_port, second_root, runtime_case.clock
    )
    first_bundle.ports = replace(first_bundle.ports, theme_planner=second_planner)
    second_outcome = _run(runtime_case, first_bundle)

    assert second_outcome.status == "complete", second_outcome
    assert second_outcome.committed_count == 15
    assert len(second_root.requests) == 2
    assert all(
        request.input_refs[0]
        != runtime_case.connection.execute(
            "SELECT snapshot_id FROM trends_acquisition WHERE run_id=? "
            "AND event_kind='initial' AND ordinal=1",
            (RUN_ID,),
        ).fetchone()[0]
        for request in second_root.requests
    )
    assert (
        runtime_case.connection.execute(
            "SELECT count(*) FROM ai_job WHERE run_id=? AND job_id LIKE 'KEY-%' "
            "AND adoption_status='accepted'",
            (RUN_ID,),
        ).fetchone()[0]
        == 6
    )


def test_successful_key_result_is_recovered_if_process_stops_before_db_index(
    runtime_case, monkeypatch
):
    from automation.inago.daily_theme_planner import DurableTrendsThemePlanner
    from automation.inago import daily_theme_planner as planner_module

    first_bundle = current_bundle(runtime_case)
    trends_port = ObservedTrendsPort(
        runtime_case.clock,
        theme_by_word=_theme_words_for_bundle(runtime_case, first_bundle),
    )
    first_root = PendingArtifactKeywordRoot(runtime_case.data_root, runtime_case.clock)
    first_planner = DurableTrendsThemePlanner(
        trends_port, first_root, runtime_case.clock
    )
    room = QueryAgnosticCurrentRoom(runtime_case.clock)
    first_bundle.room = room
    first_bundle.ports = replace(
        first_bundle.ports, theme_planner=first_planner, room_port=room
    )

    def interrupt_before_index(*_args, **_kwargs):
        raise RuntimeError("injected stop after AI runner returned")

    monkeypatch.setattr(
        planner_module, "persist_pending_key_job_result", interrupt_before_index
    )
    first_outcome = _run(runtime_case, first_bundle)

    assert first_outcome.status == "failed"
    assert len(first_root.results) == 1
    original_result = first_root.results[0]
    assert (
        runtime_case.connection.execute(
            "SELECT count(*) FROM ai_job WHERE run_id=? AND job_id LIKE 'KEY-%'",
            (RUN_ID,),
        ).fetchone()[0]
        == 0
    )
    monkeypatch.undo()

    class RecoveringKeywordRoot:
        fixture_only = False

        def __init__(self, data_root, recovered):
            self.data_root = data_root
            self.recovered = recovered
            self.recovery_requests = []
            self.requests = []
            self.delegate = PendingArtifactKeywordRoot(data_root, runtime_case.clock)

        def recover_successful_result(self, request):
            self.recovery_requests.append(request)
            if (
                request.job_id == self.recovered.job_id
                and request.attempt == self.recovered.attempt
            ):
                return self.recovered
            return None

        def run(self, request):
            self.requests.append(request)
            return self.delegate.run(request)

    recovering_root = RecoveringKeywordRoot(runtime_case.data_root, original_result)
    resumed_planner = DurableTrendsThemePlanner(
        trends_port, recovering_root, runtime_case.clock
    )
    first_bundle.ports = replace(first_bundle.ports, theme_planner=resumed_planner)

    resumed_outcome = _run(runtime_case, first_bundle)

    assert resumed_outcome.status == "complete", resumed_outcome
    assert resumed_outcome.committed_count == 15
    assert len(recovering_root.recovery_requests) == 1
    assert recovering_root.recovery_requests[0].attempt == 1
    assert len(recovering_root.requests) == 2
    assert all(request.attempt == 1 for request in recovering_root.requests)
    assert (
        runtime_case.connection.execute(
            "SELECT count(*) FROM ai_job WHERE run_id=? AND job_id LIKE 'KEY-%' "
            "AND adoption_status='accepted'",
            (RUN_ID,),
        ).fetchone()[0]
        == 6
    )


def test_exhausted_trends_uses_only_the_exact_theme_word_in_daily_room_search(
    runtime_case,
):
    from automation.inago.daily_theme_planner import DurableTrendsThemePlanner

    bundle = current_bundle(runtime_case)
    trends_port = ObservedTrendsPort(runtime_case.clock, failures=True)
    key_root = FixtureKeywordRoot()
    planner = DurableTrendsThemePlanner(trends_port, key_root, runtime_case.clock)
    room = QueryAgnosticCurrentRoom(runtime_case.clock)
    bundle.room = room
    bundle.ports = replace(bundle.ports, theme_planner=planner, room_port=room)

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete" and outcome.committed_count == 15
    assert (
        len(trends_port.requests) == 3 * trends.DurableGroupKeywordSession.MAX_ATTEMPTS
    )
    assert key_root.requests == []
    acquisitions = runtime_case.connection.execute(
        "SELECT theme_id,status,attempts_used,snapshot_id FROM trends_acquisition "
        "WHERE run_id=? ORDER BY ordinal",
        (RUN_ID,),
    ).fetchall()
    assert len(acquisitions) == 3
    assert all(row[1:] == ("exhausted", 5, None) for row in acquisitions)
    themes = json.loads(
        runtime_case.connection.execute(
            "SELECT themes_json FROM settings_version WHERE version=?",
            (bundle.ports.current_settings_version(runtime_case.connection),),
        ).fetchone()[0]
    )
    words = {item["theme_id"]: item["name"] for item in themes}
    assert [request.query for request in bundle.room.requests] == [
        words[row[0]] for row in acquisitions
    ]
    assert all(
        request.query_kind == "theme_fallback" for request in bundle.room.requests
    )


def test_late_key_result_after_1745_is_adopted_and_daily_continues(
    runtime_case,
):
    from automation.inago.daily_theme_planner import DurableTrendsThemePlanner

    bundle = current_bundle(runtime_case)
    theme_by_word = _theme_words_for_bundle(runtime_case, bundle)
    trends_port = ObservedTrendsPort(runtime_case.clock, theme_by_word=theme_by_word)
    key_root = LateFixtureKeywordRoot(runtime_case.clock)
    planner = DurableTrendsThemePlanner(trends_port, key_root, runtime_case.clock)
    room = QueryAgnosticCurrentRoom(runtime_case.clock)
    bundle.room = room
    bundle.ports = replace(bundle.ports, theme_planner=planner, room_port=room)

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert runtime_case.clock.now_jst().isoformat() >= "2026-10-12T18:00:00+09:00"
    assert len(trends_port.requests) == len(key_root.requests) == 3
    assert len(room.requests) == 3
    acquisition = runtime_case.connection.execute(
        "SELECT status,attempts_used,snapshot_id FROM trends_acquisition WHERE run_id=?",
        (RUN_ID,),
    ).fetchone()
    assert acquisition[0:2] == ("observed", 1)
    assert acquisition[2] is not None
    snapshot_id = trends_port.requests[0].request_id
    raw = trends.load_grouped_snapshot(
        runtime_case.connection,
        snapshot_id,
        request=trends.TrendsRequest(
            snapshot_id,
            RUN_ID,
            bundle.ports.current_settings_version(runtime_case.connection),
            trends_port.requests[0].parent_word,
            range="3m",
        ),
    )
    assert raw is not None and raw.decisions is not None and raw.selected


def test_each_theme_uses_related_queue_then_theme_fallback_before_next_theme(
    runtime_case,
):
    bundle = current_bundle(runtime_case, counts=(1, 0, 1, 0, 0, 1))
    outcome = _run(runtime_case, bundle)
    requests = bundle.room.requests
    assert outcome.committed_count == 15 and outcome.attempt_used > 9
    for group in range(3):
        six = requests[group * 6 : (group + 1) * 6]
        assert len({item.parent_refs[0] for item in six}) == 1
        assert [item.query_kind for item in six] == ["related"] * 5 + ["theme_fallback"]
    assert len({item.parent_refs[0] for item in requests}) >= 4
    assert [item.attempt_no for item in requests] == list(range(1, len(requests) + 1))


@pytest.mark.parametrize("words", [0, 3])
def test_fewer_groups_use_actual_queue_and_single_fallback_without_new_words(
    runtime_case, words
):
    bundle = current_bundle(runtime_case, words=words, counts=(0,))
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "shortfall" and outcome.committed_count == 0
    assert len(bundle.planner.calls) == 30
    assert len(set(bundle.planner.calls)) == 30
    assert outcome.attempt_used == 30 * (words + 1)
    assert outcome.attempt_used > 9
    assert len({item.query for item in bundle.room.requests}) == len(
        bundle.room.requests
    )


def test_keyword_failure_goes_to_metrics_without_fallback_or_next_theme(runtime_case):
    bundle = current_bundle(runtime_case, keyword_fail_at=1)
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "failed" and outcome.attempt_used == 0
    assert len(bundle.planner.calls) == 1 and bundle.room.requests == []
    assert bundle.metrics.calls


def test_room_failure_has_finite_retries_and_does_not_start_other_queries(runtime_case):
    bundle = current_bundle(runtime_case, room_fail=True)
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "failed" and outcome.attempt_used == 3
    assert len({item.query for item in bundle.room.requests}) == 1
    assert bundle.metrics.calls


def test_old_checkpoint_is_not_relabelled_as_current_search_rules(runtime_case):
    from automation.inago.orchestrator import _daily_state

    with pytest.raises(daily.DailyOrchestrationError, match="version"):
        _daily_state(
            json.dumps(
                {"daily_orchestration": {"schema_version": 1, "phase": "initial"}}
            )
        )


def test_v2_checkpoint_migrates_and_resumes_saved_plan_without_replanning(
    runtime_case, monkeypatch
):
    bundle = current_bundle(runtime_case)
    original_make_tasks = daily._make_tasks

    def interrupt_after_plan(*args, **kwargs):
        raise RuntimeError("fixture interruption after plan save")

    monkeypatch.setattr(daily, "_make_tasks", interrupt_after_plan)
    first = _run(runtime_case, bundle)
    assert first.attempt_used == 0 and len(bundle.planner.calls) == 1

    row = runtime_case.connection.execute(
        "SELECT checkpoint,seed FROM run WHERE run_id=?", (RUN_ID,)
    ).fetchone()
    root = json.loads(row["checkpoint"])
    state = root["daily_orchestration"]
    first_theme_id = state["initial_theme_ids"][0]
    saved_plan = state["plans"]["initial:01"]
    saved_plan.pop("acquisition_status")
    saved_plan.pop("acquisition_ref")
    saved_plan["rules_version"] = "requirements-1.4"
    state["schema_version"] = 2
    state["rules_version"] = "requirements-1.4"
    acquisition_id = f"ACQ-{RUN_ID}-initial-01"
    runtime_case.connection.execute(
        "DELETE FROM trends_acquisition_attempt WHERE acquisition_id=?",
        (acquisition_id,),
    )
    runtime_case.connection.execute(
        "DELETE FROM trends_acquisition WHERE acquisition_id=?", (acquisition_id,)
    )
    runtime_case.connection.execute(
        "UPDATE run SET checkpoint=? WHERE run_id=?",
        (json.dumps(root, ensure_ascii=False), RUN_ID),
    )

    monkeypatch.setattr(daily, "_make_tasks", original_make_tasks)
    resumed = _run(runtime_case, bundle)

    assert resumed.status == "complete" and resumed.committed_count == 15
    assert resumed.run_id == RUN_ID
    assert bundle.planner.calls.count(first_theme_id) == 1
    migrated = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert migrated["schema_version"] == 3
    assert migrated["rules_version"] == "requirements-1.5"
    assert migrated["migrated_from_schema_version"] == 2
    assert migrated["plans"]["initial:01"]["legacy_acquisition"] is True
    assert migrated["plans"]["initial:01"]["snapshot_ref"] == saved_plan["snapshot_ref"]
    assert (
        runtime_case.connection.execute(
            "SELECT seed FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == row["seed"]
    )


def test_new_checkpoint_saves_rules_raw_order_and_no_nine_limit(runtime_case):
    bundle = current_bundle(runtime_case)
    outcome = _run(runtime_case, bundle)
    state = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert state["schema_version"] == 3 and state["rules_version"] == "requirements-1.5"
    assert all(
        plan["rules_version"] == "requirements-1.5" for plan in state["plans"].values()
    )
    assert all(
        task["query_kind"] == "related" or task["query_kind"] == "theme_fallback"
        for task in state["tasks"]
    )
    assert len(state["offer_theme_ids"]) == 15
    assert outcome.committed_count == 15


def test_recovery_preserves_twelve_used_searches_and_permits_remaining_work():
    from automation.inago.recovery import plan_resume
    from tests_inago.test_step16 import _resume_snapshot, JST
    from datetime import datetime

    result = plan_resume(
        _resume_snapshot(search_attempts=12),
        now=datetime(2026, 10, 13, 13, 35, tzinfo=JST),
        database_integrity="ok",
        owner_confirmed=True,
        cause_resolved=True,
    )
    assert result["preserved"]["search_attempts"] == 12
    assert result["allowed"]["search"] is True


def test_initial_eleven_are_replenished_by_four_from_one_new_theme(runtime_case):
    bundle = current_bundle(runtime_case, counts=(1, 0, 1, 0, 0, 1))

    def before_search(request):
        themes = list(bundle.room.per_theme)
        if len(themes) == 1:
            bundle.room.posts_per_search = 5
        elif len(themes) == 4:
            bundle.room.posts_per_search = 4

    bundle.room.before_search = before_search
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "complete" and outcome.committed_count == 15
    assert bundle.planner.counts_before_plan == [0, 5, 8, 11]
    assert len(bundle.planner.calls) == 4
    assert outcome.attempt_used == 14
    state = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert state["theme_targets"][bundle.planner.calls[-1]] == 4


def test_fifty_source_rows_are_saved_but_only_five_per_theme_are_assigned(runtime_case):
    bundle = current_bundle(runtime_case, counts=(80,))
    outcome = _run(runtime_case, bundle)
    assert outcome.committed_count == 15 and outcome.attempt_used == 3
    assert len(bundle.runner.requests) == 15
    rows = runtime_case.connection.execute(
        "SELECT observed_posts,loaded_posts,end_reason FROM search_attempt WHERE run_id=? ORDER BY attempt_no",
        (RUN_ID,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [(50, 80, "limit_reached")] * 3
    state = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert len(state["offer_theme_ids"]) == 15


def test_old_persisted_run_is_rejected_before_mutating_its_status(runtime_case):
    bundle = current_bundle(runtime_case)
    _run(runtime_case, bundle)
    old = json.dumps({"daily_orchestration": {"schema_version": 1, "phase": "initial"}})
    runtime_case.connection.execute(
        "UPDATE run SET status='failed',checkpoint=? WHERE run_id=?", (old, RUN_ID)
    )
    before = tuple(
        runtime_case.connection.execute(
            "SELECT status,checkpoint,attempt_used,committed_count FROM run WHERE run_id=?",
            (RUN_ID,),
        ).fetchone()
    )
    with pytest.raises(daily.DailyOrchestrationError, match="version"):
        _run(runtime_case, bundle)
    after = tuple(
        runtime_case.connection.execute(
            "SELECT status,checkpoint,attempt_used,committed_count FROM run WHERE run_id=?",
            (RUN_ID,),
        ).fetchone()
    )
    assert after == before


def test_theme_plan_cannot_replace_saved_original_expression(runtime_case):
    bundle = current_bundle(runtime_case)
    original = bundle.planner.plan_theme

    def changed(connection, **values):
        plan = original(connection, **values)
        return replace(
            plan,
            related=(
                replace(plan.related[0], query="AIが作った未取得語"),
                *plan.related[1:],
            ),
        )

    bundle.planner.plan_theme = changed
    result = _run(runtime_case, bundle)
    assert result.status == "failed" and result.attempt_used == 0
    assert bundle.room.requests == [] and bundle.runner.requests == []


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "old_fields",
        "missing_fallback",
        "foreign_theme",
        "wrong_rules",
        "wrong_kind",
        "foreign_word",
        "wrong_order",
        "fallback_word",
        "duplicate_task",
        "bad_lists",
        "empty_query",
    ],
)
def test_resume_from_saved_plan_checks_contents_before_queueing(
    runtime_case, monkeypatch, fault
):
    bundle = current_bundle(runtime_case)
    original = daily._make_tasks

    def interrupt(*args, **kwargs):
        raise RuntimeError("fixture interruption after plan save")

    monkeypatch.setattr(daily, "_make_tasks", interrupt)
    first = _run(runtime_case, bundle)
    assert first.attempt_used == 0 and len(bundle.planner.calls) == 1
    root = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )
    plan = root["daily_orchestration"]["plans"]["initial:01"]
    if fault == "old_fields":
        plan["combined"] = None
    elif fault == "missing_fallback":
        del plan["fallback"]
    elif fault == "foreign_theme":
        plan["related"][0]["theme_id"] = "OTHER"
    elif fault == "wrong_rules":
        plan["rules_version"] = "requirements-1.3"
    elif fault == "wrong_kind":
        plan["related"][0]["kind"] = "parent"
    elif fault == "foreign_word":
        plan["related"][0]["selected_word_ref"] = "OTHER"
    elif fault == "wrong_order":
        plan["keyword_refs"].reverse()
    elif fault == "fallback_word":
        plan["fallback"]["selected_word_ref"] = "W1"
    elif fault == "duplicate_task":
        plan["related"][1]["task_id"] = plan["related"][0]["task_id"]
    elif fault == "bad_lists":
        plan["related"] = "invalid"
    elif fault == "empty_query":
        plan["related"][0]["query"] = ""
    runtime_case.connection.execute(
        "UPDATE run SET checkpoint=? WHERE run_id=?", (json.dumps(root), RUN_ID)
    )
    monkeypatch.setattr(daily, "_make_tasks", original)
    second = _run(runtime_case, bundle)
    if fault == "none":
        assert second.status == "complete" and second.committed_count == 15
        assert len(bundle.planner.calls) == 3
    else:
        assert second.status == "failed" and second.attempt_used == 0
        assert len(bundle.planner.calls) == 1 and bundle.room.requests == []
