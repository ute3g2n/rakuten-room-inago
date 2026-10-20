# SC05 F5 follow-up record

## Scope and versions

This immutable follow-up is separate from F3/F4 records. It reruns the 21-node focused maintenance-chain suite and the four planned SCT04/SCT08 existing nodes after the whitespace-only EOF cleanup. No FC01–FC11 node was run. Synthetic identity: job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`, source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`, target date `2026-10-17`, source_run_id `null`.

- Plan SHA-256: `6351570e94dcf6050081f374d2a58c51129b15111068dbf1cee1e442cb58a633`
- `automation/inago/maintenance/skill_chain.py`: `3e8e3f34cd87739ef2ef57f88bc76f0fcd1fc93fa51a4fcea50ccd79e10d9e12`
- `tests_inago/test_maintenance_skill_chain.py`: `6c2d480d29e33d19c57dacd2251a5df20bd01dbbe3d47073769ff7d89951328f`
- `automation/inago/app_server.py`: `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`
- `tests_inago/test_maintenance_ai.py`: `f3c97ae9a65707953113b7b96731251394d146d18e4d48c32310546837d41a26`
- HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`, branch `main`, tracking `origin/main`.

## F3 regression under F4 implementation

`test_resume_after_final_review_reuses_author_and_reviewer_outputs` first verifies a public readback mismatch selects role 8 while preserving final author HTML/review. It then alters `report.md` after final author revision, preserving the marker text, and expects role 6 (`role_call_incomplete`). This is a synthetic resume-position/preservation test; it does not claim live-job resume.

## Collection and execution evidence

Collection command: `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py --collect-only -q`; started `2026-10-20T14:37:47.4037244+09:00`, ended `2026-10-20T14:37:48.3187322+09:00`, exit 0, 21 nodes.

Exact focused command:

```text
.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes tests_inago/test_maintenance_ai.py::test_c26_app_server_checks_model_and_effort_before_thread_start tests_inago/test_maintenance_ai.py::test_c24_app_server_passes_role_instructions_and_settings_to_thread_start tests_inago/test_maintenance_ai.py::test_c27_app_server_log_is_confined_to_host_owned_root -q --basetemp %TEMP%/inago-sc05-f5-20261020-1600 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F5-followup/junit.xml
```

Started `2026-10-20T14:37:48.3531388+09:00`, ended `2026-10-20T14:37:54.7238278+09:00`, integer exit 0. Expected all 25 selected cases to pass; observed `25 passed in 5.59s`, zero failures/errors/skips. JUnit reports 25 tests / 0 failures / 0 errors / 0 skipped. The 21 complete skill-chain node IDs and the four exact existing node IDs are in `node-list.txt` and `node-list.json`; console and JUnit are `test.log` and `junit.xml`.

Four additional cases passed: SCT04 transport logger failure preserved timeout while calling interrupt and close; SCT08 model/effort capability check before thread start; SCT08 C24 role instructions/settings passed through to thread/start; SCT07/SCT08 C27 host-root log confinement (test coverage only, not independent review approval). FC11/FC07 remain NOT_RUN due explicit FC01–FC11 prohibition. SCT06 daily/worker, SCT07 independent review, SCT09 same-job operation are separate and unclaimed.

No persistent runtime job, Daily, worker, DB, credentials, drafts, 10/19 data, remote or posts were accessed or modified; pytest fixtures stayed under the attempt-specific temp directory.
