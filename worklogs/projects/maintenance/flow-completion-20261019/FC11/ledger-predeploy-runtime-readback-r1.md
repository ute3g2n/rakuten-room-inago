# 台帳・runtime正式適用前readback r1

- 固定root: `C:\project\rakuten-room-inago`。branch `main`、upstream `origin/main`。HEAD / origin/main は `08794e0f14c0296fbdfa7a7973e8c90ea44a2a06`、clean。
- 前回switcher確認commit `204939e8dbca217489278e0133ea86ca35885def` からHEADまでの変更は 11 paths。application/policy paths（`automation/`、`.agents/`、`.codex/`、`docs/standards/`）の差分は 0 paths。現行コードとレビュー済みコードは同じ。
- Scheduled Taskの実行rootは固定repo root。`current_commit()`は `08794e0f14c0296fbdfa7a7973e8c90ea44a2a06`、HEADでのhealthは `True`。一方switcher state fileはactive `204939e8dbca217489278e0133ea86ca35885def`、pending nullのまま。今回状態fileを書き換えたのではなく、実行rootのGit HEADと状態記録のずれをread-onlyで発見した。
- DB schema 16 / quick_check `ok`。正式ledger最新行はrelease `U23-REPORT-MD-20261019-7a90002` / active `22d68761cc47e089bfc2ed59dc9eadc4e69126ee`。同一jobは`complete`、resume 27。10/19 queueは1件、queue event 0。daily owner active=False、pending human action count=0。
- 同一jobの最終HTMLは`fallback-revision-8b0283aef941d24922fa`、SHA `86de30baeb3de58a11de7290b08307d26a8ba93fc623f5cdb6fe2e342382b32e`、review approved、公開commit `fa6c78a64f2242a7f2a65ce3bb63ddd3e275494d`。

## 適用方針

既存`MaintenanceStore.create_release`、test/review記録API、`begin_deployment`と正式`DeploymentCoordinator.deploy`を使用し、固定rootの現在HEADを候補とする。test/review evidenceには実在する回帰試験、reviewed HTML、remote/terminal readbackだけを登録する。直接SQLでrelease/deployment結果を作らない。成功の条件はCoordinatorのdeployed結果とDB `active_commit`、Scheduled Task current commit、origin/mainが候補commitと完全一致すること。失敗した場合はその実エラーを残し、成功したと報告しない。
