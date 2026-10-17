# AT-OBS-01 失敗観測artifact契約

版：要件HTML 2.3／2026-10-16 JST。対象はTrendsの失敗attemptに付随する診断証拠であり、成功snapshotの代替ではない。

## 識別と保存

失敗観測artifactは`source_kind=daily`のprivate JSONとしてdata root配下の`artifacts/daily`へ原子的に保存する。`artifact_ref`は同じ`request_id`に対し再開をまたいで安定し、artifact本文とDB参照を冪等に照合する。`request_id`・`attempt_no`・`run_id`・`settings_version`・元の失敗codeを記録する。既存SQLite v5の`trends_acquisition_attempt.evidence_ref`と`artifact`表を使うため、schema migrationは追加しない。

artifact本文は`schema_version`、`kind=trends_failure_observation`、`artifact_ref`、request/run/settings識別子、attempt番号、許可された失敗code、source status、raw observationの状態、`recorded_at`、`retention_expires_at`、data root相対の`trace_ref`、`trace_state`、allowlist済みstage要約、process return codeだけを含める。検索語、商品/投稿本文、URL、DOM、応答body、資格情報、例外message、`snapshot_id`は含めない。raw observation writerは一つのUTC時刻を`recorded_at`とし、その時刻から90日後を期限にする。failure artifactも独自の`recorded_at`から期限を再計算し、source側で別々に採取された時刻の微小差を持ち込まない。再開時に既存JSONを読む場合も、top-level field集合・各fieldの型/列挙値・attempt識別・90日 timestamp・trace path・stage eventのfield集合を全て再検証する。余分なfieldや不正なnested fieldがあれば拒否し、`write_temp_response`へ渡さない。

## 確定順と障害時

1. failure observationを`write_temp_response`で一時fileへ書き、flush/fsync後にatomic linkしてartifact fileを確定する。
2. その後のSQLite transactionでartifact metadataと`trends_acquisition_attempt.evidence_ref`を同時登録する。
3. DB lock/crashでtransactionが成立しない場合、attemptはstartedのまま、完全なartifact fileを残す。再開は同じrequest/artifact_refを再利用して登録し、外部取得を繰り返さない。
4. raw source observationが存在しない場合は`source_observation_state=missing`とする最小failure recordを作る。これは取得snapshotや元画面が存在したとの主張ではない。
5. file保存失敗は`_evidence_capture.status=write_failed`と安全な固定codeで示し、元のattempt `error_code`・処理失敗を保つ。成功status、成功snapshot、fallback可否へ変換しない。
6. DB登録前にprocess/transactionが失敗したartifactは、90日までは同じrequestの再開登録用として保持する。保持期限を過ぎてもDB未登録の場合、retentionが`artifacts/daily/TFAIL-*.json`だけをroot内で検証し、artifact_refがDBに存在しない完全なfailure artifactを安全に削除する。期限前は削除しない。期限後の削除失敗はretention結果へ数え、日次retention logへ出力し、traceをdegradedにして日次の後続処理を停止する。
7. expired/missing/invalid参照をlive evidenceとして開かない。90日到達後は登録済みartifact metadataをexpiredへ確定してfileを削除し、attemptの`evidence_ref`は履歴として残す。private root外へのpath traversalは拒否する。

## N05受入

`tests_inago/test_daily_fix_artifacts.py::test_n05`は隔離SQLite/時計/fixtureで、DB lock中のfile-before-register、同じrequestからの回復、raw observation欠落、保存権限失敗、private path越境、90日境界を確認する。失敗attemptのdraft/予約は0件のまま、`snapshot_id`と`keyword_snapshot`も作らない。N05の実測結果は本計画MとT/R04へ記録する。

N05/N05b/N05c/N05dのPASSは凍結し再実行しない。N05bは既存artifactへ許可外field/不正なnested fieldを注入し、再開時に完全schema拒否されることを確認する。N05cはraw observation欠落かつDB transaction失敗で未登録fileを作り、保持期限前に残り期限後に安全削除されること、DB参照済みartifactやroot外fileへ触れないことを確認する。N05dはsource timestamp間の微小差を与え、failure artifactの期限を`recorded_at`から正規化してcaptureがavailableになることを確認する。N05eは期限切れの未登録artifact削除を意図的に失敗させ、件数がlog/degraded/日次停止へ伝播することを確認する。各nodeは独立fixtureで個別に実行する。
