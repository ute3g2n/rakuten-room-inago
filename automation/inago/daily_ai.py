"""Use a single persistent AI-O root for a Python-controlled daily run."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
import tempfile
from pathlib import Path
from typing import Any, Mapping

from automation.inago.ai_runner import AIJobRunner
from automation.inago.app_server import AppServerSession, AppServerError
from automation.inago.contracts import AIJobRequest
from automation.inago.conversation_logs import (
    ConversationLogSyncResult,
    ConversationLogWatcher,
    sync_conversation_logs,
)


_ROOT_CHECKPOINT_KEY = "ai_daily_root"


class DailyAIRootRunner(AIJobRunner):
    """Reuse the existing envelope/artifact validator, with one app-server host.

    The program binds a run on its SQLite-owning thread. Worker calls receive
    immutable job input and perform no SQLite writes. Root metadata is later
    synchronized by the program, not by the AI or transport worker.
    """

    trace_window_by_job = True

    def __init__(self, *, session_factory=AppServerSession, **kwargs):
        kwargs.setdefault("max_timeout_seconds", 480)
        super().__init__(**kwargs)
        self.session_factory = session_factory
        self.session = None
        self.context = None
        self._turn_completed = False
        self._retained_dirs: set[Path] = set()
        self._guard = threading.Lock()
        self._job_guard = threading.Lock()
        self._active_job_lock = threading.Lock()
        self._active_job_run_id: str | None = None
        self._active_job_id: str | None = None
        self._active_job_cancel_event: threading.Event | None = None
        self._active_conversation_request: AIJobRequest | None = None
        self._conversation_watcher: ConversationLogWatcher | None = None
        self._last_conversation_log_diagnostic: tuple[str, str | None] | None = None
        self.process_starts = 0
        self._unused_root_can_be_replaced = False

    def prepare_daily_run(self, connection: sqlite3.Connection, run_id: str):
        row = connection.execute(
            "SELECT run_id,target_date,settings_version,seed,checkpoint,mode,status FROM run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            row is None
            or row["mode"] != "daily"
            or row["status"] not in {"running", "partial"}
        ):
            raise AppServerError("daily_run_missing")
        root = json.loads(row["checkpoint"] or "{}")
        if not isinstance(root, dict):
            raise AppServerError("saved_root_checkpoint_invalid")
        saved = root.get(_ROOT_CHECKPOINT_KEY)
        identity = {
            key: row[key]
            for key in ("run_id", "target_date", "settings_version", "seed")
        }
        if self.context is not None and any(
            self.context.get(key) != value for key, value in identity.items()
        ):
            raise AppServerError("root_cannot_change_daily_identity")
        if saved is not None and (
            not isinstance(saved, dict)
            or saved.get("schema_version") != 1
            or any(saved.get(key) != value for key, value in identity.items())
        ):
            raise AppServerError("saved_root_identity_invalid")
        if (
            saved is not None
            and saved.get("root_thread_id") is not None
            and (
                not isinstance(saved["root_thread_id"], str)
                or not saved["root_thread_id"].strip()
            )
        ):
            raise AppServerError("saved_root_identity_invalid")
        if saved is not None:
            count = saved.get("process_starts", 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise AppServerError("saved_root_process_count_invalid")
            self.process_starts = max(self.process_starts, count)
        self.context = {
            **identity,
            "schema_version": 1,
            "root_thread_id": (saved.get("root_thread_id") if saved else None)
            or (self.context.get("root_thread_id") if self.context else None),
        }
        if saved and saved.get("replaced_unused_root_thread_id"):
            self.context["replaced_unused_root_thread_id"] = saved[
                "replaced_unused_root_thread_id"
            ]
        self._unused_root_can_be_replaced = (
            connection.execute(
                "SELECT 1 FROM ai_job WHERE run_id=? LIMIT 1", (run_id,)
            ).fetchone()
            is None
        )
        return dict(self.context)

    def sync_daily_checkpoint(self, connection: sqlite3.Connection):
        if self.context is None:
            return
        metadata = self.root_metadata()
        owns = not connection.in_transaction
        if not owns:
            raise AppServerError("root_checkpoint_requires_idle_connection")
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT settings_version,seed,checkpoint FROM run WHERE run_id=?",
                (metadata["run_id"],),
            ).fetchone()
            if (
                row is None
                or row[0] != metadata["settings_version"]
                or row[1] != metadata["seed"]
            ):
                raise AppServerError("root_checkpoint_run_conflict")
            root = json.loads(row[2] or "{}")
            previous = root.get(_ROOT_CHECKPOINT_KEY)
            if (
                previous
                and previous.get("root_thread_id")
                and previous["root_thread_id"] != metadata["root_thread_id"]
                and not (
                    previous["root_thread_id"]
                    == metadata.get("replaced_unused_root_thread_id")
                    and connection.execute(
                        "SELECT 1 FROM ai_job WHERE run_id=? LIMIT 1",
                        (metadata["run_id"],),
                    ).fetchone()
                    is None
                )
            ):
                raise AppServerError("root_thread_cannot_change")
            root[_ROOT_CHECKPOINT_KEY] = metadata
            connection.execute(
                "UPDATE run SET checkpoint=? WHERE run_id=?",
                (
                    json.dumps(root, ensure_ascii=False, allow_nan=False),
                    metadata["run_id"],
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def root_metadata(self) -> dict[str, Any]:
        if self.context is None:
            raise AppServerError("root_not_bound")
        return {
            **self.context,
            "process_pid": self.session.process_id if self.session else None,
            "process_starts": self.process_starts,
            "closed": self.session.is_closed if self.session else True,
        }

    def start_daily_root(self, *, deadline_monotonic: float):
        if self.context is None:
            raise AppServerError("root_not_bound")
        if self.session is not None and not self.session.is_closed:
            return
        workspace = Path(
            tempfile.mkdtemp(prefix="daily-root-", dir=self._work_root())
        ).resolve()
        self._retained_dirs.add(workspace)
        self._stage_agent_workspace(workspace, "T02")
        bounded = time.monotonic() + min(
            self.max_timeout_seconds, self.clock.deadline_remaining(deadline_monotonic)
        )
        session_options = dict(
            command=(
                *self.cli_command,
                "app-server",
                "--listen",
                "stdio://",
                "--config",
                "agents.enabled=true",
                "--enable",
                "multi_agent",
            ),
            cwd=workspace,
            environment=self._env(),
            log_path=self.data_root
            / "logs"
            / self.context["target_date"]
            / "ai-root-transport.jsonl",
            resume_thread_id=self.context["root_thread_id"],
            persist_initial_context=True,
        )
        self.session = self.session_factory(**session_options)
        try:
            try:
                self.context["root_thread_id"] = self.session.start(deadline=bounded)
            except AppServerError as exc:
                if (
                    exc.code != "thread_history_not_found"
                    or not self._unused_root_can_be_replaced
                ):
                    raise
                self.process_starts += self.session.starts
                self.context["replaced_unused_root_thread_id"] = self.context[
                    "root_thread_id"
                ]
                session_options["resume_thread_id"] = None
                self.session = self.session_factory(**session_options)
                self.context["root_thread_id"] = self.session.start(deadline=bounded)
        finally:
            self.process_starts += self.session.starts

    def run(self, request: AIJobRequest, *, cancel_event=None):
        if (
            self.context is None
            or request.task_id != "T02"
            or request.source_kind != "daily"
            or any(
                getattr(request, key) != self.context[key]
                for key in ("run_id", "target_date", "settings_version")
            )
        ):
            raise AppServerError("job_does_not_match_bound_daily_root")
        if not self._job_guard.acquire(blocking=False):
            raise AppServerError("concurrent_daily_ai_job")
        active_cancel_event = cancel_event or threading.Event()
        with self._active_job_lock:
            self._active_job_run_id = request.run_id
            self._active_job_id = request.job_id
            self._active_job_cancel_event = active_cancel_event
            self._active_conversation_request = request
        try:
            return super().run(request, cancel_event=active_cancel_event)
        finally:
            with self._active_job_lock:
                if self._active_job_cancel_event is active_cancel_event:
                    self._active_job_run_id = None
                    self._active_job_id = None
                    self._active_job_cancel_event = None
                    self._active_conversation_request = None
            self._job_guard.release()

    def _record_conversation_log_result(
        self, result: ConversationLogSyncResult
    ) -> None:
        if result.status == "complete":
            self._last_conversation_log_diagnostic = None
            return
        if result.status == "waiting":
            return
        diagnostic = (result.status, result.reason_code)
        if diagnostic == self._last_conversation_log_diagnostic:
            return
        self._last_conversation_log_diagnostic = diagnostic
        request = self._active_conversation_request
        if request is not None:
            self._trace_event(
                request,
                "conversation_log_sync",
                conversation_log_status=result.status,
                conversation_log_reason_code=result.reason_code or "none",
                conversation_log_copied_count=result.copied_count,
            )
        logging.getLogger(__name__).warning(
            "AI conversation log sync status=%s reason_code=%s run_id=%s role=ai-o",
            result.status,
            result.reason_code or "none",
            self.context.get("run_id") if self.context else "unbound",
        )

    def _start_conversation_log_watcher(self) -> None:
        if self.context is None:
            return
        self._stop_conversation_log_watcher()
        try:
            environment = self._env() or {}
            self._conversation_watcher = ConversationLogWatcher(
                self.data_root,
                flow="daily",
                started_on=self.context["target_date"],
                flow_id=self.context["run_id"],
                role="ai-o",
                root_thread_id=lambda: (
                    self.context.get("root_thread_id") if self.context else None
                ),
                codex_home=environment.get("CODEX_HOME"),
                on_result=self._record_conversation_log_result,
            ).start()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "AI conversation log watcher start failed error_type=%s",
                type(exc).__name__,
            )

    def _stop_conversation_log_watcher(self) -> None:
        watcher = self._conversation_watcher
        self._conversation_watcher = None
        if watcher is None:
            return
        try:
            result = watcher.stop()
            if result is not None:
                self._record_conversation_log_result(result)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "AI conversation log watcher stop failed error_type=%s",
                type(exc).__name__,
            )

    def _save_final_conversation_logs(self) -> None:
        if self.context is None:
            return
        try:
            environment = self._env() or {}
            result = sync_conversation_logs(
                self.data_root,
                flow="daily",
                started_on=self.context["target_date"],
                flow_id=self.context["run_id"],
                role="ai-o",
                root_thread_id=self.context.get("root_thread_id"),
                codex_home=environment.get("CODEX_HOME"),
            )
            self._record_conversation_log_result(result)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "AI final conversation log sync failed error_type=%s",
                type(exc).__name__,
            )

    def _cleanup_job_dir(self, job_dir: Path):
        # Keep initial agent definitions available to the persistent root until
        # it closes. Files stay under the existing private runtime jobs root.
        self._retained_dirs.add(job_dir.resolve())

    def _transport_completion_valid(self, returncode):
        return self._turn_completed and returncode is None

    def _run_process(self, argv, job_dir, prompt, deadline, cancel_event):
        self._turn_completed = False
        bounded = time.monotonic() + min(
            self.max_timeout_seconds, self.clock.deadline_remaining(deadline)
        )
        last_message = job_dir / "final.response"
        try:
            with self._guard:
                if cancel_event is not None and cancel_event.is_set():
                    return "cancelled", None, b"", b""
                if self.session is None or self.session.is_closed:
                    self.session = self.session_factory(
                        command=(
                            *self.cli_command,
                            "app-server",
                            "--listen",
                            "stdio://",
                            "--config",
                            "agents.enabled=true",
                            "--enable",
                            "multi_agent",
                        ),
                        cwd=job_dir,
                        environment=self._env(),
                        log_path=self.data_root
                        / "logs"
                        / self.context["target_date"]
                        / "ai-root-transport.jsonl",
                        resume_thread_id=self.context["root_thread_id"],
                    )
                    try:
                        thread_id = self.session.start(deadline=bounded)
                        self.context["root_thread_id"] = thread_id
                    finally:
                        self.process_starts += self.session.starts
                if cancel_event is not None and cancel_event.is_set():
                    self.session.close()
                    return "cancelled", None, b"", b""
            self._start_conversation_log_watcher()
            instruction = (
                "\nContinue this Python-controlled daily root. Treat this turn as a "
                "new job whose identity comes only from the JSON object in this turn. "
                "That inline JSON is the exact job-input.json content written by "
                "Python and is the authoritative job input, including all identifiers "
                "and payload. Use it directly; do not reread job-input.json or the "
                "schema with filesystem tools. Windows sandbox access to Python's "
                "private temporary directories may be denied, but no file read is "
                "required when the complete canonical input is already inline. "
                "Copy protocol_version, schema_version, run_id, job_id, attempt, "
                "task_id, agent_id, skill_name, target_date, settings_version, "
                "input_refs, evidence_refs, input_hash, and deadline_at exactly from "
                "that inline object; never carry identity or hash values over from an earlier "
                "turn or recompute input_hash. For this job, create fresh authorized "
                "native subagents. A completed specialist response finishes that "
                "specialist job; do not require an unavailable child-close API or make "
                "cleanup a result gate. The Python program owns the final app-server "
                "shutdown. Do not reuse earlier job answers or children as current "
                "evidence. Pass this turn's authorized specialist its complete inline "
                "input. Use only this canonical inline job input and references.\n"
            )
            result = self.session.run_turn(
                prompt.decode("utf-8") + instruction,
                json.loads(
                    (job_dir / "ai-job.schema.json").read_text(encoding="utf-8")
                ),
                cwd=job_dir,
                deadline=bounded,
                cancel_event=cancel_event,
            )
            last_message.write_bytes(result.final_text.encode("utf-8"))
            self._turn_completed = True
            events = [
                {
                    "type": "thread.started",
                    "thread_id": result.thread_id,
                    "turn_id": result.turn_id,
                    "transport": "app-server",
                }
            ]
            for message in result.events:
                params = message["params"]
                if message["method"] == "item/completed":
                    events.append(
                        {
                            "type": "item.completed",
                            "item": params["item"],
                            "turn_id": result.turn_id,
                        }
                    )
            stream = b"".join(
                (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
                for item in events
            )
            return "completed", result.process_returncode, stream, b""
        except AppServerError as exc:
            if str(exc) == "timed_out":
                status = "timed_out"
            elif str(exc) == "cancelled":
                status = "cancelled"
            else:
                status = "error"
            return (
                status,
                (
                    self.session.process.poll()
                    if self.session and self.session.process
                    else None
                ),
                b"",
                b"",
            )
        finally:
            self._stop_conversation_log_watcher()

    def cancel_owned_jobs(self, run_id: str) -> bool:
        if self.context is None or self.context["run_id"] != run_id:
            return False
        self.close_daily_run()
        return self.session is None or self.session.is_closed

    def cancel_active_request(self, run_id: str, job_id: str) -> bool:
        """Stop the current AI turn while retaining its run for later turns."""

        with self._active_job_lock:
            if (
                self._active_job_run_id != run_id
                or self._active_job_id != job_id
                or self._active_job_cancel_event is None
            ):
                return False
            self._active_job_cancel_event.set()
        with self._guard:
            if self.session is not None and not self.session.is_closed:
                self.session.close()
            return self.session is None or self.session.is_closed

    def close_daily_run(self):
        try:
            if self.session is not None:
                self.session.close()
        finally:
            self._stop_conversation_log_watcher()
            self._save_final_conversation_logs()
        jobs_root = (self.data_root / "jobs").resolve()
        for directory in self._retained_dirs:
            if directory.parent != jobs_root or directory.is_symlink():
                raise AppServerError("staging_cleanup_path_invalid")
            shutil.rmtree(directory, ignore_errors=True)
        self._retained_dirs.clear()
