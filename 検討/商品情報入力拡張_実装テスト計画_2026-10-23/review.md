# 商品情報入力拡張計画の独立レビュー

- 結果: **計画上の残る所見なし**。実装・試験・本番の成功を示すものではなく、後続開始の承認条件にも使用しない。
- 対象: 同フォルダの `plan.md`、`requirement-step-case-map.json`、`result.json` のv1.0、2026-10-23 12:04:09 JST観測版。
- 担当: `/root/product_ocr_plan_review`。計画担当と別agent。固定rootでread-onlyレビューし、本レビュー2fileだけを作成した。追加subagent、製品変更、試験実行、Scheduler・DB・設定操作、本番起動は行っていない。
- 基準HEAD: `1739fb24168efbd07adb12e363074499bd2a1797`。

## 対象版の記録

|file|SHA-256|
|---|---|
|plan.md|68AD67EC7DD3524346A05F0388F055A4AAC30406078A45116B8E5823B432FF3D|
|requirement-step-case-map.json|9BB65B295FAB2FCCCC009B2ED24ADD4612FC40B573F18C6C98E6D69970B440C7|
|result.json|8F3A7F5EAC4C81AD574B42673862DA66FED82E212CDA93532E4CAACC543CF163|

digestは読んだ版を特定する記録であり、開始・完了・受入の条件ではない。親が後で実行記録やreview状態を追記する場合、その追記はこの観測後の版となる。

## 確認範囲

プロジェクト全体マップ、common-execution-planningのSKILL.mdとcontracts.md、原典HTML、OCR技術検証・変換報告、実装予定箇所の現行ソース、既存試験関数の定義・parametrizeを照合した。計画全文、各STEPの全文prompt、全ケース、対応表を確認した。

0〜14節の15章、11STEPの各14項目・全文prompt、27CASEの各12項目があり、16要件・19fact・19evidence・11STEP・27CASEに対応がある。親の構造・参照点検とも整合する。既存24件はソース上の実行可能な関数selectorであり、pytestは未実行。REG15・REG21等のparameter IDを含む展開済み完全nodeは未収集で、実施時collect結果へ記録する計画になっている。新設予定nodeを既存・合格済みとしていない。

|観点|確認結果|
|---|---|
|原典5工程|3要素全matchの無マスクouterHTML、全画像候補・出現順、選別、全OCR、実内容入力、商品commit後cleanupに担当とケースがある。旧マスク済みHTMLを全文一致の正解にしない。|
|状態・枠・再開|committedとactive_selectedのunion、テーマ待ち枠、生成失敗の枠解放、11件/0件供給枯渇、checkpoint中断を明記。1〜14件のdaily statusはpartial、不足数はshortfall_countと区別した。|
|OCR|検証版CPU/japan/PP-OCRv6 medium/oneDNN無効、モデル一回、全画像・全文改行・success/empty/error、正式ASCII資材、日本語画像path、59件原本投影と再OCRを区別。|
|AI入力|実factoryからai_runner._stage_job、transport、staged copywriter、委譲inputまで全HTML/OCR/ref/追加指示を確認するケースがある。パスだけの入力、全文二重包み、事前分類・要約・切詰めで代用しない。|
|保持・削除|選外folder削除のrun全体keep集合、commit後imagesのみ削除、page/ocr/URL/ref保持、commit直後クラッシュ・cleanup失敗の削除だけ再開を明記。|
|正式起動|一回skipと翌日予定、初回登録task起動、対象日/manual/再開の正式伝達、core/monitor/preflight失敗summary、論理対象日と実時刻、非同期新launch・child PID・実run監視を具体化。|
|正常終端|15件とproduction_daily_exit_codeの従来正常条件に加え、実更新版・実AI入力・OCR runtime・cleanup証拠を求める。受付exit0やlauncher.completeだけで代用しない。|
|継続・所有|本人専用方針と停止条件が全promptへ伝播。review/unknown/証拠/Git等を開始gateにしない。実対象identity・稼働owner排他を区別し、製品と試験証跡の独立レビューを計画。テストコード独立レビューは設定しない。|

## 修正済み所見

1. **実AI送信への追加指示不足**: prompt_builder更新だけでは本番送信を証明できなかった。STEP05にai_runner._stage_jobとstaged copywriterの所有を追加し、C17にtransport bytes/job-input/子skill/委譲inputの照合を追加した。
2. **原因unknownで再開後の旧エラーが正常終端を妨げる問題**: 現行orchestratorの`resumed and cause_resolved`一括退避と、未回復active影響を区別する変更をSTEP07/C25へ追加した。元FAIL・原因unknownは履歴へ残し、再成功または補充commitの実回復だけを相関付きで記録する。全消去やcause-resolved偽装で正常を作らない。
3. **跨日の対象日・13時待ち・core独自日付**: STEP07にmanual、recovery.pyとdaily_pipeline.pyの所有を明記。C24で10/24 09時の10/23再開、preflight失敗summary・monitor・trace、実時刻の非改変を検証する。
4. **非同期起動の完了誤認**: STEP10/C26に新launch/starttime/child PID/source_run_idを捕捉し、実run_id判明後は正式status --run-idへ固定する手順を追加した。
5. **15未満供給時の入力・期待値不足**: C10を15件から1失敗補充、11件枯渇、0件枯渇の3条件へ具体化。11件時は全11件OCR/copy・11commit/予約・partial・不足4、0件は呼出0・shortfall・不足15とした。STEP03のselected15条件は供給十分時に限定した。
6. **参照・非公開読取の曖昧さ**: E12/E16の実関数、最大128文字のref条件、関数selectorと展開済み完全nodeの区別を具体化。今回許可された59画像/raw/HTML/実AI入力の私有照合が禁止文で妨げられないよう明記した。

以上は計画への修正を確認した結果であり、製品への実装済みを意味しない。

## 未確認と引継ぎ

Scheduler一回skip、依存導入、新設node作成・実行、正式対象日引数の実装、実AI入力とcleanup、本番正常完走はすべて未実施。実run/launch/job identityはnullのままであり、将来の正式入口の採番を使う。所要時間・現商品lazy属性・実モデル上限・外部環境・生成cycleの最小schemaは実施時に測定・確定する。

親は計画・対応表・結果と本レビューを含む今回所有fileだけを確認してGit処理し、既存privateデータを混ぜない。レビュー担当はstage/commit/pushを実行していない。
