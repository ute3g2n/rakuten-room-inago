# OCR技術検証計画 v1.1 — 独立再レビュー

- 対象: `検討/楽天商品要素調査_2026-10-22/OCR技術検証計画.md`
- 対象版: 1.1
- 対象SHA-256: `DB4F5FC67E34FD63E925AB587065552C0BEBCFC09C5FB485890E9CCEF9D6BF77`
- 対応map: `requirement-step-case-map.json`（plan_version 1.1、plan_sha256一致）
- 対応result: `result.json`（plan_version 1.1、plan_sha256一致）
- 判定: `changes_requested`
- 範囲: 計画・対応map・result・既存の画像収集資料をread-onlyで確認。Web調査、OSS導入、OCRは実行していない。

## 前回の指摘の確認

1. **説明情報の実証条件:** 修正済みです。R-04、STEP 04、CASE-05は確認画像数・対象数・確認商品数を報告し、手続き完了と技術実証を分け、0/5なら「実証されず」とするようになっています。結果が0件でも後続を止めない方針も維持されています。
2. **cache/TEMPの隔離:** 一部反映されていますが、未解決です。検討配下へのredirectを試す一方、設定不能と分かった場合は外部pathへの生成を許容し、その記録だけで継続する記述が残っています（finding 2）。
3. **R-07根拠:** 修正済みです。F-09と `.agents/skills/common-execution-planning/SKILL.md` のevidenceが追加され、planのR-07、追跡表、mapで結び付いています。

## 指摘

### 1. [medium] Tesseract候補のnative executableと日本語データを隔離導入する手順がない

**場所:** F-05（55行）、候補比較（90行）、出力契約（119–129行）、STEP 02（188–194行）、CASE-02（290–303行）

計画はTesseract + `pytesseract` を候補に含めています。既知環境では `tesseract` CLIがPATH上にありません（F-05）。`pytesseract` はPython wrapperなので、Tesseract本体の実行ファイルと日本語 `traineddata` がなければOCRは実行できません。STEP 02の所有範囲・導入コマンド・保存先はvenvとcache等を扱いますが、この候補を選んだ場合の本体・日本語データの配置先、参照設定、版/ライセンス記録が定義されていません。system installを避ける条件のままでは、候補選定後に実行不能となるか、暗黙に検討フォルダー外へ導入する可能性があります。

**修正案:** Tesseractが選ばれた場合は、native executableと日本語traineddataを検討配下へ置く方法、`pytesseract`からその実体・data directoryを指定する方法、版/ライセンス/出典の記録をSTEP 02とCASE-02へ追加してください。配布元が検討配下への配置をサポートしない場合は、その候補の導入操作だけ未実施として理由を残し、次点の候補へ進む扱いにします。

### 2. [medium] unredirectable cacheを外部に生成してよいままになっており、保存先要件とmapが一致しない

**場所:** R-06（70行）、出力契約（124行）、STEP 02（189–194行）、CASE-02（296–303行）、map constraints の `pip_model_temp_cache_paths_redirected_under_study_folder`

R-06はすべての追加資材を `検討` 配下に置くとしています。しかし、設定で隔離できない生成先について、計画は外部pathへの生成を許し、そのpathと影響の記録だけで作業を続けます（124、147、189、191、194行）。mapはcache等を検討配下へredirect済みと断定するbooleanを持つ一方、同時にunredirectable pathを許容しており、実行可能な計画の振る舞いと一致しません。

**修正案:** cache/TEMPを検討配下へ設定し、永続するモデル・package資材を隔離できない場合はそれを外部へ生成しないで、その導入操作だけ `not_performed` として次候補・他作業を続けてください。意図しない外部生成が偶発した場合はpathと種類を記録し、今回の所有物として扱わないよう区別してください。mapのbooleanも保証できる意味に合わせてください。これは承認gateではなく、ユーザーが指定した保存範囲を守るための個別操作の扱いです。

## その他の確認

- 計画はv1.0所見を受けてR-04とR-07を具体化しています。
- 0〜14節、5 STEPの14項目と全文prompt、5 CASEの12項目を確認しました。既存pytest nodeがないことを明記しており、nodeを捏造していません。
- 5商品・59画像（9/11/6/24/9）、既存 `span.sale_desc` 範囲、非説明画像の混在、OCR未実施という根拠は既存 `index.json`、README、確認レポートと整合しています。
- mapとresultのplan version/hashは本文の1.1および上記SHA-256と一致します。R-07/F-09とR-04/CASE-05の追跡も更新されています。
- 技術検証失敗・未確認・レビュー結果を後続のgateにしない方針、投稿文生成系・production venvへの非介入、全画像OCR・全文保存が維持されています。

## 引き継ぎ

上記2点を計画ownerへ返してください。所見は後続作業の開始gateではありません。今回の再レビューでは計画、map、resultを編集していません。