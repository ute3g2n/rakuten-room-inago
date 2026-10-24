# 投稿文品質改善 実装レビュー

## 対象と結論

- 対象: `automation/inago/ai_runner.py`, `copy_service.py`, `orchestrator.py`, `prompt_builder.py`、関連schema、writer/reviewer skills・references・role、README・全体マップの今回差分。
- 要求/基準: `検討/投稿文品質改善_2026-10-24/計画と検証.md` と `.agents/skills/common-review/SKILL.md`。
- レビュー方法: 製品差分と関連文書を読取専用で照合。テストコードの独立レビュー、試験実行、DB・本番daily・Git操作は未実施。
- 結果: **approved**。初回のP2所見は修正後の実ファイルを確認し、解消を確認した。ほかの確認した実装箇所では、最終本文とレビュー本文の完全一致、同一job/商品/版/全参照の結合、情報不足の正常結果、保存失敗時に同一本文を再利用する復旧経路を阻害する明確な問題は確認しなかった。
- 試験状況: 最終回帰04は222 passed、0 failed、0 skipped、136.55秒、exit 0を記録で確認。試験ログは別testerの証拠として参照し、当レビューでは試験を再実行していない。skills quick_validate 3件と全体`git diff --check`は親からexit 0との共有。

## 所見

### [解消済み P2] COPY契約がreviewerを許可役から除外している

- ファイル/行: `.agents/skills/rr-inago-copywriter/references/contracts.md:3`
- 計画対応: 「作成規則・全references・独立レビュー規則を同一基準にそろえる」「AI-Oが同じjob内で独立reviewerを実起動する」。
- 根拠: 同行は「許可組合せはAI-OのT02から`rr-inago-copywriter`だけ」と規定する一方、同文書後半は`AG-COPY-REVIEW` / `rr-inago-copy-reviewer`を同一job内の必須delegationとして定めている。runnerの許可表・job schemaとAI-Oのskillも reviewer を許可しているため、contract自体が実装と矛盾する。writerまたはcontrollerがこの先頭の排他的記述を優先すると、reviewerを呼ばない/不許可扱いする解釈につながる。
- 対応・再確認: 2026-10-24に先頭を「本文作成はAI-OのT02からAG-COPYへ委譲し、同じjobの独立レビューは別AG-COPY-REVIEWへ委譲する」と更新したことを、`.agents/skills/rr-inago-copywriter/references/contracts.md:3` の実ファイルで確認。排他的な「だけ」はなくなり、後半の必須review手順およびrunner/schemaとの矛盾は解消した。
- 差分検査: `git diff --check -- .agents/skills/rr-inago-copywriter/references/contracts.md` はexit 0。GitはCRLF→LFに関する警告を出したが、whitespace errorはなかった。

## 確認した要点

- `ai_runner.py`はAI-Oへ、両担当に同じ全入力refsと完全な商品入力を渡し、revise時に同一作成子・同一reviewerをfollowupし、最終declarationを実際の子応答へ結び付ける指示を持つ。
- reviewer delegationはnative childの最終JSON hashとpayloadを照合する。copy serviceはwriter/reviewerのjob identity、参照集合、商品/版、`reviewed_body == writer.body`を検証し、readyはpassのみ、情報不足は両者一致のみ受け入れる。
- `insufficient_information`は正常なcopy結果として扱われ、候補を処理済みとして保存した後に入力を整理し、次候補へ進む流れがある。保存失敗後は既存のcheckpoint/response復旧経路を利用している差分で、再生成を指示する変更は見当たらない。
- writer/reviewerの両roleは共有アンチパターンと文型ルールを参照し、参照ファイルは新規ファイルとして存在する。アンチパターン集には履歴例A01〜A12があり、実例を商品事実根拠に転用しない注意書きもある。
- schemaのwriter/reviewer payloadと、writer/reviewer skillのフィールド・verdict・情報不足分岐は確認した範囲で整合している。

## 最終回帰試験証拠（別tester実施）

- 記録: `検討/投稿文品質改善_2026-10-24/test.md` の「最終回帰04（診断出力有効・freeze版）」。
- ログ: `検討/投稿文品質改善_2026-10-24/final-regression-20261024-04.log`。
- 対象: 計画 §6 の9試験ファイル。正確なpytestコマンドは `test.md` 該当節を参照。
- ソース版: HEAD `8939ab62fce902a86af54f3562ce37f50fa59dad` と開始時作業ツリー差分。製品コードはfreeze後で実行中変更なし。投稿文skillの明確化1行（段落で商品名を繰り返すことは必須とせず、取得URL/ID/要素・画像の対応で対象照合する）は実行開始時点で含まれていた。本文品質・全資料利用の要件は維持。
- 実測: 222 passed, 0 failed, 0 skipped、136.55秒、exit 0。開始/終了は2026-10-24 11:00:10–11:02:28 JST、隔離領域 `copy-quality-final-20261024-04`。
- 結果の読み方: 回帰04は計画nodeの成功を示す。回帰03の予期しないpartial再現の直接原因は未特定のまま。追加診断と局所10実行・関連9 node passを経て回帰04がpassしたことは記録するが、直接原因を特定・修正したとは扱わない。診断JSON 15件の再分類は未実施。
- 実AIによる文章品質そのものはpytest fixture回帰の対象外であり、別記の `skill-behavior.md` と混同しない。

## 未確認事項

- 回帰04の全node passは確認済み。03の間欠的な予期しないpartialの直接原因は未特定であり、回帰04の成功を原因修正の証拠とはしない。
- AIの実文章品質はコード差分・fixture試験だけでは実測していない。

### 親による証拠整理の補記

最終試験担当が後で実際のファイル列挙を照合し、診断JSONの15件という報告を計数誤りと訂正した。正しくは14件、整理後のpartial-diagnostics-summary.jsonはcaptured_files=14・観測数合計14・6種類である。記録された原因は試験が意図的に作ったエラーのみで、途中の不測partialの直接原因は未特定のまま保持する。最終222 passedの結果と製品レビュー判断は変わらない。
