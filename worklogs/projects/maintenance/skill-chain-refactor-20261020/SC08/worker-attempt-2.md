# SC08 既存jobのWorker再開記録（第2再開）

## 固定対象

- job_id: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target_date: `2026-10-17`
- source_run_id: `null`
- 起動CLI: `.venv-inago\Scripts\python.exe -B -m automation.inago.maintenance.cli --data-root rakuten-room-inago/runtime worker --once --source-launch-id historical-9e7f0c175cc4c874c955a5dab29c4427`
- shell開始観測: 2026-10-20 17:06:23 JST。`--data-root`を付けない前回CLI試行は`data_root_required` / exit 1で、対象job処理前に失敗した。
- chain resume event: `2026-10-20T08:06:25.384Z`、既存chain start `2026-10-20T05:50:41.047Z`、resume_from_role=1。

## 途中の実測

- role 1 `rr-inago-incident-investigator`: runner event `role_call_finished`、`turn_status=completed`、`failure_code=null`。時刻 `2026-10-20T08:15:43.663Z`。出力は同job `investigation.md` 45,943 bytes / SHA-256 `5e5f7163928f297e9968ea8559fe211280f4382b0288de663bede0e41c632c6f`。入力incident hashは`b6d5e5d0470096614b3bcae3418df57110d7f41dc9700be0b8ab173a870779b5`。source_run_idはnullのまま。
- role 1完了後、runnerは同invocation内のrole 2 `common-implementer`を起動した。起動時点ではrole 2以降の終了状態とWorker processのexit codeはまだ観測できていないため、ここでは未確定とする。
- DBはread-onlyで再取得した。maintenance jobは同じID、`source_run_id=null`、`status=queued`、`resume_count=30`。10/19 worker queueは既存IDのまま`queued`、check/completed/reasonはnull、queue eventは0件。10/20 Daily launchは既存の準備失敗、exit 4、source_run_id nullの1行。Daily run DBの10/17・10/19・10/20 target_date行は0件。再開CLIはDailyを起動していない。

## 未確定・更新位置

- この追記時点ではWorker processは稼働中で、8 role invocationの最終終了結果は未確定。完了または具体的な操作失敗を観測したら、実終了時刻、integer exit、最新calls.jsonlのrole event、同job出力hash、DB再読値、10/19不変の結果をこの記録の追記として残す。
- 公開済み最終HTMLはGit/remoteにある2225 bytes / SHA-256 `67c0d8cb2b3a61f7bcf64160e3a8460fa6aeaae04e3555a80e96fcc66d250277`。再開処理が新しいHTMLを作成・修正した場合、作者更新後の実物を別HTML reviewerに再読させ、最新review済みbytesだけを再公開する。

## 第2再開の最終実測 — 2026-10-20 17:51 JST

- 実行終了: CLI process integer exit `0`。出力JSONは `status=maintenance_incomplete`, `reason_code=skill_chain_role_call_incomplete`, `maintenance_resolution_status=unknown`, `queue_id=null`。固定job/launch/dateは維持され、`source_run_id=null`。
- 同一chain: `chain_started_at=2026-10-20T05:50:41.047Z`。`calls.jsonl`にはrole 1–8の呼出し、最終role 8完了（08:51:30.667Z）、その直後の`chain_finished=incomplete`（08:51:30.778Z）がある。role 6 minimal HTML retryは`handoff_target_mismatch`でfailedとなったが、role 7 rereviewとrole 8 publicationはその後に実行された。
- runner eventの`previous_read_acknowledged`はrole 1–8すべてfalse、`previous_read_evidence=[]`。role threadのcompleted commandExecution記録では、各次担当が前担当Markdownを固定pathから`Get-Content -Raw -LiteralPath`で読み、exit 0となった。実コマンド記録とrunner flagの不一致は未解決として保持し、runnerが読取確認済みと認定したことにはしない。詳細は`role-read-ledger.md`。
- 最終chain artifact hash: `investigation.md` `5e5f7163928f297e9968ea8559fe211280f4382b0288de663bede0e41c632c6f`; `implementation.md` `4cadb6b242d0c65c0b899519a3ce5a237b0bfa7f8c978934f13d31a61ef3f9b5`; `test.md` `f896509648e3f35140f52cb47ec2dcd28fc244f4edad98ca2a08cc54b920b5d0`; `code-review.md` `30d3a309fa18d5a154959042b566b95a32650c56a6ff1e00e9fd1239fc4e7696`; `deployment.md` `9400641438ba926f02ffbc3e0266cfafc64a20dbee5a796e10b0fe85753f69c2`; `report.html` `a1275018b28c303630e78130d90a9926ded192173a2aaa68d457ef5e0bde88a8`; `html-review.md` `a4fc2237381c100d22a9a175bb01c3374999d0cbdebd05b79367c30a453dd00b`; `publication.md` `50bd76d618a88973bf8d9101e43a8f530fe9c3b2b9ae5f16f2e35d72d061d73d`。
- Worker後read-only DB照合: maintenance jobは同一ID、`status=queued`, `resume_count=30`, `finished_at=null`, `terminal_reason_code=null`, `source_run_id=null`。10/17 queueなし。10/19の既存queueは同じIDでqueued、event 0。10/20 Dailyは別launchの既存failed/exit 4/null-runのまま。10/17・10/19・10/20日次runは0件、下書き・予約も増加なし。`WORKER_DISPATCH_EXCLUDED_DATES=frozenset({"2026-10-19"})`を再確認した。
- 同job内の日次診断差分は`automation/inago/daily_pipeline.py`（SHA-256 `ca269b0e138093a1e2071d41a62b7d44a4fbdf16105a76f54ae0cea9ffb6265a`）。SCT06の隔離fixture試験と別製品reviewは通過したが、本番適用・適用版readback・同jobに結び付く新production traceは未実施。Git index書込とScheduled Task readがPermissionDeniedであり、原因unknownは維持する。
- SC09公開: review済みHTMLとローカル公開先はともに2314 bytes / SHA `a1275018b28c303630e78130d90a9926ded192173a2aaa68d457ef5e0bde88a8`。`origin/main`の公開HTMLは旧SHA `67c0d8cb2b3a61f7bcf64160e3a8460fa6aeaae04e3555a80e96fcc66d250277`のままで、remote同期は未実施。
- 次の再開: 既存jobのみを対象に、Git indexへの書込権限が回復したらtask-owned pathを明示stage/commit/pushし、許可されたTask Scheduler readback/固定root runtime switchで日次診断コードの適用版を確認する。その後、同一job/同一launchで既存resumeを使う。10/19/10/20 DailyとDaily手動起動は行わない。
