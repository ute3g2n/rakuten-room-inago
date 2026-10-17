# AT-MAINT-01 保守対応要件・受入契約

正本：要件定義書HTML 2.22 §27.17。対象：MR-001～028 / MA-001～028。これは新しい日次不具合対応基盤の補足受入契約であり、既存IR-001～028・AT-001～028・AT-S/AT-A/AT-TRACE/AT-OBS契約を置換しない。計画ケースC01～C60、LC01～LC06、運用受入OP01～OP04との対応は要件正本と実行計画M §10・§11に従う。

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

親Aのroleは`gpt-6.1-sol`/`xhigh`/`default`/Fast disabled、親Bのroleは`gpt-6-luna`/`max`/Fastを指定する。role TOMLのdeveloper instructions、設定digest、model/effort/tier、sandbox境界をsessionへ結び、requestedとobservedを分ける。Fastは`service_tier="fast"`と`features.fast_mode=true`で要求し、protocol上のtier `priority`として capability を確認する。モデル/effort/tier/Fastまたはrestricted sandboxが未対応ならfallbackせず拒否する。観測イベントに設定値がない場合は`configured_unverified`とし、設定した事実だけで`verified`にしない。

調査roleはattempt outputだけ、親Bの実装/試験roleはmanaged worktreeだけをwrite rootとする。reviewerはread-only。restricted read/write rootsをapp-serverが強制できない場合はprocess起動前にblockedとする。親セッション実接続のLC01/LC02はU18で一度確認し、U07の設定・隔離試験を実モデル適用済みと数えない。app-server transport JSONLのfield/pathと日次L02との分離は[AT-TRACE-01](AT-TRACE-01_共通トレース契約.md)に定義する。

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
| MA-011 | 2親、6スキル、9担当の役割・モデル・考える量・入力・出力・書込範囲が一致し、レビュー担当は対象版に対する読み取り専用となる。日次T02の権限を流用しない。 |
| MA-012 | management DB、ID、版、状態、例外、出力所有、隔離範囲が実装schemaに対応し、run nullを含む欠測を正しく表す。AI出力はschema/identity/ownerを検証してから確定する。 |
| MA-013 | 既存Lの26分類は許可一覧に基づいて収集し、元のID・時刻・期限・相関・取得不能/失効理由を保持する。新しいmaintenance出力を既存日次traceへ混在させない。 |
| MA-014 | 元の日次結果と主例外を維持し、maintenanceログ・証拠コピーの期限、容量、回収、抑止/書込不能を明示する。元データの期限を延長・短縮せず、保持失敗を成功扱いしない。 |
| MA-015 | 調査レポートが計画で定める9章を備え、事実/推定/未確認と根拠を追跡でき、別担当のレビューを通る。原因をerror名だけで断定しない。 |
| MA-016 | 修正計画が0～14節、STEPごとの14項目、ケースごとの12項目、各STEPの独立promptを備え、STEP・ケース・受入・出力が相互追跡できる。 |
| MA-017 | 実装・テスト・レビューの所有が分かれ、意味のある再現REDまたは証拠に基づく「再現不可/文書契約違反」の記録が修正前に残る。managed worktree外と所有外ファイルを変更しない。 |
| MA-018 | U19で凍結した全必須対象を途中は新規/未実施/FAILだけ実行し、U20の全回帰は1回。回帰後は影響が不確実な既存PASSを流用せず、不成立ならreleaseを止める。 |
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
