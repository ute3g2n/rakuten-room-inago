# AG-INSIGHT 入出力契約

- AI-OのT05からのみ起動し、本人の明示依頼をjobに記録します。T02日次の必須役ではありません。
- 入力はscope、集計値、`input_refs`, `evidence_refs`, 比較条件、観測時点、可用性・暫定・訂正状態です。AIが数値を再取得しません。
- 出力は`automation/inago/schemas/ai-insight.schema.json`に合わせます。rootに文字列`scope`と`findings`配列、各findingに文字列`statement`、文字列配列`evidence_refs`、文字列`comparison_basis`、文字列`limitations`を含めます。`limitations`は複数の制約をまとめた1つの文章にし、配列にはしません。
- 参照は入力`evidence_refs`内に限ります。null、0、未取得、未確定値を保持し、異なる成果scopeを足したり配分したりしません。
- 実投稿の根拠がないdraftを投稿済みとしません。任意の説明失敗は数値取得・保存結果を巻き戻しません。
