"""Owned Python -> Node public Playwright observations, with bounded cancellation.

This does not create a daily scheduler, activate settings or connect a production
DB. The caller composes these ports explicitly after acceptance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
from typing import Any
from urllib.parse import quote, urlsplit
import uuid

from automation.inago.room_adapter import RoomSearchRequest, _unavailable_response
from automation.inago.trends_adapter import TrendsRequest
from automation.atomic_io import atomic_write_json


SCRIPT = Path(__file__).resolve().parent / "adapters" / "public_sources.mjs"
ACCESS_FAILURES = frozenset({"http_401", "http_403", "http_429", "access_denied"})
STOP_FAILURES = ACCESS_FAILURES | {"owned_cleanup_unconfirmed"}
SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class PublicSourceConfig:
    chrome_executable: Path
    evidence_root: Path
    environment: str = "public_ui"
    timeout_seconds: float = 300
    fixture_url: str | None = None
    node_executable: str = "node"


def _trends_failure(request: TrendsRequest, code: str) -> dict[str, Any]:
    return {
        **asdict(request),
        "snapshot_id": request.request_id,
        "source": "google_trends_public_ui",
        "current_parent": None,
        "refresh_complete": False,
        "ui_period_label": None,
        "status": "unavailable",
        "fetched_at": None,
        "error_code": code,
        "words": [],
    }


class PublicBrowserSource:
    """One fresh, ordinary browser per request. No fallback after access denial."""

    fixture_only = False

    def __init__(self, config: PublicSourceConfig):
        if config.environment not in {"fixture", "public_ui"}:
            raise ValueError("unknown browser source environment")
        if not config.chrome_executable.is_file():
            raise ValueError("explicit Chrome executable does not exist")
        if (
            not isinstance(config.timeout_seconds, (int, float))
            or isinstance(config.timeout_seconds, bool)
            or not 1 <= config.timeout_seconds <= 900
        ):
            raise ValueError("source timeout must be between 1 and 900 seconds")
        if config.environment == "fixture":
            url = urlsplit(config.fixture_url or "")
            if (
                url.scheme != "http"
                or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.username
                or url.password
            ):
                raise ValueError("fixture endpoint must be plain loopback HTTP")
        elif config.fixture_url is not None:
            raise ValueError("public UI does not accept a fixture endpoint")
        if shutil.which(config.node_executable) is None:
            raise ValueError("Node executable unavailable")
        self.config = config
        self.fixture_only = config.environment == "fixture"
        self._lock = threading.Lock()
        self._serial = threading.Lock()
        self._active: dict[subprocess.Popen, str] = {}
        self._cancelled_runs: set[str] = set()
        self._stopped_runs: set[str] = set()
        self._uncertain_processes: set[subprocess.Popen] = set()
        self._facts: dict[str, dict[str, Any]] = {}
        self._evidence = config.evidence_root.resolve()
        self._evidence.mkdir(parents=True, exist_ok=True)
        self._recover_access_stops()

    def _recover_access_stops(self) -> None:
        # Legacy captures from the first probe also restore the denied run.
        records = (
            list(self._evidence.glob("access-stop-*.json"))
            + list(self._evidence.glob("observation-*.json"))
            + list(self._evidence.glob("room-*-observation-*.json"))
            + list(self._evidence.glob("trends-*-observation-*.json"))
        )
        for path in records:
            if path.is_symlink() or not path.resolve().is_relative_to(self._evidence):
                raise ValueError("source stop record escapes evidence root")
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("source stop record is invalid")
            run_id = record.get("run_id", record.get("request", {}).get("run_id"))
            if record.get("error_code") in STOP_FAILURES:
                if not isinstance(run_id, str) or not SAFE_REF.fullmatch(run_id):
                    raise ValueError("source stop run identity is invalid")
                self._stopped_runs.add(run_id)

    @property
    def active_process_count(self) -> int:
        with self._lock:
            return len(self._active)

    def search(self, request: RoomSearchRequest) -> dict[str, Any]:
        response = self._invoke("room", request)
        if "error_code" in response and "response" not in response:
            failure = _unavailable_response(request, response["error_code"])
            failure.update(
                query_kind=request.query_kind,
                keyword_snapshot_ref=request.keyword_snapshot_ref,
                selected_word_ref=request.selected_word_ref,
                loaded_posts=0,
                condition_evidence_ref=None,
            )
            return failure
        return response["response"]

    def fetch(self, request: TrendsRequest) -> dict[str, Any]:
        response = self._invoke("trends", request)
        if "response" not in response:
            return _trends_failure(request, response["error_code"])
        return response["response"]

    def product_fact(self, evidence_ref: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._facts.get(evidence_ref)
            if value is not None:
                return dict(value)
        attempt, marker, index = evidence_ref.rpartition(":row:")
        if not marker or not SAFE_REF.fullmatch(attempt) or not index.isdigit():
            return None
        found = None
        for path in self._evidence.glob(
            f"room-{quote(attempt, safe='')}-observation-*.json"
        ):
            if path.is_symlink() or not path.resolve().is_relative_to(self._evidence):
                raise ValueError("captured fact file escapes evidence root")
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                record.get("kind") != "room"
                or record.get("request", {}).get("attempt_id") != attempt
            ):
                raise ValueError("captured fact attempt differs")
            value = record.get("product_facts", {}).get(evidence_ref)
            if value is None:
                continue
            if not isinstance(value, dict) or value.get("evidence_ref") != evidence_ref:
                raise ValueError("captured fact reference differs")
            if found is not None and found != value:
                raise ValueError("captured fact conflict")
            found = value
        if found is not None:
            with self._lock:
                self._facts[evidence_ref] = found
            return dict(found)
        return None

    def _invoke(self, kind: str, request) -> dict[str, Any]:
        # A denial cannot race with another request starting on this connector.
        with self._serial:
            return self._invoke_serial(kind, request)

    def _invoke_serial(self, kind: str, request) -> dict[str, Any]:
        with self._lock:
            if request.run_id in self._stopped_runs:
                return {"error_code": "source_access_stopped"}
            if request.run_id in self._cancelled_runs:
                return {"error_code": "source_cancelled"}
        config = asdict(self.config)
        config["chrome_executable"] = str(self.config.chrome_executable.resolve())
        config.pop("evidence_root")
        config.pop("node_executable")
        payload = json.dumps(
            {"kind": kind, "request": asdict(request), "config": config},
            ensure_ascii=False,
        )
        process = None
        node_stderr = ""
        result: dict[str, Any]
        try:
            flags = (
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            with self._lock:
                if request.run_id in self._cancelled_runs:
                    return {"error_code": "source_cancelled"}
                process = subprocess.Popen(
                    [self.config.node_executable, str(SCRIPT)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    creationflags=flags,
                    start_new_session=not hasattr(subprocess, "CREATE_NO_WINDOW"),
                )
                self._active[process] = request.run_id
            output, node_stderr = process.communicate(
                payload, timeout=self.config.timeout_seconds + 5
            )
            result = json.loads(output)
            if not isinstance(result, dict) or not (
                set(result) in ({"error_code"}, {"error_code", "diagnostics"})
                or set(result) == {"response", "product_facts", "diagnostics"}
            ):
                result = {"error_code": "source_response_invalid"}
            elif "response" not in result and (
                not isinstance(result["error_code"], str)
                or not SAFE_REF.fullmatch(result["error_code"])
            ):
                result = {"error_code": "source_response_invalid"}
            elif "response" in result and (
                process.returncode != 0
                or not isinstance(result["response"], dict)
                or not isinstance(result["product_facts"], dict)
            ):
                result = {"error_code": "source_response_invalid"}
        except subprocess.TimeoutExpired as exc:
            if isinstance(exc.stderr, bytes):
                node_stderr = exc.stderr.decode("utf-8", errors="replace")
            elif isinstance(exc.stderr, str):
                node_stderr = exc.stderr
            result = {"error_code": "source_timeout"}
        except (OSError, UnicodeError, json.JSONDecodeError):
            result = {"error_code": "source_process_error"}
        finally:
            if process is not None:
                confirmed = process not in self._uncertain_processes
                if process.poll() is None:
                    confirmed = self._terminate_tree(process)
                if confirmed:
                    try:
                        process.communicate(timeout=2)
                    except (subprocess.TimeoutExpired, OSError):
                        confirmed = False
                with self._lock:
                    if confirmed:
                        self._active.pop(process, None)
                    else:
                        self._uncertain_processes.add(process)
                        self._cancelled_runs.add(request.run_id)
                        result = {"error_code": "owned_cleanup_unconfirmed"}
        with self._lock:
            if (
                request.run_id in self._cancelled_runs
                and result.get("error_code") not in STOP_FAILURES
            ):
                result = {"error_code": "source_cancelled"}
            if result.get("error_code") in STOP_FAILURES:
                self._stopped_runs.add(request.run_id)
            if "response" in result:
                self._facts.update(result["product_facts"])
        # Private observations; no cookie/token/account/profile or other posts' text.
        record = {
            "kind": kind,
            "request": asdict(request),
            "environment": self.config.environment,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "retention_expires_at": (
                datetime.now(timezone.utc) + timedelta(days=90)
            ).isoformat(),
            **result,
            "node_events": self._metadata_events(node_stderr),
            "process_returncode": process.returncode if process is not None else None,
        }
        identity = request.attempt_id if kind == "room" else request.request_id
        if not isinstance(identity, str) or not SAFE_REF.fullmatch(identity):
            raise ValueError("observation identity is unsafe")
        path = (
            self._evidence
            / f"{kind}-{quote(identity, safe='')}-observation-{uuid.uuid4()}.json"
        )
        if result.get("error_code") in STOP_FAILURES:
            stop = self._evidence / f"access-stop-{quote(request.run_id, safe='')}.json"
            atomic_write_json(
                stop,
                {
                    "run_id": request.run_id,
                    "kind": kind,
                    "error_code": result["error_code"],
                    "recorded_at": record["recorded_at"],
                },
            )
        # The stop is durable before saving ancillary observation details.
        atomic_write_json(path, record)
        return result

    @staticmethod
    def _metadata_events(raw: str) -> list[dict[str, Any]]:
        events = []
        for line in raw.splitlines()[:1000]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(item, dict)
                and item.get("event") == "public_source_diagnostic"
            ):
                signal = item.get("signal")
                if signal == "summary":
                    event = {"event": "public_source_diagnostic", "signal": "summary"}
                    valid = True
                    for key in (
                        "page_count",
                        "console_error_count",
                        "page_error_count",
                        "failed_request_count",
                        "popup_navigation_count",
                        "dropped_groups",
                    ):
                        value = item.get(key)
                        if (
                            not isinstance(value, int)
                            or isinstance(value, bool)
                            or value < 0
                        ):
                            valid = False
                            break
                        event[key] = value
                    if valid:
                        events.append(event)
                    continue
                if signal not in {
                    "console_error",
                    "page_error",
                    "failed_request",
                    "popup_navigation",
                    "navigation_request",
                    "navigation_response",
                    "navigation_finished",
                }:
                    continue
                stage = item.get("stage")
                row_index = item.get("row_index")
                page_role = item.get("page_role")
                count = item.get("count")
                if (
                    not isinstance(stage, str)
                    or not SAFE_REF.fullmatch(stage)
                    or row_index is not None
                    and (
                        not isinstance(row_index, int)
                        or isinstance(row_index, bool)
                        or not 1 <= row_index <= 50
                    )
                    or page_role not in {"main", "popup"}
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 1
                ):
                    continue
                event = {
                    "event": "public_source_diagnostic",
                    "signal": signal,
                    "stage": stage,
                    "page_role": page_role,
                    "count": count,
                }
                if row_index is not None:
                    event["row_index"] = row_index
                first_elapsed_ms = item.get("first_elapsed_ms")
                if (
                    isinstance(first_elapsed_ms, int)
                    and not isinstance(first_elapsed_ms, bool)
                    and first_elapsed_ms >= 0
                ):
                    event["first_elapsed_ms"] = first_elapsed_ms
                if signal == "page_error":
                    error_class = item.get("error_class")
                    if error_class not in {
                        "Error",
                        "TypeError",
                        "ReferenceError",
                        "SyntaxError",
                        "RangeError",
                        "URIError",
                        "EvalError",
                        "AggregateError",
                        "SecurityError",
                        "NotAllowedError",
                        "other",
                    }:
                        continue
                    event["error_class"] = error_class
                if signal in {
                    "failed_request",
                    "navigation_request",
                    "navigation_response",
                    "navigation_finished",
                }:
                    host_class = item.get("host_class")
                    resource_type = item.get("resource_type")
                    if host_class not in {
                        "room",
                        "product",
                        "rakuten_other",
                        "trends",
                        "other",
                    } or resource_type not in {
                        "document",
                        "stylesheet",
                        "image",
                        "media",
                        "font",
                        "script",
                        "texttrack",
                        "xhr",
                        "fetch",
                        "eventsource",
                        "websocket",
                        "manifest",
                        "other",
                    }:
                        continue
                    event.update(
                        host_class=host_class,
                        resource_type=resource_type,
                    )
                    if signal == "failed_request":
                        failure_class = item.get("failure_class")
                        if failure_class not in {
                            "aborted",
                            "blocked",
                            "dns",
                            "connection_reset",
                            "connection_closed",
                            "timed_out",
                            "connection_refused",
                            "other",
                        }:
                            continue
                        event["failure_class"] = failure_class
                    if signal == "navigation_response":
                        status = item.get("status")
                        if (
                            not isinstance(status, int)
                            or isinstance(status, bool)
                            or not 100 <= status <= 599
                        ):
                            continue
                        event["status"] = status
                if signal == "popup_navigation":
                    url_kind = item.get("url_kind")
                    if url_kind not in {
                        "blank",
                        "empty",
                        "rakuten_item",
                        "rakuten_other",
                        "other_https",
                        "other_scheme",
                        "invalid",
                    }:
                        continue
                    event["url_kind"] = url_kind
                events.append(event)
                continue
            if (
                not isinstance(item, dict)
                or item.get("event") != "public_source_stage"
                or not isinstance(item.get("stage"), str)
                or not SAFE_REF.fullmatch(item["stage"])
            ):
                continue
            event = {"event": "public_source_stage", "stage": item["stage"]}
            for key in ("loaded_posts", "accepted_posts", "row_index"):
                if (
                    isinstance(item.get(key), int)
                    and not isinstance(item[key], bool)
                    and item[key] >= 0
                ):
                    event[key] = item[key]
            phase = item.get("phase")
            if phase in {
                "card_link",
                "preview_click",
                "detail_button_ready",
                "detail_button_click",
                "popup_wait",
                "popup_initial_url",
                "product_navigation",
                "product_url_parse",
            }:
                event["phase"] = phase
            popup_url_kind = item.get("popup_url_kind")
            if popup_url_kind in {
                "blank",
                "empty",
                "rakuten_item",
                "rakuten_other",
                "other_https",
                "other_scheme",
                "invalid",
            }:
                event["popup_url_kind"] = popup_url_kind
            if (
                isinstance(item.get("popup_url_length"), int)
                and not isinstance(item["popup_url_length"], bool)
                and item["popup_url_length"] >= 0
            ):
                event["popup_url_length"] = item["popup_url_length"]
            element_tag = item.get("element_tag")
            if element_tag in {"a", "button", "div", "span", "other"}:
                event["element_tag"] = element_tag
            if isinstance(item.get("has_href"), bool):
                event["has_href"] = item["has_href"]
            href_kind = item.get("href_kind")
            if href_kind in {
                "none",
                "empty",
                "blank",
                "rakuten_item",
                "rakuten_other",
                "other_https",
                "other_scheme",
                "invalid",
            }:
                event["href_kind"] = href_kind
            link_target = item.get("link_target")
            if link_target in {"none", "_blank", "_self", "other"}:
                event["link_target"] = link_target
            link_scope = item.get("link_scope")
            if link_scope in {"none", "self", "ancestor", "descendant", "other"}:
                event["link_scope"] = link_scope
            if (
                isinstance(item.get("elapsed_ms"), int)
                and not isinstance(item["elapsed_ms"], bool)
                and item["elapsed_ms"] >= 0
            ):
                event["elapsed_ms"] = item["elapsed_ms"]
            events.append(event)
        return events

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> bool:
        if process.poll() is not None:
            return True
        try:
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                # Only this owned process tree. No image-name/global Chrome kill.
                result = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5,
                    check=False,
                )
                if result.returncode != 0:
                    return False
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            return False
        return True

    def cancel_owned_jobs(self, run_id: str) -> bool:
        with self._lock:
            self._cancelled_runs.add(run_id)
            processes = [
                p for p, owned_run in self._active.items() if owned_run == run_id
            ]
        confirmed = True
        for process in processes:
            if process in self._uncertain_processes or not self._terminate_tree(
                process
            ):
                confirmed = False
                with self._lock:
                    self._uncertain_processes.add(process)
        return confirmed
