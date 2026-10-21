# 2026-10-21 日次の実行・修正記録

## 範囲と開始状態

- 本人依頼：今から実日次を起動し、失敗原因を調査・修正して完走を確認する。
- 固定作業場所：`C:/project/rakuten-room-inago`。開始時 `git status --short` は空。ブランチ `main`、追跡先 `origin/main`。
- 運用DBの初期化・置換・手動補正はしていない。日次プログラム自身のtransaction、バックアップ、観測保存、復旧入口を使用する。
- 時計の差替え・日付の偽装はしていない。午前中の本人指定起動を `daily --manual` で実装した。通常の13時起動は既定値を保持。

## 観測と原因

1. 19日・20日の開始準備は `IdempotencyConflict`。保存済み設定と現在設定の内容は同じで、import理由中の `settings_path` だけが文書移動前後で異なった。正確な旧パスは `plan/イナゴスキル事前準備/初期設定引渡し.json`。この既知の移動だけを比較時に正規化し、既存行と作成日時は維持。他の設定内容や確認根拠の不一致は引き続き検出する。
2. 21日の実行では、関連欄を検出した後に `query_widget_contract_mismatch`。描画途中で高さ0の欄を表示済みと扱うと同じ失敗を再現できた。実画面で失敗した瞬間の寸法は未保存なので、高さ0が実際の直接原因だったとの断定はしない。寸法判定を揃え、判定間の再描画は既存期限内で再観測する。
3. 修正後の同一run再開では、保存済みwidgetエラーが新しい取得を永久に禁止していた。別invocationでは元のacquisition・試行番号・5回予算を保持して再開する。以前の失敗履歴は削除しない。成功時と枯渇・期限到達時の履歴検証もこの限定的な再開を扱う。
4. 実機で `thread/resume` が `no rollout found for thread id ...` を返した。初回AI turn前の検索失敗ではthreadだけが作られ、rolloutがまだ作られていなかった。初期contextを推論なしで保存する。既存の未使用rootに限って、明示された履歴不存在を分類し、旧IDを記録して復旧する。使用済みAI jobの会話を別会話へ差し替えない。
5. 実機で `thread/start → thread/inject_items → close → thread/resume` を実施し、同じthread IDで再開成功を確認した。API keyの読取り・設定変更はしていない。根拠となるRPCは[公式文書](https://learn.chatgpt.com/docs/app-server)と、使用中binaryが生成したschemaで照合した。
6. 実接続でGoogle Trendsの429応答を観測。外部側の制限理由は不明。プログラムの60秒以上の待機と既存5回予算を適用する。TrendsがROOM50商品用の17100秒枠を継承し得たため、Trendsだけ1回120秒以内にした。ROOMの枠・通常起動時刻・日次件数は変更していない。
7. 18日の保存済み観測3件はすべて、最初の商品詳細後の `room_product_modal_close_error / room_search_restore_failed`。現行実装には当時の観測にないEscape復帰処理が既にあるため、今回その修正を行ったとは扱わない。今回の完走した実日次では同じ検索停止エラーは発生しなかった。
8. 実際の紹介文生成が `JOB_INPUT_UNREADABLE` で失敗した。Pythonは同一bytesのjob JSONをファイルとpromptの両方へ渡していたが、AI rootがファイルの再読取りを必須と解釈し、sandbox拒否で失敗していた。届いたinline JSONを正本として使う指示に修正した。job識別子・hash照合・子agentへの委譲は維持。修正後、実際の紹介文生成と下書き保存が成功した。
9. 所有を確認した日次processを修正版へ切り替えた後、正常なcommitted WALが残っているだけで `database_recovery_pending` になった。起動前の読取りをWALがある場合は `mode=ro` とし、保存済みWALを含む最新状態を検証する。rollback journalは引き続き拒否する。WALの設定版不一致を検出する試験も追加し、main DB bytesが不変であることを確認した。
10. `--cause-resolved` が復旧計画にしか渡らず、修正前のエラーが残り続ける問題を修正。明示指定された同一runの再開時だけ、run lock内で過去のactive errorを日時付き `resolved_error_batches` へ退避する。生成・検索の失敗履歴は保持し、新しいエラーは引き続きactiveとして数える。既に15件ある場合も再生成せず処理する。
11. scheduler回帰試験で、正常なHumanApi定義も旧定義として再登録する不具合を確認。現行定義一致を除外した。実schedulerの定義は読取りだけ行い、日次launcher・Ready・時間上限なし・WakeToRunを確認した。実schedulerの再登録はしていない。
12. ROOM第2検索は20件を約482秒で取得しNodeはexit 0だったが、親の480秒deadlineが先に到達し `room_request_timeout` として全結果が不採用になった。実機では商品ごとに約22秒かかっていた。50件を読むrequestの有限上限を1800秒へ変更し、485秒の正常取得を再検索・取消なしで採用する再現試験を追加した（RED `f7d9283` → GREEN）。既存の期限超過・遅延結果不採用・取消未確認の2試験も、固定480秒でなく実際の上限定数までclockを進め、対象計3件PASS。元の失敗した検索結果を手動で採用していない。
13. その後、本人から「上限自体撤廃」と明示指示。12の1800秒案は撤回した。ROOM requestのdeadlineをNoneとし、日次のROOM親timeout wrapperを削除。期限なしROOMに限ってPython sourceの子process待機とNodeの全体deadlineも撤廃した。Trends・AIと個々の画面操作の設定は維持。485/1801/17101秒の観測を再試行・取消なしで採用する3試験、実browserでsource設定1秒を超える商品読取り、期限生成の試験が計5件合格。期限なしを含む明示cancelの2試験も合格。旧ROOM全体期限専用の2試験は本人指定の仕様変更で廃止し、その証拠は過去commitに保持する。

## 操作上の訂正と未実施

- 最初のlauncher呼出しは相対パスで設定検証が失敗した。絶対パスに訂正すると検証は成功し、午前中なので `not_due` となった。相対パスの失敗を製品障害と扱わない。
- 最初のpytest呼出しはfixture領域外でsetup失敗。以後 `--basetemp=非公開作業データ/イナゴ実装試験/<実行名>` を指定。setup失敗はREDの証拠に使わない。
- 旧パスを最初に読み違えていたため、実機再実行で残った衝突からUnicode escapeで正確な文字列を照合し、再現試験・製品の両方を訂正した。
- 検索worker PID 79148の個別終了を検討したが、本人向け進捗後の所有確認時には既に自然終了していた。所有確認条件が成立せず終了操作は未実施。日次親process・他browser・通常schedulerは停止していない。
- 言語設定参照先 `%USERPROFILE%/.codex/settings/language.md` は存在せず読めなかった。日本語の本人依頼に従い日本語で対応。
- inline入力修正の反映時、ownerファイルの `pid`、run ID、command lineで所有を確認した日次PID 87348とその子だけを制御終了し、同じrunを再開した。誤ったownerキーを参照した最初の確認は条件不成立で終了操作を行っていない。実行結果は `controlled-restart.txt`。他の日次・browser・schedulerは停止していない。
- ROOM時間上限撤廃の明示指示後、旧上限を読込済みの日次PID 110304について同じ所有確認を行い、所有する子processとともに終了。同じrun・5件の下書き・設定・seed・検索履歴を保って修正版を再開した（`unbounded-room-restart.txt`）。中断した検索はプログラム自身が `process_interrupted` として保存し、残る既定の再試行を実行する。手動で試行数を増やしていない。
- 25件への変更依頼後、2テーマ10下書きの区切りで日次PID 87300の所有を同様に確認し終了。第3テーマのTrends途中記録を保ち、その後の再開では残りの既定試行だけを使う（`row-limit-restart.txt`）。

## 再現・対象試験

共通prefix：`.venv-inago/Scripts/python.exe -B -m pytest`。各実行は `-q --basetemp=非公開作業データ/イナゴ実装試験/<個別名>` を付けた。正確なnodeは以下。RED/GREENのstdoutは同directoryの対応txtへ保存した。

| node | 修正前 | 最終対象試験 | 証拠 |
|---|---|---|---|
| `tests_inago/test_step04.py::test_reimport_after_document_move_preserves_existing_settings` | exit 1、IdempotencyConflict | exit 0 | settings-exact-red/green.txt |
| `tests_inago/test_step04.py::test_document_move_does_not_hide_changed_settings_provenance` | 期待どおり不一致検出 | exit 0 | settings-exact-green.txt |
| `tests_inago/test_daily_fix_pipeline.py::test_manual_production_daily_runs_before_scheduled_hour` | exit 1、未実装引数 | exit 0、15件 | manual-red.txt / target-green.txt |
| `tests_inago/test_daily_fix_trends.py::test_related_widget_waits_for_nonzero_layout` | exit 1、実障害と同じエラー | exit 0 | widget-red/green.txt（greenのもう一件は当時fixture期待値失敗、合格扱いしない） |
| `tests_inago/test_step13.py::test_fixture_cli_daily_requires_explicit_context_and_respects_not_due[True]` | exit 1、manual無視 | exit 0 | manual-fixture-green.txt。fixture供給不足のshortfall/CLI 5を正しく確認 |
| `tests_inago/test_step21_trends_retry.py::test_restarted_widget_acquisition_preserves_failed_attempt_and_resumes` | exit 1、再開禁止 | exit 0 | acquisition-red.txt / resume-green.txt（この時の他node失敗は合格扱いしない） |
| `tests_inago/test_daily_fix_pipeline.py::test_manual_daily_resumes_corrected_widget_before_scheduled_hour` | exit 1、復旧時刻・履歴検証 | exit 0、同一run15件 | resume-red.txt / resume-final.txt |
| `tests_inago/test_step21_ai_root.py::test_daily_root_initial_context_is_persisted_without_inference` | exit 1、保存RPCなし | exit 0 | ai-persist-red/green.txt |
| `tests_inago/test_step21_ai_root.py::test_unused_missing_root_is_replaced_with_preserved_provenance` | exit 1、rpc_error | exit 0 | ai-persist-red/green.txt |
| `tests_inago/test_step21_trends_retry.py::test_widget_repair_then_exhaustion_allows_verified_theme_fallback` | exit 1、履歴検証で停止 | exit 0 | fallback-red/green.txt |
| `tests_inago/test_daily_fix_trends.py::test_trends_request_has_its_own_finite_timeout` | exit 1、17100秒継承 | exit 0 | budget-red/green.txt |

初期再現試験のコミットは `f74d4cf`、最初のGREENは `f0a9586`。追加再現は `7f3f061`, `55f2eb3`, `0526fbc`、修正は `89f046c`。修正前後の失敗理由と証拠は各txtで保持する。

## 実日次コマンド

```
.venv-inago/Scripts/python.exe -B -m automation.inago.maintenance.daily_launcher --config C:/project/rakuten-room-inago/rakuten-room-inago/runtime/config/settings.json --data-root C:/project/rakuten-room-inago/rakuten-room-inago/runtime
.venv-inago/Scripts/python.exe -B -m automation.inago.cli daily --manual --environment production --config C:/project/rakuten-room-inago/rakuten-room-inago/runtime/config/settings.json --data-root C:/project/rakuten-room-inago/rakuten-room-inago/runtime
.venv-inago/Scripts/python.exe -B -m automation.inago.cli daily --manual --resume-confirmed --cause-resolved --environment production --config C:/project/rakuten-room-inago/rakuten-room-inago/runtime/config/settings.json --data-root C:/project/rakuten-room-inago/rakuten-room-inago/runtime
```

各再開は同じ `INAGO-2026-10-21` を使用。既存の認証・設定を利用し、投稿操作は行わない。生stdout、観測、DBはgitignore対象の非公開領域に保存し、共有文書には件数・状態だけを記載する。

## 独立レビュー

- code-reviewer：fixture CLIのmanual伝播漏れ、枯渇時の履歴検証漏れを指摘。両方を再現・修正した。
- python-reviewer：追加の重大指摘なし。今回の差分行をblackで整形した。Trends120秒上限と元設定不変を再確認しApprove。
- 実機永続化についてはfake serverのACKだけで成功としない。実app-serverの同一ID再開で確認済み。

## 回帰試験・実日次の最終結果

### 機械検証

- 初回回帰：8 failed / 295 passed / 1 skipped、exit 1。6件は既存scheduler試験の旧起動定義期待値、2件は実際のHumanApi再登録不具合。対象を修正し、schedulerの対象12件はexit 0。
- 第2回回帰：303 passed / 1 skipped、300.65秒、exit 0。その後の実日次でinline入力・WAL・過去エラー問題が見つかったため、追加の対象試験を修正前後で実施。
- 第3回回帰：**307 passed / 1 skipped、311.00秒、exit 0**。`regression-round3.txt/xml`。未実行は `tests_inago/test_daily_fix_scheduler.py::test_n30`（専用の通常権限Windows task内だけで実行する条件付き試験）。未実行を合格に含めていない。ROOM時間上限撤廃の前の版なので最終版の検証とは扱わない。
- 第4回はROOM期限延長の際、step11の旧deadline期待値が失敗し、対象調査へ戻るため中断。最初の終了操作はPython親子2個を単一processと期待した所有条件に合わず未実施。正しい親子関係で所有確認後に終了した。終了確認前に当該試験の期待値を訂正したため、その実行は版固定の最終証拠に使わない。
- 第5回は `test_fourteen_committed_bodies_resume_same_run_seed_and_search_results` の最初の結果がpartialでなくcompleteとなり、84 passed / 1 failed、exit 1。直前の強制中断で同名fixture DBが残った影響が疑われる。fixtureはtmp_path.nameでDB領域を決めるため別実行と同じ場所になる。この不一致を合格扱いせず、cleanup後の同nodeは第6回で合格を確認した。第5回も最終版の証拠ではない。
- **第6回（25件化前）：320 passed / 1 skipped、315.79秒、exit 0**。`regression-round6.txt/xml`。14件からの再開nodeも合格。途中終了した第4回とは版を分け、source `db0f547` と `source-hashes-round6.json` の全hash一致を確認。その後の25件化を含む最終結果は第9回を使用する。
- 第6回時点のcoverage：対象11moduleで4124/6192文（66.6%）。当時の変更文は **67/71文、94.4%**。`coverage-round6.txt`、`changed-coverage-round6.json`。JavaScript・PowerShellはPython coverageに含めない。
- 時間上限撤廃の4層をcode-reviewerとpython-reviewerが独立確認しApprove。Python reviewerの変更行整形指摘はblack再確認（対象5files unchanged）で解消した。
- 25件への変更後、第7回は214 passed / 1 failed、exit 1（旧50件期待の `test_fifty_source_rows_are_saved_but_only_five_per_theme_are_assigned`）。実結果25件は新仕様どおりで、試験名・期待値を25件へ更新し対象1件PASS。
- 第8回は424 passed / 1 failed / 1 skipped、326.87秒、exit 1。既存 `test_n20m_ai_cancel_confirmation_is_reset_for_each_job` のrequest fixtureに `input_payload` がなく、既存製品処理のhash作成でAttributeErrorになった。同型の `test_n20p_ai_cancel_confirmation_is_job_scoped_after_request_identity_change` とともに入力を補い、対象2件PASS。製品側のAI timeout処理は変更していない。code-reviewer承認済み。
- **最終第9回：448 passed / 1 skipped、362.20秒、exit 0**。`regression-round9.txt/xml`。専用通常権限taskだけで行うN30は未実行のままであり、合格に含めない。正確なコマンドは `regression-round9-command.txt`。`source-hashes-round9.json` 全hash一致、source/test treeは `827b17e`。
- 最新coverage：対象13moduleで5426/7934文（68.4%）。今回変更したPython実行文は **89/93文、95.7%**。`coverage-round9.txt`、`changed-coverage-round9.json`。JavaScriptとSQLはPython coverage対象ではなく、実browser fixture・構文検査と、隔離移行試験・本番移行照合で検証した。
- 第3回時点の対象sourceは `c27528f`。`source-hashes-round3.json` の全hashは当時の検証後も一致。検証後のHEADに他タスクの文書コミット `21d4ee6` があったが、その時点で対象sourceの変更はなかった。
- 第3回時点のPython対象10moduleの総coverageは64.5%（既存未通過箇所を含む）。当時の変更実行文は **56/61、91.8%**。`coverage-round3.txt`、`changed-coverage-round3.json`。変更行は `git diff --unified=0 f74d4cf^ -- <file>` の追加側範囲を、coverage JSONの実行文と交差して集計。
- 差分行black、JavaScript `node --check`、PowerShell parser、`git diff --check` を確認。未導入のruff・mypy・pylintは実行していない。
- 共有JUnit XMLの `hostname` だけを `LOCAL-REDACTED` へ置換。試験名・期待/実結果・時間・失敗・件数は保持。変更前のhostname入りXMLは残していない。対応stdoutは併記したtxtに保持する。
- 最終ステージ時に試験出力の引用行へ末尾空白を検出したため、共有txt/XMLの行末空白だけを除去した。試験の実行内容・判定・件数は変更していない。要件HTMLの冒頭件数カードも現行25件へ更新した。
- code-reviewerはschedulerの修正を承認。python-reviewerはinline入力、WALの読取り、新規エラーを維持する復旧履歴の修正を承認し、追加の重大・中程度指摘なし。

第3回回帰の正確なコマンド：

```
.venv-inago/Scripts/python.exe -B -m coverage run --data-file=非公開作業データ/daily-live-coverage-round3 --source=automation.inago.settings,automation.inago.cli,automation.inago.production_runtime,automation.inago.orchestrator,automation.inago.trigger,automation.inago.recovery,automation.inago.trends_adapter,automation.inago.daily_ai,automation.inago.app_server,automation.inago.live_sources -m pytest tests_inago/test_step04.py tests_inago/test_step13.py tests_inago/test_step21_daily.py tests_inago/test_step21_ai_root.py tests_inago/test_step21_trends_retry.py tests_inago/test_step23_12_daily_e2e.py tests_inago/test_step24_production.py tests_inago/test_daily_fix_pipeline.py tests_inago/test_daily_fix_trends.py tests_inago/test_daily_fix_scheduler.py -q -rs --basetemp=非公開作業データ/イナゴ実装試験/regression-round3 --junitxml=worklogs/projects/maintenance/daily-live-20261021/regression-round3.xml
```

第6回の正確なコマンド：

```
.venv-inago/Scripts/python.exe -B -m coverage run --data-file=非公開作業データ/daily-live-coverage-round6 --source=automation.inago.settings,automation.inago.cli,automation.inago.production_runtime,automation.inago.orchestrator,automation.inago.trigger,automation.inago.recovery,automation.inago.trends_adapter,automation.inago.daily_ai,automation.inago.app_server,automation.inago.live_sources,automation.inago.room_adapter -m pytest tests_inago/test_step04.py tests_inago/test_step11.py tests_inago/test_step13.py tests_inago/test_step21_daily.py tests_inago/test_step21_ai_root.py tests_inago/test_step21_trends_retry.py tests_inago/test_step23_12_daily_e2e.py tests_inago/test_step24_production.py tests_inago/test_daily_fix_pipeline.py tests_inago/test_daily_fix_trends.py tests_inago/test_daily_fix_scheduler.py tests_inago/test_step21_browser.py::test_room_without_deadline_ignores_source_duration_limit tests_inago/test_step21_browser.py::test_cancel_terminates_owned_process_and_returns_no_candidate -q -x -rs --basetemp=非公開作業データ/イナゴ実装試験/regression-round6 --junitxml=worklogs/projects/maintenance/daily-live-20261021/regression-round6.xml
```

### 実日次

`INAGO-2026-10-21` は **2026-10-21 11:21:14 JSTに完了、CLI exit 0**。下書き15件、不足0件、現在のerror_count 0。集計observed、export ready、終了backup ready、月次restore確認already_checked。投稿操作は行っていない。DBはschema7、integrity_check ok、foreign_key_errors 0。最終状態は `final-run-evidence.json` に保存した。

| 段階 | 検索attempt | 取得枠 | observed / loaded | eligible | 主な除外 | 下書き |
|---|---:|---:|---:|---:|---|---:|
| 初期1 G01-T01 | 1 | 50 | 20 / 20 | 19 | 重複1 | 5 |
| 初期2 G02-T03 | 4 | 50 | 20 / 20 | 18 | 重複2 | 5 |
| 初期3 G03-T06 | 5 | 25 | 5 / 5 | 2 | 重複2・非商品1 | 2 |
| 追加1 G01-T02 | 6 | 25 | 1 / 1 | 1 | なし | 1 |
| 追加2 G02-T01 | 7 | 25 | 20 / 20 | 18 | 重複2 | 2 |

成功した5テーマで取得66行、eligible58行、下書き15件。初期3テーマの実下書き12件と追加2テーマの3件で15件に達した。最終evidenceのinitial_allocation_count 15 / supplemental_allocation_count 5は割当計画の値で、実下書き件数ではない。最初の2テーマは変更前の50件枠、残る3テーマは25件枠であり、旧記録を書き換えていない。今回は画面結果が25件未満のため、本番の時間短縮量は測定できていない。画面に30件ある隔離browser fixtureでは25件までの詳細取得を確認した。

Google Trendsの429は今回の各テーマでも発生した。既存5回の試行予算・60秒待機後のテーマ語によるROOM代替検索で処理を完了した。外部制限自体を解消したとは扱わない。

過去のROOM timeoutと制御中断によるsource_failures 2件、およびinline入力修正前の制御終了時に残ったgeneration_attemptのin_progress 1件を履歴として保持した。この生成試行は現在動作中ではなく、後続成功を確認できない過去の未確定記録である。今回の調査で共有trace枠に達し、traceはdegraded / quota_already_reached。生観測と最終phase診断は保存されているが、全操作のtraceが揃ったとは表現しない。実障害時widget寸法は不明のままなので、再発時は該当瞬間の寸法と表示判定を追加採取する診断候補として残す。

完了後、resume/cause-resolved指定なしの同日 `daily --manual --environment production` を再実行し **exit 0**。下書き行・本文hash、検索attempt 7件、生成attempt 20件、AI job 38件がすべて不変で、検索・生成・下書きの再実行がないことを確認した（`idempotency-result.json`）。実行processとapp-serverは終了済み、ownerファイルは残っていない。

最終HTMLは `reports/incidents/2026-10-21/manual-daily-live.html`。症状・原因確度・修正・追加記録の4点、5テーマの件数、完走・再実行不変、試験数、未確定履歴とtrace制約をcode-reviewerが証拠と照合してApprove。headless browserで表示し、横はみ出しなしを確認、画像も目視確認した。共有worklog・報告の秘密情報／個人情報検査で該当なし。

## 検索件数と除外後の候補数についての本人確認

本人から、将来取得件数を減らす判断に使うため、各検索の取得件数と重複・投稿済み除外後の件数が残るか質問あり。この質問時点は50件だった。その後、本人が25件へ変更するよう指定したため、以下の追加変更を反映した。

- `search_attempt`：検索のattempt ID、段階、取得状態、`max_posts`、`loaded_posts`、`observed_posts`、終了理由・エラー。
- `source_post`：検索ごとの商品行と取得順 `order_index`。
- `candidate_decision`：検索に結び付く商品ごとの主な除外理由・副理由。判定stageが `daily-search:<attempt_id>` の行を集計すると候補数と理由別件数が分かる。別stageの同一性確認を二重に数えない。
- 今回の最初の検索：取得20件、同一商品の重複1件、eligible19件、下書き5件。時間切れ・中断した検索の取得件数0を「検索結果0件」と混同しない。
- 取得順と当時の判定から、取得50件の検索について先頭30件の候補数を後から比較できる。後続商品の内容そのものを30件版でも取得できたと扱うことはしない。

本人から、最初の3テーマで15件に満たない場合の追加段階の上限も質問あり。現設定を読取り、有効テーマ30件を確認。未使用テーマを1つずつ追加するため、15件未達かつ検索を続けられる状態なら最大30テーマ（追加27テーマ）。1テーマ内の関連語検索・再試行はこのテーマ数とは別。15件到達時またはテーマ枯渇で終了する。情報源の拒否などによる停止条件も既存どおり適用される。

## 本人指定の追加変更：50件から25件へ

- 新しい日次検索の先頭取得枠は `DAILY_SEARCH_POST_LIMIT=25`。重複・投稿済みなどを除外した後に26番目以降で枠を埋め直さない。画面にまとめて30件表示されても詳細取得・候補判定は先頭25件まで。
- 取得上限 `max_posts`、画面読込数 `loaded_posts`、採用した行数 `observed_posts`、商品別の主・副除外理由を保存する。将来、25件中の採用率が下がった場合に本人の判断で増やす。自動増加処理は追加していない。
- 旧DBには `CHECK(max_posts=50)` があったため、schema7の正規migrationで1〜50のrequest枠を保存可能にした。旧50件の行や全20列、自己参照・参照先・下書きは保持。新規日次の発行値は25とし、旧記録の読取りのための50許容と区別する。
- code-reviewerの指摘により、通常dailyが自動migrationしないことを確認。本人指定変更の一度きり操作として `upgrade_search_budget.py` を作り、正規 `repository.initialize_database` APIで移行する手順をレビューした。
- python-reviewerの指摘により、未知の追加列を落とす可能性を再現。既知DDLの `PRAGMA table_xinfo` 全列構造と照合し、通常列・生成列を含む未知構造は書換えず拒否するよう修正。`limit25-column-red.txt` / `limit25-generated-red.txt` → `limit25-guard-green.txt`。カスタムindexなども拒否する。
- `row_limit` 終了根拠のtrace許可漏れも両reviewerが確認し、Node・Python source・共通traceの3層へ追加。旧 `first_50` は履歴用に保持。
- 移行前に既存backup CLIをschema6のまま実施し `ready`、exit 0。`daily-final-20261021-c2b91a59d015461bb8e9269effa180d7`。その後RunLock内でbackupと重要5tableを照合し、正規migrationを実行。移行前後でschema_migrationを除く全tableの件数・行hashが一致、integrity_check=ok / foreign_key_errors=0 / schema6→7、exit 0。`schema-upgrade-result.json`。
- backup CLIが終了済み日次のowner metadataを消すため、実観測済みの元ownerを `stopped-owner.json` へ記録。同じbyte lock内で元PID dead・子0・同run running/10下書き・現在ownerが移行script自身であることを確認し、元metadataを原子的に復元。架空のownerやDB状態を作らず、復旧が確認できる状態を保った。fixtureで同時lock拒否とrelease後の元owner保持も確認。
- 移行scriptは両reviewerが実行前Approve。その後、同じ `INAGO-2026-10-21` を再開し、残りの商品検索を25件上限で実行して下書き15件まで完走した。

移行前backupコマンド：

```
.venv-inago/Scripts/python.exe -B -m automation.inago.cli backup --environment production --config C:/project/rakuten-room-inago/rakuten-room-inago/runtime/config/settings.json --data-root C:/project/rakuten-room-inago/rakuten-room-inago/runtime
.venv-inago/Scripts/python.exe -B worklogs/projects/maintenance/daily-live-20261021/upgrade_search_budget.py
```

通常dailyの自動migrationやDBの手動SQL修正は行っていない。移行後の通常daily再開が、保存済み10件の続きから進むことを確認した。

## 自己評価

agent-self-evaluationの5軸で確認した。平均 **4.0 / 5**。

| 軸 | 評点 | 根拠・残る改善点 |
|---|---:|---|
| 正確性 | 4 | 実runの終了、DB件数、同日再実行hashと版固定の448件合格で確認。widget実寸法・外部429の直接原因は未確認と明記した。 |
| 完全性 | 4 | 実行・修正・25件化・上限撤廃・集計出力backup・独立レビューまで実施。専用通常権限taskのN30は未実行として残した。 |
| 明瞭さ | 4 | 平易なHTMLと検索別件数表で判断材料を提示。実行記録は監査のため長く、歴史的結果と最終結果を明記した。 |
| 実用性 | 4 | 修正を実運用で検証し、取得枠と除外理由を保存。将来の枠増加には保存データの期間集計と本人判断が必要。 |
| 簡潔さ | 4 | 最終報告は成果と変更・検証・Git先に絞る。再現と失敗履歴は別記録に保持した。 |

今後の改善候補は、専用通常権限環境でのN30確認、widget障害再発時の寸法採取、本人が件数増加を判断するときの採用率集計。未実施を今回の成功に含めない。本人が求めた「実際の完走」「上限撤廃」「25件化」と記録確認に対応しており、将来の外部サービス正常性まで保証する表現は避けた。

## Git反映

対象は今回所有する製品・試験・2文書・対応報告・この作業記録だけ。既存の他タスク文書commitは含め直さず、現在のmainからorigin/mainへ通常pushする。製品・試験の最終版は827b17e、最終報告・記録を追加commitしてpushした結果のcommitと追跡先を本人向け最終報告に記載する。
