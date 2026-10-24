# 最終回帰試験記録 — 2026-10-24

## 実行

- 対象: 投稿文品質改善の計画 §6 に指定された9ファイル（`test_copy_quality_review.py`、`test_copy_quality_selection.py`、`test_product_page_copy.py`、`test_product_page_selection.py`、`test_daily_fix_ai.py`、`test_step08.py`、`test_step09.py`、`test_step12.py`、`test_step13.py`）。
- 正確なコマンド:
  ```powershell
  .venv-inago/Scripts/python.exe -m pytest tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_daily_fix_ai.py tests_inago/test_step08.py tests_inago/test_step09.py tests_inago/test_step12.py tests_inago/test_step13.py -q --disable-warnings --maxfail=5 --basetemp=非公開作業データ/イナゴ実装試験/copy-quality-final-20261024-01
  ```
- 開始/終了 (JST): 2026-10-24 10:42:22 +09:00 / 2026-10-24 10:44:42 +09:00。
- 実行識別子/一時領域: `copy-quality-final-20261024-01`。
- ソース版: 開始時 HEAD `8939ab62fce902a86af54f3562ce37f50fa59dad` と同時点の作業ツリー差分。作業ツリーには投稿文生成・レビュー・選択の実装差分と関連テスト差分が含まれていた。作業中にソースは変更していない。
- 入力/fixture版: 9対象ファイルの開始時作業ツリー版。pytest の合成 `runtime_case` / SQLite fixture と固定時計を使用。失敗caseは `tests_inago/test_step13.py` の `runtime_case` に旧run/draft/pending human_action を合成して追加する。
- 期待: 全対象nodeがpass。
- 実結果: 209 collected; 208 passed, 1 failed, 0 skipped. 終了コード 1。
- 失敗完全node: `tests_inago/test_step13.py::test_pending_historical_reports_are_applied_before_search`。
- 失敗内容: `search_attempt` の期待件数60に対し、実測30。テストは既存pending historical reportが検索前に適用されること等を確認し、その後の件数assertionで失敗した。原因はこの試験結果だけでは未確定。
- 画面証拠: ブラウザー操作を含まないfixture試験のため対象外。pytestコンソール出力を代替証拠として保存。
- ログ: `検討/投稿文品質改善_2026-10-24/final-regression-20261024-01.log`。
- 実AIによる投稿文の品質評価はこのfixture試験に含まれず、未確認。
- 次の対応: 全体回帰は反復しない。上記失敗nodeと直接影響範囲を局所調査・修正し、原因が分かった後に影響nodeを局所確認する。

## 2026-10-24 局所調査後の再確認

### 先行失敗nodeの原因・修正と局所確認

- 先行回帰で失敗した `tests_inago/test_step13.py::test_pending_historical_reports_are_applied_before_search` の期待件数60は、テスト側の期待値誤りと判明した。実装担当からの報告では、HEAD時点の `orchestrator.py` を `git show` からin-memoryでロードした基準実行でも30件となり、1 failed / 3.01s / exit 1。既存 `FakeThemePlanner` は30テーマそれぞれにrelated語を1件返し、related語があると既存 `normalize_theme_query_fallback` はfallback語を加えないため、30件が実挙動となる。
- 最小修正: 該当assertを `search_attempt == len(bundle.room.requests) == 30` とし、全requestが `query_kind == "related"` であることを確認する。検索前のhistorical report適用callbackとpublication確認は維持。
- 局所確認 (実装担当からの実測報告):
  ```powershell
  .venv-inago/Scripts/python.exe -m pytest tests_inago/test_step13.py::test_pending_historical_reports_are_applied_before_search -q --disable-warnings --basetemp=非公開作業データ/イナゴ実装試験/copy-quality-historical-focused-20261024-01
  ```
  期待: pass。実結果: 1 passed、3.14s、終了コード0。局所ログは個別ファイル保存なしとの報告。これは実装担当から受け取った実測であり、本試験担当が独立に再実行したものではない。

### 回帰再実行 02

- コマンドは上記「実行」欄と同じ9ファイル。`--basetemp=非公開作業データ/イナゴ実装試験/copy-quality-final-20261024-02`。
- 開始/終了 (JST): 2026-10-24 10:46:42 +09:00 / 2026-10-24 10:48:57 +09:00。実行識別子: `copy-quality-final-20261024-02`。
- ソース版: HEAD `8939ab62fce902a86af54f3562ce37f50fa59dad` と同時点の作業ツリー差分。独立reviewで指摘されたcheckpoint再開時の品質レビュー照合不足P1の修正前。実行中にソースは変更していない。
- 入力/fixture: 9対象ファイルの実行時作業ツリー版。fixture・時計は先行runと同じ設定、専用basetempは新規run用に分離。
- 期待: 全対象node pass。実結果: 209 collected; 208 passed, 1 failed, 0 skipped。終了コード1。
- 新しい失敗完全node: `tests_inago/test_product_page_selection.py::test_resume_recaptures_failed_selected_page_without_room_search`。期待 `outcome.status == "complete"`、実測 `"partial"`。原因はcheckpoint再開品質レビュー照合不足P1の影響と親担当から共有された。P1の修正はこの試験時点で未適用。
- ログ: `検討/投稿文品質改善_2026-10-24/final-regression-20261024-02.log`。
- 次の対応: このnodeとP1の影響範囲を局所修正・確認した後、計画済み9ファイル回帰を修正後版で最終実行する。回帰02は修正前版の記録として保持し、再実行しない。

## 最終回帰 03（P1修正・freeze版）

- 実行コマンド:
  ```powershell
  .venv-inago/Scripts/python.exe -m pytest tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_daily_fix_ai.py tests_inago/test_step08.py tests_inago/test_step09.py tests_inago/test_step12.py tests_inago/test_step13.py -q --disable-warnings --maxfail=5 --basetemp=非公開作業データ/イナゴ実装試験/copy-quality-final-20261024-03
  ```
- 開始/終了 (JST): 2026-10-24 10:53:45 +09:00 / 2026-10-24 10:56:03 +09:00。実行識別子/隔離領域: `copy-quality-final-20261024-03`。
- ソース版: HEAD `8939ab62fce902a86af54f3562ce37f50fa59dad` と開始時作業ツリー。開始時の `git status --short` にはP1修正を含む `automation/inago/repository.py`、関連実装・試験差分があった。親担当の指示ではこの版はfreeze済みで、実行中にソース変更なし。
- 入力/fixture: 指定9試験ファイルの開始時作業ツリー版、固定clockおよびpytest合成fixture。product-page選択fixtureはfull `tmp_path` 由来digestで領域を分離する修正を含む。
- 期待: 9対象ファイルの全node pass。実結果: 222 collected; 221 passed, 1 failed, 0 skipped。終了コード1。
- 完全失敗node: `tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr`。
- 失敗内容: 初回は意図したfixture interruptionにより `partial`、4 commits、OCR 15回、checkpoint保存を確認。復帰回の期待 `second.status == "complete"` に対し、実測 `"partial"`。完全nodeとassertionはログに保存。
- 原因/限界: 原因はこのrunでは未確定。親担当の事前報告では隔離後の類似partial 1回について直接原因を特定できず、`checkpoint.errors` JSON診断を追加した版で同nodeを1回pass (5.82秒) し、間欠診断5回も全てpass (5.83、5.75、5.69、5.72、6.05秒)。今回の再現は固定版でも残っているため、原因修正済みとは結論しない。親担当は5件の診断を `capture-partial-diagnostic.log` へ集約し、元の5ログを削除したと報告。ログ本文は本試験担当は未確認。
- pytestログ: `検討/投稿文品質改善_2026-10-24/final-regression-20261024-03.log`。
- 実AI品質確認はこのfixture回帰の対象外。`skill-behavior.md` に実AI作成2商品（具体説明ready / 情報不足empty）とレビュー4判定が別記されているとの親担当報告。
- 次の対応: 回帰03を繰り返さず、上記完全nodeとcheckpoint復帰の関連範囲を局所調査する。原因・修正を確認し、影響nodeを局所passさせた後に計画された9ファイルの回帰を最終実行する。

## 最終回帰04（診断出力有効・freeze版）

- コマンド:
  ```powershell
  $env:INAGO_SELECTION_TEST_DIAGNOSTIC_ROOT = 'C:/project/rakuten-room-inago/検討/投稿文品質改善_2026-10-24/partial-checkpoint-diagnostics'
  .venv-inago/Scripts/python.exe -m pytest tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_daily_fix_ai.py tests_inago/test_step08.py tests_inago/test_step09.py tests_inago/test_step12.py tests_inago/test_step13.py -q --disable-warnings --maxfail=5 --basetemp=非公開作業データ/イナゴ実装試験/copy-quality-final-20261024-04
  ```
- 開始/終了 (JST): 2026-10-24 11:00:10 +09:00 / 2026-10-24 11:02:28 +09:00。実行識別子/隔離領域: `copy-quality-final-20261024-04`。
- ソース版: HEAD `8939ab62fce902a86af54f3562ce37f50fa59dad` と開始時作業ツリー。製品コードは03版からfreezeとの親担当指示。今回のskill文面差分は、商品段落で繰り返し商品名を不要とし、取得URL/ID/要素画像の対応で文脈を照合する旨の明確化1行。実行中に変更なし。
- 入力/fixture: 指定9ファイルの開始時作業ツリー版、固定clock、合成SQLite/runtime fixture。診断root環境変数は上記値。実行直後の `Get-ChildItem` 列挙にはJSON 14ファイルがあり、私の手作業の件数記録を誤って15件としていた。親担当による後続整理でも14個を読み取り、`partial-diagnostics-summary.json` に `captured_files=14`、合計14 observations、6 diagnostic groupsとして集約した。個別JSONは整理時に削除済み。集約内容は意図的fixture failure / RuntimeError / cleanup retryに対応し、不測partialを示す観測はないとの親担当報告。
- 期待: 全対象node pass。実結果: 222 passed、0 failed、0 skipped、136.55秒、終了コード0。
- pytestログ: `検討/投稿文品質改善_2026-10-24/final-regression-20261024-04.log`。
- 関連局所確認（親担当からの実測報告。個別コマンド全文/ログ参照は引継ぎに含まれず、本試験担当は独立再実行していない）:
  - `tests_inago/test_product_page_selection.py` の前回失敗2 nodeを各5回（計10実行）、全てpass。各組の報告時間: 12.23、11.54、11.65、11.64、11.54秒。
  - `test_product_page_selection.py` 全9 node: 9 passed、38.05秒、終了コード0。
  - 親担当は `capture-partial-diagnostic.log` へ5回分の診断を集約し、元5ファイルを削除。診断は意図的fixture_copy_failure (15 commit/error 1) と意図的step13_error_RuntimeError (4 commit/error 1) のみだったとの報告。
- 未解決の限界: 03で発生した不測partialの直接原因は未特定のまま。診断計装により生成error、checkpoint.errors、image/unselected cleanup失敗のfunction/lineを保存できる。04の回帰はpassだが、これだけで元のpartialの直接原因を確定・修正したとは扱わない。
- 実AI文章品質の確認は別工程であり、このpytest fixture回帰では評価していない。実AI評価結果は `skill-behavior.md` の記録と区別する。

## 親による証拠の整理

実行時のpartial-checkpoint-diagnostics/*.jsonはpartial-diagnostics-summary.jsonへ集約し、元ファイル名・観測数・原因情報を保持した。個別小JSONは整理済みである。局所診断ログはcapture-partial-diagnostic.logへ集約した。最終回帰のstdoutログ4件は各回の実結果を残すため保持する。

## 診断JSON件数の訂正

- 04直後に `Get-ChildItem` で列挙した個別JSONは14件だった。直前の記録で「15件」としたのは本試験担当の計数誤りで、出力の実件数が15件だった証拠はない。
- 親担当の整理後、`検討/投稿文品質改善_2026-10-24/partial-diagnostics-summary.json` を確認。`captured_files=14`、groups内 observations 合計14、unique diagnostic groups=6。各グループのsource file名もsummaryに保持され、個別JSONは削除済み。
- summaryの6グループは `unselected_folder_cleanup_failed`、意図的な `step13_error_RuntimeError` / `step13_error_DailyOrchestrationError`、`fixture_copy_failure` 等、意図的fixture中断・再試行診断に対応していた。unexpected partialを示す観測はsummary上確認されなかった。直接原因未特定の限界は維持する。
