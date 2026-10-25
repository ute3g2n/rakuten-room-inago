# item_descのHTML・全画像・OCRを投稿文入力へ加える実行計画

## 0. 文書版・対象・状態

- 計画版: 1.2 / 作成日: 2026-10-25 JST。調査基準版: `2eb41c272677ac5f906c081350e09f858ffb252c`。
- 1.1変更: 独立reviewのP2指摘を受け、画像/OCR再試行の保証範囲を局所関数の明示再呼出しへ限定。通常run resumeの挙動変更・orchestrator所有追加は行わない。
- 1.2変更: 初回の計画作成後、本人の「実行計画を1stepずつ順番に実行」という明示指示を受けてSTEP 1～5を実行した。仕様2.65、STEP 1～4は完了。STEP 5は局所範囲と独立製品reviewが合格し、計画回帰82 passed/4 failedのため部分完了。実測は各C01～C07 worklogと実行記録に結び付ける。
- 固定root: `C:/project/rakuten-room-inago`。別worktree、checkout、workspaceを作らず、共同作業者の変更を戻さない。
- 初回依頼は計画のみだったが、その後の本人指示で実行へ範囲が更新された。初版の`planning_only`と`not_started`は計画作成時点の履歴であり、現在の状態は本節1.2と第9・14節、`worklogs/projects/implementation/item-desc-capture-20261025/C01`～`C07/`を参照する。1.1内の割当promptにある「計画だけの間は実行しない」という記述は実行前の計画時点を示し、現在は本人指示により superseded。
- 現在の所有変更: 正本仕様、product capture adapter、capture/OCR/copy tests、writer/reviewer skillとprompt、計画実行記録・証拠。Git操作は親担当が行う。
- identity: `job_id=null`, `incident_id=null`, `source_launch_id=null`, `source_run_id=null`, `target_date=null`, `invocation_id=null`, `attempt_id=null`。`missing_reason`: 「本人によるローカル機能追加でcontrollerのjob/run/attempt入力はない」。文書の日付を日次対象日に転用しない。`overall_status=partial_with_known_unrelated_regression_failures`, `daily_status=null`, `reason_code=user_authorized_plan_execution`, `failure_phase=planned_regression`, `trace_status=local_test_evidence_recorded`。

### 必ず守る項目

本人1名が本人のローカル環境だけで使うシステムであり、堅牢性や防御層の追加を品質目標・開始条件にしない。review/test/readiness/release、approved、FAIL、unknown原因、証拠不足、rejected/changes_requested/unavailable、receipt、回数上限を本人が明示した開始・再開や後続工程の自作gateにしない。操作不能ならその操作だけ`not_performed`と具体理由を残し、独立して可能な作業と最終報告を続ける。失敗を成功と表現しない。原因不明なら実行可能な切分け・trace/logを残す。自作制限を発見した担当は許可範囲で根本制限を除去し、所有外なら親へ必要差分を渡して範囲更新後に続行する。正しい対象を選ぶID、実際の環境・外部権限、今回の本人による機能追加の実行範囲は維持する。全体runの反復で調査しない。まず失敗nodeと直接影響node、修正後に計画した回帰全体を一度実行する。

後日の実行担当はreasoning effort=highを指定し、利用可能な既定modelを用いる。model/tier/session実測は現時点null（未起動）で、未観測を開始条件にしない。独立計画reviewは別担当の読取専用確認、製品reviewは製品コードと実測証跡を対象とし、テストコードの独立レビュー工程は設けない。

## 1. 目的・完成状態・範囲

ROOM検索から実際の商品ページへ遷移した時に、既存の`span.sale_desc`等に加えて`span.item_desc`全要素のrendered `outerHTML`全文と、その中の全画像データを取得する。HTMLを`page.json`、画像を同商品ディレクトリの`images/`、全画像のOCR結果を`ocr.json`に保存し、作成・独立レビューの両担当へ全文を渡す。投稿文作成skillにはitem_descのHTMLとその画像OCRを参考にすることを明記する。

本計画内のR01～R07は追跡用ローカル要件IDであり、既存仕様に存在するIDと偽らない。

| ID | 要件・受入状態 |
| --- | --- |
| R01 | 正本HTMLの要件を最初に更新し、内蔵source・表示本文・辞書・追跡表を同期する。仕様の更新履歴から今回の追記を読める。 |
| R02 | sale_descとitem_descの両方、片方、複数、空、不在、取得不能を区別し、全outerHTMLを無切捨てでpage.jsonへ保存する。 |
| R03 | 両説明領域の全画像候補を取得対象とし、重複URLを同一化してもselector・要素・DOM出現箇所・属性の全由来を残す。遅延・srcset等も対象。 |
| R04 | item_desc由来を含む全画像を既存OCR工程へ通し、全結果・全文・失敗をocr.jsonへ登録する。局所的にcapture関数またはOCR関数を明示再呼出しした場合だけ未完了操作を再試行する。通常run resumeから画像再取得・ocr_partial再OCRへ到達する挙動は今回変更・保証しない。 |
| R05 | item_desc HTML・その画像OCRをAI入力へ全文で渡し、作成・独立レビューskill、実行prompt、文型ルールに明記する。根拠参照と商品同一性を維持する。 |
| R06 | 旧page/ocr/runの互換、既存ref・画像保持・確定本文を維持する。破壊的DB migration、旧runの一括再取得・再生成を行わない。 |
| R07 | 局所・回帰・独立製品review・記録を版と証拠に結ぶ。機密や外部原文を共有ログへ漏らさず、未実施と成功を分ける。 |

対象外はROOM検索条件、テーマ選択、商品選定、文型の新設、AI応答schema変更、OCR engine/model変更、全体日次の起動、投稿、DB migration、過去runへの遡及追加。

## 2. 調査からの事実・事象追跡

以下はソースを実際に読んだ事実で、実行結果や本番画面の確認を意味しない。今回の事象は追加要件であり、現行コードが新要件を実装済みとは扱わない。

| fact | 確認済み事実 | evidence refs |
| --- | --- | --- |
| F01 | 要件HTMLが唯一の正本。内部requirements-sourceをloadしbuildが再生成する。内蔵版は2.61。 | `tools/docs/inago-requirements/README.md:3`; `tools/docs/inago-requirements/document-source.mjs:29`; `tools/docs/inago-requirements/build.mjs:9`; `tools/docs/inago-requirements/finalize-html.mjs:39`; `docs/specifications/inago/イナゴスキル要件定義書.html:5269`（内蔵source第11・14章、IR-010/IR-014） |
| F02 | 現行selectorはsale_desc、normal_reserve_item_name、normal_reserve_catch_copyの3種類。画像探索はsale_descだけ。全一致のouterHTML配列は取得する。 | `automation/inago/adapters/product_page_inputs.mjs:7`; `automation/inago/adapters/product_page_inputs.mjs:174`; `automation/inago/adapters/product_page_inputs.mjs:180`; `automation/inago/adapters/product_page_inputs.mjs:212` |
| F03 | currentSrc、src、遅延属性、srcset、背景画像を列挙しURLで重複排除する。occurrencesはorder/tag/attributeのみでselector由来がない。data/blobは現状除外。HTTP画像は公開URL・redirectを検査する。 | `automation/inago/adapters/product_page_inputs.mjs:88`; `automation/inago/adapters/product_page_inputs.mjs:113`; `automation/inago/adapters/product_page_inputs.mjs:186`; `automation/inago/adapters/product_page_inputs.mjs:196`; `automation/inago/adapters/product_page_inputs.mjs:214` |
| F04 | capturedの既存pageは即再利用され、画像の個別失敗でもpageはcaptured。ROOM遷移と直接取得は同じ保存関数を呼ぶが、orchestratorはcapturedの既存pageを返し、page欠落時にだけ再取得を呼ぶ経路があり、ocr_partialも処理済みとしてskipする。JS関数の局所再呼出しは通常run resumeの再取得到達を証明しない。 | `automation/inago/adapters/product_page_inputs.mjs:26`; `automation/inago/adapters/product_page_inputs.mjs:157`; `automation/inago/adapters/product_page_inputs.mjs:247`; `automation/inago/adapters/product_page_inputs.mjs:278`; `automation/inago/adapters/public_sources.mjs:775`; `automation/inago/adapters/public_sources.mjs:1432`; `automation/inago/orchestrator.py:3281`; `automation/inago/orchestrator.py:3474`; `automation/inago/orchestrator.py:3822` |
| F05 | Pythonのpage loaderはschema_version=1とrun/shop/item/refを照合しselector keyを固定していない。確定後はimages/だけを削除しcleanup.jsonを残す。未選定商品の全フォルダー掃除も既存機能。 | `automation/inago/product_page_inputs.py:60`; `automation/inago/product_page_inputs.py:105`; `automation/inago/product_page_inputs.py:143`; `automation/inago/product_page_inputs.py:206`; `automation/inago/product_page_inputs.py:329` |
| F06 | OCRはpage.images全件を処理。refはpage_refと画像順に依存。各画像後に保存し同一バイトhash・成功/空結果は再利用、失敗は再実行する。25MiB/120frame/40Mpixel制限と明示エラーがある。 | `automation/inago/product_ocr.py:42`; `automation/inago/product_ocr.py:481`; `automation/inago/product_ocr.py:527`; `automation/inago/product_ocr.py:575`; `automation/inago/product_ocr.py:633`; `automation/inago/product_ocr.py:688` |
| F07 | copy specはselector配列の全HTMLを動的に展開し参照を作る。OCRはref/status/textを全文で投影。AI応答schemaは生成結果用でselector列挙ではない。 | `automation/inago/production_runtime.py:775`; `automation/inago/production_runtime.py:786`; `automation/inago/production_runtime.py:825`; `automation/inago/production_runtime.py:857`; `automation/inago/schemas/ai-copy.schema.json:1` |
| F08 | writer/reviewer/文型ルールとpromptは全HTML・全OCRを要求するが、説明上の列挙は現行3種類。実行時も両担当に同じ全入力を渡す指示がある。 | `automation/inago/prompt_builder.py:311`; `automation/inago/ai_runner.py:949`; `automation/inago/ai_runner.py:958`; `.agents/skills/rr-inago-copywriter/SKILL.md:20`; `.agents/skills/rr-inago-copy-reviewer/SKILL.md:19`; `.agents/skills/rr-inago-copywriter/references/文型ルール確定.md:21`; `.agents/skills/rr-inago-copywriter/references/文型ルール確定.md:789`; `.agents/skills/rr-inago-copywriter/references/contracts.md:5`; `.agents/skills/rr-inago-copy-reviewer/references/contracts.md:3` |
| F09 | capture、OCR全文変換、AI全文輸送、大容量、再開、cleanup、URL安全の既存nodeがある。コード読取で存在を確認しただけで今回未実行。 | `tests_inago/test_product_page_capture.py:92`; `tests_inago/test_product_ocr.py:362`; `tests_inago/test_product_page_copy.py:158`; `tests_inago/test_product_page_copy.py:209`; `tests_inago/test_product_page_copy.py:251`; `tests_inago/test_product_page_selection.py:654`; `tests_inago/test_product_page_cleanup.py:162`; `tests_inago/test_product_page_url_safety.py:81` |

## 3. 現行実装・環境・既知制約

Windows/PowerShellの固定root。既存Python実行入口は`.venv-inago/Scripts/python.exe`、JSの試験はpytestからNodeを呼ぶ。依存に`playwright-core`がある（`package.json:6`）。今回その稼働やブラウザー接続、OCR worker/model実体は検査していない。

`tests_inago/test_product_page_capture.py`はsynthetic DOMと偽requestのNode harnessであり、実ブラウザー遅延読込を証明しない。遅延挙動は後日のネットワークなしPlaywright局所fixtureで画面証拠を取る。利用不可ならその操作の未実施理由と代替DOM証拠を記録し、他caseと製品reviewを続ける。

HTML内sourceのIR-010（商品ID・ROOM取得）、IR-014（本文生成）へ今回の具体契約を追記する。現在HTMLの任意情報記述を、根拠のない商品説明補完や商品情報不足gateへ変えない。辞書・追跡表を編集した後のbuildは参照資料を`docs/document-index.json`から読み直すため、HTMLへの埋込みも同期される。生成スクリプトを一括実行して過去仕様を巻き戻さない。

## 4. 技術案・責務・依存

1. 仕様変更を最初に終える。正本HTML内のsourceと版・更新履歴を更新し、表示本文を既存buildで再生成する。別Markdown正本を作らない。
2. `SELECTORS`末尾へ`item_desc`を追加する。既存3種類の順序を変えず、selector_index由来の既存HTML refを維持する。画像は既存sale_descの探索順を維持してからitem_desc分を加え、URL単位で取得を同一化する。両classを持つ同一要素、入れ子や複数領域でもselectorごとのHTMLを全て残す。
3. occurrencesに`selector_key`, `selector`, `selector_element_index`, `dom_element_index`, `tag`, `attribute`, `order`を加算する。1画像の取得と、その画像が使われる複数の場所を分けて保持する。URL同一化の後に出現元を削らない。異URLの同一バイトは今回同一化しない。
4. 全候補をpage.imagesへ載せることで既存OCRを再利用する。画像由来はpage.imagesの同一順と`ppiimg`/`ocrimg`の対応で追跡する。OCR公開payloadの形を変更せず、詳細由来はpage.jsonとocr-raw.jsonの既存source_refで照合する。OCRコードは必要な最小差分だけにし、機能追加のための無用な書換えは行わない。
5. AI入力は既存の全selector展開を利用し、明示文言の固定3種類をitem_desc込みへ更新する。writerだけでなくreviewerも同じ原文を読む。copy_service、DB、AI応答schemaは変更不要という現時点の判断。実装時に別の固定列挙が見つかったら根拠と最小所有追加を親へ返す。

## 5. 入出力・状態・保存契約

保存先は既存`<data_root>/product-page-inputs/<encoded-run>/shop-<base64url-shop>/item-<base64url-item>/`を継続する。`page.json`は既存schema_version=1の加算拡張とし、item_descのselectorレコードと由来項目を追加する。Python loaderが既存形を受けることを確認済みなのでmajor version増加・DB migrationは不要。

| 入出力 | 計画する内容 |
| --- | --- |
| page.selectors | 既存3種類にitem_descを末尾追加。全一致のouter_html配列、captured/empty/not_found/unavailable。空配列だけで未取得と不在を混ぜない。 |
| page.images | 全領域からの全候補を記録。バイナリはimages/へ保存しfile/content_type/byte_length/statusを記録。参照できない候補も失敗理由を残し、無言で捨てない。 |
| 候補種類 | img.currentSrc/src、既存data-*属性、source/imgのsrcset・遅延srcset、inline/computed背景。data:imageは復号、blobは同じページ内でバイトを読む。取得・復号不能は明示失敗にする。既存公開HTTP・redirect制約を維持し認証cookieを外部転送しない。 |
| 遅延画像 | 各説明領域と画像を局所スクロールし、ロード後の属性・HTMLを再採録。単純な固定sleep追加にせず、fixtureの遅延イベントを観測して完了・未完了を区別する。完了時点以降に追加される無限更新の完全性は断言しない。 |
| OCR | page.images全件についてsuccess/empty/errorをocr.jsonへ記録し全文・改行を保持。ダウンロード不能もerrorレコードを残す。既存engine・上限は変更しない。上限超過を切捨て成功に変えない。 |
| AI | product_page_html.selectors[].elements[].outer_html、product_page_ocr.images[].textを全件全文でwriterとreviewerへ渡す。HTML/OCR refをinput_refsへ、採用根拠を既存evidence_refs/used_input_refsへ結ぶ。 |

旧pageにitem_desc keyがなければ「旧取得契約につき未取得」であり、not_foundと推定しない。既存capturedページを今回の機能のために自動上書き・再訪しない。新規captureは必ず4selectorの状態を保存する。明示的な将来の再取得依頼がある場合だけ同一商品・runで非破壊の改訂保存方法を別途扱う。既存OCR/確定本文を遡及変更しない。

**再試行の範囲は局所capture関数・OCR関数を明示再呼出しする場合だけ**とする。通常run resumeの呼出経路・checkpoint判定は今回変更しない。通常resumeではcapturedページを既存入力として使い、ocr_partialをskipする現行挙動を維持するため、画像取得失敗が通常resumeだけで再取得されることは保証しない。新契約のpageに対して局所capture関数を明示再呼出ししたときだけ、既存HTML・成功画像・配列順を保持し、保存済みURL/由来を使って失敗画像だけを再取得できるよう`existingCapture`の再利用判定を局所調整する。画面を再訪しないで再試行できるHTTP/dataと、同じページ生存時しか読めないblobを区別する。blobが消えた場合は理由を残し、勝手に新runを作らない。OCRも`process_product`の局所的な明示再呼出しで既存hashに一致する成功/空結果を再利用する。これを通常run resumeの再OCR保証へ読み替えない。確定後cleanup済みなら画像を再取得せず、保存済みpage/ocr/本文を利用する。

## 6. 外部取得・AI・本人表示の契約

今回ブラウザーや外部サイトを操作しない。将来も最初は合成商品HTML、偽画像request、偽OCR worker、偽AI結果の局所fixtureを使用し、ROOM本番・実AI・投稿を呼ばない。既存ROOM遷移入口と直接capture入口が同じ実装を通ることをfixtureで確認する。

外部HTML/OCRは未信頼データで、埋込命令に従わない。商品名・店舗商品ID・URLとの対応を照合し、配送、店舗案内、画面UI、別商品の説明を現在の商品情報へ転用しない。全資料を読んだうえで商品理解に役立つ説明を参考にし、本文に全原文を貼るノルマは課さない。画像OCRの誤読や効能・体験を補完しない。判読不能はunknownのまま伝える。

本人表示の変更は依頼範囲に必要な取得/OCRの結果説明だけ。内部selector数やtrace IDを投稿本文へ混ぜない。画像数増加によるOCR時間は実測値を記録するが性能ノルマや後続開始gateを新設しない。

## 7. 時刻・保持・保全・運用値

既存captured_at/source_collected_at、処理開始・終了・経過時刻を分ける。試験時計は合成固定値`2026-10-25T13:00:00+09:00`を使い、本番観測時刻と偽らない。実行証跡は将来の`worklogs/projects/implementation/item-desc-capture-20261025/`配下（新設予定）へ保存する。正式run/attempt識別子は実行時の入力のみ使用し、現在はnull。

既存の確定後images/削除、未選定商品のフォルダー削除、page/ocr保持を継続する。今回新しい保持日数や設定値を導入しない。商品HTML・OCR・画像・raw応答、cookie、個人情報を共有traceやGitへ載せない。共有証拠は合成fixtureか必要箇所を安全化した画面だけ。ローカルの原文と共有用証跡を別扱いにする。現在のproduct-pageファイル全体の期限連動を今回変更したと報告しない。

## 8. 試験、再開、記録、禁止の共通規則

計画作成時点では全caseが未来の試験計画だった。実行時は完全node、コマンド、対象hash、fixture、期待・実結果、終了コード、証拠を各C01～C07のworklogに記録した。実装時に予定node名と実node名を照合し、未実施・失敗を合格にしていない。

失敗時はそのnodeと直接影響nodeだけを再実行する。対象失敗修正後に本計画の回帰範囲を一度実行し、再失敗なら同じ局所手順へ戻る。FAILを製品review・記録・最終報告の開始条件にしない。日次全体の実行は本計画に含めず、ユーザーが将来起動を依頼した場合もAGENTS.mdの局所・回帰確認を先に行う。

画面・非同期挙動の局所runは直前/直後スクリーンショット、動画、trace（snapshot・操作記録）を残す。取得できない証拠は具体理由と代替DOM/logを残す。合成画面を本番の証拠として扱わない。現在は全画面証拠未取得（計画のみのため）。

禁止: 本番DB照会・更新、設定・Scheduler変更、日次全体起動、商品投稿、実AI呼出し、別workspace、旧run書換え、秘密情報出力、無関係な大規模修正、テストコードの独立レビュー。

## 9. 実行STEP一覧と各STEPの完全prompt

| STEP | 内容 | 状態 | 複雑度・依存 |
| --- | --- | --- | --- |
| 1 | 要件HTML・辞書・追跡表の先行同期 | completed | 仕様2.65 build成功 / C01,C07 |
| 2 | 全HTML・全画像・由来・局所再試行 | completed | capture/browser/URL safety 19 passed / C02～C04,C07 |
| 3 | OCRの全件登録・全文・互換確認 | completed | OCR/copy/resume/cleanup連鎖3 passed / C05,C07 |
| 4 | 作成・レビューAI入力とskillの明示同期 | completed | skill/promptの連鎖確認 / C06,C07 |
| 5 | 統合局所・回帰・独立製品review・記録 | partial | 独立review指摘なし、計画回帰82 passed/4 failed / C07 |

### STEP 1: 要件を先に修正する

1. **番号・名称・状態**: STEP 1 / 正本仕様の更新 / completed（2.65）。
2. **目的と成立状態**: 商品ページの両説明領域・画像・OCR・全入力参照が仕様で追跡できる。
3. **要件・受入・原典**: R01,R02,R03,R04,R05,R06,R07。F01,F02,F03,F05,F08,F09。既存IR-010/IR-014へ今回の受入追記を行う。新しい既存風の要件IDを捏造しない。
4. **先行・開始条件**: 後日の本人による実行依頼と本計画が入力。計画review承認は開始条件ではない。今回の計画のみ依頼では実行しない。
5. **入力・版・DB状態**: 基準HEAD、正本HTML内source version 2.61、辞書/追跡表。DB入力なし。
6. **作業場所・所有範囲**: 固定root。`docs/specifications/inago/イナゴスキル要件定義書.html`, `docs/specifications/inago/要件データ辞書.md`, `docs/specifications/inago/要件追跡表.md`。生成経路は既存toolを読取利用し変更しない。buildで生成される既存`docs/specifications/inago/図解/`は副生成差分を点検し、今回に無関係なら採用しない。
7. **実施手順**: 3文書の現行差分を確認。HTML内source第11/14/17/18/26/28章の関連記述と更新履歴へ両selector全件、画像、OCR、互換・参照を追記。辞書・追跡表も対応する。HTML内snapshotをserializeDocumentと同じescapeで更新し、`node tools/docs/inago-requirements/build.mjs`を実行。既存source全章・内蔵参照が保たれたことと、再生成後も追記が残ることを確認する。
8. **成果物**: 仕様3文書の差分とC01記録。要件更新を終えてから製品変更へ渡す。
9. **変更制約**: 仕様以外の製品・試験・skill・設定・DBは変更しない。旧Markdownを正本に復活させない。過去修正scriptの一括再実行をしない。
10. **レビュー・検証**: C01。独立計画review所見も利用するが承認待ちgateにしない。
11. **合格条件・証拠**: HTML表示・内蔵sourceと辞書/追跡表でR01～R06の内容が一致し、build exit=0、既存要件の欠落なし。失敗は実値で記録。
12. **失敗・修正・再開**: build失敗なら仕様入力・原因箇所を局所修正。仕様の保存済み版を失わず、独立可能な読取整理を継続する。
13. **引き渡し**: 仕様差分、版、buildコマンド/終了コード、未確定をSTEP 2～4と親へ渡す。Gitは親担当。
14. **AIエージェントへ渡すprompt全文**:

```text
C:/project/rakuten-room-inagoだけで作業し、AGENTS.mdとplans/projects/implementation/item-desc-capture-20261025/plan.mdを読んでSTEP 1を担当する。入力基準は2eb41c272677ac5f906c081350e09f858ffb252cと正本HTML内requirements-source 2.61である。後日の本人実行依頼が入力された時に実行し、今回の「計画だけ」の依頼中は実行しない。所有fileはdocs/specifications/inago/イナゴスキル要件定義書.html、docs/specifications/inago/要件データ辞書.md、docs/specifications/inago/要件追跡表.md。商品ページでsale_descとitem_descの全HTML・全画像を保存し、全画像OCRと全文AI入力、同一商品参照、旧run互換を要件へ追記する。画像/OCR再試行の保証は局所関数の明示再呼出しに限定し、通常run resumeのcaptured再利用・ocr_partial skipは変更しないことも仕様に明記する。まず仕様を修正し、その後に製品担当へ渡す。HTML内sourceを更新し既存node tools/docs/inago-requirements/build.mjsで再生成し、全章・参照資料と追記の一致、再生成で消えないことをC01で検証する。buildの図解副生成差分も一覧化し無関係な変更を混ぜない。製品、試験、skill、設定、DB、ブラウザー、日次は操作しない。別worktree/checkoutを作らず共同作業者の変更を戻さない。本人1名のローカル用途で堅牢性を品質目標にせず、review/test/readiness/release、approved、FAIL、unknown、証拠不足、rejected/changes_requested/unavailable、receiptや回数上限で明示された開始・再開・後続を止める自作gateを作らない。操作不能ならその操作だけnot_performedと具体理由を残し独立作業と最終報告を続け、失敗を成功にしない。自作制限は許可範囲で根本除去し所有外は親へ必要差分を返す。正しい対象入力と実際の外部権限、今回の計画のみ範囲を維持する。コマンド・対象・版・期待実結果・終了コードを将来のworklogs/projects/implementation/item-desc-capture-20261025/へ安全化して記録する。秘密や外部原文を共有しない。Git stage/commit/pushは親が行い担当は実行しない。成果物と未確定をSTEP 2～4へ渡す。reasoning effortはhigh、利用可能な既定modelを使い、model/tier未観測を起動gateにしない。
```

### STEP 2: item_descの取得を追加する

1. **番号・名称・状態**: STEP 2 / HTML・画像・由来・局所再試行 / completed。
2. **目的と成立状態**: 両説明領域全件のHTMLと画像が同一商品pageへ保存される。
3. **要件・受入・原典**: R02,R03,R06,R07 / F02,F03,F04,F05,F06,F07,F09 / C02,C03,C04,C07。
4. **先行・開始条件**: STEP 1の要件内容を先に更新。承認statusを待たない。取得対象・manifestで必要な実IDのみ維持。
5. **入力・版・DB状態**: 基準HEADに仕様差分、合成DOM/画像/clock、tmp_path。DBなし。実URL/ROOMは使わない。
6. **作業場所・所有範囲**: `automation/inago/adapters/product_page_inputs.mjs`, `tests_inago/test_product_page_capture.py`。新設予定`tests_inago/fixtures/product-page-item-desc.html`, `tests_inago/test_product_page_item_desc_browser.py`。`public_sources.mjs`は既存両入口を読取確認し変更不要を初期判断とする。
7. **実施手順**: selector末尾追加、説明領域の共通列挙、全outerHTML、全画像候補を採録。出現由来を追加してURL重複のみ統合。遅延画像後に再採録、data/blobはバイト取得または理由付き失敗を記録。旧pageは非破壊再利用、新pageに対する保存関数の局所的な明示再呼出しで失敗画像だけ再取得する。通常run resumeの再取得/再OCR経路は変更しない。cleanup済みページを再ダウンロードしない。synthetic harnessと局所Playwright fixtureを追加する。
8. **成果物**: 取得差分、fixture、局所証拠。C02～04/C07の予定node実在確認結果。
9. **変更制約**: 既存selector/ref順序、公開URL・redirect制約を維持。無関係な検索、OCR、生成、DBは変更しない。全HTML/画像を件数・文字数で切捨てない。
10. **レビュー・検証**: 新設予定C02～04と既存capture/URL safety nodeを実装後に収集・局所実行。動的画面は前後画像・動画・traceを保存する。
11. **合格条件・証拠**: 全DOM要素の完全一致、全候補状態、重複由来、retry対象限定、旧ファイル非破壊、正確なbyte一致。画面証拠未取得は理由を記録し合格と偽らない。
12. **失敗・修正・再開**: 失敗caseのみ局所修正。局所capture再呼出し時だけpageの成功HTML/画像を保持し順番を変えず残りを再試行する。通常run resumeの画像再取得到達は対象外。blob読取不能は理由を残す。
13. **引き渡し**: schema_version=1の追加契約、画像順・由来、失敗状態、局所結果をSTEP 3/4/5へ。親がGit所有。
14. **AIエージェントへ渡すprompt全文**:

```text
C:/project/rakuten-room-inagoでAGENTS.mdとplans/projects/implementation/item-desc-capture-20261025/plan.mdを読みSTEP 2を実装する。今回は計画だけなので後日の本人実行依頼までは製品を変更しない。入力は基準2eb41c272677ac5f906c081350e09f858ffb252c、先に更新した仕様、合成商品DOM・画像・固定時計2026-10-25T13:00:00+09:00で、DBを使わない。所有はautomation/inago/adapters/product_page_inputs.mjs、tests_inago/test_product_page_capture.py、新設予定tests_inago/fixtures/product-page-item-desc.htmlとtests_inago/test_product_page_item_desc_browser.py。SELECTORS末尾にspan.item_descを追加し、sale_descとitem_desc双方・片方・不在・複数・空・失敗を記録して全outerHTMLを保存する。全画像のcurrentSrc/src/遅延属性/srcset/背景/data/blobを採録し取得または具体的失敗を残す。URL重複は1取得へまとめてもselector、要素番号、DOM位置、属性の全由来を保持する。既存sale画像順とrefを保ち、capture保存関数を局所的に明示再呼出しした場合だけ失敗画像を同一対象で再試行する。通常run resumeの再取得/ocr_partial再OCR経路は変更・保証せず、orchestrator.pyは所有外とする。旧captured pageやcleanup済み画像を自動上書き・再取得しない。既存公開URL/redirect制約を維持する。C02/C03/C04/C07の完全nodeを新設予定と区別して収集後に局所実行し、非同期fixtureの操作前後画像・動画・traceをworklogs/projects/implementation/item-desc-capture-20261025/へ保存、不可なら理由とDOM代替を残す。秘密、cookie、原文を共有ログへ出さない。OCR、生成、検索、DB、設定、日次、実サイト、実AIは変更・操作しない。別worktree/checkoutを作らず共同変更を戻さない。本人1名ローカル用途で堅牢性を品質目標にせず、review/test/readiness/release、approved、FAIL、unknown、証拠不足、rejected/changes_requested/unavailable、receipt、回数上限を後続の自作gateにしない。操作不能はその操作だけnot_performedと理由を記録し独立作業と最終報告を続け、失敗を成功にしない。自作制限は許可範囲で根本除去し所有外は親へ必要差分を返す。必要な対象ID、実環境権限、計画だけの範囲を維持する。失敗nodeと直接影響nodeだけ修正再実行し、版・入力hash・コマンド・期待実結果・終了コード・証拠を記録する。Gitは親所有で実行しない。manifestと結果をSTEP 3/4/5へ渡す。effort high、既定modelを用い実測不明を開始条件にしない。
```

### STEP 3: OCR全件登録と互換を確認する

1. **番号・名称・状態**: STEP 3 / OCR全画像・全文・再開 / completed（既存OCR pipelineを再利用）。
2. **目的と成立状態**: item_desc画像もocr.jsonに入り、元画像・OCR ref・全文と失敗が追跡できる。
3. **要件・受入・原典**: R04,R06,R07 / F04,F05,F06,F09 / C04,C05,C07。
4. **先行・開始条件**: STEP 2の新manifestを入力とする。review承認待ちは不要。
5. **入力・版・DB状態**: 仕様版と取得差分、synthetic画像・偽worker・tmp_path、旧v1 fixture。本番DBなし。
6. **作業場所・所有範囲**: `tests_inago/test_product_ocr.py`, `tests_inago/product_page_test_support.py`。`automation/inago/product_ocr.py`と`automation/inago/product_page_inputs.py`は追加manifestで実際に不足が判明した箇所だけ最小変更する所有候補。不要なら製品差分0を正確に記録する。
7. **実施手順**: sale/item混在manifestで全画像処理、失敗レコード、改行全文、順/ref対応、画像hash再利用、process_productの局所的な明示再呼出しによる途中保存の再利用を確認する。通常run resumeからの再OCR到達は対象外とする。旧v1 page/ocrでも既存loaderが動くこと、OCR上限で明示errorとなることを確認する。selector固定filterを新設しない。
8. **成果物**: 全件OCR fixture/結果、必要な最小修正、旧互換とresume記録。
9. **変更制約**: engine/version/model/閾値/schema_version/上限は変えない。DB migrationや旧ocr書換え、全文切捨てなし。
10. **レビュー・検証**: C05と既存全文変換node、必要時のみ失敗関連node。テストコードの独立reviewは実施しない。
11. **合格条件・証拠**: page.images件数=ocr画像結果件数、対応順・全文・改行一致、成功分は不要に再OCRされず、失敗が残ればpartial/failedの実値を保持する。
12. **失敗・修正・再開**: process_productの局所的な明示再呼出しで、同じmanifest・保存済みOCRから未完了画像だけ再試行し、旧結果版と新結果版を混ぜない。通常run resumeのocr_partial skipは変更しない。engine利用不能なら偽worker検査を継続し実engine未実施を残す。
13. **引き渡し**: manifest/OCR参照・版・状態をSTEP 4/5へ。Gitは親。
14. **AIエージェントへ渡すprompt全文**:

```text
C:/project/rakuten-room-inagoのAGENTS.mdとplans/projects/implementation/item-desc-capture-20261025/plan.mdを読みSTEP 3を担当する。今回の計画だけの依頼では実行せず後日の本人実行依頼を入力とする。基準2eb41c272677ac5f906c081350e09f858ffb252cと仕様・取得差分、合成sale/item画像manifest、旧schema_version=1 fixture、固定時計2026-10-25T13:00:00+09:00を使う。所有はtests_inago/test_product_ocr.py、tests_inago/product_page_test_support.py。automation/inago/product_ocr.pyとautomation/inago/product_page_inputs.pyは入力追加で不足が実証された部分だけ最小変更し、不要なら製品差分0を記録する。page.images全件を既存OCRへ通しitem_desc由来もocr.jsonへ全結果・全文・改行を保存、page配列順とsource/ocr refを照合する。失敗画像error、partial/failed、hash一致成功結果再利用、process_productを局所的に明示再呼出しした際の途中保存再利用と未完了だけの再試行、旧v1読取をC04/C05/C07と既存全文変換nodeで確認する。通常run resumeはcaptured page再利用・ocr_partial skipの現行契約を維持し、その経路から画像再取得や再OCRへ到達すると主張しない。orchestrator.pyは所有外で変更しない。engine/model/上限/設定/schema_versionを変えず切捨て成功を作らない。合否は件数・順・全文・ref一致と再開時の呼出し対象で判定し、版・入力hash・コマンド・node・期待実結果・終了コードを将来worklogs/projects/implementation/item-desc-capture-20261025/へ記録する。本番DB、旧run、実サイト、実AI、日次、検索、他担当fileは操作しない。秘密や原文を共有ログへ出さない。別workspaceを作らず共同変更を戻さない。本人1名のローカル用途で堅牢性を品質目標にせず、review/test/readiness/release、approved、FAIL、unknown、証拠不足、rejected/changes_requested/unavailable、receipt、回数上限を明示された開始・再開や後続の自作gateにしない。操作不能ならその操作だけnot_performedと理由を残し独立作業と最終報告を続け、失敗を成功にしない。自作制限は許可内で根本除去し所有外の必要差分は親へ返す。必要な対象ID・実権限・計画だけの範囲は維持する。失敗と直接影響nodeだけ直して再実行する。テストコードの独立reviewは行わない。Gitは親所有で実行しない。実結果と未確認をSTEP 4/5へ渡す。effort high、既定model、model/tier未観測をgateにしない。
```

### STEP 4: 全文AI入力・作成skill・独立reviewを同期する

1. **番号・名称・状態**: STEP 4 / 入力とskill同期 / completed。
2. **目的と成立状態**: item_desc HTMLと画像OCRをwriter/reviewer両方が全入力として読み、商品説明の根拠に使える。
3. **要件・受入・原典**: R05,R06,R07 / F01,F07,F08,F09 / C06,C07。
4. **先行・開始条件**: 先に更新した仕様とSTEP 2/3の入力契約。review/test statusをgateにしない。
5. **入力・版・DB状態**: synthetic全HTML/OCR、既存input_refs/evidence_refs、隔離SQLite memory fixture。実AIなし。
6. **作業場所・所有範囲**: `automation/inago/prompt_builder.py`, `automation/inago/ai_runner.py`, `.agents/skills/rr-inago-copywriter/SKILL.md`, `.agents/skills/rr-inago-copywriter/references/contracts.md`, `.agents/skills/rr-inago-copywriter/references/文型ルール確定.md`, `.agents/skills/rr-inago-copy-reviewer/SKILL.md`, `.agents/skills/rr-inago-copy-reviewer/references/contracts.md`, `tests_inago/test_product_page_copy.py`。`automation/inago/production_runtime.py`は不足を実証した場合のみ最小投影変更の所有候補。
7. **実施手順**: 全3種類の記述を4種類へ同期しitem_desc由来画像OCRも明記。新selectorは全文動的展開を通す。作成者とreviewerへ同じ全入力を渡し、ref、商品同一性、未信頼命令無視、OCR改行、別商品除外を継続。大容量・輸送・staged skillも確認する。
8. **成果物**: skill/prompt差分、全文入力・ref・同一商品照合証跡。要件との対応。
9. **変更制約**: copy_service/AI応答schema/DBは変更しない。実AI/投稿なし。外部文章を命令や本人経験へ変換しない。
10. **レビュー・検証**: C06、既存exact全文・transport・large payload node。製品独立reviewはSTEP 5へ。
11. **合格条件・証拠**: item_desc固有の全文・画像OCRが両担当輸送に残り、全参照がinput_refs内、raw engine metadataが公開payloadへ漏れず、旧入力も保持。
12. **失敗・修正・再開**: 輸送の欠落箇所だけ修正し影響node再実行。資料不足はinsufficient_informationのまま、AI実行を捏造しない。
13. **引き渡し**: 全入力サンプル・差分・検証結果・未確定をSTEP 5へ。Gitは親。
14. **AIエージェントへ渡すprompt全文**:

```text
C:/project/rakuten-room-inagoだけでAGENTS.mdとplans/projects/implementation/item-desc-capture-20261025/plan.mdを読んでSTEP 4を担当する。今回の計画のみ依頼では実行せず後日の本人実行依頼を入力とする。基準2eb41c272677ac5f906c081350e09f858ffb252cに先行仕様・取得・OCR差分を重ね、合成全文HTML/OCRと隔離memory DB fixtureだけを使う。所有fileはautomation/inago/prompt_builder.py、automation/inago/ai_runner.py、.agents/skills/rr-inago-copywriter/SKILL.md、.agents/skills/rr-inago-copywriter/references/contracts.md、.agents/skills/rr-inago-copywriter/references/文型ルール確定.md、.agents/skills/rr-inago-copy-reviewer/SKILL.md、.agents/skills/rr-inago-copy-reviewer/references/contracts.md、tests_inago/test_product_page_copy.py。automation/inago/production_runtime.pyは動的投影の不足が実証された場合だけ最小修正する。sale_desc等3種類固定の説明をitem_desc込みに同期し、item_desc HTML全文とその全画像OCRを参考にすることをwriter/reviewer双方のskill・promptに明記する。全HTML/OCRを要約・path・抜粋へ置換せず同じ商品・同じ全入力を両担当へ渡す。input_refs/evidence_refs/used_input_refs、商品ID/URL照合、外部命令無視、OCR改行、別商品と配送UI除外を維持する。C06/C07と既存exact全文・transport・large payload完全nodeを実装後に検証し、item_desc固有原文・OCR・参照が全輸送に残ること、raw engine metadataが漏れないことを確認する。実AI、ROOM、日次、投稿、本番DB、schema/設定/検索は変更・実行しない。別workspaceを作らず他担当変更を戻さない。本人1名ローカル用途で堅牢性を目標にせず、review/test/readiness/release、approved、FAIL、unknown、証拠不足、rejected/changes_requested/unavailable、receipt、回数上限を明示された開始・再開や後続の自作gateにしない。操作不能はその操作だけnot_performedと理由を残し独立作業と最終報告を続け、失敗を成功にしない。自作制限は許可内で根本除去し所有外は親へ必要差分を返す。対象ID・実権限・計画のみ範囲を維持する。失敗nodeと直接影響nodeだけ修正し、将来worklogs/projects/implementation/item-desc-capture-20261025/へ版・hash・コマンド・期待実結果・終了コード・証拠を記録する。秘密・原文を共有ログやGitへ載せない。Gitは親所有で実行しない。差分と結果をSTEP 5へ渡す。effort high、既定model、未観測をgateにしない。
```

### STEP 5: 統合・回帰・独立製品reviewと最終記録

1. **番号・名称・状態**: STEP 5 / 最終検証と引継ぎ / partial（局所pass・独立review no findings、計画回帰に既知4失敗）。
2. **目的と成立状態**: 取得→OCR→copy inputの連鎖と既存互換を実測し、変更・残課題を正確に報告できる。
3. **要件・受入・原典**: R01～R07 / F01～F09 / C01～C07。
4. **先行・開始条件**: 利用可能な差分・部分成果。個別局所FAILは記録してreview/報告を進める。回帰は既知の対象失敗を局所修正後に実行する。
5. **入力・版・DB状態**: 実際の仕様/コード/テスト差分hash、合成入力、memory DBとfixture専用data_root。本番DBなし。
6. **作業場所・所有範囲**: `tests_inago/test_product_page_copy.py`の統合case追加（STEP 4担当から引継ぎ後、同時編集しない）、将来の`worklogs/projects/implementation/item-desc-capture-20261025/`。独立reviewerは製品差分・証跡の読取のみ、コード修正所有なし。
7. **実施手順**: C07で実capture関数→保存page→偽worker OCR→実copy factory→job stageを連結。C01～06結果を集約。C04/C05の再試行は局所関数再呼出しであり、C07の正常な保存済み入力の通常resume回帰とは別に記録する。通常run resumeから画像失敗を再取得・再OCRする保証は追加しない。既知失敗は局所対応後、下記回帰全体を一度実行。別担当で製品コードと実測証跡を独立reviewし指摘は所有担当へ戻す。仕様同期と未確認も最終記録へ。
8. **成果物**: local/regression/review記録、未実施・未確定一覧、親へのGit対象一覧。日次全体run結果はnot_performed。
9. **変更制約**: テストコードの独立reviewなし。reviewerがコードを変更しない。日次、実AI、ネットワーク、本番DB、旧run変更なし。
10. **レビュー・検証**: 第10節の全回帰コマンド、独立製品review。review承認を開始/反映gateにせず実値を報告する。
11. **合格条件・証拠**: 全入力連鎖で欠落・切捨て・別商品混入・既存ref破壊がなく、計画caseの実測が記録される。FAIL/skip/not_performedは合格へ読み替えない。
12. **失敗・修正・再開**: 失敗nodeと直接影響nodeに戻り、必要差分後の回帰を再実行する。未実施操作は理由と再開位置を保持し報告まで続ける。
13. **引き渡し**: 変更範囲・差分・検証結果・残課題を親へ。親がgit statusを確認し所有fileのみstage/commit、現在branch追跡先へpushし結果を最終報告する。
14. **AIエージェントへ渡すprompt全文**:

```text
C:/project/rakuten-room-inagoでAGENTS.mdとplans/projects/implementation/item-desc-capture-20261025/plan.mdを読みSTEP 5の統合検証を担当する。今回の計画だけの範囲では試験を実行せず後日の本人実行依頼が入力された時だけ進む。入力は基準2eb41c272677ac5f906c081350e09f858ffb252cと実仕様/製品/テスト差分hash、合成商品fixture、固定時計2026-10-25T13:00:00+09:00、memory DB・fixture専用data_root。所有はSTEP 4担当の引継ぎ後のtests_inago/test_product_page_copy.py統合caseと将来worklogs/projects/implementation/item-desc-capture-20261025/の証跡。C07で実capture関数からpage.json、偽worker OCRのocr.json、実copy factory、job-inputまでつなぎitem_desc固有HTML/画像OCRと両selector由来、ref、旧互換を検証する。C04/C05は局所関数の明示再呼出し、C07の通常resume回帰は保存済み正常入力の再利用であり、通常resumeによる失敗画像再取得やocr_partial再OCRを保証しない。orchestrator.pyは変更しない。新設予定nodeはcollectで存在を確認する。失敗nodeと直接影響nodeだけ局所修正担当へ戻し、対象失敗修正後に本計画第10節の全回帰を一度実行する。画面非同期caseは前後画像・動画・traceを記録し、未取得は理由と代替DOMを残す。版・入力hash・時計・コマンド・完全node・期待実結果・終了コード・証拠pathを保存し、skip/not_performed/FAILをPASSにしない。独立製品reviewerには製品コード差分と実測証跡だけを渡して読取reviewさせる。テストコードの独立reviewは行わずreviewerはコードを編集しない。実ROOM、実AI、日次全体、投稿、本番DB、設定、旧runは操作しない。別worktree/checkoutを作らず共同変更を戻さない。本人1名ローカル用途で堅牢性を品質目標にせずreview/test/readiness/release、approved、FAIL、unknown、証拠不足、rejected/changes_requested/unavailable、receiptや回数上限を明示された開始・再開・後続の自作gateにしない。操作不能はその操作だけnot_performedと理由を残し独立作業と最終報告を続ける。自作制限は許可内で根本除去し所有外の必要差分は親へ返す。対象ID・実環境権限・計画だけの範囲は維持する。秘密・外部原文を共有ログやGitへ出さない。Gitは親所有で実行しない。変更対象一覧、差分、局所と回帰と独立reviewの実値、日次全体未実施、未確定と再開位置を親へ渡す。effort high、既定model、model/tier未観測をgateにしない。
```

## 10. 要件/受入/STEP/case対応と各ケース12項目

計画作成時点でcase statusは`not_started`として保存した。実行時の状態は隣接JSONのexecution updateと各C01～C07 result/execution記録へ更新した。元の「新設予定node」は実装時に実際のnode名と照合し、変更・追加されたnodeはworklogの最終コマンドで確認する。

| 要件 | fact | STEP | case |
| --- | --- | --- | --- |
| R01 | F01,F02,F08 | 1,5 | C01 |
| R02 | F02,F04,F07,F09 | 1,2,5 | C02,C03,C07 |
| R03 | F02,F03,F04,F09 | 1,2,5 | C03,C04,C07 |
| R04 | F04,F06,F09 | 1,3,5 | C04,C05,C07 |
| R05 | F01,F07,F08,F09 | 1,4,5 | C06,C07 |
| R06 | F04,F05,F06,F07,F09 | 1,2,3,4,5 | C04,C05,C06,C07 |
| R07 | F01,F03,F05,F06,F08,F09 | 1,2,3,4,5 | C01,C02,C03,C04,C05,C06,C07 |

### C01: 正本・内蔵source・参照資料の同期

1. **対応要件・STEP**: R01,R07 / STEP 1,5。
2. **目的**: 仕様を最初に更新し再生成で失わない。
3. **種別・方法**: 文書buildと構造・内容の照合。pytest nodeなし（非pytest文書検証）。
4. **事前条件・初期データ**: 変更前HTML内source 2.61と3文書のhashを保全。DBなし。
5. **入力値・操作**: 両selector・全画像・OCR・全文AI・互換要件を追記、辞書/追跡表同期。
6. **実行方法・設定**: `node tools/docs/inago-requirements/build.mjs`。同じ更新sourceから再buildして意味上の一致を確認。toolは変更しない。
7. **期待する出力**: 表示本文とrequirements-sourceに同じ要件、辞書/追跡表の対応、旧全章/内蔵資料保持。
8. **期待DB・件数・予約・版・ログ**: DB/予約変更0。文書版・前後hash・build終了コードを記録。
9. **禁止する副作用**: 過去要件・別担当差分の消失、製品/設定変更、日次起動。
10. **合否判定**: exit=0と要件内容一致。機械hash一致自体を開始gateにしない。build失敗はFAIL実値。
11. **保存証拠**: `worklogs/projects/implementation/item-desc-capture-20261025/C01/`（将来新設）のsafe build結果・差分・照合記録。
12. **後片付け・再実行**: 無関係副生成差分を採用しない。失敗したsource箇所だけ直し再build。既存他変更を戻さない。

### C02: selector全件・空・不在・片方・複数

1. **対応要件・STEP**: R02,R07 / STEP 2,5。
2. **目的**: 4selectorの全HTMLを切らず状態を区別する。
3. **種別・方法**: Node synthetic DOMをpytestから実行。新設予定node `tests_inago/test_product_page_capture.py::test_description_selectors_preserve_all_html_and_states`。
4. **事前条件・初期データ**: tmp_path、saleのみ/itemのみ/両方/双方不在/空/複数/例外のfixture群、固定clock。既存pageなし。
5. **入力値・操作**: 各説明HTMLに末尾固有文字列を入れ、同じclass複数・両class同一要素も与える。
6. **実行方法・設定**: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_description_selectors_preserve_all_html_and_states`。新設後collect確認して実行。
7. **期待する出力**: 全outer_html文字列の完全一致。既存3種類順維持、item末尾。not_found/empty/unavailableを区別。
8. **期待DB・件数・予約・版・ログ**: DB/予約0。selector4件、各DOM件数とHTML件数一致。fixture/code hashと終了コード記録。
9. **禁止する副作用**: DOM先頭だけ採用、HTML末尾切捨て、要素重複の勝手な削除、実network。
10. **合否判定**: 全fixtureの状態/順/全文一致。例外fixtureは失敗状態保存を期待するため、その仕様一致を合格とする。
11. **保存証拠**: 将来`.../C02/`へ安全な合成pageとnode結果。画面証拠不要（DOM論理case）。
12. **後片付け・再実行**: tmp_pathだけ片付け、失敗したfixture/nodeを再実行。

### C03: 全画像・重複由来・遅延取得

1. **対応要件・STEP**: R02,R03,R07 / STEP 2,5。
2. **目的**: 両領域画像を全件採録し、出現場所と取得1件を区別する。
3. **種別・方法**: synthetic候補列挙＋ネットワークなしPlaywright局所画面。新設予定nodes `tests_inago/test_product_page_capture.py::test_description_images_keep_all_candidates_and_origins`, `tests_inago/test_product_page_item_desc_browser.py::test_item_desc_lazy_images_are_captured_with_visual_evidence`。
4. **事前条件・初期データ**: 合成HTMLにimg/source/srcset/遅延src/background/data/blob、saleとitem同URL・多属性・入れ子、失敗blobを用意。ローカル画像byteは合成。
5. **入力値・操作**: item領域が画面下にありスクロール後に属性/DOMが更新されるfixtureでcaptureを一度実行。前後画面・動画・traceを取得する。
6. **実行方法・設定**: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_description_images_keep_all_candidates_and_origins tests_inago/test_product_page_item_desc_browser.py::test_item_desc_lazy_images_are_captured_with_visual_evidence`。browserはローカル合成画面だけ、外部requestはfake/route。
7. **期待する出力**: 全候補のbyteまたは明示失敗、遅延更新後HTML、重複URLは1画像・由来全件、sale/image順維持。selector/element/DOM/属性を全由来へ保存。
8. **期待DB・件数・予約・版・ログ**: DB/予約0。unique候補件数=page.images件数、全DOM出現をoccurrencesで照合。時計・版・hash・終了コードを記録。
9. **禁止する副作用**: 外部ネットワーク、同URL由来欠落、data/blob無言除外、全体日次、認証情報採録。
10. **合否判定**: 全候補/由来/bytesと遅延状態の期待一致。ブラウザー不可はnot_performedと理由、合格としない。
11. **保存証拠**: 将来`.../C03/`の合成manifest、before/after PNG、video、trace、DOM。取得不可証拠は代替と理由。
12. **後片付け・再実行**: browserを閉じ合成tmpだけ掃除。失敗した遅延/候補nodeのみ再実行。

### C04: 取得関数の局所再呼出しと非破壊再試行

1. **対応要件・STEP**: R03,R04,R06,R07 / STEP 2,3,5。
2. **目的**: HTML成功・画像一部失敗に対し、capture保存関数を局所的に明示再呼出しした場合の非破壊再試行を確認する。通常run resumeの到達試験ではない。
3. **種別・方法**: fake requestと既存/新規capture fixture。新設予定node `tests_inago/test_product_page_capture.py::test_description_image_retry_preserves_manifest_and_legacy_capture`。
4. **事前条件・初期データ**: 新4selector pageの一部画像503→成功、旧3selector captured page、cleanup済みpage、消失blobを別fixtureに用意。
5. **入力値・操作**: 同一run/shop/itemでsaveProductPageInputsを初回と局所再呼出しの計2回直接呼ぶ。orchestratorの通常resumeは呼ばない。旧pageとcleanup済みは保存関数の再利用操作だけ。
6. **実行方法・設定**: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_description_image_retry_preserves_manifest_and_legacy_capture`。新設後collect確認。
7. **期待する出力**: 局所saveProductPageInputs再呼出し時に新pageの失敗画像だけretry、成功画像/HTML/順/ref保持。通常resumeからの再取得保証はしない。旧pageはitem不在と偽らずlegacy未取得、旧ファイルとcleanup記録を保持。消失blobは理由付き失敗。
8. **期待DB・件数・予約・版・ログ**: DB/予約0。既存成功取得の追加call0、旧pageの再訪0、retry前後件数不変。正式run/attemptはfixture値のみ。
9. **禁止する副作用**: 新run作成、旧page/ocr書換え、全体再検索、cleanup済み画像復活。
10. **合否判定**: retry call対象・byte/hash・配列順・状態が一致。失敗候補が残れば失敗として保持。
11. **保存証拠**: 将来`.../C04/`の安全なcall履歴、前後manifest、hash、node結果。
12. **後片付け・再実行**: tmpだけ掃除し失敗した再開nodeを修正再実行、成功済み無関係nodeは繰り返さない。

### C05: 全画像OCR・全文・失敗・再開・旧互換

1. **対応要件・STEP**: R04,R06,R07 / STEP 3,5。
2. **目的**: item画像もOCRへ入り、process_productの局所的な明示再呼出しで途中結果を再利用できる。通常resumeのocr_partial再実行は対象外。
3. **種別・方法**: 偽workerを使う実ProductPageOCR。新設予定node `tests_inago/test_product_ocr.py::test_description_images_ocr_preserves_text_and_resumes`。既存確認済node `tests_inago/test_product_ocr.py::test_every_image_and_exact_returned_text_survive_conversion`。
4. **事前条件・初期データ**: sale+item混在・重複統合済み・一部download不可のmanifest、改行/長文/空/一度OCR失敗の応答、旧v1入力。
5. **入力値・操作**: 実process_productを局所的に明示再呼出しし途中保存から同じ商品を再試行する。orchestratorの通常resumeは呼ばない。1画像は上限エラーfixture。時計固定、実modelなし。
6. **実行方法・設定**: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_ocr.py::test_description_images_ocr_preserves_text_and_resumes tests_inago/test_product_ocr.py::test_every_image_and_exact_returned_text_survive_conversion`。
7. **期待する出力**: 全画像ocr.jsonレコード、正確な全文と改行、empty/error維持、page順/ref対応、hash一致成功/空を再利用、失敗だけ再推論。旧v1 loader成功。
8. **期待DB・件数・予約・版・ログ**: DB/予約0。OCRレコード数=page.images数、evidence_refsは成功非空のみ。版/hash/終了コード記録。
9. **禁止する副作用**: 実engineダウンロード、本文生成、OCRテキスト切捨て、旧データ上書き、上限変更。
10. **合否判定**: 件数/全文/呼出し/ref/status一致。画像上限エラーを期待通り保持することも確認。
11. **保存証拠**: 将来`.../C05/`の合成ocr.json/ocr-raw.json、call回数・hash・node結果。共有は合成のみ。
12. **後片付け・再実行**: worker停止、tmp掃除。失敗nodeと直接関係する変換nodeのみ再実行。

### C06: 作成・独立reviewへの全文入力と明示指示

1. **対応要件・STEP**: R05,R06,R07 / STEP 4,5。
2. **目的**: item_desc HTMLと画像OCRが全AI輸送へ残り、両担当のskillへ明記される。
3. **種別・方法**: copy factory/job stage/promptのfixture検査。新設予定node `tests_inago/test_product_page_copy.py::test_item_desc_full_inputs_and_guidance_reach_both_specialists`。
4. **事前条件・初期データ**: memory DB、旧v1と新4selector page、item固有の末尾識別文字を持つ大容量HTML/OCR、合成input_refs。
5. **入力値・操作**: 実copy factory、prompt builder、AI runner stagingを呼びjob JSONとwriter/reviewerに渡す指示・staged skillを読む。実AIは呼ばない。
6. **実行方法・設定**: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py::test_item_desc_full_inputs_and_guidance_reach_both_specialists tests_inago/test_product_page_copy.py::test_copy_spec_contains_exact_html_ocr_and_all_input_refs tests_inago/test_product_page_copy.py::test_runner_transport_prompt_and_staged_copy_skill_keep_full_guidance tests_inago/test_product_page_copy.py::test_large_payload_is_never_silently_truncated`。後3nodeは既存確認済。
7. **期待する出力**: 全HTML/OCRの完全一致と末尾保持、item_desc/画像OCR明示、両担当へ同じ全入力、参照subset、商品照合/未信頼命令無視。raw engine responseは公開入力に出ない。
8. **期待DB・件数・予約・版・ログ**: fixture外DB/予約変更0、実AI call0。既存selector由来ref維持、版/hash/node結果を記録。
9. **禁止する副作用**: path/要約で全文を代用、実AI起動、投稿、実run作成、根拠ない商品効能生成。
10. **合否判定**: 入力・末尾・参照と指示の一致。実AIが読んだとまでは主張しない。
11. **保存証拠**: 将来`.../C06/`へ合成job-inputとsafe比較・skill差分・node結果。
12. **後片付け・再実行**: memory DB/fixture jobを閉じて掃除、欠落した輸送nodeのみ再実行。

### C07: 連鎖統合・既存正常入力resume・cleanup回帰

1. **対応要件・STEP**: R02,R03,R04,R05,R06,R07 / STEP 2,3,4,5。
2. **目的**: 個別fixtureだけでなく取得→OCR→copy inputを同じデータでつなぐ。
3. **種別・方法**: 実capture関数のNode harness出力を実Python OCRとcopy factoryへ渡す隔離統合。新設予定node `tests_inago/test_product_page_copy.py::test_item_desc_capture_to_ocr_to_copy_inputs`。
4. **事前条件・初期データ**: 合成sale/item HTML・画像bytes、偽request/worker、memory DBに同一商品選定checkpoint。新規と旧v1の別fixture。
5. **入力値・操作**: captureを一回、page loader、OCR、copy factory、job stageを順に実行。item固有HTML/OCRの末尾まで比較。保存済み正常入力の通常resumeと確定後cleanupは既存nodeも回帰する。C04/C05の局所関数再呼出しと区別し、通常resumeから画像取得失敗の再取得・ocr_partial再OCRへ到達する試験は今回計画しない。
6. **実行方法・設定**: `.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py::test_item_desc_capture_to_ocr_to_copy_inputs tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy`。後2nodeは既存確認済。
7. **期待する出力**: captureで得た全HTML/画像のOCRが同一商品のjob-inputへ完全に到達、重複由来とref追跡、既存正常入力のresumeは再検索/再生成なし、cleanup後はpage/ocr保持。通常resumeの画像失敗再取得やocr_partial再OCRをこのcaseから推論しない。
8. **期待DB・件数・予約・版・ログ**: fixture内の期待件数だけを検証。本番DB/予約0、実AI0。新規画像数・OCR数・AI入力数の対応、hash・終了コード記録。
9. **禁止する副作用**: 日次全体・外部ROOM・本番DB・実AI、新runで回避、既存確定本文再生成、旧file破壊。
10. **合否判定**: 同じ入力の連鎖で全文/bytes/ref/状態一致、resume/cleanup不変。未実行は合格でない。
11. **保存証拠**: 将来`.../C07/`へ合成連鎖manifest、OCR、job-inputの比較、前後hashとnode結果。
12. **後片付け・再実行**: fixture worker/DB終了、隔離tmpのみ掃除。連鎖の失敗境界へ戻り局所修正後に最終回帰。

### 最終回帰の予定範囲

下記は計画時点で記した実装後の完全コマンド。現在の実行結果は、実際に使った最終コマンドをC07/result.jsonとexecution.mdへ保存した。

```powershell
.venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_inputs.py tests_inago/test_product_page_url_safety.py tests_inago/test_product_ocr.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_product_page_cleanup.py tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py
```

既存実engine/model依存nodeが環境条件によりskipや未実施になったときは、その理由・対象を明示して件数を分ける。fake workerの結果を実engine成功と表現しない。上記範囲外の178case等の過去結果を今回版の合格証拠に流用しない。

## 11. 導入・停止・復旧・最終報告

今回は計画成果物だけを親へ渡す。後日実行では仕様を先に更新し、取得・OCR・入力の差分、局所・回帰・独立製品reviewを記録して固定rootに反映する。通常起動の仕組み・Scheduler・設定を変更しない。旧データを移行しないため、新規captureから適用され旧runは既存入力で再開する。通常run resumeのcaptured page再利用・ocr_partial skipは維持し、局所関数の明示再呼出しによる画像再試行とは区別する。

復旧は未確定の新規差分だけを対象に、担当差分と基準版の比較から修正する。reset --hard、checkout、履歴書換え、force pushを使わない。旧データ・確定本文を巻き戻さない。個別環境失敗では対象操作の理由と再開位置を残し、独立reviewと最終報告を継続する。

最終報告は「仕様更新」「取得」「OCR」「AI入力/skill」「局所」「回帰」「独立review」「全体run未実施」「未解決」を分けて実値で記す。親は完了直前にgit status --short、所有差分・機械検証・Secret混入を確認し、今回所有fileだけを意味ある単位でcommitして現branch追跡先へpushする。成功branch/commit/remote、失敗や禁止なら保持理由を報告する。担当はGitを実行しない。

## 12. 未確定事項・判断・影響

| 項目 | 判断・未確定・影響 |
| --- | --- |
| 新仕様の正式版番号 | 現行2.61からの実行時点版を確認して採番する。今は次番号を確定しない。既存依頼や他差分を上書きしない。 |
| 実商品でのitem_desc出現・全画像種類 | 本人の観測を要件入力とし、今回は画面未確認。合成caseで契約を検証し本番観測済みとは言わない。 |
| data/blob | 現行無言除外は新要件の「全画像」に合わないため、取得または理由付き失敗を計画。blob消失時は復元成功を保証しない。 |
| 遅延読込完了 | 同期方法は局所browser fixtureで決める。固定sleepだけの追加や無限待ちを採用しない。未完了状態を記録する。 |
| 旧run | 自動補完は行わず既存入力を非破壊維持。item_desc key欠落をDOM不在とみなさない。旧runへの追加取得を望む場合は別の明示依頼の範囲。 |
| schema/DB | 現行loader/動的selector対応からschema_version=1加算拡張で十分と判断。AI応答schemaとDB migrationは不要。 |
| 再試行の範囲（確定判断） | capture保存関数とprocess_productの局所的な明示再呼出しのみ。通常run resumeからの失敗画像再取得・ocr_partial再OCRは今回対象外。現行orchestratorのcaptured即返却/page欠落時再取得/ocr_partial skipを変更せず所有外とする。このため画像失敗が通常resumeだけで回復するとは保証しない。将来必要なら同run未確定checkpointからの呼出経路を別計画で扱う。 |
| OCR変更量 | 全画像ループが既存なので製品差分0の可能性がある。fixtureで不足が実証された場合だけ最小修正。 |
| 性能・実model・browser | 未実測。画像増分・処理時間を結果に残す。利用不能な個別操作を記録し独立caseを進める。 |
| 入力全文量 | 今回も全文を切捨てない。実際の輸送上限に当たる場合は失敗と対象を保存し、要約で成功に見せず実行可能な部分と報告を続ける。 |

## 13. 機械確認と独立レビュー

計画作成時の機械確認はJSON parse、0～14節、STEP 5件×14項目、case 7件×12項目、要件/fact/STEP/caseの全件対応、現行根拠pathと既存node関数の実在、計画時点の許可出力を対象にした。これは製品試験とは別の計画品質確認だった。後続の本人指示により別途製品実装・build・browser・pytestを実行した結果をC01～C07に記録する。

独立計画reviewは親が別担当へ実起動し、本計画全文と対応表、根拠・所有・予定nodeと既存node・全文prompt・範囲・未確定を確認させる。reviewerは本計画を編集せず、指摘は計画担当へ戻す。review statusは観測値であり後日工程のgateではない。製品独立reviewはSTEP 5で将来実施する。

## 14. 実行記録欄

| 区分 | 現在の記録 |
| --- | --- |
| 調査 | AGENTS、プロジェクト全体マップ、planning skill/契約、仕様生成経路、取得/OCR/AI/skill/試験を読取確認。基準HEAD 2eb41c272677ac5f906c081350e09f858ffb252c。 |
| 計画作成 | 本書と対応JSONを作成。1.1で独立reviewのP2指摘に対応し、再試行を局所関数の明示再呼出しに限定、通常resume挙動変更を対象外と明記。計画機械確認と独立レビューの実結果は親の作業記録へ。 |
| 仕様STEP 1 | completed / spec v2.65、HTML build exit 0。 |
| 取得・OCR・生成STEP 2～4 | completed / 19 capture/browser/URL safety cases; OCR-to-copy integration and skills verified. |
| 局所・回帰STEP 5 | partial / local suites pass; final planned regression 82 passed/4 failed/0 errors; C07 records exact commands and nodes. |
| 画面・動画・trace | captured / local synthetic browser fixture; C03 evidence contains before/after, video, trace. |
| 日次全体・外部操作 | not_performed / 本計画の実行範囲外。 |
| Git | 親担当が所有。担当側でstage/commit/push未実行。 |

実行記録には操作名、対象node/run、コード/仕様版、正確なコマンド、期待/実結果、終了コード、証拠参照、変更/未実施理由を追記した。controller由来identityは存在しないのでnullと理由を維持する。将来の成果を現在の実績へ書き換えない。
