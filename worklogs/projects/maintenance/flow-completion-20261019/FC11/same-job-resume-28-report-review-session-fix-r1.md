# 同一job resume 28のレビュー版不一致と修正 r1

## 実行結果

- 観測日時：2026-10-20 01:24 UTC時点の作業記録。固定root `C:\project\rakuten-room-inago`、branch `main`、HEAD/`origin/main` `72ff1940cf0c28695363932fed46c27bf19f059a`。
- 指定job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、対象日 `2026-10-17`、`source_run_id=null` のまま同一jobをresume count 28で実行した。
- DB上はjob `complete`、finished `2026-10-20T01:11:56.491913Z`、resume_count 28。ただし最終HTMLは独立reviewで承認されておらず、計画受入として完了ではない。
- HTML report attempt `f5640b94-07f4-4f67-b2eb-2b4807645fe2`、attempt 27、result version `fallback-revision-f2d5127ebf7e65522987`。resultは`next_action=publish`、`report_status=pending`、`stage_status=review_unavailable`、reviewer `controller-fallback-review-unavailable`、`review.status=unavailable`。
- reviewerは2回実起動。初回は対象4点のうち元の日次不具合の説明が不明瞭との指摘を返し、作者がHTMLを修正。2回目は修正後HTMLのSHAを受け取ったが、過去版の`reviewed_version`を返した。HTML versionとreview versionの不一致をcontrollerが検出して`report_review_version_mismatch`とし、承認済みとは扱わなかった。
- それでもreport fallback経路は公開を進め、remote `origin/main`にcommit `72ff1940cf0c28695363932fed46c27bf19f059a`、path `reports/incidents/2026-10-17/job-39e268f67f036cc9225e.html`、公開HTML SHA-256 `ae6672d2e9a40a984caaba66380a23db2f6d1d7bbdfb4e4346525c9480a870bf`が存在する。この版を「最終レビュー承認済み」とは扱わない。
- よってFC-A06/A07および計画全体は未完了。DB終端completeだけでは最終report受入を満たさない。

## 原因と修正

- 10/17の日次障害そのものの原因は引き続きunknownであり、resume 28では修正していない。
- 今回のreview失敗原因は、report HTML版が変わってもAI adapterが同じreviewer会話を再利用していたこと。版1の指摘後に版2を渡しても、会話が版1の結果を引き継ぎ、版2のSHAと旧`reviewed_version`が混在した応答を返した。
- `MaintenanceAIAdapter.plan_session`にoptional `session_discriminator`を追加。指定時は識別子のSHA-256をsession key/log pathへ含め、異なるHTML版で会話を分離し、同じ版の再開だけは既存sessionへ結び付ける。互換性のため省略時は既存keyを保つ。
- controller fallback reviewerと通常report reviewerの両方で`attempt_id + result_version + html_sha256`からdiscriminatorを作る。旧会話の再利用によるreview versionの取り違えを避ける。
- AI adapter testsのうち既にfixed-root/high設定へ変わったC25–C27期待値を、旧worktree/max/fast期待のまま残していた箇所も現行契約へ同期した。

## 検証・独立製品レビュー

- 新規adapter testは修正前、未知の`session_discriminator`引数で失敗するREDを確認。workflow integration testも、異なるreport version間でreview session discriminatorが別になることを修正前に失敗させた。
- 修正後：adapter/fallback関連9件PASS。`tests_inago/test_maintenance_ai.py`全体36 passed（11.73秒）。`tests_inago/test_maintenance_report_publication_stage.py`全体48 passed（96.51秒）。
- 変更対象4ファイルの`py_compile`成功。`git diff --check` exit 0（`production_workflow.py`の改行形式についてGit警告のみ）。
- 独立read-only製品reviewer `/root/html_review_scope_product_review` はAPPROVE、指摘0。review対象は`ai_adapter.py` SHA-256 `89a1fcd4a1acd4cdb16ba849a4a6fad3d041dbdab1b3a45582e4eb271b31d2dc`、`production_workflow.py` SHA-256 `1b4178ac278a384f9aaad366024ccb183edfe3228f5d8f75065a325e744d5e72`。テストコード独立reviewは依頼していない。
- HTML reviewerの観点は既存同期済みのまま。ユーザー指定の4事実（不具合、原因または不明、実施修正、追加したtrace/logと記録内容）と事実性・理解可能性、秘密/個人情報だけを扱う。今回の指摘「日次不具合が分からない」はこの範囲内なので、不要観点を追加した指摘ではない。雛形は変更していない。

## Git・本番反映・次の再開位置

- 本記録作成前のcached diffは0、uncached tracked変更は上記4ファイルのみ。作業差分はまだcommit/pushしていない。本修正も正式runtimeへ未適用。
- 次はこの4製品/試験ファイルと今回の計画・worklogだけを所有対象としてhash・秘密混入・cached/uncached/untrackedを再監査し、選択stage/commit/pushする。続けて正式runtime apply/read-backを行う。
- その後、同じjob・同じlaunch・同じ対象日・null source_run_idだけを再開する。fresh HTMLの内容をreporterが作成し、異なるHTML版間のreview会話再利用がなく、最終修正後の同一HTML SHAが独立reviewでapprovedになり、そのbytesが公開され、DB/remote/job terminalと整合することをread-backする。
- 10/19 queue/historyは触らない。jobの新規作成、日次run捏造、Daily手動起動、不足draft生成を行わない。FC-A01～10全体はまだ再照合していないため、計画完了とは判定しない。

## 記録・stage照合コマンドの失敗と回復

- 現在地照会コマンドの初回PowerShell実行ではGit upstream指定`@{u}`をPowerShellがhashtable構文として解釈し、コマンド全体がparse errorとなった。Git操作は開始していない。upstream文字列を引用して再実行し、`origin/main`とHEADを照合した。
- 所有stageのassertionは、Windows `Path.relative_to()`が返すbackslashとGitの`/` path表記を同一視せずfalse failureとなった。git addはその直前に計画対象7 pathだけへ実行済み。separatorを正規化した集合比較で再検証し、意図した7 pathのみstagedであることを確認した。差分内容は変えていない。
