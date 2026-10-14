# Step 13 結果

- 状態: success
- 実Edge ChromiumをPlaywright-coreで起動し、一時workspaceと127.0.0.1のfixtureサーバーだけを使って13 E2Eを実行。設計書第11節の受け入れ条件6件を`path-coverage-dashboard.json`のtest IDへ対応付けた。
- 3日分の降順、最新日のみ展開、目次、同日更新、ID・商品名・ジャンル・URL・時刻・本文の一致、本文のみコピー、投稿チェックの保存・再読込・サーバー再起動、訂正理由、file表示時の無効化、HTML/script/イベント属性の無害化を確認。
- 保存サーバー停止時の接続失敗、HTTP 500、409競合応答はブラウザの`fetch`に決定的な失敗応答を注入して復元表示を確認。サーバーの実競合判定はPythonテストで確認。入力不整合3種は誤カードを生成せず画面へ表示し、日次フローも失敗報告を保存する。
- 外部商品リンクは新規タブで開くことを確認。1件の外部要求はテストルータで遮断し、外部通信送信0。console error、未処理promise 0。
- 成功時の代表画面: `dashboard-e2e/dashboard-success.png`。失敗時のscreenshot/trace保存処理をテストハーネスに実装。途中の失敗試行で生成された証跡も同フォルダに残した。

## 修正

- HTMLのasset参照を相対化し、`file://`でも保存不能の説明を表示。
- 投稿状態APIに履歴件数を使った競合検出を追加。古い画面からの更新は409で拒否し、保存済み状態を維持。
- dashboard生成エラーを`dashboard-error.json`に記録し、画面で表示。日次のID・URL・draft不整合では失敗結果を`outputs/daily-report.json`にも記録。

## 機械検証

| コマンド | 結果 |
|---|---|
| `node --test tests/dashboard-e2e.mjs` | 13 passed、exit 0 |
| `pytest -q tests_kai` | 141 passed、exit 0 |
| `npm test` | 51 passed、exit 0 |
| `node --check tests/dashboard-e2e.mjs` | exit 0 |

実楽天ROOMへの投稿・状態変更は行っていない。
