---
name: rr-kai-product-verify
description: 楽天ROOM化け物・改の実商品ページsnapshotから適格性を判定する。オーケストレータからproduct_verifyジョブを受けたときに使う。
---

# 実商品ページsnapshotから適格性を判定する

## 適用範囲と入力

- `product_verify`専用。必須入力は`page_snapshots`、`selected_candidates`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/product-verification.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

商品名、型番、販売状態、価格条件、レビュー、確度を確認する。`heat_scores`が入力された場合、6項目の点数と根拠は機械採点結果をそのまま使い、再採点しない。nullは0点ではなく未確認。加熱スコアと在庫・商品同定の適格性を混同しない。取得失敗を推測で補わない。
`verified` は商品ページsnapshotの `status=observed`、`availability=in_stock`、商品名、価格がすべて揃うときだけ使う。販売状態が未取得なら `unavailable` に理由を記録し、掲載可能な商品として扱わない。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: 確認済み根拠を持つ商品だけをverifiedに入れる。Schema適合JSONだけを返す。
