"""Durable Trends, AG-KEY, and saved search-plan composition for daily runs."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from automation.inago.clock import Clock
from automation.inago.contracts import AIJobRequest, AIJobResult
from automation.inago.orchestrator import (
    DailyOrchestrationError,
    DailySearchPlan,
    DailyThemePlan,
)
from automation.inago.repository import (
    accept_pending_key_job_result,
    load_pending_key_job_result,
    persist_pending_key_job_result,
)
from automation.inago.trends_adapter import (
    BrowserSnapshotPort,
    DurableGroupKeywordSession,
    TrendsContractError,
    grouped_key_input,
    load_grouped_snapshot,
    save_grouped_snapshot,
    validate_grouped_key_response,
)


class AIKeywordRunner(Protocol):
    def run(self, request: AIJobRequest) -> AIJobResult:
        """Run one T02 AI-O request whose only permitted specialist is AG-KEY."""


class DurableTrendsThemePlanner:
    """Connect public Trends attempts and the same daily AI-O root to search plans.

    The injected AI runner is the daily root already owned by the Python
    controller. It delegates the observed Top20 to AG-KEY inside that execution;
    this planner never starts a separate CLI process or network service.
    """

    def __init__(
        self,
        trends_port: BrowserSnapshotPort,
        key_runner: AIKeywordRunner,
        clock: Clock,
    ) -> None:
        self.trends_port = trends_port
        self.key_runner = key_runner
        self.clock = clock
        self.fixture_only = bool(
            getattr(trends_port, "fixture_only", False)
            and getattr(key_runner, "fixture_only", False)
        )

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
        run = connection.execute(
            "SELECT target_date,draft_deadline,settings_version,status FROM run "
            "WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            run is None
            or run[2] != settings_version
            or run[3] != "running"
            or event_kind not in {"initial", "additional"}
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 1
        ):
            raise DailyOrchestrationError("theme_planner_run_identity_invalid")
        theme_word = self._theme_word(connection, settings_version, theme_id)
        acquisition_id = f"{run_id}:{event_kind}:{ordinal:02d}:{theme_id}"
        session = DurableGroupKeywordSession(
            connection,
            self.trends_port,
            self.clock,
            run_id=run_id,
            settings_version=settings_version,
            event_kind=event_kind,
            ordinal=ordinal,
            theme_id=theme_id,
            seed=seed,
            deadline_at=str(run[1]),
            acquisition_id=acquisition_id,
        )
        acquisition = session.fetch(theme_word)
        if acquisition.status == "exhausted":
            return self._plan(
                theme_id,
                theme_word,
                event_kind,
                ordinal,
                acquisition_id,
                status="exhausted",
            )
        if acquisition.status == "deadline":
            return self._plan(
                theme_id,
                theme_word,
                event_kind,
                ordinal,
                acquisition_id,
                status="deadline",
            )
        if acquisition.snapshot is None:
            raise TrendsContractError("successful Trends acquisition has no snapshot")

        snapshot = acquisition.snapshot
        stored = load_grouped_snapshot(
            connection, snapshot.snapshot_id, request=snapshot.request
        )
        if stored is None:
            save_grouped_snapshot(connection, snapshot)
            stored = load_grouped_snapshot(
                connection, snapshot.snapshot_id, request=snapshot.request
            )
        if stored is None:
            raise TrendsContractError("raw Trends snapshot was not persisted")
        key_result = None
        if stored.decisions is None:
            if snapshot.words:
                if session.expire_pending_response_at_deadline():
                    return self._plan(
                        theme_id,
                        theme_word,
                        event_kind,
                        ordinal,
                        acquisition_id,
                        status="deadline",
                    )
                if self.fixture_only:
                    key_result = None
                else:
                    key_result = load_pending_key_job_result(
                        connection,
                        run_id=run_id,
                        target_date=str(run[0]),
                        settings_version=settings_version,
                        snapshot_id=snapshot.snapshot_id,
                        data_root=getattr(self.key_runner, "data_root", ""),
                    )
                if key_result is None:
                    response, key_result = self._run_key_job(
                        connection=connection,
                        run=run,
                        run_id=run_id,
                        settings_version=settings_version,
                        event_kind=event_kind,
                        ordinal=ordinal,
                        snapshot=snapshot,
                        key_input=grouped_key_input(snapshot),
                    )
                else:
                    response = dict(key_result.subjobs[0].payload)
                decisions = validate_grouped_key_response(snapshot, response)
            else:
                decisions = ()
            if session.expire_pending_response_at_deadline():
                return self._plan(
                    theme_id,
                    theme_word,
                    event_kind,
                    ordinal,
                    acquisition_id,
                    status="deadline",
                )
            if (
                key_result is not None
                and getattr(key_result, "artifact", None) is not None
                and not self.fixture_only
            ):
                persist_pending_key_job_result(connection, key_result)
            if key_result is not None and getattr(key_result, "artifact", None):
                self._save_grouping_with_key_result(
                    connection,
                    snapshot,
                    decisions=decisions,
                    seed=seed,
                    result=key_result,
                )
            else:
                save_grouped_snapshot(
                    connection, snapshot, decisions=decisions, seed=seed
                )

        if session.expire_pending_response_at_deadline():
            return self._plan(
                theme_id,
                theme_word,
                event_kind,
                ordinal,
                acquisition_id,
                status="deadline",
            )
        session.adopt_snapshot(snapshot)
        adopted = session.fetch(theme_word)
        if adopted.status == "deadline":
            return self._plan(
                theme_id,
                theme_word,
                event_kind,
                ordinal,
                acquisition_id,
                status="deadline",
            )
        if adopted.status not in {"observed", "normal_zero"}:
            raise TrendsContractError("saved Trends snapshot was not adopted")
        if adopted.snapshot is None:
            raise TrendsContractError("adopted Trends acquisition has no snapshot")
        saved = load_grouped_snapshot(
            connection,
            adopted.snapshot.snapshot_id,
            request=adopted.snapshot.request,
        )
        if saved is None or saved.decisions is None or saved.seed != seed:
            raise TrendsContractError("adopted KEY result or draw is missing")
        return self._plan(
            theme_id,
            theme_word,
            event_kind,
            ordinal,
            acquisition_id,
            status="observed",
            snapshot_ref=saved.snapshot.snapshot_id,
            selected=tuple((word.word_id, word.raw) for word in saved.selected),
        )

    def _run_key_job(
        self,
        *,
        connection: sqlite3.Connection,
        run,
        run_id: str,
        settings_version: str,
        event_kind: str,
        ordinal: int,
        snapshot,
        key_input: dict[str, Any],
    ) -> tuple[dict[str, Any], AIJobResult]:
        now = self.clock.now_jst()
        deadline_at = datetime.fromisoformat(str(run[1]))
        deadline_seconds = max(
            0.0,
            (deadline_at.astimezone(now.tzinfo) - now).total_seconds(),
        )
        target_date = str(run[0])
        base_job_id = (
            f"KEY-{target_date.replace('-', '')}-"
            f"{'I' if event_kind == 'initial' else 'A'}-{ordinal:02d}"
        )
        attempt, job_id = self._next_key_job_identity(
            connection,
            run_id=run_id,
            snapshot_id=snapshot.snapshot_id,
            base_job_id=base_job_id,
        )

        def build_request(job_attempt: int, job_identity: str) -> AIJobRequest:
            return AIJobRequest(
                job_id=job_identity,
                run_id=run_id,
                attempt=job_attempt,
                task_id="T02",
                target_date=target_date,
                settings_version=settings_version,
                input_refs=(snapshot.snapshot_id,),
                evidence_refs=(),
                deadline_at=deadline_at.isoformat(),
                deadline_monotonic=self.clock.monotonic() + deadline_seconds,
                input_payload={
                    "keywords": key_input,
                    "word_ids": [item["word_id"] for item in key_input["words"]],
                },
                trigger="scheduled_daily",
                explicit_user_request=False,
                source_kind="daily",
            )

        request = build_request(attempt, job_id)
        result: AIJobResult | None = None
        recover_success = getattr(self.key_runner, "recover_successful_result", None)
        if not self.fixture_only and callable(recover_success) and attempt > 1:
            recovered_attempt = attempt - 1
            recovered_job_id = (
                base_job_id
                if recovered_attempt == 1
                else f"{base_job_id}-r{recovered_attempt:02d}"
            )
            recovered_request = build_request(recovered_attempt, recovered_job_id)
            result = recover_success(recovered_request)
            if result is not None:
                request = recovered_request
        if result is None:
            result = self.key_runner.run(request)
        if getattr(result, "result_status", None) != "success":
            raise DailyOrchestrationError("daily_key_job_not_accepted")
        if self.fixture_only:
            if getattr(result, "adoption_status", None) != "accepted":
                raise DailyOrchestrationError("daily_key_job_not_accepted")
        elif getattr(result, "adoption_status", None) != "pending":
            raise DailyOrchestrationError("daily_key_job_not_accepted")
        subjobs = list(getattr(result, "subjobs", ()))
        if (
            len(subjobs) != 1
            or getattr(subjobs[0], "agent_id", None) != "AG-KEY"
            or getattr(subjobs[0], "task_id", None) != "T02"
            or getattr(subjobs[0], "result_status", None) != "success"
            or not isinstance(getattr(subjobs[0], "payload", None), dict)
        ):
            raise DailyOrchestrationError("daily_key_subjob_missing_or_invalid")
        if not self.fixture_only and (
            getattr(result, "job_id", None) != request.job_id
            or getattr(result, "run_id", None) != run_id
            or getattr(result, "attempt", None) != request.attempt
            or getattr(result, "task_id", None) != "T02"
            or getattr(result, "agent_id", None) != "AI-O"
            or getattr(result, "skill_name", None) != "rakuten-room-inago"
            or getattr(result, "settings_version", None) != settings_version
            or tuple(getattr(result, "input_refs", ())) != (snapshot.snapshot_id,)
            or getattr(result, "artifact", None) is None
        ):
            raise DailyOrchestrationError("daily_key_root_identity_mismatch")
        return dict(subjobs[0].payload), result

    def _next_key_job_identity(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        snapshot_id: str,
        base_job_id: str,
    ) -> tuple[int, str]:
        refs_json = json.dumps([snapshot_id], ensure_ascii=False, separators=(",", ":"))
        stored = connection.execute(
            "SELECT COALESCE(MAX(attempt),0) FROM ai_job WHERE run_id=? "
            "AND task_id='T02' AND agent_id='AI-O' AND input_refs_json=?",
            (run_id, refs_json),
        ).fetchone()
        last_attempt = int(stored[0]) if stored else 0
        data_root = getattr(self.key_runner, "data_root", None)
        if isinstance(data_root, (str, Path)):
            root = Path(data_root).expanduser().resolve()
            artifact_dir = root / "artifacts" / "daily"
            if artifact_dir.is_symlink():
                raise DailyOrchestrationError("daily_key_artifact_directory_unsafe")
            try:
                artifact_dir.resolve().relative_to(root)
            except (OSError, RuntimeError, ValueError) as exc:
                raise DailyOrchestrationError(
                    "daily_key_artifact_directory_unsafe"
                ) from exc
            if artifact_dir.is_dir():
                pattern = re.compile(
                    re.escape(base_job_id)
                    + r"(?:-r\d+)?-a(?P<attempt>[1-9]\d*)-response\.response\Z"
                )
                for artifact_path in artifact_dir.glob(
                    f"{base_job_id}*-response.response"
                ):
                    if artifact_path.is_symlink():
                        raise DailyOrchestrationError("daily_key_artifact_path_unsafe")
                    match = pattern.fullmatch(artifact_path.name)
                    if match:
                        last_attempt = max(last_attempt, int(match["attempt"]))
        attempt = last_attempt + 1
        job_id = base_job_id if attempt == 1 else f"{base_job_id}-r{attempt:02d}"
        return attempt, job_id

    def _save_grouping_with_key_result(
        self,
        connection: sqlite3.Connection,
        snapshot,
        *,
        decisions,
        seed: str,
        result,
    ) -> None:
        savepoint = "daily_key_adoption"
        owns_transaction = not connection.in_transaction
        connection.execute(
            "BEGIN IMMEDIATE" if owns_transaction else f"SAVEPOINT {savepoint}"
        )
        try:
            save_grouped_snapshot(connection, snapshot, decisions=decisions, seed=seed)
            accept_pending_key_job_result(
                connection,
                result,
                accepted_at=self.clock.now_jst().isoformat(),
            )
            if owns_transaction:
                connection.commit()
            else:
                connection.execute(f"RELEASE {savepoint}")
        except BaseException:
            if owns_transaction:
                connection.rollback()
            else:
                connection.execute(f"ROLLBACK TO {savepoint}")
                connection.execute(f"RELEASE {savepoint}")
            raise

    @staticmethod
    def _theme_word(
        connection: sqlite3.Connection, settings_version: str, theme_id: str
    ) -> str:
        row = connection.execute(
            "SELECT themes_json FROM settings_version WHERE version=?",
            (settings_version,),
        ).fetchone()
        if row is None:
            raise DailyOrchestrationError("daily_theme_settings_missing")
        try:
            themes = json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as exc:
            raise DailyOrchestrationError("daily_theme_settings_invalid") from exc
        if not isinstance(themes, list):
            raise DailyOrchestrationError("daily_theme_settings_invalid")
        matches = [
            item
            for item in themes
            if isinstance(item, dict) and item.get("theme_id") == theme_id
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("name"), str):
            raise DailyOrchestrationError("daily_theme_name_missing")
        name = matches[0]["name"].strip()
        if not name:
            raise DailyOrchestrationError("daily_theme_name_missing")
        return name

    @staticmethod
    def _plan(
        theme_id: str,
        theme_word: str,
        event_kind: str,
        ordinal: int,
        acquisition_id: str,
        *,
        status: str,
        snapshot_ref: str | None = None,
        selected: tuple[tuple[str, str], ...] = (),
    ) -> DailyThemePlan:
        if status == "deadline":
            return DailyThemePlan(
                theme_id,
                (),
                (),
                None,
                None,
                acquisition_status="deadline",
                acquisition_ref=acquisition_id,
            )
        fallback = DailySearchPlan(
            f"{theme_id}-fallback-{event_kind}-{ordinal:02d}",
            theme_id,
            theme_word,
            (theme_id,),
            "theme_fallback",
            "theme_fallback",
        )
        if status == "exhausted":
            return DailyThemePlan(
                theme_id,
                (),
                (),
                fallback,
                None,
                acquisition_status="exhausted",
                acquisition_ref=acquisition_id,
            )
        if status != "observed" or snapshot_ref is None:
            raise DailyOrchestrationError("daily_theme_plan_status_invalid")
        related = tuple(
            DailySearchPlan(
                f"{theme_id}-related-{event_kind}-{ordinal:02d}-{index:02d}",
                theme_id,
                raw,
                (theme_id,),
                "related",
                "related",
                snapshot_ref,
                word_id,
            )
            for index, (word_id, raw) in enumerate(selected, start=1)
        )
        return DailyThemePlan(
            theme_id,
            tuple(word_id for word_id, _ in selected),
            related,
            fallback,
            snapshot_ref,
            acquisition_status="observed",
            acquisition_ref=acquisition_id,
        )
