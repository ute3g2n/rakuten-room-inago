# AT-MAINT-01 保守対応要件・受入契約

正本：要件定義書HTML 2.35 §27.17。対象：MR-001～028 / MA-001～028。これは新しい日次不具合対応基盤の補足受入契約であり、既存IR-001～028・AT-001～028・AT-S/AT-A/AT-TRACE/AT-OBS契約を置換しない。計画ケースC01～C67、LC01～LC06、運用受入OP01～OP04との対応は要件正本と実行計画M §10・§11に従う。

## 共通受入規則

- 受入状態は `PASS / FAIL / 未実施 / 保留` を区別する。未収集・skip・xfail・error・未実施をPASSに数えない。
- 本契約は各STEPで実装・導入する受入条件である。U02の完了は文書の静的追跡・正本再生成確認、U05の完了は隔離した保存層/C06/C07/C28/C53/CLI契約の確認を示す。いずれもアプリ全回帰、モデル設定の実適用、本番起動、実障害の修正が済んだことを意味しない。
- 時刻・日付はJSTを基準にし、対象日、観測日時、受付日時、作成日時、反映日時を別に保持する。保存runがない場合 `source_run_id=null` を許可する。
- 業務状態の正本はmaintenance管理DBとその検証済み成果物である。診断JSONLは証拠参照であり、日次の元結果や管理DBの状態を上書きしない。
- 安全なID・列挙値・範囲内数値・参照だけを診断ログへ出す。prompt、応答本文、商品/投稿本文、URL、query、DOM、認証情報、秘密、個人情報は出力しない。
- 本人の採用済み基本設定を維持する。新しい商品再分析、自動ROOM投稿、過去日分の下書き追生成を追加しない。

## U05管理DB・状態実装の対応

管理DBは`D/maintenance/state.sqlite`（SQLite schema v1）、専用設定は`D/maintenance/settings.json`（settings schema v1）とし、日次`D/state.sqlite`とのpath/file aliasを拒否する。table、field、enum、release gate、lease再開契約は[要件データ辞書](要件データ辞書.md)の「U05 management DB schema v1」を正本とする。CLIは`python -m automation.inago.maintenance.cli --data-root D [--daily-database PATH] {init,status,release-gate,start-job,finish-job}`。

U05でC06/C07/C28/C53の専用受入ケースを隔離DBに対して確認した。step順序は最新の独立承認を要求し、`changes_requested`後は新attemptで再開する。handoffは現在job leaseと一致する場合だけ受理し、受理後のreview更新を拒否する。reviewはrelease単位、および同一job内のstep単位（全attempt通算）で最大3巡。上限後は追加reviewと次attempt開始を拒否して保留する。`finish-job complete`はrelease gateと全step承認を再確認し、active job枠を解放する。failed/blocked/cancelledではrunning attemptも閉じる。再開上限到達時は`blocked`終端化できる。CLIのinit/status/blocked gate/DB alias拒否/finish-job完了を確認した。productionのmanagement DBは初期化・変更しておらず、本番日次の再実行、AI/ブラウザー実接続、production deploymentは未実施。各機能の実装範囲と後続条件は実行計画M §9/§14に記録する。

## U07 parent session・manifest・sandbox契約

U07 migrationはmanagement DBをschema v1からv2へ進め、`job_input_manifest`、`maintenance_ai_session`、`maintenance_ai_observation`を加える。job inputは`incident_id`/`source_launch_id`/nullable `source_run_id`/`target_date`/`job_id`を完全一致で固定する。欠落・不一致・曖昧さ・nullness不一致を推測で補わず、session開始前にblockedとする。manifestとsession設定はimmutable、観測はappend-onlyとする。

親Aのroleはgpt-6.1-sol/xhigh/default/Fast disabled、親Bのroleはgpt-6-luna/max/Fastを指定する。role TOMLのdeveloper instructions、設定digest、model/effort/tier、基本sandbox modeをsessionへ結び、requestedとobservedを分ける。Fastはservice_tier="fast"とfeatures.fast_mode=trueで要求し、protocol上のtier priorityとしてcapabilityを確認する。モデル/effort/tier/FastまたはreadOnly/workspaceWrite modeが未対応ならfallbackせず拒否する。per-folder access/readOnlyAccess/readableRoots/writableRootsがないことは拒否理由にしない。観測イベントに設定値がない場合はconfigured_unverifiedとし、設定した事実だけでverifiedにしない。

調査/review roleはreadOnly mode、親Bの実装/試験roleは専用managed worktree cwdでworkspaceWrite modeを使う。CLIのsandboxにper-folder rootsは設定しない。roleのallowed_outputsは作業指示・返却schemaの所有契約であり、OS/CLIのfolder ACLではない。各turnはDraft 2020-12 JSON Schemaで返却JSONを検証し、manifestの5項目identityが完全一致することを確認する。host/controllerは管理DBから実行中step attemptを読み、job/source runとの一致を検証する。出力rootは`attempts/<job_id>/<attempt_id>`、worktree rootは`worktrees/<job_id>`に固定し、別job/attemptのrootを拒否する。設定済みrootからjob/attempt directoryまでの各path componentにreparse point（symlink/junctionを含む）がなく、解決後もroot配下であることを起動計画時・turn前・保存時に再検証する。これはCLIのfolder ACLを設定する要件ではなく、hostがroot外へのpath aliasを拒否するための確認である。allowed_outputsはAIの直接書込権限ではなく、親Bの実装/試験roleはmanaged worktree相対、それ以外は当該attempt output相対の安全なパスだけを受け付ける。Windowsで無効な文字、末尾ドット/空白、予約デバイス名、正規化後に衝突するpathは拒否する。host/controllerは`maintenance-ai-response-v1` envelopeにidentity、attempt_id、role/config digest、allowed_outputs、thread/turn、観測状態、検証済みresponseを束ね、当該attempt output rootへ新規JSONとして原子的に保存し、path/hashを返す。Bはhandoff/release前にworktree diffを宣言済みownerと照合し、範囲外変更を未承認として次へ渡さない。sandbox全面解除や日次T02への権限流用はしない。親セッション実接続のLC01/LC02はU18で一度確認し、U07の隔離試験を実モデル適用済みと数えない。app-server transport JSONLのfield/pathと日次L02との分離は[AT-TRACE-01](AT-TRACE-01_共通トレース契約.md)に定義する。

## U08 daily launcher・最終完了通知

U08は日次taskと`run.ps1 daily`から専用launcherを呼び、maintenance DBへ起動記録をcommitしてから日次CLIを子processとして開始する。実際の子PIDを記録して終了を待ち、CLIがexport・終了保全・共通trace・L01最終記録を含む全処理を返した後にだけ最終通知を確定する。launcher、子process、ownerの終了確認前にmaintenance workerを起動しない。有効で整合する日次結果のexit code 0/4/5（正常/実行・保全等の失敗/処理エラーのない不足）は保ったまま返す。結果JSONがないのにexit 0、またはstatus・終了codeが矛盾する要約は契約失敗exit 4へ正規化する。日次全体の固定期限は設定せず、Schedulerの`ExecutionTimeLimit=PT0S`と個別有限timeoutを維持する。

U08 migration `003_daily_launch.sql`でmaintenance schemaをv3へ進め、`daily_launch`に`source_launch_id`、JST `target_date`、owner/child PID、`running/complete/failed/blocked/unknown` status、nullable `source_run_id`、exit code、完了時刻、安全な結果summaryを保存する。起動前停止はrun IDを創作せず、失敗phase/class、failed check、trace欠測を記録する。正常・不足・未実施の要約は対象日一致、正常/不足はrun IDを必須とし、overall/daily statusはnullにしない。daily_checkとterminal launch resultは同一transactionで確定し、terminal recordは変更/削除できない。結果summaryとstderrにはallowlist済みの識別値・状態・code/classのみを記録し、raw stderr、自由文、秘密、path、下書き数は記録しない。

起動記録を保存できないときは子processを起動しない。有効な結果の完了記録を保存できないときは起動行を`running`のまま残し、通知欠測を出力しながら元の日次exit codeを維持する。launcherは既存production DBを自動migrationしないため、schema v4が事前に導入済みの場合にだけ日次CLIへ進む。U08/U09のtask XML変更は予定差分であり、Scheduler登録と実日次起動はU21の導入ゲート後に行う。

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

OS exit code 0だけで成功と判定しない。完了通知・日次status・exit code・phase・件数が矛盾すれば `contract_violation`。既知外部stop code `authentication_required`、`shared_deadline_exhausted`、`source_cancelled`、`source_timeout`、`transport_error` は失敗時に `external_wait` として切り分ける。証拠や完了通知が不足して判定できない場合は `unknown` とする。候補枯渇による不足は日次理由 `candidate_or_theme_exhausted` とstatusが一致するときだけ正常不足にする。

C20/C21/C22とC55のdetector部分はU11専用fixtureで確認する。C23の同日重複・再発抑止はU09の完了証拠を再利用し、U11では再実行しない。C19のcheck-stage構造と隔離Store記録はU12の合成fixtureで検証し、実Sol/xhigh app-server接続はU18のLC01/LC02で確認する。C55のreport表示受入はU16で検証する。ライブincident・production DB・認証・投稿は変更しない。

## U12 check・調査出力・独立review・handoff

U12の構造化stage契約は `automation/inago/maintenance/investigation.py` と同ディレクトリのresponse schemasを正本とする。U11の事前分類結果をcheck stageへ渡し、`maintenance-check-response-v1`のschema、manifest identity、許可済みevidence refs、assessment/reason_codeの組合せを確認する。`unknown`と`contract_violation`はAIを呼ばずblockedにし、`external_wait`はAIのnormal回答で解除しない。正常checkは`daily_check`へ最小statusを記録し、incident/jobを作らない。要調査はinvestigationへ進み、外部待ちは`incomplete`、不明は`unknown`として記録する。これはU11の`maintenance-check-result-v1`純粋判定出力とは別のstage responseである。

調査responseは `maintenance-investigation-response-v1` とし、schema_version、対象日、incident/source launch/source run/job/attempt、base commit、input/evidence refsを省略せず一致させる。factの日付を対象日に固定し、各factのevidence refsをmanifestの許可範囲に限定する。cause_confidenceがunknownなら原因文を許さず、裏付けのない原因断定を拒否する。reportは9章を固定し、factが3件を超えてもすべての操作・期待・実結果・影響・回復・原因確度・根拠を記載する。

controllerは検証済みfactsを`facts.json`、9章報告を`investigation.md`、版・identity・base commit・input/evidence refs・artifact hash・review状態を`result.json`として、現在のrunning investigation attempt専用の`attempts/<job_id>/<attempt_id>/`へ新規保存する。既存ファイルを上書きしない。POSIXではroot descriptorから各path componentを`O_NOFOLLOW`付きで開き、Windowsでは親directory handleに相対する`NtCreateFile`を`OBJ_DONT_REPARSE`付きで実行し、開いたhandleのfinal pathも照合する。各file handleがregular fileであることを確認し、属性/fstat検証に失敗したときは、取得済みnative handleまたはCRT descriptorを閉じる。保存先rootとjob/attempt identityをhost側で検証する。path integrity確認はhost側の検査であり、CLIのfolder-level ACLは要求しない。調査statusがblockedでも3成果物を保存し、handoffは行わない。

reviewは`maintenance-investigation-review-v1`でreviewer ID、review_execution_id、reviewed_versionをresponseへ結ぶ。調査者とreviewerの担当IDと実行IDがそれぞれ異なること、同じresult_versionへのreviewであることを確認するreview roundはStoreの既存review件数から導出し、呼出側の値と一致しなければreviewer起動前に拒否する。同じreviewer execution IDを別roundで再利用する要求も、過去のhash検証済みresultから照合し、reviewer起動・成果物作成前に拒否する。review後かつ`result.json`保存前にleaseを再確認し、Storeは完了・review記録を1トランザクションで保存するときにもleaseとreview roundを照合する。handoffは調査statusがcomplete、review statusがapproved、版と全artifact hashが一致するときだけStoreへ渡す。handoff失敗時は現在の最新attempt・独立承認・版・hash・leaseをStore transaction内で再検証してから再開し、検証失敗ではattempt/job状態を変更しない。既にacceptedのhandoff再試行は同一結果を返し、二重登録しない。作成済みartifactのwrite/flush/fsyncが失敗した場合は同じno-reparse root-relative方式でpartial fileを除去し、除去失敗は元のwrite errorへexception noteとして付記する。次STEPは`handoff_accepted`後だけ進める。changes_requestedは同一stepの新attemptで再試行し、review上限は3巡。U12のC19/C29/C30は合成fixtureで構造・保存・Store連携を確認する。実Sol/xhigh app-server接続、runtime設定の実観測、production DB/Scheduler接続はU18/U21の後続条件であり、実接続済みとは記録しない。

## U13 修正計画・独立review・run-null logging contract

U13は`automation/inago/maintenance/planning.py`、`maintenance-planning-response-v1`、`maintenance-planning-review-v1`を正本とする。計画開始時にjob manifest、release base commit、最新のapprovedかつhandoff acceptedなU12 investigation result/facts artifactを読み、5項目identity、調査attempt ID、result version、base commit、input/evidence refs、result/facts SHA-256を照合する。U12 result/factsの実使用evidence refsは相互一致し、manifestで許可されたrefsの部分集合でなければならない。各factのrefsは実使用refsの範囲内に置く。呼出側が調査版や参照を差し替えた場合は、計画artifactを作る前に拒否する。

計画responseは0～14節を番号順に持ち、各STEPは14項目と単独で実行できる全文promptを持つ。各caseは12項目、実在するmanifest nodeを持つ。対応表は各factのevidence refsから要件、STEP、caseまでを相互追跡し、全要件・fact・STEP・caseの対応漏れを拒否する。原因確度がunknownのfactは確認のみまたは保留とし、原因を前提にした修正を許可しない。成果物`plan.md`、`requirement-step-case-map.json`、reviewerの`review.json`/`review.md`、hostの`result.json`は各stage attempt専用rootに新規保存し、U12同様にpath integrity/schema/identity/owner/hashを検証する。CLIでfolder-level ACLは要求しない。

C32の計画追加ログ契約は、nullable `source_run_id`、`overall_status`、`daily_status`、safe `reason_code`、`failure_phase`、`trace_status`と、L02/完了通知を作れない理由を示す`missing_reason`を計画結果へ記録する。許容値は`source_run_not_saved`、`trace_not_written`、`trace_unavailable`、`trace_degraded`、`none`。`source_run_id=null`なら`source_run_not_saved`を選び、それ以外はtrace状態が`not_written`/`unavailable`/`degraded`なら対応する値、`healthy`なら`none`とする。各値は保存済みdaily completionと照合し、7項目全てをschema/source/C32 nodeのbindingへ結ぶ。`missing_reason`はplanning response schema/source/testのみに追加し、既存`daily-launch-completion-v1`やL02へ新欄を黙って追加しない。run IDがnullならnullを維持し、架空IDを生成しない。raw SQL、認証情報、stdout/stderr、例外本文、秘密、個人情報は計画ログへ複写しない。

plannerとreviewerは担当ID・実行IDを分け、review対象versionは同一でなければならない。review入力にはplan/map、approved U12 facts artifactの検証済み内容/path/SHA-256、daily completion summary/SHA-256を含め、reviewerが事実対応とC32値を照合できるようにする。hostは`work_root`をrequest内の一致値だけで信頼せず、controllerを含む実行checkoutからrootを決め、plan documentがroot内へresolveすることを検証する。この検査はpath integrityであり、CLI folder-level ACLの設定要求ではない。外部待ち・blocked計画も独立reviewと最終説明を保存する。reviewerが計画の完全性をapprovedとしても、plan statusがreadyでなければStore gateを`changes_requested`にして後続STEPを止め、同じsequenceで再開する。C31/C32は合成fixtureと隔離Storeだけを使い、production DB/Scheduler/AI接続や実障害への対応は行わない。

## U14 repair・テスト台帳・独立review

U14 sequence 3開始時に、Store transactionで承認済み・readyなU13 predecessorのattempt ID、result version、output SHA-256をimmutable pinし、その正確な計画だけを完了まで使う。後から新しいU13 attemptが作られても差し替えない。U13 responseの`repair_scope.owned_paths`は必須の正規化済みrepo-relative path集合であり、承認resultとhash-bound `repair-scope.json`へ保存する。repair requestには`host_*`自己申告欄を設けない。controllerはStore attempt、job manifest、base commit、pinしたU13 result/artifact hash、identity、version、reviewer、ready/handoffを照合する。resolver未設定、scope/path/schema/hash不一致では完了を拒否する。AI起動前のCLI folder-level ACLは要求しない。実checkoutのpath containment、reparse拒否、owner/identity/schema/hash検証とdiff reviewはhost側で維持する。

標準`RepositoryRepairEvidenceResolver`は実行checkoutからrootを取り、実際のtracked・staged・untracked・削除/rename diffとapproved U13 scopeを照合する。変更pathが承認scope外、root外、symlink/reparse経由なら拒否する。baseline/candidate assertion snapshotと決定的なcurrent code SHA-256を取り、`RepairHostEvidence`を作る。test outcomeを直接書くpersistent ledger APIは拒否し、`HostPytestRunner`が全登録case/nodeを固定したfinal full-regression snapshotで実行してStoreへ保存したreceiptだけを消費する。C34 REDは期待assertion failure 1件、error/collection error 0件の単独実測で、修正後PASSも同じsemantic case/nodeのhost receiptへ結ぶ。full regression receiptはcampaignの全登録case/node集合（個別PASS済み/frozenを含む）、token ID、code SHA、exit/collected/error/skip/collection error、parent状態に結び、一度だけ消費する。completionは今回run receiptが全registered case/nodeのPASSを含む場合だけ許可し、過去receiptとのunionで未実行caseを補わない。code hash変更時はcase定義だけをnew campaignへ継承し、旧PASS receiptを無効化して全登録caseを新hashで再実行する。arbitrary PASS map、nested run、集合変更、PASS再選択、実行中hash driftを拒否する。

`maintenance-repair-test-ledger-v1`はcampaign identity、semantic case ID/node/signature、凍結case、receipt-bound結果、full regression receipt metadata、approved caseから得たC34 evidenceを持つ。schema v5の`repair_campaign`、`repair_campaign_case`、`repair_campaign_result`、`repair_case_run_start`、`repair_run_receipt`へone-use stateを永続化し、Store再open後もcase freeze・PASS history・token/snapshot/receiptを復元する。run owner/child PIDとprocess start identity、expiryを記録し、expired claimはowner/child終了をhost確認した場合だけfailedへ閉じる。case-run失敗もdurable failed stateへ閉じ、started recordを残さない。semantic signatureは初期・追加caseとも必須かつ一意。PASS済みcaseのID/node/signature再登録は拒否する。code hash変更後のforkはcase定義だけを保持し、旧PASS/frozen receiptを新hashの成功根拠にせず全登録caseを新hashで再実行する。final full regressionは個別PASS/frozenを含む全登録caseを再実行し、そのrun receiptで全件PASSを確認する。frozen ID/node/signatureを再登録、full regression開始後のcase追加、receiptの改変/再生を拒否する。full regressionはcampaignごとに一度だけ開始・claim・記録できる。修復対象のPython code/testはhost検証済みOS sandbox内だけで実行し、書込範囲・network・environment・process treeを制御する。強制可能なsandbox providerがない場合はchild process起動前に保留し、拒否を実行成功/PASSとして扱わない。安全な拒否のunit testとOS sandbox内でのpositive execution evidenceを区別する。この要件はAI起動前のCLI folder-level ACLとは別であり、CLI ACLを要求しない。

repair artifactはattempt専用rootに`patch-assessment.json`、`test-ledger.json`、`review.json`、`result.json`として新規保存する。各resultはidentity、attempt/result version、tested code SHA、reviewer、path/SHAを結ぶ。独立reviewはStoreに記録されたhost-observed read-only reviewer session/turnとapproved/findingsを含むsource rowを参照し、reviewer identityとattempt/result version/code SHA/patch-test artifact SHA/review output SHAを照合した一回消費receiptで承認する。検証できないtested/reviewed commit claimは保存しない。review resolver返却直後とStore完了直前に実checkoutを再hashし、review中のcode driftを拒否する。callerが渡すreviewer ID/status/receipt IDだけでは承認しない。同一payloadの発行は冪等、異なるpayloadの競合は拒否。実review receipt provider未構成なら完了を保留する。raw patch、console、AI応答、秘密、個人情報をtraceへ複写しない。過去のC34外側pytest PASSは修復runnerのRED/PASS実測receiptへ読み替えず、承認済みU13 case mapに対象caseがない場合は完了を保留する。C33～C37/C61～C67の合否と専用証拠はM §10/§14、private Tへ記録する。C62～C67はapproved scope、predecessor pin、Store restart、default resolver、receipt消費、runner境界を個別に確認する。production DB、Scheduler、実AI、投稿状態は変更しない。

## 受入条件

| ID | 合格条件 |
|---|---|
| MA-001 | 基準一覧と移行結果が文書ID単位で対応し、同じ文書の重複移動・欠落・未分類・移動先衝突が0。既存3件のGit除外ファイルもローカル実在文書として扱う。 |
| MA-002 | 管理対象の現行参照が索引の新パスへ解決し、通常リンク切れ0。履歴として残す旧パスは過去の位置と現在の入口を区別して説明する。 |
| MA-003 | HTML内部正本、生成ソース、同梱資料、図、辞書、追跡表、受入条件が同じ版に対応し、正本HTMLの再生成に成功する。旧Markdownのimportで新しい正本を巻き戻さない。 |
| MA-004 | 本人が採用済みの基本設定を保持し、未採用の詳細候補・比較値は候補のまま表示する。文書移動や要件同期だけで自動採用されない。 |
| MA-005 | 日次CLI最終結果・後処理・保全完了後に起動通知を確定する。起動前停止でrunがない場合、未起動、通知欠落、途中終了を識別し、存在しないrun IDを作らない。 |
| MA-006 | 正常日と根拠のある正常な不足は最小のcheck記録だけを確定し、原因調査・修正・報告の別案件を起動しない。 |
| MA-007 | 既知の不具合、外部要因、正常な結果、情報不足/未知を別状態に分類する。確認済み事実、推定、未確認と根拠参照が分離される。 |
| MA-008 | 同一起動のcheckを一意にし、重複incidentと修正jobを作らない。lease、worker再起動、再開、再発時の未解決案件への関連付けが維持される。 |
| MA-009 | check・調査・修正計画は親A `gpt-6.1-sol / xhigh / normal`で実行し、調査・計画の各レビューを作成者と異なる担当が行う。指定や観測が得られない場合は未確認で停止する。 |
| MA-010 | 実装・テスト・コードレビュー・報告は親B `gpt-6-luna / max / Fast`の下で別担当が行う。Fastは親Bの `service_tier=fast` と使用版の `features.fast_mode=true` の実適用を観測して確認する。 |
| MA-011 | 2親、6スキル、9担当の役割・モデル・考える量・入力・論理的な出力所有が一致し、reviewerはreadOnly modeとなる。出力所有はCLIのfolder ACLではなく、role契約とhost側のschema/owner/diff検証で確認する。日次T02の権限を流用しない。 |
| MA-012 | management DB、ID、版、状態、例外、出力所有、基本sandbox modeが実装schemaに対応し、run nullを含む欠測を正しく表す。AI出力はschema/identity/ownerを検証してから確定する。 |
| MA-013 | 既存Lの26分類は許可一覧に基づいて収集し、元のID・時刻・期限・相関・取得不能/失効理由を保持する。新しいmaintenance出力を既存日次traceへ混在させない。 |
| MA-014 | 元の日次結果と主例外を維持し、maintenanceログ・証拠コピーの期限、容量、回収、抑止/書込不能を明示する。元データの期限を延長・短縮せず、保持失敗を成功扱いしない。 |
| MA-015 | 調査レポートが計画で定める9章を備え、事実/推定/未確認と根拠を追跡でき、別担当のレビューを通る。原因をerror名だけで断定しない。 |
| MA-016 | 修正計画が0～14節、STEPごとの14項目、ケースごとの12項目、各STEPの独立promptを備え、STEP・ケース・受入・出力が相互追跡できる。 |
| MA-017 | 実装・テスト・レビューの所有が分かれ、意味のある再現REDまたは証拠に基づく「再現不可/文書契約違反」の記録が修正前に残る。C34はapproved case/nodeの実runner receiptで確認し、合成fixtureのcaller-made証拠を認めない。独立reviewはattempt/version/code/patch/test/output hashとhost-observed read-only session/turnに結び付くreceiptを消費する。同一payloadの再試行は冪等とし、provider欠落は保留する。managed worktree外と所有外ファイルを変更しない。 |
| MA-018 | U19で凍結した全必須対象を途中は新規/未実施/FAILだけ実行し、U20の全回帰は1回。修復対象PythonのrunnerはOS sandbox内でのみ実行し、sandbox未構成ならchild process前に保留し、安全な拒否だけをrunner成功としない。full regressionは個別PASS/frozenを含む全approved case/nodeを一度実行し、今回のrun receiptだけで全件PASSを示す。code hash変更時は旧PASS receiptを無効化し、case定義だけを保持して全登録caseを新hashで再実行する。回帰後は影響が不確実な既存PASSを流用せず、不成立ならreleaseを止める。 |
| MA-019 | 差分対象を限定し、作業commitを追跡先へ通常pushし、レビュー済みの正確な版だけを通常fast-forwardで反映する。非交差の既存ユーザー変更を保持して差分不変を確認し、交差またはff不能時は保留する。force push、reset、履歴書換えを行わない。 |
| MA-020 | 対応HTMLが事象、原因確度、修正、試験、レビュー、反映、未完了を平易に説明し、独立した報告レビューを通る。要求された表示確認方法で対象版を確認し、未確認を表示する。 |
| MA-021 | maintenanceの全stage・担当・指定/観測model/effort/tier・入力/証拠/成果参照・base/result版・レビュー・試験・停止/再開・deployment結果が追跡される。 |
| MA-022 | 日次全体とmaintenance全体に固定終了期限を設けない。個別job/request、取消確認、再試行、レビュー巡、再開には有限上限を適用し、次の日次開始時は日次を優先して本番反映を待つ。 |
| MA-023 | 全変更・必須テスト・必要な実接続確認・独立レビュー・追跡先commit・起動設定の一致が機械照合されるまでreleaseをreadyにせず、基盤ready前の遡及受付を拒否する。 |
| MA-024 | 2026-10-17の元記録、件数、欠測、採取日時、元期限、対象日を保全し、観測されていないrun・trace・metricsを創作しない。稼働DBを過去snapshotで置き換えない。 |
| MA-025 | U21 ready後、元のfailed結果を保ったまま新フローが本日案件のcheck・調査・計画・実装・テスト・レビュー・反映・報告を行う。外側の手作業を新フロー実績として扱わない。 |
| MA-026 | 本日調査MD・修正計画MD・対応HTML、各独立レビュー、試験/反映/公開の証拠が同じ案件IDと版で結び、所有対象だけをGit公開する。 |
| MA-027 | 10/17のfailed/IntegrityError、当日確定0、既存表示12、他の件数とユーザー報告を保持する。再実行・追生成・過去結果のsuccess化で元の記録を置換しない。 |
| MA-028 | 次の自然日次で修正対象の再発、正常日の不要工程省略、結果と報告の一致を観測する。未観測は検証待ち、再発は同一案件へ関連付けて再開し、追加の日次を試験起動しない。 |

## U02の確認範囲

U02では本契約と要件対応を静的に確認し、HTML正本を再生成する。アプリテスト・実モデル適用・実Scheduler変更・本番日次・10/17障害の原因調査は対象外。実装と実運用の合否は、それぞれ計画上の後続STEP/LC/OPで証拠を得る。
