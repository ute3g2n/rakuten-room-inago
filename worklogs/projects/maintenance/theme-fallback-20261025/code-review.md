# テーマ単独補充：独立製品コードレビュー

- reviewer: `/root/room_zero_review`（製品実装作者とは別担当）
- 作業日: 2026-10-25 JST、固定root `C:/project/rakuten-room-inago`。
- 対象: `automation/inago/orchestrator.py`、`automation/inago/daily_theme_planner.py` のbase `ac99547` からの製品差分。
- RED checkpoint / 観測HEAD: `97820674ed9b60008a3c654c7324f6577c3ba778`。
- 初回観測SHA256: orchestrator `13f6a821fc607ff0de9b115c7bcef57a4976afbdf6b2cb8e5459328c7335e13e`、planner `6a18de0d44521232d1fa7f3e1b98c8ae3faf6b0f9c7f087c761558ad5c14b7f4`。
- 最終再確認SHA256: orchestrator `f3ac4de9e39251298b4d8cf9e6040673673567b4d4b4532eb41da64b9e9a8cf9`。plannerは上記と同じ。
- 中間再確認版: orchestrator `4c8741ee1e75f606bd99542699f2e67ee4480b551b1b129c96730e4d771f41dd`、`3ebfd62cea183ce04f461ee5f51ad1889a786ee968c70b5eb176aa080a3953d2`。
- 最終製品レビュー判定: `approved`。F01～F04はいずれも更新実物で解消を確認し、追加の旧v2互換情報保持も再確認した。最終回帰全体はレビュー時点で実行中であり、この判定は未完了の試験を合格へ読み替えない。所見は後続工程の開始gateではない。

## 適用とレビュー範囲

AGENTS.md、プロジェクト全体マップ、common-reviewを再読。要件定義HTMLの第13章（`chapter-13`）の全文を確認した。本文確定5件未満の補充、同取得結果の次候補優先、同run同queryの保存結果再利用、追加テーマ枠、旧checkpointでの識別子保持を対象とした。試験コード自体の独立レビューは行っていない。

`git diff --staged` / `git diff` により製品差分を読み、関連するplanner構成、task登録・再照合、run再開、本文件数・候補件数、選択/OCR/本文処理、候補history、入力cleanup・再capture・回復処理を読んだ。製品・試験コードの編集、DB操作、ブラウザー操作、日次起動、Git stage/commit/pushは行っていない。本review.mdだけを所有する。

## 所見

### F01 [HIGH、修正後解消] 検索taskが空の旧テーマの補充taskが復元されない

- 初回箇所: `orchestrator.py:1646～1655`、`_reconcile_pending_theme_tasks` の `theme_has_tasks` 条件。
- 問題: 旧planでraw関連語あり・KEY全除外の場合、`related=()` / `fallback=None` / 当該テーマtaskなしが保存される。`initial_plan_index`または`additional_events`は既に進んでいるため、正規化でfallback計画が復元されても `theme_has_tasks=False` でtaskへ登録されず、そのテーマ本文0件のまま次へ進む。
- 対応要件: 第13章の全関連語消化後のテーマ単独補充、旧checkpoint復元。
- 修正要求: 既存taskの有無だけでなく、そのplanのtask登録段階通過を保存済index/eventから判定し、既存task番号・cursorを保持して不足fallbackを登録する。
- 再確認: 更新版1646～1667行に `theme_was_enqueued` と初期index/追加eventによる判定が追加されたことを確認。plan保存後・task登録前の中断状態と、task登録済だが0件だった状態を区別する。
- 実結果証拠: `resume-red.txt` は `test_resume_restores_missing_fallback_without_renumbering[empty-key-selection]` がFAIL（1 failed, 2 passed、0.41s）。`resume-green.txt` は対象再実行2 passed、4.05s。reviewerが試験を実行したものではない。

### F02 [HIGH、修正後解消] 旧active候補の不採用で空きができても同取得結果の次候補へ進まない

- 初回箇所: `orchestrator.py:4411～4417`、`_process_search_result` の `next_selection_order` 不変によるbreak。
- 問題: 既選択5・未確定の旧checkpointと、同保存結果に未使用6番目がある場合、選択段階は候補5件のため追加せずorder不変になる。その後の本文処理で4確定・1不採用になってもorder不変だけでbreakし、6番目を先に処理せずcleanupへ進む。
- 対応要件: 候補5件を達成扱いしない。同じ取得結果の次の利用可能商品を先に使う。
- 修正要求: コピー処理で候補がactiveから外れた変化も進捗として扱い、空き枠ができたら同結果を再選択する。
- 再確認: 更新版4403～4405、4435～4440行に `previous_active` の集合比較が追加されたことを確認。order不変でも既active候補の確定/不採用による変化があれば次の反復へ進む。
- 限界: このループへ到達する前の旧processed attempt問題はF03に分離する。

### F03 [HIGH、修正後解消] processed関連attemptの旧未確定候補を処理せず補充検索を開始する

- 箇所: 更新版 `orchestrator.py:5366～5380` と補充task実行経路（更新前行番号）。`_process_search_result` の処理済attempt早期returnも関連する。
- 問題: 旧checkpointで関連attempt処理済・同テーマselected5未確定・cursorが補充task直前、日次選択数15未満・selection_flush_due=Falseの場合、再開直後の本文処理条件を満たさない。そのまま補充taskを実行し、関連結果だけで5本文確定できた場合にも不要なテーマ単独検索を発行する。同じ関連結果の余剰候補による補充も先に行われない。
- 対応要件: 本文確定件数で5達成を判定し、同保存結果を先に処理してから不足時だけ1回補充。
- 修正要求: 補充task判定前に当該テーマの既active候補を処理し、必要に応じて関連attemptの保存結果を再利用して余剰候補を使う。既存attempt・本文・task番号を保持する。
- 実結果証拠: `processed-resume-red.txt` の `test_resume_processed_related_search_commits_before_deciding_fallback` は期待query_kind `[related]` に対し `[related, theme_fallback]`、1 failed、4.06s。
- 再確認: 最新版5390～5417行で、active候補の `(source_attempt_id, theme_id)` を保存順に抽出し、通常task判定前に `reuse_saved_result=True` で既存結果を処理する。欠測IDのactive候補も5418行以降の本文処理へ渡す。新しいROOM検索を起こさず、既存結果の余剰候補を処理してから補充判定することを確認した。`boundary-result.txt` の当該nodeを含む13 nodeは13 passed、36.18s。

### F04 [HIGH、修正後解消] 保存結果再利用先での再capture失敗を元テーマへ戻して回復する

- 箇所: 更新前 `orchestrator.py:3293～3298` のcapture failure保存、`3591～3613` の回復theme決定。新たな保存結果再利用・recapture経路は `4571～4614` と `3262～3270`。
- 問題: テーマBのfallbackがテーマAの同query attemptを再利用し、cleanup済み未使用候補の再captureに失敗すると、failureは元Aの `source_attempt_id` だけを保持する。次のresumeは `_task_theme_for_attempt` で元Aのthemeを復元する。Aが既に5本文ならtheme_remaining=0として回復を試さず、Bの不足候補として処理されない。Aに空きがあればBの補充候補がAへ割り当てられる。
- 対応要件: 同run同query再利用時の補充先テーマとの対応を保持し、保存結果・未処理候補を再利用する。
- 修正要求: capture failureへ今回の補充先 `theme_id` を保存し、回復時に有効な保存割当を優先する。旧failureだけは従来の元attemptから復元する。既に確定済みの割当は変更しない。
- 証拠種別: 製品コードの制御経路読取による所見。reviewerによる実capture失敗/DB再現は未実施。親担当へ即時引継いだ。
- theme継承の再確認: 最新版3312行でfailureへtheme_idを保存し、3609～3613行でそれを優先、旧recordだけ元attemptから推定する。回復groupも `(attempt_id, theme_id)` に分ける変更を確認した。
- **中間版で検出した副作用（旧3726～3733行）**: 各group直後のcleanupが全group共通の `recovered_unselected` で候補を絞っていた。同じ元attemptでthemeA用X・themeB用Yが回復した場合、両方をrecaptureしてからgroupAのXを選ぶが、その直後のcleanupが未選択のY入力を削除する。次のgroupBでは `recapture_missing_inputs=False` の選択処理となり、回復直後のYが再びcapture失敗になる。cleanup候補を当該groupのrecovered offer集合だけに限定するか、全group選択後にcleanupする修正を要求し、親へ即時連絡した。
- **最終版の再確認**: 3712～3714行に当該groupの `group_offer_keys` を構成し、選択のrecovering集合と3733～3736行のcleanup候補filterの両方へ使用する変更を確認した。後続group用の入力を先行groupのcleanupへ渡さなくなり、副作用は解消。
- 実結果証拠: `recovery-group-red.txt` の `test_recovery_of_two_themes_from_one_attempt_keeps_later_group_inputs` は後group候補欠落で1 failed、1.22s。`final-local-green.txt` は同nodeと有効v2再開nodeを合わせて2 passed、2.00s。途中の `cleanup-local.txt` はcapture要求数の期待不一致で1 failed, 1 passed、6.41sとして残っており、成功証拠と混同しない。

## 最終追加差分：旧v2計画の互換情報保持

親担当の局所診断では、有効な旧v2計画を再開した際、`_get_plan` がfallback/taskの正規化でplanを保存し直すと `legacy_acquisition` markerが消え、次の再読で新形式の取得証拠を要求して失敗していた。最終版1790～1794行は、既存の `legacy_plan` 判定が真だったplanだけ `legacy_acquisition=True` を保存し直す。

`legacy_plan` は移行元schema2/3と保存markerに限定され、現在planへ無条件に互換扱いを広げていない。`_plan_from_dict` は現在fields＋markerの形を既に受け付けるため、正規化後も読み戻せる。既存task番号・確定本文・query・snapshot参照を変更する修正ではない。`v2-local-diagnostic2.txt` の失敗（theme_snapshot_or_related_queue_missing、1 failed、0.91s）と、上記最終局所2 passedの証拠を照合した。テストfixture自体の独立レビューは実施していない。

## 指摘以外の確認

- plannerはdeadline以外のテーマへfallbackを構成し、`_make_tasks` は選択関連語の後ろへfallbackを置く。本文5件達成時は実行ループで後続taskをskipする。
- `_theme_draft_count(state=...)` はdraft表に存在するofferだけを数え、run内の `offer_theme_ids` を優先する。選択中候補だけで本文件数は増えない。
- 新規追加テーマ枠は `_draft_count` に基づく `min(5, 15-確定本文)` へ変更されている。
- 同query再利用はrun/queryをパラメーター化SQLで限定し、historyは現在の確定・同日選択・投稿/予約等を再評価する。再利用先themeは新たな選択manifestへ記録される。既に確定済み・失敗確定の候補はhistory/manifestで再選択しない。
- 未選定cleanupで消えた商品入力は再利用時だけ既存 `_capture_saved_product_page` で復元する。run/shop/item/page_ref検証は維持されている。回復時のtheme継承不足はF04として分離。
- 検索task番号は振り直さず、新規taskはmax+1として既存attempt ID対応を維持する。
- 変更箇所に新規の秘密直書き、SQL文字列連結、秘密ログ出力は確認しない。新しい回数/時間上限や後続開始gateは要求していない。

## 利用可能な試験結果と限界

- `red.txt`: 9 failed, 1 passed、25.39s。
- `local-failed-nodes.txt`: 対象9 nodeの完全名を確認。
- `local-green-attempt1.txt`: 当該9 node、9 passed、24.21s。
- resumeおよびprocessed-resumeは各findingに記載。テストコード自体は独立レビューしていない。
- `boundary-nodes.txt` の13 nodeと `boundary-result.txt` を照合し、13 passed、36.18sを確認。
- `regression-attempt1.txt`: 初回回帰は15 failed, 162 passed、172.73s。
- `baseline-old-expectations.txt`: base ac99547での代表4 nodeが4 failed、3.37s。14件すべてのbaseline実行証拠ではないため、そのような断定はしない。
- `regression-local-fixes.txt`: 1 failed, 14 passed、6.22s。残る有効v2再開はその後上記製品修正と `final-local-green.txt` で確認。
- `regression-final-targets.txt` に最終回帰の完全指定を確認。最終再確認時点の `regression-final.txt` は出力未完了であり、全178case想定の回帰結果はまだ未確認。
- `git diff --check` は終了コード0（CRLF警告のみ）。
- reviewerは試験コマンドを実行していない。試験ログ単体にコマンド/版がない箇所は親担当の最終実行記録で版対応を補完する。最終回帰全体・本番日次・実ブラウザーの成功は未確認。
- 本件の所見は純粋な制御フロー・保存状態に関するもので、画面原因の調査はしていない。スクリーンショット・動画・traceをreviewerは取得していない。

## Review Summary

| Severity | 未解決Count | Status |
|----------|------------:|--------|
| CRITICAL | 0 | pass |
| HIGH | 0 | pass |
| MEDIUM | 0 | pass |
| LOW | 0 | pass |

Verdict: APPROVE — F01～F04と追加v2互換情報保持の更新実物を確認し、未解決の製品コード指摘なし。最終回帰全体・本番日次・実ブラウザー確認は別の結果として保持する。
