# SC04 failure and resume implementation record (pre-test)

## Same-job failure handling

The SC03 runner at `automation/inago/maintenance/skill_chain.py` records each role's requested and observed model/effort/tier, safe failure code/class, bounded cause classes/codes, repository-relative traceback frames, response byte count/SHA-256, input/output path hashes and whether the assigned agent wrote fresh outputs. It does not persist raw response or exception text. AppServer's runner-only `best_effort_logging` keeps a log-only OSError from interrupting a successful turn, reports a generic safe stderr event, and exposes a flag that the independent same-job call ledger records.

Malformed handoffs receive one format-only turn in the same session. If the retry fails, a controller fallback is labeled as such and later fixed roles still launch. That fallback is not counted as an agent output or as evidence the predecessor was read. `calls.jsonl` has a separate `calls-fallback.jsonl` sink; if both fail the runner still attempts all roles and terminal inspection remains incomplete because successful call records are absent.

At a later process start, an incomplete same-job chain is read from its saved calls and Markdown. The first incomplete role is selected; its outputs and all downstream outputs are archived to a new history directory before fresh calls. Completed predecessor files remain and are passed directly to the resumed role. A fully verified chain is read idempotently. The fixed existing job directory must already exist; the runner never makes a new runtime job directory. No maintenance stage, planning pin, approval, receipt, lease, or resume count is consulted.

The author update and independent reviewer reread now occur after role 7 and before role 8. Terminal verification checks the author's final HTML hash in `report.md`, the separate reviewer's exact final hash/status/round marker, role 8's input hashes, the local publication bytes, and published/tracking-remote hash markers.

## Legacy reference audit

- The CLI worker branch calls `build_skill_chain_workflow_provider`; this branch does not import `MaintenanceController`, `production_workflow`, `workflow_provider`, planning, repair, or reporting code.
- `historical.py` now uses a `TYPE_CHECKING` controller import.
- The new worker adapter reads the skill-chain Markdown/HTML terminal. The `workflow_reconciliation` lazy import remains only under the separate legacy terminal-adapter branch.
- The old `production_workflow.py`, `workflow_provider.py`, `ai_adapter.py`, stage/historical tools, planning, repair and reporting modules are retained. Read-only reference search shows active consumers in the legacy CLI, probe path, controller, reconciliation and existing tests; removing them would break those entrypoints and history tooling. The implementation removes them from the new worker route rather than deleting still-referenced modules.
- `WORKER_DISPATCH_EXCLUDED_DATES` remains exactly `frozenset({"2026-10-19"})`. This work did not read/write the 10/19 queue/history or start Daily.

## Mechanical check and limits

- Python syntax compile for `app_server.py`, `cli.py`, `worker.py`, `historical.py`, `skill_chain.py`: exit 0.
- `git diff --check`: exit 0.
- Tests and synthetic failure injection have not run; do not mark SCT03/04/05/11/14 PASS from these checks.
- Code hash at this checkpoint: `automation/inago/maintenance/skill_chain.py` SHA-256 `399dbc49fdd19decf1b8029ce7ef784d5d6dc0b9b9c3d1204cbf20e09e3b881c`.

## Handoff

- Next: SC05 focused synthetic tests using temporary job directories, SQLite and local bare Git only.
- Inputs: this record, `SC03/production-entry-call-map.md`, `SC03/md-handoff-example.md`, the plan's SCT01–14 parameters, current five-file code version and `incident.md`.
- Request: exercise each new runner/failure boundary without rerunning any old FC01–FC11 node and without changing production/historical DB or 10/19 records.

## SC05 follow-up — corrected target version

The pre-test code hash above (`399dbc49...`) is superseded for acceptance purposes. SC05 found that role 8 receives repository-relative paths in `input_refs`, while terminal validation compared them using only a basename. That caused a complete synthetic chain to return `skill_chain_publication_inputs_stale`. The predicate was corrected to normalize and compare the same repository-relative path plus final SHA-256. The affected integration case and the full new focused file passed after the fix.

- Corrected `automation/inago/maintenance/skill_chain.py` SHA-256: `75e8815002d425c93ee4ea3cb541a000fb356ab8e122142a1bff26b557a58e89`.
- Test file SHA-256: `5816b5ce38c3f51bd7b161207aa47f45bf549e00a6218c6f095657a38a77664d`; final run: 10 passed, exit 0; JUnit has 0 failures/errors/skips. Evidence: `SC05/test.md`, `SC05/test.log`, `SC05/junit.xml`, `SC05/node-list.json`.
- The test agent's standalone SC05 report was briefly placed at the assigned runtime `test.md` path. It was preserved byte-for-byte at `rakuten-room-inago/runtime/maintenance-flow/job-historical-9e7f0c175cc4c874c955a5dab29c4427/history/sc05-prechain/test.md` and remains in `SC05/test.md`; the assigned runtime `test.md` path is now absent so no synthetic test report can be mistaken for the live role-3 result.
- SCT06 remains NOT_RUN pending the real same-job role-2 daily instrumentation diff. SCT07 is in independent SC06 review; SCT09 remains an SC08/09 operational check. SC05 mapping and all other limits are in its `test.md`/`node-list.json`.

## Final failure/resume review fixes (2026-10-20 14:33 JST)

The complete pre-test/F3 notes above remain as history. Current runner hash is `5b82597fa9f2621796397b4bc05ee52b0020259751a294769fd26362ac535447`; focused test hash is `ac90004ce13a594d9fbac23a49220973e20bc7ac811f2ae32cd5aa08c2303d14`. Final F4 test evidence is `SC05/F4-followup/`, with 25/25 PASS, and final independent product review is `SC06/follow-up-review-v3.md` (approved, no findings; SHA-256 `eb76e00460a80f39b17cf24ec6a1c36ec05ec370d8cd9d5bd8299c4b6a2633a6`).

- The event-backed read check rejects a missing, failed, or different-path AppServer `Get-Content` result even if the model repeats the prior hash.
- Role 6 gets a fresh same-role recovery session on failed/invalid/empty report output; failures do not stop roles 7/8. After author correction, `author_revision_finished` records both final `report.md` and `report.html` hashes. Terminal and resume compare both current files to those exact refs; the final rereview input hash for `report.md` is checked against current bytes.
- After successful role 6/7 replacement events, `_resume_start_role` no longer treats their legitimate replacement of initial role outputs as corruption. The SC05 F3 regression confirms a public-byte-only mismatch reuses the final report/review and resumes at role 8. A `report.md` post-revision mutation, even with a still-matching HTML marker, resumes at role 6.
- These synthetic resume cases are not the live job. SC08/09 still must observe the same existing 10/17 job and its actual role calls. `source_run_id` stays null; Daily, worker and 10/19 history are not used by the test or direct-chain route.

## Final SC05/SC06 verification (2026-10-20 14:37 JST)

The F4 failure/resume corrections are retained in final runner SHA-256 `3e8e3f34cd87739ef2ef57f88bc76f0fcd1fc93fa51a4fcea50ccd79e10d9e12`; final test source SHA-256 is `6c2d480d29e33d19c57dacd2251a5df20bd01dbbe3d47073769ff7d89951328f`. The F5 rerun against that exact code/test pair completed 25/25 selected nodes, exit 0, with zero failures/errors/skips. Its artifact directory is `SC05/F5-followup/`.

Independent reviewer v4 approved the final runner with no new findings (`SC06/follow-up-review-v4.md`, SHA-256 `3eccc4f42453abf63dbe6bb3bd4841e73bc8338704aaf1a43ffb29cc4ffff4ab`). F5 synthetic resume coverage is not evidence of the live same-job resume; proceed to SC07/08/09.
## F6 identity-line parser repair (2026-10-20)

The pre-role SC08 command failed with `skill_chain_incident_identity_unreadable`; diagnosis and no-side-effect readback are in `SC08/invocation-attempt-1.md`. The actual incident line contains explanatory text after `のまま。`. The corrected runner `fcc23783b14f626c97d32c1f804c9dad5f0f95f1a39a8db873aca5c2b2b3976a` now captures the run ID while allowing that suffix, and the test fixture matches the actual incident text (`bceb82fcfaa2c0d408f71f3c4451c173c486828319b60c59048e0527edfbba1b`).

SC05 F6 reran 21 focused maintenance-chain nodes and the four mapped SCT04/SCT08 nodes against these exact hashes: 25 passed, 0 failed/errors/skipped, exit 0. Exact command, 21 collected nodes, timestamps, JUnit and normalized console evidence are in `SC05/F6-followup/`; original path-bearing artifacts and their normalized repository hashes are recorded in `SC05/privacy-redaction-record.md`.

This verification does not change the live incident or create a role call. The review v5 and production apply/readback for the repaired runner must precede the actual same-job eight-role invocation.
## F6 final identity parser review — 2026-10-20

The explanatory source_run_id suffix fix is runner `fcc23783b14f626c97d32c1f804c9dad5f0f95f1a39a8db873aca5c2b2b3976a` with test source `bceb82fcfaa2c0d408f71f3c4451c173c486828319b60c59048e0527edfbba1b`. SC05 F6 selected 25 plan-scoped nodes: 25 passed, zero failed/error/skipped, exit 0. Separate SC06 review v5 approved this exact runner hash with no finding (`SC06/follow-up-review-v5.md`, SHA-256 `8b2b3109b6e906c5cc7331c5722f687d0508943a68d99a720524ba6faf063ff5`). The reviewer verified that suffix text is ignored while the backtick capture must still equal `null`.

Next: record the corrected code unit in Git and push, reapply/read back its commit in SC07, then start the actual same-job 8-role chain once. The first attempt remains a pre-role input-parser failure and is not counted as an agent call.