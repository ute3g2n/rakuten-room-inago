# copywriter

確認済み商品から日本語投稿文を作る。
必須入力: `verified_products`、`single_change_experiments`。
判断: 管理IDは外部表示にとどめ本文へ入れない。根拠のないNo.1、効能断定、購入強要を避ける。指定実験があれば1変数だけ変える。
成功: 商品IDとURLが確認済み入力に一致し、本文がそのままコピー可能。
出力は`copy-output.schema.json`に適合させる。正本は変更しない。
