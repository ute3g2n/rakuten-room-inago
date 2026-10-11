# 楽天アフィリエイト注文明細取得のTDD記録

実施日: 2026-09-24

## 対象

- 「注文詳細」画面の公式CSVから発生日時、成果報酬、売上金額、ジャンル、ショップ名、商品名、ステータス、リンクタイプを全件取得する。
- 注文0件を確認済み0件として保存し、空表、列変更、対象月外行、ログイン切れとは区別する。
- 月別の最新値、変更履歴、実行結果、人向け状態へ保存する。

## RED

先に `tests/affiliate-report.test.mjs` へ注文明細の抽出、0件判定、列変更・対象月外拒否、同値比較のテストを追加した。

```text
node --test tests/affiliate-report.test.mjs
SyntaxError: ../scripts/affiliate-report-lib.mjs does not provide an export named 'parseOrderDetailTable'
tests 1 / fail 1
```

明示的な0件行だけを0件にし、DOM上の空表を取得成功にしない追加テストも先に失敗させた。

```text
AssertionError: Missing expected exception.
tests 9 / pass 8 / fail 1
```

レビュー後、全件CSVと当月・前月再取得へ設計を変え、CSVパーサーのテストを先に追加して未実装エラーを確認した。

```text
SyntaxError: ../scripts/affiliate-report-lib.mjs does not provide an export named 'parseOrderDetailCsv'
tests 1 / fail 1
```

## GREEN

初期のDOM表読取は、読込前の仮0件と1ページ10件制限がレビューで判明したため廃止した。注文レポート通信のHTTP成功を待ち、画面の公式CSVリンクをクリックする方式へ変更した。`parseOrderDetailCsv`、`sameOrderDetails`、当月・前月の再取得、月別保存と変更履歴を実装した。12注文とカンマ入り商品名を含むCSVテストで、10件を超えても全件取得できることを確認した。

```text
npm test
tests 48 / pass 48 / fail 0
```

対象ライブラリのカバレッジ:

```text
node --test --experimental-test-coverage tests/affiliate-report.test.mjs
line 94.71% / branch 80.00% / functions 100.00%
```

## 実サイト確認

保存済みの本人ログイン状態を使って `npm run affiliate:check` を実行した。

```text
2026-09-23: クリック 1、売上 0円、未確定報酬 0円
直近 1日を確認、数値の新規記録・変更 0日。
注文明細: 0件（2026-09を確認済み）
```

終了コード0を確認し、`latest.json.orderDetailsByMonth["2026-09"]` が `status: "observed"`、注文空配列、公式CSVの情報源URLを保持することを確認した。`status.txt` と `runs.jsonl` にも注文明細0件の成功結果が記録された。

検証時点では実注文が0件だった。このため、実際の注文行を使った抽出確認は注文発生後に再確認する。CSVの11列から必要な8項目を抽出する処理、金額変換、状態変化、列変更・対象月外行の拒否は自動テストで確認済みである。
