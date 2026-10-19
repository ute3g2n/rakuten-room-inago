# FC07 measured case status

All thirteen FCT identifiers have an explicit measured result or a scope-specific NOT_RUN entry; no case is presented as full-plan acceptance.

| Case | FC07 result | Remaining gap / scope |
|---|---|---|
| FCT01 | Partial PASS | The targeted runner/handoff patterns and consecutive nested failures were measured, but not every possible operation/failure combination. |
| FCT02 | Partial PASS | Timeout/malformed, empty reporter, transport/log failure, redaction, correlation and fallback replay were measured with synthetic adapters; no live AI service was called and every parser/transport variant is not claimed. |
| FCT03 | Partial PASS | Controller fallback planning/no-change explanation measured; full real planner/reviewer agent orchestration belongs later stages. |
| FCT04 | Partial PASS | Stage runner spies and report author/reviewer provider paths measured; real controller launches of tester/reviewer/deployer roles and all no-change role outputs were not measured here. |
| FCT05 | Partial PASS | Primary DB sink failure, independent JSONL fallback/replay, simultaneous sink outage and same-identity recovery measured; every artifact/rename/schema/logger-recursion boundary was not separately injected. |
| FCT06 | Partial PASS | Rejected/malformed review and missing preflight reached report/finalize; direct reviewer revision is measured under FCT08, while all orchestration combinations are not covered. |
| FCT07 | Partial PASS | Six local-remote publication/recovery/sanitization cases passed; no public remote was touched and this is not the full real deployment transaction. |
| FCT08 | Focused PASS for normal and fallback review/transport paths | Synthetic production-workflow integration confirms normal revision, fallback findings to author, changed artifact/version/hash, reviewer on exact version and publication; transport failure publishes truthful pending fallback. Real AI and real-job publication are FC10/11. All earlier failures are preserved. |
| FCT09 | Final synthetic target approved (r6) | r2/r3 changes_requested reviews remain as intermediate history. Independent review r6 approved exact final manifest SHA 19cdf63a192710d095886308875bda2326a8238a3c9189e515a9993486e74f7a (18/18 hashes matched; review file SHA 4304a8c5d05658b8c6fd3850f3f05d990b94240e1aece722c5e67bc97b3ea984). FC07 regression tests were run against earlier manifest 261aa9d333707060fcc81b894c36d67ff450abecb0daedf9f54442ba186a287; later plan-source hash updates were not included or tested, while product source hashes remained unchanged. The actual 10/17 report and browser/print rendering remain for FC10/11. |
| FCT10 | Partial PASS | Abrupt child exit, same synthetic SQLite job resume, artifact-before-terminal-registration, old attempt interruption and report-only resume are measured; public Git readback/no-duplicate commit chained to the crash window is not performed by FC07. |
| FCT11 | NOT_RUN by tester scope | The real 10/17 production job and final publication are controller FC10/11 only; FC07 did not touch production state. |
| FCT12 | Partial PASS | Fixed synthetic identity, null source_run_id, no secret marker and 10/19 exclusion are tested; production history/queue counts are reserved for controller read-only/authorized stages. |
| FCT13 | Partial PASS | 24 schema JSON, 9 role TOML, 5 skill files and high setting were statically read; full active requirements/docs/gate scan and final Git ownership reconciliation are FC09/controller. |

`case-registry.json`, `fct-additional-results.json`, `execution-record.json`, JUnit/console artifacts, and `handoff.json` contain per-run commands, hashes, outcomes, and earlier failures. FC07 does not claim overall acceptance.

## FC08-F1/F2/F3 follow-up evidence (2026-10-20)

`fc08-f1-f3-final-results.json` records exact commands, test/source hashes, outcomes, temporary data scope, and prior setup/assertion failures. New focused results:

- F1 publication-after-sidecar child interruption resumed the same synthetic SQLite job. Reconciliation read all eight handoffs/observations and required outputs, the report result/HTML/sidecar, deployment, and report bytes from a temporary bare remote. Only the reconciler finalized the job; incident remained `in_progress`. Missing a handoff or public report independently prevented `complete`.
- F2 source fingerprint changed after a maintenance source changed in an isolated copy. One invocation fingerprint was shared by failure event and handoffs. A capture failure recorded `unknown` with a concrete cause and source reference while later stages ran.
- F3 deployment referenced a validated repair sequence-3 attempt or null; report used the actual maintenance_report sequence-4 attempt.
- The existing six-parameter publication retry node initially failed because its test indexed an empty finalizer list after asserting publication had made no finalizer call. The stale test assertion was removed, then the same node passed 6/6. The initial failure JUnit remains alongside the final run.

These are synthetic SQLite/local-Git measurements. They do not replace FC10/11 production same-job, real reviewer, public publication, or final readback measurements. Overall FC07 acceptance remains partial.
