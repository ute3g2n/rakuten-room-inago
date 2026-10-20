# SC05 F6 follow-up record

## Scope and versions

Fresh immutable rerun after the F6 source_run_id incident-line parsing fix. The full 21-node maintenance-chain suite plus four previously mapped SCT04/SCT08 existing nodes ran once. No earlier SC05/F3/F4/F5 record was overwritten. Synthetic identity: job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`, source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`, date `2026-10-17`, source_run_id `null`.

- Plan SHA-256: `27febad6aa79025a88cbb29679969952f7c2ac27094f4b8e9ae9a0c2a8bbd284`
- Runner SHA-256: `fcc23783b14f626c97d32c1f804c9dad5f0f95f1a39a8db873aca5c2b2b3976a`
- SC05 test SHA-256: `bceb82fcfaa2c0d408f71f3c4451c173c486828319b60c59048e0527edfbba1b`
- AppServer SHA-256: `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`
- Existing AI test SHA-256: `f3c97ae9a65707953113b7b96731251394d146d18e4d48c32310546837d41a26`
- HEAD `dadffcd01ee7cc7672ad7f7c446839128dbe12e7`; branch `main`; tracking `origin/main`.

The test fixture's incident input uses the current explanatory source_run_id line: `- 日次run: `null` のまま。既存値を推測で埋めない。`. The runner accepted this line and preserved `source_run_id=null` through the chain-focused tests. No persistent runtime job, Daily, worker, database, credentials, drafts, 10/19 queue/history, external remote, or posts were accessed or modified.

## Collection and run

Collection command: `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py --collect-only -q`.
Started `2026-10-20T14:44:31.9780917+09:00`; ended `2026-10-20T14:44:32.8445658+09:00`; exit `0`; 21 exact nodes.

Exact executed command:

```text
.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes tests_inago/test_maintenance_ai.py::test_c26_app_server_checks_model_and_effort_before_thread_start tests_inago/test_maintenance_ai.py::test_c24_app_server_passes_role_instructions_and_settings_to_thread_start tests_inago/test_maintenance_ai.py::test_c27_app_server_log_is_confined_to_host_owned_root -q --basetemp %TEMP%/inago-sc05-f6-20261020-1610 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F6-followup/junit.xml
```

Started `2026-10-20T14:44:32.8844349+09:00`; ended `2026-10-20T14:44:39.2680704+09:00`; integer exit `0`. Expected all 25 selected cases to pass; actual `25 passed in 5.65s`, failures=0, errors=0, skipped=0. JUnit is `junit.xml`; console is `test.log`; exact IDs/results and SCT mapping are `node-list.json` and `node-list.txt`.

The run includes F3's final review/public readback and final `report.md` mutation/resume selection regression, plus the four SCT04/SCT08 plan nodes: transport logging failure cleanup; model/effort preflight before thread start; C24 role/settings transmission; C27 host-root log confinement. C27 result is test coverage only, not independent review approval. FC11 and FC07 remain NOT_RUN; SCT06, SCT07 independent review, and SCT09 same-job operation remain separate/unclaimed.

The pytest basetemp was under host TEMP as specified. Repository evidence was copied byte-for-byte to a local temp archive before profile-path normalization; originals and normalized hashes are appended to `SC05/privacy-redaction-record.md`.
