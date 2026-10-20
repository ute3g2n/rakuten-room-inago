# 構成案2.0移行実行計画 独立レビュー

現在の対象は `plans/projects/maintenance/保守フロー簡素化と10月20日日次不具合対応_実行計画_2026-10-21.md` の修正版 **1.1 / 2026-10-21**。追確認の状態は **approved**、初回5所見は全て解消。初回提出版1.0の **changes_requested** と具体所見を下に履歴として保持する。review状態は将来の実装・試験・反映・報告を止める条件ではない。

担当は `/root/simplification_plan_review`。親の初稿保存通知後、本文全節、13STEPの14項目と全prompt、18caseの12項目、対応JSON全要素、plan-resultを実読した。原典構成案2.0、planning skill/contracts、10/20一次facts、コード・試験台帳、preparation、構成案reviewのSPR-01 resolved履歴を比較した。hash/bytes/mtime/markerによる版照合はしていない。

identityは受領値のまま、launch `fa3292ae0c7e43bb9569576fcb1a5d85`、対象日 `2026-10-20`、source_run_id `null`、queue `37a2be6b24fa4ffb8f859012e00a209a`。maintenance job/incidentは一次調査時0行という観測、controller attemptは未提供。reviewerは値を採番・推測していない。実行時に既存jobを再確認し、無ければ正規初回採番する計画を確認した。

## 確認できた内容

- 0〜14節、全13STEPの14項目＋全文prompt、全18caseの12項目を備える。R01〜15、OCT20-F01〜17、CI-01〜14、SPR-01は対応JSONへ記録されている。
- 移行製品の独立reviewを別に設け、10/20は対象Worker CLI一回から新runnerが7担当を実起動する。外側agentの代理修正を成果にしない。
- 日次側の原因修正又は有用なprepare/backup/trace計装、試験、作者別製品review、必要反映、平易なHTML4点と作者別実読を要求する。calls.jsonlだけの追加を日次対応としない。
- 通常・追加・外側Workerの実終了、AppServer turn終端と所有CLI終了、exit回収、安全な応答/stdout/stderr実内容sinkを計画している。
- hash/既読/JSON/全artifact/全tree/旧status/承認/lease/receipt/Git/回数等のgate撤廃と代替照合禁止、固定workspace、10/19保全、通常Daily/新run/draft/10/17流用/13時待機禁止をpromptへ伝播している。
- 原因unknown、失敗・未実施、workflow終了、incident解決、ローカルHTML保存を分離する。原因が分かった、直った、試験が通ったという未実測の成功主張は検出していない。既存Git識別子は参照情報として扱う。
- DB migrationの過去行保全とダミーhash禁止、保守診断hash撤去と通常業務artifact hash維持を区別する。新規予定nodeは未作成/NOT_RUNと記す。

## 初回提出版1.0の改善所見（履歴）

| ID / 重要度 | 提出版1.0の箇所 | 内容と必要な修正 |
|---|---|---|
| EPR-01 / medium | STEP03 prompt 233行、HTML作者489行、HTMLreview521行ほか各prompt検証段落 | 非試験担当にも最小node debug/testと全関係回帰の実行命令が反復される。read-only/反映/HTMLの所有と衝突し、STEP01/07の試験禁止とも同一prompt内で矛盾する。試験実行はSTEP02/08へ限定し、他担当は証拠読取と具体的な局所試験依頼に変える。 |
| EPR-02 / medium | §5 94行、STEP13 536/551行 | controller最小HTML本文生成が再導入され、作者最終path直書き・runner責任縮小とCI-12のcontroller本文生成撤去に反する。controllerは短いfallback Markdownを残し、最小HTMLも作者へ局所依頼して実終了を回収する。作者でも不能ならHTML未実施を記録し、review/報告へ進む。 |
| EPR-03 / medium | C07 712行、C08 732行、C16 889/892行 | 局所caseに全module実行、最終回帰に移行/日次共通14fileが指定される。局所は目的別完全nodeにし、C16は移行scopeと日次scopeを分ける。STEP08で変更のない移行PASSを一律再実行しない。改名/置換nodeはコマンドにも同期する。 |
| EPR-04 / low | C11 791行、C14 851行 | powershell fenceの中が実行不能な日本語説明。text又は文章に変え、C11はSTEP05対象指定Workerを一回だけ実行、C14は別agentの実物読取/所見保存と明示する。 |
| EPR-05 / medium | STEP02 183/198行、C16 889行 | 回帰対象test_maintenance_completion.pyがSTEP02の改訂所有にない。移行直接関係箇所の所有を本文/prompt/JSONに追加するか、移行回帰から無関係fileを外して日次側の必要nodeをSTEP08に割り当てる。 |

各所見の要件・STEP・case・owner・期待出力・根拠と引継ぎは同名JSONへ記録した。旧8担当/既読testはC01で期待更新・置換後実行と明記されているため、旧期待を戻す命令とは判定しなかった。ただし改名後の実コマンド同期は必要である。

## 修正版1.1の実物追確認

親の修正版保存通知後、版表示、全STEPの検証責任、HTML失敗処理、C07/C08/C11/C14/C16、STEP02所有、対応JSONの変更箇所とplan-resultを再読した。追確認の観測時刻は `2026-10-20T20:48:42Z`（2026-10-21 05:48:42 JST）。これは初回全文レビューに対する修正箇所の追確認で、製品試験・本番検証はしていない。

| 所見 | 修正版1.1で確認した実物 | 状態 |
|---|---|---|
| EPR-01 | 全13STEPの検証prompt。試験実行はSTEP02/08だけ、他担当は実物・既存証拠読取と実追加修正時の局所試験依頼。JSONのtest_executionも2担当だけtrue。 | resolved |
| EPR-02 | 94/538/553行。同jobのHTML作者へ最小HTMLを局所再依頼し実終了を回収、なお不能ならfallback MarkdownとHTML未実施。controllerの本文合成禁止。 | resolved |
| EPR-03 | C07/C08は目的別完全関数node。追加nodeのソース定義を実読確認。C16 886〜910行は移行/日次別command、未変更移行PASS再実行禁止と実差分に応じたscope確定を明記。JSONも同期。 | resolved |
| EPR-04 | C11 793行/C14 853行をtext fenceへ変更し、実行コマンドではないと明記。 | resolved |
| EPR-05 | STEP02 185/200行とJSON所有にcompletion.pyの直接必要な試験/fixture調整を追加。 | resolved |

残るレビュー所見は0件。原因、実採番ID、実model/tier、製品試験・反映・HTML・worker終了が将来の実測事項であることは維持する。承認語や追確認回数を新しい開始条件へ変換していない。

## 不明点と未実施

10/20の直接原因・衝突対象・cause stack、正式job/incident/attempt ID、実model/tier/認証/本番実行成功・全worker終了は未確認。10/20一次調査の独立review記録は未提供と計画が明記しており、これを待機条件にはしない。`settings/language.md` は現workspaceに存在しなかった。

製品・試験・DB・Runtime・Schedulerの変更/実行、hash生成/照合、Git操作は実施していない。親の明示担当制約によりGit statusも実施しておらず、今回所有文書のGit処理は親へ渡す。今回本人依頼は計画のみなので、実装workerは起動していない。実装開始をreview statusに結び付けたものではない。

## 引継ぎ

親へ1.1の追確認とEPR-01〜05解消を渡す。所見修正も将来のreview承認gateへ変換しない。実行開始依頼が来たら、利用可能な1.1計画と残るunknownでSTEP01へ進む。各次担当promptにも、本人一名ローカル・堅牢性を目標にしない・review/test/readiness/release/FAIL/unknown/証拠/回数/旧blockedをgateにしない・個別不能操作だけ未実施と理由を残す・内容ある通常又はfallback成果とhandoffを渡し可能な次担当を起動する方針を維持する。
