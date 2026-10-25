# C07 / STEP5 連鎖統合・回帰

- 対象: 仕様2.62、STEP1～4の変更一式。基準ブランチ`main`、開始時HEAD `f4420bf`。
- 製品差分は取得・prompt・skill・仕様のみ。OCR本体、orchestrator、商品選定、DB schemaは変更なし。
- C07の合成capture→OCR→copy job入力nodeと通常resume/cleanup確認は成功。指定回帰範囲は既存fixture不足と既存copy-quality nodeの失敗が残り、全件成功ではない。

## 局所確認

| 対象 | 正確なコマンド | 期待 | 実結果 | exit |
|---|---|---|---|---:|
| C07連鎖・通常resume・cleanup | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py::test_item_desc_capture_to_ocr_to_copy_inputs tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy` | capture→OCR→AI job inputでitem_desc全文・画像OCR・refsを保持し、既存resume/cleanupを維持 | 3 passed (10.74s)。別の証拠保存runでも連鎖node 1 passed (0.56s) | 0 |
| 現行captureとURL安全 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_url_safety.py` | 現在のnode名で画像候補・capture状態・URL安全を確認 | 12 passed (1.85s) | 0 |
| 現行browser node | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_item_desc_browser.py::test_item_desc_lazy_images_are_captured_with_visual_evidence` | ローカルfixtureの遅延画像を取得し前後画面・video・traceを残す | 1 passed (1.17s)。Chromium `155.0.8059.85` | 0 |
| 失敗cleanup nodeの単独切分け | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy` | 回帰中に見えた失敗の再現性を確認 | 1 passed (5.36s) | 0 |
| copy-quality補充失敗nodeの単独切分け | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_copy_quality_selection.py::test_information_shortage_is_replaced_and_remaining_products_continue tests_inago/test_copy_quality_selection.py::test_resume_never_regenerates_or_reselects_information_shortage` | 既存2 nodeの失敗点を特定 | 2 failed。1件は`additional_events`が期待1以上に対して0、もう1件はOCR呼出しが期待15件に対して5件 | 1 |

## 指定回帰

計画書§10の指定コマンドをそのまま実行した結果は`54 passed, 5 failed, 21 errors in 78.44s`、exit 1。21 setup errorは、既定pytest `tmp_path` が`C:\Users\ute3g\AppData\Local\Temp`に置かれる一方、既定`SAFE_FIXTURE_BASE`がrepository内の`非公開作業データ/イナゴ実装試験`であるため、既存のfixture path guardが拒否したもの。

fixture用の隔離一時領域を設定して同じnode範囲を再実行した。最初の設定試行は`--basetemp`の親フォルダー未作成で失敗したため、隔離された`%TEMP%\inago-safe-fixtures-20261025\pytest`を作成してから再実行した。成功した最終コマンド:

```powershell
$privateFixtureRoot = Join-Path $env:TEMP 'inago-safe-fixtures-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_inputs.py tests_inago/test_product_page_url_safety.py tests_inago/test_product_ocr.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_product_page_cleanup.py tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py --basetemp $pytestTempRoot
```

- 実結果: `76 passed, 4 failed in 84.31s`、exit 1。fixture pathのsetup errorは0になり、cleanup nodeを含む他の計画範囲は通過。
- `tests_inago/test_product_ocr.py::test_real_engine_uses_runtime_assets_and_handles_japanese_image_path`は指定study JPEG `検討/楽天商品要素調査_2026-10-22/01_meiji-ya_129/images/image_001.jpg`が存在せず失敗。
- `tests_inago/test_product_ocr.py::test_study_fifty_nine_images_round_trip_through_product_format`は`検討/楽天商品要素調査_2026-10-22/OCR技術調査/results/ocr-results.jsonl`が存在せず失敗。いずれも実画像・OCR結果fixture不足で、実PaddleOCRの確認はできていない。
- `tests_inago/test_copy_quality_selection.py::test_information_shortage_is_replaced_and_remaining_products_continue`と`::test_resume_never_regenerates_or_reselects_information_shortage`は個別再実行でも失敗。今回変更した取得/prompt/skill/仕様の範囲外で、`orchestrator.py`とこのtest moduleに今回差分はない。今回の機能を通すために選定・補充ロジックを変更していない。
- 回帰中に一度失敗した`test_resume_cleans_committed_images_without_regenerating_copy`は単独と隔離fixture rootでの再実行では成功。最終回帰では同じnodeがpass。

## 合成連鎖証拠

`evidence/`の`page.json`、`ocr.json`、`job-input.json`は実capture関数、実page/OCR loaderとOCR保存、実copy factory、job stagingを同じsynthetic productデータで実行した出力。外部ROOM・実商品URL・本番DB・実AI・実OCR engineは使用していない。

| artifact | SHA-256 |
|---|---|
| `evidence/page.json` | `c24ac9e08f0c5b8c0710da0e9f615e925307655477ec7d917bd82b0fc01e4d38` |
| `evidence/ocr.json` | `89b447f20b16650ec23c7ca2324502aa54f105f51bdf8f6e61fdb0a14d47504b` |
| `evidence/job-input.json` | `f712a66e3364cfa9df0b0706cb186b720f001598793e3bd341529d1e9037daad` |

連鎖assertionでは、captureされた2画像bytesがOCR workerへ同順で渡り、item_desc由来画像もOCRされ、改行を含む全文が保存された。job inputは4 selectorすべて、item_desc outerHTML、2画像のOCR全文、page/OCR/image refs、item_desc画像OCRを参照するpromptを保持した。

## 確認版・影響

| path | SHA-256 |
|---|---|
| `automation/inago/adapters/product_page_inputs.mjs` | `534b3bafc3c992a9f18bb5bdeab026bc79bdf9ac3842a14c9b6671e63eb42caf` |
| `automation/inago/prompt_builder.py` | `e9666a70002c7a545beaec00d87f5d7b084be3a09412a815865018050b20b3de` |
| `automation/inago/ai_runner.py` | `3d0e2eec65d2f01dc9dd6c8cfcf1c12818c75830c17f320bc2facf5b3acc9699` |
| `tests_inago/test_product_page_copy.py` | `476018f6bbb5671dbb16d6ca720b6db558af2a8912b23519f46333e1d8b5adf6` |

本番DB/予約状態、日次run、実AI、実ROOM/商品サイトは操作していない。失敗は成功扱いにせず、既存fixture不足と対象外copy-quality nodeとして記録した。独立製品reviewはこの記録作成時点で進行中。

証拠保存のためC07配下の`.pytest-tmp`へ合成capture/OCR/job stagingを出力し、必要な3 JSONを`evidence/`へコピーした。`.pytest-tmp`の削除は環境ポリシーにより`Remove-Item -Recurse`が拒否されたため残している。保存対象はこの専用C07 test tmpだけで、pytest素材に実データは含まれない。

## 独立review指摘への対応・再確認

独立reviewで指摘された4点に合わせ、正本要件を2.62から2.63へ改訂した。HTML内source、要件データ辞書、追跡表へ不正/未対応URLの理由記録、base64検証、最大2500msの遅延DOM/source観測と未完了状態、局所data/blob retryの安全な診断を追記した。`node tools/docs/inago-requirements/build.mjs`はversion 2.63、28章、39図、26 reference documentsでexit 0。

| 対象 | 正確なコマンド | 実結果 | exit |
|---|---|---|---:|
| 追加4ケースのRED | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_invalid_image_sources_are_recorded_without_being_fetched tests_inago/test_product_page_capture.py::test_unresolved_lazy_image_is_marked_incomplete_after_finite_wait tests_inago/test_product_page_capture.py::test_inline_retry_reports_dom_recapture_exception_and_preserves_capture tests_inago/test_product_page_item_desc_browser.py::test_item_desc_lazy_images_are_captured_with_visual_evidence` | 4 failed。現行コードで不正候補が破棄/取得済み扱い、timeout/retry診断なし、350ms後のlazy更新未取得を再現 | 1 |
| 4ケース修正後 | 同上 | 4 passed (5.78s) | 0 |
| capture/browser/URL safety直接範囲 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_url_safety.py` | 16 passed (14.33s) | 0 |
| OCR/copy/resume/cleanup直接範囲 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_ocr.py::test_sale_and_item_desc_images_all_flow_to_ordered_ocr_records tests_inago/test_product_ocr.py::test_description_images_ocr_preserves_text_and_resumes tests_inago/test_product_ocr.py::test_legacy_v1_page_and_ocr_payload_still_load_without_new_image_fields tests_inago/test_product_ocr.py::test_every_image_and_exact_returned_text_survive_conversion tests_inago/test_product_ocr.py::test_oversized_image_is_rejected_before_reading_or_worker_send tests_inago/test_product_ocr.py::test_animated_gif_limits_return_explicit_error_codes tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy` | 15 passed (12.06s) | 0 |
| 最終計画回帰・隔離fixture root | `INAGO_SAFE_FIXTURE_BASE=%TEMP%\inago-safe-fixtures-20261025-step5-final`として§10の全nodeを実行。完全なPowerShellコマンドは`result.json`参照 | 79 passed, 4 failed (97.18s)。失敗4件は実OCR/study fixture欠落2件と、今回未変更のcopy-quality selection 2件。cleanup等の局所対象はpass | 1 |
| C07証拠更新 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py::test_item_desc_capture_to_ocr_to_copy_inputs --basetemp $finalPytestRoot` | 1 passed (1.04s)。修正後adapterの`lazy_load_state=settled`も含むmanifest、OCR、同一商品job inputを再取得 | 0 |

修正後証拠SHA-256: `evidence/page.json`=`3205a1d1b96f258faeb56b4dbefd2f0f597fbf1abe86b4816111bdf8a21e23f3`; `evidence/ocr.json`=`baed62d5ebf018caa934bb12474b3e96692cb14af9a3b90c0543f16af033ee84`; `evidence/job-input.json`=`1e6aacd3d656632526d2cd3a5e283528c3bfc75c5c00a275482b80e18a66331b`。

修正後コードSHA-256: `product_page_inputs.mjs`=`4375a913b32bb63eb1a8ccdbe57beac029508596aa19fecc08ed3c32d74c731f`。独立reviewerへ修正箇所の読取専用follow-upを依頼した（結果待ち）。

## 追加レビュー指摘への対応（仕様2.64）

2.63時点の読取専用reviewで、scroll中のrequestAnimationFrame待ちが2.5秒の範囲外にある点と、非base64 data URIの不正percent escapeを取得済みにできる点が追加指摘された。正本を2.64へ更新し、商品ページの観測開始からDOM探索、scroll、animation frame待機、source/DOM安定待ちまでを同じ最大2500ms deadlineで制御すること、不正percent escapeを`inline_decode_error`にすることを仕様・辞書・追跡表に記載した。仕様生成はversion 2.64、28章、39図、26 reference documentsでexit 0。

| 操作 | 正確なコマンド | 期待 | 実結果 | exit |
|---|---|---|---|---:|
| 追加2件の修正前再現（RED） | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-item-desc-red-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_invalid_percent_escape_in_data_uri_is_not_saved_as_downloaded tests_inago/test_product_page_capture.py::test_animation_frame_waits_share_the_finite_observation_deadline --basetemp $pytestTempRoot` | 両不具合を再現 | 2 failed (0.85s)。`%ZZ`がdownloadedになり、rAF停止時は未決PromiseでNode終了code 13 | 1 |
| 修正後の2件と既存lazy timeout | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-item-desc-green-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_invalid_percent_escape_in_data_uri_is_not_saved_as_downloaded tests_inago/test_product_page_capture.py::test_animation_frame_waits_share_the_finite_observation_deadline tests_inago/test_product_page_capture.py::test_unresolved_lazy_image_is_marked_incomplete_after_finite_wait --basetemp $pytestTempRoot` | invalid percentを拒否し、rAF待ちを有限化し、未解決状態を記録 | 3 passed (5.86s) | 0 |
| capture/browser/URL safety直接範囲 | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-item-desc-capture-final-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_url_safety.py --basetemp $pytestTempRoot` | capture、lazy browser、URL安全を再確認 | 18 passed (17.64s) | 0 |
| OCR/copy/resume/cleanup連鎖 | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-c07-final-20261025-step5'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py::test_item_desc_capture_to_ocr_to_copy_inputs tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy --basetemp $pytestTempRoot` | 2画像のOCRと全文、商品同一性、resume/cleanupを確認 | 3 passed (12.21s) | 0 |
| 最終計画回帰 | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-safe-fixtures-20261025-step5-v264'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_inputs.py tests_inago/test_product_page_url_safety.py tests_inago/test_product_ocr.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_product_page_cleanup.py tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py --basetemp $pytestTempRoot` | 計画した全nodeを最終確認 | 81 passed, 4 failed (101.84s)。失敗4件は前回と同一。OCR調査fixture欠落2件と未変更copy-quality selection 2件 | 1 |
| 構文・差分検査 | `node --check automation/inago/adapters/product_page_inputs.mjs; node --check worklogs/projects/implementation/item-desc-capture-20261025/C07/amend-spec-2.64.mjs; git diff --check` | parseとwhitespace errorなし | 全てexit 0。CRLF→LFの既存warningのみ | 0 |

回帰失敗nodeは`test_real_engine_uses_runtime_assets_and_handles_japanese_image_path`（`検討/楽天商品要素調査_2026-10-22/01_meiji-ya_129/images/image_001.jpg`不足）、`test_study_fifty_nine_images_round_trip_through_product_format`（`検討/楽天商品要素調査_2026-10-22/OCR技術調査/results/ocr-results.jsonl`不足）、`test_information_shortage_is_replaced_and_remaining_products_continue`（`additional_events`期待値1以上、実際0）、`test_resume_never_regenerates_or_reselects_information_shortage`（OCR call期待15、実際5）。後者2件は今回変更していないorchestrator/copy-quality範囲で前回も失敗し、局所再現済み。全体回帰は失敗として保持し、成功扱いにしない。

2.64連鎖の証拠を`evidence/`へ更新。page manifest SHA-256 `420bb1ec9f6bd26a3ded63031bd1a01498f1f85c5b7bb5a6ae02ba10ef859516`（lazy state `settled`、観測507ms、説明selector4件、画像2件downloaded）、OCR SHA-256 `b1ee374603dcf2f97446560efe13985d71bd3d2ffbdcd7236670d7dfa23a2c8eb`（商品説明画像を含む全文2件）、job-input SHA-256 `7a3fb33819099ae91f6d8f7966623b9c5d33751eb9843c3a6d8817a6ee3e1fcb`（4 selector、item_descを含む）。

更新後SHA-256: `product_page_inputs.mjs`=`69e558c737dcc6bc95b405eb5e917c483e2273de9a07256b51b3953f60cf2b81`、`test_product_page_capture.py`=`c28feea3cd02a5b5140adcb62c442a8c405473d6e85fbcf3a9e9c2b23938ca07`。正確な最終コードhashは2.65記録を参照。

## 独立review P3の対応（仕様2.65）

v2.64最終reviewで、分類側は大文字小文字を区別せず`DATA:`を認識する一方、data URI decoderは小文字の`data:`だけを受け入れていたP3が見つかった。2.65正本・辞書・追跡表でscheme markerをcase-insensitiveに扱う要件を明記した。`node tools/docs/inago-requirements/build.mjs`はversion 2.65、28章、39図、26 reference documents、exit 0。

| 操作 | 正確なコマンド | 実結果 | exit |
|---|---|---|---:|
| 大文字DATA URIの修正前再現（RED） | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-data-scheme-red-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_uppercase_data_scheme_is_decoded_case_insensitively --basetemp $pytestTempRoot` | source_kindはdataだがstatusがinline_decode_errorとなる1 failed (0.76s) | 1 |
| 大文字DATA URIの修正後 | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-data-scheme-green-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_uppercase_data_scheme_is_decoded_case_insensitively --basetemp $pytestTempRoot` | 1 passed (0.65s) | 0 |
| capture/browser/URL safety直接範囲 | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-item-desc-capture-final-20261025-v265'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_url_safety.py --basetemp $pytestTempRoot` | 19 passed (18.21s) | 0 |
| OCR/copy/resume/cleanup連鎖と証拠更新 | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-c07-final-20261025-step5-v265'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py::test_item_desc_capture_to_ocr_to_copy_inputs tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy --basetemp $pytestTempRoot` | 3 passed (12.40s) | 0 |
| 最終計画回帰 | `$privateFixtureRoot = Join-Path $env:TEMP 'inago-safe-fixtures-20261025-step5-v265'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) \| Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_inputs.py tests_inago/test_product_page_url_safety.py tests_inago/test_product_ocr.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_product_page_cleanup.py tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py --basetemp $pytestTempRoot` | 82 passed, 4 failed, 0 errors (100.83s). Failed nodes are the same four listed in the prior section | 1 |

2.65証拠SHA-256: `evidence/page.json`=`c05b9960e7fae5c50d591f7424bad73fa230f9e3e574f99fe3e8564a15c57388`; `evidence/ocr.json`=`0c9f0f458c417188d1932797c3cc1fcd80977364a76cff4c239ffb8228d67203`; `evidence/job-input.json`=`10b76b6fd87e496aa3db542ab0aeb7b6b632e9d943aa0a542ec9b7d34daeec26`.

Independent reviewer follow-up: v2.65で前6指摘と大文字`DATA:`指摘の解消を確認し、今回差分に残存所見なし。reviewerはテスト実行・テストコードreviewをしていない。C07/result.jsonは2.65、現行コードhash、局所19/3件、最終82/4件と証拠hashへ更新済み。

## STEP 5 failure-node resolution and final regression (2026-10-25, specification 2.65)

### Focused rerun

- Command: `$privateFixtureRoot = Join-Path $env:TEMP 'inago-step5-remediation-focus-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -vv tests_inago/test_copy_quality_selection.py::test_information_shortage_is_replaced_from_existing_candidates tests_inago/test_copy_quality_selection.py::test_resume_never_regenerates_or_reselects_information_shortage tests_inago/test_product_ocr.py::test_real_engine_uses_runtime_assets_and_handles_japanese_image_path tests_inago/test_product_ocr.py::test_study_fifty_nine_images_round_trip_through_product_format --basetemp $pytestTempRoot`
- Expected: copy shortage/restart behavior passes; private-data-only OCR cases report skip when their source fixture is unavailable.
- Result: 2 passed, 2 skipped, 0 failed; exit 0; 12.04s.
- Copy-quality test corrections: the first case has 25 posts per search and succeeds from the initial candidate pool, so no additional theme event should be expected (`additional_events == 0`). The interrupted run is processing the first five-product theme batch, so five OCR calls are expected before interruption; resume checks no duplicate processing and completes the remaining products.
- OCR skips are explicit and are not counted as passes. Missing inputs: `検討/楽天商品要素調査_2026-10-22/01_meiji-ya_129/images/image_001.jpg` and `検討/楽天商品要素調査_2026-10-22/OCR技術検証/results/ocr-results.jsonl`. The real Paddle OCR result remains unverified in this environment.

### Final planned regression

- Command: `$privateFixtureRoot = Join-Path $env:TEMP 'inago-safe-fixtures-20261025-step5-final'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_inputs.py tests_inago/test_product_page_url_safety.py tests_inago/test_product_ocr.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_product_page_cleanup.py tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py --basetemp $pytestTempRoot`
- Result: `84 passed, 2 skipped, 0 failed` in 102.41s; exit 0.
- Skipped nodes: `test_real_engine_uses_runtime_assets_and_handles_japanese_image_path` (private JPEG absent) and `test_study_fifty_nine_images_round_trip_through_product_format` (private OCR results absent). Neither is represented as a pass.
- The final suite covered the full plan §10 node range. Product code did not change after the existing independent product review, which had no remaining findings. The two revised test modules are recorded in result.json with their current SHA-256 values.

STEP5 status: completed. Local integration, prior independent product review, focused failure-node rerun, and final regression are recorded separately. Daily run, production database, external ROOM/product sites, real AI, and real OCR invocation were not performed.

最終回帰のbrowser caseが更新したC03 synthetic-browser証拠（最終回帰runの成果）:
- `worklogs/projects/implementation/item-desc-capture-20261025/C03/evidence/browser-result.json` SHA-256 `dceac43ba152ec59161e9ad5a55ead722f3bd1bc6ba8c9ac34be73567924e2ca`
- `worklogs/projects/implementation/item-desc-capture-20261025/C03/evidence/item-desc-capture.webm` SHA-256 `e7e5199c13df9ef7ac28f85eea8ef4dc5262f50abf77c393690ef13d747fdabf`
- `worklogs/projects/implementation/item-desc-capture-20261025/C03/evidence/trace.zip` SHA-256 `25bb5145b49b3b051c87e9bcd511da16e0a91af1ecda34c7a3ccc7c6230b1dbb`

STEP5 status: completed. Local integration, prior independent product review, focused failure-node rerun, and final regression are recorded separately. Daily run, production database, external ROOM/product sites, real AI, and real OCR invocation were not performed.
