# S04 本番 read-only 観測（resume前）

- Workspace: C:\project\rakuten-room-inago
- Query mode: SQLite URI mode=ro + PRAGMA query_only=ON; DB/Scheduler/queue/historyに書込みなし.
- Capture interval: 2026-10-19T20:36:40.7904901+09:00 ～ 2026-10-19T20:36:42.2412393+09:00
- Exact command purpose: target maintenance job, manifest, attempts, relevant leases, 10/19 queue/events/watchdog queue; Python maintenance process; three scheduled task readbacks.

## Maintenance DB snapshot

- captured_at_jst: 2026-10-19T20:36:40+09:00
- database: maintenance state.sqlite, schema=13, application_id=1229866829, quick_check=ok
- job: status=running, resume_count=7, source_run_id=null, updated_at_utc=2026-10-19T10:45:30.005772Z, terminal_reason=null
- manifest: incident_id=352492da-ac58-4680-b1fd-fe5f24000229, source_launch_id=historical-9e7f0c175cc4c874c955a5dab29c4427, source_run_id=null, target_date=2026-10-17, sha256=e97b191fdde1eb2f178c53bf51a5ad61979b69b80868a9a31b8accf31ab48d30
- attempt: sequence=1 step=maintenance_investigation no=1 status=complete attempt_id=084a8a5a-adca-4da7-9d05-1de201344987 source_run_id=null started_at_utc=2026-10-18T22:09:30.897331Z finished_at_utc=2026-10-18T22:14:56.091406Z
- attempt: sequence=1 step=maintenance_investigation no=2 status=complete attempt_id=3c8c0ca4-0c47-4e30-a992-5eda9e44ecac source_run_id=null started_at_utc=2026-10-18T23:24:19.059504Z finished_at_utc=2026-10-18T23:30:42.681350Z
- attempt: sequence=1 step=maintenance_investigation no=3 status=complete attempt_id=1f3baae0-1b5c-436c-ac19-aae8b4644b2a source_run_id=null started_at_utc=2026-10-18T23:59:17.160656Z finished_at_utc=2026-10-19T00:06:12.203895Z
- attempt: sequence=1 step=maintenance_investigation no=4 status=blocked attempt_id=b8b2a5a2-d95e-4717-804b-83b13ef6732e source_run_id=null started_at_utc=2026-10-19T00:49:26.286722Z finished_at_utc=2026-10-19T00:52:53.226651Z
- attempt: sequence=1 step=maintenance_investigation no=5 status=complete attempt_id=b1b81722-37ff-4445-beff-ad1a71c2bae5 source_run_id=null started_at_utc=2026-10-19T04:30:27.630877Z finished_at_utc=2026-10-19T04:42:50.845615Z
- attempt: sequence=1 step=maintenance_investigation no=6 status=complete attempt_id=dacda68d-92d0-4271-b5dd-2cc0069c96f3 source_run_id=null started_at_utc=2026-10-19T04:42:50.971082Z finished_at_utc=2026-10-19T04:52:07.855483Z
- attempt: sequence=1 step=maintenance_investigation no=7 status=interrupted attempt_id=50dfedb2-3a19-4594-982e-ee1022406b97 source_run_id=null started_at_utc=2026-10-19T04:52:08.018331Z finished_at_utc=2026-10-19T05:04:27.632526Z
- attempt: sequence=1 step=maintenance_investigation no=8 status=complete attempt_id=82296e84-d1ac-4643-b248-9767cd8fad7d source_run_id=null started_at_utc=2026-10-19T05:04:27.632526Z finished_at_utc=2026-10-19T05:11:57.147979Z
- attempt: sequence=2 step=maintenance_planning no=1 status=running attempt_id=3df719f6-ef87-4734-8279-9ed14b23eeaa source_run_id=null started_at_utc=2026-10-19T10:45:30.005772Z finished_at_utc=null
- lease: state=released generation=10 updated_at_utc=2026-10-19T01:23:28.830007Z
- lease: state=released generation=1 updated_at_utc=2026-10-19T10:45:34.682290Z
- 2026-10-19 worker_queue: [('queued', 1)]; event_count=0
- 2026-10-19 watchdog_queue: []; total=0

## Process and Task Scheduler

- Matching maintenance/Daily Python process count: 0
- RakutenRoomInagoDaily: State=Ready, Enabled=True, LastRun=2026/10/19 13:00:01, LastResult=4, NextRun=2026/10/20 13:00:00
- RakutenRoomInagoWatchdog: State=Ready, Enabled=True, LastRun=2026/10/19 13:24:01, LastResult=2, NextRun=2026/10/20 12:55:00
- RakutenRoomInagoWorker: State=Ready, Enabled=True, LastRun=2026/10/19 7:09:29, LastResult=3, NextRun=

## Post-push, immediate pre-resume check

- Capture: 2026-10-19T20:38:10.5218049+09:00 ～ 2026-10-19T20:38:11.9758163+09:00
- Git: branch=main, HEAD=5f206cc6d3b81d4695f7d827a04b1afd1fb7f653
- Worker exclusion constant: 31:WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})
- Matching maintenance/Daily Python process count: 0
- Scheduled tasks:
RakutenRoomInagoDaily State=Ready Enabled=True Next=2026/10/20 13:00:00; RakutenRoomInagoWatchdog State=Ready Enabled=True Next=2026/10/20 12:55:00; RakutenRoomInagoWorker State=Ready Enabled=True Next=

### Read-only maintenance DB

schema 13 quick_check ok
job {'status': 'running', 'resume_count': 7, 'source_run_id': None, 'lease_key': 'maintenance:worker:a7a1acb769a54c4ea6a8b956073dea7b', 'updated_at': '2026-10-19T10:45:30.005772Z'}
manifest {'incident_id': '352492da-ac58-4680-b1fd-fe5f24000229', 'source_launch_id': 'historical-9e7f0c175cc4c874c955a5dab29c4427', 'source_run_id': None, 'target_date': '2026-10-17', 'manifest_sha256': 'e97b191fdde1eb2f178c53bf51a5ad61979b69b80868a9a31b8accf31ab48d30'}
planning_attempt {'attempt_id': '3df719f6-ef87-4734-8279-9ed14b23eeaa', 'status': 'running'}
job_lease {'state': 'released', 'generation': 1, 'updated_at': '2026-10-19T10:45:34.682290Z'}
worker_queue_2026-10-19 [('queued', 1)] events 0
watchdog_queue_2026-10-19 [] total 0

## Official same-job resume-worker

- Command: .\.venv-inago\Scripts\python.exe -m automation.inago.maintenance.cli --data-root C:\project\rakuten-room-inago\rakuten-room-inago\runtime --maintenance-id d587b04ff9314ddebd125c20112a0460 resume-worker --source-launch-id historical-9e7f0c175cc4c874c955a5dab29c4427 --job-id job-historical-9e7f0c175cc4c874c955a5dab29c4427
- Started: 2026-10-19T20:38:20.9200145+09:00
- Ended: 2026-10-19T20:38:21.9564456+09:00
- Exit code: 0
- Output:
  {"job_id": "job-historical-9e7f0c175cc4c874c955a5dab29c4427", "resume_count": 8, "resume_event_id": "e2f6cd80971746c4a8700934f1099461", "source_launch_id": "historical-9e7f0c175cc4c874c955a5dab29c4427", "status": "queued"}

## Official one-shot Worker for the existing historical launch

- Command: .\.venv-inago\Scripts\python.exe -m automation.inago.maintenance.cli --data-root C:\project\rakuten-room-inago\rakuten-room-inago\runtime --maintenance-id d587b04ff9314ddebd125c20112a0460 worker --once --source-launch-id historical-9e7f0c175cc4c874c955a5dab29c4427
- Started: 2026-10-19T20:38:29.4354528+09:00
- Ended: 2026-10-19T20:46:20.1851608+09:00
- Exit code: 3
- Output:
  {"check_id": "d8178c1e-61fb-4a53-a4e5-0216722fda1b", "duplicate": false, "maintenance_resolution_status": "blocked", "queue_id": null, "reason_code": "maintenance_workflow_stage_failed", "schema_version": "maintenance-worker-result-v3", "scope": "maintenance_workflow", "source_launch_id": "historical-9e7f0c175cc4c874c955a5dab29c4427", "status": "maintenance_incomplete", "worker_job_id": "job-historical-9e7f0c175cc4c874c955a5dab29c4427"}

## Post-Worker read-only observation

- Capture interval: 2026-10-19 20:46:44.211670～20:46:59.704861 JST. The one-shot Worker ended at 20:46:20.1851608 JST, exit code 3.
- Read-only maintenance DB: schema/user_version 13; `quick_check=ok`.
- Same target job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`: DB row still reports `status=running`, `resume_count=8`, `source_run_id=null`, `finished_at=null`, `terminal_reason_code=null`. This DB value is distinct from the Worker CLI result `maintenance_incomplete` / `maintenance_resolution_status=blocked` / `reason_code=maintenance_workflow_stage_failed`; do not describe the DB job row as terminal-blocked or completed.
- Latest workflow invocation `workflow-0b3e8042053547f88986f13a7398c86d`: check_incident, evidence, job, investigation = complete; planning = blocked (`planning_author_turn_failed`); repair = blocked (`repair_plan_not_approved`); deployment = blocked (`deployment_candidate_repair_unavailable`); report = blocked (`maintenance_workflow_stage_failed`). Existing planning step attempt `3df719f6-ef87-4734-8279-9ed14b23eeaa` remains `running` with no output/finished time. No report step_attempt row was present in the post-run query. The stage-observation rows preserve the stage outcomes.
- 2026-10-19 worker queue group: 1 `queued`; `worker_queue_event` rows 0; `watchdog_queue` rows 0. Matching maintenance/Daily Python processes: 0. `WORKER_DISPATCH_EXCLUDED_DATES` remains exactly `frozenset({"2026-10-19"})`.
- Task Scheduler read-back at 20:46:55 JST: Daily, Worker, Watchdog all Ready and Enabled. Daily next 2026-10-20 13:00; last 2026-10-19 13:00:01/result 4. Worker has no next trigger; its action points at the fixed repository runtime and `worker --once`. Watchdog next 2026-10-20 12:55; action points at fixed repository runtime. No Daily/Watchdog/Worker task was manually started as part of U25.
- Formal deployment ledger query (corrected after an initial read-only query asked for nonexistent `deployment.updated_at`): latest row `9f8a0536-cba8-4e4e-9a2b-e3add8b5c583`, release `U23-REPORT-MD-20261019-7a90002`, status `deployed`, active/result SHA `22d68761cc47e089bfc2ed59dc9eadc4e69126ee`, deployed_at `2026-10-19T05:03:33.523174Z`. This remains different from the code commit `5f206cc6d3b81d4695f7d827a04b1afd1fb7f653` pushed to `origin/main`; repo task actions point at the fixed workspace, but formal ledger activation of that newer SHA is unconfirmed.
- The result does not establish root cause, repair, recovery of the 10/17 daily, real posting count, recurrence-free operation, or successful publication. Continue with report review/finding correction/publication and U24/U25 documentation.
