# FC10-F4 execution handoff

## Result

Added an independent secondary JSONL journal at the maintenance root's `failure-journal-secondary/events.jsonl`. A primary append error is captured as safe exception class/cause, errno, code frames, and capture location inside the same correlated event before that event is appended to the secondary file. When both sinks fail, `append_failure_event` returns sanitized details and `tracked_operation` still yields to its business operation.

Reconciliation and DB replay now read both JSONL sources. Reconciliation recognizes a begin record in the secondary spool and writes an interruption observation. Replay de-duplicates equal event IDs across the sources; the DB's `INSERT OR IGNORE` keyed by `event_id` makes repeated replay idempotent across restarts.

## Reproduction and checks

The focused pytest command was first run before implementation. It returned exit 1 with one expected failure: the injected primary open failure returned `unavailable`, and the independent spool did not exist. After the change, the same two exact test nodes returned exit 0 (`2 passed in 0.32s`). They cover secondary-spool recovery/reconciliation, reading primary and secondary events, two replay passes into a temporary SQLite store with no duplicate event IDs, and continued business execution when both `os.open` calls fail.

`git diff --check` and `py_compile` on the two assigned source/test files both returned exit 0. No production state or external service was used.

## Files and revisions

- `automation/inago/maintenance/failure_journal.py` — SHA-256 `244c9378776c134896d84ba9bfd36cdfb35d28af2695b3836fe0f4443b59b568`
- `tests_inago/test_maintenance_integration.py` — SHA-256 `f84b5ce3265b31e08371ab5ae34e73ad027de9051f238be3eb8b2b7441352a53`
- Exact structured record: `execution-result.json`

## Limitation

If both independent file sinks fail, there cannot be a durable local journal event. The function returns safe details about both failures and emits only a generic stderr marker; this does not stop the caller. The focused verification does not claim the 10/17 production job or whole workflow completed.
