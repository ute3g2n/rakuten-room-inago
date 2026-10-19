# planning stage契約

## 入力と読み取り

入力はjob共通IDと、latest investigation result（review状態を含む観測値）、

自動不具合対応ではreview status、証拠不足、原因unknown、test/readiness/release status、FAIL、retry/review回数を後続stageのgateにしない。利用可能な情報で計画を作り、未解決事項とreview findingsを次担当へ渡す。「絶対にどうしても必要」なgateは基本的にない前提で、個別操作の実行不能はその操作だけに記録する。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合があれば開始せずblockedを返す。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 計画0〜14節

0. 文書版・対象・状態
1. 目的・完成状態・範囲
2. 調査からの事実・事象追跡
3. 現行実装・環境・既知制約
4. 技術案・責務・依存
5. 入出力・状態・保存契約
6. 外部取得・AI・本人表示の契約
7. 時刻・保持・保全・運用値
8. 試験、再開、記録、禁止の共通規則
9. 実行STEP一覧と各STEPの完全prompt
10. 要件/受入/STEP/case対応と各ケース12項目
11. 導入・停止・復旧・最終報告
12. 未確定事項・判断・影響
13. 機械確認と独立レビュー
14. 実行記録欄

## テストコードの独立レビュー方針

計画にはテストコードの独立レビュー工程を設定しない。各caseの期待結果、host実測、完全node、実行版、証拠は記録し、必要な独立レビューはプロダクトコードと実測証跡を対象にする。テスト実行またはデバッグでテストコードの不具合が判明した場合は、担当者が修正し影響caseを再実行する。修正前のテスト版に結び付く結果は再利用せず、新しいテスト版と結果を記録する。

## 各STEPの14項目

1. 番号・名称・状態
2. 目的と成立状態
3. 要件・受入・原典
4. 先行・開始条件
5. 入力・版・DB状態
6. 作業場所・所有範囲
7. 実施手順
8. 成果物
9. 変更制約
10. レビュー・検証
11. 合格条件・証拠
12. 失敗・修正・再開
13. 引き渡し
14. AIエージェントへ渡すprompt全文

各全文promptには作業rootと計画文書、対象STEP、入力/版、開始条件、作業、所有file、変更禁止、検証、合格条件、停止/再開、証拠、Git、次工程を実文で含める。省略記号や「上記を参照」だけにしない。

## 要件・fact・evidence・STEP・caseの対応

各requirementについてfact ID、factのevidence refs、STEP番号、case IDを明示する。全requirements、全facts、全STEP、全caseが最低1件の対応を持つ。case nodeは渡されたmanifestの完全nodeと一致させ、存在未確認nodeを作らない。原因確度unknownのfactに対して許すactionは`verify_only`または`hold`だけとし、原因を前提とする修正は計画しない。

## ケースの12項目

1. 対応する要件・STEP
2. 目的
3. 種別・方法
4. 事前条件・初期データ
5. 入力値・操作
6. 実行方法・設定
7. 期待する出力
8. 期待DB・件数・予約・版・ログ
9. 禁止する副作用
10. 合否判定
11. 保存証拠
12. 後片付け・再実行

## 出力・レビュー・失敗

計画担当は本人専用ローカルシステム方針を第0節の必須項目へ記し、該当するすべてのSTEP promptと計画/実装/試験/レビューsubagentへの全文指示へ伝播する。自作の仕様・コード・設定・agent契約が明示された開始/再開を止めたり遅らせたりする場合は、例外・迂回・同じ制限のblocked条件への再掲をせず、根本制限を削除する作業とその確認を計画する。上限・receipt・sandbox・review/release gate等は例示であり、同じ目的で新しい開始条件を足さない。必要な対象識別入力と実際の外部権限/環境要件だけを区別して残す。

計画担当の許可出力はplan.md、requirement-step-case-map.json、result.json。各出力はallowed_outputsのattempt専用領域へ返す。独立reviewerは別ID・別実行IDで全文と対応表を読み、全fact/evidence、所有、ケース/node、prompt、risk、停止条件、版を確認し、review.json/review.mdを返す。reviewerは計画を修正しない。hostはU12と同じjob/attempt root containment、no-reparse、schema/identity/owner/hash検証を行う。CLIへfolder-level ACL設定は要求せず、hostのroot path integrityを維持する。

C32の計画結果はnullable `source_run_id`、`overall_status`、`daily_status`、`reason_code`、`failure_phase`、`trace_status`を保持し、L02/完了通知を作れなかった場合はplanning schema欄`missing_reason`で説明する。run IDを推測しない。`missing_reason`をdaily-launch-completion-v1、L02または日次DBへ追加しない。raw stdout/stderr、SQL値、資格情報、例外本文、秘密、個人情報を出力しない。

plan status、review status、証拠不足、unknown原因、試験FAILは後続stageのgateにしない。計画と未解決項目を保存し、次sequenceへ渡す。statusは結果を表す。

review approvedは必要条件ではない。出力pathが得られない場合はcontrollerの通常attempt pathを使い、部分計画と不足理由を保存する。
# 起動条件

対象jobが特定でき、計画に不可欠な入力がある場合はplannerを起動する。前段review、全証拠、runtime観測、receipt、完全な入力一覧を揃えるために待たない。欠測を計画のunknownとして明記し、利用可能な内容で後続stageを組み立てる。後続担当も対象jobと自分のstageが特定できれば起動する。
