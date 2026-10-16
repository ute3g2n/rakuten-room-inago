# AT-TRACE-01 共通トレース・例外・保持契約

正本：要件定義書HTML 2.12、§8・§27.6～27.12・§28.4/28.7。対応：IR-004/008/010/018/019/021/028、F02/F03/F04/F05/F07/F08/F11、STEP R03/R05/R06/R07/R08/R09。これは診断用の補足受入条件であり、業務SQLite schema、settings_version、rules_versionを追加しない。

## 共通イベント

各JSONL eventは`schema_version`、UUIDの`event_id`/`span_id`、任意の`parent_span_id`、`component`、`operation`、`phase`、JSTの`at`、`elapsed_ms`、`run_id`、`target_date`、`retention_expires_at`、`correlation_status`を持つ。phaseは`begin`/`end`/`error`/`cancel`/`status`。request/job/root-job/task/attempt/deadline/remaining、`active_stage`、`last_completed_stage`を許可項目内で付ける。spanは親runへ結び、異なるrequest/jobのIDを共有しない。

run root spanは実イベントとして出力し、公開取得・AI等の子spanはそのroot span IDをparentとして参照する。イベントは、許可したID、分類コード、列挙値、範囲内整数、真偽値だけを持つ。例外本文・stack・URL・query・header・Cookie・資格情報・投稿本文・商品本文・完全DOM・認証画面画像は保存しない。主例外のcode/class、causeのcode/class、cleanupのcode/classは別欄で記録し、記録失敗が業務例外を置き換えない。自由文を別途診断表示する場合は共通redactorを通す。

既定上限は1 runあたり2048 event、4 MiB、1 eventあたり16 KiB、data rootあたり256 trace JSONL、保持90日。quota接近時は最大1 KiBを抑制marker用に確保し、抑制理由と`degraded`状態を記録する。markerの`suppression_marker_count`は出力したmarker件数を表し、破棄eventの総数を示さない。markerは相関IDを省略して小さく保ち、run/spanと任意のparent spanで結ぶ。root quotaと期限削除は同じprocess-shared lockで直列化し、書込中のeventを失わない。書込不能は`unavailable`とし、古い成功状態をhealthyと再表示しない。これらはproductionのコード既定値であり、Tの隔離nodeだけが小さいpolicyを注入できる。期限切れファイル削除はSQLite commit後に行い、ログI/O失敗で確定済みDBを巻き戻さない。

共通保存先は`<runtime_root>/logs/traces/<実行日>/<run_id>.jsonl`。AI runnerの既存native traceは応答復旧の証拠として維持し、共通traceはその代替にしない。公開取得側はsource stage/failureの安全な要約だけを同じrun/request spanへ移す。匿名観測に保存する`trace_ref`はdata root相対、`trace_state`はhealthy/degraded/unavailableである。

## Trends関連widget待機の安全な診断（STEP R05）

`trends_period_reflected`後に、`trends_related_widget_wait_begin`から`trends_related_widget_wait_ready`、`trends_related_widget_wait_timeout`、または`trends_related_widget_contract_error`までを記録する。許可項目は`widget_scope=explore_related_queries`、`widget_selector_version=related-widget-v2|related-widget-v3|related-widget-v4`（v4が現行、v2/v3は既存traceの後方互換）、`widget_kind=related_keywords|related_topics|unknown|multiple`、DOM/visible/match/wrong-visible各count（0..100）、`related_widget_generation`と`generation_before_period`（0..10000000）、`widget_period_refresh_required`、`widget_empty_evidence=none|explicit_no_data`、`widget_outcome=ready|deadline|ambiguous|contract_mismatch`に限る。見出し文字列・検索語句・URL・DOM・応答bodyは記録しない。

現行v4 selectorは対象widget内の可視`h1`～`h6`/`[role="heading"]`、title/heading class、widgetの`aria-label`、可視`aria-labelledby`を意味で分類する。日本語の「トピック」「キーワード/検索/クエリ」と英語のtopics/keywords/queries/searchesの許可済み表現を`related_topics`または`related_keywords`へ写像し、候補に矛盾がある場合は`unknown`のまま扱う。見出し本文はtraceへ保存しない。旧v2/v3 traceも検証可能なまま保つ。

期間変更後に再描画が必要な場合、readyは`related_widget_generation > generation_before_period`を満たす。可視の正確な単一関連キーワードwidgetが期限内に出ない、または期間更新が観測できない場合は`source_timeout`として有限再試行へ渡す。複数の可視一致は`query_widget_ambiguous`、別種の可視widgetのみは`query_widget_contract_mismatch`としてprocessing_errorへ渡し、取得retry/fallbackを行わない。正常0語の受理には、許可済みの明示的空結果表示が必要。

## ROOM条件popup閉じの安全な診断（STEP R06）

検索条件popupは、開状態とquery/likes=300/commentsなしの反映を確認してから閉じる。既にhiddenなら`room_condition_close_method=already_closed`で冪等完了とする。popup内にある可視・有効な正確な閉じcontrolが1件だけなら、通常クリックを1回行い、popup hiddenを有限なrequest timeout内で待つ。正規close controlが一意でない場合に、可視・有効・完全一致の「検索」controlが1件だけあれば通常クリックを1回行い、条件を適用して結果一覧を開く。この`search_submit`は検証済み条件を適用する公開操作として記録する。いずれも使えない場合は再クリック/toggleを行わずEscapeを1回だけ送り、同じ有限request timeout内でhiddenを待つ。閉状態が不明なら検索一覧走査へ進まず`condition_popup_close_failed`を返す。

許可する追加fieldは`room_condition_close_control_count`と`room_condition_search_control_count`（整数0..100）、`room_condition_close_method=close_control|search_submit|escape|already_closed|none`、`room_condition_popup_state=hidden|visible|unknown`、`room_conditions_likes_verified`と`room_conditions_comments_verified`（boolean）、`error_class=Error|TimeoutError|TypeError|ReferenceError|SyntaxError|RangeError|URIError|EvalError|AggregateError|TargetClosedError|BrowserClosedError|ProtocolError|SourceFailure|other`である。開き始め/開確認、条件検証、閉じ始め/完了/失敗、`room_condition_popup_closed`、`room_results_scan_begin`の順序と各elapsed/phaseを記録する。query文字列、URL、DOM、画面本文、認証情報は記録しない。閉じ失敗は当該attemptのsource failureを1件だけ増やし、同一task/queryの有限retryと`retry_of`で続ける。成功retryは失敗履歴を保持しつつ最終失敗に数えず、最大3失敗でsourceを停止して原因を保持する。

| Node | 注入 | 合格条件 |
| --- | --- | --- |
| `tests_inago/test_daily_fix_room.py::test_n09` | 1.5秒より長く遅れて閉じる一意な正規control | 通常クリック1回、条件保持、hidden確認後に検索走査開始。閉じ待ちと共通traceはhealthy。 |
| `tests_inago/test_daily_fix_room.py::test_n09b_already_closed_and_ambiguous_controls` | 既にhiddenのpopup、誤候補を含む複数の「閉じる」control | hiddenは冪等完了。曖昧候補をクリックせずEscapeを1回使い、状態・条件bool・候補数を記録する。 |
| `tests_inago/test_daily_fix_room.py::test_n10` | 1回失敗後の同一task回復、および3回連続閉じ失敗 | `retry_of`連鎖・attempt数・source failure数が一致し、回復を最終失敗に数えない。3回後に停止し、3 failure observationと原因が残る。 |
| `tests_inago/test_daily_fix_room.py::test_n09c_public_search_action_applies_verified_conditions` | 正規closeなし、正確な検索操作1件、条件適用後の結果一覧 | 「検索」を通常クリックで一度だけ実行し、`search_submit`/検索control countを記録する。条件保持・hidden確認後に結果走査へ進み、common traceをhealthyにする。 |

## ROOM商品詳細の通常操作・遷移・検索復帰（STEP R07、要件HTML 2.10）

公開ROOMの実際の表示要素から操作対象を特定し、tag/role/visible/enabled/obscured、handlerの種類・位置、href有無とリンク範囲を安全な分類値で記録する。hrefがないことだけを理由に操作対象から除外しない。クリックはforceを使わず通常操作する。画面本文・商品名・query・hrefやURL・DOM・認証情報はtraceへ保存しない。

`room_detail_button_click_begin`から`room_detail_button_click_complete`または`room_detail_button_click_error`までをclick spanとし、`room_product_destination_wait_begin`から`room_product_destination_wait_complete`または`room_product_destination_wait_error`までを別spanとする。遷移監視はclick前にpopupと同一tabを観測できるよう登録するが、遷移の有限待機時間はclick成功後に開始する。click失敗時はこのrequestが所有する待機を取り消し、遅れて届いたpopupを商品到達として採用しない。popupとsame-tabは`destination_kind=popup|same_tab`で分ける。

click timeoutと遷移待ちtimeoutは個別requestに残る有限時間の範囲内で決める。例外本文を除外して内側の`TimeoutError`等のerror class、`failed_operation`、`timeout_ms`、`wait_elapsed_ms`を保存する。same-tabで商品へ移動した場合は元のROOM検索URLと順序をrequest timeout内に復元し、復帰が確認できるまで次の行へ進まない。実際の商品ページで得た正規商品URLとshop/item keyは、private observationに保存する。

許可する追加fieldは`element_tag=a|button|div|span|other`、`element_role=link|button|other`、`element_visible`/`element_enabled`/`element_obscured`/`has_href`（boolean）、`handler_kind=link|button|role|onclick|none`、`handler_tag=a|button|div|span|other`、`handler_scope=self|ancestor|none`、`href_kind=none|empty|blank|rakuten_item|rakuten_other|other_https|other_scheme|invalid`、`link_scope=none|self|ancestor|descendant|other`、`link_target=none|_blank|_self|other`、`destination_kind=popup|same_tab`、`timeout_ms`（整数0..60000）、`wait_elapsed_ms`（整数0..17100000）、`failed_operation`（安全なstage ID）である。既存の安全な`error_class` enumを使い、例外本文・URL・DOMは記録しない。

click errorの後に遷移待ちのtimeoutを追加しない。destination wait error、same-tab restore errorは元の検索request failureとして残し、click成功・商品解決へ書き換えない。request deadlineまでの残時間を各spanで共有し、日次17:45/18:00の終了時刻は使わない。

| Node | 注入・対象 | 合格条件 |
| --- | --- | --- |
| `tests_inago/test_daily_fix_room.py::test_n11` | ancestor link内の子要素とhrefなしhandler div | forceなしのclick begin/completeとdestination waitを分離し、実遷移から正規商品URL/keyを取得。actionabilityとリンク/handler分類が安全なtraceに残る。 |
| `tests_inago/test_daily_fix_room.py::test_n12` | 80件表示・same-tab遷移 | 元の先頭50行の順序を維持し、各same-tab商品から元の検索一覧へ復帰。51件目で穴埋めしない。 |
| `tests_inago/test_daily_fix_room.py::test_n13` | click遮蔽/timeout、行先なし、request deadline | click errorとdestination wait errorを区別し、click失敗後の遅いpopupを採用せず待機を取消す。全体request残時間を超える二重待機をしない。 |
| L02 | 許可された正規の公開ROOM UI port | likes=300/コメント指定なしを確認し、通常操作で実商品URL/keyへ到達、popup/same-tab別の遷移traceと一覧復帰・順序をprivate evidenceで確認する。未到達やcondition close failureをPASSへ置き換えない。 |

各nodeの結果はM §14.11およびT/R07のcase ledgerに完全node ID・attempt・観測・trace参照と共に保存する。PASS済みnodeはR15の最終全回帰まで再実行しない。L02の実接続で必要な正常経路を確認できなければR07を未完了として保持する。

## ROOM商品行失敗・検索全体失敗・一覧完了の安全な診断（STEP R08）

ROOM詳細解決は受理した元の先頭50枠を維持する。個別行のclick/商品遷移timeoutは、共有query deadlineが残り、元一覧を回復できる場合だけその行を`unresolved`として残し、次の行へ進む。10件以下の行失敗は検索を継続し、11件以上はsearch attemptを`partial`にする。失敗枠は元order_indexを持ったまま先頭50内を消費し、51件目以降で穴埋めしない。認証・条件不成立・stale query・共有deadline・元一覧復帰不能はquery failureとして保持し、行失敗数へ足さない。例外を一律行失敗に変換しない。

許可fieldは`scope=row|query|run`、`row_status=identified|unresolved`、`row_failure_count`と`identified_row_count`（各integer 0..50）、`stable_round`（integer 0..3）、`end_evidence=explicit_no_results|explicit_end|stable_no_progress|first_50`。row-indexが付いたdeadline/auth/condition/restore failureも明示`scope=query`を優先して投影する。`room_product_resolution_summary`はquery scopeでloaded/accepted/row failure/identified countを記録する。`room_product_row_complete`はrow scopeと元row_index/statusを記録する。

一覧の0件は、明示された「検索結果がありません」「該当する商品がありません」「検索結果はありません」だけを正常0とする。「検索結果は以上です」は正数表示後の終了表示であり、0件成功の根拠ではない。1件以上の短い一覧は、各最大2秒待機を伴うスクロールで3回続けて増加しなければ実表示件数で完了する。表示0で終了証拠なし、古いquery、一覧障害は`incomplete_search`または`query_not_reflected`等のquery failureにする。

`source_failures`は`source_status != observed`で確定したsearch attempt単位の件数であり、attemptごとに最大1増やす。`observed`のまま終わった10件以下の商品行失敗は`source_failures`を増やさず、行数は`row_failure_count`と各post identityで数える。partial/unavailable/invalidは各search attemptの1失敗である。3回のquery attemptを使い切った時は`source_stopped=true`、`source_stopped_reason=attempt_budget_exhausted`とし、attempt/retry_ofと失敗理由を保つ。回復成功では停止理由を残さない。

失敗観測artifactは許可済みのROOM stage IDと`condition_popup_close_failed`、`product_row_timeout`、`product_resolution_failures_exceeded`、`room_search_restore_failed`、`query_not_reflected`、`incomplete_search`等の有限codeだけを保持する。raw query/URL/DOM/本文/資格情報/例外messageを記録しない。scope・row index・count・enumは共通trace側で型と範囲を再検証する。

| Node | 注入 | 合格条件 |
|---|---|---|
| N14 | 1行/10行のdetail click TimeoutError、後続行は成功 | row scopeで未解決行を元位置に残し、残行を続ける。summary failure/identified countとhealthy common traceが一致する。 |
| N15 | 11行失敗と50行目失敗を含む80行一覧 | 11件でpartial、50番目の失敗枠を保持し、51番目以降で穴埋めしない。 |
| N16 | shared deadline/auth/condition/restore/stale/empty/short-listと同task再試行 | query failureはrow scopeへ落とさず、明示0件・3回安定終了・3attempt停止理由を正確に保持する。 |

## AI turn identity・契約拒否・再開診断（STEP R09）

各root turnは新しいjobとして扱う。AI-Oはそのturnの`job-input.json`にあるprotocol/schema/run/job/attempt/task/agent/skill/date/settings/input_refs/evidence_refs/input_hash/deadlineをそのまま用い、前turnのidentity/hashを持ち越さない。保存担当は応答を現在requestのidentityと厳密に照合し、最初の不一致fieldをcauseとして記録する。`input_hash`や別identityを期待値へ書き換えず、validatorを緩めない。

copy lifecycle traceは`ai_result_received`、`ai_contract_rejected`、再開時の`contract_rejection_resumed`/`expired_attempt_resumed`を記録する。契約拒否eventはrunnerが応答した後のため`ai_run_invoked=true`、どちらの再開eventも新たなrunner呼出しをしないため`false`とする。許可する診断fieldは`attempt_no`（1..3）、`status`、safe `error_code`、safe `cause_code`、safe `artifact_ref`、`ai_run_invoked`（boolean）。wrapperの`copy_runner_contract_rejected`とcause（例：`input_hash`）は別値とし、artifact参照は保存済みraw responseを指す。日次checkpointのcopy error referencesにも、記録済みの場合だけ同じcause/artifact refを保持する。値が不正なartifact_refはtraceへ出さず、responseを未採用のcontract errorとして停止する。自由文、prompt、response body、本文、URL、DOM、credentialは記録しない。

契約不一致はAI生成error/empty retryとして扱わず、draft/active reservation/committed countを増やさず、orchestratorの最上位結果へ停止理由を伝える。DB保存だけが失敗した場合は保存済み応答artifactを同じidentityで再利用し、AIを再呼出ししない。empty/generation errorだけは初回込み最大3回とする。再開時にcheckpoint保存前の契約拒否を読む場合、`generation_attempt`と対応するroot `ai_job.error_json`からcause/artifactを復元し、参照不一致や不正値は引き継がない。

| Node | 注入 | 合格条件 |
|---|---|---|
| N17 | draft14/予約14の後、current requestと異なるinput_hashを返す | raw response artifactと`input_hash` causeを保持し、契約errorを最上位へ伝播。draft/予約/countは14のまま。 |
| N18 | 同一root内でKEY→COPY A→COPY Bを異なるturn identity/refsで実行 | 各turnが現在のjob-inputに合うhash/refsで応答し、native parentと採用件数を区別する。 |
| N19 | empty→generation error→success、および保存失敗からの再開 | 生成は最大3、保存失敗では同一response/artifactで再開し、生成を繰り返さない。 |
| R09 review recovery | checkpoint前の契約拒否後に同じ商品を再開する | 新しいAI呼出しなしでcause/artifactを復元し、`contract_rejection_resumed`に記録する。 |
| R09 review invalid-ref | 非文字列artifact_refを返す | trace書込みを例外で中断せず、attemptをerror確定し、`artifact_ref` cause付きcontract errorで停止する。 |
| R09 review invocation | runnerが契約不一致応答を返す | `ai_contract_rejected`に`ai_run_invoked=true`を記録する。 |
| R09 review expired resume | timeout結果を保存した後に同じ商品を再開する | `expired_attempt_resumed`へ記録し、契約拒否再開eventとして誤分類しない。 |

これらはAI/保存境界の追加診断契約であり、業務DBの新列やschema migrationを求めない。完全node ID・attempt履歴・N17/N18/N19/L03と追加review nodeの隔離証拠はM §14.13およびT/R09へ保存し、PASS済みnodeはR15まで再実行しない。

## 受入node

| Node | 注入 | 合格条件 |
| --- | --- | --- |
| `tests_inago/test_daily_fix_trace.py::test_n03` | FakeClock上のTrends stage完了、adapterのstage変換、別job並行、待機中timeout | 同一request eventのrun/span/parentが一致し、別jobが混ざらない。source完了marker `trends_period_reflected` / `trends_top_rows_updated` をendとして記録する。timeout eventはactive=`trends_top_rows_wait_begin`、last completed=`trends_period_reflected`、elapsed 1250ms、残り18750ms。 |
| `tests_inago/test_daily_fix_trace.py::test_n04` | URL/query、Authorization/Cookie、bodyを含む例外、cause、cleanup例外 | primary/cause/cleanupのclassとsafe codeを分離し、URL・secret・body・例外messageがtraceへ0件。 |
| `tests_inago/test_daily_fix_trace.py::test_n29` | event/byte/file quota、thread/process同時file quota、90日期限、partial append、保持rewrite、JSONL I/Oエラー | quota超過を`suppression_marker_count=1`のmarkerとdegradedで示す。期限当日にraw event/fileが失効し、rewrite後の権限を維持する。append/rewrite失敗を成功に数えず、partial append後は追記を止める。trace I/O失敗はunavailableを返し、独立SQLiteのcommit済みsentinelは変化しない。 |

各nodeの結果はM §14.5とT/R03のcase ledgerに完全node ID・attempt・証拠参照と共に記録する。PASS後はR15の最終全回帰まで再実行しない。pytest cacheだけを合否根拠にしない。
