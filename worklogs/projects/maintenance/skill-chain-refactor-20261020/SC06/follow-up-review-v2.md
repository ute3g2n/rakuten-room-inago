# SC06 独立製品レビュー — F3再確認 v2

## 対象版・証拠

- job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`; target_date: `2026-10-17`; source_run_id: `null`。
- workspace: `C:/project/rakuten-room-inago`。reviewerは担当実装者とは別のSC06レビュー担当。
- 製品 `automation/inago/maintenance/skill_chain.py` SHA-256: `a8fe46b191dab88924eedfe01af92984a9eb225840e5a83f77d7339be533ab51`。
- 試験コードSHA-256（試験コード自体はreviewせず版照合のみ）: `3338f570147c9a667432bf947dcdfb8daa1dcf702fcde787f903a40b4a627d49`。
- 現行計画SHA-256（SC05 F3追試記録の版結合）: `ca8ae7acd1fb5d0a1a6b84303bb501f0881d172762c539471e79be7dc7ed3a30`。
- SC05 F3追試: `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F3-followup/test.md`, `test.log`, `junit.xml`, `node-list.txt`を読んだ。記録上、21件のskill-chain試験と4件の計画指定既存node、計25件が成功、失敗/skip/error 0、exit 0。JUnitの件数とconsole末尾は一致する。実行時刻は2026-10-20 14:28:50.8783611〜14:28:57.1264360 JST。
- 試験記録中のF3 nodeは、成功済みの合成同job chainから公開readbackのみ不一致にし、role 8から再開する判断とreport.html/html-review.mdのbyte維持を確認する。これはlive job再開の証拠ではない。SCT06/SCT09は未実施のまま。旧FC01〜FC11 nodeは実行されていない。
- 製品、試験コード、日次job、DB、Daily、10/19履歴は変更・実行していない。
- 実行時Git観測: branch `main`, HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b`, tracking `origin/main`。共有workspaceには他担当の変更が多数あるため、これらを変更・stageしていない。

## 判定

**changes_requested（後続工程を止めない所見1件）**。前回のF3は解消した。F1/F2も現行版で維持されることを確認した。残る所見は、role 6の最終作者成果`report.md`のevent/hash結合が弱く、terminal/resumeがその最終内容の同一性を検証しない点である。HTML公開の内容そのものとrole 8再開の期待を否定するものではないが、作者の最終成果一式を再利用したという監査証拠として不完全である。

## 前回所見の再確認

- **F1（role 6の同じ作者による復旧）解消を再確認。** `skill_chain.py:1331-1349`はrole 6の初回呼出しが失敗、handoff不正、成果未更新、または実読確認不足の場合に`_retry_report_author`を呼び、同じ作者役の新しいsessionで担当を再試行する。失敗時も固定role順の7/8へ続く。SC05の復旧6 parameter（成功3、失敗3）はこの版に結び付いている。
- **F2（前担当Markdown実読のevent証拠）解消を再確認。** `skill_chain.py:578-650`はturn eventにある完了済み`commandExecution`、exit code 0、完全な引用pathを照合し、限定したpath/hash/tool metadataのみを記録する。`1275-1321`でこの読取証拠を担当成果とhandoffに結び付ける。自由応答中の自己申告hashだけで承認しない。SC05のexact-path success/missing/failed/mismatched各結果を版照合した。
- **F3（role 6/7成果置換後にrole 8から再開できない）解消を確認。** `_resume_start_role`はrole 6の作者revision出力とrole 7の最終rereview出力を現行bytesに照合し、初回role-call hashだけで最終成果を不正としない（`:1673-1750, 1771-1824`）。role 8以外の前段が整い、terminal理由が`skill_chain_publication_bytes_mismatch`ならrole 8を返す（`:1752-1762`）。terminal側もrole 6/7の初回成果記録と、それを置換したrevision/rereview記録を分けて扱う（`:1911-2007`）。F3 synthetic testとこのコード経路は整合する。

## 所見

### F4 — 低: 最終作者Markdownのhashがrevision/rereviewの終端・resume照合で検証されない

- **場所:** `automation/inago/maintenance/skill_chain.py:1479-1505`、`:1542-1564`、`:1771-1824`、`:1965-1989`。
- **計画/case:** SC-R04、SC-A04、SCT07/SCT14（最終成果の正確な版・event根拠と再利用）。
- **根拠:** `author_revision_finished`は作者が`report.md`と`report.html`を更新するpromptを使うが、eventの`output_refs`は`report.html`だけを記録する。`html_rereview_finished`は`report.md`のhashをinput refに書くものの、`_rereview_output_matches`とterminalの`html_rereview_complete`はそのrefを現行`report.md`と照合しない。`_revision_output_matches`もreport.htmlのみを対象にする。terminalはreport.mdの存在と`final_html_sha256` markerを確認するが、そのMarkdown全体がrevision eventで記録した最終bytesかは確認しない。したがって、他の正規成果が揃った後にreport.md本文がmarkerを残したまま変わっても、terminalはrole 6/7成果を確認済みとしてrole 8から再開し得る。
- **影響:** `report.html`と`html-review.md`の公開対象hash確認、最終HTML再レビュー確認、F3の公開readback mismatchでのrole 8再開は引き続き働く。一方、最終作者Markdownの改変/取り違えを検知する同一性証拠は弱く、再利用が作者最終出力一式に及ぶことをevent ledgerから確認できない。
- **修正案:** 作者revision eventに最終`report.md`のpath/bytes/hashを記録し、resumeとterminalでその現物hashを照合する。最終rereview eventに記録した`report.md` input hashも現物と一致することを検証する。差分が入る場合は単体試験で、公開readbackのみ不一致ならrole 8再開を維持しつつ、report.md変更時は成果を再確認する挙動を確認する。
- **重大度:** Low。一般的な完了経路と公開bytesの検証は成立する。欠けるのは作者Markdownの改変検出であり、この所見をフローや公開の停止条件にしない。

## イベント・再開・秘密境界の追加確認

- ledger読取は同じ`chain_started_at`のイベントだけを再利用し、通常/fallback ledgerの重複をdeduplicateする（`:1567-1604`）。resumeではrole 6/7のrevision/rereview eventで現物bytesを確認し、role 8 public mismatchの再試行で最終HTMLと最終reviewを保持する。
- predecessor read evidenceは正確なコマンドpathに加えて完了状態とexit code 0を要求する。read eventの自由なcommand textや結果本文をmaintenance ledgerにコピーしていない。
- `_append_event`の許可field集合、`_response_observation`のhash/byte数要約、`_safe_error_context`の例外class/safe code/限定stack frameを確認した。通常の担当本文、例外本文、SQL値、token/cookie、投稿データ、個人情報をskill-chain ledgerへ入れる実装は確認しなかった。
- workerの履歴routeは固定10/17 identityと既存jobを照合し、10/19を除外する現行分岐を保持する（`:2040-2120`）。新しいjob identityやDaily経路へ迂回する変更は見つけていない。

## 引継ぎ

SC07/後続担当はF4を所見データとして受け取り、必要なら修正して同jobの作業を続ける。review結果、未実施のSCT06/SCT09、synthetic試験である限界は後続工程を止めない。以前のSC06 reportおよびSC05の証拠は変更していない。
