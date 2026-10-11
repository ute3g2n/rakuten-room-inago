"""External read adapters and bounded model jobs for the orchestration engine."""

import json
from datetime import date, timedelta
from itertools import chain
from pathlib import Path

from automation.adapters.node_commands import CommandError, NodeAdapters, run_json_command
from automation.atomic_io import atomic_write_json
from automation.codex_cli_runner import CodexCliRunner
from automation.inventory_service import normalize_url
from automation.jobs.contracts import check_job_output, prepare_job
from automation.observation_service import affiliate_snapshot, room_snapshot
from automation.research_collect_service import build_research_input, reclassify_snapshot
from automation.research_batching import candidate_is_postable, chunk_candidates, distribute_target, order_triaged_candidates, select_candidate_pool
from automation.schema_validator import validate_document, validate_product_store


class LiveServices:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.node = NodeAdapters(self.project_root)
        self.cli = CodexCliRunner(self.project_root)

    def affiliate(self, target_date: str, dry_run: bool, run_dir: Path | None = None) -> dict:
        prior = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
        try:
            output_dir = Path(run_dir) / "affiliate-report" if dry_run and run_dir is not None else None
            latest = self.node.affiliate_check(output_dir=output_dir) if output_dir else self.node.affiliate_check()
            return affiliate_snapshot(latest,
                                      {"status": "observed"}, prior)
        except CommandError as error:
            from automation.kai_orchestrator import FlowError
            if "ログイン" in error.stderr or "AUTH_REQUIRED" in error.stderr:
                raise FlowError("AUTH_REQUIRED", "affiliate login is required") from error
            if "CAPTCHA" in error.stderr.upper():
                raise FlowError("CAPTCHA_REQUIRED", "affiliate CAPTCHA is required") from error
            return affiliate_snapshot({}, {"status": "unavailable"}, prior)
        except Exception:
            return affiliate_snapshot({}, {"status": "unavailable"}, prior)

    def room(self, target_date: str, products: list[dict], dry_run: bool, run_dir: Path) -> dict:
        try:
            return room_snapshot(self.node.room_likes(Path(run_dir) / "room-metrics"),
                                 {"status": "observed"}, target_date, products)
        except Exception:
            return room_snapshot({}, {"status": "unavailable"}, target_date, products)

    def model(self, context, name: str, payload: dict) -> dict:
        from automation.kai_orchestrator import FlowError
        spec = prepare_job(self.project_root, context.logger.run_dir, name, context.run_id,
                           context.manifest["target_date"], payload)
        allowed = {x.get("id") or x.get("product_id") for key in
                   ("api_candidates", "selected_candidates", "verified_products", "eligible_products")
                   for x in payload.get(key, []) if isinstance(x, dict)}
        urls = {x.get("id") or x.get("product_id"): x.get("url") for key in
                ("api_candidates", "selected_candidates", "verified_products", "eligible_products")
                for x in payload.get(key, []) if isinstance(x, dict) and x.get("url")}
        result = self.cli.run(context, name, spec["subskill"], spec["input_path"], spec["envelope"],
                              spec["schema_name"], allowed_ids=allowed or None, url_by_id=urls or None,
                              output_validator=lambda output: check_job_output(name, output, payload),
                              template_path=spec["template"])
        if not result.success:
            raise FlowError(result.error_code or "CLI_NONZERO_EXIT", f"{name} failed")
        return json.loads(result.output_path.read_text(encoding="utf-8"))

    def research(self, context, root: Path, inventory: dict, performance: dict, dry_run: bool,
                 target_count: int | None = None,
                 target_by_genre: dict[str, int] | None = None) -> dict:
        from automation.kai_orchestrator import FlowError
        run_dir = context.logger.run_dir
        config = json.loads((self.project_root / "rakuten-room-kai/orchestrator/automation-config.json").read_text(encoding="utf-8"))
        batch_size = config["research_batch_size"]
        max_page_checks = config["research_max_page_checks"]
        if dry_run:
            batch_size = min(batch_size, 3)
            max_page_checks = min(max_page_checks, 3)
        if target_count is None:
            target_count = min(config["daily_target"], max(0, inventory.get("shortfall_count", config["daily_target"])))
        if not isinstance(target_count, int) or isinstance(target_count, bool) or target_count < 0:
            raise FlowError("INPUT_INVALID", "research target must be a non-negative integer")
        if target_by_genre is None:
            target_by_genre = distribute_target(target_count, config["genre_targets"])
        if (not isinstance(target_by_genre, dict) or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in target_by_genre.values()) or sum(target_by_genre.values()) != target_count):
            raise FlowError("INPUT_INVALID", "research genre targets must sum to the candidate target")
        if set(target_by_genre) - set(config["genre_targets"]):
            raise FlowError("INPUT_INVALID", "research target contains an unknown genre")

        learned = {key: performance[key] for key in
                   ("winning_conditions", "deprioritized_conditions", "research_requests")}
        plan = self.model(context, "research_plan", {"inventory_brief": inventory,
                          "genre_targets": config["genre_targets"], **learned})
        atomic_write_json(run_dir / "outputs/research-plan.json", plan)
        genre_plan = json.loads((self.project_root / "rakuten-room-kai/research/genre-plan.json").read_text(encoding="utf-8"))
        brief_path = run_dir / "inputs/research-brief.json"
        atomic_write_json(brief_path, build_research_input(inventory, genre_plan))
        snapshot = self.node.research_collect(run_dir / "api-results", brief_json=brief_path,
                                              plan_json=run_dir / "outputs/research-plan.json")
        new_candidates = [x for x in reclassify_snapshot(snapshot, root)["items"] if x["status"] == "new"]
        product_store = json.loads((root / "products.json").read_text(encoding="utf-8"))
        queries_by_genre: dict[str, list[str]] = {}
        for query in snapshot.get("queries", []):
            genre = query.get("genreName")
            keyword = query.get("keyword")
            if genre and keyword:
                values = queries_by_genre.setdefault(genre, [])
                if keyword not in values:
                    values.append(keyword)
        next_id = product_store["next_id"]
        assigned_all = []
        for candidate in new_candidates:
            source_queries = candidate.get("sourceQueries")
            source_queries = source_queries if isinstance(source_queries, list) else []
            source_genres = [query.get("genreName") for query in source_queries
                             if isinstance(query, dict) and query.get("genreName")]
            genre = next((name for name in source_genres if target_by_genre.get(name, 0) > 0), None)
            genre = genre or candidate.get("genreName") or next(iter(source_genres), None)
            assigned_all.append({"product_id": f"P{next_id:02}", "url": candidate["itemUrl"],
                                 "name": candidate.get("itemName") or candidate.get("name"),
                                 "genre": genre,
                                 "item_code": candidate.get("itemCode"),
                                 "search_queries": queries_by_genre.get(genre, [])})
            next_id += 1
        pool = select_candidate_pool(assigned_all, target_by_genre, max_page_checks)
        atomic_write_json(run_dir / "outputs/api-candidates.json", {
            "items": pool,
            "total_new_candidates": len(assigned_all),
            "page_check_pool_limit": max_page_checks,
        })
        if pool:
            triage = self.model(context, "triage", {"api_candidates": pool, "inventory_brief": inventory})
        else:
            triage = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                      "selected": [], "excluded": [], "uncertain": []}
        atomic_write_json(run_dir / "outputs/triage-output.json", triage)
        by_id = {item["product_id"]: item for item in pool}
        page_queue = order_triaged_candidates(pool, triage, max_page_checks)
        initial_selected_ids = {item["product_id"] for item in triage["selected"]}
        fallback_ids = {item["product_id"] for item in page_queue} - initial_selected_ids
        initial_queue = [item for item in page_queue if item["product_id"] in initial_selected_ids]
        fallback_queue = [item for item in page_queue if item["product_id"] in fallback_ids]
        page_request_count = 0
        fallback_page_request_count = 0

        all_snapshots = []
        all_heat_scores = []
        all_verified = []
        all_rejected = []
        all_unavailable = []
        all_drafts = []
        all_unsupported_claims = []
        all_quality_accepted = []
        all_quality_rejected = []
        all_quality_edits = []
        qualified_count = 0
        checked_batches = 0
        accepted_ids = []
        accepted_by_genre = {genre: 0 for genre in target_by_genre}

        def target_reached() -> bool:
            return all(accepted_by_genre.get(genre, 0) >= amount
                       for genre, amount in target_by_genre.items())

        batches = chain(chunk_candidates(initial_queue, batch_size),
                        chunk_candidates(fallback_queue, batch_size))
        for batch_index, candidates_batch in enumerate(batches, start=1):
            if target_reached():
                break
            checked_batches += 1
            page_request_count += len(candidates_batch)
            fallback_page_request_count += sum(item["product_id"] in fallback_ids for item in candidates_batch)
            batch_name = f"batch-{batch_index:02}"
            input_path = run_dir / "inputs" / f"page-candidates-{batch_name}.json"
            output_path = run_dir / "outputs" / f"page-snapshots-{batch_name}.json"
            page_candidates = [{"id": item["product_id"], "url": item["url"],
                                "search_queries": item["search_queries"]} for item in candidates_batch]
            atomic_write_json(input_path, {"candidates": page_candidates})
            batch_pages = run_json_command(
                [self.node.node, "scripts/verify-product-pages.mjs", "--input", str(input_path),
                 "--output", str(output_path)], output_path, self.project_root,
                config["timeouts_seconds"]["playwright"], frozenset({0, 2}))
            atomic_write_json(output_path, batch_pages)
            batch_snapshots = batch_pages.get("snapshots", [])
            batch_scores = batch_pages.get("heat_scores", [])
            all_snapshots.extend(batch_snapshots)
            all_heat_scores.extend(batch_scores)

            snapshots_by_id = {item.get("candidate_id"): item for item in batch_snapshots}
            scores_by_id = {item.get("candidate_id"): item for item in batch_scores}
            eligible_for_verify = [item for item in candidates_batch if candidate_is_postable(
                scores_by_id.get(item["product_id"]), snapshots_by_id.get(item["product_id"]))]
            qualified_count += len(eligible_for_verify)
            eligible_scores = [scores_by_id[item["product_id"]] for item in eligible_for_verify]
            eligible_pages = {"schema_version": batch_pages.get("schema_version", 1),
                              "observed_at": batch_pages.get("observed_at"),
                              "requested": len(batch_snapshots),
                              "observed": sum(item.get("status") == "observed" for item in batch_snapshots),
                              "unavailable": sum(item.get("status") == "unavailable" for item in batch_snapshots),
                              "snapshots": batch_snapshots,
                              "heat_scores": batch_scores}
            if eligible_for_verify:
                verified = self.model(context, "product_verify", {
                    "page_snapshots": eligible_pages,
                    "heat_scores": eligible_scores,
                    "selected_candidates": eligible_for_verify,
                })
            else:
                verified = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                            "verified": [], "rejected": [], "unavailable": []}
            atomic_write_json(run_dir / "outputs" / "research-batches" / batch_name / "product-verification.json", verified)
            all_verified.extend(verified["verified"])
            all_rejected.extend(verified["rejected"])
            all_unavailable.extend(verified["unavailable"])

            product_context = [{"product_id": item["product_id"], "name": item["name"],
                                "genre": item["genre"], "url": item["url"]}
                               for item in eligible_for_verify
                               if item["product_id"] in {entry["product_id"] for entry in verified["verified"]}]
            if verified["verified"]:
                copy = self.model(context, "copywriter", {
                    "verified_products": verified["verified"], "product_context": product_context,
                    "page_snapshots": eligible_pages,
                    "single_change_experiments": performance["single_change_experiments"],
                })
                quality = self.model(context, "quality_gate", {
                    "verified_products": verified["verified"], "product_context": product_context,
                    "page_snapshots": eligible_pages, "copy_output": copy,
                    "single_change_experiments": performance["single_change_experiments"],
                })
                if quality["required_edits"]:
                    copy = self.model(context, "copywriter", {
                        "verified_products": verified["verified"], "product_context": product_context,
                        "page_snapshots": eligible_pages,
                        "single_change_experiments": performance["single_change_experiments"],
                        "rejection_feedback": quality["required_edits"],
                    })
                    quality = self.model(context, "quality_gate", {
                        "verified_products": verified["verified"], "product_context": product_context,
                        "page_snapshots": eligible_pages, "copy_output": copy,
                        "single_change_experiments": performance["single_change_experiments"],
                    })
            else:
                copy = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                        "drafts": [], "unsupported_claims": []}
                quality = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                           "accepted": [], "rejected": [], "required_edits": []}
            batch_dir = run_dir / "outputs" / "research-batches" / batch_name
            atomic_write_json(batch_dir / "copy-output.json", copy)
            atomic_write_json(batch_dir / "quality-gate.json", quality)
            all_drafts.extend(copy["drafts"])
            all_unsupported_claims.extend(copy["unsupported_claims"])
            all_quality_accepted.extend(quality["accepted"])
            all_quality_rejected.extend(quality["rejected"])
            all_quality_edits.extend(quality["required_edits"])
            for product_id in quality["accepted"]:
                if product_id not in accepted_ids:
                    accepted_ids.append(product_id)
                    accepted_by_genre[by_id[product_id]["genre"]] = accepted_by_genre.get(
                        by_id[product_id]["genre"], 0) + 1

        page_summary = {"schema_version": 1,
                        "observed_at": all_snapshots[-1].get("observed_at") if all_snapshots else None,
                        "requested": len(all_snapshots),
                        "observed": sum(item.get("status") == "observed" for item in all_snapshots),
                        "unavailable": sum(item.get("status") == "unavailable" for item in all_snapshots),
                        "snapshots": all_snapshots, "heat_scores": all_heat_scores}
        atomic_write_json(run_dir / "outputs/page-snapshots.json", page_summary)
        heat_scores = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                       "scoring_method": "legacy-heat-6-factor-v1",
                       "scores": all_heat_scores}
        validate_document("heat-scoring", heat_scores)
        atomic_write_json(run_dir / "outputs/heat-scores.json", heat_scores)
        verified = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                    "verified": all_verified, "rejected": all_rejected, "unavailable": all_unavailable}
        atomic_write_json(run_dir / "outputs/product-verification.json", verified)
        copy = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                "drafts": all_drafts, "unsupported_claims": all_unsupported_claims}
        atomic_write_json(run_dir / "outputs/copy-output.json", copy)
        quality = {"run_id": context.run_id, "target_date": context.manifest["target_date"],
                   "accepted": all_quality_accepted, "rejected": all_quality_rejected,
                   "required_edits": all_quality_edits}
        atomic_write_json(run_dir / "outputs/quality-gate.json", quality)
        verified_by_id = {item["product_id"]: item for item in verified["verified"]}
        draft_by_id = {item["product_id"]: item for item in copy["drafts"]}
        drafts_store = json.loads((root / "drafts.json").read_text(encoding="utf-8"))
        accepted = []
        for product_id in quality["accepted"]:
            item, proof, draft = by_id.get(product_id), verified_by_id.get(product_id), draft_by_id.get(product_id)
            if not item or not proof or not draft or not proof["evidence"] or draft["status"] != "accepted":
                raise FlowError("DATA_MISMATCH", f"unverified accepted product: {product_id}")
            if not item["name"] or item["genre"] not in config["genre_targets"]:
                raise FlowError("DATA_MISMATCH", f"missing product fields: {product_id}")
            if normalize_url(item["url"]) != normalize_url(proof["url"]) or normalize_url(item["url"]) != normalize_url(draft["url"]):
                raise FlowError("DATA_MISMATCH", f"URL mismatch: {product_id}")
            if not draft["checked_at"]:
                raise FlowError("DATA_MISMATCH", f"draft confirmation missing: {product_id}")
            product_store["products"].append({"id": product_id, "name": item["name"], "url": item["url"],
                "normalized_url": normalize_url(item["url"]), "genre": item["genre"], "status": "eligible",
                "item_code": item["item_code"], "model_candidate": None,
                "checked_at": proof["checked_at"][:10], "posted_at": None})
            drafts_store["drafts"].append({"product_id": product_id, "url": item["url"], "body": draft["body"],
                "status": "accepted", "checked_at": draft["checked_at"]})
            accepted.append(product_id)
        if accepted:
            product_store["next_id"] = max(int(p["id"][1:]) for p in product_store["products"]) + 1
            validate_product_store(product_store)
            validate_document("drafts", drafts_store)
            atomic_write_json(root / "drafts.json", drafts_store)
            atomic_write_json(root / "products.json", product_store)
        target_satisfied = target_reached()
        stop_reason = "target_reached" if target_satisfied else (
            "page_check_limit" if page_request_count >= max_page_checks else
            "triaged_candidates_exhausted")
        result = {"status": "success" if target_satisfied and not quality["required_edits"] else "partial",
                  "accepted_product_ids": accepted, "candidate_count": len(assigned_all),
                  "candidate_pool_count": len(pool), "target_count": target_count,
                  "target_by_genre": target_by_genre, "accepted_by_genre": accepted_by_genre,
                  "target_reached": target_satisfied, "page_check_count": len(all_snapshots),
                  "page_request_count": page_request_count,
                  "batch_size": batch_size, "checked_batches": checked_batches,
                  "triage_selected_count": len(triage["selected"]),
                  "triage_uncertain_count": len(triage["uncertain"]),
                  "triage_excluded_count": len(triage["excluded"]),
                  "fallback_page_request_count": fallback_page_request_count,
                  "qualified_count": qualified_count, "stop_reason": stop_reason,
                  "unresolved_quality_edits": quality["required_edits"]}
        atomic_write_json(run_dir / "outputs/research-result.json", result)
        return result
