# SC08 invocation attempt 1 — preflight failure before any role call

- Fixed existing job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`; source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`; target date `2026-10-17`; CLI omitted `--source-run-id`, so it remained null.
- Exact operation: `.venv-inago/Scripts/python.exe -m automation.inago.maintenance.cli skill-chain --job-id job-historical-9e7f0c175cc4c874c955a5dab29c4427 --source-launch-id historical-9e7f0c175cc4c874c955a5dab29c4427 --target-date 2026-10-17`.
- Observed stdout: `{"reason_code":"skill_chain_incident_identity_unreadable","status":"not_performed"}`; integer exit 1. Exact dispatch timestamp was not emitted by the CLI/session wrapper; failure was examined at 2026-10-20 14:44:09 JST.
- Incident input readback: runtime job `incident.md`, SHA-256 `b6d5e5d0470096614b3bcae3418df57110d7f41dc9700be0b8ab173a870779b5`.
- Cause: the existing run-null line is `- 日次run: `null` のまま。既存値を推測で埋めない。`. Runner's regular expression required end-of-line immediately after `のまま。`; it rejected the additional explanation. This is a parser/fixture contract defect, not evidence about the Oct 17 Daily failure.
- Readback after the failed attempt: `calls.jsonl` was absent, no role/session was started, no job artifact or persistent state was created, and no Daily, worker, maintenance DB, 10/19 history or 10/20 processing was invoked. `source_run_id` remains null.
- Corrective work: the synthetic fixture now uses the actual explanatory-line shape; runner parser accepts that suffix without changing the captured run ID. F6 plan-scoped focused tests and separate SC06 product review are in progress. The code has not yet been reapplied; do not rerun this CLI until F6, review and SC07 follow-up apply/readback are recorded.

## Next action

Continue with F6 verification on the corrected code, request independent SC06 review against the final hash, commit/push that fix, apply/read back the new deployed commit, then perform a single corrected skill-chain invocation. The first role invocation must be chain-started from the corrected, reviewed, deployed version.