### SCT06 計画指定node実測 — 2026-10-20 15:40 JST

- 対象job: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`。対象日 `2026-10-17`、source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、source_run_id `null`。
- 計画指定の完全node `tests_inago/test_maintenance_skill_chain.py::test_daily_gap_emits_useful_safe_diagnostics` を新設し実行。pytest結果 `1 passed`、失敗/error/skip 0、整数exit `0`。
- 実行時刻 `2026-10-20T15:40:53.6716333+09:00`–`2026-10-20T15:40:55.4884424+09:00`。command: `.venv-inago/Scripts/python.exe -m pytest tests_inago/test_maintenance_skill_chain.py::test_daily_gap_emits_useful_safe_diagnostics -q --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SCT06/junit.xml`。
- 検証内容: 1) 一次日次失敗を保持しcallbackのValueErrorをsafe secondary diagnosticに記録、2) ROOM/Trends acquisition/Trends attemptsの各query失敗でretry summaryの4項目をnullに保ち、queryラベル/捕捉場所/コードhashを記録、3) commit後export例外でもcommit済み件数と最終exportを保ち、4) `secondary_errors` が隔離L01相当JSONL payloadに届き例外canaryを含まないこと。すべて実期待と一致。
- daily code SHA-256 `3bb8bb1e935ec0644dfe7b8715d0e94c6cebda0c2a79ba848e77ceac44898e1f`; daily diagnostic test SHA-256 `b1bb21df8cfa6254de62fec3d82d4e76175481a7fb5c069ced1362d9b049a8e7`; chain test SHA-256 `ec1ba305e0db3d267f81b4eda9f2c1d66356a61f15f8be55dd642bd2b48c4bfc`.
- Console SHA-256 `a7421f760146405279528e5cb60adb1709a0741fb38c131f1251aaac34fdac35`; JUnit SHA-256 `8ef4a06b178b60ba7c91c5fe628befbb0e54a40843ea952d97374c9a1fddce86`. job内複製は `test-evidence/sct06-plan-node/`。
- テストはtmp_path上の隔離DBと一時ファイルのみ使用。本番Daily/DB、Worker、日次run、draft、10/19 queue/historyは操作していない。この試験は本番ログ永続化やruntime applyを証明しない。
