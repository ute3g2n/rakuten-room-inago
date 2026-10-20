# skill直列化計画 v1.0 独立レビュー

判定：**approved**。新規の修正要求は0件。以下の対象bytesについて計画として承認する。製品実装、試験合格、本番適用、同一job実動、障害解消を承認したものではない。レビュー状態は後続担当の起動条件にしない。

- レビュー担当：作者と別の `/root/skill_chain_plan_review`。reasoning effort要求high。実model/effort/tierの独立観測は未提供。
- 確認日：2026-10-20 JST。唯一の作業root：`C:/project/rakuten-room-inago`。
- 許可出力：このMDと `plans/projects/maintenance/reviews/skill-chain-refactor-v1.0-review.json` のみ。計画、対応表、製品、試験、DB、設定、本番を変更していない。

## 対象版とidentity

| 対象 | 版・bytes | SHA-256 |
|---|---|---|
| `plans/projects/maintenance/保守フローskill直列化と10月17日実動完遂_実行計画_2026-10-20.md` | 1.0 / 227,995 | `001b904b786c1dea6c95b54a307f4e2ace60358f850308e0566c164a9b7afaf5` |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/requirement-step-case-map.json` | plan_version 1.0 / 150,899 | `643fe0b00d12a6a4057ca74c55feaaf6bc652ecbe3e335a24db3bf779c88e1ef` |
| `docs/architecture/保守フロー_skill直列構成案_2026-10-20.html` | 1.1 / 52,900 | `0177ae92e0981eb1ab179b49d04739de9ffbfbc056daa3e7f7be52b79a28b3ec` |

供給identityはjob `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、対象日 `2026-10-17`、source_run_id `null`。incidentは供給値 `352492da-ac58-4680-b1fd-fe5f24000229`。正式planning/review attempt、controller指定base_commit、完全refsは未供給。これらを現在HEADや過去attemptで補っていない。親が今回の許可出力2pathを明示しており、不可欠な対象版を受領して実レビューした。

## 照合結果

| 観点 | 確認した位置と結果 |
|---|---|
| 0〜14節 | 全15節が順に存在。第0/14節は今回が計画作成のみで将来SC01〜10はNOT_RUNと明示。 |
| STEP・全文prompt | 第9節のSC01〜10は各14項目。10個のSTEP promptと8用途promptは実文で、root、対象、入力/版、開始、所有、制約、作業、検証、証拠、Git、次担当を持つ。 |
| ケース | 第10節のSCT01〜14は各12項目。最新稿ではcase別の初期状態と期待DB/ファイル/ログ変化が具体化された。13個の新nodeは新設予定・未collect、SCT09は実job操作でnodeを捏造しない。既存10nodeはinventoryの定義確認と未実行を区別する。 |
| fact・所見の対応 | 原fact13＋F-SC7＝20fact、7要件、10STEP、14case、20個の集約mappingを照合。全IDを網羅し、evidence、ownership_refs、owner、output、reviewer、実行後証拠先を持つ。原調査reviewの限定3件も個別に要件/STEP/case/担当/成果へ対応する。 |
| 最小構成 | 第3〜5節、SC02〜04、SCT01/02/08/14は6skills/8用途、9MD、薄いrunner1本、次起動3項目、LLM本文直接保存を維持。旧controller/production_workflow/workflow_provider/planning/repair/reporting制御の本番import/callを外し、旧工程別schema/state/registry/A-Bセッション/receiptを再構築しない。 |
| AI実接続 | 第3/4節、SC03全文prompt、SCT08とmapの用途別request routeで、modelはthread/start・turn/start、highはturn/start。Fastは実在するsubprocessの`--strict-config -c features.fast_mode=...`とBの`service_tier="priority"`を使い、架空payload fieldへ置換しない。 |
| 観測と自作gate除去 | model catalogとfast featureの両probeを新runnerのみadvisoryとし、実provider拒否と未観測を区別。第0節、全18prompt、SC04、SCT03/05/08/11はreview/test/readiness/release、unknown、欠測、FAIL、回数等で後続を止めない。 |
| ログ保存失敗 | SC03/04とSCT04はAppServer内部の正常AIに対する5種のlog-only OSErrorを扱う。小さい新runner専用best-effort処理で同じcallの正常final_textを維持しstderr/独立予備保存を試す。全sink喪失は復帰時unknown区間として残す。既存C26 timeout-cleanup結果だけを正常AI継続の証明に使わない。 |
| 所有と権限 | SC02〜09は具体path/hunkを役割へ割当。reviewerはworkspaceWriteで指定reviewMDのみ保存し製品/試験/HTMLを編集しない。deliveryはnetworkAccess:true/approvalPolicy never。OS書込制限の証明、host receipt、C34を要求しない。 |
| 同一jobの日次対応 | SC08/10、SCT06/09/11は同job担当の日次新差分→隔離実ログ→別製品review→本番適用版→同job新証拠を要求。外側保守差分、既存計装の重複、過去PASS、job.complete、公開のみを達成へ流用しない。三不足候補は10/17原因と断定しない。 |
| 本文と公開 | 第6節、SC09、SCT10/12は作者LLMの直接作成/修正→別reviewerの修正後実物読取→同bytes公開を記す。本文reviewは4点の事実/平易さと秘密露出のみ。未承認を承認済みとせず同jobで再作業する。pushと適用、公開と対応達成、workflowとincident解消を分ける。 |
| 範囲・同期・復旧 | SC02/04/07〜10、SCT12〜14に要件/生成物/skills/roles/運用同期、所有hunk選択Git、現在trackingへpush、旧raw履歴・10/19 queue/history/除外維持、同job明示再開を割当。Daily手動、新job/run代替、下書き追生成、13時待機、別workspaceは禁止。 |
| 基準版の精度 | 第3/12節とbaselineはactive6b6と開始HEAD fc8bを別観測にする。比較対象はコード6件。5件raw一致、production_workflow.pyは改行差で正規化後一致であり、全raw一致/7製品と主張しない。将来適用時は再採取する。 |

## 原調査と継続する限定

原調査 `u23-08-update-v2` と独立review-v2（2026-10-19 20:49:37 JST、approved）を原文で読んだ。調査v2本文に残る「再review未実施」は作者時点の記録で、別の後発review-v2実施記録と分けて扱った。原因、run未保存理由、実投稿件数、回復、再発防止はunknownを保持する。

| 既存所見/欠測 | 位置・範囲 | 次担当へ渡す内容 |
|---|---|---|
| IR-U23-03 | 原review-v2限定節、計画第2/12節、map.preserved_review_limits | 歴史原snapshot7件未再読、F008 hash unknown。供給値との照合範囲を独立原証拠検証へ広げない。SC01/08/09/10、SCT09/10/11へ。 |
| IR-U23-04 | 同上、原調査第5/9章 | U001〜U004原記録は原調査reviewer指定外。過去PASS/approvedと今回実job証拠を分ける。SC01/08/09/10、SCT06/07/09/10/11へ。 |
| IR-U23-V2-01 | 同上、S04の時点対応 | 20:36のrunning/resume7と後続追記、今回baselineを混ぜない。SC01/08/09/10、SCT09/10/11へ。 |
| 正式ID・base・完全refsの欠測 | 計画第0/12節、このreview JSONのinformation_gaps | null/unknownと理由を維持。今回確認したSHAと供給identityを使い、値を捏造しない。 |
| 実model/effort/tier等の未観測 | 計画第4/12節、用途別map | 要求値から実観測を推定しない。実起動を試し実拒否だけ個別操作失敗として渡す。 |

これらは計画の開始/継続を止める修正要求ではなく、明示的に保持された根拠の限定である。

## 実施と引継ぎ

最終対象bytesでJSON parse、全節/全項目/18promptの必須文脈、identity/nullness、全ID網羅、20集約mappingの参照・所有・出力、8用途の要求値/実観測nullを読取検査し終了コード0。途中の確認スクリプトは旧355行形式の`step_id`および実キーと異なる`request_route`を仮定してKeyErrorになったため、実際の20集約mappingと`fast_request_route`/`effort_request_route`を読んで確認し直した。この確認スクリプトの失敗を計画の欠陥や製品試験FAILへ転記していない。

製品pytest、collect、実AI接続、DB操作、Daily/Worker/resume、適用/公開は今回の計画レビュー範囲外で未実施。新構成の実装完了や同job日次対応達成は未確認のまま。

親controllerへこのreview2ファイルと上記限定を渡す。今回の次作業は計画作成記録と所有成果だけのGit確認・commit/pushである。将来の実行が割り当てられた場合はSC01から利用可能入力で開始し、承認・試験・証拠・原因unknownをgateにせず内容ある成果とhandoffを保存して次担当を実起動する。実行不能な操作だけ理由付きnot_performedとし、他工程・報告・公開・同job再作業を続ける。レビュー担当は計画作成のみという今回の範囲を越えて製品担当を起動していない。Gitは共有変更を混ぜず親controllerが所有ファイルを選択する。

## LF正規化の差分レビュー追補

親のGit index照合でCRLF/LF差を検出したため、計画・対応表とbaseline-snapshotをLFへ統一した最新版へ対象SHA/bytesを更新した。文書版は1.0、判定approved、新規修正要求0件を維持する。今回の再確認範囲は改行差と対象版の更新である。

現在のLF bytesの全LFをCRLFへ逆変換してSHA-256を計算したところ、計画は旧`a949129c0989b8c9aef21d375875634ac2c837bd5db074e135fe8ab3a2530fa5`、対応表は旧`3d4813722daf7e6e79f4677ef0d15fae9befa903a655b9777673ab7b60ba3662`、baseline-snapshotは旧`bf3ad1694f65b1c51f501798dbf71ee5d9d105c0215accc48d499acca18557b2`とそれぞれ完全一致した。計画1,134 bytes、対応表3,651 bytes、baseline157 bytesの減少はCR除去だけであり、本文とJSON値の変更はない。baselineの現SHAは`c912038ebf3b78fa11e77c71c8c64d40becf2c2f74ebb5ddbf9d1f38a21921b5`。

レビュー2ファイルもUTF-8/LFで保存した。Git stage後の対象blobとreview.targetsの再照合、選択commit/pushは親controllerへ渡す。判定を後続起動gateにはしない。
