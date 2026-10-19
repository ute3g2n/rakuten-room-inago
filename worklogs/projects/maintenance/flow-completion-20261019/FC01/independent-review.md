# FC01 独立レビュー

- reviewer: `/root/fc01_review`（作成担当とは別agent、reasoning effort high）
- 判定: **changes_requested**。指摘2件。FC02は利用可能な成果物と本指摘を受け取り、そのまま開始する。
- レビュー時刻: 2026-10-19 22:47:09 JST（13:47:09 UTC）。
- 計画: `自動不具合対応フロー完遂と全工程追跡_実行計画_2026-10-19.md` version 1.2、SHA-256 `e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381`。
- レビューした実行基準commit: `51cfc7503775a25af731c8f4bcf727c94f10a595`。
- 対象: FC01の5成果物と計画FC01の14項目・全文prompt。これは新規計画全体の再レビュー、製品コードレビュー、同一job完遂の認定ではない。

## 対象成果物の版

| 成果物 | SHA-256 |
|---|---|
| baseline.md | c7d776fee9bbea3e79fbc3b37f1970ad8b30ec0bcd019135d436a3a672194306 |
| failure-path-map.md | 9049dd672f093d8e881b4928ec36c0acc81df77799272b1f61890943aaf42295 |
| ownership.md | e91ccc0ff92326f86ea5da3f2e6b97f7747a2cef022a9ead480234ffe57e4eef |
| instrumentation-backlog.md | 2198858511d8a4f4b54454709ec1a48bf24a6fe5d6f596218fdabb11456ea3bd |
| handoff.json | b0c469bbb925f956f9e68c53692136d3599e40a4f3acf37de30cf8058c605572 |

## 確認結果

5成果物は全て内容があり、必要な不変identity（job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、source run `null`、対象日 `2026-10-17`）を正しく引き継いでいる。現在値と計画作成時の値、旧blocked履歴と将来の契約変更、観測済みとunknownが概ね区別されている。

実装候補I-01～09は具体的で、logger自身の障害、AI応答、成果物保存、DB、中断、Git公開、報告・公開・終端を含む。FC03で不足計装を実装し、FC09で正式適用、FC10で同一jobの新しい記録まで確認する引継ぎがある。外側で書いた実装をjob自身が書いたと称さない点も明記されている。ownerはFC01 controllerと後続STEPに割り当てられ、関数単位の詳細はFC02のowner mapへ引き継がれている。

レビュー時にもDBをSQLite URI `mode=ro`、`PRAGMA query_only=ON`で照合した。対象jobのstatus `running`、resume_count 8、source_run_id/finished_at/terminal_reason_codeがnull、attempt 11件、stage observation 16件、handoff artifact 1件は記録と一致した。最新invocationの8工程を直接取得し、下記R01の局所的な不一致を確認した。`git status --short`はFC01のuntracked directoryだけで、HEADは上記基準commitだった。この照合は本番データ・製品・設定・Schedulerへ書き込んでいない。

## 指摘

### FC01-R01（low）最新工程列の本文がDBと一致しない

- 場所: `baseline.md:42`。
- 問題: 「investigationだけcomplete」とあるが、同じinvocationのDB実値と`failure-path-map.md:9`はcheck_incident、evidence、job、investigationの4工程がcompleteである。
- 根拠: `workflow-0b3e8042053547f88986f13a7398c86d`のstage observationをread-onlyで再取得した。後半4工程のblocked履歴は記載と一致した。
- 対応: 最新8工程中、前半4工程complete・後半4工程blockedという過去観測へ本文を訂正する。DB/raw履歴は変更しない。
- 計画対応: FC01項目2/7/11、FC-R01/FC-R07、FCT01/FCT11の静的入力。

### FC01-R02（medium）既存日次計装の適用範囲を辿る根拠が不足する

- 場所: `failure-path-map.md:32`、`instrumentation-backlog.md:5`。
- 問題: 既存daily計装の機能は列挙されるが、FC01項目7が求めるfile/operation/行単位の対応と、実装・正式適用・過去試験のどこまで確認したかの根拠がない。現repo HEADとactive runtimeのSHA差もあるため、ソースの存在を本番適用済みと読み替えない粒度が必要である。
- 根拠: ソースには`daily_pipeline.py:765`のSQLite分類、`:871`の捕捉位置、`:1064`以降のpersisted run観測、`orchestrator.py:714`付近のrun作成診断、`trace.py:417`以降の関連field検証がある。一方、対象成果物は3 file名を一括で挙げるだけで、当該箇所と適用を示す版付き証拠を参照していない。
- 対応: 既存計装についてfile/function/行・対象operation・記録field・読んだソース版を短い表にする。正式適用の根拠は版付きledger/既存記録へ結ぶ。該当実行版で確認できないものはunknownと明記し、重複回避判断と不足計装割当へ渡す。再試験や本番副作用をFC01へ追加する必要はない。
- 計画対応: FC01項目7/8/11、FC-R02/FC-R04、FCT02/FCT12の静的入力。

## 引継ぎと限界

本判定と2件の指摘はFC02の入力にする。レビューstatus、unknown、過去のblocked、モデル実観測不足を後続の起動条件にしない。作者がFC01を修正した場合、本レビューは上表の旧bytesを対象にした履歴として残す。

このレビューはFC-A01～10を充足しない。製品差分、focused local verification、独立製品review、正式適用、新しい同一jobのtrace、実reporterとreport reviewer、最終HTML公開、正しい終端登録は未レビューであり、後続STEPで実測する。原障害の原因・業務回復はunknownのままであり、解決したとは認定しない。
