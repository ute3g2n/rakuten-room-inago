# コピー＋チェック＋遷移（2026-10-26）

本人依頼：既存の版別本文コピーボタンの右横に「コピー+チェック+遷移」を追加し、本文コピー・投稿済みON・商品ページ別タブ表示を一操作で行う。既にONならOFFや重複報告にしない。

固定rootで実装する。既存のitem-desc案件の未追跡pytest一時領域は今回の変更に含めない。通常コピーは既存のコピー専用動作を維持する。新ボタンだけが本人の明示操作として既存の投稿報告保存経路を使用する。

局所試験は隔離SQLiteと実Chromiumで、通常コピーとの区別、右横配置、本文一致、別タブURL/opener、ON化と再押下のON保持・報告1件、コピー失敗時の独立動作、通信断時保留と復旧後受付、安全なURLがない場合の無効化を確認する。本番商品へのチェック・投稿は試験しない。原本スクリーンショット・動画・traceは非公開領域にattempt別保存する。

## 実装と本人の追加指定

- 新ボタンだけを青色 `#1b58a6` にし、既存の緑色コピー専用ボタンとの間を16px空ける。狭い画面ではflexで折り返す。
- Clipboard APIの書込みを画面がfocusされたuser gesture内で開始し、既存の安全確認済み商品リンクを同じgesture内で別タブへ開く。linkのnoopener／noreferrerを維持する。
- OFFの場合だけ既存submitReportでposted報告を保存する。ON済みなら保存報告を増やさずONのまま。処理中の同じdraft/versionへの連打も抑止する。
- コピー失敗時も保存と遷移を実行する。保存後に再描画された現行本文を手動コピー用に選択し、同じdraft/versionの自動更新でもfocusと選択範囲を維持する。

## 試験・レビュー

実行コマンド、期待／実結果、終了コードは `local-tests.txt` と `validation.txt`、対象版とfixtureは `tested-version.json` に記録した。

| 局所ケース | 結果 |
| --- | --- |
| 新ボタンがない旧実装 | success nodeのRED（exit 1） |
| 通常コピー→新ボタン→ON済みで再押下 | コピー本文一致、正しい別タブ2回、opener=null、DB postedは1件、OFFにならない。PASS |
| コピーAPI失敗 | コピー失敗を表示し、投稿受付と別タブは成功。保存・SSE後の手動コピー選択も確認。PASS |
| POST通信断 | IndexedDBへ保留、復旧・再読込で同じ報告を受付、DBは1件。PASS |
| 安全なURLなし | 新ボタン無効。コピー専用は使え、DB報告0件。PASS |
| 色と距離 | 実Chromiumで元ボタンと異なる色、同じ行で16px以上。実稼働画面でも青rgb(27,88,166)、既存コピー緑rgb(35,103,75)、columnGap=16pxを読取確認。PASS |

独立製品レビューは `source-command-python-review` の担当 `history_http_review` がread-onlyで実施。コピー失敗の選択が保存後のtextarea置換で失われるMEDIUM所見をREDで再現し、選択復元で修正した。再選択だけでは後続SSEで再消失したため、同じdraft/versionのfocus・選択保持も加えた。350ms後の実ブラウザー選択範囲でGREENを確認し、最終再レビューは承認。残るCRITICAL／HIGHなし。テストコードの独立レビューはしていない。

fixtureの修正も区別する。初回popup検査はabout:blankのDOMContentLoadedを先に見たため、正しい商品URLへのcommit待ちに変更した。URLなしcaseでDBのNOT NULL制約にNoneを入れた最初のsetupは失敗したため、HTTPのunsafe URLが表示用に除外されるfixtureへ変更した。これらを製品の遷移失敗・DB障害とは扱わない。

最終関連回帰：`test_history_combined_action.py`、`test_history_web.py`、`test_daily_fix_history.py`、`test_step23_12_history_interactions.py`。**30件PASS、31.09秒、exit 0**。black対象1ファイル、node --check対象2ファイル、git diff --checkもPASS。日次全体runは未実行・対象外。

## 証拠と実稼働反映

原本媒体は `非公開作業データ/イナゴ実装試験/history-combined-action-20261026/<attempt>/<case>/`。同じbytesを当案件の `.private-evidence/` へコピーして保存し、この配下は案件の.gitignoreでGit登録を除外した。traceにはfixture session cookieを含み得る。最終回帰のattemptは `history-combo-regression-01`。success/clipboard-failure/offline/missing-urlのcaseごとに01-before、02-after、successの03-already-on、動画webm、trace.zipを分ける。失敗時はfailure.pngも保存する。局所fixtureの証拠を本番操作の証明に代用しない。

read-onlyのproduction exportで単体 `runtime/posting-history.html` を再生成した。旧Python templateを保持していた8765番のプロセスは、CLI serveと固定runtime/config引数を確認した上で、そのPIDだけを停止し、ルートバッチから新サーバーとHTTP画面を開いた。実画面の配置確認はブラウザー5/tab35743158で読取のみ（新ボタン操作なし）。最初のtab取得は未claimのため失敗、続く変数取得も失敗したが、同じbrowserの正規claim後にCSS値を確認できた。

本番DBの反映前後の論理SHA256は一致した。`runtime-before.json`／`runtime-after.json` を参照（run 9、draft 96、human_action 81、publication_event 2）。今回の試験・画面確認で本番へ投稿報告を作成していない。要件定義書へIR-HISTORY-COMBO-01／AT-HISTORY-COMBO-01を追加済み。
