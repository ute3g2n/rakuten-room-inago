from __future__ import annotations

import json
import time
from pathlib import Path

from automation.inago.conversation_logs import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    ConversationLogWatcher,
    conversation_log_directory,
    sync_conversation_logs,
)


def _rollout(
    sessions: Path,
    thread_id: str,
    *,
    parent_thread_id: str | None = None,
    thread_source: str | None = None,
    body: bytes = b"",
    day: str = "2026/10/21",
) -> Path:
    directory = sessions / day
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"id": thread_id}
    if parent_thread_id is not None:
        payload["parent_thread_id"] = parent_thread_id
    if thread_source is not None:
        payload["thread_source"] = thread_source
    path = directory / f"rollout-2026-10-21T00-00-00-{thread_id}.jsonl"
    path.write_bytes(
        json.dumps({"type": "session_meta", "payload": payload}).encode("utf-8")
        + b"\n"
        + body
    )
    return path


def test_sync_copies_root_child_and_grandchild_bytes_only(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    sessions = codex_home / "sessions"
    root_path = _rollout(sessions, "root", body=b"RAW_ROOT_BODY\n")
    child_path = _rollout(
        sessions,
        "child",
        parent_thread_id="root",
        thread_source="subagent",
        body=b"RAW_CHILD_BODY\n",
    )
    grandchild_path = _rollout(
        sessions,
        "grandchild",
        parent_thread_id="child",
        thread_source="subagent",
        body=b"RAW_GRANDCHILD_BODY\n",
    )
    _rollout(
        sessions,
        "unrelated",
        parent_thread_id="different-root",
        thread_source="subagent",
        body=b"UNRELATED_BODY\n",
    )

    result = sync_conversation_logs(
        tmp_path / "runtime",
        flow="daily",
        started_on="2026-10-21",
        flow_id="run-01",
        role="ai-o",
        root_thread_id="root",
        codex_home=codex_home,
    )

    assert result.status == "complete"
    assert result.copied_count == 3
    destination = result.directory
    assert (destination / "main__root.jsonl").read_bytes() == root_path.read_bytes()
    assert (destination / "sub__child.jsonl").read_bytes() == child_path.read_bytes()
    assert (destination / "sub__grandchild.jsonl").read_bytes() == grandchild_path.read_bytes()
    assert not (destination / "sub__unrelated.jsonl").exists()
    status = (destination / "status.txt").read_text(encoding="utf-8")
    assert "root_thread_id=root" in status
    assert "parent_thread_id=child" in status
    assert "RAW_ROOT_BODY" not in status
    assert "RAW_CHILD_BODY" not in status


def test_same_flow_resume_keeps_old_threads_and_separates_other_runs(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    sessions = codex_home / "sessions"
    _rollout(sessions, "first-root")
    first = sync_conversation_logs(
        tmp_path / "runtime",
        flow="maintenance",
        started_on="2026-10-21T00:00:00+09:00",
        flow_id="job:one/unsafe",
        role="investigator",
        root_thread_id="first-root",
        codex_home=codex_home,
    )
    _rollout(sessions, "retry-root")
    retry = sync_conversation_logs(
        tmp_path / "runtime",
        flow="maintenance",
        started_on="2026-10-21T00:00:00+09:00",
        flow_id="job:one/unsafe",
        role="investigator",
        root_thread_id="retry-root",
        codex_home=codex_home,
    )
    other_run = sync_conversation_logs(
        tmp_path / "runtime",
        flow="maintenance",
        started_on="2026-10-21",
        flow_id="job-two",
        role="investigator",
        root_thread_id="retry-root",
        codex_home=codex_home,
    )

    assert first.directory == retry.directory
    assert (retry.directory / "main__first-root.jsonl").is_file()
    assert (retry.directory / "main__retry-root.jsonl").is_file()
    assert "thread_id=first-root" in (retry.directory / "status.txt").read_text(
        encoding="utf-8"
    )
    assert other_run.directory != retry.directory
    assert other_run.directory.is_relative_to(tmp_path / "runtime" / "conversation-logs")
    assert conversation_log_directory(
        tmp_path / "runtime",
        flow="maintenance",
        started_on="2026-10-21",
        flow_id="job-two",
        role="tester",
    ) != other_run.directory


def test_watcher_copies_appends_while_turn_is_active(tmp_path: Path) -> None:
    assert DEFAULT_POLL_INTERVAL_SECONDS == 30
    codex_home = tmp_path / "codex-home"
    source = _rollout(
        codex_home / "sessions",
        "active-root",
        body=b"FIRST_TURN\n",
    )
    watcher = ConversationLogWatcher(
        tmp_path / "runtime",
        flow="daily",
        started_on="2026-10-21",
        flow_id="run-active",
        role="ai-o",
        root_thread_id=lambda: "active-root",
        codex_home=codex_home,
        poll_interval_seconds=0.02,
    )

    watcher.start()
    try:
        destination = watcher.directory / "main__active-root.jsonl"
        assert destination.read_bytes().endswith(b"FIRST_TURN\n")
        with source.open("ab") as stream:
            stream.write(b"SECOND_TURN\n")
            stream.flush()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if destination.read_bytes().endswith(b"SECOND_TURN\n"):
                break
            time.sleep(0.01)
        assert destination.read_bytes().endswith(b"SECOND_TURN\n")
    finally:
        watcher.stop()


def test_missing_rollout_is_recorded_without_raising(tmp_path: Path) -> None:
    result = sync_conversation_logs(
        tmp_path / "runtime",
        flow="daily",
        started_on="2026-10-21",
        flow_id="run-missing",
        role="ai-o",
        root_thread_id="not-yet-created",
        codex_home=tmp_path / "missing-codex-home",
    )

    assert result.status == "partial"
    assert result.reason_code == "root_thread_not_found"
    status = (result.directory / "status.txt").read_text(encoding="utf-8")
    assert "reason_code=root_thread_not_found" in status
