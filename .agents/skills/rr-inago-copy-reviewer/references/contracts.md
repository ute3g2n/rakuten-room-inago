# AG-COPY-REVIEW 入出力契約

AI-O/T02で許可された一商品・一版を独立レビューする。入力はoffer_key、draft_version、設定版、input_refs、evidence_refs、作成結果と同一商品の全元資料。商品名・概要・factsだけでなく全HTML outer_html・全OCR textを読む。

出力はai-copy-review.schema.jsonの八フィールドのみ: offer_key, draft_version, verdict, reviewed_body, reason, evidence_refs, used_input_refs, findings。verdictはpass / revise / insufficient_information。reviewed_bodyは実際に読んだ本文と完全一致。参照はjobで許可された値のみ。

タイトル・概要の言い換えや名称・容量等の羅列だけは不合格。説明材料があるならrevise、全資料でも材料がなければinsufficient_information。readyの最終本文へのpassのみ保存対象。正常情報不足は作成担当の空本文と一致させる。自己承認・本文の代作・外部アクセス・DB変更・投稿はしない。

毎回[共通アンチパターン](../../rr-inago-copywriter/references/投稿文アンチパターン.md)と[共通作成ルール](../../rr-inago-copywriter/references/文型ルール確定.md)を参照する。
