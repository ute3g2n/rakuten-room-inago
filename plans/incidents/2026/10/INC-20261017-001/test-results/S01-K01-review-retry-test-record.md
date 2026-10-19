# S01 focused test record

- Job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427` (source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`, `source_run_id=null`).
- Base HEAD before and after: `d7241547d5e6111962177105c9149d6414a2ddee`.
- Environment: Python 3.12.13, SQLite 3.53.1; pytest version not reported by the runner.
- Product file SHA-256: `7BB78C32DF35F2C86D170BD281D44CC381E70D2BA547262C858A11A5059078EC`; Git blob `48f1f6385b5b896db4fbc17a3370d50a2db0e6d3`.
- Test file SHA-256: `4A26988C4D397A8A7A1645A02EA60149EA8E30528C7FEDC15D98FB5B85D8287F`; Git blob `d9b31f6e227cb4a3b9853d9271526b446c7d5f08`.

## Five focused nodes

Command, PowerShell in the fixed workspace:

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_maintenance_store.py::test_c07_contract tests_inago/test_maintenance_store.py::test_c07_later_step_can_start_with_missing_predecessor_sequence tests_inago/test_maintenance_store.py::test_c07_job_finalization_releases_active_slot 'tests_inago/test_maintenance_store.py::test_c53_step_review_rounds_continue_across_retries[changes_requested]' 'tests_inago/test_maintenance_store.py::test_c53_step_review_rounds_continue_across_retries[rejected]' -q
```

Exact nodes:

- `tests_inago/test_maintenance_store.py::test_c07_contract`
- `tests_inago/test_maintenance_store.py::test_c07_later_step_can_start_with_missing_predecessor_sequence`
- `tests_inago/test_maintenance_store.py::test_c07_job_finalization_releases_active_slot`
- `tests_inago/test_maintenance_store.py::test_c53_step_review_rounds_continue_across_retries[changes_requested]`
- `tests_inago/test_maintenance_store.py::test_c53_step_review_rounds_continue_across_retries[rejected]`

Started 2026-10-19 20:01:38.644 JST, ended 20:01:40.401 JST. Exit 0; 5 passed in 1.14s; captured output SHA-256 `9DE677659B69A3583058FB395D2BAC8902D1584DDC0AC005E4ED1749FDE88FCB`; output file `K01-K53-s01-after-fix-focused.txt`.

## Focused test-file run

```powershell
.\.venv-inago\Scripts\python.exe -m pytest tests_inago/test_maintenance_store.py -q --junitxml plans/incidents/2026/10/INC-20261017-001/test-results/K01-K53-s01-after-fix.xml
```

Started 2026-10-19 20:01:45.644 JST, ended 20:01:49.578 JST. Exit 0; 15 passed, 0 failed, 0 errors, 0 skipped; pytest-reported 3.27s, wall 3.934s. Console output SHA-256 `DD19F6CF7AF594205779FBF1CC3B86E50C486324E4734E23F6DE299E42D3021A`; JUnit SHA-256 `553E4CB1629DD95AE10484FDCCE4A749259EE8568BEA89EA724EABF820B215F3`. Evidence files: `K01-K53-s01-after-fix-file.txt` and `K01-K53-s01-after-fix.xml`.

The runner reported both HEAD observations as the base commit above. No production database, scheduler, Daily task, or 10/19 queue was changed. The focused command used existing `test_c53_*` function names; `K01` is the incident-plan case ID, and no separate incident-plan `K53` case is defined.
