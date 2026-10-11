"""Build the release candidate path matrix from verified Step 15 artifacts."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.atomic_io import atomic_write_json

IMPL = ROOT / "rakuten-room-kai/implementation"


def document(relative: str) -> dict:
    return json.loads((IMPL / relative).read_text(encoding="utf-8-sig"))


def manifest(summary_file: str) -> tuple[dict, dict]:
    summary = document(summary_file)
    path = Path(summary["manifest_path"])
    return summary, json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    rows: list[dict] = []

    def add(name: str, passed: bool, evidence: list[str], **details: object) -> None:
        rows.append({"path": name, "status": "pass" if passed else "fail",
                     "evidence": evidence, **details})

    actual = {
        "setup/manual": ("step15-setup.json", "action_required"),
        "performance/manual": ("step15-performance-retry.json", "success"),
        "research/manual": ("step15-research-retry2.json", "partial"),
        "daily/manual": ("step15-daily.json", "partial"),
        "daily/chat": ("step15-chat-daily.json", "partial"),
        "daily/scheduler": ("step15-daily-scheduler-summary.json", "partial"),
        "config-change/manual": ("step15-config-change.json", "success"),
        "recovery/manual": ("step15-recovery.json", "partial"),
        "status/manual": ("step15-status-flow.json", "success"),
    }
    for path_name, (filename, expected_status) in actual.items():
        summary, saved = manifest(filename)
        stages = saved["stages"]
        passed = (summary["status"] == expected_status == saved["status"]
                  and not summary["errors"] and stages
                  and all(stage["status"] == "success" for stage in stages)
                  and summary["target_date"] == "2026-09-25")
        add(path_name, bool(passed), [f"rakuten-room-kai/implementation/{filename}",
                                      summary["manifest_path"]],
            run_id=summary["run_id"], observed_status=summary["status"],
            stage_count=len(stages), source=summary["source"])

    scenarios = json.loads((IMPL / "fixtures/integration/scenarios.json").read_text(encoding="utf-8"))
    test_log = (IMPL / "step15-path-tests.txt").read_text(encoding="utf-8-sig")
    fixture_passed = "25 passed" in test_log
    for flow in scenarios["flows"]:
        for source in scenarios["sources"]:
            add(f"fixture/{flow}/{source}", fixture_passed,
                ["tests_kai/test_step12.py", "rakuten-room-kai/implementation/step15-path-tests.txt"],
                flow=flow, source=source)
    for branch in ("inventory_sufficient", "inventory_shortage"):
        add(f"branch/{branch}", fixture_passed,
            ["tests_kai/test_step12.py", "rakuten-room-kai/implementation/step15-path-tests.txt"])

    routes = document("step15-chat-route-only-result.json")["routes"]
    expected_routes = {"automation/run_daily.py", "automation/run_research.py",
                       "automation/run_performance.py", "automation/show_status.py",
                       "automation/run_recovery.py"}
    add("chat/routing", {item["entry"] for item in routes} == expected_routes
        and all("chat" in item["arguments"] for item in routes),
        ["rakuten-room-kai/implementation/step15-chat-route-only-result.json",
         "rakuten-room-kai/implementation/step15-chat-full-result.txt"], route_count=len(routes))

    model_results = [document("step15-normal-model-result.json"), document("step15-astra-result.json")]
    attempts = []
    for result in model_results:
        saved = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        attempts.extend(saved["model_attempts"])
    for model, effort, schema, escalated in (
        ("gpt-6-luna", "low", "daily-plan", False),
        ("gpt-6-sol", "medium", "quality-gate", False),
        ("gpt-6-astra", "high", "quality-gate", True),
    ):
        matching = [item for item in attempts if item["model"] == model]
        passed = len(matching) == 1 and matching[0]["status"] == "success"
        if passed:
            attempt = matching[0]
            passed = (attempt["reasoning_effort"] == effort and attempt["schema_name"] == schema
                      and attempt["escalated"] == escalated
                      and attempt.get("usage", {}).get("input_tokens", 0) > 0)
        add(f"model/{model}", passed,
            ["rakuten-room-kai/implementation/step15-normal-model-result.json",
             "rakuten-room-kai/implementation/step15-astra-result.json"],
            reasoning_effort=effort, schema=schema, escalated=escalated)

    tasks = document("step15-task-results.json")
    for item in tasks:
        expected_exit = 2 if "DailyCandidate" in item["task"] else 0
        add(f"task/{item['task']}", item["state"] == "Disabled"
            and item["last_task_result"] == expected_exit
            and item["working_directory_matches"] and item["user_matches"]
            and Path(ROOT / item["xml_path"]).is_file(),
            ["rakuten-room-kai/implementation/step15-task-results.json", item["xml_path"]],
            exit_code=item["last_task_result"])

    statuses = document("step15-status-options.json")
    add("status/options", len(statuses) == 5 and all(item["exit_code"] == 0 for item in statuses),
        ["rakuten-room-kai/implementation/step15-status-options.json"], option_count=len(statuses))
    hashes = document("step15-postflight-hashes.json")
    add("dry-run/production-hashes", len(hashes) == 4 and all(item["unchanged"] for item in hashes),
        ["rakuten-room-kai/implementation/preflight-step15-hashes.json",
         "rakuten-room-kai/implementation/step15-postflight-hashes.json"])
    add("start-date/2026-09-25", not (ROOT / "rakuten-room-kai/daily-plans/2026-09-24.json").exists()
        and (Path(manifest("step15-daily.json")[0]["manifest_path"]).parent
             / "sandbox-data/daily-plans/2026-09-25.json").is_file(),
        ["rakuten-room-kai/implementation/step15-daily.json"])
    add("security/secret-scan", "0 finding(s)" in (IMPL / "step15-secret-scan.txt").read_text(encoding="utf-8"),
        ["rakuten-room-kai/implementation/step15-secret-scan.txt"])

    unpassed = [row["path"] for row in rows if row["status"] != "pass"]
    result = {"target_date": "2026-09-25", "row_count": len(rows),
              "unpassed_count": len(unpassed), "unpassed_paths": unpassed, "rows": rows}
    atomic_write_json(IMPL / "full-path-coverage.json", result)
    print(json.dumps({"row_count": len(rows), "unpassed_count": len(unpassed)}, ensure_ascii=False))
    return 0 if not unpassed else 1


if __name__ == "__main__":
    raise SystemExit(main())
