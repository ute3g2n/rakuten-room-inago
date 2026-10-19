# FC11: 同一jobの承認済みreportを更新できない不具合

実行時刻: 2026-10-20 08:43 JST
作業root: `C:\project\rakuten-room-inago`
基準commit: `bc8f2a68ad5b96865993a68e2d3a020747966a44`
状態: **修正・focused検証・独立製品review済み。本番適用と同一job再開は未実施。計画未完了。**

## 起きたことと原因

resume23では、同じ10/17 jobのreport担当が新しいHTMLを作り、独立reviewerが指摘なしで承認しました。しかし公開先には同じjobが前回公開した承認済みr13があり、publish処理は置き換えを拒否しました。

前のコードは、同じjobのcontroller fallbackが置かれていた場合だけ更新対象として認めていました。今回の公開先は、report結果・review記録・publication sidecar・Git commit・HTML hashが同じattemptへ結び付いた承認済みreportでした。処理はその正当な前回reportを置換可能と認識できず、`report_publication_path_conflict`で公開を止めました。

対象は固定の同一job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、2026-10-17、`source_run_id=null`です。resume23で作られたr14は独立review `approved`、指摘0、HTML SHA-256 `af31348ef7607a642e2393e4efe4821ec9f22d585bdc63b48520217ceb4cd738`でした。前回r13のSHAは `2644b0118ac4d147c89b5de72ab9911b33b1f42b253f9f36593e2859f962b650`、publication commitは `e17cf65f2fcca5c14ad28eb51b033b8ad996bd9d`でした。

## 実施した変更

`automation/inago/maintenance/production_workflow.py`で、同一jobの前回結果・DB review record・publication sidecar・remoteの祖先commit・commit内HTML blobが一致する場合に限り、前回の承認済みreportまたはcontroller fallbackのhashを置換対象として渡すようにしました。publisher側の公開直前hash照合と一ファイルcommit条件は維持しています。

拒否時の候補attemptと理由は、failure-journalの深さ制限を通る浅い安全文字列リストとしてoperation contextに残します。reviewerから深さ制限で候補理由が消える指摘を受け、変更して再確認しました。

仕様は`AT-MAINT-01`、`AT-TRACE-01`、運用トレース一覧へ同期し、正本要件HTML builder v2.56を実行しました（28章、39図、26参照資料、4,200,630 bytes）。report HTMLひな形とreview対象4点の範囲は変更していません。schema/role/skill変更は不要でした。

## 検証と独立review

- 最終診断形へ変える直前に公開stageテストファイル全体を実行: **46 passed / exit 0**。この時点で置換ロジックは最終の承認済みreport分岐を含みます。
- 最終診断形の後、置換・診断深度確認nodeを再実行: **1 passed, 45 deselected / exit 0**。
- `py_compile`対象2ファイル: exit 0。
- `git diff --check`: exit 0。production fileにGit標準のCRLF/LF正規化警告あり。
- 独立製品review: `fc11_html_scope_product_review_r2`、**APPROVE / 指摘0**、対象source SHA-256 `02df5a197434cd659f905f2f8b617fccc87f4326f5d182001bd0e6bbc7daeead`。
- reviewerは前回HTMLのidentity/result/review/sidecar/hash/ancestry/blob結合と公開直前再照合が維持されていること、診断が深さ制限内にあることを確認しました。テストコードreviewは行っていません。

## 次に行うこと

task-owned差分だけを監査してcommit/pushし、公式runtime switcherでfixed rootに適用します。その後、同一10/17 job/source launchだけを再開して限定Workerを実行し、fresh report/review、同じbytesのHTML公開、DB publication sidecar、正しいterminal read-backを確認します。10/19 queue/historyは処理せず、10/19 dispatch除外、日次runを作らないこと、`source_run_id=null`を再確認します。

記録JSON: `publication-replacement-review-r1.json`
