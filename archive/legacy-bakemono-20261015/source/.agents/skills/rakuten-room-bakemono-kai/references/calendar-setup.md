# カレンダー連携のセットアップ（オプトイン）

ユーザーが「**カレンダー連携したい**」「Webで見たい」「スマホで見たい」等と言ったときだけ、この手順を始める。**それまではSupabase・カレンダーの話を一切しない。**

連携すると、毎日のプラン・リサーチが Supabase に貯まり、スマホのWebカレンダーで「いつ・何を・どの文で投稿するか」を見られるようになる。

進め方：**①環境確認 → ②足りないものを用意 → ③SQL吐き出し → ④キー受領 → ⑤Web公開の案内**。1つずつ、ユーザーの確認を取りながら。

---

## STEP ①：環境確認

同期は **curl だけ**で動く（Python等は不要）。まず確認：

```bash
curl --version
```

- **出れば** → 環境OK。STEP ③へ（②はスキップ）
- **出なければ** → curlが無い。STEP ② へ

すでに作業フォルダ内の `rakuten-room/calendar.json` があれば**連携済み**。「もう連携できています」と伝え、必要なら再設定するか聞く。

## STEP ②：足りないものを用意（curlが無い場合のみ）

curl は Windows 10（1803以降）・macOS・Linux に標準搭載なので、通常ここは不要。無い場合だけ：
- **Windows**：Windows Updateで最新にする／Git for Windows を入れると `curl` が付いてくる
- **Mac/Linux**：`curl` をパッケージマネージャで入れる（例：`brew install curl` / `sudo apt install curl`）

入ったら `curl --version` を再確認してSTEP ③へ。

> ※以前は同期にPythonが必要だったが、現在は不要（curlのみ）。Pythonの案内はしない。

## STEP ③：Supabaseプロジェクトを作り、SQLを実行してもらう

1. ユーザーに案内：「https://supabase.com で無料アカウントを作り、**New project** を1つ作ってください（リージョンは Tokyo が速い）」
2. **schema.sql の中身をそのままチャットに貼り出す**（`scripts/schema.sql` を Read して全文を提示）。「Supabaseの左メニュー **SQL Editor → New query** に貼って **Run** してください」と案内
3. `Success. No rows returned` が出れば完了。これで2つのテーブル（`rakuten_posts` / `rakuten_products`）とRLS（誰でも読める・誰も書けない）が作られる

## STEP ④：接続情報をもらって保存する

Supabaseダッシュボードから3つの値をもらう。**取得場所も具体的に案内する**：

| 値 | 取得場所 | 公開してよいか |
|---|---|---|
| Project URL | Settings → Data API | ✅ |
| **publishable** キー（`sb_publishable_...`） | Settings → API Keys | ✅（RLSで守られる） |
| **secret** キー（`sb_secret_...`） | Settings → API Keys → Reveal | ❌ 公開厳禁 |

> ※古いプロジェクトは `anon public` / `service_role`（`eyJ...` 形式）の場合がある。どちらでも動く。

もらったら作業フォルダ内の `rakuten-room/calendar.json` に保存（Write）。**各値は1行1項目・閉じ `}` は独立行**にする（同期時のキー抽出が末尾記号を拾わないため）：
```json
{
  "SUPABASE_URL": "https://xxxxx.supabase.co",
  "PUBLISHABLE_KEY": "sb_publishable_...",
  "SECRET_KEY": "sb_secret_..."
}
```
- **secretキーはこのファイル（ローカル）だけに置く。** webアプリ側には絶対に書かない
- チャットにsecretキーが残るのが気になる人には「デモ後にSettings→API Keysで再発行できます」と添える

保存できたら、**その場で一度 `references/calendar-sync.md` の同期を実行**して、既存の作業フォルダ内 `rakuten-room/` の内容をSupabaseに入れる。手順6でDBを読んで件数を確認し、「◯件入りました」と実数で報告する。

## STEP ⑤：Webカレンダーを公開する

STEP④の最後の同期で、デプロイ用フォルダ 作業フォルダ内の `rakuten-room/calendar-web/` に **index.html（スキルのwebappからコピー）＋ config.js（publishableキーで生成）** が組み立てられる（手順は `calendar-sync.md`）。**このフォルダを丸ごと無料ホストに置く**とスマホで見られる。

案内する方法（**アカウント不要で一番簡単なのは Cloudflare Pages のドラッグ&ドロップ or Netlify Drop**）：

1. **Netlify Drop**：https://app.netlify.com/drop を開く → 作業フォルダ内の `rakuten-room/calendar-web/` フォルダを**ドラッグ&ドロップ** → 数秒で公開URLが出る（無料。継続利用は無料アカウント作成を促される）
2. **Cloudflare Pages**：https://pages.cloudflare.com → Direct Upload でフォルダをアップ
3. **自分のサーバーがある人**：`calendar-web/` の中身をそのままアップロード

公開できたら：
- そのURLをスマホのホーム画面に追加すると毎日ワンタップで開ける、と案内
- **`config.js` はpublishableキーのみ**なので公開して安全。**`calendar.json` は絶対にアップしない**、と念押し
- 「以後はプランを作るたびに自動で同期される。Webは再読み込みすれば最新になる」と伝える

> ⚠️ Supabase無料プランは **7日間クエリが無いとプロジェクトが一時停止**する（毎日使っていれば起きない）。長期間空ける人には「たまに開けば止まらない」と一言添える。

---

## 連携後の挙動（重要）

- 連携済み（`calendar.json` あり）なら、**プラン・リサーチの保存後に自動で同期**する（`calendar-sync.md`）。ユーザーに毎回「同期しました」と言わなくてよいが、**件数が変わる操作のときは「カレンダーにも反映しました」と一言添えてよい**
- 同期が失敗してもプラン提示は止めない（エラーは握りつぶす）
- **未連携なら、この一切を行わない。** 従来通りチャットで完結する
