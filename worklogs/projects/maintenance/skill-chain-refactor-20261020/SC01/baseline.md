# SC01 現在版・固定identity基準

観測時刻: 2026-10-20 13:40:54 Asia/Tokyo。これは今回の実行開始時に読み直した値であり、計画作成時の値を現在値として再利用していない。

## Gitと原典

- 作業場所: `C:\project\rakuten-room-inago`。
- branch: `main`。HEADと`origin/main`はともに`0f73627dea4c5b1f913ff79bf0021212c321695b`。追跡先とのahead/behindは0/0。
- 開始時のcached、uncached、untracked変更: なし。`git status --short --branch`は`## main...origin/main`。
- 実行計画の開始原典: version 1.0、227,995 bytes、SHA-256 `001b904b786c1dea6c95b54a307f4e2ace60358f850308e0566c164a9b7afaf5`。
- 対応表: version 1.0、150,899 bytes、SHA-256 `643fe0b00d12a6a4057ca74c55feaaf6bc652ecbe3e335a24db3bf779c88e1ef`。
- 構成案: version 1.1、52,900 bytes、SHA-256 `0177ae92e0981eb1ab179b49d04739de9ffbfbc056daa3e7f7be52b79a28b3ec`。
- 計画独立reviewは原典SHAを対象として`approved`、修正要求0件。実装や本番適用の承認を意味しない。
- `settings/language.md`と`%USERPROFILE%\.codex\settings\language.md`はいずれも存在しない。日本語で記録する。

## 固定対象と今回のread-only読戻し

```json
{"job_id":"job-historical-9e7f0c175cc4c874c955a5dab29c4427","source_launch_id":"historical-9e7f0c175cc4c874c955a5dab29c4427","target_date":"2026-10-17","source_run_id":null,"incident_id":"352492da-ac58-4680-b1fd-fe5f24000229"}
```

maintenance DBは`rakuten-room-inago/runtime/maintenance/state.sqlite`をURI `mode=ro`、`PRAGMA query_only=ON`で開いた。schema versionは16。jobは旧結果の`complete`、resume_count 29、result_version `be04335443dc6cb1eddd7f464062eeaaf8d48a5a`、実行中attempt 0。manifestはincident/job/launch/date/null-runを指定値どおり保持し、SHA-256は`e97b191fdde1eb2f178c53bf51a5ad61979b69b80868a9a31b8accf31ab48d30`。この旧completeは新SC-A03の証拠ではない。

10/17のDaily runは0件。10/20 Dailyはmaintenance DB上で1件の起動がfailed、source_run_id=null、終了コード4。保存済み観測の安全な分類は`daily_status=failed`、`reason_code=daily_runtime_error`、`failure_phase=prepare`、`trace_status=not_written`。利用者から共有されたIdempotencyConflictは別の実行観測として保持するが、このDB snapshotには詳細原因や例外本文が残っておらず、原因は未確認。10/20 Dailyは再起動・再処理しない。

10/19保護対象を同一の投影・ハッシュ方法で前後比較するため、開始時はqueue 1件 (`queued`)、そのqueue event 0件、daily launch 1件 (`failed`, source_run_id null, exit_code 4)を保存した。比較用SHAは[この安全な開始digest](daily-protection-before.json)にある。10/19のqueue/history処理は実行していない。`WORKER_DISPATCH_EXCLUDED_DATES=frozenset({"2026-10-19"})`は変更していない。

最新deployment行のstatusは`deployed`、active commitは`6b6dc80a577cd6e9e75e0d3887b7b1bf23eba850`、pendingはなし。現在HEADはこれより後だが、active commitからHEADまでの`automation/`、`.agents/`、`.codex/`、`docs/standards/`の変更pathは0。本番適用版と現在HEADを同じ観測として扱わない。

## 日次コード版

現在HEADから取得したSHA-256:

| ファイル | SHA-256 |
|---|---|
| `automation/inago/daily_pipeline.py` | `d010be9965577358686eb9574ae11c59cfe85a273e9951316f1b83b7dab7d202` |
| `automation/inago/trace.py` | `92a2c9d7f4395839e1590539ea5adc57a48b267f5669ff19bc492acc112dcab9` |
| `automation/inago/orchestrator.py` | `f615a5c3c8df6a94412791f95273560ce45d023beba34932d80616c295d7f2a7` |
| `automation/inago/maintenance/cli.py` | `e274d1d1191a18c22b95cb882693dd506497c959bd641a0196c0d51b85471441` |
| `automation/inago/maintenance/worker.py` | `e741df2d0594270637aaa7b9107a1d6d915ae8cd19beb99c33672a202538f2c0` |
| `automation/inago/app_server.py` | `d32678392c90aa4d0a0a8c9310cdbd7c4ab7950171b9d9ec5a7c23864ba3019c` |

原因は未確定。10/17の直接原因と、10/20の準備時競合の原因を混同しない。

## 次の作業

次はSC02契約同期とSC03/SC04の本番経路変更。SC08で使う8用途の実行は、新runnerを通じて同じjobの同一invocation内に順番に起動する。正式invocation IDはcontrollerが割り当てるまでnull/unknownとし、ここでは採番しない。
