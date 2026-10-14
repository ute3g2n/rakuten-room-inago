# Step 07 結果

- 状態: success
- `performance_service`で投稿別公開反応、日別合計、購入商品、ショップ集計の保存枠を分離した。未取得と0、投稿後の観測期間、比較可能な反応件数を明示する。
- `attribution`は商品コード、正規化URL、NFKCで正規化した商品名完全一致の順で、投稿済み商品だけを照合する。ショップ名があれば完全一致を追加。複数ID一致はDATA_MISMATCH。不一致注文は「購入商品は確認済み・入口投稿は未特定」。
- `pattern_ledger`はA/B/C/保留の根拠を検査し、A条件から別商品3件の調査要求、Bの単一変数、Cの条件優先度低下を記録。投稿済み商品の再調査指定と、日別合計だけを根拠としたB/Cを拒否する。
- 同一観測の履歴はハッシュで重複排除。注文状態の変化は別観測。出所run_idと根拠を台帳・調査キューに保存する。

## 判定表とfixture

| 条件 | 判定・処理 | 検証 |
|---|---|---|
| 投稿商品と注文が一致 | 注文商品照合による確定帰属、Aなら別商品3件 | `test_exact_match_priority_and_ambiguity`、`test_a_request_idempotence_and_order_status_change` |
| 注文が不一致・複数一致 | 非帰属・DATA_MISMATCH | `test_exact_match_priority_and_ambiguity` |
| 日別クリックのみ | 日別集計に保持し投稿別B/C不可 | `test_clicks_and_zero_likes_stay_separate`、`test_b_c_hold_guard_and_single_variable` |
| いいね0・未取得・少数観測 | クリック/購入0へ転換しない、傾向保留 | `test_clicks_and_zero_likes_stay_separate` |
| B/C/保留 | 投稿別確定根拠を要求、保留は除外せず、実験は1変数 | `test_b_c_hold_guard_and_single_variable` |
| 同一観測・注文状態変化 | 同一は重複せず、状態変化は新履歴 | `test_a_request_idempotence_and_order_status_change` |

第19節の受け入れ条件1と9のフロー伝播・manifest追跡はStep 09のオーケストレータ結合テストでも検証する。条件2～8は上表の単体テストで検証した。

検証: `pytest -q tests_kai` 45 passed、exit 0。Codex CLIと実サイトは呼んでいない。
