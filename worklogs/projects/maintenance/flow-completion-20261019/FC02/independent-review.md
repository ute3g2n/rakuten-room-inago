# FC02 独立レビュー

判定は **changes_requested**。指摘1件（medium）。後続FC03の開始条件にはしない。

reviewerは `/root/fc02_review`、指定effort high、作者と別agent。確認時刻2026-10-19 23:02:15 JST（14:02:15 UTC）。対象はfc02-contract-v1、HEAD `51cfc7503775a25af731c8f4bcf727c94f10a595`、承認計画1.2原文SHA `e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381`。全対象ファイルと成果物の今回SHAはJSONに列挙した。

FC02-R01: `.codex/agents/common-tester.toml` 11–12行にpytest collect一律禁止と「U20全回帰以外でPASSを再実行しない」が残り、`.agents/skills/common-testing/SKILL.md`27行にもcollect禁止が残る。同contracts.md16行のfocused pytest/最小collection、今回FC07の新node実在確認と矛盾する。旧U20制約を直接削除・置換し、計画指定のfocused node/最小collectionと変更版に必要な再確認を認め、無関係な全suite・根拠のない反復だけを除く。所有表・検証結果・hashも更新する。FC-R05/R10、FC02/FC07、FCT13に対応する。

32 tracked差分はwrite-intentの対象集合と完全一致し、実diffは宣言した契約同期範囲内。9 role TOMLは全てhigh、5件の元maxはbaseと一致し、内蔵effort maxはない。全12skill/contractsを読み、source_run_id nullと固定identity、future blocked禁止、通常/fallbackの必須内容、次担当実起動、operation/publication/workflow/incident分離を確認した。上記tester制約以外に、新たな承認・receipt・lease・回数やworktree作成条件、report statusからincident resolvedへ結ぶ現役指示は認めなかった。旧履歴の記述は保持される。

85 operation owner-mapは実在/予定とfile境界・順次所有・output/reviewer/evidence/caseを持つ。FC-R/A10件、fact10件、STEP11件、case13件の対応を確認。原計画snapshotは0～14節、各STEP14項目、各case12項目、全文promptを保持する。現計画の変更は第14節実行行と履歴区分だけであり、FC01行を維持してFC02実績と未達が記録される。

全synchronization after-SHAとhandoff12出力SHAは現bytesと一致した。result/handoffのfinished_atはいずれも `2026-10-19T14:00:07.285227+00:00` で、13:59のmachine-checksおよび実行記録と矛盾しない。初期に懸念された終了時刻不整合は最終版では解消している。F12 XML parse成功、他11SVGはHEADとbyte一致、git diff --checkはexit0。HTML2.50/28章/39図/26同梱/4042174 bytesとbuilder exit0は作者記録・現在hashに結ぶ。reviewerは書換えを伴うbuilderを再実行していない。

本レビューは契約/文書/role同期だけを対象とする。製品pytestはNOT_RUN、本番schema適用、同一job新trace、実reporter/reviewerと最終HTML公開、正しい終了登録は未達。FC-A01～10を承認しない。ai_adapter parent B max→highはFC03に明示割当済み。元障害原因・回復・実投稿数unknownと調査v2の原証拠/範囲/cutoff制約を維持する。

作者がFC02-R01を修正し、FC03は利用可能な成果と所見を受け取って進める。review statusをgateにせず、各担当は内容ある成果/handoffを保存し次担当を実起動する。future blockedと別名gateを作らず、raw履歴・操作成否・公開・フロー完遂・元障害解消を分ける。reviewerはこのMD/JSONだけを作成し、製品/DB/Scheduler/Git index/remoteを変更していない。
