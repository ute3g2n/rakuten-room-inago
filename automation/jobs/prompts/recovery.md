# recovery

失敗したrunと工程だけを診断する。
必須入力: `failed_run_id`、`failed_step`、`failure_manifest`。
判断: 認証、CAPTCHA、入力不足、取得失敗はモデル昇格で解決しない。無関係工程や正本を触らない。
成功: 対象run_id/step、局所再試行案、利用者対応の要否を明示する。
出力は`recovery-review.schema.json`に適合させる。正本は変更しない。
