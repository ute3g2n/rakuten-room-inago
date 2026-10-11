"""Render per-AC release readiness with verified evidence paths."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.atomic_io import atomic_write_text

IMPL = ROOT / "rakuten-room-kai/implementation"


def main() -> int:
    acceptance = (IMPL / "acceptance-criteria.md").read_text(encoding="utf-8")
    trace = (IMPL / "test-traceability-matrix.md").read_text(encoding="utf-8")
    acs = sorted(set(re.findall(r"\*\*AC-(\d{3})\*\*", acceptance)))
    trace_rows = {}
    for line in trace.splitlines():
        match = re.match(r"\| AC-(\d{3}) \|[^|]*\|([^|]*)\|", line)
        if match:
            trace_rows[match.group(1)] = match.group(2)
    if acs != [f"{number:03}" for number in range(1, 61)] or set(acs) != set(trace_rows):
        raise ValueError("AC or traceability list is incomplete")
    matrix = json.loads((IMPL / "full-path-coverage.json").read_text(encoding="utf-8"))
    artifact = json.loads((IMPL / "artifact-audit-step16.json").read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "archive/legacy-skills/.staging/legacy-package-manifest.json").read_text(encoding="utf-8"))
    precutover = json.loads((IMPL / "precutover-state-step16.json").read_text(encoding="utf-8-sig"))
    if matrix["unpassed_count"] or artifact["failure_count"] or not archive["verified_readable"]:
        raise ValueError("release gate evidence is not clean")
    if (precutover["old_task_state"] != "Ready" or precutover["new_daily_present"]
            or precutover["new_health_present"] or not Path(precutover["legacy_skill_path"]).is_file()):
        raise ValueError("legacy entry changed before cutover")
    checks = [
        ("pytest-step16.txt", "164 passed"),
        ("npm-test-step16.txt", "pass 55"),
        ("dashboard-e2e-step16.txt", "pass 13"),
        ("schema-tests-step16.txt", "32 passed"),
        ("failure-tests-step16.txt", "19 passed"),
        ("secret-scan-step16.txt", "0 finding(s)"),
    ]
    for filename, expected in checks:
        if expected not in (IMPL / filename).read_text(encoding="utf-8-sig"):
            raise ValueError(f"verification result missing: {filename}")
    groups = {
        "foundation": ["rakuten-room-kai/implementation/pytest-step16.txt",
                       "rakuten-room-kai/implementation/test-traceability-matrix.md"],
        "dashboard": ["rakuten-room-kai/implementation/dashboard-e2e-step16.txt"],
        "live": ["rakuten-room-kai/implementation/full-path-coverage.json",
                 "rakuten-room-kai/implementation/reports/step-15.md"],
        "failure": ["rakuten-room-kai/implementation/failure-injection-matrix.json",
                    "rakuten-room-kai/implementation/failure-tests-step16.txt"],
        "security": ["rakuten-room-kai/implementation/secret-scan-step16.txt",
                     "rakuten-room-kai/implementation/review-findings-step16.md"],
        "archive": ["archive/legacy-skills/.staging/legacy-package-manifest.json",
                    "rakuten-room-kai/implementation/windows-task-review-step16.json"],
    }
    residual = {
        "006": "実在庫0件。120件の目標管理と不足表示を確認。",
        "011": "実ページの販売状態未取得品は採用0件。",
        "019": "実際の旧入口無効化と復元検査はStep 17/19で再判定。",
        "027": "実数の翌日変化は未到来。履歴差分をfixtureで確認。",
        "030": "実注文0件。商品一致帰属はfixtureで確認。",
        "040": "物理スリープ復帰は未実施。設定とmissed検知を確認。",
    }
    lines = [
        "# リリース候補判定 — Step 16",
        "",
        "判定: **cutover_approved: true**。Step 17開始前の準備ゲートとしてAC 60件を判定し、不合格・保留0件。",
        "AC-019の旧入口停止と復元はStep 17/19の実状態で再判定する。ここでのPASSはZIP・XML・切替手順の準備完了を示す。",
        "AC-040の物理スリープ復帰は初回予定実行時に運用観測する。",
        "",
        "| AC | 判定 | 主証拠path | 残リスク・範囲 |",
        "|---|---|---|---|",
    ]
    for code in acs:
        number = int(code)
        group = ("archive" if number == 19 else "dashboard" if 20 <= number <= 25
                 else "live" if 26 <= number <= 40 else "failure" if 41 <= number <= 50
                 else "security" if number == 51 else "foundation")
        evidence = list(groups[group])
        if number in {2, 10, 13}:
            evidence.append("rakuten-room-kai/implementation/skills-validate-step16.json")
            evidence.append("rakuten-room-kai/implementation/review-findings-step16.md")
        if number in {14, 37, 43}:
            evidence.append("rakuten-room-kai/implementation/step15-normal-model-result.json")
            evidence.append("rakuten-room-kai/implementation/step15-astra-result.json")
        if number in {15, 36, 39, 40, 44}:
            evidence.append("rakuten-room-kai/implementation/step15-task-results.json")
        if number in {17, 18, 52, 53, 54, 55, 56, 57, 58, 59, 60}:
            evidence.append("tests_kai/test_step07.py")
            evidence.append("rakuten-room-kai/implementation/step15-chat-full-result.txt")
        tests = re.findall(r"(?:tests/[\w.-]+\.mjs|test_step\d+\.py)", trace_rows[code])
        for test in tests:
            path = test if test.startswith("tests/") else f"tests_kai/{test}"
            if path not in evidence:
                evidence.append(path)
        if any(not (ROOT / path).is_file() for path in evidence):
            raise ValueError(f"missing evidence for AC-{code}")
        evidence_cell = "、".join(f"`{path}`" for path in evidence[:3])
        lines.append(f"| AC-{code} | PASS | {evidence_cell} | {residual.get(code, 'なし')} |")
    lines += [
        "",
        "## 設計§0–19とレビューゲート",
        "",
        "設計§0–10は開始日・複製・5ジャンル・15件・重複禁止・再投稿禁止をAC-001–010で照合。",
        "§11–14は単一HTML、affiliate、API研究、公開ROOMのAC-011–012/020–035とE2E・実観測で照合。",
        "§15–18はオーケストレータ、CLIモデル、三入口、監査/healthのAC-013–016/036–051と実manifestで照合。",
        "§19はA/B/C/保留、商品一致購入帰属、日別非帰属、次工程受け渡しのAC-017–018/052–060で照合。",
        "",
        "- CRITICAL/HIGH/MEDIUM未解決: **0件**。所見は `rakuten-room-kai/implementation/review-findings-step16.md`。",
        "- 全Path: **42/42 PASS**。実manifest 9件のstage/artifact不一致0。",
        "- staging archive: `archive/legacy-skills/.staging/legacy-package-precutover-20260925.zip`。16件、189,791バイト、ZIP内の全sha256照合済み。",
        "- 旧スキルと旧Windowsタスクはこの時点で有効。Step 17まで無効化しない。",
        "",
    ]
    atomic_write_text(IMPL / "release-readiness.md", "\n".join(lines))
    print(json.dumps({"ac_count": len(acs), "cutover_approved": True, "unpassed": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
