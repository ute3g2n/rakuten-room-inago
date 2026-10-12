"""Safe CLI shell; execution modes remain explicit unimplemented stubs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from automation.inago.paths import PathContractError, resolve_runtime_paths


SUPPORTED_MODES = (
    "setup",
    "daily",
    "status",
    "recovery",
    "config-change",
    "export",
    "serve",
    "migrate",
    "backup",
    "restore-check",
    "acceptance",
)
EXIT_INVALID_INPUT = 2
EXIT_NOT_IMPLEMENTED = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inago")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in SUPPORTED_MODES:
        mode_parser = subparsers.add_parser(mode)
        mode_parser.add_argument("--config", required=True)
        mode_parser.add_argument("--data-root", required=True)
    return parser


def _emit(stream: TextIO, payload: dict[str, str]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    ports: object | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Validate the fixture boundary, then report that modes are not ready.

    ``ports`` is injected so future modes can depend on explicit adapters. This
    scaffold never inspects or invokes it; no service, DB, or UI can run here.
    """

    del ports
    output = stdout if stdout is not None else sys.stdout
    error_output = stderr if stderr is not None else sys.stderr
    args = _parser().parse_args(argv)

    try:
        paths = resolve_runtime_paths(
            data_root=args.data_root,
            config_path=args.config,
            fixture_mode=True,
        )
        config = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, PathContractError) as exc:
        _emit(error_output, {"status": "invalid_input", "reason": str(exc)})
        return EXIT_INVALID_INPUT

    if not isinstance(config, dict):
        _emit(
            error_output,
            {"status": "invalid_input", "reason": "config must be an object"},
        )
        return EXIT_INVALID_INPUT
    if (
        config.get("environment") != "fixture"
        or config.get("network_allowed") is not False
    ):
        _emit(
            error_output,
            {
                "status": "invalid_input",
                "reason": "fixture mode must disable network access",
            },
        )
        return EXIT_INVALID_INPUT

    _emit(output, {"status": "not_implemented", "mode": args.mode})
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    raise SystemExit(main())
