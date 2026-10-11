"""Archive only the disabled legacy skill after the Step 17 cutover."""

import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.atomic_io import atomic_write_json


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".agents/skills"
ARCHIVE_ROOT = ROOT / "archive/legacy-skills"
PARTS = ("SKILL.md.disabled", "assets", "references", "scripts", "templates", "webapp")
JST = timezone(timedelta(hours=9))


def within_workspace(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError(f"path outside workspace: {path}")
    return resolved


def file_records(base: Path) -> list[dict]:
    records = []
    for part in PARTS:
        target = base / part
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = sorted(path for path in target.rglob("*") if path.is_file())
        else:
            raise FileNotFoundError(target)
        for path in files:
            archive_path = path.relative_to(base).as_posix()
            original_path = "SKILL.md" if archive_path == "SKILL.md.disabled" else archive_path
            records.append({"path": original_path, "archive_path": archive_path,
                            "size_bytes": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return sorted(records, key=lambda record: record["path"])


def main() -> None:
    ledger = json.loads((ROOT / "rakuten-room-kai/implementation/execution-ledger.json").read_text(encoding="utf-8"))
    if ledger["steps"]["17"]["status"] != "success":
        raise ValueError("Step 17 is incomplete")
    cutover = json.loads((ROOT / "rakuten-room-kai/implementation/cutover-state.json").read_text(encoding="utf-8-sig"))
    baseline = json.loads((ROOT / "rakuten-room-kai/implementation/baselines/legacy-skill-files.json").read_text(encoding="utf-8"))
    staging_zip = ARCHIVE_ROOT / ".staging/legacy-package-precutover-20260925.zip"
    if not staging_zip.is_file():
        raise FileNotFoundError(staging_zip)
    expected = {entry["path"]: entry["size_bytes"] for entry in baseline["files"]}
    before = file_records(SKILLS)
    actual = {entry["path"]: entry["size_bytes"] for entry in before}
    if actual != expected or len(before) != baseline["file_count"] or sum(actual.values()) != baseline["total_size_bytes"]:
        raise ValueError("legacy source differs from Step 00 baseline")
    old_skill = next(entry for entry in before if entry["path"] == "SKILL.md")
    if old_skill["size_bytes"] != cutover["old_skill_size_bytes"] or old_skill["sha256"].lower() != cutover["old_skill_sha256"].lower():
        raise ValueError("disabled skill differs from Step 17 record")

    stamp = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
    package = ARCHIVE_ROOT / "rakuten-room-bakemono-20260924"
    if package.exists():
        package = ARCHIVE_ROOT / f"rakuten-room-bakemono-20260924-{stamp}"
    source = package / "source"
    for path in [SKILLS, ARCHIVE_ROOT, package, source, staging_zip]:
        within_workspace(path)
    if source.exists():
        raise FileExistsError(source)
    package.mkdir(parents=True)
    source.mkdir()
    moved: list[tuple[Path, Path]] = []
    archive_zip = package / staging_zip.name
    try:
        for part in PARTS:
            src = within_workspace(SKILLS / part)
            dst = within_workspace(source / part)
            shutil.move(str(src), str(dst))
            moved.append((src, dst))
        after = file_records(source)
        if after != before:
            raise ValueError("archived files differ from original files")
        archive_zip = within_workspace(archive_zip)
        zip_created = datetime.fromtimestamp(staging_zip.stat().st_ctime, JST).isoformat(timespec="seconds")
        shutil.move(str(staging_zip), str(archive_zip))
        result = {
            "archived_at": datetime.now(JST).isoformat(timespec="seconds"),
            "source_path": str(source), "file_count": len(after),
            "total_size_bytes": sum(entry["size_bytes"] for entry in after), "files": after,
            "staging_zip": {"path": str(archive_zip), "size_bytes": archive_zip.stat().st_size,
                            "created_at": zip_created,
                            "sha256": hashlib.sha256(archive_zip.read_bytes()).hexdigest()},
        }
        atomic_write_json(package / "archive-manifest.json", result)
        atomic_write_json(ROOT / "rakuten-room-kai/implementation/archive-state-step18.json",
                          {"package": str(package), "manifest": str(package / "archive-manifest.json"),
                           "file_count": len(after), "total_size_bytes": result["total_size_bytes"]})
        print(json.dumps({"package": str(package), "file_count": len(after),
                          "total_size_bytes": result["total_size_bytes"]}, ensure_ascii=False))
    except Exception:
        if archive_zip.exists() and not staging_zip.exists():
            shutil.move(str(archive_zip), str(staging_zip))
        for src, dst in reversed(moved):
            if dst.exists() and not src.exists():
                shutil.move(str(dst), str(src))
        raise


if __name__ == "__main__":
    main()
