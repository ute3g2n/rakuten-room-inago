# FC11 独立受入照合 r1

照合担当によるread-onlyの再確認結果を記録する。approved計画snapshotは`worklogs/projects/maintenance/flow-completion-20261019/FC02/plan.md`、SHA-256 `e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381`。SQLiteをread-onlyで照合し、作業ツリー・runtime・DBは変更していない。

| 条件 | 判定 | 実測の要点 |
|---|---|---|
| FC-A01 同一jobの終端 | 達成 | resume 27、同一job／launch／10/17／source run null。job complete、全8 stage観測、running 0、終端missing 0。以前のrunning attemptはinterruptedとして閉じている。 |
| FC-A02 失敗追跡 | 達成 | planning・repair・deploymentの実失敗に、理由、例外連鎖、捕捉位置、stack、相関ID、入力・出力参照を持つhandoffとoperation eventがある。 |
| FC-A03 必須成果 | 達成 | 調査・計画・修復・deployment・reportの成果または失敗handoffを終端が再読。fallback作成とagent応答を区別。 |
| FC-A04 計装の適用・実行 | 達成 | fixed-root switcherが`204939e8dbca217489278e0133ea86ca35885def`をhealthyで適用確認。その後のresume 27の16 event／handoffに新source fingerprintがある。 |
| FC-A05 後段継続 | 達成 | planning／repair失敗後もdeployment・report担当が起動し、全stageがblocked以外の結果または有用なhandoffを残した。 |
| FC-A06 最終HTML | 達成 | reporterと別reviewerが実起動。最終HTMLはcontroller fallback作成、独立reviewerが厳定義の対象版をapproved／指摘0。原因unknownと修正未確認を保つ。 |
| FC-A07 review・公開一致 | 達成 | report result／review／publication sidecar／terminal／remote mainのversion・SHAが一致。公開commitは`fa6c78a64f2242a7f2a65ce3bb63ddd3e275494d`。 |
| FC-A08 incident未解決を保持 | 達成 | terminalは`incident_resolution=unknown`。元不具合の原因・修正・日次回復を解決済みと偽っていない。 |
| FC-A09 副作用制限 | 達成 | 10/19 queueはqueued・event 0、同日のfailed launchは不変。新job／daily run作成なし、source run nullを維持。 |
| FC-A10 文書・運用・正式台帳 | 部分達成 | schema16とquick_checkは正常。requested effortはhighを保存。適用runtimeは`204939e8...`だが正式deployment ledger最新activeは`22d68761...`で不一致の行なし。実際のmodel/effort observationはnull／configured_unverified。resume 27の記録はこの照合時点の§14に未記載だったため後続追記した。 |

## 根拠ファイル

- `FC11/same-job-resume-27-final-readback.json`
- `FC11/same-job-resume-27-worker.json` と `FC11/same-job-resume-27-worker.console.txt`
- `runtime/workflow-terminal/job-historical-9e7f0c175cc4c874c955a5dab29c4427/workflow-80ea6993227c4c6f9c3efc96b730ac3d.json`
- resume 27のplanning／repair／deployment handoffとfailure journal
- report attempt `dfad8488-b363-43ad-a3b7-8570af7be8b4` のresult／HTML／review／publication sidecar
- fixed-root runtime apply readbackとread-only maintenance DB query

総合判定は計画未完了。FC-A10の最新runtimeと正式deployment ledgerの不一致を、正式なAPI・switcher経路で解消し、read-backする必要がある。旧runに保存されたblocked attemptは履歴事実として保持し、後続起動判定には利用しない。
