# C03 / STEP2 ローカルブラウザー証拠

- 結果: PASS。
- Fixture: `tests_inago/fixtures/product-page-item-desc.html`。ローカル`file:`ページのみを開き、外部ROOM・商品サイトや外部ネットワークを使っていない。
- ブラウザー: Chromium `155.0.8059.85`。
- 現行実行node: `tests_inago/test_product_page_item_desc_browser.py::test_item_desc_lazy_images_are_captured_with_visual_evidence`。後から計画どおり専用browser test fileへ移動・改名した。
- 合成ページでsale_descと下方のitem_descを表示し、scroll後350msで追加される遅延sourceと子imageを有限観測内に取り込むこと、data/blob画像の実バイトがmanifest参照ファイルへ一致して保存されることを確認した。HTML全文、item_desc由来、3件に統合された画像候補の`downloaded`状態、`lazy_load_state=settled`を確認。

| 操作 | 正確なコマンド | 期待 | 実結果 | exit |
|---|---|---|---|---:|
| 現行browser局所node | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_item_desc_browser.py::test_item_desc_lazy_images_are_captured_with_visual_evidence` | 350ms後のDOM更新を待ち、遅延画像と追加子要素、data/blob bytesを保存 | 1 passed (1.74s)。Chromium `155.0.8059.85`、3 manifest画像とも`downloaded`、lazy state `settled` | 0 |
| 現行capture/URL safety集合 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_url_safety.py` | 現行capture nodeとURL safetyを確認。browser nodeは別fileで個別実行 | 12 passed (1.85s) | 0 |

## 保存証拠

- 直前画面: `C03/evidence/before.png`
- 直後画面: `C03/evidence/after.png`
- Playwright video: `C03/evidence/item-desc-capture.webm`
- Playwright trace (screenshots/snapshots/sources有効): `C03/evidence/trace.zip`
- manifestとbyte照合結果: `C03/evidence/browser-result.json`
- fixture: `tests_inago/fixtures/product-page-item-desc.html`

PNG signature、WebM EBML header、trace ZIP整合性検査はいずれも成功した。最初のrunner呼出しはNode `-e`引数位置を誤り終了code 1となったため、引数sliceを修正して上記nodeを再実行した。合成画像は表示確認できるSVGであり、late child imageもafter画面とmanifestに含まれる。試験node自体の最終結果はpass。

## STEP5 v2.65での最終再確認

- 計画回帰内で同じbrowser nodeを実行し、続けてcapture/browser/URL safety直接範囲を実行した。2.65のdirect commandはC07 `execution.md` に記録。
- Chromium `155.0.8059.85`、合成ページの遅延子要素を取得し、3画像全てdownloaded、`lazy_load_state=settled`、`lazy_load_observed_ms=637`を確認した。
- 直前/直後画面、Playwright video、trace、browser resultは本ディレクトリの `evidence/` に保存されている。今回の実行でもスクリーンショット/動画/traceを取得できた。
