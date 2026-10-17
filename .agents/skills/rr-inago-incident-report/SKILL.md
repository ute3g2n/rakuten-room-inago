---
name: rr-inago-incident-report
description: 合格済みの調査・計画・試験・反映記録から、平易で根拠付きの障害対応HTMLを作る。未確認事項を成功と表現しない。
---

# イナゴ障害対応HTML

## 使う場面

management stage=reportで、調査・計画・実装・試験・レビュー・反映状態をcontrollerが承認済みとして渡した場合だけ使用する。modelはgpt-6-luna、effortはmax。報告reviewerは新規の別担当。

## 作成

- docs/templates/incidents/日次不具合対応レポート_ひな形.htmlの構造・文体・安全な記載ルールを守る。
- 何が起きたか、期待と実結果、影響、回復、原因確度、実際に変わった動作、試験と反映の確認範囲を平易に書く。
- 当日事実、調査で判明した不足、試験で見つかったリスク、作業場所の合格、本番反映、次回日次確認を別々に示す。
- 未確認、保留、欠測を明示し、本文・商品名・個人情報・秘密・生データを公開出力へ複製しない。

## 禁止と停止

- 元の日次結果、投稿、元証拠、計画、review、実装、設定、DBを変更しない。
- fixture成功を本番成功と表現しない。反映、再発防止、次回観測を推測で完了にしない。
- 調査/plan/review版の参照不一致や根拠不足があればHTMLを成功成果として出さず、report stageをblockedにする。

## 参照契約

入力版、HTML章、公開範囲、reviewと再開の詳細は[contracts.md](references/contracts.md)を参照する。