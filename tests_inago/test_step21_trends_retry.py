"""Durable, bounded retry contracts for the STEP21.5 Trends source."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from automation.inago.clock import FakeClock
from automation.inago import repository, trends_adapter as trends
from automation.inago import orchestrator as daily
from automation.inago.retention import apply_retention
from tests_inago.test_step13 import SETTINGS_VERSION


NOW = "2026-10-14T13:00:00+09:00"
RUN_ID = "RETRY-RUN"
THEME_ID = "T01"
THEME_WORD = "電子レンジ"
ACQUISITION_ID = "RETRY-RUN:initial:01:T01"


@pytest.fixture
def retry_case(tmp_path):
    connection = sqlite3.connect(tmp_path / "retry.sqlite", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    repository.initialize_database(connection)
    connection.execute(
        "INSERT INTO settings_version (version,confirmed_at,genres_json,themes_json,"
        "pattern_refs_json,change_reason,created_at) VALUES (?, ?, '[]','[]',"
        "'[\"P01\"]',NULL,?)",
        (SETTINGS_VERSION, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO run (run_id,target_date,mode,status,settings_version,seed,"
        "started_at,draft_deadline,end_deadline,attempt_used,committed_count,"
        "checkpoint,metrics_status,finished_at) VALUES (?, '2026-10-14','daily',"
        "'running', ?, 'SEED-1', ?, NULL,NULL,0,0,NULL,'pending',NULL)",
        (RUN_ID, SETTINGS_VERSION, NOW),
    )
    connection.execute(
        "INSERT INTO theme_selection (selection_id,run_id,theme_id,event_kind,"
        "count_event_id,seed,result_word_refs_json,selected_at) VALUES (?, ?, ?,"
        "'initial', ?, 'SEED-1', '[]', ?)",
        (
            f"theme-selection:{RUN_ID}:initial:01",
            RUN_ID,
            THEME_ID,
            f"theme-selection:{RUN_ID}:initial:01",
            NOW,
        ),
    )
    clock = FakeClock(datetime.fromisoformat(NOW))
    try:
        yield connection, clock
    finally:
        connection.close()


def response_for(request, *, status="observed", error=None):
    if status == "unavailable":
        current_parent = None
        refresh_complete = False
        fetched_at = None
        words = []
    else:
        current_parent = request.parent_word
        refresh_complete = True
        fetched_at = request_id_time(request.request_id)
        words = [
            {
                "word_id": f"{request.request_id}-w1",
                "raw": "電子レンジ 調理",
                "source_rank": 1,
                "popularity_index": 75,
            }
        ]
    return {
        "request_id": request.request_id,
        "snapshot_id": request.request_id,
        "run_id": request.run_id,
        "settings_version": request.settings_version,
        "source": "google_trends_public_ui",
        "parent_word": request.parent_word,
        "current_parent": current_parent,
        "refresh_complete": refresh_complete,
        "language": "ja",
        "geo": "JP",
        "search_type": "web",
        "range": "3m",
        "list_type": "Top",
        "ui_period_label": "過去3か月間" if status == "observed" else None,
        "status": status,
        "fetched_at": fetched_at,
        "error_code": error,
        "words": words,
    }


def request_id_time(_request_id):
    return NOW


class SequencePort:
    def __init__(self, responses, saved_responses=None):
        self.responses = list(responses)
        self.requests = []
        self.saved_responses = dict(saved_responses or {})

    def fetch(self, request):
        self.requests.append(request)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(request)
        return value

    def load_saved_response(self, request_id):
        return self.saved_responses.get(request_id)


def session(connection, clock, port, *, deadline_at=None):
    return trends.DurableGroupKeywordSession(
        connection,
        port,
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed="SEED-1",
        deadline_at=deadline_at,
        acquisition_id=ACQUISITION_ID,
    )


def test_restarted_widget_acquisition_preserves_failed_attempt_and_resumes(retry_case):
    connection, clock = retry_case
    port = SequencePort(
        [response_for_dummy_unavailable("query_widget_contract_mismatch"), response_for]
    )
    with pytest.raises(trends.TrendsContractError):
        session(connection, clock, port).fetch(THEME_WORD)
    assert len(port.requests) == 1

    result = session(connection, clock, port).fetch(THEME_WORD)

    assert result.status == "response_ready"
    assert result.attempts_used == 2
    rows = attempts(connection)
    assert rows[0][2:4] == ("processing_error", "query_widget_contract_mismatch")
    assert rows[1][1:3] == (2, "response_ready")
    assert rows[1][4] == rows[0][0]


def test_widget_repair_then_exhaustion_allows_verified_theme_fallback(retry_case):
    connection, clock = retry_case
    port = SequencePort(
        [response_for_dummy_unavailable("query_widget_contract_mismatch")]
        + [response_for_dummy_unavailable("http_429")] * 4
    )
    with pytest.raises(trends.TrendsContractError):
        session(connection, clock, port).fetch(THEME_WORD)
    result = session(connection, clock, port).fetch(THEME_WORD)
    assert (result.status, result.attempts_used) == ("exhausted", 5)
    plan = daily.DailyThemePlan(
        theme_id=THEME_ID,
        keyword_refs=(),
        related=(),
        snapshot_ref=None,
        fallback=daily.DailySearchPlan(
            "fallback",
            THEME_ID,
            THEME_WORD,
            (THEME_ID,),
            "theme_fallback",
            "theme_fallback",
        ),
        acquisition_ref=ACQUISITION_ID,
        acquisition_status="exhausted",
    )
    daily._validate_plan_snapshot(
        connection,
        plan,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        seed="SEED-1",
        event_kind="initial",
        ordinal=1,
    )


@pytest.mark.parametrize("leading_symbol", ["_", "-"])
def test_urlsafe_run_seed_accepts_base64url_leading_symbols(
    retry_case, leading_symbol
):
    connection, clock = retry_case
    value = trends.DurableGroupKeywordSession(
        connection,
        SequencePort([]),
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed=f"{leading_symbol}{'A' * 31}",
        acquisition_id=ACQUISITION_ID,
    )

    assert value.seed.startswith(leading_symbol)


def test_run_seed_rejects_characters_outside_persisted_seed_contract(retry_case):
    connection, clock = retry_case

    with pytest.raises(trends.TrendsContractError, match="safe persisted seed"):
        trends.DurableGroupKeywordSession(
            connection,
            SequencePort([]),
            clock,
            run_id=RUN_ID,
            settings_version=SETTINGS_VERSION,
            event_kind="initial",
            ordinal=1,
            theme_id=THEME_ID,
            seed="invalid/seed",
            acquisition_id=ACQUISITION_ID,
        )


def attempts(connection):
    return connection.execute(
        "SELECT request_id,attempt_no,status,error_code,retry_of,next_retry_at,response_json "
        "FROM trends_acquisition_attempt WHERE acquisition_id=? ORDER BY attempt_no",
        (ACQUISITION_ID,),
    ).fetchall()


def test_retries_twice_then_returns_first_success_and_persists_each_attempt(retry_case):
    connection, clock = retry_case
    port = SequencePort(
        [
            response_for_dummy_unavailable("http_429"),
            response_for_dummy_unavailable("source_timeout"),
            response_for,
        ]
    )
    result = session(connection, clock, port).fetch(THEME_WORD)

    assert result.status == "response_ready"
    assert result.attempts_used == 3
    assert result.snapshot.status == "observed"
    assert [item.request_id for item in port.requests] == [
        f"{ACQUISITION_ID}-a1",
        f"{ACQUISITION_ID}-a2",
        f"{ACQUISITION_ID}-a3",
    ]
    saved = attempts(connection)
    assert [row[1] for row in saved] == [1, 2, 3]
    assert [row[2] for row in saved] == ["unavailable", "unavailable", "response_ready"]
    assert saved[1][4] == saved[0][0] and saved[2][4] == saved[1][0]
    assert saved[2][6] is not None
    assert clock.now_jst().isoformat() == "2026-10-14T13:02:00+09:00"


def test_daily_trends_request_has_no_run_wide_deadline(retry_case):
    connection, clock = retry_case
    port = SequencePort([response_for])

    session(connection, clock, port).fetch(THEME_WORD)

    assert port.requests[0].deadline_at is None


def test_retry_after_is_honored_when_longer_than_default(retry_case):
    connection, clock = retry_case

    def too_many_requests(request):
        payload = response_for(request, status="unavailable", error="http_429")
        payload["retry_after_seconds"] = 125
        return payload

    result = session(
        connection, clock, SequencePort([too_many_requests, response_for])
    ).fetch(THEME_WORD)

    assert result.status == "response_ready" and result.attempts_used == 2
    assert clock.now_jst().isoformat() == "2026-10-14T13:02:05+09:00"


def test_attempt_result_and_next_retry_state_are_committed_atomically(retry_case):
    connection, clock = retry_case
    current = session(connection, clock, SequencePort([]))
    current._ensure_acquisition(THEME_WORD)
    request = current._reserve_attempt(THEME_WORD, 1)
    connection.execute(
        "CREATE TRIGGER reject_retry_state BEFORE UPDATE ON trends_acquisition "
        "WHEN NEW.next_retry_at IS NOT NULL BEGIN "
        "SELECT RAISE(ABORT, 'injected transition failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected transition failure"):
        current._finish_attempt(
            request.request_id,
            "unavailable",
            "http_429",
            response_for(request, status="unavailable", error="http_429"),
            "2026-10-14T13:01:00+09:00",
            acquisition_status="collecting",
            acquisition_next_retry_at="2026-10-14T13:01:00+09:00",
            acquisition_error_code="http_429",
        )

    assert attempts(connection)[0][2] == "started"
    acquisition = connection.execute(
        "SELECT status,attempts_used,next_retry_at FROM trends_acquisition "
        "WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()
    assert tuple(acquisition) == ("collecting", 1, None)


def test_normal_zero_is_adopted_as_success_and_uses_theme_fallback(retry_case):
    connection, clock = retry_case

    def empty_success(request):
        payload = response_for(request)
        payload["words"] = []
        return payload

    port = SequencePort([empty_success])
    current = session(connection, clock, port)
    acquired = current.fetch(THEME_WORD)
    assert acquired.status == "response_ready"
    assert acquired.snapshot.status == "observed" and acquired.snapshot.words == ()

    trends.save_grouped_snapshot(
        connection, acquired.snapshot, decisions=(), seed="SEED-1"
    )
    current.adopt_snapshot(acquired.snapshot)
    resumed = current.fetch(THEME_WORD)
    plan = daily.DailyThemePlan(
        theme_id=THEME_ID,
        keyword_refs=(),
        related=(),
        fallback=daily.DailySearchPlan(
            "theme-fallback",
            THEME_ID,
            THEME_WORD,
            (THEME_ID,),
            "theme_fallback",
            "theme_fallback",
        ),
        snapshot_ref=acquired.snapshot.snapshot_id,
        acquisition_ref=ACQUISITION_ID,
    )
    daily._validate_plan(plan, THEME_ID)
    daily._validate_plan_snapshot(
        connection,
        plan,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        seed="SEED-1",
        event_kind="initial",
        ordinal=1,
    )
    tasks = daily._make_tasks([plan], phase="initial", first_number=1)

    assert resumed.status == "normal_zero" and resumed.snapshot.words == ()
    assert len(port.requests) == 1 and len(attempts(connection)) == 1
    assert tasks[0]["query"] == THEME_WORD
    assert tasks[0]["query_kind"] == "theme_fallback"


def test_observed_daily_plan_cannot_skip_durable_acquisition_adoption(retry_case):
    connection, clock = retry_case
    current = session(connection, clock, SequencePort([response_for]))
    acquired = current.fetch(THEME_WORD)
    decisions = trends.validate_grouped_key_response(
        acquired.snapshot,
        {
            "snapshot_id": acquired.snapshot.snapshot_id,
            "query_word": THEME_WORD,
            "decisions": [
                {
                    "word_id": word.word_id,
                    "decision": "candidate",
                    "group_ref": word.word_id,
                    "reason": "独立した候補",
                }
                for word in acquired.snapshot.words
            ],
        },
    )
    trends.save_grouped_snapshot(
        connection, acquired.snapshot, decisions=decisions, seed="SEED-1"
    )
    stored = trends.load_grouped_snapshot(
        connection,
        acquired.snapshot.snapshot_id,
        request=acquired.snapshot.request,
    )
    plan = daily.DailyThemePlan(
        theme_id=THEME_ID,
        keyword_refs=tuple(item.word_id for item in stored.selected),
        related=tuple(
            daily.DailySearchPlan(
                f"related-{index}",
                THEME_ID,
                item.raw,
                (THEME_ID,),
                "related",
                "related",
                acquired.snapshot.snapshot_id,
                item.word_id,
            )
            for index, item in enumerate(stored.selected, 1)
        ),
        fallback=daily.DailySearchPlan(
            "theme-fallback",
            THEME_ID,
            THEME_WORD,
            (THEME_ID,),
            "theme_fallback",
            "theme_fallback",
        ),
        snapshot_ref=acquired.snapshot.snapshot_id,
        acquisition_ref=ACQUISITION_ID,
    )

    without_acquisition = daily.replace(plan, acquisition_ref=None)
    with pytest.raises(
        daily.DailyOrchestrationError, match="theme_acquisition_state_not_adopted"
    ):
        daily._validate_plan_snapshot(
            connection,
            without_acquisition,
            run_id=RUN_ID,
            settings_version=SETTINGS_VERSION,
            seed="SEED-1",
            event_kind="initial",
            ordinal=1,
        )

    with pytest.raises(
        daily.DailyOrchestrationError, match="theme_acquisition_state_not_adopted"
    ):
        daily._validate_plan_snapshot(
            connection,
            plan,
            run_id=RUN_ID,
            settings_version=SETTINGS_VERSION,
            seed="SEED-1",
            event_kind="initial",
            ordinal=1,
        )


def response_for_dummy_unavailable(code):
    return lambda request: response_for(request, status="unavailable", error=code)


def test_five_failures_become_exhausted_without_inventing_a_snapshot(retry_case):
    connection, clock = retry_case
    port = SequencePort([response_for_dummy_unavailable("http_429") for _ in range(5)])
    result = session(connection, clock, port).fetch(THEME_WORD)

    assert result.status == "exhausted"
    assert result.attempts_used == 5
    assert result.snapshot is None
    assert len(port.requests) == 5
    assert connection.execute(
        "SELECT status,attempts_used,snapshot_id FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()[:] == ("exhausted", 5, None)
    assert [row[2] for row in attempts(connection)] == ["unavailable"] * 5


def test_exhausted_acquisition_produces_only_exact_theme_fallback(retry_case):
    connection, clock = retry_case
    acquired = session(
        connection,
        clock,
        SequencePort([response_for_dummy_unavailable("http_429") for _ in range(5)]),
    ).fetch(THEME_WORD)
    fallback = daily.DailySearchPlan(
        "theme-fallback-01",
        THEME_ID,
        THEME_WORD,
        (THEME_ID,),
        "theme_fallback",
        "theme_fallback",
    )
    plan = daily.DailyThemePlan(
        theme_id=THEME_ID,
        keyword_refs=(),
        related=(),
        fallback=fallback,
        snapshot_ref=None,
        acquisition_status="exhausted",
        acquisition_ref=ACQUISITION_ID,
    )

    daily._validate_plan(plan, THEME_ID)
    daily._validate_plan_snapshot(
        connection,
        plan,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        seed="SEED-1",
        event_kind="initial",
        ordinal=1,
    )
    tasks = daily._make_tasks([plan], phase="initial", first_number=1)

    assert acquired.status == "exhausted" and acquired.attempts_used == 5
    assert len(tasks) == 1 and tasks[0]["query"] == THEME_WORD
    assert tasks[0]["query_kind"] == "theme_fallback"
    assert tasks[0]["keyword_snapshot_ref"] is None
    assert tasks[0]["selected_word_ref"] is None
    assert plan.keyword_refs == plan.related == ()


def test_deadline_acquisition_is_saved_without_fallback_or_room_task(retry_case):
    connection, clock = retry_case
    clock.advance(30)
    acquired = session(
        connection,
        clock,
        SequencePort([response_for_dummy_unavailable("source_timeout")]),
        deadline_at="2026-10-14T13:01:00+09:00",
    ).fetch(THEME_WORD)
    plan = daily.DailyThemePlan(
        theme_id=THEME_ID,
        keyword_refs=(),
        related=(),
        fallback=None,
        snapshot_ref=None,
        acquisition_status="deadline",
        acquisition_ref=ACQUISITION_ID,
    )

    daily._validate_plan(plan, THEME_ID)
    daily._validate_plan_snapshot(
        connection,
        plan,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        seed="SEED-1",
        event_kind="initial",
        ordinal=1,
    )

    assert acquired.status == "deadline" and acquired.attempts_used == 1
    assert daily._make_tasks([plan], phase="initial", first_number=1) == []


def test_interrupted_started_attempt_is_counted_and_resume_does_not_reset_budget(
    retry_case,
):
    connection, clock = retry_case
    first = SequencePort([KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        session(connection, clock, first).fetch(THEME_WORD)

    resumed_port = SequencePort(
        [response_for_dummy_unavailable("source_timeout") for _ in range(4)]
    )
    result = session(connection, clock, resumed_port).fetch(THEME_WORD)

    assert result.status == "exhausted"
    assert result.attempts_used == 5
    assert [item.request_id for item in resumed_port.requests] == [
        f"{ACQUISITION_ID}-a2",
        f"{ACQUISITION_ID}-a3",
        f"{ACQUISITION_ID}-a4",
        f"{ACQUISITION_ID}-a5",
    ]
    saved = attempts(connection)
    assert [row[1] for row in saved] == [1, 2, 3, 4, 5]
    assert saved[0][2] == "interrupted"
    assert len(resumed_port.requests) == 4


def test_saved_response_is_reused_without_a_second_external_fetch(retry_case):
    connection, clock = retry_case
    first_port = SequencePort([response_for])
    first = session(connection, clock, first_port).fetch(THEME_WORD)
    second_port = SequencePort([])
    second = session(connection, clock, second_port).fetch(THEME_WORD)

    assert first.status == second.status == "response_ready"
    assert first.snapshot == second.snapshot
    assert len(first_port.requests) == 1
    assert second_port.requests == []


def test_response_saved_by_source_evidence_recovers_after_interruption_before_db_save(
    retry_case,
):
    connection, clock = retry_case

    class SourceEvidencePort:
        def __init__(self):
            self.requests = []
            self.saved = {}

        def fetch(self, request):
            self.requests.append(request)
            self.saved[request.request_id] = response_for(request)
            raise KeyboardInterrupt()

        def load_saved_response(self, request_id):
            return self.saved.get(request_id)

    source = SourceEvidencePort()
    with pytest.raises(KeyboardInterrupt):
        session(connection, clock, source).fetch(THEME_WORD)

    resumed_port = SequencePort([], saved_responses=source.saved)
    result = session(connection, clock, resumed_port).fetch(THEME_WORD)

    assert result.status == "response_ready" and result.attempts_used == 1
    assert len(source.requests) == 1 and resumed_port.requests == []
    assert attempts(connection)[0][2] == "response_ready"


def test_saved_invalid_source_contract_stops_after_interruption_without_retry(
    retry_case,
):
    connection, clock = retry_case

    class SourceEvidencePort:
        def __init__(self):
            self.requests = []
            self.saved = {}

        def fetch(self, request):
            self.requests.append(request)
            self.saved[request.request_id] = response_for(
                request, status="unavailable", error="source_response_invalid"
            )
            raise KeyboardInterrupt()

        def load_saved_response(self, request_id):
            return self.saved.get(request_id)

    source = SourceEvidencePort()
    with pytest.raises(KeyboardInterrupt):
        session(connection, clock, source).fetch(THEME_WORD)

    resumed = SequencePort([], saved_responses=source.saved)
    with pytest.raises(trends.TrendsContractError):
        session(connection, clock, resumed).fetch(THEME_WORD)

    assert len(source.requests) == 1 and resumed.requests == []
    assert connection.execute(
        "SELECT status,attempts_used FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()[:] == ("processing_error", 1)
    assert attempts(connection)[0][2:4] == (
        "processing_error",
        "source_response_invalid",
    )


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError()])
def test_transport_exceptions_are_recorded_as_retryable_source_failures(
    retry_case, error
):
    connection, clock = retry_case
    port = SequencePort([error, response_for])

    result = session(connection, clock, port).fetch(THEME_WORD)

    assert result.status == "response_ready" and result.attempts_used == 2
    assert len(port.requests) == 2
    assert attempts(connection)[0][2:4] == (
        "unavailable",
        (
            "source_timeout"
            if isinstance(error, TimeoutError)
            else "source_connection_error"
        ),
    )


def test_unexpected_port_exception_is_processing_error_not_theme_fallback(
    retry_case,
):
    connection, clock = retry_case
    port = SequencePort([RuntimeError("implementation bug"), response_for])

    with pytest.raises(trends.TrendsContractError):
        session(connection, clock, port).fetch(THEME_WORD)

    assert len(port.requests) == 1
    assert connection.execute(
        "SELECT status,attempts_used FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()[:] == ("processing_error", 1)
    assert attempts(connection)[0][2:4] == ("processing_error", "runtimeerror")


def test_access_denial_is_terminal_and_never_exhausts_into_theme_fallback(retry_case):
    connection, clock = retry_case
    port = SequencePort([response_for_dummy_unavailable("http_403"), response_for])

    with pytest.raises(trends.KeywordAcquisitionStopped):
        session(connection, clock, port).fetch(THEME_WORD)

    assert len(port.requests) == 1
    assert connection.execute(
        "SELECT status,attempts_used FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()[:] == ("processing_error", 1)


def test_invalid_theme_or_settings_response_is_processing_error_not_retry(retry_case):
    connection, clock = retry_case

    def wrong_theme(request):
        result = response_for(request)
        result["parent_word"] = "別テーマ"
        return result

    port = SequencePort([wrong_theme, response_for])
    with pytest.raises(trends.TrendsContractError):
        session(connection, clock, port).fetch(THEME_WORD)

    assert len(port.requests) == 1
    assert (
        connection.execute(
            "SELECT status FROM trends_acquisition WHERE acquisition_id=?",
            (ACQUISITION_ID,),
        ).fetchone()[0]
        == "processing_error"
    )
    assert attempts(connection)[0][2] == "processing_error"


@pytest.mark.parametrize("error_code", ["invalid_request", "source_response_invalid"])
def test_invalid_source_contract_does_not_retry_or_trigger_theme_fallback(
    retry_case, error_code
):
    connection, clock = retry_case
    port = SequencePort([response_for_dummy_unavailable(error_code), response_for])

    with pytest.raises(trends.TrendsContractError):
        session(connection, clock, port).fetch(THEME_WORD)

    assert len(port.requests) == 1
    assert connection.execute(
        "SELECT status,attempts_used FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()[:] == ("processing_error", 1)
    assert attempts(connection)[0][2:4] == ("processing_error", error_code)


@pytest.mark.parametrize(
    "error_code", ["source_process_error", "browser_adapter_error"]
)
def test_browser_implementation_errors_do_not_retry_or_trigger_theme_fallback(
    retry_case, error_code
):
    connection, clock = retry_case
    port = SequencePort([response_for_dummy_unavailable(error_code), response_for])

    with pytest.raises(trends.TrendsContractError):
        session(connection, clock, port).fetch(THEME_WORD)

    assert len(port.requests) == 1
    assert connection.execute(
        "SELECT status,attempts_used FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()[:] == ("processing_error", 1)
    assert attempts(connection)[0][2:4] == ("processing_error", error_code)


def test_retry_wait_beyond_request_deadline_skips_next_attempt(
    retry_case,
):
    connection, clock = retry_case
    port = SequencePort([response_for_dummy_unavailable("source_timeout")])
    result = session(
        connection,
        clock,
        port,
        deadline_at="2026-10-14T13:00:30+09:00",
    ).fetch(THEME_WORD)

    assert result.status == "deadline"
    assert result.attempts_used == 1
    assert len(port.requests) == 1
    assert [row[2] for row in attempts(connection)] == ["unavailable"]


def test_success_returned_after_request_deadline_is_not_adopted(retry_case):
    connection, clock = retry_case

    def response_after_cutoff(request):
        clock.advance(60)
        return response_for(request)

    port = SequencePort([response_after_cutoff])
    result = session(
        connection,
        clock,
        port,
        deadline_at="2026-10-14T13:00:30+09:00",
    ).fetch(THEME_WORD)

    assert result.status == "deadline" and result.snapshot is None
    assert len(port.requests) == 1
    assert attempts(connection)[0][2] == "deadline"
    assert attempts(connection)[0][6] is not None
    acquisition = connection.execute(
        "SELECT status,snapshot_id FROM trends_acquisition WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()
    assert tuple(acquisition) == ("deadline", None)
    assert (
        connection.execute("SELECT count(*) FROM keyword_snapshot").fetchone()[0] == 0
    )


def test_saved_success_after_request_deadline_is_not_reused(retry_case):
    connection, clock = retry_case

    class SourceEvidencePort:
        def __init__(self):
            self.saved = {}

        def fetch(self, request):
            self.saved[request.request_id] = response_for(request)
            raise KeyboardInterrupt()

        def load_saved_response(self, request_id):
            return self.saved.get(request_id)

    source = SourceEvidencePort()
    with pytest.raises(KeyboardInterrupt):
        session(
            connection,
            clock,
            source,
            deadline_at="2026-10-14T13:00:30+09:00",
        ).fetch(THEME_WORD)
    clock.advance(60)

    resumed = session(
        connection,
        clock,
        SequencePort([], source.saved),
        deadline_at="2026-10-14T13:00:30+09:00",
    )
    result = resumed.fetch(THEME_WORD)

    assert result.status == "deadline" and result.snapshot is None
    assert attempts(connection)[0][2] == "deadline"
    assert attempts(connection)[0][6] is not None
    assert (
        connection.execute("SELECT count(*) FROM keyword_snapshot").fetchone()[0] == 0
    )


def test_acquisition_query_cannot_change_when_request_id_changes(retry_case):
    connection, clock = retry_case
    port = SequencePort([response_for])
    session(connection, clock, port).fetch(THEME_WORD)
    changed = trends.DurableGroupKeywordSession(
        connection,
        SequencePort([]),
        clock,
        run_id=RUN_ID,
        settings_version=SETTINGS_VERSION,
        event_kind="initial",
        ordinal=1,
        theme_id=THEME_ID,
        seed="SEED-1",
        deadline_at=None,
        acquisition_id=ACQUISITION_ID,
    )
    with pytest.raises(trends.IdempotencyConflict):
        changed.fetch("別のテーマ")


@pytest.mark.parametrize(("age_days", "expires"), [(89, False), (90, True), (91, True)])
def test_retention_expires_raw_response_but_preserves_trends_attempt_state(
    retry_case, age_days, expires
):
    connection, clock = retry_case
    session(connection, clock, SequencePort([response_for])).fetch(THEME_WORD)
    ended_at = (datetime.fromisoformat(NOW) - timedelta(days=age_days)).isoformat()
    connection.execute(
        "UPDATE trends_acquisition_attempt SET ended_at=? WHERE acquisition_id=?",
        (ended_at, ACQUISITION_ID),
    )
    data_root = Path(connection.execute("PRAGMA database_list").fetchone()[2]).parent

    result = apply_retention(connection, data_root=data_root, now=NOW)

    attempt = connection.execute(
        "SELECT attempt_no,status,response_json FROM trends_acquisition_attempt "
        "WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()
    assert result.expired_trends_responses == int(expires)
    assert attempt[0:2] == (1, "response_ready")
    assert (attempt[2] is None) is expires
    acquisition = connection.execute(
        "SELECT status,attempts_used,snapshot_id FROM trends_acquisition "
        "WHERE acquisition_id=?",
        (ACQUISITION_ID,),
    ).fetchone()
    assert tuple(acquisition) == ("response_ready", 1, None)
