---
name: rr-inago-incident-report
description: 利用可能な調査・計画・試験・反映記録から、平易で根拠付きの障害対応HTMLを作る。未確認事項を成功と表現しない。
---

# イナゴ障害対応HTML

## 使う場面

management stage=reportで、利用可能な調査・計画・試験・反映記録を入力として使用する。各stageのapproved状態は開始条件にしない。

対象jobが特定できればreport担当を直ちに起動する。調査・計画・試験・reviewの全入力やstatusが揃うまで待たず、利用可能な内容でHTMLを作る。報告書のreview対象HTMLが存在すればreport reviewerも必ず起動し、runtime観測やreceiptを理由に省略しない。

## 作成

- docs/templates/incidents/日次不具合対応レポート_ひな形.htmlの構造・文体・安全な記載ルールを守る。
- 何が起きたか、期待と実結果、影響、回復、原因確度、実際に変わった動作、試験と反映の確認範囲を平易に書く。
- 当日事実、調査で判明した不足、試験で見つかったリスク、作業場所の合格、本番反映、次回日次確認を別々に示す。
- 報告書作成担当は報告内容の所有者である。reviewerの指摘が入力されたら、根拠で確認できる範囲でreport responseの内容を修正し、hostに再描画させる。hostがHTMLを描画・保存する契約は、担当が内容を直せないという意味ではない。
- 最終HTML内の`href="#..."`は、同じHTML内に同じ`id`を持つ要素がちょうど1つあることを確認する。事象一覧と根拠欄のリンク先を一致させる。
- 未確認、保留、欠測を明示し、本文・商品名・個人情報・秘密・生データを公開出力へ複製しない。
- 根本原因がunknownでも報告を中断しない。trace/log追加だけを行った場合も、確認できた成果、原因未特定の事実、追加計装の目的、試験/レビュー/反映状況を記載してreport作成/公開まで続ける。計装を根本修正や原因判明として表現しない。
- U23.08 safe diagnosticのSQLite `error_name`/`constraint_label`はエラー分類、`failure_module`/`failure_function`/`failure_line`/`code_sha256`はpipeline内の捕捉位置を表す。callbackの根本原因や発生行を証明する値として説明しない。提案run IDと永続run ID、trace requested/persistedも別に記す。raw exception、SQL、params、秘密、個人情報を本文へ複写しない。
- `report.status=complete`だけをjob complete・incident resolved・Worker成功へ結びます。`pending`/`blocked`はartifactを保持し、jobを該当reason付きblocked、incidentをstatusに応じてin_progress/blockedとして記録します。未完了reportをWorker成功として表現しません。既にcompleteのjobを訂正する場合もpending/blockedの実statusを反映します。

## 禁止と停止

- 元の日次結果、投稿、元証拠、計画、review、実装、設定、DBを変更しない。
- fixture成功を本番成功と表現しない。反映、再発防止、次回観測を推測で完了にしない。
調査/plan/review版の不一致、根拠不足、FAIL、未反映はHTMLに未確認/未完了として記録する。review statusやfindingsを公開・後続処理のgateにしない。作成可能なHTMLを保存・公開する。

## 参照契約

入力版、HTML章、公開範囲、reviewと再開の詳細は[contracts.md](references/contracts.md)を参照する。
