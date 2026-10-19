# FC01 CLI command discovery supplement

- Observation time: 2026-10-19 JST, during the same fixed-workspace run.
- The system `python -m automation.inago.maintenance.cli --help` exited 1 before argument parsing because its interpreter lacks `jsonschema` (`ModuleNotFoundError`). No CLI operation ran. The repository runtime `.venv-inago\Scripts\python.exe` imported the command successfully.
- Read-only help commands and results:
  - `.venv-inago\Scripts\python.exe -m automation.inago.maintenance.cli --help` — exit 0. Available commands include `worker`, `resume-worker`, and `enqueue-historical`.
  - `... cli worker --help` — exit 0; requires `--once`, accepts `--source-launch-id`.
  - `... cli resume-worker --help` — exit 0; accepts `--source-launch-id` and optional existing `--job-id`; it queues/resumes but does not run the workflow.
  - `... cli enqueue-historical --help` — exit 0; historical enqueue is a separate command and will not be used for this already-registered job.
- Source behavior read-only confirmation: when `run_worker_once` receives an explicit `source_launch_id`, it queries only that worker queue row; if absent, it queries historical references for that same launch ID. It does not use automatic queue selection. This is the supported one-shot entry point for the specified historical launch. The planned invocation is `.venv-inago\Scripts\python.exe -m automation.inago.maintenance.cli --data-root runtime worker --once --source-launch-id historical-9e7f0c175cc4c874c955a5dab29c4427`. The actual production data root, job identity, and no-duplicate precondition must be re-read immediately before FC10; this is command discovery, not execution or completion evidence.
- No worker, historical enqueue, Daily task, queue mutation, or production write was performed by this discovery.
