"""Bounded stdio transport for one owned, read-only Codex root session.

Protocol: CLI 0.162.0-alpha.17.2 generated v2 schemas and the official
https://learn.chatgpt.com/docs/app-server specification. No API key is supplied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


class AppServerError(RuntimeError):
    """A sanitized transport failure; arbitrary upstream text is never logged."""


@dataclass(frozen=True, slots=True)
class AppServerTurn:
    thread_id: str
    turn_id: str
    final_text: str
    events: tuple[Mapping[str, Any], ...]
    process_returncode: int | None
    observed_model: str | None = None
    observed_effort: str | None = None
    observed_service_tier: str | None = None


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
        log_root: Path | None = None,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
        fast_mode: bool | None = None,
        role_name: str | None = None,
        role_config_sha256: str | None = None,
        developer_instructions: str | None = None,
        sandbox_policy: Mapping[str, Any] | None = None,
        on_thread_ready: Callable[[str], None] | None = None,
    ):
        if not command or any(
            not isinstance(item, str) or not item for item in command
        ):
            raise ValueError("command must be a nonempty argument array")
        self.command = tuple(command)
        self.cwd = Path(cwd).resolve()
        self.log_root = Path(log_root).absolute() if log_root is not None else None
        self.log_path = (
            Path(log_path).absolute()
            if self.log_root is not None
            else Path(log_path).resolve()
        )
        self.environment = dict(environment) if environment is not None else None
        self.resume_thread_id = resume_thread_id
        self.model = model
        self.effort = effort
        self.service_tier = service_tier
        self.fast_mode = fast_mode
        self.role_name = role_name
        self.role_config_sha256 = role_config_sha256
        self.developer_instructions = developer_instructions
        self.sandbox_policy = dict(sandbox_policy or {"type": "readOnly"})
        self.on_thread_ready = on_thread_ready
        self.observed_settings = {
            "model": None,
            "effort": None,
            "service_tier": None,
        }
        if self.sandbox_policy.get("type") not in {"readOnly", "workspaceWrite"}:
            raise ValueError("sandbox policy must not grant full access")
        if effort is not None and model is None:
            raise ValueError("effort requires an explicit model")
        if service_tier not in {None, "default", "priority"}:
            raise ValueError("unsupported service tier")
        if fast_mode is not None and type(fast_mode) is not bool:
            raise ValueError("fast mode must be a boolean")
        if developer_instructions is not None and (
            not isinstance(developer_instructions, str)
            or not developer_instructions.strip()
        ):
            raise ValueError("developer instructions must be nonempty text")
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
            "model",
            "effort",
            "service_tier",
            "fast_mode",
            "role_name",
            "role_config_sha256",
            "sandbox_profile",
            "observation_status",
        }
        record = {
            "event": event,
            "at": time.time(),
            **{key: value for key, value in fields.items() if key in allowed},
        }
        try:
            with self._log_lock:
                if self.log_root is not None:
                    self._validate_log_target()
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                if self.log_root is not None:
                    self._validate_log_target()
                with self.log_path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except AppServerError:
            raise
        except OSError as exc:
            raise AppServerError("log_write_failed") from exc

    def _maintenance_log_fields(self, observation_status: str) -> dict[str, Any]:
        if self.role_name is None:
            return {}
        return {
            "model": self.model,
            "effort": self.effort,
            "service_tier": self.service_tier,
            "fast_mode": self.fast_mode,
            "role_name": self.role_name,
            "role_config_sha256": self.role_config_sha256,
            "sandbox_profile": self.sandbox_policy.get("type"),
            "observation_status": observation_status,
        }

    @staticmethod
    def _contains_reparse_point(path: Path) -> bool:
        absolute = Path(path).absolute()
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise AppServerError("log_path_uninspectable") from exc
            if stat.S_ISLNK(metadata.st_mode):
                return True
            try:
                attributes = metadata.st_file_attributes
            except AttributeError:
                continue
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                return True
        return False

    def _validate_log_target(self) -> None:
        if self.log_root is None:
            return
        if self._contains_reparse_point(self.log_root) or self._contains_reparse_point(
            self.log_path
        ):
            raise AppServerError("log_path_invalid")
        try:
            root = self.log_root.resolve(strict=True)
            target = self.log_path.resolve(strict=False)
            relative = target.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise AppServerError("log_path_out_of_scope") from exc
        if not relative.parts or (
            self.log_path.exists() and not self.log_path.is_file()
        ):
            raise AppServerError("log_path_invalid")

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
            self._verify_restricted_read_capability()
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
            if self.model is not None:
                self._verify_model_capability(deadline=deadline)
            if self.fast_mode:
                self._verify_fast_mode_capability(deadline=deadline)
            params = {
                "cwd": str(self.cwd),
                "sandbox": (
                    "workspace-write"
                    if self.sandbox_policy["type"] == "workspaceWrite"
                    else "read-only"
                ),
                "approvalPolicy": "never",
            }
            if self.model is not None:
                params["model"] = self.model
            if self.service_tier is not None:
                params["serviceTier"] = self.service_tier
            if self.developer_instructions is not None:
                params["developerInstructions"] = self.developer_instructions
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
            if self.on_thread_ready is not None:
                self.on_thread_ready(thread)
            root_ready_fields = {
                "thread_id": thread,
                "process_pid": self.process_id,
                **self._maintenance_log_fields("configured_unverified"),
            }
            self._log("root_ready", **root_ready_fields)
            self._pending_notifications.clear()
            return thread
        except AppServerError as exc:
            try:
                self._log(
                    "session_start_blocked",
                    code=str(exc),
                    **self._maintenance_log_fields("blocked"),
                )
            finally:
                self.close()
            raise
        except Exception:
            try:
                self._log(
                    "session_start_failed",
                    code="session_start_failed",
                    **self._maintenance_log_fields("blocked"),
                )
            finally:
                self.close()
            raise

    def _verify_restricted_read_capability(self) -> None:
        """Fail closed if this CLI cannot enforce the requested read roots."""

        if not any(key in self.sandbox_policy for key in ("access", "readOnlyAccess")):
            return
        try:
            app_server_index = next(
                index
                for index, value in enumerate(self.command)
                if value.lower() == "app-server"
            )
        except StopIteration as exc:
            raise AppServerError("sandbox_capability_probe_failed") from exc
        command = [
            *self.command[: app_server_index + 1],
            "generate-json-schema",
        ]
        try:
            with tempfile.TemporaryDirectory(prefix="inago-app-server-schema-") as out:
                completed = subprocess.run(
                    [*command, "--out", out],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=15,
                    check=False,
                )
                if completed.returncode != 0:
                    raise AppServerError("sandbox_capability_probe_failed")
                schema_path = Path(out) / "codex_app_server_protocol.v2.schemas.json"
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                variants = schema["definitions"]["SandboxPolicy"]["oneOf"]
                read_only = next(
                    variant
                    for variant in variants
                    if variant.get("title") == "ReadOnlySandboxPolicy"
                )
                workspace_write = next(
                    variant
                    for variant in variants
                    if variant.get("title") == "WorkspaceWriteSandboxPolicy"
                )
                if "access" not in read_only.get(
                    "properties", {}
                ) or "readOnlyAccess" not in workspace_write.get("properties", {}):
                    raise AppServerError("restricted_read_roots_unsupported")
        except AppServerError:
            raise
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            StopIteration,
        ) as exc:
            raise AppServerError("sandbox_capability_probe_failed") from exc

    def _verify_model_capability(self, *, deadline: float) -> None:
        cursor = None
        seen_cursors = set()
        matched = None
        while True:
            params = {"limit": 100, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = self._call("model/list", params, deadline=deadline)
            entries = result.get("data")
            if not isinstance(entries, list):
                raise AppServerError("model_catalog_invalid")
            matched = next(
                (
                    item
                    for item in entries
                    if isinstance(item, dict)
                    and (
                        item.get("model") == self.model or item.get("id") == self.model
                    )
                ),
                None,
            )
            if matched is not None:
                break
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                raise AppServerError("model_catalog_invalid")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        if matched is None:
            raise AppServerError("model_unavailable")
        if self.effort is not None:
            efforts = matched.get("supportedReasoningEfforts")
            if not isinstance(efforts, list) or not any(
                isinstance(item, dict) and item.get("reasoningEffort") == self.effort
                for item in efforts
            ):
                raise AppServerError("effort_unavailable")
        if self.service_tier == "priority":
            service_tiers = matched.get("serviceTiers")
            if not isinstance(service_tiers, list) or not any(
                isinstance(item, dict) and item.get("id") == "priority"
                for item in service_tiers
            ):
                raise AppServerError("service_tier_unavailable")

    def _verify_fast_mode_capability(self, *, deadline: float) -> None:
        cursor = None
        seen_cursors = set()
        while True:
            params = {"limit": 100}
            if cursor is not None:
                params["cursor"] = cursor
            result = self._call("experimentalFeature/list", params, deadline=deadline)
            entries = result.get("data")
            if not isinstance(entries, list):
                raise AppServerError("feature_catalog_invalid")
            feature = next(
                (
                    item
                    for item in entries
                    if isinstance(item, dict) and item.get("name") == "fast_mode"
                ),
                None,
            )
            if feature is not None:
                if feature.get("enabled") is not True:
                    raise AppServerError("fast_mode_unavailable")
                return
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                raise AppServerError("fast_mode_unavailable")
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                raise AppServerError("feature_catalog_invalid")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

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
                    "sandboxPolicy": dict(self.sandbox_policy),
                    **({"model": self.model} if self.model is not None else {}),
                    **({"effort": self.effort} if self.effort is not None else {}),
                    **(
                        {"serviceTierForTurn": self.service_tier}
                        if self.service_tier is not None
                        else {}
                    ),
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
            observed = {
                "model": None,
                "effort": None,
                "service_tier": None,
            }
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
                event_turn = params.get("turn")
                if isinstance(event_turn, dict):
                    for output_key, candidates in (
                        ("model", ("model",)),
                        ("effort", ("effort", "reasoningEffort")),
                        ("service_tier", ("serviceTier", "serviceTierForTurn")),
                    ):
                        for candidate in candidates:
                            value = event_turn.get(candidate)
                            if isinstance(value, str) and value:
                                observed[output_key] = value
                                break
                if method == "model/rerouted":
                    to_model = params.get("toModel")
                    if isinstance(to_model, str) and to_model:
                        observed["model"] = to_model
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
                    self.observed_settings = dict(observed)
                    expected = {
                        "model": self.model,
                        "effort": self.effort,
                        "service_tier": self.service_tier,
                    }
                    mismatched = any(
                        observed[key] is not None and observed[key] != expected[key]
                        for key in observed
                        if expected[key] is not None
                    )
                    observed_complete = all(
                        expected[key] is None or observed[key] is not None
                        for key in observed
                    )
                    observation_status = (
                        "blocked"
                        if mismatched
                        else (
                            "verified"
                            if observed_complete and self.model is not None
                            else "configured_unverified"
                        )
                    )
                    if self.model is not None:
                        self._log(
                            "runtime_observed",
                            thread_id=self.thread_id,
                            turn_id=turn_id,
                            model=observed["model"],
                            effort=observed["effort"],
                            service_tier=observed["service_tier"],
                            observation_status=observation_status,
                        )
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
                        observed["model"],
                        observed["effort"],
                        observed["service_tier"],
                    )
        except AppServerError as exc:
            try:
                self._log(
                    "job_transport_failed",
                    code=(
                        str(exc)
                        if str(exc) in {"timed_out", "cancelled"}
                        else "transport_failed"
                    ),
                )
            except Exception:
                pass
            finally:
                try:
                    self.interrupt()
                except Exception:
                    pass
                try:
                    self.close()
                except Exception:
                    pass
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
