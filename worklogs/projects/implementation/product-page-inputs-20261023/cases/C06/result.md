# C06 結果

- 状態: **PASS**
- 結果: HTTP失敗/画像なしの状態を確認。
- 根拠: test_product_page_capture.py::test_download_failure_and_zero_images_preserve_capture_states
- 独立review追記: 画像取得失敗の状態保持はPASS。別途、localhost/private network redirectと画像以外content-typeの拒否を追加検証する。
- 詳細: `../../test.md`、計画 `../../../../../../検討/商品情報入力拡張_実装テスト計画_2026-10-23/plan.md` の当該CASE。
- 本文/商品説明/OCRテキストは保存していない。


## content-type followup（2026-10-23; followup）

The planned non-image response node failed before its assertion because fetchPublicImage was not exported from product_page_inputs.mjs. Command group result: 5 failed/11.49s/exit 1. No content-type behavior is inferred.

## C06 focused GREEN and final regression

- Commands: `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_capture.py` and `.venv-inago\Scripts\python.exe -B -m pytest -q tests_inago/test_product_page_url_safety.py`.
- Expected: distinguish image download failure/zero images and reject responses whose content type is not an image.
- Actual: capture module 4 passed in 0.78s / exit 0; URL-safety module 3 passed in 0.38s / exit 0. Final planned regression passed 67 expanded tests / exit 0.
- Current URL-safety test SHA-256: 2824686e8a117afa5e88601e6a3196130a03e39d0ab97d62b724e4924e0df21c.
