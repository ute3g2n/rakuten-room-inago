"""Lifecycle, checkpoints, and resume state for one orchestration run."""

import secrets
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from automation.execution_logger import ExecutionLogger
from automation.run_lock import RunLock
from automation.schema_validator import validate_document


JST = timezone(timedelta(hours=9))
EXIT_CODES = {"success": 0, "partial": 2, "action_required": 3, "failed": 1}
ERROR_CODES = frozenset({
    "AUTH_REQUIRED", "CAPTCHA_REQUIRED", "RAKUTEN_API_ERROR", "ROOM_FETCH_FAILED",
    "AFFILIATE_FETCH_FAILED", "CLI_START_FAILED", "CLI_TIMEOUT", "CLI_NONZERO_EXIT",
    "SCHEMA_INVALID", "OUTPUT_MISSING", "DATA_MISMATCH", "LOCKED", "MISSED_SCHEDULE",
    "STALE_HEARTBEAT", "OUTPUT_WRITE_FAILED", "UNCLASSIFIED_EXCEPTION", "INPUT_INVALID",
    "FETCH_DATA_INSUFFICIENT", "MODEL_INVALID", "CLI_CANCELLED",
})


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def daily_date_status(target_date: date, operation_start_date: str) -> str:
    return "before_operation_start" if target_date < date.fromisoformat(operation_start_date) else "eligible"


class RunContext:
    def __init__(self, root: Path, manifest: dict, resume_artifact_root: Path | None = None,
                 previous_failed_steps: list[str] | None = None):
        self.root = Path(root)
        self.manifest = manifest
        self.run_id = manifest["run_id"]
        self.parent_run_id = manifest["parent_run_id"]
        self.attempt = manifest["attempt"]
        self.logger = ExecutionLogger(self.root, manifest["target_date"], self.run_id)
        self.manifest_path = self.logger.manifest_path
        self.events_path = self.logger.events_path
        self._resume_artifact_root = resume_artifact_root
        self.previous_failed_steps = previous_failed_steps or []
        self.exit_code: int | None = None

    @classmethod
    def create(cls, root: Path, flow: str, target_date: date, source: str,
               parent_run_id: str | None = None, attempt: int = 1,
               completed_stages: list[dict] | None = None,
               artifacts: list[str] | None = None,
               resume_artifact_root: Path | None = None,
               previous_failed_steps: list[str] | None = None) -> "RunContext":
        now = datetime.now(JST)
        run_id = f"{target_date:%Y%m%d}-{now:%H%M%S}-{flow}-{secrets.token_hex(3)}"
        start = now.isoformat(timespec="seconds")
        manifest = {"run_id": run_id, "parent_run_id": parent_run_id, "attempt": attempt,
                    "flow": flow, "source": source, "target_date": target_date.isoformat(),
                    "status": "running", "started_at": start, "finished_at": None,
                    "stages": completed_stages or [], "artifacts": artifacts or [], "errors": [],
                    "last_heartbeat_at": start, "last_stage": None, "model_attempts": []}
        validate_document("manifest", manifest)
        context = cls(root, manifest, resume_artifact_root, previous_failed_steps)
        context.logger.write_manifest(manifest)
        context._event("run_started", None, None)
        return context

    @classmethod
    def resume(cls, root: Path, parent_manifest_path: Path, source: str) -> "RunContext":
        import json
        parent_manifest_path = Path(parent_manifest_path)
        parent = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
        validate_document("manifest", parent)
        completed = [
            stage.copy() for stage in parent["stages"]
            if stage["status"] == "success" and all(
                (Path(artifact) if Path(artifact).is_absolute() else parent_manifest_path.parent / artifact).is_file()
                for artifact in stage["artifacts"]
            )
        ]
        verified_artifacts = list(dict.fromkeys(artifact for stage in completed for artifact in stage["artifacts"]))
        failed_steps = [stage["id"] for stage in parent["stages"] if stage["status"] == "failed"]
        return cls.create(root, parent["flow"], date.fromisoformat(parent["target_date"]), source,
                          parent_run_id=parent["run_id"], attempt=parent["attempt"] + 1,
                          completed_stages=completed, artifacts=verified_artifacts,
                          resume_artifact_root=parent_manifest_path.parent,
                          previous_failed_steps=failed_steps)

    def resume_info(self) -> dict:
        return {"parent_run_id": self.parent_run_id, "attempt": self.attempt,
                "completed_steps": [stage["id"] for stage in self.manifest["stages"] if stage["status"] == "success"],
                "failed_steps": self.previous_failed_steps,
                "artifacts": self.manifest["artifacts"],
                "artifact_root": str(self._resume_artifact_root) if self._resume_artifact_root else None}

    def _event(self, kind: str, stage: str | None, message: str | None) -> bool:
        event = {"event_id": secrets.token_hex(12), "run_id": self.run_id, "at": _now(),
                 "type": kind, "stage": stage, "message": message}
        return self.logger.append_event(event)

    def _completed(self, name: str) -> bool:
        return any(stage["id"] == name and stage["status"] == "success" for stage in self.manifest["stages"])

    def step_started(self, name: str) -> bool:
        if self._completed(name):
            return False
        if self.manifest["status"] != "running":
            raise RuntimeError("run is closed")
        if any(stage["id"] == name and stage["status"] == "running" for stage in self.manifest["stages"]):
            raise RuntimeError(f"step already running: {name}")
        self.manifest["stages"] = [stage for stage in self.manifest["stages"] if stage["id"] != name]
        self.manifest["stages"].append({"id": name, "status": "running", "started_at": _now(), "finished_at": None, "exit_code": None, "artifacts": []})
        self.manifest["last_stage"] = name
        self.logger.write_manifest(self.manifest)
        self._event("step_started", name, None)
        return True

    def step_completed(self, name: str, artifacts: list[str] | None = None) -> bool:
        if self._completed(name):
            return False
        stage = self._running_stage(name)
        stage.update(status="success", finished_at=_now(), exit_code=0)
        stage["artifacts"] = artifacts or []
        for artifact in artifacts or []:
            if artifact not in self.manifest["artifacts"]:
                self.manifest["artifacts"].append(artifact)
        self.logger.write_manifest(self.manifest)
        self._event("step_completed", name, None)
        return True

    def step_failed(self, name: str, code: str, detail: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code}")
        stage = self._running_stage(name)
        stage.update(status="failed", finished_at=_now(), exit_code=1)
        self.manifest["errors"].append({"code": code, "detail": detail})
        self.logger.write_manifest(self.manifest)
        self._event("step_failed", name, detail)

    def _running_stage(self, name: str) -> dict:
        for stage in self.manifest["stages"]:
            if stage["id"] == name and stage["status"] == "running":
                return stage
        raise RuntimeError(f"step is not running: {name}")

    def heartbeat(self) -> None:
        self.manifest["last_heartbeat_at"] = _now()
        self.logger.write_manifest(self.manifest)
        self._event("heartbeat", self.manifest["last_stage"], None)

    def record_model_attempt(self, attempt: dict) -> None:
        self.manifest["model_attempts"].append(attempt)
        self.logger.write_manifest(self.manifest)

    def finish(self, status: str) -> int:
        if status not in EXIT_CODES:
            raise ValueError(f"invalid terminal status: {status}")
        if status == "success" and any(stage["status"] == "failed" for stage in self.manifest["stages"]):
            raise ValueError("a failed step prevents success")
        if self.manifest["finished_at"] is not None:
            return self.exit_code if self.exit_code is not None else EXIT_CODES[status]
        self.manifest["status"] = status
        self.manifest["finished_at"] = _now()
        self.exit_code = EXIT_CODES[status]
        self.logger.write_manifest(self.manifest)
        self._event("run_completed", self.manifest["last_stage"], status)
        self.logger.finish_indexes(self.manifest, self.exit_code)
        return self.exit_code

    @contextmanager
    def run_step(self, name: str):
        if not self.step_started(name):
            yield False
            return
        try:
            yield True
        except Exception as error:
            self.step_failed(name, "UNCLASSIFIED_EXCEPTION", f"{type(error).__name__}: {error}")
            self.finish("failed")
            raise
        else:
            self.step_completed(name)

    @contextmanager
    def acquire_locks(self):
        lock_dir = self.root / "locks"
        with ExitStack() as stack:
            stack.enter_context(RunLock(lock_dir / "process.lock", self.run_id))
            stack.enter_context(RunLock(lock_dir / f"{self.manifest['flow']}-{self.manifest['target_date']}.lock", self.run_id))
            yield
