# FC07 / FCT09 独立HTMLフォローアップレビュー r4

## 判定

**approved** for the exact 17-file target manifest. Manifest SHA-256: `261aa9d333707060fcc81b894c36d67ff450abecb0d6aedf9f54442ba186a287`; all 17 listed file hashes match. This approval covers the four synthetic FC06 HTML examples and their report fallback/resume code, not the actual 2026-10-17 report.

Identity checked: job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`; launch `historical-9e7f0c175cc4c874c955a5dab29c4427`; target date `2026-10-17`; source run `null`; incident `352492da-ac58-4680-b1fd-fe5f24000229`.

## Prior findings and r3 R3-F1

All seven original HTML findings, F1–F4, and r2 F1–F3 are resolved in the exact hashed target. Visible synthetic labels, truthful review/publication state, expected/actual/impact/recovery/change/test scope, known/unknown boundaries, instrumentation reasoning, event categories, existing evidence references, and separation of workflow completion from incident resolution are present. Review findings are saved against the HTML version/hash actually reviewed; a final generated fallback gets its own unavailable record. Missing identity is preserved in the report/input while validated storage identity is separately derived, allowing input and substantive fallback HTML persistence.

**R3-F1 is fixed.** Fallback result creation preserves `rejected` and `changes_requested` as `stage_status=reviewed`, `report_status=pending`, `next_action=publish`; it does not promote either result to approval. Completed fallback restore accepts approved only with complete, accepts `changes_requested` or `rejected` with pending, and accepts unavailable only with pending plus `review_unavailable`. Reconciliation uses the same status combinations. Publication validates the exact review/version/hash but does not demand approval, allowing the pending report to continue through publication. The focused test source also covers restoration of rejected and changes_requested states and a decoy report-input file; tests were not run as part of this read-only review.

Exact fallback input restoration uses `result.report_input.path` and `sha256`, restricts it to this job/attempt and the hash-versioned input filename, then checks the fallback schema and bindings. A decoy input cannot be selected by lexical order. Versioned artifacts are immutable through the save API's exclusive create behavior and strict report-stage filename allowlist.

## HTML and presentation checks

The four examples explain in plain Japanese what happened, what was checked, what changed, what remains unknown, and what happens next. Fixture examples are not presented as production evidence. Body text keeps internal IDs, statuses, reason codes, hashes, and version keys in the appendix. I found no secrets or personal data. All fragment links resolve uniquely and every cited local source path exists. Viewport, wrapping, and print CSS are present.

Browser and print rendering were **not performed**. This is not a review of an actual production incident report.

Reviewed HTML SHA-256 values:

| File | SHA-256 |
|---|---|
| `通常.html` | `31ecb4e0d115b3fd82cfa789e5cf3862a5e126f1e0a663d91e3336eeeeb2c8d4` |
| `全項目不明.html` | `751bafa96778e878e3cea113b4d9e64c6758fa83f983ce4b6815af7bde881059` |
| `変更なし.html` | `514db4f1fd61b03201f231074c4a871cf669df0a30765d676ec49f235eb10ff8` |
| `通常renderer失敗.html` | `1171f80698567fe6ae19b041944d7d4f19b9f01846e15e9d6272cd68facdd66b` |
