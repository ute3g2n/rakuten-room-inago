# FC10 製品コード独立レビュー r2

- 判定: **approved**
- 対象HEAD: `eb16f9ec8fc145a0884ad9ce78c80f7d4a098df3`
- 基準commit: `51cfc7503775a25af731c8f4bcf727c94f10a595`
- 計画1.2 SHA-256: `135a687bbb585f587a5a00a827cdbdbe7dc218b8d2cf6f5b447534feefe9394e`
- 対象: 指定9製品ファイル、固定workspace `C:/project/rakuten-room-inago`
- 対象identity: `job-historical-9e7f0c175cc4c874c955a5dab29c4427` / `historical-9e7f0c175cc4c874c955a5dab29c4427` / 2026-10-17 / `source_run_id=null`

## 結果

指定した9製品ファイルの実測SHA-256は、最終確認時の対象版と一致しました。failure journalはレビュー開始後に更新されたため、親controllerの通知を受けて再読し、更新後のhash `e3fe788367153097e9e529a4ec7e848e78784d589c180201feeaf3ea48759857` で判定を行いました。テストファイルの現在hashは `a020bd0266875538a8e212692e29096fab8963bbdd179de52733e76087afbd04` ですが、test sourceは読まず、コードレビュー対象にもしていません。

前回FC10-F1〜F4は、今回の対象版で解消したと判断します。

- 計画担当は、無効timeout・準備段階のblocked・attempt不整合を内容あるhandoffへ変換し、`unknown`を返します。準備の未完了分岐にもblockedをそのまま通す経路はありません。過去raw観測の保持と後続結果は分離されています。[`production_workflow.py:4488`](../../../automation/inago/maintenance/production_workflow.py)
- `make_stage_handoff`の全呼出箇所でinvocation IDが渡されます。schemaも必須化し、保存directoryも同じIDを使います。terminalの全handoff照合と失敗handoff照合の双方で、handoff内invocation IDを現在のinvocationと比較します。[`stage_handoff.py:54`](../../../automation/inago/maintenance/stage_handoff.py) [`workflow_reconciliation.py:202`](../../../automation/inago/maintenance/workflow_reconciliation.py)
- deployment結果は登録済みschemaで検証され、同じrepair attemptの結果版、候補のrepair handoffとidentity、release ID、DBのrelease basis、対象deployment DB rowとJSON内容まで照合します。不一致は終端証拠として採用されません。[`workflow_reconciliation.py:42`](../../../automation/inago/maintenance/workflow_reconciliation.py)
- failure journalは一次記録と別directoryの二次spoolを使います。一次write失敗のcause情報を二次spoolへ保存し、open-operation照合とDB replayは両方の記録を読み、event ID重複を除いて再生します。二重write failureでも呼出元の処理を止めません。[`failure_journal.py:209`](../../../automation/inago/maintenance/failure_journal.py) [`failure_journal.py:335`](../../../automation/inago/maintenance/failure_journal.py)
- `WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})` は維持されています。[`worker.py:35`](../../../automation/inago/maintenance/worker.py)

## 試験証拠と範囲

FC10/F4の実行記録には、一次write故障時のspool/replayと二重sink故障時の非gate動作を確認した2件のPASSがあります。計画author追補記録は、その変更についてpytest未実施と明記しています。親controllerからはFC10 focused 13件PASSとの報告を受けましたが、レビュー時点のFC10成果物には統合13件ledgerが見当たりません。私は試験を実行せず、test sourceもレビューしていません。

本番DB・対象job・公開remoteには触れていません。このコードレビューは同一jobの本番完遂を証明しません。dual sinkが同時に利用不能な場合は永続記録不能となりますが、実装は安全化した失敗情報を返して本処理を続けます。全9ファイルのhash、計画hash、前回所見の解消根拠、証拠範囲は隣接するJSONに記録しました。
