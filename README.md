# Rakuten Room Inago

楽天ROOMの日次運用を支援する自動化プログラム、保守フロー、要件・運用資料をまとめたリポジトリです。

## まず読む資料

1. [プロジェクト全体マップ](docs/architecture/プロジェクト全体マップ.md)：コード、試験、文書、作業記録の場所を探す入口です。
2. [イナゴスキル要件定義書](docs/specifications/inago/イナゴスキル要件定義書.html)：システムの要件と処理の説明です。
3. [日次タスクのトレース・ログ出力一覧](docs/operations/日次タスクのトレース・ログ出力一覧.md)：日次処理の工程、ログ、保存先を確認できます。
4. [文書一覧](docs/README.md)：仕様、標準、テンプレート、運用資料の入口です。

## 主な場所

- **automation/inago/**：日次処理、CLI、データ管理、会話ログ同期、アダプター、保守workerの実装。
- **tests_inago/**：日次・保守処理のpytest試験とfixture。
- **docs/**：仕様、構成資料、運用標準、テンプレート。
- **plans/**：要件、実装、準備、保守の計画。
- **reports/**：共有可能な障害報告。
- **worklogs/**：実行記録、レビュー、試験結果などの作業証拠。
- **tools/**：文書生成・確認用の補助ツール。

## ローカル環境とデータ

**.venv-inago/**、**node_modules/**、キャッシュ類はローカルの実行・開発環境です。**rakuten-room-inago/runtime/**と**rakuten-room-inago/backups/**には日次運用の状態、Codex会話ログの原本コピー、バックアップが保存され、Git管理対象外です。**非公開作業データ/**と認証情報も公開資料・コミットへ含めないでください。

固定された作業場所、試験、Git操作などのルールは[AGENTS.md](AGENTS.md)を参照してください。

## 投稿履歴を開く

ルートの **[open-posting-history.bat](open-posting-history.bat)** をダブルクリックします。Web／Human APIサーバーをバックグラウンドで起動し、DB接続の準備ができてから既定ブラウザーで投稿履歴を開きます。起動済みの同じサーバーは再利用します。接続先は通常 `http://127.0.0.1:8765/posting-history.html`（ポートはruntime設定に従う）です。tokenの手入力は不要です。

チェックON／OFFは同じ `runtime/state.sqlite` へ即時受付されます。「SQLite受付済み」の表示で保存を確認できます。別タブや日次処理の変更も自動表示され、画面再読込・日次HTML再生成・サーバー再起動後もDBの状態を読み戻します。通信中断時はブラウザー保留を維持して再接続時に順番に再送します。投稿報告の受付と、翌日日次の予約・恒久除外への反映は別の状態です。

SQLite用の別サーバーは不要です。ブラウザーを閉じてもローカルサーバーは動作します。Windows再起動後は再度バッチを開きます。起動診断のみ行う場合は `open-posting-history.bat --no-browser` を使います。起動結果は `runtime/logs/history-launcher.jsonl`、サーバーの起動エラーは `runtime/logs/history-server.log` に残ります。

## 紹介文の内容と独立レビュー

[作成skill](.agents/skills/rr-inago-copywriter/SKILL.md)は全HTML・全OCRを読み、タイトルや概要の言い換えを超える説明を作ります。[禁止例](.agents/skills/rr-inago-copywriter/references/投稿文アンチパターン.md)を共有する[独立レビューskill](.agents/skills/rr-inago-copy-reviewer/SKILL.md)が同じ入力と最終本文を照合します。修正後は再レビューし、合格した本文だけを新規保存します。説明材料が不足する商品は薄い紹介で埋めず正常な不採用として、既存の検索・選別から補充します。
