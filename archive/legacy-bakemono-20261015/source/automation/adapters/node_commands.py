"""Call existing Node CLIs and require a fresh JSON artifact."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from automation.redaction import redact


class CommandError(RuntimeError):
    def __init__(self, code: str, exit_code: int | None = None,
                 stdout: str = "", stderr: str = ""):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code
        self.stdout = redact(stdout)
        self.stderr = redact(stderr)


def _run(command: list[str], cwd: Path, timeout_seconds: float,
         allowed_exit_codes: frozenset[int] = frozenset({0})) -> int:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout_seconds,
                                shell=False, check=False)
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode("utf-8", "replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = error.stderr.decode("utf-8", "replace") if isinstance(error.stderr, bytes) else error.stderr or ""
        raise CommandError("TIMEOUT", stdout=stdout, stderr=stderr) from error
    except OSError as error:
        raise CommandError("START_FAILED") from error
    if result.returncode not in allowed_exit_codes:
        raise CommandError("NONZERO_EXIT", result.returncode, result.stdout, result.stderr)
    return result.returncode


def run_json_command(command: list[str], artifact: Path, cwd: Path,
                     timeout_seconds: float,
                     allowed_exit_codes: frozenset[int] = frozenset({0})) -> dict:
    artifact = Path(artifact)
    previous_mtime = artifact.stat().st_mtime_ns if artifact.is_file() else None
    _run(command, Path(cwd), timeout_seconds, allowed_exit_codes)
    if not artifact.is_file() or (previous_mtime is not None and artifact.stat().st_mtime_ns <= previous_mtime):
        raise CommandError("STALE_OR_MISSING_ARTIFACT")
    try:
        document = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CommandError("INVALID_JSON") from error
    if not isinstance(document, dict):
        raise CommandError("INVALID_JSON")
    return document


def run_snapshot_command(command: list[str], directory: Path, cwd: Path,
                         timeout_seconds: float, allow_partial: bool = False) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    before = {path for path in directory.glob("*.json")}
    started = time.time_ns()
    try:
        _run(command, Path(cwd), timeout_seconds)
        exit_code = 0
    except CommandError as error:
        if not allow_partial or error.code != "NONZERO_EXIT":
            raise
        exit_code = error.exit_code
    fresh = [path for path in directory.glob("*.json")
             if path not in before and path.stat().st_mtime_ns >= started]
    if len(fresh) != 1:
        raise CommandError("STALE_OR_MISSING_ARTIFACT")
    try:
        result = json.loads(fresh[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CommandError("INVALID_JSON") from error
    if exit_code and not result.get("summary", {}).get("failedQueries"):
        raise CommandError("NONZERO_EXIT", exit_code)
    return result


class NodeAdapters:
    def __init__(self, project_root: Path, node_executable: Path | None = None):
        self.project_root = Path(project_root).resolve()
        executable = str(node_executable) if node_executable else shutil.which("node")
        if not executable and os.name == "nt":
            program_files = os.environ.get("ProgramFiles")
            candidate = Path(program_files) / "nodejs" / "node.exe" if program_files else None
            if candidate and candidate.is_file():
                executable = str(candidate)
        if not executable:
            raise CommandError("NODE_MISSING")
        self.node = str(Path(executable).resolve())

    def affiliate_check(self, timeout_seconds: float = 600, output_dir: Path | None = None) -> dict:
        target = Path(output_dir).resolve() if output_dir is not None else self.project_root / "rakuten-room-kai/affiliate-report"
        command = [self.node, "scripts/check-affiliate.mjs", "check"]
        if output_dir is not None:
            command += ["--output-dir", str(target)]
        return run_json_command(command,
                                target / "latest.json",
                                self.project_root, timeout_seconds)

    def room_likes(self, output_dir: Path, timeout_seconds: float = 600) -> dict:
        output_dir = Path(output_dir).resolve()
        return run_json_command([self.node, "scripts/collect-room-likes.mjs", "--days", "30",
                                 "--output-dir", str(output_dir)], output_dir / "latest.json",
                                self.project_root, timeout_seconds)

    def research_brief(self, target_date: str, output_path: Path,
                       timeout_seconds: float = 120) -> dict:
        output_path = Path(output_path).resolve()
        return run_json_command([self.node, "scripts/research-brief.mjs", "--date", target_date,
                                 "--json-output", str(output_path)], output_path,
                                self.project_root, timeout_seconds)

    def research_collect(self, output_dir: Path, timeout_seconds: float = 600,
                         brief_json: Path | None = None,
                         plan_json: Path | None = None) -> dict:
        output_dir = Path(output_dir).resolve()
        command = [self.node, "scripts/research-batch.mjs", "--output-dir", str(output_dir)]
        if brief_json is not None:
            command += ["--brief-json", str(Path(brief_json).resolve())]
        if plan_json is not None:
            command += ["--plan-json", str(Path(plan_json).resolve())]
        return run_snapshot_command(command, output_dir, self.project_root, timeout_seconds,
                                    allow_partial=True)

    def rakuten_api(self, command: str, options: list[str], output_path: Path,
                    timeout_seconds: float = 120) -> dict:
        if command not in {"search", "ranking", "verify"}:
            raise ValueError("invalid Rakuten API command")
        output_path = Path(output_path).resolve()
        return run_json_command([self.node, "scripts/rakuten-api.mjs", command, *options,
                                 "--json-output", str(output_path)], output_path,
                                self.project_root, timeout_seconds)
