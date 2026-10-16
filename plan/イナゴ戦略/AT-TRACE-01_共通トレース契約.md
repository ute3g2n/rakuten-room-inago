# AT-TRACE-01 共通トレース・例外・保持契約

正本：要件定義書HTML 2.7、§8・§27.6～27.8・§28.4/28.7。対応：IR-004/008/018/019/021/028、F02/F03/F05/F08/F11、STEP R03/R05。これは診断用の補足受入条件であり、業務SQLite schema、settings_version、rules_versionを追加しない。

## 共通イベント

各JSONL eventは`schema_version`、UUIDの`event_id`/`span_id`、任意の`parent_span_id`、`component`、`operation`、`phase`、JSTの`at`、`elapsed_ms`、`run_id`、`target_date`、`retention_expires_at`、`correlation_status`を持つ。phaseは`begin`/`end`/`error`/`cancel`/`status`。request/job/root-job/task/attempt/deadline/remaining、`active_stage`、`last_completed_stage`を許可項目内で付ける。spanは親runへ結び、異なるrequest/jobのIDを共有しない。

run root spanは実イベントとして出力し、公開取得・AI等の子spanはそのroot span IDをparentとして参照する。イベントは、許可したID、分類コード、列挙値、範囲内整数、真偽値だけを持つ。例外本文・stack・URL・query・header・Cookie・資格情報・投稿本文・商品本文・完全DOM・認証画面画像は保存しない。主例外のcode/class、causeのcode/class、cleanupのcode/classは別欄で記録し、記録失敗が業務例外を置き換えない。自由文を別途診断表示する場合は共通redactorを通す。

既定上限は1 runあたり2048 event、4 MiB、1 eventあたり16 KiB、data rootあたり256 trace JSONL、保持90日。quota接近時は最大1 KiBを抑制marker用に確保し、抑制理由と`degraded`状態を記録する。markerの`suppression_marker_count`は出力したmarker件数を表し、破棄eventの総数を示さない。markerは相関IDを省略して小さく保ち、run/spanと任意のparent spanで結ぶ。root quotaと期限削除は同じprocess-shared lockで直列化し、書込中のeventを失わない。書込不能は`unavailable`とし、古い成功状態をhealthyと再表示しない。これらはproductionのコード既定値であり、Tの隔離nodeだけが小さいpolicyを注入できる。期限切れファイル削除はSQLite commit後に行い、ログI/O失敗で確定済みDBを巻き戻さない。

共通保存先は`<runtime_root>/logs/traces/<実行日>/<run_id>.jsonl`。AI runnerの既存native traceは応答復旧の証拠として維持し、共通traceはその代替にしない。公開取得側はsource stage/failureの安全な要約だけを同じrun/request spanへ移す。匿名観測に保存する`trace_ref`はdata root相対、`trace_state`はhealthy/degraded/unavailableである。

## Trends関連widget待機の安全な診断（STEP R05）

`trends_period_reflected`後に、`trends_related_widget_wait_begin`から`trends_related_widget_wait_ready`、`trends_related_widget_wait_timeout`、または`trends_related_widget_contract_error`までを記録する。許可項目は`widget_scope=explore_related_queries`、`widget_selector_version=related-widget-v2|related-widget-v3|related-widget-v4`（v4が現行、v2/v3は既存traceの後方互換）、`widget_kind=related_keywords|related_topics|unknown|multiple`、DOM/visible/match/wrong-visible各count（0..100）、`related_widget_generation`と`generation_before_period`（0..10000000）、`widget_period_refresh_required`、`widget_empty_evidence=none|explicit_no_data`、`widget_outcome=ready|deadline|ambiguous|contract_mismatch`に限る。見出し文字列・検索語句・URL・DOM・応答bodyは記録しない。

現行v4 selectorは対象widget内の可視`h1`～`h6`/`[role="heading"]`、title/heading class、widgetの`aria-label`、可視`aria-labelledby`を意味で分類する。日本語の「トピック」「キーワード/検索/クエリ」と英語のtopics/keywords/queries/searchesの許可済み表現を`related_topics`または`related_keywords`へ写像し、候補に矛盾がある場合は`unknown`のまま扱う。見出し本文はtraceへ保存しない。旧v2/v3 traceも検証可能なまま保つ。

期間変更後に再描画が必要な場合、readyは`related_widget_generation > generation_before_period`を満たす。可視の正確な単一関連キーワードwidgetが期限内に出ない、または期間更新が観測できない場合は`source_timeout`として有限再試行へ渡す。複数の可視一致は`query_widget_ambiguous`、別種の可視widgetのみは`query_widget_contract_mismatch`としてprocessing_errorへ渡し、取得retry/fallbackを行わない。正常0語の受理には、許可済みの明示的空結果表示が必要。

## 受入node

| Node | 注入 | 合格条件 |
| --- | --- | --- |
| `tests_inago/test_daily_fix_trace.py::test_n03` | FakeClock上のTrends stage完了、adapterのstage変換、別job並行、待機中timeout | 同一request eventのrun/span/parentが一致し、別jobが混ざらない。source完了marker `trends_period_reflected` / `trends_top_rows_updated` をendとして記録する。timeout eventはactive=`trends_top_rows_wait_begin`、last completed=`trends_period_reflected`、elapsed 1250ms、残り18750ms。 |
| `tests_inago/test_daily_fix_trace.py::test_n04` | URL/query、Authorization/Cookie、bodyを含む例外、cause、cleanup例外 | primary/cause/cleanupのclassとsafe codeを分離し、URL・secret・body・例外messageがtraceへ0件。 |
| `tests_inago/test_daily_fix_trace.py::test_n29` | event/byte/file quota、thread/process同時file quota、90日期限、partial append、保持rewrite、JSONL I/Oエラー | quota超過を`suppression_marker_count=1`のmarkerとdegradedで示す。期限当日にraw event/fileが失効し、rewrite後の権限を維持する。append/rewrite失敗を成功に数えず、partial append後は追記を止める。trace I/O失敗はunavailableを返し、独立SQLiteのcommit済みsentinelは変化しない。 |

各nodeの結果はM §14.5とT/R03のcase ledgerに完全node ID・attempt・証拠参照と共に記録する。PASS後はR15の最終全回帰まで再実行しない。pytest cacheだけを合否根拠にしない。
