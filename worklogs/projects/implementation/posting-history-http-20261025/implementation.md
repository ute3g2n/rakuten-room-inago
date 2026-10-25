# HTTP投稿履歴・DB同期（2026-10-25）

本人依頼：ルートのバッチをダブルクリックしてローカルWeb／APIとSQLite接続を起動し、HTTP投稿履歴を開く。チェックを即時保存し、日次更新・再起動後の保持を局所確認する。要件定義書も更新する。

## 範囲・設計

- 固定root：`C:/project/rakuten-room-inago`。既存の未追跡 `worklogs/projects/implementation/item-desc-capture-20261025/C07/.pytest-tmp/` は対象外。
- Web／Human APIは同一プロセス、SQLiteは既存ファイルDB。HTTPページはAPIでDB状態を読み、日次生成HTMLの差し替えに依存しない。
- 起動ticketと署名cookieで手入力なしに認証。既存Bearer／CSRF／loopback制約を維持。
- SSEのSQLite変更検知、送信前IndexedDB保留、成功receipt後ACK削除で同期と通信中断時の保持を扱う。
- 本番の投稿履歴は変更しない。10/16・10/21の過去チェックは今回の変更で新規作成しない。

## 局所確認の対応

| ケース | 確認対象 |
| --- | --- |
| H01 | HTTP起動画面・一回ticket・origin拒否・cookie・再起動後の認証 |
| H02 | 外部SQLite commitをSSEで検知して新日付を取得 |
| H03 | 起動済みサーバーを再利用し、同じruntimeへ接続してブラウザーを開く |
| H04 | 実Chromiumでチェック受付、応答前再読込、日次反映・新商品追加・HTML再生成、サーバー再起動、別タブ同期、解除後再読込 |

`tests_inago/test_history_web.py` と既存の履歴／報告試験を使う。日次全体runは実行対象外。実ブラウザーの原本証拠（test session cookieを含み得るtrace）は `非公開作業データ/イナゴ実装試験/posting-history-http-20261025/` に保存しGit登録しない。

## 実行結果

### 実装

- `open-posting-history.bat` → `history_launcher.py`。同じruntimeの互換APIを確認し、未起動なら既存production serveを非表示プロセスで起動する。最大30秒の準備待ち後、既定ブラウザーを開く。
- `human_api.py` がHTTP履歴の起動画面、ticket交換、SSEを配信する。商品・本文は認証済みAPIだけから取得する。長期tokenはURL・HTML・ログへ出さず、ticket fragmentを除去して署名cookieへ交換する。
- `history.js` は送信前にIndexedDBへ記録し、receipt revision順に直列送信する。ACKの直後に表示用報告とrevisionを更新し、画面状態再取得だけが失敗しても保存済み状態を保持する。接続断でも最後に確認したdataを参照する。
- 恒久拒否は要確認として保持する。成功チェック／有効pending／revision算定／取込JSONから除外し、独立商品の保存を続行する。自動revision書換えはしない。
- SQLiteの外部commitを0.25秒周期で検知し、SSEとAPI再取得で表示する。送信中・訂正入力中は再描画を保留する。

### 局所runと独立レビュー

初期機能REDはHTTP履歴404、ticket403、launcher未実装の3件。`red.txt` にコマンド・終了コード・実結果を保存した。pytest一時親ディレクトリ欠落による最初の3 setup errorも区別して記録している。

独立製品レビュー（`source-command-python-review`／`python-reviewer`）で次を確認・修正した。

| 所見 | 対応・証拠 |
| --- | --- |
| redirectでBearerが転送され得る | HTTP redirectを拒否。起動APIの302で転送0件を確認 |
| 不正cookie署名のTypeError | 401相当の不正認証として拒否。非ASCII cookie試験で確認 |
| 成功receipt後GET失敗時の古いrevision | ACK時更新とdata参照。実ブラウザーRED→GREEN |
| 復旧直後に先行ONを飛ばしてOFFを送る | 同一商品のrevision順・flush直列化。実ブラウザーRED→GREEN |
| 恒久拒否された先行保留が全商品を止める | 要確認保持・独立商品送信。実ブラウザーRED→GREEN |
| launcherログだけの失敗が起動を止める | 安全なstderr警告にして続行する局所試験PASS |
| JSON script nonceで既存HTML readerが不一致 | 非実行JSONタグの互換を維持し、実行script/styleのnonceは保持 |

最終再レビューは承認（残るCRITICAL／HIGHなし）。レビュー担当は製品差分の読取専用確認と静的検査のみで、DB操作・テストコードの独立レビュー・Git操作はしていない。局所RED／GREENのコマンドと結果、試験fixtureの修正理由は `local-results.txt` を参照。

### 回帰試験・版・画面証拠

関連回帰の対象は `test_history_web.py`、`test_daily_fix_history.py`、`test_step23_12_history_interactions.py`、`test_step05.py`、`test_step15.py`。初回回帰は57件PASS（28.83秒、終了コード0）。画面証拠をattempt別へ改善した回は56件PASS／1件FAIL（25.23秒、終了コード1）：SSEによるカード差し替えとlocator撮影の安定待ちが競合して要素が外れた試験撮影の失敗だった。checkboxを中央へscrollしてpage screenshotを撮る方式に変更し、該当nodeだけを再確認してPASS（4.69秒）。その後の最終関連回帰は **57件PASS（26.30秒、終了コード0）**。結果は `validation.txt`、対象版のSHA256は `tested-version.json` に記録する。新旧履歴・受付／管理反映の区別・冪等性・origin／CSRFもこの範囲に含む。

coverageは対象4モジュール計84%（dashboard_history 76%、history_launcher 70%、history_session 96%、human_api 91%）。4ファイルのblack、2ファイルのnode --check、git diff --checkはPASS。launcherの実プロセス起動とmainは別の本番起動確認で実行し、coverageの割合へ加算していない。

最終ブラウザー証拠は以下の非公開rootに、attempt `history-web-regression-final` と完全なpytest case名を分けて保存する。

`C:/project/rakuten-room-inago/非公開作業データ/イナゴ実装試験/posting-history-http-20261025/history-web-regression-final-02/`

- `test_history_web_browser_persi0/01-before-check.png`〜`06-correction-synced-and-reloaded.png`：対象checkboxを中央に置いた操作前後の画面。新日付・管理反映後・再起動後・別タブ解除／再読込を区別する。
- `test_confirmed_receipt_revisio0/`：ACK成功後にGET503、直後の解除、切断表示と再読込。
- `test_queued_on_then_off_is_sen0/`：ON保留後、復旧直後のOFFが順に保存されること。
- `test_rejected_queued_report_do0/`：別タブ競合の409が独立商品の保存を止めないこと。
- 各ディレクトリの `*.webm` と `trace.zip`：動画、snapshot／操作／通信記録。fixtureのsession cookieを含み得るためGit登録しない。

最初の反復では同じtrace保存先を再利用して原本を上書きした。RED時の残存根拠はpytest出力であり、過去traceが保存されたとは扱わない。最終回帰からはattemptごとに分ける。証拠はすべて局所fixtureであり本番画面の操作証明には使わない。

### 本番起動確認・DB保全

ルートのバッチを実行して `server=started`、`--no-browser` の終了コード0を確認。その後、引数なしのバッチ実行は `server=reused`／`browser_opened=true`、終了コード0。ChromeのHTTP画面でAPI接続済み、10/25の15件表示を読取確認し、画面を開いたまま残した。チェック／見送り／訂正操作は本番で行っていない。

起動前後のDB論理内容SHA256が一致した。`production-before.json` と `production-after.json` に比較を保存した（run 8、draft 81、human_action 0、publication_event 2）。10/16・10/21の未記録チェックを今回新規作成していない。

日次全体runは対象外・未実行。今回の日次更新確認は、局所DBへ新日付の商品を追加し、既存の `apply_pending_reports` を実行し、日次HTMLを再生成したもの。これを本番日次全体の完走証拠として扱わない。
