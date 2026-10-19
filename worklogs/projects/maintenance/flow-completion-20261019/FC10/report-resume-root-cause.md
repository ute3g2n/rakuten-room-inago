# Same-job report resume: measured causes and correction

対象は同じ `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、target date `2026-10-17`、`source_run_id=null`。確認対象invocationは `workflow-f09a4bc6118f419099136d2dadfcc974`、report attemptは `34ef5b9e-fcc1-481a-b780-a5cdd8473ca8`。

## 実測した失敗

1. 通常reporterの応答は前のattempt `f770f0df-d5f6-4ed2-acf0-d63658bcd6c7` に保存されていた。current attempt `34ef5b9e-fcc1-481a-b780-a5cdd8473ca8` の入力は同じ `result_version` / `created_at_jst` を参照してその過去応答を復旧候補に選んだが、保存wrapperは過去attemptのpathをcurrent attemptの応答として拒否した（`report_response_attempt_mismatch`）。current attemptにはreporter envelopeがなく、`report-input.json` はあるが formal report result は作られなかった。
2. controller fallback HTML `report-fallback-4e79d33c99455aa0540b.html` は独立reviewerへ実際に渡った。reviewer envelope `B--rr-inago-incident-report-reviewer--c2ba69aa40bb49059f86ac00af14a1c6.json` は同じjob/attempt、同じHTML version/hashを返し、6件の具体的findingと `changes_requested` を含む。
3. authored reportの `created_at_jst` は `2026-10-20T02:41:53+09:00`。reviewerの `reviewed_at` は `2026-10-19T17:42:12Z` で同じ時刻をUTC表記した値だった。`validate_report_review` がreport作成日時専用のJST検証をreview日時にも流用し、`report_created_at_not_jst` を送出した。outer fallbackはreviewを `unavailable` と記録したため、reviewerの6 findingsがauthor revisionへ渡らなかった。

## 修正と確認

- `reporting.py`: review日時だけをtimezone-aware ISO-8601として読み、保存値をJSTへ正規化する。report本文の `created_at_jst` は従来どおり厳格にJST検証する。timezoneなしは拒否する。
- `production_workflow.py`: 過去attemptの応答はそのattemptに束縛されたまま保つ。current attemptに応答が存在しないときはcross-attempt pathを渡さず、current attemptでreporterを再起動する。
- TDD reproduction: UTC `Z` reviewer responseのfocused testは修正前 `report_created_at_not_jst` でFAIL。修正後は正規化とnaive timestamp拒否を確認。
- `tests_inago/test_maintenance_report.py` と `tests_inago/test_maintenance_report_publication_stage.py`: 34 passed in 45.50s。requirements builder exit 0 (v2.50, 28 chapters, 39 figures, 26 refs, 4,155,173 bytes)。`py_compile`対象2 moduleとbuilder outputへの契約埋込確認も成功。`git diff --check` exit 0（production_workflow.pyの既存CRLF warningのみ）。
- 実際に保存済みの6-finding reviewer responseを修正後validatorへ読ませたところ、`2026-10-19T17:42:12Z` を `2026-10-20T02:42:12+09:00` に正規化し、`changes_requested` と6 findings、同じreviewed hashを維持した。
- independent product review target: `report-runtime-fix-review-target.json`。対象product source SHAは `reporting.py=5dfa6f9824327a476bc3a30199e4727d1ca364b0d11fb490e10ec135b7ac8324`, `production_workflow.py=b20e4b85b4f3c3c0c3b1a76652f0b730a2b3a40c3a781c946802a7d13f00124c`。review is pending before application.

修正版はまだ本番runtimeへ適用していない。次はこの正確なcode versionのindependent product reviewを完了し、必要な指摘を直した版だけをcommit/push・runtime applyした後、**同じjob**を明示resumeする。10/19 queue/history/Dailyは処理していない。
