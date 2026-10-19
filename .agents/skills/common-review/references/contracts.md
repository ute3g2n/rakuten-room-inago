# review stage契約

## 読取対象

利用可能なplan version（review状態を問わない）、

対象jobとreview対象versionまたは成果物があればreviewerを起動する。任意証拠、runtime観測、host receipt、前段statusが欠けても開始する。review対象そのものがない場合だけその必須入力欠落を記録する。reviewerが実際に起動していない状態を、合成した`unavailable` reviewで置き換えない。

## テストコードの独立レビュー方針

レビュアーはテストコード自体を独立レビューしない。計画上の期待と、hostが記録した完全node・実測結果・版・証拠は照合する。テスト実行またはデバッグでテストコードの不具合が判明した場合は、担当者に修正と影響caseの再実行を求め、古いテスト版の結果を流用しない。これはテストコードの独立レビュー工程を追加するものではない。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合があれば開始せずblockedを返す。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 出力

allowed_outputsに列挙されたreview.jsonとreview.mdだけを作る。reviewにはreviewer_id、scope_type/scope_id、reviewed_version、status（approved / changes_requested / rejected / unavailable）、時刻、findingsを含める。findingはseverity、file/lineまたは証拠参照、具体的な問題、plan/case対応、修正要求を記録する。

レビューを正確なresult_versionに紐付ける。実装または試験結果が変われば新しいレビューを要求する。レビュー結果を管理DBへ直接登録しない。controllerがschemaと独立性を検証して記録する。

## 判定

- approved: 計画範囲、プロダクトコード、関連試験の実測結果、禁止副作用、証拠が一致。テストコードの独立レビューは承認条件にしない。
- changes_requested: 修正可能な不足がある。具体的な修正と再確認対象を書く。
- rejected: 重大な不許可副作用、契約迂回、秘密漏えい、証拠改変など。
- unavailable: 根拠不足や対象版不明で判定できない。成功扱いにせず、欠測を記録する。いずれのstatusも後続stageを止めない。

review差戻し回数の上限を設けず、review statusやfindingsを後続stageへのgateにしない。実行不能な個別操作があってもその事実を次担当とreportへ返してworkflowを続ける。「絶対にどうしても必要」な後続gateは基本的に存在しない前提とする。
