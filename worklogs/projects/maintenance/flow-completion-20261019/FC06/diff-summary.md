# FC06 差分概要

- `reporting.py`: report専用一時Git indexでHTML一ファイルだけをcommit object化してpushし、現行worktree/index/branchを更新しない。既存report hashとremote readbackを維持。
- `production_workflow.py`: 通常report段階の失敗・例外からfallback生成へ進み、reviewerを実起動して、review不能も記録してpublicationを必ず呼ぶ。通常report attempt completionは存在するStore APIを使う。report公開後はworkflow jobをcompleteにし、reportがpending/blockedならincidentはin_progressのまま。
- `report_fallback.py`: 平易なHTMLと通常/全unknown/変更なし/renderer失敗の合成説明例を生成。
- 3 report schemas、作成ルール、ひな形、report skill/roles、受入/trace/辞書/追跡/log/settings、requirements-source HTMLとF12を同期。
- 実reporter/reviewerの今回セッション、本番report、pytest、DB、Git公開は未実施。例示reviewは作者の静的確認で、独立reviewではない。


## FCT08 / FCT09 follow-up revision (2026-10-20)

- `production_workflow.py`: fixed unbound AI wrapper context and attempt lease validation defects; fallback artifact names are content-hash versioned; reviewer `changes_requested` launches one real author revision with exact findings and one reviewer re-run; author failure or repeated findings emits a truthful pending HTML and continues publication.
- `report_fallback.py`: missing identity fields render as unknown rather than raising; examples and fallback identify event-origin class and preserve unresolved review findings.
- `investigation.py`: report stage alone permits the five exact hash-versioned fallback filenames; immutable create/no-overwrite behavior and every other stage allowlist remain intact.
- `workflow_reconciliation.py` and report-result schema: same-job/same-attempt safe versioned HTML path and hash readback.
- Four examples regenerated, nonexistent samples refs replaced by existing repo documents/code, independent review findings and follow-up scope saved.
- FC07 exact transport-failure parameter passed 1/1; evidence and all tested source hashes recorded in result/static-checks/source-hashes. New HTML independent review is still pending; acceptance is not claimed.

R2: review response records are version/hash-specific; a final not-reviewed HTML has a separate unavailable record. Fallback storage uses verified job/launch identity only for artifact context while preserving incomplete supplied identity in body/input. Resume now loads and binds the exact result.report_input reference and validates the fallback result/output/review before restoring.
