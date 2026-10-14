---
name: rr-kai-daily-plan
description: 楽天ROOM化け物・改の確認済み未投稿下書きから日次計画を組む。オーケストレータからdaily_planジョブを受けたときに使う。
---

# 確認済み未投稿下書きから日次計画を組む

## 適用範囲と入力

- `daily_plan`専用。必須入力は`inventory_brief`、`eligible_products`、`genre_targets`、`winning_conditions`、`single_change_experiments`、`daily_plan_constraints`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/daily-plan.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

5ジャンル6/2/2/3/2で最大15件。ID/URL重複、投稿済み、本文欠落を除く。条件を反映し不足時は実数と理由を示す。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: 予定がローカル制約検査を通り、成果条件の適用を追跡できる。Schema適合JSONだけを返す。
