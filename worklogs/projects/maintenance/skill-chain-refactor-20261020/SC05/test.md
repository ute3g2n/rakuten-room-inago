# SC05 試験記録

## 対象と版

- 固定job identity: `job-historical-9e7f0c175cc4c874c955a5dab29c4427` / `historical-9e7f0c175cc4c874c955a5dab29c4427` / `2026-10-17` / `source_run_id=null`
- Workspace: `C:\project\rakuten-room-inago`。branch `main`、HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`、tracking `origin/main`。
- 実行計画ファイル（実行記録追記後）SHA-256: `371f1c60efada495ae9c133d9a90c7b573433783410a9a0713953595af492f3b`。構成案1.1 SHA-256: `0177ae92e0981eb1ab179b49d04739de9ffbfbc056daa3e7f7be52b79a28b3ec`。対応表 SHA-256: `643fe0b00d12a6a4057ca74c55feaaf6bc652ecbe3e335a24db3bf779c88e1ef`。
- 試験コード `tests_inago/test_maintenance_skill_chain.py` SHA-256: `5816b5ce38c3f51bd7b161207aa47f45bf549e00a6218c6f095657a38a77664d`。
- 最終実行対象 `automation/inago/maintenance/skill_chain.py` SHA-256: `75e8815002d425c93ee4ea3cb541a000fb356ab8e122142a1bff26b557a58e89`。`automation/inago/app_server.py`: `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`。`automation/inago/maintenance/worker.py`: `e22344de5c15b0295695d60d901588a9b93214cc778cb08aab4db97d71f7de12`。
- 実行時要件HTML `docs/specifications/inago/イナゴスキル要件定義書.html` SHA-256: `0f265d690635770e8483c5281749ed06684526dbccab011e0f90db61d98a3ffa`。
- 同job runtime incident.md は存在するが、このSC05は再現性のため合成tmp jobを作成し、実jobやその履歴は読書きしていない。

## 実行記録

最小collectionは2026-10-20 14:10:49.1041172–14:10:49.8241235 JST（05:10:49–05:10:49 UTC相当）、exit 0、10 node収集。完全node IDは `node-list.json` および `node-list.txt` に保存した。

最終focused実行は次のとおり。開始2026-10-20 14:10:34.1516847 JST（05:10:34.1516847 UTC相当）、終了14:10:37.8589134 JST（05:10:37.8589134 UTC相当）、整数exit 0。

```text
.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py -q --basetemp %TEMP%/inago-sc05-20261020-1411 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit.xml
```

実結果: `10 passed in 2.97s`。JUnitはtests=10 / failures=0 / errors=0 / skipped=0。期待は10件の合成runner/scope/docs/local-Git確認が通り、実測も一致した。

通常pytest起動時の一時入力は既存job identityのincident.md 1件、call ledger 0件、workflow成果0件。成功chain終了時は成果8点（7 Markdown + report.html）とevent ledgerが作られた。failure/resume parameterでは同じ一時jobディレクトリを再利用した。SQLite/databaseの読み書きは0件。Local bare Git fixtureは2個を一時領域に作り、外部remote/networkを使っていない。後片付けはpytestのattempt-specific basetempのみ。永続した実行証拠はこのSC05配下。

## Planned case結果

- **SCT01 — PASS**: 8用途/role config/計画全文promptの一致と生成要件HTML 2.59 / F12.svgの存在を、新設nodeで照合。
- **SCT02 — PASS**: 8担当の呼出順、各前担当Markdownの相対path・hashを次入力で実読した記録、各担当の出力Markdown内の同じhashを確認。reporter/reviewerは別session instance。
- **SCT03 — PASS**: 1担当目の失敗、別担当の失敗を合成し、残り全8 call、controller fallback、内容ある最終引継ぎの継続を確認。raw履歴や失敗を成功扱いしていない。
- **SCT04 — PASS（範囲限定）**: transport exceptionと不正handoffを注入し、担当継続・format-only retry・安全な例外classの記録・自由な例外文の非記録を確認。AppServerのbest-effort logger OSErrorとadvisory probe失敗も別nodeで確認。その他の各log点別OSErrorは未試験。
- **SCT05 — PASS（runner範囲）**: 失敗した同じtmp jobを再呼出しし、role 1から同じjobで続行、source_run_id null、新job 0を確認。production SQLite上の過去attempt/pin履歴は一切読まないので、旧attempt履歴そのものの再構成は未試験。
- **SCT06 — NOT_RUN**: tester開始時にdaily_pipelineの割当差分が無く、合成失敗ケースが実行可能な製品実装の対象版が未着だったため。日次DB・Daily・draftには接続していない。daily差分が作成されたら同job担当の実差分に対する専用caseを追加/実行すること。
- **SCT07 — NOT_RUN**: 製品コードの独立レビューはSC06の担当範囲。試験担当からreview verdictを捏造しない。
- **SCT08 — PASS（入口設定の合成確認）**: production workflow factoryの10/17選択と10/19早期除外、legacy controller/reconciliation参照の分離を確認。runnerがAppServerSessionへhigh要求・advisory probe・best-effort loggingを指定する範囲を確認。実AppServerやproduction DBは起動していない。
- **SCT09 — NOT_RUN**: 同じ10/17 jobの実動はpytest caseではなくSC08/09の操作。testerがsynthetic resultを実job evidenceに代用しない。
- **SCT10 — PASS（合成bytes照合）**: author revisionの後にreviewerの再読取が行われ、公開入力のreport.html/review SHAが最終版に一致、公開先readbackとreport bytesが同じことをterminalで確認。
- **SCT11 — PASS（否定系）**: agent失敗時にreport/publish artifactが存在してもterminalはincompleteのままであることを確認。実job incident解決の判定はこのnodeの対象外。
- **SCT12 — PASS（ローカルGit、2 parameter）**: 一時bare remoteで task.txt のみ選択stage/commitし、user.txtの差分は未stageのまま保持。成功pushはremote blobと一致。到達不可remoteはpush失敗でもlocal commitとuser差分を保持。hook拒否parameterと製品delivery実操作は未実施。
- **SCT13 — PASS**: 固定除外集合が `frozenset({"2026-10-19"})` であること、および10/19 callbackではstore lookupに進まず、10/17側でも割当identityだけ照合することを合成storeで確認。実10/19 DBは読書きしていない。
- **SCT14 — PASS（同期の限定確認）**: 要件HTML 2.59/F12と8用途/role list・全文promptが合っていること、legacy controller/reconciliationが新route内に現れないことを確認。全仕様・全role本文の意味監査ではない。

旧FC01〜FC11に属するnodeは実行していない。計画中の旧FC参照nodeも全てNOT_RUNとして保持し、代替PASSに使っていない。SCT09の実job操作は後続工程、SCT07の独立レビューはSC06で行う。

## 途中失敗と修正記録

最初の2回はtemp fixtureに計画ファイルを用意していなかったためplan prompt読込で失敗し、test fixtureへその原典をコピーする処理を追加した。その後の統合実行で、fixtureが前担当hashを出力しない/次担当handoffの`担当`値がrunner期待値と合わないという試験fixtureの欠陥を直し、影響nodeだけを再実行した。これらは製品FAILではない。

統合nodeはその後、当時の実装hash `399dbc49fdd19decf1b8029ce7ef784d5d6dc0b9b9c3d1204cbf20e09e3b881c` で、terminalがrole-8のrepository-relative `input_refs[].path`をbasenameのみと比較する製品不具合を検出し、`skill_chain_publication_inputs_stale`を返した。親の実装担当が同じpath表現へ正規化して比較する修正を加えた。修正版 `skill_chain.py` SHA-256 `75e8815002d425c93ee4ea3cb541a000fb356ab8e122142a1bff26b557a58e89` に対し、影響統合nodeを含む最終10 nodeを再実行しPASSした。したがってSC04のpre-test checkpoint hash `399dbc49...` は対象版として superseded され、上記75e881...の実測だけを最終結果に使う。

途中runで作成されたfailure logs/JUnitもSC05に保持し、最終正式結果は `test.log` と `junit.xml`。各runの安全なconsole/JUnitは秘密値を含まず、最終consoleには例外自由文を出していない。

## 実施範囲と次担当への引継ぎ

この結果は合成tmp job/ローカルbare Gitのpytest実測であり、production DB、10/19 queue/history、Daily、同一job実動、daily差分のSCT06、独立製品review、適用、公開を証明しない。残す実装所見はrole-8 publication input path照合不具合で、親が修正し、現在版で再試験済み。SCT06はdaily_pipeline実差分ができた段階でtesterが同一job日次診断ケースを実行する。

次工程: SC06 の独立製品review。入力: 実装版 `automation/inago/maintenance/skill_chain.py` SHA `75e881...` とその他SC03/04製品diff、SC05 `test.md`/`test.log`/`junit.xml`/`node-list.json`。依頼: 対象版と試験証拠を読んで独立製品レビューを実施し、SCT06未実施と上記限定範囲を保持する。

## 2026-10-20 recovery / exact-read regression rerun (current pre-fix implementation)

SC06 requested a full focused rerun after the regression tests were added. The exact collection command was `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py --collect-only -q` (20 nodes, exit 0; output in `node-list.txt`). Focused run:

```text
.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py -q --basetemp %TEMP%/inago-sc05-20261020-1424 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit.xml
```

Started `2026-10-20T14:21:41.4876438+09:00`, ended `2026-10-20T14:21:47.3815919+09:00`, integer exit `1`. At launch and completion, `automation/inago/maintenance/skill_chain.py` SHA-256 was `d726a966883605cab44b10eb2926bffcf976656ded05e09c7706c6bff77d961f`; `tests_inago/test_maintenance_skill_chain.py` SHA-256 was `294f39fea0fbd147b238fd824fe508ca5c4aabeda85cb67d0d536e3020129056`. HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`, branch `main`, upstream `origin/main`.

Actual result: 20 collected; 16 passed, 4 failed, 0 skipped, 0 errors. The four failures are:

- `test_eight_roles_receive_exact_markdown`: role-8 review output Markdown did not contain the predecessor report SHA text, although the runner recorded an input ref to the report with matching SHA. This checks direct read acknowledgment plus recorded hash in each successor Markdown; investigate whether the role prompt/output contract requires that recorded citation.
- `test_reporter_failure_gets_same_author_recovery_then_later_roles[success-transport_failure]`, `[success-invalid_handoff]`, and `[success-omit_report_html]`: the role-6 recovery wrote the synthetic recovery report and roles 7/8 ran, but the terminal result remained `incomplete` instead of `complete`. This is a product behavior defect unless the fixture's successful recovery handoff/output is invalid; inspect the synthetic event and completion predicate.

All six recovery-failure variants passed by preserving incompletion and launching roles 7/8. Four exact-path event cases passed: only successful `item/completed` for the exact predecessor path acknowledged; missing, failed, and mismatched reads did not. The other ten baseline nodes also passed. No persistent runtime job, production/daily database, Daily, drafts, queue/history, or external service was read/written; test data and local bare Git fixtures were temporary under the attempt-specific basetemp. Detailed console and JUnit are `test.log` and `junit.xml`; complete node/results and exact hashes are `node-list.json`/`node-list.txt`.

This run supersedes the prior 10-node pass for the updated test/product versions; it does not erase the prior log/JUnit. Do not treat this failure as a stage gate. Re-run the impacted nodes after the implementation changes and refresh this record with that new version.

## 2026-10-20 final focused rerun after SC06 fixes

A first post-change full run against product `ff4017badc5b778914cc4021aa605140ddf51ad68c5b5d33d01e8ffd171c6964` exposed (a) that the role-7 output was legitimately rewritten after its initial read and the assertion needed to use immutable event evidence, (b) terminal treated a successful role-6 initial read as missing whenever no retry ran, and (c) the synthetic failure-recovery fixture accidentally fell through and wrote a successful report. The role-7 check now verifies the exact predecessor path and SHA in that role's immutable `previous_read_evidence`; the failure fixture now returns an invalid handoff with no outputs; the implementation owner fixed role-6 terminal read evaluation. The earlier failed result was retained in `pytest-sc06-terminal-fix-initial.log` and `junit-sc06-terminal-fix-initial.xml`; the preceding 20/20 run on the implementation hash `9f746ec56d1ed6c00bef4b9d160c092ca272ff5fcdacbf1a65243c6b18700994` is also retained separately. Since product bytes changed after that pass, a further full focused run was performed on the now-current hash.

Current minimal collection: `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py --collect-only -q`, started `2026-10-20T14:24:36.6291659+09:00`, ended `2026-10-20T14:24:37.4564356+09:00`, exit `0`, 20 exact nodes in `node-list.txt`.

Final run command:

```text
.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py -q --basetemp %TEMP%/inago-sc05-20261020-1445 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit.xml
```

Started `2026-10-20T14:24:46.1836361+09:00`; ended `2026-10-20T14:24:51.8294710+09:00`; integer exit `0`. Product `automation/inago/maintenance/skill_chain.py` SHA-256 `174a5d93d7e8638252ec224cd971796e546a4c5e5171df8fbaa9ff55fc18b4e7`; test `tests_inago/test_maintenance_skill_chain.py` SHA-256 `d0418759c4cbc619ca61563f93cc49db52bfe077bd1f8cd9147a31d3982c5f05`; HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`, branch `main`, tracking `origin/main`. Result `20 passed in 4.98s`; JUnit tests=20, failures=0, errors=0, skipped=0. The complete exact node IDs and per-node SCT mapping are in `node-list.json` and `node-list.txt`. The same synthetic-only scope applies: no persistent runtime job artifacts, database, Daily, credentials, queue/history, drafts, or 10/19 data were read/written. The temp fixtures were under the stated attempt-specific basetemp.

This final run supersedes the earlier pass/fail observations for the current SC05 test/code version. It does not claim SCT06 daily diagnostic implementation, SCT07 independent product review, SCT09 same-job operation, deployment, or publication; those remain assigned to later steps.
\n\n## 2026-10-20 planned existing SCT04/SCT08 nodes\n\nSC05 also ran the four explicitly mapped existing local synthetic nodes listed in plan §10, without running any FC node. Exact command:\n\n```text\n.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes tests_inago/test_maintenance_ai.py::test_c26_app_server_checks_model_and_effort_before_thread_start tests_inago/test_maintenance_ai.py::test_c24_app_server_passes_role_instructions_and_settings_to_thread_start tests_inago/test_maintenance_ai.py::test_c27_app_server_log_is_confined_to_host_owned_root -q --basetemp %TEMP%/inago-sc05-sct04-08-20261020-1510 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-sct04-08-existing.xml\n```\n\nStarted `2026-10-20T14:26:15.6646778+09:00`, ended `2026-10-20T14:26:16.7602836+09:00`, exit `0`; 4 passed in 0.39s. HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`, branch `main`, tracking `origin/main`. Plan SHA-256 `ca8ae7acd1fb5d0a1a6b84303bb501f0881d172762c539471e79be7dc7ed3a30`; runner SHA-256 `174a5d93d7e8638252ec224cd971796e546a4c5e5171df8fbaa9ff55fc18b4e7`; `automation/inago/app_server.py` SHA-256 `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`; `tests_inago/test_maintenance_ai.py` SHA-256 `f3c97ae9a65707953113b7b96731251394d146d18e4d48c32310546837d41a26`; main SC05 test SHA-256 `d0418759c4cbc619ca61563f93cc49db52bfe077bd1f8cd9147a31d3982c5f05`.\n\n- SCT04: `test_c26_transport_log_failure_still_interrupts_and_closes` — PASS; timeout/transport error is preserved while the AppServer interrupt and close cleanup both execute even when the failure logger raises.\n- SCT08: `test_c26_app_server_checks_model_and_effort_before_thread_start` — PASS; paginated model catalog is read and an unsupported requested effort is rejected before the thread-start call.\n- SCT08: `test_c24_app_server_passes_role_instructions_and_settings_to_thread_start` — PASS; role instructions, model, service tier, and sandbox settings reach thread/start.\n- SCT07/SCT08 coverage only: `test_c27_app_server_log_is_confined_to_host_owned_root` — PASS; a log under the host root is written, while an out-of-root location is rejected without creating that file. This is a focused test result, not the independent product-review verdict for SCT07.\n\nExpected and actual matched for all four nodes. JUnit: `junit-sct04-08-existing.xml`; safe console: `pytest-sct04-08-existing.log`. No DB/Daily/credentials/posts, persistent runtime data, 10/19 queue/history, or remote was touched; fixture data was temporary under the attempt-specific basetemp. The plan-linked FC11 and FC07 nodes remain NOT_RUN because the user expressly prohibited rerunning FC01–FC11. The same-job daily diagnostic SCT06 also remains unrun until its assigned daily product diff exists.\n
