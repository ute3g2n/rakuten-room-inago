# C29 third-node focused result: raw Trends hits with zero KEY selections on resume

- Observed: 2026-10-23. Isolated SQLite fixture and `SAFE_FIXTURE_BASE` only; the production database was not opened or changed by this pytest node.
- HEAD at run: `1dcd54ec8025abd2e0105acb41118d929c8e94b0` (`main`).
- Test source SHA-256: `64d6b3e01c1373f0a7d6b30a0379790898ec59f07225ec7fa0d299b00f115acb`.
- Product source SHA-256: `automation/inago/daily_theme_planner.py` `3f4753da3cbc9f004a412e48a4ba9e2874b386db30d4cebca8a69d02fc1ef4c1`; `automation/inago/orchestrator.py` `7358e2c824216de5245ddac94d2d008b8b8e3b01657c9ed9ae24bc429b8c1255`.

## Exact command and node

```powershell
.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_theme_query_components.py::test_resume_removes_legacy_parent_fallback_when_raw_hits_have_no_key_selection
```

Complete node: `tests_inago/test_theme_query_components.py::test_resume_removes_legacy_parent_fallback_when_raw_hits_have_no_key_selection`.

## Expected and observed

- Fixture: Trends raw related snapshot is non-empty; KEY returns `query_same` for every raw term, so the selected `plan.related` is empty. A legacy checkpoint is seeded with a pending parent-theme fallback task and an existing committed draft.
- Expected: resume removes the stale fallback plan/task without searching; `task_cursor`, processed attempts, handled committed offer, committed count, and draft count remain intact.
- Actual: **1 failed, 0 passed, 0.83s, exit 1**. Fixture validation reached the intended behavior assertion, but the current product code searched the stale fallback (`searched == []` failed). Assertions after that first failure, including state-preservation assertions, were not observed in this run.
- This is a focused RED against unchanged product source. Do not treat later assertions as tested until the node is rerun after the product fix.
- Earlier temporary invalid fixture payload attempts were test-construction errors and are superseded by this valid behavioral RED.

## Run safety

At 2026-10-23 15:25:19 JST the official `INAGO-2026-10-23` owner PID 95260 and its process tree remained live. Product source changes and same-run resume were not performed while that owner remained active. The current read-only status is recorded in `worklogs/projects/implementation/product-page-inputs-20261023/formal-run-status-20261023-152519.json`.
