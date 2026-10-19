# report stage契約

## 入力

job identity、target_date、利用可能なinvestigation version、plan version（approval状態は観測値）、

## 起動元・対象日の不変結合

現行controllerは既知のjob IDへsource_launch_id / source_run_id（nullを含む）/ target_dateを結び付ける。report対象jobが識別できれば、他のfield・任意manifest・証拠の欠落や不一致だけを理由にreport stageを止めない。得られた値と不足・不一致を報告へ記録する。source_run_idは入力どおり保持し、nullを推測値で埋めない。job自体を特定できない操作だけを未実施として理由を記録し、他の利用可能な報告を続ける。

## 読取と書込

- 読取: 対象jobに紐付く利用可能な資料、plan、C/LC実測、レビュー、反映・復旧結果、証拠参照、報告ルールG、既存HTML構造H。approved statusは入力状態として記録し、report開始条件にしない。
- 書込: allowed_outputs内のincident HTMLおよび構造化result.jsonのみ。報告担当はworktreeを直接commit/pushせず、controllerへ検証可能な出力を返す。
- 外部向けHTMLは公開可能な要約だけとし、ログ/DB/商品/投稿/個人/secretの原文を含めない。

## 内容確認

- 案件・対象日・作成時刻・実行版を区別する。
- 事象一覧と詳細で区分（当日発生 / 調査で判明 / 試験で確認）、工程、操作、期待、実結果、影響、回復、原因の確度、変更後動作、試験範囲、根拠リンクを維持する。
- 調査、作業場所での検証、release gate、反映、次回日次観測は個別の状態として表す。未実施は未実施、skip/errorはPASSでない。
- HTMLは意味的見出し、症状の一覧と詳細、索引、相対リンク、狭幅/印刷向けCSS、pre/codeの改行・字下げ保持、未確認状態の視認性を含む。
- 内部fragment link（`href="#..."`）は最終HTML内の一致する`id`へ解決し、リンク先は一意でなければならない。

## 出力・レビュー・再開

reporterはincident HTMLとresult.jsonをattempt専用pathへ返す。別のrr-inago-incident-report-reviewerが内容、数値、版、リンク、秘密、HTML構造を読取確認する。reviewerの指摘は改善情報として返し、reporterは自分のresponseと本文を修正できる。再reviewを行う場合も回数・statusを後続処理の条件にしない。

入力/リンク/版が欠けても、解決できる範囲でHTMLを作成し、欠測と未完了を明記する。review statusや不足を後続stageのgateにしない。公開操作が実行できなくてもHTML作成とreport保存を続け、公開不能の理由をreportへ記録する。

`report.status=complete`だけがjob complete/incident resolved/Worker成功へ対応する。`pending`はartifactを保持したうえでjobを`blocked`（`report_status_pending`）、incidentを`in_progress`、Worker結果を`maintenance_incomplete`にする。`blocked`はjobを`blocked`（`report_status_blocked`）、incidentを`blocked`、Worker結果を`maintenance_incomplete`にする。既存complete jobへの訂正も未完了statusと理由を保持する。これらのstatusは後続処理のgateにしない。
