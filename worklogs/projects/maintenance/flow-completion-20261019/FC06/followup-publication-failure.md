# FC06 follow-up: publication failure continuation

## FC07 observation and response

FC07 found that `run_daily_report_publication_stage` returned a future `blocked` status for identity mismatch, target mismatch, and failures after pushing but before saving the publication sidecar. The target branch helper's observed reason is `report_target_branch_mismatch`; the test's previous expectation `report_publication_target_mismatch` did not match the actual exception code.

The FC06 publication boundary now returns `unknown` for these non-cancellation failures and retains any valid exception code. When a generic exception has no stable code, it selects an operation-specific reason for remote push, sidecar save, or job finalization. Every failure is appended to the independent failure journal with operation, correlation ID, job/launch/source run/incident/attempt identity, cause chain/stack frames, capture location, input refs, artifact refs, and sink. The workflow provider receives the `unknown` result and writes its substantive report-stage handoff before continuing to terminal reconciliation.

## Exact expected results for FC07 retest

- Changed precheck identity: status `unknown`, reason `report_publication_identity_mismatch`.
- Configured branch does not match remote target: status `unknown`, reason `report_target_branch_mismatch` (preserve helper's actual reason).
- Generic injected `OSError` from report-publication sidecar save after push: status `unknown`, reason `report_publication_sidecar_save_failed`; exception details must appear in failure journal.
- Generic injected push exception without `.code`: status `unknown`, reason `report_publication_push_failed`.
- Exceptions carrying a valid `.code` keep that exact code and still return `unknown`.
- Cancellation continues to return `waiting`; it is not a failure remapped by this change.

## Static checks in this follow-up

`python -m py_compile automation/inago/maintenance/production_workflow.py` passed. Requirements builder v2.50 completed with 28 chapters, 39 figures, 26 references and 4,104,595 HTML bytes. `git diff --check` exited 0 with existing line-ending warnings only. TOML parse for both report roles passed. FC06 did not rerun tests; the assigned FC07 tester is rerunning the affected nodes against this code revision. DB, production, real job and Git operations were not performed.
