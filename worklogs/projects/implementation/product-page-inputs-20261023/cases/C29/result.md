# C29 focused test result (initial RED)

- Observed: 2026-10-23, during the v1.4 plan update.
- Scope: isolated SQLite fixture and `SAFE_FIXTURE_BASE` runtime path only. The production run and database were not opened or modified by these tests.
- HEAD at run: `1dcd54ec8025abd2e0105acb41118d929c8e94b0` (`main`).
- Test source SHA-256: `e74d736db76b1ed437b29ac345309e286229fcea488e337b6bd46a7da6006077`.
- Product source SHA-256 at test time: `orchestrator.py` `7358e2c824216de5245ddac94d2d008b8b8e3b01657c9ed9ae24bc429b8c1255`; `daily_theme_planner.py` `3f4753da3cbc9f004a412e48a4ba9e2874b386db30d4cebca8a69d02fc1ef4c1`.

## Command and result

```powershell
.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_theme_query_components.py::test_resume_migrates_legacy_fallback_plan_and_task_to_saved_trends_query tests_inago/test_theme_query_components.py::test_resume_removes_legacy_parent_fallback_plan_and_task_when_related_queries_exist
```

- Expected before the product fix: both nodes fail because resume does not reconcile saved plans/tasks with durable Trends results.
- Actual: **2 failed, 0 passed, 1.92s, exit 1**.
- `test_resume_migrates_legacy_fallback_plan_and_task_to_saved_trends_query`: the pending fallback task still used the compound theme, while the saved Trends `query_word` was one component. The assertion stopped before the later committed-state assertions; those were not observed in this run.
- `test_resume_removes_legacy_parent_fallback_plan_and_task_when_related_queries_exist`: with `task_cursor=1`, resume still searched the pending parent fallback after the related task had already been processed. The assertion stopped before the later state-preservation assertions; those were not observed in this run.

The two nodes provide the planned RED evidence. Preserve the same run ID, processed prefix/cursor, committed draft, and selected manifest when implementing the idempotent migration; then rerun these exact nodes as the focused GREEN check.


## Added resume boundary node (2026-10-23)

Added `test_resume_removes_legacy_parent_fallback_when_raw_hits_have_no_key_selection` after the original two C29 migration nodes. It seeds a non-empty Trends raw related snapshot, rejects every term at KEY selection (`plan.related == ()`), and places a legacy parent fallback task in the pending checkpoint alongside an existing committed draft.

- Command: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_theme_query_components.py::test_resume_removes_legacy_parent_fallback_when_raw_hits_have_no_key_selection`
- Expected: no ROOM search for the stale fallback; fallback is removed; cursor, attempts, committed offer and draft stay intact.
- Actual: **1 failed, 0.83s, exit 1**. The current source searched the stale fallback, so the first `searched == []` assertion failed. Assertions later in the node were not observed.
- Test SHA-256: `64d6b3e01c1373f0a7d6b30a0379790898ec59f07225ec7fa0d299b00f115acb`.
- Product hashes remained unchanged: planner `3f4753da3cbc9f004a412e48a4ba9e2874b386db30d4cebca8a69d02fc1ef4c1`; orchestrator `7358e2c824216de5245ddac94d2d008b8b8e3b01657c9ed9ae24bc429b8c1255`.
- Full command/source/input details: `cases/C29/test.md`.

This valid behavioral RED specifically covers Trends raw hits present with KEY selection empty during same-run resume. It does not establish that the state-preservation assertions pass. The official daily owner was still live, so product source and production run state were left untouched.

## C29 focused GREEN and final regression

- Command: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_theme_query_components.py`.
- Expected: same-run resume migrates a legacy fallback only when raw Trends hits are absent; with raw hits, remove pending parent fallback even if KEY selected zero, without replaying processed tasks.
- Actual: 27 passed in 2.42s / exit 0. Final planned regression passed 67 expanded tests / exit 0.
- Current test SHA-256: f57431f9ed422ffbeeac7b7019de9cc2bf73ff983d0251295ec7c0efe09bf61e.
