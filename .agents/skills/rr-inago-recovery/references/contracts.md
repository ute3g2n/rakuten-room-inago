# AG-RECOVERY 入出力契約

- AI-OのT08で、本人または保守担当から明示依頼された診断だけを受けます。
- 入力は失敗run/task/版/期限/checkpoint、最終保存段階、試行数、伏字済み`input_refs`と`evidence_refs`です。秘密・Cookie・資格情報は入力しません。
- 出力は`automation/inago/schemas/ai-recovery.schema.json`に合わせ、`causes`, `unfinished`, `resume_proposal`, `owner_actions`を返します。原因候補は根拠参照に紐付けます。
- run ID、設定版、乱数seed、検索回数・生成試行回数、予約、確定本文、未反映報告を初期化する案を出しません。期限後のAI回答を完成本文にしません。
- 実際の再開や復元はAIから切り離したプログラム/保守作業です。旧タスク・旧DBは起動・更新しません。
