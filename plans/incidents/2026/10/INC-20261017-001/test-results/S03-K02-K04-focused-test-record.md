# S03 focused test record: K02–K04

- Incident/job input: incident `352492da-ac58-4680-b1fd-fe5f24000229`, job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`, launch `historical-9e7f0c175cc4c874c955a5dab29c4427`, `source_run_id=null`, target date `2026-10-17`. Tests use isolated local fixtures only; no Daily, production DB, Scheduler, queue/history, or 10/19 exclusion setting was touched.
- Fixed workspace: `C:\project\rakuten-room-inago`.
- HEAD for the recorded run: `d7241547d5e6111962177105c9149d6414a2ddee`.
- Environment: Python 3.12.13, SQLite 3.53.1, pytest 9.1.1.
- Product file SHA-256 at run: `orchestrator.py` `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7`; `daily_pipeline.py` `48CC59099CF8E4B20424B4C096F6E7DCFE0E04B7576303A085CEF632F622D251`; `trace.py` `743D1F23ADEE411302886F9549FDFBD9F237E89FDED57396FE189B707DF178DD`.
- Test files at run: `test_daily_fix_trace.py` SHA-256 `90107AA5F08AD5FB1E3C9F55F72C7B495A36DEAAF37FC41AF77D7553F915A198`; `test_maintenance_completion.py` `1A83FE13F1A8694938DD13AAE2FF386A70435828C64901F611866E6678C5FA33`; `test_daily_fix_outcome.py` `BAB1D4942575C8A4E9FD4F2899C753CFE1CB991F51BA89A0E403C1F406565C51`.
- The local `test_n04` node was augmented because its previous assertions tested sensitive exception text but did not cover the newly introduced safe diagnostic fields or proposed/persisted run distinction. It now writes a synthetic trace event to its `tmp_path` TraceWriter and checks safe labels/codes, `proposed_run_id` with `persisted_run_id=null` and `run_persisted=false`, `trace_requested`, code hash, plus absence of the existing secret markers. No test independent review was performed.

## Requested node name discrepancy

The plan/requested name `tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_with_nullable_new_deadlines` does not exist in the checked-in file. The matching existing complete node is `tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines`. This actual node was run. The nonexistent spelling was included once in the initial invocation, which pytest rejected during node resolution before executing tests (`no tests ran`, exit 4). Its console output is retained at `S03-K02-K04-focused-console.txt`; no JUnit was produced by that attempt.

## Focused run

Command in PowerShell, fixed workspace:

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_fix_trace.py::test_n04 tests_inago/test_maintenance_completion.py::test_c09_contract tests_inago/test_daily_fix_trace.py::test_n03 tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-K02-K04-focused-attempt2.xml"
```

- Started `2026-10-19 20:11:38.694 +09:00`; finished `2026-10-19 20:11:45.342 +09:00`.
- Actual complete nodes: `test_daily_fix_trace.py::test_n04`, `test_maintenance_completion.py::test_c09_contract`, `test_daily_fix_trace.py::test_n03`, and `test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines`.
- Result: exit 1; 3 passed, 1 failed, 0 errors, 0 skips (pytest elapsed 5.91s). K03 `test_c09_contract`, K04 `test_n03`, and the existing K04 nullable-deadline node passed. K02 `test_n04` failed in the new assertion at `failure_function="_create_run"`: `trace.make_trace_event` raised `TraceContractError: failure_function is outside the safe label format`.
- Finding passed to the S02 owner/controller: production `_run_create_diagnostic` supplies `_create_run`, which the current safe-label regex rejects. The diagnostic writer catches this and leaves the trace unavailable rather than persisting the new event. This is a product-code finding, not a fixture defect. The independent S02 owner is correcting the label contract; after that change the same K02 node must be rerun and appended here. Current K02 status is FAIL pending that focused rerun, not PASS.
- Captured output: `S03-K02-K04-focused-attempt2-console.txt`; JUnit: `S03-K02-K04-focused-attempt2.xml` (4 cases, 3 passed, 1 failed). JUnit timestamp `2026-10-19T20:11:39.309577+09:00`.
- Initial/final fixture row totals were not separately measured; the nodes use isolated `tmp_path` fixtures and clean them after each test. Production database, Daily, Scheduler, and queue counts were intentionally not queried or modified. Therefore production before/after counts are unobserved, not zero.
- The first invalid-node attempt is preserved separately. It executed zero tests and must not be counted as verification evidence.

## K02 rerun after safe-label fix

After the initial failure was returned to the S02 owner, the `trace.py` safe-label contract was changed to permit the leading underscore required by `_create_run`. The same K02 complete node was rerun; this was a new test attempt against the then-current dirty implementation.

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_fix_trace.py::test_n04 -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-K02-n04-after-label-fix.xml"
```

- Started `2026-10-19 20:12:29.967 +09:00`; finished `2026-10-19 20:12:30.990 +09:00`.
- HEAD remained `d7241547d5e6111962177105c9149d6414a2ddee`; Python 3.12.13, SQLite 3.53.1, pytest 9.1.1.
- `trace.py` SHA-256 at rerun: `AB93BAF117FD2CE605CC2F6D3CCE9A828906CB38880499F0FD5E61B69EB88FA5`; test file remained `90107AA5F08AD5FB1E3C9F55F72C7B495A36DEAAF37FC41AF77D7553F915A198`.
- Result: exit 1; 0 passed, 1 failed. The safe-label assertion passed; the next assertion failed with `KeyError: 'persisted_run_id'` because `make_trace_event` omits `None`-valued optional run-id fields instead of recording explicit JSON null as K02 expects. Output: `S03-K02-n04-after-label-fix-console.txt`; JUnit: `S03-K02-n04-after-label-fix.xml`.
- This is recorded as an additional product-contract finding and passed to the S02 owner. The K02 test currently constructs an event through `make_trace_event`; that schema-only assertion does not yet exercise `_run_create_diagnostic` / `_create_run`. An isolated SQLite integration assertion will be added to the existing `test_n04` and rerun after the product null-field correction, so no end-to-end emitter result is claimed from this attempt.

## K02 isolated emitter integration

The assertion was then upgraded within the same existing complete node to invoke `_create_run` with an isolated initialized SQLite database. The fixture seeds a run row for a different date with the proposed ID, then requests a run for `2026-10-16` using that same ID. This triggers SQLite's primary-key `IntegrityError` after transaction begin, exercises the real `_run_create_diagnostic` rollback/error path, and leaves no run row for the requested date. A separate `TraceWriter` uses the proposed ID as its writer ID, matching the product emitter contract.

- A first integration invocation failed with `NameError: repository is not defined` (exit 1); the test-local import was corrected. Its output is `S03-K02-n04-emitter-integration-console.txt`.
- The next invocation failed with `StopIteration` (exit 1) because the test passed its existing N04 trace writer (`RUN-N04-TRACE`) to an emitter event whose `run_id` was `RUN-N04-PROPOSED`; the fixture was corrected to use a dedicated matching writer. Output: `S03-K02-n04-emitter-integration-attempt2-console.txt` and `.xml`.
- The following invocation found a mismatch in the test expectation: the actual injected collision was a primary-key violation, not a unique-index violation. It reported `SQLITE_CONSTRAINT_PRIMARYKEY` (extended code 1555); the expectation was corrected to `constraint_label=primary_key`. Output: `S03-K02-n04-emitter-integration-attempt3-console.txt` and `.xml`.
- The next invocation observed structured-log `trace_status=written`, while the assertion assumed `appended`; the test was changed to accept either valid writer outcome while still requiring `trace_persisted=true`. Output: `S03-K02-n04-emitter-integration-final-console.txt` and `.xml`.
- Final focused command:

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_fix_trace.py::test_n04 -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-K02-n04-emitter-integration-verified.xml"
```

- Started `2026-10-19 20:15:10.890 +09:00`; finished `2026-10-19 20:15:11.974 +09:00`; HEAD `d7241547d5e6111962177105c9149d6414a2ddee`; Python 3.12.13, SQLite 3.53.1, pytest 9.1.1; exit 0, 1 passed in 0.44s.
- Product hashes for this final K02 run: `orchestrator.py` `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7`; `daily_pipeline.py` `48CC59099CF8E4B20424B4C096F6E7DCFE0E04B7576303A085CEF632F622D251`; `trace.py` `8B6743C6ED975D6C581E8F917EA5E4F41CDF89983F061A38E445D2F8513D786F`. Updated test file SHA-256 `718D4ABED77A5E9FF5178DD884EE3C1D6AF3C22A12613D866B9BBC565FFFB087`.
- The emitted JSONL event was read from the dedicated fixture writer and verified to contain `processing_label=run_create`, `transaction_phase=rollback`, `proposed_run_id=RUN-N04-PROPOSED`, explicit `persisted_run_id=null`, `run_persisted=false`, `trace_requested=true`, `error_name=SQLITE_CONSTRAINT_PRIMARYKEY`, `constraint_label=primary_key`, safe exception/module/function fields, and a 64-character code hash. `caplog` confirmed `trace_persisted=true` and actual `trace_status=written`. No secret markers were present in either fixture trace file.
- Isolated DB count after the failure: requested target-date run rows `0`; colliding prior-date `RUN-N04-PROPOSED` rows `1`. The test DB was created under `tmp_path` and removed at fixture teardown. No production row counts were queried; they remain unobserved.

## Evidence hashes

- Final test file `tests_inago/test_daily_fix_trace.py`: SHA-256 `718D4ABED77A5E9FF5178DD884EE3C1D6AF3C22A12613D866B9BBC565FFFB087`.
- This record SHA-256 is reported by the tester to the controller at handoff.
- K03/K04 combined JUnit: `S03-K02-K04-focused-attempt2.xml`, SHA-256 `E157AAEB498E5EF6DB2D3A6B23D58CE6F58759938C28DCC35C0E6816E8864ECE`; console `S03-K02-K04-focused-attempt2-console.txt`, SHA-256 `0895E2F097785EF1C1F037DF665BB03B1B23A90FF8B28D46F59F30FBA645637F`.
- Final K02 JUnit: `S03-K02-n04-emitter-integration-verified.xml`, SHA-256 `EB5E1FB1C0684E181FFD75FBB5B6A4D2FD72FC09E26851F3E77216904F325B24`; console `S03-K02-n04-emitter-integration-verified-console.txt`, SHA-256 `9B02778CB61C4D7EF3CAACF0EC35DDBAAFD17D7639685D1455F4C565ADFD0C88`.
