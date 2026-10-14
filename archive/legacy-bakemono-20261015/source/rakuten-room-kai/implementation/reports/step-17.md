# Step 17 従来入口の無効化と新タスクへの切替

実施日: 2026-09-25 JST。判定: **success**。Step 16 success、cutover_approved、release-readinessにFAIL/BLOCKEDなし、staging ZIP 16件の読取を前提確認した。

新本番タスクを登録して当初Disabledで設定検査した。Daily開始境界2026-09-25 13:00 JST、HealthCheck 14:00 JST。過去日のcatch-upなし。新Dailyを09:33にオンデマンド実行し、run_id `20260925-093328-daily-fca1b4`、source=scheduler、9工程完了、manifest `partial`、Windows終了コード2、errors 0を確認した。これは適格商品0件による許容partial。`daily-plan.json`、`daily-report.json`、`posting-dashboard.html`、status表示が成立し、投稿0件と不足理由を記録した。

旧タスク最新XML・状態を `archive/legacy-skills/.staging/` に保存後、旧 `RakutenRoomBakemonoAffiliateDaily` をDisabledにした。旧root `SKILL.md` は内容を変えず `SKILL.md.disabled` にrenameし、前後SHA-256と50,524バイトを確認。新DailyとHealthCheckは両方Ready（Enabled）、次回09/25 13:00/14:00、WakeToRunおよびStartWhenAvailable有効、user `ute3g`、action `.venv-kai/Scripts/python.exe`、作業ディレクトリを確認した。新9スキルのentrypointは存在し、旧root entrypointは発見対象から外れた。削除ファイル0。

切替後状態とロールバック手順は `cutover-state.json`。初回予定自動実行は2026-09-25 13:00 JSTであり、その結果は現時点では未観測。Step 18へ進む。
