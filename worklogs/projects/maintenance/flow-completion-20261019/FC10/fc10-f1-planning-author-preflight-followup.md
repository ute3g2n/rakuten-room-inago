# FC10 follow-up: planning author preflight handoff

## Scope

- Owner: FC10 implementation follow-up
- Product file: `automation/inago/maintenance/production_workflow.py`
- Function only: `run_daily_planning_author_stage`
- Job identity remains the controller-provided historical 2026-10-17 job; this change does not derive a source run or launch another job.
- Production database, daily output, scheduler, and publication were not touched.

## Finding and change

The planner author stage returned a future `blocked` result directly when its timeout value was invalid or its attempt/session/context was not actionable. It also passed a preparation-stage `blocked` result through unchanged, which could stop later work without a contentful handoff.

The function now routes these start failures through `handoff_without_author`. The controller fallback records an `unknown` stage result, captures the specific reason and exception in a correlated failure event, and stores a contentful handoff in workflow state. If a trusted manifest, running attempt, valid base commit, and attempt artifact paths are available, it also writes `controller-fallback.json` and `stage-handoff.json` under that same attempt. Artifact persistence failure is captured separately while the failure-journal event remains available. The handoff includes the current invocation identifier, falling back to its correlation identifier when the provider has not supplied one.

The existing AI-failure fallback handoff in this function now carries the same invocation identifier. Historical stored observations are not rewritten.

## Evidence and checks

- `python -m py_compile automation/inago/maintenance/production_workflow.py`: passed.
- `git diff --check -- automation/inago/maintenance/production_workflow.py`: passed; Git emitted only its CRLF-to-LF notice.
- Pytest was not run in this follow-up; the controller is adding a direct-runner focused regression separately.
- No production workflow was run, and no runtime database or publication was changed.

## Review reference

- FC10 product review finding: `FC10-F1` (author preflight can return a future blocked result without a handoff).
- Invocation correlation follow-up: `FC10-F2` (bind the planner handoff to the current invocation).
- Implementation is limited to the assigned function; shared edits elsewhere in the file were preserved.

## Source snapshot

- `automation/inago/maintenance/production_workflow.py` SHA-256 at this checkpoint: `fb801b6e079bb21c2ec7a58c198a643637e6114a35ddefdc5f28b6ea753b0d2b`.
- Shared working-tree changes may update other functions in this file; this hash identifies the inspected checkpoint, not a future full-file version.
