# 楽天商品画像 OCR 技術検証計画

## 0. 文書版・対象・状態

- 文書版: 1.2 (計画案; independent review v1.0/v1.1所見を反映)
- 作成日: 2026-10-22 JST
- 対象: 既存の楽天商品ページ5件から保存済みの `span.sale_desc` 内画像59点をPython OSS OCRで処理し、商品説明に含まれる情報を読めるテキストとして取得できるかを技術検証する。
- 作業root: `C:\project\rakuten-room-inago`。作成・変更・生成する検討資材はすべて `検討/楽天商品要素調査_2026-10-22/` 配下に置く。
- 出力資料の配置案: `検討/楽天商品要素調査_2026-10-22/OCR技術検証/` に候補比較、runner、環境、OCR結果、評価を置き、本計画はその親フォルダーに置く。
- 管理job identity: controller提供の `job_id`、`source_run_id`、`target_date` は今回の入力にない。値を推測しない。`2026-10-22` は既存資料フォルダー名であり、controllerの対象日とは扱わない。
- 状態: 計画のみ。OCR OSSのWeb調査、導入、画像処理、OCR結果評価は未実施。
- AI実行設定案: 実行担当は `gpt-6.1-sol`、reasoning effort `high`。service tierと実行時の実モデル観測値は未確認で、開始条件にしない。
- 必須の本人専用ローカル方針: 本システムは本人1名が本人のローカル環境で使う。堅牢性を品質目標にしない。簡潔に実行し、review・試験・証拠・原因・readinessの状態で後続作業を止めない。個別操作ができない場合はその操作と理由だけを未実施として記録し、可能な工程と最終報告を続ける。この方針を全STEPの担当promptに明記する。

## 1. 目的・完成状態・範囲

### 目的

Pythonから利用できるOSSの中で、利用状況と保守・実行適合性の根拠を比較して代表候補を選び、隔離した検討用Python環境へ導入する。選定したOCRで既存59画像をすべて処理し、画像内の商品説明が実用上読めるテキストに変換される例を5商品ごとに確認する。

### 完成状態

1. 現在の公開指標に基づく候補比較と選定根拠が `候補比較.md` に残っている。
2. 選定したエンジンと版、言語モデル、依存を再現できる記録が検討用環境内にある。
3. 既存59画像それぞれに対応するOCR結果レコードがあり、エラーや空結果も省かれず記録されている。
4. 5商品ごとに、商品説明情報がテキスト化されたかを元画像と照合した結果が `OCR検証結果.md` にある。完全精度は求めない。
5. 入力画像の相対パスとSHA-256を処理前後で照合し、原画像を変更していないことを記録する。
6. 実施できなかった操作、未知事項、実際の失敗を成功扱いせず報告する。

### 範囲内

- Python向けOSS候補の人気・利用状況、保守状況、Python 3.12/Windows/CPU、日本語OCR、ライセンス、導入負荷の比較。
- 選定OSSと必要な実行依存の検討フォルダー内への導入。
- 既存の `index.json` と画像59点を入力にした全件OCR。
- OCR全文とエンジンが返す付随値の保存、元画像と照合した可読性の評価、実行記録。

### 範囲外

- 商品ページの再取得、Playwright再実行、画像の再ダウンロード。
- 楽天商品ページHTML本文の取得、商品名・商品説明HTMLの新規取得。
- 投稿文生成処理、本番prompt、本番依存、`.venv-inago` の変更。
- OCRの精度を一定割合以上にするための調整、画像種別分類、OCR信頼度による画像または認識結果の除外。
- 投稿文へのOCRテキスト投入やAI生成品質の評価。これはOCR技術確認後の別検討。

## 2. 調査からの事実・事象追跡

この計画は正式なプロジェクト要件IDを新設しない。以下の `F-*` はこの計画内の事実参照、`R-*` はこの作業用の要求参照であり、既存の製品要件IDではない。

| Fact | 確認できている事実 | Evidence |
| --- | --- | --- |
| F-01 | 既存収集対象は5商品フォルダー、各画像数は9、11、6、24、9で計59点。 | `検討/楽天商品要素調査_2026-10-22/README.md`; `index.json` |
| F-02 | 収集画像は各商品ページの `span.sale_desc` 内から保存された。 | `画像が商品説明専用かの確認.md`; 各商品の `manifest.json` |
| F-03 | 画像群には説明以外に、操作ボタン、動画サムネイル、ランキング・レビュー、配送告知、販促画像等も含まれ得る。 | `画像が商品説明専用かの確認.md` |
| F-04 | 既存調査ではOCRは実行されていない。 | `画像が商品説明専用かの確認.md` |
| F-05 | 実行環境はWindows、system Python 3.12.13。`.venv-inago` に `pytesseract`, `easyocr`, `paddleocr`, `rapidocr_onnxruntime`, `PIL`, `cv2` がなく、`tesseract` と `paddleocr` CLIもPATHにないという事前確認が共有された。 | 計画依頼に添付された既知の環境確認事実。元のコマンド記録ファイルは今回提供されておらずunknown。実施時に再確認する。 |
| F-06 | すべての対象画像をOCRにかけ、画像分類や信頼度による除外をしない方針が指定されている。 | 本人の今回の依頼・先行会話 |
| F-07 | 完璧な認識精度ではなく、商品説明情報がテキスト化できることの確認が目的。 | 本人の今回の依頼 |
| F-08 | OCRの調査・環境・結果など、この技術検討で作成する資材の保存先は「検討」配下。 | 本人の今回の依頼 |
| F-09 | このシステムは本人1名のローカル利用であり、堅牢性を品質目標にせず、review/test/readiness/証拠/unknown/失敗を後続作業のgateにしない。 | `.agents/skills/common-execution-planning/SKILL.md` の「必ず守る本人専用ローカル方針」。今回の依頼に適用される。 |

### 作業要求

| Requirement | 作業要求 |
| --- | --- |
| R-01 | OSSの代表候補を現在の公開指標と一次資料で比較し、採用を一つ決めて根拠を記録する。 |
| R-02 | Python package、native executable、model/data、pip/model/temp/cacheなどの永続・一時生成物を `検討` 配下に隔離する。隔離不能な候補資材は生成・導入せず、その候補の操作だけ `not_performed` として次点へ進む。 |
| R-03 | 既存59画像を全件OCRし、画像ごとのOCR全文・空結果・エラーを対応づけて記録する。 |
| R-04 | 全59画像のOCR結果を元画像と照合し、商品ごとの説明テキスト確認画像数と確認対象数を記録する。確認数が0件なら「実証されず」と結論する。 |
| R-05 | 画像分類・信頼度の閾値でOCR入力や出力を削らず、商品外情報を含むOCR結果もすべて保全する。 |
| R-06 | 元画像と既存収集成果を変更せず、追加資材とpip/model/TEMP/TMP/cache、native executable、model/data、package assetは `検討` 配下に置く。隔離できない候補資材は生成しない。 |
| R-07 | F-09の本人専用ローカル方針に従い、運用上の失敗・未確認を記録して他工程と報告を継続する。 |

## 3. 現行実装・環境・既知制約

- OCR処理やOCR連携の既存実装は、この作業の入力からは確認されていない。今回の目的では既存アプリの統合点を調査・変更しない。
- 既知の画像一覧は `検討/楽天商品要素調査_2026-10-22/index.json`。各商品ディレクトリの保存画像は合計59点。
- Python 3.12.13/Windowsが共有されている。正確な実行ファイルのパス、空き容量、ネットワーク状態、GPU有無は実行開始時に観測する。GPUは前提にせずCPUで試す。
- pip cache、model weight、OSS固有cache、TEMP/TMP、package/native/model assetを検討フォルダー内へ明示配置する。外部既定pathを使う必要がある候補はそのassetを生成せず、その候補の導入・実行だけ `not_performed` として次候補へ進む。意図しない外部生成が起きた場合は例外的な事故として実pathと種類を記録し、その生成物を今回の所有物・成果物・出力として扱わない。これらの扱いに確認承認gateを追加しない。
- OSSの利用量、stars、リリース状況、Python 3.12互換性、日本語モデル、Windows CPU導入性の現在値は未調査。STEP 01で調べる。
- 環境確認の元ログがないため、未導入の依存名は既知事実として扱いつつ、実行開始時に読み取り確認する。確認だけで作業を延期しない。
- 現在のGit HEADは実施開始時に取得する。計画に未確認のコミット値は記載しない。
- 既存の未追跡 `rakuten-room-inago/非公開作業データ/` は他作業のユーザーデータとして取り扱い、本作業の読書き・ステージ対象にしない。

## 4. 技術案・責務・依存

### 候補比較

少なくとも次の実行方式を比較対象にする。名称は候補であり、現時点で人気順位や適合性を断定しない。

1. Tesseract OCR + Python wrapper `pytesseract`（本体実行ファイルと日本語traineddataの配布・版も別に確認）
2. EasyOCR (`easyocr`)
3. PaddleOCR (`paddleocr` とWindows CPU向けPaddle依存)
4. RapidOCR (`rapidocr_onnxruntime` 等、公式配布構成を確認)

人気は単一数値へ混ぜず、候補ごとに (a) GitHub公式リポジトリのstars、最終更新日、最新release、(b) PyPIパッケージの月間download等取得可能な利用指標、(c) 指標提供元と対象期間を記録する。GitHub API等の公式指標を優先し、ダウンロード数はPyPI公式JSONに項目がなければ統計提供者と期間を明示して補助情報として扱う。Python wrapperとOCR本体は別項目で比較する。数値が取得できない候補はunknownとして他の確認済み指標で判断し、恣意的な合算スコアを作らない。

適合性は、日本語モデル、Windows CPU、Python 3.12.13、現在のライセンス、導入手順・容量・依存、更新状況を根拠付きで比較する。まず人気指標に基づく有力候補を選び、対象環境での適合性を確認して一つを採用する。適合しない場合は比較表で次点となる候補へ移る。候補の観測値や順位を計画時点で捏造しない。

### 処理の責務

- **比較担当**: 候補指標と一次資料を調べ、採用と次候補の根拠を `候補比較.md` へ記録。
- **実装担当**: 検討用venvと選定OSSを用意し、対象manifestを読む一回限りの `ocr_runner.py` を作る。runnerは全画像を処理し、画像ごとにエンジン返却全文、ボックス等の返却情報、言語、版、エラーを保存する。
- **試験担当**: 保存済み全画像だけにrunnerを実行し、カバレッジ、元画像ハッシュ、出力を確認。
- **評価担当**: 各出力と元画像を照合し、商品説明テキスト化の有無を商品ごとに記録。
- **独立reviewer**: 比較根拠、環境隔離、OCR網羅性、保存物、評価の対応をread-onlyで確認。

実作業では各担当が `gpt-6.1-sol` / highを指定する。tier・実際に有効になったモデルの観測値は記録可能な範囲で記し、未観測でも着手を止めない。単純な調査用・検証用scriptだけを作り、本番コード・テスト・設定は変更しない。

## 5. 入出力・状態・保存契約

### 入力（変更禁止）

- `検討/楽天商品要素調査_2026-10-22/index.json`
- 5つの商品フォルダーの保存済み画像59点
- 既存のREADME、商品ごとのmanifest、画像範囲の目視確認レポート

### 出力先

`検討/楽天商品要素調査_2026-10-22/OCR技術検証/`

- `候補比較.md`: 指標、出典URL、確認日時(JST)、対象期間、比較、採用/次点と理由。
- `.venv-ocr/`: 選定OSSを含む隔離環境。production venvへ入れない。
- `requirements-ocr.lock` または選定方式に対応する版一覧: Python、OCR本体/wrapper、モデル、推移可能な依存版を記録。
- `native/`, `models/`, `cache/`, `tmp/`: 選定候補のnative executable、traineddata/model weight、pip/model/cache/TEMP/TMP assetを置く。Python packageも`.venv-ocr/`内に限る。公式docsで保存先を確認し、package/native/model/data、PIP/model/TEMP/TMP/cacheを含むすべての永続・一時生成物を検討配下に置く。隔離できない候補資材は取得・生成・導入せず、その候補操作だけ`not_performed`として次候補へ進む。意図しない外部生成は例外事故として実path/種類を記録するが、今回の所有物・成果物・出力には含めない。
- `asset-manifest.json`: package、native executable、DLL/依存、traineddata/modelごとの版、source URL、license/source、隔離先を記録。
- Tesseractが選ばれた場合は `native/tesseract.exe` と必要DLL一式、`native/tessdata/jpn.traineddata` を配置する。`pytesseract`から実行ファイルpathとtessdata directoryを明示指定する。portableな一式を検討配下へ配置できない場合、その候補の導入だけ `not_performed` にして次候補へ進む。
- `ocr_runner.py`: 今回の技術検証だけで使うrunner。
- `input-sha256.txt`: 59入力画像の相対パスとSHA-256。原画像はコピー・変換・上書きしない。
- `results/ocr-results.jsonl`: 59画像ごとに一つの結果レコード。少なくとも相対パス、SHA-256、処理順、OCR engine/package/model版、言語、処理設定、エンジンが返した全文、返却された認識領域/信頼度等、空結果または例外を含める。認識文字をマスクしない。出力レコードのない画像を作らない。
- `OCR検証結果.md`: 商品ごとの確認結果、実行コマンド、件数、失敗、証拠参照、制約。
- `review.md` または `review.json`: 独立レビュー結果。

認識領域やconfidenceはエンジンが返す場合にそのまま保存する。OCR後にconfidence閾値で文字列を落とさない。エンジン内部に閾値があり変更可能な場合は出典と設定を記録し、全返却結果を残す。閾値変更ができない場合は実際のAPI仕様を記録し、返ってきた全文を削らない。

## 6. 外部取得・AI・本人表示の契約

- 外部Web閲覧は候補選定・OSS公式導入手順・パッケージ情報の確認のみに行う。取得するpackage/model/native/traineddataは検討フォルダーへ隔離できる候補だけを対象にし、商品ページへの追加アクセスや画像取得はしない。
- 人気の根拠は公式GitHub/PyPI等の出典と確認時刻を記す。download統計が第三者提供の場合は一次情報と混同しない。
- OCR実行では生成AI/投稿文生成APIを呼び出さない。人の目で元画像とOCR出力を比べる。
- 画像内の販促、レビュー、操作ボタン、配送案内なども含むため、OCR入力の事前分類はしない。OCR出力を省かず保管し、評価報告上で商品説明情報とそれ以外を区別してよい。
- 結果は本人向けの検討資料。商品情報として確実と確認できないOCR語句を商品事実と断定しない。

## 7. 時刻・保持・保全・運用値

- 調査・実行・レビューの時刻はJSTで記録する。
- OSS利用指標は取得した時刻と統計期間を併記する。ダウンロードの対象期間が比較候補間で異なるときは数値を単純比較せず差を明記する。
- 59枚の入力path/hashを実行前に保存し、OCR後に再計算して一致を確認する。画像名・商品フォルダー構成が変わった場合は、変更せず不足を記録して残りを続ける。
- OCR出力と比較資料、候補のpackage/model/native/traineddata、pip/model/temp/cacheは検討フォルダーに保持する。隔離できない候補資材は生成しない。本番運用データ、他人の作業資材、認証情報、個人情報を含めない。
- 選定OSSの公式資料で確認した設定を使い、pip/model/temp/cache pathおよび永続native/model/package assetをすべて検討フォルダー内に指定する。外部既定pathを使わないと動かない候補assetは生成せず、その候補の操作だけ `not_performed` として次点へ進む。意図しない外部生成は例外的な事故として実path/種類を記録するが、その生成物は今回の所有物・成果物・出力には含めない。新たな確認gateは置かない。
- 仮想環境、モデルキャッシュ、生成結果のサイズや追跡対象は実行時に確認する。容量不足はその操作の未実施理由を残し、独立して可能な評価や報告を続ける。
- 全候補の永続package/native/model/dataとPIP/model/TEMP/TMP/cacheを検討配下へ指定できるものだけ生成する。隔離不能な資材の生成を避け、該当候補操作を`not_performed`にして次点へ進む。偶発的な外部生成は実path/種類を事故記録し、今回の出力には含めない。追加の確認承認gateは設けない。

## 8. 試験、再開、記録、禁止の共通規則

- 今回用の既存pytest nodeは提示されていない。架空のテストnodeを割り当てない。ここでいうケースはOSS候補確認、isolated install、実データ全件処理、結果の対応・目視確認を行う一回限りの技術検証であり、回帰試験ではない。
- 各ケースに完全な実行コマンドを記録する。runner実装後の想定実行例は `& .\.venv-ocr\Scripts\python.exe .\ocr_runner.py --index ..\index.json --output .\results\ocr-results.jsonl`。実装時に選定engineのAPIに応じて確定した正確なコマンドを記録し、計画上の例と実コマンドを区別する。
- 出力の全59件対応、空結果/例外の記録、入力hash不変、5商品ごとの説明テキスト確認を判定する。完璧な認識率や最低文字数を要求しない。
- 失敗の操作名、例外class、安全に共有可能な原因概要、時刻、使用版、入力ファイル、今回の検討配下へ生成された資材を記録する。意図しない外部生成は事故として実pathと種類だけ記録し、所有成果物に含めない。例外文にローカル個人パスやtoken等がある場合は報告前に安全化する。
- 中断時は成果物と1行ごとの結果を読み、未処理入力だけを識別して同じ環境と設定で再開する。再開で重複ができる場合は元結果を保持し、再実行結果を別ファイルへ保存して対応を記録する。頑健な自動再開機構の実装は求めない。
- OCR候補選定、review、試験、証拠の状態は後続作業のgateにしない。観測事実を保ち、個別未実施だけを明記して完了報告へ続ける。
- Git対象は本計画の実施後に生成された今回所有の資料・小さなrunner・依存版記録・実行結果報告に限定する。`.venv-ocr` やモデルbinary/cacheは追跡しない。完了前に `git status --short` と対象差分を確認し、既存 `非公開作業データ/` をstageしない。リポジトリ指示どおりtask所有ファイルのみをcommit/pushする。Git失敗時は資料を保持し停止理由を記録する。

## 9. 実行STEP一覧と各STEPの完全prompt

### STEP 01 — OCR OSSの比較と採用

1. **番号・名称・状態:** STEP 01、OCR OSS比較と採用、実施済み。結果は `OCR技術検証/候補比較.md`。
2. **目的と成立状態:** 現時点の公開指標・公式仕様で候補を比較し、1候補と代替候補を根拠付きで決め `OCR技術検証/候補比較.md` に残す。
3. **要件・受入・原典:** R-01、R-07。F-05、F-07、F-09。原典は候補の公式repository、PyPI、導入/日本語対応/ライセンス文書、download統計提供元、および共通計画スキルの本人専用方針。
4. **先行・開始条件:** 計画を確認できれば開始。review承認、tier観測、事前status、全sourceの応答を待たない。
5. **入力・版・DB状態:** 本計画、F-01〜F-08、Python 3.12.13という既知値。開始時に日付、Git HEAD、利用可能なPython実体を記録。DBは使わない。
6. **作業場所・所有範囲:** `C:\project\rakuten-room-inago\検討\楽天商品要素調査_2026-10-22\OCR技術検証\候補比較.md` のみを作成。
7. **実施手順:** Tesseract+pytesseract、EasyOCR、PaddleOCR、RapidOCRのGitHub/PyPI/公式docsを照合し、stars、更新/release、月間download等の取得可能な指標、期間、ライセンス、Windows CPU/Python3.12、日本語、モデル配布、導入負荷を記録。指標の定義を混ぜず、実測根拠から採用候補と次点を決める。
8. **成果物:** `候補比較.md`、source URLとJST確認日時、採用根拠、未知点。
9. **変更制約:** 計画・既存image・product manifest・アプリコード・依存ファイルは変更しない。インストールもOCRも行わない。
10. **レビュー・検証:** 出典が主張を直接支えるか、時点と指標の比較可能性、license/compatibilityが候補本体とwrapperで区別されたかをSTEP 05 reviewerが確認。
11. **合格条件・証拠:** 4候補の比較表、出典、測定時点、採用理由と代替が残る。未取得指標はunknownと明記すれば作業結果として成立。
12. **失敗・修正・再開:** Web sourceが取得できない場合その指標だけunknownとし、他の公式情報へ進む。候補すべての特定値を得られなくても比較と制約を記録する。
13. **引き渡し:** 選定候補、対応する導入手順、Python 3.12/Windows CPUの懸念、次点をSTEP 02へ渡す。
14. **AIエージェントへ渡すprompt全文:**

> 作業rootは `C:\project\rakuten-room-inago`、計画は `検討/楽天商品要素調査_2026-10-22/OCR技術検証計画.md`。STEP 01だけを実施し、候補資料の所有範囲は `検討/楽天商品要素調査_2026-10-22/OCR技術検証/候補比較.md` です。入力は本計画のF-01〜F-09、既存 `index.json` と画像調査レポート、Python 3.12.13という共有済み観測です。現在時点のTesseract+pytesseract、EasyOCR、PaddleOCR、RapidOCRを比較してください。公式GitHub repository/API、PyPIと公式docsを優先し、stars、更新/release、利用/download指標を別々に記録し、第三者統計は提供元と期間を明記してください。日本語、Windows CPU、Python 3.12、依存、ライセンス、model配布も調べ、候補1件と次点を根拠付きで提案します。未知の値はunknownと書き、調査を止めません。今回は環境導入、画像取得、OCR、製品コード、依存ファイルを変更しません。期待証拠は候補比較資料、URL、確認時刻(JST)、指標期間、判断理由です。設定は `gpt-6.1-sol` / highを指定し、service tierや実際の観測値が分からなくても開始します。作業後は所有差分だけを確認し、別作業の未追跡資材を触らず、repositoryのGit規則に従ってください。レビュー指摘やsource取得失敗は事実として残しSTEP 02を止めず、次担当へ候補と未確認事項を引き継いでください。本人1名のローカルシステムでは堅牢性を品質目標にしません。review、証拠、未確定、statusで後続を止めず、個別操作だけ実行不能と理由を残して続けてください。

### STEP 02 — 検討用環境・runnerの作成

1. **番号・名称・状態:** STEP 02、隔離環境と一回限りrunner、実施済み。PaddleOCR一式、runner、lock、asset記録を `OCR技術検証/` に配置。
2. **目的と成立状態:** 比較で選定したOCRと、その実行に必要なpackage/native/model/dataを検討用環境へ隔離し、全59画像を処理できるrunnerを用意する。永続資材を隔離できない候補はその資材を生成せず、候補操作を `not_performed` にして次候補へ進む。
3. **要件・受入・原典:** R-02、R-03、R-05、R-06、R-07。F-01、F-03、F-05、F-06、F-08、F-09。原典はSTEP 01比較資料とOSS公式install/API docs。
4. **先行・開始条件:** STEP 01の利用可能な比較結果を入力に開始。review approvedや比較値の完全取得は条件にしない。候補が不適合なら比較にある次点を選び、理由を記録。
5. **入力・版・DB状態:** `候補比較.md`、5商品/59画像のindex、Windows/Python 3.12.13。開始時にHEAD、Python実行パス、OS、空き容量、入力path/hashを記録。DBなし。
6. **作業場所・所有範囲:** `検討/楽天商品要素調査_2026-10-22/OCR技術検証/` 内の `.venv-ocr/`, `native/`, `models/`, `cache/`, `tmp/`, `asset-manifest.json`, `ocr_runner.py`, `requirements-ocr.lock`, `input-sha256.txt`。既存入力は読み取り専用。
7. **実施手順:** 実行Pythonを確認後、同ディレクトリにvenvとpip/model/temp/cache用folderを作る。`PIP_CACHE_DIR`, `TEMP`, `TMP`, `XDG_CACHE_HOME` と選定engine公式docsで確認したmodel cache設定をすべて検討配下へ向け、外部既定cacheを使わない。python packageは`.venv-ocr`、永続model/native/DLL/traineddataは`models/`または`native/`へ置き、version/source/license/保存pathを`asset-manifest.json`へ記録する。**Tesseract選定時**はportable配布物の実行ファイル・必要DLLと`jpn.traineddata`を検討配下へ取得できるか、各source/licenseを確認する。配置できる場合、`pytesseract.pytesseract.tesseract_cmd`へ`native/tesseract.exe`の絶対pathを渡し、OCR callのconfigに`--tessdata-dir "<native/tessdata>" -l jpn`を指定する。versionとlanguage dataをそれぞれ検証・記録する。隔離可能な一式を用意できない場合、そのTesseract候補の導入だけ`not_performed`にして次候補へ進み、global installや外部実行pathへ切替えない。**その他候補も**必要なpackage/model/native assetが検討配下へ隔離できなければ生成せず、その候補だけ`not_performed`として次候補へ進む。もし事故として意図しない外部生成が起きたら実path/種類を記録し、生成物を今回の所有物・成果物として扱わない。そこで追加承認gateを置かない。利用可能な隔離候補では1画像smoke後にrunnerを作成する。runnerは59点すべてにOCRし全返却全文・metadata・空結果・例外を記録し、画像分類・confidence除外をしない。
8. **成果物:** isolated venv、選定候補の隔離package/native/model/data、`asset-manifest.json`、runner、version/license/source記録、検討内cache/temp設定、input SHA256、1画像smoke記録。意図しない外部生成物は成果物一覧へ含めない。
9. **変更制約:** `.venv-inago`, `requirements*.txt`, 製品コード、既存image/manifest、外部既定cache/pathを変更・生成先にしない。全ての予定生成物を検討配下へ置く。隔離不可候補のassetは生成しない。画像へ書き込みをしない。
10. **レビュー・検証:** UTF-8/JSONL schema、runnerに除外条件がないこと、入力path containmentと出力path、pip/model/temp/cache、native executable、DLL、traineddata/modelの保存先、版/source/license、実行時参照先をSTEP 05が確認。モデル/依存のinstall失敗も隠さない。
11. **合格条件・証拠:** 利用する候補のpackage・native・model/data・cache/tempが検討配下に隔離され、Python/API smokeが通る。Tesseractなら実行ファイルとjpn.traineddataを同フォルダーから指定でき、`--version`/`--list-langs`、版/source/licenseが記録される。隔離できない候補は生成せずnot_performedとし、次点を扱う。
12. **失敗・修正・再開:** install/import/モデル取得の操作ごとに版と結果を記録。隔離不能な永続assetは生成せずその候補操作だけnot_performedとして次点へ進む。意図しない外部生成は例外事故としてpath/種類だけ記録し、task所有物や成果物とは扱わない。新たな承認gateは設けない。環境をやり直す場合は検討用venv/cacheだけを対象にする。全候補が隔離できなくても結果を記録しSTEP 03の未実施理由・可能な別確認を渡す。
13. **引き渡し:** engine、version/model、runner command、残課題、SHA manifestをSTEP 03へ引き渡す。
14. **AIエージェントへ渡すprompt全文:**

> 作業rootは `C:\project\rakuten-room-inago`、計画は `検討/楽天商品要素調査_2026-10-22/OCR技術検証計画.md`。STEP 02を実施し、所有範囲は `検討/楽天商品要素調査_2026-10-22/OCR技術検証/` のみにします。入力はSTEP 01比較、既存index.jsonと5商品の59画像、F-05（Windows/Python 3.12.13、pytesseract等未導入、Tesseract/PaddleOCR CLIがPATHにないという共有事実）です。実行時にHEAD、Python/OS版、入力SHA-256を記録し、system Python/.venv-inago、本番依存、画像を変更しません。`.venv-ocr/`へPython packages、`native/`と`models/`へnative/model/data、`cache/`と`tmp/`へpip/model/TEMP/TMP/cacheを配置し、`asset-manifest.json`にpackage/native/model/dataの版・source URL・license・pathを記録します。公式資料で有効なcache変数だけを検討配下へ設定し、外部既定cache/pathへ書きません。**Tesseractが選定候補なら**portableな`tesseract.exe`、必要DLL一式、`jpn.traineddata`をそれぞれ`native/`と`native/tessdata/`に置けることを公式source/licenseで確認します。配置できる場合は `pytesseract.pytesseract.tesseract_cmd` に隔離した実行fileのpathを指定し、OCR callの`config`に `--tessdata-dir "<native/tessdata>" -l jpn` を渡します。`--version`と`--list-langs --tessdata-dir ...`結果も記録します。portable一式またはcache/temp/model/packageを検討内へ隔離できない場合、その候補のassetを生成・installせず、候補操作だけ`not_performed`にして次点へ進みます。global install、system PATH変更、外部既定cache利用は禁止です。意図しない外部生成は例外事故として実path/種類を記録しますが、task所有物・成果物・出力には含めません。追加承認gateを作らず、他候補と後続を続けます。隔離できた候補で1画像smokeを確認し、一回限りrunnerを作成します。runnerは59画像を分類やconfidence filterなしで全件OCRし、全文・metadata・empty/errorも記録します。正確なinstall/実行コマンド、Python/package/native/model版、source/license、path、結果と証拠、not_performed候補と次候補を保存し、次STEPへ渡してください。設定は`gpt-6.1-sol`/high。本人1名のローカルシステムでは堅牢性を品質目標にせず、review/test/readiness/unknown/failureをgateにしません。Git対象は今回所有の文書と小runnerだけで、venv/native/models/cache/tmpや既存の非公開作業データをstageしないでください。

### STEP 03 — 59画像の全件OCR

1. **番号・名称・状態:** STEP 03、既存59画像のOCR、実施済み。59 success / 0 empty / 0 error、return code 0。
2. **目的と成立状態:** 5フォルダーの対象画像を選別なしで全件処理し、全画像に一つずつ結果またはエラーを記録する。
3. **要件・受入・原典:** R-03、R-05、R-06、R-07。F-01〜F-04、F-06、F-09。原典はindex、各manifest、input SHA256。
4. **先行・開始条件:** STEP 02で利用可能になったengine/runnerで開始。source completenessを事前gateにしない。不足画像は個別記録し、残りを続ける。
5. **入力・版・DB状態:** `index.json`, 元画像59点、runner、venv/engine/model version、実行設定。開始/終了時の画像sha、Git HEAD、時計、コマンドを記録。DBなし。
6. **作業場所・所有範囲:** `OCR技術検証/results/ocr-results.jsonl` と今回の安全な実行ログ。入力画像の書込みは禁止。
7. **実施手順:** indexと画像pathを照合し、全59点を画像順にrunnerへ渡す。全返却テキストと返却情報を保存。confidenceや画像種別で除外しない。出力件数、image path重複/欠落、空/失敗レコードを計数し、入力SHAを再確認する。
8. **成果物:** 全件JSONL、正確なコマンドとreturn code、実行時間、59入力と結果の対応表、実行前後SHA照合。
9. **変更制約:** product画像・HTML・既存metadata・本番環境を一切変更しない。全出力は検討配下。
10. **レビュー・検証:** CASE-03/04の確認を行い、同じ入力集合への対応、空/例外を含むレコード、hash一致を確認。OCR文言は原画像と照合する前のrawとして保持。
11. **合格条件・証拠:** 59点すべてに結果または個別エラーが対応し、認識テキストが返ったものは全文保存される。少なくとも1画像が文字なしでもエラーでなく空結果として記録される。精度閾値は設けない。
12. **失敗・修正・再開:** 一部画像のdecode/OCR失敗はそのレコードに残し他を続ける。プロセス停止時は既存JSONLを保持し未処理分を特定、追記または別名出力で再開する。環境不調なら操作をnot_performedとして記録しSTEP 04に引き継ぐ。
13. **引き渡し:** 全raw結果、エラー、対応表をSTEP 04へ。
14. **AIエージェントへ渡すprompt全文:**

> 作業rootは `C:\project\rakuten-room-inago`、計画は `検討/楽天商品要素調査_2026-10-22/OCR技術検証計画.md`。STEP 03を担当し、入力は `index.json` に列挙された既存5商品の画像59点、STEP 02のrunnerと隔離venvです。出力 `検討/楽天商品要素調査_2026-10-22/OCR技術検証/results/ocr-results.jsonl` を所有します。商品ページを開いたり画像を取り直さず、全画像にOCRをかけてください。画像分類、confidenceによる画像またはテキストの除外はしません。エンジンの全返却テキスト/metadataを保持し、空・decode失敗・OCR失敗にも画像pathに紐づく結果レコードを作成します。実行前後の画像SHA-256、Python/OCR/model版、言語、設定、JST時刻、完全コマンド、return code、処理件数を記録し、原画像を変更していないことを照合します。検討配下以外の書込み、production `.venv-inago`、本番依存、製品ソースの変更は禁止です。59結果に欠損があれば画像単位で記録し残りを処理し、停止時は完成分を保持して未完了だけ再開します。精度を完全にする必要はありません。設定 `gpt-6.1-sol` / high、service tier unknown可。試験失敗、結果の一部欠落、レビュー状態で後続評価を止めず、実際の差をSTEP 04へ渡します。本人専用ローカルシステムでは堅牢性を品質目標にしないため、自作gateを足さず個別操作の失敗だけを記録してください。task所有範囲のみGit規則に従い、既存の非公開作業データをstageしません。

### STEP 04 — 説明テキストの確認と結果報告

1. **番号・名称・状態:** STEP 04、OCR結果の内容確認、実施済み。59画像を照合し、5商品中5商品で説明情報を確認。
2. **目的と成立状態:** 5商品の各画像でOCR結果を元画像と照合し、商品別の説明情報確認数を記録する。手続き完了（5商品すべてに成功数を付けること）と技術実証の結論を分け、確認商品が0/5なら「実証されず」とする。
3. **要件・受入・原典:** R-04〜R-07。F-01〜F-07、F-09。原典はraw OCR、画像59点、商品別manifest、確認レポート、共通計画スキルの本人専用方針。
4. **先行・開始条件:** STEP 03の利用可能な出力で開始。不完全な全件処理や一部エラーがあっても確認可能な画像を評価する。
5. **入力・版・DB状態:** OCR JSONL、元画像、engine/model/version、runner、実行結果記録。DBなし。
6. **作業場所・所有範囲:** `検討/楽天商品要素調査_2026-10-22/OCR技術検証/OCR検証結果.md`。
7. **実施手順:** 全59画像について、利用可能な各OCR recordと対応元画像を照合する。商品ごとに、OCR処理成功/空/errorの件数、説明情報が画像と照合できた成功画像数/確認対象画像数、該当画像pathとrecord IDを記録する。OCR全文はraw JSONLへ完全保存し、結果報告では判断根拠と短い確認例に留める。数値精度scoreや合格率を作らない。
8. **成果物:** 5商品すべての確認欄・説明情報確認画像数/対象画像数・該当OCR recordと元画像参照、確認できた商品数/5、全体結論（0/5なら「実証されず」）、読取例と限界、次の投稿文入力設計に必要な所見。
9. **変更制約:** raw OCR結果・原画像を改変しない。OCR textを商品ページ由来の正解値として扱わない。AI投稿文生成・本番統合なし。
10. **レビュー・検証:** CASE-04/05。各商品について全利用可能なOCR recordと元画像の対応、画像数と成功数の計数、手続き完了状態と技術実証の結論が分かれているかをSTEP 05 reviewerが確認。
11. **合格条件・証拠:** 5商品すべてに処理数・説明情報確認数と該当record/画像参照を記録した状態を手続き完了とする。技術結果は別に報告し、確認商品0/5なら「実証されず」。実証件数が0でも他商品、報告、後続を止めない。
12. **失敗・修正・再開:** 原画像が読めない、OCR結果が欠ける等の場合は対応商品だけ評価不能/未確認とし理由を記す。別商品への確認は継続。明らかなpath紐付け誤りは修正し影響箇所を再照合。
13. **引き渡し:** `OCR検証結果.md`、未確認事項、review対象のファイル一覧をSTEP 05へ。
14. **AIエージェントへ渡すprompt全文:**

> 作業rootは `C:\project\rakuten-room-inago`、計画は `検討/楽天商品要素調査_2026-10-22/OCR技術検証計画.md`。STEP 04では既存画像59点とSTEP 03のOCR JSONLを参照し、`検討/楽天商品要素調査_2026-10-22/OCR技術検証/OCR検証結果.md` を作成してください。利用可能な全59件についてOCR recordと対応元画像を目視照合し、画像ごとに商品説明情報が認識結果と照合できたか記録します。各商品でOCR処理成功/空/error件数、説明情報を確認できた画像数/対象画像数、確認できた商品数/5、該当するOCR record IDと元画像pathを集計します。手続き完了（5商品分の件数と根拠を記録）と技術実証の結論を分け、1件も確認できなければ「実証されず」と明記します。件数が0でも他商品、報告、後続を止めません。商品説明以外のUI、配送、販促、レビュー等の混在も記録してください。OCR全文はraw JSONLに保持し、この報告で削除・書き換えしません。完全精度、最低score、最低認識率を要求せず、投稿文生成・本番統合をしません。設定 `gpt-6.1-sol` / high。確認不能やreview待ちで作業を止めず、unknownを記載します。本人1名のローカルシステム方針に従い余分な承認gateを作らず、所有範囲内で報告を完了して次の独立reviewへ渡してください。

### STEP 05 — 独立reviewと差分の確定

1. **番号・名称・状態:** STEP 05、独立reviewと最終実行記録、実施済み。独立reviewを複数回実施し、コード所見と負例の実行記録所見を修正。最終read-only再確認で残る所見なし。詳細は `OCR技術検証/review.md`。
2. **目的と成立状態:** 候補根拠、隔離、runner、結果網羅性、評価根拠を独立に点検し、所見を残す。必要な修正と確認を実施記録に反映する。
3. **要件・受入・原典:** R-01〜R-07。F-01〜F-09。本計画、候補比較、runner/lock、raw OCR、評価、Git差分。
4. **先行・開始条件:** STEP 01〜04の利用可能な成果で開始。各工程が完了またはpassであること、full 59件、review合格は開始条件にしない。
5. **入力・版・DB状態:** 作業成果と実行時Git HEAD/差分。raw OCRと元画像へのread-only access。DBなし。
6. **作業場所・所有範囲:** read-only review; 所見は `OCR技術検証/review.md`。修正は発見事項の所有者に返す。最終まとめは `OCR検証結果.md` の追記分に限る。
7. **実施手順:** 独立reviewerが比較指標/source、ライセンス/compatibility、出力path containment、全59件の出力対応、除外処理の有無、元画像hash、5商品ごとの目視根拠、失敗/unknownを確認。結果は `approved/changes_requested/rejected/unavailable` 等の実観測で記し、埋め合わせstatusを作らない。必要な所見は所有者が局所修正し影響ケースを再実行する。
8. **成果物:** review記録、必要修正と影響した検証の追記、所有ファイル一覧、git status/diff checkとtaskのGit操作結果。
9. **変更制約:** reviewerはread-only。今回以外のユーザー変更、未追跡 `非公開作業データ/`、本番コード、`.venv-inago` に触れない。venv/model cacheをcommitしない。
10. **レビュー・検証:** review自体を別の承認gateとして設けず、対象の差分と実測証拠を独立確認。テストコードの独立レビューは不要。この計画では自動回帰test nodeは存在未確認。
11. **合格条件・証拠:** reviewer所見と差分/実行記録が保存され、未解決findingも最終報告に現れる。実行した場合のcommit hash/branch/push先を記録。commit/push失敗はその事実を報告。
12. **失敗・修正・再開:** reviewerが起動できない場合はその操作のみnot_performed。指摘がある場合はownershipに基づき直して該当確認を再実行する。認証/接続/追跡先不可なら変更を保持して理由を残す。
13. **引き渡し:** `OCR検証結果.md` と全成果物の所在、対象Git処理状態、未確認点を本人へ報告。
14. **AIエージェントへ渡すprompt全文:**

> 作業rootは `C:\project\rakuten-room-inago`、計画は `検討/楽天商品要素調査_2026-10-22/OCR技術検証計画.md`。独立reviewerとしてSTEP 01〜04の実成果をread-only確認し、所有fileは `検討/楽天商品要素調査_2026-10-22/OCR技術検証/review.md` のみです。候補比較のsourceと時点、人気指標の意味、ライセンスとWindows/Python3.12/CPU適合、OCR依存隔離、runnerの全件処理/出力path、入力59点への結果対応、全文・空・error記録、confidence/classificationによる除外がないこと、画像sha不変、5商品の説明情報評価根拠を照合してください。review状態は実際に観測した値として報告し、successへ丸めません。reviewer自身は変更を加えません。findingsは対象ファイルのownerへ返して影響する確認の更新を依頼し、可能な最終要約を作り続けます。レビューが利用できない場合はnot_performedの理由を記録します。本人専用ローカルシステムの簡潔性を維持し、approved/test/readinessを後続gateにしません。設定 `gpt-6.1-sol` / highを指定し、tier未確認でも開始してください。既存非公開作業データや別作業差分を開いてstageせず、出力・所見・正確なコマンド・証拠・未解決点だけを記録してください。最後に作業者がtask所有ファイルだけを `git status --short` と差分で確認しrepository規則どおりcommit/pushした結果を報告できるよう、対象範囲を列挙してください。

## 10. 要件/受入/STEP/case対応と各ケース12項目

### 要件追跡

| Requirement | 根拠Fact / Evidence | STEP | Case |
| --- | --- | --- | --- |
| R-01 候補を比較しOSSを選定 | F-05/F-07; 本依頼。既知Python版の根拠ログはunknown | 01, 05 | CASE-01 |
| R-02 isolated install | F-05; 計画入力で共有の事前環境確認（元log path unknown） | 02, 05 | CASE-02 |
| R-03 59件OCR | F-01/F-02/F-04; `index.json`, 商品別manifest, OCR未実施レポート | 02, 03, 05 | CASE-03, CASE-04 |
| R-04 商品別の説明情報確認数と結論 | F-01/F-03/F-04/F-07; `画像が商品説明専用かの確認.md`、既存画像 | 04, 05 | CASE-05 |
| R-05 全画像/全結果、除外なし | F-03/F-06; 目視確認reportと本人依頼 | 02, 03, 04, 05 | CASE-03, CASE-04, CASE-05 |
| R-06 `検討`内、元資料不変 | F-01/F-08; indexと本人指示 | 02, 03, 04, 05 | CASE-02, CASE-03, CASE-04 |
| R-07 本人専用ローカル/後続gateなし | F-09; `.agents/skills/common-execution-planning/SKILL.md`。ユーザーへの専用の文書出典はなく、この共通skillに記録された本人方針を根拠とする | 01〜05 | CASE-01〜05 |

### Test / verification cases

既存のpytest nodeは今回の入力から確認できないため、`pytest` nodeを発明しない。各CASEは一回限りの作業検証であり、caseの「完全node」は `N/A: 今回用の既存自動test nodeは提示されていない` とする。実施者は以下の正確なコマンドまたは実際に確定したrunner commandと結果を記録する。計画でこれから作るrunnerは計画上の成果物であり、現存nodeとして表現しない。

#### CASE-01 — 公開資料による候補比較と選定

1. **対応する要件・STEP:** R-01, R-07 / STEP 01, 05。
2. **目的:** OSSの代表性と対象環境適合を区別して比較できることを確認する。
3. **種別・方法:** Web/公式資料調査。完全node: N/A（既存test node未提示）。
4. **事前条件・初期データ:** F-01〜F-09と候補リスト。比較資料なし。
5. **入力値・操作:** Tesseract+pytesseract, EasyOCR, PaddleOCR, RapidOCRのrepo/PyPI/docsを参照。指標・期間・確認時刻を記録する。
6. **実行方法・設定:** 実施時Web閲覧。実行者は完全なsource URL、照会日時JST、利用可能ならGitHub API/PyPI stats取得方法を資料へ記録。現計画ターンではWebアクセスしていない。
7. **期待する出力:** 指標根拠と比較表、採用1候補/次点、Python 3.12/Windows CPU/日本語の互換性評価。
8. **期待DB・件数・予約・版・ログ:** DBなし。4候補の比較記録。人気数は取得日時点の観測値であり固定期待値は設けない。
9. **禁止する副作用:** package install、画像取得、ソース変更、未確認の数値・順位の捏造。
10. **合否判定:** sourceの意味と時点が明らかで、未知指標はunknown、採用理由と次点が記録されれば成立。比較資料の一部が得られなくても後続を止めない。
11. **保存証拠:** `候補比較.md` と直接参照URL、確認時刻、取得できた指標。
12. **後片付け・再実行:** 外部サイトを閉じ、資料は保持。必要な確認のみ再取得し、既存値を上書きせず観測日を記す。

#### CASE-02 — 検討用環境の隔離導入とsmoke

1. **対応する要件・STEP:** R-02, R-06, R-07 / STEP 02, 05。F-05, F-08, F-09。
2. **目的:** 選定OSSが検討用Python環境から起動し、production環境が変更されないことを確認する。
3. **種別・方法:** 環境構築、import/API一画像smoke。完全node: N/A（既存test node未提示）。
4. **事前条件・初期データ:** CASE-01比較表、Windows、Python 3.12.13の共有事実、空のOCR検討dir。
5. **入力値・操作:** 選定package/model/native/data、検討用venv、一画像は既存対象から読み取り。pip/model/temp/cacheの設定値と保存先を検証する。F-05にある `tesseract` CLIがPATHにない事実を根拠に、system install済みを前提にしない。
6. **実行方法・設定:** 例: `python --version`; `python -m venv .venv-ocr`; 検討用 `cache/` と `tmp/` を作り `PIP_CACHE_DIR`, `TEMP`, `TMP`, `XDG_CACHE_HOME` および選定engine公式docsで指定されるmodel cache環境変数を設定; `& .\.venv-ocr\Scripts\python.exe -m pip install <選定配布名>`。Tesseractの場合はportable一式を`native/`に置き、`& .\native\tesseract.exe --version`; `& .\native\tesseract.exe --list-langs --tessdata-dir .\native\tessdata` を実行し、Pythonでは `pytesseract.pytesseract.tesseract_cmd` に実行ファイルpath、各OCR callに `--tessdata-dir "<native/tessdata path>" -l jpn` を指定する。設定・配置を実施する前に公式配布/sourceとlicenseを確認し、版・source URL・license・実行file/data pathを`asset-manifest.json`へ記録する。実際に採用した配布名、確認したcache変数、実コマンドとAPI smokeを記録する。
7. **期待する出力:** package、native executable/DLL、model/traineddata、cache/tempがすべて検討配下に置かれ、isolated venvと固定pathから選定engineを起動し最低1画像を読む。Tesseractでは`pytesseract`が検討配下のtesseract.exeとjpn.traineddataを使う。
8. **期待DB・件数・予約・版・ログ:** DBなし。Python/package/依存/model/native版、各assetのsource/license、configured path、実path、pip記録、1画像の結果または候補別`not_performed`を保存。
9. **禁止する副作用:** `.venv-inago`, system/site packages, requirements filesの変更。入力画像の書換。検討配下以外へのpackage/native/model/traineddata/cache/temp生成、global Tesseract install、systemのPATH変更。
10. **合否判定:** 使用候補のpersistent assetとtemp/cacheが検討内にあり、固定path指定で起動すればpass。隔離できない候補assetは生成せず、その候補操作だけ`not_performed`とし次点へ進む。偶発的な外部生成は実path/種類を記録し、所有成果物には含めない。確認gateを設けず、別候補と後続可能工程を止めない。
11. **保存証拠:** `requirements-ocr.lock`, `asset-manifest.json`, version/source/license記録、実行file/data/cache/tempのpath、install command、`--version`/language listまたは他engine smoke、input hashes。
12. **後片付け・再実行:** 採用候補環境と隔離model/cacheを検討内に保持する。not_performed候補は外部assetを作らず次候補を試す。再作成時は検討dir内だけ変更し元画像を保持。

#### CASE-03 — 入力範囲・画像不変の確認

1. **対応する要件・STEP:** R-03, R-05, R-06, R-07 / STEP 02, 03, 05。
2. **目的:** OCR対象が既存59画像であり、他の取得・変更を伴わないことを確認する。
3. **種別・方法:** manifest突合、sha256確認。完全node: N/A（既存test node未提示）。
4. **事前条件・初期データ:** `index.json`, 5商品ディレクトリ, 画像59点。
5. **入力値・操作:** index/manifests内pathとディスク上の画像path、処理前後sha256。
6. **実行方法・設定:** `Get-Content ..\index.json`; PowerShell `Get-ChildItem` と `Get-FileHash -Algorithm SHA256` を検討ディレクトリから対象画像に使い、実際の取得コマンド/結果を記録。画像数を商品ごとに照合。
7. **期待する出力:** 9+11+6+24+9=59画像の入力manifest/hash一覧と処理後hashが一致。
8. **期待DB・件数・予約・版・ログ:** DBなし。入力path数59、重複pathなし、処理前後hash一致または差異一覧。
9. **禁止する副作用:** 原画像の変換/rename/overwrite、ページ再取得、OCR以外の出力を元フォルダーへ作成。
10. **合否判定:** 既存画像だけを対象とし、hash不変ならpass。欠落/差異があればfileごとに記録し他入力を処理。
11. **保存証拠:** `input-sha256.txt` と実行後hash照合結果、正確なshell command。
12. **後片付け・再実行:** hash listと成果物を検討配下に保持。異常があれば元画像を修復・削除せず報告。

#### CASE-04 — 59画像の全件OCRと結果対応

1. **対応する要件・STEP:** R-03, R-05〜R-07 / STEP 02, 03, 05。
2. **目的:** 全画像が処理され、返却OCR全文と空/失敗が欠落なく出力に記録されることを確かめる。
3. **種別・方法:** 一回限りのデータ処理確認。完全node: N/A（既存test node未提示）。
4. **事前条件・初期データ:** runner、隔離env、index/画像59点、事前hash。
5. **入力値・操作:** `index.json` とリンクされた全画像を指定順にOCR。
6. **実行方法・設定:** 計画コマンド例 `& .\.venv-ocr\Scripts\python.exe .\ocr_runner.py --index ..\index.json --output .\results\ocr-results.jsonl`。実行時は確定した引数・言語・model/engine版・return codeを記録。
7. **期待する出力:** 59入力すべてに画像pathが紐づく一件の全文/空/エラーrecord。信頼度等を後処理で削らない。
8. **期待DB・件数・予約・版・ログ:** DBなし。入力数59。出力record数が59または再実行分との重複が説明可能。処理engine/model版、言語、設定、開始終了JST。
9. **禁止する副作用:** 対象画像filter、confidence-filter、OCR output masking、外部ページアクセス、原画像変更。
10. **合否判定:** すべての入力pathが成功/empty/errorのいずれかへ対応すれば全件カバレッジ成立。エンジンが返す全textを保存。認識精度率は判定に使わない。
11. **保存証拠:** OCR JSONL、command/return code、manifest突合記録、sha照合。
12. **後片付け・再実行:** 出力とenvを残す。途中終了は既存recordを保持し、未処理pathまたは別試行fileとして再実行を記録。

#### CASE-05 — 5商品の説明情報がテキスト化されたかを確認

1. **対応する要件・STEP:** R-04〜R-07 / STEP 04, 05。
2. **目的:** 全OCR recordと元画像を照合して説明情報の確認数を商品ごとに数え、作業手続きの完了と技術実証の結論を別々に示す。
3. **種別・方法:** 元画像/OCRの人手目視照合。完全node: N/A（既存test node未提示）。
4. **事前条件・初期データ:** F-01, F-03, F-04, F-07, F-09。59画像、OCR JSONL（partialでも可）、既存商品manifestと視認レポート。
5. **入力値・操作:** 利用可能な全59件の各OCR recordと元画像を照合し、画像ごとに説明情報が画像と対応したか数える。各商品についてOCR成功/空/error件数と説明情報確認画像数を集計する。
6. **実行方法・設定:** 各商品画像と対応OCR textをローカルで表示し、`OCR検証結果.md` に対象画像数、説明情報確認画像数、該当するimage path/record ID、OCR処理状態、読取制約を記録する。手続き完了は5商品分のstatus/count/evidence記録が揃うこと、技術実証は照合で確認できた商品/画像数として別欄にする。
7. **期待する出力:** 5商品の各成功画像数/確認対象数、確認できた商品数/5、照合根拠。確認数0/5なら結論を「実証されず」とする。
8. **期待DB・件数・予約・版・ログ:** DBなし。商品評価欄5件、OCR record照合は最大59画像。参照するOCR engine/model/出力版を記録。
9. **禁止する副作用:** raw OCR/画像の編集、文字起こしの推測補完、未確認情報を商品事実と断定。
10. **合否判定:** 手続き完了は5商品の件数と証拠を記録したこと。技術実証の結論は別判定とし、成功0件なら「実証されず」。0件・一部OCR失敗でも他商品の照合、報告、後続を止めない。
11. **保存証拠:** 商品ごとの画像path、説明情報確認画像数/対象画像数、OCR状態件数、対応結果path/record ID、確認者とJST、結果報告。
12. **後片付け・再実行:** raw画像/resultは保持。再確認は原因となった商品/画像に絞り新しい所見を追記。

## 11. 導入・停止・復旧・最終報告

### 導入

環境導入は検討専用venvと、検討配下に保存する全package/native/model/data/cache/tempに限定する。選定は人気、保守状況、日本語、Windows CPU、Python 3.12の根拠を踏まえる。導入時に取得したpackage/native/model版・依存版・source・license・実際のコマンド・保存pathを保存する。隔離できない候補資材は生成せず候補操作だけ`not_performed`として次点へ進み、偶発的な外部生成は事故として記録して出力から除外する。プロダクトへ導入しない。

### 中断・再開

検討folderとraw resultを保持して、直近の完了record、engine/model、入力hashと未処理一覧から再開する。完全な自動resume/重複排除を作らない。入力や版が異なる場合は混ぜず別run出力へ保存して差分を説明する。環境構築やデータの一部失敗を全作業停止に広げない。

### 失敗時の伝達

操作別に `performed / failed / not_performed` と理由を記録する。install/computeができなかった時はOCRを成功と書かず、実際にできた比較、他候補試行、各商品で確認可能な範囲、再開方法を記録する。未確認理由は最終報告に残し、後続の投稿文入力検討を許可・不許可するgateに使わない。

### 最終報告

`OCR検証結果.md` は、選定候補と人気指標、実際に導入したOSS/版、対象/処理件数、5商品の結果、代表的な読取崩れ、商品説明以外の混在、正確なコマンド、元画像ハッシュ、未実施操作、残る限界を平易にまとめる。完全精度や本番利用可を推定しない。実行者はrepo指示に従いタスク所有ファイルだけを確認・commit・pushし、branch/commit/push先か失敗理由を報告する。

## 12. 未確定事項・判断・影響

| Unknown / decision | 今の状態 | 計画上の扱い・影響 |
| --- | --- | --- |
| controller job identity | `job_id/source_run_id/target_date` 未提供 | 推測せずunknown。今回のフォルダ文脈と作業要求で計画を続ける。 |
| 比較対象の現在のstars/download/更新値 | 未調査 | STEP 01で取得日時点の値を取得する。計画では人気順位を断定しない。 |
| 比較候補でPython 3.12/Windows CPUが実際に動作するか | 未確認 | 公式対応情報とisolated installで確かめる。失敗は記録し次点またはnot_performedを扱う。 |
| OCRの日本語model/データ版と配布ライセンス | 未確認 | 採用OSSに対応する一次資料と実際の取得版で確認。 |
| system Python実体/空き容量/network | 未確認 | 実行時にread-only観測。取得不可は操作別理由に残して次へ進む。 |
| 過去の環境チェックの保存先 | 共有事実に元ログpathなし | evidenceは親からの既知事実として参照し、実施時に状態を再確認する。 |
| 画像の実際の拡張子、破損、文字含有状況 | image count以外は全件未確認 | runner前にpathを列挙し、OCR error/emptyも結果として保存する。 |
| 5商品のうちどれで説明文OCRが成立するか | 未実施 | STEP 04で商品別に確認し、実結果のみ記載。 |
| 新規作成するOCR結果/runnerをremoteへpushできるか | 実施前 | Git差分・サイズ・対象ファイルを確認しAGENTSに従う。接続/認証不可は理由を残しファイルを保持する。 |

## 13. 機械確認と独立レビュー

- この計画作成中にOCR製品実行、パッケージ調査、install、画像処理はしていない。
- 現在の既存ファイル検証では、共有された `index.json` が5商品を列挙し、記録された画像数合計は59。目視確認レポートにOCR未実施、`span.sale_desc` 内に説明以外の画像が含まれることが記載されている。
- 実行時に `OCR技術検証/` へ新設するものは候補資料、専用環境、短いrunner、OCR結果と評価である。アプリ側のtest nodeは割り当てない。
- 計画の独立read-only reviewでは、全FactとEvidence、要求/STEP/case対応、全STEPの14項目と全文prompt、全CASEの12項目、対象範囲、model/effort/tier、操作失敗/再開、Git scope、ゲートの有無を確認する。レビュー所見は参考に反映し、レビュー結果をユーザーの当初依頼に対する新しい承認gateにしない。
- 実行後レビューはSTEP 05が対象とし、コードのreviewと結果証拠を区別する。テストコードの独立review工程は設けない。

## 14. 実行記録欄

計画作成時点では実施結果を記入しない。担当者が各操作後に事実で更新する。

| Field | Value |
| --- | --- |
| Controller job id | unknown — 未提供 |
| Source run id | unknown / not supplied — 推測しない |
| Controller target date | unknown / not supplied。folder名の `2026-10-22` と同一視しない |
| Planning status | 計画v1.2作成済み。v1.2独立再レビューのR-02 path所見は解消済み |
| Execution status | STEP 01〜05実施済み。STEP 05は独立reviewの所見を反映し、負例の再現情報も記録。最終read-only再確認で残る所見なし |
| Source image OCR | `2026-10-22 22:53:14〜23:08:26 JST`、`.\run_ocr.ps1 -OutputName 'ocr-results.jsonl'`、59 success / 0 empty / 0 error、return code 0。hash manifest不一致0、OCR前後の画像変更0。詳細は `OCR技術検証/OCR検証結果.md` |
| Package research/install | PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 / PaddleX 3.7.2、PP-OCRv6 medium、日本語、CPU。隔離venv/model/cache/tempとlockを `OCR技術検証/` に作成 |
| Tests/nodes | 製品側の既存nodeは未提示・未実施。最終runnerの1画像smokeはsuccess 1、終了コード0、wrapper実行後のR:解除も確認。範囲外index pathとstudy-root env overrideは各終了コード2、出力なし。index内の範囲外商品pathは終了コード1、出力なし。同一hashの誤った既存R:はwrapper process終了コード1でOCR開始前に拒否し、`results/ocr-alias-mismatch-probe-final.jsonl` は未作成。study/decoy runner hashは両方 `59D0C784DBB86F6FA82EB9B428486D32BEFD4AE3B81BDCA83103B6B49F366170`。harness alias解除は終了コード0。全件OCRと59画像の目視照合はCASEに沿って記録。空画像がなかったためempty入力は未確認 |
| Review | 初回独立reviewの3指摘と再reviewのコード所見を修正し、照合一覧の整合を確認。追加LOW所見の負例証拠について、wrapper command、終了コード、出力path、study/decoy hashを記録。最終read-only再確認で記録間の整合が確認され、残る所見なし。承認gateとは扱わない。詳細は `OCR技術検証/review.md` |
| Git revision / status | 実行開始時HEAD `b38cbd9fbca17591998ae5b9c6bb32d493ffefa1`、branch `main`、tracking `origin/main`。今回確認した未追跡ユーザーデータ `rakuten-room-inago/非公開作業データ/` は対象外。最終Git処理を完了時に追記 |
| Unknowns | OCRのCER/WERや正解文字との精度評価は未実施。対象は `span.sale_desc` 由来画像59点で、DOMテキストや他要素画像の網羅性は未確認。 |
| Next action | OCR技術検証の成果を使い、商品画像OCR結果を投稿文生成入力へ渡す構成・ノイズ対策を別途検討。 |
