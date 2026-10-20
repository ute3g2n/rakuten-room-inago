# SC08 / SCT06 追補: safe diagnostic focused rerun

## 対象と範囲

- job_id: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target_date: `2026-10-17`
- source_run_id: `null`（変更なし）
- 実行対象版: `automation/inago/daily_pipeline.py` SHA-256 `b26c74def13e4aed28577bea8dd8d9fac0cc8cdf4a091a493c8301d86ba15fc7`
- 試験ソース: `tests_inago/test_daily_pipeline_safe_diagnostics.py` SHA-256 `ebb56f09e07e9e2112d28e6c08079b7e81a77c17542dc78e1e2bfd6213a0459a`、`tests_inago/test_maintenance_skill_chain.py` SHA-256 `ec1ba305e0db3d267f81b4eda9f2c1d66356a61f15f8be55dd642bd2b48c4bfc`
- この試験は合成例外fixtureでsafe diagnosticの出力を検証したもの。自然発生したDailyの診断記録、10/17本番jobの原因、日次runの存在やjob完了を示さない。

## 実行と結果

実行は2026-10-20 16:58 JST。JUnitのsuite timestampは`2026-10-20T16:58:39.698577+09:00`、suite durationは1.275秒。pytestプロセスの正確な開始・終了時刻は記録から確認できない。実際のbasetempは`%TEMP%\inago-sc08-sct06-final-0b946edeede444a98585b0d295c34d1f`。記録された実行コマンド:

```text
.venv-inago\Scripts\python.exe -m pytest tests_inago/test_maintenance_skill_chain.py::test_daily_gap_emits_useful_safe_diagnostics tests_inago/test_daily_pipeline_safe_diagnostics.py::test_pipeline_failure_records_its_own_capture_location tests_inago/test_daily_pipeline_safe_diagnostics.py::test_exception_chain_truncation_boundary_is_reported_without_unsafe_data -q --basetemp %TEMP%\inago-sc08-sct06-final-0b946edeede444a98585b0d295c34d1f --junitxml=worklogs/projects/maintenance/skill-chain-refactor-20261020/SC08/SCT06/final-focused.junit.xml
```

期待結果は指定した3 nodeが完了し、失敗・error・skipがないこと。依頼元の実行結果はexit `0`、3 passed、console所要1.38秒。JUnitはtests=3, failures=0, errors=0, skipped=0。個別node:

1. `tests_inago.test_maintenance_skill_chain::test_daily_gap_emits_useful_safe_diagnostics` — PASS
2. `tests_inago.test_daily_pipeline_safe_diagnostics::test_pipeline_failure_records_its_own_capture_location` — PASS
3. `tests_inago.test_daily_pipeline_safe_diagnostics::test_exception_chain_truncation_boundary_is_reported_without_unsafe_data` — PASS

出力を再読した結果、`final-focused.log`は100 bytes、SHA-256 `e87f9a0dd001a4edeebf2757f4a61ac8585a7b363ede9207c4cf54c730a6e8e2`、最終更新時刻 `2026-10-20T16:58:41.1105353+09:00`。内容は`3 passed in 1.38s`。`final-focused.junit.xml`は694 bytes、SHA-256 `bc28d87a259b88645577d84ba254867817998a005adf2ea2dc1a40c5420cd5a0`、最終更新時刻 `2026-10-20T16:58:41.0750712+09:00`。JUnit内の完全node名は上記3件。JUnit suite timestamp/duration、console summary、およびartifact mtimeはそれぞれ別の観測値であり、artifact mtimeをプロセス開始・終了時刻とは扱わない。

## 制約と未確認事項

この追補ではテストを再実行せず、既存console/JUnitとhash/bytes/mtimeを読み戻した。Daily、Worker、DB、Scheduler、10/19 queue/history、投稿・下書きデータは操作していない。合成fixtureのPASSから自然Dailyの成功、source_run_idの変更、10/17障害の診断・解消、jobの完了を推論しない。
