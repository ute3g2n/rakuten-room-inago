# AT-MAINT-01 保守対応要件・受入契約

正本：要件定義書HTML 2.50 §27.17～27.18。対象：MR-001～029 / MA-001～029。これは新しい日次不具合対応基盤の補足受入契約であり、既存IR-001～028・AT-001～028・AT-S/AT-A/AT-TRACE/AT-OBS契約を置換しない。計画ケースC01～C67、LC01～LC06、運用受入OP01～OP04との対応は要件正本と実行計画M §10・§11に従う。

## 共通受入規則

- 受入状態は `PASS / FAIL / 未実施 / 保留` を区別する。未収集・skip・xfail・error・未実施をPASSに数えない。
- 本契約は各STEPで実装・導入する受入条件である。U02の完了は文書の静的追跡・正本再生成確認、U05の完了は隔離した保存層/C06/C07/C28/C53/CLI契約の確認を示す。いずれもアプリ全回帰、モデル設定の実適用、本番起動、実障害の修正が済んだことを意味しない。
- 時刻・日付はJSTを基準にし、対象日、観測日時、受付日時、作成日時、反映日時を別に保持する。保存runがない場合 `source_run_id=null` を許可する。
- 報告本文の `created_at_jst` はJSTで検証する。独立レビューの `reviewed_at` はISO-8601のtimezone-aware日時を受け付け、保存時にJSTへ正規化する。timezoneのない時刻は拒否する。
- 業務状態の正本はmaintenance管理DBとその検証済み成果物である。診断JSONLは証拠参照であり、日次の元結果や管理DBの状態を上書きしない。
- 安全なID・列挙値・範囲内数値・参照だけを診断ログへ出す。prompt、応答本文、商品/投稿本文、URL、query、DOM、認証情報、秘密、個人情報は出力しない。
- 本人の採用済み基本設定を維持する。新しい商品再分析、自動ROOM投稿、過去日分の下書き追生成を追加しない。
- テストコードの独立レビューは行わない。デバッグ中にテストの不具合が判明した場合だけ、該当箇所を最小修正する。レビュー対象は製品コードと仕様である。

## U23 現行方針（2026-10-19）

このシステムは本人1名がローカル環境だけで使い、堅牢性を品質目標にしない。自作制限が本人の明示した起動・再開を止める場合は制限そのものを除去し、迂回flag、追加承認、blocked/待機へ置き換えない。U23は通常のローカルpytestと別担当の通常レビュー応答で進み、検証済みOS sandbox、host-observed review receipt、C34 HostPytestRunner実測、再開/レビュー/同時実行/ログ件数上限を開始・完了条件にしない。10/19対象の自動不具合対応を除外し、10/17履歴jobだけを扱う。以前のU05～U21の厳格な記述は履歴であり、この現行方針を上書きしない。

自動不具合対応フロー全体で、前工程の状態を理由に後続処理を止めない。「絶対にどうしても必要」なgateは基本的にない前提とする。review/test/readiness/release status、原因unknown、証拠不足、FAIL、rejected/changes_requested/unavailable、retry/review回数は記録値として次担当と報告へ渡す。対象jobと担当stageが分かり、担当作業に絶対不可欠な対象・入力があれば、担当agentを直ちに実起動する。reviewerは対象versionまたは成果物があれば、cacheの有無や前段statusにかかわらず実際に起動してreviewを依頼する。起動前の推測で起動不能・未実施と決めず、合成`unavailable`結果で実起動を置き換えない。任意証拠、host/runtime receipt、model/effort一致、session記録・resume状態、完全な入力一覧、古い/不正なcache、サイズ上限は起動条件にしない。起動要求に対する実環境の応答で本当に開始できなかった場合だけ、その実際の失敗を記録する。ある個別操作を実際に実行できない場合は、その操作だけ未実施として記録し、実行可能な工程とreportを続ける。report担当はreview所見を反映してreportを自分で修正できる。工程の完了は全操作の成功を意味せず、実施・未実施・失敗を正確に記録して引き継いだことを表す。

### 後続工程の状態ゲートを設けない

調査、計画、実装、試験、コードreview、反映、reportのいずれも、review/test/readiness/release status、原因unknown、証拠不足、FAIL、rejected/changes_requested/unavailable、retry/review回数を理由に次工程を止めない。「絶対にどうしても必要」な後続gateは基本的に存在しない前提とする。指摘・失敗・未確認は値を変えず、次担当と最終reportへ引き継ぐ。特定の操作を実際に実行できない場合も、その操作だけを未実施として記録し、後続工程とreportを続ける。対象を選べず誤操作になる場合はその操作を行わず、同じ制約でworkflow全体をblockedにせず、実行可能な工程を続ける。後続の節に残る古い承認/readiness/release gateや「blockedで停止」の記述は、現行の後続処理gateではなく当時の契約・実装履歴として読む。前工程の記録に欠落や不一致があれば、該当する個別操作の未実施・失敗として残し、独立した後続工程とreportを続ける。

## U05管理DB・状態実装の対応

管理DBは`D/maintenance/state.sqlite`（初期SQLite schema v1、U21 schema v9、現行コードschema v16）、専用設定は`D/maintenance/settings.json`（settings schema v1）とし、日次`D/state.sqlite`とは別fileにする。U05/U12の最大review巡回数、active-job枠、release gate、lease/process identityを再開条件にする契約はU23で撤廃した。resume_countとreview履歴は監査値。Migration 013はfull-regressionの実測outcomeをPASS/FAIL/SKIP/ERROR/NOT_RUNのまま保存し、全PASSを後続工程の条件にしない。repair review schemaは`approved/changes_requested/rejected/unavailable`を受け入れ、各statusを結果として保存する。現行CLIは`python -m automation.inago.maintenance.cli --data-root D [--daily-database PATH] {init,status,probe-ai,enqueue-historical,worker,watchdog,resume-worker,release-gate,start-job,finish-job}`。2026-10-19時点のproduction DBはschema v12で、schema v13への更新はU23 deploymentで行う。

U05の当初受入ではstep承認、active job枠、release gate、review 3巡上限、handoff leaseを要求したが、U23本人指示でjobの起動・再開を止める上限・gate・他job/lease条件を撤廃した。現在は対象job/launch IDを選べば再queueでき、resume_countとreview回数は上限なしの監査値。工程の記録ではresult versionとartifact参照を保持する。U05時点のproduction DB未変更は当時の状態であり、後続U21/U23の適用記録は実行計画/実行記録を参照する。

## U07 parent session・manifest・sandbox契約

U07 migrationはmanagement DBをschema v1からv2へ進め、`job_input_manifest`、`maintenance_ai_session`、`maintenance_ai_observation`を加える。job_idで対象jobを選ぶ。`incident_id`/`source_launch_id`/nullable `source_run_id`/`target_date`の欠落・不一致・曖昧さ・nullness不一致はunknownとして記録し、値を推測で補わない。対象jobと作業対象が分かる場合は、これらの欠測を理由にagentを起動前に止めない。manifestとsession設定は記録可能な範囲で保存し、観測はappend-onlyとする。

親Aのroleはgpt-6.1-sol/high、親Bのroleはgpt-6-luna/highを採用設定候補とし、tier実値は別に観測する。role TOMLのdeveloper instructions、設定digest、requested model/effort/tier、基本sandbox modeは記録する。role設定の欠落・不正やmodel/effort/tierの未観測・不一致はfallback可能な設定で起動し、担当の起動条件にしない。tierの未観測を推測でfast/priorityの対応済みにせず、利用可能な環境既定値を記録する。per-folder access/readOnlyAccess/readableRoots/writableRootsがないことは拒否理由にしない。観測イベントに設定値がない場合はconfigured_unverifiedとし、未観測・不一致を記録するだけで後続stageを止めない。

調査/review roleはreadOnly mode、親Bの実装/試験roleも唯一の固定workspace C:/project/rakuten-room-inagoで担当file/hunkのみ変更する。CLIのsandboxにper-folder rootsは設定しない。roleのallowed_outputsは作業指示・返却schemaの所有契約であり、OS/CLIのfolder ACLではない。app-serverへ渡すStructured Outputs schemaは対応subsetへ整形する。固定値はenum等で制約し、objectは全propertyをrequiredにして`additionalProperties:false`を指定する。各turnの返却JSONはhostが元のDraft 2020-12 JSON Schemaで完全検証し、manifestの5項目identityが完全一致することを確認する。server側の構造化出力だけでは完全一致を確定しない。host/controllerは管理DBから実行中step attemptを読み、job/source runとの一致を検証する。出力rootは`attempts/<job_id>/<attempt_id>`、worktree rootは`worktrees/<job_id>`に固定し、別job/attemptのrootを拒否する。設定済みrootからjob/attempt directoryまでの各path componentにreparse point（symlink/junctionを含む）がなく、解決後もroot配下であることを起動計画時・turn前・保存時に再検証する。これはCLIのfolder ACLを設定する要件ではなく、hostがroot外へのpath aliasを拒否するための確認である。allowed_outputsはAIの直接書込権限ではなく、親Bの実装/試験roleはmanaged worktree相対、それ以外は当該attempt output相対の安全なパスだけを受け付ける。Windowsで無効な文字、末尾ドット/空白、予約デバイス名、正規化後に衝突するpathは拒否する。host/controllerは`maintenance-ai-response-v1` envelopeにidentity、attempt_id、role/config digest、allowed_outputs、thread/turn、観測状態、検証済みresponseを束ね、当該attempt output rootへ新規JSONとして原子的に保存し、path/hashを返す。親BのGit commandでは継承された`safe.directory`設定をprocess環境内でリセットし、対象jobの検証済みworktreeだけを追加する。他のGit環境設定は維持し、global/system Git設定ファイルやOS ACLは変更しない。Bはhandoff/release前にworktree diffを宣言済みownerと照合し、範囲外変更を未承認として次へ渡さない。sandbox全面解除や日次T02への権限流用はしない。親セッション実接続のLC01/LC02はU18で一度確認し、U07の隔離試験を実モデル適用済みと数えない。app-server transport JSONLのfield/pathと日次L02との分離は[AT-TRACE-01](AT-TRACE-01_共通トレース契約.md)に定義する。

Windowsのhost-created managed worktreeでは、app-serverのcommand runnerが親プロセスのGit環境変数を子commandへ引き継がない場合がある。Gitが所有者SIDの不一致を報告したときだけ、controller-bound attemptの正確なworktree rootを`git -c safe.directory=<worktree root>`で該当commandに指定する。これはそのGit invocationだけの設定であり、`--global`、wildcard、Git設定ファイル、OS ACLは変更しない。

## U08 daily launcher・最終完了通知

U08は日次taskと`run.ps1 daily`から専用launcherを呼び、maintenance DBへ起動記録をcommitしてから日次CLIを子processとして開始する。実際の子PIDを記録して終了を待ち、CLIがexport・終了保全・共通trace・L01最終記録を含む全処理を返した後にだけ最終通知を確定する。launcher、子process、ownerの終了確認前にmaintenance workerを起動しない。有効で整合する日次結果のexit code 0/4/5（正常/実行・保全等の失敗/処理エラーのない不足）は保ったまま返す。結果JSONがないのにexit 0、またはstatus・終了codeが矛盾する要約は契約失敗exit 4へ正規化する。日次全体の固定期限は設定せず、Schedulerの`ExecutionTimeLimit=PT0S`と個別有限timeoutを維持する。

U08 migration `003_daily_launch.sql`でmaintenance schemaをv3へ進め、`daily_launch`に`source_launch_id`、JST `target_date`、owner/child PID、`running/complete/failed/blocked/unknown` status、nullable `source_run_id`、exit code、完了時刻、安全な結果summaryを保存する。起動前停止はrun IDを創作せず、失敗phase/class、failed check、trace欠測を記録する。正常・不足・未実施の要約は対象日一致、正常/不足はrun IDを必須とし、overall/daily statusはnullにしない。daily_checkとterminal launch resultは同一transactionで確定し、terminal recordは変更/削除できない。結果summaryとstderrにはallowlist済みの識別値・状態・code/classのみを記録し、raw stderr、自由文、秘密、path、下書き数は記録しない。

起動記録を保存できないときは子processを起動しない。有効な結果の完了記録を保存できないときは起動行を`running`のまま残し、通知欠測を出力しながら元の日次exit codeを維持する。launcherは既存production DBを自動migrationしないため、必要なschema v9が事前に導入済みの場合にだけ日次CLIへ進む。U08/U09/U18のtask XML変更は予定差分であり、production DB migration/Scheduler登録と実日次起動はU21の導入ゲート後に行う。

U09 migration `004_watchdog_confirmation_queue.sql`でmaintenance schemaをv4へ進める。daily launch owner/childのPID開始時刻を追加し、未起動/途中終了の`watchdog_queue`をtask/対象日単位のoccurrenceおよびsource launch単位で一意化する。occurrence keyは予定時刻ではなくtask keyと対象日から作り、schedule時刻変更後も同じqueueへ結び直せるようにする。Scheduler historyが無効でも予定時刻から欠落を検知し、5分猶予後にrunがない場合は`unknown`として確認queueへ置く。遅れて起動したrunは同じqueueへ結び直し、その後通知前に停止した場合も同じqueueの理由を更新する。所有PIDが生存中、観測不明、またはchild PID未記録の行はqueueせず、別processをkillしない。`watchdog_cursor`は照合済みの最新日を単調に保持し、各回は新しい予定日、当日、未解決queue日、running launch日だけを照合する。既存の未解決queue日は現在のScheduler開始境界より古くても照合する。running launch用partial indexと日付単位の一括読取で過去全履歴の再走査と日付ごとのDB lookupを避ける。watchdog/worker task XMLと明示的なrender/registration scriptを追加する。実登録前にCLI worker commandと`worker --help`の`--once` option、および全task名を確認し、登録失敗時は今回追加したtaskだけをrollbackする。U09ではworker command未実装のため登録経路はfail-closedで、Scheduler登録・production migrationはU21まで行わない。

## U10 evidence collector・redaction・snapshot契約

collectorは`automation/inago/maintenance/evidence.py`の入力APIとして、M §6.1の26分類だけを受け付ける。入力は管理側から明示されたsource rootと相対パス、分類ID、形式、source参照、対象日、run/job/request/actionのsafe IDとする。物理source pathはmanifestへ書かない。パスはroot内に解決し、reparse pointを拒否する。source rootの実体identityをno-followで開いたhandleから取得・照合し、POSIXではroot descriptorからdirfd相対open、Windowsでは削除共有を拒否したroot handle相対`NtCreateFile`を`OBJ_DONT_REPARSE`付きで行う。Windowsのleafは`FILE_OPEN_REPARSE_POINT`で開き、reparse属性とfinal pathも確認する。source pathに対する事前の`exists`/`lstat`検査は行わず、安全なhandle openの結果で存在を判定する。カタログ時点で存在を確認済みのsourceがopen時に消えていれば`disappeared`、元から未保存等と判明していれば指定されたmissing statusを返す。JSON/JSONL/responseはopened handleがregular fileと確認した後にサイズを判定して読む。特殊fileは取得不能とし、可能な環境ではnonblockingでopenして読み取り停止を防ぐ。これはCLIのフォルダー単位アクセス制限を設定する要件ではない。

`maintenance-evidence-v1`は26個すべてのclass summaryを持つ。`not_listed`は今回の入力manifestにその分類が現れなかったことを示し、source自体が存在しないという結論ではない。sourceごとのstatusは`collected`、`content_omitted`、`empty`、`empty_response`、`invalid`、`invalid_response`、`not_saved`、`expired`、`unavailable`、`suppressed`、`disappeared`、`omitted_size_limit`、`no_correlated_records`を区別する。欠測statusではrecord countをnullにし、成功や0件へ変換しない。

eventは固定allowlistのIDとfield別有限enumの状態/code、範囲内件数だけを出力する。未知のenum値は出力せずdrop countに加算する。既知のdaily result status `preservation_failed`、`export_failed`、`metrics_failed`は保持する。identity keyは大文字小文字を正規化して照合し、正規化後に異なる値が衝突するrecordは除外する。GitHub形式を含むcredentialらしいIDも拒否する。自由文、prompt/response、商品・投稿本文、URL/query/DOM、認証情報、個人情報、path、commandを出力しない。raw時刻は厳格なISO日時または有効なUnix秒だけを保持し、parse失敗・書式外・秘密検出値はnullにして、取得できる値はUTCへ正規化する。run/job/request/actionが一致すればJST日付をまたぐ同一事象を結び、関連identityを持たない記録だけ対象日のJST時刻で絞る。X02にはtarget `thread_id`を必須とし、同じthread IDが一致するrecordだけを採取する。manifestには元データのretention state/expiryを保持し、元sourceの期限を変更しない。

SQLite sourceは単純copyせず、read-only接続からSQLite backup APIで別のprivate destinationへsnapshotする。snapshotはintegrity checkとforeign-key checkを行い、hashはbounded chunk readで算出する。合成試験ではcommit済み状態だけを確認する。full DB snapshotやbrowser/binary/HTML/unstructured textはAI用redacted eventへ含めない。JSON/JSONLのredacted artifactとmanifestは新規bundleへstageし、全書込後にatomic renameする。artifact名はopaque連番を使い、入力`source_ref`をWindows file nameとして使わない。writerは生成形式`artifacts/<class-id>/source-<sequence>.jsonl`以外を拒否する。schemaは26 class IDを各1件に限定し、classごとのsource formatとX02のthread ID必須条件も検証し、class-only statusをartifactに許さない。失敗時に完成manifestを残さない。schemaは`automation/inago/schemas/maintenance-evidence.schema.json`を正本とし、保存先保持/容量/孤児回収はU17のretention契約に従う。

## U11 日次結果の事前分類・check-result v1

実装正本は `automation/inago/maintenance/detector.py`、出力契約は `automation/inago/schemas/maintenance-check-result.schema.json` の `maintenance-check-result-v1`。これは完了通知、当日の日次結果、U10 evidence manifestを対象日・source_launch_id・nullable source_run_idで照合する純粋な判定器であり、永続保存、AI起動、incident作成・更新はしない。MN02への保存とSol/xhighの[1]check接続は後続U12で行う。

入力の必須ID・日付・status・件数・再試行・phaseを機械的に照合する。U10 manifest全体は `automation/inago/schemas/maintenance-evidence.schema.json` で検証してから使い、必須field欠落・型/format違反・不正な版は `manifest_invalid` としてunknownにする。目標は15、初期枠も15。補充枠3は別項目のまま扱い、確定12なら不足3とする。補充枠を目標へ加算しない。前日画面の12件を本日の結果へ流用しない。確定件数・不足数など不明または欠測の値はnullのまま返し、0や正常に補わない。run未生成の場合は `source_run_id=null` を保持する。

| 出力 | 許容値・意味 |
|---|---|
| `preclassification` | `normal_candidate`、`candidate_shortfall`、`recovered_retry`、`external_wait`、`needs_investigation`、`contract_violation`、`unknown`。既知の正常不足と回復済みretryは正常系、観測済み失敗は要調査、外部停止は外部待ち、矛盾は契約違反、根拠不足はunknown。 |
| `check_status` | `normal`、`normal_shortfall`、`abnormal`、`unknown`、`incomplete`。事前分類と独立してcheckの状態を表す。 |
| `reason_code` | `daily_complete`、`daily_target_unmet`、`daily_recovered_retry`、`external_dependency_wait`、`daily_failure_observed`、`daily_result_missing`、`evidence_incomplete`、`manifest_invalid`、`identity_mismatch`、`status_mismatch`、`count_invariant_violation`、`retry_contract_violation`、`phase_contract_violation`、`retry_observation_missing`、`daily_observation_incomplete`、`daily_not_due`。 |
| `counts` / `retry_summary` | 既定の確定数・割当数・不足数・実投稿数・source失敗数・処理error数とretry件数。欠測はnull。各数値は0以上の整数範囲。 |
| `phase_results` | daily、metrics、export、preservation、開始/終了backup、monthly restore、traceの個別状態。未実行・未構成・unknownを区別する。 |
| `evidence` | U10 manifest schema・日付・起動ID・run IDの検証結果と不足class。正常候補判定に必要なL01/L02/M03/S01/V01/B01/B02がすべて `collected` でなければ正常にしない。 |
| `routing` | 常に `next_action=check_only`、`auto_dispatch=false`、空の `steps_started`、`incident_write_allowed=false`。調査・修正・報告の[2]～[6]を起動しない。 |

OS exit code 0だけで成功と判定しない。完了通知・日次status・exit code・phase・件数が矛盾すれば `contract_violation`。ただし、nested phase statusが`unknown`または欠測というだけなら既知statusとの矛盾とは扱わず、観測不足として `unknown` 判定に残す。known phase statusがトップレベルと食い違う場合は引き続き契約違反とし、正常候補には必要なphase/evidenceの完備を求める。既知外部stop code `authentication_required`、`shared_deadline_exhausted`、`source_cancelled`、`source_timeout`、`transport_error` は失敗時に `external_wait` として切り分ける。証拠や完了通知が不足して判定できない場合は `unknown` とする。候補枯渇による不足は日次理由 `candidate_or_theme_exhausted` とstatusが一致するときだけ正常不足にする。

C20/C21/C22とC55のdetector部分はU11専用fixtureで確認する。C23の同日重複・再発抑止はU09の完了証拠を再利用し、U11では再実行しない。C19のcheck-stage構造と隔離Store記録はU12の合成fixtureで検証し、実Sol/xhigh app-server接続はU18のLC01/LC02で確認する。C55のreport表示受入はU16で検証する。ライブincident・production DB・認証・投稿は変更しない。

## U12 check・調査出力・独立review・handoff

U12の構造化stage契約は `automation/inago/maintenance/investigation.py` と同ディレクトリのresponse schemasを正本とする。U11の事前分類結果をcheck stageへ渡し、`maintenance-check-response-v1`のschema、利用可能なmanifest identity、evidence refs、assessment/reason_codeの状態を記録する。`unknown`、`contract_violation`、`external_wait`を理由に後続stageを止めない。normalは`daily_check`へ記録しincident/jobを作らない。調査対象・unknown・外部待ち・確認不能は状態を保持してinvestigationへ渡し、得られる情報の範囲で後続を進める。対象identity自体を特定できず誤った記録になる場合は、その個別の書込だけを未実施にする。これはU11の`maintenance-check-result-v1`純粋判定出力とは別のstage responseである。

調査responseは `maintenance-investigation-response-v1` とし、schema_version、対象日、incident/source launch/source run/job/attempt、base commit、input/evidence refsを省略せず一致させる。factの日付を対象日に固定し、各factのevidence refsをmanifestの許可範囲に限定する。cause_confidenceがunknownなら原因文を許さず、裏付けのない原因断定を拒否する。reportは9章を固定し、factが3件を超えてもすべての操作・期待・実結果・影響・回復・原因確度・根拠を記載する。

controllerは利用可能なfacts、9章報告、版・identity・base commit・input/evidence refs・artifact hash・review状態を`facts.json`、`investigation.md`、`result.json`としてattempt専用rootへ保存する。既存ファイルを上書きしない。path integrityは書込先の対応付けに使い、CLIのfolder-level ACLは要求しない。調査statusがblocked/unknownでも利用可能な成果を保存し、statusやreviewのapproved状態をhandoff条件にしない。特定のartifactを書けない場合はその書込だけ未実施として記録し、他の独立stageとreportを続ける。

reviewは`maintenance-investigation-review-v1`でreviewer ID、review_execution_id、reviewed_versionをresponseへ結ぶ。reviewerはauthorと別IDで、review結果は対象result versionを記録する。review roundは履歴値として保存するが上限を設けず、回数を理由にreviewer起動や次attemptを拒否しない。同じexecution responseの再送は記録済み結果を再利用する。U23ではresumeを最新status/review count/release gate/active job/leaseで拒否しない。handoff再開は同じ対象IDを使い、状態の再記録は冪等とする。U12のC19/C29/C30は合成fixtureで構造・保存・Store連携を確認する。

## U13 修正計画・独立review・run-null logging contract

U13は`automation/inago/maintenance/planning.py`、`maintenance-planning-response-v1`、`maintenance-planning-review-v1`を正本とする。計画開始時にjob manifest、release base commit、最新のapprovedかつhandoff acceptedなU12 investigation result/facts artifactを読み、5項目identity、調査attempt ID、result version、base commit、input/evidence refs、result/facts SHA-256を照合する。U12 result/factsの実使用evidence refsは相互一致し、manifestで許可されたrefsの部分集合でなければならない。各factのrefsは実使用refsの範囲内に置く。呼出側が調査版や参照を差し替えた場合は、計画artifactを作る前に拒否する。

計画responseは0～14節を番号順に持ち、各STEPは14項目と単独で実行できる全文promptを持つ。各caseは12項目、実在するmanifest nodeを持つ。対応表は各factのevidence refsから要件、STEP、caseまでを相互追跡し、全要件・fact・STEP・caseの対応漏れを拒否する。原因確度がunknownのfactは確認のみまたは保留とし、原因を前提にした修正を許可しない。成果物`plan.md`、`requirement-step-case-map.json`、reviewerの`review.json`/`review.md`、hostの`result.json`は各stage attempt専用rootに新規保存し、U12同様にpath integrity/schema/identity/owner/hashを検証する。CLIでfolder-level ACLは要求しない。

C32の計画追加ログ契約は、nullable `source_run_id`、`overall_status`、`daily_status`、safe `reason_code`、`failure_phase`、`trace_status`と、L02/完了通知を作れない理由を示す`missing_reason`を計画結果へ記録する。許容値は`source_run_not_saved`、`trace_not_written`、`trace_unavailable`、`trace_degraded`、`none`。`source_run_id=null`なら`source_run_not_saved`を選び、それ以外はtrace状態が`not_written`/`unavailable`/`degraded`なら対応する値、`healthy`なら`none`とする。各値は保存済みdaily completionと照合し、7項目全てをschema/source/C32 nodeのbindingへ結ぶ。`missing_reason`はplanning response schema/source/testのみに追加し、既存`daily-launch-completion-v1`やL02へ新欄を黙って追加しない。run IDがnullならnullを維持し、架空IDを生成しない。raw SQL、認証情報、stdout/stderr、例外本文、秘密、個人情報は計画ログへ複写しない。

plannerとreviewerは担当ID・実行IDを分け、review対象versionを記録する。review入力には利用可能なplan/map、U12 facts artifact、daily completion summaryを含め、reviewerが事実対応とC32値を照合する。hostは`work_root`のpath integrityを記録するが、調査review statusやplan statusがreadyでないことを理由に後続STEPを止めない。findingsと未確定事項を次工程へ渡し、C31/C32は合成fixtureと隔離Storeで確認する。

## U14 repair・テスト台帳・独立review

U14 repairは利用可能なU13 predecessorのattempt ID、result version、outputを入力として使う。承認・ready/handoff状態は観測値とし、repair開始のgateにしない。新しいU13 attemptや未確定内容がある場合も、対象を選べる最新の利用可能な計画を使い、findingと不足を引き継ぐ。scope/path/schema/hashの不一致でartifactを特定できない場合はそのartifactだけを使わず、取得済み情報で次工程を進める。AI起動前のCLI folder-level ACLは要求しない。

標準`RepositoryRepairEvidenceResolver`は利用可能な変更対象とcase/nodeをledgerへ記録し、`LocalPytestRunner`は通常のローカルsubprocess.runで実行可能なpytestを実行する。PASS/FAIL/SKIP/ERROR/NOT_RUN/UNVERIFIEDとcollection差異を実測どおり記録し、試験結果をreview、反映、reportのgateにしない。検証済みOS sandbox、Store host run receipt、C34 host-runner実測は要求しない。修復testコードを独立reviewせず、デバッグで不具合が見つかった場合だけ修正する。

`maintenance-repair-test-ledger-v1`はcampaign identity、semantic case ID/node/signature、required/frozen case、full regression結果を保持する。U23の標準実行はin-memory ledgerと通常pytest結果を使い、receipt IDやC34 evidenceがなくてもreadyになる。従来のStore receipt APIは任意の記録経路であり、標準repairの開始・test・completionに要求しない。

repair artifactはattempt専用rootにpatch-assessment.json、test-ledger.json、review.json、result.jsonとして保存し、identity、attempt/result version、tested code SHA、reviewer、path/SHAを結ぶ。reviewはauthorと異なるreviewerの通常応答を対象版・code SHA・patch/test artifact SHAへ結び、別reviewer確認を維持する。U23の標準経路はlocal_reviewを記録し、host-observed review receipt IDやreceipt providerを要求しない。C34は任意の追加証拠であり、emptyでも完了できる。raw patch、console、AI応答、秘密、個人情報はtraceへ複写しない。

## U15 Git統合・反映・切替

反映では試験・review・readiness/release gateの状態を観測値として記録し、PASS/approved/readyを後続工程の条件にしない。commitや反映ができなかった場合もその操作の未実施理由を記録し、reportへ進む。対象commit/branchを特定できない場合は誤った対象への反映を避け、その反映操作だけを未実施にする。ユーザーの未コミット変更を混ぜず、force push、reset、履歴書換えをしない。

candidate branchへの通常pushはGit履歴を保存するだけであり、本番反映を意味しない。反映ではactive-main tracking targetのremote HEADを読み、保存base commitと一致する場合だけ通常pushを行う。競合、認証/接続失敗、fast-forward不能、対象差分との未コミットpath交差はblockedとし、force push、reset、履歴書換えを行わない。非交差差分はactive checkoutのstatusを前後で照合して維持する。

個別の反映操作を実際に試みられない場合はdeploymentを未実施として記録し、他工程とreportを進める。`deployed`は実際に切替を観測した場合だけ記録し、health/readiness不明を成功へ書き換えない。通常push、runtime switch、日次/API影響確認など各操作の結果を個別に記録し、未実施・失敗を理由にreportを止めない。U15 local fixtureはcoordinatorの挙動を確認する。

U15 migration `006_deployment_observations.sql`で管理DBをschema v5からv6へ進め、`deployment`へ`source_commit`、`base_commit`、`tested_commit`、`pushed_commit`、`active_commit`、`remote_name`、`target_branch`、`reason_code`を追加する。`deployed`はsource/tested/pushed/active commitがすべてrelease result commitと一致する場合だけ受理する。旧deployment行の追加欄はnullを保つ。result envelopeは`maintenance-deployment-result-v1`で、schemaは`automation/inago/schemas/maintenance-deployment-result.schema.json`。

U15のC38～C43/C57は一時DB、専用Git fixture/local bare remoteと通常pytestで検証する。検証済みOS sandbox、host-observed review receipt、C34 HostPytestRunner実測は要求しない。外部remote、本番DB、実runtime switch、実日次/APIへの接続は行わない。実runtime providerが未配線の場合はfail-closedとし、local test resultをproduction wiring evidenceと混同しない。実本番の統合・切替はU21で行う。テストコードの独立レビューは行わない。

## U16 incident report生成・レビュー・公開

report stageのresponseは`maintenance-report-response-v1`、最終結果は`maintenance-report-result-v1`、独立レビューは`maintenance-report-review-v1`とする。利用可能なincident/source launch/source run/target date/job identity、調査・計画の版、実装commit、試験・反映・次回観測状態、evidence refsを入力として扱う。不一致・未承認・許可外参照・不正リンクは該当する値を未確認として記録し、作成可能なreportをblockedにしない。report作成者はreview指摘を受けてresponseとHTMLを修正できる。

HTMLは操作・期待・実結果・影響・回復・原因確度・変更後動作・確認範囲、工程tree、試験一覧、未確認・未実施事項、根拠リンクを保持する。report statusは`complete`または`pending`で、未完了状態を成功に書き換えない。過去に入力・保存された`blocked`値は監査用の事実として保持するが、新しいreport stage resultには使わない。report reviewerのstatus/findingsは改善情報として記録し、report authorはその指摘を受けて修正できる。公開可能な経路がある場合はreview approvedを待たず公開処理を試し、公開不能ならその理由を記録する。いずれの場合も他の後続処理を止めない。

同じformal attemptにreport author/reviewerの複数responseが保存される場合、host readerは同一job/attempt/identity/role/configurationを検証した全response候補を返す。今回起動したauthor/reviewer turnの結果はthread_idとturn_idの両方が一致する候補を一意に選ぶ。再開時に保存済みresponseを再利用する場合は、report result_versionとcreated_at_jst、またはreview対象のreviewed_versionとreviewed_sha256が今回の入力に一致する候補を一意に選ぶ。候補がないか複数一致する場合はそのresponseを再利用せず担当を起動し直す。以前のresponseが同じattempt内に存在することやファイル名順だけを理由に今回の修正版を失敗扱いにしない。

U16のC44～C46/C55は合成fixture・一時Git repo/local bare remoteを使う通常のローカルpytestで検証する。検証済みOS sandbox、host-observed review receipt、C34 production HostPytestRunner実測証拠を開発テストの前提・合格条件にしない。テストコードの独立レビューは行わず、デバッグで不具合が判明した場合だけ最小修正する。これらのローカル結果を実運用providerの実証として扱わない。実表示LC04が利用可能なブラウザー経路から確認できない場合は、完成HTMLを用意し未確認として本人の確認へ引き渡す。

## 受入条件

### U14～U20 開発テストの受入条件（2026-10-18 本人決定）
U14以降U20までの開発段階の合否は、合成fixture、一時DB、ローカルGit fixtureを使う通常のローカルpytestで判定する。検証済みOS sandbox、repair HostPytestRunner receipt、C34のrepair runner実測、host-observed review receiptはテスト前提・完了条件にしない。C34相当のRED/PASSやrunner/review境界は通常pytestから合成条件で確認し、独立production code reviewは通常の別担当レビュー記録で確認する。証拠はcommand、JST時刻、case/node、コード/test hash、期待/実結果、exit code、console/JUnitとする。テストコードの独立レビューは行わず、デバッグで不具合が判明した場合だけ最小修正して影響ケースを再実行する。テストはsynthetic fixture・一時DB・local Gitに限定し、production DB、Scheduler、実AI、投稿状態を変更しない。

この緩和はU14～U20の開発テストに加え、U23の10/17履歴jobに対する標準修復にも適用する。U23は通常ローカルpytestと別担当の通常レビュー応答で進み、sandbox、runner receipt、host-observed review receipt、C34実測を起動・完了条件にしない。以前のruntime receipt記述は任意の追加記録経路または当時の契約を示し、U23を止めない。本番反映・HTML公開は実施計画に沿って別記録する。

| ID | 合格条件 |
|---|---|
| MA-001 | 基準一覧と移行結果が文書ID単位で対応し、同じ文書の重複移動・欠落・未分類・移動先衝突が0。既存3件のGit除外ファイルもローカル実在文書として扱う。 |
| MA-002 | 管理対象の現行参照が索引の新パスへ解決し、通常リンク切れ0。履歴として残す旧パスは過去の位置と現在の入口を区別して説明する。 |
| MA-003 | HTML内部正本、生成ソース、同梱資料、図、辞書、追跡表、受入条件が同じ版に対応し、正本HTMLの再生成に成功する。旧Markdownのimportで新しい正本を巻き戻さない。 |
| MA-004 | 本人が採用済みの基本設定を保持し、未採用の詳細候補・比較値は候補のまま表示する。文書移動や要件同期だけで自動採用されない。 |
| MA-005 | 日次CLI最終結果・後処理・保全完了後に起動通知を確定する。起動前停止でrunがない場合、未起動、通知欠落、途中終了を識別し、存在しないrun IDを作らない。 |
| MA-006 | 正常日と根拠のある正常な不足は最小のcheck記録だけを確定し、原因調査・修正・報告の別案件を起動しない。 |
| MA-007 | 既知の不具合、外部要因、正常な結果、情報不足/未知を別状態に分類する。確認済み事実、推定、未確認と根拠参照が分離される。 |
| MA-008 | 同一起動のcheckを一意にし、重複incidentと修正jobを作らない。blocked historical jobとworker queueは、対象IDとsource launch IDで対象を選べば無条件に再queueできる。保存済みreason、review状態/回数、release gate、他のqueued/running job、Worker lease、resume leaseで再queueを拒否しない。resume_countとreview履歴は監査値として残すが、回数上限は設けない。 |
| MA-009 | check・調査・修正計画は親A gpt-6.1-sol / high / normalで実行し、調査・計画reviewは作成者と異なる担当の所見として記録する。指定や観測が得られない場合も未確認として残し、後続処理へ渡す。 |
| MA-010 | 実装・テスト・コードレビュー・報告は親B `gpt-6-luna / max / Fast`の下で別担当が行う。Fastは親Bの `service_tier=fast` と使用版の `features.fast_mode=true` の実適用を観測して確認する。 |
| MA-011 | 2親、6スキル、9担当の役割・モデル・考える量・入力・論理的な出力所有が一致し、reviewerはreadOnly modeとなる。出力所有はCLIのfolder ACLではなく、role契約とhost側のschema/owner/diff検証で確認する。日次T02の権限を流用しない。 |
| MA-012 | management DB、ID、版、状態、例外、出力所有、基本sandbox modeが実装schemaに対応し、run nullを含む欠測を正しく表す。AI出力はschema/identity/ownerを検証してから確定する。 |
| MA-013 | 既存Lの26分類は許可一覧に基づいて収集し、元のID・時刻・期限・相関・取得不能/失効理由を保持する。新しいmaintenance出力を既存日次traceへ混在させない。 |
| MA-014 | 元の日次結果と主例外を維持し、maintenanceログ・証拠コピーの期限、容量、回収、抑止/書込不能を明示する。元データの期限を延長・短縮せず、保持失敗を成功扱いしない。 |
| MA-015 | 調査レポートは9章を備え、事実/推定/未確認と根拠を追跡する。別担当reviewのstatus/findingsを記録して後続へ渡し、approvedを次工程の開始条件にしない。原因をerror名だけで断定しない。 |
| MA-016 | 修正計画が0～14節、STEPごとの14項目、ケースごとの12項目、各STEPの独立promptを備え、STEP・ケース・受入・出力が相互追跡できる。review所見や未確認項目があっても次の実装工程へ進み、所見を計画へ反映する。 |
| MA-017 | 実装・テスト・レビューの所有を分け、修正前の意味あるREDまたは根拠付きの「再現不可/文書契約違反」を記録する。U14以降U20までの開発テストは通常pytestの直接実行記録で判定し、OS sandbox、host-runner receipt、C34 runner実測、host-observed review receiptを要求しない。独立production code reviewは通常の別担当レビュー記録で確認する。実運用attemptでは上記runtime契約のreceipt要件を適用する。managed worktree外と所有外ファイルを変更しない。 |
| MA-018 | U23の標準修復では通常のローカルpytestを実行し、検証済みOS sandbox、repair HostPytestRunner receipt、C34実測証拠を開始・合格条件にしない。必要なcaseの結果は通常のpytest実行記録で確認し、テストコードの独立レビューは行わない。 |
| MA-019 | source/base/tested/pushed/active commit、試験、review、反映の実測状態を別々に記録する。review approved、全一致、health/readiness、release gateを後続stageの開始・反映条件にしない。可能な反映処理を進め、できなかった部分は未実施/失敗として後続報告へ渡す。ユーザー変更を混ぜず、force push、reset、履歴書換えを行わない。U15の開発テストは通常pytest/local fixtureで確認し、OS sandbox/host review receipt/C34 host-runner実測を要求しない。 |
| MA-020 | 対応HTMLは利用可能な入力の版・根拠を照合し、事象、原因確度、修正、試験、review、反映、未完了を平易に説明する。入力statusが未承認/未完了でも本文を作り、未確認を明示する。独立した報告reviewは所見として記録し、公開後の次工程を止めない。 |
| MA-021 | maintenanceの全stage・担当・指定/観測model/effort/tier・入力/証拠/成果参照・base/result版・レビュー・試験・停止/再開・deployment結果が追跡される。 |
| MA-022 | 日次全体とmaintenance全体に固定終了期限を設けない。job/request、再試行、review巡、再開の回数上限で処理を止めない。resume_count、待ち時間、各種状態は監査値として記録する。後続stageを日次優先・readiness・release gateで待たせない。 |
| MA-023 | 変更、テスト、実接続、review、commit、起動設定の状態を機械記録する。readiness/release gateは観測値であり、履歴受付・修正・反映・報告の開始を拒否しない。対象と実行環境を特定できる作業から進め、未確認項目を次工程へ渡す。 |
| MA-024 | 2026-10-17の元記録、件数、欠測、採取日時、元期限、対象日を保全し、観測されていないrun・trace・metricsを創作しない。稼働DBを過去snapshotで置き換えない。 |
| MA-025 | U21 ready後、元のfailed結果を保ったまま新フローが本日案件のcheck・調査・計画・実装・テスト・レビュー・反映・報告を行う。外側の手作業を新フロー実績として扱わない。 |
| MA-026 | 本日調査MD・修正計画MD・対応HTML、各独立レビュー、試験/反映/公開の証拠が同じ案件IDと版で結び、レビュー済み対応HTMLだけを一ファイルのGit commitとして通常fast-forward公開する。 |
| MA-027 | 10/17のfailed/IntegrityError、当日確定0、既存表示12、他の件数とユーザー報告を保持する。再実行・追生成・過去結果のsuccess化で元の記録を置換しない。 |
| MA-028 | 次の自然日次で修正対象の再発、正常日の不要工程省略、結果と報告の一致を観測する。未観測は検証待ち、再発は同一案件へ関連付けて再開し、追加の日次を試験起動しない。 |
| MA-029 | 対象jobと担当stage、絶対不可欠な対象入力が分かれば担当agentを直ちに実起動する。reviewerは対象versionまたは成果物があれば、cacheや前段statusによらず実際に起動してreviewする。起動前に未実施/unavailableと決めず、合成`unavailable`で実起動を代替しない。任意証拠、status、receipt、runtime/model/effort観測、session/cache状態、完全な入力一覧、任意のサイズ上限を起動条件にしない。真に不可欠な入力自体がない場合だけその不足を記録し、他の工程を続ける。実環境の応答により起動できなかった場合は、その実際の失敗を記録して後続を続ける。 |

## U17 MN04 trace・retention・運用確認

U17は通常のローカルpytestと合成fixtureを使い、MN04 `D/maintenance/logs/<target_date>/<maintenance_id>.jsonl`を含むC18/C47/C48/C58/C60を確認する。検証済みOS sandbox、host-observed review receipt、C34 HostPytestRunner実測証拠は要求しない。これらの開発テスト結果をproduction provider実証へ読み替えない。テストコードの独立レビューは行わず、debugで見つかった不具合だけ最小修正する。

C18はsource expiry/90日期限、4 MiB rotate、file count上限なし、90日超過log/evidence copy、既知orphan stage回収を確認する。L02のsource bytes/期限は維持する。C47は保留・review・repair・deploymentを含むworkflow event相関を確認する。C48は日次優先と全体終了期限なしを維持する。C58はwrite/fsync失敗時に既存logを壊さず成功eventを返さないこと、処理結果返却後のreceipt欠測でoperation resultを露出し、状態照合が必要と通知することを確認する。C60はQ/仕様/辞書/追跡/出力一覧/skills/roles/caseの対応を静的に監査する。

今日の運用境界：2026-10-18 12:19/12:47 JSTのhost読取では既存`RakutenRoomInagoDaily`はReadyで13:00に日次CLIを起動し、maintenance worker/watchdog taskは未登録だった。U18でworker CLI commandは作業ブランチへ追加したが、production task登録/DB移行は行わず、今日の自動不具合解析・修復workerを起動しない。通常日次は既存設定のまま維持し、U21のproduction導入gateを満たすまでworkerを有効化しない。

## U18 dispatch・履歴受付・翌日観測の受入

U18は通常のローカルpytest、合成fixture、一時DB、toy Git repository/local bare remoteで検証する。検証済みOS sandbox、host-observed review receipt、C34 HostPytestRunner実測は開発テストの開始条件/合格条件にしない。テストコードの独立レビューは行わず、debugで判明したfixture/API不具合だけを最小修正する。

| Case | 受入対象 |
|---|---|
| C49 / C51 | 正常checkのみで不要incident/jobを作らず、外部待ち・unknownは原因を創作せず保留する。 |
| C50 | investigation/planning controller、別担当review、toy repoでの通常pytest red→pass、修正評価、toy deployment、reviewed HTML publicationを同じ合成案件で結ぶ。これはproduction runtime providerやHostPytestRunner receiptの証明ではない。 |
| C52 | daily/worker/watchdog task XMLのaction・arguments・working directory・principal・run policy・instance policy・hidden値を比較し、driftを拒否する。 |
| C54 | 過去の失敗記録をrun-nullのまま一度受付け、同じsource/dateから同一incident/job/manifestへ冪等に収束する。元の日次結果を変更しない。 |
| C56 | deployed versionに対し、既存の翌日自然launch/checkだけを照合し、verified/recurred/blocked/not_observedを保存する。追加の日次起動はしない。 |
| C59 | daily launcher終了後、owner/child停止を確認してからworkerを一度dispatchし、重複通知を同じqueueへ収束させる。queue completeは障害修正の完了を意味しない。 |

U18のworker queue、historical intake、C50 controller chainはproduction task登録とは分離する。queueだけを消費した結果は`status=dispatch_complete`、`scope=dispatch_only`、`maintenance_resolution_status=not_started`として返し、障害の調査・修正完了と表現しない。調査から報告までの自動実行とproduction provider wiringはU21の合格・導入条件として確認する。C50の通常pytest結果をそれらの証明へ読み替えない。

U18 `probe-ai`は全turnの完了、要求modelと一致するthread設定、model rerouteなしを確認する。per-turn effort/service tierがprotocolから得られない場合は`unreported`として記録し、要求値を送っただけで適用確認済み・`verified`とは扱わない。一方、値が未報告であることだけではprobeを失敗にせず、観測された不一致・reroute・turn失敗・必要成果物欠落は失敗とする。調査/計画Markdownの番号見出しは必要な連番が重複・欠落・逆順なく現れることをhostが確認する。各reviewerの返却schemaは`approved`と`rejected`の両方を受け付け、拒否またはcommon reviewerのfindingがある場合はprobeを失敗として記録する。

repair profileのpytest結果は担当AIの申告だけで合格にしない。hostがApp Serverの`item/completed` `commandExecution` eventから、指定pytest commandの引数、`status=completed`、`exitCode=0`を確認する。実装担当後に取得したproduct diffのSHA-256はtester後にも照合し、差分が変わった場合は失敗にする。job/leaseを作る前にfilesystem-backed input/evidence参照を準備し、setup時の読取失敗でrunning jobやleaseを残さない。

C54のhistorical intakeは、許可root下からevidence fileをno-followで読み、実byte列のSHA-256をobservationと照合してからDB recordを作る。launch/dailyの両状態が`blocked`/`unknown`なら`abnormal` checkや修正jobを作らず、明示失敗が一つもない観測は拒否する。intake record用directoryはroot相対のno-reparse操作で作成する。

C56の再発理由照合はdeployment releaseに結び付くmaintenance jobが一件だけある場合に行う。jobがない、または複数なら再発incidentを推測せずblockedとする。`not_observed`は次日13:00 Asia/Tokyoより前に確定せず、対象日のdaily launchが存在する場合も確定しない。

## U21 worker queueの保留・明示再開

U21 migration `009_worker_queue_events.sql`はschema v9にappend-onlyの`worker_queue_event`と`daily_result_observation`を追加する。workflow未接続・stage失敗・外部待ちはstage outcomeとして保存し、後続stageを実行する。workflow providerがある工程を実行できなくても、残りのstageとreportの試行を続ける。queueの`complete`は全stageを試行したことを意味し、障害が修正済みであることを意味しない。stage別の失敗・未解決・欠測はreportと監査記録に残す。watchdogによる自動再試行は行わず、明示resumeは対象IDでいつでも行える。blocked/resumed履歴とdaily observationは変更・削除できない。daily observationは許可されたstatus/count/phase/trace件数を記録し、raw stdout/stderrや本文を保存しない。

U23 migration `010_historical_job_resume_events.sql`はhistorical jobの再開をappend-only eventへ記録する。未完了job/worker queueは対象IDとsource launch IDで対象を選べば無条件に再queueできる。reason/review状態・回数/release gate/他job/Worker lease/専用resume leaseで拒否しない。resume CLIは専用leaseを使わずtransactionで記録し、同じjobが既にqueuedなら冪等に成功を返す。`resume_count`とreview履歴は監査用で、回数上限はない。migration `011_multiple_resumable_jobs.sql`はqueued jobを複数保持する。review巡回上限も廃止し、変更要求後は回数で止めず同じ工程を続ける。

## U21 reportの公開・job/incident終端・再開

report公開は利用可能なreportとtarget情報で試みる。review approved、deployment済み、全inputの承認状態を公開条件にしない。statusや欠測はreportに記録する。公開結果は`attempts/<job_id>/<attempt_id>/report-publication.json`に保存し、report path/hash、target commit、source code commit、publication commit/base、remote/branch、観測時刻を記録する。credentialやHTML本文はこの公開結果へ含めない。公開は報告書HTMLだけを通常pushする。

同じ公開先に既存HTMLがある場合、同一jobの前回成果・review record・publication sidecar・remote上の祖先commit・commit内HTMLが全て同じhashとidentityへ結び付く場合だけ、その既存hashを置換対象として許可する。前回の承認済みreportとcontroller fallbackのどちらも、対応するreview状態を確認してから置換する。確認できない場合は公開を拒否し、候補attemptごとの不一致理由を安全なoperation contextに残す。公開操作が実行できない場合は具体的なreason codeを保ち、例外連鎖・捕捉位置・相関ID・入力/成果物参照をfailure journalと工程handoffへ記録する。将来のstage resultとしてblockedを返さず、unknownまたはfailedで終端照合と後続処理を続ける。

push後・publication artifact保存前に停止した場合は、利用可能なremote historyを見て既存publicationを再利用する。不一致や対象不明で公開操作を再開できない場合はその操作だけ未公開として記録し、汎用理由へ置換せず具体的reason codeを保持する。report結果は保存して他の工程を止めない。report publicationの可否と障害解消状態を混同せず、それぞれの実測statusを保存する。job/worker queueの終端statusは全stageを試行した結果として記録し、stageのapproved/readiness/release gateを終端条件にしない。

FC02の現行対応は工程の引継ぎ完了、個別操作の成否、公開、workflow完遂とincident解消を別に記録する。内容ある通常/fallback成果を保存して次担当を実起動し、旧pending/blockedを将来stageへ伝播しない。必須成果・公開版・終端read-backが揃った場合だけフロー完遂とし、原因や回復が不明ならincident_resolutionをunknown/unresolvedのまま保つ。公開未達なら原因と同一job再開位置を残して公開・終端整合操作を続ける。

## U23.08 日次障害の診断・計装

run作成と日次共有coreは、例外class、数値SQLite error code、許可済みSQLite分類、safe処理code、transaction phase、提案run IDと永続run ID、trace要求/書込結果を診断eventへ保存する。`source_run_id`を推測せず、提案IDを保存済みrunとして扱わない。日次pipelineのfailure logとJSONLは一度取得した同一のpipeline-local捕捉module/function/line/hashを共有する。この場所はcallback内の例外発生位置やroot causeを証明しない。hash取得不能は欠測/nullで記録する。

failure logはrollbackと業務failure resultの組立後にbest-effortで出力し、trace result logも同様に例外を隔離する。logging handlerの失敗でrollbackや元の日次結果を変えず、trace write failureを業務SQLite stateへ伝播させない。例外message/traceback/SQL/params/絶対path/秘密/個人情報は保存しない。状態値、review/test結果、原因unknownは観測値として引き渡し、次工程のgateにしない。隔離fixture試験のPASSをproduction運転や原因解決の証明に読み替えない。

## U02の確認範囲

U02では本契約と要件対応を静的に確認し、HTML正本を再生成する。アプリテスト・実モデル適用・実Scheduler変更・本番日次・10/17障害の原因調査は対象外。実装と実運用の合否は、それぞれ計画上の後続STEP/LC/OPで証拠を得る。

## FC02 全工程完遂・失敗追跡の現行契約（2026-10-19）

要件正本HTML 2.50 §27.18と承認実行計画1.2のFC-R01～10/FC-A01～10を局所対応として追加する。既存MR/MAを新規承認IDへ置換しない。旧U05～U23のblocked/approval/worktree/incident resolved結合は旧仕様履歴であり、現行は次の契約を使う。

- `stage_progress=pending/running/completed`、`operation_result=succeeded/failed/not_performed`、`content_result=confirmed/unknown/partial/no_change`、`artifact_origin=agent/controller_fallback`を分離する。新規stageにblockedを返さず、raw旧履歴は改変しない。
- 全stageは内容ある成果/handoffを保存し次専門担当を実起動する。FAIL/unknown/review/test/readiness/release/receipt/lease/回数/実設定観測で後続を止めない。個別操作不能だけ理由付き未実施とする。
- `workflow_completion`、公開結果と版、`incident_resolution`、元daily結果は別に保持する。原因/業務回復unknownのままflow完遂可能だが、公開・必須成果永続登録・終端read-backなしでcompleteにしない。job completeからincident resolvedを推論しない。
- 全失敗はoperation、cause/context連鎖と安全なstack frames、発生/捕捉位置、code revision、相関ID、input/output refsへ結ぶ。例外本文・SQL/値・秘密/個人情報は除外する。通常logger失敗は独立fallback spoolへ保存して本処理を続け、復帰時に追記照合する。begin-only中断と全sink喪失区間は検出時にunknown/abnormal_endを追記する。
- 通常/最小HTMLとも実reporterと独立reviewerを起動し、指摘は原作者が修正する。本文は平易に、技術記号は付録へ置く。公開版/hash/review/commit/remote/DB/finished_atを同一jobで照合する。テストコードの独立reviewは行わない。
- 固定workspace `C:/project/rakuten-room-inago`、10/17同一job/launch/null run、10/19除外、全role effort highを維持する。model/tier実観測欠測を起動gateにしない。FC02作成時点ではschema13がFC01実測、014はsource追加予定で本番未適用だった。FC02は文書とrole設定だけを同期し、製品成功/受入PASSとはしない。

詳細field・責務・case・所有は `worklogs/projects/maintenance/flow-completion-20261019/FC02/` のcontract-decision、stage-output-matrix、schema-change-list、file-operation-owner-map、requirement-step-case-mapを参照する。FC03～06は実装と同じSTEPで本文/schema/skills/図の変更を同期する。

## FC03 実装済みの失敗記録・再開契約

保守stageは相関ID、親operation、job/launch/run/incident/attempt、operation、開始/終了/失敗、実行版、例外class連鎖、コード位置、入力/成果物参照を記録する。例外本文、SQL/値、投稿、認証情報、個人情報は記録しない。通常診断ログの書込失敗は元処理を止めず、DBが使えない場合も通常loggerに依存しない独立journalへ記録する。次の起動ではjournalの未完beginを中断観測として追記し、fallback記録を管理DBへ冪等に照合する。source_run_idはnullを含め入力値を維持する。実装は管理DB schema 14の新規append-only event表と014 migration sourceを追加した段階で、本番適用は後続STEPで行う。


### FC04 調査・計画・実装の成果引継ぎ

調査担当の実行失敗や空・不正応答、証拠不足でも、確認できた事実と調べた範囲、不明な点、次の確認を含む9章の調査成果を保存して計画担当を実起動する。確認事実がないcontroller fallbackも、根本原因や修正を推測せず「確認できた事実なし」というunknown factと利用可能な入力参照を残し、計画担当が次回の原因特定に役立つ記録追加を計画できるようにする。controllerが代替文書を作った場合は担当AIの成果と区別する。計画担当は原因修正または次回の原因特定に役立つ記録追加の具体策を出し、両方ができない場合も変更しなかった理由を平易なHTMLにする手順を残す。調査や計画の失敗を将来stageのblocked結果にせず、必須対象が識別できる限り後続を続ける。修正担当は固定されたリポジトリroot内で作業し、別checkout/worktreeを作らない。

### FC05 試験・レビュー・反映と終端の継続

修正候補や試験入力が不足しても、試験結果は `NOT_RUN` と理由を成果物へ保存する。試験が実行できなかった事実を、レビュー担当の起動条件にしない。レビュー担当は利用できる差分または不足を示す成果を確認し、確認不能ならその理由を記録する。反映操作も同様に、現行版の確認または未実施理由を残して報告工程へ渡す。公開・工程完遂・障害解消は別々に記録し、原因や業務回復が不明ならincidentを解決済みにしない。

## FC06: reportの内容と継続
本文だけで「何が起きた/調べた/変えた/不明/次」を理解できる平易な日本語を使い、内部値は参考付録へ置く。reporter失敗時は最小HTMLを同一job/attemptへ保存し、実report reviewerへ渡す。review所見は作者が反映し、review不能でも未承認を正直に残して公開を試す。公開完了とincident解決を分ける。

source snapshotの調査・計画・コードreviewは、各sourceのstatusを対応させて表示し、固定文言で承認を補わない。未作成資料は本文の資料一覧へ誤ってリンクしない。リンク名は資料の役割と形式に合わせる。停止理由のnull/欠測は未確認とし、理由がなかったと断定しない。本文のreport statusは`complete`または`pending`とし、失敗・未確認はその理由と平易な表示で残す。工程tree、生status、reason code、内部ID、版キー、hashは技術付録へ置く。rendererはAI作成本文に状態コード等が含まれる時も本文へそのまま出さない。
`report_status`は元資料が示す調査・試験・反映の完了状態、`stage_status`と`review.status`はHTML文章レビューの状態として独立に扱う。たとえば`report_status=pending`と`review.status=approved`は両立し、reviewの承認で元資料の完了状態を`complete`へ昇格させない。終端の公開読戻しもこの二つを独立に照合する。レビュー不能の場合は、現行の公開契約と同じくcontroller fallback・pending・`next_action=publish`・専用reviewer ID・unavailable statusを全て確認する。
工程表の対象版欄が参考付録を指す場合、source工程の版とreport response内の全check工程名・版を付録に含める。
## FC09 schema v15のソース・適用確認

現行コードはmigration 014でappend-only operation event表を追加し、migration 015でrepair predecessorがない場合を許すためall-or-none insert triggerだけを削除する。immutable-pin triggerは維持する。隔離DBではschema v14からv15へ更新し、pinなしrepair attemptを作れることを確認する。FC09のproduction適用では固定workspace内の既存DBを公式CLIで移行し、backup、`quick_check`、version・migration記録・両triggerをread-backする。

## FC10 schema v16 failure detail

Migration 016はappend-only operation eventに安全化済み例外reason code連鎖と小さなoperation contextを追加する。contextは件数、ID、SHA-256、相対pathのように原因を特定する値を持てるが、自由文・例外本文・SQL値・秘密・個人情報を保持しない。fallback journal replayでも両fieldを保つ。隔離DBでmigration、nested reason code、contextの保存/replayとredactionを確認し、正式DBはbackupと公式CLI migration/read-backを行う。通常worker/launcher/watchdogの自動migrationは禁止のままとする。

2026-10-20の適用前 read-only確認ではproduction DBはschema v13で`quick_check=ok`、migration 001～013が記録されていた。対象jobは同一job・launch、`source_run_id=null`、status running、resume_count 8だった。この値は移行前観測であり、適用後の結果はFC09実行記録に記録する。FC01のschema13観測やFC03のschema14 source追加時点を現在状態として読み替えない。

### FC09 実適用後の観測

2026-10-20、固定workspaceの正式CLIで保守DBをschema v13からv15へ適用し、SQLite backup、migration 014/015、`quick_check=ok`と適用後のschemaを読み戻した。適用前のschema13/status観測を現在値として使わない。対象jobの以後の状態は、各同一job再開の実測記録を参照する。

### FC10 同一invocationのhandoffと失敗記録

controllerが採番した`invocation_id`をhandoff payloadにも保存し、正式schemaで必須にする。terminal照合は現在のinvocationとpayload内のIDを両方比較し、別invocationの古いhandoffを拒む。AIにはこのcontroller-owned IDを生成させない。deployment結果は登録schema、現在attempt、candidate、repair handoffのhash、release記録の全てを突合し、文字列5項目だけのJSONを反映証明にしない。

failure journalの通常先`maintenance/failure-journal/events.jsonl`への書込またはpath解決に失敗した場合、検証済みDB rootから別に導く`maintenance/failure-journal-secondary/events.jsonl`へ安全化eventを追記する。復旧時は両方を読み、event IDで重複を除いてDBへ冪等に再生する。両sinkの失敗も元のoperationを止めず、具体的な安全化された失敗情報を残す。2026-10-20の同一job実測と未解決理由はFC10実行記録で確認する。
