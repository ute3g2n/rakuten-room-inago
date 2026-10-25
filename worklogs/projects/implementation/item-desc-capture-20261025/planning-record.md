# span.item_desc 対応の計画作成記録

- 本人依頼: 2026-10-25。進行中の補充検索修正が完了した後、item_desc追加対応の計画だけを作成する。
- 先行タスク: 最終回帰178件合格、mainの2eb41c2をorigin/mainへpush済み。開始時Git変更なし。
- 固定root: C:/project/rakuten-room-inago。別worktree/checkoutなし。
- 今回の許可範囲: 現行コード・仕様・既存試験の読取、実行計画・対応表・独立計画レビュー・本記録の作成。
- 今回の非実施: item_desc対応の要件HTML/製品/skill/試験の編集、商品ページへのアクセス、DB/設定/Scheduler操作、OCR/本文生成、試験の実行。

## 依頼の追跡

1. 将来の実行順は、仕様（要件定義HTML）修正を最初とし、その後実装・テスト。
2. ROOMの商品ページ遷移時にspan.item_descのHTML全文を追加取得し、page.jsonへ登録。
3. item_desc内の画像データ全てを追加取得し、sale_descの既存処理も維持。両方存在する場合は両方扱う。
4. item_desc内画像もOCRし、抽出結果をocr.jsonへ登録。
5. 投稿文作成skillへ、item_desc HTMLとitem_desc画像由来OCRテキストも参考にする旨を明示。
6. 画像の重複とDOM上の出現箇所を区別し、既存page/ocr・旧runの扱いと失敗/再開を計画に含める。
7. 今回は計画だけであり、将来作業の実施済み/PASSを作らない。

計画本体と対応表は plans/projects/implementation/item-desc-capture-20261025/ に保存。独立レビューは本フォルダーのplan-review.mdへ保存する。計画文書の検証結果は下記へ記録した。最終Git結果は応答とローカル実行ログへ保存する。

## 最終結果

計画版1.1を作成し、独立計画レビューはapproved。初版の指摘PR01（通常resumeから画像再取得へ到達する保証が曖昧）は、保証範囲を局所保存/OCR関数の明示再呼出しへ限定して解消した。通常run resumeの挙動変更はこの計画に含めず、既存の正常保存データ再利用の回帰だけを行う予定とした。

実行順は、(1)正本要件HTMLの修正、(2)item_desc HTML/全画像取得とpage.json、(3)既存OCR全件処理の活用とocr.json、(4)作成・独立レビューの全入力/skill同期、(5)統合・回帰と記録。すべて将来のnot_started。

文書機械確認は15節、5STEP×14項目、5全文prompt、7case×12項目、7要件/9factの対応、既存6node実在と新設予定7node未作成の区別を確認しPASS（plan-validation.json）。これは文書検証であり製品試験ではない。最初の検証補助コードはnode表現を文字列と仮定してAttributeErrorとなったが、対応JSONのnodeオブジェクトを正しく読む形へ補助コードを直し最終検査を完了した。製品コードは変更していない。

今回9ファイルのsource-snapshotと照合して要件HTML・取得/OCR/AI/skillの内容が基準2eb41c2から変わっていないことも確認。item_descの試験・build・ブラウザー・OCR・AIは未実行。

自己点検: 正確性4（source根拠/独立レビュー/機械照合、実運用は未実測）、完全性4（依頼7項目を5工程7caseへ対応、通常resume変更は範囲外と明示）、明瞭性4（未取得/DOM不在、予定/実績を区別）、実行可能性4（所有・全文prompt・コマンド/将来nodeを明記、将来のfixture追加は必要）、簡潔性4（詳細計画は長いため最終応答は実行順とリンクだけ）。平均4.0。改善優先は今回未実施の明記、再開保証の限定、既存nodeと予定nodeの区別。本人の『計画だけ』という範囲を越えていないことを確認した。
