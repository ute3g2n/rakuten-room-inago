# 依頼の振り分け

| 依頼 | Python入口 | 成果物 |
|---|---|---|
| 今日の日次フロー、化け物・改を召喚 | `automation/run_daily.py --source chat` | 日次manifest、計画、投稿管理HTML |
| 商品補充、候補探し | `automation/run_research.py --source chat` | 収集・選別・確認・在庫結果 |
| 昨日の結果、成果分析 | `automation/run_performance.py --source chat` | 帰属・A/B/C/保留と学習条件 |
| 初期設定 | `automation/run_setup.py --source chat` | 設定案と整合性確認 |
| 投稿数、ジャンル、時刻の変更 | `automation/run_config_change.py --source chat` | 変更差分と影響 |
| 失敗からの再開、障害復旧 | `automation/run_recovery.py --source chat` | 局所復旧結果と残課題 |
| 状態確認 | `automation/show_status.py --source chat` | 最終状態と本人対応事項 |

入口は手動・チャット・schedulerで同じオーケストレータ、設定、Schema、状態ファイルを使う。`source`は監査用であり採用基準を変えない。判断系サブスキルのモデルは `rakuten-room-kai/orchestrator/model-policy.json` が決める。オーケストレータは検証後にだけ正本を書き換える。

プロジェクトルートから `.venv-kai\Scripts\python.exe automation\run_daily.py --source chat --date YYYY-MM-DD --json` の形式で起動する。他の `run_*.py` も同じ共通引数を使う。`--dry-run` はrunフォルダ内に隔離し、`--resume RUN_ID` は完了済み工程を飛ばして再開する。設定変更には `--proposal 設定差分.json`、復旧には `--run-id RUN_ID --step 工程名` を指定する。

状態表示は `automation\show_status.py --latest`、`--flow daily --date YYYY-MM-DD`、`--failed-only --days 7`、`--run-id RUN_ID` を使う。投稿管理画面は `python -m automation.run_dashboard --open` からlocalhostで開く。日次タスクの健康状態は `automation\check_daily_health.py --date YYYY-MM-DD --json` で確認する。Windowsタスク定義の生成は `automation\register_kai_daily_task.ps1 -ExportOnly`。タスク登録は切替工程で行う。

`rr-kai-observe` は既存の `scripts/check-affiliate.mjs` と `scripts/collect-room-likes.mjs`、`rr-kai-api-collect` は `scripts/research-batch.mjs` と `scripts/rakuten-api.mjs`、`rr-kai-inventory` と `rr-kai-dashboard` はPythonのローカル工程。これら4工程にLLMスキルは作らない。
