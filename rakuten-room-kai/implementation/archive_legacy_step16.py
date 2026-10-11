"""Create and verify a read-only pre-cutover archive of the legacy package."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.atomic_io import atomic_write_json


def main() -> int:
    baseline_path = ROOT / "rakuten-room-kai/implementation/baselines/legacy-skill-files.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    source = ROOT / ".agents/skills"
    staging = ROOT / "archive/legacy-skills/.staging"
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / "legacy-package-precutover-20260925.zip"
    expected = []
    for item in baseline["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid baseline path")
        path = source / relative
        if not path.is_file() or path.stat().st_size != item["size_bytes"]:
            raise ValueError(f"legacy file changed or missing: {relative}")
        raw = path.read_bytes()
        expected.append({"path": relative.as_posix(), "size_bytes": len(raw),
                         "sha256": hashlib.sha256(raw).hexdigest()})
    if len(expected) != baseline["file_count"] or sum(x["size_bytes"] for x in expected) != baseline["total_size_bytes"]:
        raise ValueError("legacy baseline totals changed")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for item in expected:
            zipped.write(source / item["path"], item["path"])
    with zipfile.ZipFile(archive) as zipped:
        listed = sorted(zipped.namelist())
        if listed != sorted(item["path"] for item in expected):
            raise ValueError("archive file list mismatch")
        for item in expected:
            data = zipped.read(item["path"])
            if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"archive verification failed: {item['path']}")
    report = {"archive_path": str(archive), "source_path": str(source),
              "file_count": len(expected), "total_size_bytes": sum(item["size_bytes"] for item in expected),
              "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "verified_readable": True, "files": expected}
    atomic_write_json(staging / "legacy-package-manifest.json", report)
    print(json.dumps({key: report[key] for key in
                      ("archive_path", "file_count", "total_size_bytes", "verified_readable")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
