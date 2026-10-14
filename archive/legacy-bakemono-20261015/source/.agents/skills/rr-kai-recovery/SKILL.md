---
name: rr-kai-recovery
description: 楽天ROOM化け物・改の失敗したrunと工程だけを診断する。オーケストレータからrecoveryジョブを受けたときに使う。
---

# 失敗したrunと工程だけを診断する

## 適用範囲と入力

- `recovery`専用。必須入力は`failed_run_id`、`failed_step`、`failure_manifest`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/recovery-review.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

認証、CAPTCHA、入力不足、取得失敗はモデル昇格で解決しない。無関係工程や正本を触らない。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: 対象run_id/step、局所再試行案、利用者対応の要否を明示する。Schema適合JSONだけを返す。
