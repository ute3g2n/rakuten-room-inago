"""Finite daily orchestration, recovery, and real Windows run-lock contracts."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import shutil
from io import StringIO
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from automation.inago.ai_runner import compute_input_hash
from automation.inago.clock import FakeClock
from automation.inago.cli import DailyCLIContext, main as run_cli
from automation.inago.contracts import (
    AIJobRequest,
    AIJobResult,
    AISubjobResult,
    CopyTarget,
)
from automation.inago.copy_service import CopyGenerationSpec
from automation.inago.paths import SAFE_FIXTURE_BASE, resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.room_adapter import RoomSearchRequest
from automation.inago.run_lock import RunLock, RunLockBusy, process_start_time
from automation.inago.settings import register_external_settings
from automation.inago.orchestrator import (
    DailyPorts,
    DailySearchPlan,
    DailyThemePlan,
    _intake_missing_offers,
    run_daily,
)
from automation.inago.orchestrator import OfferIntakeRequest
from automation.inago.repository import LEGACY_UNASSIGNED_THEME_ID


JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[1]
BASIC_SETTINGS_PATH = ROOT / "docs" / "specifications" / "preparation" / "初期設定引渡し.json"
PREPARATION_ROOT = BASIC_SETTINGS_PATH.parent
DETAIL_RULES_PATH = ROOT / "worklogs" / "projects" / "preparation" / "candidates" / "文型詳細ルール.json"
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
DETAILS_VERSION = "detailed-writing-rules-candidate-0.1-step68"
NOW = "2026-10-12T13:00:00+09:00"
RUN_ID = "STEP13-RUN"


@pytest.fixture
def runtime_case(tmp_path: Path, fixture_config_path: Path) -> "RuntimeCase":
    private_data_root = SAFE_FIXTURE_BASE / "step-13" / tmp_path.name / "state"
    paths = resolve_runtime_paths(
        data_root=private_data_root,
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    basic = json.loads(
        (BASIC_SETTINGS_PATH).read_text(encoding="utf-8")
    )
    details = json.loads(
        (DETAIL_RULES_PATH).read_text(encoding="utf-8")
    )
    register_external_settings(connection, basic, details, created_at=NOW)
    case = RuntimeCase(
        connection=connection,
        data_root=paths.data_root,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )
    try:
        yield case
    finally:
        connection.close()
        resolved_root = private_data_root.resolve()
        allowed_root = SAFE_FIXTURE_BASE.resolve()
        if allowed_root in resolved_root.parents:
            shutil.rmtree(resolved_root.parent, ignore_errors=True)


@dataclass
class RuntimeCase:
    connection: sqlite3.Connection
    data_root: Path
    clock: FakeClock


def _insert_offer(
    connection: sqlite3.Connection,
    *,
    theme_id: str,
    item_number: int,
    created_at: str = NOW,
) -> tuple[str, str, str]:
    shop_id = f"shop-{theme_id.lower()}"
    item_id = f"item-{item_number:02d}"
    offer_key = f"{shop_id}:{item_id}"
    connection.execute(
        """
        INSERT OR IGNORE INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES (?, ?, ?, ?, ?, '[]', ?, '[]', ?, ?, '[]')
        """,
        (
            offer_key,
            shop_id,
            item_id,
            f"試験品{item_number}",
            f"https://item.rakuten.co.jp/{shop_id}/{item_id}/",
            theme_id,
            created_at,
            created_at,
        ),
    )
    return offer_key, shop_id, item_id


def _unknown_facts() -> list[dict[str, Any]]:
    return [
        {"field": key, "value": None, "availability": "unknown", "source_ref": None}
        for key in ("price", "stock", "shipping", "rating", "promotion")
    ]


class FakeThemePlanner:
    """Return finite plans tied to selected themes and seed matching fixture offers."""

    fixture_only = True

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        untried: bool = False,
        fail_additional_once: bool = False,
        skip_first_theme_seed: bool = False,
        clock: FakeClock | None = None,
    ) -> None:
        self.connection = connection
        self.clock = clock or FakeClock(datetime.fromisoformat(NOW))
        self.untried = untried
        self.calls: list[tuple[str, str, int]] = []
        self.saved: dict[tuple[str, str, int], DailyThemePlan] = {}
        self.fail_additional_once = fail_additional_once
        self.skip_first_theme_seed = skip_first_theme_seed

    def plan_theme(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        settings_version: str,
        event_kind: str,
        ordinal: int,
        theme_id: str,
        seed: str,
    ) -> DailyThemePlan:
        assert connection is self.connection
        assert run_id
        assert settings_version == SETTINGS_VERSION
        assert seed
        key = (event_kind, theme_id, ordinal)
        self.calls.append(key)
        if event_kind == "additional" and self.fail_additional_once:
            self.fail_additional_once = False
            raise RuntimeError("fixture planner interruption")
        if key not in self.saved:
            if not (
                self.skip_first_theme_seed and event_kind == "initial" and ordinal == 1
            ):
                for number in range(1, 6):
                    _insert_offer(connection, theme_id=theme_id, item_number=number)
            from types import SimpleNamespace
            from tests_inago.fixtures_q14 import CurrentPlanner

            count = (
                2 if self.untried and event_kind == "initial" and ordinal == 1 else 1
            )
            planner = CurrentPlanner(
                SimpleNamespace(connection=connection, clock=self.clock),
                word_count=count,
            )
            self.saved[key] = planner.plan_theme(
                connection,
                run_id=run_id,
                settings_version=settings_version,
                event_kind=event_kind,
                ordinal=ordinal,
                theme_id=theme_id,
                seed=seed,
            )
        return self.saved[key]


class FakeRoomPort:
    fixture_only = True

    def __init__(
        self,
        clock: FakeClock,
        *,
        posts_per_search: int = 0,
        status: str = "observed",
        status_by_call: list[str] | None = None,
        finish_after_call: int | None = None,
        advance_seconds: float = 0,
        before_search=None,
    ) -> None:
        self.clock = clock
        self.posts_per_search = posts_per_search
        self.status = status
        self.status_by_call = status_by_call or []
        self.finish_after_call = finish_after_call
        self.advance_seconds = advance_seconds
        self.before_search = before_search
        self.requests: list[RoomSearchRequest] = []

    def search(self, request: RoomSearchRequest) -> dict[str, Any]:
        self.requests.append(request)
        if self.before_search is not None:
            self.before_search(request)
        call_number = len(self.requests)
        if self.finish_after_call == call_number:
            self.clock.advance(self.advance_seconds)
        status = (
            self.status_by_call[call_number - 1]
            if call_number <= len(self.status_by_call)
            else self.status
        )
        theme_id = request.query.split("::", maxsplit=1)[1]
        posts: list[dict[str, Any]] = []
        if status != "unavailable":
            for number in range(1, self.posts_per_search + 1):
                shop_id = f"shop-{theme_id.lower()}"
                item_id = f"item-{number:02d}"
                posts.append(
                    {
                        "internal_index": number,
                        "stable_room_id": f"ROOM-{theme_id}-{number:02d}",
                        "room_url": f"https://room.rakuten.co.jp/test/item/{theme_id}-{number:02d}",
                        "likes": 400 + number,
                        "comments": None,
                        "item_url": f"https://item.rakuten.co.jp/{shop_id}/{item_id}/",
                        "observed_shop_id": shop_id,
                        "observed_item_id": item_id,
                        "image_ref": f"IMG-{theme_id}-{number:02d}",
                        "actor_ref": None,
                        "published_at": None,
                        "evidence_ref": f"EVIDENCE-{theme_id}-{number:02d}",
                        "observed_at": self.clock.now_jst().isoformat(),
                        "availability": "observed",
                    }
                )
        available = status != "unavailable"
        return {
            "attempt_id": request.attempt_id,
            "run_id": request.run_id,
            "query": request.query,
            "parent_refs": list(request.parent_refs),
            "level": request.level,
            "likes_min": request.likes_min,
            "max_posts": request.max_posts,
            "current_search_id": request.attempt_id if available else None,
            "search_loaded": available,
            "internal_order_confirmed": available,
            "status": status,
            "fetched_at": self.clock.now_jst().isoformat() if available else None,
            "end_reached": status == "observed",
            "error_code": "fixture_unavailable" if not available else None,
            "posts": posts,
            **(
                {
                    "query_kind": request.query_kind,
                    "keyword_snapshot_ref": request.keyword_snapshot_ref,
                    "selected_word_ref": request.selected_word_ref,
                    "loaded_posts": len(posts),
                    "condition_evidence_ref": (
                        f"COND-{request.attempt_no}" if available else None
                    ),
                }
                if request.query_kind
                else {}
            ),
        }


class FakeOfferIntake:
    """Persist fixture snapshots without inferring facts from a ROOM post."""

    fixture_only = True

    def __init__(self) -> None:
        self.requests: list[OfferIntakeRequest] = []

    def persist_offer(
        self, connection: sqlite3.Connection, request: OfferIntakeRequest
    ) -> None:
        self.requests.append(request)
        shop_id, item_id = request.offer_key.split(":", maxsplit=1)
        assert request.item_url == f"https://item.rakuten.co.jp/{shop_id}/{item_id}/"
        connection.execute(
            """
            INSERT OR IGNORE INTO offer
                (offer_key, shop_id, item_id, name, url, image_refs_json,
                 main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)
            """,
            (
                request.offer_key,
                shop_id,
                item_id,
                f"試験商品 {item_id}",
                request.item_url,
                json.dumps([request.image_ref] if request.image_ref else []),
                request.theme_id,
                request.observed_at,
                request.observed_at,
                json.dumps(_unknown_facts(), ensure_ascii=False),
            ),
        )


class FakeCopyRunner:
    fixture_only = True

    def __init__(self) -> None:
        self.requests: list[AIJobRequest] = []

    def run(self, request: AIJobRequest) -> AIJobResult:
        self.requests.append(request)
        target: CopyTarget = request.copy_targets[0]
        input_ref = request.input_refs[0]
        child = AISubjobResult(
            job_id=f"{request.job_id}-COPY-{request.attempt}",
            parent_job_id=request.job_id,
            run_id=request.run_id,
            attempt=request.attempt,
            task_id="T02",
            agent_id="AG-COPY",
            skill_name="rr-inago-copywriter",
            target_date=request.target_date,
            settings_version=request.settings_version,
            input_refs=request.input_refs,
            evidence_refs=request.evidence_refs,
            input_hash="fixture-copy-input",
            deadline_at=request.deadline_at,
            result_status="success",
            payload={
                "offer_key": target.offer_key,
                "draft_version": target.draft_version,
                "body": f"試験用本文 {target.offer_key}",
                "pattern_id": "P01",
                "evidence_refs": [input_ref],
                "selection_reason": "fixture input only",
                "used_input_refs": [input_ref],
                "omitted_unknowns": [
                    "price",
                    "stock",
                    "shipping",
                    "rating",
                    "promotion",
                ],
            },
            resolved_model="fixture-model",
            reasoning_setting="fixture",
            auth_mode="fixture",
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
            evidence_refs=request.evidence_refs,
            input_hash=compute_input_hash(request.input_payload),
            deadline_at=request.deadline_at,
            result_status="success",
            adoption_status="pending",
            payload={"status": "delegated"},
            error=None,
            artifact=None,
            subjobs=(child,),
            resolved_model="fixture-model",
            reasoning_setting="fixture",
            auth_mode="fixture",
            started_at="2026-10-12T13:01:00+09:00",
            finished_at="2026-10-12T13:01:01+09:00",
        )


class FakeMetricsPort:
    fixture_only = True

    def __init__(self, clock: FakeClock, *, advance_seconds: float = 0) -> None:
        self.clock = clock
        self.advance_seconds = advance_seconds
        self.started_at: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def collect(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        deadline_at: str,
    ) -> str:
        self.calls.append((run_id, deadline_at))
        self.started_at.append(self.clock.now_jst().isoformat())
        if self.advance_seconds:
            self.clock.advance(self.advance_seconds)
        return "observed"


@dataclass
class PortsBundle:
    ports: DailyPorts
    planner: FakeThemePlanner
    room: FakeRoomPort
    runner: FakeCopyRunner
    metrics: FakeMetricsPort
    cancellations: list[str]
    intake: FakeOfferIntake


def _make_ports(
    case: RuntimeCase,
    *,
    posts_per_search: int = 0,
    status: str = "observed",
    status_by_call: list[str] | None = None,
    finish_after_call: int | None = None,
    advance_seconds: float = 0,
    untried: bool = False,
    metrics_advance_seconds: float = 0,
    before_search=None,
    copy_spec_factory=None,
    offer_intake: FakeOfferIntake | None = None,
    fail_additional_once: bool = False,
    skip_first_theme_seed: bool = False,
) -> PortsBundle:
    planner = FakeThemePlanner(
        case.connection,
        untried=untried,
        fail_additional_once=fail_additional_once,
        skip_first_theme_seed=skip_first_theme_seed,
        clock=case.clock,
    )
    room = FakeRoomPort(
        case.clock,
        posts_per_search=posts_per_search,
        status=status,
        status_by_call=status_by_call,
        finish_after_call=finish_after_call,
        advance_seconds=advance_seconds,
        before_search=before_search,
    )
    runner = FakeCopyRunner()
    metrics = FakeMetricsPort(case.clock, advance_seconds=metrics_advance_seconds)
    cancellations: list[str] = []
    intake = offer_intake or FakeOfferIntake()

    def make_copy_spec(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        offer_key: str,
        theme_id: str,
        target_date: str,
        settings_version: str,
        deadline_at: str,
        deadline_monotonic: float,
    ) -> CopyGenerationSpec | None:
        if copy_spec_factory is not None:
            return copy_spec_factory(
                connection,
                run_id=run_id,
                offer_key=offer_key,
                theme_id=theme_id,
                target_date=target_date,
                settings_version=settings_version,
                deadline_at=deadline_at,
                deadline_monotonic=deadline_monotonic,
            )
        if (
            connection.execute(
                "SELECT 1 FROM offer WHERE offer_key=?", (offer_key,)
            ).fetchone()
            is None
        ):
            return None
        ref = f"INPUT-{offer_key.replace(':', '-')}-V1"
        return CopyGenerationSpec(
            run_id=run_id,
            offer_key=offer_key,
            draft_version=1,
            target_date=target_date,
            settings_version=settings_version,
            input_refs=(ref,),
            evidence_refs=(ref,),
            input_payload={
                "offer": {"offer_key": offer_key, "draft_version": 1},
                "purpose": "fixture copy generation",
                "facts": _unknown_facts(),
                "author_experience": {"availability": "unknown", "value": None},
            },
            deadline_at=deadline_at,
            deadline_monotonic=deadline_monotonic,
            generator_version="step13-fixture-copy-v1",
            pattern_version=settings_version,
        )

    def current_settings_version(connection: sqlite3.Connection) -> str:
        return connection.execute(
            "SELECT version FROM settings_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]

    current_settings_version.fixture_only = True
    make_copy_spec.fixture_only = True

    def cancel_owned(run_id: str, request_id: str | None = None) -> bool:
        cancellations.append(run_id)
        return True

    def cancel_metrics(run_id: str) -> bool:
        return True

    cancel_owned.fixture_only = True
    cancel_metrics.fixture_only = True

    ports = DailyPorts(
        current_settings_version=current_settings_version,
        theme_planner=planner,
        room_port=room,
        copy_spec_factory=make_copy_spec,
        copy_runner=runner,
        metrics_port=metrics,
        cancel_owned_jobs=cancel_owned,
        cancel_metrics_job=cancel_metrics,
        offer_intake=intake,
        fixture_only=True,
        cancel_room_request=cancel_owned,
        cancel_ai_request=cancel_owned,
    )
    return PortsBundle(ports, planner, room, runner, metrics, cancellations, intake)


def _run(case: RuntimeCase, bundle: PortsBundle, *, run_id: str = RUN_ID):
    return run_daily(
        case.connection,
        data_root=case.data_root,
        ports=bundle.ports,
        clock=case.clock,
        run_id_factory=lambda _date: run_id,
        seed_factory=lambda: "SAVED-SEED-STEP13",
    )


@pytest.mark.skipif(
    os.name != "nt", reason="STEP 13 requires the Windows msvcrt byte lock"
)
def test_windows_owned_process_lock_rejects_second_process_and_reacquires(
    runtime_case: RuntimeCase,
    tmp_path: Path,
) -> None:
    probe = ROOT / "tests_inago" / "fixtures" / "lock_probe.py"
    lock_path = runtime_case.data_root / ".daily-run.lock"
    owner_path = runtime_case.data_root / ".daily-run-owner.json"
    ready_path = tmp_path / "first-ready"
    release_path = tmp_path / "first-release"
    pid_path = tmp_path / "first-pid"
    first = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(probe),
            "--lock-path",
            str(lock_path),
            "--owner-path",
            str(owner_path),
            "--run-id",
            "PROCESS-RUN-1",
            "--ready-path",
            str(ready_path),
            "--pid-path",
            str(pid_path),
            "--release-path",
            str(release_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and time.monotonic() < deadline:
            if first.poll() is not None:
                pytest.fail(
                    first.stderr.read() if first.stderr else "lock probe exited"
                )
            time.sleep(0.02)
        assert ready_path.is_file()
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        assert owner["run_id"] == "PROCESS-RUN-1"
        assert owner["pid"] == int(pid_path.read_text(encoding="ascii"))
        assert owner["process_start_time"].endswith("+00:00")
        assert process_start_time(owner["pid"]) == owner["process_start_time"]

        blocked_run = _run(runtime_case, _make_ports(runtime_case))
        assert blocked_run.status == "locked"
        assert (
            runtime_case.connection.execute(
                "SELECT COUNT(*) FROM run WHERE target_date='2026-10-12' AND mode='daily'"
            ).fetchone()[0]
            == 0
        )

        second = subprocess.run(
            [
                sys.executable,
                "-B",
                str(probe),
                "--lock-path",
                str(lock_path),
                "--owner-path",
                str(owner_path),
                "--run-id",
                "PROCESS-RUN-2",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert second.returncode == 10
        assert json.loads(second.stdout)["status"] == "locked"
        assert json.loads(owner_path.read_text(encoding="utf-8")) == owner

        release_path.write_text("release", encoding="utf-8")
        assert first.wait(timeout=10) == 0
        third = subprocess.run(
            [
                sys.executable,
                "-B",
                str(probe),
                "--lock-path",
                str(lock_path),
                "--owner-path",
                str(owner_path),
                "--run-id",
                "PROCESS-RUN-3",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert third.returncode == 0
        third_record = json.loads(third.stdout)
        assert third_record["status"] == "acquired"
        assert third_record["owner"]["run_id"] == "PROCESS-RUN-3"
    finally:
        if first.poll() is None:
            release_path.write_text("release", encoding="utf-8")
            first.wait(timeout=10)


def test_run_lock_owner_is_atomic_and_same_process_reentry_is_rejected(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "daily.lock"
    owner_path = tmp_path / "daily-owner.json"
    with RunLock.acquire(lock_path, owner_path, run_id="LOCK-RUN-1") as lock:
        stored = json.loads(owner_path.read_text(encoding="utf-8"))
        assert stored == lock.owner.to_dict()
        assert stored["pid"] == os.getpid()
        assert stored["run_id"] == "LOCK-RUN-1"
        assert stored["process_start_time"].endswith("+00:00")
        with pytest.raises(RunLockBusy):
            RunLock.acquire(lock_path, owner_path, run_id="LOCK-RUN-2")
    assert not owner_path.exists()


def test_daily_before_1300_does_nothing_and_after_1800_runs(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(runtime_case)
    runtime_case.clock.current = datetime(2026, 10, 12, 12, 59, 59, tzinfo=JST)

    before = _run(runtime_case, bundle)

    assert before.status == "not_due"
    assert (
        runtime_case.connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0
    )
    assert bundle.room.requests == []
    assert bundle.metrics.calls == []

    runtime_case.clock.current = datetime(2026, 10, 12, 18, 0, tzinfo=JST)
    after_end = _run(runtime_case, bundle)

    assert after_end.status != "missed"
    assert after_end.committed_count == 0
    assert bundle.room.requests
    assert len(bundle.metrics.calls) == 1
    row = runtime_case.connection.execute(
        "SELECT target_date, status, attempt_used, committed_count, metrics_status FROM run"
    ).fetchone()
    assert row[0] == "2026-10-12"
    assert row[1] != "missed"
    assert row[2] > 0
    assert row[3] == 0
    assert row[4] == "observed"


def test_1750_start_applies_reports_then_continues_daily_search(
    runtime_case: RuntimeCase,
) -> None:
    runtime_case.clock.current = datetime(2026, 10, 12, 17, 50, tzinfo=JST)
    bundle = _make_ports(runtime_case)

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "shortfall"
    assert outcome.attempt_used > 0
    assert bundle.room.requests
    assert bundle.runner.requests == []
    assert bundle.metrics.started_at == ["2026-10-12T17:50:00+09:00"]
    assert (
        runtime_case.connection.execute(
            "SELECT metrics_status FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == "observed"
    )


@pytest.mark.parametrize("manual", [False, True])
def test_fixture_cli_daily_requires_explicit_context_and_respects_not_due(
    runtime_case: RuntimeCase, fixture_config_path: Path, manual: bool
) -> None:
    runtime_case.clock.current = datetime(2026, 10, 12, 12, 59, 59, tzinfo=JST)
    bundle = _make_ports(runtime_case)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        [
            "daily",
            *(["--manual"] if manual else []),
            "--config",
            str(fixture_config_path),
            "--data-root",
            str(runtime_case.data_root),
        ],
        ports=DailyCLIContext(
            connection=runtime_case.connection,
            data_root=runtime_case.data_root,
            ports=bundle.ports,
            clock=runtime_case.clock,
        ),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == (5 if manual else 0)
    assert json.loads(stdout.getvalue())["status"] == (
        "shortfall" if manual else "not_due"
    )
    assert stderr.getvalue() == ""
    assert runtime_case.connection.execute("SELECT COUNT(*) FROM run").fetchone()[
        0
    ] == int(manual)


def test_fixture_cli_rejects_database_outside_validated_data_root(
    runtime_case: RuntimeCase,
    fixture_config_path: Path,
    tmp_path: Path,
) -> None:
    external_database = sqlite3.connect(tmp_path / "outside-fixture.sqlite")
    try:
        stdout = StringIO()
        stderr = StringIO()
        exit_code = run_cli(
            [
                "daily",
                "--config",
                str(fixture_config_path),
                "--data-root",
                str(runtime_case.data_root),
            ],
            ports=DailyCLIContext(
                connection=external_database,
                data_root=runtime_case.data_root,
                ports=_make_ports(runtime_case).ports,
                clock=runtime_case.clock,
            ),
            stdout=stdout,
            stderr=stderr,
        )
        assert exit_code == 4
        assert json.loads(stderr.getvalue()) == {
            "status": "runtime_error",
            "reason": "PathContractError",
        }
        assert stdout.getvalue() == ""
        assert (
            external_database.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            == 0
        )
        assert (
            runtime_case.connection.execute("SELECT COUNT(*) FROM run").fetchone()[0]
            == 0
        )
    finally:
        external_database.close()


def test_fixture_cli_rejects_unmarked_or_non_fixture_adapters(
    runtime_case: RuntimeCase,
    fixture_config_path: Path,
) -> None:
    bundle = _make_ports(runtime_case)
    stdout = StringIO()
    stderr = StringIO()
    non_fixture_ports = replace(bundle.ports, room_port=object())

    exit_code = run_cli(
        [
            "daily",
            "--config",
            str(fixture_config_path),
            "--data-root",
            str(runtime_case.data_root),
        ],
        ports=DailyCLIContext(
            connection=runtime_case.connection,
            data_root=runtime_case.data_root,
            ports=non_fixture_ports,
            clock=runtime_case.clock,
        ),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 4
    assert json.loads(stderr.getvalue()) == {
        "status": "runtime_error",
        "reason": "PathContractError",
    }
    assert stdout.getvalue() == ""
    assert (
        runtime_case.connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0
    )


def test_expired_pending_metrics_are_finalized_on_restart_without_recollection(
    runtime_case: RuntimeCase,
) -> None:
    runtime_case.clock.current = datetime(2026, 10, 12, 17, 50, tzinfo=JST)
    first_bundle = _make_ports(runtime_case)
    initial = _run(runtime_case, first_bundle)
    assert initial.metrics_status == "observed"
    assert len(first_bundle.metrics.calls) == 1

    row = runtime_case.connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
    ).fetchone()
    checkpoint = json.loads(row["checkpoint"])
    checkpoint["daily_orchestration"]["metrics_status"] = "pending"
    checkpoint["daily_orchestration"]["phase"] = "initial"
    checkpoint["daily_orchestration"].pop("terminal_status", None)
    runtime_case.connection.execute(
        "UPDATE run SET status='partial', metrics_status='pending', checkpoint=? WHERE run_id=?",
        (json.dumps(checkpoint), RUN_ID),
    )
    runtime_case.connection.commit()
    runtime_case.clock.current = datetime(2026, 10, 12, 18, 0, tzinfo=JST)
    resumed_bundle = _make_ports(runtime_case)

    resumed = _run(runtime_case, resumed_bundle)

    assert resumed.metrics_status == "unavailable"
    assert resumed_bundle.metrics.calls == []
    persisted = runtime_case.connection.execute(
        "SELECT metrics_status, checkpoint FROM run WHERE run_id=?", (RUN_ID,)
    ).fetchone()
    assert persisted["metrics_status"] == "unavailable"
    assert (
        json.loads(persisted["checkpoint"])["daily_orchestration"]["metrics_status"]
        == "unavailable"
    )


def test_metrics_call_is_read_only_cancelled_at_request_timeout_and_late_value_is_ignored(
    runtime_case: RuntimeCase,
) -> None:
    runtime_case.clock.current = datetime(2026, 10, 12, 17, 59, tzinfo=JST)
    bundle = _make_ports(runtime_case)
    cancelled = threading.Event()
    returned = threading.Event()
    metrics_deadlines: list[str] = []
    metrics_cancellations: list[str] = []
    original_collect = bundle.metrics.collect

    def blocked_collect(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        deadline_at: str,
    ) -> str:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT target_date FROM run WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == "2026-10-12"
        )
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "UPDATE run SET metrics_status='observed' WHERE run_id=?", (run_id,)
            )
        metrics_deadlines.append(deadline_at)
        deadline = datetime.fromisoformat(deadline_at)
        seconds_remaining = (deadline - runtime_case.clock.now_jst()).total_seconds()
        runtime_case.clock.advance(seconds_remaining + 1)
        cancelled.wait(timeout=5)
        try:
            return original_collect(connection, run_id=run_id, deadline_at=deadline_at)
        finally:
            returned.set()

    def cancel_metrics(run_id: str) -> bool:
        metrics_cancellations.append(run_id)
        cancelled.set()
        return True

    bundle.metrics.collect = blocked_collect
    bundle.ports = replace(bundle.ports, cancel_metrics_job=cancel_metrics)

    outcome = _run(runtime_case, bundle)

    assert outcome.metrics_status == "unavailable"
    assert cancelled.is_set()
    assert metrics_cancellations == [RUN_ID]
    assert returned.wait(timeout=3)
    assert (
        runtime_case.connection.execute(
            "SELECT metrics_status FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == "unavailable"
    )
    assert bundle.metrics.calls == [(RUN_ID, metrics_deadlines[0])]
    assert runtime_case.clock.now_jst() > datetime.fromisoformat(metrics_deadlines[0])


def test_three_theme_five_each_completes_15_and_starts_metrics_immediately(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(
        runtime_case,
        posts_per_search=5,
        finish_after_call=3,
        advance_seconds=0,
    )

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert outcome.attempt_used == 3
    assert len(bundle.room.requests) == 3
    assert len(bundle.runner.requests) == 15
    assert bundle.metrics.started_at == ["2026-10-12T13:00:00+09:00"]
    assert bundle.metrics.calls[0][1] == "2026-10-12T13:05:00+09:00"
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM theme_selection WHERE run_id=? AND event_kind='initial'",
            (RUN_ID,),
        ).fetchone()[0]
        == 3
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == 15
    )
    theme_counts = runtime_case.connection.execute(
        """
        SELECT offer.main_theme_id, COUNT(*)
          FROM draft JOIN offer USING (offer_key)
         WHERE draft.run_id=?
         GROUP BY offer.main_theme_id
        """,
        (RUN_ID,),
    ).fetchall()
    assert sorted(int(row[1]) for row in theme_counts) == [5, 5, 5]
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM reservation WHERE status='active'"
        ).fetchone()[0]
        == 15
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM search_attempt WHERE run_id=? AND max_posts=25",
            (RUN_ID,),
        ).fetchone()[0]
        == 3
    )


def test_order_is_related_then_theme_fallback_and_all_untried_themes(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(runtime_case, untried=True, posts_per_search=0)
    outcome = _run(runtime_case, bundle)
    queries = [request.query for request in bundle.room.requests]
    assert outcome.status == "shortfall" and outcome.committed_count == 0
    assert outcome.attempt_used == 61
    assert len(queries) == 61 and len(set(queries)) == 61
    assert len(set(bundle.planner.calls)) == 30
    assert [item.query_kind for item in bundle.room.requests[:3]] == [
        "related",
        "related",
        "theme_fallback",
    ]
    assert len({item.parent_refs[0] for item in bundle.room.requests[:3]}) == 1
    assert all(request.max_posts == 25 for request in bundle.room.requests)
    assert bundle.metrics.calls


def test_missing_offer_is_intaken_from_fixture_snapshot_before_copy(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(
        runtime_case,
        posts_per_search=5,
        skip_first_theme_seed=True,
    )

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert len(bundle.intake.requests) == 5
    assert all(
        item.theme_id == bundle.intake.requests[0].theme_id
        for item in bundle.intake.requests
    )
    assert all(
        item.offer_key
        == f"{item.offer_key.split(':')[0]}:{item.offer_key.split(':')[1]}"
        for item in bundle.intake.requests
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM offer WHERE main_theme_id=?",
            (bundle.intake.requests[0].theme_id,),
        ).fetchone()[0]
        == 5
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == 15
    )


def test_legacy_identity_offer_is_refreshed_before_candidate_selection(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    connection.execute(
        """INSERT INTO run(
               run_id,target_date,mode,status,settings_version,seed,started_at,
               draft_deadline,end_deadline,attempt_used,committed_count,checkpoint,
               metrics_status,finished_at
           ) VALUES (
               'RUN-LEGACY-INTAKE','2026-10-12','daily','running',?, 'seed',
               ?,?,?,0,0,NULL,'not_started',NULL
           )""",
        (SETTINGS_VERSION, NOW, NOW, NOW),
    )
    connection.execute(
        """INSERT INTO search_attempt(
               attempt_id,run_id,query,parent_refs_json,level,attempt_no,started_at,
               ended_at,source_status,source_order,max_posts,retry_of,error_code
           ) VALUES (
               'SEARCH-LEGACY-INTAKE','RUN-LEGACY-INTAKE','query','[]','parent',
               1,?,?, 'observed','internal',50,NULL,NULL
           )""",
        (NOW, NOW),
    )
    connection.execute(
        """INSERT INTO source_post(
               source_key,stable_room_id,room_url,attempt_id,order_index,likes,
               comments,item_url,image_ref,actor_ref,published_at,evidence_ref,
               observed_at,availability
           ) VALUES (
               'source-legacy-1','ROOM-1','https://room.rakuten.co.jp/test/item/1',
               'SEARCH-LEGACY-INTAKE',1,300,NULL,
               'https://item.rakuten.co.jp/shop-a/item-a/','image-1',NULL,NULL,
               'search-row-1',?,'observed'
           )""",
        (NOW,),
    )
    connection.execute(
        """INSERT INTO offer(
               offer_key,shop_id,item_id,name,url,image_refs_json,main_theme_id,
               aux_theme_ids_json,first_seen,last_seen,facts_json
           ) VALUES (
               'shop-a:item-a','shop-a','item-a','legacy identity only',
               'https://item.rakuten.co.jp/shop-a/item-a/','[]',
               ?,'[]',?,?, '[]'
           )""",
        (LEGACY_UNASSIGNED_THEME_ID, NOW, NOW),
    )
    connection.commit()

    class RefreshingIntake:
        fixture_only = True

        def __init__(self) -> None:
            self.requests: list[OfferIntakeRequest] = []

        def persist_offer(self, db, request: OfferIntakeRequest) -> None:
            self.requests.append(request)
            existing_theme = db.execute(
                "SELECT main_theme_id FROM offer WHERE offer_key=?",
                (request.offer_key,),
            ).fetchone()[0]
            assigned_theme = (
                request.theme_id
                if existing_theme == LEGACY_UNASSIGNED_THEME_ID
                else existing_theme
            )
            db.execute(
                "UPDATE offer SET main_theme_id=?,last_seen=?,facts_json=? "
                "WHERE offer_key=?",
                (
                    assigned_theme,
                    request.observed_at,
                    json.dumps(
                        [
                            {
                                "field": "name",
                                "value": None,
                                "availability": "unknown",
                                "source_ref": request.evidence_ref,
                                "observed_at": request.observed_at,
                                "original_display": None,
                            }
                        ]
                    ),
                    request.offer_key,
                ),
            )

    intake = RefreshingIntake()
    _intake_missing_offers(
        connection,
        run_id="RUN-LEGACY-INTAKE",
        settings_version=SETTINGS_VERSION,
        theme_id="G01-T01",
        attempt_id="SEARCH-LEGACY-INTAKE",
        identities={"source-legacy-1": "shop-a:item-a"},
        port=intake,
    )

    assert len(intake.requests) == 1
    assert intake.requests[0].offer_key == "shop-a:item-a"
    assert intake.requests[0].item_url == "https://item.rakuten.co.jp/shop-a/item-a/"
    assert (
        connection.execute(
            "SELECT main_theme_id FROM offer WHERE offer_key='shop-a:item-a'"
        ).fetchone()[0]
        == "G01-T01"
    )
    _intake_missing_offers(
        connection,
        run_id="RUN-LEGACY-INTAKE",
        settings_version=SETTINGS_VERSION,
        theme_id="G01-T01",
        attempt_id="SEARCH-LEGACY-INTAKE",
        identities={"source-legacy-1": "shop-a:item-a"},
        port=intake,
    )
    assert len(intake.requests) == 1

    # The saved observation is still October 13 in JST, while the next search
    # timestamp has a UTC date of October 13 but is already October 14 in JST.
    connection.execute(
        "UPDATE offer SET last_seen='2026-10-13T23:59:00+09:00' "
        "WHERE offer_key='shop-a:item-a'"
    )
    connection.execute(
        "UPDATE source_post SET observed_at='2026-10-13T15:00:00+00:00' "
        "WHERE source_key='source-legacy-1'"
    )
    connection.commit()
    _intake_missing_offers(
        connection,
        run_id="RUN-LEGACY-INTAKE",
        settings_version=SETTINGS_VERSION,
        theme_id="G02-T05",
        attempt_id="SEARCH-LEGACY-INTAKE",
        identities={"source-legacy-1": "shop-a:item-a"},
        port=intake,
    )
    assert len(intake.requests) == 2
    assert intake.requests[-1].observed_at == "2026-10-13T15:00:00+00:00"
    assert (
        connection.execute(
            "SELECT main_theme_id FROM offer WHERE offer_key='shop-a:item-a'"
        ).fetchone()[0]
        == "G01-T01"
    )


def test_additional_theme_plan_failure_reuses_same_selection_event_on_resume(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(
        runtime_case,
        posts_per_search=0,
        fail_additional_once=True,
    )

    first = _run(runtime_case, bundle)
    first_event = runtime_case.connection.execute(
        "SELECT count_event_id, theme_id FROM theme_selection "
        "WHERE run_id=? AND event_kind='additional'",
        (RUN_ID,),
    ).fetchone()
    checkpoint = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]

    assert first.status == "failed"
    assert first_event[0].endswith("additional:01")
    assert checkpoint["additional_events"] == 0
    assert checkpoint["phase"] != "finished"

    resumed = _run(runtime_case, bundle)

    additional_calls = [
        call for call in bundle.planner.calls if call[0] == "additional"
    ]
    assert resumed.resumed is True
    assert additional_calls[0] == additional_calls[1]
    assert additional_calls[0][2] == 1
    assert additional_calls[1][2] == 1
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM theme_selection WHERE run_id=? AND event_kind='additional' "
            "AND count_event_id LIKE '%additional:01'",
            (RUN_ID,),
        ).fetchone()[0]
        == 1
    )
    resumed_state = json.loads(
        runtime_case.connection.execute(
            "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
    )["daily_orchestration"]
    assert resumed_state["additional_events"] >= 1


def test_unavailable_search_retries_same_query_three_times_then_stops_source(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(runtime_case, status="unavailable")
    outcome = _run(runtime_case, bundle)
    assert outcome.status == "failed"
    assert outcome.attempt_used == 3 and len(bundle.room.requests) == 3
    assert len({request.query for request in bundle.room.requests}) == 1
    assert [request.retry_of for request in bundle.room.requests] == [
        None,
        bundle.room.requests[0].attempt_id,
        bundle.room.requests[1].attempt_id,
    ]
    assert (
        runtime_case.connection.execute(
            "SELECT attempt_used FROM run WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == 3
    )
    assert bundle.metrics.calls


def test_fourteen_committed_bodies_resume_same_run_seed_and_search_results(
    runtime_case: RuntimeCase,
) -> None:
    state = {"built": 0, "raised": False}

    def interrupt_at_fifteenth(
        connection: sqlite3.Connection, **values: Any
    ) -> CopyGenerationSpec:
        state["built"] += 1
        if state["built"] == 15 and not state["raised"]:
            state["raised"] = True
            raise RuntimeError("fixture interruption after fourteen commits")
        offer_key = values["offer_key"]
        ref = f"INPUT-{offer_key.replace(':', '-')}-V1"
        return CopyGenerationSpec(
            run_id=values["run_id"],
            offer_key=offer_key,
            draft_version=1,
            target_date=values["target_date"],
            settings_version=values["settings_version"],
            input_refs=(ref,),
            evidence_refs=(ref,),
            input_payload={
                "offer": {"offer_key": offer_key, "draft_version": 1},
                "purpose": "fixture copy generation",
                "facts": _unknown_facts(),
                "author_experience": {"availability": "unknown", "value": None},
            },
            deadline_at=values["deadline_at"],
            deadline_monotonic=values["deadline_monotonic"],
            generator_version="step13-fixture-copy-v1",
            pattern_version=values["settings_version"],
        )

    bundle = _make_ports(
        runtime_case,
        posts_per_search=5,
        copy_spec_factory=interrupt_at_fifteenth,
    )
    first = _run(runtime_case, bundle)
    before = runtime_case.connection.execute(
        "SELECT run_id, target_date, settings_version, seed, attempt_used, committed_count, checkpoint FROM run WHERE run_id=?",
        (RUN_ID,),
    ).fetchone()
    assert first.status == "partial"
    assert first.committed_count == 14
    assert len(bundle.room.requests) == 3
    assert len(bundle.runner.requests) == 14
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM candidate_decision "
            "WHERE run_id=? AND rule_version='room-identity-v1'",
            (RUN_ID,),
        ).fetchone()[0]
        == 15
    )
    assert before[5] == 14

    resumed = _run(runtime_case, bundle)

    after = runtime_case.connection.execute(
        "SELECT run_id, target_date, settings_version, seed, attempt_used, committed_count FROM run WHERE target_date='2026-10-12' AND mode='daily'"
    ).fetchone()
    assert resumed.status == "complete"
    assert resumed.committed_count == 15
    assert tuple(after[:5]) == tuple(before[:5])
    assert after[5] == 15
    assert len(bundle.room.requests) == 3
    assert len(bundle.runner.requests) == 15
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM run WHERE target_date='2026-10-12' AND mode='daily'"
        ).fetchone()[0]
        == 1
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM theme_selection WHERE run_id=? AND event_kind='initial'",
            (RUN_ID,),
        ).fetchone()[0]
        == 3
    )
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM candidate_decision "
            "WHERE run_id=? AND rule_version='room-identity-v1'",
            (RUN_ID,),
        ).fetchone()[0]
        == 15
    )


def test_daily_flow_continues_after_1745_and_1800(
    runtime_case: RuntimeCase,
) -> None:
    runtime_case.clock.current = datetime(2026, 10, 12, 17, 44, 30, tzinfo=JST)
    bundle = _make_ports(
        runtime_case,
        posts_per_search=5,
        finish_after_call=1,
        advance_seconds=30,
    )

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert outcome.attempt_used >= 3
    assert len(bundle.room.requests) >= 3
    assert len(bundle.runner.requests) == 15
    assert bundle.cancellations == []
    assert bundle.metrics.started_at == ["2026-10-12T17:45:00+09:00"]

    later_case = RuntimeCase(
        connection=runtime_case.connection,
        data_root=runtime_case.data_root,
        clock=FakeClock(datetime(2026, 10, 13, 18, 0, tzinfo=JST)),
    )
    later_bundle = _make_ports(later_case)
    after_end = _run(later_case, later_bundle, run_id="STEP13-RUN-NEXT")
    assert after_end.status != "missed"
    assert later_bundle.room.requests
    assert len(later_bundle.metrics.calls) == 1


@pytest.mark.parametrize("elapsed_seconds", [485, 1801, 17101])
def test_room_observation_has_no_overall_time_limit(
    runtime_case: RuntimeCase,
    elapsed_seconds: int,
) -> None:
    bundle = _make_ports(
        runtime_case,
        posts_per_search=5,
        finish_after_call=1,
        advance_seconds=elapsed_seconds,
    )

    outcome = _run(runtime_case, bundle)

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert len(bundle.room.requests) == 3
    assert bundle.cancellations == []
    assert all(request.deadline_at is None for request in bundle.room.requests)
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM search_attempt WHERE run_id=? AND source_status='unavailable'",
            (RUN_ID,),
        ).fetchone()[0]
        == 0
    )


def test_blocked_ai_copy_uses_request_timeout_and_discards_late_body(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=1)
    cancelled = threading.Event()
    late_returned = threading.Event()
    original_run = bundle.runner.run
    blocked_requests: list[AIJobRequest] = []

    def blocked_run(request: AIJobRequest) -> AIJobResult:
        blocked_requests.append(request)
        if len(blocked_requests) == 1:
            deadline = datetime.fromisoformat(request.deadline_at)
            seconds_remaining = (
                deadline - runtime_case.clock.now_jst()
            ).total_seconds()
            runtime_case.clock.advance(seconds_remaining + 1)
            cancelled.wait(timeout=5)
            try:
                return original_run(request)
            finally:
                late_returned.set()
        return original_run(request)

    bundle.runner.run = blocked_run

    def cancel(run_id: str, request_id: str) -> bool:
        bundle.cancellations.append(run_id)
        cancelled.set()
        return True

    bundle.ports = replace(bundle.ports, cancel_ai_request=cancel)
    outcome = _run(runtime_case, bundle)
    assert cancelled.is_set()
    assert outcome.committed_count > 0
    assert len(bundle.room.requests) > 1
    assert len(bundle.runner.requests) > 1
    assert bundle.metrics.started_at == ["2026-10-12T13:08:01+09:00"]
    assert blocked_requests
    blocked_offer_key = blocked_requests[0].copy_targets[0].offer_key
    attempt = runtime_case.connection.execute(
        "SELECT outcome, error_code FROM generation_attempt "
        "WHERE run_id=? AND offer_key=? ORDER BY attempt_no LIMIT 1",
        (RUN_ID, blocked_offer_key),
    ).fetchone()
    assert tuple(attempt) == ("error", "timed_out")
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=? AND offer_key=?",
            (RUN_ID, blocked_offer_key),
        ).fetchone()[0]
        == 0
    )
    assert late_returned.wait(timeout=3)
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=? AND offer_key=?",
            (RUN_ID, blocked_offer_key),
        ).fetchone()[0]
        == 0
    )


def test_new_offer_intake_failure_does_not_mark_search_processed_and_resumes_same_attempt(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=5, skip_first_theme_seed=True)
    bundle.ports = replace(bundle.ports, offer_intake=None)

    interrupted = _run(runtime_case, bundle)

    assert interrupted.status == "failed"
    assert interrupted.attempt_used == 1
    assert interrupted.committed_count == 0
    assert len(bundle.room.requests) == 1
    checkpoint_row = runtime_case.connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", (RUN_ID,)
    ).fetchone()
    state = json.loads(checkpoint_row["checkpoint"])["daily_orchestration"]
    assert state["processed_attempts"] == []
    assert state["handled_offer_keys"] == []
    assert (
        runtime_case.connection.execute(
            "SELECT COUNT(*) FROM draft WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )

    bundle.ports = replace(bundle.ports, offer_intake=bundle.intake)
    resumed = _run(runtime_case, bundle)

    assert resumed.resumed is True
    assert resumed.status == "complete"
    assert resumed.committed_count == 15
    assert len(bundle.room.requests) == 3
    assert len(bundle.intake.requests) == 5


def test_pending_historical_reports_are_applied_before_search(
    runtime_case: RuntimeCase,
) -> None:
    connection = runtime_case.connection
    report_offer, shop_id, item_id = _insert_offer(
        connection, theme_id="G01-T01", item_number=90
    )
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('OLD-RUN-STEP13', '2026-10-11', 'daily', 'complete', ?, 'OLD-SEED',
                '2026-10-11T13:00:00+09:00', '2026-10-11T17:45:00+09:00',
                '2026-10-11T18:00:00+09:00', 0, 1, NULL, 'unavailable',
                '2026-10-11T18:00:00+09:00')
        """,
        (SETTINGS_VERSION,),
    )
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES ('OLD-DRAFT-STEP13', ?, 'OLD-RUN-STEP13', 1, 'old fixture body',
                '2026-10-11T13:01:00+09:00', '2026-10-11T13:01:01+09:00',
                'fixture', ?, 'committed', NULL, 'P01', '[]')
        """,
        (report_offer, SETTINGS_VERSION),
    )
    connection.execute(
        """
        INSERT INTO human_action
            (action_id, action, offer_key, draft_id, draft_version, prior_revision,
             receipt_revision, reason, reported_at, actual_posted_at, old_event_ref,
             new_body, apply_status, applied_at, applied_run_id, apply_error)
        VALUES ('OLD-POST-STEP13', 'posted', ?, 'OLD-DRAFT-STEP13', 1, 0, 1,
                NULL, '2026-10-11T21:30:00+09:00', NULL, NULL, NULL,
                'pending', NULL, NULL, NULL)
        """,
        (report_offer,),
    )

    database_path = Path(connection.execute("PRAGMA database_list").fetchone()[2])

    def assert_applied_before_search(request: RoomSearchRequest) -> None:
        observer = sqlite3.connect(database_path)
        try:
            row = observer.execute(
                "SELECT apply_status, applied_run_id FROM human_action "
                "WHERE action_id='OLD-POST-STEP13'"
            ).fetchone()
            assert row == ("applied", RUN_ID)
            assert (
                observer.execute(
                    "SELECT effective FROM exclusion_key WHERE offer_key=?",
                    (report_offer,),
                ).fetchone()[0]
                == 1
            )
            assert request.attempt_no >= 1
        finally:
            observer.close()

    bundle = _make_ports(
        runtime_case, posts_per_search=0, before_search=assert_applied_before_search
    )
    outcome = _run(runtime_case, bundle)

    assert outcome.status == "shortfall"
    assert bundle.room.requests[0].attempt_no == 1
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM publication_event WHERE offer_key=? AND valid=1",
            (report_offer,),
        ).fetchone()[0]
        == 1
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM search_attempt WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == 60
    )
