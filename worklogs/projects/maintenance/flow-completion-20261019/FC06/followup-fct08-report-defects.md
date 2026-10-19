# FC06 FCT08 統合で観測された報告工程の不具合修正

対象identityは固定のjob `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、日付2026-10-17、source_run_id=null、incident `352492da-ac58-4680-b1fd-fe5f24000229`。このファイルはFC07から受領したFCT08統合実行の指摘と、FC06担当が加えた静的確認を記録します。FC06はpytest、DB、本番処理を実行していません。

## 観測された不具合

1. `production_workflow.py::_daily_report_save_ai_wrapper` が `store`、`source_attempt_id`、`job_id` を引数/local bindingなしに参照していました。reporterの有効応答後、author response wrapper保存中に `NameError` となる経路です。
2. `_persist_minimal_daily_report` が `StepAttemptRecord.lease_key` と `.lease_generation` を読んでいましたが、このrecord型にこれらの属性はありません。通常報告失敗後の代替HTML保存・review開始も `AttributeError` で失敗しました。実際のtraceback全体と一次sink artifactはこの担当に渡されておらず、例外連鎖の残りはunknownです。

## 修正内容

- wrapper helperへ `MaintenanceStore` と正式 `job_id` を引数で渡し、AI応答artifact pathのjob/attempt部分を検証してからStoreからsource attemptを取得します。job、attempt ID、report step、sequence、actionable statusを照合し、formal attemptとAI応答pathのattempt IDが違えば拒否します。author/reviewer双方の2 callsiteで同じ値を渡します。
- fallbackはrecordにないlease列を参照せず、候補 attempt のjob/step/sequence/statusを確認後、`MaintenanceStore.validate_step_attempt_lease` で現在のlease所有を確認します。lease不一致/終了済み候補は次候補へ進み、その他のStore障害は具体reason `report_fallback_attempt_lease_validation_failed` へcause chainを残して引継ぎます。候補がなければ現在のcontroller leaseで正式attemptを開始します。
- `run_daily_report_stage`、completed report recovery、publication境界から未来の結果 `blocked` を返さず、元のreasonを保った `unknown` で後続処理へ進めます。
- fallback HTML用の文面も修正し、本文の「報告作成」「別担当の確認」「公開」「工程終了」を日常語で表し、外部の内部IDやrole名を本文の説明に使いません。

## 静的確認と未実施

対象関数のAST確認で、wrapper signatureにstore/job_idがあり、2 callsiteが両方を渡すこと、attemptをStoreから読むこと、正式attempt IDがAI artifact pathと一致することを確認しました。fallback関数がlease validation APIを使い、recordに存在しないlease属性を読まないこと、report/recovery/publication handlerが未来にblocked statusを返さないことを確認しました。3 moduleの`py_compile`、3 report schema definition check、2 role TOML parse、requirements builderもPASSです。これはstatic confirmationであって、FCT08 pytest・role call・artifact保存・review revision・publication結果ではありません。

FC07 testerへ影響nodeの再実行を引き継ぎます。期待結果は、author wrapper保存でNameErrorが起きないこと、故意の通常report失敗でfallback writer/reviewerへ進むこと、reviewerの指摘修正版が同一attemptに保存されpublication runnerまで進むこと、レビュー起動失敗でも未承認のままpublicationを呼ぶことです。PASSはtesterの新結果を待ちます。


## Transport-failure artifact collision and bounded revision follow-up

FC07 found fallback persistence collided with pre-existing report-stage artifacts (`investigation_artifact_already_exists`, `InvestigationContractError→FileExistsError`) when reviewer transport failed after an earlier report artifact had been saved. FC06 now creates hash-versioned `report-input-fallback-<20hex>.json`, `report-fallback-<20hex>.html`, `review-input-fallback-<20hex>.json`, `review-response-fallback-<20hex>.json`, and `review-fallback-<20hex>.json` in the same immutable attempt. Result remains the formal result.json. Reconciliation reads a same-job/same-attempt hash-bound safe versioned HTML path; result schema patterns are restricted to these fixed names.

For actual `changes_requested`, exact findings are passed once to the report author; its newly versioned output receives a second reviewer launch. Author failure or repeated findings creates a truthful pending/unapproved HTML with the findings and continues publication.

Current product hashes: production_workflow.py and report_fallback.py are in `fc07-html-followup-target.json`; FC07 owns pytest and must report its result against those exact hashes. FC06 did not execute tests, DB, production, or actual role sessions.


## FC07 independent follow-up r2 (2026-10-20)

Source: `worklogs/projects/maintenance/flow-completion-20261019/FC07/html-independent-followup-review-r2.md`, SHA-256 `2827443cb8fdf314ff260042385baac1324dec966dd2f0b427b6354f74d59f28`; verdict changes_requested; 17 target hashes matched.

1. A second review finding was being copied onto a final fallback HTML version that the reviewer had not seen. Fixed: the actual finding response remains in its exact version/hash-bound sidecar; the final pending fallback has a separate `unavailable` review record bound to its own HTML SHA and contains no claim that it was reviewed.
2. Missing identity could fail artifact-context validation before HTML persistence. Fixed without weakening global `_validate_context`: artifact storage context is derived from immutable job row/current launch, while raw incomplete identity stays in report input/body and missing fields are displayed.
3. Completed fallback resume ignored result.report_input binding. Fixed: recovery reads exactly result.report_input.path and checks its SHA, same job/attempt safe path, fallback schema, raw identity, storage identity, report output path/hash, and review record before restoring state.

Fresh independent review of the changed target is pending. No approval is inferred.
