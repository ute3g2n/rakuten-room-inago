# FC01 現状基準

## 対象・版

- 計画: `plans/projects/maintenance/自動不具合対応フロー完遂と全工程追跡_実行計画_2026-10-19.md`、version 1.2、SHA-256 `e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381`。
- 独立計画レビュー: v1.2 approved、指摘0件。レビュー対象は計画であり、製品実装や同一job完遂を認定するものではない。
- 作業場所: `C:\project\rakuten-room-inago` のみ。
- 観測時刻: 2026-10-19 22:43:53 JST (13:43:53 UTC)。DBはSQLite read-only URI、`PRAGMA query_only=ON` で参照した。
- Git: `main`、HEAD `51cfc7503775a25af731c8f4bcf727c94f10a595`、upstream `origin/main`、同upstream HEADも同一。開始時 `git status --short`, cached diff, uncached diff, untracked list は全て空。計画作成記録の古い基準 `8368cd...` と比べて、今のHEADは計画作成後のコミットまで進んでいる。実行基準を本記録のHEADへ更新し、旧値を現行値とみなさない。
- `settings/language.md` はrepository内にも利用者Codexの想定参照場所にも見つからなかった。適用AGENTSの指示どおり日本語で継続した。

## 取得した入力と実操作

| 操作 | 実結果 | 影響・証拠 |
|---|---|---|
| 開始前 `git status --short`, cached/uncached/untracked分離 | 変更なし | `main` とupstreamのSHA一致。作業開始時に既存変更なし。 |
| 計画SHA-256再計算とv1.2レビュー/作成記録の読取 | 計画SHA一致、review approved/指摘0 | 計画実行の許可は今回の依頼に基づく。作成時の「計画作成のみ」は履歴として保持。 |
| CLI `status` | exit 0、status `ok`、schema 13 | 件数: maintenance_job 1, step_attempt 11, stage observations 16, AI session 8/observation 35, deployment 13, worker_queue 1, worker_queue_event 0, watchdog_queue 0。 |
| CLI `preflight` (production, `runtime/config/settings.json`) | exit 0、`ready`、DB health `ok`、maintenance DB available、settings 1.5、schema 13、daily DB separated | config SHA-256 `ccbd9c2af0c7118ff3c6f683e2e6e1c0c5149aebcda6b0320f2d562c4db680d8`。 |
| CLI `preflight` 初回呼出し | 必須 `--environment` と `--config` を省いたため argparse error | DB/設定へ書込みなし。直後に正しいproduction引数で再実行しreadyを確認。 |
| maintenance SQLite `mode=ro`, `query_only=ON`, `quick_check` | `ok` | 既存runtime DBだけを読取。表行・設定・Schedulerの変更なし。 |
| job row / input manifest / attempts / stage observations / handoff / AI observations / deployment / queue読取 | 詳細はfailure mapとhandoff.json | 読取で特定した誤った列名はschema introspectionで訂正。UPDATE/INSERT/DELETEは行っていない。 |
| production-task preflightとprocess/Scheduler snapshot | preflight ready、snapshot時に稼働中のmaintenance CLI/Python processなし。Daily/Worker/Watchdog taskはReady/Enabled | Task Schedulerは読取のみ。いずれのtaskも手動起動していない。 |

### 読取コマンドの補正記録

初回preflightで必須引数を省略し、CLI usage errorになった。読み取り用SQLの試行では、実schemaにない`maintenance_job.terminal_reason`、`deployment.created_at`、`worker_queue.source_run_id`を指定したためSQLiteが列不存在を返した。`PRAGMA table_info`で実在列を確認し、`terminal_reason_code`、`deployed_at`およびqueueの実列へ切り替えた。全てread-onlyの探索ミスで、本番データへの書込みや行変更はない。CLI/SQLの失敗は確認範囲とともに記録し、結果を成功へ読み替えない。

成果物の補助確認で、PowerShellからbash形式の`python - <<'PY'`を実行しようとしてparser errorになった。shell依存のheredocをPowerShell here-string pipelineへ変え、JSON parseとidentity/null値を確認した。ファイルの修正・本番操作への影響はない。

一時的なprocess snapshotにはmaintenance CLIを含むpython.exeが見えたが、同時にこの棚卸しがstatus/preflightを実行中だったため所有者を断定せず、終了・killしなかった。その後のsnapshotではmaintenance/workerに該当するPython processは0件だった。

## 同一jobの現在値

- job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- incident: `352492da-ac58-4680-b1fd-fe5f24000229`
- source_launch: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_run: `null` (DB manifestとjob rowの両方でnull)
- target_date: `2026-10-17` (JST)
- manifest SHA-256: `e97b191fdde1eb2f178c53bf51a5ad61979b69b80868a9a31b8accf31ab48d30`
- resume_count 8; DB job status `running`; `finished_at=null`; `terminal_reason_code=null`; updated_at `2026-10-19T11:44:47.543879Z`。
- 最新の記録済みworkflow invocation `workflow-0b3e8042053547f88986f13a7398c86d` はstage observation 8件を持つ。check_incident/evidence/job/investigationがcomplete、planning/repair/deployment/reportは過去結果としてblocked。これらの過去値はraw historyとして保存し、将来のstage条件に使わない。
- 同invocationと時刻が近いmaintenance_planning attempt `3df719f6-ef87-4734-8279-9ed14b23eeaa` はrunning/no output/no finishで残る。maintenance_report attempt `f770f0df-d5f6-4ed2-acf0-d63658bcd6c7` もrunning/no output/no finish。report stage observationはblockedだがattempt lifecycleと一致しておらず、FC05/10で同一jobの耐久状態を再照合する必要がある。
- 直近の調査attempt #9はhandoff acceptedで、DBにresult version/path/hashがある。これを新しい調査成果の代用にはしない。

## 実行先・副作用境界

- Workerの`WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})`をsourceで確認。
- 10/19 worker queueは1行`queued`、queue event 0行、watchdog queue 0行。queue行のsource_launch_idは歴史対象と別の当日ID。処理せず、変更もしていない。
- RakutenRoomInagoDaily / Worker / WatchdogはEnabled/Ready。観測時の直近実行結果はそれぞれ2026-10-19 13:00:01 / 07:09:29 / 13:24:01 JST。次回予定や自然実行を待つ工程は追加しない。
- production maintenance preflightはready。10/17 job用のresumeが可能なことを示すが、全workflow成功を意味しない。
- 最新deployment ledgerは`9f8a0536-cba8-4e4e-9a2b-e3add8b5c583`、status deployed、release `U23-REPORT-MD-20261019-7a90002`、active/result/source/pushed commit `22d68761cc47e089bfc2ed59dc9eadc4e69126ee`。現Git HEADは`51cfc7503775a25af731c8f4bcf727c94f10a595`。runtime switch stateのactive commitも22d687...。公式ledger/runtimeと現在repo HEADの差をFC09で正規deployment経路により解消・read-backする。active stateをHEADと偽装しない。

## 不明・確認範囲の限界

- 10/17元障害の正確な原因、業務回復、投稿実績は依然unknown。原調査v2とreviewの主張範囲/限界は保存し、原証拠本文や商品/個人情報を本記録へ複写していない。
- 直近のplanning `planning_author_turn_failed`に対応するAI例外のstack/cause/transport responseは今回のDB記録にないため、根本原因を特定できない。35件のAI observationは全て`configured_unverified`で、observed model/effort/tierはnull。DBに保存された最近のreporter session要求値は`gpt-6-luna` / `max` / `priority`。実利用値は未観測であり、ユーザー指定のhighに同期する対象である。
- 所定の設定ではParent Aは`gpt-6.1-sol/high/default`、Parent Bは`gpt-6-luna/max/priority`。後者のeffortは依頼に反するためFC02でTOMLとrole本文をhighへ同期し、それ以前もhigh指定可能なworker/default経路で専門promptを実起動する。
- preflightはDB/設定の可用性確認であり、実model利用、全工程起動、同一job終端、report公開を検証していない。
- 独立計画レビューは計画文書のみ。FC01 artifactsの独立reviewは別ファイルで続ける。

## 既存の日次障害記録の実在範囲

現repository HEAD `51cfc7503775a25af731c8f4bcf727c94f10a595`では、`automation/inago/daily_pipeline.py`の`_pipeline_code_sha256` (line 28), `_pipeline_capture_location` (35), `_sqlite_failure_labels` (57), `phase_error` (755–785), exception capture (868–935), pipeline trace payload (657–679), and best-effort trace-result logging (1064–1079)に、SQLite error name/constraint, exception class/code number, module/function/line/code SHA, trace requested/persisted, persisted run IDの記録がある。`automation/inago/orchestrator.py::_run_create_diagnostic` (716–811)とrun creation call sites (868–956)はproposed/persisted run ID, run-persisted flag and error contextを結ぶ。`automation/inago/trace.py`のallow-listed field handling (61–123, 394–481)はerror/cause class, SQLite labels and run IDsを型・安全範囲に従って記録する。

これら3 fileは現HEADとproduction ledgerのactive commit `22d68761cc47e089bfc2ed59dc9eadc4e69126ee`の間で差分448 insertions/22 deletionsがあり、現在の追加コードが本番に適用済みとは確認できない。FC01の適用判定はunknown (現repoにはsourceあり、本番active code SHAは古い)。FC09が正式なdeployment適用とruntime read-backを確認する。今回の計装追加はこれらの日次DB診断を重複実装せず、maintenance workflowの未充足操作へ絞る。

## FC01の結論

現状は「外側のstage観測が保存されること」と「内側の成果・失敗原因・次stage起動が耐久的に成立すること」が一致していない。現在のrunとattemptは未完了で、将来stageの生blocked結果を解消済みと扱えない。FC02へ渡す具体的な欠落は`failure-path-map.md`、実際の所有と実行順は`ownership.md`、重複を除いた計装候補は`instrumentation-backlog.md`に記す。FC01の読取では製品コード・設定・DB・Schedulerを変更していない。
