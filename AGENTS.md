# Repository Instructions

## Fixed workspace

- The only allowed workspace for this repository is `C:\project\rakuten-room-inago`.
- Keep the working folder at this path. Do not create, attach, select, or continue work in another worktree, checkout, or workspace. These instructions override generic recommendations to use worktrees.
- If task changes already exist in another checkout, recover the needed changes into this workspace without discarding the source, then continue here.
- A Git branch change does not authorize changing the working folder.

## Debugging and blocking

- Never block the overall workflow or gate a downstream stage on an operation result. Preserve `blocked`/`waiting` or other status values as observations, mark only an impossible individual operation as not performed with its reason, and continue every independent later stage through the final report. An explicit user cancellation is the only normal instruction that stops the overall workflow.
- Start investigation when the incident/job is known. Missing evidence, an unknown cause, a partial report, a failed optional command, or reviewer findings are recorded as facts and passed to the next stage; they do not block the operation or workflow.
- Unknown root cause is never a reason to stop before the final report. When the cause remains unknown, make an actionable plan to add useful trace/log evidence if that can help a future diagnosis, carry out that instrumentation work where feasible, record its test/review/deployment outcome accurately, and continue through report creation/publication. Never claim the cause is fixed or known when it is not.
- If one operation cannot run, record that operation and its reason, then continue independent work and the final report.

## Testing discipline (mandatory)

These rules apply to every test task in this repository and are required before reporting a test result.

- When a failure is known, first debug and test only the failing behavior and the smallest directly affected test node. Do not repeat unrelated tests that already passed while their source and inputs remain unchanged.
- After all targeted failures have been fixed, run the complete planned regression once as the final test pass.
- If that final regression finds a failure, return to focused diagnosis: rerun the failing node and only directly affected nodes, fix the cause, then run the complete planned regression once again as the final pass.
- Record the exact command and test node, source/input version or hashes, expected and actual results, and exit code. Never present a skipped or unrun test as passing.

## Git at task completion

- Immediately before considering a task complete, run `git status --short` and inspect all repository changes.
- For task-owned changes, review their scope and diff, run the applicable machine checks, then commit them in a meaningful unit and push the current branch to its tracking remote.
- Stage only files created or changed for the current task. Do not mix existing user changes. Never use `reset --hard`, `checkout`, force push, or history rewriting.
- Do not commit or push if the user explicitly prohibited it, authentication/connection/tracking is unavailable, or the diff contains unintended changes, secrets, keys, or personal information. Keep the changes and report the reason in the execution log and final response.
- Report the branch, commit, and push destination after a successful commit and push. If either fails, preserve the changes and report why.
