# 同一job resume17の報告review停滞とrenderer修正

記録日：2026-10-20 JST
対象：`job-historical-9e7f0c175cc4c874c955a5dab29c4427` / `historical-9e7f0c175cc4c874c955a5dab29c4427` / `2026-10-17` / `source_run_id=null`

## 観測

同じ10/17 jobのresume17で、report担当と独立report reviewerが同じformal attempt内で実起動された。attemptは`b25685a6-9567-4ee0-9487-319f6b2ee90d`。fallback HTMLのreview responseは14件あり、最後に確認したresponseも`changes_requested`で、5件の指摘があった。最新指摘は、HTML上のコードレビュー承認がsource snapshotの`not_started`と矛盾すること、調査JSONと未作成計画のリンク表示が実資料と合わないこと、未実施試験の理由コードやnull値が本文にあることを挙げていた。

resume17のworkerを止めた後、管理DBのjobは`running`、`resume_count=17`、`source_run_id=null`だった。中断の正確な秒は取得されていない。jobを新規作成せず、通常Dailyや10/19のqueue/historyを起動せず、元の日次runも作っていない。停止はその時点の不正確なHTMLを自動reviewし続けないための回復操作であり、同じjobを修正後に再開する。

## 原因

`automation/inago/maintenance/reporting.py`のrendererが、`implementation.review_status`を読まずに付録へ「承認済み（プロダクトコード）」を固定出力していた。また、停止理由nullを「なし」と表示し、調査/計画の版キーと固定リンク名を本文へ出していた。工程表は試験summaryと、`tested/pushed/active`のnullをそのまま表示していた。調査資料がJSON、計画が`not_created`でも、ひな形は「調査MD」「修正計画」へのリンクを必ず作っていた。これらは文章担当だけでは修正できないrenderer/ひな形の事実不一致であり、繰り返しreview指摘の直接原因だった。

別途、reviewに14回対応しても先へ進まない原因は前回のreport retry loop修正で解消した。resume17で各reviewer指摘後にreporterとreviewerが実際に再起動されているため、今回はreview回数制限ではなくHTML生成ルールを修正する。

## 修正と確認

`reporting.py`を修正し、工程別のreview状態をsource snapshotから日本語表示する。未作成資料にはリンクを付けず、資料形式に合う名前を表示する。試験未実施・本番未反映・翌日未確認を平易に説明し、停止理由の記録欠落は未確認と示す。本文に内部status/reason code/版/ID/hashが含まれる場合は参考資料の説明へ置き換え、工程tree・生の版とIDは折りたたみ式の技術付録へ移した。新しいreport statusは`complete`または`pending`に限定し、sourceに保存済みの過去値は監査記録のままにする。

同期先はレポートひな形、報告ルール、FC06受入契約、要件データ辞書・追跡表、要件正本HTML、報告skill/contract、reporter/reviewer role。requirements builderはversion 2.52で再生成した。

初回REDでは、blocked source statusがreport statusとして出力されることを確認した。修正後の`tests_inago/test_maintenance_report.py`は13件PASS、対象Python 2 fileは`py_compile` PASS、schema JSON parse PASS、`git diff --check` PASS、要件builder PASS（28章・39図・26参照資料）。製品コードの独立reviewと、本番適用、同じjobの再開、最終HTMLの実review・公開はこれから行う。現時点では完成と判定しない。

## 次の再開位置

この差分の独立製品reviewを実行し、指摘を修正してfocused確認を再実施する。その後、所有範囲を選択stageして現在branchへpushし、正式runtime switcherで適用する。同じjob/launch/null runをresume18として再開し、reporterとreviewerが生成・reviewした同一HTML版の公開、DB read-back、terminal状態を確認する。acceptance未達なら同じjobを再開し、別jobや外側agentのHTMLで代替しない。

## FC10 renderer follow-up（review指摘の修正）

独立製品reviewの最初のfollow-upでは、工程表の対象版欄が「参考付録を参照」としながら、source snapshotにある試験・本番反映・翌日確認の版を付録へ出していないMEDIUM指摘があった。付録へ各sourceの版・状態・観測日時を追加し、nullの本番版も「null」と技術付録内で明示した。2回目のfollow-upでは、報告担当が作った個別check行にも同じ参照表現がある一方、そのcheck固有の`version`が付録に無いMEDIUM指摘があった。個々の工程名と版をエスケープして付録に列挙する処理を追加し、source snapshotとは異なるcheck版の回帰assertを追加した。

最終focused実行は`uv run --with-requirements automation/inago/requirements-test.txt python -m pytest tests_inago/test_maintenance_report.py -q`で13件PASS（10.45秒）。JUnitは`FC10/fc10-renderer-round3-green.junit.xml`に保存し、XML読戻しは13 tests/0 failures。consoleは同回のpytest出力を`FC10/fc10-renderer-round3-green.console.txt`に保存した。`py_compile`成功、requirements builderはv2.52・28章・39図・26参照資料で成功、`git diff --check`はexit 0（既存のCRLF/LF警告のみ）。独立reviewは両MEDIUMを解消した現在sourceでAPPROVE/指摘0。対象manifest `FC10/renderer-resume17-review-target-r3.json` SHA-256は`3521c90087cf6d244578f812034d4cff5a0a63f71fa1ec40eaa064ea9af0a8b4`、結論は`FC10/renderer-resume17-review-r3.json/.md`。

検証操作の失敗も記録した。base Pythonにはpytestが無く、最初のpytest起動はtest collection前に失敗したため、repoの`requirements-test.txt`を確認してuv環境で再実行した。最初のrunner設定検索は存在しないroot-levelファイルを指定してexit 2となったため、実ファイル一覧からテスト依存ファイルを特定した。review target記録の初回生成ではPowerShell boolean表記を誤り一時的にJSON `null`を書いたが、正しい`$true`で再生成しmanifestとreview SHAの一致を再確認した。JUnit件数読取の最初のselectorは`testsuites`直下を見て空になったため、`//testsuite`で13/0を再確認した。詳細は`FC10/controller-read-operation-failures.json`。

この記録時点では、製品差分のcommit/push、official runtimeへの適用、同一job resume18、最終HTMLの独立report review・公開・terminal read-backは未実施である。元の10/17 jobがrunningのままとは直前のresume17 readbackで確認済みだが、次のresume前にDBをread-onlyで再確認する。FC-A06/A07を含む受入は未達のまま保持する。

版情報の参照契約も、報告ルール、report skill/contract、FC06受入契約、要件データ辞書、追跡表、reporter/reviewer roleへ同期した。要件正本HTMLは版2.53へ更新し、builderは28章・39図・26参照資料で再生成に成功した。報告role TOML 2件と今回参照したFC10 JSON 3件もparse成功した。追跡表への版注記追加でCRLF固定の検索位置が見つからず初回の操作は書込前に例外となった。line endingに依存しない位置検索で修正し、この失敗と回復はoperation failure journalへ記録した。
