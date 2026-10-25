# C06 / STEP4 writer・reviewerへの全文入力

- 結果: PASS。
- 基準: `main`、開始時HEAD `f4420bf`、仕様2.62、page/OCR schema_version 1。
- 対象: `prompt_builder.py`と`ai_runner.py`の指示、writer/reviewerのskill・契約、文型ルール、`test_product_page_copy.py`。
- `production_runtime.py`は変更していない。copy factoryがpage selector配列の全record/全outer_htmlとOCR画像配列の全textを動的にjob payloadへ写すことを、隔離memory DB fixtureで確認したため変更不要。

## 局所試験

| Node | 正確なコマンド | 期待 | 実結果 | exit |
|---|---|---|---|---:|
| RED: item_desc指示の輸送 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py` | writer/reviewerのskill/promptが4 selectorとitem_desc画像OCRを明示 | 1 failed、3 passed。job payloadは既にitem_descを運んでいたが、staged skillには3 selector列挙しかないことを再現 | 1 |
| C06最終 | `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py` | factory、同一入力のwriter/reviewer prompt、refs、改行、大容量輸送、staged skillを確認 | 4 passed (0.63s) | 0 |
| Python parse | `.venv-inago/Scripts/python.exe -m py_compile automation/inago/prompt_builder.py automation/inago/ai_runner.py tests_inago/test_product_page_copy.py` | 変更Python全件parse成功 | errorなし | 0 |
| whitespace | `git diff --check` | whitespace errorなし | errorなし。変更skill群および既存adapter/追跡表にCRLF→LF予定warning | 0 |

## 確認結果

- copy factoryから作成されたselector順はsale_desc、normal_reserve_item_name、normal_reserve_catch_copy、item_desc。item_descの完全outerHTMLと同一商品のOCR画像全文がjob inputへ入り、HTML/OCR refsが`input_refs`内に残る。
- AI-O transport promptはwriterと独立reviewerへ同じ未省略の一商品入力を渡し、item_desc HTMLとその領域画像のOCRも明記する。両specialistのstaged skillとcontracts、writerの文型ルールでも明記をassertした。
- raw OCR engine responseはcopy payloadへ含めない。200KB超のitem_desc HTMLとOCRテキストはjob input/promptで全文維持。
- Product/runtime copy factory、copy API schema、DB schema、実AIは変更・実行していない。隔離された`:memory:` SQLiteとjob stagingだけを使った。
- ひとつ前のassertion反復でwriter skill本文のMarkdown backtickを期待文字列に含めず1件失敗したため、テスト期待値を実際のselector表記に合わせた。最終C06はpass。
