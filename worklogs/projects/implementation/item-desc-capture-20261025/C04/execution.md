# C04 / STEP2 画像、由来、局所再試行

- 結果: PASS。
- 対象コードSHA-256: `automation/inago/adapters/product_page_inputs.mjs` = `534b3bafc3c992a9f18bb5bdeab026bc79bdf9ac3842a14c9b6671e63eb42caf`。
- 合成画像fixtureとassertionは`tests_inago/test_product_page_capture.py` SHA-256 `d3ae74a25ab9f3d8753845ae329e58fec16f7241d884055eeed783fd7e402ac3`。画像fixture HTML SHA-256 `62c22f00aed019d4a993a91d89cce1d2bce7202155608e2fc068ae6eda95747c`。

## 確認内容

- sale_desc/item_desc双方の`currentSrc`、`src`、遅延属性、`srcset`、背景URLを採録。srcset全候補、URL重複の取得統合と全出現由来、selector・match index・DOM位置・属性・順序を確認。
- data URIのbase64/percent-encoded bytesとblob fetch bytesを実際に保存し、ファイルbase64・byte長をassert。
- HTTP失敗状態、0画像、capture失敗時の再呼出しを確認。
- 通常再呼出しは既存capturedを保持し再取得しない。`retryFailedImages: true`の明示呼出しで失敗HTTP画像だけを再要求し、成功済み画像のbyte/順序/HTMLは維持する。blob失敗は明示呼出し時に現在DOMから同一URLを再採録して再試行する。
- `cleanup.json.status == complete`では明示retry指定でも再ダウンロードしない。旧schema-v1のcapturedページは通常再利用し、HTML/画像manifestを上書きしない。
- 既存公開HTTP(S)検査とredirect先制限に関する3試験nodeは引き続き合格。

## 実行結果

| 操作 | 正確なコマンド | 期待 | 実結果 | exit |
|---|---|---|---|---:|
| 画像・capture・URL safety最終 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_url_safety.py` | 計画node全件green | 13 passed (2.99s) | 0 |
| Module parse | `node --check automation/inago/adapters/product_page_inputs.mjs` | parse成功 | errorなし | 0 |
| 差分空白 | `git diff --check` | whitespace errorなし | errorなし。既存追跡表とadapterでCRLF→LF warning | 0 |

STEP2では製品manifest schema v1の追加fieldのみを使い、DB migration・OCR code・orchestrator/resume経路の変更はない。通常capture callは新たなretry flagを渡さないため、既存captured page再利用はそのままである。

## C07時点の現行名での再確認

- capture/URL safety: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_url_safety.py` → 12 passed (1.85s)、exit 0。
- browser nodeは計画どおり別fileへ移動したため、C03の専用browser nodeで個別確認した。
- この時点の`tests_inago/test_product_page_capture.py` SHA-256は`8b25860b4ef7fa3d6530de3d3ce1bc178cbd1cbf00689140ee322c7e392a12de`。旧RED/STEP2のnode名とhashは過去実行記録のまま保持する。

## STEP5 review修正後

- 不正URL/base64、未解決lazy source、DOM再取得例外の新規nodeを追加。detailsと結果はC07へ記録。
- `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_url_safety.py` → 16 passed (14.33s)、exit 0。
- 現行adapter SHA-256は`4375a913b32bb63eb1a8ccdbe57beac029508596aa19fecc08ed3c32d74c731f`、capture test SHA-256は`bee58efb7b0d4144aaea35410fffe0300f2f6976cefe0deea82c1b79016cb670`。browser fixtureも350ms後の新規子imageを追加した版へ更新した。

## v2.65最終capture範囲

- `span.item_desc` のcaptureで、scroll / requestAnimationFrame / DOM・source安定待ちが同じ最大2500ms期限を共有することを追加確認。
- base64/percent data URIの不正入力を拒否し、大文字`DATA:` schemeをcase-insensitiveでdecodeすることを確認。
- 現行製品コードSHA-256: `9c68a923547e41e23b0fe3bca6189af656903cbb4fd74a8baa2206827c60b958`。現行capture test SHA-256: `ab063afc7e5f742e77efd9872768463d6241f7e75acbf4e931870a13dbfd742b`。
- `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_url_safety.py --basetemp $pytestTempRoot`（隔離root設定の完全なPowerShellコマンドはC07/result.json）: 19 passed, exit 0。
