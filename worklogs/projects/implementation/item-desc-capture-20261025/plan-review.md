# item_desc取得追加計画の独立レビュー

- review date: 2026-10-25 JST
- reviewer: 親とは別の独立read-only担当 `/root/item_desc_plan_review`
- 固定root: `C:/project/rakuten-room-inago`
- 現在の対象: `plans/projects/implementation/item-desc-capture-20261025/plan.md` 版1.1、および同階層の`requirement-step-case-map.json`
- 調査基準HEAD: `2eb41c272677ac5f906c081350e09f858ffb252c`
- 現在の対象plan SHA256: `d2681315ddff5df9f98657c2b93fadc8685d42b7ad23d86f13ccdf8107f41c65`
- 現在の対象map SHA256: `81fc8f839b9cfd3d72ca4638f27af705a252fd9776f348afafe864e449021f36`
- 現在のreview status: `approved`（計画内容の読取レビュー結果）。PR01は1.1でclosed。製品実装・試験合格や後日の開始・再開・後続工程のgateを意味しない。
- 今回の本人依頼は計画のみ。実装・仕様更新・試験の合格を表す記録ではない。

## 版1.1の限定再レビュー（2026-10-25 JST）

PR01に直接関係するR04、第5節、F04、STEP1/2/3/5の作業・全文prompt、C04/C05/C07、第11/12/14節、JSONの`policy.retry_scope`とcaseの`execution_boundary`を読取確認した。再試行の保証は`saveProductPageInputs`/`process_product`の局所的な明示再呼出しに統一され、通常run resumeにおけるcaptured page即再利用、page欠落時取得、ocr_partial skipは変更も回復保証もしないと明記されている。orchestratorを変更所有へ追加していない。この範囲の具体化でPR01の過大保証は解消したため、指摘をclosedとする。追加の要件や通常resume修正は求めない。

本人依頼の通常成功フローは維持される。ROOM遷移時の既存保存関数へitem_desc取得を追加し、その全画像を既存page.images全件OCRへ渡し、既存の動的selector/全文OCR投影を通して同じ全入力をwriter/reviewerへ渡す設計に変更はない。C07もこの正常連鎖と既存正常入力のresume/cleanupを扱い、画像取得失敗の通常resume回復とは区別している。本人の「今回は計画だけ」、将来の仕様更新first、全STEP/case未実施も維持されている。実装時の挙動はまだ検証していない。

初回レビュー対象版1.0のplan SHA256は`f3cfc7e5e51b2c09c6fc78690ae9608fd8f558846a7043fe46216aeac217cce1`、map SHA256は`a24a60267d686df5308093991cb14cd1ff20f4ba2ad2f697cac1fe91a878040b`。以下の初回所見と確認表は履歴として保持する。再レビューで実施したのは対象箇所の読取・JSON parse・対象2fileのSHA256取得と本記録更新だけであり、実装、pytest/collect/build、ブラウザー、ネットワーク、DB、Gitは実行していない。

## 初回所見（版1.0時点の履歴）

### PR01 [P2・1.1でclosed]: 画像取得失敗からの再開が、通常の呼出経路から到達できる計画になっていない

対象は計画82、147、152、284～295行。新しい4selectorページの失敗画像を、保存済みmanifestから成功HTML・画像・順序・refを保って再取得する方針自体は適切。ただし、変更所有は主にJSの`existingCapture`判定で、予定C04は`saveProductPageInputs`を直接二度呼ぶfixtureである。通常の同一run再開から、このJS関数へ到達する箇所が計画にない。

実sourceでは`automation/inago/orchestrator.py:3281`の再captureはpage欠落時に限られ、同`:3474`～3475は`html_state=captured`なら即既存pageを返す。また同`:3822`～3827は`ocr_partial`も再OCR対象から外す。`automation/inago/adapters/public_sources.mjs:1420`は直接capture入口でも先に商品画面へ遷移する。したがって、計画82行の「画面を再訪しないで再試行できるHTTP/data」やR04の「失敗後は同一対象の未完了操作だけ再開」を、JS単体の変更だけで実運用の再開まで満たすとは判断できない。

必要な具体化は、再開をどの実入口から行い、失敗画像判定・HTTP/data再取得・OCR未完了処理をどう接続するかの明記。通常resumeを含むなら、その呼出経路の調査と必要箇所だけの所有候補を追加し、同一runで1画像取得失敗→保存checkpointから再開→失敗画像だけ取得→成功OCR再利用、を隔離caseで確認する。局所関数の手動再呼出しまでを対象とするなら、その限定と通常resumeで未対応となる範囲を明記し、成功扱いにしない。旧run非破壊・確定本文再生成禁止・cleanup済み画像再取得禁止は維持する。日次全体や本番サイトを試験する必要はない。

## 確認結果

| 観点 | 読取確認結果 |
| --- | --- |
| 構造 | 0～14節の15節、5STEP×14項目、全文prompt5本、7case×12項目を確認。省略記号によるpromptの代用なし。 |
| 本人の範囲 | 今回は計画のみ、STEP1～5/caseは未実施、製品試験結果はnull。将来の最初のSTEPが正本仕様更新で、その後実装・テスト。 |
| 仕様生成 | `tools/docs/inago-requirements/README.md:3`、`document-source.mjs:29`以降、`build.mjs:9`と306、`finalize-html.mjs:39`を確認。正本HTML内source 2.61、loadDocumentで参照資料を現pathから読み直し、build/finalizeで表示とsourceを出す具体経路に一致。 |
| 全HTMLと状態 | 既存3selectorの順を維持してitem_desc追加。sale/item双方・片方・不在・空・複数・例外と、全outerHTML無切捨てを計画。 |
| 全画像と由来 | sale→itemの既存順維持、URL重複と出現場所を分離、selector/要素/DOM/属性の全由来を保持。遅延、srcset、背景、data/blobを取得または明示失敗として扱う。新上限は追加しない。 |
| OCR | `product_ocr.py:481`、527以降の全画像ループ、575以降のhash再利用、633以降の逐次保存を確認。item由来をpage.imagesへ加える既存ループ流用の判断は合理的で、不足実証時のみ最小製品変更。OCR再開単体と通常resumeはPR01で区別が必要。 |
| AI入力 | `production_runtime.py:775`以降はselectorを動的展開、825以降は全OCRを投影。`prompt_builder.py:311`以降、`ai_runner.py:949`以降、writer/reviewer/文型ルールの3種類列挙を同期する所有がある。両担当へ同じ全原文・全文OCR、商品同一性と既存参照を保持する計画。 |
| 旧互換 | `product_page_inputs.py:105`以降はschema_version=1、identity/ref照合でselector固定なし。新4selectorの加算拡張、旧key欠落を未取得と扱いDOM不在と混同しない。旧page/ocr自動更新・遡及取得・DB移行を行わない。 |
| 所有 | STEP4→5の同一copy試験fileは引継ぎ後の編集と明記。製品の条件付所有も明記。PR01の通常resume接続が必要ならその所有が未割当。 |
| 対応表 | JSON parse成功。R01～07、F01～09、STEP1～5、C01～07の対応あり。最新版のSTEP別caseは本文に一致。factの参照path/行が実在し、主要処理内容を実sourceと照合。 |
| 試験node | 既存確認済み6nodeは関数定義をread-only確認。新設予定nodeは`proposed_not_created`/未実行と区別。collectも未実行。C03の合成browser証拠は将来取得であり本番画面証拠と偽らない。 |
| レビューと停止条件 | テストコードの独立レビュー工程なし。独立製品reviewを将来STEP5へ割当。本人1名ローカル方針・high指定・gate禁止は各STEP全文promptへ伝播。不要なapproval gate、保持日数、性能ノルマは追加しない。 |

## 実施した確認と未実施

実施したのはAGENTS、プロジェクト全体マップ、planning skill/契約、計画全文・対応表、仕様生成経路と正本内source、取得/OCR/入力/skill/既存試験の読取、PowerShellによるJSON parse・節/項目/prompt数・根拠path/行範囲・既存関数定義の確認、計画2fileのSHA256取得。この記録以外は編集していない。

pytest、collect、文書build、製品実装、仕様/skill/テスト編集、ブラウザー、ネットワーク、DB、Gitは実行していない。先行178caseの結果を今回の新機能合格証拠に転用していない。`settings/language.md`は固定root内に存在しないことを読取時に観測し、日本語で記録した。

親担当にはPR01の実source参照と必要な具体化を通知した。計画本文の修正は作者が所有し、修正版を受け取った場合は指摘箇所と直接関係する対応のみ再確認する。
