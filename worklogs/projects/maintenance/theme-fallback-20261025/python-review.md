# Python製品コード独立レビュー

- 担当: `/root/theme_fallback_python_review`（実装作者とは別担当）
- 作業場所: `C:/project/rakuten-room-inago`
- 対象差分: `git diff ac99547 -- automation/inago/orchestrator.py automation/inago/daily_theme_planner.py`
- 観測HEAD: `97820674ed9b60008a3c654c7324f6577c3ba778`
- 観測版SHA256: orchestrator.py `13F6A821FC607FF0DE9B115C7BCEF57A4976AFBDF6B2CB8E5459328C7335E13E`、daily_theme_planner.py `6A18DE0D44521232D1FA7F3E1B98C8AE3FAF6B0F9C7F087C761558AD5C14B7F4`
- 最終再確認版SHA256: orchestrator.py `F3AC4DE9E39251298B4D8CF9E6040673673567B4D4B4532EB41DA64B9E9A8CF9`（plannerは上記と同じ）。
- 最終判定: **approve**。HIGH1・HIGH2とも実装担当の修正を独立再確認して解消。未解決のCRITICAL/HIGH所見なし。これは後続工程の開始・完了gateではない。
- 参照指示: AGENTS.md、プロジェクト全体マップ、common-review、python-patterns。`settings/language.md` は固定rootに存在せず、欠測として記録する。
- 権限範囲: 製品・試験・DBを変更せず、Gitのstage/commit/pushを行わない。レビュー記録のみ所有。テストコード自体の独立レビューは実施していない。

## 指摘

### [HIGH / 修正後解消] 旧checkpointで検索taskが0件のテーマには補充taskが復元されない

File: `automation/inago/orchestrator.py:1646`（条件は1650行）

Issue: `_reconcile_pending_theme_tasks` は `theme_has_tasks` が真のときだけ欠落fallback taskを追加する。旧実装は、Trendsのraw関連語が存在してもKEYの採用が0件なら `related=()`、`fallback=None` を保存し、そのテーマの検索taskを0件にする。既に `initial_plan_index` または追加テーマの進行が保存されているため、再開時の `_get_plan` でfallback計画を復元しても、この条件によって実行taskが追加されず、本文0件の当該テーマを補充しない。

対応要件: spec13の「関連語全消化後、確定本文5件未満ならquery_wordで一度補充」と、既存checkpointへのfallback復元。

Fix: fallback計画が存在し、当該phase/themeにfallback taskがない場合は、既存の関連taskが0件でも補充taskを作る。既存cursor・task番号を保持し、後続テーマより前の未処理区間に挿入する。旧checkpointの関連task0件・採用関連語0件について局所再開ケースを実行する。

最小局所確認（DB・ファイルの変更なし、メモリ上の状態のみ）:

```powershell
@'
from automation.inago.orchestrator import DailyThemePlan, DailySearchPlan, _reconcile_pending_theme_tasks
fallback = DailySearchPlan('T1-fallback-initial-01', 'T1', 'query1', ('T1',), 'theme_fallback', 'theme_fallback')
plan = DailyThemePlan('T1', (), (), fallback, 'snapshot-ref')
state = {'tasks': [], 'task_cursor': 0, 'plans': {'initial:01': {'theme_id': 'T1'}}}
_reconcile_pending_theme_tasks(state, plan=plan, event_kind='initial')
print({'case': 'legacy_zero_related_tasks', 'expected_tasks': 1, 'actual_tasks': len(state['tasks']), 'task_cursor': state['task_cursor']})
'@ | & '.venv-inago/Scripts/python.exe' -
```

期待: 補充task1件、cursor0。実結果: `{'case': 'legacy_zero_related_tasks', 'expected_tasks': 1, 'actual_tasks': 0, 'task_cursor': 0}`。終了コード0。再現入力・コード版は上記に記載。これはレビュー用の局所関数確認であり、実checkpointを用いた全体runの成功証拠ではない。

## 修正後の再確認

実装担当は `theme_was_enqueued` を追加し、既存taskの有無に加えて、初期テーマは `initial_theme_ids[:initial_plan_index]`、追加テーマは保存planのordinalと `additional_events` から、既にタスク登録段階を通過したことを判定するよう修正した。plan保存後・task登録前の中断状態では追加せず、旧checkpointで採用関連語0件だったテーマにはfallback taskを復元する。

上の局所確認のstateへ、初期ケースでは `initial_theme_ids=['T1'], initial_plan_index=1, additional_events=0`、追加ケースではplan keyを `additional:01` とし `initial_theme_ids=['T1'], initial_plan_index=0, additional_events=1` を加えて再実行した。両ケースとも期待task1件、実task1件、cursor0、task_number1、終了コード0。追加修正箇所の製品コードを読み、初回指摘を解消とした。

他担当の `resume-red.txt` は `test_resume_restores_missing_fallback_without_renumbering[empty-key-selection]` 失敗を含む `1 failed, 2 passed in 0.41s`、`resume-green.txt` は `2 passed in 4.05s`。試験コード自体の独立レビューは実施していない。既存番号・cursorを保持する製品処理も再確認した。

### [HIGH2 / 修正後解消] 旧checkpointの既処理関連検索で選定済本文を確定する前にfallbackへ進む

File: `automation/inago/orchestrator.py:5383` 付近（指摘時hash `3EBFD62CEA183CE04F461EE5F51AD1889A786EE968C70B5EB176AA080A3953D2` の再開開始時の本文flush条件）

Issue: 再開時の `_process_selected_copies` は選定総数15件以上または `selection_flush_due=True` の場合に限られる。旧checkpointで関連検索が `processed_attempts` に入り、選定5件・本文0件・task_cursorがfallback位置にある場合、関連検索結果の通常処理は既処理としてreturnし、本文未確定のままfallbackの要否を判断する。選定5件から本文5件を確定できるケースでも追加検索が行われる。

実行結果証拠: 他担当の `processed-resume-red.txt`、完全node `tests_inago/test_theme_fallback_replenishment.py::test_resume_processed_related_search_commits_before_deciding_fallback`、期待query_kind `[related]`、実結果 `[related, theme_fallback]`、`1 failed in 4.06s`。正確なコマンド・入力hashはこの証拠自体にはない。テストコード自体は独立レビューしていない。

Fix: 再開時に残存selectedの本文処理を済ませてから補充要否を判定する。既処理関連検索の保存候補も本文不足なら再利用できる順序を確認する。上記失敗nodeと直接影響を受ける再開nodeで修正後結果を記録する。

修正後再確認: 最終hashの5390〜5417行は旧activeを `(source_attempt_id, theme_id)` でまとめ、`reuse_saved_result=True` で保存結果を再処理する。通常taskへ進む前に本文を確定し、不足なら同じ保存結果の次候補を処理する。4401行付近の反復進行判定は `next_selection_order` とactive offer集合の両方を比べるため、新しい選定がなくても旧activeの本文不採用で空きが出た場合は次の反復へ進む。新たな外部検索を発生させず、既存attempt番号・cursorを保存する。HIGH2を解消とした。

再取得失敗の3309行付近は `theme_id=selected_theme` を保存し、復旧時は保存値を優先して `(attempt_id, theme_id)` ごとに選定する。テーマ値のない旧recordだけ元taskから推定するため、別テーマの保存検索結果を補充へ再利用した後の復旧先が元テーマへ戻る問題を防ぐ。この追加差分に新たな重大所見は認めない。

他担当の修正後証拠 `boundary-result.txt` は `13 passed in 36.18s`。`boundary-nodes.txt` にHIGH2失敗node、選定済/未選定capture復旧2node、attempt guard2node、変更影響の既存期待更新等を含む完全13nodeが保存されており、結果と一覧を照合した。テストコード自体は独立レビューしていない。正確な実行コマンドは親担当の実行記録に結ぶ。

## その他の確認結果

最終追加差分の独立再確認:

- `_recover_product_page_capture_failures` の3712〜3736行は `group_offer_keys` を当該 `(attempt_id, theme_id)` のrecovered候補だけから作り、再選定とcleanupの双方へ同じ集合を渡す。未処理の別テーマgroupの再取得済みページを先行groupのcleanupで削除しない。各groupの選定済み候補は従来通りmanifestのkeep判定でも保全される。範囲を狭める変更に伴う新規重大所見なし。
- `_get_plan` の1790〜1794行は正規化後のplanを保存する際、元の `legacy_plan=True` の場合だけ `legacy_acquisition=True` を保持する。legacy判定には移行元schema2/3と既存markerの両方が必要であり、新しいplanへ無条件にlegacyを付けない。`_plan_from_dict` の許可field集合および次回 `_validate_plan_snapshot` のlegacy境界と整合するため、正規化後の再読込で元の許可を失わない。新規重大所見なし。
- `final-local-green.txt`: `2 passed in 2.00s`（親担当からv2再開・mixed-group復旧の直接ケースとの引継ぎ）。テストコード自体の独立レビューは行っていない。
- `baseline-old-expectations.txt`: base `ac99547` をロードした実行結果に、代表4nodeの失敗を確認。最新製品の不具合がないことの証明には使わず、旧期待不一致がbaseにも存在する根拠としてのみ扱う。
- 最終2製品ファイルは `ast.parse` で構文確認し終了コード0。`git diff --check` も終了コード0。最終hashを実測して親担当の指定値と一致した。
- `regression-final.txt` はこの追記時点では空で、全回帰は実行中との引継ぎ。178件の合格を推定・先取りしていない。最終回帰の結果は親担当の最終記録へ結ぶ。

- `_theme_draft_count(state=...)` はdraft表に存在するofferだけを数えるよう変更され、選定中の商品を確定本文に含めない。`offer_theme_ids` を優先し、保存offerの `main_theme_id` と補充先テーマが異なる場合のrun内割当を反映する。
- 現用商品ページ経路は保存候補を選定→OCR→本文確定し、本文不採用で空きが出た場合は同じ保存結果の次候補を先に使う。`next_selection_order` の進行有無と確定数で反復終了するため、同じ候補の無制限再処理は見当たらない。
- 既に使ったqueryの補充は保存済attemptを再利用し、パラメーター化SQLでrun/queryを限定する。既に処理済のattemptの `source_failures` は二重計上しない。
- 再利用時には、未選定フォルダーのcleanupで商品ページ入力が消えている場合のrecapture呼出しが追加されている。既存helperはrun/shop/item/page_refを照合する。実ブラウザーを用いたrecaptureの成功は今回のread-onlyレビューでは未実測。
- 既存task番号は振り直さず、新規番号をmax+1とする変更により、永続化済search attempt IDとの対応を維持する。
- 新規SQL injection、コマンド実行、秘密情報の直書き、変更箇所での無条件例外握り潰しは確認しなかった。既存の大型関数・Any型等は今回の要求を妨げる新規重大所見としては挙げない。

## 機械確認と証拠の限界

- `git diff -- '*.py'` と対象製品2ファイルのbase差分を確認。表示に含まれた試験コードについて独立レビューの判断は行っていない。
- `git diff --check -- automation/inago/orchestrator.py automation/inago/daily_theme_planner.py`: 終了コード0（CRLF→LFのGit警告あり）。
- `& '.venv-inago/Scripts/python.exe' -m black --check automation/inago/orchestrator.py automation/inago/daily_theme_planner.py`: 初回・最終hash再確認とも終了コード1、2ファイルともwould reformat。既存整形差を含むため機能所見と混同しない。整形は変更していない。
- venvで `importlib.util.find_spec` を確認: ruff/mypy/pylint未導入、black導入済み。未導入の解析は未実施。
- `red.txt`: `9 failed, 1 passed in 25.39s`。`local-failed-nodes.txt`: 対象9nodeが列記されている。`local-green-attempt1.txt`: `9 passed in 24.21s`。これらは他担当の実行結果証拠として読んだ。記録自体には正確な実行コマンド・ソースhashがないため、このレビューだけでは同一版実行を確証できない。
- 全体run・本番DB照合・ブラウザー操作は実施していない。画面原因を調べるレビューではなく、指摘は純粋な状態変換で再現できるため、スクリーンショット・動画・traceは取得していない。
- 最初の最小局所確認は `snapshot_ref` 引数不足でTypeError（終了コード1）となり、上記の完全な引数へ修正して再実行した。これを製品不具合とは扱っていない。

## 自己評価

agent-self-evaluationを適用。accuracy 4（メモリ上の再現・修正再確認済み、実ブラウザー再取得は未実測）、completeness 4（製品2ファイルと指定境界を確認、未導入静的解析は未実施）、clarity 4（行番号と再現を記載、全体runとの違いを明記）、actionability 4（修正条件と局所ケースを提示し修正後再確認、実装は所有外）、conciseness 4（証拠を残すため要約より長い）。平均4.0。改善優先は最終回帰結果と版の照合を親担当の最終記録へ結ぶこと。本人が同じ評価に同意するかは未確認。

完了前の `git status --short` で全変更を確認した。レビュー自身の所有差分は本ファイルのみ。製品・試験・他担当記録は共同作業の変更として保持し、委任範囲に従いGitのstage/commit/pushは親担当へ返す。
