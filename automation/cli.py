"""Consistent thin CLI for every Kai business flow."""

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from automation.kai_orchestrator import KaiOrchestrator
from automation.run_context import RunContext


JST = timezone(timedelta(hours=9))
FLOWS = {"daily", "research", "performance", "setup", "config-change", "recovery", "status"}
RUN_ID = re.compile(r"^\d{8}-\d{6}-(?:daily|research|performance|setup|config-change|recovery|status)-[0-9a-f]{6}$")


def _find_manifest(runtime: Path, reference: str, flow: str | None = None) -> Path:
    candidate = Path(reference)
    if candidate.is_file():
        resolved = candidate.resolve()
        if runtime.resolve() not in resolved.parents:
            raise ValueError("resume manifest outside runtime")
        return resolved
    if not RUN_ID.fullmatch(reference):
        raise ValueError("invalid run ID")
    matches = list(runtime.glob(f"runs/*/{reference}/manifest.json"))
    if len(matches) != 1:
        raise ValueError("resume run not found")
    if flow and json.loads(matches[0].read_text(encoding="utf-8"))["flow"] != flow:
        raise ValueError("resume flow mismatch")
    return matches[0]


def _invalid_run(engine: KaiOrchestrator, flow: str, target: date, source: str, detail: str) -> RunContext:
    context = RunContext.create(engine.runtime_root, flow, target, source)
    context.manifest["errors"].append({"code": "INPUT_INVALID", "detail": detail})
    context.logger.write_manifest(context.manifest)
    context.finish("failed")
    return context


def main_for_flow(flow: str | None = None, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    if flow is None:
        parser.add_argument("flow", choices=sorted(FLOWS))
    parser.add_argument("--date", default=datetime.now(JST).date().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume")
    parser.add_argument("--source", choices=("scheduler", "chat", "manual"), default="manual")
    parser.add_argument("--run-id")
    parser.add_argument("--step")
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    name = flow or args.flow
    engine = KaiOrchestrator(Path(__file__).resolve().parents[1])
    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        target = datetime.now(JST).date()
        invalid = "invalid --date"
    else:
        invalid = None
    if args.resume and (args.run_id or args.step or name == "recovery"):
        invalid = "--resume cannot be combined with run or step; use recovery target arguments"
    if name == "recovery" and not args.resume and (not args.run_id or not args.step):
        invalid = "recovery requires --run-id and --step"
    if name == "config-change" and not args.resume and args.proposal is None:
        invalid = "config-change requires --proposal"
    if name not in {"recovery", "config-change"} and (args.run_id or args.step or args.proposal):
        invalid = "run, step and proposal are not supported by this flow"
    proposal = None
    resume_path = None
    if invalid is None:
        try:
            resume_path = _find_manifest(engine.runtime_root, args.resume, name) if args.resume else None
            proposal = json.loads(args.proposal.read_text(encoding="utf-8")) if args.proposal else None
            if proposal is not None and not isinstance(proposal, dict):
                raise ValueError("proposal must be a JSON object")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            invalid = f"invalid input: {error}"
    context = (_invalid_run(engine, name, target, args.source, invalid) if invalid else
               engine.run(name, target, args.source, args.dry_run, resume_path, proposal, args.run_id, args.step))
    summary = {"run_id": context.run_id, "flow": name, "source": args.source,
               "target_date": context.manifest["target_date"], "status": context.manifest["status"],
               "exit_code": context.exit_code, "manifest_path": str(context.manifest_path),
               "errors": context.manifest["errors"]}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"{summary['status']} run_id={context.run_id} exit={context.exit_code}")
        print(summary["manifest_path"])
        for item in summary["errors"]:
            print(f"{item['code']}: {item['detail']}")
    return context.exit_code or 0
