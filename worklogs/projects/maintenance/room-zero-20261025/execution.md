# ROOM検索の読み込み中0件誤判定

対象は2026-10-24の日次 `INAGO-2026-10-24`。調査・局所再現日は2026-10-25 JST。
固定rootは `C:/project/rakuten-room-inago`。開始時HEADは `49156c31155515d716b5d5bd6d559cbebdbc5f72`、既存変更なし。

## 不具合と確認範囲

本人の「アウター ブランド／いいね300件以上では多数表示される」という画面を受けて調査した。
昨日の `search_attempt=e847784a25be54058e3e1357dfca9626` は正常取得・0件・exhaustedだったが、保存観測の終了根拠は `stable_no_progress` で、明示的な該当なし表示ではなかった。
昨日の正常0件108検索を保存観測と照合したところ、108件すべてが同じ `stable_no_progress` 根拠だった。ただし、全語句を実サイトで再検索したわけではなく、108件すべてに実際の商品があったとは断定しない。

## 局所run（本番日次とは別）

日次全体は起動せず、本番DBはmode=roのSELECTのみ。ROOM公開画面を、新規匿名ブラウザーcontextから製品の `collectRoom` で操作した。内部API・認証情報は使用していない。

- 入力: `アウター ブランド`、いいね300以上、コメント指定なし、max_posts=1（最小の取得確認）。局所attempt/run IDは `LOCAL-ROOM-ZERO-20261025`。昨日のrun IDは変更していない。
- プローブの実行期限90秒はこの局所試験の入力であり、製品へ追加した時間上限ではない。
- 修正前コマンド: `node worklogs/projects/maintenance/room-zero-20261025/private/probe.mjs`。exit 0。期待=結果を待って1商品取得、実結果=正常0件。走査開始39272ms、終了45408ms、約6.14秒の不変で終了。終了後さらに約15秒待つと20投稿が存在した。
- 修正後コマンド: `node worklogs/projects/maintenance/room-zero-20261025/private/probe.mjs worklogs/projects/maintenance/room-zero-20261025/private/after`。exit 0。期待=結果を待って1商品取得、実結果=20投稿表示、うち試験対象1商品の識別・取得成功。走査開始38475ms、20投稿検出47720ms、返却まで約76.8秒。
- Playwright動画用ffmpegが当初未導入で起動失敗（exit 1）。`node node_modules/playwright-core/cli.js install ffmpeg`（exit 0）で補完後に上記runを実施した。最初の失敗を本番障害とは扱わない。

非公開証拠は `.gitignore` でGit登録を除外。スクリーンショット、動画、traceに第三者の投稿等が含まれるため、共有資料へ原本を登録しない。

| 局所run | 操作前／遷移後／終了後の画面 | 動画 | trace・観測 |
|---|---|---|---|
| 修正前 | `private/01-before-search.png`、`02-after-navigation.png`、`03-after-collector.png`（読み込み中）、`04-after-further-wait.png`（20投稿） | `private/video/*.webm`（成功runの動画はtraceと併せて保持） | `private/trace.zip`、`probe-result.json`、`probe-stdout.jsonl`、`probe-stderr.jsonl` |
| 修正後 | `private/after/01-before-search.png`、`02-after-navigation.png`、`03-after-collector.png` | `private/after/video/*.webm` | `private/after/trace.zip`、`probe-result.json`、`private/after-stdout.jsonl`、`private/after-stderr.jsonl` |

## 原因と修正

製品ファイル `automation/inago/adapters/public_sources.mjs` の検索終了条件を `stableNoProgressRounds >= 3` から `loaded > 0 && stableNoProgressRounds >= 3` に変更した。
約2秒ずつのスクロール後待機を3回繰り返す既存処理は、0件で読み込み中でも正常終了を認めていた。
修正後は0件の不変を正常終了に使わず、投稿または明示的な該当なし表示を待つ。正の少数件の不変による終了は維持する。

最大タイムアウト秒数の変更、新しいROOM全体時間上限、新しい回数上限はない。期限未指定のROOMは上限なしで、既存のdeadline指定・アクセス拒否検出・キャンセルは維持する。読み込みも明示結果も返らない場合は、期限指定またはキャンセルがあるまで待つ。
追加のトレース機能は新設していない。既存の `room_list_no_progress`、`room_list_progress`、`room_results_complete` で0件から正の件数への遷移を追える。今回新たに取得した証拠は、局所runの画面・動画・Playwright traceである。

## 局所試験・回帰試験

共通コマンドは `.venv-inago/Scripts/python.exe -X utf8 -B -m pytest <完全node> -q --tb=short`。REDのみ `-X utf8` なし。

1. RED: `tests_inago/test_step21_browser.py::test_room_delayed_first_result_after_condition_submit_is_not_empty`、`tests_inago/test_step21_browser.py::test_zero_result_without_known_end_marker_is_not_successful_empty`。期待=遅延結果1件成功／不明0件を成功にしない。実結果=2 failed、exit 1、`red.txt`。checkpoint commit `3e0df7a`。
2. 初回修正後: 同じ2 node。実結果=1 passed / 1 failed、exit 1、`green-attempt1.txt`。遅延結果の試験はPASS。不明0件試験が期限なし入力で、正常に待ち続けてguard cancelに達した。試験入力の誤りと判明し、製品へ時間上限を追加せず、当該試験だけに明示deadlineを設定した。
3. 当該失敗nodeのみ再実行: `tests_inago/test_step21_browser.py::test_zero_result_without_known_end_marker_is_not_successful_empty`。実結果=1 passed、exit 0、`green-deadline.txt`。無関係な合格nodeはこの段階で再実行していない。
4. 最終回帰: `regression-nodes.txt` の11指定（パラメーター展開で12 case）。PowerShellで同ファイルを配列として読み、`& .venv-inago/Scripts/python.exe -X utf8 -B -m pytest @roomNodes -q --tb=short`。明示終了の完全nodeは末尾 `[0]` と `[37]`。実結果=12 passed（90.00秒）、exit 0、`regression.txt`。skip・未実施なし。

コード版は局所修正後・最終回帰で同一。SHA256:

- 製品: `42511d6301ec7c7c2bc1df19d0ea88f3a3252278927f1a48408a56d81dabbdb1`
- 試験: `3d68f48e918b94e4fd40f7a8f977e2b11467ee6a804da63148cc2fab2996d5a1`
- 局所probe: `d6191b51d353e040a690d75e66295ad654ae19954495d482a262dd5000536484`

製品の独立レビューは `code-review.md`。テストコードの独立レビューは行っていない。

## 全体runと残る事項

日次全体run・昨日runの再開は未実施。昨日の9件という保存結果、履歴、試行、DBは変更していない。15件回復したとは報告しない。
別途判明したテーマ単独補充検索の欠落は今回のROOM初回表示待機修正の対象外であり、未解決として保持する。全体runを行う前に、この既知問題の局所修正・試験が必要。
昨日の108検索すべての商品件数は未確定であり、記録の0件を実際の該当商品0件へ読み替えない。

## 自己点検

| 観点 | 評点（5点満点） | 根拠・改善余地 |
|---|---:|---|
| 正確性 | 4 | 製品差分、12回帰case、前後live記録を照合。実画面を確認した検索語は1つに限るため全108件へ一般化しない。 |
| 完全性 | 4 | 今回の初回表示待ちの不具合を修正・検証。別の補充検索の欠落と15件回復は未確認として保持。 |
| 明瞭性 | 4 | 約6秒の正常終了判定とタイムアウトを区別し、本番の待ち上限なしを明記。最初の説明でこの区別を十分に伝えられなかった点を是正。 |
| 実行可能性 | 4 | コマンド・完全node・版・終了コード・証拠場所を記録。共有先から非公開画面原本は読めないため、手元で再確認が必要。 |
| 簡潔性 | 4 | 利用者向けHTMLは4事実中心、技術詳細は本記録へ分離。作業記録は再現性のため長い。 |

平均4.0。改善優先順は、(1)昨日の0件件数を実在商品0件と解釈しない、(2)別の補充検索欠落を局所修正してから全体runを検討する、(3)タイムアウトの説明では冒頭で実際の秒数または上限なしを答える。
本人が重視する「多数ヒットするのに0件」と「実際の待ち時間」に対し、修正前後の証拠と上限なしの明示で答えているかを最終確認した。15件回復したという主張はしていない。

共有用試験ログの行末空白のみ正規化した。元のRED出力は `private/red-original.txt` に保管し、失敗内容・件数・終了結果は変更していない。
