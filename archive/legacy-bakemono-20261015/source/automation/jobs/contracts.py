"""Construct validated, path-based model jobs and check output semantics."""

from dataclasses import dataclass
from pathlib import Path

from automation.atomic_io import atomic_write_json
from automation.model_policy import ModelPolicy
from automation.schema_validator import SCHEMA_DIR, validate_document


@dataclass(frozen=True)
class Contract:
    skill: str
    schema: str
    required: tuple[str, ...]


CONTRACTS = {
    "research_plan": Contract("rr-kai-research-plan", "research-plan", ("inventory_brief", "genre_targets", "winning_conditions", "deprioritized_conditions", "research_requests")),
    "triage": Contract("rr-kai-triage", "triage-output", ("api_candidates", "inventory_brief")),
    "product_verify": Contract("rr-kai-product-verify", "product-verification", ("page_snapshots", "selected_candidates")),
    "copywriter": Contract("rr-kai-copywriter", "copy-output", ("verified_products", "single_change_experiments")),
    "quality_gate": Contract("rr-kai-quality-gate", "quality-gate", ("verified_products", "copy_output", "single_change_experiments")),
    "daily_plan": Contract("rr-kai-daily-plan", "daily-plan", ("inventory_brief", "eligible_products", "genre_targets", "winning_conditions", "single_change_experiments", "daily_plan_constraints")),
    "performance": Contract("rr-kai-performance", "performance-analysis", ("performance_input",)),
    "recovery": Contract("rr-kai-recovery", "recovery-review", ("failed_run_id", "failed_step", "failure_manifest")),
}


def _no_credentials(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(word in key.casefold() for word in ("password", "secret", "cookie", "access_key", "auth_state", "token")):
                raise ValueError("credential-shaped model input")
            _no_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _no_credentials(child)


def prepare_job(project_root: Path, run_dir: Path, job_name: str, run_id: str,
                target_date: str, payload: dict) -> dict:
    if job_name not in CONTRACTS:
        raise ValueError("unknown job")
    contract = CONTRACTS[job_name]
    missing = [field for field in contract.required if field not in payload or payload[field] is None]
    if missing:
        raise ValueError(f"missing job input: {', '.join(missing)}")
    _no_credentials(payload)
    root = Path(project_root).resolve()
    skill = root / ".agents" / "skills" / contract.skill / "SKILL.md"
    schema = SCHEMA_DIR / f"{contract.schema}.schema.json"
    template = root / "automation" / "jobs" / "prompts" / f"{job_name}.md"
    if not skill.is_file() or not schema.is_file() or not template.is_file():
        raise ValueError("job contract file missing")
    policy = ModelPolicy().job(job_name)
    run_dir = Path(run_dir)
    input_path = run_dir / "inputs" / f"{job_name}.json"
    output_path = run_dir / "outputs" / f"{job_name}.json"
    envelope_path = run_dir / "inputs" / f"{job_name}-envelope.json"
    envelope = {"run_id": run_id, "target_date": target_date, "input_path": str(input_path),
                "output_path": str(output_path), "job": job_name, "status": "pending",
                "model": policy["normal_model"], "reasoning_effort": policy["normal_reasoning_effort"], "attempt": 0}
    validate_document("job-envelope", envelope)
    atomic_write_json(input_path, {"run_id": run_id, "target_date": target_date, **payload})
    atomic_write_json(envelope_path, envelope)
    return {"job_name": job_name, "subskill": skill, "schema_name": contract.schema,
            "input_path": input_path, "envelope": envelope_path, "output_path": output_path,
            "template": template}


def check_job_output(job_name: str, result: dict, payload: dict) -> None:
    if job_name not in CONTRACTS:
        raise ValueError("unknown job")
    validate_document(CONTRACTS[job_name].schema, result)
    allowed = {x.get("product_id") or x.get("id") for field in ("api_candidates", "selected_candidates", "verified_products", "eligible_products")
               for x in payload.get(field, []) if isinstance(x, dict)}
    url_by_id = {x.get("product_id") or x.get("id"): x.get("url") for field in
                 ("api_candidates", "selected_candidates", "verified_products", "eligible_products")
                 for x in payload.get(field, []) if isinstance(x, dict) and x.get("url")}
    if job_name == "triage" and len(result["selected"]) > 60:
        raise ValueError("triage exceeds the 60-candidate review pool")
    if job_name == "triage" and len(payload["api_candidates"]) > 60:
        raise ValueError("triage input exceeds the 60-candidate review pool")
    if job_name == "triage":
        ids = [item["product_id"] for field in ("selected", "excluded", "uncertain")
               for item in result[field]]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate appears in more than one triage group")
        candidate_ids = {item.get("product_id") or item.get("id") for item in payload["api_candidates"]}
        if set(ids) != candidate_ids:
            raise ValueError("triage must classify every input candidate exactly once")
    if job_name == "research_plan":
        inventory = payload["inventory_brief"]
        if not isinstance(inventory, dict) or not isinstance(payload["genre_targets"], dict):
            raise ValueError("invalid research inputs")
        counts = inventory.get("genre_counts", {})
        target = payload["genre_targets"]
        needed = {genre for genre, daily_count in target.items() if counts.get(genre, 0) < daily_count * 8}
        searched = {item["genre"] for item in result["search_queries"]}
        if needed - searched:
            raise ValueError("research plan omits a shortage genre")
    if job_name == "product_verify":
        if any(not item["evidence"] for item in result["verified"]):
            raise ValueError("verified product has no page evidence")
        snapshots = payload["page_snapshots"].get("snapshots", []) if isinstance(payload["page_snapshots"], dict) else []
        observed = {item["candidate_id"]: item for item in snapshots if item.get("status") == "observed"}
        for item in result["verified"]:
            page = observed.get(item["product_id"])
            if page is None or page.get("availability") != "in_stock" or not page.get("name") or not page.get("price_yen"):
                raise ValueError("verified product lacks observed sale evidence")
    if job_name == "quality_gate":
        verified_ids = {item["product_id"] for item in payload["verified_products"]}
        if len(result["accepted"]) != len(set(result["accepted"])) or not set(result["accepted"]) <= verified_ids:
            raise ValueError("quality accepted ID is duplicate or unverified")
    if job_name == "daily_plan":
        ids = [p["product_id"] for p in result["posts"]]
        if len(ids) != len(set(ids)) or len(ids) > 15:
            raise ValueError("duplicate or excessive daily posts")
        if len(ids) < 15 and not result["shortfall"]:
            raise ValueError("short daily plan requires reason")
        if set(ids) & set(payload.get("posted_ids", [])):
            raise ValueError("posted product selected")
        if len(ids) == 15:
            expected = payload["genre_targets"]
            counts = {genre: sum(p["genre"] == genre for p in result["posts"]) for genre in expected}
            if counts != expected:
                raise ValueError("daily genre distribution mismatch")
    if job_name == "copywriter":
        for draft in result["drafts"]:
            if any(phrase in draft["body"] for phrase in ("絶対に治る", "必ず痩せる", "No.1", "今すぐ買って")):
                raise ValueError("unsupported or coercive claim")
            if draft["product_id"] in draft["body"]:
                raise ValueError("ID must remain outside copy body")
    if job_name == "performance":
        if result["analysis_date"] != payload["performance_input"]["analysis_date"]:
            raise ValueError("stale performance output")
    if job_name == "recovery" and (result["failed_run_id"] != payload["failed_run_id"] or
                                   result["failed_step"] != payload["failed_step"]):
        raise ValueError("recovery target mismatch")
    if allowed:
        def visit(value: object) -> None:
            if isinstance(value, dict):
                if "product_id" in value and value["product_id"] not in allowed:
                    raise ValueError("output product ID not in input")
                if "product_id" in value and value.get("url") and url_by_id.get(value["product_id"]):
                    if value["url"].rstrip("/") != url_by_id[value["product_id"]].rstrip("/"):
                        raise ValueError("output URL mismatch")
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(result)
