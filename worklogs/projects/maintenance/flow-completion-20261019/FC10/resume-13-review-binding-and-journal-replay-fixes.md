# FC10 resume 13 follow-up: report review binding and journal replay

## Same-job observation

The only resumed launch remained `historical-9e7f0c175cc4c874c955a5dab29c4427` for job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`, target date `2026-10-17`, with `source_run_id=null`. Resume 13 created report attempt 7 and a complete, hash-bound fallback result/HTML. Publication did not happen and the job did not complete because publication returned `report_review_binding_invalid`.

The saved review truthfully said that independent review was unavailable. The result was `controller_fallback`, `review_unavailable`, `pending`, and `publish`, with reviewer `controller-fallback-review-unavailable` and status `unavailable`. The database review row matched the same report version. The publication guard nevertheless accepted only the configured AI reviewer id, so it rejected this exact controller fallback. No report approval was claimed.

## Independent product review r1 and correction

Review manifest `report-review-unavailable-publication-review-target.json` SHA-256 `adce6774960a15ff198518155ab9cc0e97cefd92b447b77e30fd2f9df5ae4915` matched the reviewed product source SHA-256 `7186adc923d253c362b904f74e8cb016d1ad2adc37a800db508834788cb991e7`. `/root/fc08_product_review` returned changes requested with one Medium finding: an ordinary configured reviewer could pass when the persisted database review row was missing. The exact review is in `report-review-unavailable-publication-review-r1.json` and `.md`.

A regression test first reproduced that finding: normal reviewer + missing row incorrectly returned true. The publication binding helper now requires a dictionary review row on both ordinary and controller-unavailable paths, then matches the persisted version, reviewer id, and status to the attempt and result. Exact controller fallback constraints and ordinary reviewer identity remain unchanged.

## Failure journal replay observation and correction

Resume 13 also replayed older fallback-journal events into the maintenance operation ledger. Replay repeatedly failed for source events whose `capture_location` began with `_`, such as `_persist_minimal_daily_report`. `replay_failure_journal` forwarded that value unchanged; the store's `_safe_id` requires an alphanumeric first character and raised `operation_capture_location_invalid`. The failure was captured again in the fallback journal, but each later invocation retried the original rows and repeated the same secondary failure.

The fix normalizes only the replay copy of a leading-underscore capture location to `symbol:<original>`. The append-only fallback source remains unchanged, and SQLite `INSERT OR IGNORE` plus the regression test confirm a successful second replay does not duplicate the stored event. The focused test failed before the code fix and passed after it.

## Verification

- Report publication focused recovery/binding cases: 10 passed, 27 deselected.
- Report publication module: 37 passed.
- Legacy failure-journal replay focused cases: 2 passed, 12 deselected.
- Failure-journal test module full run: 12 passed and 2 unrelated contract cases failed. `test_c59_contract` expected `dispatch_complete` but observed `maintenance_incomplete`; `test_c50_contract` expected `deployed` but observed `blocked`. This run did not isolate those two unrelated workflows on the prior version, so causality is not claimed. The raw console and JUnit remain preserved in this directory.
- Expected red evidence: `report-normal-review-missing-db-red.*` and `failure-journal-leading-underscore-red.*`.
- Green focused/full evidence: `report-review-binding-green-r2-*.*` and `failure-journal-leading-underscore-green-*.*`.
- Focused JUnit and console files are immutable captures; their exact hashes are recorded in `report-review-binding-and-journal-replay-review-target-r2.json`.

No production action was taken after resume 13. The same job remains incomplete. A new fresh independent product review is required for the updated publication and journal replay source hashes before commit, official runtime apply, and same-job resume 14.
