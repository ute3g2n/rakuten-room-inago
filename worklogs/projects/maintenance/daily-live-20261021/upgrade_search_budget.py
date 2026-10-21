"""One-time, locked upgrade through the repository's migration API."""

import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[4]
if ROOT != Path("C:/project/rakuten-room-inago").resolve():
    raise RuntimeError("unexpected project root")
sys.path.insert(0, str(ROOT))

from automation.inago.production_runtime import (
    _open_existing_production_database,
    load_production_config,
)
from automation.inago.repository import initialize_database, schema_version
from automation.inago.run_lock import (
    LockOwner,
    RunLock,
    _write_owner_atomically,
    observe_process_owner,
    observe_process_tree,
    read_lock_owner,
)


def snapshot(connection):
    result = {}
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migration' ORDER BY name"
    ):
        quoted = name.replace('"', '""')
        rows = sorted(
            [tuple(row) for row in connection.execute(f'SELECT * FROM "{quoted}"')],
            key=repr,
        )
        result[name] = {
            "count": len(rows),
            "sha256": hashlib.sha256(
                json.dumps(rows, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        }
    return result


runtime = ROOT / "rakuten-room-inago/runtime"
backup_root = (
    ROOT
    / "rakuten-room-inago/backups/daily-final-20261021-c2b91a59d015461bb8e9269effa180d7"
)
manifest = json.loads((backup_root / "manifest.json").read_text(encoding="utf-8"))
if manifest["schema_version"] != 6:
    raise RuntimeError("expected verified schema-six backup")
backup = sqlite3.connect((backup_root / "state.sqlite").as_uri() + "?mode=ro", uri=True)
try:
    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("backup integrity check failed")
    backup_state = snapshot(backup)
finally:
    backup.close()
config = load_production_config(runtime / "config/settings.json", data_root=runtime)
original_owner = LockOwner.from_dict(
    json.loads(
        (Path(__file__).parent / "stopped-owner.json").read_text(encoding="utf-8")
    )
)
if original_owner is None or original_owner.run_id != "INAGO-2026-10-21":
    raise RuntimeError("stopped daily owner evidence is invalid")
with RunLock.acquire(
    runtime / ".daily-run.lock",
    runtime / ".daily-run-owner.json",
    run_id="SCHEMA-UPGRADE-2026-10-21",
) as guard:
    connection = _open_existing_production_database(config)
    try:
        if schema_version(connection) != 6:
            raise RuntimeError("upgrade expects schema six; refuse to rerun")
        before = snapshot(connection)
        for table in (
            "run",
            "search_attempt",
            "source_post",
            "candidate_decision",
            "draft",
        ):
            if before[table] != backup_state[table]:
                raise RuntimeError("current results differ from pre-upgrade backup")
        version = initialize_database(connection)
        after = snapshot(connection)
        if before != after:
            raise RuntimeError("migration changed saved records")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if version != 7 or integrity != "ok" or foreign_keys:
            raise RuntimeError("post-upgrade validation failed")
        daily = connection.execute(
            "SELECT status,committed_count FROM run WHERE run_id=?",
            (original_owner.run_id,),
        ).fetchone()
        process = observe_process_owner(original_owner)
        children = observe_process_tree(original_owner.pid)
        if (
            tuple(daily or ()) != ("running", 10)
            or process.status != "dead"
            or children.status != "observed"
            or children.child_pids
            or read_lock_owner(guard.owner_path).owner != guard.owner
        ):
            raise RuntimeError(
                "stopped owner cannot be restored with verified evidence"
            )
        # Restore the exact observed metadata while holding the same byte lock.
        # release() removes only its own owner, so this stopped owner survives.
        _write_owner_atomically(guard.owner_path, original_owner)
        result = {
            "status": "ready",
            "from_schema": 6,
            "to_schema": version,
            "records_unchanged": True,
            "integrity_check": integrity,
            "foreign_key_errors": foreign_keys,
            "stopped_owner_restored": True,
            "tables": after,
        }
    finally:
        connection.close()
print(json.dumps(result, ensure_ascii=False))
