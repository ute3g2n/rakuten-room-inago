# FC02 計装の既存/不足と担当

| 区分 | 実在file/境界 | 処置 | owner / evidence / case |
|---|---|---|---|
| 既存 | daily_pipeline.py、orchestrator.py、trace.pyのSQLite分類/error_name/constraint/transaction/proposed/persisted/capture tuple/hash | 既存読取を再利用。日次側同内容の再追加をしない | FC03 read-only / FC01 failure map / FCT02/12 |
| 不足I-01/02 | diagnostic_trace.py require_written/audit_maintenance_stage、CLI main、provider stage dispatch | logger失敗で本処理を止めずsafe chain/stack/captureとoperation相関、fallback記録を追加 | FC03 / 新diff・FCT02/05・FC08 review・FC09適用・FC10新event |
| 不足I-03 | ai_adapter.py PARENT_SETTINGS/transport/parseとproduction planning author捕捉 | parent B highとtransport/timeout/cancel/empty/malformed区別、actual AI IDs、原因連鎖を残す | FC03+FC04 / FCT02/03 |
| 不足I-04/05 | store/safe_file/paths/failure_journal新module | DB・artifact保存/hash・通常trace/fallback自身の失敗を個別operationへ結ぶ。独立spoolとintent→artifact→DB復帰照合 | FC03 / FCT05/10 |
| 不足I-06 | production_workflow/controller/stage_handoff新module | 通常/不正/空/失敗の内容あるhandoff、次担当が実際に入力版を読んだ起動/戻り/保存証拠 | FC04 / FCT01/03/04 |
| 不足I-07 | workflow_reconciliation/store/worker | planning/report running孤立をraw改変なく追記中断観測、同一job再開、flow/incident分離 | FC05+FC10 / FCT10/11 |
| 不足I-08 | deployment/runtime_switcher/CLIとGit境界 | 適用/未反映/失敗・remote・現用/対象/適用SHA・schema read-backを実測 | FC05+FC09 / FCT06/07/11 |
| 不足I-09 | reporting/production report runner/report_fallback | 平易最小HTML、実作者/独立reviewer/修正、exact版とpush/DB途中中断reconcile | FC06+FC10 / FCT08/09/11 |

同一10/17 jobに渡す具体候補は今回FC03のmaintenance操作相関・例外連鎖・writer fallbackの実diff。先行実装済みなら同一jobがcandidate版を実検証・製品review・正式反映して新eventを残す。外側作成差分をjob新規作成と偽らない。既存日次分類の重複追加、既知不足の先送りは行わない。実装・試験・製品review・適用・same-job eventのいずれもFC02現在は未実施。
