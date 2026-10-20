# SC06 独立製品レビュー

## 対象・実行

- 対象job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target_date: `2026-10-17`
- source_run_id: `null`（変更なし）
- 作業root: `C:/project/rakuten-room-inago`
- 対象計画: v1.0、実行追記を含む現物SHA-256 `371f1c60efada495ae9c133d9a90c7b573433783410a9a0713953595af492f3b`。承認済み原版の開始時SHA-256は `001b904b786c1dea6c95b54a307f4e2ace60358f850308e0566c164a9b7afaf5`。
- 構成案1.1 SHA-256 `0177ae92e0981eb1ab179b49d04739de9ffbfbc056daa3e7f7be52b79a28b3ec`、対応表 SHA-256 `643fe0b00d12a6a4057ca74c55feaaf6bc652ecbe3e335a24db3bf779c88e1ef`。
- 独立review担当: common-reviewに従い、このSC06の別review agent実行でコード・実行記録を読んだ。AppServerSessionの実測thread/session IDはこのレビュー入力に含まれず、記録していない。
- 結果: **changes_requested（改善所見あり。SC07以降の起動条件ではない）**。
- 書込: 本ファイルのみ。製品コード、テストコード、HTML、DB、Daily、10/19履歴は変更・実行していない。

## 対象版

| 製品ファイル | SHA-256 |
|---|---|
| `automation/inago/app_server.py` | `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71` |
| `automation/inago/maintenance/skill_chain.py` | `75e8815002d425c93ee4ea3cb541a000fb356ab8e122142a1bff26b557a58e89` |
| `automation/inago/maintenance/cli.py` | `f9831eacc3ee700e48bb010df4b2ec6a2ea3eea8a032406601bb0e79bc967cfe` |
| `automation/inago/maintenance/worker.py` | `e22344de5c15b0295695d60d901588a9b93214cc778cb08aab4db97d71f7de12` |
| `automation/inago/maintenance/historical.py` | `17a92daf2e83a3677b93c5595dda338eb94cf9c072c612d51471d441867bd4b6` |

## 試験実行記録の照合

SC05 `test.md`、`test.log`、`junit.xml`、`node-list.json`を照合した。新設テストファイルの記録hashは `5816b5ce38c3f51bd7b161207aa47f45bf549e00a6218c6f095657a38a77664d`。テストコード自体はレビューしていない。

- 実行コマンド: `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py -q --basetemp %TEMP%/inago-sc05-20261020-1411 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit.xml`
- 記録結果: exit 0、10 passed、0 failed、0 skipped、0 errors。JUnitは10 tests / 0 failures / 0 errors / 0 skipped。node-listの10完全nodeもSCT02/03/04/05/08/10/11/12/13/14の範囲で同じ結果を示す。
- test.mdは、先行する失敗した統合試験でpublication path問題を発見し、対象実装を修正した後に、修正版hash `75e881...` で影響nodeを含む最終10 nodeを再実行した、と記録している。古いhash `399dbc...` のFAILを新版PASSとして流用していない。最終JUnitとconsoleは記録上整合する。
- SCT06はNOT_RUN（日次pipeline向けの割当差分がまだなかったため）、SCT07は本レビュー工程、SCT09は同job実動工程としてNOT_RUN。これは未実施のまま扱う。計画の旧FC01〜FC11関連nodeはnode-listで明示的にNOT_RUN。SC05は合成一時環境であり、本番DB/Daily/queue/historyは対象外。

## 所見

### F1 — 高: reporter失敗時に同一作者の最小HTML再依頼が起動されない

- 場所: `automation/inago/maintenance/skill_chain.py:886-888` と `:1084-1103`。
- 根拠: role 6のAppServerSessionを `sessions` に保持するのは有効な3項目handoffを受け取った場合だけ。後段の `_revise_and_rereview_html` はrole 6 session、report.html、html-review.mdのどれかがないと `author_revision_skipped` を記録してreturnする。この関数はrole 7後の一度だけ呼ばれ、通常HTML生成失敗時の短い再依頼を起動しない。role 6が空応答、handoff不正、またはreport.htmlを書けなかった場合、role 7/8自体は呼ばれるが、公開対象HTMLがないまま終わる。
- 計画対応: SC-R04、SC-A04、SC04の失敗継続手順（計画行304: 通常report失敗時にLLM作者を短い依頼で起動し最小HTMLを作る）、SC06、SCT03/SCT04/SCT10。
- 修正依頼: role 6が通常HTMLを作れない／handoffが不正な場合にも、同じ作者のsessionまたは新しい独立role-6 sessionで確認済み事実だけの最小HTML作成を依頼し、その出力と未達を記録してからrole 7のレビュー、role 8の出版操作を実起動する。成功や承認を捏造せず、既存失敗の原因・再開位置を残す。影響試験はreporter空応答、handoff不正、HTML欠落のうち対象分岐を扱うSCT03/04/10。
- 重大度: High。失敗時の要求された復旧経路が欠け、同jobの最終HTML公開へ進めない。

### F2 — 中: predecessor read確認はファイル読取の証拠ではなく、出力中のhash引用で成立する

- 場所: `automation/inago/maintenance/skill_chain.py:950-970`。下流へ出す記録は `:991`付近の `previous_read_acknowledged`。
- 根拠: `read_ack` は入力のpath/hashを用意した後、担当者のMarkdownにそのSHA-256文字列があるかでtrueになる。これは入力値を引用したことは示すが、AppServer turnの実際のfile-read/tool eventを調べていないため、Markdownを開いて読んだ証拠にはならない。SC05のSCT02 PASSも合成role応答とhash引用の照合であり、実AppServerの読取eventではない。
- 計画対応: SC-R01/SC-A01、SC06、SCT02。SC08の「8担当の前担当Markdown実読記録」でも確認対象。
- 修正依頼: 実readを確認できるAppServer turn event/tool recordを保存・照合するか、少なくともフィールドを自己申告の確認として明示し、実読を証明する記録と誤認させない。素材hashだけの一致を実読確認と扱わない。次の運用で前担当ファイルを実際に開いた記録を残す。
- 重大度: Medium。呼出し順と入力hashは記録されるが、受入が求める実読証拠の強さは現状より弱い。

## 確認できた範囲・次工程へ渡す事実

- CLIの `skill-chain` 分岐はstore/controller構築の前にrunnerを呼ぶ。新worker callbackは割当launch/date/null-runと既存job manifestを照合してskill-chainへ渡し、10/19は明示除外を保持する。`WORKER_DISPATCH_EXCLUDED_DATES` は現物でも `frozenset({"2026-10-19"})`。
- role tableは8つの異なる計画見出しを順番に呼び、各roleに新AppServerSessionを構成する。reviewerとreporter sessionは別々に保持され、HTML作者修正後のreviewer再読取とrole 8開始順もコードにある。Fast要求はsubprocess config、model/effort/tier要求、runner限定best-effort logging/advisory probeが結線されている。
- event書込失敗、AI呼出し例外、handoff不正時に後続role loopは続き、safe failure note/fallback Markdownを試みる。記録本文にraw model response/例外messageは保存しない。SC05の合成テストは主要な継続・log失敗・scope境界の一部を確認する。
- 旧legacy modulesは新worker branchでimportされない一方、CLIの別legacy操作、probe、既存controller/reconciliation/testsには残る。SC04の現役参照表にこの保持理由があるので、今回の新経路からの隔離とlegacy利用を区別する。
- SC03/SC04にはSC05完了前のpreliminary記録が残り、そこにはrunner旧版hash `399dbc...`、未試験との記載がある。SC05最終記録が現行hash `75e881...`と実行結果を更新している。適用担当は最終記録だけを採用し、履歴記載は消さない。
- SC-A03はこのSC06対象diffでは成立を判定できない。SCT06がNOT_RUNで、daily_pipeline診断の新diff・実動ログ・適用後hashが未提供である。これは本レビューでは推測せず、SC08の同job実装とその別製品reviewへ渡す。10/17原因がunknownでも計装と公開は続ける。

## 引継ぎ

次担当はSC07反映担当。SC05の最終10-node実測とこのレビュー所見を読み、失敗復旧経路を改善した対象hash・再実行可能な範囲を記録し、reviewのstatusやfindingを後続のgateにせず、選択された今回所有ファイルだけを反映する。日次pipelineの同job差分は後半の割当で実装・試験・別reviewし、SC-A03の実物連鎖を追跡する。
