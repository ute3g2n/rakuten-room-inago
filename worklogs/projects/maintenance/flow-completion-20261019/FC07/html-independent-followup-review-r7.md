# FC07 / FCT09 独立HTMLフォローアップレビュー r7（最終版）

## 判定

**approved** for the exact target manifest `worklogs/projects/maintenance/flow-completion-20261019/FC06/fc07-html-followup-target.json`, SHA-256 `336e85f9e5972b19aa48b2dadf7b510d9132cef1dfb992bc4e8ecf7e7201475a`. The manifest's 18 source files all match their listed hashes. The only source change since approved r6 is plan §14; all HTML and product-code hashes remain unchanged.

Identity: job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`; source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`; target date `2026-10-17`; source run `null`; incident `352492da-ac58-4680-b1fd-fe5f24000229`.

## Plan §14 and R3-F1

The updated §14 correctly records the synthetic rejected/changes_requested resume tests as passed against the product source hashes that they exercised. It distinguishes those tests from publication, and keeps the earlier r6 approval bound to its exact 19cdf manifest as historical evidence. It also states that its post-r6 plan edit is not evidence of review of the previous plan hash. Those claims agree with the saved focused results and r6 record. The new manifest binds the updated §14 bytes and does not claim this r7 review result in advance.

R3-F1 remains fixed: rejected and changes_requested results preserve their reviewed version/hash, remain pending, and keep `next_action=publish`; same-job restore and terminal reconciliation accept those non-approved states without promoting them. Publication can proceed while retaining the pending review outcome. The focused synthetic cases preserve exact state on restore; they do not claim to have published.

All earlier original and follow-up findings remain resolved, including hash-specific review sidecars, durable substantive HTML with incomplete identity, and exact `result.report_input` path/hash/schema restoration in the presence of a decoy. Versioned same-attempt artifacts have restricted names and immutable persistence.

## HTML and presentation

The four synthetic examples visibly state that they are not actual 10/17 findings. Plain Japanese body text covers what happened, what was checked, what changed, what remains unknown, and what happens next. Event classification, evidence, review/publication state, workflow completion, and incident resolution are kept distinct. Internal identifiers and technical codes remain in the appendix. No secrets or personal data were found.

All fragment links resolve uniquely; all local code-reference paths exist. Viewport, text wrapping, and print CSS are present. Browser and print rendering were **not performed**. This review does not cover the actual production report.

Reviewed HTML SHA-256 values:

| File | SHA-256 |
|---|---|
| `通常.html` | `31ecb4e0d115b3fd82cfa789e5cf3862a5e126f1e0a663d91e3336eeeeb2c8d4` |
| `全項目不明.html` | `751bafa96778e878e3cea113b4d9e64c6758fa83f983ce4b6815af7bde881059` |
| `変更なし.html` | `514db4f1fd61b03201f231074c4a871cf669df0a30765d676ec49f235eb10ff8` |
| `通常renderer失敗.html` | `1171f80698567fe6ae19b041944d7d4f19b9f01846e15e9d6272cd68facdd66b` |

Previous independent reviews r4, r5, and r6 are retained as intermediate records; this r7 result binds the exact final manifest above.
