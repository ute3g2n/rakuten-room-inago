# Step 19 切替後の最終確認と復元訓練

実施日: 2026-09-25 JST。判定: **success**。全体の結論と60 ACの個別表は `final-acceptance-report.md` を参照。

旧root入口と旧resourcesを除いたactive treeには新9スキルのみ。quick_validate 9/9、Python 165 passed、Node 55 passed、Schema/config 37 passed、Playwright dashboard E2E 13 passed、secret scan 0 findings。手動Daily dry-runはpartial/2（在庫不足、errors 0）、手動成果分析dry-runはsuccess/0。`show_status.py --latest` と新スキルroutingに沿うchat相当status pathは成功。

新Dailyタスクを10:03にオンデマンド実行。run_id `20260925-100355-daily-a5b44e`、source=scheduler、全工程完了、許容partial/2、errors 0、latest-daily-status一致。初回本番run `20260925-093328-daily-fca1b4` とも対象日2026-09-25。修正版HTMLの対象日セクションは1件、予定0件、理由2件。未確認商品による穴埋めなし。健康判定は13:00前でscheduled、errors 0。

復元scriptのWhatIfは16ファイルの復元元・先とタスク切替順を出力して成功。隔離tempへの復元は16ファイル、189,791バイト、相対パス・サイズ・ハッシュ一致。本番active treeへは戻していない。旧task Disabledかつ存在、新Daily/HealthCheck Ready。新Daily次回13:00、HealthCheck次回14:00。

Step 00–18は全件successかつreportあり。AC 60/60 PASS、full-path 42/42 PASS、未解決重大所見0。物理スリープ復帰と13:00初回予定実行は時刻未到来のため未実測であり、運用観測事項として最終報告に明記した。


## 追補: 在庫判定の抽出修正

2026-09-25 22:02:48 +09:00、P13の商品ページが <meta itemprop="availability" content="http://schema.org/InStock"> を持つことを確認した。従来の取得コードは同要素の href だけを読み、content を見落としていた。scripts/verify-product-pages.mjs は content、href、要素テキストの順に読むよう修正した。ここに記載したテスト実績はこの追補より前の実行であり、修正後の再実行はまだ行っていない。


## 在庫判定修正後の15商品再確認

2026-09-25 22時台JST、13:00の実行で確認した同じ15 URLを修正後のPlaywright取得処理で再確認した。ページ取得は15/15件成功（取得不可0）。`itemprop=availability` の `content` / `href` / テキストから14件が `in_stock`、1件（P28）が `out_of_stock`。P13も `in_stock` と取得でき、ユーザー提示画面と一致。これは在庫あり/なしの状態で、正確な数量はこの抽出では取得していない。詳細は `availability-recheck-summary-step19.json` と `availability-recheck-step19.json`。この修正後の再確認は実ページ取得であり、Python/Node全テストは再実行していない。
