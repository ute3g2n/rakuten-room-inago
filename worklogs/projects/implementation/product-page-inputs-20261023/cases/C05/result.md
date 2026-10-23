# C05 結果

- 状態: **PASS**
- 結果: 画像候補順と同URL統合を確認。
- 根拠: test_product_page_capture.py::test_all_image_candidates_are_captured_in_occurrence_order
- 独立review追記: DOM由来URLとredirect先のnetwork/content-type検証不足を確認。公開画像の取得を保ちつつ不正宛先を拒否するnodeを追加する。
- 詳細: `../../test.md`、計画 `../../../../../../検討/商品情報入力拡張_実装テスト計画_2026-10-23/plan.md` の当該CASE。
- 本文/商品説明/OCRテキストは保存していない。


## URL safety followup（2026-10-23; followup）

The three planned URL safety nodes ran together with the two past-date cases: total command 5 failed/11.49s/exit 1. The URL nodes failed before functional assertions because the test import could not find isSafePublicImageUrl/fetchPublicImage exports. This is a harness/module export mismatch; no URL safety behavior is inferred.

## C05 focused GREEN and final regression

- Commands: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_capture.py` and `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_url_safety.py`.
- Expected: complete image candidate order/dedup and rejection of private/mixed DNS answers and private redirects.
- Actual: capture module 4 passed in 0.78s / exit 0; URL-safety module 3 passed in 0.38s / exit 0. Final planned regression passed 67 expanded tests / exit 0.
- Current `product_page_inputs.mjs` SHA-256: 8e45e872d17c4f8c4ecdf5ab8f18afeb486410bd81c6b95d10f220da71594596.
