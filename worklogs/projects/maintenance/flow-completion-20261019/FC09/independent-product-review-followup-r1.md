# FC09 独立製品コードレビュー追補 r1

## 判定

**approved**。前回のFC09-F1は対象版で解消されています。レビュー対象manifest SHA-256は `c50687d9ccf1ddbe7a7963b3ac16b008e44615d752e276034d196a1620bf1213`、基準commitは `51cfc7503775a25af731c8f4bcf727c94f10a595`、現行計画SHA-256は `7f306659f6d7d159fec563ba1aafaaf709901c31eb3fb2be719c904c7fa695eb` です。manifest記載の39ファイルすべてで期待hashと実hashが一致しました。hashごとの照合結果は `independent-product-review-followup-r1.json` に保存しています。

## FC09-F1の確認

identity/queue前提が一致しない場合、providerはjob参照を行わず、jobやattemptを更新しません。代わりにjob_id/incident_idをnullにした相関failure eventを記録し、誤ったqueue identityを持たない安全なcontextを作って、8工程すべての内容あるhandoffをunbound領域に保存します。handoffの将来stage statusはunknownで、operation結果は通常未実施として理由と次の行動を記します。configured report runnerも1回呼び出され、最後にworkflow outcomeはunknownとして返ります。したがってこの経路でfuture stage statusにblockedは使われません。

FC09実行結果のJUnitとconsoleを読み、記録hashとの一致、1 testcase、failure/error/skipなし、exit 0／1 passedを確認しました。JUnitのPASSは合成temporary SQLiteでのcallback・安全な未結合handoff確認です。テストコードは読みませんでした。

## 範囲と制限

migration 014/015とschema v15の前回照合結果は変わらず、今回のFC09-F1 follow-up source manifest内にも含まれます。今回の追加確認では、10/19 dispatch除外が WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"}) のまま保持され、除外判定がunbound/report処理より先に行われることを確認しました。

実際のreport runner callbackが起動することはコードと合成実行記録で確認しましたが、このinvalid-identity経路でAIによる実report生成や公開HTML作成まで成功したことは示していません。誤ったjobへ公開しないため、identityがないhandoffを正しい10/17 jobの公開とみなしません。本番DB、対象job、queue、scheduler、公開remoteは操作しておらず、pytestも再実行していません。次工程はこの制限を事実として引き継いでFC10/FC11へ進めます。
