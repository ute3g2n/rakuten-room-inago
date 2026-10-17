# review stage契約

## 読取対象

approved plan version、implementerのプロダクトコード差分/変更file一覧、testerの完全node・実行証拠・status、schema、計画で指定した安全制約。reviewer自身の成果は対象に含めない。親Bのレビューは読取専用。テストコードの独立レビューは対象外とする。

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
- unavailable: 根拠不足や対象版不明で判定できない。成功扱いにしない。

同じjob/step/sequenceのreview差戻しはattemptをまたいで通算し、3件を超えて新reviewを作らない。第三者確認が必要ならcontrollerへblockedを返す。