---
name: rr-kai-copywriter
description: 楽天ROOM化け物・改の確認済み商品から日本語投稿文を作る。オーケストレータからcopywriterジョブを受けたときに使う。
---

# 確認済み商品から日本語投稿文を作る

## 適用範囲と入力

- `copywriter`専用。必須入力は`verified_products`、`single_change_experiments`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/copy-output.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

管理IDは外部表示にとどめ本文へ入れない。根拠のないNo.1、効能断定、購入強要を避ける。指定実験があれば1変数だけ変える。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: 商品IDとURLが確認済み入力に一致し、本文がそのままコピー可能。Schema適合JSONだけを返す。
