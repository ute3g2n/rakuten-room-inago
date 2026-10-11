"""Audit storage for one run and its monthly/status indexes."""

import os
from pathlib import Path

from automation.atomic_io import append_jsonl_unique, atomic_write_json, atomic_write_text
from automation.redaction import redact, redact_data
from automation.schema_validator import validate_document


class ExecutionLogger:
    def __init__(self, root: Path, target_date: str, run_id: str):
        self.root = Path(root)
        self.run_dir = self.root / "runs" / target_date / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.run_dir / "manifest.json"
        self.events_path = self.run_dir / "events.jsonl"

    def write_manifest(self, manifest: dict) -> None:
        safe = redact_data(manifest)
        validate_document("manifest", safe)
        atomic_write_json(self.manifest_path, safe)

    def append_event(self, event: dict) -> bool:
        safe = redact_data(event)
        validate_document("event", safe)
        return append_jsonl_unique(self.events_path, safe, "event_id")

    def append_output(self, kind: str, text: str) -> None:
        if kind not in {"stdout", "stderr"}:
            raise ValueError("invalid log kind")
        path = self.run_dir / f"{kind}.log"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(redact(text) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def finish_indexes(self, manifest: dict, exit_code: int) -> None:
        safe = redact_data(manifest)
        validate_document("manifest", safe)
        history = self.root / "history" / f"execution-history-{safe['target_date'][:7]}.jsonl"
        summary = {"run_id": safe["run_id"], "parent_run_id": safe["parent_run_id"], "attempt": safe["attempt"], "flow": safe["flow"], "source": safe["source"], "target_date": safe["target_date"], "status": safe["status"], "started_at": safe["started_at"], "finished_at": safe["finished_at"], "exit_code": exit_code, "last_stage": safe["last_stage"], "errors": safe["errors"], "manifest_path": str(self.manifest_path)}
        append_jsonl_unique(history, summary, "run_id")
        status = self.root / "status"
        atomic_write_json(status / "latest.json", safe)
        if safe["flow"] == "daily":
            atomic_write_json(status / "latest-daily.json", safe)
            message = f"{safe['target_date']} {safe['status']} run_id={safe['run_id']} finished_at={safe['finished_at']} exit_code={exit_code}\n"
            atomic_write_text(status / "latest-daily-status.txt", message)
