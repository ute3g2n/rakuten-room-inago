# FC02 schema差分設計

確認したsource migrationは001～013、FC01 productionはschema 13。旧migration/旧raw enumを書換えない。次の未使用番号014をFC03の新規source `automation/inago/maintenance/sql/014_flow_completion_trace.sql` に割当てる（現時点予定・未作成・未適用）。FC05で同migrationを調整する場合はFC03担当と順次所有し、適用前sourceの最終hashを記録する。別migrationが必要なら直前に番号を再確認して所有表へ追加する。

| 対象 | 新規/既存 | 決定内容 | 担当/受入 |
|---|---|---|---|
| maintenance operation journal | 新table予定 | event_id、identity、invocation/stage/attempt、operation/parent、phase、時刻、code revision、safe errors、refs、sink/recovery。append-onlyで旧observationを変更しない | FC03 / FCT02/05/10/12 |
| stage handoff/publication/reconciliation | 新tableまたは既存tableの加算extension | stage_progress、content_result、operation result、artifact origin、版/hash/refs、workflow_completion、incident_resolution、公開版/remote/finished_atを独立記録 | FC04/05/06 / FCT01/03/04/06/07/08/10/11 |
| maintenance-diagnostic-event.schema.json | 既存拡張 | correlation、operation、safe cause/stack/capture、code/artifact/sink fields、旧raw reader互換 | FC03 / FCT02/05 |
| maintenance-stage-handoff.schema.json | 新file予定 | maintenance-stage-handoff-v1。必須意味はcontract-decision、stage_progressにblockedなし | FC04 / FCT01/03/04 |
| investigation/planning/repair schemas | 既存拡張 | raw statusを保持し、新handoffのcompleted/failed/not_performed/contentを分離 | FC04 / FCT03/04/12 |
| test/review/deployment schemas | 既存拡張 | FAIL/ERROR/NOT_RUNやreview所見とstage継続/適用操作実値を分離、承認等を開始gateにしない | FC05 / FCT04/06/07 |
| report response/review/result schemas | 既存拡張 | 通常/最小HTML、作者と別reviewer、exact版、指摘反映、公開/終端参照、元障害unknown | FC06 / FCT08/09/11 |

追加の正式table名/JSON storage形式は実装担当が既存Store責務と照合して確定し、変更理由と実在path/hashを同STEPで追記する。意味契約を削減しない。FC02はmigrationコード/製品schema/本番DBを変更していない。schema 14を本番現行値と称さない。
