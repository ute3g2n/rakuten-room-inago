# 同一job resume 12回目：公開前のID照合失敗

## 対象と結果

- job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target_date: `2026-10-17`
- source_run_id: `null`
- invocation: `workflow-7bffb23e08b04d9094660f5eb1c7879a`
- 実行時の公式active commit: `70fca6ac8708ab74551bf55abb089dd32843d884`
- 同一jobのresume_countは12。report attempt 6（`a4d986bd-f0dc-4391-9328-6dc3f08f120c`）は`complete`になり、result JSONとfallback HTMLのhashはDB参照と一致しました。
- その後、publication stageが`report_publication_identity_mismatch`で停止しました。HTMLはattempt directoryに保存されていますが、公開先への送信はなく、jobは`failed`、`workflow_terminal_observations_incomplete`。FC-A受入は未達です。

## 失敗連鎖と原因

1. 通常レポートauthorとfallback revisionはresume 11と同じ`report_evidence_ref_not_allowed`で失敗しました。failure journalは`ReportContractError`、`run_daily_report_stage`の捕捉位置、`prepare_incident_report`→`_validate_response`のframe、revision用HTML/応答参照を記録しました。
2. controller fallbackはattempt 6へHTMLと`result.json`を保存しました。fallback結果はreviewer unavailable・report pendingであり、承認済みとは偽っていません。`MaintenanceStore.next_step_review_round`を使う前回修正によりstep attemptはcompleteとなり、resultとHTMLの参照hashもDB read-backと一致しました。
3. publication guardは`run_daily_report_publication_stage`で`report_publication_identity_mismatch`を返しました。結果JSONのidentityはjob manifestのidentityと一致し、attempt ID/version、`review_unavailable`状態、result/HTML path/hash、DB output path/hashも一致します。失敗位置`production_workflow.py:10433`の照合式に残った候補は`workflow.state["precheck_identity"] != manifest.identity()`でした。
4. `_precheck_identity`は歴史入力用のattempt ID・base commit・input/evidence参照も持ちます。報告書作成に入る前のこの付加情報がprecheckへ残っていましたが、公開段階は`job_input_manifest.identity()`だけとの辞書全体一致を要求します。reporter/fallback自体はmanifestの正しい5項目を使っていたため、結果JSONとprecheck stateの表現だけが異なりました。入力/evidence参照を正確に保管したまま、precheck identityを正準化する箇所がありませんでした。

これは10/17当日の元障害の原因ではありません。日次障害の原因と業務影響は引き続き不明です。

## 修正と検証

- manifest identityの検証後、report fallback保存時と通常report stageで`precheck_identity`を正確な`manifest.identity()`へ正準化しました。同じIDを復元するcompleted-fallback/recovery branchesも正準化します。`report_input_refs`と`report_evidence_refs`はそれぞれの専用state fieldに残し、publicationの完全一致検証は維持します。identity不一致のときは正準化前に失敗として残します。
- 修正前のredは、歴史precheckへattempt/base/input/evidenceを含めてfallback stageを実行し、publicationへ渡されたidentityに余分な4項目が残ることを確認しました。また既存のfallback stage runner nodeでも同じ不一致を再現しました。
- 修正後の2 focused casesは2 passed。report publication moduleは32 passed / 43.25s。console/JUnit、正確な対象source/test hashを`report-publication-identity-review-target.json`へ記録しています。
- 独立製品reviewは対象source hash固定で依頼中です。レビュー後も必要な指摘を修正し、同じjobの次の限定resumeで実publication/read-backを確認します。

## 次の再開

新差分を独立review後にcommit/pushし、公式fixed-root runtimeへ適用してactive SHA・health・4 scheduled task read-backを確認します。その後、同じjob/source_launch_idだけをresume 13し、実reporter/reviewer/publication/read-back/terminalを続けて確認します。10/19 queue/historyとDailyは起動しません。完遂までFC-A達成を宣言しません。


## 独立review指摘の修正

最初のcanonical identity修正をfresh product reviewしたところ、通常report復旧branchで検証済み `input_refs` / `evidence_refs` が専用state fieldへ戻らず、partial-input準備へ進んだとき履歴参照を失う可能性があるというMedium指摘を受けました。これをFC04 owner mapへ追記し、通常report入力の復旧時に参照を `report_input_refs` / `report_evidence_refs` へ保存する実差分を加えました。exact manifest identity検証と不正ID拒否は維持しています。

修正前の追加nodeは `report_input_refs` 欠落のKeyErrorで1 failed、修正後は1 passed / 0.29s。報告stage module全体は33 passed / 43.96s、対象2 module `py_compile`も成功しました。再review対象manifest `report-publication-identity-review-target-r2.json` SHA `a98e247b9df6ff210092e4d84b6202630476a7b01b6b790fc270bd4be8e3e864`、product source SHA `949ae4e9ab61a62e66f9fabd983eff9d0efe820f4623429bab759291a37fb4f2`。fresh independent product reviewはこれから実起動し、その結果を保存します。


Fresh independent product review r2 is approved with no findings or conditions. Manifest SHA `a98e247b9df6ff210092e4d84b6202630476a7b01b6b790fc270bd4be8e3e864` and product source SHA `949ae4e9ab61a62e66f9fabd983eff9d0efe820f4623429bab759291a37fb4f2` matched. The reviewer confirmed the dedicated input/evidence reference restoration and strict identity checks in all relevant branches. Test source hash was integrity-only; reviewer did not read or run tests, edit files, or touch production data. Results are `report-publication-identity-review-r2.json/.md`.
