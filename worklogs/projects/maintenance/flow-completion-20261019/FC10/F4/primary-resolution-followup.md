# FC10-F4 primary-path-resolution follow-up

Observed at 2026-10-20 02:32:25 +09:00.

## Change

Secondary spool resolution now uses `resolve_maintenance_database_path()` directly and does not depend on resolving the primary journal. Append attempts primary resolution and secondary resolution separately. Recovery resolves and reads the secondary source before attempting the primary source, so a primary reparse/path validation failure remains visible as a source error while valid secondary rows are still reconciled and replayed.

Added `test_failure_journal_primary_resolution_failure_keeps_secondary_replay_available`. It injects a primary resolver `ValueError`, verifies the event is persisted to the independent spool, and replays that event into a temporary SQLite store while retaining the primary-resolution error in the result.

## Evidence

The new regression was first run before implementation and failed as expected: `append_failure_event` returned `unavailable` and emitted the dual-sink-unavailable marker because the secondary path depended on primary path resolution.

After implementation, this exact command passed: `.\\.venv-inago\\Scripts\\python.exe -m pytest tests_inago/test_maintenance_integration.py::test_failure_journal_primary_resolution_failure_keeps_secondary_replay_available tests_inago/test_maintenance_integration.py::test_failure_journal_primary_failure_uses_independent_spool_and_replays_once tests_inago/test_maintenance_integration.py::test_failure_journal_dual_sink_failure_does_not_gate_tracked_operation -q` — exit 0, `3 passed in 0.41s`.

`git diff --check` and `py_compile` for the two assigned files both exited 0.

## Current shared-file hashes

- `automation/inago/maintenance/failure_journal.py`: `e3fe788367153097e9e529a4ec7e848e78784d589c180201feeaf3ea48759857`
- `tests_inago/test_maintenance_integration.py`: `a020bd0266875538a8e212692e29096fab8963bbdd179de52733e76087afbd04`

No production DB or external service was accessed. Other shared-tree edits were preserved.
