# S03/K08 report-stage focused test record

## Scope and expected behavior

- Case: K08, report stage strict-preparation → partial-input fallback → author runner invocation or transport result.
- Expected: the real `run_daily_report_stage` call uses the isolated `MaintenanceStore` SQLite fixture; strict input reconstruction reports `report_source_attempt_unavailable` because no sequence 1–3 artifacts exist; the stage then builds partial report input, calls the author session's `run_turn`, and preserves the synthetic transport-unavailable outcome as a blocked result.
- Selected full node: `tests_inago/test_maintenance_report_publication_stage.py::test_report_stage_falls_back_and_invokes_author_runner`.
- Existing node inspection: this file previously contained only `test_publication_stage_recovers_after_push_before_artifact_save` (three parameters), which calls `run_daily_report_publication_stage` only. It does not cover strict preparation, fallback, or author execution. Per S03 §6/K08 plan language allowing the narrow fixture/test addition when existing tests do not express the path, this isolated full-stage node was added. No product code was changed.

## Environment and version

- Workspace: `C:\project\rakuten-room-inago`; no workspace switch or Git worktree.
- JST: first attempt 2026-10-19 20:28:27–20:28:29; diagnostic rerun 20:28 (full timestamps are in the console log); passing attempt 2026-10-19 20:29:24–20:29:25.
- HEAD: `d7241547d5e6111962177105c9149d6414a2ddee` (unchanged throughout this case).
- Python: 3.12.13; pytest: 9.1.1; SQLite: 3.53.1.
- Test file SHA-256 after final test edit: `AE6128EFE672817ACE6E3B7B3E4D7AA4566B96D925ED889D0FA71426DB62CF7A`.
- Product file `automation/inago/maintenance/production_workflow.py` SHA-256: `EEA1A3F46536FE3F8BEC26EDA0FB245AB717FB163BB397A0C0954C88C1BD848C` (read-only; unchanged).

## Executions

1. Initial targeted run, 2026-10-19 20:28:27–20:28:29 JST:
   - Command: `.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_maintenance_report_publication_stage.py::test_report_stage_falls_back_and_invokes_author_runner -q --junitxml=plans/incidents/2026/10/INC-20261017-001/test-results/S03-K08-run_daily_report_stage.junit.xml`
   - Result: exit 1; collected 1, passed 0, failed 1, errors 0, skipped 0.
   - Observed defect in the new fixture setup: fallback received no safe input/evidence refs, so the real stage returned `blocked/input_refs_invalid` before reaching the author runner. The initial failure is retained as evidence; it was not treated as a product defect.
   - Console SHA-256: `944AC1EF69B490D0B90A0DC5AA070653EE50E025C242DC3EDF9205CEBC9DDEA6`.
   - JUnit SHA-256: `9FE2EAE9CD62069B6C79498144354E3711A5115F6B5CA4719F37D4A0E4F4EB99`.
2. Diagnostic rerun with `--showlocals`, 2026-10-19 20:28 JST:
   - Same complete node; exit 1; collected 1, passed 0, failed 1.
   - Locals confirmed `WorkflowStageResult(status='blocked', reason_code='input_refs_invalid')` and `author_calls=[]`.
   - Console SHA-256: `199403EC855D652A8151A4E09AF2B6A2FD62ADB7254B0110DD58D8D1F643DBBB`.
   - JUnit SHA-256: `45DFF570038961B9C032BC25B09E2B9E8A845E6138B3B943303AA0205348B869`.
3. Corrected fixture rerun, 2026-10-19 20:29:24–20:29:25 JST:
   - Command: `.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_maintenance_report_publication_stage.py::test_report_stage_falls_back_and_invokes_author_runner -q --junitxml=plans/incidents/2026/10/INC-20261017-001/test-results/S03-K08-run_daily_report_stage-attempt2.junit.xml`
   - Result: exit 0; collected 1, passed 1, failed 0, errors 0, skipped 0.
   - Console SHA-256: `BE95D3EFBD114C091262AB73A1ECAFD2CD6FBD6CD68261B942E1F74C72F397A7`.
   - JUnit SHA-256: `773BABF7F7E7D20347FB06EF7C398240D03C039D4323A3483EA03480AD01B201`.

## Observed assertions and limits

- The test calls `run_daily_report_stage` against the actual product implementation and a newly initialized temporary SQLite `MaintenanceStore` fixture.
- The real strict-preparation function fails for missing report-source attempts, then the real partial-input fallback records `workflow.state['report_preparation_observation'] == 'report_source_attempt_unavailable'` and produces a source snapshot bound to the fixture manifest identity.
- The adapter boundary is stubbed in this local test. The test observes `create_session` and `run_turn` on its synthetic author session, which raises `MaintenanceAIBlockedError('synthetic_author_transport_unavailable')`; the session is closed. The real stage returns `blocked/synthetic_author_transport_unavailable`. This proves the author runner call path is reached, not that an external model transport was available or that an HTML report was authored.
- Fixture target date is 2026-10-17, `source_run_id=null`; all persistence is under pytest's temporary directory. No production DB, Daily, Scheduler, queue/history, or 10/19 target was accessed. The tests did not measure production DB row counts because production was not opened.
- The initial and diagnostic failures remain distinguishable from the passing corrected-fixture result by separate console/JUnit files. No failure was reclassified as PASS.
- Final `git diff --check -- tests_inago/test_maintenance_report_publication_stage.py`: clean. Final HEAD remained unchanged. Shared repository status included the pre-existing dirty files; this test task owns only the appended node and evidence listed here.

## Evidence files

- `S03-K08-run_daily_report_stage.console.log`
- `S03-K08-run_daily_report_stage.junit.xml`
- `S03-K08-diagnostic.console.log`
- `S03-K08-diagnostic.junit.xml`
- `S03-K08-run_daily_report_stage-attempt2.console.log`
- `S03-K08-run_daily_report_stage-attempt2.junit.xml`

## Black-formatted final K08 rerun

- Black format applied only to the K08-owned test file `tests_inago/test_maintenance_report_publication_stage.py`; Black 25.12.0, Python 3.12.13. Format command exited 0 at 2026-10-19 20:31:18.594–20:31:19.169 +09:00 and reported one file reformatted.
- Formatted test SHA-256: `627022605882940EDAB7107D9945B23BCD8DFF3BA619839F537BA82ABA9BCCE9`. Product file remained read-only and SHA-256 `EEA1A3F46536FE3F8BEC26EDA0FB245AB717FB163BB397A0C0954C88C1BD848C`.
- Workspace `C:\project\rakuten-room-inago`; HEAD `d7241547d5e6111962177105c9149d6414a2ddee`; Python 3.12.13; pytest 9.1.1; SQLite 3.53.1.
- Exact command: `.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_maintenance_report_publication_stage.py::test_report_stage_falls_back_and_invokes_author_runner -q --junitxml=plans/incidents/2026/10/INC-20261017-001/test-results/S03-K08-black-formatted-final.junit.xml`
- JST: 2026-10-19 20:31:24.643–20:31:25.975 +09:00; exit 0; 1 collected, 1 passed, 0 failed/errors/skips.
- Console: `S03-K08-black-formatted-final.console.log`, SHA-256 `353949A8236178D20EF1829924BAA88A4DBCA28C29768E1D1D47C6E1199D3468`.
- JUnit: `S03-K08-black-formatted-final.junit.xml`, SHA-256 `8B39CCBCF97E7E8E45262258109A9597E238DD9D5686FC690F8F3FEA46A588CD`.
- Post-format `black --check tests_inago/test_maintenance_report_publication_stage.py`: exit 0 at 2026-10-19 20:31:30.863–20:31:31.205 +09:00; output “1 file would be left unchanged.”
- Post-format `git diff --check -- tests_inago/test_maintenance_report_publication_stage.py`: exit 0, no diagnostics.
- Final `git status --short` was checked; shared workspace remains dirty with the pre-existing task files plus this K08-owned test/evidence. HEAD stayed unchanged. Earlier K08 failure, diagnostic, and pass artifacts above were retained.
