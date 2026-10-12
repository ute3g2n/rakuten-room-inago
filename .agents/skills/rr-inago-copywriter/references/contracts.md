# AG-COPY 入出力契約

- 許可組合せはAI-OのT02から`rr-inago-copywriter`だけです。1回につき`copy_targets`は1件です。
- 入力は`offer_key`, `draft_version`, `settings_version`, `input_refs`, `evidence_refs`, 1商品の`offer`情報、投稿目的、任意の商品事実・販促条件・本人申告体験です。参照と入力は親プログラムのjobに一致させます。
- 出力は`automation/inago/schemas/ai-copy.schema.json`に合わせ、`offer_key`, `draft_version`, `body`, `pattern_id`, `evidence_refs`, `selection_reason`, `used_input_refs`, `omitted_unknowns`を返します。追加フィールドを作りません。
- `body`は貼り付け用の日本語本文だけです。管理用の型理由・内部rule ID・引用元注記を含めません。`selection_reason`等は本文の外側の内部フィールドです。
- `pattern_id`はP01～P05。数値・販促・体験主張には今回入力の参照を対応させます。未確認の値は`omitted_unknowns`へ残します。
- 採用済みの長さ150～300文字、タグ5～12個はそれぞれ`mandatory=false`です。本文完成のゲートや水増し目的に使いません。
- 基本5型は本人採用版`confirmed-0.3-step46-all132`です。隔離jobでは`copy_settings.patterns`とこのskillに記載した型条件が実行入力です。詳細ルールJSONの候補版`detailed-writing-rules-candidate-0.1-step68`は`adopted=false`です。設定runが有効版と確認記録を持つ場合だけ85型別規則と14共通規則がpromptに含まれます。P05固有ruleの根拠はB0002/B0201/B0213/B0235/B0252に限定されます。
- 132観測は文章構造の参考に限られ、反応・売上効果、本人の実体験、別商品の事実を証明しません。
