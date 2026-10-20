# STEP01 実装記録 — 移行製品・契約

対象: 計画1.1 STEP01、target_date `2026-10-20`、source_launch_id `fa3292ae0c7e43bb9569576fcb1a5d85`、source_run_id `null`、queue `37a2be6b24fa4ffb8f859012e00a209a`、check `18fd9a8b-343b-4c66-addb-a1bce71ecf83`。

## 実施内容

- 通常担当を調査→実装→試験→独立製品review→本番反映→HTML作成→独立HTML reviewの7 roleへ整理し、担当番号による再開を実装しました。全roleは `gpt-6-luna` / effort `high`、priority/Fast要求を持ちます。
- `worker --once --source-launch-id` は指定launchのworker経路から新runnerを呼び、該当maintenance DBが存在する場合にだけ既知migrationを適用してから開始します。missing DBをworkerが新規作成する経路にはしていません。
- runnerは各担当のturn終端、応答、stdoutイベント要約、安全化済stderr、所有process PID/return code、process終了操作の観測、close error classを `calls.jsonl` とapp-serverログに記録します。AI応答の実質部分は伏字後に保存します。次担当JSON/schema整形retry/既読証明はrunner contractに含めていません。HTML reviewerが `指摘: あり` とした場合、同じ記録へHTML作者とreviewerの追加実行を記録します。
- HTML作者は `reports/incidents/{target_date}/{job_id}.html` に直接保存するようpromptを変更し、HTML reviewerには同じ実物のpathを渡します。
- 初回受付はlaunch/run/date/既存queue/checkの同一性を使い、checkにすでに紐付くincidentを再利用します。incidentがまだない場合、既存abnormal checkへincidentを追加できます。maintenance job IDは新規UUIDで採番し、queue worker_job_idを流用しません。
- migration 17で `maintenance_job.release_id` をnullableにし、release行を借用せず正式jobを作れるようにしました。過去maintenance_jobの列値は移行時にcopyします。旧release連動の歴史照会からnullable jobを外しました。非NULL release IDの外部キーは引き続き有効です。
- migration 18で既存 `maintenance_ai_session.role_config_sha256` 値を保全したままnullableにしました。旧AI adapter/store/controller/AppServer経路はrole設定hashを生成・比較しません。
- 明示launch指定時は古いPID状態観測を選択阻害条件にしません。10/19除外日は `frozenset({"2026-10-19"})` のままです。
- `daily_pipeline.py`、`orchestrator.py`、`trace.py` のsource診断hash生成・schema allowlist検査を削除しました。障害phase、例外、安全な発生位置、SQLite分類は維持します。
- `code_revision.py` の保守source全件走査/hash/mtime/schema/tree検査は固定的な説明markerへ置き換え、`workflow_provider` からのsource revision検査による開始分岐をなくしました。
- `production_workflow.py` が参照する10/17固定計画pathを除き、fallback報告の合成例を日付非固定化し、fallback HTMLのhash由来IDをUUIDに変えました。10/17障害を指す固定文も対象日一般の文へ変更しました。
- maintenance CLIから `probe-ai`、`enqueue-historical`、`release-gate`、`start-job`、`finish-job` の旧経路を除きました。worker、resume-worker、skill-chain、status、watchdog、preflightは維持しました。probe内部のprobe evidence/output/diff hashesも除きました。
- 固定rootのdirty拒否、content SHAによるdirty/active状態判定をdeployment/runtime_switcherの今回必要なhunkで外しました。既存ユーザーのdirty試験変更は編集していません。
- reviewer/author/delivery/testerのrole設定、6つの保守skill、保守運用設定仕様、要件追跡表、障害HTML作成ルールへ7担当とhash不要/直接HTML保存の指示を同期しました。

## 変更ファイル一覧

今回STEP01で作成・変更したもの:

- `automation/inago/maintenance/skill_chain_runtime.py` (新規)
- `automation/inago/maintenance/skill_chain.py`
- `automation/inago/maintenance/sql/017_optional_release_anchor.sql` (新規)
- `automation/inago/maintenance/sql/018_optional_ai_role_config_hash.sql` (新規)
- `automation/inago/maintenance/store.py`
- `automation/inago/maintenance/controller.py`
- `automation/inago/maintenance/cli.py`
- `automation/inago/maintenance/worker.py`
- `automation/inago/maintenance/ai_adapter.py`
- `automation/inago/maintenance/code_revision.py`
- `automation/inago/maintenance/failure_journal.py`
- `automation/inago/maintenance/probe.py`
- `automation/inago/maintenance/production_workflow.py`
- `automation/inago/maintenance/report_fallback.py`
- `automation/inago/app_server.py`
- `automation/inago/daily_pipeline.py`
- `automation/inago/orchestrator.py`
- `automation/inago/trace.py`
- `automation/inago/maintenance/deployment.py` (既存dirty hunkへ必要差分を調整)
- `automation/inago/maintenance/runtime_switcher.py` (既存dirty hunkへ必要差分を調整)
- `.codex/agents/common-reviewer.toml`
- `.codex/agents/common-tester.toml`
- `.codex/agents/rr-inago-incident-investigator.toml`
- `.codex/agents/rr-inago-incident-report-reviewer.toml`
- `.codex/agents/rr-inago-incident-reporter.toml`
- `.codex/agents/rr-inago-maintenance-delivery.toml`
- `.agents/skills/common-execution-planning/SKILL.md`
- `.agents/skills/common-implementation/SKILL.md`
- `.agents/skills/common-review/SKILL.md`
- `.agents/skills/common-testing/SKILL.md`
- `.agents/skills/rr-inago-incident-investigation/SKILL.md`
- `.agents/skills/rr-inago-incident-report/SKILL.md`
- `.agents/skills/rr-inago-maintenance-delivery/SKILL.md`
- `docs/specifications/inago/保守運用設定仕様.md`
- `docs/specifications/inago/要件追跡表.md`
- `docs/standards/イナゴ日次不具合レポート作成ルール.md`
- `worklogs/projects/maintenance/skill-chain-simplification-20261021/plan/migration/implementation.md`

## 既存差分の由来と保全

開始時のbranchは `main`、trackingは `origin/main`。ユーザー指示どおりstatus/diffを確認し、別worktreeを作らずstage/commit/pushを行っていません。

- `deployment.py` と `runtime_switcher.py` に当初からあった変更は、fixed root dirty拒否とcontent-SHA保存/照合の追加でした。該当hunkを読み、dirty root許容の既存目的を保ちながらSHA gateを外しました。deploymentのruntime health probeも活性確認gateにしないよう `healthy = switched` としています。その他の外部Scheduler操作は実行していません。
- `tests_inago/test_maintenance_deployment.py` の変更（`working_tree_content_sha256` import、active content SHA期待、追加SHA gate試験を含む）は開始前からあり、読取のみです。編集も試験実行もしていません。現在production側からhelperを外したため、このdirty testは参照先がなくなる可能性をSTEP02へ伝えます。STEP02 ownerが局所的に解決してください。
- 既存dirtyの旧計画・10/17 HTML・SC10記録・`.pytest-tmp`、他のsource/docsは変更・削除・整理していません。`git status`のwarningで `.pytest-tmp` の一部を読取不能でしたが、触れていません。

## 要件/根拠マッピング

- STEP01 prompt §7 (7担当、指定担当再開、safe contentログ、10/17固定解除、正式受付): `skill_chain_runtime.py`, `cli.py`, `worker.py`, `app_server.py`, `store.py`。
- release anchor制約、既存check/incident関連付け、real job ID: `sql/017_optional_release_anchor.sql`, `store.py`。
- dirty fixed-rootとruntime-state制限: `deployment.py`, `runtime_switcher.py`。
- STEP01 docs/role/skill同期: listed `.codex/agents`, `.agents/skills`, docs files。
- R01–R10, C01–C10/C17/C18, CI-03/04/05/06/07/10/02/08/09/12/13/14/11/01, OCT20-F05/F07/F12/F13/F14/F15/F10/F11/F16/F17: 対応表のSTEP01 ownershipに対応。正式DB受付と日次診断実作業は後続worker stageで行い、今回のcontroller単独実行はしていません。

## 確認結果

- 読取のみ: 計画1.1 STEP01本文/prompt、対応表、current facts、code inventory、構成案2.0、common-implementation skillと参照契約、既存dirty deployment/runtime/test差分、migration 12/worker/CLI/app-server経路。
- 機械的syntax check: `.venv-inago/Scripts/python.exe -B -m py_compile` を今回編集したmaintenance/runner/CLI/adapter/controller/store/daily diagnostic Python群へ実行、exit 0。SQL migrationは実行していません。
- TOML parse: `.codex/agents/{common-reviewer,common-tester,rr-inago-incident-investigator,rr-inago-incident-report-reviewer,rr-inago-incident-reporter,rr-inago-maintenance-delivery}.toml` を `tomllib.loads` で読取、`toml_parse=ok`。
- `git diff --check` はSTEP01以外の既存差分に既存末尾空白/空行を表示。対象外ファイルは修正していません。
- 製品testは実行していません (STEP02 owner)。maintenance DB migrationは実行していません (STEP05の指定launch workerで既存DBを開く時に適用予定)。

## hash/call-path残件監査（未完了として引継ぎ）

このSTEPで新runner経路にcontent hash照合を追加せず、release anchorの借用、source revision全件fingerprint、role-config hash、Daily source診断hash、probe hash、旧CLI gate entrypointを除去しました。しかしmaintenance旧経路全体のhash削除は完了していません。現行状況は次のとおりです。

- `store.py`: manifest/input/attempt output/workflow lineage hash、repair campaign nonce/token/code/receipt/snapshot hashが残存。レガシー管理APIの生成・照合を除去できていない。
- `controller.py`, `production_workflow.py`, `repair.py`, `ai_adapter.py`, `evidence.py`, `historical.py`, `investigation.py`, `workflow_reconciliation.py`, `workflow_provider.py`: 旧plan/test/review/deployment/report/historical経路のcode, receipt, manifest, output等のhash生成/照合が残る。新worker CLIは `build_skill_chain_workflow_provider` と `skill_chain_runtime`へ接続するが、旧API自体は存在し、除去未完了。
- `reporting.py`: HTML/JSON出力SHA、reviewed bytes照合、Git blob/push/publish照合が残存。保守公開の旧gateで、通常Daily artifact hashではない。除去未完了。
- `production_workflow.py` の旧report/repair/review処理には `role_config_sha256` フィールド参照が残る。現在AI adapterが生成する値はなく、旧経路との互換修正は未完了。
- `automation/inago/schemas/maintenance-*.schema.json` と `maintenance/sql/*.sql`: repair/test/review/handoff/report等のhash必須schema/SQL trigger/checkが残る。過去rowを保ち新規のhash必須制約を外すmigrationは一部（release anchor、role-config）だけ実装し、全テーブル分は未実施。
- `deployment.py`: deployment artifacts/commit tracking/normal Git commit machinery remains; content SHA gate removed. Git commit/push commands remain available as separate maintenance commands; this task runner does not invoke them. Existing user changes to deploy tracking preserved.
- `failure_journal.py`: invocation fingerprint fields describe operation identity/correlation, not file-content digest (verify before removing); `settings.py` bytes limit is ordinary bounded input, not hash gate.
- `daily_launcher.py` and diagnostic modules include launch/result ids and size-limited encoded data; read the exact consumers before deciding ordinary business data vs maintenance gate.
- `worker.py`: synthetic 10/17 historical dispatch now uses UUID, not hash. Current required `WORKER_DISPATCH_EXCLUDED_DATES` remains exactly 2026-10-19.
- `app_server.py` no longer accepts/stores role-config hash; AI adapter no longer passes it. The associated old report/repair workflow schemas and field expectations still require coordinated removal.
- `production_workflow.py` no longer contains the fixed `_PLANNING_PLAN_DOCUMENT`; it passes `None` until an active job supplies a plan document. `report_fallback.py` no longer carries a 10/17 example date or claim. Other old report/repair hash gates remain.

## 未確認・次担当

- No test execution, no independent reviewer outcome, no DB migration, no formal incident/job intake, no daily trace implementation, no Scheduler apply, no final HTML creation, and no worker launch/return-code observation occurred in STEP01.
- Exact check status/reason/timestamp and maintenance DB schema version were not read by this implementation agent; the parent reported read-only no incident/job/manifest and confirmed no launch-keyed release. Migration-on-worker-start is designed but still unobserved.
- Initial tester handoff: focus on migration17 table rebuild/foreign-key checks, existing abnormal-check incident association, nullable release job row retrieval, worker-command migration path, HTML reviewer finding marker/follow-up (7+2 roles), process close/wait logging, and dirty `test_maintenance_deployment.py` importing a removed helper. Run only plan-assigned focused nodes, do not edit production DB, and record failures as data.
- STEP01 status is implementation assembled with known remaining legacy hashes/date path inventory above; it is not a claim that all repository-wide maintenance hash removals or production behavior are complete.

## STEP02からの局所差戻し（C03/C17）

STEP02の実測から製品修正依頼があり、product file `automation/inago/maintenance/skill_chain_runtime.py` のみを追加変更しました。試験ファイルと一次failure evidenceは変更していません。

- C03 `test_owned_worker_exit_is_reaped_after_close_error`: `test-evidence/C03-C03-01.xml` はcloseが `OSError` で失敗した場合に所有processの `wait_calls == 0`、終了コード未取得となった実測です。close例外を記録した後、所有processにwaitを実行し、呼出し結果/returncodeまたは失敗classをledgerへ記録する処理を追加しました。close error自体は抑制せず、`close_error_class`として保持します。
- C17 `test_safe_stdout_stderr_and_response_exclude_secrets`: `test-evidence/C17-C17-02.xml` はstderrの `Bearer abc.def.ghi` と `token=canary-secret` が `calls.jsonl` に残った実測です。stderr各行を保存前に既存 `_safe_text` へ通し、URL/メール/credential patternを伏字にします。responseは従来どおり同関数で処理します。
- この追加修正の後はtesterへ該当2 nodeのみの再実行を依頼します。再試験は本agentでは実行していません。試験ファイルは変更していません。

## STEP02からの局所差戻し（C08）

STEP02のC08-06実測を受け、製品ファイル `automation/inago/maintenance/store.py` のみを追加変更しました。テスト・fixture・failure evidence・DBは変更していません。

- 対象node: `tests_inago.test_maintenance_workflow_provider::test_worker_cli_connects_provider_without_release_gate`
- evidence: `worklogs/projects/maintenance/skill-chain-simplification-20261021/plan/test-evidence/C08-06.xml`。合成の2026-10-20失敗launch（source_run_id=null）と既存incomplete check/queueで、worker CLIは終了コード0だった一方、incident件数は期待1に対し0、maintenance jobも作られず、maintenance resolutionはunknownとなった記録です。
- 原因: `ensure_skill_chain_job_for_launch()` は正しいlaunch/check identityを保って既存checkへincidentを関連付けますが、`record_daily_check()` は既存checkが `incomplete` の場合、`incomplete` 状態のままincidentを新規関連付けることを拒否していました。従来許可されるのは `incomplete` から `abnormal` へ更新する場合か、すでに `abnormal` の場合だけでした。
- 修正: 既存checkのidentity・状態・reason・観測時刻が呼出しと一致し、かつ対応するdaily launchのstatusが `failed` の場合に限って、check状態を変更せずincident_runを追加できるようにしました。これにより正確なlaunch/checkを使った正式受付からmaintenance job作成へ進めます。release anchorは不要で、queueのworker_job_idもjob IDへ流用しません。
- 機械確認: 対象Pythonを `ast.parse` で構文解析し、成功しました。テストはSTEP02担当のため未実行です。
- 再確認依頼: testerは上記C08 nodeだけを再実行し、incidentとmaintenance jobの作成、およびrelease gateなしのworker CLI結果を記録してください。失敗時は実測を保全して製品差戻ししてください。

変更ファイル: `automation/inago/maintenance/store.py`、本記録 `worklogs/projects/maintenance/skill-chain-simplification-20261021/plan/migration/implementation.md`。

## STEP02 C16差戻し評価（C53 duplicate job）

`test-evidence/C16-diagnostic-01.xml` の `tests_inago.test_maintenance_store::test_c53_contract` は、同じincident/releaseに対して最初に `blocked-job` を登録した後、release gateがreadyになったとして別IDの `ready-job` を `start_fix_job()` へ渡し、2件目のINSERTで `UNIQUE maintenance_job.incident_id, maintenance_job.release_id` に違反した実測です。

この失敗は今回の新フローの製品欠陥とは判定しません。C53は旧release gateの状態変化ごとに同一incident/releaseから別jobを起動する旧契約です。計画§正式初回受付は、同launch/run/dateに関連するjobが存在すれば同じ実jobを再利用し、新しいjobを作らないと定めています。新経路 `ensure_skill_chain_job_for_launch()` はlaunch/run/dateで関連jobを検索して再利用し、初回だけreleaseなしのjob IDを採番します。既存の非null releaseを持つレガシーjobのincident/release一意制約は、同じ仕事を異なるjob IDで二重登録しない整合性制約として維持します。これを削るとC53の `ready-job` を成功させる一方、同じincident/releaseへ複数のqueued jobを登録でき、計画のjob再利用と衝突します。よって製品・migrationは変更しません。STEP02はこの旧テスト期待を今回の再設計に対するlegacy assertionとして記録してください。テスト自体は本実装担当では編集していません。

## レガシーhash/schema撤去の現状（STEP01差分監査）

全面撤去は未完了です。完了している部分は `maintenance_job.release_id` をnullable化するmigration 017、maintenance AI role-config hashを新規session契約から外すmigration 018、および新runner起動経路でのrelease gate/source revision fingerprint/role hash/Daily diagnostic hash/probe hash/旧CLI gate entrypointの削除です。Daily業務成果の通常hashや履歴値は保持しています。

未完了として残る範囲は `store.py` のmanifest/input/attempt output/workflow lineage・repair campaignのhash生成/照合、旧controller/production_workflow/repair/AI adapter/evidence/historical/investigation/reconciliation/provider APIのcode/receipt/manifest/output hash gates、`reporting.py` のHTML/JSON SHA・bytes・Git publish照合、旧production_workflowのrole_config_sha256参照、maintenance schema/SQLのrepair/test/review/handoff/report必須hash条件です。これらの既存rowと新規制約をどう移行するかは未変更・未検証です。deployment.pyの一般Git commit machineryは残存しますが、この依頼でGit操作は呼びません。`implementation.md` のhash/call-path残件監査が具体的なfile別棚卸しです。C16差戻しのためにこの範囲は変更していません。

## STEP03局所差戻し（C16 C27 evidence reuse / FC07 failure result）

STEP02が記録したC16診断を受け、製品ファイル `automation/inago/maintenance/production_workflow.py` と `automation/inago/maintenance/workflow_provider.py` を変更しました。試験コード、fixture、一次failure evidence、DBは編集していません。試験は実行していません。

- C27: `test_daily_evidence_stage_persists_and_reuses_redacted_bundle` の実測は `blocked/daily_evidence_bundle_write_failed`、証拠ディレクトリなし、maintenance operation event 0件、追加traceなしで、元の例外class/codeを特定できませんでした。既存bundleをdirectory有無だけで受理せず、manifest実物を読み、対象日・launch ID・nullable run IDを確認してそのmanifestをworkflow stateへ戻すようにしました。出力作成との競合で相手側がatomic publishを完了した場合も再利用を継続します。内容SHA/bytes照合は加えていません。未知例外は自由文を保存せず、安全な例外class名をreason codeに含めます。C27の同一node再試験をSTEP02へ依頼済みです。新結果は未確認です。
- FC07: 連続stage失敗時にも全stageを実行済みであるのに、最後のreportだけがcompleteならprovider全体がcompleteを返す状態でした。全stageの試行後にfailed/unknownが残る場合は、その最初の実失敗をreasonとしてoverall outcomeをfailed/unknownで返すようにしました。後続stageの実行経路には条件分岐を追加していません。STEP02へこのnodeの再試験結果確認を依頼済みです。
- C43: unhealthy runtimeではrollbackと `health_check_failed` を保つ修正 (`deployment.py` health判定) が現在の共有ファイルに存在します。これはstage継続の制限ではなく、deploy操作結果を正しく表すためです。STEP02が該当nodeを再実行し、rollback実動結果を確認します。

### C16残件の分類

- C24 report-review session discriminator: `ai_adapter.py` からhash形式のsession_discriminator生成/検証を除去しました。report version SHAによるisolationは提供しません。既存セッション識別はcontroller由来のsession IDで行います。
- C27 cwd: runtime/attempt/worktreeの固定root分離とtraversal防止は維持します。`plan_session()` の `cwd` は許可rootとの完全一致を要求し、repo root配下の任意tmp directoryを許可rootとみなしません。
- C27 Daily evidence reuse: artifact fingerprintやSHA比較はmaintenance gateとして除去し、保存manifestの識別値で再利用します。STEP02の再実行待ちです。
- C53: duplicate legacy same-incident/release job unique制約はjob二重登録を防ぐ既存記録整合性であり、新経路は同じlaunchのjobを再利用するため保持。旧release遷移で同じincident/releaseから別IDのjobを作るテスト期待は新契約外と評価済み。

### CR-01..04の進捗と正確な未完了範囲

CR-02のrunner `outputSchema` 要求は `skill_chain_runtime.py` から削除し、app-serverはschemaを渡された場合に限り要求へ含めます。CR-03ではrole全件、実turn完了、process終了code、close状態、非空responseをterminal ledgerで確認し、不足/空/不正logをcompleteとしません。legacy workflow providerも全stageの失敗をoverall completeにしません。後続stageは失敗にかかわらず実行します。CR-04はactive skill/agentのSC07 precedenceを追加し、固定root・本人操作・hash/receipt/commit条件を退ける現行規則を明示しました。

CR-01の全面撤去は**未完了**です。旧maintenance API/SQL/schemaへの以下の保守hash・receipt生成/照合/required field/triggerは現在も残り、未移行です。既存履歴は変更していません。

- `automation/inago/maintenance/store.py`: workflow/manifest/input/attempt hashの生成・read-back照合、repair/test/review receipt API。
- `automation/inago/maintenance/controller.py`, `repair.py`, `historical.py`, `investigation.py`, `workflow_reconciliation.py`, `reporting.py`, `production_workflow.py`, `ai_adapter.py`, `workflow_provider.py`: legacy manifest/code/test/review/report/publication hash validation and receipt-bound paths. `reporting.py` also retains old output bytes/SHA/git publish verification.
- `automation/inago/schemas/maintenance-*.schema.json`: repair/test/review/handoff/report schema fields and required arrays still contain maintenance hashes/receipts.
- `automation/inago/maintenance/sql/001_initial.sql`, `002_job_manifest_ai_sessions.sql`, `005_repair_campaigns.sql`, `009_worker_queue_events.sql`, `018_optional_ai_role_config_hash.sql` and existing constraints/triggers: many hash/receipt NOT NULL, CHECK, UNIQUE, FK and trigger conditions remain. Migration 017 release nullable and migration 018 role-config optional are only partial changes.

通常Daily業務artifact hash、Daily result observation hash、Daily report document hashes are not bulk-modified as maintenance gates; each call site still needs a final semantic boundary audit. Active skills and agent files still contain historical hash terms in explanatory policies/contracts. SC07 top-priority rules now say not to use them as gates, but the full CR-04 wording cleanup has not been reviewed end-to-end.

### This handoff checks and limitations

- No tests, DB open/migration, live launch/CLI, Scheduler operation, deployment, stage/commit/push were performed by this remediation turn.
- Static check: `.venv-inago/Scripts/python.exe -B -c "import ast,pathlib; ..."` parsed `production_workflow.py` and `workflow_provider.py` successfully (`ast_parse=ok`). `git diff --check` passed for these product files and the implementation record; initial check found one extra final blank line in the active contract, which was removed. No test result is claimed.
- STEP02 owns focused reruns of C27, FC07, and C43. Other C16 regression observations remain as recorded in `test.md`; they are not made complete by this patch.
- Remaining implementation risk: broad CR-01 legacy old-path and schema/SQL cleanup is unresolved. Exact paths above are a handoff of implementation facts, not an approved exclusion or completion claim.

## 追加差戻し対応（C03 runtime ledger、C27 admission tail、schema v19）

STEP02のC27担当から、evidence bundleの初回保存・再利用とlaunch-bound正式受付が同一focused nodeでPASSしたとの連絡を受けた（`C16-diagnostic-15`, 1 passed, exit 0）。ただし私は再試験しておらず、記録した実結果を引用している。

`automation/inago/maintenance/skill_chain_runtime.py` ではledger allowlistに `job_id`, `source_launch_id`, `source_run_id`, `target_date`, `started_at_utc`, `finished_at_utc` を追加した。実worker eventにidentityが保存されるため、terminal inspectorが要求するjob/launch/dateをdiskから確認できる。close失敗時の所有process回収を、wait(5s) → terminate → wait(5s) → kill → wait(5s) の順に拡張し、実行・失敗した操作名、例外class、取得できたreturncodeを別々のledger eventへ記録する。close failure自体はworker_finishedの `close_error_class` として保持する。通常worker turnでもstart/end時刻と実際のprocess returncodeを既存worker_finished rowに保存する。STEP02はledger roundtrip identity、timeout分岐、追加のrunner回帰だけを再試験する必要がある。

CR-01 migration v19を `store.py` へ登録し、旧履歴のhash/receipt/commit値を維持しつつ、対象テーブルの該当列のNOT NULL・CHECK、hash/receipt/commitを含むtable constraint/index、旧triggerを除去するschema-preserving table rebuild処理を追加した。Daily business table `daily_result_observation` は再構築対象から除外した。SQL migrationは既知のlegacy triggerをdropし、SQLite DDLから対象制約を除去する処理はstore migration runner内で行う。FK check対象へv19を加えた。**まだmigrationを実行しておらず**、実DBへの適用結果は未知である。テストも実行していない。適用前にSTEP02の隔離DB focused migration nodeでDDL・index・FK保持とrow保存を確認する必要がある。以前の草案CTASはtable定義/外部キーを失うため撤去した。

### 今回の静的確認

- 対象Python 6ファイル `ast.parse`: 成功。変更外の共有treeに存在する追加blank line/trailing whitespaceもあるため、全tree `git diff --check` は通過扱いにしていない。テスト/DB/migration/launchは未実行。
- `C16-diagnostic-15` evidence/admission test: STEP02からの結果共有、1 passed, exit 0。自身による試験実行ではない。
- 引き続き未完了: `store.py` legacy APIs、reporting/reconciliation/repair/historical old gate、maintenance JSON schemasとSQL/history routes、active role instructionsのCR-01/04残部。具体的な未処理を実装で削減してからSTEP01を閉じる。

### C03再修正の詳細（runtime close error回収）

close失敗後のprocess回収は、waitだけでなく時間切れを受けてterminate→wait、まだ生存ならkill→waitまで続けます。各wait/terminate/killは別ledger eventにoperation名、結果または例外classと見えたreturncodeを残します。waiterがreturncode属性を更新しないwrapperでもwait戻り値を記録します。process終了codeが取得できない場合はnullのままで、close failureを成功に読み替えません。C03 focused再試験待ちです。

### STEP02 runtime ledger correction actual reruns

STEP02からの実測（`plan/migration/test.md`）: C16-diagnostic-16のC03 close error回収で、wait timeout→terminate→wait timeout→kill→waitにより終了code 137が取得でき、close失敗後もprocessを回収した。C17 response/stdout/stderr secret redactionもPASS。C16-diagnostic-18のC02は、テストmock signatureを現行APIへ合わせた後に7worker eventのjob/launch/run/date/timing roundtripとterminal complete判定がPASS。C16-diagnostic-17ではempty ledger、missing role、empty responseをcompleteにしない3確認がPASS。各結果のtest authorはSTEP02であり、本実装agentによる実行ではない。
- CR-04 active-instruction sync: replaced stale 10/17 job/date and controller-owned commit/push wording in common-execution-planning SKILL/contracts, and the incident-investigation SKILL’s 10/17 target statement. Incident-report contract now specifies direct final HTML creation and does not require a publication worker or result/hash/bytes/version validation. Historical plan/report references were not rewritten.
 
### 保守evidence snapshotのdigest生成撤去
 
`automation/inago/maintenance/evidence.py` のmaintenance evidence snapshotからファイルSHA-256生成と`SQLiteSnapshot.sha256`を削除しました。redacted artifact manifestに新しい`redacted_sha256`を出力せず、期限切れ更新時に旧履歴の同項目をnullへ書き換える処理も削除しました。`maintenance-evidence.schema.json`は過去履歴に既にある`redacted_sha256`を読み取れるよう optional unconstrained propertyとして保持し、digest形式を照合しません。Daily result business observationは別用途のため未変更。
 
### deployment pending-action digest/receipt gate撤去
 
`deployment.py` からpending human action setのSHA revision snapshot生成を削除しました。`production_workflow.py` preflightはbefore/after pending snapshotのread・equality gateを持たず、deployment stage stateにもpending count/revisionを記録しません。既存のpending_probe constructor keywordだけは外部互換のため未使用optional argumentとして残します。Dailyプロセスの実稼働観測とローカルdeployment exclusion lock、および実runtime health/rollbackは operation-level behavior として維持しています。Python AST parseは該当2 fileで成功。関連deployment testはSTEP02 ownerのため未実行で、focused確認を依頼します。
 
### Workerのstdout/RPC出力記録

`skill_chain_runtime.py` はraw app-server stdout/RPC frameを記録しません。C03/C17対応のworker event要約を拡張し、許可済みのevent method、item type/phase/status、command exit code、agent messageまたはcommand `text`/`aggregatedOutput`/`output` の最初の文字列を、URL/email/credential redactionと4000文字上限を通して `stdout_summary` に保存します。最大200 eventです。final answerは全文を既存safe-text処理後にresponseへ保存し、stderrは各行をsafe-text処理後に保存します。raw prompt/RPC frame、環境やtool request全体は保存しません。取りこぼしやredactionを実turnで最終確認する作業はworker実動後に記録します。

### v19 focused migration failure修正とlegacy manifest API hash撤去

STEP02のC16-diagnostic-19/20で `test_maintenance_deployment.py` のC39/C43は製品動作へ入る前に隔離DB初期化に失敗しました。19ではSQL分割器が末尾commentを未完了statementとして返すこと、20ではtable rebuild時に他tableのtriggerがまだ旧tableを参照し `ALTER TABLE ... RENAME` が失敗することを確認しました。migration 019末尾に `SELECT 1;` を加え、v19 runnerはtrigger SQLを保全して一旦全triggerをdropし、対象tableの全rebuild後に旧hash/receipt/commit/repair gate以外を再作成する順へ変更しました。`daily_result_observation` に属するtrigger定義は保全対象です。C38/C39/C43の再実行をSTEP02へ依頼済み。DB/migrationは本agentでは実行していません。

`store.py` の `JobInputManifest` と `AIParentSessionRecord` ではmanifest SHAの新規生成・read-back計算照合を削除し、新規値はNULL、既存値は歴史値として読み取るだけにしました。`bind_ai_parent_session` のSHA format/match要件も削除し、session再利用のidentity比較からmanifest digestを除去しました。`controller.py` のhistorical manifest SHA比較と引数必須を外し、`ai_adapter.py` はSHAを渡しません。`historical.py` は新intake recordにmaintenance manifest SHAを書かず、read時もその比較をしません。daily business observation/evidenceの既存hash項目は今回の局所差分では変更していません。

`cli.py` では旧maintenance診断traceの必須write/readback receiptを開始・終了・retention前後のゲートから外し、CLI操作の実結果を返します。通常retention失敗も別commandの開始を止めません。standalone `skill-chain` subcommand（formal launch admissionを迂回する実行路）と、同じ旧trace IDを渡す `--maintenance-id` を削除し、通常7担当runnerの起動入口を既存launchを指定するWorker CLIへ限定しました。preflightは引き続きread-onlyです。

追加静的確認コマンド: `.venv-inago/Scripts/python.exe -B -c "import ast,pathlib; fs=['automation/inago/maintenance/store.py','automation/inago/maintenance/controller.py','automation/inago/maintenance/ai_adapter.py','automation/inago/maintenance/historical.py','automation/inago/maintenance/production_workflow.py','automation/inago/maintenance/cli.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in fs]; print('ast_parse=ok')"` → `ast_parse=ok`, exit 0。`git diff --check -- automation/inago/maintenance/store.py automation/inago/maintenance/controller.py automation/inago/maintenance/ai_adapter.py automation/inago/maintenance/historical.py automation/inago/maintenance/production_workflow.py automation/inago/maintenance/cli.py automation/inago/maintenance/sql/019_remove_legacy_maintenance_attestation_gates.sql worklogs/projects/maintenance/skill-chain-simplification-20261021/plan/migration/implementation.md` → 出力なし、exit 0。`rg -n "MaintenanceDiagnosticTrace|MaintenanceAuditReceiptError|run_maintenance_retention|diagnostic_trace|skill-chain" automation/inago/maintenance/cli.py` → 0 matches。いずれも静的確認であり、pytest、DB migration、launch、CLI実動確認ではありません。対応はCR-01旧CLI診断receipt/manifest hash API、CR-04旧standalone CLI routeです。

STEP02のC16-diagnostic-21は前述C38/C39/C43 migration/deployment nodeを3 passed, exit 0で再実測しました。隔離pytest DBです。C16-diagnostic-23のC08 worker CLIも、runner ledger欠落をmaintenance incomplete/unknownとして正直に返す期待で1 passed, exit 0となりました。実本番runやmigrationではありません。

### CR-04 active skill/contract矛盾の追加修正

独立review CR-04に基づき、`.agents/skills/common-implementation/SKILL.md`, `common-review/SKILL.md`, `common-testing/SKILL.md`, `rr-inago-incident-investigation/SKILL.md`, `rr-inago-incident-report/SKILL.md` と同5領域の`references/contracts.md`で、現行セクションに残っていたcontroller本文fallback、公開専用worker、report artifact hash/bytes/version必須文言を作者の最終HTML直作成・独立reviewへ合わせて修正しました。investigationのsnapshot確認からhash/base commit照合指示を除去し、testingはsource/input version/pathで版を記録しhashを生成しないと明記しました。incident report trace operation contextは新しいhash生成を行わない指定へ更新。U23/legacy paragraph等の過去履歴の記述は今回改変していません。active instruction文字列の静的検索は継続し、role配置全体の完全監査は未完了です。

### CR-01 legacy release digestとmaintenance JSON schema制約

`store.py` の `create_release`, `register_repair_candidate_release`, `_evaluate_release_gate` からrelease case manifest SHAの生成・書込・同値照合を削除しました。existing `case_manifest_sha256` history is not rewritten; new release records use NULL under migration v19. `production_workflow.py` のlegacy repair setup artifactから `case_manifest_sha256`生成・出力も削除しました。

maintenance schema files `maintenance-deployment-candidate.schema.json`, `maintenance-repair-handoff.schema.json`, `maintenance-repair-patch-assessment.schema.json`, `maintenance-repair-result.schema.json`, `maintenance-repair-review-claim.schema.json`, `maintenance-repair-review-target.schema.json`, `maintenance-repair-review.schema.json`, `maintenance-repair-test-evidence.schema.json`, `maintenance-repair-test-ledger.schema.json`, `maintenance-report-result.schema.json`, `maintenance-report-review.schema.json` では、hash/receipt/fingerprint/digest/discriminator propertyをunconstrained optional propertyへ変更し、必須配列から該当keyを除去しました。既存文書の値は保持されます。全24件の `maintenance-*.schema.json` はJSON parseし、`maintenance-evidence.schema.json`以外の23件にrequired hash/receipt keyや制約されたhash propertyがないことを確認。`maintenance-evidence.schema.json` はDaily business artifact/historical `redacted_sha256`を過去値として読み得るため変更していません。

検証: 同じPowerShell callで23 maintenance schema全件をJSON parseし、evidence以外のrequired/property constraintsをrecursive scan → `json_parse=ok; checked=23; evidence_schema_preserved=1`。上記6製品PythonのAST parse → `ast_parse=ok`。対象product/schema/SQL/implementation記録の `git diff --check` → 出力なし。これはunit/integration testやSQLite migration実行ではありません。STEP02へmigration v19隔離DB row-preservation/new-NULL/trigger-removal focused nodeを依頼済みで、結果未取得です。


### CR-01旧workflow reconciliationのレガシー読戻しゲート削除（STEP02差戻し後）

`automation/inago/maintenance/workflow_reconciliation.py` から旧deployment candidate/result・repair handoff・attempt output・publication sidecar・Git remote HTML readbackを再検査する呼出しと実装を削除しました。これらの関数はSHA生成/照合、receipt/release binding、全成果物readbackを通して終端statusを止めていました。終端記録は当invocationのstage observationとidentity観測を記録し、publication evidenceは `legacy_publication_readback_not_required` として未要求の事実を残します。workflow terminalへの保存とDB finalizeの操作結果は別のままです。Daily business artifact hashはこのfileでは扱っていません。

STEP02が隔離DBで実行したv18→19保存確認について、親から `C16-migration-02` / diagnostic-25 PASS（過去manifest/session hash値を保持、retired trigger除去、新規NULL値を許容）と報告を受けました。私自身はそのtestやDB migrationを実行していません。本agentはSQL/DBには追加変更をしていません。

確認: `.venv-inago/Scripts/python.exe -B -c "import ast,pathlib; p=pathlib.Path('automation/inago/maintenance/workflow_reconciliation.py'); ast.parse(p.read_text(encoding='utf-8'), filename=str(p)); print('ast_parse=ok')"` → `ast_parse=ok`, exit 0。`rg -n "sha256|hashlib|expected_sha256|receipt|_published_report_readback|_attempt_and_deployment_outputs|_durable_stage_handoffs|_deployment_result_matches_attempt" automation/inago/maintenance/workflow_reconciliation.py` → 0 matches。pytest・DB migration・CLI実動は未実行。

CR-01は依然未完了です。次の実残件は `reporting.py` のHTML/result/review digestとGit push publication API、`repair.py`/`store.py` のrepair campaign・receipt・code hash APIとその旧controller/production_workflow呼出し、`historical.py` のsource/evidence digestとdigest由来ID、関連 maintenance SQL/schema/triggers の旧repair/test/review制約です。これらを未実施理由として承認済み扱いにせず、実差分を続けます。

終端statusの正確性も維持するため、観測済み全stageが `complete` 以外（`failed` / `blocked` / `unknown`等）なら `unsuccessful_stages` に残し、terminalをcompleteにしない処理を追加しました。これは後続起動を止めずに、実際の失敗・不明を完了と混同しないための終端記録です。観測の欠落や失敗結果は成果物hash/readbackの条件に戻していません。STEP02にworkflow reconciliation関連nodeがある場合は次回の対象回帰に含めるよう親へ報告します。

### store.py旧handoff/output hash・review invocation証明の撤去

独立review CR-01/旧契約監査に基づき、`store.py` の `_read_handoff_artifact()` は保守handoff内容とSHAを比較しなくなりました。`accept_handoff()` は過去payloadに残るoptional `sha256` 値を無視し、output/facts/report pathだけを扱い、新handoff rowのSHAはNULLにします。`read_attempt_artifact()` は過去呼び出しの互換引数を受けても使わず、ファイルは安全なattempt root配下からpathで読みます。`read_u12_investigation_result()` もDBのoutput digestをreadback条件にしません。`prior_u12_review_execution_ids()` と `prior_step_review_execution_ids()` は過去review応答のexecution IDを読み返す同一invocation証明を返さず、空集合を返します。これらはlegacy step-attempt APIだけの変更です。7担当runnerのprompt/identityや通常Daily business artifact hashには触れていません。

確認: `store.py` の全 `_read_handoff_artifact` 呼出しを検索し、旧expected SHA引数が残らないことを確認。store.py AST parse成功、対象fileとimplementation.mdの `git diff --check` は出力なし。テスト・DB migrationは未実行。旧repair/test campaign内部の独立したcode/receipt/hash APIは引き続き残り、ここでは除去未完了です。

### historical intakeのmaintenance digest・digest由来ID撤去

`historical.py` から、過去evidence/source-file/intake/observation SHAの必須形式・内容比較・新規生成を除去しました。既存観測/intake文書に残る `evidence_sha256`, `observation_sha256`, `manifest_sha256` は読み取り時にoptionalな歴史値として許容し、今回の新しいintake recordには書きません。copied source file descriptorの歴史 `bytes`/`sha256` 値も照合しません。上限bytesは一括読込・メモリ使用を抑える入力サイズ上限として残しています（hash代替一致ゲートではありません）。hash-derived source/incident/job IDsは `historical-{original_source_launch_id}` と `job-{source_launch_id}` に変更しました。ID長が既存ID契約を超える場合は理由付きで失敗し、hashへのフォールバックはしません。`controller.py` historical planning completionからevidence/intake SHAを出力せず、Daily completion JSON内容hashもmaintenance dependencyへ生成しなくしました。通常Daily DBのbusiness observation hash pathは変更していません。

確認: `rg -n "hashlib|sha256|_SHA256|digest|source_digest|incident_digest" automation/inago/maintenance/historical.py` ではoptionalな歴史key名とcompatibility propertyだけが残る。`historical.py` / `controller.py` AST parse成功。テスト・DB migration・historical intake実動は未実行。注意: historical launch→incident/jobの既存release-bound route自体はこの差分では廃止しておらず、hash removal後の呼出し整合を次の関連nodeで確認する必要があります。

### CR-04 active contract cleanup (追加)

既存のactive common-implementation contractに残っていた、deploymentをrepair candidate/attemptへ完全一致で結ぶこと、reportをspecific attempt sequenceへ固定することを「実測値の記録はするが開始・完了条件にしない」へ修正しました。incident-investigation contractは`base_commit`を担当の開始入力から外し、identity以外の欠測をunknownで扱うようにしました。incident-report `references/contracts.md` に残っていた「Git stage/commit/pushは本人が行う」という現行worker向け曖昧文を、この保守フローでは実行せず本人へ引き継ぐ文へ修正。`.agents/skills/`と`.codex/agents/`を対象にした検索では10/17固定やsha/receiptの必須ゲート文言は見つかりません。歴史節で値や旧契約を説明する記述は保持しています。

### next-owner handoff JSON routeの削除

`investigation.py` の `build_investigation_handoff_payload()` と `maintenance-handoff-v1` envelope構築を削除し、調査review resultは `next_action=continue` を返すようにしました。investigation `save_attempt_artifact()` の許可ファイルから `stage-handoff.json` と `repair-handoff.json`、report publication sidecarを除去しました。`controller.py` はreview後にstoreへhandoff JSONを送らず、結果のhandoffフィールドはnullです。既存の `resume_investigation_handoff()` は旧API互換のno-op（None）にし、storeの `accept_handoff()` 全実装と `_HANDOFF_KEYS` を削除しました。Storeの旧artifact read helperには安全なroot path読取だけ残りdigest比較はありません。歴史rowやstatus値はmigrationで書き換えていません。

確認: `rg` で `accept_handoff()` と `build_investigation_handoff_payload()` の呼出し/定義がなく、builder schema constantを除いたことを確認。`investigation.py`, `controller.py`, `store.py` AST parse成功。pytest・DB migrationは未実行。旧provider/controller内には `next_action` や attempt artifact hashを伴う旧step routeがまだ残り、これらのrouteを今回の7担当runnerで使う必要性はありません。runner実動前に残る呼出しは続けて監査し、過去API/statusを新stage開始条件として使わないようにします。

### CR-01 reporting direct-write and old publication API (2026-10-21 update)

`automation/inago/maintenance/reporting.py` now generates no HTML/result SHA-256 values. The report result `output` stores only a path. Review validation retains author/reviewer independence but does not compare review version or digest to output; the schema makes historical `reviewed_version`/`reviewed_sha256` optional and unconstrained so old values remain readable. Historical commit observations are plain optional strings and no longer require Git SHA format or equality across implementation/deployment fields. The old `_git*`, ancestry, remote readback, temporary index, commit/push implementation and `ReportPublication` contract were deleted. `save_report_artifacts()` writes the authored HTML and result JSON directly beneath the validated fixed attempt root using `safe_file.open_file_beneath` and only returns their relative paths. Path containment and no-reparse checks remain filesystem safety boundaries, not artifact identity proofs.

Static check: `.venv-inago/Scripts/python.exe -B -c "import ast,pathlib; p=pathlib.Path('automation/inago/maintenance/reporting.py'); ast.parse(p.read_text(encoding='utf-8'), filename=str(p)); print('ast_parse=ok')"` returned `ast_parse=ok`, exit 0. `git diff --check -- automation/inago/maintenance/reporting.py automation/inago/schemas/maintenance-report-review.schema.json` emitted no diagnostics. No tests, DB operations, migration apply, CLI, Git actions, or publishing were run by this implementation agent.

**Still unresolved:** `production_workflow.py` retains the obsolete report-publish callsite and downstream publication commit/sidecar contract. Since `publish_reviewed_report` and its Git implementation were removed here, that old route is currently incompatible and is not a successful publication path; it needs to be retired or replaced by direct final-path author output before STEP01 can be called complete. `repair.py` and the legacy repair parts of `store.py` still contain many code/review/test/receipt SHA contracts and need continued removal while retaining historical values. The old role-config and report/result attempt readers in `production_workflow.py` also remain under the broad CR-01 scope.

### CR-01 legacy production report publisher callsite disabled

`automation/inago/maintenance/production_workflow.py::run_daily_report_publication_stage` now records the legacy Git-publisher stage as `not_performed` with reason `report_author_writes_final_path`, and its old failure recorder was removed. This preserves accurate operation status while allowing the legacy provider to continue later stages; it performs no git fetch/commit/push, remote readback, report digest generation, or publication sidecar write. The new seven-role runner's HTML author owns direct creation at the final path. The old publication helper functions and prior-publication candidate/hash reconciliation elsewhere in `production_workflow.py` still need removal; they are not called by this replacement stage but remain in the module and are unresolved for the broad legacy-route audit.

Static check: `production_workflow.py` AST parse → `ast_parse=ok`, exit 0; `git diff --check -- automation/inago/maintenance/production_workflow.py` emitted no diagnostics. No tests or DB operations were run.

### Legacy publication helper follow-up

Removed the remaining report-only Git helpers `_daily_report_git`, target-head and remote-report readers, publication-parent lookup, temporary worktree publisher, and prior-publication digest authorization from `production_workflow.py`. Its publication stage is now a truthful `not_performed` observation; reviewer and report author stages remain separate. A targeted `rg` over `reporting.py` and the production publication region finds no `publish_reviewed_report`, `_remote_report_head`, `_report_blob_sha256`, `_daily_report_git`, or `report_publication.push_remote` call. Production workflow still contains other legacy report/result attempt SHA validation outside this publication region; those remain unresolved and are not claimed removed.

Static check rerun: `production_workflow.py` AST parse → `ast_parse=ok`; publication helper scoped `rg` → 0 matches; `git diff --check` → no diagnostics. No tests or DB actions were performed.


### STEP01 broad legacy route retirement checkpoint (2026-10-21)

This update supersedes prior unresolved notes about the production workflow and repair API. `production_workflow.py` was reduced to a compatibility entry point delegating to the seven-role skill-chain provider; the former parallel investigation/planning/repair/report/publish workflow and its artifact hash/review/version bindings no longer execute or remain as callable stages. `repair.py` was removed, and `deployment.py` no longer exposes repair candidate commit creation or candidate stage/commit/push helpers. The current delivery skill no longer asks for target hash matching or publication-byte/hash equality. Its operation record is direct and status does not gate later roles.

The old controller/AI adapter/probe modules were removed after static import search showed no current worker/CLI/skill-chain imports; the only former product dependencies were the legacy probe, historical enqueue helper, and old provider/controller chain. `historical.py` retains the read-only `load_historical_work_item()` path used by the worker, but the former controller-backed `enqueue_historical_observation()` route was removed. `ai_adapter.py` and `investigation.py` no longer generate hashes for newly written model/attempt artifacts; the latter returns `None` in its legacy tuple slot and treats a colliding artifact name as an existing-artifact error without comparing bytes. Historical saved hash fields remain readable where required.

Plan/fact mapping: STEP01 sections 7.1/7.2 and CR-01/CR-04; user removals covering old routes, output/input/response hashes, receipts, version/readback gates, candidate commits, and provider path; existing implementation finding CR-01. New runner’s exact launch identity and safe file-root containment remain, but are not digest/version/marker equality checks. Daily `daily_result_observation.observation_sha256` remains intentionally as an ordinary Daily observation integrity value (per the plan’s maintenance-gate boundary); it is not used to decide whether later maintenance roles start. Migration v19 retains stored historical hash values while allowing NULL for new maintenance writes and removes retired attestation triggers; STEP02 reported its focused preservation node PASS.

Owned paths changed in this checkpoint: `automation/inago/maintenance/production_workflow.py`, `automation/inago/maintenance/deployment.py`, deleted `automation/inago/maintenance/repair.py`, deleted `automation/inago/maintenance/controller.py`, deleted `automation/inago/maintenance/ai_adapter.py`, deleted `automation/inago/maintenance/probe.py`, `automation/inago/maintenance/investigation.py`, `automation/inago/maintenance/historical.py`, and `.agents/skills/rr-inago-maintenance-delivery/SKILL.md`. Existing prior STEP01 changes to `store.py`, `reporting.py`, migration v19, schemas and workflow reconciliation remain preserved. No test code, fixtures, production DB, credentials, scheduler registration, evidence, or daily outputs were edited.

Non-test machine checks: AST parsed the changed runtime modules (`production_workflow.py`, `deployment.py`, `ai_adapter.py` before retirement, `investigation.py`, `historical.py`); the two retained changed modules parsed after the final route removal. Static `rg` found no product imports of removed controller/adapter/probe/repair modules or calls to `prepare_repair_candidate_commit`, `stage_owned_paths`, `commit_owned_paths`, `push_candidate_commit`, `RepairController`, or `repository_code_sha256`. Tests and live CLI/DB migration remain intentionally unrun by STEP01; STEP02 owns tests and STEP05 owns the actual target intake/worker execution. No stage/commit/push or deployment was run.

Residual static findings for follow-through (not represented as success): `store.py` still carries historical nullable `manifest_sha256`, `output_sha256`, and `case_manifest_sha256` columns/record members for preserving old rows; new maintenance writes in the changed paths are NULL and there is no SHA comparison in those accessors. The old SQL migration files retain their original historical trigger/receipt DDL; migration 019 removes those constraints when opening a database and preserves prior rows. `store.py` still contains legacy release/test/deployment persistence APIs and their status/version identity checks, plus the ordinary Daily result-observation hash generation/verification noted above. `reporting.py` retains optional hash-named compatibility result members initialized to `None`, with no generation/comparison. `deployment.py::DeploymentCoordinator` retains operation-level Git/deployment observation and runtime health rollback semantics; it is not called by the current seven-role provider, but was retained for truthful operation status/rollback compatibility. Active non-delivery agent prompts still have generic “version-bound review” wording in older investigator/reviewer TOMLs and should be audited against the current seven-role override if the static scan marks them as active. The final all-file diff/status audit is pending this checkpoint.

### STEP01最終追加checkpoint: hash projection / worker lease撤去（2026-10-21）

このcheckpointで追加変更した製品ファイル:
- `automation/inago/maintenance/store.py`: `JobInputManifest`からmaintenance manifest digest DTOを除去し、manifestの新規insert/read APIはidentity列だけを返すよう変更。`bind_ai_parent_session` と `get_ai_parent_session` / `record_ai_session_observation`、`AIParentSessionRecord`を退役し、無視する `manifest_sha256`引数や`sha256=None` DTOを残さない。未使用の `StepAttemptResult` / `GateResult` / `JobStartResult` / `HandoffResult` / `HandoffContractError`を削除。worker queue complete/block と job finalize のAPIからlease必須引数・`_assert_lease`・lease ownership列の新規書込みを除去。historical DB列/値はmigrationで保持する。
- `automation/inago/maintenance/historical.py`: `HistoricalSourceDocument`から常時NoneのSHA fieldを削除。過去intake JSONのlegacy digest keyはraw history入力の後方読取り互換として許容するが、比較・workflow identityへの投影・新規生成には使わない。
- `automation/inago/maintenance/worker.py`: 通常workerのlease取得/heartbeat/release処理を削除し、queue terminal更新とworkflow継続をlease/API可用性に依存させない。`WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})`は維持。
- `automation/inago/maintenance/skill_chain_runtime.py`: runner providerからlease引数を除去し、job終端記録はleaseなしで実施する。

対応: STEP01の通常7-role runner/CLI移行、CR-01のmaintenance hash/receipt契約削除、CR-03のoperation failureとworkflow completionの分離、構成案2.0の同一launch・unknown継続を反映。既存manifest/session/receipt列・記録値の履歴は改変しない。Daily業務観測 `daily_result_observation` のJSON SHA生成・比較だけは、明示された通常Daily観測データ整合性として維持。10/17 固定route制限と10/19 queue exclusionは前述の既存変更どおり扱う。

残存静的検索の分類:
- `store.py` の `hashlib.sha256` は `daily_result_observation` の通常Daily observation JSON生成/read integrity確認の2経路のみ。
- `historical.py` の `manifest_sha256`/`evidence_sha256`/`observation_sha256`語は既存intake JSONをraw compatibilityとして読めるkey allowlistのみで、digest計算・比較・必須条件ではない。
- `store.py` schema v19 migration と `sql/001..018` は旧DBからhistorical column/valueを保全しながらconstraint/triggerを外すための歴史定義。現行新規APIはこれらhash値を作らない。v19のisolated row preservation/new-NULL/retired-trigger確認はSTEP02のdiagnostic-25 PASSとして別担当から報告済み。本agentは本番DBを開かず、migrationを適用していない。
- `watchdog.py` とCLIの旧 `watchdog` commandは別の旧スケジューラ観測経路として未変更で、現行 `worker --once --source-launch-id` 7-role経路からは呼ばれない。そこでの古いlease関数呼出しは残存しており、workerの通常7-role経路には接続していない。直接CLI経路も全maintenance old route撤去要件に含める判断が必要なら後続作業箇所は `automation/inago/maintenance/cli.py` のwatchdog parser/dispatch と `watchdog.py` です。
- 新runnerの `stage_handoff.py` は内容ある初期fallback記録にのみ使い、hash/schema/既読/同invocation/next-owner証明として検証せず、開始/終端gateにしない。

確認（本agent実行）: `.venv-inago/Scripts/python.exe -B -c "import ast,pathlib; root=pathlib.Path('automation/inago/maintenance'); fs=[*root.rglob('*.py')]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in fs]; import automation.inago.maintenance.cli, automation.inago.maintenance.worker, automation.inago.maintenance.skill_chain_runtime; print(f'ast_parse=ok files={len(fs)} imports=ok')"` → `ast_parse=ok files=23 imports=ok`, exit 0。`rg -n "_assert_lease|acquire_lease|heartbeat_lease|release_lease|lease=lease|manifest_sha256|AIParentSessionRecord|bind_ai_parent_session|HandoffContractError|MaintenanceAuditReceiptError|require_completion_receipt|outputSchema|session_discriminator" automation/inago/maintenance --glob '*.py' --glob '*.json'` → worker/store/runner側に該当なし。残りは旧 `watchdog.py` のlegacy lease routeと `historical.py` の過去JSON key allowlistのみ。テスト/DB/Worker CLIは実行していない。STEP02へC08および直接影響nodeだけのfocused再試験を依頼済み。全対象ファイルの差分レビュー、migration最終状態の製品独立review、本番反映、実launch実動は後続stage所有。

### STEP01追加: 旧watchdog/lease入口の退役

追加で判明した通常workerのDB `acquire_lease` / `_assert_lease` 呼出し不整合を解消しました。`worker.py`からlegacy lease取得・heartbeat・release、旧Scheduler起動用 `dispatch_ready_worker` を除去し、`skill_chain_runtime` providerの引数を4項目に合わせました。`store.py`のworker queue complete/blockとjob finalizeはqueue/check/job identityと実操作結果を保存し、lease assertionや新規lease owner/generation書込みをしません。過去のnullable lease/claim列値は保持します。v19 schema-preserving rebuildはretired lease/claim列に関係するNOT NULL/CHECK/trigger/index gateも外し、history rowをコピーします。

旧 `watchdog` CLI parser/dispatch、`watchdog.py`自体、およびstoreのwatchdog queue/cursor write/read APIを退役しました。Daily observation process identity checkは通常Daily成果の観測として残し、10/19は`WORKER_DISPATCH_EXCLUDED_DATES`で引き続き除外します。対象10/20以外の日次処理やWatchdogを本agentは実行していません。

このcheckpoint時点の残存scanは、`store.py`の通常Daily observation SHA生成・read照合のみが実行時digest処理です。`historical.py`のmanifest/evidence/observation digest名は過去intake JSON key allowlistであり、新規生成/比較/flow projectionではありません。SQL 001–018は既存DBの歴史DDLとして不変で、019/v19がそのconstraints/triggersを新しいDB migration時に除去します。

### STEP01最終補足: watchdog旧入口を撤去しworker lease残存を解消

直前checkpointに「watchdogは別経路として残る」と記した後、親からlegacy CLI/worker入口も退役するよう指示を受け、内容を更新しました。CLIのwatchdog parser/dispatch、`automation/inago/maintenance/watchdog.py`、storeのwatchdog queue/cursor操作API、workerの旧Scheduler起動dispatch/APIを削除しました。watchdogの過去DB tables/countsとmigration履歴は改変せず、既存行を保全します。

migration v19のschema-preserving transform対象をhash/receipt/commit/signature/fingerprint/digest/discriminatorに加えてlease/claim/owner/generation列制約とtrigger/indexへ広げました。worker_queue eventの新規owner/generationはNULLを許容し、過去値はそのまま読み取れます。これはreceipt等の代替ゲートを導入せず、workerの通常queue/job終端を所有証明なしで記録するためです。

最終静的確認: `.venv-inago/Scripts/python.exe -B -c "import ast,pathlib; root=pathlib.Path('automation/inago/maintenance'); fs=[*root.rglob('*.py')]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in fs]; import automation.inago.maintenance.cli, automation.inago.maintenance.worker, automation.inago.maintenance.skill_chain_runtime; print(f'ast_parse=ok files={len(fs)} imports=ok')"` → 22 Python files AST parse、CLI/worker/runner imports all OK, exit 0。maintenance Python/JSON scanでlease token/acquire/heartbeat/release/assertion、watchdog command/API/old dispatch、manifest SHA DTO/API、AI session config session API、completion receipt/next-owner/outputSchema/session discriminator call sitesは0件。残る `historical.py` の`*_sha256`は旧intake raw-key allowlistで比較もworkflow projectionもせず、`store.py` のSHA generation/comparisonは許可されたDaily business observation JSON integrityのみ。`diagnostic_trace.py` mtime参照は期限切れdiagnostic/evidence stageのretention cleanup用途でworkflow start/completion判定には使わない。

DB/migration、pytest、対象launch CLIはいずれも本agentでは実行していません。変更後の直接影響nodeをSTEP02へ依頼済み。v19 migrationの既存行保存/新規nullable/retired trigger再確認もSTEP02担当です。現時点で本番DBを適用していないため、その実DBへのschema反映結果は未確認です。

### STEP03独立review差戻し対応とC08-09 schema差分（2026-10-21）

**今回の追加変更ファイル**
- `automation/inago/maintenance/worker.py`: `_record_outer_workflow_failure()` は従来の8段階JSON handoffと`stage_handoff` proof builderを使わず、同一jobの`workflow-fallback.md`へ実際に未取得の7担当結果、job/launch/run/date/check、失敗コードと例外種別、次の再開作業を平易に保存します。各stageの実行・完了を偽らず、invocation/next-owner JSONを新規生成しません。
- `automation/inago/maintenance/stage_handoff.py`（削除）: 旧JSON proofを生成する呼出可能utilityを退役。runtimeからの参照はなく、過去のテスト参照が残る場合はSTEP02が旧契約nodeを計画に従って整理します。
- `automation/inago/maintenance/skill_chain_runtime.py`: worker ledgerへ自然文回答と分離した`reported_save_path`を保存し、turn status、所有process終了コード、error/close errorを別フィールドのまま終端集計します。workflow terminal statusは実turn/process結果でのみ判定し、報告pathとartifact内容の未確認を完了の根拠にしません。保存ファイルのbytes/hash/mtime/marker検査や全artifact再読はしません。終端情報に「artifact path reported」と「artifact contents re-inspected=false」を分けて出します。HTML review本文のmarker regex分岐を除去し、レビュー本文が空またはmarkerなしでもHTML作者へ再起動、作者の後に同じ別reviewerへ最終HTML pathを渡して再読させます。レビュー指摘の有無で後続を止めません。
- `.codex/agents/rr-inago-incident-investigator.toml`, `.codex/agents/rr-inago-incident-reporter.toml`, `.codex/agents/rr-inago-incident-report-reviewer.toml`および`.agents/skills/rr-inago-incident-investigation/SKILL.md`, `.agents/skills/rr-inago-incident-report/SKILL.md`: 3-key JSON応答およびHTML review markerを要求せず、自然文と実保存path、レビュー本文のhandoffを記述。
- `automation/inago/maintenance/store.py`: STEP02 C08-09の隔離DB診断でmigration後に残る16個の過去テーブルが厳密なtable-set比較で拒否されていた。現行必須table群の存在を確認しつつ、列挙済みの16個の歴史tableのみ追加で許容します。不明な追加tableは引き続き拒否し、過去table/rowの削除や再書換えはしません。

**要件対応**: STEP03 review CR-01/CR-03/CR-04の内容fallback、終端の自然文path・実turn/process分離、HTML review→author→same reviewer再読、investigator/reporter handoff応答を反映。追加のC08-09差分はSTEP02から渡された隔離DBの`expected_not_actual=[]`と16 historical extrasの実測に対応し、`_SCHEMA_TABLES`の必要要素だけを必須にしました。10/20の実DBは開かず、日次run/launchや本番migrationも実施していません。

**機械確認**: `.venv-inago/Scripts/python.exe -B -c "import ast,pathlib,tomllib; fs=['automation/inago/maintenance/worker.py','automation/inago/maintenance/skill_chain_runtime.py','automation/inago/maintenance/store.py']; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8'),filename=p) for p in fs]; cfg=['.codex/agents/rr-inago-incident-investigator.toml','.codex/agents/rr-inago-incident-reporter.toml','.codex/agents/rr-inago-incident-report-reviewer.toml']; [tomllib.loads(pathlib.Path(p).read_text(encoding='utf-8')) for p in cfg]; import automation.inago.maintenance.worker,automation.inago.maintenance.skill_chain_runtime,automation.inago.maintenance.store; print('ast_parse=ok files=3 toml_parse=ok files=3 imports=ok')"` → `ast_parse=ok files=3 toml_parse=ok files=3 imports=ok`, exit 0。対象差分の`git diff --check`はno diagnostics（skill Markdownに既存CRLF変換warningのみ）。限定`rg`でactive runtime/config/skillに古いHTML marker判定・3-key final JSON文言なし。pytest、DB、CLI、migration apply、本番反映は未実施。

**STEP02 handoff/未確認**: 今回のworker fallback/terminal/review prompt changesで直接影響するplanned nodes、およびC08-09 schema initialization nodesだけをfocused retestしてください。特にC08-09は根本回帰runでなく、schema-table recognition修正の局所確認を依頼します。旧`stage_handoff`をimportする歴史nodeは廃止契約のassertionを残さない形で扱ってください。全STEP01回帰、実DB migration、対象launch実動は未確認のまま後続工程へ進めます。

**最終静的確認**: 上記後にmaintenance配下の全21 PythonファイルをAST parseし、CLI/worker/skill-chain/storeをimport、3つの担当TOMLをparseし全て成功しました。対象runtime/config/skillに旧`stage_handoff` API、HTML marker分岐、3-key応答文の静的残存なし。対象diffの`git diff --check`はno diagnostics（既存Markdown CRLF warning）。これはコード静的確認であり、pytestやDB初期化実測の代わりではありません。

### C08-11 job finalization reason 修正（2026-10-21）

STEP02から受領したC08-11の実測: providerはterminal unknown時に`final_status='failed'`を渡すが、`reason_code`を渡さず、`MaintenanceStore.finalize_job()`のtransaction開始前validationが`job_terminal_reason_required`で拒否。worker queueはcompleteとなる一方、maintenance jobはqueuedのままという不一致でした。変更は`automation/inago/maintenance/skill_chain_runtime.py`だけです。terminalの実際の`reason_code`が許容形式なら同値を渡し、不正/欠測なら固定のtruthful `skill_chain_terminal_incomplete`を渡します。incident statusは`in_progress`のままで、incident resolutionを推測しません。finalizeが成功したときは返却された実job statusを`finalization_status`としてcalls ledgerへ保存し、失敗時は`not_recorded:<exception class>`を保持します。immutable job/launch/run/date identityもeventへ記録します。worker queue完了/role起動をfinalization結果でgateしません。

この局所変更後の試験は未実施。STEP02へC08-11の対象node（failed terminal reason persistence + queue completion）と直接影響するC08をfocused rerunするよう依頼します。pytest、DB、CLIはこの実装担当では未実行です。
