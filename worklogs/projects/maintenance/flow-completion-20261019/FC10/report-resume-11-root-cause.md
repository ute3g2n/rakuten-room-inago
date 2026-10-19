# 同一job resume 11回目：レポート保存失敗の原因

## 対象と結果

- job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target_date: `2026-10-17`
- source_run_id: `null`
- invocation: `workflow-eb61b66512e54d608170f6e9845d498b`
- 実行時の公式active commit: `525ad3fa8d2d1acdb6e2909d011e988be2deceff`
- Workerは同じjobをresume_count 11で処理し、最終結果は`maintenance_incomplete / report_publication_inputs_incomplete`。jobは`failed`、terminal_reasonは`workflow_terminal_observations_incomplete`、incidentは未解決のままです。
- 8工程のうち、check・evidence・job・investigationはcomplete。planningは`ai_transport_failed`、repairは`repair_attempt_not_actionable`、deploymentは`deployment_candidate_unavailable`、reportは`report_publication_inputs_incomplete`でした。これらは今回のstage観測であり、後続stageを停止させるgateにはしていません。

## 原因と失敗の連鎖

1. 通常レポート作成は`report_evidence_ref_not_allowed`で失敗しました。failure-journal event `workflow.report.validate_render_review_persist`は例外型`ReportContractError`、捕捉位置`run_daily_report_stage`、呼出し連鎖`production_workflow.py`→`reporting.py:prepare_incident_report`→`reporting.py:_validate_response`を記録しています。
2. controller fallbackは読めるHTMLと`result.json`をattempt 5（`0e1f5f2f-a30f-4e11-ae1a-4157a43cfae9`）へ保存しました。fallback reviewerは変更指摘を返し、指摘反映のauthor応答も同じ`report_evidence_ref_not_allowed`で検証に失敗しました。author失敗は`workflow.report.fallback_author_revision`として、HTML・response参照、例外型、捕捉位置と呼出しframe付きで記録されました。最終fallbackのreview状態は`unavailable`であり、承認済みとは記録していません。
3. fallbackをstep attemptとして確定する際、実装は`attempt_no`を`review_round`に誤用し、値5を渡しました。しかし同じjob/step/sequenceに保存されたreview rowは0件で、storeの不変条件が求める次のreview roundは1でした。`MaintenanceStore.complete_step_attempt_with_review`は`review_round_mismatch`（`store.py:5642`）でtransactionを拒否し、例外は`workflow.report.fallback_create_review`として捕捉・記録されました。
4. attemptはfailedのまま終了し、HTML `job-historical-9e7f0c175cc4c874c955a5dab29c4427/0e1f5f2f-a30f-4e11-ae1a-4157a43cfae9/report-fallback-7722123e906a609d74a4.html`はローカルattempt directoryに残りました。ただしstep attemptのDB output参照は空で、公開段階が必要とするcomplete attempt/result/reviewの組がありませんでした。publication guardは`report_publication_inputs_incomplete`を記録し、公開と正しいjob終了登録は行われませんでした。

この原因は10/17当日の元障害の原因ではありません。日次障害の原因と業務影響は引き続き不明です。

## 修正とローカル検証

- `_persist_minimal_daily_report`は、現在lease検証済みでrunningのattemptについて、`MaintenanceStore.next_step_review_round(current.attempt_id)`から保存済みreview履歴に基づく次の番号を得るよう変更しました。attempt回数をレビュー番号として使わず、既存のstore検証と同じ規則に合わせています。
- 再現ケースは4回の過去attemptを作り、その全てにreview rowがない状態で5回目のcontroller fallbackを実行します。旧コードでは`unknown`で失敗し、新コードではreview status `unavailable`を正直に保存したうえでattemptがcompleteになります。
- 旧コードの再現: [report-fallback-review-round-red.console.txt](report-fallback-review-round-red.console.txt)（pytest 1 failed。`result.status`がunknown）。
- 修正後のfocused module: [report-fallback-review-round.console.txt](report-fallback-review-round.console.txt)（`tests_inago/test_maintenance_report_publication_stage.py`、32 passed / 43.20s）。機械試験記録 [report-fallback-review-round.junit.xml](report-fallback-review-round.junit.xml)。
- 独立製品reviewはapproved、指摘/条件0。manifestとsource hashは [review target](report-fallback-review-round-review-target.json)、判定は`report-fallback-review-round-review.json`に保存します。

## 次の再開

修正差分をcommit/pushし、正式Task Scheduler runtimeへ適用した版とhealth/task状態をread-backしてから、同一job・同一source_launch_id・null source_run_idを明示resumeします。次の実行でreporter・reviewer・publication・公開後read-back・正しいterminal登録を一つずつ確認します。10/19 queue/historyとDailyは起動しません。完遂するまでFC-A受入を達成扱いしません。
