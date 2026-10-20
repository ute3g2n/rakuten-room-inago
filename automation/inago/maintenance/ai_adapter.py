"""Build Codex sessions for maintenance jobs from available role settings."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from automation.inago.app_server import AppServerSession, AppServerTurn
from automation.inago.maintenance.controller import MaintenanceController
from automation.inago.maintenance.diagnostic_trace import audit_maintenance_stage
from automation.inago.maintenance.failure_journal import append_failure_event
from automation.inago.maintenance.paths import _has_reparse_point
from automation.inago.maintenance.store import (
    AIParentSessionRecord,
    JobInputManifest,
    MaintenanceStoreError,
)


PARENT_ROLES: dict[str, tuple[str, ...]] = {
    "A": (
        "rr-inago-incident-investigator",
        "rr-inago-incident-investigation-reviewer",
        "common-execution-planner",
        "common-execution-plan-reviewer",
    ),
    "B": (
        "common-implementer",
        "common-tester",
        "common-reviewer",
        "rr-inago-incident-reporter",
        "rr-inago-incident-report-reviewer",
    ),
}

PARENT_SETTINGS: dict[str, tuple[str, str, str, bool]] = {
    "A": ("gpt-6.1-sol", "high", "default", False),
    "B": ("gpt-6-luna", "high", "priority", True),
}

_WORKTREE_WRITERS = {"common-implementer", "common-tester"}
_FIXED_ROOT_READONLY_REVIEWERS = {"common-reviewer"}
_AGENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_WINDOWS_RESERVED_DEVICE_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$",
    re.IGNORECASE,
)
_WINDOWS_INVALID_PATH_CHARS = frozenset('<>"|?*')


class MaintenanceAIBlockedError(RuntimeError):
    """A sanitized reason why a maintenance model session cannot proceed."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class MaintenanceAISessionPlan:
    manifest: JobInputManifest
    attempt_id: str
    attempts_root: Path
    worktrees_root: Path
    parent_key: str
    role_name: str
    session_key: str
    requested_model: str
    requested_effort: str
    requested_service_tier: str
    requested_fast_mode: bool
    role_config_sha256: str
    developer_instructions: str
    command: tuple[str, ...]
    cwd: Path
    attempt_output_root: Path
    worktree_root: Path
    log_path: Path
    sandbox_policy: Mapping[str, object]
    observed_settings: Mapping[str, str | None]
    observation_status: str
    resume_thread_id: str | None


@dataclass(frozen=True, slots=True)
class MaintenanceAITurn:
    turn: AppServerTurn
    observation_status: str
    response: Mapping[str, Any]
    output_path: Path
    output_sha256: str


def _path_within(path: Path, parent: Path, *, allow_equal: bool = True) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return allow_equal or bool(relative.parts)


def _absolute_path(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def resolve_codex_executable() -> str | None:
    """Find the installed Codex CLI even when a scheduled task has a short PATH."""

    configured = os.environ.get("CODEX_EXECUTABLE")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return str(configured_path.resolve())
        configured_command = shutil.which(configured)
        if configured_command:
            return configured_command
    available = shutil.which("codex")
    if available:
        return available
    install_roots = {
        (
            Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "bin"
            if os.environ.get("LOCALAPPDATA")
            else None
        ),
        Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin",
    }
    candidates = sorted(
        {
            path.resolve()
            for root in install_roots
            if root is not None
            for path in root.glob("*/codex.exe")
            if path.is_file()
        },
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return str(candidates[0])
    return None


def _appserver_environment(plan: MaintenanceAISessionPlan) -> dict[str, str] | None:
    """Trust only the assigned worktree for Git commands in repair sessions."""
    if plan.parent_key != "B" or not _path_within(plan.cwd, plan.worktree_root):
        return None

    environment = dict(os.environ)
    config_parameters = environment.get("GIT_CONFIG_PARAMETERS")
    if config_parameters:
        try:
            parameter_items = shlex.split(config_parameters, posix=True)
        except ValueError as exc:
            raise MaintenanceAIBlockedError("git_config_environment_invalid") from exc
        if any(
            item.partition("=")[0].casefold() == "safe.directory"
            for item in parameter_items
        ):
            raise MaintenanceAIBlockedError("git_config_environment_invalid")

    try:
        config_count = int(environment.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError as exc:
        raise MaintenanceAIBlockedError("git_config_environment_invalid") from exc
    if not 0 <= config_count <= 128:
        raise MaintenanceAIBlockedError("git_config_environment_invalid")

    inherited_config: list[tuple[str, str]] = []
    for index in range(config_count):
        key_name = f"GIT_CONFIG_KEY_{index}"
        value_name = f"GIT_CONFIG_VALUE_{index}"
        key = environment.get(key_name)
        value = environment.get(value_name)
        if key is None or value is None:
            raise MaintenanceAIBlockedError("git_config_environment_invalid")
        if key.casefold() != "safe.directory":
            inherited_config.append((key, value))

    if len(inherited_config) + 2 > 128:
        raise MaintenanceAIBlockedError("git_config_environment_invalid")
    for index in range(128):
        environment.pop(f"GIT_CONFIG_KEY_{index}", None)
        environment.pop(f"GIT_CONFIG_VALUE_{index}", None)
    for index, (key, value) in enumerate(inherited_config):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value

    reset_index = len(inherited_config)
    environment["GIT_CONFIG_COUNT"] = str(reset_index + 2)
    environment[f"GIT_CONFIG_KEY_{reset_index}"] = "safe.directory"
    environment[f"GIT_CONFIG_VALUE_{reset_index}"] = ""
    environment[f"GIT_CONFIG_KEY_{reset_index + 1}"] = "safe.directory"
    environment[f"GIT_CONFIG_VALUE_{reset_index + 1}"] = str(
        _absolute_path(plan.worktree_root)
    )
    return environment


def _assert_no_reparse_components(path: Path, code: str) -> None:
    """Reject a reparse point anywhere between the volume root and ``path``."""
    try:
        absolute = _absolute_path(path)
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            if _has_reparse_point(current):
                raise MaintenanceAIBlockedError(code)
    except MaintenanceAIBlockedError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise MaintenanceAIBlockedError(code) from exc


def _directory(path: Path, code: str) -> Path:
    try:
        absolute = _absolute_path(path)
        _assert_no_reparse_components(absolute, code)
        resolved = absolute.resolve(strict=True)
    except MaintenanceAIBlockedError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise MaintenanceAIBlockedError(code) from exc
    if not resolved.is_dir():
        raise MaintenanceAIBlockedError(code)
    return resolved


def _directory_under_root(path: Path, root: Path, code: str) -> Path:
    """Resolve an existing directory only when every component stays in root."""
    try:
        absolute_root = _absolute_path(root)
        absolute_path = _absolute_path(path)
        trusted_root = _directory(absolute_root, code)
        _assert_no_reparse_components(absolute_path, code)
        relative = absolute_path.relative_to(absolute_root)
        if not relative.parts:
            raise MaintenanceAIBlockedError(code)
        resolved = absolute_path.resolve(strict=True)
        if not resolved.is_dir() or not _path_within(
            resolved, trusted_root, allow_equal=False
        ):
            raise MaintenanceAIBlockedError(code)
        return resolved
    except MaintenanceAIBlockedError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise MaintenanceAIBlockedError(code) from exc


class MaintenanceAIAdapter:
    """Validate immutable job identity and settings before creating a session."""

    def __init__(
        self,
        controller: MaintenanceController,
        *,
        base_command: Sequence[str],
        project_root: str | Path,
        attempts_root: str | Path,
        worktrees_root: str | Path,
        agent_config_dir: str | Path | None = None,
    ) -> None:
        if not base_command or any(
            not isinstance(value, str) or not value for value in base_command
        ):
            raise ValueError("base_command must be a nonempty argument array")
        self.controller = controller
        self._diagnostic_trace = controller._diagnostic_trace
        self.base_command = tuple(base_command)
        self.project_root = _absolute_path(project_root)
        self.attempts_root = _absolute_path(attempts_root)
        self.worktrees_root = _absolute_path(worktrees_root)
        self.logs_root = self.attempts_root.parent / "logs"
        self.agent_config_dir = (
            Path(agent_config_dir).resolve()
            if agent_config_dir is not None
            else self.project_root / ".codex" / "agents"
        )

    @staticmethod
    def allowed_roles(parent_key: str) -> tuple[str, ...]:
        if parent_key not in PARENT_ROLES:
            raise MaintenanceAIBlockedError("parent_key_unauthorized")
        return PARENT_ROLES[parent_key]

    @staticmethod
    def validate_role_settings(
        *, parent_key: str, role_name: str, model: str, effort: str
    ) -> None:
        if role_name not in MaintenanceAIAdapter.allowed_roles(parent_key):
            raise MaintenanceAIBlockedError("role_unauthorized")

    @staticmethod
    def validate_model_capability(
        model: str,
        effort: str,
        *,
        available_models: Sequence[Mapping[str, object]],
    ) -> None:
        matched = next(
            (
                entry
                for entry in available_models
                if entry.get("model") == model or entry.get("id") == model
            ),
            None,
        )
        if matched is None:
            raise MaintenanceAIBlockedError("model_effort_unavailable")
        supported = matched.get("supportedReasoningEfforts")
        if not isinstance(supported, list) or not any(
            isinstance(item, Mapping) and item.get("reasoningEffort") == effort
            for item in supported
        ):
            raise MaintenanceAIBlockedError("model_effort_unavailable")

    def _role_config(
        self, *, parent_key: str, role_name: str
    ) -> tuple[str, str, str, str]:
        if not _AGENT_NAME_PATTERN.fullmatch(role_name):
            raise MaintenanceAIBlockedError("role_name_invalid")
        if role_name not in self.allowed_roles(parent_key):
            raise MaintenanceAIBlockedError("role_unauthorized")
        config_path = self.agent_config_dir / f"{role_name}.toml"
        raw_config = b""
        config: dict[str, Any] = {}
        try:
            if not _has_reparse_point(config_path):
                raw_config = config_path.read_bytes()
            config = tomllib.loads(raw_config.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            config = {}
            raw_config = b""
        if config.get("name") != role_name:
            config = {}
            raw_config = b""
        default_model, default_effort, _, _ = PARENT_SETTINGS[parent_key]
        model = config.get("model")
        effort = config.get("model_reasoning_effort")
        developer_instructions = config.get("developer_instructions")
        if not isinstance(model, str) or not model.strip():
            model = default_model
        if not isinstance(effort, str) or not effort.strip():
            effort = default_effort
        if (
            not isinstance(developer_instructions, str)
            or not developer_instructions.strip()
        ):
            developer_instructions = (
                f"Perform the assigned {role_name} work using the available inputs. "
                "Record missing information as unknown and continue."
            )
        self.validate_role_settings(
            parent_key=parent_key,
            role_name=role_name,
            model=model,
            effort=effort,
        )
        return (
            model,
            effort,
            developer_instructions,
            hashlib.sha256(raw_config).hexdigest(),
        )

    def _command(self, parent_key: str) -> tuple[str, ...]:
        if parent_key not in PARENT_SETTINGS:
            raise MaintenanceAIBlockedError("parent_key_unauthorized")
        if parent_key == "A":
            return (
                *self.base_command,
                "--strict-config",
                "-c",
                "features.fast_mode=false",
            )
        return (
            *self.base_command,
            "--strict-config",
            "-c",
            'service_tier="priority"',
            "-c",
            "features.fast_mode=true",
        )

    @staticmethod
    def _sandbox_policy(*, parent_key: str, role_name: str) -> dict[str, object]:
        if parent_key == "B" and role_name in _WORKTREE_WRITERS:
            return {"type": "workspaceWrite", "networkAccess": False}
        return {"type": "readOnly", "networkAccess": False}

    @audit_maintenance_stage("model")
    def plan_session(
        self,
        *,
        parent_key: str,
        role_name: str,
        job_id: str,
        attempt_id: str,
        identity: Mapping[str, object],
        cwd: str | Path,
        attempt_output_root: str | Path,
        worktree_root: str | Path,
        session_discriminator: str | None = None,
    ) -> MaintenanceAISessionPlan:
        if parent_key not in PARENT_SETTINGS:
            raise MaintenanceAIBlockedError("parent_key_unauthorized")
        if identity.get("job_id") != job_id:
            raise MaintenanceAIBlockedError("job_identity_invalid")
        try:
            manifest = self.controller.get_job_input_manifest(job_id=job_id)
        except MaintenanceStoreError as exc:
            raise MaintenanceAIBlockedError(exc.code) from exc
        try:
            attempt = self.controller.get_step_attempt(attempt_id=attempt_id)
        except MaintenanceStoreError as exc:
            raise MaintenanceAIBlockedError(exc.code) from exc
        if attempt.job_id != manifest.job_id:
            raise MaintenanceAIBlockedError("step_attempt_not_actionable")
        model, effort, developer_instructions, role_config_sha256 = self._role_config(
            parent_key=parent_key, role_name=role_name
        )
        _default_model, _default_effort, tier, fast_mode = PARENT_SETTINGS[parent_key]
        attempts_root = _directory(self.attempts_root, "write_root_out_of_scope")
        fixed_root_readonly_reviewer = (
            parent_key == "B" and role_name in _FIXED_ROOT_READONLY_REVIEWERS
        )
        worktrees_root = (
            self.project_root
            if fixed_root_readonly_reviewer
            else _directory(self.worktrees_root, "write_root_out_of_scope")
        )
        expected_attempt_root = attempts_root / manifest.job_id / attempt.attempt_id
        expected_worktree = worktrees_root / manifest.job_id
        if _absolute_path(attempt_output_root) != expected_attempt_root:
            raise MaintenanceAIBlockedError("write_root_out_of_scope")
        attempt_root = _directory_under_root(
            expected_attempt_root, attempts_root, "write_root_out_of_scope"
        )
        fixed_root_writer = parent_key == "B" and role_name in _WORKTREE_WRITERS
        if fixed_root_writer or fixed_root_readonly_reviewer:
            if _absolute_path(worktree_root) != self.project_root:
                raise MaintenanceAIBlockedError("write_root_out_of_scope")
            worktree = _directory(self.project_root, "write_root_out_of_scope")
        else:
            if _absolute_path(worktree_root) != expected_worktree:
                raise MaintenanceAIBlockedError("write_root_out_of_scope")
            worktree = _directory_under_root(
                expected_worktree, worktrees_root, "write_root_out_of_scope"
            )
        working_directory = _directory(Path(cwd), "cwd_out_of_scope")
        if fixed_root_writer or fixed_root_readonly_reviewer:
            permitted_cwd = (worktree,)
        elif parent_key == "A":
            permitted_cwd = (self.project_root, attempt_root)
        else:
            permitted_cwd = (self.project_root, worktree)
        if not any(_path_within(working_directory, parent) for parent in permitted_cwd):
            raise MaintenanceAIBlockedError("cwd_out_of_scope")

        policy = self._sandbox_policy(parent_key=parent_key, role_name=role_name)
        session_config_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "effort": effort,
                    "fast_mode": fast_mode,
                    "manifest_sha256": manifest.manifest_sha256,
                    "model": model,
                    "parent_key": parent_key,
                    "role_config_sha256": role_config_sha256,
                    "role_name": role_name,
                    "sandbox": policy,
                    "service_tier": tier,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        def matches_session_config(
            session: AIParentSessionRecord | None,
        ) -> bool:
            return session is None or self._stored_session_matches(
                session,
                parent_key=parent_key,
                role_name=role_name,
                manifest=manifest,
                model=model,
                effort=effort,
                tier=tier,
                fast_mode=fast_mode,
                role_config_sha256=role_config_sha256,
                sandbox=policy,
            )

        base_session_key = f"{parent_key}:{role_name}"
        session_scope = None
        if session_discriminator is not None:
            if not isinstance(session_discriminator, str) or not session_discriminator:
                raise MaintenanceAIBlockedError("session_discriminator_invalid")
            session_scope = hashlib.sha256(
                session_discriminator.encode("utf-8")
            ).hexdigest()[:24]
        scoped_session_key = (
            f"{base_session_key}:scope-{session_scope}"
            if session_scope is not None
            else base_session_key
        )
        session_key = scoped_session_key
        try:
            stored = self.controller.get_ai_parent_session(
                job_id=job_id,
                session_key=session_key,
            )
        except MaintenanceStoreError:
            stored = None
        if not matches_session_config(stored):
            # Session rows are immutable. Keep the old conversation for audit,
            # and use a separate key when role instructions or session settings
            # have changed instead of trying to resume it under new settings.
            session_key = f"{scoped_session_key}:cfg-{session_config_sha256}"
            try:
                stored = self.controller.get_ai_parent_session(
                    job_id=job_id,
                    session_key=session_key,
                )
            except MaintenanceStoreError:
                stored = None
            if not matches_session_config(stored):
                session_key = f"{session_key}:new-{uuid4().hex}"
                stored = None
        log_name_parts = [parent_key, role_name]
        if session_scope is not None:
            log_name_parts.extend(("scope", session_scope))
        if session_key != scoped_session_key:
            log_name_parts.extend(("cfg", session_config_sha256))
        log_name = "--".join(log_name_parts) + ".jsonl"
        log_path = self.logs_root / manifest.target_date / manifest.job_id / log_name
        return MaintenanceAISessionPlan(
            manifest=manifest,
            attempt_id=attempt.attempt_id,
            attempts_root=attempts_root,
            worktrees_root=worktrees_root,
            parent_key=parent_key,
            role_name=role_name,
            session_key=session_key,
            requested_model=model,
            requested_effort=effort,
            requested_service_tier=tier,
            requested_fast_mode=fast_mode,
            role_config_sha256=role_config_sha256,
            developer_instructions=developer_instructions,
            command=self._command(parent_key),
            cwd=working_directory,
            attempt_output_root=attempt_root,
            worktree_root=worktree,
            log_path=log_path,
            sandbox_policy=policy,
            observed_settings={
                "model": None,
                "effort": None,
                "service_tier": None,
            },
            observation_status="configured_unverified",
            resume_thread_id=stored.thread_id if stored is not None else None,
        )

    @staticmethod
    def _stored_session_matches(
        stored: AIParentSessionRecord,
        *,
        parent_key: str,
        role_name: str,
        manifest: JobInputManifest,
        model: str,
        effort: str,
        tier: str,
        fast_mode: bool,
        role_config_sha256: str,
        sandbox: Mapping[str, object],
    ) -> bool:
        return (
            stored.parent_key == parent_key
            and stored.role_name == role_name
            and stored.manifest_sha256 == manifest.manifest_sha256
            and stored.requested_model == model
            and stored.requested_effort == effort
            and stored.requested_service_tier == tier
            and stored.requested_fast_mode == fast_mode
            and stored.role_config_sha256 == role_config_sha256
            and dict(stored.sandbox) == dict(sandbox)
        )

    @audit_maintenance_stage("model")
    def create_session(self, plan: MaintenanceAISessionPlan) -> "MaintenanceAISession":
        log_path = self._prepare_log_path(plan.log_path)

        def bind_thread(thread_id: str) -> None:
            try:
                self.controller.bind_ai_parent_session(
                    job_id=plan.manifest.job_id,
                    session_key=plan.session_key,
                    parent_key=plan.parent_key,
                    role_name=plan.role_name,
                    thread_id=thread_id,
                    manifest_sha256=plan.manifest.manifest_sha256,
                    requested_model=plan.requested_model,
                    requested_effort=plan.requested_effort,
                    requested_service_tier=plan.requested_service_tier,
                    requested_fast_mode=plan.requested_fast_mode,
                    role_config_sha256=plan.role_config_sha256,
                    sandbox=plan.sandbox_policy,
                    created_at=datetime.now(timezone.utc),
                )
            except MaintenanceStoreError:
                pass
            try:
                self.controller.record_ai_session_observation(
                    job_id=plan.manifest.job_id,
                    session_key=plan.session_key,
                    thread_id=thread_id,
                    status="configured_unverified",
                    observed_at=datetime.now(timezone.utc),
                    reason_code="runtime_observation_unavailable",
                )
            except MaintenanceStoreError:
                pass

        app_server = AppServerSession(
            command=plan.command,
            cwd=plan.cwd,
            environment=_appserver_environment(plan),
            log_path=log_path,
            log_root=self.logs_root,
            resume_thread_id=plan.resume_thread_id,
            model=plan.requested_model,
            effort=plan.requested_effort,
            service_tier=plan.requested_service_tier,
            fast_mode=plan.requested_fast_mode,
            role_name=plan.role_name,
            role_config_sha256=plan.role_config_sha256,
            developer_instructions=plan.developer_instructions,
            sandbox_policy=plan.sandbox_policy,
            on_thread_ready=bind_thread,
        )
        return MaintenanceAISession(self.controller, plan, app_server)

    def _prepare_log_path(self, log_path: Path) -> Path:
        if not _path_within(log_path, self.logs_root, allow_equal=False):
            raise MaintenanceAIBlockedError("log_path_out_of_scope")
        try:
            _assert_no_reparse_components(self.logs_root, "log_path_invalid")
            self.logs_root.mkdir(parents=True, exist_ok=True)
            root = _directory(self.logs_root, "log_path_invalid")
            relative = log_path.relative_to(self.logs_root)
            current = root
            for part in relative.parts[:-1]:
                current = current / part
                _assert_no_reparse_components(current, "log_path_invalid")
                current.mkdir(exist_ok=True)
                if not current.is_dir():
                    raise MaintenanceAIBlockedError("log_path_invalid")
            target = root.joinpath(*relative.parts)
            _assert_no_reparse_components(target, "log_path_invalid")
            resolved = target.resolve(strict=False)
            if not _path_within(resolved, root, allow_equal=False):
                raise MaintenanceAIBlockedError("log_path_out_of_scope")
            if target.exists() and not target.is_file():
                raise MaintenanceAIBlockedError("log_path_invalid")
            return resolved
        except MaintenanceAIBlockedError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise MaintenanceAIBlockedError("log_path_invalid") from exc


class MaintenanceAISession:
    """Revalidate the manifest on each turn and append runtime observations."""

    def __init__(
        self,
        controller: MaintenanceController,
        plan: MaintenanceAISessionPlan,
        app_server: AppServerSession,
    ) -> None:
        self.controller = controller
        self._diagnostic_trace = controller._diagnostic_trace
        self.plan = plan
        self.app_server = app_server

    @audit_maintenance_stage("model")
    def run_turn(
        self,
        prompt: str,
        output_schema: Mapping[str, Any],
        *,
        allowed_outputs: Sequence[str],
        server_output_schema: Mapping[str, Any] | None = None,
        cwd: str | Path | None = None,
        deadline: float,
        cancel_event=None,
    ) -> MaintenanceAITurn:
        validator = self._prepare_output_schema(output_schema)
        server_schema = output_schema
        if server_output_schema is not None:
            self._prepare_output_schema(server_output_schema)
            server_schema = server_output_schema
        server_schema = self._strict_server_output_schema(server_schema)
        self._prepare_output_schema(server_schema)
        allowed = self._validate_allowed_outputs(allowed_outputs)
        self._revalidate_write_roots()
        try:
            self.controller.get_job_input_manifest(
                job_id=self.plan.manifest.job_id,
            )
        except MaintenanceStoreError as exc:
            raise MaintenanceAIBlockedError(exc.code) from exc
        turn_cwd = self._validate_turn_cwd(cwd)
        try:
            turn = self.app_server.run_turn(
                prompt,
                server_schema,
                cwd=turn_cwd,
                deadline=deadline,
                cancel_event=cancel_event,
            )
        except TimeoutError as exc:
            append_failure_event(
                self.controller._store.database_path.parent.parent,
                phase="failed",
                operation="ai_transport.run_turn",
                job_id=self.plan.manifest.job_id,
                source_launch_id=self.plan.manifest.source_launch_id,
                source_run_id=self.plan.manifest.source_run_id,
                incident_id=self.plan.manifest.incident_id,
                invocation_id=self.plan.attempt_id,
                stage="model",
                attempt_id=self.plan.attempt_id,
                reason_code="ai_transport_timeout",
                error=exc,
                capture_location="ai_adapter.MaintenanceAISession.run_turn",
                input_refs=(self.plan.session_key,),
                primary_sink="app_server_transport",
            )
            raise MaintenanceAIBlockedError("ai_transport_timeout") from exc
        except Exception as exc:
            append_failure_event(
                self.controller._store.database_path.parent.parent,
                phase="failed",
                operation="ai_transport.run_turn",
                job_id=self.plan.manifest.job_id,
                source_launch_id=self.plan.manifest.source_launch_id,
                source_run_id=self.plan.manifest.source_run_id,
                incident_id=self.plan.manifest.incident_id,
                invocation_id=self.plan.attempt_id,
                stage="model",
                attempt_id=self.plan.attempt_id,
                reason_code="ai_transport_failed",
                error=exc,
                capture_location="ai_adapter.MaintenanceAISession.run_turn",
                input_refs=(self.plan.session_key,),
                provider_error_type=getattr(exc, "provider_error_type", None),
                provider_error_code=getattr(exc, "provider_error_code", None),
                provider_error_param=getattr(exc, "provider_error_param", None),
                primary_sink="app_server_transport",
            )
            raise MaintenanceAIBlockedError("ai_transport_failed") from exc
        observed = {
            "model": turn.observed_model,
            "effort": turn.observed_effort,
            "service_tier": turn.observed_service_tier,
        }
        expected = {
            "model": self.plan.requested_model,
            "effort": self.plan.requested_effort,
            "service_tier": self.plan.requested_service_tier,
        }
        mismatch = any(
            observed[key] is not None and observed[key] != expected[key]
            for key in expected
        )
        complete = all(observed[key] is not None for key in expected)
        status = "verified" if complete and not mismatch else "configured_unverified"
        reason = (
            "runtime_setting_mismatch"
            if mismatch
            else (None if complete else "runtime_observation_unavailable")
        )
        try:
            self.controller.record_ai_session_observation(
                job_id=self.plan.manifest.job_id,
                session_key=self.plan.session_key,
                thread_id=turn.thread_id,
                status=status,
                observed_model=observed["model"],
                observed_effort=observed["effort"],
                observed_service_tier=observed["service_tier"],
                reason_code=reason,
                observed_at=datetime.now(timezone.utc),
            )
        except MaintenanceStoreError as exc:
            append_failure_event(
                self.controller._store.database_path.parent.parent,
                phase="fallback",
                operation="ai_session_observation.persist",
                job_id=self.plan.manifest.job_id,
                source_launch_id=self.plan.manifest.source_launch_id,
                source_run_id=self.plan.manifest.source_run_id,
                incident_id=self.plan.manifest.incident_id,
                invocation_id=self.plan.attempt_id,
                stage="model",
                attempt_id=self.plan.attempt_id,
                reason_code=exc.code,
                error=exc,
                capture_location="ai_adapter.MaintenanceAISession.run_turn",
                input_refs=(self.plan.session_key,),
                primary_sink="maintenance_ai_observation",
            )

        response = self._validate_response(turn.final_text, validator)
        output_path, output_sha256 = self._persist_response(
            turn=turn,
            response=response,
            allowed_outputs=allowed,
            observation_status=status,
        )
        return MaintenanceAITurn(
            turn=turn,
            observation_status=status,
            response=response,
            output_path=output_path,
            output_sha256=output_sha256,
        )

    @staticmethod
    def _prepare_output_schema(output_schema: Mapping[str, Any]):
        if not isinstance(output_schema, Mapping):
            raise MaintenanceAIBlockedError("output_schema_invalid")
        schema = dict(output_schema)
        try:
            json.dumps(schema, allow_nan=False)
            Draft202012Validator.check_schema(schema)
        except (TypeError, ValueError, SchemaError) as exc:
            raise MaintenanceAIBlockedError("output_schema_invalid") from exc
        return Draft202012Validator(schema)

    @classmethod
    def _strict_server_output_schema(
        cls, output_schema: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Copy a response schema into the Codex strict-schema subset.

        The original schema remains the local validation contract. Codex does
        not accept Draft 2020-12's ``unevaluatedProperties`` keyword, so the
        transport copy drops it. Where a closed root object has only explicit
        root properties, ``additionalProperties: false`` is equivalent and
        retains that rejection at the provider boundary.
        """

        def convert(value: Any) -> Any:
            if isinstance(value, Mapping):
                converted = {
                    key: convert(child)
                    for key, child in value.items()
                    if key != "unevaluatedProperties"
                }
                if (
                    value.get("unevaluatedProperties") is False
                    and value.get("type") == "object"
                    and isinstance(value.get("properties"), Mapping)
                    and not any(
                        keyword in value
                        for keyword in ("$ref", "allOf", "anyOf", "oneOf")
                    )
                ):
                    converted.setdefault("additionalProperties", False)
                return converted
            if isinstance(value, list):
                return [convert(item) for item in value]
            return value

        converted = convert(output_schema)
        if not isinstance(converted, dict):
            raise MaintenanceAIBlockedError("output_schema_invalid")
        return converted

    def _validate_allowed_outputs(
        self, allowed_outputs: Sequence[str]
    ) -> tuple[str, ...]:
        if isinstance(allowed_outputs, (str, bytes)) or not isinstance(
            allowed_outputs, Sequence
        ):
            raise MaintenanceAIBlockedError("allowed_outputs_invalid")
        fixed_root_writer = (
            self.plan.parent_key == "B"
            and self.plan.role_name in _WORKTREE_WRITERS
        )
        fixed_root_readonly_reviewer = (
            self.plan.parent_key == "B"
            and self.plan.role_name in _FIXED_ROOT_READONLY_REVIEWERS
        )
        if fixed_root_writer:
            owner_root = _directory(
                self.plan.worktree_root, "allowed_outputs_invalid"
            )
            if owner_root != self.plan.cwd:
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
        elif self.plan.role_name in _WORKTREE_WRITERS:
            owner_root = _directory_under_root(
                self.plan.worktree_root,
                self.plan.worktrees_root,
                "allowed_outputs_invalid",
            )
        elif fixed_root_readonly_reviewer:
            worktree_root = _directory(
                self.plan.worktree_root, "write_root_out_of_scope"
            )
            if worktree_root != self.plan.cwd:
                raise MaintenanceAIBlockedError("write_root_out_of_scope")
        else:
            owner_root = _directory_under_root(
                self.plan.attempt_output_root,
                self.plan.attempts_root,
                "allowed_outputs_invalid",
            )
        normalized: list[str] = []
        seen: set[str] = set()
        for value in allowed_outputs:
            if not isinstance(value, str) or not value or "\\" in value:
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            if ":" in value or "\x00" in value or any(ord(char) < 32 for char in value):
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            windows_path = PureWindowsPath(value)
            if windows_path.is_absolute() or windows_path.drive:
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            parts = value.split("/")
            if PurePosixPath(value).is_absolute() or any(
                part in {"", ".", ".."} for part in parts
            ):
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            if any(
                part.endswith((".", " "))
                or any(char in _WINDOWS_INVALID_PATH_CHARS for char in part)
                or _WINDOWS_RESERVED_DEVICE_NAME.fullmatch(part)
                for part in parts
            ):
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            target = owner_root
            for part in parts:
                target = target / part
                try:
                    _assert_no_reparse_components(target, "allowed_outputs_invalid")
                except MaintenanceAIBlockedError:
                    raise
            try:
                resolved = target.resolve(strict=False)
            except (OSError, RuntimeError, ValueError) as exc:
                raise MaintenanceAIBlockedError("allowed_outputs_invalid") from exc
            if not _path_within(resolved, owner_root, allow_equal=False):
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            if target.exists() and not target.is_file():
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            relative = PurePosixPath(*parts).as_posix()
            windows_normalized = "/".join(part.casefold() for part in parts)
            if windows_normalized in seen:
                raise MaintenanceAIBlockedError("allowed_outputs_invalid")
            normalized.append(relative)
            seen.add(windows_normalized)
        return tuple(normalized)

    def _validate_response(self, final_text: str, validator) -> dict[str, Any]:
        def reject_constant(_value: str):
            raise ValueError("non-finite JSON number")

        if not isinstance(final_text, str) or not final_text.strip():
            raise MaintenanceAIBlockedError("response_empty") from ValueError(
                "empty model response"
            )
        try:
            response = json.loads(final_text, parse_constant=reject_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MaintenanceAIBlockedError("response_json_invalid") from exc
        if not isinstance(response, dict):
            raise MaintenanceAIBlockedError("response_schema_invalid")
        try:
            validator.validate(response)
        except ValidationError as exc:
            raise MaintenanceAIBlockedError("response_schema_invalid") from exc
        expected_identity = self.plan.manifest.identity()
        if any(
            key not in response or response[key] != value
            for key, value in expected_identity.items()
        ):
            raise MaintenanceAIBlockedError("response_identity_mismatch")
        return response

    def _persist_response(
        self,
        *,
        turn: AppServerTurn,
        response: Mapping[str, Any],
        allowed_outputs: tuple[str, ...],
        observation_status: str,
    ) -> tuple[Path, str]:
        root = _directory_under_root(
            self.plan.attempt_output_root,
            self.plan.attempts_root,
            "response_output_root_invalid",
        )
        try:
            envelope = {
                "schema_version": "maintenance-ai-response-v1",
                "identity": self.plan.manifest.identity(),
                "attempt_id": self.plan.attempt_id,
                "owner": {
                    "parent_key": self.plan.parent_key,
                    "role_name": self.plan.role_name,
                    "role_config_sha256": self.plan.role_config_sha256,
                },
                "allowed_outputs": list(allowed_outputs),
                "thread_id": turn.thread_id,
                "turn_id": turn.turn_id,
                "observation_status": observation_status,
                "response": dict(response),
            }
            encoded = (
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            artifact_ref = (
                f"{self.plan.parent_key}--{self.plan.role_name}--{uuid4().hex}"
            )
            destination = root / f"{artifact_ref}.json"
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".maintenance-ai-",
                    suffix=".tmp",
                    dir=root,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(encoded)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                root = _directory_under_root(
                    self.plan.attempt_output_root,
                    self.plan.attempts_root,
                    "response_output_root_invalid",
                )
                os.link(temporary_path, destination)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            _assert_no_reparse_components(destination, "response_output_path_invalid")
            resolved = destination.resolve(strict=True)
            if not _path_within(resolved, root, allow_equal=False):
                raise MaintenanceAIBlockedError("response_output_path_invalid")
            return resolved, hashlib.sha256(encoded).hexdigest()
        except MaintenanceAIBlockedError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise MaintenanceAIBlockedError("response_persistence_failed") from exc

    def _validate_turn_cwd(self, cwd: str | Path | None) -> Path:
        candidate = self.plan.cwd if cwd is None else Path(cwd)
        resolved = _directory(candidate, "cwd_out_of_scope")
        if resolved != self.plan.cwd:
            raise MaintenanceAIBlockedError("cwd_out_of_scope")
        return resolved

    def _revalidate_write_roots(self) -> None:
        attempt_root = _directory_under_root(
            self.plan.attempt_output_root,
            self.plan.attempts_root,
            "write_root_out_of_scope",
        )
        fixed_root_writer = (
            self.plan.parent_key == "B"
            and self.plan.role_name in _WORKTREE_WRITERS
        )
        if fixed_root_writer:
            worktree_root = _directory(
                self.plan.worktree_root, "write_root_out_of_scope"
            )
            if worktree_root != self.plan.cwd:
                raise MaintenanceAIBlockedError("write_root_out_of_scope")
        else:
            worktree_root = _directory_under_root(
                self.plan.worktree_root,
                self.plan.worktrees_root,
                "write_root_out_of_scope",
            )
        if (
            attempt_root != self.plan.attempt_output_root
            or worktree_root != self.plan.worktree_root
        ):
            raise MaintenanceAIBlockedError("write_root_out_of_scope")

    def close(self) -> None:
        self.app_server.close()

    def __enter__(self) -> "MaintenanceAISession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
