# 実行後のWorker終端記録修正

## 対象

- 対象launch: `fa3292ae0c7e43bb9569576fcb1a5d85`
- 対象日: `2026-10-20`
- `source_run_id`: `null`
- 正規保守job: `job-maintenance-c2254aaa9e184dbba8ccd8c625f610d6`
- incident: `51146afb-856d-4eb3-b1b1-b8a290388c3f`
- 追加修正: `automation/inago/maintenance/skill_chain_runtime.py` の `build_skill_chain_workflow_provider` 終端処理
- 証拠: `plan/worker-cli/fa3292ae0c7e43bb9569576fcb1a5d85/process.json` と同directoryの安全化stdout/stderr、正式jobの `calls.jsonl`、同jobの `code-review.md`、`plan/migration/test.md`

## 実10/20実行で見えた不整合

対象Worker CLIは指定launchから一度だけ実行され、プロセス終了コード0で回収された。`process.json` のstdoutに保存されたJSONは `status=maintenance_incomplete`、`reason_code=skill_chain_failed`、`maintenance_resolution_status=unknown` を返していた。

同じ実行の `calls.jsonl` には7種類の担当による9件の `worker_finished` があり、全件 `turn_status=completed`、process return code 0、`error=null`、`close_error=null` だった。一方で `job_finalization_observed` は0件だった。対象PIDは外側CLIを含め事後確認時にすべて終了していた。

秘密値を出力しない読取専用DB確認では、正式jobは `complete`、queueも `complete`、既存checkは元の `incomplete` のまま、Daily `run` の2026-10-20件数は0だった。Worker CLIのoperation resultと保存済みjob/queue状態は別の観測値として記録する。

## 原因と修正

`build_skill_chain_workflow_provider` はterminal結果を保存した後、`job_finalization_observed` を追加する辞書内で、そのclosureに存在しない `identity` を参照していた。DB finalization後のこの例外が外側catchへ入り、正常終了した担当列に対してCLIが汎用 `skill_chain_failed` を返していた。

provider内で、正式受付が返す `job.job_id` とlaunchのsource ID、run ID、target dateから `identity` を一度作り、terminal inspectionと終端記録の両方へ渡すよう修正した。既存job、queue、check、Daily記録は再処理・更新していない。日次の10/20根本原因は引き続きunknownで、この修正はcontrollerの終端記録不具合を直すものである。

## 修正後の試験と独立レビュー

親controllerの修正後focused実測:

```powershell
& 'C:\project\rakuten-room-inago\.venv-inago\Scripts\python.exe' -B -m pytest tests_inago/test_maintenance_skill_chain.py::test_workflow_provider_records_finalization_after_job_is_finalized tests_inago/test_maintenance_skill_chain.py::test_worker_cli_admits_target_launch_without_lease -q
```

期待: providerがfinalization観測を同じidentityで記録してcompleteを返す。隔離SQLite CLIの incomplete runnerでは実際の終端reasonを保持し、queue/check/jobの状態を分ける。実結果: 2 passed、pytest終了コード0。JUnitはこの短いfocused実行では作っていない。完全node・期待/結果は `plan/migration/test.md` の追記を参照。

追加修正後のSTEP02最終関係回帰 `C16-migration-06` は23 passed、失敗/エラー/skip 0、終了コード0。完全commandとnode、JUnit・console・process証拠は `plan/test-evidence/C16-migration-06.*` と `plan/migration/test.md` に保存。

作者と別のreview担当がproduct sourceをread-only確認し、この修正に所見なしと記録した。レビュー記録は正式jobの `code-review.md`。レビュー時点では修正後試験結果がまだ届いていなかったため、試験結果は後から追加された `test.md` の実測を採用する。

## 適用範囲・再開位置

修正は唯一の固定workspace `C:/project/rakuten-room-inago` の実行コードに直接適用済みで、修正後focused試験とC16最終関係回帰を通過した。対象job/queueはすでにterminal、7担当のturnと所有processも終了済みのため、10/20の7担当を再実行してoperation resultを後から書き換えることはしていない。初回Worker CLIのJSONが `maintenance_incomplete` だった事実は保持する。Daily runは0件のままで、通常Daily・queue再処理・新しいlaunch・下書きは作っていない。

次の同種runから修正済みproviderを使用する。今回の対象について追加stageの再開は不要であり、再開が必要な場合は同じjobから終端記録箇所だけを対象とする。Git stage/commit/pushは本人の明示禁止に従い実施していない。
