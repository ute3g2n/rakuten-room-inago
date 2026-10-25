# STEP5 独立製品レビュー

## 対象と結果

- 結果: `changes_requested`。これはレビュー時点の観測値であり、後続作業を止める条件ではない。
- 対象版: 固定root `C:/project/rakuten-room-inago` の現在の作業差分（基準HEAD `f4420bf`）。ファイルhashは生成・比較していない。
- 計画: `plans/projects/implementation/item-desc-capture-20261025/plan.md` 1.1、要件R02～R06、STEP2～5、C02～C07。
- 読取対象: 要件HTML・辞書・追跡表、`automation/inago/adapters/product_page_inputs.mjs`、`automation/inago/prompt_builder.py`、`automation/inago/ai_runner.py`、writer/reviewerのskillと契約。未変更のOCR/loader/copy factoryも、既存の全画像処理と入力参照を確認する範囲で読んだ。
- テストコード・fixtureの独立レビューはしていない。以下は実行記録のstatusとnodeだけを確認した。

## 所見

### [P2] 不正なbase64 data URIを取得成功として保存する

- 場所: `automation/inago/adapters/product_page_inputs.mjs:282-291, 324-361`
- 要件/ケース: R03/R04、§5、C02/C04/C05。
- `Buffer.from(payload, 'base64')`は不正文字や不完全なbase64を厳密には拒否せず、空または一部だけ復号したbytesを返し得る。その後、content typeが`image/*`ならそのbytesを保存して`status: downloaded`にするため、壊れた/空の画像が取得済み扱いになり、OCR失敗まで取得異常が見えない場合がある。
- 修正案: data URI base64の文字集合・padding/長さを検証し、復号不能または0 byteなら`inline_decode_error`等の失敗状態にして`downloaded`にしない。

### [P2] 非HTTP・非data/blob画像候補を記録せず破棄する

- 場所: `automation/inago/adapters/product_page_inputs.mjs:177-195`（特に190、194行）
- 要件/ケース: R03、辞書`docs/specifications/inago/要件データ辞書.md:185`、C02/C04。
- `img`/`source`属性や背景から候補を列挙した後、URL parse失敗と`http(s)`以外のschemeを`return`で捨てている。該当候補は`page.json.images[]`にも失敗理由にも残らず、OCR対象にもならない。辞書は候補を無言で捨てない契約を明記している。
- 修正案: 安全のため取得しないschemeや無効URLも候補記録へ残し、`source_type_error`/`invalid_source`等の明示statusと全occurrenceを保存する。HTTP(S)の既存公開先検証は維持する。

### [P2] lazy画像・HTMLの更新完了を確認せず2 frame後に確定する

- 場所: `automation/inago/adapters/product_page_inputs.mjs:154-170`
- 要件/ケース: R02/R03、§4～5、C03。
- 各root/`img`をscrollした後は`requestAnimationFrame`を2回待って再取得するだけで、画像の`load`/`error`、遅延属性の更新、DOM追加の完了または未完了を観測・記録していない。lazy loaderがその2 frameより後に`src`を設定したり要素を追加した場合、`outer_html`と候補一覧は早い時点の内容で確定し、pageが`captured`でもその画像は存在しないままになる。
- 保存済みC03のブラウザー証拠はsynthetic fixtureでIntersectionObserverの簡単な遅延更新を示すが、遅い非同期更新/未完了の判別までは示さない。
- 修正案: lazy更新/画像状態を観測してから再取得し、有限の待機で完了しない場合は未完了・失敗を明示記録する。無限待機にはしない。

### [P2] data/blob再試行でDOM再取得例外を握りつぶす

- 場所: `automation/inago/adapters/product_page_inputs.mjs:394-418`（特に398-401）
- 要件/ケース: R03/R04、C04。
- 明示的な`retryFailedImages`時にdata/blob用DOM再取得が例外になると空のcatchで無視される。そのまま戻り値が`existing_preserved`になり、今回の再試行が失敗した理由を呼出側にもmanifestにも示さない。既存画像レコードの前回statusは残るが、再試行操作の失敗と再試行されなかった理由は観測できない。
- 修正案: 以前の画像bytes/HTMLを保持したまま、再取得の失敗を安全なerror class/statusとして戻り値と作業記録へ出す。

## 確認できた適合点

- `SELECTORS`は従来3 selectorの順を保って`span.item_desc`を4番目へ追加している。captureはsale/item双方の全一致outerHTMLを保持し、selector状態も記録する。
- 画像列挙は両領域を対象に`currentSrc`、遅延属性、`srcset`、`source`、style/computed backgroundを確認し、同一URLを統合した後もselector/match/DOM/attribute/order由来を保持する。data/blobはページ内でbytesを得る実装で、HTTP(S)は既存のpublic URL・redirect検査を通る。
- 既存Python loaderはselector keyを固定せずschema v1を読む。`ProductPageOCR.process_product`は`page.json.images[]`を全件順番に処理し、失敗もOCR結果へ保存する。OCR製品コード変更が不要だったとのC05記録は、この読み取りと整合する。
- copy factoryは全selectorのouterHTMLとOCR画像の全文を同じ商品jobへ投影し、refsも商品単位で保持する。prompt、writer/reviewer skill、文型ルールにitem_descとitem_desc画像OCRの使用が明記されている。copy factory/API/schemaの変更が不要とのC06記録は、読取内容と整合する。
- DB、日次run、実AI、ROOM/商品サイトを操作する新しいコードは見当たらず、review/test結果を開始・再開gateとして使う自作制限も対象差分内には見当たらない。

## 確認した実行証拠と制約

- C02/C04の記録: capture・URL safetyの旧node集合は13 passed、JavaScript parseと`git diff --check`もexit 0。browser nodeはその後別ファイルへ移動・改名されている。
- 現在のbrowser node `tests_inago/test_product_page_item_desc_browser.py::test_item_desc_lazy_images_are_captured_with_visual_evidence`: 1 passed、exit 0。局所fixtureのスクリーンショット、video、trace、manifest/bytes照合が`C03/evidence/`にある。外部ROOM/商品サイトの実測ではない。
- 現在のcapture/URL safety集合: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_url_safety.py` → 12 passed、exit 0。
- C05記録: fake worker中心のOCR nodeは8 passed。実engine test用JPEGが欠落して実PaddleOCRは未実施。最終回帰でも、既存実engine/研究データfixtureがないためOCR調査2 nodeが失敗している。
- C06記録: writer/reviewerへのitem_desc全文/OCR輸送・skill指示の局所確認は4 passed。C07のcapture→OCR→copy-input、既存resume、cleanup nodeは3 passed。cleanup nodeは単独でも1 passed。
- 計画の回帰範囲を既定一時領域で実行した際は`54 passed, 5 failed, 21 errors`。fixture DB用`tmp_path`が許可されたprivate fixture root外だったためsetup errorが出た。private rootとpytest basetempを設定して同じ範囲を実行した結果は`76 passed, 4 failed`、exit 1。残る失敗は上記OCR fixture欠落2件と、今回変更していない`tests_inago/test_copy_quality_selection.py`の補充イベント/OCR呼出数期待2件。後者は単独実行でも2 failedとなった。回帰全体をPASSとは扱わない。

以上の所見は製品差分の改善依頼として親担当へ渡す。レビュー結果および既存回帰失敗を、後続工程の停止条件にはしない。

## v2.64 独立follow-up（2026-10-25）

### 対象・証拠結合

- 対象は固定rootの現在差分にある `automation/inago/adapters/product_page_inputs.mjs`、要件正本HTML v2.64、`docs/specifications/inago/要件データ辞書.md:3,181`、`docs/specifications/inago/要件追跡表.md:3`。コード変更は `git diff` で実物を読んだ。hashは生成・比較していない。
- 計画参照: `plans/projects/implementation/item-desc-capture-20261025/plan.md` のR03/R04/R07、§5、C04/C07。HTML正本は1行生成HTMLで、`<title>`/見出しの2.64とAT-ITEM-DESC-01の補足要件を確認した。
- テストコードはレビューせず、試験も実行していない。C07の実行記録と合成証拠だけを読んだ。
- C07記録は `worklogs/projects/implementation/item-desc-capture-20261025/C07/result.json:7` が `specification_version: 2.63` のままで、実行記録も同ディレクトリのv2.63再確認までしか含まない。C07の保存記録には本追確認時点で2.64の18 passed / 3 passed、または実行コマンド・コード版との結び付きがない。担当親からその件数の連絡はあるが、保存証拠未確認として扱う。`C07/evidence/page.json` はitem_descを含む4 selector、2画像downloaded、`lazy_load_observed_ms=507` の合成成功例であり、今回の境界ケースを証明しない。計画回帰は親の連絡では実行中で、最新結果は未確認。
- controllerの不変job_id/source_run_id/target_dateはこのreview依頼に含まれない。推測せず、上記の識別可能な計画と成果物を対象にした。

### 6指摘の再確認

| 前回/今回の指摘 | 判定 | 根拠と影響 |
| --- | --- | --- |
| invalid/unsupported URL候補が破棄される | 解消 | `product_page_inputs.mjs:278-299` でparse例外と非HTTP(S) schemeを `invalid`/`unsupported` として候補化しoccurrenceを付け、`:428-445` でsource hash・occurrencesと `invalid_source`/`source_type_error` をmanifestへ残して取得を早期終了する。C07の旧v2.63証拠にも候補記録のfocused assertionがあるが、v2.64での新実測は未結合。 |
| 不正base64/0 byteがdownloaded扱いになる | 解消 | `:393-425` がbase64文字集合・padding・canonical decode・復号後byte数を検査し、不正/空をthrowする。`:446-456` のdata経路でthrowされた場合はcatchにより `inline_decode_error`、0 byteもdownloadedへ進まない。 |
| lazy観測が無期限/未完了を表さない | 解消 | `:149-168` で観測開始時点に2500ms deadlineを置き、RAF待ちを残り時間で打ち切る。`:242-262` の安定待ちも同じ残り時間を消費し、timeout/未解決を `timed_out`・理由・occurrenceとして返す。 |
| data/blob retryのDOM例外を握りつぶす | 解消 | `:528-537` でsafe error classを保持し、`:555-567` で `dom_recapture_failed` として呼出結果へ返す。例外時に `replacements` は空のままなので既存page/image manifestは書換えられない。 |
| scroll/rAF待機が2.5秒deadlineの外にある | 解消（協調的deadlineとして） | deadlineはDOM selector探索前の`:149-153`で開始。`:189-199` がscroll前に残り時間を確認し、各scroll後の2-frame待ちを`:154-168`の同じdeadlineで打ち切る。後続のDOM/source安定待ちも同じ残り時間を使う。JS同期処理自体は中断不能だが、コード上のscroll/RAF/安定待機に別タイマーや追加の2.5秒枠はない。 |
| malformed percent escapeをdownloaded扱いする | 解消 | `:408-424` が各 `%` の直後に2桁hexがあることを検証し、不正/切れたescapeを `inline_decode_error` にする。 |

### 残存所見

#### [P3] 大文字の `DATA:` schemeをdata URIとして認識後に拒否する

- 場所: `automation/inago/adapters/product_page_inputs.mjs:279-281,393-396`。
- 計画/要件: R03/R04、§5（data:image候補を保存しOCR対象へ渡す）、要件データ辞書v2.64 `:181`。
- 候補分類は `/^data:/i` でschemeの大文字小文字を許容するが、decoderは `value.startsWith('data:')` をcase-sensitiveで検査する。従って `DATA:image/png;base64,...` は `source_kind='data'` になった後 `inline_decode_error` となり、候補によっては画像ファイル/OCRを失う。`src`属性とbrowserが正規化した`currentSrc`の両方が列挙される場合も、URL文字列が異なるため不要な失敗recordを追加し得る。
- 修正案: decoder側もscheme比較をcase-insensitiveにして、header開始位置をURI scheme長に合わせる（またはschemeだけ正規化する）。
- このfollow-upで試験コードは読まず、当該形の実測は未確認。小頻度の入力境界としてP3に留める。

### 回帰結果の扱い

C07の既記録v2.63回帰は `79 passed, 4 failed`（`C07/execution.md:68`、`C07/result.json` の `post_remediation_planned_full_regression`）。失敗は欠落study画像/JSONLのOCR fixture 2件と、今回変更範囲外のcopy-quality selection 2件で、同記録に個別再実行もあり、製品差分の今回6指摘とは原因・責務が異なる。したがってその4件は記録時点の範囲では無関係と判断する。ただし、v2.64の回帰は親から実行中と連絡があり、再発有無は未確認である。C07の新しい18/3件数をこの旧回帰記録や旧2.63結果へ上書きしていない。

## v2.64 follow-up 結果

- 前回4件と追加2件の計6指摘は現行コードで解消を確認した。製品コードの残存所見は上記P3の1件。
- C07の2.64証拠版結合と最終回帰は未確認として後続へ渡す。未確認・P3所見・旧回帰結果を後続工程の停止条件にはしない。

## v2.65 最終follow-up（2026-10-25）

### 対象と再確認

- 対象: 要件正本HTML v2.65、要件データ辞書/追跡表 v2.65、`automation/inago/adapters/product_page_inputs.mjs` の現行実物。hashは生成・比較していない。
- 計画参照: R03/R04/R07、§5、C04/C07。試験コードはレビューせず、試験も実行していない。
- HTML正本のタイトルと版2.65、data URIのscheme markerをcase-insensitiveで扱う要件を確認。辞書は `要件データ辞書.md:3,181`、追跡表は `要件追跡表.md:3`。

### 前回6件と `DATA:` 所見

- invalid/unsupported URLの候補保持は引き続き解消。`product_page_inputs.mjs:278-299,428-445` がsource種別、全occurrence、hash、取得しない理由を記録する。
- base64文字/padding/復号後byte数と0 byte拒否は引き続き解消。同ファイル`:393-425,446-456` で不正/空bytesは `inline_decode_error` となる。
- lazy観測の有限期限とtimeout記録は引き続き解消。同ファイル`:149-168,242-262` が2500msを起点にRAF/安定待ちの同一deadlineを使い、未完了理由とoccurrenceを返す。
- retry時のDOM再取得例外診断は引き続き解消。同ファイル`:528-537,555-567` がsafe error classを返し、既存manifestを維持する。
- scroll/rAF待機のdeadlineは引き続き解消。同ファイル`:149-153,189-199` でDOM探索前に開始し、scrollと2-frame待ちが同じ残り時間を使う。
- malformed percent escapeは引き続き解消。同ファイル`:408-424` がhex 2桁を要求し、不正値を `inline_decode_error` にする。
- 前回[P3]とした大文字 `DATA:` のdecoder拒否は解消。同ファイル`:279-281` の候補分類と`:393-396` のdecoderがどちらも `/^data:/i` を使う。固定長5文字のschemeを除いた`:396` のheader位置も正しいため、`DATA:image/png;base64,...` はscheme比較で拒否されない。要件2.65のcase-insensitive契約とも一致する。

### C07実行証拠と残る範囲

- C07 `execution.md:75-92` にはv2.64までの追加ケースRED/GREEN、capture/browser/URL-safety 18 passed、OCR/copy/resume/cleanup 3 passed、および最終回帰 `81 passed, 4 failed` が記録されている。最終回帰の4失敗は前回と同一の欠落OCR study fixture 2件と、変更外copy-quality selection 2件で、記録上は今回のdata URI修正と別の範囲・原因である。2.65最終回帰の再発有無は未確認。
- `C07/result.json:7` は `specification_version: 2.63` のままで、実行記録のv2.64更新を反映していない。今回の2.65結果もまだ保存記録には見当たらない。親からは「修正前の新規case 1 failed（DATA schemeはdata分類されるがinline_decode_error）、修正後1 passed、capture/browser/URL-safety 19 passed」と連絡されたが、コマンドと版に結び付くC07保存証拠は本確認時点で未確認。C07更新中との引継ぎとして後続へ伝える。
- 上記の件数・記録不足・前回回帰FAILは観測事実であり、レビュー所見として扱う。後続工程の停止条件にはしない。

## v2.65 最終review結果

現行の要件と実装で前回6件および `DATA:` のP3所見が解消したことを確認した。今回の対象差分に新たな製品コード所見はない。C07の2.65証拠保存と最終回帰の状態は未確認として次工程へ渡す。

## 親担当による2.65証拠・回帰記録の追記

最終reviewerが確認した直後に、親担当がC07を更新した。独立reviewerのread-only判定は「前回6件と大文字DATA:のP3所見が解消、残存製品コード所見なし」であり、reviewerはテストを実行していない。

- C07のspecification versionを2.65へ更新し、現行コード・test・specification hashを保存した。
- 修正前の大文字`DATA:`ケースは1 failed、修正後は1 passed。capture/browser/URL safetyは19 passed、OCR/copy/resume/cleanupは3 passed。
- 2.65の計画回帰は82 passed / 4 failed / 0 errors。4件は欠落OCR study fixture 2件と今回対象外copy-quality selection 2件であり、2.64回帰と同じnode。全体試験が失敗した事実は維持している。
- 最新の`C07/evidence/page.json`, `ocr.json`, `job-input.json`のSHA-256はC07/result.jsonとexecution.mdへ記録済み。
