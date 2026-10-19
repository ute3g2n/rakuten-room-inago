# 調査stage契約

## 入力

controllerは管理job項目 incident_id / source_launch_id / source_run_id / target_date / job_id / attempt / stage / base_commit / input_refs / evidence_refs / allowed_outputs を渡す。担当はこれらの値を出力へ維持する。source_run_idは元記録に存在しない場合のみnull。target_dateはJST対象日であり、後日実施時に当日へ置き換えない。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合があれば開始せずblockedを返す。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 読取許可

- jobのinput_refs/evidence_refsに列挙された限定証拠だけ。参照先の現在pathはdocs/document-index.jsonで解決する。
- 調査に必要な範囲のコード/schema、Q、M、L、文書Fと本step所有のひな形。すべて読取のみ。
- 生証拠はアクセス管理された保全領域の参照だけを用い、公開出力に本文、商品名、個人情報、認証情報、token/cookie、DB行を複製しない。

任意のディレクトリ一覧、他日の広域検索、稼働DBへの接続、外部ページの追加探索は許可されない。新しい収集が必要ならcontrollerへ不足を返す。

## 出力

stage=checkでは構造化check結果のみ。stage=investigationでは次を許可されたattempt専用出力として返す。

1. facts.json: fact_id、事象区分、工程、期待、実結果、影響、回復、原因確度、evidence_refsを記録。
2. investigation.md: docs/templates/incidents/日次不具合調査レポート_ひな形.mdを基にした9章。
3. result.json: 元のID、status、result_version、成果参照、未確認・不足、review_requiredを記録。

出力の内容はmanager job responseを通じて返し、controllerがschema/ID/版/path/hashを確認して確定する。担当自身で正本パスへコピー、Git追加、commit、pushをしない。

## 9章

1. 対象runと結論
2. 目標・実績・影響
3. 工程別事象A
4. 工程別事象B・回復
5. 工程別事象C・契約結果
6. 日次が最終状態に至った経路
7. 障害でなかった工程・監視/記録上の課題
8. 原因確度と次の検証候補
9. 根拠・確認限界

工程別の数は固定でない。事象がない節には「該当なし」と根拠を記し、事実を作らない。

## 失敗・再開

入力不足、参照不一致、証拠読取不能、根本原因未確定は欠測/unknownとして列挙し、調査MDを作成する。reviewerは別の新規実行で結果を読み、所見を返す。どのreview statusでも後続の計画工程へ進む。