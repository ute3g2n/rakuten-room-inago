# S03 focused retest after R01/R02

実行場所は固定workspace `C:\project\rakuten-room-inago`。すべて通常の `.venv-inago` pytest、隔離fixtureで実行した。製品コード、テストfixture、DB、Daily、Scheduler、queue/history、10/19対象は操作・変更していない。

- HEAD（全実行時）: `d7241547d5e6111962177105c9149d6414a2ddee`
- 環境: Python 3.12.13、SQLite 3.53.1、pytest 9.1.1
- 実行前 `git status --short`:

```text
 M automation/inago/daily_pipeline.py
 M automation/inago/maintenance/store.py
 M automation/inago/orchestrator.py
 M automation/inago/trace.py
 M tests_inago/test_daily_fix_trace.py
 M tests_inago/test_maintenance_store.py
?? plans/incidents/2026/10/INC-20261017-001/
?? tests_inago/test_daily_pipeline_safe_diagnostics.py
```

## 新規 daily-pipeline safe diagnostics 2 node

実行対象node:

- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_diagnostic_logger_failures_do_not_change_daily_result`
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location`

Command:

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_diagnostic_logger_failures_do_not_change_daily_result tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-R01R02-safe-diagnostics.xml"
```

- JST: 2026-10-19 20:19:08.732–20:19:09.714
- Exit 1; 0 passed, 2 failed, 0 errors, 0 skipped (pytest elapsed 0.32 s).
- Both nodes failed before exercising their intended assertions: `run_daily_execution_core()` raised `TypeError: unexpected keyword argument 'target_date'`. Its current signature is `(ports, *, clock)`. No fixture or implementation was changed; this is recorded as a test failure for the owner to address.
- Tested file hashes: `daily_pipeline.py` SHA-256 `82161CD1A2C1A9BBDFF5DA6701BAF6B60189BB4EE55712BEF1CD922EDA137C72`; new test file SHA-256 `95336ADF5558D686D58EEE9856B6F498578F4EB6BB4BAE0F34F780F2912358AD`.
- Console: `S03-R01R02-safe-diagnostics-console.txt`, SHA-256 `1FD518CB2CF31992946BE285420FE1B6D45CCD3796DB1A5B3F483833616A58BA`.
- JUnit: `S03-R01R02-safe-diagnostics.xml`, SHA-256 `7073663F76F7236A94A8E70A7611A36938A82485AB311E4F2701362AB4721FCE`.

## Existing K02–K04 nodes

Exact nodes:

- K02: `tests_inago/test_daily_fix_trace.py::test_n04`
- K03: `tests_inago/test_maintenance_completion.py::test_c09_contract`
- K04: `tests_inago/test_daily_fix_trace.py::test_n03`
- K04 nullable deadline: `tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines`

The plan/requested spelling `test_n20f_daily_started_with_nullable_new_deadlines` does not exist; the complete actual node above was run.

Command:

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_fix_trace.py::test_n04 tests_inago/test_maintenance_completion.py::test_c09_contract tests_inago/test_daily_fix_trace.py::test_n03 tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-R01R02-K02-K04.xml"
```

- JST: 2026-10-19 20:19:20.893–20:19:27.439
- Exit 0; 4 passed, 0 failed/errors/skips (pytest elapsed 5.82 s).
- `test_n04` exercised an isolated SQLite primary-key failure and validated the emitted diagnostic fields; `test_c09_contract`, `test_n03`, and the nullable-deadline case also passed.
- Product hashes: `orchestrator.py` SHA-256 `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7`; `daily_pipeline.py` `82161CD1A2C1A9BBDFF5DA6701BAF6B60189BB4EE55712BEF1CD922EDA137C72`; `trace.py` `8B6743C6ED975D6C581E8F917EA5E4F41CDF89983F061A38E445D2F8513D786F`. Test hashes: `test_daily_fix_trace.py` `718D4ABED77A5E9FF5178DD884EE3C1D6AF3C22A12613D866B9BBC565FFFB087`; `test_maintenance_completion.py` `1A83FE13F1A8694938DD13AAE2FF386A70435828C64901F611866E6678C5FA33`; `test_daily_fix_outcome.py` `BAB1D4942575C8A4E9FD4F2899C753CFE1CB991F51BA89A0E403C1F406565C51`.
- Console: `S03-R01R02-K02-K04-console.txt`, SHA-256 `E1D50FF067C6BFDA6510D6D78F49E0A11A04CA13A88CD8F185940C71D2A56983`.
- JUnit: `S03-R01R02-K02-K04.xml`, SHA-256 `9C7E8CB92CF8BC8343D11E7C14C935D215B579513343D543589C7CCB90D5AA92`.

## Narrow related pipeline regression

Since the new tests target `run_daily_execution_core`'s failure and cleanup behavior, the directly related existing regression `tests_inago/test_daily_fix_pipeline.py::test_n22c_monitor_start_failure_survives_successful_stop_status` was also run (no broad suite).

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_fix_pipeline.py::test_n22c_monitor_start_failure_survives_successful_stop_status -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-R01R02-daily-fix-pipeline-n22c.xml"
```

- JST: 2026-10-19 20:19:34.087–20:19:35.054; exit 0; 1 passed in 0.30 s.
- Python/SQLite/pytest and HEAD as above. `daily_pipeline.py` SHA-256 `82161CD1A2C1A9BBDFF5DA6701BAF6B60189BB4EE55712BEF1CD922EDA137C72`; `test_daily_fix_pipeline.py` `5B370E2A08C48CB37432A5EB3911994A580071100B727CCF9D2200F20BD63B69`.
- Console: `S03-R01R02-daily-fix-pipeline-n22c-console.txt`, SHA-256 `188C7D2C78E3347BD0649F49B266C0CD289FD68F9CFE0221F5EA1989C86953FF`.
- JUnit: `S03-R01R02-daily-fix-pipeline-n22c.xml`, SHA-256 `6CDF3BB267C7446BA869B284DD96993DAB5E4B0EB4CD034D8BB1288CA5ACB062`.

All test databases used by the executed tests are temporary/in-memory fixtures. Production DB row counts and 10/19 queue/history state were not queried; they remain unobserved in this test pass.

## Follow-up after correcting the test call and final trace hash capture

The earlier 2-node invocation and its `TypeError` result above are retained as the first observed test version. The owner removed only the unsupported `target_date` keyword from the two test calls. The product then gained a small change to compute `failure_code_sha256` once and reuse it in the logger and JSONL.

Command:

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_pipeline_safe_diagnostics.py -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-R01R02-safe-diagnostics-final.xml"
```

- JST: 2026-10-19 20:20:29.636–20:20:30.744
- HEAD: `d7241547d5e6111962177105c9149d6414a2ddee`; Python 3.12.13, SQLite 3.53.1, pytest 9.1.1.
- Exit 0; 2 passed, 0 failed/errors/skipped (pytest elapsed 0.43 s). The two exact node names are listed in the preceding section.
- Final tested SHA-256: `orchestrator.py` `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7`; `daily_pipeline.py` `B145C3DE5D8A97CFF9EE9825DCF191376423E5929FAD325E568B90E657D07DA6`; `trace.py` `8B6743C6ED975D6C581E8F917EA5E4F41CDF89983F061A38E445D2F8513D786F`; `test_daily_pipeline_safe_diagnostics.py` `EE257FE62DB58D2717FE049C3F168887E91413B0A9DF3F5530EDADCF731AF132`.
- R01 `test_pipeline_diagnostic_logger_failures_do_not_change_daily_result`: both synthetic diagnostic log calls were made to raise `OSError`. The assertion observed `daily_status=failed` and `rolled_back == [True]`, where the failure callback recorded `not connection.in_transaction`; a separate succeeding call still returned `daily_status=complete`. The observed rollback assertion is transaction-state based; the test did not count deleted table rows after rollback.
- R02 `test_pipeline_failure_records_its_own_capture_location`: the captured logger record and persisted phase-error JSONL both asserted module `automation.inago.daily_pipeline`, function `run_daily_execution_core`, equal failure line, and the same SHA-256 recomputed from the current source bytes. Both locations point to `_pipeline_capture_location()` at that source line. These assertions passed.
- Console: `S03-R01R02-safe-diagnostics-final-console.txt`, SHA-256 `8A78A30B3F0A7FC7C2E1781DF7E6448A8D043429921E1CBFD4526EF993B8E206`.
- JUnit: `S03-R01R02-safe-diagnostics-final.xml`, SHA-256 `04407AF43A3168657773A9D83C2F56133F8DC7E2D003EAE7CA0AFF5D474DAC47`.
- The R01/R02 follow-up touched no production DB, Daily, Scheduler, queue/history, or 10/19 data. Final `git status --short` was checked after recording; it showed the same dirty paths as at the start (the existing incident directory remains untracked and contains these records/evidence).

## Latest six-node final-version run

This is the latest evidence for the R01/R02 diagnostics and K02–K04 group. It supersedes neither nor deletes the preceding attempts; earlier TypeError and 3-node PASS results remain above as historical observations. This final command executed all six requested nodes together against the final reviewed `daily_pipeline.py` hash and the post-whitespace `test_daily_fix_trace.py` version.

Complete nodes:

- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_diagnostic_logger_failures_do_not_change_daily_result`
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location`
- `tests_inago/test_daily_fix_trace.py::test_n04`
- `tests_inago/test_maintenance_completion.py::test_c09_contract`
- `tests_inago/test_daily_fix_trace.py::test_n03`
- `tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines`

Command:

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_diagnostic_logger_failures_do_not_change_daily_result tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location tests_inago/test_daily_fix_trace.py::test_n04 tests_inago/test_maintenance_completion.py::test_c09_contract tests_inago/test_daily_fix_trace.py::test_n03 tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines -q --tb=short --junitxml "plans\incidents\2026\10\INC-20261017-001\test-results\S03-R01R02-final-six-nodes.xml"
```

- JST: 2026-10-19 20:24:24.516–20:24:31.667
- Python 3.12.13; SQLite 3.53.1; pytest 9.1.1; HEAD `d7241547d5e6111962177105c9149d6414a2ddee`.
- Exit 0; 6 passed, 0 failed/errors/skips; pytest elapsed 6.36 s.
- Final tested product SHA-256: `orchestrator.py` `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7`; `daily_pipeline.py` `AEAB818F9C50F98ABB1B603578BB6DD9CFF0BBD96B3E728DA31F4A15BECB290E`; `trace.py` `8B6743C6ED975D6C581E8F917EA5E4F41CDF89983F061A38E445D2F8513D786F`.
- Final tested test SHA-256: `test_daily_pipeline_safe_diagnostics.py` `EE257FE62DB58D2717FE049C3F168887E91413B0A9DF3F5530EDADCF731AF132`; `test_daily_fix_trace.py` `743FF1C4FE1F675DA5E7C03F39A16E02E2BB0E3D2F7F07CD328E7F8F44DE00BE`; `test_maintenance_completion.py` `1A83FE13F1A8694938DD13AAE2FF386A70435828C64901F611866E6678C5FA33`; `test_daily_fix_outcome.py` `BAB1D4942575C8A4E9FD4F2899C753CFE1CB991F51BA89A0E403C1F406565C51`.
- R01 assertions: synthetic diagnostic log writes raise `OSError`, yet `daily_status` is `failed` and the failure callback observes `not connection.in_transaction`; a separate success invocation remains complete. The test does not assert post-rollback row count.
- R02 assertions: diagnostic logger and JSONL share the same module, function, failure line, and source SHA-256; the failure line resolves to `_pipeline_capture_location()` and the stored code hash matches the source.
- Console: `S03-R01R02-final-six-nodes-console.txt`, SHA-256 `464D4F8CA0E37D7F8435878612C9265E9CEEC10CA8006F975FE38C106C258595`.
- JUnit: `S03-R01R02-final-six-nodes.xml`, SHA-256 `3FD8866D35038207213BA62A677DEA8455D88603F208B50A1B930974549D92BF`.
- This six-node run is the latest result; the earlier three-node pass is retained only as a distinct intermediate run. No production database, Daily, Scheduler, queue/history, or 10/19 data was read or changed.

## Post-K08 final six-node focused retest (current workspace version)

This run was performed after the K08 pass at 20:29 JST. It is the latest six-node evidence; the earlier six-node run logged at 20:24 JST above remains historical and is not reused for these hashes.

- JST: 2026-10-19 20:30:10.461–20:30:17.425 +09:00.
- Workspace: `C:\project\rakuten-room-inago`; HEAD `d7241547d5e6111962177105c9149d6414a2ddee`; Python 3.12.13; pytest 9.1.1; SQLite 3.53.1.
- Exact command:
  `.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_diagnostic_logger_failures_do_not_change_daily_result tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location tests_inago/test_daily_fix_trace.py::test_n04 tests_inago/test_maintenance_completion.py::test_c09_contract tests_inago/test_daily_fix_trace.py::test_n03 tests_inago/test_daily_fix_outcome.py::test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines -q --tb=short --junitxml=plans/incidents/2026/10/INC-20261017-001/test-results/S03-R01R02-final-six-nodes-after-K08.xml`
- Exit 0; 6 collected, 6 passed, 0 failed, 0 errors, 0 skipped; pytest elapsed 6.26 s.
- Current code hashes (SHA-256): `orchestrator.py` `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7`; `daily_pipeline.py` `D010BE9965577358686EB9574AE11C59CFE85A273E9951316F1B83B7DAB7D202`; `trace.py` `92A2C9D7F4395839E1590539EA5ADC57A48B267F5669FF19BC492ACC112DCAB9`; `production_workflow.py` `EEA1A3F46536FE3F8BEC26EDA0FB245AB717FB163BB397A0C0954C88C1BD848C`; `diagnostic_trace.py` `20CEFF649A8F3C4BA05979883BEFDB31F3F7DBDEE5F11A95EF82F69405295C66`; `workflow_provider.py` `F10A2B6B20819BB55798C68DA4532B9F4BBB1443AB49E232938202499F6AB68A`.
- Current test hashes (SHA-256): `test_daily_pipeline_safe_diagnostics.py` `B9BFD7C601A410EAF24E4CD1CBB2FDAF68DC690002A385FF48FFA9DBCDF43859`; `test_daily_fix_trace.py` `FF594EB86F41B99E94FB090B9CD286FA2A9E22D8A645909EE57FEBA9DD4BD418`; `test_maintenance_completion.py` `1A83FE13F1A8694938DD13AAE2FF386A70435828C64901F611866E6678C5FA33`; `test_daily_fix_outcome.py` `BAB1D4942575C8A4E9FD4F2899C753CFE1CB991F51BA89A0E403C1F406565C51`.
- K08 test file current SHA-256: `AE6128EFE672817ACE6E3B7B3E4D7AA4566B96D925ED889D0FA71426DB62CF7A`.
- Console: `S03-R01R02-final-six-nodes-after-K08.console.txt`, SHA-256 `BF40F341F95A97F6681E101B3EE1E9B57CFDB27108461585A5611057FA3F344D`.
- JUnit: `S03-R01R02-final-six-nodes-after-K08.xml`, SHA-256 `738AFF09251C487F2DDFE7F7F589ADDC74C3773361158C7463488C142D06D7AA`.
- Observed safe-diagnostic assertions in this tested version: injected diagnostic logger sink failures do not change the failed daily result; the failure callback observes the SQLite transaction already rolled back; a separate normal invocation remains complete. The source capture assertion verifies logger/JSONL module, function, line, and source hash match the live capture helper. There is no assertion of post-rollback row count.
- These tests use synthetic callbacks and in-memory/temp fixtures; they did not call production Daily, Scheduler, queue/history, or the production database. The 10/19 timestamp used in the isolated fake-clock test remains fixture-only.
