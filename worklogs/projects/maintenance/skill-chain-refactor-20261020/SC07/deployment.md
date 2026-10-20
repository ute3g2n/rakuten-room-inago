# SC07 本番コードroot適用・読戻し記録

## 対象と入力

- Workspace: `C:\project\rakuten-room-inago` のみ。worktree/checkout切替なし。
- Branch/upstream: `main` / `origin/main`。
- 適用commit: `dadffcd01ee7cc7672ad7f7c446839128dbe12e7`。runner: `3e8e3f34cd87739ef2ef57f88bc76f0fcd1fc93fa51a4fcea50ccd79e10d9e12`。
- 直前のremote headは `git ls-remote --heads origin main` で同じcommitを返した。pushは `0f73627..dadffcd main -> main`。
- 固定jobは `job-historical-9e7f0c175cc4c874c955a5dab29c4427` / launch `historical-9e7f0c175cc4c874c955a5dab29c4427` / date `2026-10-17` / `source_run_id=null`。

## 適用前のread-only確認

適用直前に `TaskSchedulerRuntimeSwitcher` を固定root/`rakuten-room-inago/runtime` で作り直し、task snapshot・switch-state・Git identity・production preflightを読んだ。HEADとbranchは `dadffcd...` / `main`、upstream `origin/main`、task WorkingDirectory commitも同hash。Daily/Worker/Watchdog/HumanApiは全て `Ready`、有効、固定workspace向きで、`healthy(dadffcd...)` はtrue。runtime switch-stateだけ古い `6b6dc80a577cd6e9e75e0d3887b7b1bf23eba850` をactiveとしていた。稼働root・差分・対象版を再照合してから切替を実行した。

## 実適用と読戻し

`.venv-inago/Scripts/python.exe -B -c "...TaskSchedulerRuntimeSwitcher.activate(commit); confirm_active(commit)..."` を1プロセスで実行。`activate` 中の4タスク全てdisabled/fencedであることを読戻し、その後 `confirm_active` がhealthを確認して元の有効状態へ戻した。コマンド整数exit 0。switch-stateの最終書込時刻は2026-10-20 14:42:13.0040667 JST、ファイルSHA-256 `7f0578aaa34cdef47abdadd0eb6a4f4619d715a246dc18c2bbdbcdf2160bc39b`。

- Task names: RakutenRoomInagoDaily, RakutenRoomInagoWorker, RakutenRoomInagoWatchdog, RakutenRoomInagoHumanApi。
- 各taskの最終 `WorkingDirectory`: 固定workspace。
- 最終 `Enabled`: true、`State`: `Ready`。
- 最終active commit: `dadffcd01ee7cc7672ad7f7c446839128dbe12e7`、active root: 固定workspace、pending: null、status: active。
- postflight `healthy(dadffcd...)`: true。TaskSchedulerRuntimeSwitcherは切替前後のExecute/Argumentsも同一であることを検査し成功した。

本操作はDailyを起動せず、10/19 queue/historyにも触れていない。これはSC07の適用/読戻しのみで、同一jobでの日次差分・試験・別review・日次版適用・同job新証拠およびreport/publicationはSC08/09/10で未実施。

## 引継ぎ

- 次担当: 固定jobの連続8-role skill-chain invocation。
- 入力: incident.md、原調査/review、SC01–07記録、commit `dadffcd...`。
- 次作業: 既存jobのみを明示し、single CLI invocationでrole 1–8を実起動。Daily/worker/10/19/10/20処理は禁止のまま。
## SC07 同一固定rootへの追補適用（2026-10-20 21:59:03 +09:00）

- read-only preflight: `TaskSchedulerRuntimeSwitcher` を `repository=C:\project\rakuten-room-inago`, `runtime_root=C:\project\rakuten-room-inago\rakuten-room-inago\runtime`, `worktree_root=C:\project` で構成。main HEAD/upstream `4569a34d89a9538036a3058f4b94417e4d74fedf`、適用前4 tasksは固定workspace向きでReady/enabled。switch-stateの旧active digestと現在のtracked working bytesが不一致、`healthy(HEAD)=false` を確認。
- apply/confirm: `2026-10-20T12:59:03.4257866Z` 実行の `TaskSchedulerRuntimeSwitcher.activate(4569a34d89a9538036a3058f4b94417e4d74fedf); confirm_active(...)` は完了。別read-backでactive commit `4569a34d89a9538036a3058f4b94417e4d74fedf`、active fixed root、pending null、status active、content SHA-256 `0b383aa6592cf86325dd8ea6d01b115fcd088cc0fb5622581ed92fd9bc6ac142`、health true、Daily/Worker/Watchdog/HumanApi全てReady/enabled・同じfixed WorkingDirectoryを確認。実行ラッパーは30秒で出力待ちを切り上げたためconfirmのstdout/exitを直接取得できなかったが、プロセス終了後の状態読戻しでactive/healthyを確認。
- active content digestはHEADに加えて適用時点のtracked working bytesを束ねる。この時点で既存の未stage変更 `deployment.py`, `runtime_switcher.py`, `test_maintenance_deployment.py` が存在していたため、それらのSHAはdigestに含まれるが、この追補コミットへは混ぜていない。
- 先行read-only constructor probeは位置引数で呼んだためTypeErrorとなり、状態変更前に終了。直後、必須keyword引数を使った正しい構成でread/apply/confirmを実施した。
- Dailyを手動起動せず、10/19 queue/historyにも触れていない。同じ10/17 job read-only DB preflight: `status=queued`, `resume_count=30`, `source_run_id=null`; input manifestは同じlaunch ID・target_date `2026-10-17`。worker queueには同launchの新しい行はなかった。
- 次: 同一job/launchを明示したWorker `--once` の1回だけを実行し、8担当の呼出し・引継ぎ読取・production trace・terminalの新しい証拠を検査する。
