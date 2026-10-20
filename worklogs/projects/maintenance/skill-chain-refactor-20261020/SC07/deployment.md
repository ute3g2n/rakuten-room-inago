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