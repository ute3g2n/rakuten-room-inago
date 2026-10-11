---
name: rr-kai-performance
description: 楽天ROOM化け物・改の投稿別反応と集計成果を分けて学習する。オーケストレータからperformanceジョブを受けたときに使う。
---

# 投稿別反応と集計成果を分けて学習する

## 適用範囲と入力

- `performance`専用。必須入力は`performance_input`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/performance-analysis.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

第19節の帰属規則を守る。日別クリックを投稿へ配らない。いいね0を購入0やCへ変えない。9配列を欠落させない。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: A/B/C/保留の根拠と翌日の調査・計画条件を出す。Schema適合JSONだけを返す。
