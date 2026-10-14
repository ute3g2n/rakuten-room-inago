---
name: rr-kai-quality-gate
description: 楽天ROOM化け物・改の別ジョブで投稿文の根拠と表現を検査する。オーケストレータからquality_gateジョブを受けたときに使う。
---

# 別ジョブで投稿文の根拠と表現を検査する

## 適用範囲と入力

- `quality_gate`専用。必須入力は`verified_products`、`copy_output`、`single_change_experiments`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/quality-gate.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

ID、名前、URL、本文、根拠、NG表現、実験の単一変更を照合する。不合格理由と再作成対象を構造化する。
確認済み商品と下書きがともに0件なら審査対象なしとして合格・不合格・要修正を空にする。商品確認工程に推測による認定を要求しない。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: 合格と不合格が理由付きで分かれ、原稿を自分で書き換えない。Schema適合JSONだけを返す。
