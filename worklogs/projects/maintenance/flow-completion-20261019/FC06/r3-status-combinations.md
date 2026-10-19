# R3-F1 status-combination correction

Independent review source: `worklogs/projects/maintenance/flow-completion-20261019/FC07/html-independent-followup-review-r3.md` (SHA-256 `095b33891177ab49cf23f37914125a4f76ff2815ef854fab81d197266e50f713`), verdict `changes_requested`.

The restore and terminal readback checks previously accepted `reviewed` only with `complete` and `approved`. That rejected truthful fallback results whose reviewer returned `changes_requested` or `rejected`, despite the report being pending. FC06 now accepts exactly these combinations: (1) `reviewed` + `complete` + `approved`; (2) `reviewed` + `pending` + (`changes_requested` or `rejected`); (3) `review_unavailable` + `pending` + `unavailable`. These checks preserve the reviewer result and never promote a result to approval. Pending results continue through publication/reconciliation.

Changed sources: `automation/inago/maintenance/production_workflow.py` SHA-256 `481b4623c8a0a9b5aa100fa41d20abc521c9427c8ca96663ced2be989de4a273`; `automation/inago/maintenance/workflow_reconciliation.py` SHA-256 `fad8b8038b235c23ce8a07e228ff215c497a60d8ffb640d40c22f16738fa111c`. `python -m py_compile` for both files passed. FC06 did not run pytest. FC07 owns focused parameterized tests for `changes_requested` and `rejected`; results are pending. Fresh independent review is pending.

Target manifest: `worklogs/projects/maintenance/flow-completion-20261019/FC06/fc07-html-followup-target.json` SHA-256 `261aa9d333707060fcc81b894c36d67ff450abecb0d6aedf9f54442ba186a287`. Owner map SHA-256 remains `6a1cc69c0c1c112b9d8ee1eb9273e3e7652f16da17d066ef2775572396ff78f3`.
