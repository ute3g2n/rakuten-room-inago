# FC10 terminal status reconciliation fix

- Recorded: 2026-10-20 JST
- Job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- Source launch: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- Target date: `2026-10-17`; `source_run_id=null`
- Workspace: `C:\project\rakuten-room-inago`, branch `main`
- Base: `e17cf65f2fcca5c14ad28eb51b033b8ad996bd9d` (`origin/main`)

## Resume 20 outcome and cause

Resume 20 ran the stages and published the live report r13, but terminal reconciliation left the job `failed` with `workflow_terminal_observations_incomplete`. The terminal JSON is `runtime/workflow-terminal/job-historical-9e7f0c175cc4c874c955a5dab29c4427/workflow-b15b811f26054614af3d91cb2ce25a35.json`; it records `report_publication_readback_reason=report_result_identity_or_status_mismatch` and `workflow_completion=incomplete`.

The report result had `stage_status=reviewed`, `review.status=approved`, and `report_status=pending`. `pending` was correct because source investigation, test, and deployment evidence remained incomplete; `approved` meant the reviewer approved the HTML wording. The terminal readback incorrectly required those independent facts to be coupled. The report HTML had already been published at `reports/incidents/2026-10-17/job-39e268f67f036cc9225e.html`, SHA-256 `2644b0118ac4d147c89b5de72ab9911b33b1f42b253f9f36593e2859f962b650`, by publication commit `e17cf65f2fcca5c14ad28eb51b033b8ad996bd9d`.

Read-only SQLite verification returned `PRAGMA quick_check=ok`, resume count 20, job status `failed`, target date `2026-10-17`, exact source launch, and null source run. The terminal document showed all stage handoff references and observations present; only published report readback was false. Planning transport failed, repair was not actionable, and deployment had no candidate; these remain recorded facts and do not establish the original incident cause.

## Change

- `workflow_reconciliation._published_report_readback` accepts reviewed reports by their review disposition independently of source report completeness. For unavailable review, it requires the exact controller-fallback / pending / publish / unavailable identity and binding.
- `production_workflow._persist_minimal_daily_report` keeps fallback source status `pending`, even when its wording review is approved. It does not promote incomplete evidence to complete.
- The report rule, incident-report skill and contracts, reporter/reviewer roles, acceptance contract, requirements dictionary and trace table now describe these independent status axes.
- A terminal integration regression case reproduces resume reconciliation after publication with `report_status=pending` and `review.status=approved`, while checking that the incident remains `in_progress`. A negative case rejects an unavailable-review record that does not use the exact controller fallback binding.

## Verification

- TDD red before the fix: the reproduced same-job reconciliation ended incomplete with `report_result_identity_or_status_mismatch`.
- Focused regression tests after the fix: 3 passed, including same-job publication-crash recovery, pending+approved terminal completion, and invalid unavailable-review rejection.
- Review-revision cases: 4 passed, including changes-requested then approved, multiple findings then approved, current-turn mismatch recovery, and transport failure.
- Full focused modules: `.venv-inago\Scripts\python.exe -B -m pytest tests_inago/test_maintenance_report.py tests_inago/test_maintenance_report_publication_stage.py -q` — **61 passed in 87.09 seconds**.
- Independent product review first requested a stricter unavailable-review binding. That finding was applied. Follow-up review approved the final two product files with no findings: `workflow_reconciliation.py` SHA-256 `9a2653dfcbc88c71b02e8bb1b1439cec075fabe7805ca57eecb4262caedb58d0`; `production_workflow.py` SHA-256 `9956d1a464ccbd5d62720daa86b37a8a6a7742089e18cdb2b425058d22d9c29c`. The reviewer did not read or run tests.
- Python compilation, role TOML parsing, requirements builder v2.56 (28 chapters / 39 figures / 26 references), and `git diff --check` passed. The diff check reported only existing CRLF/LF normalization warnings.

The code and synchronized documents are not yet committed, pushed, or applied to the fixed-root runtime. The same job remains failed at resume 20. Next: finish ownership/diff/secret audit, commit and push only task-owned changes to `origin/main`, apply with the official `TaskSchedulerRuntimeSwitcher`, confirm runtime health and code identity, and resume only this job and source launch at resume 21. Then run only the explicitly scoped historical Worker and verify publication, remote readback, and correct same-job terminal completion. Overall acceptance is not yet met.
