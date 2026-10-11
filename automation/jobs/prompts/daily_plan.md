# daily_plan

確認済み未投稿下書きから日次計画を組む。
必須入力: `inventory_brief`、`eligible_products`、`genre_targets`、`winning_conditions`、`single_change_experiments`、`daily_plan_constraints`。
判断: 5ジャンル6/2/2/3/2で最大15件。ID/URL重複、投稿済み、本文欠落を除く。条件を反映し不足時は実数と理由を示す。
成功: 予定がローカル制約検査を通り、成果条件の適用を追跡できる。
出力は`daily-plan.schema.json`に適合させる。正本は変更しない。
