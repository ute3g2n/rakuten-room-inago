# 受け入れ条件と検証経路

各ACの主検証経路。`済`は実装フェーズまでの自動テスト通過、`予定`は後続Stepの実機・E2E判定待ち。複合条件は後続で最終判定する。

| AC | 種別 | 主なテスト・検証工程 | 現在 |
|---|---|---|---|
| AC-001 | unit / integration | `test_step09.py::test_prestart_production_rejected_and_setup_status_do_not_call_model`, `test_step10.py::test_health_start_boundary_missed_stale_and_action_required` | 済 |
| AC-002 | skill / live | Step 01 skill copy検査、Step 17/19 active tree | 予定 |
| AC-003 | integration | `test_step09.py::test_daily_sufficient_inventory_skips_research_and_rerun`, `test_daily_shortage_partial_and_dry_run_isolated` | 済 |
| AC-004 | contract | `test_step02.py`, automation-config Schema | 済 |
| AC-005 | integration | `test_step09.py`, `test_step08.py` daily-plan契約 | 済 |
| AC-006 | contract | automation-config Schema、`test_step05.py`在庫不足 | 済 |
| AC-007 | unit | `test_step02.py` product-ledger、`test_step05.py`重複照合 | 済 |
| AC-008 | integration | `test_step02.py`移行、`test_step05.py`再在庫化防止 | 済 |
| AC-009 | integration / E2E | `test_step06.py` plan照合、Step 13 browser | 予定 |
| AC-010 | skill / live | `test_step08.py`契約、Step 15実ページ | 予定 |
| AC-011 | integration / live | `test_step09.py::test_research_pipeline_verifies_then_commits_and_rechecks_quality`, Step 15 API | 予定 |
| AC-012 | unit / live | `tests/room-likes.test.mjs`、Step 15自ROOM | 予定 |
| AC-013 | skill / unit | Step 01/08 quick_validate、`test_step08.py`8ジョブ | 済 |
| AC-014 | contract | `test_step04.py` model-policyとfake CLI | 済 |
| AC-015 | integration / live | `test_step10.py`入口、Step 16タスク | 予定 |
| AC-016 | unit | `test_step03.py` logger/lock/resume、`test_step10.py` health | 済 |
| AC-017 | integration | `test_step07.py`台帳、`test_step09.py`受渡し、Step 12 | 予定 |
| AC-018 | unit | `test_step07.py`帰属とday aggregate | 済 |
| AC-019 | live | Step 17切替、Step 19復元検査 | 予定 |
| AC-020 | E2E | Step 13 dashboard latest/同日再生成 | 予定 |
| AC-021 | E2E | Step 13目次・折り畳み | 予定 |
| AC-022 | E2E | Step 13再読込・再起動のチェック復元 | 予定 |
| AC-023 | E2E | Step 13 clipboard本文照合 | 予定 |
| AC-024 | integration / E2E | `test_step06.py`投稿済み復帰防止、Step 13 | 予定 |
| AC-025 | integration / E2E | `test_step06.py`DATA_MISMATCH、Step 13画面 | 予定 |
| AC-026 | live | Step 15楽天アフィリエイト日次取得 | 予定 |
| AC-027 | unit / live | `tests/affiliate-report.test.mjs`履歴、Step 15翌日再取得 | 予定 |
| AC-028 | failure / live | `test_step05.py`未取得、Step 14/15ログイン失効 | 予定 |
| AC-029 | unit | `test_step07.py`非帰属集計 | 済 |
| AC-030 | unit / live | `test_step07.py`注文照合、Step 15注文詳細 | 予定 |
| AC-031 | integration / live | `test_step05.py`ROOM照合、Step 15自ROOM | 予定 |
| AC-032 | integration / live | `tests/room-likes.test.mjs`履歴、Step 15 | 予定 |
| AC-033 | unit | `tests/room-likes.test.mjs`同値重複防止 | 済 |
| AC-034 | failure / live | `test_step05.py`未取得写像、Step 14/15 | 予定 |
| AC-035 | security / live | `tests/room-likes.test.mjs`公開読取、Step 15 | 予定 |
| AC-036 | live | Step 16 Windows Daily task end-to-end | 予定 |
| AC-037 | contract / live | `test_step04.py`fake CLI、Step 16実モデル3種 | 予定 |
| AC-038 | integration / live | `test_step09.py`同日再実行・resume、Step 16 | 予定 |
| AC-039 | integration / live | `test_step10.py`source、Step 16三入口 | 予定 |
| AC-040 | live | Step 16 sleep/wakeとStartWhenAvailable | 予定 |
| AC-041 | failure | `test_step04.py` timeout/Schema、`test_step09.py` lock、Step 14 | 予定 |
| AC-042 | security | `test_step04.py`redaction、Step 14 secret scan | 予定 |
| AC-043 | contract / live | `test_step04.py`昇格、Step 16実モデル | 予定 |
| AC-044 | integration / live | `test_step03.py`audit、`test_step10.py`source、Step 16 | 予定 |
| AC-045 | failure | `test_step04.py`、`test_step09.py`、Step 14 matrix | 予定 |
| AC-046 | failure | `test_step03.py`heartbeat、Step 14強制終了 | 予定 |
| AC-047 | unit | `test_step10.py::test_health_start_boundary_missed_stale_and_action_required` | 済 |
| AC-048 | unit / live | `test_step10.py::test_status_filters_and_no_model`, Step 16手動確認 | 予定 |
| AC-049 | unit / failure | `test_step05.py`fresh artifact、Step 14終了コード注入 | 予定 |
| AC-050 | unit | `test_step03.py`resume/append履歴 | 済 |
| AC-051 | security | Step 11 secret scan、Step 14全artifact再検査 | 予定 |
| AC-052 | integration | `test_step09.py`入力snapshot、Step 12 fake全経路 | 予定 |
| AC-053 | unit | `test_step07.py::test_a_request_idempotence_and_order_status_change` | 済 |
| AC-054 | unit | `test_step07.py::test_b_c_hold_guard_and_single_variable` | 済 |
| AC-055 | unit / integration | `test_step07.py`C根拠ガード、Step 12 | 予定 |
| AC-056 | unit | `test_step07.py::test_clicks_and_zero_likes_stay_separate` | 済 |
| AC-057 | unit | `test_step07.py::test_exact_match_priority_and_ambiguity` | 済 |
| AC-058 | unit | `test_step07.py`いいね0と比較件数 | 済 |
| AC-059 | unit / integration | `test_step07.py`未取得、Step 12 | 予定 |
| AC-060 | integration | `test_step09.py`学習入力snapshot、Step 12 manifest trace | 予定 |

割当: 60/60。最終PASS判定は各予定工程の完了後に更新する。
