# quality_gate

別ジョブで投稿文の根拠と表現を検査する。
必須入力: `verified_products`、`copy_output`、`single_change_experiments`。
判断: ID、名前、URL、本文、根拠、NG表現、実験の単一変更を照合する。不合格理由と再作成対象を構造化する。
`verified_products` と `copy_output.drafts` がともに空なら審査対象なしとして `accepted`、`rejected`、`required_edits` をすべて空配列にする。確認済み商品を作るよう求めない。
成功: 合格と不合格が理由付きで分かれ、原稿を自分で書き換えない。
出力は`quality-gate.schema.json`に適合させる。正本は変更しない。
