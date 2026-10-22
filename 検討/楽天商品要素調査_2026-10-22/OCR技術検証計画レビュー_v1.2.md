# OCR技術検証計画 v1.2 — 独立再レビュー

- 対象: `検討/楽天商品要素調査_2026-10-22/OCR技術検証計画.md`
- 対象版: 1.2
- 対象SHA-256: `F75693DB05FF2897F03C82897781258721EDCCE9D020073E3E9FBAC8C94B6D42`
- 対応map/result: `requirement-step-case-map.json` と `result.json`。両方ともplan_version `1.2`、plan_sha256は本文と一致。
- 判定: `changes_requested`（対応mapの1 path表記）
- 範囲: 計画・対応map・result・既存画像収集資料をread-onlyで確認。Web調査、OSS導入、OCRは行っていない。

## 前回所見の確認

1. **Tesseract native executableと日本語データの隔離:** 修正済み。portable `tesseract.exe`、必要DLL、`jpn.traineddata` の検討配下配置、`pytesseract.tesseract_cmd` / `--tessdata-dir` の明示指定、版・source・license記録がSTEP 02とCASE-02にあります。既知のCLI未検出を明記し、隔離できない場合はその候補の資材を作らず次点へ進む方針もあります。
2. **cache等の隔離不能資材:** 修正済み。候補の永続package/native/model/dataとpip/model/TEMP/TMP/cacheを検討配下へ置けない場合、その候補の資材を生成せず候補操作を `not_performed` として次点へ進む定義になっています。偶発的な外部生成は事故として記録し、所有成果物・出力に含めない扱いで、mapの制約にも反映されています。
3. **R-04の実証条件と手続き完了:** 修正済み。商品別確認数・分母・根拠・確認商品数を記録し、0/5を「実証されず」とする結論と手続き完了を分けています。0件でも他の照合・報告・後続を止めません。
4. **R-07のFact/evidence:** 修正済み。F-09と共通計画スキルのevidenceが計画とmapのR-07へ結び付いています。

## 指摘

### [low] mapのR-02出力pathが計画の作業場所と一致しない

**場所:** `requirement-step-case-map.json` の `requirements` 内 `R-02.output`

計画本文では作業先を `検討/楽天商品要素調査_2026-10-22/OCR技術検証/` としています。一方、mapのR-02.outputは `検討/OCR技術検証/.venv-ocr` で始まり、`楽天商品要素調査_2026-10-22` の階層が抜けています。ほかのmap要素では `OCR技術検証/...` を親計画フォルダー相対で表す箇所があり、R-02だけ別の誤ったroot-relative pathに見えます。ownerへのhandoff pathを誤らせるおそれがあります。

**修正案:** R-02.outputを `検討/楽天商品要素調査_2026-10-22/OCR技術検証/...` の完全pathか、計画フォルダー相対で統一してください。計画本文のSTEP 02とCASE-02は対象場所と隔離手順を正しく指定しています。

## その他の確認

- 計画版は1.2で、指定されたSHA-256を実測確認しました。mapとresultのversion/hashも一致しています。JSON両ファイルは読み取り可能で、mapは9 facts・7 requirements・5 steps・5 casesを持ちます。
- 0〜14節、5 STEPの14項目と全文prompt、5 CASEの12項目を確認しました。既存nodeが未提示であることを記し、存在未確認のpytest nodeを作っていません。
- 既存根拠と、5商品・59画像（9/11/6/24/9）、すべて `span.sale_desc` から保存、説明以外の画像を含む、OCR未実施という記述は整合しています。
- 全画像のOCR、全文・空・失敗recordの保存、元画像hash確認、productコードとproduction venvへの非介入、レビュー/試験/未確認を後続gateにしない規則が維持されています。
- `result.json`は計画作成時点で再レビューpendingを記録したものです。この再レビューではファイルを変更していません。

## 引き継ぎ

mapのR-02.output path所見をownerへ渡してください。これは後続stageの開始gateではありません。計画、map、resultは本レビューでは編集していません。
## 追補 — R-02.output path再確認

- 再確認対象: `requirement-step-case-map.json` の `requirements.R-02.output`。
- 結果: 前回所見は解消しています。pathは `検討/楽天商品要素調査_2026-10-22/OCR技術検証/.venv-ocr` 以下を指す形に修正されています。
- version/hash: mapと`result.json`はいずれもplan_version `1.2`、plan_sha256 `F75693DB05FF2897F03C82897781258721EDCCE9D020073E3E9FBAC8C94B6D42`。計画本文の実測SHA-256とも一致します。
- この再確認の判定: R-02.output path finding resolved。今回確認した範囲で追加指摘なし。計画本文は不変です。