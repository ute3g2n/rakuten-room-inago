# 新規フロー完遂計画 v1.1 独立再レビュー

判定：**changes_requested**。前回FCP-01～03は解消。新たな修正要求はmedium1件。

- 対象版1.1：`plans/projects/maintenance/自動不具合対応フロー完遂と全工程追跡_実行計画_2026-10-19.md`
- SHA-256：`e8846c3a875125004bdb2e4a9db590f0d1d954b5355adbc0687b605da617bbd6`。供給値と一致。
- 確認時刻：2026-10-19 22:26:32 JST。reviewerは`/root/flow_completion_plan_review`、指定effort high、実model/effort/tierは未観測。
- v1.0保存snapshotのSHAも`9aa136c5…063f81`と一致。旧reviewは保存する。
- 同一10/17 job/launch、source_run_id=null、対象日2026-10-17を維持。formal review attempt/base_commitは未割当で推測しない。

## FCP-04：FC07に差分存在を条件とする起動文が残る（medium）

位置はFC07第4項429行、第5項と全文promptの開始/入力443行。第4項は「FC03～06の利用可能差分がある時点」であり、入力も変更差分を前提とする。差分0・実装失敗・無変更fallback時には、文字どおり読むと試験担当を開始できない。第4.2節は差分0でも成果物整合を実際に検査すると定め、実質省略は実装だけという本人要件にも反する。

FC07の開始・入力・全文promptを「利用可能な差分、無変更説明、controller fallback/handoffのいずれか」で開始する文に直し、差分存在の前提を除く。差分0なら成果物/schema/引継ぎ/報告データを実際に検査し、pytest対象なしや実行不能な個別検査の理由を残す。空PASSは作らない。FCT04へ無差分時のtester実起動assertも明記する。

対応はR03/R05 → FC07 → FCT04/FCT13。ownerは計画作者、実行controller/tester。成果は修正開始条件/入力/promptと実起動・意味ある試験結果の期待。指摘を作成担当と次担当へ渡し、review判定を工程のgateにしない。

## 前回指摘と全文の確認

FCP-01はFC08の459/473行で解消。差分0・試験証拠なしでも利用可能結果/無変更説明/fallbackを渡し独立reviewerが実起動する。FCT04/FCT06にも起動assertがある。approvedの捏造を禁じる。

FCP-02は第3.1節の所有表、FC02～06/09の第6項と各全文promptで解消。schema/sql実在path、日次3file、12skill契約file、9role、文書/図/生成物の書込所有を具体化し、FC02のfile-operation-owner-mapをFC03/FC10が読む。共有fileは順次編集、5つの新module/schemaは不存在の予定と区別する。仕様変更STEP内で正本HTML内source・受入/辞書/追跡/ログ・skill/role/template/図を同期し、FC09は最終照合とする。

FCP-03は既存max5roleをbaselineとして記録し、FC02でTOMLと内蔵指示をhighへ同期する契約により解消。同期前からhigh指定worker/defaultで実起動する指示は全11promptに伝播している。モデル等の未観測は開始条件にしない。

15節・11STEP各14項目/全文prompt・13case各12項目、全requirement/fact/STEP/case/owner/output/reviewer/evidence対応を再確認した。既存14完全nodeはv1.0と同じ定義実在のもの。新nodeは未作成として区別し、試験/collectionは実行していない。想定外制御文字0件。source実在確認では新規予定5path以外の既存sourceを確認した。home `.codex/AGENTS.md`参照をrepo fileと誤認しない。

SQLite分類・constraint・transaction・proposed/persisted runの既存実装を不足計装と分離し、共通基盤traceを実績作りで遅延せず、重複追加しない指示が具体化された。今回新差分・focused試験・独立製品review・適用・同一job新eventを実測する受入は維持される。基盤候補をjobが検証/正式適用する経路でも、その差分をjobが新規作成したとは称さない。

同一job自身の全成果/review/公開/終端の永続結合、外側HTMLによる完遂代用禁止、元障害unknownとフロー完遂の分離、全失敗境界の具体相関traceとfallback/recovery、物理限界の正確な記録、意味あるhandoffと後続role実起動を維持。平易HTMLは既存文章基準・本文5問・独立review/著者修正・最小renderer・自己review/publicationのsidecarを維持する。10/19非処理、Daily手動/追生成/重複job/別workspace禁止も保持する。

本reviewは計画だけを扱う。製品・test・DB・本番・旧証拠・計画を変更せず、担当review2fileのみを作成した。試験実行0。review結果と不足を後続gateにしないが、今回依頼はPLAN ONLYなので実装/本番再開を開始しない。Git処理は親controllerが担当する。
