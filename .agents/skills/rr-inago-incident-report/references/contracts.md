# report stage契約

## 入力

job identity、target_date、investigation approved version、plan approved version、実装commit/version、各test/review結果、deployment result、evidence_refs、allowed_outputs。作成時刻は実時刻JST、障害発生日は対象日とする。入力のreport statusと本文の状態を混同しない。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合があれば開始せずblockedを返す。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 読取と書込

- 読取: 対象jobに紐付くapproved資料、plan、C/LC実測、レビュー、反映・復旧結果、許可証拠参照、報告ルールG、既存HTML構造H。
- 書込: allowed_outputs内のincident HTMLおよび構造化result.jsonのみ。報告担当はworktreeを直接commit/pushせず、controllerへ検証可能な出力を返す。
- 外部向けHTMLは公開可能な要約だけとし、ログ/DB/商品/投稿/個人/secretの原文を含めない。

## 内容確認

- 案件・対象日・作成時刻・実行版を区別する。
- 事象一覧と詳細で区分（当日発生 / 調査で判明 / 試験で確認）、工程、操作、期待、実結果、影響、回復、原因の確度、変更後動作、試験範囲、根拠リンクを維持する。
- 調査、作業場所での検証、release gate、反映、次回日次観測は個別の状態として表す。未実施は未実施、skip/errorはPASSでない。
- HTMLは意味的見出し、症状の一覧と詳細、索引、相対リンク、狭幅/印刷向けCSS、pre/codeの改行・字下げ保持、未確認状態の視認性を含む。
- 内部fragment link（`href="#..."`）は最終HTML内の一致する`id`へ解決し、リンク先は一意でなければならない。

## 出力・レビュー・再開

reporterはincident HTMLとresult.jsonをattempt専用pathへ返す。別のrr-inago-incident-report-reviewerが内容、数値、版、リンク、秘密、HTML構造を読取確認する。改訂後は新versionを発行し、reviewを取り直す。

入力/リンク/版が欠落したら成功公開せずblocked。再開時は同じincident/job/対象日と承認済み各版を引き継ぎ、古い日の結果を新しい日の事実として流用しない。
