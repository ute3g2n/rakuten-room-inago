"""Run a bounded, schema-checked Codex job without a shell."""

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from automation.atomic_io import atomic_write_json
from automation.job_prompt_builder import build_job_prompt
from automation.model_policy import ModelPolicy, NO_RETRY_ERRORS
from automation.redaction import redact_data
from automation.run_context import RunContext
from automation.schema_validator import SCHEMA_DIR, SchemaValidationError, validate_document


@dataclass
class CliResult:
    success: bool
    error_code: str | None
    output_path: Path | None
    attempts: list[dict]


class CodexCliRunner:
    def __init__(self, project_root: Path, executable: Path | None = None,
                 executable_prefix: list[str] | None = None, timeout_seconds: float = 1200,
                 extra_env: dict[str, str] | None = None, policy: ModelPolicy | None = None):
        self.project_root = Path(project_root).resolve()
        found = str(executable) if executable is not None else shutil.which("codex")
        if not found and os.name == "nt":
            # Task Scheduler does not inherit Codex Desktop's session PATH.
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                candidates = list((Path(local_app_data) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
                if candidates:
                    found = str(max(candidates, key=lambda path: path.stat().st_mtime_ns))
        if not found:
            raise ValueError("Codex executable is not installed")
        self.executable = Path(found).resolve()
        if not self.executable.is_file():
            raise ValueError("Codex executable path does not exist")
        self.executable_prefix = executable_prefix or []
        self.timeout_seconds = timeout_seconds
        self.extra_env = extra_env or {}
        self.policy = policy or ModelPolicy()

    def command(self, model: str, effort: str, schema_path: Path, output_path: Path) -> list[str]:
        return [str(self.executable), *self.executable_prefix, "exec", "-C", str(self.project_root),
                "--skip-git-repo-check", "--ephemeral", "--model", model,
                "--config", f"model_reasoning_effort={effort}", "--output-schema", str(schema_path),
                "--json", "--output-last-message", str(output_path), "-"]

    def _invoke(self, command: list[str], prompt: str, cancel_event: Event | None) -> tuple[int | None, str, str, str | None]:
        environment = os.environ.copy()
        environment.update(self.extra_env)
        try:
            process = subprocess.Popen(command, cwd=self.project_root, env=environment,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                       errors="replace", shell=False)
        except OSError:
            return None, "", "", "CLI_START_FAILED"
        deadline = time.monotonic() + self.timeout_seconds
        pending_input = prompt
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                stdout, stderr = process.communicate()
                return process.returncode, stdout, stderr, "CLI_CANCELLED"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                stdout, stderr = process.communicate()
                return process.returncode, stdout, stderr, "CLI_TIMEOUT"
            try:
                stdout, stderr = process.communicate(input=pending_input, timeout=min(0.2, remaining))
                return process.returncode, stdout, stderr, None
            except subprocess.TimeoutExpired:
                pending_input = None

    @staticmethod
    def _check_product_links(value: object, allowed_ids: set[str] | None,
                             url_by_id: dict[str, str] | None) -> None:
        if isinstance(value, dict):
            product_id = value.get("product_id")
            if product_id is not None:
                if allowed_ids is not None and product_id not in allowed_ids:
                    raise SchemaValidationError(f"unknown product ID: {product_id}")
                if url_by_id is not None and "url" in value and product_id in url_by_id:
                    if value["url"].rstrip("/") != url_by_id[product_id].rstrip("/"):
                        raise SchemaValidationError(f"URL mismatch for {product_id}")
            for nested in value.values():
                CodexCliRunner._check_product_links(nested, allowed_ids, url_by_id)
        elif isinstance(value, list):
            for nested in value:
                CodexCliRunner._check_product_links(nested, allowed_ids, url_by_id)

    def run(self, context: RunContext, job_name: str, subskill: Path, input_path: Path,
            envelope: Path, schema_name: str, escalation_reason: str | None = None,
            allowed_ids: set[str] | None = None, url_by_id: dict[str, str] | None = None,
            cancel_event: Event | None = None,
            output_validator: Callable[[dict], None] | None = None,
            template_path: Path | None = None) -> CliResult:
        schema_path = SCHEMA_DIR / f"{schema_name}.schema.json"
        if not schema_path.is_file():
            raise ValueError(f"unknown output schema: {schema_name}")
        # The model API rejects JSON Schema's `uri` format. Keep the canonical
        # schema for local validation and send a compatible copy to the model.
        model_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        def strip_unsupported_formats(value: object) -> None:
            if isinstance(value, dict):
                if value.get("format") == "uri":
                    del value["format"]
                for child in value.values():
                    strip_unsupported_formats(child)
            elif isinstance(value, list):
                for child in value:
                    strip_unsupported_formats(child)
        strip_unsupported_formats(model_schema)
        model_schema_path = context.logger.run_dir / "inputs" / f"{job_name}-model-schema.json"
        atomic_write_json(model_schema_path, model_schema)
        try:
            job = self.policy.job(job_name)
        except ValueError:
            return CliResult(False, "MODEL_INVALID", None, [])
        attempts: list[dict] = []
        planned = [(job["normal_model"], job["normal_reasoning_effort"], False)] * (job["normal_retries"] + 1)
        final_error: str | None = None
        canonical = context.logger.run_dir / "outputs" / f"{job_name}.json"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        try:
            build_job_prompt(subskill, envelope, input_path, model_schema_path, canonical, template_path)
        except ValueError:
            return CliResult(False, "INPUT_INVALID", None, [])
        for index, (model, effort, escalated) in enumerate(planned, 1):
            success, error = self._attempt(context, job_name, subskill, input_path, envelope,
                                           schema_name, model_schema_path, model, effort, escalated,
                                           index, canonical, allowed_ids, url_by_id, cancel_event, attempts, output_validator,
                                           template_path)
            if success:
                return CliResult(True, None, canonical, attempts)
            final_error = error
            if error in NO_RETRY_ERRORS or error == "CLI_CANCELLED":
                return CliResult(False, error, None, attempts)
        if self.policy.can_escalate(job_name, escalation_reason, final_error):
            success, error = self._attempt(context, job_name, subskill, input_path, envelope,
                                           schema_name, model_schema_path, job["escalation_model"],
                                           job["escalation_reasoning_effort"], True,
                                           len(attempts) + 1, canonical, allowed_ids,
                                           url_by_id, cancel_event, attempts, output_validator, template_path)
            if success:
                return CliResult(True, None, canonical, attempts)
            final_error = error
        return CliResult(False, final_error, None, attempts)

    def _attempt(self, context: RunContext, job_name: str, subskill: Path, input_path: Path,
                 envelope: Path, schema_name: str, schema_path: Path, model: str, effort: str,
                 escalated: bool, number: int, canonical: Path, allowed_ids: set[str] | None,
                 url_by_id: dict[str, str] | None, cancel_event: Event | None,
                 attempts: list[dict], output_validator: Callable[[dict], None] | None,
                 template_path: Path | None) -> tuple[bool, str | None]:
        fd, temporary = tempfile.mkstemp(prefix=f"{job_name}-{number}-", suffix=".tmp",
                                         dir=canonical.parent)
        os.close(fd)
        output_path = Path(temporary)
        try:
            prompt = build_job_prompt(subskill, envelope, input_path, schema_path, output_path, template_path)
            command = self.command(model, effort, schema_path, output_path)
            exit_code, stdout, stderr, early_error = self._invoke(command, prompt, cancel_event)
            if stdout:
                context.logger.append_output("stdout", stdout)
            if stderr:
                context.logger.append_output("stderr", stderr)
            error = early_error
            if error is None and exit_code != 0:
                combined = stdout + "\n" + stderr
                if "AUTH_REQUIRED" in combined:
                    error = "AUTH_REQUIRED"
                elif "CAPTCHA_REQUIRED" in combined:
                    error = "CAPTCHA_REQUIRED"
                else:
                    error = "CLI_NONZERO_EXIT"
            if error is None and (not output_path.exists() or output_path.stat().st_size == 0):
                error = "OUTPUT_MISSING"
            if error is None:
                try:
                    raw = json.loads(output_path.read_text(encoding="utf-8"))
                    safe = redact_data(raw)
                    if safe != raw:
                        raise SchemaValidationError("credential-shaped output")
                    validate_document(schema_name, raw)
                    if raw.get("run_id", context.run_id) != context.run_id:
                        raise SchemaValidationError("run ID mismatch")
                    if raw.get("target_date", context.manifest["target_date"]) != context.manifest["target_date"]:
                        raise SchemaValidationError("target date mismatch")
                except (ValueError, SchemaValidationError, UnicodeError):
                    error = "SCHEMA_INVALID"
                else:
                    try:
                        self._check_product_links(raw, allowed_ids, url_by_id)
                    except SchemaValidationError:
                        error = "DATA_MISMATCH"
                    else:
                        if output_validator is not None:
                            try:
                                output_validator(raw)
                            except (ValueError, SchemaValidationError):
                                error = "DATA_MISMATCH"
                        if error is None:
                            try:
                                atomic_write_json(canonical, raw)
                            except OSError:
                                error = "OUTPUT_WRITE_FAILED"
            attempt = {"job": job_name, "model": model, "reasoning_effort": effort,
                       "schema_name": schema_name,
                       "attempt": number, "escalated": escalated, "exit_code": exit_code,
                       "status": "success" if error is None else error,
                       "output_path": str(canonical) if error is None else "none"}
            for line in reversed(stdout.splitlines()):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "turn.completed":
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        attempt["usage"] = {key: value for key, value in usage.items()
                                            if key in {"input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                                                       "output_tokens", "reasoning_output_tokens"}
                                            and isinstance(value, int) and value >= 0}
                    break
            context.record_model_attempt(attempt)
            attempts.append(attempt)
            return error is None, error
        finally:
            output_path.unlink(missing_ok=True)
