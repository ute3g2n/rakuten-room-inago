# 10/20 日次不具合の現在事実と正式受付経路

対象: 承認済み構成案2.0の実装・隔離試験後、2026-10-20の実launchを使って保守フローを動作確認するための計画入力。

- 作業場所: `C:/project/rakuten-room-inago`。
- 担当範囲: 読み取り専用調査とこの文書だけ。通常Daily、Worker、Watchdog、job、queue、DBの変更操作は実行していない。
- 主な一次DB参照時刻: `2026-10-20T20:38:25.707568+00:00` ～ `2026-10-20T20:39:13.456493+00:00`（2026-10-21 05:38～05:39 JST）。コード読取も同じ調査内で実施。
- `settings/language.md` は現workspaceに存在しないため、日本語で記録した。
- ハッシュの生成・照合は一切していない。既存ハッシュ列は投影していない。

## 確認済みの事実

| fact_id | 一次根拠・選択条件 | 安全な観測結果 |
|---|---|---|
| OCT20-F01 | maintenance DB `daily_launch`、`target_date='2026-10-20'` | 1行。`source_launch_id=fa3292ae0c7e43bb9569576fcb1a5d85`、`status=failed`、`source_run_id=null`、`exit_code=4`。 |
| OCT20-F02 | 同上の時刻・プロセス列 | 開始 `2026-10-20T04:00:01.966587Z`（13:00:01.966587 JST）、終了 `2026-10-20T04:00:02.824264Z`（13:00:02.824264 JST）。owner PID `17232`、child PID `60584`。PIDの現在生存状態は本調査では確認していない。 |
| OCT20-F03 | 同launchの `result_json` をメモリ内で安全なキーだけ投影 | `schema_version=daily-launch-completion-v1`、`daily_status=failed`、`overall_status=failed`、`reason_code=daily_runtime_error`、`failure_phase=prepare`、`primary_error_class=IdempotencyConflict`、`secondary_error_classes=[start_backup_failed,metrics_unknown,preservation_failed]`、`trace_status=not_written`、`failed_checks=[]`。例外classは一次保存値として確認済み。例外message、cause chain、stack、衝突対象は未確認。 |
| OCT20-F04 | `daily_check`、同launch完全一致 | `check_id=18fd9a8b-343b-4c66-addb-a1bce71ecf83`、`target_date=2026-10-20`、`source_run_id=null`、`check_status=incomplete`、`reason_code=daily_runtime_error`、`observed_at=created_at=2026-10-20T04:00:02.824264Z`。 |
| OCT20-F05 | `worker_queue`、同launch完全一致 | 1行、`queue_id=37a2be6b24fa4ffb8f859012e00a209a`、`worker_job_id=worker-10e0017fed254a2db314299ef3b23c34`、`target_date=2026-10-20`、`status=queued`、`check_id=null`、`reason_code=null`、作成/更新 `2026-10-20T04:00:02.824264Z`。`worker_job_id` はqueue側の識別子であり、保守jobが存在する証拠ではない。 |
| OCT20-F06 | `worker_queue_event WHERE queue_id='37a2be6b24fa4ffb8f859012e00a209a'` | 0行。これだけで全実行履歴の不存在を断定しないが、当queueのevent保存履歴はない。 |
| OCT20-F07 | `incident_run` と `job_input_manifest`、同launch完全一致。`maintenance_job`/`incident` は `incident_run` を同launchで限定してJOIN | 当launchのincident関連付け0行、manifest0行、関連する保守job0行、関連incident0行。現在の計画時点で再利用する10/20保守job IDは存在確認できていない。新job IDやincident IDを仮採番していない。 |
| OCT20-F08 | `daily_result_observation`、同launch完全一致 | 1行。`schema_version=daily-result-observation-v1`、`captured_at=2026-10-20T04:00:02.824264Z`。内容は許可キーだけ投影して確認。`overall_status=failed`、`daily_status=failed`、`daily_reason=null`、`reason=null`、`committed_count=0`、`error_count=1`、`actual_posted_count=null`、`initial_allocation_count=null`、`shortfall_count=null`、`supplemental_allocation_count=null`。 |
| OCT20-F09 | 同observation `phase_results` のstatusだけ | daily `failed`、export `ready`、metrics `unknown`、preservation `not_run`、start_backup `not_run`、trace `not_written`。`committed_count=0` は保存された値であり、未確認の実投稿数を0と認定する根拠にはしない。 |
| OCT20-F10 | Daily DB `run WHERE target_date='2026-10-20'` のcountのみ | 0行。新runを生成して確認する必要はない。保存された `source_run_id=null` を保持する。 |
| OCT20-F11 | `worker.py:32`、`run_worker_once`、`dispatch_ready_worker` | `WORKER_DISPATCH_EXCLUDED_DATES=frozenset({'2026-10-19'})` が存在し、明示対象でも除外日を処理しない分岐がある。10/20はこの除外日に含まれない。 |
| OCT20-F12 | `cli.py:_parser`、`_run_command` のworker分岐 | 実在CLIは `--data-root <root> worker --once --source-launch-id <id>`。現在は `build_skill_chain_workflow_provider` をコールバックにして `run_worker_once` へ渡す。通常Dailyの起動は含まない。 |
| OCT20-F13 | `skill_chain.py:build_skill_chain_workflow_provider` | 現行providerは10/17の固定launch/date/null-runしか受理しない。10/20実launchは `worker_dispatch_not_assigned_to_plan` を返す実装。受理後も既存保守jobがちょうど1件必要で、初回保守jobを生成する分岐はない。**現行workerをそのまま実行しても今回の正規初回job受付にはならない。** |
| OCT20-F14 | `skill_chain.py:run_skill_chain` | job/launch/dateを10/17の固定値と比較し、不一致で `skill_chain_target_identity_mismatch`。旧10/17計画のファイルパス固定もある。構成案2.0の実装ではprovider受付だけでなく、この実行本体と計画入力の固定対象依存も対象化する必要がある。 |
| OCT20-F15 | `production_workflow.py:run_daily_check_incident_stage`、`run_daily_job_stage`、`store.py:record_daily_check`、`controller.py:start_fix_job` | 既存の通常launch向け正式操作primitiveはある。check段で保存launch/observationから分類し、`record_daily_check` が同checkをincomplete→abnormalへ更新してincident関連付けを作れる。job段は同launch/run/dateのjobを検索し、既存jobを再利用、なければrelease anchorを得て `start_fix_job`、`bind_job_input_manifest` を行う。これらは現CLIのskill-chain providerから直接呼ばれていない。 |

## 10/17・10/19との区別

| fact_id | 対象 | 今回のread-only観測 |
|---|---|---|
| OCT20-F16 | 10/17の履歴受付 | 実 `daily_launch` は0行。履歴用launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、check `d8178c1e-61fb-4a53-a4e5-0216722fda1b`、incident `352492da-ac58-4680-b1fd-fe5f24000229`、保守job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`。jobは `queued`、resume_count=30、null run。10/20のjobとして流用しない。Daily `run` countは0。 |
| OCT20-F17 | 除外する10/19 | launch `642daa0ac86a4535bb34448373d08259`、開始 `2026-10-19T04:00:02.331648Z`、終了 `2026-10-19T04:00:03.226334Z`、failed/exit4/null run。check `9d150491-3c6e-4824-9640-26d108617596`、queue `82e35e362a064e3292355d27ed0f4289`、worker ID `worker-2a0f6cdfb9a4479f8f9c1b9244303e04`、queued。queue event0行、Daily run0行。今回の対象へ選ばない。 |

10/17履歴jobや10/19 queueの状態は比較用読取であり、更新・再開・処理していない。

## 計画に採用できる操作経路

### 1. 先に構成案2.0を実装する

OCT20-F13/F14の固定対象制限を解消し、実launchからの正式初回受付をskill直列経路に接続する。OCT20-F15は既存primitiveの参照先であり、旧巨大provider全体を再起動する指示ではない。

実装には以下を明記する。

1. 10/20実launch・既存check・既存observation・既存queueを読み、date/null-runを引き継ぐ。
2. 同launch/run/dateから保守jobを再確認し、既存jobがあれば再利用する。今回読取時点の0行を、実行時にも無条件で0行とみなさない。
3. ない場合は保存された実launchに対して通常受付のincident/check関連付けと初回保守job、manifestを正式操作で生成する。controllerの実採番結果を後続skillへ渡す。仮のsource_run_id・historical launch・既存10/17 jobで代用しない。
4. 10/20用 `incident.md` を保存された一次値から生成し、同jobのskill連鎖へ渡す。旧10/17 incident/planをコピーして対象事実と混在させない。
5. ハッシュ生成・照合禁止は実装・試験・実動にも引き継ぐ。参照した旧primitiveにはハッシュ算出・一致比較が残るため、そのまま実行して制約適合と扱わない。正式identity関係と内容参照による経路へ必要な変更を計画する。
6. 原因不明、試験/レビューの結果、任意証拠の欠測を後続開始条件にせず、個別の未実施と理由を保存して独立工程・最終HTMLまで進める。incident解決とworkflow完遂を混同しない。

### 2. 実装・試験後に指定Workerを実行するための既存CLI形

下記は実在する引数形の計画例であり、**この調査では未実行**。現行版のままではOCT20-F13の対象制限がある。構成案2.0の実装・確認後の版で実施する工程へ置く。

```powershell
& 'C:\project\rakuten-room-inago\.venv-inago\Scripts\python.exe' -B -m automation.inago.maintenance.cli --data-root 'C:\project\rakuten-room-inago\rakuten-room-inago\runtime' worker --once --source-launch-id fa3292ae0c7e43bb9569576fcb1a5d85
```

引数を省略した `worker --once` は他queue/historical jobも自動選択し得るため、今回の対象指定には使わない。`enqueue-historical` は実launch既存の今回対象に不要。`start-job` はincident/release/job等を要求する低レベルCLIで、未存在IDを人為作成して迂回するために使わない。

既存jobが正式に生成された後の再開には、実在CLIとして以下がある。`<actual_job_id>` は本番受付の実結果だけを使う。

```powershell
& 'C:\project\rakuten-room-inago\.venv-inago\Scripts\python.exe' -B -m automation.inago.maintenance.cli --data-root 'C:\project\rakuten-room-inago\rakuten-room-inago\runtime' resume-worker --source-launch-id fa3292ae0c7e43bb9569576fcb1a5d85 --job-id <actual_job_id>
```

`resume-worker` はDB変更操作であり、この計画作成では実行していない。`skill-chain --job-id ... --source-launch-id ... --target-date ...` もparserには存在するが、既存job用で、初回正式受付の代替にはしない。null-runの場合は `--source-run-id` を与えない。

### 3. 事後read-back

同launchのcheck、queue、incident_run、job_input_manifest、maintenance_job、生成された同jobの成果を再読する。Daily `run WHERE target_date='2026-10-20'` をcountで確認し、通常Daily、新run、下書き追生成を動作確認の方法にしない。10/19除外と別対象の保全を確認する。公開結果、workflow完遂、原因特定、業務回復は別々の観測として報告する。

## 実際に読んだDBと選択条件

すべてPython標準 `sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)` と `PRAGMA query_only=ON`。repoのStore/CLIは生成・初期化しなかった。SQL値はプレースホルダで渡した。

| DB/テーブル | 選択条件・投影 |
|---|---|
| `rakuten-room-inago/runtime/maintenance/state.sqlite` / `daily_launch` | `target_date IN ('2026-10-17','2026-10-19','2026-10-20')`。launch/date/status/run/exit/開始終了/PIDのみ。結果JSONは対象launch完全一致でメモリ内読取し、OCT20-F03の許可キーのみ出力。 |
| 同 / `daily_check` | 10/20、10/19、10/17履歴の既知launch完全一致。check ID、launch/run/date、status/reason、observed/createdのみ。 |
| 同 / `worker_queue` | 同3 launch完全一致。queue/worker/check ID、date、status/reason、created/updatedのみ。 |
| 同 / `incident_run` | 同3 launch完全一致。incident/check/launch/run IDとcreatedのみ。 |
| 同 / `job_input_manifest` | 同3 launch完全一致。job/incident/launch/run/date/createdのみ。ハッシュ列を選択しなかった。 |
| 同 / `maintenance_job`、`incident` | `incident_run.source_launch_id IN (同3 ID)` でJOIN限定。ID/status/時刻/再開回数/終端reasonのみ。 |
| 同 / `worker_queue_event` | 既知10/20 queue IDと10/19 queue ID完全一致でcountのみ。 |
| 同 / `daily_result_observation` | 10/20/10/19 launch完全一致でcount。10/20のみschema/capturedとobservation JSONをメモリ内読取し、OCT20-F08/F09のキーだけ出力。生payloadやハッシュ列を出力しなかった。 |
| `rakuten-room-inago/runtime/state.sqlite` / `run` | `target_date=?` を10/17、10/19、10/20に限定しcountのみ。商品・投稿・下書きテーブルにはアクセスしなかった。 |

必要列を特定するため上表の関連テーブルに `PRAGMA table_info` を実施した。observation/eventテーブル名の存在だけを `sqlite_master` の名前条件で確認した。ハッシュの生成・照合は行っていない。

## 実際に読んだファイルとコード入口

- `.agents/skills/common-execution-planning/SKILL.md`：計画・禁止事項の確認。旧10/17固定契約部分は今回の本人最新依頼の10/20対象へ読み替える必要がある。
- `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC01/baseline.md`、同 `execution-record.md`、同 `SC10/final-report.md`：既知観測との区別のために参照。現在値は上記一次DB読戻しを採用した。
- `automation/inago/maintenance/paths.py:resolve_maintenance_database_path`：DB位置。
- `automation/inago/paths.py`：固定rootとruntime path解決の参照。
- `automation/inago/maintenance/cli.py:_parser`、worker/resume-worker分岐：実在引数と現在のprovider接続。
- `automation/inago/maintenance/worker.py:run_worker_once`、`dispatch_ready_worker`：queue選択、プロセス停止確認、10/19除外、workflow呼出し。
- `automation/inago/maintenance/skill_chain.py:build_skill_chain_workflow_provider`、`run_skill_chain`：10/17固定条件、既存job要求、旧planパス。
- `automation/inago/maintenance/daily_launcher.py:run_daily_launch`：既存launchの保存経路参照。実行していない。
- `automation/inago/maintenance/store.py:record_daily_check`：incomplete→abnormalの正式更新、incident関連付け。
- `automation/inago/maintenance/controller.py:start_fix_job`、`bind_job_input_manifest`：正式job操作の入口参照。
- `automation/inago/maintenance/production_workflow.py:run_daily_check_incident_stage`、`run_daily_job_stage`：通常launchの既存受付/再利用primitive。実行していない。
- `automation/inago/maintenance/workflow_provider.py`：旧providerが既存jobを引く分岐とstage呼出しを参照。
- `automation/inago/maintenance/sql/003_daily_launch.sql`：launchのidentity/終端不変契約。
- `automation/inago/register_maintenance_tasks.ps1`：repo Python、runtime root、worker引数の構成のみ。Schedulerを変更・実行していない。
- `automation/inago/cli.py`：Dailyのexit4・例外class返却の入口。

## 未確認と調査操作の限界

- `IdempotencyConflict` の直接原因、衝突した保存物、例外cause/stackは、この限定調査では特定していない。classから具体原因や修正完了を推論しない。
- phase `prepare` と副次分類は保存された分類値。個々の失敗発生位置、開始backupがnot_runになった理由、traceがnot_writtenになった具体原因を本調査だけで確定しない。
- Schedulerの実行履歴・現在プロセス・稼働版activationは今回の担当範囲では未確認。DBの13:00台開始時刻は確認したが、その時刻だけからScheduler起動証拠を合成しない。
- 初期の探索では存在しない `integration.py` / `operations.py` / `workflow_stages.py` / `storage.py` / `persistence.py` 等やPowerShellで展開されない引数globを含むrgがexit1/2を返した。実在する `production_workflow.py` 等へ訂正して上記コード入口を読んだ。この探索失敗を製品試験結果として扱わない。
- テストは本担当で実行していない。DB mutation、job新規作成、通常Daily再起動、Worker実行、公開、Git操作も実行していない。
