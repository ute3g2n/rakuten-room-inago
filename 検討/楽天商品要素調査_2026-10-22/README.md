# 楽天商品ページ要素・画像取得試行（2026-10-22）

Playwrightで楽天市場の商品ページ5件を開き、次のセレクターの存在、テキスト長、HTML構造、内包画像を確認した。

- `span.sale_desc`
- `span.normal_reserve_item_name`
- `span.normal_reserve_catch_copy`

## 結果

| 商品フォルダー | 商品URL | HTTP | 各セレクター | `sale_desc`文字数 | 保存画像 |
|---|---|---:|---:|---:|---:|
| `01_meiji-ya_129` | [meiji-ya/129](https://item.rakuten.co.jp/meiji-ya/129/) | 200 | 各1件 | 137 | 9 |
| `02_dripcoffee_house-500-4` | [dripcoffee/house-500-4](https://item.rakuten.co.jp/dripcoffee/house-500-4/) | 200 | 各1件 | 44 | 11 |
| `03_blueritual_kh001` | [blueritual/kh001](https://item.rakuten.co.jp/blueritual/kh001/) | 200 | 各1件 | 8 | 6 |
| `04_sawaicoffee-tea_ac-sale-1960` | [sawaicoffee-tea/ac-sale-1960](https://item.rakuten.co.jp/sawaicoffee-tea/ac-sale-1960/) | 200 | 各1件 | 1,798 | 24 |
| `05_kkonlinestore_cafe07` | [kkonlinestore/cafe07](https://item.rakuten.co.jp/kkonlinestore/cafe07/) | 200 | 各1件 | 11 | 9 |

全ページで3つの要素を検出した。`normal_reserve_catch_copy` は `dripcoffee/house-500-4` では空で、残る4ページではテキストがあった。画像は重複URLをまとめて計59ファイル保存した。

## 商品ごとのファイル

- `span_..._match_01.html`: セレクターに一致した要素のタグ構造と属性
- `images/`: 対象要素内の画像ファイル
- `manifest.json`: URL、HTTP結果、セレクター別の文字数と一致件数、画像URL・保存結果
- `index.json`: 5ページの一覧

## 取得方法と制限

- Microsoft EdgeをPlaywrightから起動し、ページのDOMを確認した。
- 商品説明文の再掲を避けるため、HTMLファイルにはタグ構造と画像参照属性を残し、テキストノード・元HTMLコメント・商品文を含みうる属性値を省略した。HTMLテンプレート内のテキストも同様に省略している。商品説明文そのものは保存していないため、これらは原文の完全なHTMLではなく、構造確認用のスナップショットである。
- 文字数はページ上の要素の `textContent` のJavaScript文字数。保存したHTMLではなく、取得時DOMの値から計測した。
- `src`、`currentSrc`、`srcset`、遅延読み込み属性、インライン背景画像を調べ、許可した楽天画像ホスト `image.rakuten.co.jp` と `www.rakuten.ne.jp` から取得した。候補属性が同じ画像URLを指す場合は、画像ファイルを1点にまとめた。
- 再実行コマンド: `node 検討/capture_rakuten_product_elements.mjs`
