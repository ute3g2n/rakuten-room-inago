# FC01 不足計装と実装割当

## 現状結論

今回必要な計装は既存の日次SQLite分類の再実装ではない。現repository HEADの`daily_pipeline.py::_sqlite_failure_labels` (57), `phase_error` (755–785), broad exception capture (868–935), trace-result logging (1064–1079), `orchestrator.py::_run_create_diagnostic` (716–811, call sites 868–956), and `trace.py` allowlist/normalization (61–123, 394–481, 1244–1249) already cover daily SQLite labels, capture site/code SHA, proposed/persisted run identifiers, and best-effort logging. However, these three files differ from deployment active commit `22d68761cc47e089bfc2ed59dc9eadc4e69126ee` by 448 insertions/22 deletions; the current additions' production applicability is unknown until FC09. Do not duplicate them. The maintenance workflow's stage observation, AI, artifact, logger itself, process resume, Git publication and job termination still lack cross-operation/cause/correlation tracking, and trace write receipt can stop the originating operation. Implement this real gap and carry it through fault-injected tests, independent product review, formal apply and a new trace emitted by the same job.

## backlog

| Priority / planned owner | 不足した操作と必要な記録 | 実装・確認の方向 |
|---|---|---|
| I-01 / FC03 | workflow providerから各inner stageへの入出境界。eventにjob/incident/launch/run/null/date/invocation/attempt、operation name, correlation ID, source SHA/runtime SHA, input/artifact refs, start/end/time, outcomeを付ける。 | JSON schemaを追加・versionし、同一correlationをprocess resume後も維持。safe fields/allow-list、secret/PII除外を先に定義。外側の1 stage summaryだけで代替しない。 |
| I-02 / FC03 | 例外捕捉: `run_daily_planning_author_stage`, controller repair/deployment/report publication, provider stage runner, CLI command boundary。 | error class/code, chained `__cause__` / `__context__`, sanitized traceback frames(file/function/line), capture site, operation, upstream event ID, input/output refを保存。exception detailsが無い過去raw resultを補って作らない。 |
| I-03 / FC03 | AI create_session/run_turn/close, transport/timeout/cancel, malformed/empty response, JSON/schema validation, persistence exception。 | requested vs actual/unknown設定を区別し、actual observationなしをgateにしない。AI SDK失敗の例外連鎖・turn/session ID・allowed output refsを保存。強制high設定を既定契約へ反映。 |
| I-04 / FC03 | `MaintenanceDiagnosticTrace.write` / `require_written`, `audit_maintenance_stage`, CLI trace writer error。 | journal writerはnormal trace/storeに依存しない別path/SQLite connectionまたはlocal append-only spoolへ記録する。通常logger失敗を元処理の例外へすり替えない。sink failureとbusiness operation resultを別々に保存し、原exceptionを保つ。 |
| I-05 / FC03 + FC05 | artifact atomic write/rename/hash/read-back, maintenance DB begin/commit/unique constraint, dual-store partial commit, log/fallback journal failure。 | begin intent→operation result→artifact ref/hash→store commitを関連付ける。書込中断では復帰時に耐久beginから未確定区間をunknownとして追記。各sink failureを具体的なstage/operation/cause/capture siteと結び、同一失敗を再帰的に同一loggerへ書かない。 |
| I-06 / FC04 | downstream trigger/dispatch, actual child execution/accepted handoff, stage returns and empty/invalid artifacts. | status/quality/progressを分け、stage errorを記録しても内容ある通常またはcontroller fallback成果を次担当が読む・実処理する。future stage result `blocked`やreason alias gateを作らない。 |
| I-07 / FC05 + FC10 | stale running attempts, process interruption, resume_count, final job/incident/worker reconciliation. | append-only interruption/recovery event with prior attempt ID, exact persisted refs, last event, operation, recovery action, new attempt, same job/identity; don't erase raw `running`/`blocked`. terminal result and finished_at must agree with final published report. |
| I-08 / FC05 + FC09 | Git fetch/push, deployment candidate/preflight/test/review, official runtime switch and read-back failures. | subprocess/network/remote ref and commit, operation start/end, exception/status, captured stderr classification, current repo SHA, target SHA, active runtime SHA, DB migration/read-back. No blanket stage success on a local commit. |
| I-09 / FC06 | report author/reviewer/revision, HTML validation/publish, remote/read-back, sidecar. | exact artifact version/hash and its review target, findings/author response, publication URL/remote ref and read-back; fallback HTML uses the same reviewer/publication loop. Internal IDs remain appendix-only. |

## 譲らないテストと受入

1. FC07 synthetic fixtures inject DB failure, trace logger failure, fallback logger failure, artifact atomic-save/hash failure, AI transport/timeout/cancel/empty/malformed response, subprocess interruption and Git publish failure. Each fault must show the correct original failure and logger failure relationship without generic-only reason.
2. For each injection, prove substantive fallback artifact exists, downstream responsible agent was actually launched and processed its input, failure reason/operation/capture-site/correlation/artifact refs persisted, same-job resume progressed, and final HTML/publication/terminal reconciliation occurred. A stage function-call count or outer stage loop alone does not pass.
3. Test-only faults use local test/fixture DB and synthetic repo/remote. Production DB, scheduler and user content are read-only until approved plan's official apply/resume steps.
4. Physical all-sink/OS/disk loss cannot be logged in the moment; after recovery record the unobservable interval as unknown from last durable begin. This is the only hard physical limit; it does not excuse ordinary logger failure.

## FC02 required decisions

- Confirm a schema/version and whether to use existing `diagnostic_trace.py` plus dedicated `failure_journal.py` (currently planned but absent) or another implementation that demonstrably isolates fallback persistence.
- Confirm correlation-ID lifecycle and operation/event vocabulary; migration number is not assigned yet.
- Specify safe traceback serialization, secret/PII redaction, retention, fsync/atomic-write behavior, self-recovery and integrity verification.
- Map each I-item to exact function/file/hunk owner, case/node, evidence file and FC step. Do not defer I-01..I-05 until after FC10.
- Allocate useful implementation operation to the same 10/17 job: implement this uncovered maintenance operation/fallback trace path, or (if the same candidate is committed before the job begins) have the job's plan/repair/test/review/deployment actually validate and officially apply it and capture new correlation events. Do not mislabel outside-created source diff as job-created work.
