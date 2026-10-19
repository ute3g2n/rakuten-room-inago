# implementation stage契約

自動不具合対応ではreview/test/readiness/release status、証拠不足、原因unknown、FAIL、retry/review回数を後続stageのgateにしない。利用可能な計画と入力で実装を試み、実装できない事項も理由付きで記録して次工程へ渡す。「絶対にどうしても必要」なgateは基本的にない前提とする。

対象jobと担当stageが特定できればimplementerを起動する。任意証拠、runtime観測、receipt、設定一致や全入力の充足を開始条件にしない。必要な実装入力が欠ける部分だけをunknownとして記録し、可能な変更を続ける。

## 入力

job/incident/attempt identity、利用可能なplan_version（review結果は記録値）、

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合があれば開始せずblockedを返す。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 読取と書込

- 読取: 利用可能な計画とreview所見、
- 書込: allowed_outputsが列挙した実装対象ファイルだけ。tests/fixtures、agent skill/TOML、ユーザー変更、稼働データは所有外。
- 出力: diff summary、変更file一覧、要件/STEP/case対応、実行した確認、未確認、差分patch参照をresult.jsonに返す。

利用可能な成果物で作業を開始し、変更できないfileや基準があればその範囲を記録して他の実装を進める。controllerが作業用管理worktreeを用意し、

## 禁止事項

本番DB・証拠原本・credentials・設定、投稿・Scheduler・task登録、release gateは変更禁止。testsを編集/実行しない。独自の大規模整形、範囲外リファクタ、削除、reset、checkoutで他者差分を戻す、force push、commit/push/deployは禁止。

## 引継ぎ・再開

実装結果をtesting担当へ渡し、review approvalやtest statusを後続stageの条件にしない。
