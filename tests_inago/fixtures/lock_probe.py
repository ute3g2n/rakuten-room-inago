"""Small owned process used to verify the Windows daily lock contract."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.inago.run_lock import RunLock, RunLockBusy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock-path", required=True)
    parser.add_argument("--owner-path", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ready-path")
    parser.add_argument("--release-path")
    parser.add_argument("--pid-path")
    args = parser.parse_args()
    try:
        lock = RunLock.acquire(args.lock_path, args.owner_path, run_id=args.run_id)
    except RunLockBusy as exc:
        print(
            json.dumps(
                {
                    "status": "locked",
                    "owner": exc.owner.to_dict() if exc.owner else None,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return 10
    with lock:
        print(
            json.dumps(
                {"status": "acquired", "owner": lock.owner.to_dict()},
                separators=(",", ":"),
            ),
            flush=True,
        )
        if args.ready_path:
            Path(args.ready_path).write_text("ready", encoding="utf-8")
        if args.pid_path:
            Path(args.pid_path).write_text(str(lock.owner.pid), encoding="ascii")
        if args.release_path:
            release = Path(args.release_path)
            deadline = time.monotonic() + 10
            while not release.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not release.exists():
                return 11
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
