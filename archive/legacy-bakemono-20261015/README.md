# Bakemono / Bakemono-Kai archive

This archive contains the legacy Bakemono and Bakemono-Kai assets that were in this repository when the Inago cleanup was performed on 2026-10-15.

## Contents

- `source/` preserves tracked files under their former repository-relative paths. It includes the old skill, Python and Node implementations, test suites, configuration, and implementation notes.
- `local-preserved/` preserves local-only runtime material under its former relative paths. It is intentionally excluded from Git because it can contain account state, operational records, test leftovers, or a Python virtual environment.
- The already archived `archive/legacy-skills/rakuten-room-bakemono-20260924/` was left in place.

The two JavaScript helpers used by Inago metrics were moved to `automation/inago/adapters/room-likes-lib.mjs` and `automation/inago/adapters/affiliate-report-lib.mjs`. `automation/inago/adapters/metrics_live.mjs` imports those active copies. The legacy schemas under `source/rakuten-room-kai/orchestrator/schemas/` remain a read-only input for Inago's explicit legacy-history migration; the old runtime itself is archived.

## Restore

To restore a tracked legacy file, move it from `source/<former repository-relative path>` back to that former path. Keep local-only material inside `local-preserved/` and use it from there where possible. If a specific local-only file must be restored to its former path, the legacy private-data patterns in the repository `.gitignore` keep known runtime data excluded from Git; review `git status` before any staging and never force-add local account or operational data. A restored virtual environment may need to be recreated if its original absolute path has changed. The archive does not activate any old scheduled task or runtime.

The old Bakemono scheduled-task entries found on this machine were already disabled. No Task Scheduler entries were changed as part of this archive.
