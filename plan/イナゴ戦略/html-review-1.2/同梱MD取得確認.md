# 要件データ辞書

版1.1補足・2026-10-03 JST：正本はイナゴスキル要件定義書.html。以下の版1.0確定履歴を保持し、末尾に28章のAIジョブ契約を追加する。

版1.0・確定記録2026-09-30 11:42:16 JST。IR-016の補足、STEP 13で型/必須性を明確化しQ-FINAL/D-060で確定。取得できない情報を埋めるSchemaではない。例は架空の型例であり実商品/実観測ではない。!は保存時必須、?は任意/未取得null。配列は0要素可。ただし各行の個別制約が優先する。timestamp=ISO8601オフセット付きJST、date=JST YYYY-MM-DD、integer>=0、numberは有限数、string!は原則非空。
共通表の複数項目は各項目を別列として持ち、/を連結した単一値にしない。entityの列名は実装者が変えられるが型・意味・欠測・制約を維持する。unknown/unavailableを数値0へ変換しない。

## 共通項目

| 項目 | 意味 | 型/必須 | 型例 | 取得元/未取得・保持 |
|---|---|---|---|---|
| run_id | 日次/操作実行の一意キー | string! | inago-run-example | 実行管理、運用中保持 |
| target_date | 作成対象のJST暦日 | date! | 2026-09-30 | 起動入力、前日追生成なし |
| mode | 実行操作 | enum! | daily | setup/daily/status/recovery/config-change、取得/生成はdaily内 |
| task_id | 28章のタスク参照 | enum! | T02 | T01～T11。modeと別で、日次内T04を別日次runにしない |
| status | 対象の状態 | enum! | active | entityごとの17章/下記列挙、未取得はavailabilityへ |
| settings_version | run開始時の設定版 | string! | settings-example-1 | 本人確定、再開で固定 |
| seed | 抽選再現の種 | string! | seed-example-1 | run保存、再開で維持 |
| offer_key | 店舗＋商品IDの識別キー | string! | shop_example:item_example | 公開商品URL/本人補完、ID未取得は候補でnull |
| shop_id/item_id | 店舗/商品識別子を別保持 | string! | shop_example / item_example | 商品URL等、値を創作しない |
| draft_id/version | 本文識別子/版 | string! / integer! | draft-example / 1 | 生成/本人修正、version>=1 |
| body | 完成本文 | string! | 本文の型例 | 生成/本人修正、空白だけ不可 |
| reported_at | 本人が報告した時刻 | timestamp! | 2026-09-30T18:05:00+09:00 | サーバー受領時、実投稿時刻と別 |
| actual_posted_at | 本人が特定できた実投稿時刻 | timestamp? | 2026-09-30T18:00:00+09:00 | 本人、分からなければnull |
| observed_at | 取得/観測時刻 | timestamp? | 2026-09-30T17:50:00+09:00 | 取得成功時、失敗ならnull可 |
| created_at/updated_at | 作成/最新更新時刻 | timestamp! | 2026-09-30T13:00:00+09:00 | システム、元イベント時刻を上書きしない |
| source_ref/evidence_refs | 原記録参照/参照集合 | string? / array<string>! | artifact-example / [artifact-example] | 参照台帳、配列0要素可、削除後expired |
| value/unit | 観測値/単位 | numberまたはstring? / enum! | 0 / count | 成功0と欠測nullを区別、unit=count/JPY/seconds/bytes/ratio/text |
| availability | 観測可否/未知の種類 | enum! | unknown | observed/unknown/unavailable/not_present/unmatched/expired |
| revision | 競合検出用更新番号 | integer! | 1 | DB、>=0、expectedと比較 |
| reason/error_code | 判断理由/エラー種別 | string? | generation_empty | 状態に応じ必須、秘密値を入れない |

## エンティティごとの全初期項目と制約

先の共通項目名を単独記した場合は共通表の型を適用する。複合JSONは内部要素の型も示す。付加フィールドは版付き拡張とし、空列を商品適格性へ変えない。

| Entity | 初期フィールド/型/必須性 | 値・列挙/意味の補足 | 取得/期限/制約 |
|---|---|---|---|
| settings_version | version:string!、confirmed_at:timestamp?、genres:array<genre>!、themes:array<theme>!、pattern_refs:array<string>!、change_reason:string? | genre={genre_id:string!,name:string!,definition:string!}、theme={theme_id:string!,genre_id:string!,name:string!,definition:string!,included_examples:array<string>!,boundary_examples:array<string>!,evidence_refs:array<string>!,material_count:integer?,material_kind:string?,state:activeまたはretired!,selection_count:integer!} | 5 genres/30 active themes。初期設定は別計画で本人確定した結果を登録。材料数・種別は提供された場合だけ保存し、未提供ならnull |
| run | run_id/target_date/mode/status/settings_version/seed、started_at:timestamp!、draft_deadline/end_deadline:timestamp!、attempt_used/committed_count:integer!、checkpoint:string?、metrics_status:enum!、finished_at:timestamp? | run status=created/running/complete/partial/failed/shortfall/missed、metrics=pending/observed/partial/unavailable/not_started | attempt0..9、committed0..15、target_date+dailyの一意制約。日時例は共通 |
| search_attempt | attempt_id/run_id/query:string!、parent_refs:array<string>!、level:enum!、attempt_no:integer!、started_at:timestamp!、ended_at:timestamp?、source_status:enum!、source_order:enum!、max_posts:integer!、retry_of:string?、error_code:string? | level=combined/parent、source_order=internal、source_status=observed/partial/unavailable/invalid | attempt1..9、max_posts=50。失敗も1開始イベント |
| source_post | source_key:string!、stable_room_id/room_url:string?、attempt_id:string!、order_index:integer!、likes/comments:integer?、item_url/image_ref:string?、actor_ref:string?、published_at:timestamp?、evidence_ref:string!、observed_at:timestamp!、availability:enum! | source_keyは恒久IDまたは暫定キーと区別。コメント条件なしでも取れた場合保存 | likes/comments>=0、order1..50、原本文/画面90日。取得不能値null |
| offer | offer_key/shop_id/item_id、name/url:string!、image_refs:array<string>!、main_theme_id:string!、aux_theme_ids:array<string>!、first_seen/last_seen:timestamp!、facts:array<fact>! | fact={field:string!,value:numberまたはstring?,availability:enum!,source_ref:string?,observed_at:timestamp?,original_display:string?} | name/urlは本文/HTML入力。facts0要素可、全事実unknownでも選出後生成可 |
| candidate_decision | decision_id/run_id:string!、offer_key/source_key:string?、stage:string!、primary_reason:string!、secondary_reasons:array<string>!、representative_ref:string?、representative_likes:integer?、rule_version:string!、decided_at:timestamp! | 少なくともoffer_keyまたはsource_key。例primary=already_posted、副理由配列 | 一次理由1つ、各段階数で母数を定義。取得投稿→代表→本文を混ぜない |
| keyword_snapshot | snapshot_id/parent_word/source:string!、geo/range/search_type/list_type:string!、words:array<word>!、fetched_at:timestamp?、availability:enum!、filter_decisions:array<object>! | word={raw:string!,normalized:string!,source_rank:integer!,popularity_index:number?,semantic_reason:string!,is_child:boolean!} | geo=JP、range=90d、search_type=web、list_type=Top。words=[]は取得成功0か失敗をavailabilityで分離 |
| theme_selection | selection_id/run_id/theme_id:string!、event_kind:enum!、count_event_id/seed/result_word_refs:stringまたはarray<string>!、selected_at:timestamp! | event_kind=initial/additional、result_word_refsはarray、その他string | count_event_id一意、同run再開で同じevent。語句未確定は配列[] |
| draft | draft_id/offer_key/run_id/version/body、generated_at/committed_at:timestamp!、generator_version/pattern_version:string!、status:enum!、artifact_ref:string? | status=committed/human_edited/posted/skipped/do_not_post_already_posted | 完成本文は空不可。生成版は保持、同run+offerの生成初版一意 |
| generation_attempt | generation_attempt_id/run_id/offer_key:string!、draft_id:string?、attempt_no:integer!、outcome:enum!、artifact_ref/error_code:string?、started_at:timestamp!、ended_at:timestamp? | outcome=success/empty/error/in_progress、試行例1 | 候補ごと1..3、再開で回数維持、空本文をcompletedへ登録しない |
| reservation | offer_key/draft_id、status:enum!、created_at:timestamp!、released_at:timestamp?、release_reason:string? | status=active/released、release_reason=posted/skipped/valid_prior_post | 期限なし。offer_keyにつきactive最大1、本人報告で関連全予約を整合 |
| publication_event | event_id/offer_key:string!、draft_id:string?、draft_version:integer?、reported_at/actual_posted_at、room_url:string?、evidence_kind:enum!、valid:boolean!、revision:integer! | evidence_kind=human_check/legacy_evidence/human_statement、actual date例は共通 | 訂正は別イベントで参照、他有効証拠を消さない。原作成日と報告日別 |
| human_action | action_id/action/offer_key:string!、draft_id:string?、prior_revision:integer!、reason:string?、reported_at:timestamp!、old_event_ref:string?、new_body:string? | action=posted/skipped/correction/edit、reasonはcorrection必須 | 過去見送り商品でも本人の実投稿事実を受け入れる。実投稿版を保持 |
| exclusion_key | offer_key、reason:string!、evidence_refs:array<string>!、effective:boolean!、updated_at:timestamp! | reason=actual_posted、evidence refs例は共通 | 少なくとも1有効証拠でeffective=true。本文作成だけではtrueにしない |
| metric_observation | observation_id/scope/ref/metric:string!、value/unit、observed_at:timestamp!、window:string?、availability:enum!、source_ref:string?、provisional:boolean? | scope=day_aggregate/room_post/order_item/entry_post、metric=clicks/likes/sales_count/sales_yen/commission_unconfirmed_yen | 値>=0の通常観測、取消/変更は元statusと履歴。1年保持、P未投稿は対象外 |
| revenue_order | order_ref:string!、is_temporary_ref:boolean!、occurred_at:timestamp?、shop_name/product_name/item_code/product_url:string?、sales_yen/commission_yen:number?、order_status/link_type:string?、source_ref:string!、match_kind:enum!、matched_offer_key/entry_post_ref:string? | match_kind=unmatched/purchased_item_match/entry_post_confirmed | 直接入口証拠なしならentry_post_ref=null。公式CSVでない列を創作しない |
| artifact | artifact_ref/path/hash/source_kind:string!、created_at:timestamp!、retention_expires_at:timestamp?、status:enum! | source_kind=external_analysis_result/daily/metrics/generation、status=available/expired/missing | 絶対パスは新root内、hashは内容ハッシュ。raw90日、削除後参照status |
| recovery/backup | operation_id/type:string!、run_ref/checkpoint/cause/owner:string?、status:string!、backup_path:string?、restored_at:timestamp?、validation:object!、observed_at:timestamp! | type=backup/restore_check/recovery、validation={db_integrity:boolean?,counts_match:boolean?,details_expired_filtered:boolean?,errors:array<string>!} | 新rootとbackup外部領域を識別、稼働正本を復元テストで上書きしない |

## 関係と一意性

run→attempt/source/selection/draftは外部キー。offer→draft/reservation/publication/exclusionはoffer_key外部キー。publicationは公開に使ったdraft版を参照し、後の本人修正で入口本文を上書きしない。
新日次(target_date,daily)は一意。同run同offerの生成初版は一意。active reservationはoffer_key一意。publication/human_action/選択count_event/attempt開始はidempotency keyで重複を防ぐ。誤入力訂正は原イベント参照を持ち、有効証拠集合から除外キーを再計算する。
本人の古いHTMLから実投稿申告を受けた場合は、同offerの後発active予約を全て解除し、後発本文はdo_not_post_already_postedへ。本文生成件数の履歴は減らさず、現在投稿に使える件数を別に表示する。本文を削除して帳尻を合わせない。
raw証拠の期限削除は外部キーの参照キーを残してexpiredへ。復元時に有効な投稿/未解決予約を優先し、expired artifactを今ある根拠に戻さない。バックアップ後の本人操作が未補完なら新選出停止。
型例のIR-016/AT-016と、停止/競合/見送り後の本人投稿はAT-S14/19/20/25/26/29で実装テストする。

## 版1.2：AIジョブ契約（IR-028）

| 項目 | 型／必須 | 意味・制約 |
|---|---|---|
| protocol_version／schema_version | string!／string! | AI起動プログラムは、受け渡しと回答形式の版を別々に記録します。保存プログラムは、未対応の版の回答を処理エラーにします。 |
| job_id／task_id／agent_id／skill_name | string!／enum!／enum!／string! | 日次制御プログラムは、28章で許可した作業・AI・指示書の組合せを渡します。個別AI作業番号は日次番号とは別に付けます。 |
| attempt | integer! >=1 | AI起動プログラムは、同じ作業の生成回数を記録します。本文の初回と再試行2回を上限とし、再開でも回数を戻しません。 |
| input_refs／evidence_refs／input_hash | array<string>!／array<string>!／string! | AI起動プログラムは、実在する資料の参照と入力の照合値を渡します。保存プログラムは、AIが創作した参照を受理しません。 |
| deadline_at／started_at／finished_at | timestamp!／timestamp!／timestamp? | 日次制御プログラムは、前工程の終了直後に成果取得を始めます。17:45は前工程の例外的な打切り、18:00は成果取得の上限です。 |
| resolved_model／reasoning_setting／auth_mode | string!／string!／enum! | AI起動プログラムは、実際のモデル・推論設定・認証の種類を記録します。鍵やCookieの値を保存しません。 |
| result_status／payload／error | enum!／object?／object? | AI起動プログラムは、正常・空・エラー・打切りを区別します。失敗理由から秘密情報を除きます。 |
| artifact_ref／accepted_at | string?／timestamp? | 保存プログラムは、回答ファイルの参照と採用時刻を記録します。AIの生成成功だけでは完成件数を増やしません。 |
| adoption_status | enum! | 保存プログラムは、未採用・採用・形式不一致・期限後到着・競合を記録します。期限後の本文を新しい完成本文へ採用しません。 |

本文payloadはoffer_key・draft_version・body・pattern_id・evidence_refs。その他jobのpayloadは28.5の出力契約をJSON Schema化する。schema整合検査は商品事実確認／文章品質承認ゲートではない。実装名の変更は意味・型・採用順を維持する。

## 版1.2：事前分析との境界

実装者は、実装前の別計画で確定した分類・文型・本文長さ・タグ目安を初期設定として受け取る。システムは事前分析を実行しない。実装者はその専用スクリプト・AI・skillを作成しない。settings_versionは外部結果の登録先であり、artifactのexternal_analysis_resultは受領済み成果資料を示す。いずれも事前分析の実行機能ではない。
