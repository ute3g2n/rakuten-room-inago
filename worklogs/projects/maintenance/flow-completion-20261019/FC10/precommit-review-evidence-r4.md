# FC10 R4 pre-commit review-evidence audit

- R4 review target SHA-256: `1476d687f26f60af85cb4a8103ff09a5206a72674e08d1a04e1280e7af660a5f` (matches recorded target hash).
- Product source hashes: all match the R4 target.
- Test integrity hashes: all match the R4 target.
- Owner map hash: matches.
- Independent product review result SHA-256: `c6f499d09edab1af109d2d05abc4fedb2876c48b1b02f315e644572e17ab6ab4`.

The local-evidence manifest has 1 current hash mismatch(es). This audit records them instead of claiming a complete manifest match:

- `FC10/terminal-handoff-and-report-evidence-path-fix.md`: expected `5ebb14de746d0a7630703f101263f66a5ae21ca52deb09c37b0f00894892acbe`, current `c9b06efef7cfb10cfa31c9ce71a3509c89d8812fbc7b646bf43a5d062df33d81`.

The independent R4 product review approved zero findings and its recorded product source hashes match the current code. The R4 target local-evidence manifest has a supplemental narrative hash drift for terminal-handoff-and-report-evidence-path-fix.md because a review-result note was added after target creation and the earlier text was then restored with one line ending rather than the exact snapshot bytes. The R4 addendum retains the review-result note. This is not a product-source change or a review-scope source mismatch; the full local-evidence manifest is not claimed to match.

No product source changed after R4 review. The mismatch is supplementary narrative evidence, not code reviewed by R4.
