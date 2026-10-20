# FC11 resume 26 publication conflict and approved fallback replacement

## 対象と結果

- job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source launch: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target date: `2026-10-17`; `source_run_id=null`
- deployed code: `b12fa875ca6069193077ca90f28e512e239ab935`
- same-job resume: 26
- workflow invocation: `workflow-3dc239a461a948c19dbea9cc7c24263a`

resume 26 ran the specified historical Worker and recorded every stage. The Worker process exited 0, but its workflow result was `maintenance_incomplete`; the job ended `failed`. The report fallback HTML received a real independent review (`approved`, 0 findings) for the exact HTML SHA-256 `35890643079fd6ca1d3fbe8e0e3939039bcc57155690b85c81c801ca0c0b4a57`. Publication then failed with `report_publication_path_conflict`, so this run did not publish the new HTML and did not satisfy terminal completion.

## Cause and correction

The previous public report was a controller-generated fallback that had later received an independent approval. The replacement guard recognized an approved report only for a reporter-origin result, or a controller fallback whose review was unavailable. It therefore refused a prior controller fallback with a genuine approval, despite the exact same-job identity, result version/hash, database review, publication sidecar, remote ancestry, and blob checks being present.

The guard now recognizes this additional case only when `stage_status=reviewed`, `report_origin=controller_fallback`, `reviewer_id=rr-inago-incident-report-reviewer`, and `review.status=approved`. The existing same-job identity, attempt/result binding, persisted review, sidecar, remote ancestry and exact blob checks remain required. Existing review-unavailable fallback compatibility was not changed.

The regression was run red before the source change and green after. The new scenario verifies that a controller fallback with real independent approval is eligible for the already-existing exact-match validation. Focused command:

```text
.venv-inago\Scripts\python.exe -m pytest tests_inago/test_maintenance_report_publication_stage.py -k "only_same_job_proven_report_publication_can_authorize_blob_replacement or fc10_report_reviewer_prompt_limits_findings_to_requested_four_facts" -q
2 passed, 46 deselected
```

Independent read-only product review: **APPROVE, 0 findings**, production source SHA-256 `aa169d2b40b32065935e246a1fd7dc0288243beae90eb583f4da5bf06b1d8ae3`. The reviewer specifically confirmed exact identity/version/hash/review/sidecar/ancestry/blob checks remain required; the pre-existing review-unavailable compatibility branch remains unchanged.

`py_compile` and `git diff --check` passed; the latter emitted only the repository's CRLF/LF normalization warning. This correction is not yet committed, pushed, or applied to the official runtime. The same job is not complete. Next: ownership/secret audit, selected commit and push, official fixed-root runtime apply/read-back, then resume the same job and verify the report is reviewed, published byte-for-byte, and properly registered at terminal state.

## Evidence

- `same-job-resume-26-cli.json`
- `same-job-resume-26-worker.json`
- `same-job-resume-26-worker.console.txt`
- `same-job-resume-26-publication-failure.json`
- `same-job-resume-26-publication-fix-product-review-r1.md`
- `runtime-apply-b12fa87-readback.json`
- `same-job-preflight-b12fa87.json`
