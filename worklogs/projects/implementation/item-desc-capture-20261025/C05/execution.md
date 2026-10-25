# C05 / STEP3 OCR manifest round-trip

- 結果: PASS (isolated fake worker tests)。OCR製品コードの変更は不要だった。
- 基準: `main`、開始時HEAD `f4420bf`。入力page manifestはschema_version 1。
- 変更対象: `tests_inago/test_product_ocr.py` SHA-256 `d3145e2b27207ceb9b42a7552bdc845f4376237bc07f8daf58a6c9790864c591`。
- `automation/inago/product_ocr.py`、`tests_inago/product_page_test_support.py`は差分なし。OCR処理はmanifestの全imagesを順に通し、downloaded状態とfileを個別判定していたため、selector種別で絞る追加変更は不要と確認した。

## 局所試験

| Node | 正確なコマンド | 期待 | 実結果 | exit |
|---|---|---|---|---:|
| sale/item混在の全件OCR | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_ocr.py::test_sale_and_item_desc_images_all_flow_to_ordered_ocr_records tests_inago/test_product_ocr.py::test_explicit_product_ocr_retry_reuses_successful_image_hash tests_inago/test_product_ocr.py::test_legacy_v1_page_and_ocr_payload_still_load_without_new_image_fields tests_inago/test_product_ocr.py::test_every_image_and_exact_returned_text_survive_conversion tests_inago/test_product_ocr.py::test_oversized_image_is_rejected_before_reading_or_worker_send tests_inago/test_product_ocr.py::test_animated_gif_limits_return_explicit_error_codes` | 全画像・順序・ref・全文/改行・error/partial・hash再利用・v1互換・上限errorを確認 | 8 passed (0.32s)。4枚manifestのsale/item由来全件が同じ順にOCR payloadへ入り、1枚失敗時partial、ref順、改行全文を保持 | 0 |
| 実engine fixture可用性 | `Test-Path rakuten-room-inago/runtime/ocr-assets/.venv/Scripts/python.exe; Test-Path '検討/楽天商品要素調査_2026-10-22/01_meiji-ya_129/images/image_001.jpg'` | 実engine testの前提を特定 | runtime executableは存在、testが要求する画像fixtureは不在 (`True`, `False`) | 0 |

## 判定と制限

- OCR結果配列の件数はpage.imagesと一致し、順序・image_ref・source_ref・OCR ref、input_refs/evidence_refs、改行入り全文、error状態を照合した。
- `process_product`の明示的な局所再呼出しでは、同じbyte hashの成功画像はworkerへ再送せず、前回失敗した画像だけを再試行し、partialからcompleteへ移ることを確認した。orchestrator通常resume経路には触れていない。
- 旧schema v1 page manifestと既存形のOCR JSONを`load_product_page_ocr`で読み込めることを確認した。OCR byte/frame/pixel上限は明示error codeで返った。
- 実PaddleOCRは未実行。既存real-engine試験が要求する検討用JPEG fixtureが見つからず、合成worker試験のみを実行した。実engineによる日本語認識成功は本STEPでは確認していない。
- 本番DB、runtime state、OCR model、日次runは変更・起動していない。

## C07時点のnode名

- 旧node `test_explicit_product_ocr_retry_reuses_successful_image_hash` は計画名に合わせて `test_description_images_ocr_preserves_text_and_resumes` へ改名した。C05の上記コマンドと8 passedは改名前の実行記録として維持する。
- 計画最終回帰では改名後nodeを含むOCR範囲が実行された。隔離temp rootを使った回帰で通過したのはfake-worker等のfixture node。実engine nodeと59画像研究データnodeは入力ファイル欠落により失敗し、実OCR結果とはみなさない（詳細はC07）。
