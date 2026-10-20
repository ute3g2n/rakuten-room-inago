# 正式反映確認の失敗と修正 r1

## 実反映の観測

- 固定root `C:\project\rakuten-room-inago`、branch `main`、実行時HEAD/`origin/main` `0ad1765a16eaf8be73d0c315655d1d525fa7cc26`。作業rootを変更していない。
- 正式APIで作ったrelease `FC11-REPORT-20261020-0ad1765`、deployment `fa835401-7247-4d35-9d02-e5e8bbaeed14` は、base `204939e8dbca217489278e0133ea86ca35885def`、tested/pushed/active `0ad1765a16eaf8be73d0c315655d1d525fa7cc26` を記録した。
- この行は `2026-10-20T00:59:06.148841Z` に `deployed` と保存された。しかし直後のruntime確認は `DeploymentError("runtime_switch_state_invalid")` で失敗し、上位で `DeploymentError("runtime_task_release_pending")` となった。したがってDBの成功statusを実反映成功として受け入れない。既存行は履歴として保持し、直接SQLで訂正しない。
- 失敗時のswitch state fileはactive `204939e8dbca217489278e0133ea86ca35885def`、pending null、status activeのまま。Scheduled Taskは固定rootを指し、そのHEAD `0ad1765...` はhealthyだった。実root/commitとswitcher記録だけがずれていた。
- 保存済みfailure-journal event `81e77df29b0e4453ab3e544bcd138f09` はcorrelation `4756cd9d2d474c97a2cce013ea760c65`、時刻 `2026-10-20T00:59:11.776+00:00`、operation `deployment.deploy`、capture `automation.inago.maintenance.deployment.DeploymentCoordinator.deploy`、exception chain `DeploymentError → DeploymentError`、reason chain `runtime_task_release_pending → runtime_switch_state_invalid`、stack `diagnostic_trace.py:941 → deployment.py:1474` を記録する。対応するdiagnostic JSONLはmaintenance ID `bd6d1f365f654e87b6374031dc2fc1d2`で、release IDとstage start/failureを含む。
- このstandaloneな正式apply operationには歴史jobのjob_id/source_launch_id/attempt_idは渡されず、code revisionも `unknown`、failure journalのinput/artifact refsは空だった。job identityや実行時刻を後付けで捏造しない。release/deployment ID、時刻、correlation、runtime stateと例外連鎖を本記録で対応付ける。operation event tableにはこの時刻以降の同job stage eventはなく、raw履歴を更新しない。

## 原因と変更

- `TaskSchedulerRuntimeSwitcher.activate()` は、Task Schedulerがすでに同じfixed root・同じHEADを使っているとき、`checkout`付きの再適用をno-opとして戻していた。switch stateが古くてもactive commit/rootを読み直して保存しないため、その後の`confirm_active()`が古いstateを見て失敗した。
- `DeploymentCoordinator.deploy()` は、runtime `confirm_active()`より先にdeployment行を`deployed`で確定していた。confirmが失敗しても成功行が残る順序だった。
- 同じroot/commitをTask Scheduler readbackで確認した場合は、switcherのactive commit/rootを実観測値に合わせて保存する。タスク設定の再変更はしない。
- Coordinatorは`confirm_active()`が成功してから`finish_deployment(status="deployed")`を呼ぶ。確認失敗はpendingの再開対象になり、成功に偽装しない。

## 回帰検証と独立レビュー

- 修正前RED: 新規同root/stale-state試験は`runtime_switch_state_invalid`で失敗。confirm失敗試験は例外`runtime_task_release_pending`に加え、ledgerが先に`deployed`となることを確認した。
- 修正後: `tests_inago/test_maintenance_deployment.py` 全体 `9 passed`。confirm失敗後に同じdeployment IDから再開してdeployedへ進むこと、同root/stale-state補正、fixed-root contractを確認。
- `python -m py_compile automation/inago/maintenance/runtime_switcher.py automation/inago/maintenance/deployment.py tests_inago/test_maintenance_deployment.py` 成功。`git diff --check` exit 0。
- 独立read-only製品コードreview `/root/html_review_scope_product_review` はAPPROVE／指摘0。`runtime_switcher.py` SHA-256 `67a8ebc831824a7e76e3338b9e0f956a690e6a3dffdcfac131085675bb2e6a34`、`deployment.py` SHA-256 `a60f1c6deaac7c647f4dfb42b9b696a32ccbaed2395707ac814b4cf3622b8da`。テストコードはreview対象外。
- 初回の古いC38テストは別checkout/worktreeを期待しており、現fixed-root実装と不一致だった。テストを、dirtyな固定rootを保持したまま`fixed_repository_dirty`で拒否する契約へ更新し、focused file全体を再実行して9件PASSを確認した。

## 操作の失敗と回復

- 最初の`pytest`直起動は`ModuleNotFoundError: automation`で収集前に失敗。`python -m pytest`も選択Pythonにpytestがなく失敗。PowerShell process内で`PYTHONPATH`を固定rootにし、pytest launcherで対象fileを実行して回復した。
- read-only DB読取の初回パス `runtime/maintenance/state.sqlite` は誤りでopenに失敗。実root `rakuten-room-inago/runtime/maintenance/state.sqlite`へ訂正した。
- deployment一覧の初回SELECTで存在しない`started_at`列を指定し失敗。`PRAGMA table_info`に基づき実列`deployed_at`へ直してread-only確認した。
- `rg`のPowerShell pattern quoting試行がsyntax error。ファイル変更なし。単純なliteral searchへ切替えてfailure-journalとdiagnostic JSONLを特定した。
- いずれもリポジトリの変更、reset/checkout、追加DB書込は伴わない。DB rowは上記の正式Coordinator呼出だけが作成した。

## 次の再開位置

製品差分・試験・本記録・計画§14だけを所有範囲として選択stageし、差分/秘密を確認してcommit/pushする。その後、正式Store APIと`DeploymentCoordinator`で修正済みHEADを適用し、switch state・Task Scheduler root/commit・health・formal ledgerをread-backする。さらに同じ10/17 job/launchのみをresumeし、新しい計装trace、review済みHTML、公開bytes、終端と副作用不変を測定する。現時点でFC-A10および計画全体は未完了。
