# product_verify

実商品ページsnapshotから適格性を判定する。
必須入力: `page_snapshots`、`selected_candidates`。
判断: 商品名、型番、販売状態、価格条件、レビュー、確度を確認する。`heat_scores`が入力された場合、6項目の点数と根拠は機械採点結果をそのまま使い、再採点しない。nullは0点ではなく未確認。加熱スコアと在庫・商品同定の適格性を混同しない。取得失敗を推測で補わない。
成功: `verified` は対応するsnapshotが `status=observed`、`availability=in_stock`、商品名と価格が確認済みの商品のみに限定する。`availability` がnull・unknown・out_of_stockなら、観測済みページでも `verified` に入れず、`unavailable` に `code` と不足内容の `detail` を記録する。未取得の販売状態を商品名や価格から推測しない。
出力は`product-verification.schema.json`に適合させる。正本は変更しない。
