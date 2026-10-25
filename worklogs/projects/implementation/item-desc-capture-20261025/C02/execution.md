# C02 / STEP2 selector capture

- 結果: PASS。
- 基準: `main`、STEP2開始時HEAD `f4420bf`。仕様はSTEP1で更新した2.62。
- 実装版SHA-256: `automation/inago/adapters/product_page_inputs.mjs` = `534b3bafc3c992a9f18bb5bdeab026bc79bdf9ac3842a14c9b6671e63eb42caf`。
- 対象: 既存selector列末尾に`span.item_desc`を追加し、sale/item両領域の全match・完全な`outerHTML`・empty/not_found/unavailable状態を同じmanifestへ保存する。
- 既存経路: `automation/inago/adapters/public_sources.mjs`のROOM商品遷移側2箇所(770行付近、1428行付近)が既に`saveProductPageInputs`を呼ぶことを読取確認した。呼出側の変更は不要で、ファイルは編集していない。

## 局所試験

| Node | 正確なコマンド | 期待 | 実結果 | exit |
|---|---|---|---|---:|
| RED: 新selector/画像/retry | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_item_desc_is_appended_and_every_matching_description_html_is_saved tests_inago/test_product_page_capture.py::test_description_images_keep_all_sources_bytes_and_selector_provenance tests_inago/test_product_page_capture.py::test_only_explicit_local_retry_retries_failed_images_and_preserves_success tests_inago/test_product_page_capture.py::test_cleanup_and_legacy_captures_are_never_refetched` | 実装前に新契約が失敗する | 3 failed、1 passed。未対応selector、item画像取得、明示retryの不在を再現。cleanupとlegacyの通常非再取得は既に合格 | 1 |
| selector/capture最終 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_url_safety.py` | 既存selector順、全HTML、状態、画像、retry、旧captureとURL安全性を維持 | 13 passed (2.99s)。capture 10 node、URL safety 3 node | 0 |
| JavaScript parse | `node --check automation/inago/adapters/product_page_inputs.mjs` | syntax errorなし | errorなし | 0 |
| whitespace | `git diff --check` | 差分エラーなし | errorなし。product adapterと既存追跡表にCRLF→LF予定のwarning | 0 |

## 判定

- `sale_desc`、`item_desc`は各selector内の全matchをselector順に保存する。`item_desc`のみ、双方、複数match、空、不在、評価失敗を合成DOMで確認した。
- 従来のsale/name/catch-copy selector順は維持した。
- 取得処理は両説明領域へ共通化した。public_sources呼出側、OCR、DB、日次runは変更・起動していない。
- 変更版の詳細hash、全ケースの結果、ブラウザー証拠はC03/C04の記録を参照。
