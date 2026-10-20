# SC03 production entry and call map

## Intended route

```text
maintenance CLI: skill-chain
  -> run_skill_chain()
  -> eight ROLE_CALLS in plan order, within one Python invocation
  -> one AppServerSession per assigned role (new session; no resume_thread_id)
  -> agent writes its assigned same-job Markdown directly
  -> next role receives the predecessor files with path and SHA-256
```

The worker entry is also wired to the same runner:

```text
maintenance CLI: worker
  -> run_worker_once(..., workflow_runner=build_skill_chain_workflow_provider(...))
  -> provider queries the existing job by source_launch_id/source_run_id/target_date
  -> run_skill_chain(existing job identity)
  -> worker terminal adapter reads the same-job Markdown, call ledger and report bytes
```

The direct `skill-chain` CLI command is the planned same-job execution entry. It accepts only the fixed job, launch and date, with `source_run_id` left null. It does not initialize, enqueue or advance a Daily run. The worker dispatcher still excludes `2026-10-19`.

## Role order and handoff

| Call | Plan heading | Role config | Model / effort / tier / fast | Main output |
|---:|---|---|---|---|
| 1 | 調査 | `rr-inago-incident-investigator.toml` | `gpt-6.1-sol / high / default / false` | `investigation.md` |
| 2 | 実装 | `common-implementer.toml` | `gpt-6-luna / high / priority / true` | `implementation.md` |
| 3 | 試験 | `common-tester.toml` | `gpt-6-luna / high / priority / true` | `test.md` |
| 4 | レビュー（製品） | `common-reviewer.toml` | `gpt-6-luna / high / priority / true` | `code-review.md` |
| 5 | 反映・公開（反映） | `rr-inago-maintenance-delivery.toml` | `gpt-6-luna / high / priority / true` | `deployment.md` |
| 6 | レポート作成 | `rr-inago-incident-reporter.toml` | `gpt-6-luna / high / priority / true` | `report.md`, `report.html` |
| 7 | レビュー（HTML） | `rr-inago-incident-report-reviewer.toml` | `gpt-6-luna / high / priority / true` | `html-review.md` |
| 8 | 反映・公開（公開） | `rr-inago-maintenance-delivery.toml` | `gpt-6-luna / high / priority / true` | `publication.md` |

The plan headings, not the shared role name, key the prompt table because the delivery role appears twice with different duties. Each call opens `incident.md` and all earlier role artifacts from disk; the immediately preceding Markdown must record its exact path, SHA-256 and one fact read. The runner does not pass a previous Markdown summary in place of the file.

Call 7 initially reviews the author-created HTML. After call 7 returns, while calls 6 and 7 still own their separate AppServer sessions, the author receives the review and current HTML and may correct it. The distinct reviewer then rereads the updated HTML and rewrites `html-review.md`. Call 8 starts only after that rereview, and receives the final review plus the exact HTML bytes. Terminal inspection requires the final review hash/status markers and the local publication bytes to match.

## Controller boundaries

- The runner imports `AppServerSession` directly and constructs the small role command locally; it does not import the old AI adapter.
- CLI imports `MaintenanceController` only inside legacy commands. The worker branch selects `build_skill_chain_workflow_provider`.
- `historical.py` keeps a type-only controller reference.
- `worker.py` inspects the Markdown terminal for the new adapter; it retains the existing legacy reconciliation branch for callers that still explicitly use the legacy adapter.
- No new DB schema or job identifier is introduced. `chain_started_at` groups events from one actual runner process and is not described as a formal invocation ID.
- Raw model responses, exception messages, prompts and report contents are not copied into the call ledger. The ledger stores safe codes, role settings, IDs, paths, hashes and output outcomes.

## Current implementation version

The runner is connected to the maintenance worker path and has passed focused synthetic testing. The same-job invocation, independent product review, deployment and live same-job work are still pending. This is not a workflow acceptance result.

- `automation/inago/app_server.py`: `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`
- `automation/inago/maintenance/skill_chain.py`: `75e8815002d425c93ee4ea3cb541a000fb356ab8e122142a1bff26b557a58e89` (the pre-test snapshot `399dbc49...` failed terminal validation for role-8 repository-relative input references and is superseded)
- `automation/inago/maintenance/cli.py`: `f9831eacc3ee700e48bb010df4b2ec6a2ea3eea8a032406601bb0e79bc967cfe`
- `automation/inago/maintenance/worker.py`: `e22344de5c15b0295695d60d901588a9b93214cc778cb08aab4db97d71f7de12`
- `automation/inago/maintenance/historical.py`: `17a92daf2e83a3677b93c5595dda338eb94cf9c072c612d51471d441867bd4b6`
- Approved plan source remains v1.0, original SHA-256 `001b904b786c1dea6c95b54a307f4e2ace60358f850308e0566c164a9b7afaf5`; architecture source remains v1.1, SHA-256 `0177ae92e0981eb1ab179b49d04739de9ffbfbc056daa3e7f7be52b79a28b3ec`.
- Current checks: `.venv-inago/Scripts/python.exe -m py_compile automation/inago/app_server.py automation/inago/maintenance/cli.py automation/inago/maintenance/worker.py automation/inago/maintenance/historical.py automation/inago/maintenance/skill_chain.py` exit 0; plan prompt extraction returns eight distinct headings/eight calls with distinct apply/publish prompts; `git diff --check` exit 0; SC05 focused suite 10 passed, JUnit 10/0 failures/0 errors/0 skipped (`SC05/test.md`, `test.log`, `junit.xml`, `node-list.json`).

## Open checks

SC05 observed and reproduced a stale role-8 publication-input check because terminal validation did not compare the repository-relative paths passed to that role. The predicate now normalizes those paths and matches the final report/review SHA-256 values; the corrected version passed the affected integration test and the full 10-node focused run. Synthetic checks confirm malformed-handoff retry, non-gating role failures, safe cause/frames/digests, same-job resume, excluded-date protection, and publication gates. Independent product review, the role-2 daily instrumentation diff/case, selected commit/push, deployed-code readback, live same-job calls, final HTML rereview and exact-byte publication remain pending.
