# 楽天公式の商品検索・ランキングAPI

このCLIは楽天ウェブサービスの[商品検索API（2026-07-01版）](https://webservice.rakuten.co.jp/documentation/ichiba-item-search)と[ランキングAPI（2022-06-01版）](https://webservice.rakuten.co.jp/documentation/ichiba-item-ranking)を呼び、商品ID・名称・価格・レビュー件数・商品URLなどを短く表示する。商品台帳や投稿ログには自動登録しない。

## 利用前に必要なこと

1. [利用規約](https://webservice.rakuten.co.jp/guide/rule)第10条1項(10)は、特定の人のみがアクセスできる環境での利用を、楽天が明示的に許可した場合を除き禁止している。現在のPC専用CLIはこれに該当する可能性がある。実運用前に、規約に沿う公開アプリ構成にするか、楽天の明示的な許可を得る。明示的な許可を希望する場合は[楽天ウェブサービスの問い合わせフォーム](https://webservice.faq.rakuten.net/hc/ja/requests/new)で用途と公開範囲を伝える。ROOMが公開されているだけで、このCLI自体を公開アプリとみなさない。
2. [楽天ウェブサービスの新規アプリ登録](https://webservice.rakuten.co.jp/app/create)で、実際の用途に即したアプリ名・URL・種類・許可サイト・データ利用目的・予定QPSを登録する。楽天アフィリエイト登録とは別の手続き。発行されたアプリIDとアクセスキーを確認する。[公式ガイド](https://webservice.rakuten.co.jp/guide)参照。ROOMのURLを、実際には存在しないアプリのURLとして登録しない。公開アプリでAPIデータを表示する場合は[公式クレジット表示ガイド](https://webservice.rakuten.co.jp/guide/credit)に従う。
3. プロジェクト直下の `rakuten-api.credentials.json` を開き、楽天ウェブサービスのアプリIDとアクセスキーを入力する。このファイルは `.gitignore` に登録済みで、Gitには含まれない。ID・キーはチャットに貼らない。アクセスキーはHTTPヘッダーで送られ、CLIの引数・出力には出ない。

```powershell
notepad .\rakuten-api.credentials.json
```

値は以下のJSON項目へ貼り付ける。外側の引用符とカンマは残す。

```json
{
  "applicationId": "ここにApplication ID",
  "accessKey": "ここにAccess Key"
}
```

ファイルの値より `RAKUTEN_APP_ID` と `RAKUTEN_ACCESS_KEY` 環境変数が優先される。楽天アフィリエイトIDは現在の調査CLIでは使わない。

楽天アフィリエイトのログインID・パスワード・アフィリエイトIDは、この調査CLIには不要。

## 実行

```powershell
npm run rakuten:verify
npm run rakuten:search -- --keyword '時短 おかず' --hits 10
npm run -s rakuten:search -- --item-code 'amuood:mail-50-002_c' --hits 1 --format json
npm run rakuten:ranking -- --page 1
npm run -s rakuten:ranking -- --genre-id 100283 --page 1 --format json
npm run research:collect
```

`verify` は商品検索1件と総合ランキング1ページを順番に呼び、各1商品だけ表示する。楽天の規約と認証情報の準備が済んでから実行する。429が返ったときは自動で繰り返さず、時間を空ける。`--format json` は後続のローカル処理向けの整形済みデータを標準出力へ返す。

`research:collect` はローカル調査ブリーフの不足ジャンルと検索語設定を使って検索APIを連続実行し、重複除外・投稿済み照合済みの結果を `rakuten-room-kai/research/api-results/` に保存する。詳細は[ローカル調査ブリーフ](../rakuten-room-kai/research/README.md)を参照。

`npm test` は通信を模したテストとローカルHTTPサーバーへの実通信テストを実行する。楽天の本番APIには接続しない。`npm run -s` はnpmの実行見出しを省くため、JSONをそのまま次の処理へ渡せる。

5つの運用ジャンルは楽天市場のジャンルIDと1対1ではない。ジャンルIDを推測して固定せず、キーワード検索や[ジャンル検索API](https://webservice.rakuten.co.jp/documentation/ichiba-genre-search)で確認したIDを指定する。APIの価格・在庫などは取得時点の値であり、投稿前に商品ページで再確認する。
