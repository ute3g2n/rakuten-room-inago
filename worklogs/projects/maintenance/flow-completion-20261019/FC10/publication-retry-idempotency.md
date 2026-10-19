# FC10: 公開済みHTMLの再試行を同じ公開として復旧する

- 記録時刻: 2026-10-19T22:14:56Z
- 対象job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- 対象日: 2026-10-17、`source_run_id=null`
- 対象report attempt: `248743c6-c710-4ef3-af53-9336fbe2e3c1`
- 作業場所/branch: `C:\project\rakuten-room-inago` / `main`
- 変更前HEAD: `0a841e3e1b7c3e9a8b2a39ba4a0f72b5cbe0eb9a`

## 再現した問題

resume 21では同じjobが同じreportを公開したあと、終端の読戻しで不完全として終了した。公式の失敗記録はoperation `workflow.report.publication.push_remote`、例外`ReportContractError`、reason `report_path_conflict`。捕捉位置は`run_daily_report_publication_stage`で、呼出し経路は`publish_reviewed_report` → `_find_report_publication` → `_report_publication_commit`。相関/ invocation IDは`workflow-6cc9a7fdd24b4789affb54939aa1ed06`、耐久failure eventは`9a9560eb3f6b4ea8b24b426cde23a8da`。

r13のreview済みHTML SHAは`2644b0118ac4d147c89b5de72ab9911b33b1f42b253f9f36593e2859f962b650`。公開先に同じ内容がある一方、前のcontroller fallback blobは`ccbc1b6872ac533ff965d2dc03b75aabd1a30550d0dba77f3fe0172f2d3035b2`だった。再試行側が既存内容と異なる「公開前の親blob」を照合情報に戻さなかったため、厳密な同一公開判定が衝突した。10/17の元の不具合原因は引き続き不明。

## 修正と確認

`production_workflow.py`は、正当性確認済みの同じattemptのpublication sidecarから公開前blobを復元する。push後にsidecar保存前の中断だった場合は、job key・result version・report pathが一致するリモートcommitがただ一つある時だけ、そのcommitの親blobを使う。曖昧な複数commitは失敗させ、既存publisherのcommit/親/scope/ancestry/reviewed HTML SHA検証を通す。回帰testは、以前のfallback報告が存在する場合をfixtureに作り、push後/sidecar保存失敗後の再開と、その後の再実行が重複commitを作らないことを確認する。

- 修正前RED: synthetic local bare remoteで中断後の再試行が`report_path_conflict`になることを確認。
- focused検証: `test_maintenance_report.py`と`test_maintenance_report_publication_stage.py`、**61 passed / 0 failed、115.40秒**。
- 静的確認: `py_compile`成功、Worker除外日が`2026-10-19`のまま、`git diff --check`成功（既存の改行正規化警告のみ）。
- 独立製品review: `APPROVE`、指摘0。対象`production_workflow.py` SHA-256 `06e792b49cf10fbc4148ce972f3f7498fcbdaadc912775996be2fdcb15265d88`。reviewerはテストコードと本番DB操作をreviewしていない。
- 対象SHA、command、result、review範囲は`publication-retry-idempotency-verification.json`と`publication-retry-idempotency-review.json`。

## 現在の本番job状態と再開位置

読取専用DB確認は`quick_check=ok`、schema 16。jobはresume_count 21 / status `failed` / reason `workflow_terminal_observations_incomplete`。launch/date/null-runは指定値のまま。10/19 Worker queueの1件は`queued`のままで、Daily launch・新job・source runの生成を確認していない。今回の修正はまだcommit/push/runtime apply前で、本番に有効化していない。

次は今回所有する差分とFC10記録だけを選択commit/pushし、公式`TaskSchedulerRuntimeSwitcher`で固定rootに適用・読戻しする。その後、同じjob/source_launch_idだけを再開し、明示的な履歴Workerを当該launchに限定して実行する。最終HTMLの独立review・修正・公開readback・正しい終端が実測されるまではFC-A06/A07と全体受入を達成扱いにしない。
