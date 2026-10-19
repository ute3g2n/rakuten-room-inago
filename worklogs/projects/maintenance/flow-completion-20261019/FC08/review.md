# FC08 独立製品コードレビュー

## 判定

**changes_requested**。対象は基準 `51cfc7503775a25af731c8f4bcf727c94f10a595` からの maintenance 製品コードとschemaの未commit差分です。版は `fc08-product-2c42b5ff228e721c239d0b4c9e576405fa653affc62a75fdbad78dadecda972f`（完全なfile hash manifestは `review.json`）に固定しました。対象は38ファイル、reviewerは試験コードを読んでいません。対象job identityは計画 v1.2 原文SHA `e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381`、FC07最終更新後の計画ファイルSHA `d27eaa2e1df0569747eb51717d3bf3000b2500bc53ebb82e95892ea5b71bf2ba` に結合しています。対象jobは job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、日付 `2026-10-17`、source_run_id `null`、incident `352492da-ac58-4680-b1fd-fe5f24000229` です。

## 指摘

1. **High — 公開工程が全体終端照合より先にjobを完了扱いにし、報告レビューを元障害の解決と取り違えます。** [production_workflow.py](/C:/project/rakuten-room-inago/automation/inago/maintenance/production_workflow.py) の `run_daily_report_publication_stage` は公開後すぐ `finalize_job(complete)` を呼び、report_status が `complete` なら incident を `resolved` にします。一方、[workflow_reconciliation.py](/C:/project/rakuten-room-inago/automation/inago/maintenance/workflow_reconciliation.py) は後から8工程の記録・出力・公開読戻しを照合して終端を登録します。途中終了時には照合前のcomplete/resolvedがDBに残り得ます。報告が承認された事実だけでは、10/17の障害原因や業務回復を確認できません。FC-R01/R07/R08、FCT10に関係します。公開工程ではpublication記録だけを保存し、job終端は照合後に一元登録し、incidentは根拠なくresolvedにしないよう修正してください。

   FC07のSQLite中断試験はHTML保存後・試行結果登録前の中断を測っています。公開後・この早期finalize後の中断と公開読戻しを組み合わせた試験は、FC07記録で未測定です。

2. **Medium — 失敗ログと引継ぎに記録する実行版が、通常は補助モジュール1ファイルのhashです。** [failure_journal.py](/C:/project/rakuten-room-inago/automation/inago/maintenance/failure_journal.py) は `MAINTENANCE_CODE_REVISION` が外から設定された場合だけそれを使い、未設定なら自身のファイルだけをhashします。[stage_handoff.py](/C:/project/rakuten-room-inago/automation/inago/maintenance/stage_handoff.py) も自身のhashを記録します。製品ソースを更新しても補助ファイルが同じなら、記録上の実行版が同じままで、実際に動いたworker/workflow/schema版を追跡できません。FC-R02/R04/R10、FCT02/05/10に関係します。起動時に実際のコード版を一度取得して工程イベントへ渡し、取得不能ならunknownと理由を記録してください。

3. **Medium — deployment工程の操作記録が既存maintenance_report試行へ誤結合する可能性があります。** [workflow_provider.py](/C:/project/rakuten-room-inago/automation/inago/maintenance/workflow_provider.py) は deployment をsequence 4、reportをsequence 5としてattemptを探しますが、[production_workflow.py](/C:/project/rakuten-room-inago/automation/inago/maintenance/production_workflow.py) の報告試行はsequence 4です。deploymentに専用attemptはなく、stage handoffは渡されたattempt_idを検証せず記録します。同じjobの再開時に既存報告試行があると、deploymentのbegin/失敗イベントやhandoffがその報告attemptを指し得ます。FC-R02/R03/R10、FCT01/10に関係します。deployment候補のrepair attemptを参照するか、専用attemptがないならnullとし、既存report attemptを指さないfocused確認を追加してください。

## 確認範囲と試験証拠

FC07の記録では、8段階failure injection、trace fallback/replay、timeout・不正AI応答、publication recovery、reporter/reviewer revision、fallback再開等をsynthetic/temp DB/local Gitで実測しています。r6の独立HTMLレビューは固定manifestに対してapprovedです。ただし、この結果から実job完遂やFC-A01〜10全達成を推定していません。FC07では本番DB・対象job・Daily/Scheduler・10/19 queue/history・public remoteを操作していません。今回のレビューも製品読取と試験記録の確認のみでpytest・本番操作はしていません。

次担当はこれらの指摘を修正・影響case再実行へ渡し、review状態に関係なくFC09/10を続行してください。



