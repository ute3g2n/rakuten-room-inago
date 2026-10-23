# 商品ページ入力拡張 実装記録

## 対象

- 対象日: 2026-10-23
- 作業root: `C:/project/rakuten-room-inago`
- 作業開始HEAD: `1dcd54ec8025abd2e0105acb41118d929c8e94b0` (`main`, upstream `origin/main`)
- 実装範囲: 計画STEP02〜07。STEP01の一回skipは別のScheduler記録に保存。
- 非公開の商品本文・OCR本文、設定本文、認証情報はこの記録に含めない。

## 実装した内容

- Rakuten商品遷移後、`span.sale_desc`、`span.normal_reserve_item_name`、`span.normal_reserve_catch_copy` の描画済み `outerHTML` と、商品説明要素内の全画像候補をrun/shop/item単位で保存する。重複URLを統合し、HTML/image取得状態・元のidentity・安全な参照を残す。
- 選別商品のrun内registryとcheckpointを追加。15件の選別・OCR完了後に投稿文作成へ進み、コピー失敗後の補充、同run再開、未選別folderの削除を扱う。
- PaddleOCR 3.7.0、PaddlePaddle 3.3.1、PaddleX 3.7.2 の隔離runtimeとlock/setupを追加。全画像の返却文字列・状態・参照を商品別JSONへ保存し、改行を保持する。
- HTML/OCR JSONと全参照を既存コピー入力に追加し、改行解釈と商品に無関係な文章の扱いをcopywriter指示へ追加。入力を無断で短縮しない。
- Draft commit後に当該商品の `images/` だけを削除し、page/OCR/cleanup結果を保持。同run再開時に未完cleanupを再試行し、生成やOCRをやり直さない。
- 正式launcher、CLI、production runtime、trigger、orchestrator、daily pipeline、recoveryへ `target_date` / manual / explicit resume を伝達。過去日を10/24 09:00の実時計のまま10/23対象として再開できるようにし、cause unknownは解決済みと偽らず、証拠のない一括error消去をなくした。
- 安全な商品取得/OCR/cleanup trace項目と運用trace一覧を追加。

## 実装中に直した問題

実PaddleOCRの隔離workerがWindows既定コードページでJSON応答を書き、親プロセスのUTF-8 readerで商品画像のOCR応答を読めなかった。子プロセスの`PYTHONIOENCODING=utf-8`を固定し、OCRライブラリ出力をJSON-lines用stdoutから分離した。実エンジンの日本語パスcase通過を確認した。

既存の日次fixtureが商品ページ参照を作らず、新しいcopy factoryが`shop_id`、`item_id`、商品入力rootを必要とする箇所があった。fixtureに3要素HTMLの合成ページを追加し、既存copy-spec fixtureにも必須identity/rootを渡した。

明示された`cause_resolved`だけで根拠のないcopy errorを消す既存試験期待を更新した。errorはactiveのまま保持し、claimがあれば「operation evidenceなしでは効果なし」を記録する。

## STEP09 独立レビュー

2026-10-23に独立read-only製品レビューを実施。結果は`changes_requested`（HIGH 2件、MEDIUM 2件）。

- 商品DOM評価失敗(`html_state=capture_failed`)を選別時に拒否しないため、空HTML/0画像OCRをコピー入力としてcommitし得る。
- `_resolve_product_slot_failure_on_commit`が同一offerに対応する失敗がないときactive failure先頭をfallbackで解決し、無関係なcommitを補充成功扱いし得る。
- launcherを迂回する直接production CLIが、manual指定だけで既存runのない過去日runを作成し得る。
- 商品DOM由来画像URLとredirect宛先を検証せずGETし、画像以外のcontent-typeも拒否しない。

詳細根拠は`code-review.md`。レビュー時点の試験コードは編集せず、非公開商品本文/OCR本文にもアクセスしていない。正式runはレビュー到着前に起動済みなので、稼働中の子プロセスを止めず実状を記録する。指摘修正後は最小影響nodeを実行し、所定の全回帰を最終実行する。

## STEP03/04 レビュー追補: 旧checkpointと補充相関

- 担当範囲: `automation/inago/orchestrator.py` の保存済み選別再検証と失敗枠の補充相関。計画v1.10、STEP03/04、R12/R14、C04/C10/C12/C25に対応。
- `_ocr_selected_products` はdraft実在状態を先にselected registryへ反映し、未commitの選別だけで保存pageを再読込する。`html_state=captured`およびimages配列を確認してからOCR完了状態を再利用する。保存page欠測、capture_failed、html_state欠測、images欄欠測はfailedとactive product slot failureへ保存し、OCR/copyへ渡さない。正常取得でimagesが空配列のページは従来どおり処理する。
- `_record_product_slot_failure` はtheme_idを保存する。旧failureは同offerのselected registryまたはoffer_theme_idsからテーマを確認する。補充選別は同テーマの未割当失敗枠に順序どおり結び付け、commit時もoffer/slot/theme一致を確認する。異なるテーマの通常commitや旧checkpointの誤った別テーマ割当は、元失敗を解除しない。
- commit済み商品のcleanup後再開はdraft照合を優先し、保存画像の欠測を新規取得失敗として扱わない。
- 実行確認: 割当指示に従い、ソースと計画・試験定義を読取。試験、構文検証、collect-onlyなどの機械検証は未実施。結果をPASSとはしていない。DB/設定/Scheduler/本番データ、試験コード、計画・レビュー・結果文書は変更していない。commit/pushは親担当へ引き渡す。
- 既存の最小影響node: `tests_inago/test_product_page_selection.py::test_capture_failed_page_is_skipped_and_replenished_before_copy`、`tests_inago/test_product_page_selection.py::test_unrelated_commit_does_not_resolve_active_slot_failure`、`tests_inago/test_product_page_selection.py::test_failed_copy_is_replenished_without_recounting_committed_offers`、`tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr`、`tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy`。
- 追加確認を親へ引継ぎ: 旧selected/ocr_complete/copy_in_progressのcapture_failed・page欠測・images欄欠測を拒否する境界、複数active failureの同テーマ割当、別テーマの旧replacement_slot_idでcommitしても失敗を解除しない境界。既存補充flow nodeは追加テーマでもcomplete/error_count0を期待しており、今回の「別テーマで元失敗を解除しない」要件と試験期待の整合確認が必要。
