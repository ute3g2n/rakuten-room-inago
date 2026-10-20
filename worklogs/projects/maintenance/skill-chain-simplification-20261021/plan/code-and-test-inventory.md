# 構成案2.0 実装計画用コード・試験台帳

調査日: 2026-10-21。担当: code inventory。作業場所: `C:\project\rakuten-room-inago` のみ。本人の構成案2.0と今回の親依頼を根拠とする。製品・試験・DB・Runtimeは変更していない。試験、CLI、Daily、worker、ハッシュ生成・照合は実行していない。下記はソース読取と静的検索で確認した事実、変更候補、未確認である。出力所有はこのMarkdownだけ。Git操作は親指示で禁止され、行っていない。

## 1. 事実と呼出し経路

| 事実ID | 確認できた実経路・所在 | 計画へ渡す変更候補 |
|---|---|---|
| CI-01 | `automation/inago/register_maintenance_tasks.ps1:10,55,56` は `.venv-inago\Scripts\python.exe -B -m automation.inago.maintenance.cli --data-root <runtime> worker --once` とwatchdogを登録する。 | 現行実入口を更新対象に含める。別の新runnerだけ作って登録経路が旧処理へ入る残存を避ける。登録変更が必要なときだけ当該操作を行う。全taskのReadyは完了条件にしない。 |
| CI-02 | `maintenance/cli.py:141` skill-chain parser、`:593` mainのskill-chain分岐はstoreを経由せず `run_skill_chain` を直接起動。`:385` worker分岐はstore→ `run_worker_once(...workflow_runner=build_skill_chain_workflow_provider(...))`。`:480` watchdog後は `dispatch_ready_worker`。 | `skill-chain` とworkerの双方を7担当共通runnerへ接続。jobと開始担当の明示引数、短い追加作業依頼を扱う。DB旧statusから開始担当を逆算しない。 |
| CI-03 | `maintenance/skill_chain.py:68` ROLE_CALLSは8呼出し。調査→実装→試験→製品レビュー→反映→作者→HTMLレビュー→公開。`:918` runと`:2051` providerは10/17既存jobへ固定される。`:945` は旧10/17計画のSC08/SC09の8用途promptを抽出する。 | 7担当へ変更、公開呼出し8とpublication.mdの新規生成を除く。今回の実動は10/20の実在launch/queueを使う。`oct20-current-facts.md:OCT20-F07` は現時点のincident/manifest/job関連付け0行とするため、受付時に既存jobを再確認し、無ければ正規の初回保守jobを作り実採番IDを受け取る。既存10/20 jobがあるとは仮定しない。IDをハッシュで生成しない。10/17既存HTMLの特例pathは保持できるが、10/20を10/17へ書き込まない。source_run_id=nullを維持、10/19除外。旧計画のprompt依存を新しい短い担当責務へ置換。 |
| CI-04 | `skill_chain.py:113` hash helper、`:141` observationはpresent/bytes/hashを収集。`:308` archive/clear、`:421` JSON3項目parse、`:433` promptにはhashと既読証跡、`:585,602` commandExecution既読抽出、`:675` 作者再試行、`:904` 全体run、`:1430` 作者修正/再review、`:1628` event出力照合、`:1695` 再開位置推定、`:1848` 最終全成果物照合。 | hash、bytes/mtime/markerの代替gate、既読抽出、JSON整形再試行、全出力archive/clear、最終再検査を撤去。前担当MDと実物のpathを普通に渡す。本文レビューと実worker終了は実施し、その結果を短く記録する。失敗時は操作と理由を次担当へ渡す。 |
| CI-05 | `skill_chain.py:510` は共有 `automation/inago/app_server.py:115 AppServerSession` をroleごとに生成。`:1156` run_turnはoutput schemaを指定。`:1212,1226` 作者とreviewerのsessionを保持し、`:1374` 最後にcloseする。closeの例外はpassになる。 | `run_turn` の実終了と所有CLIの終了を両方記録。前担当closeを確かめてから次担当へ。作者修正、追加製品修正など全追加workerも同じ終了記録へ含める。close失敗を握りつぶさず、生存を実行中として明記し回収を続ける。全担当実終了前の完了報告をしない。 |
| CI-06 | `app_server.py:481 start` はPopen(shell=False、Windows CREATE_NO_WINDOW、stdout/stderr pipe)。`:803` turn/completed受信。`:905 close` はinterrupt、stdin close、wait、terminate、wait、kill、waitを行い、process_closedとreturncodeをログへ保存。`:210 is_closed` はpoll確認。 | この共有transportを使えば実終了回収の基礎を再利用できる。保守専用の終了記録/returncode取得をrunnerへ返す。AppServerのturn完了をCLI終了と混同しない。共有daily利用へ影響するAPI破壊を避ける。 |
| CI-07 | `app_server.py:215 _log` は許可欄のみ、`:378 _discard_stderr` は秘密promptを含み得るためstderrを捨てる。stdoutはJSON-RPC読取で生値を保存しない。`skill_chain.py:878` は応答をhash/bytesで記録する。 | 「秘密を除いた応答・stdout・stderr保存」は現行では不足。保守の選択可能なsafe sinkを用意し、既存allowlist、秘密除外、例外class/cause/capture位置を保つ。生RPC・provider error本文・SQL・token等をそのまま保存しない。応答hash生成で代替しない。 |
| CI-08 | `worker.py:281` historical queue contextはjob_idからdispatch hash生成。`:467` run_worker_onceはqueue/lease/checkを使いworkflow起動。`:804` runner後に `inspect_skill_chain_terminal` を再実行、legacyでは`:845` reconciliation。`:901` historical結果でDB jobが先にcompleteでなければmaintenance_incompleteを返す。 | 二重終端照合とDB complete前提の循環を除く。既存IDをそのまま使い、実起動と終了記録後に同job終了をDBへ反映。DB更新失敗は記録して実作業の終了を報告。DB queued/blockedを開始・再開・終了条件にしない。 |
| CI-09 | `store.py:6267 finalize_job` はleaseとjob current statusを照合、queued/running以外を拒否し、異なるterminalの再完了も拒否する。`:1413 bind_job_input_manifest` / `:1488 get_job_input_manifest` はhash manifest登録/照合、`:1911` AI session bindもhash依存。`:1630` historical refsにも旧状態による絞込みがある。 | 新runnerの終了更新は最小の既存job更新APIへ集約。旧状態を観測として保ち、実終了を書ける形へ調整。新manifest/receipt/session hash登録をしない。既存行を消さず、必要ならnullable化/表再作成migration。ダミーhashを入れない。 |
| CI-10 | `production_workflow.py` は全legacy stage群、`workflow_provider.py:568` は旧provider、`controller.py` はformal attempt・review・repair・manifestを集約。新skill-chain providerのdocstringはlegacy controller/providerをロードしないが、worker legacy分岐や旧CLIは残る。 | 新入口、watchdog dispatch、直接CLI、再開、各roleからのhost helper参照を含む呼出し確認が必要。使わないlegacy保守関数は削除/現行入口から除外し、fallbackとして復活させない。単なる「新経路では通常使わない」だけでhash全撤去済みとしない。 |
| CI-11 | `deployment.py:444 working_tree_content_sha256` はtracked全資材をhash。`:507 ManagedWorktreeManager` は名称が旧式でも固定rootを返す。`:561 stage_owned_paths`、`:647 commit_owned_paths`、`:662 prepare_repair_candidate_commit`、`:873 push_candidate_commit`、`:985 deploy` はGit候補/反映制御。`runtime_switcher.py:217 _active_identity`、`:298 activate`、`:520 confirm_active`、`:628 healthy` はroot/commit/content/state/task状態を照合。 | 対象ファイルと必要な設定/schema/restart操作へ絞る。whole-tree/commit/content/active-pending state/healthyによる完了制限とGitを撤去。固定rootで未commit内容を扱う。日次の実行所有や投稿DBアクセス保護の共有処理は用途を見て保つ。 |
| CI-12 | `reporting.py:1305 prepare_incident_report` はcontroller HTML rendererとresult hash、`:1496 save_report_artifacts`、`:1807 publish_reviewed_report` はGit publish/hash読戻し。`production_workflow.py:12111` はjob_idからHTML filename hash生成。`workflow_reconciliation.py:405` 以降もHTML・publication・remote hash照合。 | 作者が `reports/incidents/<対象日>/<既存job_id>.html` へ直接作成・修正、reviewerはその実物を読む。10/17既存保存先だけはそのまま参照。controller本文生成/別HTML copy/公開worker/remote検査を除く。報告用事実収集は簡潔なpath参照へ。 |
| CI-13 | `failure_journal.py:26,77,500` / `diagnostic_trace.py:23,931` は `code_revision.py` の全source fingerprintを参照。`code_revision.py:111,173` は各fileとmanifestをhash。 | safe class/cause/capture位置・operation・既存job/path/時刻は残す。実行版fingerprint生成/照合、例外fingerprintは除く。failure journalのprimary障害が本作業を止めない性質も保つ。 |
| CI-14 | `daily_pipeline.py:28 _pipeline_code_sha256` と `orchestrator.py:751` は日次診断にsource hashを追加。共有業務のhashは `artifact_io.py:146` 保存artifact hash、`ai_runner.py:207,1246` 入力/応答artifact、`repository.py:993,2017` artifact内容確認、`operations.py:273` 業務操作digestに存在。 | 保守の版・例外診断用source hashは生成と欄を撤去し、`trace.py:132,434` および診断試験を同期。日次入力/AI結果/投稿成果物管理という本来の業務hashは今回の保守ゲートとは別用途として維持。保守旧helperがそれを流用して照合する呼出しだけを除く。共有業務hashを全消去しない。 |

## 2. 担当所有ファイルの分割候補

1. **実runner/入口/終了**: `automation/inago/maintenance/{skill_chain,cli,worker}.py` と必要最小限の `automation/inago/app_server.py`。新7担当・明示開始担当・追加worker回収・safe出力記録を一人の所有へまとめる。shared AppServer変更が必要なら専用の所有者を決め、runner所有者とAPIを先にそろえる。
2. **DBと旧経路/hash撤去**: `maintenance/{store,historical,controller,ai_adapter,investigation,planning,repair,production_workflow,workflow_provider,workflow_reconciliation,code_revision,evidence,probe,report_fallback,reporting,stage_handoff,diagnostic_trace,failure_journal}.py`、`maintenance/sql/`、`automation/inago/schemas/maintenance-*.schema.json`。全旧機能を作り直すのでなく、到達不要な旧経路を除去し必要な既存行読取・終了更新へ絞る。hash引数や必須欄が残るAPIへの新runner接続は禁止。migrationは過去行保全、ダミー値なし。
3. **反映**: `maintenance/{deployment,runtime_switcher}.py`、必要なら `task_runtime_working_directory.ps1`。既存未commit差分の保全/調整は下節。TaskSchedulerの全体状態を新gateにしない。固定rootを維持。
4. **日次診断**: `automation/inago/{daily_pipeline,orchestrator,trace}.py`、実際に欄を定義するschemaと診断trial。source hash除去と、次回の原因切分けに役立つ操作・開始終了・安全な例外・SQLite code/constraint・run保存有無などを保つ。10/20の原因断定は別の調査証拠が必要。
5. **技能・role・仕様**: 下節のSKILL/references/roles/テンプレート/仕様/図。旧promptの動的抽出をやめる場合、過去承認済み計画を現行規則として読ませない。
6. **試験**: `tests_inago/test_maintenance_skill_chain.py`、`test_maintenance_{integration,store,deployment,ai,flow_completion_fc07,workflow_provider,report,report_publication_stage,deployment_probe,docs}.py` と `test_daily_pipeline_safe_diagnostics.py`、`test_daily_fix_trace.py`。実変更ごとに直接関係するnodeだけ選び、旧hash/既読/公開gateの期待を除く。テストコード独立レビュー工程は追加しない。製品コード独立レビューは別agentで行う。

各所有範囲は同じworkspace。各担当は他者と同じrepoで作業し、他の変更を戻さず調整する。新技能の乱立や新しい汎用堅牢化frameworkは不要。

## 3. 既存未commitコード差分の保全

親から開始前変更と指定された3ファイルを `git diff` で読取確認した。`deployment.py` は94追加/4削除、`runtime_switcher.py` は114追加/30削除、`test_maintenance_deployment.py` は121追加/4削除（静的numstat観測、内容hash未計算）。

- `deployment.py:444 working_tree_content_sha256` の追加と `ManagedWorktreeManager.open` のdirty tracked許容が主眼。dirty自体を拒否しない方向は新要件と合うが、全tracked資材hashによる内容証明は採用2.0で不要になる。
- `runtime_switcher.py:_active_identity/_code_record/current_content_sha256/activate/confirm_active/healthy` はcommit/rootだけだったstateへcontent_sha256を追加し、反映中/反映後の内容変更を拒否する。この強化gateは採用2.0に合わせ撤去候補。既存dirty作業を破棄せず、必要操作だけ行う振る舞いへ調整する。
- 試験の `test_c38_fixed_root_allows_dirty_tracked_content_with_identity` は元 `test_c38_fixed_root_contract` から改名済み。後者を現行実在nodeに書かない。`test_runtime_switch_readback_rejects_content_mutated_during_switch` と `test_runtime_confirmation_rejects_content_changed_after_activation` は既存差分で追加されたhash gate期待なので、新完了要件のまま残せない。
- この3ファイルを一括stage、restore、旧HEADへ戻す作業はしない。実装担当は開始時差分を読んで自分の変更を重ね、差分の由来を記録する。今回調査では変更・stageしていない。

## 4. 技能・role・仕様・schemaの同期範囲

- 7担当: 調査=`.agents/skills/rr-inago-incident-investigation/`、実装=`common-implementation/`、試験=`common-testing/`、製品/HTMLレビュー=`common-review/`、反映=`rr-inago-maintenance-delivery/`、作者=`rr-inago-incident-report/`。各 `SKILL.md` と `references/contracts.md` の両方を確認する。`common-execution-planning/` は今後の計画契約と完全promptに旧hash gateが入らないよう同期。`rakuten-room-inago/SKILL.md` 等から保守を呼ぶ記述も静的参照確認対象。
- `.codex/agents/` の `rr-inago-incident-investigator.toml`、`common-implementer.toml`、`common-tester.toml`、`common-reviewer.toml`、`rr-inago-maintenance-delivery.toml`、`rr-inago-incident-reporter.toml`、`rr-inago-incident-report-reviewer.toml`。レビューは作者と別agent、全員effort=high。通常7担当に公開役を足さない。調査/計画reviewerのroleを通常直列に追加しない。実際の起動model/tier/fast設定は本人採用計画とroleでそろえる。
- 現行referencesには10/17固定、Git controller所有、HTML hash/正式attempt/receipt/公開完遂、controller fallback本文等が残る（例: `common-implementation/references/contracts.md:49,51,62`、`rr-inago-incident-report/references/contracts.md:29,45,51,55,61`、`common-testing/references/contracts.md:40,46`）。過去成果物の履歴は残し、現行技能規則だけ同期する。
- `AGENTS.md` のGit必須/ハッシュ試験記録と、本人2.0採用の「Gitは本人・対象ファイル/差分で記録」を同期。上位 `settings/language.md` はこのworkspaceには存在しなかった。言語は本人依頼どおり日本語。
- `docs/templates/incidents/日次不具合調査レポート_ひな形.md`、`日次不具合修正計画_ひな形.md`、`日次不具合対応レポート_ひな形.html`。本文4点、unknown明記、既存参照、作者最終path、別担当の実物reviewへ合わせる。hash/approved語の必須markerを新設しない。
- `docs/specifications/inago/{AT-MAINT-01_保守対応要件受入契約.md,保守運用設定仕様.md,要件追跡表.md,要件データ辞書.md,イナゴスキル要件定義書.html,図解/F12.svg}`。生成元 `tools/docs/inago-requirements/{task-flows,flow-descriptions,flow-layout,document-source,build}.mjs` と関連sourceを同期。`task-flows.mjs:68` は6 skills/8担当/3項目handoff/同bytes公開、`flow-descriptions.mjs:101,103` はtracking先/公開hashを残している。
- `maintenance/sql/001_initial.sql` はrelease case manifest/output hash、`002_job_manifest_ai_sessions.sql` はmanifest/role config hash NOT NULL、`005_repair_campaigns.sql` はhash/nonce/receipt/full snapshot制約とtrigger、`009_worker_queue_events.sql` はobservation hashを持つ。後続migrationで現行書込みに不要な制約を外す。既存schemaを過去行破壊で上書きしない。具体migration番号は実装着手時に最新SCHEMA_VERSIONを見て決める。
- hashを要求するmaintenance schemaの実在一覧と、生成地点の全行は後述の静的抽出。JSON schemaのresponse/handoffを残すためにJSON LLM返答を強制しない。

## 5. 試験計画の優先順と境界

現在の試験FAILはこの調査で実行確認していない。既知の実動問題は旧SC10 `final-report.md` に記録された `skill_chain_role_call_incomplete`、既読判定false、queuedのDB終端循環、whole-tree state不一致、TaskScheduler操作失敗、Git権限失敗である。SC10は10/17の過去結果であり10/20の原因を示さない。旧SCT06の診断試験PASSを現在版のPASSに転用しない。

| 順番 | 既存の完全node候補（ソース実在、未実行） | 期待/変更方針 |
|---|---|---|
| 局所1 | `tests_inago/test_maintenance_skill_chain.py::test_non_gating_status_and_early_end_continue_remaining_roles`、`::test_operation_failures_keep_raw_and_continue` | 既知の後続起動問題を最小fixtureで確認。先行操作失敗/旧statusでも7担当の次へ進み、実行していない成果を成功にしない。 |
| 局所2 | `tests_inago/test_maintenance_skill_chain.py::test_resume_same_job_without_predecessor_pin`、`::test_resume_after_final_review_reuses_author_and_reviewer_outputs` | 開始担当を明示する再開に変更。hash/既読/全artifact検査で再開位置を逆算しない。 |
| 局所3 | `tests_inago/test_maintenance_integration.py::test_workflow_block_status_does_not_block_worker_path`、`tests_inago/test_maintenance_store.py::test_job_completion_updates_incident_and_recovers_idempotently`、`::test_resume_maintenance_job_ignores_resume_preconditions` | job旧statusと実worker終了の循環を除去。実終了後の更新、DB失敗時の作業終了記録を確認。incident cause/recovery unknownを勝手にresolvedへしない。 |
| 局所4 | `tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes`、`::test_c24_generic_app_server_root_log_preserves_daily_fields` | 共有AppServer変更がある場合に終了回収とdaily互換を確認。safe logging失敗でcloseを飛ばさない。 |
| 局所5 | `tests_inago/test_maintenance_deployment.py::test_c38_fixed_root_allows_dirty_tracked_content_with_identity`、`::test_same_root_activation_repairs_stale_switch_state` | 既存差分を調整し、dirty fixed rootを扱いwhole-tree/commit/stateを完了gateにしない期待へ。名前が変更されれば新旧対応を記録。 |
| 局所6 | `tests_inago/test_maintenance_skill_chain.py::test_daily_gap_emits_useful_safe_diagnostics`、`tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location`、`::test_callback_secondary_exception_preserves_primary_failure_and_records_safe_diagnostic`、`::test_secondary_errors_reach_isolated_cli_l01_record_payload`、`::test_sqlite_error_code_reaches_primary_secondary_and_l01_payload` | code hashを要求せず、primary/secondary例外、capture位置、SQLite安全情報が届く。10/20原因調査後に追加した計装があれば、その最小fixtureを先に実施。 |

表の `::` 省略表記は直前の同じファイルを意味する。後述では全nodeにファイルを繰り返して載せる。parametrizeした実在関数は個別IDまで後述。今回の静的調査ではpytest collectionを行っていないためcollection成功とまでは主張しない。

**新設候補（まだ実在しない）**: `tests_inago/test_maintenance_skill_chain.py::test_seven_roles_wait_for_owned_worker_exit`、`::test_resume_uses_explicit_start_role_without_artifact_recheck`、`::test_author_writes_final_report_path_without_publication_worker`、`::test_html_findings_return_to_author_and_wait_for_all_extra_workers`、`::test_safe_stdout_stderr_and_response_exclude_secrets`、`tests_inago/test_maintenance_integration.py::test_queued_job_finishes_after_workers_exit_without_prior_db_complete`、`::test_db_finish_failure_keeps_actual_worker_exit_record`。新nodeを作る際に実際の関数名を確定し、実在確認後に完全nodeで実行する。既存試験名を新しい期待へ改名する選択も可能。

旧 `test_eight_roles_receive_exact_markdown`、`test_previous_read_acknowledgement_requires_successful_exact_path_event[...]`、`test_git_failure_preserves_scope_and_resumes[...]`、fingerprint/hash/readback必須試験は撤去/更新対象。旧仕様どおりPASSさせるため製品gateを残さない。旧report publication fixtureは新経路への非到達や作者最終保存の試験に置換し、外部pushを新実動に要求しない。

最終関係回帰は、局所失敗を直し終えてから一度だけ実行する。候補ファイルは実際の変更に合わせ `test_maintenance_skill_chain.py`、`test_maintenance_integration.py`、`test_maintenance_store.py`、`test_maintenance_deployment.py`、`test_maintenance_ai.py`、`test_maintenance_docs.py`、`test_daily_pipeline_safe_diagnostics.py`。legacy削除/互換変更に直接関係するreport/provider/FC07/schema試験だけ追加し、無関係の日次試験を一括実行しない。最終回帰失敗時は失敗nodeと直接影響nodeへ戻り、修正後に最終関係回帰を再び一度。記録は完全command/node、対象ファイル/差分参照、期待、実結果、整数exit、開始終了時刻。hashなし。現時点は全て未実行。

計画用の最小command例（未実行、fixture専用tmpを使用）:

```powershell
& .venv-inago/Scripts/python.exe -B -m pytest 'tests_inago/test_maintenance_skill_chain.py::test_non_gating_status_and_early_end_continue_remaining_roles' -q --basetemp .pytest-tmp/simplification-focused --junitxml=worklogs/projects/maintenance/skill-chain-simplification-20261021/tests/focused.junit.xml
```

最終関係回帰commandの候補（未実行。変更が確定した時点で直接関係する追加node/fileを確定してから一度実施）:

```powershell
& .venv-inago/Scripts/python.exe -B -m pytest tests_inago/test_maintenance_skill_chain.py tests_inago/test_maintenance_integration.py tests_inago/test_maintenance_store.py tests_inago/test_maintenance_deployment.py tests_inago/test_maintenance_ai.py tests_inago/test_maintenance_docs.py tests_inago/test_daily_pipeline_safe_diagnostics.py -q --basetemp .pytest-tmp/simplification-final --junitxml=worklogs/projects/maintenance/skill-chain-simplification-20261021/tests/final.junit.xml
```

## 6. Windows実CLI・実動確認の方法（今回未実行）

- 実在パス確認だけ実施済み: `.venv-inago/Scripts/python.exe`、同site-packagesのpytest/jsonschema/tzdataの `__init__.py`。`Get-Command` でPATH pythonはuv配下3.12、codexは `C:\Users\ute3g\AppData\Local\OpenAI\Codex\bin\2e5e00daee91c61d\codex.exe`。存在は起動成功/認証成功/機能対応の証明ではない。`.venv-inago` を使う。requirementsはjsonschema[format]>=4.23,<5、tzdata>=2026.1、test要件pytest>=8.3,<10など。実importや--version/probeは行っていない。
- 将来の実行は固定cwdで所有CLIを起動し、Popen/Start-Processならhidden、stdout/stderrをsafe sinkへ通し、WaitForExit/`wait()` とexit codeを回収する。AppServerは `turn/completed` と `process.poll()/wait()` の両方を記録。追加作者/再review/追加製品修正workerを一覧に含める。単に「終了」と応答したことやMD更新時刻を終了証拠にしない。
- 一つの実行ログにjob、担当番号、開始終了、turn/thread ID（得られる場合）、所有PID、終了code、ログpath、操作失敗を追記。ログ保存失敗そのものは作業gateにせず、可能な戻り値/別safe出力へ実結果を残す。hash・receipt・全成果物検査を戻さない。
- safeログfixtureはtoken/cookie/password/SQL parameters/商品投稿本文/例外本文のcanaryを使い、許可欄と漏えいなしを確認する。regex blacklist一つで原文保存を正当化せず、許可欄による要約を基本とする。AppServer stderrは現状破棄なのでsafe取得経路は新実装が必要。
- 本番実動は10/20実在launch `fa3292ae0c7e43bb9569576fcb1a5d85` と既存queue/source_run_id=nullを使う（根拠: 同ディレクトリ `oct20-current-facts.md` OCT20-F01/F07/F15）。受付時に既存jobを再確認し、無ければ正規初回incident/check/job関連付けを作り実採番job IDを後続へ渡す。架空ID、10/17/historical代用、代替job乱立は行わない。ID/identity保存にhash必須の旧primitiveをそのまま呼ばず、新仕様へ直して使う。調査→必要な本番修正または診断計装→局所試験→関係回帰→別担当製品review→必要反映→作者の最終HTML→別担当実物review→必要な作者修正/再読→全起動worker終了確認まで進める。10/19のqueue/history、通常Daily手動起動、日次run/draft追生成は禁止。次のDaily時刻待ち、同job新Dailyログ、Git、原因known、旧statusを完了条件にしない。
- 実際の10/20原因と変更する日次処理の特定、実Scheduler操作の必要性・成功、実worker終了、実HTML内容の最終確認はこのコードinventoryでは未確認。調査・実装・実動各担当の記録が必要。

## 7. ハッシュ全経路分類と撤去残存防止

分類A=入力/出力/設定/コード/証拠/HTMLの生成・照合、B=hash由来ID/filename/session key、C=nonce/token/receipt/campaign/manifestの証明、D=全tree/Git/runtime state、E=日次source診断fingerprint。保守配下にあるA〜Dは現行経路から撤去し、残った未使用コードもhash helperの再流入先にしない。Eは追加診断部分とschema/testを同期して除く。既存IDに含まれる過去hash文字列や過去記録は再計算せず履歴/既存識別子として扱う。

| ファイル | 主な分類 |
|---|---|
| skill_chain、ai_adapter | A設定/応答/入力、B session scope、C同一実行証明 |
| code_revision、failure_journal、diagnostic_trace | D全source fingerprintとその伝播 |
| controller、investigation、planning、repair、production_workflow | A全stage artifact/diff/test/code、B job/check/fact/candidate/input file、C試験/レビューreceipt |
| evidence、historical、worker | A evidence/observation、B historical source/incident/dispatch |
| deployment、runtime_switcher | A差分、D全tree/state/commit/候補照合 |
| storeとsql | A artifact、B receipt ID等、C manifest/nonce/token/receipt/full snapshotと制約 |
| reporting、report_fallback、workflow_reconciliation | A HTML/result/publication、B report filename、D Git remote blob |
| probe | A source/product diff、C試験/モデル/role証明 |
| cli | A production config hash |
| daily_pipeline、orchestrator診断、trace | E source code hashと型・pattern欄 |

撤去確認は静的検索で生成（hashlib/hexdigest/createHash等）、照合/必須欄（sha256/hash/digest/fingerprint）、旧関数参照、role prompt/reference/schemaの3方向を読む。内容hashを実計算して確認しない。既存business hashは用途を注記して対象から除外するが、保守がその値を入力/出力gateに使う呼出しは残さない。下の抽出はソース文字列の検索結果で、計算結果ではない。

周辺toolにも注意する。`tools/docs/inago-requirements/review.mjs:13`、`review-1.3.mjs:13`、`finish-daily-review.mjs:15` はHTML hashを計算するため、本人のハッシュ禁止下でそのまま機械実行しない。`build.mjs` には今回の静的検索でhash生成式は見つからなかったが、実行する場合は現在のimport先も確認する。この調査ではどの生成/レビューtoolも実行していない。`operations.py:273` のdigestは日次進捗観測の重複判定であり、保守の全tree/HTML一致とは別用途。新保守フローからそれを版証明・完了gateへ転用しない。

## 8. 静的全生成地点（実計算なし）

保守配下Python全ファイルの文字列検索。生成開始/digest確定の全地点。照合/必須欄は§7と併せて確認する。


### ai_adapter.py

- `automation/inago/maintenance/ai_adapter.py:378` `_role_config`: `hashlib.sha256(raw_config).hexdigest(),`
- `automation/inago/maintenance/ai_adapter.py:476` `plan_session`: `session_config_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/ai_adapter.py:492` `plan_session`: `).hexdigest()`
- `automation/inago/maintenance/ai_adapter.py:515` `plan_session`: `session_scope = hashlib.sha256(`
- `automation/inago/maintenance/ai_adapter.py:517` `plan_session`: `).hexdigest()[:24]`
- `automation/inago/maintenance/ai_adapter.py:1080` `_persist_response`: `return resolved, hashlib.sha256(encoded).hexdigest()`

### cli.py

- `automation/inago/maintenance/cli.py:263` `_run_read_only_preflight`: `"config_sha256": hashlib.sha256(config_bytes).hexdigest(),`

### code_revision.py

- `automation/inago/maintenance/code_revision.py:111` `capture_invocation_code_revision`: `{"path": relative, "sha256": hashlib.sha256(content).hexdigest()}`
- `automation/inago/maintenance/code_revision.py:173` `capture_invocation_code_revision`: `digest = hashlib.sha256(encoded).hexdigest()`

### controller.py

- `automation/inago/maintenance/controller.py:1680` `_load_daily_completion`: `hashlib.sha256(historical.intake_record_bytes).hexdigest(),`
- `automation/inago/maintenance/controller.py:1723` `_load_daily_completion`: `hashlib.sha256(daily_launch.result_json.encode("utf-8")).hexdigest(),`

### deployment.py

- `automation/inago/maintenance/deployment.py:243` `observe_pending_action_snapshot`: `revision = hashlib.sha256(`
- `automation/inago/maintenance/deployment.py:245` `observe_pending_action_snapshot`: `).hexdigest()`
- `automation/inago/maintenance/deployment.py:455` `working_tree_content_sha256`: `digest = hashlib.sha256()`
- `automation/inago/maintenance/deployment.py:504` `working_tree_content_sha256`: `return digest.hexdigest()`
- `automation/inago/maintenance/deployment.py:745` `verify_candidate`: `observed_hash = hashlib.sha256(`
- `automation/inago/maintenance/deployment.py:753` `verify_candidate`: `).hexdigest()`
- `automation/inago/maintenance/deployment.py:764` `prepare_repair_candidate_commit`: `source_diff_hash = hashlib.sha256(`
- `automation/inago/maintenance/deployment.py:772` `prepare_repair_candidate_commit`: `).hexdigest()`

### evidence.py

- `automation/inago/maintenance/evidence.py:1403` `collect_evidence`: `artifact["redacted_sha256"] = hashlib.sha256(encoded).hexdigest()`
- `automation/inago/maintenance/evidence.py:1833` `snapshot_sqlite`: `digest_state = hashlib.sha256()`
- `automation/inago/maintenance/evidence.py:1837` `snapshot_sqlite`: `digest = digest_state.hexdigest()`

### historical.py

- `automation/inago/maintenance/historical.py:273` `enqueue_historical_observation`: `if hashlib.sha256(evidence_bytes).hexdigest() != observation["evidence_sha256"]:`
- `automation/inago/maintenance/historical.py:277` `enqueue_historical_observation`: `source_digest = hashlib.sha256(`
- `automation/inago/maintenance/historical.py:279` `enqueue_historical_observation`: `).hexdigest()[:32]`
- `automation/inago/maintenance/historical.py:281` `enqueue_historical_observation`: `incident_digest = hashlib.sha256(`
- `automation/inago/maintenance/historical.py:283` `enqueue_historical_observation`: `).hexdigest()[:32]`
- `automation/inago/maintenance/historical.py:339` `enqueue_historical_observation`: `"observation_sha256": hashlib.sha256(source_bytes).hexdigest(),`
- `automation/inago/maintenance/historical.py:456` `_read_historical_source_documents`: `or hashlib.sha256(encoded).hexdigest() != expected_sha256`
- `automation/inago/maintenance/historical.py:614` `load_historical_work_item`: `observation_sha256 = hashlib.sha256(observation_bytes).hexdigest()`
- `automation/inago/maintenance/historical.py:615` `load_historical_work_item`: `evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()`

### investigation.py

- `automation/inago/maintenance/investigation.py:799` `save_attempt_artifact`: `return relative_path, hashlib.sha256(encoded).hexdigest()`
- `automation/inago/maintenance/investigation.py:814` `save_attempt_artifact`: `return relative_path, hashlib.sha256(encoded).hexdigest()`
- `automation/inago/maintenance/investigation.py:835` `save_attempt_artifact`: `return relative_path, hashlib.sha256(encoded).hexdigest()`
- `automation/inago/maintenance/investigation.py:853` `save_attempt_artifact`: `return relative_path, hashlib.sha256(encoded).hexdigest()`

### probe.py

- `automation/inago/maintenance/probe.py:1385` `run_ai_probe`: `digest = hashlib.sha256((project_root / relative).read_bytes()).hexdigest()`
- `automation/inago/maintenance/probe.py:1670` `run_ai_probe`: `evidence["product_diff_sha256"] = hashlib.sha256(`
- `automation/inago/maintenance/probe.py:1672` `run_ai_probe`: `).hexdigest()`
- `automation/inago/maintenance/probe.py:1712` `run_ai_probe`: `product_diff_hash_after_test = hashlib.sha256(`
- `automation/inago/maintenance/probe.py:1714` `run_ai_probe`: `).hexdigest()`

### production_workflow.py

- `automation/inago/maintenance/production_workflow.py:169` `_precheck_identity`: `suffix = hashlib.sha256(launch_id.encode("utf-8")).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:479` `run_daily_check_incident_stage`: `reason_digest = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:481` `run_daily_check_incident_stage`: `).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:1896` `run_daily_repair_review_input_stage`: `product_diff_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:1904` `run_daily_repair_review_input_stage`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:2841` `run_daily_evidence_stage`: `shard = hashlib.sha256(launch.source_launch_id.encode("utf-8")).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:2981` `run_daily_evidence_stage`: `workflow.state["evidence_bundle_sha256"] = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:2983` `run_daily_evidence_stage`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:3068` `run_daily_job_stage`: `job_digest = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:3070` `run_daily_job_stage`: `).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:3465` `_historical_investigation_snapshot`: `"sha256": hashlib.sha256(historical.observation_bytes).hexdigest(),`
- `automation/inago/maintenance/production_workflow.py:3470` `_historical_investigation_snapshot`: `"sha256": hashlib.sha256(historical.evidence_bytes).hexdigest(),`
- `automation/inago/maintenance/production_workflow.py:3578` `_read_daily_investigation_evidence`: `if hashlib.sha256(manifest_bytes).hexdigest() != expected_hash:`
- `automation/inago/maintenance/production_workflow.py:3630` `_read_daily_investigation_evidence`: `if hashlib.sha256(artifact_bytes).hexdigest() != artifact_digest:`
- `automation/inago/maintenance/production_workflow.py:4592` `_planning_requirement_and_case_inputs`: `digest = hashlib.sha256(fact_id.encode("utf-8")).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:6074` `_daily_repair_setup_document`: `case_manifest_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:6081` `_daily_repair_setup_document`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:6193` `_read_daily_repair_attempt_artifact`: `return content, hashlib.sha256(content).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:6968` `_ensure_daily_repair_handoff`: `expected_product_diff_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:6976` `_ensure_daily_repair_handoff`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:7143` `_ensure_daily_repair_handoff`: `evidence_shard = hashlib.sha256(launch_id.encode("utf-8")).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:7307` `prepare_daily_deployment_candidate_stage`: `candidate_key = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:7316` `prepare_daily_deployment_candidate_stage`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:7404` `prepare_daily_deployment_candidate_stage`: `shard = hashlib.sha256(launch_id.encode("utf-8")).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:7875` `run_daily_deployment_execution_stage`: `evidence_shard = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:7877` `run_daily_deployment_execution_stage`: `).hexdigest()[:24]`
- `automation/inago/maintenance/production_workflow.py:7940` `run_daily_deployment_execution_stage`: `+ hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:7944` `run_daily_deployment_execution_stage`: `).hexdigest()[:40]`
- `automation/inago/maintenance/production_workflow.py:7960` `run_daily_deployment_execution_stage`: `+ hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:7964` `run_daily_deployment_execution_stage`: `).hexdigest()[:40]`
- `automation/inago/maintenance/production_workflow.py:7982` `run_daily_deployment_execution_stage`: `+ hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:7986` `run_daily_deployment_execution_stage`: `).hexdigest()[:40]`
- `automation/inago/maintenance/production_workflow.py:8614` `prepare_daily_report_inputs_stage`: `deployment_sha256 = hashlib.sha256(raw).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:9064` `_daily_report_read_bytes`: `and hashlib.sha256(raw).hexdigest() != expected_sha256`
- `automation/inago/maintenance/production_workflow.py:9211` `_daily_report_ai_envelopes`: `digest = hashlib.sha256(raw).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:9927` `_persist_minimal_daily_report`: `input_name = "report-input-fallback-" + hashlib.sha256(input_bytes.encode("utf-8")).hexdigest()[:20] + ".json"`
- `automation/inago/maintenance/production_workflow.py:9999` `_persist_minimal_daily_report`: `review_input_name = "review-input-fallback-" + hashlib.sha256(review_input_bytes.encode("utf-8")).hexdigest()[:20] + ".json"`
- `automation/inago/maintenance/production_workflow.py:10064` `_persist_minimal_daily_report`: `revision_input = {**input_document, "result_version": "fallback-revision-" + hashlib.sha256((html_sha256 + "\n".join(revision_findings)).encode("utf-8")).hexdigest()[:20], "created_at_jst": created_at, "prior_findings": revision_findings, "reviewed_html_path": html_path, "reviewed_html_sha256": html_sha256}`
- `automation/inago/maintenance/production_workflow.py:10239` `_persist_minimal_daily_report`: `"revision_input_sha256": hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:10246` `_persist_minimal_daily_report`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:10475` `run_daily_report_stage`: `source_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:10483` `run_daily_report_stage`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:11470` `_restore_completed_daily_report_context`: `computed_source_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/production_workflow.py:11478` `_restore_completed_daily_report_context`: `).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:11905` `_daily_report_replaceable_prior_publication_sha256`: `if hashlib.sha256(prior_html).hexdigest() != existing_sha256:`
- `automation/inago/maintenance/production_workflow.py:11940` `_daily_report_replaceable_prior_publication_sha256`: `and hashlib.sha256(prior_blob.stdout).hexdigest() == existing_sha256`
- `automation/inago/maintenance/production_workflow.py:12111` `run_daily_report_publication_stage`: `job_key = hashlib.sha256(job.job_id.encode("utf-8")).hexdigest()[:20]`
- `automation/inago/maintenance/production_workflow.py:12929` `_read_daily_repair_review_result`: `digest = hashlib.sha256(encoded).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:13001` `_read_daily_repair_author_result`: `digest = hashlib.sha256(encoded).hexdigest()`
- `automation/inago/maintenance/production_workflow.py:13064` `_daily_repair_product_file_hashes`: `hashes[path] = hashlib.sha256(content).hexdigest()`

### repair.py

- `automation/inago/maintenance/repair.py:406` `_assertion_snapshot_hash`: `return hashlib.sha256(encoded).hexdigest()`
- `automation/inago/maintenance/repair.py:1808` `repository_code_sha256`: `digest = hashlib.sha256()`
- `automation/inago/maintenance/repair.py:1828` `repository_code_sha256`: `return digest.hexdigest()`
- `automation/inago/maintenance/repair.py:2664` `__call__`: `signatures[case_id] = hashlib.sha256(`
- `automation/inago/maintenance/repair.py:2666` `__call__`: `).hexdigest()`

### report_fallback.py

- `automation/inago/maintenance/report_fallback.py:187` `build_fallback_report`: `digest = hashlib.sha256(body.encode("utf-8")).hexdigest()`

### reporting.py

- `automation/inago/maintenance/reporting.py:1367` `prepare_incident_report`: `html_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()`
- `automation/inago/maintenance/reporting.py:1405` `prepare_incident_report`: `result_hash = hashlib.sha256(result_json.encode("utf-8")).hexdigest()`
- `automation/inago/maintenance/reporting.py:1491` `finalize_report_review`: `result_sha256=hashlib.sha256(result_json.encode("utf-8")).hexdigest(),`
- `automation/inago/maintenance/reporting.py:1624` `_git_content_sha256`: `return hashlib.sha256(completed.stdout).hexdigest()`
- `automation/inago/maintenance/reporting.py:1923` `publish_reviewed_report`: `if hashlib.sha256(content).hexdigest() != reviewed["reviewed_sha256"]:`
- `automation/inago/maintenance/reporting.py:1978` `publish_reviewed_report`: `if hashlib.sha256(content).hexdigest() != reviewed["reviewed_sha256"]:`

### skill_chain.py

- `automation/inago/maintenance/skill_chain.py:114` `_sha256`: `return hashlib.sha256(raw).hexdigest()`

### store.py

- `automation/inago/maintenance/store.py:646` `_validated_daily_result_observation`: `return document, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()`
- `automation/inago/maintenance/store.py:1054` `_read_handoff_artifact`: `if hashlib.sha256(content).hexdigest() != expected_sha256:`
- `automation/inago/maintenance/store.py:1363` `_manifest_digest`: `return hashlib.sha256(payload.encode("utf-8")).hexdigest()`
- `automation/inago/maintenance/store.py:2171` `_record_host_repair_review_execution`: `host_receipt_id = hashlib.sha256(`
- `automation/inago/maintenance/store.py:2178` `_record_host_repair_review_execution`: `).hexdigest()`
- `automation/inago/maintenance/store.py:2285` `_repair_host_review_execution_document`: `receipt_id = hashlib.sha256(`
- `automation/inago/maintenance/store.py:2292` `_repair_host_review_execution_document`: `).hexdigest()`
- `automation/inago/maintenance/store.py:2450` `record_repair_review_receipt`: `receipt_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/store.py:2457` `record_repair_review_receipt`: `).hexdigest()`
- `automation/inago/maintenance/store.py:2549` `_repair_review_receipt_document`: `digest = hashlib.sha256(`
- `automation/inago/maintenance/store.py:2556` `_repair_review_receipt_document`: `).hexdigest()`
- `automation/inago/maintenance/store.py:2821` `get_daily_result_observation`: `digest = hashlib.sha256(record.observation_json.encode("utf-8")).hexdigest()`
- `automation/inago/maintenance/store.py:4035` `create_release`: `manifest_hash = hashlib.sha256(`
- `automation/inago/maintenance/store.py:4037` `create_release`: `).hexdigest()`
- `automation/inago/maintenance/store.py:4180` `register_repair_candidate_release`: `manifest_hash = hashlib.sha256(`
- `automation/inago/maintenance/store.py:4182` `register_repair_candidate_release`: `).hexdigest()`
- `automation/inago/maintenance/store.py:4919` `_evaluate_release_gate`: `manifest_hash = hashlib.sha256(`
- `automation/inago/maintenance/store.py:4923` `_evaluate_release_gate`: `).hexdigest()`
- `automation/inago/maintenance/store.py:6978` `_repair_campaign_snapshot`: `or hashlib.sha256(canonical_snapshot.encode("ascii")).hexdigest()`
- `automation/inago/maintenance/store.py:7433` `_begin_repair_case_run`: `nonce_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()`
- `automation/inago/maintenance/store.py:7717` `_record_repair_case_receipt`: `nonce_hash = hashlib.sha256(nonce.encode("ascii", errors="strict")).hexdigest()`
- `automation/inago/maintenance/store.py:7765` `_record_repair_case_receipt`: `receipt_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/store.py:7772` `_record_repair_case_receipt`: `).hexdigest()`
- `automation/inago/maintenance/store.py:7989` `_repair_receipt_document`: `expected_receipt_hash = hashlib.sha256(`
- `automation/inago/maintenance/store.py:7996` `_repair_receipt_document`: `).hexdigest()`
- `automation/inago/maintenance/store.py:8050` `begin_repair_full_regression`: `token = hashlib.sha256(uuid.uuid4().bytes).hexdigest()`
- `automation/inago/maintenance/store.py:8084` `begin_repair_full_regression`: `snapshot_hash = hashlib.sha256(snapshot.encode("ascii")).hexdigest()`
- `automation/inago/maintenance/store.py:8304` `_claim_repair_runner_nonce`: `nonce_hash = hashlib.sha256(nonce_value.encode("ascii")).hexdigest()`
- `automation/inago/maintenance/store.py:8454` `_record_repair_run_receipt`: `nonce_hash = hashlib.sha256(nonce.encode("ascii", errors="strict")).hexdigest()`
- `automation/inago/maintenance/store.py:8483` `_record_repair_run_receipt`: `receipt_sha256 = hashlib.sha256(`
- `automation/inago/maintenance/store.py:8490` `_record_repair_run_receipt`: `).hexdigest()`
- `automation/inago/maintenance/store.py:8643` `consume_repair_run_receipt`: `or hashlib.sha256(`
- `automation/inago/maintenance/store.py:8645` `consume_repair_run_receipt`: `).hexdigest()`

### worker.py

- `automation/inago/maintenance/worker.py:288` `_historical_queue_context`: `dispatch_id = hashlib.sha256(reference.job_id.encode("utf-8")).hexdigest()[:24]`

### workflow_reconciliation.py

- `automation/inago/maintenance/workflow_reconciliation.py:135` `_deployment_result_matches_attempt`: `candidate_key = hashlib.sha256(`
- `automation/inago/maintenance/workflow_reconciliation.py:144` `_deployment_result_matches_attempt`: `).hexdigest()`
- `automation/inago/maintenance/workflow_reconciliation.py:405` `_published_report_readback`: `html_sha256 = hashlib.sha256(html_bytes).hexdigest()`
- `automation/inago/maintenance/workflow_reconciliation.py:419` `_published_report_readback`: `f"{hashlib.sha256(job_id.encode('utf-8')).hexdigest()[:20]}.html"`
- `automation/inago/maintenance/workflow_reconciliation.py:474` `_published_report_readback`: `"publication_sha256": hashlib.sha256(publication_bytes).hexdigest(),`
- `automation/inago/maintenance/workflow_reconciliation.py:534` `failure_handoff`: `"sha256": hashlib.sha256(raw).hexdigest(),`
- `automation/inago/maintenance/workflow_reconciliation.py:641` `_attempt_and_deployment_outputs`: `"output_sha256": hashlib.sha256(content).hexdigest(),`
- `automation/inago/maintenance/workflow_reconciliation.py:734` `_attempt_and_deployment_outputs`: `"sha256": hashlib.sha256(raw).hexdigest(),`

## 9. hash関連maintenance schema実在一覧

- `automation/inago/schemas/maintenance-deployment-candidate.schema.json`
- `automation/inago/schemas/maintenance-evidence.schema.json`
- `automation/inago/schemas/maintenance-repair-handoff.schema.json`
- `automation/inago/schemas/maintenance-repair-patch-assessment.schema.json`
- `automation/inago/schemas/maintenance-repair-result.schema.json`
- `automation/inago/schemas/maintenance-repair-review-claim.schema.json`
- `automation/inago/schemas/maintenance-repair-review-target.schema.json`
- `automation/inago/schemas/maintenance-repair-review.schema.json`
- `automation/inago/schemas/maintenance-repair-test-evidence.schema.json`
- `automation/inago/schemas/maintenance-repair-test-ledger.schema.json`
- `automation/inago/schemas/maintenance-report-response.schema.json`
- `automation/inago/schemas/maintenance-report-result.schema.json`
- `automation/inago/schemas/maintenance-report-review.schema.json`

## 10. 実在確認した完全test node索引

AST定義/parametrize literalから静的展開。pytest収集/実行は未実施。パラメーターIDは通常pytest表示からの候補で、実施時はcollection表示も記録する。新設候補は含めない。旧gate期待の定義は削除/更新対象。


### test_maintenance_skill_chain.py

- `tests_inago/test_maintenance_skill_chain.py::test_eight_roles_receive_exact_markdown` — 定義 `tests_inago/test_maintenance_skill_chain.py:260`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_non_gating_status_and_early_end_continue_remaining_roles` — 定義 `tests_inago/test_maintenance_skill_chain.py:303`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_operation_failures_keep_raw_and_continue` — 定義 `tests_inago/test_maintenance_skill_chain.py:321`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_resume_same_job_without_predecessor_pin` — 定義 `tests_inago/test_maintenance_skill_chain.py:339`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_resume_after_final_review_reuses_author_and_reviewer_outputs` — 定義 `tests_inago/test_maintenance_skill_chain.py:359`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_appserver_best_effort_logging_and_advisory_probe` — 定義 `tests_inago/test_maintenance_skill_chain.py:429`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_scope_preserves_excluded_date_and_daily_data` — 定義 `tests_inago/test_maintenance_skill_chain.py:451`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_production_route_is_separate_from_legacy_controller` — 定義 `tests_inago/test_maintenance_skill_chain.py:477`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_contract_roles_and_generated_flow_are_synchronized` — 定義 `tests_inago/test_maintenance_skill_chain.py:486`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_git_failure_preserves_scope_and_resumes[available]` — 定義 `tests_inago/test_maintenance_skill_chain.py:503`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_git_failure_preserves_scope_and_resumes[unavailable]` — 定義 `tests_inago/test_maintenance_skill_chain.py:503`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_reporter_failure_gets_same_author_recovery_then_later_roles[success-transport_failure]` — 定義 `tests_inago/test_maintenance_skill_chain.py:540`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_reporter_failure_gets_same_author_recovery_then_later_roles[success-invalid_handoff]` — 定義 `tests_inago/test_maintenance_skill_chain.py:540`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_reporter_failure_gets_same_author_recovery_then_later_roles[success-omit_report_html]` — 定義 `tests_inago/test_maintenance_skill_chain.py:540`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_reporter_failure_gets_same_author_recovery_then_later_roles[failure-transport_failure]` — 定義 `tests_inago/test_maintenance_skill_chain.py:540`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_reporter_failure_gets_same_author_recovery_then_later_roles[failure-invalid_handoff]` — 定義 `tests_inago/test_maintenance_skill_chain.py:540`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_reporter_failure_gets_same_author_recovery_then_later_roles[failure-omit_report_html]` — 定義 `tests_inago/test_maintenance_skill_chain.py:540`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_previous_read_acknowledgement_requires_successful_exact_path_event[success]` — 定義 `tests_inago/test_maintenance_skill_chain.py:573`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_previous_read_acknowledgement_requires_successful_exact_path_event[missing]` — 定義 `tests_inago/test_maintenance_skill_chain.py:573`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_previous_read_acknowledgement_requires_successful_exact_path_event[failed]` — 定義 `tests_inago/test_maintenance_skill_chain.py:573`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_previous_read_acknowledgement_requires_successful_exact_path_event[mismatched]` — 定義 `tests_inago/test_maintenance_skill_chain.py:573`、未実行。
- `tests_inago/test_maintenance_skill_chain.py::test_daily_gap_emits_useful_safe_diagnostics` — 定義 `tests_inago/test_maintenance_skill_chain.py:595`、未実行。

### test_maintenance_integration.py

- `tests_inago/test_maintenance_integration.py::test_failure_journal_dual_sink_failure_does_not_gate_tracked_operation` — 定義 `tests_inago/test_maintenance_integration.py:340`、未実行。
- `tests_inago/test_maintenance_integration.py::test_worker_block_and_explicit_resume_are_durable` — 定義 `tests_inago/test_maintenance_integration.py:628`、未実行。
- `tests_inago/test_maintenance_integration.py::test_workflow_block_status_does_not_block_worker_path` — 定義 `tests_inago/test_maintenance_integration.py:671`、未実行。

### test_maintenance_store.py

- `tests_inago/test_maintenance_store.py::test_job_completion_updates_incident_and_recovers_idempotently` — 定義 `tests_inago/test_maintenance_store.py:468`、未実行。
- `tests_inago/test_maintenance_store.py::test_c07_terminal_job_closes_running_attempt` — 定義 `tests_inago/test_maintenance_store.py:609`、未実行。
- `tests_inago/test_maintenance_store.py::test_resume_maintenance_job_ignores_resume_preconditions` — 定義 `tests_inago/test_maintenance_store.py:1162`、未実行。
- `tests_inago/test_maintenance_store.py::test_schema_15_upgrade_drops_only_optional_repair_predecessor_trigger` — 定義 `tests_inago/test_maintenance_store.py:1282`、未実行。

### test_maintenance_deployment.py

- `tests_inago/test_maintenance_deployment.py::test_same_root_activation_repairs_stale_switch_state` — 定義 `tests_inago/test_maintenance_deployment.py:188`、未実行。
- `tests_inago/test_maintenance_deployment.py::test_runtime_confirmation_failure_does_not_finalize_deployment` — 定義 `tests_inago/test_maintenance_deployment.py:265`、未実行。
- `tests_inago/test_maintenance_deployment.py::test_c38_fixed_root_allows_dirty_tracked_content_with_identity` — 定義 `tests_inago/test_maintenance_deployment.py:304`、未実行。
- `tests_inago/test_maintenance_deployment.py::test_runtime_switch_readback_rejects_content_mutated_during_switch` — 定義 `tests_inago/test_maintenance_deployment.py:319`、未実行。
- `tests_inago/test_maintenance_deployment.py::test_runtime_confirmation_rejects_content_changed_after_activation` — 定義 `tests_inago/test_maintenance_deployment.py:376`、未実行。

### test_maintenance_ai.py

- `tests_inago/test_maintenance_ai.py::test_c27_app_server_log_is_confined_to_host_owned_root` — 定義 `tests_inago/test_maintenance_ai.py:936`、未実行。
- `tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes` — 定義 `tests_inago/test_maintenance_ai.py:1217`、未実行。
- `tests_inago/test_maintenance_ai.py::test_c24_generic_app_server_root_log_preserves_daily_fields` — 定義 `tests_inago/test_maintenance_ai.py:1388`、未実行。

### test_daily_pipeline_safe_diagnostics.py

- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:111`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_callback_secondary_exception_preserves_primary_failure_and_records_safe_diagnostic` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:399`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_exception_chain_truncation_boundary_is_reported_without_unsafe_data` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:518`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_hash_failure_is_recorded_as_unknown` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:564`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_each_retry_summary_query_failure_keeps_result_and_null_fallbacks[retry_summary_room_attempts-retry_summary_room_attempts]` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:580`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_each_retry_summary_query_failure_keeps_result_and_null_fallbacks[retry_summary_trend_acquisitions-retry_summary_trend_acquisitions]` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:580`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_each_retry_summary_query_failure_keeps_result_and_null_fallbacks[retry_summary_trend_attempts-retry_summary_trend_attempts]` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:580`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_secondary_errors_reach_isolated_cli_l01_record_payload` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:808`、未実行。
- `tests_inago/test_daily_pipeline_safe_diagnostics.py::test_sqlite_error_code_reaches_primary_secondary_and_l01_payload` — 定義 `tests_inago/test_daily_pipeline_safe_diagnostics.py:869`、未実行。

### test_maintenance_workflow_provider.py

- `tests_inago/test_maintenance_workflow_provider.py::test_provider_loads_existing_job_and_attempt_before_running_stages` — 定義 `tests_inago/test_maintenance_workflow_provider.py:66`、未実行。
- `tests_inago/test_maintenance_workflow_provider.py::test_completed_job_resume_runs_only_report_stage` — 定義 `tests_inago/test_maintenance_workflow_provider.py:343`、未実行。
- `tests_inago/test_maintenance_workflow_provider.py::test_repair_execution_stays_in_fixed_root_and_preserves_existing_edits` — 定義 `tests_inago/test_maintenance_workflow_provider.py:441`、未実行。
- `tests_inago/test_maintenance_workflow_provider.py::test_worker_cli_connects_provider_and_blocks_without_ready_release` — 定義 `tests_inago/test_maintenance_workflow_provider.py:1739`、未実行。

### test_maintenance_flow_completion_fc07.py

- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[check_incident]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[evidence]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[job]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[investigation]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[planning]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[repair]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[deployment]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_stage_failure_keeps_handoff_and_runs_every_later_stage[report]` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:122`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_nested_failure_is_correlated_redacted_and_replayed` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:717`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_fallback_report_and_october_19_exclusion_are_preserved` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:829`、未実行。
- `tests_inago/test_maintenance_flow_completion_fc07.py::test_fc07_invocation_revision_fingerprint_changes_with_maintenance_source` — 定義 `tests_inago/test_maintenance_flow_completion_fc07.py:862`、未実行。

### test_maintenance_docs.py

- `tests_inago/test_maintenance_docs.py::test_c60_contract` — 定義 `tests_inago/test_maintenance_docs.py:15`、未実行。

### test_maintenance_completion.py

- `tests_inago/test_maintenance_completion.py::test_daily_child_pid_is_recorded_and_process_is_joined` — 定義 `tests_inago/test_maintenance_completion.py:335`、未実行。

### test_daily_fix_trace.py

- `tests_inago/test_daily_fix_trace.py::test_n29` — 定義 `tests_inago/test_daily_fix_trace.py:405`、未実行。
