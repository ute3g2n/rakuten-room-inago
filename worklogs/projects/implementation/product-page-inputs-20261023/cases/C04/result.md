# C04 結果

- 状態: **PASS**
- 結果: selectorなし/空/取得失敗状態を区別。
- 根拠: test_product_page_capture.py::test_missing_empty_and_failed_selector_states_are_distinct
- 独立review追記: capture_failedを保存できることはPASS。ただしselectionがそのstateを拒否しない境界漏れを確認したため、fail-closed/replenishment nodeを追加する。
- 詳細: `../../test.md`、計画 `../../../../../../検討/商品情報入力拡張_実装テスト計画_2026-10-23/plan.md` の当該CASE。
- 本文/商品説明/OCRテキストは保存していない。


## 選別境界followup（2026-10-23; NOT_RUN）

Planned node: tests_inago/test_product_page_selection.py::test_capture_failed_page_is_skipped_and_replenished_before_copy.
Expected: capture_failed is rejected before selection/OCR/copy/commit and replenished.
Attempt 1: .venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_selection.py::test_capture_failed_page_is_skipped_and_replenished_before_copy; no output for about 80 seconds; interrupted via Ctrl-C; command exit 1; assertion result not observed.
Attempt 2: .venv-inago\Scripts\python.exe -B -m pytest -vv -o faulthandler_timeout=15 tests_inago/test_product_page_selection.py::test_capture_failed_page_is_skipped_and_replenished_before_copy; 1 node collected; faulthandler at 15s showed trace.py::_validate_trace_event -> TraceWriter._load_state -> trace write -> _cleanup_unselected_product_inputs. Interrupted at 76.47s; KeyboardInterrupt, no tests ran, exit 1. No product PASS/FAIL inferred.
Source hashes: test_product_page_selection.py 8ebf3a720c438b960508a761862faf2ce2df64361378dc9fce358e2b7291330a; orchestrator.py 7358e2c824216de5245ddac94d2d008b8b8e3b01657c9ed9ae24bc429b8c1255; trace.py 6163b086cd0379c820f4f615a5d988c69c41311dc5dbc1246551d5e9a0d0d34f.
Formal owner status at 2026-10-23T16:03:04+09:00: still active. Evidence: worklogs/projects/implementation/product-page-inputs-20261023/formal-run-status-20261023-160304.json.


## C04 selection focused RED（2026-10-23T16:13:30+09:00）

Exact command: .venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_selection.py::test_capture_failed_page_is_skipped_and_replenished_before_copy
Expected: capture_failed offer is excluded before selection/OCR/copy/commit and an eligible candidate fills the slot.
Actual: 1 failed in 20.79s; exit 1. The first direct registry assertion failed because the capture_failed offer shop-g01-t02:item-01 remained in selected_products. Later OCR/copy/commit assertions were not observed. The earlier assertion that it was absent from copied_offers passed, but that does not prove it was excluded from selection or OCR.
The test now patches only orchestrator._trace_product_stage to no-op because repeated per-product trace validation dominated the initial full-flow attempts; C21 remains the trace contract test. Test SHA-256 A4b2d815b86924bffe3a75932f448b98bdf9407a8f80b56ae2ea996ef680fcb7. Product source SHA-256 orchestrator.py 7358e2c824216de5245ddac94d2d008b8b8e3b01657c9ed9ae24bc429b8c1255.

## C04 focused GREEN and final regression

- Exact node: `tests_inago/test_product_page_selection.py::test_capture_failed_page_is_skipped_and_replenished_before_copy`.
- Command: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_selection.py`.
- Expected: failed product-page capture is excluded before selection/OCR/copy/commit, and a valid replacement fills the target.
- Actual: 5 passed in 22.30s / exit 0; this complete module includes the exact C04 node. The final 63-selector regression also passed all 67 expanded tests / exit 0.
- Current source hashes: `orchestrator.py` c3a27fc09cfccec13d8022787300b5c5bd2d03dadcee7bdfb472b4f30add4143; `test_product_page_selection.py` 59311205edd95c3529ca9c1c764c51fcf59006b5101153df26ec034379110884.


## v1.11 legacy checkpoint resume validation (final regression)

- Exact node: `tests_inago/test_product_page_selection.py::test_resume_rejects_legacy_selected_checkpoint_with_capture_failed_page`.
- Expected: reject saved `capture_failed` page before reusing OCR or creating copy input; persist a failure record and do not call OCR.
- Actual: included in the final planned 65-selector run; 69 expanded tests passed / exit 0, including this exact selector.
- Source SHA-256: `7263a447ac2bbe8e39e19db02f861621f9ff67ad6331bcf8d5f2aaf33bc36342`; test SHA-256: `4ebec838e38f5354f12af31e07feaa69bdb5405f23a378e99eb28e987594809e`.
- Run manifest: `../../final-regression-v1.11-20261023.json` (aggregate source/test SHA-256 `c13ec893eb910e7fbae7f428a7197e45dfde8d7ef00bdd09c8a8139f964973e8`).
