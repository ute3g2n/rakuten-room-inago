"""Keep production scheduler code roots on the fixed repository checkout."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

from automation.inago.maintenance.deployment import DeploymentError
from automation.inago.maintenance.paths import _has_reparse_point


_COMMIT_PATTERN = re.compile(r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
_TASK_NAMES = (
    "RakutenRoomInagoDaily",
    "RakutenRoomInagoWorker",
    "RakutenRoomInagoWatchdog",
    "RakutenRoomInagoHumanApi",
)
_SWITCH_STATE_SCHEMA = "maintenance-runtime-switch-v1"
_POWER_SHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


class TaskSchedulerRuntimeSwitcher:
    """Switch only the code WorkingDirectory for the installed maintenance tasks.

    Executable, arguments, trigger, principal, and runtime/database paths stay
    unchanged. The previous active root is saved before changing task actions so
    DeploymentCoordinator can restore it after a failed health check or resume.
    """

    def __init__(
        self,
        *,
        repository: str | Path,
        runtime_root: str | Path,
        worktree_root: str | Path,
        python_executable: str | Path | None = None,
        task_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve(strict=True)
        self.runtime_root = Path(runtime_root).expanduser().resolve(strict=True)
        self.worktree_root = Path(worktree_root).expanduser().resolve(strict=False)
        self.python_executable = (
            Path(
                python_executable
                or self.repository / ".venv-inago" / "Scripts" / "python.exe"
            )
            .expanduser()
            .resolve(strict=True)
        )
        self.config_path = self.runtime_root / "config" / "settings.json"
        self.task_script = (
            Path(__file__)
            .with_name("task_runtime_working_directory.ps1")
            .resolve(strict=True)
        )
        self.state_path = (
            self.runtime_root / "maintenance" / "runtime-switch-state.json"
        )
        self._task_runner = task_runner

    def _run_git(self, root: Path, *arguments: str) -> str:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GCM_INTERACTIVE"] = "Never"
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentError("runtime_code_identity_unavailable") from exc
        if result.returncode != 0:
            raise DeploymentError("runtime_code_identity_unavailable")
        return result.stdout.strip()

    def _resolve_code_root(self, value: str | Path) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute() or _has_reparse_point(candidate):
            raise DeploymentError("runtime_code_root_invalid")
        try:
            root = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise DeploymentError("runtime_code_root_unavailable") from exc
        if _has_reparse_point(root):
            raise DeploymentError("runtime_code_root_invalid")
        if root != self.repository:
            raise DeploymentError("runtime_code_root_out_of_scope")
        git_root = Path(self._run_git(root, "rev-parse", "--show-toplevel"))
        if git_root.resolve(strict=True) != root:
            raise DeploymentError("runtime_code_root_invalid")
        commit = self._run_git(root, "rev-parse", "HEAD")
        if not _COMMIT_PATTERN.fullmatch(commit):
            raise DeploymentError("runtime_code_identity_invalid")
        if self._run_git(root, "status", "--porcelain", "--untracked-files=all"):
            raise DeploymentError("runtime_code_root_dirty")
        return root

    def _task_snapshot(
        self,
        mode: str,
        target: Path | None = None,
        original_enabled: Mapping[str, bool] | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        if mode not in {"Read", "Set", "Release"}:
            raise DeploymentError("runtime_task_mode_invalid")
        if mode in {"Set", "Release"} and target is None:
            raise DeploymentError("runtime_task_target_missing")
        if mode in {"Set", "Release"} and (
            not isinstance(original_enabled, Mapping)
            or set(original_enabled) != set(_TASK_NAMES)
            or any(not isinstance(value, bool) for value in original_enabled.values())
        ):
            raise DeploymentError("runtime_original_task_state_invalid")
        command = [
            _POWER_SHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.task_script),
            "-Mode",
            mode,
        ]
        if target is not None:
            command.extend(("-TargetWorkingDirectory", str(target)))
        if original_enabled is not None:
            command.extend(
                (
                    "-OriginalEnabledJson",
                    json.dumps(dict(original_enabled), separators=(",", ":")),
                )
            )
        try:
            completed = self._task_runner(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeploymentError("runtime_task_operation_unavailable") from exc
        if completed.returncode != 0:
            raise DeploymentError("runtime_task_operation_failed")
        try:
            document = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DeploymentError("runtime_task_readback_invalid") from exc
        rows = document.get("tasks") if isinstance(document, dict) else None
        names = (
            [row.get("TaskName") for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else []
        )
        if (
            not isinstance(document, dict)
            or document.get("status") != "ok"
            or not isinstance(rows, list)
            or len(names) != len(_TASK_NAMES)
            or len(set(names)) != len(_TASK_NAMES)
            or set(names) != set(_TASK_NAMES)
        ):
            raise DeploymentError("runtime_task_readback_invalid")
        return tuple(rows)

    @staticmethod
    def _original_enabled(
        snapshot: Sequence[Mapping[str, Any]],
    ) -> dict[str, bool]:
        states = {
            str(row.get("TaskName")): row.get("Enabled")
            for row in snapshot
            if row.get("TaskName") in _TASK_NAMES
        }
        if set(states) != set(_TASK_NAMES) or any(
            not isinstance(value, bool) for value in states.values()
        ):
            raise DeploymentError("runtime_task_readback_invalid")
        return {name: states[name] for name in _TASK_NAMES}

    @staticmethod
    def _working_directory(snapshot: Sequence[Mapping[str, Any]]) -> Path:
        roots = [
            str(row.get("WorkingDirectory", ""))
            for row in snapshot
            if row.get("TaskName") in _TASK_NAMES[:3]
        ]
        normalized = {
            os.path.normcase(os.path.normpath(root)) for root in roots if root
        }
        if len(roots) != 3 or len(normalized) != 1:
            raise DeploymentError("runtime_task_roots_disagree")
        return Path(roots[0])

    def _active_identity(self) -> tuple[Path, str]:
        snapshot = self._task_snapshot("Read")
        root = self._resolve_code_root(self._working_directory(snapshot))
        commit = self._run_git(root, "rev-parse", "HEAD")
        if any(
            row.get("State") in {"Running", "Queued"}
            for row in snapshot
            if row.get("TaskName") == _TASK_NAMES[3]
        ):
            raise DeploymentError("human_api_active_during_deployment")
        return root, commit

    def current_commit(self) -> str | None:
        try:
            _, commit = self._active_identity()
            return commit
        except DeploymentError:
            return None

    def _read_state(self) -> Mapping[str, Any] | None:
        if not self.state_path.exists():
            return None
        try:
            if _has_reparse_point(self.state_path):
                raise DeploymentError("runtime_switch_state_invalid")
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except DeploymentError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DeploymentError("runtime_switch_state_invalid") from exc
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != _SWITCH_STATE_SCHEMA
        ):
            raise DeploymentError("runtime_switch_state_invalid")
        return document

    def _write_state(self, document: Mapping[str, Any]) -> None:
        parent = self.state_path.parent
        if (
            not parent.is_dir()
            or _has_reparse_point(parent)
            or _has_reparse_point(self.state_path)
        ):
            raise DeploymentError("runtime_switch_state_root_invalid")
        payload = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".runtime-switch-", suffix=".tmp", dir=parent
            )
            temporary = Path(raw_path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except (OSError, ValueError) as exc:
            raise DeploymentError("runtime_switch_state_write_failed") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def activate(self, commit: str, checkout: Path | None = None) -> None:
        if not isinstance(commit, str) or not _COMMIT_PATTERN.fullmatch(commit):
            raise DeploymentError("runtime_commit_invalid")
        state = self._read_state()
        pending = state.get("pending") if isinstance(state, Mapping) else None
        if checkout is None:
            target_value = None
            if isinstance(pending, dict):
                for key in ("previous", "target"):
                    record = pending.get(key)
                    if isinstance(record, dict) and record.get("commit") == commit:
                        target_value = record.get("root")
                        break
            active = state.get("active") if isinstance(state, Mapping) else None
            if target_value is None and isinstance(active, dict):
                if active.get("commit") == commit:
                    target_value = active.get("root")
            if target_value is None:
                target_value = self.repository
        else:
            target_value = checkout
        target = self._resolve_code_root(target_value)
        target_commit = self._run_git(target, "rev-parse", "HEAD")
        if target_commit != commit:
            raise DeploymentError("runtime_target_commit_mismatch")

        snapshot_before = self._task_snapshot("Read")
        original_enabled_value = (
            pending.get("original_enabled") if isinstance(pending, dict) else None
        )
        if isinstance(pending, dict):
            if (
                not isinstance(original_enabled_value, dict)
                or set(original_enabled_value) != set(_TASK_NAMES)
                or any(
                    not isinstance(value, bool)
                    for value in original_enabled_value.values()
                )
            ):
                raise DeploymentError("runtime_switch_state_invalid")
            original_enabled = {
                name: original_enabled_value[name] for name in _TASK_NAMES
            }
        else:
            original_enabled = self._original_enabled(snapshot_before)
        try:
            current_root = self._resolve_code_root(
                self._working_directory(snapshot_before)
            )
            current_commit = self._run_git(current_root, "rev-parse", "HEAD")
        except DeploymentError:
            if checkout is not None or not isinstance(pending, dict):
                raise
            active = state.get("active") if isinstance(state, Mapping) else None
            active_record = (
                active
                if isinstance(active, dict)
                else {"commit": commit, "root": str(target)}
            )
            previous = pending.get("target")
            if not isinstance(previous, dict):
                previous = active_record
            restore_state = {
                "schema_version": _SWITCH_STATE_SCHEMA,
                "active": active_record,
                "pending": {
                    "target": {"commit": commit, "root": str(target)},
                    "previous": previous,
                    "operation": "restore",
                    "original_enabled": original_enabled,
                },
                "status": "restore_pending_health",
            }
            self._write_state(restore_state)
            self._task_snapshot("Set", target, original_enabled)
            root_after, commit_after = self._active_identity()
            if root_after != target or commit_after != commit:
                raise DeploymentError("runtime_task_switch_rollback_failed")
            self._write_state(restore_state)
            return
        if any(
            row.get("State") in {"Running", "Queued"}
            for row in snapshot_before
            if row.get("TaskName") == _TASK_NAMES[3]
        ):
            raise DeploymentError("human_api_active_during_deployment")
        if current_commit == commit and current_root == target:
            if checkout is None:
                if not isinstance(pending, dict):
                    active = state.get("active") if isinstance(state, Mapping) else None
                    active_record = (
                        active
                        if isinstance(active, dict)
                        else {"commit": commit, "root": str(target)}
                    )
                    restore_state = {
                        "schema_version": _SWITCH_STATE_SCHEMA,
                        "active": active_record,
                        "pending": {
                            "target": {"commit": commit, "root": str(target)},
                            "previous": active_record,
                            "operation": "restore",
                            "original_enabled": original_enabled,
                        },
                        "status": "restore_pending_health",
                    }
                    self._write_state(restore_state)
                    self._task_snapshot("Set", target, original_enabled)
                    fenced_snapshot = self._task_snapshot("Read")
                    if any(
                        row.get("Enabled") is not False
                        for row in fenced_snapshot
                        if row.get("TaskName") in _TASK_NAMES
                    ):
                        raise DeploymentError("runtime_task_transition_fence_missing")
                    self._write_state(restore_state)
                    return
                active = state.get("active") if isinstance(state, Mapping) else None
                active_record = (
                    active
                    if isinstance(active, dict)
                    else {"commit": commit, "root": str(target)}
                )
                restore_state = {
                    "schema_version": _SWITCH_STATE_SCHEMA,
                    "active": active_record,
                    "pending": {
                        "target": {"commit": commit, "root": str(target)},
                        "previous": pending.get("target", active_record),
                        "operation": "restore",
                        "original_enabled": original_enabled,
                    },
                    "status": "restore_pending_health",
                }
                self._write_state(restore_state)
                self._task_snapshot("Set", target, original_enabled)
                self._write_state(restore_state)
            elif isinstance(pending, dict):
                if pending.get("operation") != "activate":
                    raise DeploymentError("runtime_switch_state_invalid")
                self._task_snapshot("Set", target, original_enabled)
                fenced_snapshot = self._task_snapshot("Read")
                if any(
                    row.get("Enabled") is not False
                    for row in fenced_snapshot
                    if row.get("TaskName") in _TASK_NAMES
                ):
                    raise DeploymentError("runtime_task_transition_fence_missing")
                self._write_state({**state, "status": "active_pending_health"})
            return
        active = state.get("active") if isinstance(state, Mapping) else None
        active_record = (
            active
            if isinstance(active, dict)
            else {"commit": current_commit, "root": str(current_root)}
        )
        state_document = {
            "schema_version": _SWITCH_STATE_SCHEMA,
            "active": active_record,
            "pending": {
                "target": {"commit": commit, "root": str(target)},
                "previous": {"commit": current_commit, "root": str(current_root)},
                "operation": "restore" if checkout is None else "activate",
                "original_enabled": original_enabled,
            },
            "status": "switch_pending",
        }
        self._write_state(state_document)
        self._task_snapshot("Set", target, original_enabled)
        root_after, commit_after = self._active_identity()
        if root_after != target or commit_after != commit:
            raise DeploymentError("runtime_task_switch_readback_mismatch")
        snapshot_after = self._task_snapshot("Read")
        before_by_name = {row["TaskName"]: row for row in snapshot_before}
        after_by_name = {row["TaskName"]: row for row in snapshot_after}
        for name in _TASK_NAMES:
            old = before_by_name[name]
            new = after_by_name[name]
            for key in ("Execute", "Arguments"):
                if old.get(key) != new.get(key):
                    raise DeploymentError("runtime_task_unintended_change")
            if new.get("Enabled") is not False:
                raise DeploymentError("runtime_task_transition_fence_missing")
        if checkout is None:
            self._write_state({**state_document, "status": "restore_pending_health"})
        else:
            self._write_state({**state_document, "status": "active_pending_health"})

    def confirm_active(self, commit: str) -> None:
        root, active_commit = self._active_identity()
        if active_commit != commit:
            raise DeploymentError("runtime_active_commit_mismatch")
        state = self._read_state()
        pending = state.get("pending") if isinstance(state, Mapping) else None
        active = state.get("active") if isinstance(state, Mapping) else None
        if pending is None:
            if (
                isinstance(active, dict)
                and active.get("commit") == commit
                and active.get("root") == str(root)
            ):
                if root == self.repository:
                    return
                original_enabled = self._original_enabled(self._task_snapshot("Read"))
            else:
                raise DeploymentError("runtime_switch_state_invalid")
        else:
            original_enabled_value = (
                pending.get("original_enabled") if isinstance(pending, dict) else None
            )
            if (
                not isinstance(original_enabled_value, dict)
                or set(original_enabled_value) != set(_TASK_NAMES)
                or any(
                    not isinstance(value, bool)
                    for value in original_enabled_value.values()
                )
            ):
                raise DeploymentError("runtime_switch_state_invalid")
            original_enabled = {
                name: original_enabled_value[name] for name in _TASK_NAMES
            }

        production_root = self._resolve_code_root(self.repository)
        if self._run_git(production_root, "rev-parse", "HEAD") != commit:
            raise DeploymentError("runtime_production_checkout_commit_mismatch")
        if isinstance(active, dict):
            active_record = active
        else:
            active_record = {"commit": active_commit, "root": str(root)}
        pending_record = {
            "target": {"commit": commit, "root": str(production_root)},
            "previous": {"commit": active_commit, "root": str(root)},
            "operation": "activate",
            "original_enabled": original_enabled,
        }
        self._write_state(
            {
                "schema_version": _SWITCH_STATE_SCHEMA,
                "active": active_record,
                "pending": pending_record,
                "status": "active_root_restore_pending",
            }
        )
        self._task_snapshot("Set", production_root, original_enabled)
        active_root, confirmed_commit = self._active_identity()
        if active_root != production_root or confirmed_commit != commit:
            raise DeploymentError("runtime_production_checkout_readback_mismatch")
        fenced = self._task_snapshot("Read")
        if any(
            row.get("Enabled") is not False
            for row in fenced
            if row.get("TaskName") in _TASK_NAMES
        ):
            raise DeploymentError("runtime_task_transition_fence_missing")
        if not self.healthy(commit):
            raise DeploymentError("runtime_active_health_check_failed")

        released = self._task_snapshot("Release", production_root, original_enabled)
        if self._original_enabled(released) != original_enabled:
            raise DeploymentError("runtime_task_release_readback_mismatch")
        self._write_state(
            {
                "schema_version": _SWITCH_STATE_SCHEMA,
                "active": {"commit": commit, "root": str(production_root)},
                "pending": None,
                "status": "active",
            }
        )

    def healthy(self, expected_commit: str) -> bool:
        try:
            root, commit = self._active_identity()
            if commit != expected_commit or not self.config_path.is_file():
                return False
            result = subprocess.run(
                [
                    str(self.python_executable),
                    "-B",
                    "-m",
                    "automation.inago.maintenance.cli",
                    "--data-root",
                    str(self.runtime_root),
                    "preflight",
                    "--environment",
                    "production",
                    "--config",
                    str(self.config_path),
                ],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=45,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except (OSError, subprocess.SubprocessError, DeploymentError):
            return False
        if result.returncode != 0:
            return False
        for line in reversed(result.stdout.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload.get("status") == "ready"
        return False
