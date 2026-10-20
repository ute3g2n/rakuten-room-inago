"""Small sequential maintenance runner used by the local worker entry point.

This runner records what each owned process actually returned. It deliberately
does not require approval markers, content digests, or a machine-readable
handoff from one role before starting the next role.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import tomllib
from typing import Any, Callable, Mapping


class SkillChainError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Role:
    stage: str
    name: str
    config: str
    artifact: str
    task: str


ROLES = (
    Role("調査", "rr-inago-incident-investigator", "rr-inago-incident-investigator.toml", "investigation.md", "調査記録と日次側の原因・追加観測点を整理してください。"),
    Role("実装", "common-implementer", "common-implementer.toml", "implementation.md", "調査結果を読み、原因修正または次回診断に役立つ日次trace/logを実装してください。"),
    Role("試験", "common-tester", "common-tester.toml", "test.md", "実変更に直接関係する計画試験を実行し、未実施も正確に記録してください。"),
    Role("独立製品レビュー", "common-reviewer", "common-reviewer.toml", "code-review.md", "製品変更と試験記録を独立して読み、根拠付きの所見を記録してください。"),
    Role("本番反映", "rr-inago-maintenance-delivery", "rr-inago-maintenance-delivery.toml", "deployment.md", "割り当てられた製品変更を固定rootへ反映し、実操作の結果を記録してください。"),
    Role("HTML作成", "rr-inago-incident-reporter", "rr-inago-incident-reporter.toml", "report.md", "平易なHTMLを最終保存先へ直接作成してください。本文で事象、原因の確度、修正、追加trace/logを説明してください。"),
    Role("独立HTMLレビュー", "rr-inago-incident-report-reviewer", "rr-inago-incident-report-reviewer.toml", "html-review.md", "実際のHTMLを読み、事象・原因・修正・追加trace/logの正確さと秘密露出だけを確認してください。"),
)

_SECRET = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|((?:token|cookie|password|secret|api[_-]?key)\s*[:=]\s*)[^\s,;]+"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_text(value: object, limit: int = 20000) -> str | None:
    if not isinstance(value, str):
        return None
    value = _SECRET.sub(lambda match: (match.group(1) or match.group(2) or "") + "[redacted]", value)
    value = re.sub(r"https?://[^\s\"']+", "[url omitted]", value)
    value = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[address omitted]", value, flags=re.IGNORECASE)
    return value[:limit]


def _reported_save_path(response: str | None) -> str | None:
    """Extract a worker-reported destination without inspecting the artifact."""
    if not response:
        return None
    match = re.search(
        r"(?im)(?:保存先|保存パス|保存した(?:ファイル|成果物)|saved\s+(?:to|at))\s*[:：]?\s*([^\s、。，;；]+)",
        response,
    )
    return _safe_text(match.group(1), 1000) if match else None


def _append(log_path: Path, record: Mapping[str, object]) -> None:
    allowed = {
        "event", "at_utc", "stage", "role", "requested_model", "requested_effort",
        "turn_status", "turn_id", "thread_id", "process_id", "process_returncode",
        "close_error_class", "error_class", "error_code", "response", "stdout_summary",
        "stderr_output", "artifact", "reported_save_path", "job_id", "source_launch_id", "source_run_id",
        "target_date", "started_at_utc", "finished_at_utc", "operation",
        "finalization_status",
    }
    safe = {key: value for key, value in record.items() if key in allowed}
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, TypeError, ValueError):
        try:
            with (log_path.parent / "calls-fallback.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError):
            pass


def _stdout_event_summaries(events: object) -> list[dict[str, object]]:
    """Keep readable safe event/command output without persisting raw RPC frames."""
    if not isinstance(events, (list, tuple)):
        return []
    summaries: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        params = event.get("params")
        if not isinstance(params, Mapping):
            continue
        item = params.get("item")
        turn_data = params.get("turn")
        summary: dict[str, object] = {}
        method = _safe_text(event.get("method"), 80)
        if method:
            summary["event"] = method
        if isinstance(item, Mapping):
            for field, output_field, limit in (
                ("type", "item_type", 80),
                ("phase", "phase", 80),
                ("status", "status", 80),
            ):
                value = _safe_text(item.get(field), limit)
                if value is not None:
                    summary[output_field] = value
            exit_code = item.get("exitCode")
            if type(exit_code) is int:
                summary["exit_code"] = exit_code
            for field in ("text", "aggregatedOutput", "output"):
                value = item.get(field)
                if isinstance(value, str) and value:
                    summary["output"] = _safe_text(value, 4000)
                    break
        if isinstance(turn_data, Mapping):
            status = _safe_text(turn_data.get("status"), 80)
            if status is not None:
                summary["turn_status"] = status
        if summary:
            summaries.append(summary)
            if len(summaries) >= 200:
                break
    return summaries


def _reap_owned_process_after_close_error(process: object, ledger: Path, role: Role, error: BaseException) -> None:
    """Ensure a close failure cannot leave this runner's child unreaped."""

    owned = getattr(process, "process", None)
    if owned is None:
        _append(
            ledger,
            {
                "event": "process_reap_unavailable",
                "stage": role.stage,
                "role": role.name,
                "process_id": getattr(process, "process_id", None),
                "close_error_class": type(error).__name__,
            },
        )
        return

    returncode = getattr(owned, "returncode", None)
    if returncode is None:
        def observed_code(returned: Any) -> Any:
            code = getattr(owned, "returncode", None)
            return returned if code is None else code

        def operation(name: str, action: Callable[..., Any], *, timeout: float | None = None) -> tuple[bool, Any]:
            try:
                if timeout is None:
                    returned = action()
                else:
                    try:
                        returned = action(timeout=timeout)
                    except TypeError:
                        returned = action()
                code = observed_code(returned)
                _append(
                    ledger,
                    {
                        "event": "process_reap_operation",
                        "operation": name,
                        "stage": role.stage,
                        "role": role.name,
                        "process_id": getattr(process, "process_id", None),
                        "process_returncode": code,
                        "close_error_class": type(error).__name__,
                    },
                )
                return True, code
            except Exception as reap_error:
                _append(
                    ledger,
                    {
                        "event": "process_reap_operation_failed",
                        "operation": name,
                        "stage": role.stage,
                        "role": role.name,
                        "process_id": getattr(process, "process_id", None),
                        "process_returncode": getattr(owned, "returncode", None),
                        "close_error_class": type(error).__name__,
                        "error_class": type(reap_error).__name__,
                    },
                )
                return False, None

        waiter = getattr(owned, "wait", None)
        if callable(waiter):
            waited, result = operation("wait", waiter, timeout=5)
            returncode = observed_code(result)
            if not waited and returncode is None:
                terminator = getattr(owned, "terminate", None)
                if callable(terminator):
                    operation("terminate", terminator)
                    waited, result = operation("wait_after_terminate", waiter, timeout=5)
                    returncode = observed_code(result)
                if not waited and returncode is None:
                    killer = getattr(owned, "kill", None)
                    if callable(killer):
                        operation("kill", killer)
                    _, result = operation("wait_after_kill", waiter, timeout=5)
                    returncode = observed_code(result)
        else:
            _append(
                ledger,
                {
                    "event": "process_reap_operation_unavailable",
                    "operation": "wait",
                    "stage": role.stage,
                    "role": role.name,
                    "process_id": getattr(process, "process_id", None),
                    "close_error_class": type(error).__name__,
                },
            )

    _append(
        ledger,
        {
            "event": "process_reaped_after_close_error",
            "stage": role.stage,
            "role": role.name,
            "process_id": getattr(process, "process_id", None),
            "process_returncode": returncode,
            "close_error_class": type(error).__name__,
        },
    )


def _codex() -> str | None:
    configured = os.environ.get("CODEX_EXECUTABLE")
    if configured:
        found = shutil.which(configured)
        if found:
            return found
        if Path(configured).is_file():
            return str(Path(configured).resolve())
    return shutil.which("codex")


def _developer_instructions(root: Path, role: Role) -> str:
    path = root / ".codex" / "agents" / role.config
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        value = config.get("developer_instructions")
        if isinstance(value, str) and value.strip():
            return value
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        pass
    return "Complete the assigned maintenance task in the fixed project workspace, record unknowns, and continue to the next role."


def _prompt(root: Path, job_root: Path, identity: Mapping[str, object], index: int, request: str) -> str:
    role = ROLES[index]
    report_path = (
        root / "reports" / "incidents" / str(identity["target_date"])
        / f"{identity['job_id']}.html"
    )
    previous = [job_root / item.artifact for item in ROLES[:index]]
    inputs = [job_root / "incident.md", *previous]
    if index == 5 and (job_root / "html-review.md").is_file():
        inputs.extend((job_root / "html-review.md", job_root / role.artifact, report_path))
    if index == 6:
        inputs.append(report_path)
    visible = "\n".join(f"- {path}" for path in inputs if path.is_file()) or "(利用可能な前担当資料なし)"
    if index == 5 and (job_root / "html-review.md").is_file():
        task = (
            "html-review.mdのレビュー本文を読み、根拠に沿って必要な修正を判断してください。"
            "必要なら最終HTMLを直接修正し、report.mdも更新してください。"
            "定型markerの有無で分岐せず、レビュー内容を反映したか、変更不要かを自然文で記録してください。"
        )
    elif role.stage == "実装":
        task = request or role.task
    else:
        task = role.task
    return (
        f"担当: {role.stage} ({role.name})。全担当のreasoning effortはhighです。\n"
        f"対象の不変入力: {json.dumps(dict(identity), ensure_ascii=False, sort_keys=True)}\n"
        f"{task}\n"
        f"入力資料:\n{visible}\n"
        f"自分の成果は {job_root / role.artifact} に直接保存してください。"
        "保存後の最終回答には、通常の文章で保存した実際の相対pathを含めてください。"
        + (f" 最終HTMLは {report_path} へ直接保存してください。" if index == 5 else "")
        + (f" この実物を独立確認し、review本文を保存してください。定型markerを必須にしません。" if index == 6 else "")
        + "\n前段の失敗、空応答、未知原因、review/test statusで後続を止めません。通常Dailyを起動せず、10/17成果や10/19 queueを扱わず、秘密や個人情報を記録しないでください。"
    )


def _session(root: Path, job_root: Path, role: Role, factory: Callable[..., Any] | None):
    if factory is None:
        from automation.inago.app_server import AppServerSession
        factory = AppServerSession
    command = [_codex() or "codex", "app-server", "--strict-config", "-c", 'service_tier="priority"', "-c", "features.fast_mode=true"]
    return factory(
        command=command,
        cwd=root,
        log_path=job_root / "app-server" / f"{role.name}.jsonl",
        log_root=job_root,
        model="gpt-6-luna",
        effort="high",
        service_tier="priority",
        fast_mode=True,
        role_name=role.name,
        developer_instructions=_developer_instructions(root, role),
        sandbox_policy={"type": "workspaceWrite", "networkAccess": role.stage == "本番反映"},
        advisory_capability_probes=True,
        best_effort_logging=True,
    )


def run_skill_chain(
    *,
    repository_root: str | Path,
    job_id: str,
    source_launch_id: str,
    target_date: str,
    source_run_id: str | None = None,
    session_factory: Callable[..., Any] | None = None,
    timeout_seconds: int = 1200,
    start_role: int = 1,
    request: str = "",
    _allow_html_revision: bool = True,
) -> dict[str, object]:
    root = Path(repository_root).resolve()
    if not job_id or not source_launch_id or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
        raise SkillChainError("skill_chain_identity_invalid")
    if source_run_id is not None and not source_run_id.strip():
        raise SkillChainError("skill_chain_source_run_invalid")
    if isinstance(start_role, bool) or not isinstance(start_role, int) or not 1 <= start_role <= len(ROLES):
        raise SkillChainError("skill_chain_start_role_invalid")
    runtime_root = root / "rakuten-room-inago" / "runtime"
    job_root = runtime_root / "maintenance-flow" / job_id
    job_root.mkdir(parents=True, exist_ok=True)
    identity = {"job_id": job_id, "source_launch_id": source_launch_id, "source_run_id": source_run_id, "target_date": target_date}
    ledger = job_root / "calls.jsonl"
    executable = _codex()
    attempted = 0
    results: list[dict[str, object]] = []
    for index in range(start_role - 1, len(ROLES)):
        role = ROLES[index]
        attempted += 1
        process = None
        turn = None
        error = None
        close_error = None
        began = _now()
        if not executable:
            error = SkillChainError("codex_cli_unavailable")
        else:
            try:
                process = _session(root, job_root, role, session_factory)
                turn = process.run_turn(
                    _prompt(root, job_root, identity, index, request),
                    cwd=root,
                    deadline=time.monotonic() + timeout_seconds,
                )
            except Exception as exc:
                error = exc
            finally:
                if process is not None:
                    try:
                        process.close()
                    except Exception as exc:
                        close_error = exc
                        _reap_owned_process_after_close_error(
                            process, ledger, role, exc
                        )
        response = _safe_text(getattr(turn, "final_text", None)) if turn is not None else None
        stdout_summary = _stdout_event_summaries(
            getattr(turn, "events", ()) if turn is not None else ()
        )
        artifact = role.artifact
        result = {
            **identity,
            "stage": role.stage,
            "role": role.name,
            "started_at_utc": began,
            "finished_at_utc": _now(),
            "turn_status": (
                next(
                    (
                        str(event.get("params", {}).get("turn", {}).get("status"))
                        for event in reversed(getattr(turn, "events", ()))
                        if isinstance(event, Mapping)
                        and event.get("method") == "turn/completed"
                        and isinstance(event.get("params"), Mapping)
                        and isinstance(event["params"].get("turn"), Mapping)
                        and isinstance(event["params"]["turn"].get("status"), str)
                    ),
                    "returned_without_turn_completion",
                )
                if turn is not None
                else "not_completed"
            ),
            "thread_id": getattr(turn, "thread_id", None),
            "turn_id": getattr(turn, "turn_id", None),
            "process_id": getattr(process, "process_id", None),
            "process_returncode": getattr(getattr(process, "process", None), "returncode", None),
            "response": response,
            "reported_save_path": _reported_save_path(response),
            "stdout_summary": stdout_summary,
            "stderr_output": [
                safe_line
                for item in getattr(process, "safe_stderr_output", ())
                if (safe_line := _safe_text(item, 1000)) is not None
            ] if process is not None else [],
            "error_class": type(error).__name__ if error is not None else None,
            "error_code": getattr(error, "code", None) if error is not None else None,
            "close_error_class": (
                type(close_error).__name__
                if close_error is not None
                else getattr(process, "close_error_class", None)
            ),
            "artifact": artifact,
        }
        _append(ledger, {"event": "worker_finished", **result, "requested_model": "gpt-6-luna", "requested_effort": "high"})
        results.append(result)
    result = {
        **identity,
        "status": "failed",
        "reason_code": "skill_chain_role_incomplete",
        "roles_attempted": attempted,
        "roles": results,
        "job_root": job_root.relative_to(root).as_posix(),
    }
    review_path = job_root / "html-review.md"
    if _allow_html_revision:
        # Always give the author the reviewer's actual prose and then ask the
        # same distinct reviewer to read the final path again. An empty or
        # marker-free review is still passed forward and never stops roles.
        follow_up = run_skill_chain(
            repository_root=root,
            job_id=job_id,
            source_launch_id=source_launch_id,
            target_date=target_date,
            source_run_id=source_run_id,
            session_factory=session_factory,
            timeout_seconds=timeout_seconds,
            start_role=6,
            request=request,
            _allow_html_revision=False,
        )
        result["roles"] = [*results, *follow_up.get("roles", [])]
        result["roles_attempted"] = attempted + int(follow_up.get("roles_attempted", 0))
        result["html_review_author_recheck_attempted"] = True
    terminal = inspect_skill_chain_terminal(
        job_root=job_root,
        repository_root=root,
        identity=identity,
    )
    result["status"] = terminal["status"]
    result["reason_code"] = terminal.get("reason_code")
    return result


def inspect_skill_chain_terminal(*, job_root: str | Path, repository_root: str | Path, identity: Mapping[str, object], chain_started_at: str | None = None) -> dict[str, object]:
    path = Path(job_root) / "calls.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {"status": "incomplete", "reason_code": "skill_chain_log_missing", "recorded_roles": 0, "identity": dict(identity)}
    except (OSError, UnicodeError):
        return {"status": "incomplete", "reason_code": "skill_chain_log_unreadable", "recorded_roles": 0, "identity": dict(identity)}
    try:
        events = [json.loads(line) for line in lines if line.strip()]
    except json.JSONDecodeError:
        return {"status": "incomplete", "reason_code": "skill_chain_log_invalid", "recorded_roles": 0, "identity": dict(identity)}
    role_events = [
        item for item in events
        if isinstance(item, Mapping) and item.get("event") == "worker_finished"
    ]
    by_role: dict[str, Mapping[str, object]] = {}
    for item in role_events:
        if isinstance(item.get("role"), str):
            by_role[item["role"]] = item
    complete = len(by_role) == len(ROLES) and all(
        role.name in by_role
        and all(by_role[role.name].get(key) == value for key, value in identity.items())
        and by_role[role.name].get("turn_status") == "completed"
        and by_role[role.name].get("process_returncode") == 0
        and by_role[role.name].get("error_class") is None
        and by_role[role.name].get("close_error_class") is None
        and bool(by_role[role.name].get("response"))
        for role in ROLES
    )
    artifact_observations = {
        role.name: by_role[role.name].get("reported_save_path")
        for role in ROLES if role.name in by_role
    }
    return {
        "status": "complete" if complete else "incomplete",
        "reason_code": None if complete else "skill_chain_terminal_incomplete",
        "recorded_roles": len(role_events),
        "identity": dict(identity),
        "artifact_paths_reported": artifact_observations,
        "all_role_save_paths_reported": len(artifact_observations) == len(ROLES)
        and all(artifact_observations.values()),
        "artifact_contents_reinspected": False,
    }


def build_skill_chain_workflow_provider(store: Any, *, repository_root: str | Path, start_role: int = 1, request: str = ""):
    root = Path(repository_root).resolve()

    def run(launch, check, queue, cancel_event):
        from automation.inago.maintenance.worker import WorkerWorkflowOutcome
        try:
            now = datetime.now(timezone.utc)
            job = store.ensure_skill_chain_job_for_launch(
                source_launch_id=launch.source_launch_id,
                source_run_id=launch.source_run_id,
                target_date=launch.target_date,
                queue_id=queue.queue_id,
                observed_at=now,
            )
            identity = {
                "job_id": job.job_id,
                "source_launch_id": launch.source_launch_id,
                "source_run_id": launch.source_run_id,
                "target_date": launch.target_date,
            }
            job_root = store.database_path.parent.parent / "maintenance-flow" / job.job_id
            job_root.mkdir(parents=True, exist_ok=True)
            incident_path = job_root / "incident.md"
            if not incident_path.exists():
                incident_path.write_text(
                    "# 10月20日の日次不具合\n\n"
                    "## 確認できたこと\n\n"
                    f"- 対象日: {launch.target_date}\n"
                    f"- 日次起動: {launch.source_launch_id}\n"
                    f"- 日次run: {launch.source_run_id if launch.source_run_id is not None else '未生成（null）'}\n"
                    f"- 既存queue: {queue.queue_id}\n"
                    f"- check: {check.check_id}\n"
                    f"- 保存された起動状態: {launch.status}\n"
                    f"- 保存された終了コード: {launch.exit_code if launch.exit_code is not None else '不明'}\n\n"
                    "直接原因と競合対象は未確認です。新しい日次runや下書きは作成せず、同じ起動記録を調べます。\n",
                    encoding="utf-8",
                    newline="\n",
                )
            result = run_skill_chain(
                repository_root=root,
                job_id=job.job_id,
                source_launch_id=launch.source_launch_id,
                target_date=launch.target_date,
                source_run_id=launch.source_run_id,
                start_role=start_role,
                request=request,
            )
            terminal = inspect_skill_chain_terminal(
                job_root=job_root,
                repository_root=root,
                identity=identity,
            )
            final_status = "complete" if terminal["status"] == "complete" else "failed"
            final_reason = None
            if final_status != "complete":
                candidate_reason = terminal.get("reason_code")
                final_reason = (
                    candidate_reason
                    if isinstance(candidate_reason, str)
                    and re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", candidate_reason)
                    else "skill_chain_terminal_incomplete"
                )
            try:
                finalized = store.finalize_job(
                    job_id=job.job_id,
                    final_status=final_status,
                    finalized_at=datetime.now(timezone.utc),
                    reason_code=final_reason,
                    incident_status="in_progress",
                )
                finalization = finalized.status
            except Exception as exc:
                finalization = f"not_recorded:{type(exc).__name__}"
            _append(
                job_root / "calls.jsonl",
                {
                    **identity,
                    "event": "job_finalization_observed",
                    "at_utc": _now(),
                    "stage": "controller",
                    "role": "workflow completion",
                    "finalization_status": finalization,
                    "artifact": "calls.jsonl",
                },
            )
            return WorkerWorkflowOutcome(
                status="complete" if terminal["status"] == "complete" else "unknown",
                check_id=check.check_id,
                reason_code=terminal.get("reason_code"),
                terminal_adapter="skill-chain-markdown",
            )
        except Exception as exc:
            return WorkerWorkflowOutcome(status="unknown", check_id=check.check_id, reason_code=getattr(exc, "code", "skill_chain_failed"), terminal_adapter="skill-chain-markdown")

    return run
