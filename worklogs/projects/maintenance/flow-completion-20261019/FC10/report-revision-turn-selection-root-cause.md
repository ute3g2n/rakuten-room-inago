# 同一job 10回目の報告revision追跡

- 調査時のactive/source commit: `52d03320a0e6cb5cc457491263f2c5c13b22ee2a`。同一job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、target `2026-10-17`、`source_run_id=null`。
- 10回目のworker invocation: `workflow-d8542485e61b49f788949830e4f2f570`。正式report attempt `00374287-2b70-43aa-a5ed-eedbd3e3a5a8`。
- 通常reporterのHTML生成は `report_evidence_ref_not_allowed` で失敗した。controller fallback HTML `fallback-e802a2ad014587235068` は独立reviewerが同一attempt/hashに対して7件の指摘を返した。
- reviewer responseからreporterを再起動し、修正版response envelopeが同じattemptに保存された。しかし`_persist_minimal_daily_report`はroleの全envelope件数が1であることを要求し、初稿を含む2件をまとめて判定した。failure journalの根本記録は `ReportContractError: report_revision_author_output_missing_or_mismatched`、捕捉箇所 `_persist_minimal_daily_report.author_revision`、参照はレビュー対象fallback HTMLとそのresponse sidecar。
- このため修正版は次のreviewへ渡らず、未reviewの別fallback hash `6bda528cfe579ffd9cf1d9a56e27286042964c1e988f743c13de01ebe96aa3a9` が `review_unavailable` で記録された。10回目jobは `workflow_terminal_observations_incomplete` のまま。
- FC10追補は、通常report revisionとfallback revisionの両方で、その呼出が返した`thread_id`と`turn_id`が一致する一つのenvelopeを選ぶ。過去turnを保持したまま、attempt/job identity検査は維持する。
- TDD赤実行: `report-revision-turn-selection-red.console.txt`。fallback revisionが失敗し、2回目reviewまで届かないことを実測した。
- 製品差分SHA-256: `743065fcad6bd2fcdf38c420162a330c4f40783b16027ec0e42170f325e97019`。test code SHA-256 (independent review対象外): `3c8c49d25641b01d9f4b5850d885d827e079e5b26a402e40b0343a6391d32ba8`。契約同期のhashと対象版はreview target manifestに保存。
- 次: 独立reviewを完了し、focused testとfailure trace red/greenを保存する。その後だけcommit/push・runtime apply・同じjobの次resumeを行う。

## 独立製品review r1 の追加指摘と修正

- 独立review r1 は上記v2 source hashを正確に読み、HIGH 1件を返した。`_daily_report_ai_envelopes()` 自体がファイル名順で検証済み応答を集めた後に `found[-1]` だけを返すため、呼出元のthread/turn filterは全候補を見ていなかった。以前の呼出元mockによるgreenはこのhelper動作を検証していない。
- owner mapへ編集前にscope extensionを追加した。helperは同一attempt内の全候補を返し、attempt/job/identity/role/config digestとresponse schemaを個別検証する。新規AI turnはthread_id+turn_idが一意一致したresponseのみを選ぶ。再開時のauthorはresult_version+created_at_jst、reviewerはreviewed_version+reviewed_sha256が一意一致したresponseのみを再利用する。候補なし/複数一致では新しい担当turnを起動する。
- 追加したhelper実ファイル読取テストは修正前にFAILし、辞書順で後の`turn-initial`だけが返ることを実測した。修正後helper単体+fallback revisionを2 passed。contracts/docsとincident-report skill契約を同期し、正本HTMLを公式builderで再生成した（v2.50、28章、39図、26参照、4,158,044 bytes、exit 0）。
- 本番コードの`py_compile`と`git diff --check` exit 0。通常の関連pytest 2 moduleは36 passed in 46.49s。初回OS `python -m pytest` はそのPython 3.12 interpreterにpytestがなく起動しなかったため、repo `.venv-inago` interpreterへ切替えて実行した。これは環境選択の失敗で、テスト結果には混ぜていない。
- 実行記録: `report-envelope-turn-selection.console.txt`, `report-envelope-turn-selection.junit.xml`, `report-envelope-requirements-builder.console.txt`。レビュー対象の新manifestは`report-envelope-turn-selection-review-target-r1.json`。実装SHA `5586e72ea61a57b7b79cccd22d214c70b29a0b0351f96222fc8eb58e7f33e35a`、test SHA（独立review対象外）`92db0c386a655bf6eb7d808c5d3805d71905c2919e9e39c592f3cfa271dd9f06`。contracts/HTML hashesはmanifestに記録した。
- 次: 新版manifestに対する独立製品reviewを行う。reviewで新しい指摘があればscopeを追記して修正・再試験・fresh reviewを行う。commit/apply/same-job resumeはfresh review後に進める。FC-A受入とjob完遂はまだ未達。

## 独立製品review r1 の追加2指摘と修正

- r1は指定manifest SHA `4f31d93c3c1194d9f627a88372e70138cc1e5789279b07b60c900e948c50710e` と3つの製品/契約hashを照合し、HIGH 2件を追加した。
- 指摘1: current wrapperはpath/hashで一つを特定していたが、同じresult_version+created_at_jstまたはreviewed_version+reviewed_sha256に一致する複数envelopeがあるかを確かめていなかった。wrapperが前の応答を参照すれば曖昧な同一入力応答を再利用できた。wrapper復元時も全候補の入力結合と一意性を先に検査するよう修正した。report author wrapper、reviewer wrapper双方に重複envelopeのfocused regressionを追加した。
- 指摘2: `run_daily_report_stage`は過去attemptの一致reviewer envelopeを`reviewer_raw`へ移してcurrent wrapper保存に渡していた。`_daily_report_save_ai_wrapper`はsame attemptを必須にするため、cross-attempt応答はその後必ず拒否される。過去attempt reviewer再利用を除去し、current attemptに一致応答がなければreviewerを実起動する。
- stage regressionでは同じHTML版/hashに一致するだけの過去attempt reviewer envelopeを用意し、current reviewer prompt・turnが呼ばれ、改訂・新レビュー・publicationまで到達することを確認した。wrapper曖昧性テストはコード変更前にFAILし、保存wrapperが古い1件を返すことを実測。修正後はwrapper両role、実ファイルreader、normal/fallback turn選択、過去attempt reviewer再起動を含むfocused 7 nodeがPASS。関連2 moduleは39 passed in 46.88s。
- 再実行: implementation/testの`py_compile` PASS、owner map JSON parse PASS、`git diff --check` exit 0（production_workflow.pyとowner mapにGitのCRLF→LF警告）。対象ログは`report-envelope-turn-selection-r2.console.txt/.junit.xml`。
- 次の独立製品review対象manifestは`report-envelope-turn-selection-review-target-r2.json`（SHA `149d48602b2e1ddb09d20a8f16511296bb3b04d989d7e0a85f45e2cbc9c0c49a`）。製品source SHA `ca7dd2e76dbda55074a5d20cbd12ab47f7c379b297a06cfe174fcb643d945236`、test SHA `1925cf2ca69be9c0e58a14b7fc114d67429a450e41b07eb029ef6dd8cf704f49`。r2 review完了、commit/push、runtime apply、same-job resumeは未実施で、FC-A/job completionは未達。

## 独立review r2 source_attempt_id指摘の修正

- r2（target manifest SHA `149d48602b2e1ddb09d20a8f16511296bb3b04d989d7e0a85f45e2cbc9c0c49a`）はHIGH 1件を報告した。current wrapperの`attempt_id`検査はあったが、`source_attempt_id`が現attemptと同じかを検査せず、一致する過去attempt envelopeを返せた。
- owner map `FC04/file-operation-owner-map.json`へ修正前にscope extensionを追記した。production loaderでenvelope読込前に`source_attempt_id == current_attempt_id`を要求し、AI参照pathは同じjob/attemptを継続して要求する。
- 新regressionは修正前にFAILし、過去attemptの一致wrapper応答が返ることを実測（console SHA `fe25de15c9bcb08764665cfa365c5b8ae21e2e7adbd0d95463da24fd11ad1c88`）。曖昧同一attempt caseとのfocused試験は修正後3 passed（console SHA `f8feed2046ccb4dc75badab9a64627a064e9a237bee83b87878d6696efec292c`）。
- 関連report 2-module pytestは40 passed in 47.80s。console SHA `a7c55344cac9a9261e1d3d2d92c4f1f36f8af25b94947342fd339e48f7a7c819`、JUnit SHA `79cf480e79ab5acc2a990a3c47017d1783dbe30841eb5325c1a74f3060aa5847`。`py_compile` 2 files、owner map/manifest JSON parse、`git diff --check` exit 0。Gitはproduction sourceとowner mapのCRLF/LF warning 2件を出したが whitespace errorなし。並行の初回static commandはPowerShell parse errorで確認を起動できず、改めた独立commandで全static checksを再実行しPASSした。
- r3独立製品reviewはmanifest SHA `1684966153e515cc5849bfd8f271410901d193284b74d9d2b1c7c0a1c9c7d4c7`、source SHA `29623f1e0c843dc26ed080ce5aea08e206d92ca510ef24ecd3710fc5f5709567`を完全照合してAPPROVED/指摘0。review artifact: `report-envelope-turn-selection-review-r3.md` SHA `10a1d736b3234cd41f0812409a2a2cde0f0e6dd3cdd94a608cf3eca49ae80ecb`、JSON SHA `a5a0de6f5e8e3e8fd4548e71ecdaac9140024c350aa90f557631fbe32fc16ff6`。test source hash `52488c264a04cd90ddfa619e0ed0331cad51baf31327ea5d138d0190cc55e2fe`はmanifestに保存し、reviewerは内容を見ていない。
- 次: task-owned変更をscope確認・secret scan・machine verification後にcommit/pushし、公式runtimeを最終版へ適用/read-backして、同一10/17 jobを次回resumeする。今の同一jobは未完了で、FC-A受入は達成していない。
