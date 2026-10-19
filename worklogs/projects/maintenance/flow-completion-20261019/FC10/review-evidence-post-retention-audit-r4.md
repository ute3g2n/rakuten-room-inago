# FC10 post-retention review-evidence audit R4

- R4 target SHA-256: `1476d687f26f60af85cb4a8103ff09a5206a72674e08d1a04e1280e7af660a5f`; reviewer result: approved, zero findings (`c6f499d09edab1af109d2d05abc4fedb2876c48b1b02f315e644572e17ab6ab4`).
- Product source hashes: both match.
- Test integrity source and owner-map hashes: match.
- Current local-evidence mismatches: 2.
- `FC10/terminal-handoff-and-report-evidence-path-fix.md`: target `5ebb14de746d0a7630703f101263f66a5ae21ca52deb09c37b0f00894892acbe`, current `c9b06efef7cfb10cfa31c9ce71a3509c89d8812fbc7b646bf43a5d062df33d81`.
- `FC10/redacted-execution-evidence.json`: target `dfc831886b402edab3bcb5adc82be522a30fac78add37266df094650232433c4`, current `32d37c3c661e39e83893df9a7d58b93373fa314ec2a3e43ea41f1549219ba023`.

The independent product review remains bound to the exact current product source hashes and its approved zero-finding outcome. Two supplementary local-evidence references now differ from R4 target bytes: the repair narrative was restored with a different line ending, and the redaction map now records the current ignored storage location of unchanged raw inputs. This audit does not claim full R4 local-evidence manifest parity. The R4 reviewer did not review test files or reporting.py renderer/allowlist internals.
