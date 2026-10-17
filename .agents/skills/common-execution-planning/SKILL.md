---
name: common-execution-planning
description: 合格した障害調査と要件を実行可能な0〜14節計画、各STEP14項目、ケース12項目、全文promptへ変換する。調査結果やコードを改変しない。
---

# 障害対応計画

## 使う場面

management stage=planningでのみ使用する。入力はreview approvedの調査result_version、管理job契約、現在の要件・受入、コード版、使用可能なモデルと制約。計画担当とreviewerは別のcustom agentとして起動する。

## 作成基準

- 計画テンプレートの0〜14節を埋める。各STEPは14項目をすべて持ち、最後に単独で実行できる全文promptを付ける。
- 各test caseは12項目を持つ。完全node、入力、時計、版、初期状態、期待、禁止副作用、証拠、再実行条件を明示する。テストコードの独立レビューは計画の受入条件にしない。テスト実行やデバッグでテストコードの不具合を見つけた場合は担当者が修正し、影響caseを再実行して版と証拠を更新する。
- 承認済み調査のfact_idと根拠へ各作業を追跡する。未確定原因を前提にした修正を置かず、確認方法または保留条件を計画する。
- 入力、所有file、出力、reviewer、model/effort/tier、失敗、再開、Git、要件同期、release gateを明示する。

## 禁止と停止

- コード、テスト、DB、設定、Scheduler、task、証拠原本を変更しない。
- 調査にない事実、過去日の結果、存在を確かめていないnodeや要件IDを作らない。
- 未解決の所有・安全・モデル・権限・本番反映条件を成功扱いしない。判定不能ならblockedとして必要な情報を列記する。

## 参照契約

章構成、STEP14項目、ケース12項目、全文promptとレビュー契約は[contracts.md](references/contracts.md)を参照する。