---
name: rr-kai-research-plan
description: 楽天ROOM化け物・改の5ジャンルの在庫不足と成果条件から検索計画を作る。オーケストレータからresearch_planジョブを受けたときに使う。
---

# 5ジャンルの在庫不足と成果条件から検索計画を作る

## 適用範囲と入力

- `research_plan`専用。必須入力は`inventory_brief`、`genre_targets`、`winning_conditions`、`deprioritized_conditions`、`research_requests`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/research-plan.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

不足数、勝ち条件、優先度低下、調査キューをすべて読む。保留商品を除外しない。商品名や需要を捏造しない。

- 不足があるジャンルごとに、悩み・用途・属性の組合せを変えた重複しない検索語を5〜8件作る。商品名だけの言い換えで件数を満たさない。
- 検索語は各商品候補の検索露出スコアにも使う。後続のページ確認では、楽天市場検索1ページ目の掲載位置とPR/通常枠を記録する。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: 5ジャンルの優先順位と検索語が不足数・根拠に対応する。Schema適合JSONだけを返す。
