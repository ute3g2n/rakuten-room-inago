# Google Trends取得の安定化（2026-10-22）

## 対象と結論の範囲

本人依頼：今日の日次でTrends取得が連続失敗して元テーマへフォールバックした原因を調べ、複数回続けて取得できるように修正する。複合テーマを構成語へ分ける変更も本人承認済み。

固定rootは `C:\project\rakuten-room-inago`。開始版は `1472a22`、追跡先は `main` → `origin/main`。開始時の既存未追跡 `rakuten-room-inago/非公開作業データ/` は対象外。

## 調査で確認できたこと

- 昨日の本番アダプターの「スキンケア」成功は20件・約24.2秒の単発確認。ユーザーChromeの新UIでの成功と専用profileの結果を同一条件として扱わない。昨日の本番アダプター保存証拠は従来UIへのfallbackで成功していた。
- 今日の日次では「下着・靴下」「調味料・加工食品・惣菜」「ペットケア・用品」「靴・履物」が各5回失敗。タイムアウトとHTTP 429を区別する。
- 修正前の本番アダプターで「スキンケア」は23.77秒で20件取得できた。同じ入口で「下着・靴下」は30.5秒でタイムアウト。
- 後者の実公開DOMは、q=下着・靴下／JP／過去3か月で「関連キーワード：ここに表示するデータはありません。」、行0件、並べ替えcontrolなしだった。この文言を旧実装が認識せず、存在しないcontrolを待っていた。全複合テーマが同様とは断定できない。
- 可視パネルの位置番号を保持する実装では、地域パネル挿入後に別パネルへずれる。修正したfixture入力で旧ソースを別診断ファイルから実行して失敗し、新ソースで成功した。最初のfixtureにはonclickの引用符不備があり、修正前のその結果は原因証明に使わない。旧ソース診断コピーのSHA-256は `e2fb5bb4eff9562b927b8a1532992bae08c04f805640903f5bd14c2b7ec9d75c`。開始時の実ファイルはCRLFで `46ed6bbf87e55ff530ea8483aed57d818a2ca4fb33c86c7e46788d5695196b6c`。
- 期間の「過去90日間」は許容されるが、新UIのreload判定が「過去3か月間」だけを許容していた。
- 60秒間隔の実測では20件取得が3回続いた後に429。3分間隔での検証を別途実施。Google側の具体的な制限値は未公開・未特定で、180秒は恒久的な成功保証ではない。

## 変更

1. パネルを意味上の見出しに結び付ける。位置番号を継続利用しない。selector診断版は `related-widget-v5`。
2. controlのない全パネルの「データなし」を認識する。期間変更前の古い表示を採用しないよう、検証済み公開URLを一度reloadし、検索語・JP・3か月・webと表示を再確認する。Risingだけの0件をTop0件へ変換しない。
3. 新UIの期間確認で3か月と90日を許容。
4. 複合テーマを「・」で分割し、runのseedと取得IDから1構成語を選ぶ。略された共有文脈は「用品」→「ペット用品」等で保つ。グローバル乱数を変更しない。
5. 取得queryは既存 `trends_acquisition.query_word` に保存。再開時は保存値を再利用し、旧runの複合テーマqueryも維持する。ROOMの元テーマfallback、テーマID、KEY後の関連語原文は維持する。queryとfallbackが一致する旧契約を維持し、両者が異なる新しい経路で設定版の元テーマ・許容構成語を確認する。snapshot自体は保存queryへ結び付ける。
6. 本番公開portは前回取得完了から最低180秒空ける。DBのattempt時刻を使い、テーマをまたぐ場合やプロセス再開にも適用。fixtureは実時間待機しない。Retry-Afterが長ければそちらを待つ。取得上限5回・deadline・401/403/429等のguardは保持する。
7. レビューで、5回目の429のRetry-Afterが消える境界を検出。最終attemptにも公開取得の待機期限を残し、次テーマへ引き継ぐよう修正。通常結果と保存応答復旧の両経路を試験。
8. 追加の診断stageは固定許可語のみ。待機ログには取得ID・待機期限・秒数を記録。probeは公開DOMだけを採取し、cookie等は採取しない。

## 試験の記録

作業場所は上記root。`P` = `.venv-inago/Scripts/python.exe -X utf8 -m pytest`、`D` = `rakuten-room-inago/runtime/diagnostics/trends-stability-20261022`。各行の実コマンドは `P <node> -q --basetemp=D/<一時領域> --junitxml=D/<XML>`。完全なパラメータ展開nodeは各JUnitのclassname/nameにも保存。

| 段階／node | 一時領域／XML | 期待／実結果 | exit |
|---|---|---|---:|
| UI修正前：`tests_inago/test_step21_browser.py::test_trends_missing_sort_control_requires_explicit_whole_widget_zero`（2node）、`::test_trends_related_widget_stays_bound_when_region_widget_is_inserted`、`::test_trends_new_explore_selects_scope_and_reads_top20_only[True]` | pytest-red / red.xml | 新条件を正しく判定／4失敗、78.06秒。パネル挿入fixtureに不備あり | 1 |
| UI初回修正：同4node | pytest-focused / focused.xml | 4成功期待／3成功1失敗、41.02秒。fixture引用符の不備を修正 | 1 |
| 修正fixture：`tests_inago/test_step21_browser.py::test_trends_related_widget_stays_bound_when_region_widget_is_inserted` | pytest-index-focused / index-focused.xml | 関連語20件／1成功、2.78秒 | 0 |
| 同node・旧 `aad0551` ソースを診断コピーで実行 | pytest-index-red-corrected / index-red-corrected.xml | 関連語20件／source_timeout、1失敗12.63秒 | 1 |
| `tests_inago/test_step21_browser.py::test_trends_old_whole_widget_zero_is_rechecked_after_scope_reload` | pytest-stale-zero-red / stale-zero-red.xml | reload後3件／旧表示0件、1失敗2.19秒 | 1 |
| reload修正：上nodeとmissing-sort2node | pytest-stale-zero-focused / stale-zero-focused.xml | 3成功期待／2成功1失敗、26.14秒。fixtureがreload時にURLの期間を戻す不備 | 1 |
| fixture修正：missing-sort `[True]` | pytest-zero-final-focused / zero-final-focused.xml | 0件を確認／1成功2.56秒 | 0 |
| 当初回帰：`tests_inago/test_step21_browser.py tests_inago/test_step21_trends_retry.py tests_inago/test_daily_fix_trends.py` | pytest-regression / regression.xml | 全成功期待／127成功2失敗336.72秒。selector版期待v4の更新漏れ | 1 |
| 失敗nodeのみ：`tests_inago/test_daily_fix_trends.py::test_n08b_related_keyword_heading_alias_and_semantic_level`、`::test_n08c_title_container_alias_and_unknown_heading_stay_safe` | pytest-selector-focused / selector-focused.xml | v5診断／2成功10.17秒 | 0 |
| 構成語変更前：`tests_inago/test_theme_query_components.py::test_compound_query_is_saved_and_reused_with_original_theme_fallback` | pytest-components-red / components-red.xml | 構成語query／複合queryのまま、1失敗0.53秒 | 1 |
| 構成語実装：`tests_inago/test_theme_query_components.py`（当時4node） | pytest-components-focused / components-focused.xml | 保存・再開・不正拒否／4成功0.77秒 | 0 |
| 構成語拡張：同ファイル（当時16node） | pytest-components-extended / components-extended.xml | 1語・略語・seed・KEY・fallback／16成功1.00秒 | 0 |
| 待機追加前：`::test_public_source_interval_survives_new_theme_session`、`::test_public_source_interval_applies_to_retries`、`::test_public_source_interval_does_not_send_request_beyond_deadline` | pytest-pacing-red / pacing-red.xml | 待機／1成功3失敗0.90秒。うち2件は第2テーマfixture不足 | 1 |
| 上の2fixture修正段階 | pytest-pacing-corrected-red / pacing-corrected-red.xml | 2成功期待／2失敗0.70秒。同一テーマ重複制約のfixture不備 | 1 |
| 第2テーマをT02に修正：上2node | pytest-pacing-corrected2-red / pacing-corrected2-red.xml | 180秒待機／未待機とdeadline超過送信で2失敗0.59秒 | 1 |
| 待機修正：上待機4node | pytest-pacing-focused / pacing-focused.xml | 180秒・長いRetry-After・deadline／4成功0.62秒 | 0 |
| レビュー指摘再現：`tests_inago/test_theme_query_components.py::test_final_retry_after_applies_to_next_theme_after_restart`（2node） | pytest-final-retry-red / final-retry-red.xml | 600秒待機／180秒で2失敗0.79秒 | 1 |
| 同node修正後 | pytest-final-retry-focused / final-retry-focused.xml | 通常/保存復旧とも600秒／2成功0.67秒 | 0 |

上表でファイル名を省略した`::node`は直前の同じ試験ファイル。実行順序を保持し、不正fixtureによる失敗を製品原因の再現として数えない。

### 追加回帰と互換修正

```powershell
& .venv-inago/Scripts/python.exe -X utf8 -m pytest tests_inago/test_step21_browser.py tests_inago/test_step21_trends_retry.py tests_inago/test_daily_fix_trends.py tests_inago/test_theme_query_components.py tests_inago/test_step21_daily.py -q --basetemp=rakuten-room-inago/runtime/diagnostics/trends-stability-20261022/pytest-regression-final --junitxml=rakuten-room-inago/runtime/diagnostics/trends-stability-20261022/regression-final.xml
```

上の回帰1は **160成功・28失敗、351.74秒、exit1**。版は `versions-regression1.json` と `D/regression-final-versions.json`。

失敗した最小2node（`tests_inago/test_step21_daily.py::test_durable_trends_and_same_root_key_job_feed_the_daily_theme_plan` と `::test_scheduled_daily_applies_raw_retention_before_processing`）を絞って確認。`P <2node> -q -s --basetemp=D/pytest-daily-debug --junitxml=D/daily-debug.xml` 相当のpytest.main呼出しは2失敗1.01秒/exit1。再確認は `D/pytest-daily-debug2`、`D/daily-debug2.xml`、2失敗0.97秒/exit1で、orchestratorのエラー記録関数を診断process内だけ包み、例外原因を出力した。

- 旧CurrentPlanner fixtureは `query=fallback='theme::ID'`。新検証で既存の完全一致契約まで設定の表示名へ限定していたため拒否していた。元実装どおり、完全一致は許可する互換処理へ修正した。異なるquery/fallbackの新経路では設定の元テーマ・許容構成語検証を維持。
- 実planner用ObservedTrendsPortの辞書が複合テーマ名しか持たず、構成語「履物」等でKeyErrorを起こしていた。`test_step21_daily.py::_theme_words_for_bundle` のfixture辞書へ元テーマと構成語を登録。
- 追加試験中の「fallbackを保存queryと同じ構成語へ変更しても拒否する」という部分は、旧契約にない追加制約だったため削除。無関係語・不一致の拒否、実plannerが元テーマをfallbackへ保持する確認は維持した。

修正後の最小2node：`P <2node> -q --basetemp=D/pytest-daily-minimal-focused --junitxml=D/daily-minimal-focused.xml` → **2成功1.97秒/exit0**。

失敗28nodeと直接影響する構成語6node：`pytest.main` に `daily-failed-focused-command.json` の正確な34node/引数を渡して実行 → **34成功26.86秒/exit0**。証拠 `D/daily-failed-focused.xml`。この互換修正はcode-reviewerとpython-reviewerが再レビューし、双方APPROVE。

### 最終回帰2

```powershell
& .venv-inago/Scripts/python.exe -X utf8 -m pytest tests_inago/test_step21_browser.py tests_inago/test_step21_trends_retry.py tests_inago/test_daily_fix_trends.py tests_inago/test_theme_query_components.py tests_inago/test_step21_daily.py -q --basetemp=rakuten-room-inago/runtime/diagnostics/trends-stability-20261022/pytest-regression-final2 --junitxml=rakuten-room-inago/runtime/diagnostics/trends-stability-20261022/regression-final2.xml
```

結果：**188成功・失敗0・skip0、369.58秒、exit0**。証拠 `D/regression-final2.xml`。実行前の製品・fixtureのSHA-256は公開可能な `versions.json` と非公開の `D/regression-final2-versions.json`。入力やソースに変更がない間の合格済み回帰全体の繰り返しは行っていない。

追加の機械検証：`node --check automation/inago/adapters/public_sources.mjs`、`node --check worklogs/projects/maintenance/trends-stability-20261022/probe.mjs` と `git diff --check` はexit0。blackで今回の新規Pythonとtrends_adapterを整形。既存live_sources/test_daily_fix_trendsには変更外のblack差分があるため、全体整形を加えていない。ruff/mypy/banditは当環境に未導入で未実行。

## 実サイトでの検証

`PublicBrowserSource.fetch(TrendsRequest(..., range='3m'))` を本番と同じNode・Chrome・専用profileで実行。JP／web／Top／過去90日または3か月をresponse契約で確認。単発120秒上限。429が出た系列はその場で中断し、identity変更・profile変更・認証回避はしていない。

| 系列 | JSTの完了時刻 | 結果 |
|---|---|---|
| 01 | 15:53:19 | 下着・靴下：429、0件。この429は「データなし」の証拠に使わない |
| 02・取得完了後60秒待機 | 15:57:26、15:58:50、16:00:13、16:01:36 | スキンケア20、収納20、コスメ20、キッチン429で終了 |
| 03・取得完了後180秒待機 | 16:05:54、16:09:18、16:12:42、16:16:08、16:19:33 | スキンケア20、収納20、コスメ20、調味料20、靴下20。5回連続で順位1～20を確認、429なし。各24.39/23.31/24.41/26.36/24.30秒 |

系列03の実行コードを `live-series-03.ps1` に保存。実行したPowerShellコマンド本文と同じで、作業場所は固定root。系列01～03の呼出しprocessはexit0。ただし、コードは失敗responseを保存後にloopを抜けるため、exit0自体を取得成功の意味には使わない。系列03は別の機械確認で5件ともobserved、20件、順位1～20、DB不変をassertしexit0を確認。

共有可能な集計結果は `live-results.json`。原文・順位を含むobservationは `D/live-series-0N-results.json`、`D/live-series-0N/observations/` に非公開保存。各系列の前後で `runtime/state.sqlite` のSHA-256は同じ `c8eff96545a5ee54ee4d3226b35bbd1bfec0611ecde818132c71c373be7683f9`。本番日次再実行・DB書換え・スケジューラ変更は今回行っていない。

## 独立レビュー

code-reviewerとpython-reviewerの双方が読取で確認。最終試行のRetry-After引継ぎをHIGHとして指摘し、修正後に双方APPROVE。指摘の再現試験と修正後試験を上表に保存。レビュー担当は製品変更・本番操作・試験実行をしていない。

## 残る範囲

- 関連語が存在しない検索語は正常0件になり得る。全テーマ・全構成語に20件がある保証はない。
- 180秒間隔での今回の連続成功は、他の日・Google側の制限変化まで保証しない。429とRetry-Afterはそのまま観測・待機・記録する。
- 実サイトで確認した取得処理と、隔離DBで確認した保存・再開・KEY/ROOM接続を分けて報告する。本番DBを更新した日次フロー全体の実行証拠として流用しない。

## 最終結果とGit対象

実サイトは5回連続各20件、順位1～20。隔離最終回帰は188成功。新規の0件表示、古い0件のreload確認、パネル挿入、新UI90日期間、構成語、再開、最終429の待機引継ぎを含む。code-reviewer/python-reviewerは最終互換修正後も承認。再現スクリプトとHTMLもcode-reviewerが承認した。報告HTMLは `reports/incidents/2026-10-22/trends-stability.html`。

最終差分を確認し、次の今回所有ファイルだけをステージする：

- 製品：`automation/inago/adapters/public_sources.mjs`、`daily_theme_planner.py`、`live_sources.py`、`orchestrator.py`、`trends_adapter.py`、`theme_queries.py`（Pythonはすべてautomation/inago直下）。
- 試験：`tests_inago/test_step21_browser.py`、`test_step21_daily.py`、`test_daily_fix_trends.py`、`test_theme_query_components.py`。
- 記録：このディレクトリの7ファイルと報告HTML。runtimeと既存未追跡の非公開作業データは除外。

完了直前の `git status --short`・対象diff・機械確認の後、現在の `main` を通常コミットし `origin/main` へ通常プッシュする。実行結果とcommit識別子は非公開 `D/git-result.json` と最終応答に記録する。履歴書換え・force pushは行わない。

## 自己評価（agent-self-evaluation）

**4.6/5。実測と隔離試験の確認範囲を明示した状態で報告する。**

| 軸 | 点 | 根拠・改善点 |
|---|---:|---|
| 正確さ | 5 | 実測5連続20件・順位1～20と188成功を実出力で確認。0件、429、旧fixture不備を区別し、将来のGoogle制限まで保証しない |
| 完全性 | 4 | 構成語・再開・待機・通常/復旧の最終Retry-Afterまで確認。実サイトは5語であり全30テーマは未検証。追加確認する場合は未検証テーマを優先する |
| 明瞭さ | 5 | 不具合・原因・修正・追加記録をHTMLで説明し、実測と試験結果を分けた |
| 実用性 | 5 | 次回CLI起動で使用される実装、保存互換、再現スクリプト、対象版ハッシュを用意。本人の追加操作を必要としない |
| 簡潔さ | 4 | fixtureの修正経緯が増えて詳細記録が長い。完全node引数をJSONへ分離し、本人向けHTMLと最終応答では要点を示す |

追加改善の優先順：未検証テーマを追加検証する際の対象選定、詳細証拠の索引化。2以下の軸なし。本人の判断にも必要な確認範囲を示しており、この評価に合意を得られると考える。判定：提出可。
