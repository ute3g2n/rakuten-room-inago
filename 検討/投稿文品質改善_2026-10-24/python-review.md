# 投稿文品質改善 Python製品コードレビュー

## 対象と結果

- 対象差分: `automation/inago/ai_runner.py`, `copy_service.py`, `orchestrator.py`, `prompt_builder.py`。
- 参照: `検討/投稿文品質改善_2026-10-24/計画と検証.md`、`.agents/skills/common-review/SKILL.md`、関連schemaとwriter/reviewer skill。
- 方法: Python製品コードの差分と再開・選択経路を読取専用で確認。テストコードの独立レビュー、試験実行、DB・日次実行、Git操作は未実施。
- 初回結果: changes_requested — P1 1件。
- 再レビュー結果: **approved — 初回P1は解消済み**。
- 静的確認（初回レビュー時）: `black --check automation/inago/ai_runner.py automation/inago/copy_service.py automation/inago/orchestrator.py automation/inago/prompt_builder.py` はexit 1で4ファイルをreformat対象とした。既存行を含むファイル全体の判定のため、これは差分行だけの形式違反を特定した結果ではない。`ruff` / `mypy` / `pylint` は実行環境で利用可能コマンドを確認できなかった。`git diff --check` はexit 0（orchestrator.pyのCRLF→LF警告のみ）。最終回帰は別担当の試験記録とログを読み、こちらでは再実行していない。

## 所見

### [解消済み P1] 保存済み成功checkpointの再開で独立reviewと入力版を検証しない

- ファイル/行: `automation/inago/copy_service.py:657-704`, `968-989`。
- 計画対応: 「合格していない本文は新規確定しない」「最終本文へのレビュー結合」「保存失敗時の同一本文復旧」。
- 問題: `latest["outcome"] == "success"` の場合、`_copy_checkpoint_request` が `AG-COPY` writer行・保存payload・artifactだけを検証し、`AG-COPY-REVIEW` の保存行、pass verdict、`reviewed_body`と保存本文の一致を確認せず `_commit_saved_response` へ渡す。加えて保存済み参照は現在specの参照の部分集合であれば通るため、checkpoint作成後に入力参照が追加されても旧本文をそのまま確定できる。新コードがレビュー成功後にcheckpointを作る通常経路は確認したが、このresume境界ではレビュー結合が保証されない。旧版で作られた未レビューの成功checkpointや、追加資料を含むspecでの再開が該当する。
- 影響: 再開処理で新規reviewを実行しない仕様自体は、同一本文を安全に再保存するため必要。ただし保存済み本文がレビュー合格済みの同じ本文・同じ入力版だと照合できず、独立レビューを通っていない本文や現在の完全な入力を反映していない本文が確定し得る。
- 修正・再確認: `repository.py:1339-1379` で成功attemptのroot、当該attempt/job prefixに属するreviewer、writer、artifactを読む。`copy_service.py:688-786` はroot input hashと現spec payload hash、root/writerの全refs集合、各job identityを確認し、保存されたwriter/reviewerを同一attemptへ復元して既存の `_validate_copy_review` を再利用する。passと完全一致するreviewed bodyが確認されない場合は、`generate_and_commit_copy` が理由付き `contract_error` を返し、checkpointを残してAI再生成・commitへ進まない。通常生成でも同じvalidatorを使用する。既存の確定済みdraftは先行する `prior` 分岐で引き続き再資格化・書換えしない。差分を読み、初回P1を解消済みと判断した。
- 修正後の局所試験（親からの実行記録。レビュー担当は再実行していない）: `test_copy_quality_review.py` の3 node (`test_unverified_or_changed_checkpoint_cannot_bypass_review`, `test_verified_checkpoint_resumes_identical_body_without_an_ai_call`, `test_already_committed_body_is_not_requalified_or_rewritten`) と `test_step12.py::test_save_failure_resumes_same_artifact_without_ai_call`。合計15 passed、1.15秒、exit 0との共有。

## 確認した事項

- 通常生成と保存checkpoint再開では `_validate_copy_review` がwriter/reviewerのjob identity、参照集合、対象商品・版、`reviewed_body == writer.body` を確認する。再開時はさらにroot入力hashと参照集合を現在specと照合し、保存済みの同一本文のみを再利用する。
- `insufficient_information` は生成エラー再試行と区別され、orchestratorが候補をhandledとして記録し、選択manifest上で有効数から外して入力整理後に次候補へ進む。確認した経路に、同一候補を繰り返し補充する無限ループは見当たらない。
- native reviewer最終JSONは、観測された子rolloutから抽出したhashをdeclaration payloadと比較する実装があり、runnerの既定値はobserved metadata必須。今回読めた実装から環境イベント形式との不整合は確認できなかった。
- Writer/reviewerのrole指示とpromptは全HTML/OCRの確認、タイトル・概要の再掲禁止、内容に基づく判定、正常な情報不足結果、共有アンチパターン参照を含む。

## 最終回帰試験記録の確認

- 参照した実物: `検討/投稿文品質改善_2026-10-24/test.md` の「最終回帰04（診断出力有効・freeze版）」、および `final-regression-20261024-04.log`。
- 9対象ファイルの実結果: **222 passed、0 failed、0 skipped、136.55秒、exit 0**。ログ末尾は `222 passed in 136.55s (0:02:16)`。正確なコマンド、開始/終了時刻、隔離領域、環境変数はtest.mdに記録されている。対象版はP1修正後のfreeze版で、実行中に製品コード変更なしとの記録。
- 失敗nodeの追跡: regression 03で `test_resume_reuses_selected_registry_and_completed_ocr` がpartialとなった後、test.mdには直接原因が未特定と記録されている。該当product-page selectionの2 nodeを各5回（合計10回）局所実行して全pass、selection 9 nodeの共有helper実行も9 passedとの担当報告があり、診断helperを追加した版でregression 04は全passした。
- 限界: regression 04の成功と診断出力の追加は確認したが、間欠partialの直接原因を特定・修正した根拠にはならない。test.mdでも原因未特定を保持している。regression 04の診断JSON 15件は出力確認の記録がある一方、個々のJSON内容は本レビューでは分類・検査していない。これらのfixture試験は実AIによる文章品質の実測でもない。

### 親による証拠整理の補記

最終試験担当が後で実際のファイル列挙を照合し、診断JSONの15件という報告を計数誤りと訂正した。正しくは14件、整理後のpartial-diagnostics-summary.jsonはcaptured_files=14・観測数合計14・6種類である。記録された原因は試験が意図的に作ったエラーのみで、途中の不測partialの直接原因は未特定のまま保持する。最終222 passedの結果と製品レビュー判断は変わらない。
