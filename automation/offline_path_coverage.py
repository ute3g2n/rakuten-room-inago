"""Build an auditable offline path map from passing JUnit cases and temporary run artifacts."""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from automation.atomic_io import atomic_write_json


GROUPS = {
    "test_daily_sufficient_path_and_sources": ["1", "10"],
    "test_daily_shortage_real_research_chain_partial_and_idempotent": ["2", "3", "5"],
    "test_all_flow_sources_manifest_and_artifacts": ["4", "10"],
    "test_observation_zero_order_match_unmatched_and_unavailable": ["6", "7"],
    "test_resume_and_setup_no_duplicate": ["5"],
    "test_posted_product_never_reenters_daily_plan": ["5"],
    "test_learning_a_b_c_hold_and_single_change": ["8"],
    "test_fake_cli_retry_escalation_and_auth_ban": ["9"],
}


JS_PATHS = {
    "7": ("JSTの暦日で当日を含む30日間の開始時刻を求める",
          "期間内の通常投稿がないページまで進んだときだけ期間境界へ到達と判定する",
          "期間巡回で確認済み0件なら空の最新範囲として保存できる"),
    "6": ("正常取得した注文明細CSVのデータ行なしは確認済み0件として扱う",),
}


def build(junit: Path, temp_root: Path, output: Path, tap: Path | None = None) -> dict:
    cases = ET.parse(junit).findall(".//testcase")
    records = []
    for case in cases:
        name = case.attrib["name"]
        bare = name.split("[")[0]
        if bare not in GROUPS:
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            result = "failed"
        elif case.find("skipped") is not None:
            result = "skipped"
        else:
            result = "passed"
        prefix = bare[:30]
        candidate_dirs = [p for p in temp_root.iterdir() if p.is_dir() and p.name.startswith(prefix)]
        manifests = [p for folder in candidate_dirs for p in folder.rglob("manifest.json")]
        run_ids = []
        artifacts = []
        for path in manifests:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            run_ids.append(manifest["run_id"])
            artifacts.extend(str(path.parent / artifact) for artifact in manifest["artifacts"])
        if not run_ids:
            run_ids = [f"fixture:{name}"]
        for group in GROUPS[bare]:
            records.append({"group": group, "test": name, "result": result,
                            "run_id": sorted(set(run_ids)), "artifacts": sorted(set(artifacts))})
    if tap is not None:
        content = tap.read_text(encoding="utf-8")
        for group, names in JS_PATHS.items():
            for name in names:
                if f"✔ {name}" not in content:
                    raise ValueError(f"Node test result missing: {name}")
                records.append({"group": group, "test": f"node:{name}", "result": "passed",
                                "run_id": [f"fixture:node:{name}"], "artifacts": [str(tap)]})
    output.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "source": "pytest JUnit XML plus temporary run manifests",
                "groups": {str(i): sum(row["group"] == str(i) and row["result"] == "passed"
                                          for row in records) for i in range(1, 11)},
                "paths": records}
    atomic_write_json(output, document)
    if any(not count for count in document["groups"].values()) or any(row["result"] != "passed" for row in records):
        raise ValueError("offline path coverage incomplete")
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tap", type=Path)
    args = parser.parse_args()
    report = build(args.junit, args.temp_root, args.output, args.tap)
    print(json.dumps({"groups": report["groups"], "paths": len(report["paths"])}, ensure_ascii=False))
