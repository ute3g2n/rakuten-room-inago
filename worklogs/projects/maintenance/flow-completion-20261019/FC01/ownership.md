# FC01 所有・次工程の境界

## FC01 実際に書いたもの

FC01の書込対象を先に5 fileに限定した。production code/config/database/Scheduler、旧履歴、証拠原本は読取専用で、Gitの開始差分には何もなかった。

| File | Owner | 用途 |
|---|---|---|
| `worklogs/projects/maintenance/flow-completion-20261019/FC01/baseline.md` | controller | 実行時のrepo/runtime/DB/queue/Scheduler/input基準、限界、未実施を記録 |
| `worklogs/projects/maintenance/flow-completion-20261019/FC01/failure-path-map.md` | controller | 内側/外側の失敗・捕捉・gate・writer・永続化境界と出典を対応 |
| `worklogs/projects/maintenance/flow-completion-20261019/FC01/ownership.md` | controller | 今STEPと後続STEPのfile/operation責任境界を追跡 |
| `worklogs/projects/maintenance/flow-completion-20261019/FC01/instrumentation-backlog.md` | controller | 既存重複を除外した実装候補・試験・同一job確認を指定 |
| `worklogs/projects/maintenance/flow-completion-20261019/FC01/handoff.json` | controller | structured identity, version, outcomes, evidence refs, next stage |

## 後続のplanned ownership (FC02 owner-mapで確定する)

この節はplan §3.1の初期ownershipとFC01が読んだ呼出箇所を実行用に索引する。FC02で関数/operation/read-write/owner/execution order/output/reviewer/evidenceを`file-operation-owner-map.json`へ固定する。FC02以降で担当が実行ファイルを更新した際に、先行変更を戻さない。

| STEP | 主なfile/operation群 | Owner / required independent role | FC01からの引渡し |
|---|---|---|---|
| FC02 | plan status/handoff/schema contract; all nine role TOMLs and matching instructions; active skills/contracts; source HTML requirements + builder output; glossary/trace inventory/flows | planner/controller; separate plan reviewer | FC01 artifacts; actual schema 13/unused migration number to inspect; five max roles and parent B setting; no gate semantics; operation and ownership map |
| FC03 | `diagnostic_trace.py`, `workflow_provider.py`, `controller.py`, `store.py`, `ai_adapter.py`, `safe_file.py`, `paths.py`, `settings.py`, maintenance schemas/new migration, maintenance failure journal; daily trace boundaries only where FC02 finds uncovered operation | implementer (high worker/default until role sync); focused tester; FC08 independent product reviewer | Implement only missing operation correlation/failure chain/capture location/writer fallback/recovery; preserve existing daily SQLite classifiers |
| FC04 | `stage_handoff.py`/handoff schema, production stage author/reviewer paths, controller continuation, contracts/skills/role docs/source requirements | implementation owner assigned by FC02; stage authors are separately invoked; separate product reviewer at FC08 | All failures/unknown produce substantive artifact and next owner executes; do not let prior raw status gate |
| FC05 | `workflow_reconciliation.py`, store/attempt lifecycle, controller/worker result reconciliation, deployment verification and publication state records | assigned implementer; focused tester; separate product reviewer | Reconcile planning/report attempts left running against raw event/artifact without changing raw history; separate progress, quality, publication and terminal state |
| FC06 | `report_fallback.py`, report generation/render/publication paths, `reporting.py`, HTML structure, report rule/template, source requirements and generated contract docs | reporter and separate report reviewer must both actually run; implementer owns fallback mechanics | Minimum human-readable HTML on all author errors; reviewer observations bind exact bytes/version and author revises |
| FC07 | test/fixture files named by case plan, append-only test ledger for local synthetic tests | tester; no test-code independent review | Fault-inject every boundary on isolated fixture/test DB; prove next-stage agent launch, content, root-cause trace, resume, final publication; do not use production faults |
| FC08 | read-only changed product diff and test evidence | independent product reviewer | Must review exact implemented SHA and findings; status is not a later-stage gate; test code itself does not get an independent review |
| FC09 | task-owned files/hunks, numbered migration, official deployment/runtime switch and version/read-back | controller; production changes only through existing official APIs | selectively stage only task-owned hunks; inspect diff/secrets; commit/push tracked `origin/main`; backup/official apply/read-back; resolve current SHA mismatch without claiming before verified |
| FC10 | same maintenance job with fixed manifest identity, official resume API, attempt result/handoff/report/publish/terminal records | controller; investigator/planner/implementer/tester/reviewer/reporter all actually invoked as assigned | Same job/launch/null run; no new job, Daily launch, 10/19 processing, or draft regeneration; collect new trace and public HTML bytes/sidecar/terminal status |
| FC11 | plan §14, execution records, acceptance evidence, Git state, published report references | controller + independent end-to-end reconciliation | Check FC-A01–10 with measured DB/filesystem/remote evidence, correct gaps and resume same job; do not claim finished from trial/file presence |

## Collision avoidance and preservation

- FC03–06 share `production_workflow.py`, `controller.py`, requirements HTML and skills. Edits proceed by plan order and owner map; each step records its files/hunks before modifying them.
- Source migrations 001–013 are historical. Do not edit earlier migrations. Choose any new migration number only after live source/runtime confirms it's unused and schema compatibility.
- Current active runtime HEAD `22d687...` and repo HEAD `51cfc...` are distinct states. FC09 owns official reconciliation; FC01 does not change the switch or ledger.
- FC01 does not rewrite any `running`, `blocked`, or `interrupted` row. The newly observed planning/report attempt drift is explicitly assigned to FC05/FC10.
- Future report author and reviewer are distinct real runs. Report reviewer is not the product-code reviewer. The case plan prohibits independent test-code review.
