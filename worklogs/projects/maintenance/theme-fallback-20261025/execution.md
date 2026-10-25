# テーマ語句による不足補充の修正

## 依頼・要件

2026-10-25、本人から「関連語を使い切っても本文5件に不足していればテーマ語句そのもので補充検索。5件揃った場合は補充検索しない。局所テストで確認」と明示依頼された。
正本は要件定義HTML第13章。関連語を順に使い、確定保存本文が5に不足するときだけ、保存済みテーマquery_wordで1回補充する。4本文なら1、3なら2、0なら5を補う。5確定後は余った関連語・補充検索を実行しない。追加テーマ枠はmin(5,15−確定本文数)。同run同query/300条件は再検索せず保存結果を使う。

固定root `C:/project/rakuten-room-inago`、開始HEAD `ac99547`、既存変更なし。本番DB、設定、Schedulerは変更せず、日次全体や昨日runは起動していない。画面問題ではなく検索計画・本文件数・再開の制御を隔離DB/fixtureで試験したため、今回の局所試験ではブラウザー画面・動画・Playwright traceは取得対象外。以前の実サイト表示確認を今回の補充フロー確認へ読み替えない。

## 原因と製品変更

所有製品は `automation/inago/orchestrator.py` と `automation/inago/daily_theme_planner.py` の2ファイル。

1. 関連語ありのplanからfallbackを消すplanner・正規化処理と、その併存を拒む検証が原因だった。関連語の後にテーマ補充を予定し、確定本文数で実行・skipを判断するよう修正。
2. 商品ページ経路では候補選択数を本文数へ含め、15候補まで本文確定を遅らせていた。検索ごとに選択・OCR・本文を確定し、不採用があれば同じ保存結果の次候補を先に処理する。不要候補のcleanupはその後に実施。
3. 再開時は既存task/attempt番号を維持して不足する補充taskを復元。KEY採用0件で旧taskが空でも、処理済みテーマ情報から復元する。plan保存とtask追加の間の中断で二重追加しない。
4. 旧selected未確定・processed検索を再開するときは保存結果の本文処理を先に進め、5確定後に不要な補充検索を発行しない。同一queryの補充は保存結果を再利用し、再利用参照をcheckpointへ保存する。
5. 再利用先テーマを取得失敗記録へ保持し、復旧でも同じテーマへ戻す。複数テーマが同じattemptを再利用した際のcleanupは当該groupだけに限定する。v2移行planのlegacy markerを正規化保存後も維持する。

新しいROOM時間上限・検索回数上限・承認gateは追加していない。複合テーマについては既存仕様の採用済みquery_wordを再利用し、構成語を引き直していない。
新しい常設トレース機能を作ったとは扱わない。既存の商品処理traceを使い、今回追加した保存情報は検索結果再利用のtask/theme/source_attempt参照と、取得失敗時の再利用先themeである。

## 局所試験と修正履歴

共通入口は `.venv-inago/Scripts/python.exe -X utf8 -B -m pytest <完全node> -q --tb=short`。全て隔離fixture・本番ネットワーク不使用。

| 段階 | 対象・実結果 | 終了コード・証拠 |
|---|---|---|
| 最初のRED | 新規10case。0/3/4/5本文×legacy/product-page、候補5→本文不採用、同結果次候補。9 failed / 1 passed | exit1、`red.txt`、checkpoint `9782067` |
| 最初の局所GREEN | REDの既知失敗9caseだけ再実行。9 passed | exit0、`local-failed-nodes.txt`、`local-green-attempt1.txt` |
| 旧空task復元RED | `test_resume_restores_missing_fallback_without_renumbering` と二重追加境界。1 failed / 2 passed | exit1、`resume-red.txt` |
| 復元・旧active境界 | 空KEY復元の失敗nodeと旧active5→不採用→同結果次候補。2 passed | exit0、`resume-green.txt` |
| 旧processed境界RED | `test_resume_processed_related_search_commits_before_deciding_fallback`。不要な補充検索で1 failed | exit1、`processed-resume-red.txt` |
| 関連境界確認 | processed境界・capture復旧・query guard・検索順・OCR順・複合語再開など13 nodes。13 passed | exit0、`boundary-nodes.txt`、`boundary-result.txt` |
| 回復group RED | `test_theme_fallback_capture_recovery.py::test_recovery_of_two_themes_from_one_attempt_keeps_later_group_inputs`。後groupの入力削除で1 failed | exit1、`recovery-group-red.txt` |
| 回復と画像cleanup | 当該2node。画像cleanupはpass、回復は主要条件を満たしたが不要な再capture回数期待でfail。既存captured page再利用で0回が正しいため試験期待を修正 | exit1、`cleanup-local.txt` |
| v2移行・group修正確認 | valid v2旧形式移行、回復groupの2node。2 passed | exit0、`final-local-green.txt` |

新規局所nodeは `tests_inago/test_theme_fallback_replenishment.py` と `test_theme_fallback_capture_recovery.py`。パラメーター名と完全nodeは各ログ、最終JUnitに保持する。
テストコードの独立レビューは実施していない。製品作者とは別担当の製品コードレビューとPythonレビューを `code-review.md`、`python-review.md` に保存する。

## 回帰試験

最初の範囲は `regression-targets.txt`（177case）。コマンドは上記pytestに `--junitxml=worklogs/projects/maintenance/theme-fallback-20261025/regression-attempt1.xml` を追加。
実結果は15 failed / 162 passed、exit1（172.73秒）、`regression-attempt1.txt` / `.xml`。

そのうち、現行schema4・採用構成語・契約例外と試験の旧期待の不一致は、開始版ac99547の2製品モジュールをGit blobから**メモリ内だけ**でロードし、代表4nodeが同じ理由で失敗することを確認した（`baseline-old-expectations.txt`、exit1、4 failed）。別checkout/worktreeや本番DBは使用していない。試験を現行契約へ更新し、実際の不正planを受け入れる変更はしていない。
v2試験入力は本当の旧6fieldsへ修正したうえで製品marker消失を検出し、製品側を修正した（`v2-local-diagnostic*.txt`）。失敗node14とgroup1の局所再実行は1 failed / 14 passed、残るv2とgroupを修正後の2node再実行で合格（`regression-local-fixes.txt`、`final-local-green.txt`）。

画像cleanupの初回回帰1失敗は、直後の局所再実行では合格し原因は未確認。既存のfixture診断保存機能を最終回帰で有効にし、再発時にはチェックポイントの安全なエラー詳細を `private/final-diagnostics/` に保存する。再現なしを原因修正済みとは報告しない。

最終範囲は `regression-final-targets.txt`（上記に新規group試験1を追加、178case）。同じpytest入口で `--junitxml=worklogs/projects/maintenance/theme-fallback-20261025/regression-final.xml` を指定。最終結果は178 passed（175.34秒）、exit0。失敗・skipなし。完全node178件を `regression-final-nodes.txt` に保存した。

## 実行版と証拠

最終製品SHA256:

- orchestrator.py: `f3ac4de9e39251298b4d8cf9e6040673673567b4d4b4532eb41da64b9e9a8cf9`
- daily_theme_planner.py: `6a18de0d44521232d1fa7f3e1b98c8ae3faf6b0f9c7f087c761558ad5c14b7f4`

全コマンド対象・node、結果と終了コードを本記録と各ログで結ぶ。共有用ログの行末空白・JUnitのhost情報は公開前に除去し、未加工出力はprivateへ保持する。試験データと本番の10/24実績を区別する。

## 全体run・反映

本番日次全体runと10/24同一runの再開は未実施。昨日の9本文を増やしたとは報告しない。固定rootの製品差分・試験・報告だけを確認してGit管理し、本人の作業指示どおり現在のmain追跡先へpushする。成功したcommit/remoteは最終応答とprivate実行記録へ保存する。

## 最終検証と自己点検

構文確認（製品2file AST/py_compile）とGit差分検証は合格。最終回帰は178件すべて合格。初回回帰の画像cleanup失敗は最終同一caseでも合格し再現なし、原因未確認という限界は保持する。独立製品/Python/HTML reviewは最終版で未解決指摘0。Black全file整形チェックは既存形式を含み未合格で、広範囲の整形変更は今回行っていない。ruff/mypy/pylintは未導入で未実施。

自己評価は正確性4（最終同一版178件と独立レビューで確認、過去の本番回復は未確認）、完全性4（依頼された不足/達成・再開境界を確認、実日次未実行）、明瞭性4（本文数と候補数を区別、技術詳細は付録）、実行可能性4（完全node/版/終了値とraw非公開保存、原本の共有先閲覧はローカル要）、簡潔性4（利用者向けHTMLと詳しい作業記録を分離）。平均4.0。改善優先は実日次を局所合格と混同しないこと、既存試験の古い期待と製品修正を区別すること、未知原因を解消済みと書かないこと。本人の5件基準を実結果で示せるかを最終確認した。
