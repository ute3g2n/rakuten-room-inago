# SC05 F3 regression follow-up record

## Scope and immutable prior records

This follow-up is stored separately at `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F3-followup/`; the existing SC05 `test.md`, `test.log`, `junit.xml`, and node list were not overwritten. It verifies the newly added F3 regression and reruns the complete SC05 focused suite plus four plan-assigned existing SCT04/SCT08 nodes. No FC01–FC11 node was executed.

Fixed identity in the synthetic job fixtures: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`, launch `historical-9e7f0c175cc4c874c955a5dab29c4427`, target date `2026-10-17`, `source_run_id=null`. No persistent runtime job, Daily, worker, production/daily database, credentials, drafts, 10/19 data, remote, or posts were accessed. All fixtures used the attempt-specific temp directory below.

## Source versions

- Plan SHA-256: `ca8ae7acd1fb5d0a1a6b84303bb501f0881d172762c539471e79be7dc7ed3a30`
- `automation/inago/maintenance/skill_chain.py` SHA-256: `a8fe46b191dab88924eedfe01af92984a9eb225840e5a83f77d7339be533ab51`
- `tests_inago/test_maintenance_skill_chain.py` SHA-256: `3338f570147c9a667432bf947dcdfb8daa1dcf702fcde787f903a40b4a627d49`
- `automation/inago/app_server.py` SHA-256: `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`
- `tests_inago/test_maintenance_ai.py` SHA-256: `f3c97ae9a65707953113b7b96731251394d146d18e4d48c32310546837d41a26`
- HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`; branch `main`; tracking `origin/main`.

## F3 regression node

`tests_inago/test_maintenance_skill_chain.py::test_resume_after_final_review_reuses_author_and_reviewer_outputs` starts with a successful synthetic same-job chain, saves the job's final `report.html` and `html-review.md` bytes, simulates only a mismatched public readback, and checks that resume selection returns role 8 with reason `skill_chain_publication_bytes_mismatch`. It also checks that the job's author HTML and final review bytes remain unchanged. This exercises selection/resume position and preservation in the synthetic fixture; it does not claim a live job resume.

## Collection and execution

Minimal collection command: `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py --collect-only -q`; started `2026-10-20T14:28:50.0002542+09:00`, ended `2026-10-20T14:28:50.8420672+09:00`, exit `0`, 21 nodes. Exact collected IDs are in `node-list.txt` and `node-list.json`.

Full SC05 focused plus four mapped existing nodes command:

```text
.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes tests_inago/test_maintenance_ai.py::test_c26_app_server_checks_model_and_effort_before_thread_start tests_inago/test_maintenance_ai.py::test_c24_app_server_passes_role_instructions_and_settings_to_thread_start tests_inago/test_maintenance_ai.py::test_c27_app_server_log_is_confined_to_host_owned_root -q --basetemp %TEMP%/inago-sc05-f3-20261020-1530 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F3-followup/junit.xml
```

Started `2026-10-20T14:28:50.8783611+09:00`, ended `2026-10-20T14:28:57.1264360+09:00`, integer exit `0`; expected 25 cases to pass and observed `25 passed in 5.50s`, zero failures/errors/skips. JUnit reports tests=25 / failures=0 / errors=0 / skipped=0. Console is `test.log`; JUnit is `junit.xml`; complete new-suite collection and combined exact nodes are recorded in `node-list.txt`/`node-list.json`.

The newly added F3 node passed as part of the 21 focused skill-chain tests. The four plan-mapped existing nodes also passed:

- SCT04 `test_c26_transport_log_failure_still_interrupts_and_closes`: timeout error remained the surfaced failure while both interrupt and close cleanup executed despite a logger error.
- SCT08 `test_c26_app_server_checks_model_and_effort_before_thread_start`: paginated capability query found the requested model but rejected unsupported effort before thread start.
- SCT08 `test_c24_app_server_passes_role_instructions_and_settings_to_thread_start`: role instructions, model, service tier, and sandbox were passed in thread/start.
- SCT07/SCT08 test coverage `test_c27_app_server_log_is_confined_to_host_owned_root`: in-root log creation succeeded and out-of-root logging was rejected without creating the outside path. This does not substitute for SC06 independent product review.

The plan-linked FC11 and FC07 nodes remain `NOT_RUN` due the explicit prohibition on running any FC01–FC11 node. SCT06 remains assigned to the daily implementer/tester path and was not run here. SCT07 review verdict and SCT09 same-job operations remain separate and unclaimed.
