# SC06 独立製品レビュー — F4修正再確認 v3

## 対象版と証拠

- 対象job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- `source_launch_id`: `historical-9e7f0c175cc4c874c955a5dab29c4427`; `target_date`: `2026-10-17`; `source_run_id`: `null`。
- 固定workspace: `C:/project/rakuten-room-inago`。製品実装者と別のSC06 review担当として実施。
- review対象 `automation/inago/maintenance/skill_chain.py` SHA-256: `5b82597fa9f2621796397b4bc05ee52b0020259751a294769fd26362ac535447`。
- 版照合した試験コードSHA-256（試験コード自体はreview対象外）: `ac90004ce13a594d9fbac23a49220973e20bc7ac811f2ae32cd5aa08c2303d14`。
- SC05/F4追試 `test.md`, `test.log`, `junit.xml`, `node-list.txt`を実読した。記録の実行結果は25件成功、失敗・skip・error 0、整数exit 0。実行時刻は2026-10-20 14:32:02.8521034〜14:32:09.2238812 JST。JUnitもtests=25 / failures=0 / errors=0 / skipped=0。
- 計画SHA-256（追試記録が参照する版）: `ca8ae7acd1fb5d0a1a6b84303bb501f0881d172762c539471e79be7dc7ed3a30`。
- 試験記録は既存node `test_resume_after_final_review_reuses_author_and_reviewer_outputs`が、公開readbackだけの不一致でrole 8から再開しreport.htmlとhtml-review.mdを保持する経路と、revision後のreport.mdだけを変更した場合にrole 6へ戻る経路の両方を含むと説明する。これはsynthetic same-job fixtureでの選択・保持確認であり、live 10/17 jobの再開証拠ではない。
- このreviewでは製品・テスト・runtime job・DB・Daily・10/19履歴を変更・実行していない。FC01〜FC11は実行していない。
- Git観測: `main`, HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`, tracking `origin/main`。共有workspaceの他担当差分はread-onlyで維持した。

## 判定

**approved（このSC06製品review範囲）**。F4の作者Markdown最終hash欠落はこの版で解消している。求められたevent/ref・terminal/resume照合が一致し、F3のrole 8再開とreport.md変更によるrole 6再検証が両立する。F1/F2も維持される。SC05は計画指定focused試験の実測証拠として25/25成功だが、SC06 reviewはlive job完遂や未実施SCT06/SCT09を認定しない。findingなし。

## F4修正確認

- **作者revisionがreport.mdとreport.htmlの最終hashを記録:** `author_revision_finished`作成時、現物を再読して`output_refs`に両pathと各sha256（およびreport.mdのpresent）を入れる（`automation/inago/maintenance/skill_chain.py:1475-1505`）。
- **terminalとresumeがreport.mdの現物hashを確認:** `_revision_outputs_match`は現物report.md/report.htmlの両hashがrevision eventの各refに一致することを要求する（`:1772-1796`）。resume選択はrole 6の完了条件としてこれを使う（`:1716-1743`）。terminalも同関数を使い、作者revision未確認として扱う（`:1983-2013`）。
- **最終rereviewがreport.mdの入力hashを現物と照合:** `html_rereview_finished`はreport.mdをinput refに保存する（`:1548-1564`）。`_rereview_output_matches`は現物report.md、report.html、html-review.mdのhashを当該event refsと比較する（`:1799-1833`）。terminal側も現物report.mdとrereview input hashの一致を確認する（`:1984-2002`）。したがって、report.mdが変わるとrole 7のrereviewとterminal整合が失われる。
- **F3再開との両立:** report.mdとreport.htmlがrevision eventに一致し、html-review.mdとその入力がrereview eventに一致すれば、role 6/7成果を再実行せずterminalの公開バイト不一致に到達しrole 8から再開できる。report.mdだけが改変されればrole 6 revision整合が落ち、resumeはrole 6を選ぶ（`:1721-1759`）。SC05記録の2経路の期待はこの実装と整合する。

## F1 / F2再確認と範囲

- **F1:** role 6の初回呼出しでtransport/handoff/成果/read条件を満たさない場合、`_retry_report_author`を実行する（`:1331-1349`, `:655-770`）。復旧失敗でも固定8担当の後続roleは続く。SC05/F4の25件にはreporter復旧の成功3/失敗3のparameterが含まれ、現行実装hashに結び付いている。
- **F2:** `_successful_predecessor_reads`はAppServer turn eventの`item/completed`、`commandExecution`、status completed、exitCode 0、完全一致するquoted `Get-Content -LiteralPath` pathを照合し、縮約したpath/hash/method/status/item metadataを残す（`:578-650`, `:1275-1321`）。本文hashの自己申告を実読に置き換えない。SC05のsuccess/missing/failed/mismatched各実測を版照合した。
- **プライバシー/副作用:** ledgerのfield allowlistとresponse/error要約を確認し、自由な応答・例外本文・SQL値・token/cookie・商品/投稿情報・個人情報をtraceへ出す実装は見つからなかった。固定10/17 identityと10/19除外、通常Daily/旧legacy routeとの分離に今回のF4修正が干渉する変更も確認しなかった。

## 引継ぎ

SC07および後続担当はこのapproved結果とSC05/F4追試証拠を入力として続行できる。これはコードとfocused testのレビューであり、同一jobでの実動・反映・公開・workflow完遂の代替証拠ではない。review状態や未実施試験を後続開始条件にしない。
