---
name: rr-kai-triage
description: 楽天ROOM化け物・改のAPI候補から商品ページ確認対象を選ぶ。オーケストレータからtriageジョブを受けたときに使う。
---

# API候補から商品ページ確認対象を選ぶ

## 適用範囲と入力

- `triage`専用。必須入力は`api_candidates`、`inventory_brief`。`run_id`と`target_date`はジョブ共通。
- `rakuten-room-kai/orchestrator/schemas/triage-output.schema.json`を出力Schemaとする。
- 参照: `../rakuten-room-bakemono-kai/references/data-policy.md`、`../rakuten-room-bakemono-kai/references/output-contracts.md`。成果帰属は設計書第19節を優先。

## 判断規則

重複・投稿済みを除き、商品ページ確認の価値がある候補を最大60件まで選び、優先度の高い順に並べる。明らかなジャンル違いなど以外は残す。入力された全候補IDを選択・除外・不確実のいずれか一つに必ず分類する。API候補を適格確定しない。後続は10件ずつページを確認する。

## 禁止と成功条件

- 正本の台帳、下書き、投稿管理HTML、投稿状態、実行台帳を直接更新しない。認証情報を読まない。投稿・いいね・フォロー・購入・ログインをしない。
- 入力にない商品・価格・反応・売上・根拠を補わない。欠落は欠落として示す。モデル名と推論量はmodel-policy.jsonに従う。
- 成功: 選択・除外・不確実の理由が候補IDに対応する。Schema適合JSONだけを返す。
