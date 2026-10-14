"""Apply validated performance decisions to durable, deduplicated learning ledgers."""

import hashlib
import json
from pathlib import Path

from automation.atomic_io import atomic_write_json, atomic_write_text, append_jsonl_unique
from automation.schema_validator import validate_document


def _key(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def apply_analysis(root: Path, run_id: str, performance_input: dict, analysis: dict) -> dict:
    validate_document("performance-input", performance_input)
    validate_document("performance-analysis", analysis)
    if analysis["analysis_date"] != performance_input["analysis_date"]:
        raise ValueError("analysis date mismatch")
    posts = {p["product_id"]: p for p in performance_input["post_observations"]}
    for decision in analysis["post_decisions"]:
        post = posts.get(decision["product_id"])
        if post is None:
            raise ValueError("unknown post decision ID")
        rating = decision["rating"]
        active_orders = [o for o in post["confirmed_orders"] if o["order_status"].casefold() not in
                         {"cancelled", "canceled", "キャンセル", "取消", "取り消し"}]
        if rating == "A" and (decision["attribution"] not in {"post_confirmed", "post_confirmed_by_order_product_match"}
                              or not active_orders):
            raise ValueError("A requires post-confirmed purchase")
        if rating == "B" and (not post["mature"] or post["likes"] is None or
                              decision["attribution"] != "post_confirmed"):
            raise ValueError("B requires comparable post-level outcome")
        if rating == "C" and (not post["mature"] or post["likes"] != 0 or
                              decision["attribution"] != "post_confirmed"):
            raise ValueError("C requires confirmed post-level no reaction")
    if analysis["deprioritized_conditions"] and not any(d["rating"] == "C" for d in analysis["post_decisions"]):
        raise ValueError("C evidence is required before deprioritization")
    if analysis["winning_conditions"] and not any(d["rating"] == "A" for d in analysis["post_decisions"]):
        raise ValueError("A evidence is required before winning conditions")
    if len({e["variable"] for e in analysis["single_change_experiments"]}) > 1:
        raise ValueError("one changed variable per analysis")
    posted_ids = set(posts)
    for request in analysis["research_requests"]:
        if request["condition"] in posted_ids:
            raise ValueError("cannot research a posted product itself")
    for decision in analysis["post_decisions"]:
        if decision["rating"] == "A":
            condition = next((w["condition"] for w in analysis["winning_conditions"]), None)
            if condition and not any(r["condition"] == condition for r in analysis["research_requests"]):
                raise ValueError("A requires research request for same condition")
    folder = Path(root) / "performance"
    folder.mkdir(parents=True, exist_ok=True)
    ledger_path = folder / "pattern-ledger.json"
    queue_path = folder / "research-queue.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {
        "version": 1, "winning_conditions": [], "single_change_experiments": [],
        "deprioritized_conditions": [], "daily_plan_constraints": []}
    queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {"version": 1, "requests": []}
    signature = _key({"input": performance_input, "analysis": analysis})
    for field in ("winning_conditions", "single_change_experiments", "deprioritized_conditions", "daily_plan_constraints"):
        for item in analysis[field]:
            record = {**item, "source_run_id": run_id, "analysis_date": analysis["analysis_date"], "observation_key": signature}
            if not any(x["observation_key"] == signature and x["condition"] == item.get("condition") and
                       x.get("variable") == item.get("variable") for x in ledger[field]):
                ledger[field].append(record)
    for item in analysis["research_requests"]:
        record = {**item, "requested_count": 3 if any(w["condition"] == item["condition"] for w in analysis["winning_conditions"]) else 1,
                  "exclude_product_ids": sorted(posted_ids), "source_run_id": run_id,
                  "analysis_date": analysis["analysis_date"], "observation_key": signature}
        if not any(x["observation_key"] == signature and x["condition"] == item["condition"] for x in queue["requests"]):
            queue["requests"].append(record)
    atomic_write_json(ledger_path, ledger)
    atomic_write_json(queue_path, queue)
    atomic_write_json(folder / "latest.json", {"run_id": run_id, "observation_key": signature, "analysis": analysis})
    append_jsonl_unique(folder / "observations.jsonl", {"observation_key": signature, "run_id": run_id,
                                                      "analysis_date": analysis["analysis_date"], "analysis": analysis}, "observation_key")
    counts = {rating: sum(d["rating"] == rating for d in analysis["post_decisions"]) for rating in ("A", "B", "C", "hold")}
    atomic_write_text(folder / "status.txt", f"{analysis['analysis_date']} A {counts['A']} / B {counts['B']} / C {counts['C']} / 保留 {counts['hold']}\n")
    return {"observation_key": signature, "counts": counts, "pattern_ledger": ledger, "research_queue": queue}
