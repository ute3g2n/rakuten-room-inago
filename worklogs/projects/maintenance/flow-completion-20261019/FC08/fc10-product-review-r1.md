# FC10 製品コード独立レビュー r1

- 結果: **changes_requested**
- 対象: `main` HEAD `eb16f9ec8fc145a0884ad9ce78c80f7d4a098df3` に対する指定5ファイルの作業差分
- 基準commit: `51cfc7503775a25af731c8f4bcf727c94f10a595`
- 固定workspace: `C:/project/rakuten-room-inago`
- 対象identity: job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、date `2026-10-17`、source_run_id `null`
- 計画1.2現行SHA-256: `135a687bbb585f587a5a00a827cdbdbe7dc218b8d2cf6f5b447534feefe9394e`

## 対象hash照合

指定された5ファイルは、依頼で示されたSHA-256とすべて一致しました。全値は隣接するJSON記録に保存しています。10/19のworker除外値も `frozenset({"2026-10-19"})` のままでした。

## 所見

1. **High — planning runnerが未来のstage結果として`blocked`を返す** (`production_workflow.py:4491-4521`, `run_daily_planning_author_stage`)。provider wrapperではunknownへ変換されますが、runner自身はblockedを返し、直接呼ぶ経路に正規化がありません。FC-A05契約と揃える必要があります。
2. **High — handoff内容が現在invocationへ結合されていない** (`workflow_reconciliation.py:49-92`, `_durable_stage_handoffs`)。current invocation配下のpathを読むだけでpayloadのinvocation identityを検査しません。handoff schemaと生成関数にもその値がありません。過去invocation由来のコピーをcurrentとして誤認できる余地があります。FC-A03/A07。
3. **High — deployment終端読戻しが内容を検証しない** (`workflow_reconciliation.py:516-547`, `_attempt_and_deployment_outputs`)。deployment JSONのstatus enum、release/commitとの整合、保存済hashや正式deployment recordを照合せず、複数フィールドが文字列ならterminal proofにします。FC-A07/A08。
4. **Medium — failure journal自身のwrite failureが永続記録されない** (`failure_journal.py:268-304`, `append_failure_event`)。stderr固定markerだけで元操作と原因を再生できず、独立fallback/recoveryを欠いています。FC-A02。

## 確認範囲と限界

strict transport schemaは送信前に別copyへ変換され、元のschema validatorはresponse検証に保持されています。providerの生messageはcauseとして保存せず、短いtype/code/paramだけを安全化して記録します。report attemptの再利用時はstoreの正式lease validatorを呼んでいました。planning fallbackはmanifest identity由来contextで通常保存を試み、repair NOT_RUNは捕捉した元例外と成果物保存を維持していました。10/19除外定数は一致しています。

レビュー担当はpytest・本番DB/job・公開remoteを操作していません。FC09のidentity precondition一件のsynthetic SQLite PASS証拠は履歴として読みましたが、FC10証拠ではありません。FC10 evidence directoryはレビュー時点で存在しませんでした。対象hash、plan hash、証拠hash、finding構造は `fc10-product-review-r1.json` に記録しています。`settings/language.md`はrepo内に見つからず、記録は日本語です。