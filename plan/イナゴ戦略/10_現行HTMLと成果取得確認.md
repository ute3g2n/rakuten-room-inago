# 現行HTMLと成果取得の補足確認

2026-09-30 JST。STEP 10のQ-031/036回答に伴う読取確認。要件決定は意思決定台帳D-045/047/050/052が正本。コードの読み取りのみで、本番の投稿状態・成果・認証情報を取得/更新していない。

履歴資料の注意：本書は2026-09-30時点の確認記録であり、D-077により17:45/18:00の日次終了期限は廃止済み。現行要件はHTML 2.20とD-077を参照する。

## F-049：HTMLのチェックは本人の投稿確認を保存する操作

[dashboard_service.py](../../automation/dashboard_service.py) の _render は商品URL、本文、コピー操作、投稿済みチェックを日付順に表示する。
[app.js](../../rakuten-room-kai/dashboard-assets/app.js) は127.0.0.1のHTTPサーバーへ接続できた時だけチェックを有効にし、保存成功後に確定表示する。保存失敗は元の表示へ戻す。単体ファイルを開いた場合、投稿済みチェックの永続保存はできない。
[dashboard_server.py](../../automation/dashboard_server.py) は本人確認時刻と訂正履歴をJSONへ保存する。既存形式の利用は表示・コピー・チェックの操作を新SQLiteへ接続することで成立させる。旧schema、7日確認期限、eligible/accepted要求、P番号の固定形式、旧JSON書込みをそのまま移植する要件ではない。

未チェックを見送りと解釈しない。D-046は本人の投稿または見送り報告まで予約するため、見送りを明示できる操作/入力が別途必要。チェック取消は実投稿の取消と誤入力訂正を区別する必要がある。ユーザーは全件当日投稿を想定しているが、HTML作成時点で実投稿済みを設定しない。

## F-050：現行の成果取得方式

[README-affiliate.md](../../scripts/README-affiliate.md) と [check-affiliate.mjs](../../scripts/check-affiliate.mjs)、[node_commands.py](../../automation/adapters/node_commands.py) を確認。
楽天アフィリエイトの日別画面と公式CSVから、前日から最大7日の日別クリック・売上件数・売上金額・未確定成果報酬、当月/前月の注文明細を取得する方式。画面/CSV取得不能を観測0へ変換しない。確定報酬額を取得する機能ではない。
本人のログインが必要な場合は本人操作へ戻し、Cookie・認証情報を新正本や文書へコピーしない。ここではログイン状態の存在/有効性を実確認していない。

[node_commands.py](../../automation/adapters/node_commands.py) の room_likes は [collect-room-likes.mjs](../../scripts/collect-room-likes.mjs) に --days 30 と出力先を渡す。自分の公開ROOMの直近30日を取得する現行方式で、投稿後24時間/7日の専用時刻タスクではない。新日次の本文作成後に実行し、反応の実観測時刻と投稿からの経過時間を保存する。D-052により探索17:45まで、成果取得18:00まで。

## F-051：再利用時に切るべき依存

アフィリエイトCLIの --output-dir は、現行では rakuten-room-kai/orchestrator/runs 配下かつ末尾 affiliate-report に限定される。新名前空間の出力先へ単に引数を変えるだけでは動かない。
ROOM CLIは明示ROOM URLがなければ旧account.mdを探索する。新設定で本人のROOM URLを明示し、旧正本への暗黙依存を除く。
現行Nodeアダプタの各成果取得timeout初期値600秒を2工程連続にそのまま使うと、成果用15分を超え得る。新系は工程の残り時間を両工程で共有し、18:00で取得不足を表示する。旧timeout値を本人決定としない。

## F-052：購入商品と入口投稿の帰属を分ける

[observation_service.py](../../automation/observation_service.py) の affiliate_snapshot は未確定報酬を観測値として持つ。一方 [performance_service.py](../../automation/performance_service.py) の day_aggregates は現在clicks/sales_count/sales_yenのみで、未確定報酬を成果分析入力へ渡していない。新要件では、取得済みの未確定報酬も日別成果として保持し売上金額と区別する。

[attribution.py](../../automation/attribution.py) は店舗商品コード/正規化URLまたは完全一致の正規化商品名で、実投稿済み商品と注文明細を照合する。ただし文字列一致は、入口投稿の証拠とは異なる。現行出力ラベル post_confirmed_by_order_product_match を、その投稿が収益を発生させたと断定する根拠として使わない。
旧affiliate_snapshotは注文の店舗名/日時などを一部落とすため、取得元にあるデータと変換で残るデータを分けて移植評価する。新系は注文の購入商品一致、入口投稿が確認できた場合、日別集計、不明を区別する。欠測や帰属不明の金額を商品へ配分しない。

## 未実証と後続

現行取得方式が新設定/新正本で動くこと、ログイン有効性、現在の画面/CSV列への適合、15分枠での継続取得は未実証。導入前に小試験し、不成立なら作成済み本文を保持して成果未取得を表示する。
将来の移植・試験は別途実装依頼の範囲。旧タスク、旧正本、旧CLIのコードは今回変更していない。

