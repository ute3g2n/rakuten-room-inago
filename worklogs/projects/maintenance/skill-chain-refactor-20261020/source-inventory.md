# skill直列化計画のソース調査

調査日: 2026-10-20 JST。対象Git HEAD: `fc8b0a82ee91d0c8ab3a6d2274601498455d717f`。
独立したread-only explorer `skill_chain_inventory` がソースとtest定義を調査し、親が本記録へ保存した。pytest・本番実行・DB接続・ファイル変更をexplorerは行っていない。現在DBのread-only観測は別の `baseline-snapshot.json` にある。

## I01 起動経路と最小の差替え

- `automation/inago/run.ps1:8–23`: 固定rootのvenv、`rakuten-room-inago/runtime`、production configを組み立てる。dailyは既存launcherを呼ぶ。
- `automation/inago/register_maintenance_tasks.ps1:9–11,35–59` と `automation/inago/task-xml/RakutenRoomInagoWorker.xml:32`: Workerは既存CLIの `worker --once`。CLIを維持すれば、Schedulerコマンドの変更は原則不要。
- `automation/inago/maintenance/cli.py:377–393`: worker分岐で旧factoryをimportし、`run_worker_once(workflow_runner=...)`へ注入。新runner factoryへ変更する入口。
- `automation/inago/maintenance/worker.py:83–100,729,745–763`: 呼出しは既存launch/check/queue/lease/cancel_eventを渡し、`WorkerWorkflowOutcome`を受ける。
- `production_workflow.py:13703` 以降の旧factoryはcheck/evidence/job/investigation/planning/repair/deployment/reportの8 stage。新構成の8 LLM呼出しとは異なる。
- 旧controllerを新runtimeから外すため、`cli.py:18` のtop-level importはlegacy CLI分岐内へ局所化する。`historical.py:13` も型注釈等のためのimportを見直す。workerは同moduleのload関数を使うため、factory差替えだけでは旧controllerがloadされる。
- `worker.py:807` の旧 `reconcile_workflow_terminal` と `:852–880` のhistorical job終端読取は、新MD成果と既存jobへの最小の終端登録に合わせる必要がある。旧attempt/report JSON/registryを新runnerへ丸ごと再実装しない。

## I02 AI接続の再利用

- 低層 `automation/inago/app_server.py` の `AppServerSession`: `__init__:117`, `start:423`, `run_turn:636`, `interrupt:818`, `close:835`。旧maintenance Store/controllerへの依存はない。
- command/cwd/model/effort/tier/developer instructions/sandbox/log/deadline/cancel等を既存低層で扱える。
- `ai_adapter.py:120` のexecutable解決はScheduled TaskのPATH事情も考慮する小処理。必要部分だけ再利用可能。
- 旧 `MaintenanceAIAdapter` はsession plan、controller経由manifest/attempt、A/B、allowed outputs、session台帳、identity付きJSON応答の結合を持つ。全体を新runnerの土台へ転用すると、今回除去したい中間制御が残る。
- 現低層の `run_turn` はoutputSchemaを受け取る。skillがMD/HTMLを直接ファイルへ書き、最終応答だけ共通3項目JSONにすれば、大きい新envelopeは不要。schema任意化の採否は実装時にこの既存APIとの対応で決める。
- AppServerのmodel/effort確認に関係する既存nodeは下記I04に記録。設定の未観測を新たな起動gateへしないことと、現在の日次処理への無関係な変更を避けることを計画で扱う。
- 現adapterの要求値はA=`gpt-6.1-sol/high/default`、B=`gpt-6-luna/high/priority`。TOMLが設定値の優先元。観測した実model/effortとは区別する。既存主要roleのTOMLはhigh。新しいdelivery役もhighへ同期する対象。

## I03 パスと履歴jobの明示実行

- runtime: `C:\project\rakuten-room-inago\rakuten-room-inago\runtime`
- maintenance DB: `rakuten-room-inago/runtime/maintenance/state.sqlite`
- 日次DB: `rakuten-room-inago/runtime/state.sqlite`
- production config: `rakuten-room-inago/runtime/config/settings.json`
- maintenance設定: `rakuten-room-inago/runtime/maintenance/settings.json`
- `MaintenanceStore.initialize()` はmigration/writeを行う。getter共通の `_connect` もrw。計画作成の観測はURI `mode=ro` のSQLite接続を使用した。
- 既存の同一job明示再開コマンド（本計画作成では実行していない）:

```powershell
& .\.venv-inago\Scripts\python.exe -B -m automation.inago.maintenance.cli --data-root 'C:\project\rakuten-room-inago\rakuten-room-inago\runtime' resume-worker --source-launch-id historical-9e7f0c175cc4c874c955a5dab29c4427 --job-id job-historical-9e7f0c175cc4c874c955a5dab29c4427
& .\.venv-inago\Scripts\python.exe -B -m automation.inago.maintenance.cli --data-root 'C:\project\rakuten-room-inago\rakuten-room-inago\runtime' worker --once --source-launch-id historical-9e7f0c175cc4c874c955a5dab29c4427
```

- `worker.py:35` の10/19除外定数と対象選択・dispatchの保護を維持する。新runnerへも対象を渡し、誤って10/19を呼ばないことを局所試験する。
- Storeのschemaは16。新構成に工程別DB schemaを増やす前提にはしない。

## I04 実在する代表node（定義のみ確認、未実行）

```text
tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes
tests_inago/test_maintenance_ai.py::test_c26_app_server_checks_model_and_effort_before_thread_start
tests_inago/test_maintenance_ai.py::test_c24_app_server_passes_role_instructions_and_settings_to_thread_start
tests_inago/test_maintenance_ai.py::test_c27_app_server_log_is_confined_to_host_owned_root
tests_inago/test_maintenance_store.py::test_resume_maintenance_job_ignores_resume_preconditions
tests_inago/test_maintenance_store.py::test_fc11_operation_events_are_read_by_exact_job_launch_and_invocation
tests_inago/test_maintenance_integration.py::test_worker_block_and_explicit_resume_are_durable
tests_inago/test_maintenance_integration.py::test_workflow_block_status_does_not_block_worker_path
tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_fallback_report_and_october_19_exclusion_are_preserved
tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location
```

旧stage/manifest/attempt/A-B envelope等の期待が変わるtestは、新しい期待に合わせて更新・退役を仕分ける。候補ファイルは `test_maintenance_workflow_provider.py`, `test_maintenance_ai.py`, `test_maintenance_planning.py`, `test_maintenance_repair*.py`, `test_maintenance_report_publication_stage.py`。低層やlegacy CLIの生きた回帰試験まで一括削除する根拠にはしない。新runner testのnodeは新設予定であり、実装後に完全nodeを確認する。

## I05 日次診断の情報欠落候補

以下はソースで見つけた不足候補であり、10/17の原因という主張ではない。調査担当が不足を評価し、次の診断に有用な変更を同じjobの実装・試験・独立製品review・本番適用へ渡す。既存計装の再実装はしない。

| ソースの事実 | 有用性の評価・試験候補 |
| --- | --- |
| `daily_pipeline.py::_result_mapping:239–244` は `failure_result` callback自身の例外を捕捉してdefault結果へ戻すが、二次例外の種類・捕捉箇所が残らない。 | 一次失敗と二次callback失敗を識別する安全な記録。callbackを故意に失敗させ、一次結果維持と二次失敗の記録を確認。 |
| `_retry_summary_for_run:265–297` はSQLite例外で集計値をnullへするが、どのqueryの失敗か残らない。 | ROOM/search、Trends acquisition/attempt等の固定操作ラベルとSQLite分類。個別SELECT失敗時のnull維持とラベル記録を確認。 |
| `refresh_history_after_commit:851–859` はexport例外を件数とerror classへ縮約し日次を続ける。callbackの失敗箇所・安全なreasonはそこで残らない。 | export操作の失敗時診断と相関。合成commit後export失敗でcommit継続と安全な診断を確認。 |

外側pipelineのmodule/function/line/code SHAは `daily_pipeline.py:868–932` で既に記録する。その捕捉行はcallback内部の発生行や根本原因の証明ではない。既存の外側診断だけをもう一度追加し、今回の日次計装の実績にしない。

## I06 追加確認: 新runnerの直接接続と成果物の書込み

- `AppServerSession.__init__:117–135` の必須値はcommand/cwd/log_path。run_turnがstartを呼ぶため二重の開始操作は不要。deadlineはtime.monotonic基準の絶対値。
- `run_turn:636–655` はoutputSchemaをそのままturn/startへ渡す。既存APIで担当・入力・依頼の3項目JSON schemaを渡せる。MD/HTML本文を巨大なJSONへ包む必要はない。
- `AppServerTurn:101–111` のfinal_textは低層ではJSON parseしない。新runnerで3項目だけparseし、不正な元応答は保持して整形依頼または後続へ渡す。
- `thread/start.developerInstructions:491–493` へ、対象skillの絶対パスを読んで作業する指示、入力、出力範囲、最終3項目JSONを渡せる。各担当は別instance、独立reviewはresume_thread_idなしの新sessionにする。
- 現 `readOnly` sandboxではreview Markdownの新規保存もできない。指定MDを担当LLMが直接書く設計ではworkspaceWrite＋製品コードを変更しない役割指示が必要。個別MDだけへのOS書込制限が保証されるとは言わず、その証明を開始条件にしない。
- deliveryはGit pushのためtool側networkAccessが必要。他担当と同じnetworkAccess:falseを流用し、公開可能と推測しない。実際の認証/接続拒否は操作の失敗として残す。

## I07 追加確認: 設定観測とcatalog preflightの違い

- runtime model/effort観測の欠落は `app_server.py:735–758` でconfigured_unverifiedとして続行する。不一致もobservationの表示であり、この箇所ではraiseしない。観測不足そのものが現在の起動gateだとは断定しない。
- 別のcapability catalog probeが `start:475–478` でthread開始前に走る。`_verify_model_capability:548–599` はcatalog不正、model不存在、highやpriority一覧不足等をraiseする。Fast probeは `:601–633`。実providerへthread/startを要求する前に失敗し得る。
- 新runnerでこの事前probeをadvisoryとして扱う局所変更を検討し、要求model/high等をthread/turnへ渡す。旧日次の呼出し互換と関係nodeを確認する。modelを省略するとeffort指定も拒否されるため、high指定を失う迂回はしない。
- 新たな確認待ち・role観測receiptは足さず、実際のthread/turn拒否はその担当の失敗記録と次担当への引継ぎへ結ぶ。
