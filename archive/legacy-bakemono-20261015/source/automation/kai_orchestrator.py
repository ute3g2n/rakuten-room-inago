"""Shared, resumable engine for scheduled and chat workflows."""

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from automation.atomic_io import atomic_write_json, atomic_write_text
from automation.config_loader import load_config, load_workflow
from automation.dashboard_service import DashboardError, _validate_plan, record_dashboard_error, upsert_dashboard
from automation.inventory_service import build_inventory_brief
from automation.jobs.contracts import check_job_output
from automation.observation_service import affiliate_snapshot, room_snapshot
from automation.research_batching import distribute_target, genre_shortfalls
from automation.pattern_ledger import apply_analysis
from automation.performance_service import build_performance_input
from automation.redaction import redact
from automation.run_context import ERROR_CODES, RunContext, daily_date_status
from automation.schema_validator import SchemaValidationError, validate_document


class FlowError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


class KaiOrchestrator:
    def __init__(self, project_root: Path, services: object | None = None):
        self.project_root = Path(project_root).resolve()
        self.data_root = self.project_root / "rakuten-room-kai"
        self.runtime_root = self.data_root / "orchestrator"
        self.config = load_config(self.runtime_root)
        self.workflow = load_workflow(self.runtime_root)
        self.services = services

    def _prepare_data(self, context: RunContext, dry_run: bool) -> Path:
        if not dry_run:
            return self.data_root
        target = context.logger.run_dir / "sandbox-data"
        if not target.exists():
            target.mkdir(parents=True)
            for name in ("products.json", "drafts.json", "post-status.json", "posts-log.jsonl", "dashboard-days.json"):
                if (self.data_root / name).exists():
                    shutil.copy2(self.data_root / name, target / name)
            for folder in ("performance", "daily-plans"):
                if (self.data_root / folder).is_dir():
                    shutil.copytree(self.data_root / folder, target / folder)
        return target

    def _stage(self, context: RunContext, name: str, artifact: str, produce):
        path = context.logger.run_dir / artifact
        if not context.step_started(name):
            prior = Path(context.resume_info()["artifact_root"] or "") / artifact
            source = path if path.is_file() else prior
            if not source.is_file():
                raise FlowError("OUTPUT_MISSING", f"resume artifact absent: {name}")
            return json.loads(source.read_text(encoding="utf-8")) if source.suffix == ".json" else source
        try:
            result = produce()
            if path.suffix == ".json":
                atomic_write_json(path, result)
            elif isinstance(result, Path):
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(result, path)
            else:
                atomic_write_text(path, str(result))
            if not path.is_file() or path.stat().st_size == 0:
                raise FlowError("OUTPUT_MISSING", f"stage artifact absent: {name}")
            context.step_completed(name, [artifact])
            return result
        except Exception as error:
            code = getattr(error, "code", "OUTPUT_WRITE_FAILED" if isinstance(error, OSError)
                           else "UNCLASSIFIED_EXCEPTION")
            context.step_failed(name, code if code in ERROR_CODES else "UNCLASSIFIED_EXCEPTION",
                                f"{type(error).__name__}: {error}")
            raise

    def _state(self, root: Path) -> dict:
        state = {name: json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
                 for name in ("products", "drafts", "post-status")}
        for name, schema in (("products", "product-ledger"), ("drafts", "drafts"), ("post-status", "post-status")):
            validate_document(schema, state[name])
        return state

    def _model(self, context: RunContext, name: str, payload: dict) -> dict:
        if self.services is None:
            from automation.live_services import LiveServices
            self.services = LiveServices(self.project_root)
        try:
            result = self.services.model(context, name, payload)
            check_job_output(name, result, payload)
        except SchemaValidationError as error:
            raise FlowError("SCHEMA_INVALID", str(error)) from error
        except ValueError as error:
            raise FlowError("DATA_MISMATCH", str(error)) from error
        return result

    def _eligible(self, root: Path, inventory: dict) -> list[dict]:
        state = self._state(root)
        drafts = {d["product_id"]: d for d in state["drafts"]["drafts"]}
        return [{**p, "body": drafts[p["id"]]["body"]} for p in state["products"]["products"]
                if p["id"] in inventory["eligible_product_ids"]]

    def _observe(self, context: RunContext, root: Path, dry_run: bool):
        if self.services is None:
            from automation.live_services import LiveServices
            self.services = LiveServices(self.project_root)
        target = context.manifest["target_date"]
        products = self._state(root)["products"]["products"]
        complete = {s["id"] for s in context.manifest["stages"] if s["status"] == "success"}
        with ThreadPoolExecutor(max_workers=min(3, self.config["max_parallel_jobs"])) as pool:
            affiliate_future = pool.submit(self.services.affiliate, target, dry_run,
                                           context.logger.run_dir) if "affiliate_observe" not in complete else None
            room_future = pool.submit(self.services.room, target, products, dry_run, context.logger.run_dir) if "room_observe" not in complete else None
            inventory_future = pool.submit(build_inventory_brief, root, target) if "inventory_brief" not in complete else None
            affiliate = self._stage(context, "affiliate_observe", "outputs/affiliate-observation.json",
                                    affiliate_future.result if affiliate_future else lambda: None)
            room = self._stage(context, "room_observe", "outputs/room-observation.json",
                               room_future.result if room_future else lambda: None)
            inventory = self._stage(context, "inventory_brief", "outputs/inventory-brief.json",
                                    inventory_future.result if inventory_future else lambda: None)
        return affiliate, room, inventory

    def _analyze(self, context: RunContext, root: Path, affiliate: dict, room: dict) -> dict:
        target = context.manifest["target_date"]
        previous_path = root / "performance/pattern-ledger.json"
        previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {}
        data = build_performance_input(root, target, affiliate, room, previous)
        atomic_write_json(context.logger.run_dir / "inputs/performance-input.json", data)
        result = self._model(context, "performance", {"performance_input": data})
        apply_analysis(root, context.run_id, data, result)
        return result

    def _daily(self, context: RunContext, root: Path, dry_run: bool) -> str:
        target = context.manifest["target_date"]
        def load_state():
            if not dry_run and daily_date_status(date.fromisoformat(target), self.config["operation_start_date"]) != "eligible":
                raise FlowError("INPUT_INVALID", "daily target date is before operation start")
            return self._state(root)
        self._stage(context, "load_state", "inputs/state.json", load_state)
        affiliate, room, inventory = self._observe(context, root, dry_run)
        performance = self._stage(context, "performance", "outputs/performance-analysis.json",
                                  lambda: self._analyze(context, root, affiliate, room))
        def research():
            needed_by_genre = genre_shortfalls(self.config["genre_targets"], inventory["genre_counts"])
            if not any(needed_by_genre.values()):
                return {"status": "skipped", "accepted_product_ids": [], "reason": "inventory sufficient"}
            atomic_write_json(context.logger.run_dir / "inputs/research-learning.json", {
                "winning_conditions": performance["winning_conditions"],
                "deprioritized_conditions": performance["deprioritized_conditions"],
                "research_requests": performance["research_requests"]})
            prior_root = context.resume_info()["artifact_root"]
            if prior_root:
                prior_result = Path(prior_root) / "outputs/research-result.json"
                if prior_result.is_file():
                    return json.loads(prior_result.read_text(encoding="utf-8"))
            return self.services.research(context, root, inventory, performance, dry_run,
                                           target_count=sum(needed_by_genre.values()),
                                           target_by_genre=needed_by_genre)
        research_result = self._stage(context, "research", "outputs/research-result.json", research)
        refreshed = build_inventory_brief(root, target)
        def make_plan():
            existing_path = root / "daily-plans" / f"{target}.json"
            existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.is_file() else None
            if existing and len(existing["posts"]) >= self.config["daily_target"]:
                return existing
            payload = {"inventory_brief": refreshed, "eligible_products": self._eligible(root, refreshed),
                       "genre_targets": self.config["genre_targets"],
                       "posted_ids": [p["id"] for p in self._state(root)["products"]["products"] if p["status"] == "posted"],
                       "winning_conditions": performance["winning_conditions"],
                       "single_change_experiments": performance["single_change_experiments"],
                       "daily_plan_constraints": performance["daily_plan_constraints"]}
            atomic_write_json(context.logger.run_dir / "inputs/daily-plan.json", payload)
            plan = self._model(context, "daily_plan", payload)
            prior_posts = existing["posts"] if existing else []
            prior_ids = {post["product_id"] for post in prior_posts}
            plan["posts"] = prior_posts + [post for post in plan["posts"] if post["product_id"] not in prior_ids]
            plan["posts"] = plan["posts"][:self.config["daily_target"]]
            if len(plan["posts"]) == self.config["daily_target"]:
                plan["shortfall"] = []
            _validate_plan(root, plan, prior_ids)
            return plan
        try:
            plan = self._stage(context, "daily_plan", "outputs/daily-plan.json", make_plan)
            self._stage(context, "dashboard", "posting-dashboard.html", lambda: upsert_dashboard(root, plan))
        except (DashboardError, FlowError) as error:
            if isinstance(error, FlowError) and error.code not in {"DATA_MISMATCH", "SCHEMA_INVALID"}:
                raise
            record_dashboard_error(root, str(error))
            failed_report = {"target_date": target, "run_id": context.run_id, "status": "failed",
                             "planned_count": 0, "posts": [],
                             "fetch_failures": affiliate["errors"] + room["errors"],
                             "learning_applied": [],
                             "shortfall": [{"code": getattr(error, "code", "DATA_MISMATCH"),
                                            "detail": redact(str(error))}]}
            validate_document("daily-report", failed_report)
            atomic_write_json(context.logger.run_dir / "outputs/daily-report.json", failed_report)
            raise
        partial = (len(plan["posts"]) < self.config["daily_target"] or
                   affiliate["status"] != "observed" or room["status"] != "observed")
        status = "partial" if partial else "success"
        report = self._stage(context, "daily_report", "outputs/daily-report.json", lambda: {
            "target_date": target, "run_id": context.run_id, "status": status,
            "planned_count": len(plan["posts"]), "posts": plan["posts"],
            "fetch_failures": affiliate["errors"] + room["errors"],
            "learning_applied": plan["applied_learning"], "shortfall": plan["shortfall"]})
        validate_document("daily-report", report)
        lines = [f"{target} 投稿予定 {len(plan['posts'])}/{self.config['daily_target']}件"]
        if "page_check_count" in research_result:
            lines.append(f"商品ページ確認 {research_result['page_check_count']}/{research_result['candidate_pool_count']}件、"
                         f"A/B・在庫確認通過 {research_result['qualified_count']}件、"
                         f"品質確認後 {len(research_result['accepted_product_ids'])}/{research_result['target_count']}件、"
                         f"停止理由 {research_result['stop_reason']}")
            lines.append(f"初回トリアージ選定 {research_result.get('triage_selected_count', 0)}件、"
                         f"補充でページ取得を依頼 {research_result.get('fallback_page_request_count', 0)}件")
        for post in plan["posts"]:
            lines.append(f"{post['product_id']}｜{post['name']}｜{post['url']}｜{post['genre']}｜{post['body']}")
        if plan["shortfall"]:
            lines.append("不足: " + "; ".join(item["detail"] for item in plan["shortfall"]))
        atomic_write_text(context.logger.run_dir / "outputs/daily-report.txt", "\n".join(lines) + "\n")
        return status

    def _performance(self, context: RunContext, root: Path, dry_run: bool) -> str:
        if self.services is None:
            from automation.live_services import LiveServices
            self.services = LiveServices(self.project_root)
        target = context.manifest["target_date"]
        already_observed = any(s["id"] == "observations" and s["status"] == "success"
                               for s in context.manifest["stages"])
        affiliate = None if already_observed else self.services.affiliate(target, dry_run, context.logger.run_dir)
        room = None if already_observed else self.services.room(target, self._state(root)["products"]["products"],
                                                               dry_run, context.logger.run_dir)
        observations = self._stage(context, "observations", "outputs/observations.json",
                                   lambda: {"affiliate": affiliate, "room": room})
        self._stage(context, "performance", "outputs/performance-analysis.json",
                    lambda: self._analyze(context, root, observations["affiliate"], observations["room"]))
        return "success" if all(observations[name]["status"] == "observed"
                                for name in ("affiliate", "room")) else "partial"

    def _research(self, context: RunContext, root: Path, dry_run: bool) -> str:
        if self.services is None:
            from automation.live_services import LiveServices
            self.services = LiveServices(self.project_root)
        inventory = self._stage(context, "inventory_brief", "outputs/inventory-brief.json",
                                lambda: build_inventory_brief(root, context.manifest["target_date"]))
        empty = {"winning_conditions": [], "deprioritized_conditions": [], "research_requests": [],
                 "single_change_experiments": []}
        prior_root = context.resume_info()["artifact_root"]
        prior_result = Path(prior_root) / "outputs/research-result.json" if prior_root else None
        if prior_result and prior_result.is_file():
            result = json.loads(prior_result.read_text(encoding="utf-8"))
            for artifact in ("research-plan", "api-candidates", "triage-output", "page-snapshots", "heat-scores",
                             "product-verification", "copy-output", "quality-gate", "research-result"):
                source = Path(prior_root) / "outputs" / f"{artifact}.json"
                if source.is_file():
                    target = context.logger.run_dir / "outputs" / f"{artifact}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        else:
            target_count = min(self.config["daily_target"], inventory["inventory_shortfall_count"])
            result = self.services.research(
                context, root, inventory, empty, dry_run, target_count=target_count,
                target_by_genre=distribute_target(target_count, self.config["genre_targets"]))
        for name, artifact in (("research_plan", "outputs/research-plan.json"), ("api_collect", "outputs/api-candidates.json"),
                               ("triage", "outputs/triage-output.json"), ("heat_scoring", "outputs/heat-scores.json"),
                               ("product_verify", "outputs/product-verification.json"),
                               ("copywriter", "outputs/copy-output.json"), ("quality_gate", "outputs/quality-gate.json")):
            def require(path=artifact):
                file = context.logger.run_dir / path
                if not file.is_file():
                    raise FlowError("OUTPUT_MISSING", f"research artifact missing: {path}")
                return json.loads(file.read_text(encoding="utf-8"))
            self._stage(context, name, artifact, require)
        self._stage(context, "inventory_commit", "outputs/research-result.json", lambda: result)
        return "success" if result["status"] == "success" else "partial"

    def _setup(self, context: RunContext, root: Path) -> str:
        state = self._stage(context, "load_state", "inputs/state.json", lambda: self._state(root))
        migration_path = root / "migration-report.json"
        if migration_path.is_file():
            validate_document("migration-report", json.loads(migration_path.read_text(encoding="utf-8")))
        self._stage(context, "setup_review", "outputs/setup-review.json", lambda: {
            "product_count": len(state["products"]["products"]), "draft_count": len(state["drafts"]["drafts"]),
            "needs_review_count": sum(p["status"] == "needs_review" for p in state["products"]["products"]),
            "migration_report_present": migration_path.is_file(),
            "profile_confirmed": False, "requires_user_review": True})
        return "action_required"

    def _config_change(self, context: RunContext, proposal: dict | None, dry_run: bool) -> str:
        before = self._stage(context, "load_config", "inputs/config.json", lambda: self.config)
        if not isinstance(proposal, dict):
            raise FlowError("INPUT_INVALID", "config proposal required")
        after = {**before, **proposal}
        validate_document("automation-config", after)
        if sum(after["genre_targets"].values()) != after["daily_target"]:
            raise FlowError("INPUT_INVALID", "invalid genre total")
        if after["operation_start_date"] <= after["preparation_date"]:
            raise FlowError("INPUT_INVALID", "operation start must follow preparation date")
        self._stage(context, "change_review", "outputs/config-change.json", lambda: {
            "before": before, "after": after, "changed_keys": list(proposal)})
        self._stage(context, "config_commit", "outputs/config-commit.json",
                    lambda: {"applied": not dry_run, "config": after})
        if not dry_run:
            atomic_write_json(self.runtime_root / "automation-config.json", after)
        return "success"

    def _recovery(self, context: RunContext, failed_run_id: str | None, failed_step: str | None) -> str:
        if not failed_run_id or not failed_step:
            raise FlowError("INPUT_INVALID", "failed run ID and step required")
        if not re.fullmatch(r"\d{8}-\d{6}-(?:daily|research|performance|setup|config-change|recovery|status)-[0-9a-f]{6}", failed_run_id):
            raise FlowError("INPUT_INVALID", "invalid failed run ID")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", failed_step):
            raise FlowError("INPUT_INVALID", "invalid failed step")
        source = next(self.runtime_root.glob(f"runs/*/{failed_run_id}/manifest.json"), None)
        if source is None:
            raise FlowError("INPUT_INVALID", "failed run not found")
        failed = self._stage(context, "load_manifest", "inputs/failed-manifest.json",
                             lambda: json.loads(source.read_text(encoding="utf-8")))
        if not any(s["id"] == failed_step and s["status"] == "failed" for s in failed["stages"]):
            raise FlowError("INPUT_INVALID", "selected step was not failed")
        review = self._stage(context, "recovery_review", "outputs/recovery-review.json",
                             lambda: self._model(context, "recovery", {"failed_run_id": failed_run_id,
                              "failed_step": failed_step, "failure_manifest": failed}))
        def resume_stage():
            result = {"target_run_id": failed_run_id, "target_step": failed_step,
                      "proposed_action": review["proposed_action"], "automatic_retry": False,
                      "resumed_run_id": None, "resumed_status": None}
            non_retryable = {"AUTH_REQUIRED", "CAPTCHA_REQUIRED", "INPUT_INVALID", "DATA_MISMATCH"}
            failed_code = next((error["code"] for error in reversed(failed["errors"])), None)
            if not review["retryable"] or review["requires_user_action"] or failed_code in non_retryable:
                return result
            if failed["flow"] not in {"daily", "research", "performance", "setup"}:
                return result
            resumed = RunContext.resume(self.runtime_root, source, "manual")
            dry = (source.parent / "sandbox-data").is_dir()
            if dry:
                shutil.copytree(source.parent / "sandbox-data", resumed.logger.run_dir / "sandbox-data")
            try:
                root = self._prepare_data(resumed, dry)
                if failed["flow"] == "daily":
                    final_status = self._daily(resumed, root, dry)
                elif failed["flow"] == "research":
                    final_status = self._research(resumed, root, dry)
                elif failed["flow"] == "performance":
                    final_status = self._performance(resumed, root, dry)
                else:
                    final_status = self._setup(resumed, root)
                resumed.finish(final_status)
            except Exception as error:
                if resumed.manifest["status"] == "running":
                    resumed.finish("action_required" if getattr(error, "code", None) in
                                   {"AUTH_REQUIRED", "CAPTCHA_REQUIRED"} else "failed")
            result.update(automatic_retry=True, resumed_run_id=resumed.run_id,
                          resumed_status=resumed.manifest["status"])
            return result
        outcome = self._stage(context, "resume_stage", "outputs/recovery-result.json", resume_stage)
        if outcome["resumed_status"] == "success":
            return "success"
        if review["requires_user_action"] or outcome["resumed_status"] == "action_required":
            return "action_required"
        return "partial"

    def _status(self, context: RunContext) -> str:
        manifests = [path for path in self.runtime_root.glob("runs/*/*/manifest.json")
                     if path.parent.name != context.run_id]
        latest = max(manifests, key=lambda p: p.stat().st_mtime_ns) if manifests else None
        self._stage(context, "read_status", "outputs/status.json", lambda: {
            "latest_manifest": str(latest) if latest else None,
            "latest_status": json.loads(latest.read_text(encoding="utf-8"))["status"] if latest else None})
        return "success"

    def run(self, flow: str, target_date: date, source: str = "manual", dry_run: bool = False,
            resume_manifest: Path | None = None, proposal: dict | None = None,
            failed_run_id: str | None = None, failed_step: str | None = None) -> RunContext:
        if flow not in self.workflow["flows"] or flow == "health-check":
            raise ValueError("unsupported flow")
        context = (RunContext.resume(self.runtime_root, resume_manifest, source) if resume_manifest else
                   RunContext.create(self.runtime_root, flow, target_date, source))
        try:
            with context.acquire_locks():
                root = self._prepare_data(context, dry_run)
                status = {"daily": lambda: self._daily(context, root, dry_run),
                          "research": lambda: self._research(context, root, dry_run),
                          "performance": lambda: self._performance(context, root, dry_run),
                          "setup": lambda: self._setup(context, root),
                          "config-change": lambda: self._config_change(context, proposal, dry_run),
                          "recovery": lambda: self._recovery(context, failed_run_id, failed_step),
                          "status": lambda: self._status(context)}[flow]()
                context.finish(status)
        except Exception as error:
            if not context.manifest["errors"]:
                code = getattr(error, "code", "UNCLASSIFIED_EXCEPTION")
                context.manifest["errors"].append({"code": code if code in ERROR_CODES else "UNCLASSIFIED_EXCEPTION",
                                                   "detail": f"{type(error).__name__}: {error}"})
                context.logger.write_manifest(context.manifest)
            if context.manifest["status"] == "running":
                context.finish("action_required" if getattr(error, "code", None) in
                               {"AUTH_REQUIRED", "CAPTCHA_REQUIRED"} else "failed")
            context.logger.append_output("stderr", f"{type(error).__name__}: {error}\n")
        return context


def create_run(root: Path, flow: str, target_date: date, source: str) -> RunContext:
    """Compatibility helper retained for Step 03 tests."""
    return RunContext.create(root, flow, target_date, source)
