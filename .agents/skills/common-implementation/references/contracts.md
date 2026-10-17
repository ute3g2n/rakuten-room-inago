# implementation stage契約

## 入力

job/incident/attempt identity、review approved plan_version、target commit、明示されたfile list、managed worktree、allowed_outputs、parent-B model/effort/tier設定、未変更ユーザー差分基準を必要とする。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合があれば開始せずblockedを返す。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 読取と書込

- 読取: 承認済み計画、依頼で列挙した対象コード、依存するschema、実装専用case/acceptance、Git base/worktree状態。
- 書込: allowed_outputsが列挙した実装対象ファイルだけ。tests/fixtures、agent skill/TOML、ユーザー変更、稼働データは所有外。
- 出力: diff summary、変更file一覧、要件/STEP/case対応、実行した確認、未確認、差分patch参照をresult.jsonに返す。

controllerが作業用管理worktreeを用意し、file allow-listとbase_commitを固定する。空白・未定義のallow-list、dirty user-owned file、基準commit不一致なら編集せずblocked。

## 禁止事項

本番DB・証拠原本・credentials・設定、投稿・Scheduler・task登録、release gateは変更禁止。testsを編集/実行しない。独自の大規模整形、範囲外リファクタ、削除、reset、checkoutで他者差分を戻す、force push、commit/push/deployは禁止。

## 引継ぎ・再開

実装結果をtesting担当へ渡す。再開は同一job/step/sequenceと現在の管理worktreeを保持する。追加fileが必要なときはparent/controllerがplanとallowed_outputsを改訂し、新result_versionを作る。reviewerが承認するまでは修正が受入済みとは扱わない。