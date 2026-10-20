# SC05 F7 focused follow-up test

## Scope and source versions

- Purpose: check the F-01/F-02 focused repairs to handoff status telemetry and next-role display labels.
- Product source `automation/inago/maintenance/skill_chain.py`: SHA-256 `3a46cdb6988a55024bf98019a074f4361b5003a12ee151ee0291291e1457690e`.
- Test source `tests_inago/test_maintenance_skill_chain.py`: SHA-256 `739f3e557fe9c19279911efae0bccfa97e2e23ef9241f6aaf7d435a1b8e5bfff`.
- Repository rule `AGENTS.md`: SHA-256 `840f14ab299dcc5198a9a691ced96bbf532cdae3cd11256519830c43a67d4161`.
- Workspace: `C:\project\rakuten-room-inago`.

## Focused result

- Command: `$env:PYTHONPATH = (Get-Location).Path; pytest -q -p no:cacheprovider --basetemp (Join-Path $env:TEMP ('pytest-skill-chain-targeted-' + [guid]::NewGuid().ToString('N'))) tests_inago/test_maintenance_skill_chain.py::test_resume_after_final_review_reuses_author_and_reviewer_outputs`
- Start: `2026-10-20T12:39:59.5233270Z`.
- Expected: the role display mapping matches independently fixed Japanese labels; false read/target telemetry and failed turn-status fields do not prevent completion when assigned artifacts and their hashes are present; a changed published HTML resumes at role 8; a changed author report resumes at role 6.
- Actual output: `. [100%]`, `1 passed in 0.38s`.
- Exit code: `0`.
- End: `2026-10-20T12:40:00.9453175Z`.
- `git diff --check` for the three task-owned source files exited `0`; Git reported only its existing CRLF normalization notices.

## Environment setup attempts

- `python -m pytest ...` exited `1` before test collection because the default Python 3.12 environment had no `pytest` module.
- `pytest ...` exited `1` during conftest import because the repository root was not on `PYTHONPATH` (`ModuleNotFoundError: automation`).
- Neither setup attempt executed a test. The recorded passing run used the installed pytest executable with `PYTHONPATH` set to the fixed workspace root.

## Review and remaining verification

- The independent reviewer found F-01 (turn/handoff fields still gated role completion) and F-02 (test expectation reused the implementation mapping). The product changes address both and retain output-reference/hash, author revision, independent rereview, final approval marker, and publication-byte checks.
- Independent follow-up review of these exact source hashes approved F-01/F-02 as resolved, with no remaining findings.

## Final planned regression

- Node list: `plan-node-list.txt`, SHA-256 `9d17abe0333a163038bcf67c59927ae48ae79f8a838c1c109518e417f7cd62d8` (25 exact node IDs: 21 maintenance-chain nodes plus four plan-assigned existing AppServer nodes).
- Command: set `PYTHONPATH` to the repository root, then run `pytest -q -p no:cacheprovider --basetemp <unique %TEMP% path> --junitxml worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F7-followup/planned-regression.junit.xml` with all 25 node IDs from `plan-node-list.txt`.
- Start: `2026-10-20T12:41:58.6657158Z`.
- Expected: all 25 plan-assigned focused regression nodes pass against the reviewed product and test source hashes above.
- Actual output: `......................... [100%]`, `25 passed in 5.28s`.
- Exit code: `0`.
- End: `2026-10-20T12:42:04.9833967Z`.
- Log: `planned-regression.log`, SHA-256 `cd1f79013a3984e1d99595a19efc744d81e7508af359623ea3614513905c9960`.
- JUnit: `planned-regression.junit.xml`, SHA-256 `8483767ceb5230437133b03a35fb9c0e5c6e2ba0b120591dd58ac9ad18ae4d0f`.
- An initial suite invocation passed the node-list explanatory heading as if it were a test ID and exited `4` with `no tests ran`; it did not execute a node. The final command filtered the exact test-node entries and ran the 25-node plan regression once.
