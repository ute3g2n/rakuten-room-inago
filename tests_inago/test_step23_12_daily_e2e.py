"""Isolated end-to-end checks for the production daily composition."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from automation.inago.acceptance import load_acceptance_registry
from automation.inago.clock import FakeClock, JST
from automation.inago.contracts import AIJobResult, AISubjobResult
from automation.inago.repository import initialize_database
from automation.inago.settings import register_external_settings
from tests_inago.test_step13 import FakeCopyRunner


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "plan" / "イナゴスキル事前準備" / "初期設定引渡し.json"
DETAILS_PATH = PROJECT_ROOT / "plan" / "イナゴスキル事前準備" / "文型詳細ルール.json"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
RUN_ID = "INAGO-2026-10-15"
NOW = "2026-10-15T13:15:00+09:00"


def test_full_flow_acceptance_registry_maps_h05_and_h08_to_new_evidence() -> None:
    registry = load_acceptance_registry(PROJECT_ROOT)
    module = "tests_inago/test_step23_12_daily_e2e.py"

    assert module in registry["H-05"].module_paths
    assert module in registry["H-08"].module_paths
    assert registry["H-05"].phase == "planned"
    assert registry["H-08"].phase == "planned"
    assert "未実証" in registry["H-08"].live_evidence


def test_h07_acceptance_registry_includes_file_reports_and_daily_import() -> None:
    registry = load_acceptance_registry(PROJECT_ROOT)
    expected = {
        "tests_inago/test_step23_09_reports.py",
        "tests_inago/test_step23_12_history_interactions.py",
        "tests_inago/test_step23_12_report_import.py",
        "tests_inago/test_step23_12_daily_e2e.py",
    }

    assert expected.issubset(set(registry["H-07"].module_paths))


def test_h07_registry_maps_new_report_count_nodes() -> None:
    registry = load_acceptance_registry(PROJECT_ROOT)
    module_path = "tests_inago/test_step23_12_history_interactions.py"
    module_text = (PROJECT_ROOT / module_path).read_text(encoding="utf-8")

    assert module_path in registry["H-07"].module_paths
    assert (
        "def test_history_html_displays_separate_day_scoped_report_and_outbox_counts"
        in module_text
    )
    assert (
        "def test_history_report_counts_are_day_action_status_scoped_and_not_recent_limited"
        in module_text
    )


class _FixturePublicSource:
    fixture_only = True

    def __init__(
        self,
        data_root: Path,
        clock: FakeClock,
        *,
        mode: str = "normal",
    ) -> None:
        self.evidence_root = data_root / "observations"
        self.clock = clock
        self.mode = mode
        self.trends_requests: list[Any] = []
        self.search_requests: list[Any] = []
        self.facts: dict[str, dict[str, Any]] = {}
        self.cancelled_runs: list[str] = []

    def fetch(self, request: Any) -> dict[str, Any]:
        self.trends_requests.append(request)
        unavailable = self.mode == "trends_unavailable"
        words = (
            []
            if self.mode == "zero_supply"
            else [
                {
                    "word_id": "W-01",
                    "raw": f"{request.parent_word} fixture",
                    "source_rank": 1,
                    "popularity_index": None,
                }
            ]
        )
        return {
            "request_id": request.request_id,
            "snapshot_id": request.request_id,
            "run_id": request.run_id,
            "settings_version": request.settings_version,
            "source": "google_trends_public_ui",
            "parent_word": request.parent_word,
            "current_parent": None if unavailable else request.parent_word,
            "refresh_complete": not unavailable,
            "language": "ja",
            "geo": "JP",
            "search_type": "web",
            "range": request.range,
            "list_type": "Top",
            "status": "unavailable" if unavailable else "observed",
            "fetched_at": None if unavailable else self.clock.now_jst().isoformat(),
            "error_code": "fixture_timeout" if unavailable else None,
            "words": [] if unavailable else words,
            **(
                {"ui_period_label": "過去3か月"}
                if request.range == "3m" and not unavailable
                else {}
            ),
        }

    def search(self, request: Any) -> dict[str, Any]:
        self.search_requests.append(request)
        call_number = len(self.search_requests)
        failed = self.mode == "room_unavailable" or (
            self.mode in {"intake_failure", "copy_failure", "save_failure"}
            and call_number > 1
        )
        empty = self.mode == "zero_supply"
        count = 5 if self.mode in {"normal", "intake_failure", "save_failure"} else 1
        posts: list[dict[str, Any]] = []
        if not failed and not empty:
            for index in range(1, count + 1):
                shop_id = f"step23shop{call_number:02d}"
                item_id = f"item{index:02d}"
                evidence_ref = f"ROOM-EVIDENCE-{call_number:02d}-{index:02d}"
                observed_at = self.clock.now_jst().isoformat()
                item_url = f"https://item.rakuten.co.jp/{shop_id}/{item_id}/"
                posts.append(
                    {
                        "internal_index": index,
                        "stable_room_id": f"ROOM-{call_number:02d}-{index:02d}",
                        "room_url": (
                            "https://room.rakuten.co.jp/fixture/items/"
                            f"{call_number:02d}-{index:02d}"
                        ),
                        "likes": 500 + index,
                        "comments": None,
                        "item_url": item_url,
                        "observed_shop_id": shop_id,
                        "observed_item_id": item_id,
                        "image_ref": f"IMAGE-{call_number:02d}-{index:02d}",
                        "actor_ref": None,
                        "published_at": None,
                        "evidence_ref": evidence_ref,
                        "observed_at": observed_at,
                        "availability": "observed",
                    }
                )
                self.facts[evidence_ref] = {
                    "offer_key": f"{shop_id}:{item_id}",
                    "name": f"試験商品 {call_number}-{index}",
                    "item_url": item_url,
                    "evidence_ref": evidence_ref,
                    "observed_at": observed_at,
                    "origin": "public_product_image_label",
                    "image_ref": f"IMAGE-{call_number:02d}-{index:02d}",
                }
        status = "unavailable" if failed else "observed"
        return {
            "attempt_id": request.attempt_id,
            "run_id": request.run_id,
            "query": request.query,
            "parent_refs": list(request.parent_refs),
            "level": request.level,
            "likes_min": request.likes_min,
            "max_posts": request.max_posts,
            "current_search_id": None if failed else request.attempt_id,
            "search_loaded": not failed,
            "internal_order_confirmed": not failed,
            "status": status,
            "fetched_at": None if failed else self.clock.now_jst().isoformat(),
            "end_reached": not failed,
            "error_code": "fixture_source_unavailable" if failed else None,
            "posts": posts,
            **(
                {
                    "query_kind": request.query_kind,
                    "keyword_snapshot_ref": request.keyword_snapshot_ref,
                    "selected_word_ref": request.selected_word_ref,
                }
                if request.query_kind
                else {}
            ),
            "loaded_posts": len(posts),
            "condition_evidence_ref": f"COND-{call_number:02d}" if not failed else None,
        }

    def product_fact(self, evidence_ref: str) -> dict[str, Any] | None:
        if self.mode == "intake_failure":
            return None
        return self.facts.get(evidence_ref)

    def cancel_owned_jobs(self, run_id: str) -> bool:
        self.cancelled_runs.append(run_id)
        return True


class _FixtureAIRoot:
    fixture_only = True

    def __init__(self, *, fail_copy: bool = False) -> None:
        self.fail_copy = fail_copy
        self.copy_runner = FakeCopyRunner()
        self.requests: list[Any] = []
        self.lifecycle: list[tuple[str, str | None]] = []

    def prepare_daily_run(self, _connection: sqlite3.Connection, run_id: str) -> None:
        self.lifecycle.append(("prepare", run_id))

    def start_daily_root(self, *, deadline_monotonic: float) -> None:
        assert deadline_monotonic > 0
        self.lifecycle.append(("start", None))

    def sync_daily_checkpoint(self, _connection: sqlite3.Connection) -> None:
        self.lifecycle.append(("sync", None))

    def close_daily_run(self) -> None:
        self.lifecycle.append(("close", None))

    def cancel_owned_jobs(self, run_id: str) -> bool:
        self.lifecycle.append(("cancel", run_id))
        return True

    def run(self, request: Any) -> AIJobResult:
        self.requests.append(request)
        if request.copy_targets:
            if self.fail_copy:
                raise RuntimeError("fixture copy generation failed")
            evidence_first = replace(
                request,
                input_refs=(
                    *request.evidence_refs,
                    *(
                        ref
                        for ref in request.input_refs
                        if ref not in request.evidence_refs
                    ),
                ),
            )
            result = self.copy_runner.run(evidence_first)
            return replace(
                result,
                input_refs=request.input_refs,
                subjobs=tuple(
                    replace(subjob, input_refs=request.input_refs)
                    for subjob in result.subjobs
                ),
            )

        keyword_input = request.input_payload["keywords"]
        decisions = [
            {
                "word_id": word["word_id"],
                "decision": "candidate",
                "group_ref": word["word_id"],
                "reason": "fixture distinct candidate",
            }
            for word in keyword_input["words"]
        ]
        subjob = AISubjobResult(
            job_id=f"{request.job_id}-KEY",
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
            input_hash="fixture-key-input",
            deadline_at=request.deadline_at,
            result_status="success",
            payload={
                "snapshot_id": keyword_input["snapshot_id"],
                "query_word": keyword_input["query_word"],
                "decisions": decisions,
            },
        )
        return AIJobResult(
            job_id=request.job_id,
            run_id=request.run_id,
            attempt=request.attempt,
            task_id="T02",
            agent_id="AI-O",
            skill_name="rakuten-room-inago",
            target_date=request.target_date,
            settings_version=request.settings_version,
            input_refs=request.input_refs,
            evidence_refs=(),
            input_hash="fixture-key-root-input",
            deadline_at=request.deadline_at,
            result_status="success",
            adoption_status="accepted",
            payload={"status": "delegated"},
            error=None,
            artifact=None,
            subjobs=(subjob,),
            resolved_model="fixture-model",
            reasoning_setting="fixture",
            auth_mode="fixture",
        )


class _FixtureMetrics:
    fixture_only = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def collect(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        deadline_at: str,
    ) -> str:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        self.calls.append((run_id, deadline_at))
        if self.fail:
            raise RuntimeError("fixture metrics unavailable")
        return "observed"


def _database_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _make_daily_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    now: str = NOW,
    source_mode: str = "normal",
    fail_copy: bool = False,
    fail_metrics: bool = False,
):
    from automation.inago import production_runtime

    runtime_root = tmp_path / "runtime"
    backup_root = tmp_path / "backups"
    runtime_root.mkdir()
    backup_root.mkdir()
    database_path = runtime_root / "state.sqlite"
    initial_settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    detailed_rules = json.loads(DETAILS_PATH.read_text(encoding="utf-8"))
    connection = _database_connection(database_path)
    try:
        initialize_database(connection)
        settings_runtime = register_external_settings(
            connection,
            initial_settings,
            detailed_rules,
            created_at=now,
        )
    finally:
        connection.close()
    config = SimpleNamespace(
        start_date="2026-10-01",
        runtime_root=runtime_root,
        backup_root=backup_root,
        adopted_settings=initial_settings,
        detailed_rules=detailed_rules,
    )
    clock = FakeClock(datetime.fromisoformat(now))
    source = _FixturePublicSource(runtime_root, clock, mode=source_mode)
    ai_root = _FixtureAIRoot(fail_copy=fail_copy)
    metrics = _FixtureMetrics(fail=fail_metrics)
    copy_spec_factory = production_runtime._make_copy_spec_factory(
        config, settings_runtime
    )
    copy_spec_factory.fixture_only = True

    def cancel_metrics(run_id: str) -> bool:
        return True

    cancel_metrics.fixture_only = True
    components = {
        "source": source,
        "ai_root": ai_root,
        "copy_spec_factory": copy_spec_factory,
        "metrics_port": metrics,
        "cancel_metrics_job": cancel_metrics,
    }
    monkeypatch.setattr(
        production_runtime,
        "require_production_ready",
        lambda _config: {"settings_version": SETTINGS_VERSION},
    )
    monkeypatch.setattr(
        production_runtime, "_assert_production_roots", lambda _config: None
    )
    monkeypatch.setattr(
        production_runtime,
        "_open_existing_production_database",
        lambda _config: _database_connection(database_path),
    )
    monkeypatch.setattr(
        production_runtime,
        "register_production_settings",
        lambda *_args, **_kwargs: settings_runtime,
    )
    monkeypatch.setattr(
        production_runtime,
        "build_public_daily_components",
        lambda *_args, **_kwargs: components,
    )
    return config, database_path, clock, source, ai_root, metrics


def _read_html_payload(path: Path) -> dict[str, Any]:
    markup = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        markup,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_daily_imports_browser_reports_before_applying_pending_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, _clock, _source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    connection = _database_connection(database_path)
    try:
        connection.execute(
            """
            INSERT INTO run
                (run_id, target_date, mode, status, settings_version, seed,
                 started_at, draft_deadline, end_deadline, attempt_used,
                 committed_count, checkpoint, metrics_status, finished_at)
            VALUES ('PRIOR-RUN', '2026-10-14', 'daily', 'complete', ?, 'prior-seed',
                    '2026-10-14T13:00:00+09:00', '2026-10-14T17:45:00+09:00',
                    '2026-10-14T18:00:00+09:00', 0, 1, NULL, 'not_started',
                    '2026-10-14T18:00:00+09:00')
            """,
            (SETTINGS_VERSION,),
        )
        connection.execute(
            """
            INSERT INTO offer
                (offer_key, shop_id, item_id, name, url, image_refs_json,
                 main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
            VALUES ('prior_shop:prior_item', 'prior_shop', 'prior_item',
                    'prior fixture', 'https://item.rakuten.co.jp/prior_shop/prior_item/',
                    '[]', 'G01-T01', '[]', '2026-10-14T13:00:00+09:00',
                    '2026-10-14T13:00:00+09:00', '[]')
            """
        )
        connection.execute(
            """
            INSERT INTO draft
                (draft_id, offer_key, run_id, version, body, generated_at,
                 committed_at, generator_version, pattern_version, status,
                 artifact_ref, pattern_id, evidence_refs_json)
            VALUES ('D-PRIOR', 'prior_shop:prior_item', 'PRIOR-RUN', 1,
                    'prior body', '2026-10-14T13:05:00+09:00',
                    '2026-10-14T13:05:00+09:00', 'fixture-copy', 'fixture-pattern',
                    'committed', NULL, 'P01', '[]')
            """
        )
        connection.execute(
            """
            INSERT INTO reservation
                (reservation_id, offer_key, draft_id, status, created_at,
                 released_at, release_reason)
            VALUES ('RES-PRIOR', 'prior_shop:prior_item', 'D-PRIOR', 'active',
                    '2026-10-14T13:05:00+09:00', NULL, NULL)
            """
        )
    finally:
        connection.close()

    inbox = config.runtime_root / "incoming-human-reports"
    inbox.mkdir()
    report = {
        "action_id": "ACT-IMPORT-TO-DAILY",
        "offer_key": "prior_shop:prior_item",
        "draft_id": "D-PRIOR",
        "draft_version": 1,
        "expected_receipt_revision": 0,
        "action": "posted",
        "reason": None,
        "correction_ref": None,
        "actual_posted_at": None,
    }
    (inbox / "human-reports-2026-10-14.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reports": [
                    {"queued_at": "2026-10-14T21:05:00+09:00", "report": report}
                ],
            }
        ),
        encoding="utf-8",
    )

    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )

    connection = _database_connection(database_path)
    try:
        daily_state = json.loads(
            connection.execute(
                "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
        )["daily_orchestration"]
        assert result["status"] == "complete"
        assert daily_state["report_import"] == {
            "files_processed": 1,
            "reports_received": 1,
        }
        assert daily_state["report_apply"]["applied_count"] == 1
        action = connection.execute(
            "SELECT apply_status, reported_at FROM human_action "
            "WHERE action_id='ACT-IMPORT-TO-DAILY'"
        ).fetchone()
        assert tuple(action) == ("applied", "2026-10-14T21:05:00+09:00")
        assert (
            connection.execute(
                "SELECT status FROM draft WHERE draft_id='D-PRIOR'"
            ).fetchone()[0]
            == "posted"
        )
        assert (
            connection.execute(
                "SELECT json_extract(checkpoint, '$.daily_orchestration.report_apply.applied_count') "
                "FROM run WHERE run_id=?",
                (RUN_ID,),
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()
    assert (
        config.runtime_root
        / "processed-human-reports"
        / "human-reports-2026-10-14.json"
    ).is_file()


def test_scheduler_composition_commits_fifteen_copies_and_exports_final_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, _clock, source, ai_root, metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )

    result = production_runtime.run_production_daily(
        config,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )

    assert (result["status"], result["daily_status"], result["committed_count"]) == (
        "complete",
        "complete",
        15,
    )
    assert result["target_date"] == "2026-10-15"
    assert result["metrics_status"] == "observed"
    assert metrics.calls == [(RUN_ID, "2026-10-15T13:20:00+09:00")]
    assert len(source.trends_requests) == 3
    assert len(source.search_requests) == 3
    assert len(source.facts) == 15
    assert len(ai_root.copy_runner.requests) == 15
    assert (
        len([request for request in ai_root.requests if not request.copy_targets]) == 3
    )
    assert ai_root.lifecycle[0] == ("prepare", RUN_ID)
    assert ai_root.lifecycle[1][0] == "start"
    assert ai_root.lifecycle[-2][0] == "close"
    assert ai_root.lifecycle[-1][0] == "sync"

    connection = _database_connection(database_path)
    try:
        run = connection.execute(
            "SELECT status, committed_count, metrics_status FROM run WHERE run_id=?",
            (RUN_ID,),
        ).fetchone()
        assert tuple(run) == ("complete", 15, "observed")
        assert connection.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 15
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 15
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
            == 15
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM ai_job WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
            == 30
        )
        assert (
            connection.execute(
                "SELECT json_extract(checkpoint, '$.daily_orchestration.report_apply.applied_count') "
                "FROM run WHERE run_id=?",
                (RUN_ID,),
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()

    html_path = config.runtime_root / "posting-history.html"
    day = _read_html_payload(html_path)["days"][0]
    assert day["target_date"] == "2026-10-15"
    assert day["item_count"] == 15
    assert result["export"]["draft_count"] == 15
    assert result["preservation"]["status"] == "ready"
    assert result["preservation"]["end_backup"]["status"] == "ready"
    assert result["preservation"]["monthly_restore"]["status"] == "ready"
    backup_database = (
        Path(result["preservation"]["end_backup"]["path"]) / "state.sqlite"
    )
    backup_connection = sqlite3.connect(backup_database)
    try:
        assert (
            backup_connection.execute(
                "SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
            == 15
        )
    finally:
        backup_connection.close()
    assert not (config.runtime_root / ".daily-run-owner.json").exists()


@pytest.mark.parametrize(
    ("now", "expected_status", "metrics_calls"),
    (
        ("2026-10-15T17:45:00+09:00", "complete", 1),
        ("2026-10-15T18:00:00+09:00", "complete", 1),
    ),
    ids=("continues-after-1745", "continues-after-1800"),
)
def test_scheduler_composition_continues_after_former_daily_cutoffs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    now: str,
    expected_status: str,
    metrics_calls: int,
) -> None:
    from automation.inago import orchestrator, production_runtime

    recorded_errors: list[str] = []
    original_record_error = orchestrator._record_orchestration_error

    def record_orchestration_error(state, error):
        recorded_errors.append(f"{type(error).__name__}: {error}")
        original_record_error(state, error)

    monkeypatch.setattr(
        orchestrator, "_record_orchestration_error", record_orchestration_error
    )

    config, _database_path, clock, source, ai_root, metrics = _make_daily_fixture(
        monkeypatch, tmp_path, now=now
    )

    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == expected_status, recorded_errors
    assert result["run_id"] == RUN_ID
    assert result["committed_count"] == 15
    assert source.search_requests
    assert source.trends_requests
    assert sum(bool(request.copy_targets) for request in ai_root.requests) == 15
    assert len(metrics.calls) == metrics_calls
    assert result["reason"] == "daily_target_reached"


def test_observed_zero_supply_is_a_shortfall_with_an_empty_day_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, clock, source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path, source_mode="zero_supply"
    )

    result = production_runtime.run_production_daily(config, clock=clock)

    assert (result["daily_status"], result["reason"], result["committed_count"]) == (
        "shortfall",
        "candidate_or_theme_exhausted",
        0,
    )
    assert source.search_requests
    assert source.facts == {}
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM search_attempt WHERE source_status='observed'"
        ).fetchone()[0] == len(source.search_requests)
    finally:
        connection.close()
    day = _read_html_payload(config.runtime_root / "posting-history.html")["days"][0]
    assert day["target_date"] == "2026-10-15"
    assert day["item_count"] == 0
    assert day["run_status"] == "件数不足"
    assert result["export"]["status"] == "ready"


def test_total_room_source_outage_fails_without_claiming_zero_supply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, clock, source, ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path, source_mode="room_unavailable"
    )

    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "failed"
    assert result["committed_count"] == 0
    assert len(source.search_requests) == 3
    assert source.facts == {}
    assert not [request for request in ai_root.requests if request.copy_targets]
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
        attempts = connection.execute(
            "SELECT source_status FROM search_attempt ORDER BY started_at"
        ).fetchall()
        assert [row["source_status"] for row in attempts] == [
            "unavailable",
            "unavailable",
            "unavailable",
        ]
    finally:
        connection.close()
    day = _read_html_payload(config.runtime_root / "posting-history.html")["days"][0]
    assert day["run_status"] == "失敗"
    assert day["item_count"] == 0


def test_all_product_intake_failures_remain_distinct_from_normal_zero_supply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, clock, source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path, source_mode="intake_failure"
    )

    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "failed"
    assert result["committed_count"] == 0
    assert len(source.facts) == 5
    connection = _database_connection(database_path)
    try:
        run = connection.execute(
            "SELECT status, committed_count, checkpoint FROM run WHERE run_id=?",
            (RUN_ID,),
        ).fetchone()
        assert run["status"] == "failed"
        assert run["committed_count"] == 0
        checkpoint = json.loads(run["checkpoint"])["daily_orchestration"]
        assert checkpoint["error_count"] > 0
        assert connection.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
    finally:
        connection.close()
    day = _read_html_payload(config.runtime_root / "posting-history.html")["days"][0]
    assert day["run_status"] == "失敗"
    assert day["item_count"] == 0


def test_copy_generation_failure_keeps_zero_commits_and_exports_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, clock, _source, ai_root, _metrics = _make_daily_fixture(
        monkeypatch,
        tmp_path,
        source_mode="copy_failure",
        fail_copy=True,
    )

    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "failed"
    assert result["committed_count"] == 0
    copy_request_count = sum(bool(request.copy_targets) for request in ai_root.requests)
    assert copy_request_count == 3, (
        f"copy_requests={copy_request_count}; trends_requests={len(_source.trends_requests)}; "
        f"lifecycle={ai_root.lifecycle}; outcome={result}"
    )
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM generation_attempt WHERE outcome='error'"
            ).fetchone()[0]
            == 3
        )
    finally:
        connection.close()
    assert result["export"]["status"] == "ready"
    assert (
        _read_html_payload(config.runtime_root / "posting-history.html")["days"][0][
            "item_count"
        ]
        == 0
    )


def test_copy_save_failure_rolls_back_draft_and_reservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, clock, _source, ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path, source_mode="save_failure"
    )
    connection = _database_connection(database_path)
    try:
        connection.execute(
            "CREATE TRIGGER reject_fixture_draft BEFORE INSERT ON draft "
            "BEGIN SELECT RAISE(ABORT, 'fixture save failure'); END"
        )
    finally:
        connection.close()

    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "failed"
    assert result["committed_count"] == 0
    assert ai_root.copy_runner.requests
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()
    assert result["export"]["status"] == "ready"


def test_metrics_failure_does_not_erase_completed_drafts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime

    config, database_path, clock, _source, _ai_root, metrics = _make_daily_fixture(
        monkeypatch, tmp_path, fail_metrics=True
    )

    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "complete"
    assert result["status"] == "metrics_failed"
    assert result["committed_count"] == 15
    assert result["metrics_status"] == "unavailable"
    assert len(metrics.calls) == 1
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 15
        assert (
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
            == 15
        )
    finally:
        connection.close()
    assert result["export"]["draft_count"] == 15


def test_html_failure_preserves_prior_html_and_committed_drafts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import dashboard_history, production_runtime

    config, database_path, clock, _source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    html_path = config.runtime_root / "posting-history.html"
    prior_html = b"previously saved report"
    html_path.write_bytes(prior_html)

    def fail_html_export(*_args, **_kwargs):
        raise OSError("fixture html write failure")

    monkeypatch.setattr(
        dashboard_history, "export_dashboard_history_html", fail_html_export
    )
    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "complete"
    assert result["status"] == "export_failed"
    assert result["export"]["status"] == "failed"
    assert result["export"]["error_code"] == "OSError"
    assert html_path.read_bytes() == prior_html
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 15
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM reservation WHERE status='active'"
            ).fetchone()[0]
            == 15
        )
    finally:
        connection.close()


def test_final_backup_failure_keeps_daily_result_and_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import backup, production_runtime

    config, database_path, clock, _source, _ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path
    )
    create_backup = backup.create_daily_backup

    def fail_final_backup(*args, **kwargs):
        if kwargs.get("generation_stage") == "final":
            raise OSError("fixture final backup failure")
        return create_backup(*args, **kwargs)

    monkeypatch.setattr(backup, "create_daily_backup", fail_final_backup)
    result = production_runtime.run_production_daily(config, clock=clock)

    assert result["daily_status"] == "complete"
    assert result["status"] == "preservation_failed"
    assert result["preservation"]["end_backup"]["status"] == "failed"
    assert result["export"]["status"] == "ready"
    assert (
        _read_html_payload(config.runtime_root / "posting-history.html")["days"][0][
            "item_count"
        ]
        == 15
    )
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 15
    finally:
        connection.close()


def test_cutoff_resume_reuses_same_run_and_does_not_regenerate_committed_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automation.inago import production_runtime, trigger

    config, database_path, clock, source, ai_root, _metrics = _make_daily_fixture(
        monkeypatch, tmp_path, source_mode="copy_failure"
    )
    original_daily = trigger.run_public_scheduled_daily
    interruption_injected = False

    def interrupt_after_first_commit(connection, **kwargs):
        nonlocal interruption_injected
        original_callback = kwargs["on_draft_commit"]

        def commit_callback(callback_connection):
            nonlocal interruption_injected
            original_callback(callback_connection)
            if not interruption_injected:
                interruption_injected = True
                raise KeyboardInterrupt("fixture process interruption after commit")

        kwargs["on_draft_commit"] = commit_callback
        return original_daily(connection, **kwargs)

    monkeypatch.setattr(
        trigger, "run_public_scheduled_daily", interrupt_after_first_commit
    )
    with pytest.raises(
        KeyboardInterrupt, match="fixture process interruption after commit"
    ):
        production_runtime.run_production_daily(config, clock=clock)
    assert interruption_injected is True
    first_copy_count = sum(bool(request.copy_targets) for request in ai_root.requests)
    first_search_count = len(source.search_requests)
    assert first_copy_count == 1
    assert first_search_count == 1

    owner_path = config.runtime_root / ".daily-run-owner.json"
    owner_path.write_text(
        json.dumps(
            {
                "pid": 2_147_483_647,
                "process_start_time": "2000-01-01T00:00:00+00:00",
                "run_id": RUN_ID,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def interrupted_process(_pid: int) -> str:
        raise ProcessLookupError("fixture owner process has exited")

    resumed = production_runtime.run_production_daily(
        config,
        clock=clock,
        resume_confirmed=True,
        cause_resolved=True,
        process_start_time_reader=interrupted_process,
        process_tree_reader=lambda _pid: (),
    )

    assert resumed["daily_status"] == "partial"
    assert resumed["run_id"] == RUN_ID
    assert resumed["resumed"] is True
    assert resumed["committed_count"] == 1
    assert (
        sum(bool(request.copy_targets) for request in ai_root.requests)
        == first_copy_count
    )
    assert len(source.search_requests) > first_search_count
    assert all(request.run_id == RUN_ID for request in source.search_requests)
    connection = _database_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()
