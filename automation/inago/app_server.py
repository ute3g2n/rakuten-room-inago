"""Bounded stdio transport for one owned, read-only Codex root session.

Protocol: CLI 0.162.0-alpha.17.2 generated v2 schemas and the official
https://learn.chatgpt.com/docs/app-server specification. No API key is supplied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class AppServerError(RuntimeError):
    """A sanitized transport failure; arbitrary upstream text is never logged."""


@dataclass(frozen=True, slots=True)
class AppServerTurn:
    thread_id: str
    turn_id: str
    final_text: str
    events: tuple[Mapping[str, Any], ...]
    process_returncode: int | None


class AppServerSession:
    """Own one process and root thread; all calls are sequential and bounded."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        cwd: Path,
        log_path: Path,
        environment: Mapping[str, str] | None = None,
        resume_thread_id: str | None = None,
    ):
        if not command or any(
            not isinstance(item, str) or not item for item in command
        ):
            raise ValueError("command must be a nonempty argument array")
        self.command = tuple(command)
        self.cwd = Path(cwd).resolve()
        self.log_path = Path(log_path).resolve()
        self.environment = dict(environment) if environment is not None else None
        self.resume_thread_id = resume_thread_id
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.starts = 0
        self.process: subprocess.Popen | None = None
        self._messages: queue.Queue = queue.Queue(maxsize=4096)
        self._outgoing: queue.Queue = queue.Queue(maxsize=128)
        self._send_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._job_lock = threading.Lock()
        self._closed = False
        self._next_id = 0
        self._pending_notifications = []
        self._reader_thread = None
        self._stderr_thread = None
        self._writer_thread = None

    @property
    def process_id(self) -> int | None:
        return self.process.pid if self.process else None

    @property
    def is_closed(self) -> bool:
        return self._closed and (
            self.process is None or self.process.poll() is not None
        )

    def _log(self, event, **fields):
        allowed = {
            "method",
            "thread_id",
            "turn_id",
            "process_pid",
            "status",
            "returncode",
            "code",
        }
        record = {
            "event": event,
            "at": time.time(),
            **{key: value for key, value in fields.items() if key in allowed},
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _reader(self):
        try:
            while not self._closed:
                line = self.process.stdout.readline(8 * 1024 * 1024 + 1)
                if not line:
                    self._messages.put(None)
                    return
                if len(line) > 8 * 1024 * 1024:
                    self._messages.put(AppServerError("rpc_line_too_large"))
                    return
                try:
                    message = json.loads(line.decode("utf-8"))
                    if not isinstance(message, dict):
                        raise ValueError("not object")
                except (ValueError, UnicodeDecodeError):
                    self._messages.put(AppServerError("rpc_invalid_json"))
                    return
                self._messages.put(message)
        except OSError:
            self._messages.put(AppServerError("rpc_read_failed"))

    def _discard_stderr(self):
        # Drain the pipe to avoid deadlock; stderr may contain private prompts.
        try:
            while self.process.stderr.read(4096):
                pass
        except OSError:
            pass

    def _send(self, message):
        with self._send_lock:
            if self._closed or self.process is None or self.process.poll() is not None:
                raise AppServerError("server_closed")
            try:
                encoded = (
                    json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n"
                ).encode("utf-8")
                self._outgoing.put_nowait(encoded)
            except (queue.Full, ValueError) as exc:
                raise AppServerError("rpc_write_failed") from exc

    def _writer(self):
        while not self._closed:
            try:
                value = self._outgoing.get(timeout=0.05)
            except queue.Empty:
                continue
            if self._closed:
                return
            try:
                self.process.stdin.write(value)
                self.process.stdin.flush()
            except (OSError, ValueError):
                if not self._closed:
                    self._messages.put(AppServerError("rpc_write_failed"))
                return

    def _receive(self, deadline, cancel_event=None):
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise AppServerError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerError("timed_out")
            try:
                item = self._messages.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            if item is None:
                raise AppServerError("server_stdout_closed")
            if isinstance(item, Exception):
                raise item
            return item

    def _deny_server_request(self, message):
        method = message.get("method")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            self._send({"id": message["id"], "result": {"decision": "decline"}})
        elif method == "item/permissions/requestApproval":
            self._send(
                {"id": message["id"], "result": {"permissions": {}, "scope": "turn"}}
            )
        else:
            self._send(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": "unsupported unattended request",
                    },
                }
            )
        self._log("request_denied", code="unsupported_or_ungranted_request")

    def _call(self, method, params, *, deadline, cancel_event=None):
        self._next_id += 1
        request_id = self._next_id
        self._send({"id": request_id, "method": method, "params": params})
        self._log("rpc_sent", method=method, process_pid=self.process_id)
        while True:
            message = self._receive(deadline, cancel_event)
            if "method" in message:
                if "id" in message:
                    self._deny_server_request(message)
                else:
                    self._pending_notifications.append(message)
                continue
            if message.get("id") != request_id:
                # An earlier interrupt response is harmless; it is not a job.
                continue
            if "error" in message:
                self._log("rpc_failed", method=method, code="rpc_error")
                raise AppServerError("rpc_error")
            if not isinstance(message.get("result"), dict):
                raise AppServerError("rpc_result_invalid")
            return message["result"]

    def start(self, *, deadline: float):
        if self.thread_id is not None:
            if self._closed:
                raise AppServerError("server_closed")
            return self.thread_id
        if self._closed:
            raise AppServerError("server_closed")
        try:
            self.process = subprocess.Popen(
                list(self.command),
                cwd=self.cwd,
                env=self.environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.starts += 1
            self._log("process_started", process_pid=self.process_id)
            self._reader_thread = threading.Thread(
                target=self._reader, name="inago-codex-rpc", daemon=True
            )
            self._stderr_thread = threading.Thread(
                target=self._discard_stderr, name="inago-codex-stderr", daemon=True
            )
            self._writer_thread = threading.Thread(
                target=self._writer, name="inago-codex-writer", daemon=True
            )
            self._reader_thread.start()
            self._stderr_thread.start()
            self._writer_thread.start()
            self._call(
                "initialize",
                {
                    "clientInfo": {
                        "name": "rakuten_room_inago",
                        "title": "Inago daily root",
                        "version": "1.4",
                    }
                },
                deadline=deadline,
            )
            self._send({"method": "initialized", "params": {}})
            account = self._call(
                "account/read", {"refreshToken": False}, deadline=deadline
            )
            if (
                not isinstance(account.get("account"), dict)
                or account["account"].get("type") != "chatgpt"
            ):
                raise AppServerError("auth_mode_not_permitted")
            params = {
                "cwd": str(self.cwd),
                "sandbox": "read-only",
                "approvalPolicy": "never",
            }
            method = "thread/start"
            if self.resume_thread_id:
                params["threadId"] = self.resume_thread_id
                method = "thread/resume"
            result = self._call(method, params, deadline=deadline)
            thread = result.get("thread", {}).get("id")
            if (
                not isinstance(thread, str)
                or not thread
                or (self.resume_thread_id and thread != self.resume_thread_id)
            ):
                raise AppServerError("root_thread_identity_invalid")
            self.thread_id = thread
            self._log("root_ready", thread_id=thread, process_pid=self.process_id)
            self._pending_notifications.clear()
            return thread
        except (OSError, AppServerError):
            self.close()
            raise

    def run_turn(
        self,
        prompt: str,
        output_schema: Mapping[str, Any],
        *,
        cwd: Path,
        deadline: float,
        cancel_event=None,
    ) -> AppServerTurn:
        if not self._job_lock.acquire(blocking=False):
            raise AppServerError("concurrent_root_turn")
        try:
            self.start(deadline=deadline)
            result = self._call(
                "turn/start",
                {
                    "threadId": self.thread_id,
                    "cwd": str(Path(cwd).resolve()),
                    "input": [{"type": "text", "text": prompt}],
                    "outputSchema": dict(output_schema),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly"},
                },
                deadline=deadline,
                cancel_event=cancel_event,
            )
            turn_id = result.get("turn", {}).get("id")
            if not isinstance(turn_id, str) or not turn_id:
                raise AppServerError("turn_identity_invalid")
            self.turn_id = turn_id
            self._log(
                "turn_started",
                thread_id=self.thread_id,
                turn_id=turn_id,
                process_pid=self.process_id,
            )
            final = None
            events = []
            pending, self._pending_notifications = self._pending_notifications, []
            while True:
                message = (
                    pending.pop(0) if pending else self._receive(deadline, cancel_event)
                )
                if "id" in message and "method" in message:
                    self._deny_server_request(message)
                    continue
                params = message.get("params", {})
                method = message.get("method")
                if not isinstance(params, dict):
                    raise AppServerError("notification_params_invalid")
                if params.get("threadId") != self.thread_id:
                    continue
                current_turn = params.get("turnId") or (
                    params.get("turn", {}).get("id")
                    if isinstance(params.get("turn"), dict)
                    else None
                )
                if current_turn != turn_id:
                    continue
                events.append(message)
                if method == "item/completed":
                    item = params.get("item", {})
                    if (
                        item.get("type") == "agentMessage"
                        and item.get("phase") == "final_answer"
                    ):
                        if final is not None:
                            raise AppServerError("multiple_final_messages")
                        final = item.get("text")
                elif method == "turn/completed":
                    status = params["turn"].get("status")
                    self._log(
                        "turn_finished",
                        thread_id=self.thread_id,
                        turn_id=turn_id,
                        status=status,
                        process_pid=self.process_id,
                    )
                    self.turn_id = None
                    if status != "completed":
                        raise AppServerError("turn_not_completed")
                    if not isinstance(final, str):
                        raise AppServerError("final_response_missing")
                    return AppServerTurn(
                        self.thread_id,
                        turn_id,
                        final,
                        tuple(events),
                        self.process.poll(),
                    )
        except AppServerError as exc:
            self._log(
                "job_transport_failed",
                code=(
                    str(exc)
                    if str(exc) in {"timed_out", "cancelled"}
                    else "transport_failed"
                ),
            )
            self.interrupt()
            self.close()
            raise
        finally:
            self._job_lock.release()

    def interrupt(self):
        if self.thread_id and self.turn_id and not self._closed:
            self._next_id += 1
            try:
                self._send(
                    {
                        "id": self._next_id,
                        "method": "turn/interrupt",
                        "params": {"threadId": self.thread_id, "turnId": self.turn_id},
                    }
                )
                self._log(
                    "interrupt_sent", thread_id=self.thread_id, turn_id=self.turn_id
                )
            except AppServerError:
                pass

    def close(self):
        if self._closed:
            return
        self.interrupt()
        self._closed = True
        process = self.process
        if process is not None:
            # Closing a buffered stdin can wait forever behind a blocked writer.
            # Terminate the owned process first for an unfinished job.
            if self._job_lock.locked() and process.poll() is None:
                process.terminate()
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            self._log(
                "process_closed", process_pid=process.pid, returncode=process.returncode
            )
            while True:
                try:
                    self._messages.get_nowait()
                except queue.Empty:
                    break
            for worker in (
                self._reader_thread,
                self._stderr_thread,
                self._writer_thread,
            ):
                if worker is not None and worker is not threading.current_thread():
                    worker.join(timeout=1)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
