"""Start/reuse the local history server and open its authenticated HTTP page."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import webbrowser

from automation.inago.history_session import HISTORY_SERVICE, history_runtime_id
from automation.inago.paths import PROJECT_ROOT
from automation.inago.production_runtime import (
    HumanApiRuntimeConfig,
    load_human_api_config,
    read_human_api_token,
)


class HistoryLaunchError(RuntimeError):
    """The requested history service could not be prepared safely."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never forward the private bearer key to a redirect.
        return None


def _ready_ticket(config: HumanApiRuntimeConfig, token: str) -> str:
    origin = f"http://127.0.0.1:{config.human_api_port}"
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(origin + "/health", timeout=1) as response:
        health = json.load(response)
    if health.get("status") != "ready":
        raise HistoryLaunchError("投稿履歴DBの準備が完了していません。")
    request = Request(
        origin + "/api/history-launch-ticket",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": origin,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with opener.open(request, timeout=1) as response:
        result = json.load(response)
    if result.get("service") != HISTORY_SERVICE or result.get(
        "runtime_id"
    ) != history_runtime_id(config.runtime_root):
        raise HistoryLaunchError(
            "指定ポートのサーバーが別のDBまたは非互換のサービスです。"
        )
    ticket = result.get("ticket")
    if not isinstance(ticket, str) or not 32 <= len(ticket) <= 128:
        raise HistoryLaunchError("投稿履歴の起動受付が不正です。")
    return ticket


def _start_server(config: HumanApiRuntimeConfig, config_path: Path) -> subprocess.Popen:
    log_path = config.runtime_root / "logs" / "history-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-B",
        "-m",
        "automation.inago.cli",
        "serve",
        "--environment",
        "production",
        "--config",
        str(config_path),
        "--data-root",
        str(config.runtime_root),
    ]
    with log_path.open("ab") as log:
        return subprocess.Popen(
            command,
            cwd=config.application_root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )


def _record(config: HumanApiRuntimeConfig, status: str, **values: object) -> None:
    path = config.runtime_root / "logs" / "history-launcher.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "status": status,
                        **values,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        print("投稿履歴の起動ログを保存できませんでした。", file=sys.stderr)


def launch_history(
    config: HumanApiRuntimeConfig,
    *,
    config_path: Path,
    open_browser: Callable[[str], bool] | None = None,
    start_server: Callable[
        [HumanApiRuntimeConfig, Path], subprocess.Popen
    ] = _start_server,
    open_page: bool = True,
    timeout: float = 30,
) -> dict[str, object]:
    """Wait for the same runtime, then open a single-use bootstrap URL."""
    if not (config.runtime_root / "state.sqlite").is_file():
        raise HistoryLaunchError(
            "既存の投稿履歴DBが見つかりません。DBは初期化していません。"
        )
    token = read_human_api_token(config)
    deadline = time.monotonic() + timeout
    child = None
    first_probe = True
    while True:
        try:
            ticket = _ready_ticket(config, token)
            break
        except HTTPError as exc:
            if exc.code != 503:
                raise HistoryLaunchError(
                    f"指定ポートのサービスへ接続できません（HTTP {exc.code}）。"
                ) from None
        except (URLError, TimeoutError, ConnectionError):
            if first_probe:
                child = start_server(config, config_path)
                _record(config, "starting", pid=child.pid)
        except (ValueError, AttributeError):
            raise HistoryLaunchError(
                "指定ポートのサービスが投稿履歴APIではありません。"
            ) from None
        first_probe = False
        if time.monotonic() >= deadline:
            raise HistoryLaunchError(
                f"サーバーの起動待ちが{timeout:g}秒を超えました。history-server.logを確認してください。"
            )
        if child is not None and child.poll() is not None:
            # A concurrent double-click can lose the bind race; probe the winner.
            try:
                ticket = _ready_ticket(config, token)
                break
            except (URLError, TimeoutError, ConnectionError, ValueError):
                raise HistoryLaunchError(
                    "サーバーが起動中に終了しました。history-server.logを確認してください。"
                ) from None
        time.sleep(0.2)
    page_url = f"http://127.0.0.1:{config.human_api_port}/posting-history.html"
    opened = False
    if open_page:
        opened = bool((open_browser or webbrowser.open)(page_url + "#launch=" + ticket))
        if not opened:
            raise HistoryLaunchError(
                "既定ブラウザーを開けませんでした。ブラウザー設定を確認してください。"
            )
    result = {
        "status": "ready",
        "server": "started" if child else "reused",
        "url": page_url,
        "browser_opened": opened,
    }
    _record(config, "ready", server=result["server"], browser_opened=opened)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Open the local Inago posting history."
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Check startup without opening a browser.",
    )
    args = parser.parse_args(argv)
    config = None
    try:
        runtime = PROJECT_ROOT / "rakuten-room-inago" / "runtime"
        config_path = runtime / "config" / "settings.json"
        config = load_human_api_config(config_path, data_root=runtime)
        result = launch_history(
            config, config_path=config_path, open_page=not args.no_browser
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        if config is not None:
            _record(config, "failed", error_class=type(exc).__name__)
        message = (
            str(exc) if isinstance(exc, HistoryLaunchError) else type(exc).__name__
        )
        print("投稿履歴を開けませんでした: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
