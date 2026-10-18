# AT-MAINT-01 保守対応要件・受入契約

正本：要件定義書HTML 2.38 §27.17。対象：MR-001～028 / MA-001～028。これは新しい日次不具合対応基盤の補足受入契約であり、既存IR-001～028・AT-001～028・AT-S/AT-A/AT-TRACE/AT-OBS契約を置換しない。計画ケースC01～C67、LC01～LC06、運用受入OP01～OP04との対応は要件正本と実行計画M §10・§11に従う。

## 共通受入規則

- 受入状態は `PASS / FAIL / 未実施 / 保留` を区別する。未収集・skip・xfail・error・未実施をPASSに数えない。
- 本契約は各STEPで実装・導入する受入条件である。U02の完了は文書の静的追跡・正本再生成確認、U05の完了は隔離した保存層/C06/C07/C28/C53/CLI契約の確認を示す。いずれもアプリ全回帰、モデル設定の実適用、本番起動、実障害の修正が済んだことを意味しない。
- 時刻・日付はJSTを基準にし、対象日、観測日時、受付日時、作成日時、反映日時を別に保持する。保存runがない場合 `source_run_id=null` を許可する。
- 業務状態の正本はmaintenance管理DBとその検証済み成果物である。診断JSONLは証拠参照であり、日次の元結果や管理DBの状態を上書きしない。
- 安全なID・列挙値・範囲内数値・参照だけを診断ログへ出す。prompt、応答本文、商品/投稿本文、URL、query、DOM、認証情報、秘密、個人情報は出力しない。
- 本人の採用済み基本設定を維持する。新しい商品再分析、自動ROOM投稿、過去日分の下書き追生成を追加しない。
- テストコードの独立レビューは行わない。デバッグ中にテストの不具合が判明した場合だけ、該当箇所を最小修正する。レビュー対象は製品コードと仕様である。

## U05管理DB・状態実装の対応

管理DBは`D/maintenance/state.sqlite`（初期SQLite schema v1、U18時点でmigration後のschema v8）、専用設定は`D/maintenance/settings.json`（settings schema v1）とし、日次`D/state.sqlite`とのpath/file aliasを拒否する。table、field、enum、release gate、lease再開契約は[要件データ辞書](要件データ辞書.md)を正本とする。CLIは`python -m automation.inago.maintenance.cli --data-root D [--daily-database PATH] {init,status,probe-ai,enqueue-historical,worker,watchdog,release-gate,start-job,finish-job}`。production DB migration/Scheduler登録はU21 gateまで未実施。

U05でC06/C07/C28/C53の専用受入ケースを隔離DBに対して確認した。step順序は最新の独立承認を要求し、`changes_requested`後は新attemptで再開する。handoffは現在job leaseと一致する場合だけ受理し、受理後のreview更新を拒否する。reviewはrelease単位、および同一job内のstep単位（全attempt通算）で最大3巡。上限後は追加reviewと次attempt開始を拒否して保留する。`finish-job complete`はrelease gateと全step承認を再確認し、active job枠を解放する。failed/blocked/cancelledではrunning attemptも閉じる。再開上限到達時は`blocked`終端化できる。CLIのinit/status/blocked gate/DB alias拒否/finish-job完了を確認した。productionのmanagement DBは初期化・変更しておらず、本番日次の再実行、AI/ブラウザー実接続、production deploymentは未実施。各機能の実装範囲と後続条件は実行計画M §9/§14に記録する。

## U07 parent session・manifest・sandbox契約

U07 migrationはmanagement DBをschema v1からv2へ進め、`job_input_manifest`、`maintenance_ai_session`、`maintenance_ai_observation`を加える。job inputは`incident_id`/`source_launch_id`/nullable `source_run_id`/`target_date`/`job_id`を完全一致で固定する。欠落・不一致・曖昧さ・nullness不一致を推測で補わず、session開始前にblockedとする。manifestとsession設定はimmutable、観測はappend-onlyとする。

親Aのroleはgpt-6.1-sol/xhigh/default/Fast disabled、親Bのroleはgpt-6-luna/max/Fastを指定する。role TOMLのdeveloper instructions、設定digest、model/effort/tier、基本sandbox modeをsessionへ結び、requestedとobservedを分ける。Fastはservice_tier="fast"とfeatures.fast_mode=trueで要求し、protocol上のtier priorityとしてcapabilityを確認する。モデル/effort/tier/FastまたはreadOnly/workspaceWrite modeが未対応ならfallbackせず拒否する。per-folder access/readOnlyAccess/readableRoots/writableRootsがないことは拒否理由にしない。観測イベントに設定値がない場合はconfigured_unverifiedとし、設定した事実だけでverifiedにしない。

調査/review roleはreadOnly mode、親Bの実装/試験roleは専用managed worktree cwdでworkspaceWrite modeを使う。CLIのsandboxにper-folder rootsは設定しない。roleのallowed_outputsは作業指示・返却schemaの所有契約であり、OS/CLIのfolder ACLではない。app-serverへ渡すStructured Outputs schemaは対応subsetへ整形する。固定値はenum等で制約し、objectは全propertyをrequiredにして`additionalProperties:false`を指定する。各turnの返却JSONはhostが元のDraft 2020-12 JSON Schemaで完全検証し、manifestの5項目identityが完全一致することを確認する。server側の構造化出力だけでは完全一致を確定しない。host/controllerは管理DBから実行中step attemptを読み、job/source runとの一致を検証する。出力rootは`attempts/<job_id>/<attempt_id>`、worktree rootは`worktrees/<job_id>`に固定し、別job/attemptのrootを拒否する。設定済みrootからjob/attempt directoryまでの各path componentにreparse point（symlink/junctionを含む）がなく、解決後もroot配下であることを起動計画時・turn前・保存時に再検証する。これはCLIのfolder ACLを設定する要件ではなく、hostがroot外へのpath aliasを拒否するための確認である。allowed_outputsはAIの直接書込権限ではなく、親Bの実装/試験roleはmanaged worktree相対、それ以外は当該attempt output相対の安全なパスだけを受け付ける。Windowsで無効な文字、末尾ドット/空白、予約デバイス名、正規化後に衝突するpathは拒否する。host/controllerは`maintenance-ai-response-v1` envelopeにidentity、attempt_id、role/config digest、allowed_outputs、thread/turn、観測状態、検証済みresponseを束ね、当該attempt output rootへ新規JSONとして原子的に保存し、path/hashを返す。親BのGit commandでは継承された`safe.directory`設定をprocess環境内でリセットし、対象jobの検証済みworktreeだけを追加する。他のGit環境設定は維持し、global/system Git設定ファイルやOS ACLは変更しない。Bはhandoff/release前にworktree diffを宣言済みownerと照合し、範囲外変更を未承認として次へ渡さない。sandbox全面解除や日次T02への権限流用はしない。親セッション実接続のLC01/LC02はU18で一度確認し、U07の隔離試験を実モデル適用済みと数えない。app-server transport JSONLのfield/pathと日次L02との分離は[AT-TRACE-01](AT-TRACE-01_共通トレース契約.md)に定義する。

Windowsのhost-created managed worktreeでは、app-serverのcommand runnerが親プロセスのGit環境変数を子commandへ引き継がない場合がある。Gitが所有者SIDの不一致を報告したときだけ、controller-bound attemptの正確なworktree rootを`git -c safe.directory=<worktree root>`で該当commandに指定する。これはそのGit invocationだけの設定であり、`--global`、wildcard、Git設定ファイル、OS ACLは変更しない。

## U08 daily launcher・最終完了通知

U08は日次taskと`run.ps1 daily`から専用launcherを呼び、maintenance DBへ起動記録をcommitしてから日次CLIを子processとして開始する。実際の子PIDを記録して終了を待ち、CLIがexport・終了保全・共通trace・L01最終記録を含む全処理を返した後にだけ最終通知を確定する。launcher、子process、ownerの終了確認前にmaintenance workerを起動しない。有効で整合する日次結果のexit code 0/4/5（正常/実行・保全等の失敗/処理エラーのない不足）は保ったまま返す。結果JSONがないのにexit 0、またはstatus・終了codeが矛盾する要約は契約失敗exit 4へ正規化する。日次全体の固定期限は設定せず、Schedulerの`ExecutionTimeLimit=PT0S`と個別有限timeoutを維持する。

U08 migration `003_daily_launch.sql`でmaintenance schemaをv3へ進め、`daily_launch`に`source_launch_id`、JST `target_date`、owner/child PID、`running/complete/failed/blocked/unknown` status、nullable `source_run_id`、exit code、完了時刻、安全な結果summaryを保存する。起動前停止はrun IDを創作せず、失敗phase/class、failed check、trace欠測を記録する。正常・不足・未実施の要約は対象日一致、正常/不足はrun IDを必須とし、overall/daily statusはnullにしない。daily_checkとterminal launch resultは同一transactionで確定し、terminal recordは変更/削除できない。結果summaryとstderrにはallowlist済みの識別値・状態・code/classのみを記録し、raw stderr、自由文、秘密、path、下書き数は記録しない。

起動記録を保存できないときは子processを起動しない。有効な結果の完了記録を保存できないときは起動行を`running`のまま残し、通知欠測を出力しながら元の日次exit codeを維持する。launcherは既存production DBを自動migrationしないため、必要なschema v8が事前に導入済みの場合にだけ日次CLIへ進む。U08/U09/U18のtask XML変更は予定差分であり、production DB migration/Scheduler登録と実日次起動はU21の導入ゲート後に行う。

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

## U15 Git統合・反映・切替

releaseはStore上のfrozen状態、必須caseすべての最新PASS、同じ`result_version`への独立approved reviewを照合する。入力されたtested/reviewed versionが保存releaseと違う場合は止める。managed worktreeはrelease ID、source repository、exact commitと外部owner registryを結び、同一ownerのcleanなものだけを再利用する。既存checkoutの未コミット差分はworktreeへコピーしない。

candidate branchへの通常pushはGit履歴を保存するだけであり、本番反映を意味しない。反映ではactive-main tracking targetのremote HEADを読み、保存base commitと一致する場合だけ通常pushを行う。競合、認証/接続失敗、fast-forward不能、対象差分との未コミットpath交差はblockedとし、force push、reset、履歴書換えを行わない。非交差差分はactive checkoutのstatusを前後で照合して維持する。

deployment leaseと`DeploymentExclusionGuard` providerを必須とし、日次owner停止を確認する。pending actionの件数/revisionを照合し、queueを書き換えない。guard/観測が利用できない場合は切替しない。runtime switcherはexact tested commitをactiveにし、active commit照合とhealth確認が揃った場合だけ`deployed`とする。切替後に日次/API状態またはactive checkoutが変わった場合も旧版へ戻し、復旧失敗なら`active_commit=null`でblockedとする。DB/投稿/予約/pending actionを過去backupへ戻さない。U15 local fixtureはcoordinatorのfail-closed/provider contractを確認するもので、実日次/API providerの結線確認はU21に残す。

U15 migration `006_deployment_observations.sql`で管理DBをschema v5からv6へ進め、`deployment`へ`source_commit`、`base_commit`、`tested_commit`、`pushed_commit`、`active_commit`、`remote_name`、`target_branch`、`reason_code`を追加する。`deployed`はsource/tested/pushed/active commitがすべてrelease result commitと一致する場合だけ受理する。旧deployment行の追加欄はnullを保つ。result envelopeは`maintenance-deployment-result-v1`で、schemaは`automation/inago/schemas/maintenance-deployment-result.schema.json`。

U15のC38～C43/C57は一時DB、専用Git fixture/local bare remoteと通常pytestで検証する。検証済みOS sandbox、host-observed review receipt、C34 HostPytestRunner実測は要求しない。外部remote、本番DB、実runtime switch、実日次/APIへの接続は行わない。実runtime providerが未配線の場合はfail-closedとし、local test resultをproduction wiring evidenceと混同しない。実本番の統合・切替はU21で行う。テストコードの独立レビューは行わない。

## U16 incident report生成・レビュー・公開

report stageのresponseは`maintenance-report-response-v1`、最終結果は`maintenance-report-result-v1`、独立レビューは`maintenance-report-review-v1`とする。入力のincident/source launch/source run/target date/job identity、承認済み調査・計画の版、実装commit、試験・反映・次回観測状態、許可済みevidence refsを相互照合する。不一致・未承認・許可外根拠・不正リンクはHTMLを成功成果として扱わずblockedにする。

HTMLは固定テンプレートへ文字列をescapeして描画し、操作・期待・実結果・影響・回復・原因確度・変更後動作・確認範囲、工程tree、試験一覧、未確認・停止条件、根拠リンクを保持する。`complete`は必要な試験・反映・次回観測が全て確認済みの場合だけ使い、それ以外はpending/blockedを示す。report reviewerは作成者と別担当とし、同じresult versionとHTML SHA-256を承認対象に結ぶ。reportだけを別commitで公開し、対象branchの現在HEAD、元コードcommitの祖先関係、report pathの競合、staged/commit対象を確認して通常fast-forward pushする。公開前のコード版変更・review不一致・remote競合は保留する。

U16のC44～C46/C55は合成fixture・一時Git repo/local bare remoteを使う通常のローカルpytestで検証する。検証済みOS sandbox、host-observed review receipt、C34 production HostPytestRunner実測証拠を開発テストの前提・合格条件にしない。テストコードの独立レビューは行わず、デバッグで不具合が判明した場合だけ最小修正する。これらのローカル結果を実運用providerの実証として扱わない。実表示LC04が利用可能なブラウザー経路から確認できない場合は、完成HTMLを用意し未確認として本人の確認へ引き渡す。

## 受入条件

### U14～U20 開発テストの受入条件（2026-10-18 本人決定）
U14以降U20までの開発段階の合否は、合成fixture、一時DB、ローカルGit fixtureを使う通常のローカルpytestで判定する。検証済みOS sandbox、repair HostPytestRunner receipt、C34のrepair runner実測、host-observed review receiptはテスト前提・完了条件にしない。C34相当のRED/PASSやrunner/review境界は通常pytestから合成条件で確認し、独立production code reviewは通常の別担当レビュー記録で確認する。証拠はcommand、JST時刻、case/node、コード/test hash、期待/実結果、exit code、console/JUnitとする。テストコードの独立レビューは行わず、デバッグで不具合が判明した場合だけ最小修正して影響ケースを再実行する。テストはsynthetic fixture・一時DB・local Gitに限定し、production DB、Scheduler、実AI、投稿状態を変更しない。

この緩和はU14～U20の開発テスト証拠要件に適用する。実運用の修復attempt完了では、上記runtime契約に記すsandbox、runner receipt、review receiptの照合を引き続き要求し、provider欠落時は安全停止する。通常pytestの結果やreview記録を実運用receiptの代替として扱わない。本番接続・切替の確認はU21の運用ゲートで別に判断する。

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
| MA-017 | 実装・テスト・レビューの所有を分け、修正前の意味あるREDまたは根拠付きの「再現不可/文書契約違反」を記録する。U14以降U20までの開発テストは通常pytestの直接実行記録で判定し、OS sandbox、host-runner receipt、C34 runner実測、host-observed review receiptを要求しない。独立production code reviewは通常の別担当レビュー記録で確認する。実運用attemptでは上記runtime契約のreceipt要件を適用する。managed worktree外と所有外ファイルを変更しない。 |
| MA-018 | 実運用runnerはOS sandbox内だけで修復対象Pythonを実行し、sandbox未構成ならchild process前に保留する。U14以降U20までの開発テストでは通常pytestを直接実行してcampaign/runner境界の合成fixtureを検証でき、sandbox内positive executionやrepair HostPytestRunner receiptを要求しない。全回帰の対象node/結果/hashを通常pytestの実行記録で確認する。実運用attemptのreceipt規則とcode hash変更後のrelease停止条件は維持する。 |
| MA-019 | 差分対象を限定し、作業commitを追跡先へ通常pushし、レビュー済みの正確な版だけを通常fast-forwardで反映する。非交差の既存ユーザー変更を保持して差分不変を確認し、交差またはff不能時は保留する。force push、reset、履歴書換えを行わない。deploymentにはsource/base/tested/pushed/active commitを別記し、すべての一致とhealth確認後だけdeployedとする。candidate branch pushは本番反映扱いしない。U15のC38～C43/C57は通常pytest/local fixtureで確認し、OS sandbox/host review receipt/C34 host-runner実測は開発テストに要求しない。 |
| MA-020 | 対応HTMLが承認済み入力の版・根拠を照合し、事象、原因確度、修正、試験、レビュー、反映、未完了を平易に説明する。独立した報告レビューは同じversion/hashを確認し、要求された表示確認方法で対象版を確認し、未確認を表示する。 |
| MA-021 | maintenanceの全stage・担当・指定/観測model/effort/tier・入力/証拠/成果参照・base/result版・レビュー・試験・停止/再開・deployment結果が追跡される。 |
| MA-022 | 日次全体とmaintenance全体に固定終了期限を設けない。個別job/request、取消確認、再試行、レビュー巡、再開には有限上限を適用し、次の日次開始時は日次を優先して本番反映を待つ。 |
| MA-023 | 全変更・必須テスト・必要な実接続確認・独立レビュー・追跡先commit・起動設定の一致が機械照合されるまでreleaseをreadyにせず、基盤ready前の遡及受付を拒否する。 |
| MA-024 | 2026-10-17の元記録、件数、欠測、採取日時、元期限、対象日を保全し、観測されていないrun・trace・metricsを創作しない。稼働DBを過去snapshotで置き換えない。 |
| MA-025 | U21 ready後、元のfailed結果を保ったまま新フローが本日案件のcheck・調査・計画・実装・テスト・レビュー・反映・報告を行う。外側の手作業を新フロー実績として扱わない。 |
| MA-026 | 本日調査MD・修正計画MD・対応HTML、各独立レビュー、試験/反映/公開の証拠が同じ案件IDと版で結び、レビュー済み対応HTMLだけを一ファイルのGit commitとして通常fast-forward公開する。 |
| MA-027 | 10/17のfailed/IntegrityError、当日確定0、既存表示12、他の件数とユーザー報告を保持する。再実行・追生成・過去結果のsuccess化で元の記録を置換しない。 |
| MA-028 | 次の自然日次で修正対象の再発、正常日の不要工程省略、結果と報告の一致を観測する。未観測は検証待ち、再発は同一案件へ関連付けて再開し、追加の日次を試験起動しない。 |

## U17 MN04 trace・retention・運用確認

U17は通常のローカルpytestと合成fixtureを使い、MN04 `D/maintenance/logs/<target_date>/<maintenance_id>.jsonl`を含むC18/C47/C48/C58/C60を確認する。検証済みOS sandbox、host-observed review receipt、C34 HostPytestRunner実測証拠は要求しない。これらの開発テスト結果をproduction provider実証へ読み替えない。テストコードの独立レビューは行わず、debugで見つかった不具合だけ最小修正する。

C18はsource expiry/90日期限、4 MiB rotate、日次file quota、90日超過log/evidence copy、既知orphan stage回収を確認する。L02のsource bytes/期限は維持する。C47は保留・review・repair・deploymentを含むworkflow event相関を確認する。C48は日次優先と全体終了期限なしを維持する。C58はwrite/fsync失敗時に既存logを壊さず成功eventを返さないこと、処理結果返却後のreceipt欠測でoperation resultを露出し、状態照合が必要と通知することを確認する。C60はQ/仕様/辞書/追跡/出力一覧/skills/roles/caseの対応を静的に監査する。

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

## U02の確認範囲

U02では本契約と要件対応を静的に確認し、HTML正本を再生成する。アプリテスト・実モデル適用・実Scheduler変更・本番日次・10/17障害の原因調査は対象外。実装と実運用の合否は、それぞれ計画上の後続STEP/LC/OPで証拠を得る。
