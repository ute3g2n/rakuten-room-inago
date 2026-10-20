# resume 27 最終照合

- 対象は10/17の同一job・同一launch、`source_run_id`は`null`のまま。resume countは27、Workerはexit 0／`maintenance_complete`、DB quick_checkはok。
- 最新workflow `workflow-80ea6993227c4c6f9c3efc96b730ac3d` は`complete`、jobは`complete`、元incidentの解決状態は`unknown`。全8 stage observationと全handoffを終端が再読し、missing stage/handoff/outputとrunning attemptは0。planning・repair・deploymentは成功を装わず`unknown`として具体的理由と引継ぎを残した。
- この実行のoperation eventは16件で、全件のsource fingerprintは`maintenance-source-v1-38ce77b9dfff5d589ec8fe1e73c38983f31e5740d869b18219d6ca912b3d2994`。失敗3工程のhandoffには操作、reason、correlation ID、例外連鎖、capture location、stack、入力／成果物参照がある。
- 最終reportはcontroller fallback生成（reporterは起動済み）、独立reviewer `rr-inago-incident-report-reviewer` がversion `fallback-revision-8b0283aef941d24922fa` / SHA `86de30baeb3de58a11de7290b08307d26a8ba93fc623f5cdb6fe2e342382b32e` をapproved・指摘0件。公開commit `fa6c78a64f2242a7f2a65ce3bb63ddd3e275494d` のHTML SHAも一致し、公開bytesは21972。
- 10/19 queueはqueuedのまま、queue event 0、10/19 daily launchは既存failed行のまま。10/17 daily launchの新規作成なし。
- 独立read-only受入照合：FC-A01〜09 complete、FC-A10 partial。適用runtimeは`204939e8dbca217489278e0133ea86ca35885def`だが、最新の正式deployment ledgerは`22d68761cc47e089bfc2ed59dc9eadc4e69126ee`。よってこの時点では計画を完了扱いにしない。

## 参照

- `same-job-resume-27-worker.json` / `same-job-resume-27-worker.console.txt`
- `same-job-resume-27-cli.json` / `same-job-resume-27-final-readback.json`
- `runtime/workflow-terminal/job-historical-9e7f0c175cc4c874c955a5dab29c4427/workflow-80ea6993227c4c6f9c3efc96b730ac3d.json`
- 同一attemptのresult、final HTML、review、publication sidecar
- `runtime/maintenance/state.sqlite` read-only snapshot

## 読取照合での操作失敗と回復

- `maintenance_job.source_launch_id`を直接読む最初の照会は列がなく失敗。`job_input_manifest`とのjoinに変更。DB書込なし。
- `daily_launch.reason_code`を読む照会も列がなく失敗。`PRAGMA table_info`で列構造を確認し、実在するstatus/source_run/exit/completed列へ変更。DB書込なし。
- handoff読取ではruntime rootを一段重ねた相対pathで`FileNotFoundError`。正しい固定root内pathへ修正。書込なし。
- いずれも最終readbackは正常取得できた。

- 追加のread-only照合回復：`git rev-list HEAD...@{u}`はPowerShellの`@{}`解釈でparser errorとなりGitは実行されなかった。安全な引用付きupstream照会と`git ls-remote --heads origin main`で回復し、local HEADとremote mainが`fa6c78a64f2242a7f2a65ce3bb63ddd3e275494d`で一致と確認。
- official runtime applyの初回起動はexecの待機時間上限内に結果を返さず、二重適用を避けて再実行しなかった。保存済み`runtime-apply-204939e-readback.json`とswitcherのactive/readiness読戻しで同じ適用commitの成功を確認。
- staged diff検査の初回`git diff --cached --check`はconsole artifact末尾の余分な空行を検出（exit 2）。最後の改行だけを残して修正し、該当ファイルだけ再stageする。
