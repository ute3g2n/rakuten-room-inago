# FC03 failure-tracking matrix

対象は承認計画1.2のFC03と、FC02 `file-operation-owner-map.json` でFC03に割り当てられた操作である。下記の「実装あり」はコード差分・静的確認から分かる到達経路を示す。fault注入での実測を意味しない。

| 失敗境界 | 実装位置・記録内容 | primary failure時の処理 | FC03での実測 | 次の実行担当 |
|---|---|---|---|---|
| 保守JSONL / diagnostic event writer | `maintenance/diagnostic_trace.py::write`; catch境界で元exception chain・数値errno・file/line/functionを独立journalへ記録。`require_written`はcatch済み記録をgeneric eventで重複させない | 本処理は失敗扱いにせず、詳細eventをjournalへ一度記録。journalも不可なら短いstderr markerを試みる | 構文/JSON parse/buildのみ。writer failure injectionは未実施 | FC07 FCT05でtrace I/O失敗、require_written/receipt失敗、stderr捕捉を確認 |
| operation event DB insert/commit | `store.py::record_operation_event`; event/correlation/job/launch/run/incident/invocation/stage/attempt/operation/phase/time/revision/reason/exception chain/frames/capture/ref/sinkを記録 | `workflow_provider.py::_record_operation` は例外をfallback journalへ送る | migration DDLはin-memory SQLiteでtableとappend-only triggersを確認。commit失敗注入は未実施 | FC07 FCT05でDB insert/commit失敗後のstage到達とfallback eventを照合 |
| DB replay failure | `failure_journal.py::replay_failure_journal`; failed `record_operation_event` call is captured as a new failure-journal event with original correlation/job/launch/stage and safe exception chain/errno/frames | replay failure increments the observed unknown count and is also independently diagnosable; fallback write failure is reported only through the fallback's own safe stderr last resort | Static inspection only; DB replay failure injection not run | FC07 FCT05 injects DB replay failure, then confirms a correlated fallback event exists |
| Fallback journal append/fsync | 新規 `failure_journal.py`; JSONL独立追記、`O_APPEND`, `O_NOFOLLOW` (利用可能OS), process lock、fsync。例外class chainと basename/line/function framesを保存し、例外messageは保存しない | journal失敗時に再帰loggerなしでsafe stderr markerを直接write。markerも失敗する場合は書込不能区間がunknown | 構文確認のみ。read-only/permission/short-write/fsync障害注入は未実施 | FC07 FCT05で個別fallback失敗と全sink停止境界を確認 |
| AI timeout / transport / empty response | `ai_adapter.py`; timeoutとtransportを個別理由、空応答を別理由でfallbackへ送信。safe exception chain/framesを残し元例外を再送出 | AI失敗は実際の呼出失敗として返し、trace書込み失敗を混同しない | 構文確認のみ。AI adapter nodeは未実行 | FC07 FCT02でnested cause、timeout、transport、malformed/empty/legacy blocked、secret囮0、次role実起動を確認 |
| safe-file open/unlink/publish/directory | `safe_file.py`の各操作wrapperと `failure_journal.py::record_current_operation_failure`; operation nameと相関ID、例外型chain、安全なframesを送る | fallbackは本処理とは別に記録。元操作例外は変更せず再送出 | 構文確認のみ。filesystem permission/rename障害注入は未実施 | FC07 FCT05でartifact rename/atomic publishの失敗を確認 |
| process interruption / open begin | DB側 `list_open_operation_events` / `reconcile_open_operation_events`; 最新eventは`observed_at`とSQLite append-order `rowid`で選択しtimestamp tieを決定。fallback側 `reconcile_open_operations`; workflow start時にjournal replayとDB reconciliationを試す | 終端なしbeginへ `interruption_observed` を追加する。実終了時刻や原因は創作しない。fallbackからDBへの再送はevent IDを保ち `INSERT OR IGNORE` | migration構文とPython構文のみ。rowid timestamp tieと子process restartは未実施 | FC07 FCT10でbegin/end timestamp tieとbegin後/AI前/rename前後/commit前後の中断を合成し、job数1/null run/orphan running 0を確認 |
| 全sink同時停止 | fallback appendは独立pathとstderrを最後に試す。どちらも書けない時間は完全原因保存を保証しない | 次回読めたbeginから中断観測を追記し、未記録区間の原因はunknown | 試験未実施。FCT05/10内で合成fixtureのみ | FC07 FCT05/FCT10 |
| history / identity / 10/19 exclusion | source run nullは既存対象から引継ぎ。FC03はraw records/queue/Scheduler/workerを変更しない。migrationはsource SQLのみ | production state untouched。10/19除外定数を編集していない | 静的所有範囲の確認のみ。production before/after比較はFC01/FC09/FC10担当 | FC07 FCT12の静的/fixture確認、FC09/10の本番 read-only比較 |

## 検証の境界

- 実行済み: 9 maintenance module `py_compile`; 23 maintenance JSON schema files built-in JSON parse; migration 014のin-memory SQLite DDL; 要件HTML builder; `git diff --check`。
- 未実施: pytest・FCT02/FCT05/FCT10/FCT12 failure injection・JSON Schema semantic validation。FC03実装agentの実行指示ではtestsを実行せず、計画上の実測担当FC07 common-testerへ完全nodeを渡す。環境内に `jsonschema` Python packageがないためsemantic validatorも未実施。
- Static passやmigration smokeを受入PASS/製品独立review/production applyの証拠とは扱わない。

## 独立review follow-up (追加差分)

Root reviewerがFC03差分を読取確認し、次の3件を指摘した。修正後もFCT02/FCT05/FCT10/FCT12の fault-injection はNOT_RUNのままFC07へ渡す。

| review finding | 根拠位置 | 変更 | 今回の静的確認 | 未確認/次の担当 |
|---|---|---|---|---|
| trace writer catch がgeneric reasonだけで原因を落とす | `diagnostic_trace.py::MaintenanceDiagnosticTrace.write`, `require_written` | catch境界から例外class/cause、数値errno、basename/line/functionを独立journalへ記録。`DiagnosticWriteResult.failure_journal_attempted`で呼出側のgeneric-only重複を抑止。journal自体が失敗しても二度目の再試行へ隠れない | py_compile成功、差分検査成功 | writer I/O/errno/secret囮 failure-injectionはFC07 FCT05 |
| journal replayのDB例外がカウンターだけで消える | `failure_journal.py::replay_failure_journal` | catchしたstore例外を元correlation/job/launch/stage/attemptに結び、cause/errno/framesと失敗したevent ID参照を持つ独立fallback eventとして追記 | py_compile成功、コード経路静的確認 | DB replay failure注入・回復はFC07 FCT05 |
| 同じms timestampのbegin/end tieをopenと誤認する | `store.py::list_open_operation_events` | latest event選択を`observed_at`→SQLite append-order `rowid`の二段比較へ変更し、docstring/AT-TRACE/辞書/log表に意味を記録 | py_compile成功、migration DDLにerrno列あり | tie case・restartはFC07 FCT10 |

補足: migration 014に`exception_errno INTEGER`を追加し、Store insertとfallback replayで数値errnoを保持する。latest/tieとreplayのfault注入は行っていない。要件HTML builderの初回追補実行は生成SVG `docs/specifications/inago/図解/F12.svg`をopenする段階でNode `UNKNOWN`/errno -4094 exit 1。ファイルとdirectoryのACLはModifyを許し、malformed JavaScriptやpermission denialは報告されず、lockの有無はこの出力から判定できなかった。FC04の共有file書込がpauseされた後に一度だけ同じbuilderをretryし、exit 0、v2.50/28 chapters/39 figures/26 references/4,066,177 bytesで生成完了した。詳細stack・triage・両実行結果は `review-followup.json`。

| FC07 publication-failure ref regression | failure journal reference sanitizer | FC07 observed evidence/u21/input.json converted to null by ID-only validation | separate strict ref sanitizer preserves allowed relative POSIX ref; unsafe refs omitted and never coerced to identity | static PASS; runtime NOT_RUN | FC07: rerun this single publication failure parameter and inspect persisted input_refs |

| FC08-F2 / R02 invocation source fingerprint | code_revision.py + provider context | helper-module hash did not identify worker/workflow/schema source versions | immutable invocation-start manifest hash spans maintenance Python/SQL/schemas and daily boundaries, with explicit worker/workflow/schema metadata; same value bound to failure events/handoffs; capture failure is unknown plus concrete reason and continues | static PASS; runtime NOT_RUN | FC07 run fingerprint-sharing and capture-failure continuation nodes |
| FC08-F3 / R03 stage attempt association | workflow_provider.py | sequence 4 is maintenance_report; deployment/report lookup could cross-attach | validate step key/job/source run; deployment points to validated repair candidate/attempt or null; report points only to maintenance_report seq4 | static PASS; runtime NOT_RUN | FC07 run two attempt-association nodes |
