# SC03 runner implementation record (preliminary)

## Task and identity

- Job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- Source launch: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- Target date: `2026-10-17`
- Source run: `null`
- Workspace: `C:\project\rakuten-room-inago`
- Current implementation entry: `automation/inago/maintenance/skill_chain.py`

## Changes in this preliminary code version

- Added the fixed eight-role AppServer runner and direct `skill-chain` CLI.
- Connected the maintenance worker factory to the new runner and a Markdown-based terminal adapter while keeping the legacy adapter lazy for legacy callers.
- Kept the historical controller import type-only.
- Added runner-scoped advisory capability probing and best-effort AppServer log handling; the existing default behavior remains unchanged for other AppServer users.
- Logs capture role requests/observations, safe result codes, handoffs, predecessor path/hash references, output hashes, and whether AppServer log writes failed. Free-form model output and exception messages are omitted.
- Calls proceed in fixed order on a role failure, with an explicit controller-authored fallback artifact marked as such. A malformed handoff receives one format-only retry in the same role session. Safe failure records include a bounded class/code cause chain, repository-relative frame references, and response digest/length without response text.
- A subsequent invocation on an incomplete chain resumes at the first call whose saved handoff or output hashes do not match, archiving affected same-job artifacts first. A completed chain is read back idempotently. It does not create a runtime job directory.
- The HTML author/reviewer correction turn is performed after role 7 and before publication role 8. The terminal adapter requires the final review marker and exact local publication bytes.

## Preliminary machine checks

- Python syntax compile: `.venv-inago/Scripts/python.exe -m py_compile automation/inago/app_server.py automation/inago/maintenance/cli.py automation/inago/maintenance/worker.py automation/inago/maintenance/historical.py automation/inago/maintenance/skill_chain.py` — exit 0.
- Approved plan prompt extraction: eight distinct plan headings / eight fixed role calls — exit 0.
- `git diff --check` — exit 0.
- Current `automation/inago/maintenance/skill_chain.py` SHA-256: `399dbc49fdd19decf1b8029ce7ef784d5d6dc0b9b9c3d1204cbf20e09e3b881c`.

These checks do not cover runtime behavior. SCT focused tests, failure injection, an independent product review, production application, same-job execution, author correction/rereview and publication remain pending. The product review is expected to drive any implementation repairs before SC07.

## Handoff

- Next: finish SC03/SC04 code and record; then SC05 focused tests.
- Inputs: this repository's approved plan and eight role prompts, SC01/SC02 records, same-job `incident.md`, `automation/inago/app_server.py`, and current runner diff.
- Request: verify the fixed route and all failure-continuation/recording behavior against the plan; later use only the same job and never start Daily or process 2026-10-19.

## Final reviewed implementation version (2026-10-20 14:33 JST)

The preliminary `399dbc49...` snapshot above is historical and superseded. SC05 exposed the role-8 repository-relative path comparison defect; the corrected and subsequently reviewed runner is `automation/inago/maintenance/skill_chain.py` SHA-256 `5b82597fa9f2621796397b4bc05ee52b0020259751a294769fd26362ac535447`. Linked source hashes: `app_server.py` `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`, `cli.py` `f9831eacc3ee700e48bb010df4b2ec6a2ea3eea8a032406601bb0e79bc967cfe`, `worker.py` `e22344de5c15b0295695d60d901588a9b93214cc778cb08aab4db97d71f7de12`, and `historical.py` `17a92daf2e83a3677b93c5595dda338eb94cf9c072c612d51471d441867bd4b6`.

Final SC05 F4 follow-up ran 21 new skill-chain nodes and four plan-mapped existing SCT04/SCT08 nodes: 25 passed, 0 failed/errors/skipped, exit 0. Exact artifacts, command, clocks and test hashes are in `SC05/F4-followup/`. The independent SC06 reviewer issued approved/no findings for this code hash in `SC06/follow-up-review-v3.md` (review file SHA-256 `eb76e00460a80f39b17cf24ec6a1c36ec05ec370d8cd9d5bd8299c4b6a2633a6`).

The SC06 history and fixes are retained in `SC06/`: F1 adds a same-role reporter recovery when role 6 cannot produce a valid report; F2 binds predecessor read acknowledgment to a successful exact-path AppServer command event; F3 recognizes successful author/reviewer replacement outputs so publication-only failure resumes at role 8; F4 records/verifies final `report.md` and `report.html` hashes in resume and terminal checks. These tests and review do not replace SC07 production apply/readback or the SC08/09 live same-job chain. FC01–FC11 remain NOT_RUN.

## Final SC05/SC06 version (2026-10-20 14:37 JST)

After the F4 fixes, a whitespace-only EOF cleanup produced final runner SHA-256 `3e8e3f34cd87739ef2ef57f88bc76f0fcd1fc93fa51a4fcea50ccd79e10d9e12`. The final focused test source is `6c2d480d29e33d19c57dacd2251a5df20bd01dbbe3d47073769ff7d89951328f`. F5 reran the 21 focused runner nodes and four plan-mapped existing nodes against these exact hashes: 25 passed, 0 failed/errors/skipped, exit 0. Exact command, node IDs, clock, JUnit and normalized console evidence are in `SC05/F5-followup/`; original bytes for profile-path-bearing evidence are held only in the host-local temporary archive, with hashes recorded in `SC05/privacy-redaction-record.md`.

Separate SC06 reviewer approval for the final runner is recorded in `SC06/follow-up-review-v4.md` (review file SHA-256 `3eccc4f42453abf63dbe6bb3bd4841e73bc8338704aaf1a43ffb29cc4ffff4ab`), with no new findings. This still does not establish SC07 deployment or SC08/09 same-job operations.