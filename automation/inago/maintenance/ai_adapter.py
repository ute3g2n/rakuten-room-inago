"""Build fail-closed Codex sessions for maintenance jobs."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from automation.inago.app_server import AppServerSession, AppServerTurn
from automation.inago.maintenance.controller import MaintenanceController
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
    "A": ("gpt-6.1-sol", "xhigh", "default", False),
    "B": ("gpt-6-luna", "max", "priority", True),
}

_OUTPUT_WRITERS = {
    "rr-inago-incident-investigator",
    "common-execution-planner",
}
_WORKTREE_WRITERS = {"common-implementer", "common-tester"}
_AGENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_MANIFEST_KEYS = {
    "incident_id",
    "source_launch_id",
    "source_run_id",
    "target_date",
    "job_id",
}


class MaintenanceAIBlockedError(RuntimeError):
    """A sanitized reason why a maintenance model session cannot proceed."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class MaintenanceAISessionPlan:
    manifest: JobInputManifest
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
    log_path: Path
    sandbox_policy: Mapping[str, object]
    observed_settings: Mapping[str, str | None]
    observation_status: str
    resume_thread_id: str | None


@dataclass(frozen=True, slots=True)
class MaintenanceAITurn:
    turn: AppServerTurn
    observation_status: str


def _path_within(path: Path, parent: Path, *, allow_equal: bool = True) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return allow_equal or bool(relative.parts)


def _directory(path: Path, code: str) -> Path:
    if _has_reparse_point(path):
        raise MaintenanceAIBlockedError(code)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MaintenanceAIBlockedError(code) from exc
    if not resolved.is_dir():
        raise MaintenanceAIBlockedError(code)
    return resolved


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
        self.base_command = tuple(base_command)
        self.project_root = Path(project_root).resolve()
        self.attempts_root = Path(attempts_root).resolve()
        self.worktrees_root = Path(worktrees_root).resolve()
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
        expected_model, expected_effort, _, _ = PARENT_SETTINGS[parent_key]
        if model != expected_model or effort != expected_effort:
            raise MaintenanceAIBlockedError("role_model_effort_mismatch")

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

    @staticmethod
    def require_verified_runtime(plan: MaintenanceAISessionPlan) -> None:
        expected = {
            "model": plan.requested_model,
            "effort": plan.requested_effort,
            "service_tier": plan.requested_service_tier,
        }
        if any(
            plan.observed_settings.get(key) != value for key, value in expected.items()
        ):
            raise MaintenanceAIBlockedError("runtime_observation_unavailable")

    def _role_config(
        self, *, parent_key: str, role_name: str
    ) -> tuple[str, str, str, str]:
        if not _AGENT_NAME_PATTERN.fullmatch(role_name):
            raise MaintenanceAIBlockedError("role_name_invalid")
        if role_name not in self.allowed_roles(parent_key):
            raise MaintenanceAIBlockedError("role_unauthorized")
        config_path = self.agent_config_dir / f"{role_name}.toml"
        if _has_reparse_point(config_path):
            raise MaintenanceAIBlockedError("role_config_path_invalid")
        try:
            raw_config = config_path.read_bytes()
            config = tomllib.loads(raw_config.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise MaintenanceAIBlockedError("role_config_unavailable") from exc
        if config.get("name") != role_name:
            raise MaintenanceAIBlockedError("role_config_identity_mismatch")
        model = config.get("model")
        effort = config.get("model_reasoning_effort")
        developer_instructions = config.get("developer_instructions")
        if not isinstance(model, str) or not isinstance(effort, str):
            raise MaintenanceAIBlockedError("role_model_effort_missing")
        if (
            not isinstance(developer_instructions, str)
            or not developer_instructions.strip()
        ):
            raise MaintenanceAIBlockedError("role_instructions_missing")
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
            'service_tier="fast"',
            "-c",
            "features.fast_mode=true",
        )

    def _sandbox_policy(
        self,
        *,
        parent_key: str,
        role_name: str,
        attempt_output_root: Path,
        worktree_root: Path,
        readable_roots: Sequence[Path],
    ) -> dict[str, object]:
        normalized_reads: list[str] = []
        allowed_read_parents = (self.project_root, attempt_output_root, worktree_root)
        for raw_root in readable_roots:
            root = _directory(Path(raw_root), "read_root_out_of_scope")
            if not any(_path_within(root, parent) for parent in allowed_read_parents):
                raise MaintenanceAIBlockedError("read_root_out_of_scope")
            normalized_reads.append(str(root))
        if not normalized_reads:
            raise MaintenanceAIBlockedError("read_roots_missing")
        read_access: dict[str, object] = {
            "type": "restricted",
            "includePlatformDefaults": True,
            "readableRoots": sorted(set(normalized_reads)),
        }
        if parent_key == "A" and role_name in _OUTPUT_WRITERS:
            writable_root = attempt_output_root
        elif parent_key == "B" and role_name in _WORKTREE_WRITERS:
            writable_root = worktree_root
        else:
            writable_root = None
        if writable_root is None:
            return {
                "type": "readOnly",
                "access": read_access,
                "networkAccess": False,
            }
        return {
            "type": "workspaceWrite",
            "writableRoots": [str(writable_root)],
            "readOnlyAccess": read_access,
            "networkAccess": False,
        }

    def plan_session(
        self,
        *,
        parent_key: str,
        role_name: str,
        job_id: str,
        identity: Mapping[str, object],
        cwd: str | Path,
        attempt_output_root: str | Path,
        worktree_root: str | Path,
        readable_roots: Sequence[str | Path],
    ) -> MaintenanceAISessionPlan:
        if parent_key not in PARENT_SETTINGS:
            raise MaintenanceAIBlockedError("parent_key_unauthorized")
        if set(identity) != _MANIFEST_KEYS or identity.get("job_id") != job_id:
            raise MaintenanceAIBlockedError("job_identity_invalid")
        try:
            manifest = self.controller.get_job_input_manifest(
                job_id=job_id,
                expected_identity=dict(identity),
            )
        except MaintenanceStoreError as exc:
            raise MaintenanceAIBlockedError(exc.code) from exc
        model, effort, developer_instructions, role_config_sha256 = self._role_config(
            parent_key=parent_key, role_name=role_name
        )
        expected_model, expected_effort, tier, fast_mode = PARENT_SETTINGS[parent_key]
        if model != expected_model or effort != expected_effort:
            raise MaintenanceAIBlockedError("role_model_effort_mismatch")

        attempt_root = _directory(Path(attempt_output_root), "write_root_out_of_scope")
        worktree = _directory(Path(worktree_root), "write_root_out_of_scope")
        if not _path_within(attempt_root, self.attempts_root, allow_equal=False):
            raise MaintenanceAIBlockedError("write_root_out_of_scope")
        if not _path_within(worktree, self.worktrees_root, allow_equal=False):
            raise MaintenanceAIBlockedError("write_root_out_of_scope")
        working_directory = _directory(Path(cwd), "cwd_out_of_scope")
        if parent_key == "A":
            permitted_cwd = (self.project_root, attempt_root)
        else:
            permitted_cwd = (self.project_root, worktree)
        if not any(_path_within(working_directory, parent) for parent in permitted_cwd):
            raise MaintenanceAIBlockedError("cwd_out_of_scope")

        policy = self._sandbox_policy(
            parent_key=parent_key,
            role_name=role_name,
            attempt_output_root=attempt_root,
            worktree_root=worktree,
            readable_roots=tuple(Path(root) for root in readable_roots),
        )
        session_key = f"{parent_key}:{role_name}"
        try:
            stored = self.controller.get_ai_parent_session(
                job_id=job_id,
                session_key=session_key,
            )
        except MaintenanceStoreError as exc:
            raise MaintenanceAIBlockedError(exc.code) from exc
        if stored is not None and not self._stored_session_matches(
            stored,
            parent_key=parent_key,
            role_name=role_name,
            manifest=manifest,
            model=model,
            effort=effort,
            tier=tier,
            fast_mode=fast_mode,
            role_config_sha256=role_config_sha256,
            sandbox=policy,
        ):
            raise MaintenanceAIBlockedError("session_config_mismatch")
        log_path = (
            self.logs_root
            / manifest.target_date
            / manifest.job_id
            / f"{parent_key}--{role_name}.jsonl"
        )
        return MaintenanceAISessionPlan(
            manifest=manifest,
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
                self.controller.record_ai_session_observation(
                    job_id=plan.manifest.job_id,
                    session_key=plan.session_key,
                    thread_id=thread_id,
                    status="configured_unverified",
                    observed_at=datetime.now(timezone.utc),
                    reason_code="runtime_observation_unavailable",
                )
            except MaintenanceStoreError as exc:
                raise MaintenanceAIBlockedError(exc.code) from exc

        app_server = AppServerSession(
            command=plan.command,
            cwd=plan.cwd,
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
            self.logs_root.mkdir(parents=True, exist_ok=True)
            if _has_reparse_point(self.logs_root):
                raise MaintenanceAIBlockedError("log_path_invalid")
            root = self.logs_root.resolve(strict=True)
            relative = log_path.relative_to(self.logs_root)
            current = root
            for part in relative.parts[:-1]:
                current = current / part
                if _has_reparse_point(current):
                    raise MaintenanceAIBlockedError("log_path_invalid")
                current.mkdir(exist_ok=True)
                if not current.is_dir():
                    raise MaintenanceAIBlockedError("log_path_invalid")
            target = root.joinpath(*relative.parts)
            if _has_reparse_point(target):
                raise MaintenanceAIBlockedError("log_path_invalid")
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
        self.plan = plan
        self.app_server = app_server

    def run_turn(
        self,
        prompt: str,
        output_schema: Mapping[str, Any],
        *,
        cwd: str | Path | None = None,
        deadline: float,
        cancel_event=None,
    ) -> MaintenanceAITurn:
        try:
            self.controller.get_job_input_manifest(
                job_id=self.plan.manifest.job_id,
                expected_identity=self.plan.manifest.identity(),
            )
        except MaintenanceStoreError as exc:
            raise MaintenanceAIBlockedError(exc.code) from exc
        turn_cwd = self._validate_turn_cwd(cwd)
        turn = self.app_server.run_turn(
            prompt,
            output_schema,
            cwd=turn_cwd,
            deadline=deadline,
            cancel_event=cancel_event,
        )
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
        status = (
            "blocked"
            if mismatch
            else "verified" if complete else "configured_unverified"
        )
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
            raise MaintenanceAIBlockedError(exc.code) from exc
        if mismatch:
            raise MaintenanceAIBlockedError("runtime_setting_mismatch")
        return MaintenanceAITurn(turn=turn, observation_status=status)

    def _validate_turn_cwd(self, cwd: str | Path | None) -> Path:
        candidate = self.plan.cwd if cwd is None else Path(cwd)
        resolved = _directory(candidate, "cwd_out_of_scope")
        policy = self.plan.sandbox_policy
        access = policy.get("readOnlyAccess", policy.get("access"))
        roots: list[Path] = []
        if isinstance(access, Mapping):
            readable = access.get("readableRoots")
            if isinstance(readable, list):
                roots.extend(Path(root) for root in readable if isinstance(root, str))
        writable = policy.get("writableRoots")
        if isinstance(writable, list):
            roots.extend(Path(root) for root in writable if isinstance(root, str))
        if not roots or not any(_path_within(resolved, root) for root in roots):
            raise MaintenanceAIBlockedError("cwd_out_of_scope")
        return resolved

    def close(self) -> None:
        self.app_server.close()

    def __enter__(self) -> "MaintenanceAISession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
