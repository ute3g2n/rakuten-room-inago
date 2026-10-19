# FC07 独立HTML review記録（修正前版）

結果: changes_requested（controllerから受領した独立review所見。本文作成者による判断ではありません。）

対象版hashは `worklogs/projects/maintenance/flow-completion-20261019/FC06/fc07-html-review-target.json` に記録しました。例HTML4件、fallback builder、template、reporter prompt、report contractの修正前SHAを保存しています。review担当はFC07 independent HTML reviewerです。対象IDはFC07作業記録と同じjob identity（2026-10-17、source_run_id=null）ですが、このレビューは本番incident reportではなくFC06の合成例とreport contractsへの指摘です。

## 指摘

1. Visible synthetic label is hidden in collapsed appendix.
2. Examples claim no review changes although no review ran.
3. Normal example omits event classification, expected/actual/impact/recovery/change/evidence and asserts no unknowns.
4. Unknown example contradicts known investigation facts and fails to separate known from unknown scope.
5. No-change example lacks instrumentation consideration and the reason it was not added.
6. Renderer failure example lacks confirmed facts/fallback contents and review/publication state.
7. All examples need day/classification, expected/actual/impact/recovery, concrete evidence references, and separate workflow completion versus incident resolution.

## 追加確認

秘密情報は見つからず、確認可能なリンク/anchorに破損はなく、狭幅/印刷のCSS構造にもソース上の問題は見つからなかったとの結果です。HTMLを実ブラウザや印刷で描画した確認ではありません。

## 対応

作成者が4例、fallback renderer、reporter/reviewer prompt、標準・template・contractsを修正します。修正後HTMLは未承認です。controllerへ独立follow-up reviewを依頼するまでは、指摘0件やapprovedと扱いません。


## FC07 FCT09 independent follow-up (2026-10-20)

Source report: `worklogs/projects/maintenance/flow-completion-20261019/FC07/html-independent-followup-review.md`; review SHA-256 `4fd1bd3e0e292a3c27887fcfc3fe42740327ffadf099c3d27c64049984acccb57`; verdict changes_requested.

1. All examples and fallback need visible event-origin category: fixed via `event_category` and section heading.
2. Nine nonexistent `samples/...` evidence refs: replaced with existing standards, contracts and implementation references.
3. Findings were not applied and fallback could loop: one actual reporter revision now receives exact findings; it is rerun through independent reviewer once. Author failure/repeated findings are preserved as pending and publication continues.
4. Missing identity raised ValueError: fallback renderer now shows unknown/missing fields and continues.

Independent follow-up after these changes remains pending; do not infer approval from static checks.


## FC07 independent follow-up r2 (2026-10-20)

Source: `worklogs/projects/maintenance/flow-completion-20261019/FC07/html-independent-followup-review-r2.md`, SHA-256 `2827443cb8fdf314ff260042385baac1324dec966dd2f0b427b6354f74d59f28`; verdict changes_requested; 17 target hashes matched.

1. A second review finding was being copied onto a final fallback HTML version that the reviewer had not seen. Fixed: the actual finding response remains in its exact version/hash-bound sidecar; the final pending fallback has a separate `unavailable` review record bound to its own HTML SHA and contains no claim that it was reviewed.
2. Missing identity could fail artifact-context validation before HTML persistence. Fixed without weakening global `_validate_context`: artifact storage context is derived from immutable job row/current launch, while raw incomplete identity stays in report input/body and missing fields are displayed.
3. Completed fallback resume ignored result.report_input binding. Fixed: recovery reads exactly result.report_input.path and checks its SHA, same job/attempt safe path, fallback schema, raw identity, storage identity, report output path/hash, and review record before restoring state.

Fresh independent review of the changed target is pending. No approval is inferred.
