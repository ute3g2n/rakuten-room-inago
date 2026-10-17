# AT-TRACE-01 共通トレース・例外・保持契約

正本：要件定義書HTML 2.27、§8・§27.6～27.17・§28.4/28.7。対応：IR-004/008/010/015/017/018/019/020/021/022/028、MR-005～028、F02/F03/F04/F05/F07/F08/F09/F11/F12/F13/F18、STEP R03/R05/R06/R07/R08/R09/R10/R11/R12/R13/R14、AT-MAINT-01。これは診断用の補足受入条件であり、業務SQLite schema、settings_version、rules_versionを不必要に追加しない。R10の`run`期限nullable migrationは明示要件のschema v6である。U05の保守専用管理DB schema v1にU07 migrationでmanifest/AI session/observationを加え、U08 migrationでdaily launch ledgerを加えてschema v3、U09 migrationでprocess identityとwatchdog queueを加えてschema v4とする。日次業務DB/L02と保守出力は分離する。MN04 full maintenance JSONLのschema/path/quotaは後続STEPで確定する。

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

## 日次最終結果（STEP R10）

`daily_target`、初期/追加allocation、`committed_count`、`shortfall_count`、`actual_posted_count`、`source_failures`、`error_count`を別々に保持する。`primary_error`は最初の未解消理由、`secondary_errors`は他の診断理由を示す。`daily`、`metrics`、`export`、`preservation`のstatusを`phase_results`として個別に示し、metric/export/backup/restoreの失敗を本文不足や正常statusへ畳み込まない。blocked recoveryで工程を実行しなかった`not_run`を失敗理由として数えない。overall failureの`reason`にはprimary error codeを使い、日次目標到達/不足の理由は`daily_reason`へ残す。正常fallbackの利用だけでprocessing errorを作らない。

OS日次終了コードは完全成功/not_due=0、処理error/contract/recovery/lock/postprocessing failure=4、処理errorのないpartial/shortfall=5。partial/shortfallに未解消processing errorまたはmetrics未完了がある場合は4を優先する。完全成功にはdaily complete、metrics observed、export ready、preservation ready/end backup ready、monthly restore readyまたはalready_checkedが必要。Windows wrapperの子Python終了コードを実プロセス終了コードとして検証する。

17:45/18:00のrun-wide deadlineとWindows Task Schedulerのrun-wide実行時間上限は停止判定へ使わない。daily task XMLは`ExecutionTimeLimit=PT0S`とする。各AI/ROOM/metrics requestの有限timeoutを個別に適用し、期限後応答を成功へ採用しない。ROOM/AI timeoutのcancel scopeはrequest/job単位とし、run IDに加えてROOMの`attempt_id`またはAIの`job_id`で対象を特定する。取消確認後は後続request/retryを続ける。ROOM requestが直列実行待ち・worker登録前・プロセス起動前に取消された場合も、対象IDの取消を記録し、後から起動しないことを確認する。取消未確認なら同runの後続処理を停止し、phase/codeを記録する。新runの`run.draft_deadline`/`run.end_deadline`はnull、旧値は監査履歴として保持し、schema v5→v6で値を変換せず、run参照子行も保持する。完了済みrunにmetrics pending/not_startedがある場合はRecoveryで旧owner停止を確認し、pending jobを停止できた後にmetricsのみ再開する。取消未確認時は新metrics requestを起動せず異常を記録する。検索/本文生成/報告反映を再生しない。


## 日次共有coreの工程trace（STEP R11）

`daily_pipeline` componentの許可operationは`preflight`、`database_open`、`prepare`、`start_backup`、`operations_monitor`、`daily`、`operations_monitor_stop`、`database_close`、`export`、`preservation`。各実行stageは`phase=begin/end/error`の順序付きeventとし、同runのpipeline spanをroot run spanへ結ぶ。`event_kind=stage`、`active_stage`、`last_completed_stage`、elapsed、許可済みstatus、失敗時safe `primary_error_code`だけを保存する。自由文・例外message・path絶対値・商品/投稿本文・URL/query/DOM・資格情報を含めない。

`phase_results`はpreflight/database_open/prepare/start_backup/daily/metrics/operations_monitor/database_close/export/preservation/traceを別statusで返す。保全はend_backupとmonthly_restoreの内訳を保持し、実行条件が成立せず飛ばした工程を`not_run`として示す。operations monitorのstart failure後にstop cleanupが成功してもoperations_monitorはfailedのままにし、stop eventの成功とstart phaseの失敗を別にする。日次数到達やmetrics statusとHTML/保全statusを混同しない。

`pipeline_trace`のstatusはhealthy/degraded/unavailable/not_written、event_countは非負整数、referenceはdata root相対、error_codeは許可されたsafe identifierだけとする。TraceWriterへの書込失敗はtrace stateをunavailable/degradedにし、既にcommitしたSQLite業務状態をrollbackせず、日次主例外を上書きしない。report apply途中失敗は同じrunのcheckpointから再開し、pending全件の管理反映確認前に探索を開始しない。17:45/18:00やScheduler時間上限は日次停止判定に使わず、各request/jobだけに有限timeoutを適用する。

| Node | 注入 | 合格条件 |
|---|---|---|
| `tests_inago/test_daily_fix_pipeline.py::test_n22_probe_executes_daily_html_backup_and_restore_as_one_pipeline` | isolated probeの正常15供給 | 共通core全工程、HTML、metrics、backup、隔離restoreが同一runで成功し、DB integrity/FKが保たれる。 |
| `tests_inago/test_daily_fix_pipeline.py::test_n22b_production_entry_uses_shared_core_after_production_guards` | production ready/root guardとcore entry | guard後に一度共有coreへ入り、phase結果とhealthy trace、15 commitを確認する。 |
| `tests_inago/test_daily_fix_pipeline.py::test_n22c_monitor_start_failure_survives_successful_stop_status` | monitor start failure、stop cleanup成功 | operations_monitor statusはfailedを保ち、daily未起動、cleanup stopは完了として区別される。 |
| `tests_inago/test_daily_fix_pipeline.py::test_n24a_report_apply_restarts_same_fourteen_run_across_old_cutoffs` | 報告反映途中失敗と17:45/18:00後の再開 | 14件/run identityを維持し、全報告反映後に探索を再開、15件へ到達する。 |
| `tests_inago/test_daily_fix_pipeline.py::test_n24b_late_ai_response_is_discarded_and_next_offer_continues` | 個別AI timeoutと遅延応答 | 取消確認済みrequestの期限後応答採用0、別候補の進行は許可される。 |
| `tests_inago/test_daily_fix_pipeline.py::test_n25_partial_metrics_and_full_retention_preserve_only_supported_evidence` | partial metrics、raw expiry、7世代・restore | null/0とscope/status、expired raw非復活、予約/投稿/pending/除外履歴を検証する。 |

N22/N22b/N22c/N23a/N23b/N24a/N24b/N25の隔離証拠はM §14.15とT/R11へ保存する。PASS済みnodeはR15最終全回帰まで再実行しない。R11結合fixtureは本番日次の実運転・Scheduler有効化を証明しない。


## 本人報告・同期/取込trace（STEP R12）

投稿履歴HTMLの一操作を、localhost Human API受付・IndexedDB/outbox再送・JSON import・翌日report applyまで共通traceで辿る。共通eventの必須schema、run root parent、JST、90日保持、上限、safe fieldの再検証を維持する。報告本文、理由、URL、JSON payload/ファイル名、token、CSRF、Cookie、header、例外messageをdiagnosticsへ出さない。SQLite receipt/管理履歴が状態正本で、traceは状態を上書きしない。

R12で追加許可するevent fieldは`action_id`/`draft_id`（safe ID）、`draft_version`/`receipt_revision`（非負integer、draft_versionは1以上）、`report_action`（`posted/skipped/correction/edit`）、`apply_status`（`pending/applied/rejected`）。個別処理の相関はaction_idをrequest_idとして同じrun/target_dateへ結び、別actionのspanを混ぜない。`report_receipt`は`human_api` componentで成功受付のbegin/endを記録し、receipt revision/statusは確定DBから得る。`report_import`はJSON batch commit後に各受付actionのendを記録する。`report_apply`はactionごとのbegin/end/errorを記録し、commit後は`applied`、失敗時はsafe primary error codeとpendingを示す。自由文や内容差分をtraceに記録しない。

TraceWriterが診断eventを書けない場合も、既にcommitしたreceipt/管理反映をrollbackせず、本人向け受付の業務結果をtraceの成否で置き換えない。report eventはrun-level trace phaseの代替ではなく、trace I/O不能時には該当diagnostic eventが欠落し得る。日次全体・Schedulerの17:45/18:00終了期限は設けず、個別API/request/processには有限timeoutを使う。

| Node | 入力・境界 | 合格条件 |
|---|---|---|
| `tests_inago/test_daily_fix_history.py::test_n26_history_date_copy_report_and_pending_receipt_trace` | 15件の当日履歴、過去日2件、チェック/訂正、localhost受付 | 日付/版/本文/URL/checkboxが表示され、copyは投稿扱いにならない。固定OFF理由・receipt pending・未変更の予約/実投稿数とhuman_api action traceを確認する。 |
| `tests_inago/test_daily_fix_history.py::test_n27_browser_outbox_json_import_and_next_day_apply_trace` | API停止/復帰、IndexedDB reload、JSON export/import、翌日apply | 同じaction_idのACK後だけoutboxを消し、JSON取込は冪等。重複/逆順/競合/旧版/不正JSONを拒否し、翌日のDB反映は一回だけ。report_import/apply trace、reservation・revision・applied stateの対応を確認する。 |

N26/N27の完全node、隔離root、command、期待/実測、SQLite件数・reservation/posted状態、action/revision trace、localhost画面操作証拠はM §14.16とT/R12へ保存する。PASS済みnodeはR15最終全回帰まで再実行しない。HTML/fixture PASSは本番日次実行、本人の実投稿、Scheduler有効化を証明しない。

## Scheduler設定の実接続確認（STEP R13）

日次の終了期限はJSONL traceへ混ぜず、Windows Task Scheduler APIから実設定を取得してprivate execution evidenceへ記録する。許可する証拠項目はtask名、state、principal SID、logon type、run level、ExecutionTimeLimit、restart設定、command/arguments/working directoryの期待値比較結果、trigger種別、前後設定の同一性だけとする。アカウント名・環境変数値・認証情報・任意の外部データは公開traceへ記録しない。

日次taskのExecutionTimeLimitはPT0Sでなければならない。登録処理は実登録値とテンプレートを照合し、Ready/Disabledで値が異なる場合だけ定義済みXMLへ再登録する。実行中taskは変更しない。修復後にprincipal/action/working directory/triggerが保持されたことをScheduler APIで確認する。N30は現行の実日次taskと一時taskの設定を読み、通常主体から隔離probeを実行する。L04は同主体でread-only Human API healthおよびmetrics接続を実証する。これらは実production日次の実行・投稿を示さない。

## U07 app-server session transport log（MN05）

MN05はmaintenance parent sessionのtransport診断用JSONLで、日次L04の汎用`app_server`出力、日次L02 common trace、MN04のworkflow全体traceとは別の出力である。標準pathは`D/maintenance/logs/<target_date>/<job_id>/<parent>--<role>.jsonl`。attempt output/worktreeの外に置くhost-owned rootに保存し、AI roleからの直接書込を許さない。configured log rootから対象fileまでの全path componentでreparse point（symlink/junction等）を拒否し、resolve後もhost-owned root配下であることをsession作成時に確認する。path escapeまたはreparse pointを検出した場合は記録を拒否する。

MN05の共通fieldはevent名とUnix epoch秒の`at`。許可する追加fieldは`method`、safe `thread_id`/`turn_id`、`process_pid`、safe `status`/`returncode`/`code`、要求`model`/`effort`/`service_tier`/`fast_mode`、`role_name`、`role_config_sha256`、`sandbox_profile`、`observation_status`。保守role付きsessionだけがmodel・role等を記録するため、一般の日次T02/L04の`root_ready` field setは従来どおり維持する。

root_readyは指定値を含むが、観測状態をconfigured_unverifiedとする。実際のturn eventから受け取ったmodel/effort/tierだけをruntime_observedに記録し、欠けた値は未確認のままとする。開始前にbasic sandbox mode/model/tier/Fast capabilityを確認し、unsupportedはsession_start_blockedとしてsafe codeと要求設定を記録する。per-folder rootsの有無はcapability条件に含めない。requested値をobserved値として扱わない。prompt、developer instructions本文、AI応答本文、任意例外本文、認証情報、URL/query/DOMは出力しない。

MN05はcommon traceのschema_version/UUID span/JST/quota envelopeを実装したものとは扱わない。90日/4 MiB/日20件の保持・quota、atomic workflow events、stage相関はMN04の実装条件であり、MN05を本番運用可能とする前に別途確定する。U07での実モデル接続は行わず、LC01/LC02をU18で確認する。per-folder roots項目のないCLIでもreadOnly/workspaceWriteのbasic modeを使える場合はsessionを起動できる。

## U08 daily launcher ledger（MN01・schema v3）

日次launcherの起動/完了通知はmaintenance管理SQLite `D/maintenance/state.sqlite`の`daily_launch`と`daily_check`に保存し、日次L01/L02、日次業務DB、MN04 workflow診断JSONLへ混ぜない。`source_launch_id`とJST `target_date`を先にcommitし、起動owner/child PIDとrunning/terminal statusを結ぶ。日次子processがexport、終了保全、共通trace、L01最終記録を含む処理を終えて戻った後でだけ、結果summaryとcheckを同一transactionでterminal化する。terminal launch行はimmutableとする。

結果JSON schema `daily-launch-completion-v1`は`source_launch_id`、`target_date`、`status`、整数`exit_code`、nullable `source_run_id`、必須の`overall_status`/`daily_status`、safe `reason_code`/`failure_phase`、`failed_checks`、primary/secondary error class、`trace_status`を持つ。run未生成時はnullとし、架空IDを作らない。正常・不足・未実施の結果はparsed `target_date`が起動対象日と一致し、正常/不足なら`source_run_id`を持つ。失敗statusに成功overall statusを組み合わせず、not_dueはrun IDなしを許す。非文字列trace statusは`not_written`として安全に処理する。終了コード0なのにparse可能な結果がない場合、およびstatus・daily_check・終了コードが矛盾する子要約は契約エラーexit 4へ正規化し、Schedulerへ成功を返さない。記録/標準出力/標準エラーにはraw stderr/stdout、例外本文、stack、path、下書き数、秘密、個人情報を含めない。有効な日次結果の通知保存失敗時は起動行を`running`で残し、日次CLIの本来のexit codeを維持してU09 watchdogへ委ねる。

launcherはproduction管理DBを自動migrationしない。schema v4がない場合はdaily childを起動しない。U08では専用隔離DBのmigrationを確認し、production DBの初期化・移行、Scheduler登録、日次起動を行っていない。C32のrun-null基本summaryはU08で観測し、計画追加ログ契約全体はU13の受入対象とする。

## U09 watchdog確認queue（MN01・schema v4）

U09 migration `004_watchdog_confirmation_queue.sql`は`daily_launch.owner_start_time`/`child_start_time`、`watchdog_cursor`、running launch用partial indexと、task key/target dateから作る安定した`occurrence_key`およびnullable `source_launch_id`がそれぞれuniqueな`watchdog_queue`を加える。schedule時刻が変わっても同じ対象日のqueueを再利用する。cursorは日付を後退させず、各runで新しい予定日、当日、未解決queue、running launchの対象日だけを再照合する。未解決queue日は現在のScheduler開始境界より古くても再読込する。U08以前のprocess開始時刻がNULLの行は推測補完せず、該当PIDが動作中なら所有不明として保留する。開始時刻不一致はPID再利用とみなして旧processの停止確認に使うが、別processを終了しない。child PID未記録は子なしの証明ではなく保留する。ownerと記録済みchildの両方について停止またはPID再利用が確認できた未完了起動だけを確認queueへ入れる。

日次予定の欠落はScheduler historyに依存せず、日次taskとwatchdog監視開始のschedule境界から生成する。5分の猶予後も当日の`daily_launch`がなければ`unknown/daily_launcher_not_started`として一度記録する。正常な遅延起動が後から現れた場合は同じoccurrenceへsource launchを結び、`awaiting_completion`または`resolved`へ進める。後着launchが通知前に停止した場合はそのqueueのreasonを更新し、同じ`source_launch_id`を持つ別queueを作らない。cursorより前の日付を毎回再走査せず、当日と未解決queue/running launchの日付は継続照合する。schedule時刻の変更後もtask/対象日で同じqueueを特定し、既存queue日は現在の開始境界より古くても読み直す。未解決・所有不明はqueue上に残し、watchdog自身はdailyを再起動せず、workerは通常dispatchだけで起動する。`daily-watchdog` leaseが不明/生存中ならそのrunは状態を更新しない。

## U09 task XML・登録手順

`RakutenRoomInagoWatchdog.xml`は1分ごとにmaintenance CLI `watchdog`を起動し、`MultipleInstancesPolicy=IgnoreNew`と`ExecutionTimeLimit=PT0S`を使う。`RakutenRoomInagoWorker.xml`はtriggerなしで、通常queue dispatch後の明示起動に限る。`register_maintenance_tasks.ps1`は既定でXMLをrenderするだけで、`-Register`指定とShouldProcess承認なしにtaskを登録しない。実登録前に両task名の衝突、CLIの`worker` command、および`worker --help`に実際の`--once` optionがあることを確認し、登録後検証失敗時は今回追加したtaskだけをrollbackする。U09時点では`worker` commandが未実装なので、登録経路はfail-closedとなる。U09ではScheduler登録・production DB migration・実日次起動をしていない。

## Maintenance保守JSONL（MR-012/013/014/021/022）

これは将来実装する保守基盤専用の診断JSONLであり、L02の日次共通traceのschema/path/quotaへ混ぜない。計画保存先は `D/maintenance/logs/<実行日>/<maintenance_id>.jsonl`。正確なschema_version、field enum、atomic append、quota処理はU05/U10で実装に合わせて確定する。予定fieldは `event_id`、`maintenance_id`、`source_launch_id`、nullable `source_run_id`、`target_date`、nullable `incident_id`、`stage`、`attempt`、`phase`、safe `status`/`error_code`、`at`、`requested_model/observed_model`、effort/tier、`base_commit/result_version`、安全な入力/証拠/出力参照、`reviewed_version`、`retention_expires_at`。件数/試行番号は非負整数または正整数の範囲検証を行う。

新規maintenance log retentionは90日、1ファイル4 MiB、1日20ファイルを初期値とし、1ファイル単位で期限管理・rotateする。監査必須eventを保存できない場合は省略を隠さず、stageまたはdeployment/releaseを保留して欠測を記録する。採取証拠の写しは元データの期限を引き継ぎ、元期限がない場合だけ採取後90日を初期値とする。既存L01～L05、O01～O03、A02/A03、M01/M02等の現在の保持規則を変更しない。

診断記録にprompt、AI応答本文、商品/投稿本文、URL/query/DOM、credential、秘密、自由文exception messageを含めない。primary/cause/cleanupを分け、書込失敗やquota到達はhealthy/successとして再表示しない。maintenance管理DBのcommit済み状態と元日次状態をtraceの成否で上書きしない。

## U10 evidence bundle・分類manifest（MN06）

MN06は保守調査用の証拠索引と伏字済みJSON/JSONL写しであり、MN04 workflow trace、MN05 app-server transport log、日次L01/L02、業務DBとは別に扱う。保存先はprivate `D/maintenance/evidence/<target_date>/<job_id>/<attempt_id>/manifest.json`とし、artifact pathはmanifest内のbundle相対参照だけを使う。source rootの実体identityをno-followで開いたhandleから取得・照合し、POSIXではroot descriptorからdirfd相対open、Windowsでは削除共有を拒否したroot handle相対`NtCreateFile`を`OBJ_DONT_REPARSE`付きで行う。Windowsのleafは`FILE_OPEN_REPARSE_POINT`で開き、reparse属性とfinal pathも確認する。source pathに対する事前の`exists`/`lstat`検査は行わず、安全なhandle openの結果で存在を判定する。カタログ時点で存在を確認済みのsourceがopen時に消えていれば`disappeared`、元から未保存等と判明していれば指定されたmissing statusを返す。JSON/JSONL/response sourceはopened handleがregular fileと確認した後にサイズを判定して読む。特殊fileは取得不能とし、可能な環境ではnonblocking openで読み取り停止を防ぐ。物理source path、raw CLI rollout、raw DB、認証・browser stateをAI入力や共有文書へ含めない。

manifest schemaは`automation/inago/schemas/maintenance-evidence.schema.json`（`maintenance-evidence-v1`）。26 class IDは`L01`～`L05`、`O01`～`O03`、`A01`～`A03`、`M01`～`M03`、`S01`～`S03`、`W01`、`V01`、`H01`～`H02`、`B01`～`B02`、`X01`～`X03`で固定する。各classにstatus、artifact count、取得済みrecord countまたはnullを必ず記録する。`not_listed`は入力manifestに分類が現れなかった意味だけを持ち、source不存在を意味しない。source単位では`collected`、`content_omitted`、`empty`、`empty_response`、`invalid`、`invalid_response`、`not_saved`、`expired`、`unavailable`、`suppressed`、`disappeared`、`omitted_size_limit`、`no_correlated_records`を区別する。

event項目はsafe ID、時刻、固定allowlist内のstatus/code/countだけとし、自由文を保存しない。元時刻形式とUTC正規化値を両方保持する。対象run/job/request/action IDが一致するeventは対象日のJST境界を越えて相関し、IDがないeventは対象日のJST日付で選別する。source format、retention status、元expiry値/UTC値、取得不能理由を記録する。元sourceの期限は変更しない。

`S01`/`B01`/`B02`のSQLiteはread-only接続のbackup APIで専用private snapshotへ出力し、integrity/foreign-key checksを行う。snapshot hashはbounded chunk readで算出し、snapshot自体はredacted artifactではなくAIへ渡さない。`V01` HTML、`H01` browser store、binary/unstructured text/XMLもraw内容をmanifestやAI入力へ入れず、必要なadapter/取得不能stateを記録する。`X02` rolloutは要求側で`thread_id`を必須にし、record側も同じIDが一致する場合だけ明示allowlist fieldを抽出する。X02は日付だけのfallbackを使わない。E0 manifestの読取は形式・件数・分類indexに限定し、参照先の原本を自動で開かない。

時刻のraw値は厳格なISO日時または有効なUnix秒だけを保持し、parse失敗・書式外・秘密検出値は`null`にする。event/status/component/operation/error等のstring fieldはfield別の有限enumで検証し、未知値は出力せずdrop数へ加算する。daily result statusの`preservation_failed`/`export_failed`/`metrics_failed`は保持する。identity keyはcasefoldして照合し、異なる値を持つ正規化後の重複keyがあるrecordは除外する。credentialらしいGitHub形式のIDは拒否する。日付fallbackは関連identity fieldを一つも持たないrecordに限り、別IDまたは不一致IDを持つrecordは除外する。artifact file名はopaqueな連番とし、入力`source_ref`をWindows file nameとして使わず、writerも生成形式外のpathを拒否する。schemaは26 class IDの各1件、class別source format、X02の`thread_id`を要求し、class集計専用の`not_listed`/`partial`をsource artifact statusで拒否する。

redacted bundleはstage directoryへ新規作成し、artifactとmanifestを全てflushした後に同じ親内でatomic renameする。書込不能・容量上限・不正入力では成功manifestを残さず、保存失敗を明示する。証拠写しの保持期限/容量/孤児回収はMN04とU17 retention契約を適用する。collectorのroot containment/reparse確認はhost側のpath integrityであり、CLI起動前にfolder ACLを設定する条件ではない。

## U11 check-result v1（MN02 preclassification）

U11の純粋判定器は完了通知・当日結果・MN06 manifestをtarget_date/source_launch_id/nullable source_run_idで照合し、`maintenance-check-result-v1`を返す。MN06全体を `automation/inago/schemas/maintenance-evidence.schema.json` で検証し、必須field/型/formatが不正なら証拠完備にしない。正本schemaは `automation/inago/schemas/maintenance-check-result.schema.json`。出力はsafe ID、固定status/reason enum、範囲内count、許可phase、必要証拠class状態だけを持つ。欠測数はnull、run未生成はnull、previous dashboard countは入力項目にしない。OS exit code単独を成功条件にしない。

正常候補・候補不足・回復済みretryは[1] checkだけへ渡す。外部停止は待ち、観測済み失敗は調査へ回す分類、identity/count/retry/phase矛盾は契約違反、観測不足はunknownとする。出力routingは常に `check_only`、自動dispatch false、開始済みstep空、incident書込false。AI check呼び出しとMN02への永続化はU12の対象で、U11の判定関数は状態を変更しない。

正常候補判定で必要なU10 classはL01/L02/M03/S01/V01/B01/B02で、各statusがcollectedであることを確認する。外部stop codeは `authentication_required`、`shared_deadline_exhausted`、`source_cancelled`、`source_timeout`、`transport_error`。全reason/status enum、15件目標と補充3の分離、不足数、retry/phase invariantsはAT-MAINT-01 U11および同schemaを参照する。
