# ROOM正常0件誤判定修正：独立製品コードレビュー

- 実施日時: 2026-10-25 09:27 JST
- reviewer: `/root/room_zero_review`（製品実装作者とは別担当）
- 固定root: `C:/project/rakuten-room-inago`
- 対象run: `INAGO-2026-10-24`（依頼で指定された値）
- 対象: `automation/inago/adapters/public_sources.mjs` の未コミット差分、1320～1322行。`stableNoProgressRounds >= 3` に `loaded > 0` を追加し、説明コメントを追加する変更。
- base HEAD: `3e0df7a1d33b3188376caf96b27b5eebad3f6f6b`
- 対象製品ファイル SHA256: `42511D6301EC7C7C2BC1DF19D0EA88F3A3252278927F1A48408A56D81DABBDB1`
- review scope: 製品コード差分・関連終了条件・既存deadline/cancel・利用可能な試験結果。テストコード自体の独立レビューは実施していない。
- product code review: `approved`（確度80%超の修正要求なし）。試験全体合格の判定を意味しない。

## 要件とコードの照合

1. **読み込み中0件の不変を正常0件としない。** 1322行の条件により、0件では `stableShortBatch` がtrueにならず、3回以降も走査を続ける。0件でループを抜けても、1333～1340行は明示no-results以外を `incomplete_search` とするため、正常0件の応答へ到達しない。一般的な終端文言「検索結果は以上です」が0件で出る場合も正常0件にはしない。
2. **明示no-resultsを正常0件として扱う。** 1225～1245行の可視文言検出と1255～1261行の終了条件は変更されず、1333～1346行の `explicit_no_results` 経路を保持する。
3. **正の少数結果は従来の3回不変で終了する。** 正の件数では追加条件が成立し、従来と同じ3回で終了する。遅れて投稿が出現した場合、1309～1310行で `stableNoProgressRounds` を0に戻すため、先行する0件期間の回数を持ち越して短絡終了しない。
4. **新しいROOM全体時間上限を追加しない。** 1252行の既存deadline、1267～1279行のスクロール前期限検査、1282行の既存1回2秒の変化待機は変更なし。`main()` 2231～2237行で期限未指定ROOMはInfinityを保持する。`live_sources.py:1470～1490` の期限なしROOMをNoneとする処理、1063～1070行のsubprocess待機、2099行以降の既存cancel経路を確認し、新しい制限・開始gateは追加されていない。
5. **エラー・データ処理の副作用。** guardPageによるアクセス拒否観測は各反復に残り、商品同定・応答schema・保存処理には変更がない。変更に伴う秘密情報出力もない。

## 実際に確認した証拠

- 修正前live局所probe: `private/probe-result.json` と `private/probe-stderr.jsonl` を実読。検索語は「アウター ブランド」。`room_results_scan_begin` elapsed=39272ms、0件の不変3回目=45408msで `room_results_complete / stable_no_progress / loaded_posts=0` となっている。probe観測ではcollector終了直後0件、さらに約15.1秒後に20投稿が存在した。これは**10/25の局所再現でこの誤判定経路が存在した証拠**であり、10/24全108件それぞれの原因を証明しない。
- `red.txt`: `test_room_delayed_first_result_after_condition_submit_is_not_empty`、`test_zero_result_without_known_end_marker_is_not_successful_empty` の2 failed（16.02s）を確認。前者は遅延投稿に対して0件、後者はunavailable期待に対してobserved。
- 09:26時点の初回修正後結果（後に `green-attempt1.txt` へ保持）: 同じ2 nodeで **1 passed, 1 failed（26.54s）**。遅延初回結果のnodeはPASS。未知の終端・0件のnodeは `assert not cancellation_fired.is_set()` でFAIL。期限なしROOMは明示キャンセルまで待つ既存契約に一致するため、この失敗だけから製品差分の不具合とは判断しない。親へ試験担当による期待・deadline入力の確認と影響nodeの再実行を依頼した。
- 続く局所再実行の `green-deadline.txt`: **1 passed（10.52s）**を実読。親担当より、元の期限なし入力を明示deadline（now+8s）へ修正し、上記失敗nodeだけ再実行したとの引継ぎを受けた。テストコード自体の独立レビューは行っていない。初回失敗を上書きせず保持し、修正後入力で成功した結果と区別する。最終回帰試験全体の結果はレビュー時点で未確認。
- 修正後live probe: 親担当より `private/after/` に別保存して実行中との引継ぎを受けた。レビュー時点で同配下に直前・遷移後画像ファイルがあるが、成功結果は未確認。
- reviewerの機械検証: `node --check automation/inago/adapters/public_sources.mjs` exit=0、`git diff --check` exit=0。pytest・日次全体runをreviewerは実施していない。

## 報告時の注意と引継ぎ

- 確認できたのは、0件を6秒不変で正常終了させた実装経路と「アウター ブランド」の局所live再現。**10/24の全108 zeroがすべて同じ原因だったと断定しないこと。** 全件ごとの画面・待機履歴はこのレビューでは確認していない。
- 初回失敗を成功として扱わず、明示deadline入力での局所再実行と最終回帰結果を区別して追記する。レビュー状態を後続工程の開始gateにしない。
- 製品コード・試験コードの編集、Git stage/commit/push、DB操作は行っていない。Git操作は親担当に引き継ぐ。

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0 | pass |
| HIGH | 0 | pass |
| MEDIUM | 0 | pass |
| LOW | 0 | pass |

Verdict: APPROVE — 製品差分に修正要求なし。初回失敗nodeは入力修正後の局所再実行でPASS。最終回帰全体と修正後live結果は未確認。

## 後続証拠の追記（元の製品レビュー対象版は維持）

2026-10-25、`regression.txt` を実読し12 passed（90.00秒）を確認。`execution.md` には同回帰のexit 0と、製品SHA256が上記レビュー対象と同一であることが記録されている。最終回帰の証拠参照のみ追記し、上記の先行レビュー時点の所見・対象版は変更していない。

