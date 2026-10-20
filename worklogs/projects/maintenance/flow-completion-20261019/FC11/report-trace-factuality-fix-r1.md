# FC11: report trace factuality correction

## Result

The resume-25 report had been published and its reviewer had approved the exact HTML bytes, but it did not clearly state which trace/report integration was added in this change and overstated what fields were populated in the observed records. This left FC-A06 unmet. The exact same job must be resumed again after this correction; the prior publication is retained as history.

The report author now describes the added same-run trace-to-report integration, distinguishes fields the trace can store from values present in the observed run, and includes the actual run summary. The fallback report also distinguishes a successful read with no matching records from a read failure. The report template and the user's narrow HTML review scope are unchanged.

## Changes and verification

- Product files changed: `automation/inago/maintenance/reporting.py` and `automation/inago/maintenance/production_workflow.py`.
- Test files changed: `tests_inago/test_maintenance_report.py` and `tests_inago/test_maintenance_report_publication_stage.py`.
- `python -m pytest` using the global Python failed because that interpreter has no pytest package; the `pytest` launcher also lacked the repository package root. These attempts made no product changes.
- The first focused run with `.venv-inago` and `PYTHONPATH=.` found 1 stale assertion expecting the previous connective wording. The test expectation was updated to the new accurate wording.
- Final focused checks: `tests_inago/test_maintenance_report.py` **20 passed**; publication fallback and narrow reviewer-prompt tests **2 passed**; `py_compile` passed; `git diff --check` exit 0 (line-ending warning only).
- Independent product review by `/root/fc11_html_scope_product_review_r2`: **APPROVE, 0 findings**. Reviewed SHA-256: `reporting.py` `5d4da9cf78af2ac33c07ce05c0f92cd1f328d5469324be7f737c7c8b3771a4fa`; `production_workflow.py` `423f6da15874674b2cd873ba7983a9a5826d0048b884c3ec7c36df0381da06d2`. Reviewer checked recorded-field versus populated-value wording, exception-cause wording, not-found versus unavailable behavior in normal and fallback reports, unchanged template, and unchanged narrow scope. Reviewer did not run tests or edit files.

## Current production and restart position

The prior same-job resume-25 worker run terminated `maintenance_complete`, and its report was published at commit `aebdb86a7d93325c040c1e4cac0057dbe985db2b`. Its exact reviewed/published HTML bytes were SHA-256 `06794328757cd5efb2d543b79c201121b5413afca6547c6434440cebd7ed485d`. That HTML does not satisfy FC-A06 because it omitted the new trace/report integration fact; it is not the final acceptance artifact. The same job identity remains `job-historical-9e7f0c175cc4c874c955a5dab29c4427`, launch `historical-9e7f0c175cc4c874c955a5dab29c4427`, target `2026-10-17`, `source_run_id=null`.

At this checkpoint local `main` is at `a491ba8e7d693939fa3a977137b9a72f44a430e5`, tracking `origin/main`, and behind by the report-publication commit above. The correction is not committed or applied. Next: verify the remote fast-forward without switching workspace, record final ownership and secret scans, commit/push only owned changes, use the official fixed-root runtime switcher and readback, then resume only this same job and explicitly scoped historical Worker. Confirm fresh report content, independent approval, publication exact-byte readback, terminal registration, and unchanged 10/19 queue before accepting FC-A06/A07 or overall completion.
