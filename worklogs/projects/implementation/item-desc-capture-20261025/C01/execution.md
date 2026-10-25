# C01 / STEP1 実行記録

- 結果: PASS。要件正本を2.61から2.62へ更新し、本文・内部`requirements-source`・辞書・追跡表を同期した。
- 基準: branch `main`、STEP開始時HEAD `f4420bf`。正本HTMLの基準SHA-256は計画source snapshotの値とも一致した。
- 対象: `docs/specifications/inago/イナゴスキル要件定義書.html`、`docs/specifications/inago/要件データ辞書.md`、`docs/specifications/inago/要件追跡表.md`。
- 要件追跡: IR-010/011/014/017/018/026/028、補足受入AT-ITEM-DESC-01。版2.62にselector全件、HTML全文、画像由来と失敗状態、page.json/ocr.json、OCR全文、同一商品の作成/独立review入力、旧captured互換、DB migrationなし、通常resume挙動維持を記載した。

## 操作・結果

| 操作 | 正確なコマンド | 期待値 | 実結果 | 終了コード |
|---|---|---|---|---:|
| 正本再生成 | `node tools/docs/inago-requirements/build.mjs` | 2.62、28章、内蔵参照を保持して生成成功 | 3回目の実行で2.62、28章、39図、内蔵参照26件を生成 | 0 |
| 同一source再生成 | `node tools/docs/inago-requirements/build.mjs` | 再生成後も意味とHTML bytesが安定 | 28章・39図・参照26件。同じHTML SHA-256 `e0a413c1ccefb69c201819af41fd943d326c5bf3aa8df6093a4d69b2de544a79` | 0 |
| 文書照合 | `node worklogs/projects/implementation/item-desc-capture-20261025/C01/verify.mjs` | 版/日付、全章、要件文、内蔵資料と外部参照の一致 | 9項目、28章、26参照の一致を確認 | 0 |
| whitespace確認 | `git diff --check` | 差分の空白エラーなし | エラーなし。追跡表にCRLFを次回LFへ正規化するGit warningあり | 0 |

基準HEAD `f4420bf` と結果fileのSHA-256/byte数は `result.json` に記録した。buildは最初、追加表の空行、次に独立review指示書表の列数で各1回失敗した。Markdown表の境界・列数だけを修正し、3回目と再生成は成功した。途中のsource変換と基準hash取得では、重複prefix・既定buffer上限による補助コマンド失敗もあったが、どちらもファイルを変更せず、再実行で解消した。検証用の最初の章数assertionは27.19の履歴節を章と数えて失敗したため、主章番号の範囲を明示して修正し、保存済み`verify.mjs`で再実行している。

## 範囲と証拠

- product code、tests、skills、設定、DB、Scheduler、実ROOM、AI、日次runは変更・起動していない。
- 要件HTMLの図解副生成は39図で、build後のGit statusに図解差分はなかった。
- HTMLは静的な文書build/本文照合で確認した。ブラウザー画面・動画・traceは本STEPでは不要なため未取得。
- STEP2はこのSTEPの完了後に開始する。製品変更・feature testsの結果を本記録へ先取りしていない。
