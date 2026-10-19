# 新規フロー完遂計画 v1.0 独立レビュー

判定：**changes_requested**。重大度mediumの修正要求3件。レビュー結果は後続工程のgateではなく、具体指摘を作成担当と次担当へ渡す。

- 対象：`plans/projects/maintenance/自動不具合対応フロー完遂と全工程追跡_実行計画_2026-10-19.md`、版1.0。
- 対象SHA-256：`9aa136c57b1a119995c07cec26054bed2695abed5deb9e6b392454c52b063f81`。親供給値と一致。
- reviewer：`/root/flow_completion_plan_review`。指定effort high、実model/effort/tierのruntime観測は未提供。
- 対象job：`job-historical-9e7f0c175cc4c874c955a5dab29c4427`。source launchは`historical-9e7f0c175cc4c874c955a5dab29c4427`、source_run_idはnull、対象日2026-10-17。formal controller review attempt/base_commitは割当なしでnullを維持。
- 計画全文・構造・既存source/node定義・調査v2・その独立review・S04・既存文章ルールを読取。製品/試験/DB/settings/Scheduler/計画/旧証拠は変更せず、試験実行0。本レビュー2出力だけを作成する。

## 修正要求

### FCP-01：FC08の起動条件に無差分・試験証拠欠測時の停止条件が残る（medium）

位置：FC08第4項（438行）と全文promptの開始条件（450行）。第4.2節130行との不整合。

「製品コード差分と利用可能試験証拠がある時点」は、差分0や試験証拠が取得できなかった場合の独立reviewer起動を妨げる。第4.2節では無差分でも実担当を起動して対象なしを確認すると決めており、最新本人方針も任意の試験証拠を起動条件にしない。

FC08はjob/stageと利用可能な製品結果・無変更説明・fallback成果が特定できたら開始する文へ直す。差分や試験証拠の存在を起動条件から除く。差分0では実reviewerが対象なしを確認し、approvedを捏造しない。FCT04/FCT06に無差分・試験証拠なしでの実起動assertを結ぶ。

対応：R03/R05 → FC08 → FCT04/FCT06/FCT13。修正ownerは計画作者、実行ownerはcontrollerと製品reviewer。修正版の開始条件・全文prompt・case期待を次担当へ渡す。

### FCP-02：schemaと計装境界の書込所有pathが曖昧（medium）

位置：第3節97行、FC03第6項/全文prompt（290/305行）、FC04/05/06第6項と全文prompt、FC09第6項/全文prompt（470/485行）。

`automation/inago/maintenance/schema`は実在しない。実在するJSON schemaは`automation/inago/schemas/maintenance-*.schema.json`、migrationは`automation/inago/maintenance/sql/`。また「必要なschemas/sql」「該当6skills/contracts」「固定workspace本番maintenance/schema」やbasename省略では、sourceと本番操作の所有範囲が確定しない。第3節にある日次診断境界の実在file `daily_pipeline.py`、`orchestrator.py`、`trace.py`はFC03～06の所有に明記されていない。

実在repo相対pathを第6項とpromptへ記載し、FC02成果にschema群・新module予定・active skills/contracts/roles・日次診断境界のfile/operation/owner表を定める。必要な日次計装は具体所有者へ割り当てるか、FC01/02で割り当てる表をFC03/FC10が必ず読む契約を明記する。本番DB/migration操作とsource directoryを区別する。所有更新のための追加承認gateは作らない。

対応：R02/R04/R09/R10 → FC01/02/03/04/05/06/09/10 → FCT02/05/11/13。出力は実在path付き所有表と同期prompt、既存/新規計装割当。製品担当へその表を引き渡す。

### FCP-03：既存repo roleのeffortはhighではなくmax（medium）

位置：第4.4節149行、第13節831行、全11全文prompt、FC09での同期順序。

現物の`.codex/agents/common-implementer.toml`、`common-tester.toml`、`common-reviewer.toml`、`rr-inago-incident-reporter.toml`、`rr-inago-incident-report-reviewer.toml`は第4行が`model_reasoning_effort = "max"`、内蔵developer指示もeffort maxである。「repo .codex/agentsのhigh設定を正式経路に使う」という記述と一致せず、正式同期はFC09なので初回実装・試験・reviewより後になる。固定maxの回避経路はsession-toolだけでなくrepo roleも含めて明確化が必要。

現在maxである5 roleをbaselineとして記録する。各初回起動時にrepo roleがmaxなら同じ専門promptをhigh指定できるworker/defaultで実起動する、と明記するか、task所有のhigh同期をFC02へ前倒しする。TOML値と内蔵指示の両方を対象にする。実model/effort/tierの未観測は起動を妨げない。全promptへ同じ規則を反映する。

対応：R10 → FC01～11 → FCT13。ownerは計画作者/controllerとrole契約担当。出力は正確な設定baseline、FC03～08より前に利用できるhigh起動経路、同期所有表。

## 確認結果と範囲

第0～14節が各1回、FC01～11は各14項目と全文prompt1件、FCT01～13は各12項目ある。全R/F/STEP/caseは第10節に対応し、owner/output/reviewer/evidenceも記載される。既存完全pytest nodeは14件全て指定file内に定義実在を確認した。pytest collection/実行は行わず、新caseは未作成として区別されている。想定外制御文字0件。

blockedを名前だけcompletedへ変えず、内容あるfallback成果と後段読取・実担当起動を求める契約、実装だけが原因修正/有用計装の双方不能時に実質省略可能な契約、内外の失敗注入表、DB/FS/独立spool/stderrとprocess中断reconciliation、Git push後DB前中断の回復が明記される。全sink/電源全面停止は物理限界として最後の耐久beginとunknown区間で表し、失われた歴史原因を復元できるとは約束していない。

同一10/17 job自身の成果/review/公開/終端登録を必須とし、外側reporter HTMLだけで完遂にしない。新計装は実差分・focused試験・独立製品review・反映・新traceが必要で、元障害解消とは分離される。10/19除外・queue/history非処理・run null・同一job/launch・固定workspace・Daily手動/追生成禁止も保持する。無関係全suite、OS sandbox、host receipt/C34、test-code reviewは要求しない。

調査v2のresult statusはpartial、別の後日独立reviewはapprovedで、IR-U23-03/04/V2-01の原証拠/読取範囲/追記時刻の限定は残る。本計画はそれを保持している。根本原因・業務回復unknownを解消済みにしない。

平易なHTMLは既存`docs/standards/イナゴ日次不具合レポート作成ルール.md`を必須とし、一覧/詳細、期待/実結果/影響/対応/不明、技術付録への分離、本文だけの5問、別reviewerと作者修正、決定的最小HTML、version sidecarによる自己review/公開書換循環回避を含む。既存文章基準を緩和しない。実表示不能はその操作だけ未確認とする。

## 計装予約に引き継ぐ観測

SQLite分類・許可constraint・transaction begin/commit/rollback/rollback_failed・proposed/persisted run情報は既に`automation/inago/orchestrator.py:719`以降、同870～956、`daily_pipeline.py:765/885/929/1064`付近と`trace.py:121/472`付近に実装がある。これらを未実装の新規作業と誤認せず、FC01/02で不足する具体境界と差分を確定する。共通失敗追跡の基盤を意図的に欠落させて同一jobの作業を作ることも、同じ項目の重複追加を成果にすることも不可。本計画第12節の重複禁止方針を実割当に適用する。

本レビューは計画だけを扱う。3件を新版へ反映して実reviewするが、review statusや指摘件数を実行・後続のgateにしない。今回は計画作成依頼なので実装や本番再開へ進まない。Gitは親controllerが所有fileを確認して扱う。
