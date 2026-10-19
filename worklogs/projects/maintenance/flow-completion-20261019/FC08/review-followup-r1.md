# FC08 独立製品コードレビュー追補 r1

## 判定

**approved**。前回の3指摘は対象版で解消されたことをソースとFC07の追加試験証拠から確認しました。レビュー対象版は `fc08-followup-20ccc11e1c6c04cb2fd443a62885e4e7f0bb5b14f9d13b70aba0e14de1f45401`、基準commitは `51cfc7503775a25af731c8f4bcf727c94f10a595`、計画の現行SHA-256は `c1d70b2c449f6d2fac5f1add8b184956c6a3dd281ba989dc3517db2dd9852b52` です。対象はmaintenance製品コードとschemaの 39 ファイルで、正確な各ファイルhashは `review-followup-r1.json` に記録しています。テストコード自体はreviewしていません。

## 前回指摘の確認

- **FC08-F1（High）解消** — 公開工程は公開記録を保存するだけです。job終端を登録するのは `workflow_reconciliation.reconcile_workflow_terminal` だけで、8工程の観測と引継ぎ、成果物、deployment結果、report、公開sidecar、公開先の読戻しを照合した後に登録します。incidentは `in_progress` のままです。FC07では公開直後の子プロセス中断から同じ合成jobを再開し、照合後の単独終端登録と公開先HTMLの読戻しを確認しています。handoff欠落・公開読戻し欠落のケースは終端完了になりません。
- **FC08-F2（Medium）解消** — invocation開始時にmaintenanceソースとschema群の指紋を一度取得し、同一の値を失敗記録と工程引継ぎへ渡します。変更したソースで指紋が変わること、取得失敗時にunknownと具体的理由を残して後続工程へ進むことをFC07のfocused caseで確認しました。
- **FC08-F3（Medium）解消** — reportは実際の `maintenance_report` sequence 4のattempt、deploymentはidentityの合う完了済み `maintenance_repair` sequence 3のattempt、該当attemptがない場合はnullに結び付きます。既存report attemptがある合成再開ケースでも誤結合しないことを確認しました。

## 検証証拠と範囲

FC07の追加結果 `fc08-f1-f3-final-results.json` を読み、同ファイルに記録された最終ソースhashを照合しました。9個の最終JUnitファイルは各testcase、failure、error、skipを直接確認し、全てPASSでfailure/error/skipはありません。詳細なnode、コマンド、JUnit hashはJSONにあります。前段の試行失敗もFC07記録に保持されています。

確認したworker除外は `WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})` のままです。レビュー担当はpytestを実行せず、本番DB、対象job、scheduler/worker、10/19のqueue/history、公開remoteを操作していません。FC07試験は一時SQLite・合成identity・ローカルbare remoteの範囲なので、この承認は本番同一jobの完遂や障害原因解決を意味しません。

次はFC09/FC10へ進めます。この結果は上記source manifestの版だけに対する判定です。
