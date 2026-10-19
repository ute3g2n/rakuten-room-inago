# FC06 HTML例の作成者チェック（FC07指摘の修正後）

対象は通常、全項目不明、変更なし、通常の報告書作成失敗の合成HTML例4件です。各ページの最初に、実際の2026年10月17日の調査記録ではない合成シナリオと表示しています。この確認は作者による静的な文章・構造確認です。独立follow-up reviewでも本番report reviewでもなく、承認結果を作っていません。

| 確認点 | 修正版に含めた内容 |
|---|---|
| 何が起きたか | 対象日、平易な事象分類、期待した動きと実際の動きを本文に記載。 |
| 何を調べたか | 確認した範囲と分かっている事実を本文で説明し、根拠リンクから同じHTML内の付録へ移動できる。 |
| 何を変えたか | 元の処理の変更有無、報告工程で行ったこと、試験の範囲を記載。変更なし例では修正と計装を見送った具体的理由、次に確認する接続点を記載。 |
| 影響と回復 | 分かる範囲を説明し、利用者への影響や回復が不明な場合はその範囲を明記。 |
| 何が不明か | 確認済みの事実と未確認事項を分ける。全項目不明例も、開始記録と原因候補を示す調査記録が存在することを既知情報として残す。 |
| 確認・公開の状態 | 合成例の想定状態を記載し、確認を実行していない例では指摘なし・承認済みと書かない。 |
| 工程完了と元の不具合 | 自動対応工程の終了状態と元の日次不具合が解決したかを別の欄に表示。 |
| 根拠の参照 | 本文の分かりやすいリンク先に対応する一意の `id` があり、付録は最初から開いた状態で、移動後に根拠を読める。 |

通常例は予定3件と記録2件の差を示し、原因・実際の影響を不明としています。全項目不明例は、終了記録がないことに加え、調査記録に原因候補を説明する情報があることを既知とし、原因確定・影響・回復を未確認としています。変更なし例は、原因の手掛かりがなく、相関IDや呼出元の情報を受け渡す場所も見つかっていないため、単独ログを足しても処理に結び付かないことを理由として示し、次に接続点を調べる計画を記しています。通常の報告書作成失敗例は、確認できた開始記録と代わりの報告の内容を示し、別担当による確認と公開が未実施であることを記しています。

合成表示、期待・実際・影響・回復・変更・試験範囲・確認範囲・根拠、review/publication状態、workflow終端とincident解決の分離について4ページを静的検査しました。内部記録の参照先は付録に置いています。幅狭/印刷CSSは存在しますが、実ブラウザ描画・印刷は未確認です。

独立reviewの修正前所見は `html-review-findings.md` にあります。修正前版のSHAは `fc07-html-review-target.json`、修正版とfollow-up reviewer向け対象SHAは `fc07-html-followup-target.json` に記録しています。修正版は独立follow-up reviewが行われるまで未承認です。


## FC07 FCT09 follow-up revision (author static check)

新しい例HTML版は全4件に「当日発生／調査で判明／試験で確認」の区分を見える形で追加しました。存在しない samples/ 参照を削除し、実在する要件契約・標準・実装ファイルを技術付録で参照します。例は合成シナリオであることを本文に表示し、合成値を実績と誤認させません。fallback rendererは識別情報欠測を例外終了せず、対象日不明と欠測項目を説明します。レビュー指摘は実reporterへ一度渡し、改訂版をreviewerへ再投入します。作者または2回目reviewが失敗/未解決でも、指摘と失敗を含むpending HTMLを公開へ進めます。今回の確認はHTML source parserと参照先存在確認であり、独立review・browser/print・実reporter実行ではありません。fresh independent follow-up reviewはこのmanifest版で待ちです。


## FC07 independent follow-up r2 (2026-10-20)

Source: `worklogs/projects/maintenance/flow-completion-20261019/FC07/html-independent-followup-review-r2.md`, SHA-256 `2827443cb8fdf314ff260042385baac1324dec966dd2f0b427b6354f74d59f28`; verdict changes_requested; 17 target hashes matched.

1. A second review finding was being copied onto a final fallback HTML version that the reviewer had not seen. Fixed: the actual finding response remains in its exact version/hash-bound sidecar; the final pending fallback has a separate `unavailable` review record bound to its own HTML SHA and contains no claim that it was reviewed.
2. Missing identity could fail artifact-context validation before HTML persistence. Fixed without weakening global `_validate_context`: artifact storage context is derived from immutable job row/current launch, while raw incomplete identity stays in report input/body and missing fields are displayed.
3. Completed fallback resume ignored result.report_input binding. Fixed: recovery reads exactly result.report_input.path and checks its SHA, same job/attempt safe path, fallback schema, raw identity, storage identity, report output path/hash, and review record before restoring state.

Fresh independent review of the changed target is pending. No approval is inferred.
