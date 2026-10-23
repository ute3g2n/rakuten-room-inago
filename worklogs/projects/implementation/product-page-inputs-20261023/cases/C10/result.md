## Historical v1.10 result (superseded by v1.11 same-theme correlation)

- 状態: historical PASS under the earlier cross-theme replenishment expectation; not current acceptance evidence
- 結果: copy失敗を履歴に保ち、補充commitで不足影響だけを回復。
- 根拠: test_product_page_selection.py::test_failed_copy_is_replenished_without_recounting_committed_offers
- 独立review追記: このnodeは単一補充例ではPASS。無関係なcommitが別slotのactive failureを解決扱いする反例を未検証と確認したため、相関修正後に追加nodeを実行する。
- 詳細: `../../test.md`、計画 `../../../../../../検討/商品情報入力拡張_実装テスト計画_2026-10-23/plan.md` の当該CASE。
- 本文/商品説明/OCRテキストは保存していない。

## C10 focused GREEN and final regression

- Exact node: `tests_inago/test_product_page_selection.py::test_unrelated_commit_does_not_resolve_active_slot_failure`.
- Command: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_selection.py`.
- Expected: only the failed product or its bound replacement slot resolves an active selection-slot failure; an unrelated commit does not.
- Actual: 5 passed in 22.30s / exit 0, including the exact node. Final planned regression passed 67 expanded tests / exit 0.


## v1.11 same-theme replacement rule and final regression

- Exact full-flow node: `tests_inago/test_product_page_selection.py::test_other_theme_supplement_does_not_resolve_failed_slot`. A different-theme supplement cannot be assigned to or resolve the failed slot; when it fills the fifteenth committed post, the expected result is 15 commits, `error_count=1`, and `partial` status.
- Exact correlation node: `tests_inago/test_product_page_selection.py::test_replacement_slots_and_commits_require_matching_theme`. A legacy failure without `theme_id` recovers its theme from the selected registry; a different-theme assignment/commit leaves the failure active; a same-theme commit for the matching slot resolves it.
- The separate unrelated-commit node also remains active and must not clear failure state.
- Final planned regression: 65 selectors / 69 expanded tests; actual 69 passed in 136.10s / exit 0, including all three exact nodes above.
- Source SHA-256: `7263a447ac2bbe8e39e19db02f861621f9ff67ad6331bcf8d5f2aaf33bc36342`; selection test SHA-256: `4ebec838e38f5354f12af31e07feaa69bdb5405f23a378e99eb28e987594809e`.
- Run manifest: `../../final-regression-v1.11-20261023.json` (aggregate source/test SHA-256 `c13ec893eb910e7fbae7f428a7197e45dfde8d7ef00bdd09c8a8139f964973e8`).
- Original failure history and failed-product images remain preserved; only active error impact is resolved by matching offer/slot/theme evidence.
