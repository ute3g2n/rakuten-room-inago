# C28 focused evidence

## Existing five-node run (2026-10-23 14:11 JST)

- Expected: related raw query only when related words exist; exact saved Trends query_word only when no related words exist.
- Actual: 4 failed, 1 passed in 1.03s, exit 1. Legacy query resume node passed. Full exact command and historical test hash are recorded in `../../test.md` under “ROOM検索語の条件補足とfocused RED”.

## New Trends-raw-hit / KEY-empty boundary (2026-10-23 15:06 JST)

- Command: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_theme_query_components.py::test_raw_trends_hits_with_no_key_selection_do_not_use_query_fallback`
- Expected: for `has_related_words=True` and `selected=()`, plan has no fallback and `_make_tasks` emits no query_word task.
- Actual: 1 failed in 0.38s, exit 1. `DurableTrendsThemePlanner._plan()` does not accept the new `query_word`/`has_related_words` predicate; product source unchanged.
- Test SHA-256: `0a6fd15ebd08b8dea4098490ce252bdfa108d5049620821c6eba8b615ed9e87b`.
- Product source SHA-256: `automation/inago/daily_theme_planner.py` `3f4753da3cbc9f004a412e48a4ba9e2874b386db30d4cebca8a69d02fc1ef4c1`; `automation/inago/orchestrator.py` `7358e2c824216de5245ddac94d2d008b8b8e3b01657c9ed9ae24bc429b8c1255`.

## Existing related-only regression expectation (2026-10-23 15:06 JST)

- Updated node: `tests_inago/test_step13.py::test_related_queries_are_used_without_parent_fallback_for_all_untried_themes`.
- Expected: 31 related-only searches across 30 theme planner calls, no `theme_fallback` tasks.
- Actual: 1 failed in 4.75s, exit 1; current product source produced 61 attempts.
- Test SHA-256: `tests_inago/test_step13.py` `7447b0fabfadcedbde5220491139bb7ec22e77bea75511e81c4b17463525f1e0`.
- Product source SHA-256: same hashes as above.

These are test-first RED results against the currently unchanged product source. The official daily owner is still live, so product-source updates and same-run resume are deferred; no test is represented as GREEN.

## C28 focused GREEN and final regression

- Command: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_theme_query_components.py`.
- Expected: Trends raw-hit existence, not KEY post-filter count, controls the permitted query_word fallback.
- Actual: 27 passed in 2.42s / exit 0. Final planned regression passed 67 expanded tests / exit 0.
- Current test SHA-256: f57431f9ed422ffbeeac7b7019de9cc2bf73ff983d0251295ec7c0efe09bf61e.
