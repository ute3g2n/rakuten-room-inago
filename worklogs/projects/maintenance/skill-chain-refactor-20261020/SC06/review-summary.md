# SC06 independent product review summary

対象は固定workspace `C:/project/rakuten-room-inago`、job `job-historical-9e7f0c175cc4c874c955a5dab29c4427` / launch `historical-9e7f0c175cc4c874c955a5dab29c4427` / date `2026-10-17` / source_run_id `null`。

## Review history

| Record | Reviewed product hash | Outcome | Follow-up |
|---|---|---|---|
| `independent-product-review.md` | `75e8815002d425c93ee4ea3cb541a000fb356ab8e122142a1bff26b557a58e89` | changes_requested: F1 same-author recovery, F2 exact-path read event | Fixed in later code versions; original report retained. |
| `follow-up-review.md` | `174a5d93d7e8638252ec224cd971796e546a4c5e5171df8fbaa9ff55fc18b4e7` | changes_requested: F3 resume after final author/reviewer replacement | Fixed and tested; report retained. |
| `follow-up-review-v2.md` | `a8fe46b191dab88924eedfe01af92984a9eb225840e5a83f77d7339be533ab51` | changes_requested: F4 final `report.md` output hash not verified | Fixed and tested; report retained. |
| `follow-up-review-v3.md` | `5b82597fa9f2621796397b4bc05ee52b0020259751a294769fd26362ac535447` | **approved, no findings in review scope** | Exact final review hash `eb76e00460a80f39b17cf24ec6a1c36ec05ec370d8cd9d5bd8299c4b6a2633a6`. |

The final reviewed product tree also included `app_server.py` `e64291f2875d9043222963bbe81b058aa84929654ba73630885a4d3861686f71`, `cli.py` `f9831eacc3ee700e48bb010df4b2ec6a2ea3eea8a032406601bb0e79bc967cfe`, `worker.py` `e22344de5c15b0295695d60d901588a9b93214cc778cb08aab4db97d71f7de12`, and `historical.py` `17a92daf2e83a3677b93c5595dda338eb94cf9c072c612d51471d441867bd4b6`.

## Final linked test evidence

SC05 final applicable source version: runner `5b82597fa9f2621796397b4bc05ee52b0020259751a294769fd26362ac535447`, focused test source `ac90004ce13a594d9fbac23a49220973e20bc7ac811f2ae32cd5aa08c2303d14`, and app-server tests `f3c97ae9a65707953113b7b96731251394d146d18e4d48c32310546837d41a26`. F4 follow-up executed 21 focused skill-chain cases plus four plan-mapped existing SCT04/SCT08 nodes: 25 passed, 0 failed/errors/skipped, exit 0, 2026-10-20 14:32:02.8521034–14:32:09.2238812 JST. Full command, node list and JUnit are in `SC05/F4-followup/`.

The F3 regression first establishes a complete synthetic chain, alters only public readback, and verifies resume selects role 8 while preserving final HTML and review bytes. It then changes `report.md` while retaining the final HTML marker and verifies resume returns to role 6. This is isolated synthetic evidence, not a production-job resume.

FC01–FC11 were not run. SCT06 (same-job daily diagnostic diff test) and SCT09 (same-job live operation) are still outstanding. This product review does not certify production apply, same-job completion, or publication.

## Final version review — v4 (2026-10-20)

| Record | Reviewed product hash | Outcome | Follow-up |
|---|---|---|---|
| `follow-up-review-v4.md` | `3e8e3f34cd87739ef2ef57f88bc76f0fcd1fc93fa51a4fcea50ccd79e10d9e12` | **approved, no findings in review scope** | Review file SHA-256 `3eccc4f42453abf63dbe6bb3bd4841e73bc8338704aaf1a43ffb29cc4ffff4ab`. F5 evidence: 25/25 PASS against runner `3e8e3f34...` and test source `6c2d480d...`. |

The approved v4 review read the F5 follow-up record and verifies F1–F4, fixed identity, excluded date, event-backed Markdown reads, resume/publication boundaries and the secret boundary for the exact final product hash. It is a product review only; production application, live-job execution, same-job daily instrumentation, final HTML review and publication remain open for SC07–SC10.
## Final source_run_id parser review — v5 (2026-10-20)

| Record | Reviewed product hash | Outcome | Follow-up |
|---|---|---|---|
| `follow-up-review-v5.md` | `fcc23783b14f626c97d32c1f804c9dad5f0f95f1a39a8db873aca5c2b2b3976a` | **approved, no findings in review scope** | Review file SHA-256 `8b2b3109b6e906c5cc7331c5722f687d0508943a68d99a720524ba6faf063ff5`; F6 25/25 PASS against test source `bceb82fcfaa2c0d408f71f3c4451c173c486828319b60c59048e0527edfbba1b`. |

SC06 v5 separately verified that the actual incident line retains `source_run_id=null`; suffix parsing captures only the backtick-delimited value and exact comparison rejects a different ID. It also reread the F6 evidence and carried forward the earlier F1–F4 and privacy-boundary review. This code approval does not certify runtime apply or live operation. The failed pre-role invocation did not create calls or job outputs; SC08 records it separately.