# FC11 fallback・handoff修正とfocused検証

実行日: 2026-10-20 JST
基準HEAD: `abfa4482666c44d47cd2c2053c34edba9f251c2f`
作業root: `C:\project\rakuten-room-inago`
同一job受入は未完了。これはFC10同一job実行前の差分検証記録。

## 実施した修正

- 調査authorが通常応答に失敗しても、管理側fallback応答・contextが有効なら独立調査reviewerへ渡す。通常author操作の失敗はfailed handoffとして保持する。
- 管理側の調査fallbackに、原因を断定しない「原因判断可能な事実は確認できなかった」というunknown factを追加した。これにより後続plannerが、根本原因unknownの計装計画へ進める。
- 修正authorの一般例外を`repair_author_failure`へ保持し、後続のNOT_RUN結果で元の例外・同一identity/attempt・入力/出力参照・failure-journal観測・operation/correlation情報を引き継ぐ。
- 同じrepair attemptの統合handoffをversion付きJSONへ保存し、保存が失敗した場合は存在しない成果物pathを参照せず、保存失敗自体を相関付きerrors/journalに記録する。

## 独立製品review

`/root/fc11_html_scope_product_review_r2` がHEAD `abfa4482666c44d47cd2c2053c34edba9f251c2f`との差分を読取専用review。判定 **APPROVE、指摘0**。review対象 `production_workflow.py` SHA-256: `873cf684cd2254ef91c87b97b6ba0a2a61a07584f03802eb0ff4eafc7ae11b2e`。テストソースは読まず、review中のpytest実行なし。

以前の2指摘は解消済み。reviewerは追加の保存失敗境界も確認し、保存前に候補pathを出力参照へ置く点を指摘した。コードは保存失敗時にその候補だけ取り除き、既存の有効な参照は保つよう修正した。独立reviewerはその状態で再確認し、上記APPROVEを返した。

## 検証結果

対象node:

```text
tests_inago/test_maintenance_report_publication_stage.py::test_fc10_report_reviewer_prompt_limits_findings_to_requested_four_facts
tests_inago/test_maintenance_report_publication_stage.py::test_fc10_repair_setup_cause_survives_into_not_run_handoff
tests_inago/test_maintenance_report_publication_stage.py::test_fc11_deployment_candidate_failure_has_a_correlated_handoff
tests_inago/test_maintenance_report_publication_stage.py::test_fc10_planning_author_invalid_timeout_returns_unknown_with_invocation_handoff
tests_inago/test_maintenance_workflow_provider.py::test_daily_evidence_stage_persists_and_reuses_redacted_bundle
```

最終run: `.venv-inago/Scripts/python.exe -m pytest -q <上記5 node>`、exit 0、**5 passed / 7.25秒**。この試験には調査fallbackから独立reviewerを起動するケース、fallback後にplanner入力を準備するケース、repair handoff保存故障注入、四点review範囲が含まれる。

前回の検証試行は2回失敗した。1回目に空fact fallbackが後続plannerを止める欠陥が判明し、unknown factを追加して修正した。もう1回はテストが過去に正常保存済みのhandoff参照まで「未保存」と誤判定していた。今回のテストは今回失敗した一意pathだけを除外対象とし、同じ5 nodeで再実行して成功した。製品コード失敗を隠したものではなく、最初のplanner failureは実装修正で解消した。

`py_compile` はmaintenance production modules 6件で成功。`git diff --check` はexit 0、CRLF/LF正規化の警告のみ。`automation/inago/daily_pipeline.py`はGit差分なし。無関係な全suiteは実行していない。

## 次の再開位置

最新の計画・実装・試験・review・worklog差分を所有単位で監査し、task-owned fileだけを選択stage/commit/pushする。続いて公式fixed-root runtimeへ実commitをapply/read-backし、resume_count 22でcompleteながらplanning attemptがrunningのまま残った同じ10/17 jobを、明示source_launch_id付きWorkerで再開する。最終HTMLは訂正版4点reviewer promptで実起動・指摘修正・同一bytes公開・DB/terminal read-backを確認する。10/19除外とsource_run_id=nullを維持する。

## 仕様・運用資料同期と再確認

実施日: 2026-10-20 JST
確認対象版: branch `main` の未commit差分（production_workflow.pyは前記の独立review対象SHA `873cf684cd2254ef91c87b97b6ba0a2a61a07584f03802eb0ff4eafc7ae11b2e` のまま）

- `AT-MAINT-01_保守対応要件受入契約.md`、`要件データ辞書.md`、`日次タスクのトレース・ログ出力一覧.md`、`要件追跡表.md`へ、事実がない調査fallbackもunknown fact・入力参照・次の確認を残し、計画担当へ渡す契約を追加した。
- 要件HTMLのフロー正本 `tools/docs/inago-requirements/task-flows.mjs` のF12へ同じ説明を同期し、`node tools/docs/inago-requirements/build.mjs` でHTMLとF12図を再生成。出力はversion 2.56、28章、39図、26参照資料、4,197,142 bytesで正常終了。
- この動作はcontroller-owned fallbackでAI role指示の変更ではないため、role/skill変更は加えていない。既存のFC11 HTML review範囲のrole/skill同期は別節に記録済み。
- 6 maintenance moduleの `py_compile` は成功。対象5 focused node（調査fallback→独立reviewer→planner入力、fallback/handoff保存失敗、repair例外引継ぎ、四点HTML review prompt、deployment failure handoff）は **5 passed / 7.03秒 / exit 0**。
- `daily_pipeline.py`、HTMLひな形は変更していない。通常Daily、別job、source run、10/19 queue/historyは操作していない。
- pre-commit secrets scannerの初回実行はscript内の`os`未importで`NameError`になり、scan完了とは扱わなかった。Git/filesystemへの書込はなく、importを加えた同じscanを再実行してprivate key/token/bearer/email/profile pathのhigh-confidence patternは0件。
- 本番DB preflightの初回誤path `runtime/maintenance/state.sqlite` は `sqlite3.OperationalError: unable to open database file`、次の誤table `maintenance_step_attempt` は `no such table` でread-only失敗。公式apply readbackのruntime rootを使い直し、`sqlite_master`で実在する`step_attempt`を特定して回復。全read-only操作の履歴は `runtime-preflight-r1.json`。
- 正式DB `rakuten-room-inago/runtime/maintenance/state.sqlite` はschema16、`quick_check=ok`。job/launch/date/null runは指定値を維持し、resume_count 22、job status completeだがplanning attempt #11（`0809d9f7-36ce-4d5e-93cc-683484e2d383`）がrunning / `finished_at=null`。これは正しい終端ではないため完了扱いしない。target launchのworker queueは0件、10/19 worker queueは既存1件queued、watchdog queueは0件。10/19 queue/historyとDailyは未操作。

## 次の再開位置

すべての変更をownership単位で監査し、cached/uncached/untracked、secret scan、`git diff --check`を確認してtask-owned差分だけを選択stage/commit/pushする。そのHEADを公式fixed-root runtimeへapply/read-back後、対象job/source_launch_idだけをWorkerで再開し、fresh reporter/reviewer/revision/publication/DB terminalを確認する。完遂判定はまだ行わない。

## 選択stage前の所有監査

- branch `main`、HEAD `abfa4482666c44d47cd2c2053c34edba9f251c2f`、upstream `origin/main`、ahead/behind `0/0`、cached changes `0`。
- stage前の候補はtracked 25 filesとuntracked evidence 6 files。全件はFC03〜FC11のこの計画・実装・試験・review・同一job実測を所有根拠に確認した。`precommit-ownership-review.md`に各候補のworktree SHA-256とbytesを記録し、同manifest自身はhash一覧から外す。
- diff check exit 0。schema/role parseとfocused testsは上記記録の通り。raw resume22 consoleは未変更のまま候補に含み、追加行とuntracked evidenceに対するhigh-confidence secret scanは0 matches。完全一致や秘密値はログへ出力していない。
- 明示stage対象は上記31候補とownership manifestのみ。indexを確認してからcommitする。reviewed production source `production_workflow.py`のSHAは製品reviewと一致。
- stage後の最初の`git diff --cached --check`は、このworklogのMarkdown改行表記に使った末尾空白4行を指摘してexit 2。製品コードではなく文書書式の指摘であり、末尾空白を除去して意味を保った。再確認後にhash manifestを更新し、選択範囲を再照合してからcommitする。
- 選択ファイル整合assertの初回はownership表のhash表記を誤ってbacktick付きと仮定し、31件を読めずassertion errorとなった。読み取りのみでファイル・index変更なし。実際のplain hash列へparserを合わせ、32 staged filesと6 JSONを確認できた。
- staged full-file privacy scanはcanonical requirement HTML内のユーprofile path 1件とtests内の合成`example.invalid` address 5件を検出した。Git HEADのcanonical HTMLにも同じprofile referenceが1件あり、生成元の既存`worklogs/projects/requirements/notes/00_実行管理.md`は今回の所有差分ではない。builderが参照資料から再生成するためその歴史資料は編集せず、計画本文に今回追加されていたprofile pathだけを「global Codex AGENTS.md」へ置換した。合成addressは今回の追加行にはない。final staged added-line scanはprivate key/token/bearer/profile path/real emailすべて0件。
- plan内のprofile path除去後、canonical source builder v2.56を再実行し28章/39図/26参照資料/4,197,768 bytesで終了。canonical HTMLの既存referenceはGit HEADと同じ内容に保たれた。
