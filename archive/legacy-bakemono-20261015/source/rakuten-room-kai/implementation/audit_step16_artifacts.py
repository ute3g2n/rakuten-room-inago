"""Independently verify release path evidence and saved run artifacts."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.atomic_io import atomic_write_json
from automation.schema_validator import validate_document


def main() -> int:
    implementation = ROOT / "rakuten-room-kai/implementation"
    matrix = json.loads((implementation / "full-path-coverage.json").read_text(encoding="utf-8"))
    failures = []
    checked_manifests = set()
    for row in matrix["rows"]:
        if row["status"] != "pass":
            failures.append(f"unpassed path: {row['path']}")
        for evidence in row["evidence"]:
            path = Path(evidence)
            if not path.is_absolute():
                path = ROOT / path
            if not path.is_file():
                failures.append(f"missing evidence: {row['path']} / {evidence}")
            if path.name != "manifest.json" or not path.is_file() or path in checked_manifests:
                continue
            checked_manifests.add(path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            validate_document("manifest", saved)
            events = [json.loads(line) for line in (path.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()]
            if not events or events[0]["type"] != "run_started" or events[-1]["type"] != "run_completed":
                failures.append(f"event boundary mismatch: {saved['run_id']}")
            if saved["finished_at"] is None or saved["status"] == "running":
                failures.append(f"run not closed: {saved['run_id']}")
            for stage in saved["stages"]:
                if stage["status"] != "success":
                    failures.append(f"stage did not pass: {saved['run_id']}/{stage['id']}")
                for artifact in stage["artifacts"]:
                    item = Path(artifact)
                    if not item.is_absolute():
                        item = path.parent / item
                    if not item.is_file():
                        failures.append(f"missing stage artifact: {saved['run_id']}/{artifact}")
    result = {"matrix_rows": len(matrix["rows"]), "checked_manifests": len(checked_manifests),
              "failure_count": len(failures), "failures": failures}
    atomic_write_json(implementation / "artifact-audit-step16.json", result)
    print(json.dumps({key: result[key] for key in ("matrix_rows", "checked_manifests", "failure_count")}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
