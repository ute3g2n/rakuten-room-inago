# SC06 独立製品レビュー — 修正版フォローアップ

## 対象版と実行結果

- 対象job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target_date: `2026-10-17`
- source_run_id: `null`
- 固定workspace: `C:/project/rakuten-room-inago`
- 対象計画: 承認済みv1.0。実行追記後の現物hash `371f1c60efada495ae9c133d9a90c7b573433783410a9a0713953595af492f3b`。構成案v1.1 hash `0177ae92e0981eb1ab179b49d04739de9ffbfbc056daa3e7f7be52b79a28b3ec`。
- 製品review対象 `automation/inago/maintenance/skill_chain.py` SHA-256: `174a5d93d7e8638252ec224cd971796e546a4c5e5171df8fbaa9ff55fc18b4e7`。
- 併読した製品hash: `app_server.py` `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`; `cli.py` `f9831eacc3ee700e48bb010df4b2ec6a2ea3eea8a032406601bb0e79bc967cfe`; `worker.py` `e22344de5c15b0295695d60d901588a9b93214cc778cb08aab4db97d71f7de12`; `historical.py` `17a92daf2e83a3677b93c5595dda338eb94cf9c072c612d51471d441867bd4b6`。
- 試験記録の実装hashは上記対象hashと一致。test hash `d0418759c4cbc619ca61563f93cc49db52bfe077bd1f8cd9147a31d3982c5f05`。最終commandは `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py -q --basetemp %TEMP%/inago-sc05-20261020-1445 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit.xml`、20 collected / 20 passed / 0 failed / 0 skipped / 0 errors、exit 0。完全nodeとSCT対応は `SC05/node-list.json`。
- SCT06（日次pipeline実装試験）とSCT09（同job実動）はNOT_RUNのまま。今回の再レビューではそれらをPASSにしていない。旧FC01〜FC11 nodeは実行していない。
- 実行担当: SC06 independent follow-up reviewer。コードと試験記録を読み取り専用で確認。製品・試験・runtime job・DB・Daily・10/19履歴は変更していない。
- 総合結果: **changes_requested**。前回所見F1とF2は対象hashで解消を確認。以下F3を残す。これは後続工程のgateではない。

## 前回所見の再確認

### F1 — role-6 report復旧

解消を確認。現在の `run_skill_chain` はrole 6の主呼出しがtransport失敗、handoff不正、または担当出力/読取確認不足なら `_retry_report_author` を呼ぶ（`skill_chain.py:1331-1349`）。復旧turnは同一のrole/config/model/権限で新AppServerSessionを起動し、同じ担当者として部分成果を読み、`report.md` と`report.html`を作成するよう指示する（`:655-770`）。復旧失敗でも固定role loopはrole 7と8へ進む。SC05の3つの復旧成功parameterと3つの復旧失敗parameterが現行hashでPASS。未達時のterminal/publication失敗は成功扱いされていない。

### F2 — predecessor Markdown実読

解消を確認。現在は担当turnのAppServer eventから、完全一致の入力pathをPowerShell `Get-Content -LiteralPath` で読んだ `item/completed`、status `completed`、exitCode `0`を照合し、event内に与えたpath/hashを `previous_read_evidence` として保存する（`:554-617, 1275-1321`）。本文中hashの自己申告だけではread acknowledgementにならない。SC05のexact-path success/missing/failed/mismatched各parameterが現行hashでPASS。保存対象は縮約したpath/hash/tool-result metadataであり、AppServerの全eventやcommand本文、自由応答をledgerへ複写していない。

## 残る所見

### F3 — 中: 再開選択が作者修正・最終HTML再レビュー後のrole出力hash更新を認識しない

- 場所: `automation/inago/maintenance/skill_chain.py:1673-1710`、特に1701-1709。
- 根拠: `_resume_start_role` は各roleの初回 `role_call_finished.output_refs` と現在ファイルを `_event_outputs_match` で比較する。ところが正常なHTML修正・再レビューでは、role 6が所有する `report.md/report.html` を作者が更新し、role 7が所有する `html-review.md` を最終reviewerが更新する。終端照合はそれらの後続revision/rereview eventを使い、現行versionの一致を検査する（`:1831-1837, 1874-1898`）が、resume選択はその後続eventをrole 6/7の保存状態として扱わない。
- 具体的な影響: 8番目の公開だけが失敗し、role 6/7の初回turn、作者revision、最終rereviewが揃ったchainを再開する場合でも、作者またはreviewerが初回出力を置換していれば初回hash比較でrole 6相当の再作業へ戻る。role 7の初回成果hashが最終html-reviewと異なる場合も同様にrole 6へ戻る。公開だけの再試行で足りる状態でもreport/reviewの呼出しと成果再作成を繰り返し、前回最終版をarchiveして再生成する。
- 計画対応: SC-R04 / SC-A04、SC04の保存済み同job再開と最初の未完了担当から続ける要件、SCT05/SCT10。
- 修正依頼: resume判定でrole 6/7の初回出力だけを現行hashと比較せず、同chainの成功した `author_revision_finished` と `html_rereview_finished` の正確な出力refs、入力hash、review hashを照合して最終成果を再利用する。公開未達のみならrole 8から再開する。対応する隔離試験では作者とreviewerが初回fileを置換した後に公開だけが未達の状態をseedし、再呼出しがrole 6/7を再起動せずrole 8から続くこと、最終HTML/review bytesが保たれることを記録する。
- 重大度: Medium。後続処理は止まらないが、再開位置と同job最終成果の再利用が計画どおりでない。試験記録にこのspecific resume scenarioは含まれていない。

## 継続確認した要件

- 8 roleは固定順の1つの `run_skill_chain` loopで実行され、role failure後も残りroleの呼出しへ進む。role 6の復旧があってもrole 7と8への流れは維持する。
- source identityは固定入力と一致し、`source_run_id`をnullのまま保持する。worker callbackは固定launch/date/run-nullと既存jobを確認し、`WORKER_DISPATCH_EXCLUDED_DATES=frozenset({"2026-10-19"})`を保持する。
- HTMLレビュー後の作者修正と別reviewerの再読取はrole 8開始前に実行する。terminal照合は最終report hash、最終review hash/marker、作者修正event、再レビューevent、および公開対象のlocal bytesを確認する。
- traceの保存経路はresponse本文・例外本文・全AppServer eventをledgerへ持ち込まない。cause class/safe code、repository相対frame、限定したhash/path/turn情報を保存する。今回の再レビューではsecret/private valueの露出を確認しなかった。
- 新CLI routeはstore/controllerのlegacy構築より前に `skill-chain` を分岐し、worker routeはskill-chain providerへつなぐ。legacy controller/reconciliationの別経路は保持されているが、SC04記録の既存参照と区別され、新routeから到達しない設計を確認した。

## 引継ぎ

SC07反映担当は、対象versionとSC05最終20-node証拠を使い、F3を具体修正するか、実操作上の限界と再開位置を記録してください。changes_requestedとF3は反映・次工程・HTML公開を止める条件ではありません。F1/F2は上記版で解消済みなので、最初のreview recordは履歴として維持してください。
