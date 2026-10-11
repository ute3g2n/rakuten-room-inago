# Step 05 結果

- 状態: success
- 既存Node/Playwright/API処理を引数配列で呼ぶ`NodeAdapters`を追加。出力は更新されたJSON成果物から読み、旧stdout文面を正本にしない。
- 既存`research-brief.mjs`と`rakuten-api.mjs`へ後方互換の`--json-output`、`research-batch.mjs`へ`--output-dir`と新在庫ブリーフ入力`--brief-json`を追加。
- `inventory_service`が新商品・下書き・投稿状態・投稿ログを結合し、7日以内確認済みの未投稿完成下書きを集計。投稿済みIDの復帰を防ぐ。
- `research_collect_service`が新投稿状態でAPI候補を再分類し、raw、重複、投稿済み、計画中、台帳、要確認、新規、部分失敗を保持。
- `observation_service`がアフィリエイトと公開ROOMの保存済みJSONを観測済み/未取得/未照合を区別してSchemaへ写像。
- 選別済み候補だけを最大15件、楽天商品ページ限定、読取専用で確認する`verify-product-pages.mjs`を追加。取得失敗はnullとunavailableを残す。

## 検証

- `npm test`: 51 passed、exit 0。
- `pytest -q tests_kai`: 37 passed、exit 0。コマンド失敗・timeout・古い成果物・部分失敗・秘密マスク・在庫0/15件・重複・確認済み0/未取得・商品ページ失敗を確認。
- ローカル実行: `NodeAdapters.research_brief`のJSON成果物を生成。新正本の準備済み商品0、投稿済み1、当日不足15、在庫不足120。
- 保存済み調査JSONを新正本で再分類: 生50、重複除外後49、新規48。保存済みアフィリエイトJSONの観測変換成功。
- 保存済みの他ユーザー公開ROOM検証データ52投稿はすべて未照合として保持。本人の投稿データには使っていない。
- 本Stepでの実サイト通信: 0回。認証ファイル本文の読取り・run outputsへの複製なし。

## JSON契約・引き継ぎ

- `inventory-brief.schema.json`へ`inventory_shortfall_count`と`eligible_product_ids`を追加。
- Node商品ページsnapshotは`schema_version`、`observed_at`、requested/observed/unavailable、商品別の根拠欄と失敗を持つ。Step 08以降にジョブ契約へ接続する。
- 商品ページの実サイト表示は未検証。実ブラウザでの適合性と本人ROOMの管理ID照合はStep 15の対象。
- 楽天APIの本番通信は行っていない。既存READMEに利用条件の確認事項があるため、Step 15で実通信前に確認する。
