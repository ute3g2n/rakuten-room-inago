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

後続の局所run・回帰試験・レビュー・起動確認の結果を追記する。
