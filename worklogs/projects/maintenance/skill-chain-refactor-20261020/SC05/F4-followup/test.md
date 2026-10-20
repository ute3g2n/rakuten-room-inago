
## F4 follow-up (immutable additional run)

### Scope and versions

This record is separate from `SC05/F3-followup/`; no prior SC05 record was overwritten. It records the full SC05 focused suite plus the four plan-mapped existing SCT04/SCT08 nodes after the F4 product change. The fixed synthetic identity remains job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`, launch `historical-9e7f0c175cc4c874c955a5dab29c4427`, date `2026-10-17`, source_run_id null. No persistent runtime job, Daily, worker, DB, credentials, drafts, 10/19 data, remote, or posts were accessed or modified.

- Plan SHA-256: `ca8ae7acd1fb5d0a1a6b84303bb501f0881d172762c539471e79be7dc7ed3a30`
- `automation/inago/maintenance/skill_chain.py`: `5b82597fa9f2621796397b4bc05ee52b0020259751a294769fd26362ac535447`
- `tests_inago/test_maintenance_skill_chain.py`: `ac90004ce13a594d9fbac23a49220973e20bc7ac811f2ae32cd5aa08c2303d14`
- `automation/inago/app_server.py`: `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`
- `tests_inago/test_maintenance_ai.py`: `f3c97ae9a65707953113b7b96731251394d146d18e4d48c32310546837d41a26`
- HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`; branch `main`; tracking `origin/main`.

### Regression and result

`tests_inago/test_maintenance_skill_chain.py::test_resume_after_final_review_reuses_author_and_reviewer_outputs` now verifies two invalidation paths in the synthetic same-job fixture: public readback mismatch preserves final author HTML/review and selects role 8; changing `report.md` after final author revision while preserving the marker selects role 6 with reason `role_call_incomplete`. This is resume-position selection and byte-preservation test evidence, not a live-job resume.

Minimal collection command `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py --collect-only -q`: started `2026-10-20T14:32:02.0588512+09:00`, ended `2026-10-20T14:32:02.8222802+09:00`, exit 0, 21 new focused nodes.

Full run command:

```text
.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py tests_inago/test_maintenance_ai.py::test_c26_transport_log_failure_still_interrupts_and_closes tests_inago/test_maintenance_ai.py::test_c26_app_server_checks_model_and_effort_before_thread_start tests_inago/test_maintenance_ai.py::test_c24_app_server_passes_role_instructions_and_settings_to_thread_start tests_inago/test_maintenance_ai.py::test_c27_app_server_log_is_confined_to_host_owned_root -q --basetemp %TEMP%/inago-sc05-f4-20261020-1540 --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F4-followup/junit.xml
```

Started `2026-10-20T14:32:02.8521034+09:00`, ended `2026-10-20T14:32:09.2238812+09:00`; exit 0; expected all 25 selected cases to pass, actual `25 passed in 5.68s`; JUnit: 25 tests, 0 failures, 0 errors, 0 skipped. The 21 exact current new-suite nodes and four additional exact plan nodes appear in `node-list.json` and `node-list.txt`; complete console is `test.log` and JUnit is `junit.xml`.

The four existing planned cases passed: SCT04 transport log failure still interrupts/closes; SCT08 model/effort check before thread start; SCT08 C24 role instructions/settings in thread/start; SCT07/SCT08 C27 host-root log confinement. C27 is only focused test evidence, not SC06 review approval. FC11/FC07 remain NOT_RUN under the explicit prohibition. SCT06 daily work and SCT09 same-job operation remain assigned elsewhere and are not claimed here.
