# 楽天アフィリエイトの日次確認（Playwright）

このスクリプトは楽天アフィリエイトの「期間別 > 日別」と「注文詳細」レポートを読みます。前日から最大7日分のクリック数・売上件数・売上金額・未確定成果報酬に加え、当月と前月の注文発生日時・成果報酬・売上金額・ジャンル・ショップ名・商品名・ステータス・リンクタイプを `rakuten-room-kai/affiliate-report/` に保存します。注文詳細は画面の読込通信が成功した後、画面の公式CSVリンクをクリックして全件を読みます。登録日（2026-09-23）より前の数値と注文は日次運用の対象にしません。毎回AIを呼ばないため、定期実行そのものにCodexのトークンは使いません。

1. このフォルダで `npm install` を実行。
2. `npm run affiliate:login` を実行。開いたEdgeで**本人が**楽天アフィリエイトにログインする。チャットやスクリプトにID・パスワードを渡さない。専用Edgeプロファイルは `%LOCALAPPDATA%\rakuten-room-bakemono\affiliate-playwright-profile`、日次タスクと共有するログイン状態はプロジェクトの隣の `C:\project\rakuten-room-bakemono-private\auth-state.json` に置く。後者はログイン可能なCookieを含むため、スクリプトが本人・管理者・SYSTEMだけ読めるアクセス権に設定する。どちらもプロジェクトには入らない。
3. `npm run affiliate:check` で手動確認。ログイン切れや画面変更では終了コード1と `runs.jsonl` の `unavailable` を残し、0件を作らない。
4. `powershell -ExecutionPolicy Bypass -File .\scripts\register-affiliate-task.ps1` でWindowsタスクを登録。毎日13:00にスリープを解除して実行する（このユーザーがログオン中の場合）。時刻を逃した場合は、次に実行可能になった時点で開始する。PCがシャットダウン中の場合は起動できない。

`status.txt` を開けば直近の成功・失敗、前日の数値、注文明細件数が読めます。`latest.json` は `byDate` に日別の最新値、`orderDetailsByMonth` に月別の注文明細を保存します。`observations.jsonl` は日別数値、`order-details-observations.jsonl` は注文明細の初回観測と変更履歴、`runs.jsonl` は毎回の成功・失敗を保存します。日別成果は未確定値です。注文詳細の `status` は楽天画面の値をそのまま保存し、確定報酬額そのものはこのスクリプトでは取得しません。楽天のログインが切れた場合は `npm run affiliate:login` を再実行します。注文明細・売上情報と専用Edgeプロファイルは、Windows上で本人・SYSTEM・Administratorsだけが読めるACLへ自動設定します。

スクリプトはサイトの表示HTMLと画面にある公式CSVダウンロードを使います。画面読込通信の失敗、CSV取得失敗、楽天側の画面・CSV列変更ではエラーとして停止し、仮の0件表示を確認済み0件として保存しません。自動再ログイン、投稿、設定変更はしません。
