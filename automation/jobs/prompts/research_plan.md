# research_plan

5ジャンルの在庫不足と成果条件から検索計画を作る。
必須入力: `inventory_brief`、`genre_targets`、`winning_conditions`、`deprioritized_conditions`、`research_requests`。
判断: 不足数、勝ち条件、優先度低下、調査キューをすべて読む。保留商品を除外しない。商品名や需要を捏造しない。不足があるジャンルごとに、悩み・用途・属性を変えた重複しない検索語を5〜8件作る。同じ商品名の言い換えで埋めない。
成功: 5ジャンルの優先順位と検索語が不足数・根拠に対応する。
出力は`research-plan.schema.json`に適合させる。正本は変更しない。
