# FC10 follow-up result: terminal successful-output gap and report evidence refs

## Identity and current state

- Job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- Source launch: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- Target date: `2026-10-17`
- `source_run_id`: `null`
- Same-job resume14 did not complete. The integrated reporter and independent reviewer ran; reviewer returned seven change requests. Reporter revision failed because evidence references contained Markdown labels and multiple links. The publicly read-back HTML is a controller fallback with no accepted review and is not the final report.

## Implemented correction

- `automation/inago/maintenance/workflow_reconciliation.py`: terminal reconciliation accepts persisted outputs for matching `complete` and `handoff_accepted` attempts only after successful expected-SHA readback. If a matching successful attempt has a missing or unreadable/hash-invalid output, it records a cause-bearing missing-output gap and does not substitute a stage failure handoff.
- `automation/inago/maintenance/production_workflow.py`: normal and reviewer-driven author prompts require exactly one raw allowed path string in each evidence reference. The renderer remains responsible for the readable label.
- `tests_inago/test_maintenance_report_publication_stage.py`: tests cover accepted handoff output, corrupted successful output plus a valid failure handoff, strict non-success handoffs, publication crash recovery, and exact report reference prompt.
- The FC04 operation-owner map records the exact source hunk and scope extension before the correction.

## Verification and review

- Regression RED: `handoff-waiver-red.console.txt` and `handoff-waiver-red.junit.xml`; the test failed because the terminal incorrectly completed despite the tampered successful output and a valid failure handoff.
- First GREEN attempts exposed stale synthetic-test assumptions that treated missing output from a status=`complete` attempt as a legitimate failure handoff. The exact failures were preserved in `fc10-round5`, `fc10-round6`, and `fc10-round7` console/JUnit evidence. The fixture now marks these attempts as failed explicitly; the dedicated new case retains the invalid-success-output check.
- Latest focused verification: `fc10-round8-focused-green.console.txt` and `.junit.xml`, 5 passed in 15.18s.
- Independent product review r2: `terminal-handoff-and-report-evidence-path-review-r2.json/.md`; changes requested, one High finding. Its correction is in the current source. The review scope did not include `reporting.py`, so that file's allowlist and renderer internals remain outside this review result.
- Fresh r3 source-hash-bound independent review target: `terminal-handoff-and-report-evidence-path-review-target-r3.json`, SHA-256 `210adf60357834b8c4e12503b00c1578833d9331b3dd3ed4d3c7c32174149963`; approved with 0 findings for the permitted production-source scope; the reporting.py renderer/allowlist internals remain explicitly outside review scope.

## Resume position

Do not claim FC-A acceptance. After fresh product review approval, run final source/evidence/secret and ownership checks, commit and push only owned files to `main`/`origin/main`, apply through the official fixed-root scheduler switcher and read it back, then resume this same job/source launch only. Verify the actual author correction, independent report approval, public report readback, terminal database state, source_run null, and 10/19 queue invariants before proceeding to FC11 completion audit.
