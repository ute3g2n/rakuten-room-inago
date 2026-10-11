# カレンダー同期の手順（Python不要・curlのみ）

作業フォルダ内の `rakuten-room/` のMarkdownを読んで Supabase に upsert する。**外部の実行環境は不要**。Codex（あなた）が①Markdownを読み ②JSONを書き ③curlで送る、を行う。

**大前提：作業フォルダ内の `rakuten-room/calendar.json` が無ければ同期しない**（カレンダー未連携＝従来運用）。存在確認してから始める。

## 保存先の決め方

- 保存先は `~/rakuten-room/` ではなく、**Codexで開いている作業フォルダ内の `rakuten-room/`**。
- shellで実行する場合は、まず現在の作業フォルダを確認し、そこから `rakuten-room/` を見る。
- 同梱スクリプト `scripts/sync-calendar.ps1` は、既定で「現在の作業フォルダ + `rakuten-room`」を同期対象にする。
- 旧版の `~/rakuten-room/` を見つけても勝手に同期対象にしない。ユーザーに移行確認をする。

## 画像同期の必須ルール（最重要）

- `image_url` はWebカレンダーの日付セルに商品画像を出すための必須項目。作業フォルダ内の `rakuten-room/.image-cache.json` に画像URLがある商品は、`rakuten_posts` と `rakuten_products` の両方へ必ず同じ `image_url` を入れる。
- 投稿レコードの画像は、`posts-log.md` だけで判断しない。`products.md` の商品名・URLと照合し、その商品の画像URLを継承する。
- `image_url:null` が許されるのは、キャッシュにも商品ページの `og:image` にも画像が無い場合だけ。キャッシュに画像URLがあるのに `null` でupsertした場合は同期失敗として扱う。
- JSON例の `"image_url":"..."` は説明用の文字列ではなく、実際の画像URLまたは `null` に置き換える。`"（手順4）"` や `"（下の手順4で取得）"` のような文字列をDBに送らない。

`calendar.json` の形：
```json
{ "SUPABASE_URL": "https://xxxxx.supabase.co",
  "PUBLISHABLE_KEY": "sb_publishable_...",
  "SECRET_KEY": "sb_secret_..." }
```

## いつ呼ばれるか

- リサーチ更新後・日次プラン確定後（`references/storage.md` の保存が済んだ直後）
- ユーザーが「カレンダー更新して」「同期して」と言ったとき
- **失敗してもユーザーの作業を止めない**。エラーは1行で握りつぶし「（カレンダー同期はスキップ）」程度に留める

## 手順

### 0. 推奨：同梱スクリプトで同期する

手作業でJSONを組み立てると `image_url` を落としやすい。通常はこのスキル内の同梱スクリプトを使う。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".agents\skills\rakuten-room-autopilot\scripts\sync-calendar.ps1"
```



このスクリプトは、現在の作業フォルダ内の `rakuten-room/` を読み、`.image-cache.json` を優先して `image_url` を入れたうえで、`rakuten_posts` / `rakuten_products` を再同期する。既存投稿の画像URLだけが空だった場合も、再実行で補正する。

以下は、スクリプトが使えない場合の手動手順。

### 1. 設定を読む

作業フォルダ内の `rakuten-room/calendar.json` を読む。無ければ**ここで終了**（同期しない）。

### 2. rakuten_posts のJSONを組み立てる

作業フォルダ内の `rakuten-room/posts-log.md` の「直近の投稿」テーブルから、日付が `YYYY-MM-DD` の行を拾う。各行を次の形に：

```json
{ "post_date":"2026-07-18", "post_time":"21:00〜22:00", "post_role":"関連",
  "product_name":"three-lack フットレスト 足置き台 1,880円",
  "product_url":"（products.md の同名商品のURL）",
  "image_url":"https://shop.r10s.jp/example/cabinet/item.jpg",
  "price":"1,880円", "hook_type":"問題提起型",
  "post_text":"（drafts/<日付>.md の全文。1日複数投稿なら該当セクション）",
  "status":"計画", "reaction":null, "learning":null }
```

- `post_text` は `rakuten-room/drafts/<post_date>.md` から取る。1日複数投稿の日は `## <商品名>` セクションで分かれているのでマッチする方を使う。
- `price`・`hook_type` は投稿行の文面から読み取れる範囲で。無ければ `null`。
- 商品名・URLの対応は `products.md` を正とする。
- `image_url` は対応する `products.md` の商品の画像URLを継承する。商品キャッシュに画像URLがある場合、投稿側だけ `null` にしない。

### 3. rakuten_products のJSONを組み立てる

作業フォルダ内の `rakuten-room/products.md` の候補一覧＋「除外した商品」から：

```json
{ "product_name":"...", "product_url":"...", "priority":"A",
  "price":"...", "pain_point":"（刺さる悩み）", "role_fit":"（確定役割）",
  "heat_evidence":"（加熱根拠）", "checked_on":"YYYY-MM-DD",
  "last_posted":"（最終投稿日、無ければnull）", "listing":"候補",
  "exclude_reason":null, "image_url":"https://shop.r10s.jp/example/cabinet/item.jpg" }
```

- 除外品は `listing":"除外"`、`exclude_reason` に理由、`product_url` が無ければ `"excluded://<商品名を安全な文字に>"` を割り当てて一意にする。

### 4. 商品画像（og:image）をcurlで取得

各商品URL（`http` で始まるもの）について、キャッシュ `rakuten-room/.image-cache.json` を先に見る。末尾スラッシュ有無の違いも同じURLとして扱う。無ければ：

```bash
curl -sL "<商品URL>" -H "User-Agent: Mozilla/5.0" | grep -o '<meta[^>]*og:image[^>]*>' | grep -o 'content="[^"]*"' | head -1
```

取れた画像URLを `image_url` に入れ、キャッシュに追記する（一度取れば次回は再取得しない）。取れなければ `null`。
ただし、すでにキャッシュに画像URLがある商品の `image_url` を `null` に戻してはいけない。

### 5. JSONファイルに書き出してcurlで送る

一時ファイルに配列JSONを書き（例：`rakuten-room/.sync-posts.json` / `.sync-products.json`）、curlで送る。**インライン --data は日本語で壊れるので必ずファイル（`--data @`）で送る。**

```bash
DATA_DIR="./rakuten-room"
CFG="$DATA_DIR/calendar.json"
val(){ sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$CFG"; }
URL=$(val SUPABASE_URL)
SEC=$(val SECRET_KEY)

curl -s -X POST "$URL/rest/v1/rakuten_posts?on_conflict=post_date,product_name" \
  -H "apikey: $SEC" -H "Authorization: Bearer $SEC" \
  -H "Content-Type: application/json" \
  -H "Prefer: resolution=merge-duplicates,return=representation" \
  --data @"$DATA_DIR/.sync-posts.json" -w "\nposts HTTP %{http_code}\n"

curl -s -X POST "$URL/rest/v1/rakuten_products?on_conflict=product_url" \
  -H "apikey: $SEC" -H "Authorization: Bearer $SEC" \
  -H "Content-Type: application/json" \
  -H "Prefer: resolution=merge-duplicates,return=representation" \
  --data @"$DATA_DIR/.sync-products.json" -w "\nproducts HTTP %{http_code}\n"
```

HTTP 200/201 なら成功。404＝テーブル未作成（schema.sql未実行）、401/403＝キー誤り。

### 6. 検証（重要・省略しない）

**syncの見た目だけで成功と判断しない。** 実際にDBを読み直して件数を確認する：

```bash
curl -s "$URL/rest/v1/rakuten_posts?select=post_date,product_name,image_url&order=post_date" \
  -H "apikey: $SEC" -H "Authorization: Bearer $SEC"
```

期待した日付が入っているか目視。入っていなければ原因（JSON不正・キー・テーブル）を調べる。
さらに、`.image-cache.json` に画像URLがある商品なのに `image_url` が `null` の投稿があれば同期未完了。手順0のスクリプトを再実行するか、該当行を再upsertして補正する。

### 7. 一時ファイルを消す

`.sync-posts.json` `.sync-products.json` は消してよい（キャッシュ `.image-cache.json` は残す）。

## デプロイ用フォルダ `rakuten-room/calendar-web/` を組み立てる

公開に必要なのは **index.html ＋ config.js の2ファイル**。同期のたびに次を確認・更新する：

1. **index.html を用意**：作業フォルダ内の `rakuten-room/calendar-web/index.html` が無ければ、**このスキルの `webapp/index.html` をコピー**する。
   ```bash
   mkdir -p ./rakuten-room/calendar-web
   cp "<スキルのbase>/webapp/index.html" ./rakuten-room/calendar-web/index.html
   ```
   ※webアプリを更新したいときは上書きコピーし直す。
2. **config.js を生成**：作業フォルダ内の `rakuten-room/calendar-web/config.js` を publishableキーで上書きする（**secretキーは絶対に書かない**）：
   ```js
   window.SUPABASE_CONFIG = { URL: "<SUPABASE_URL>", ANON_KEY: "<PUBLISHABLE_KEY>" };
   ```

これで `calendar-web/` に index.html と config.js が揃い、無料ホストにフォルダごと上げれば動く。

## やってはいけない

- `SECRET_KEY` を `calendar-web/` や `config.js`・HTMLに書く（公開されDB全操作可能になる）
- 実行結果を確かめずに「同期しました」と報告する（**必ず手順6でDBを読んでから**言う）
- 旧保存先 `~/rakuten-room/` を勝手に同期対象にする
