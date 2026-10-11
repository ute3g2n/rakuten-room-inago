# Step 08 結果

- 状態: success
- 8つの判断サブスキルに目的、範囲、必須入力、参照先、判断規則、禁止事項、出力Schema、成功条件を記載した。正本の直接更新は禁止し、既存スキルは変更していない。
- `automation/jobs/contracts.py`が各ジョブの入力必須項目、出力Schema、モデル方針、実行envelopeを一元化。8つのprompt templateを作成。通常モデルはmodel-policy.jsonから選び、ジョブ定義には埋め込まない。
- copywriterとquality-gateは独立したジョブ。出力後の意味検査でID/URL、15件上限、投稿済み再選定、ジャンル配分、不足理由、NG表現、performance日付、recovery対象を確認する。CLIランナーは正本保存前にこの検査を実行できる。
- `recovery-review.schema.json`を追加。

検証: fake CLIで全8ジョブのSchema適合成功と不足入力拒否、NG文面の出力保存拒否を確認。`pytest -q tests_kai` 55 passed、exit 0。`quick_validate.py`で新旧入口を含む9スキルがすべてvalid、exit 0。実モデルは呼んでいない。
