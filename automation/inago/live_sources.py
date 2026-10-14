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
import stat
import subprocess
import threading
from typing import Any
from urllib.parse import quote, urlsplit
import uuid

from automation.inago.room_adapter import RoomSearchRequest, _unavailable_response
from automation.inago.retention import retention_expiry
from automation.inago.trends_adapter import TrendsRequest
from automation.atomic_io import atomic_write_json


SCRIPT = Path(__file__).resolve().parent / "adapters" / "public_sources.mjs"
ACCESS_FAILURES = frozenset({"http_401", "http_403", "http_429", "access_denied"})
STOP_FAILURES = ACCESS_FAILURES | {"owned_cleanup_unconfirmed"}
SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
MAX_SOURCE_TIMEOUT_SECONDS = 17100


def _expiration_is_past(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return True
    return parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def _observation_is_expired(record: dict[str, Any]) -> bool:
    expires_at = record.get("retention_expires_at")
    if expires_at is None:
        recorded_at = record.get("recorded_at")
        try:
            expires_at = retention_expiry("raw", recorded_at).isoformat()
        except (TypeError, ValueError):
            return True
    return _expiration_is_past(expires_at)


@dataclass(frozen=True, slots=True)
class PublicSourceConfig:
    chrome_executable: Path
    evidence_root: Path
    environment: str = "public_ui"
    timeout_seconds: float = MAX_SOURCE_TIMEOUT_SECONDS
    fixture_url: str | None = None
    node_executable: str = "node"
    headless: bool = False
    user_data_dir: Path | None = None
    fixture_product_delay_ms: int = 0
    fixture_browser_startup_delay_ms: int = 0
    fixture_room_scroll_delay_ms: int = 0
    fixture_expire_deadline_before_room_restore: bool = False


def _trends_failure(request: TrendsRequest, code: str) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "snapshot_id": request.request_id,
        "run_id": request.run_id,
        "settings_version": request.settings_version,
        "source": "google_trends_public_ui",
        "parent_word": request.parent_word,
        "current_parent": None,
        "refresh_complete": False,
        "language": request.language,
        "geo": request.geo,
        "search_type": request.search_type,
        "range": request.range,
        "list_type": request.list_type,
        "ui_period_label": None,
        "status": "unavailable",
        "fetched_at": None,
        "error_code": code,
        "words": [],
    }


class PublicBrowserSource:
    """Use an owned persistent public profile or an isolated fixture context."""

    fixture_only = False

    def __init__(self, config: PublicSourceConfig):
        if config.environment not in {"fixture", "public_ui"}:
            raise ValueError("unknown browser source environment")
        if not isinstance(config.headless, bool):
            raise ValueError("headless must be a boolean")
        if (
            isinstance(config.fixture_product_delay_ms, bool)
            or not isinstance(config.fixture_product_delay_ms, int)
            or config.fixture_product_delay_ms < 0
            or (
                config.environment != "fixture" and config.fixture_product_delay_ms != 0
            )
        ):
            raise ValueError(
                "fixture product delay must be a non-negative fixture value"
            )
        if (
            isinstance(config.fixture_browser_startup_delay_ms, bool)
            or not isinstance(config.fixture_browser_startup_delay_ms, int)
            or config.fixture_browser_startup_delay_ms < 0
            or (
                config.environment != "fixture"
                and config.fixture_browser_startup_delay_ms != 0
            )
        ):
            raise ValueError(
                "fixture browser startup delay must be a non-negative fixture value"
            )
        if (
            isinstance(config.fixture_room_scroll_delay_ms, bool)
            or not isinstance(config.fixture_room_scroll_delay_ms, int)
            or config.fixture_room_scroll_delay_ms < 0
            or (
                config.environment != "fixture"
                and config.fixture_room_scroll_delay_ms != 0
            )
        ):
            raise ValueError(
                "fixture ROOM scroll delay must be a non-negative fixture value"
            )
        if not isinstance(config.fixture_expire_deadline_before_room_restore, bool) or (
            config.environment != "fixture"
            and config.fixture_expire_deadline_before_room_restore
        ):
            raise ValueError("deadline expiry injection is fixture-only")
        if not config.chrome_executable.is_file():
            raise ValueError("explicit Chrome executable does not exist")
        if (
            not isinstance(config.timeout_seconds, (int, float))
            or isinstance(config.timeout_seconds, bool)
            or not 1 <= config.timeout_seconds <= MAX_SOURCE_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"source timeout must be between 1 and {MAX_SOURCE_TIMEOUT_SECONDS} seconds"
            )
        if config.environment == "fixture":
            if config.user_data_dir is not None:
                raise ValueError("fixture mode cannot use a persistent user profile")
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
        self._user_data_dir = (
            None
            if config.environment == "fixture"
            else self._resolve_owned_profile(config.user_data_dir)
        )
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

    @staticmethod
    def _resolve_owned_profile(configured: Path | None) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise ValueError(
                "public UI requires LOCALAPPDATA to resolve the owned profile"
            )
        owned_profile = Path(local_app_data) / "InagoPlaywrightProfile"
        profile_env = os.environ.get("INAGO_PLAYWRIGHT_USER_DATA_DIR")
        profile = configured or (Path(profile_env) if profile_env else owned_profile)
        if PublicBrowserSource._has_reparse_point(profile) or (
            PublicBrowserSource._has_reparse_point(owned_profile)
        ):
            raise ValueError(
                "the owned Playwright profile cannot be a symlink or junction"
            )
        resolved = profile.expanduser().resolve()
        resolved_owned = owned_profile.resolve()
        if resolved != resolved_owned:
            raise ValueError(
                "public UI may use only LOCALAPPDATA\\InagoPlaywrightProfile"
            )
        if not resolved.is_dir():
            raise ValueError("owned Playwright profile directory is unavailable")
        return resolved

    @staticmethod
    def _has_reparse_point(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            return False
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return path.is_symlink() or bool(
            getattr(metadata, "st_file_attributes", 0) & reparse_flag
        )

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
            request = record.get("request")
            request = request if isinstance(request, dict) else {}
            run_id = record.get("run_id", request.get("run_id"))
            kind = record.get("kind")
            request_id = request.get("request_id")
            if record.get("error_code") in STOP_FAILURES and not (
                kind == "trends"
                and record.get("error_code") == "http_429"
                and isinstance(request_id, str)
                and re.search(r"-a[1-5]$", request_id)
            ):
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
            failure = _trends_failure(request, response["error_code"])
            if isinstance(response.get("retry_after_seconds"), int):
                failure["retry_after_seconds"] = response["retry_after_seconds"]
            return failure
        return response["response"]

    def load_saved_response(self, request_id: str) -> dict[str, Any] | None:
        """Recover this exact request's private observation after a DB interruption."""

        if not SAFE_REF.fullmatch(request_id):
            raise ValueError("saved Trends request reference is unsafe")
        found = None
        for path in self._evidence.glob(
            f"trends-{quote(request_id, safe='')}-observation-*.json"
        ):
            if path.is_symlink() or not path.resolve().is_relative_to(self._evidence):
                raise ValueError("saved Trends response escapes evidence root")
            record = json.loads(path.read_text(encoding="utf-8"))
            request = record.get("request") if isinstance(record, dict) else None
            if (
                not isinstance(record, dict)
                or record.get("kind") != "trends"
                or not isinstance(request, dict)
                or request.get("request_id") != request_id
            ):
                raise ValueError("saved Trends observation identity differs")
            if _observation_is_expired(record):
                raise ValueError("saved Trends observation expired")
            saved_request = TrendsRequest(
                request_id=request_id,
                run_id=request.get("run_id"),
                settings_version=request.get("settings_version"),
                parent_word=request.get("parent_word"),
                language=request.get("language", "ja"),
                geo=request.get("geo", "JP"),
                search_type=request.get("search_type", "web"),
                range=request.get("range", "3m"),
                list_type=request.get("list_type", "Top"),
                deadline_at=request.get("deadline_at"),
            )
            response = record.get("response")
            if isinstance(response, dict):
                current = dict(response)
            elif isinstance(record.get("error_code"), str):
                current = _trends_failure(saved_request, record["error_code"])
                retry_after = record.get("retry_after_seconds")
                if (
                    isinstance(retry_after, int)
                    and not isinstance(retry_after, bool)
                    and 0 <= retry_after <= 86400
                ):
                    current["retry_after_seconds"] = retry_after
            else:
                continue
            if found is not None and found != current:
                raise ValueError("saved Trends observations conflict for one request")
            found = current
        return found

    def product_fact(self, evidence_ref: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._facts.get(evidence_ref)
            if value is not None:
                try:
                    expires_at = retention_expiry("raw", value.get("observed_at"))
                except (TypeError, ValueError):
                    return None
                if expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                    return None
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
            if _observation_is_expired(record):
                continue
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
        request_timeout = self._request_timeout_seconds(request)
        # The Node adapter rejects timeouts below one second as an invalid
        # request. At that point no useful browser work can start before the
        # daily cutoff, so report an ordinary timeout without launching it.
        if request_timeout < 1:
            return {"error_code": "source_timeout"}
        config = asdict(self.config)
        config["timeout_seconds"] = min(config["timeout_seconds"], request_timeout)
        config["chrome_executable"] = str(self.config.chrome_executable.resolve())
        config["user_data_dir"] = (
            str(self._user_data_dir) if self._user_data_dir is not None else None
        )
        config["fixture_product_delay_ms"] = self.config.fixture_product_delay_ms
        config["fixture_browser_startup_delay_ms"] = (
            self.config.fixture_browser_startup_delay_ms
        )
        config["fixture_room_scroll_delay_ms"] = (
            self.config.fixture_room_scroll_delay_ms
        )
        config["fixture_expire_deadline_before_room_restore"] = (
            self.config.fixture_expire_deadline_before_room_restore
        )
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
                if self._request_timeout_seconds(request) < 1:
                    return {"error_code": "source_timeout"}
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
                payload,
                timeout=config["timeout_seconds"]
                + (2 if getattr(request, "deadline_at", None) else 5),
            )
            result = json.loads(output)
            error_shapes = {
                frozenset({"error_code"}),
                frozenset({"error_code", "diagnostics"}),
                frozenset({"error_code", "retry_after_seconds"}),
                frozenset({"error_code", "diagnostics", "retry_after_seconds"}),
            }
            if not isinstance(result, dict) or not (
                frozenset(result) in error_shapes
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
            elif "retry_after_seconds" in result and (
                not isinstance(result["retry_after_seconds"], int)
                or isinstance(result["retry_after_seconds"], bool)
                or not 0 <= result["retry_after_seconds"] <= 86400
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
            if request.run_id in self._cancelled_runs and not self._should_stop_run(
                kind, request, result.get("error_code")
            ):
                result = {"error_code": "source_cancelled"}
            if self._should_stop_run(kind, request, result.get("error_code")):
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
        if self._should_stop_run(kind, request, result.get("error_code")):
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

    def _request_timeout_seconds(self, request) -> float:
        """Cap a browser process at the request's daily cutoff when supplied."""

        request_deadline = getattr(request, "deadline_at", None)
        if request_deadline is None:
            return float(self.config.timeout_seconds)
        try:
            deadline = datetime.fromisoformat(request_deadline)
        except (TypeError, ValueError):
            return 0.0
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            return 0.0
        now = datetime.now(deadline.tzinfo)
        return max(0.0, (deadline - now).total_seconds())

    @staticmethod
    def _should_stop_run(kind: str, request, error_code: object) -> bool:
        if error_code not in STOP_FAILURES:
            return False
        # New durable Trends retries have a unique, persisted -a1..-a5 request
        # identity. A 429 from one such request is retryable under that same
        # public UI contract; legacy 429 records still stop old runs because
        # their retry count cannot be reconstructed safely.
        if (
            kind == "trends"
            and error_code == "http_429"
            and re.search(r"-a[1-5]$", getattr(request, "request_id", ""))
        ):
            return False
        return True

    @staticmethod
    def _metadata_events(raw: str) -> list[dict[str, Any]]:
        events = []
        for line in raw.splitlines()[:5000]:
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
            if isinstance(item, dict) and item.get("event") == "public_source_failure":
                stage = item.get("stage")
                error_code = item.get("error_code")
                error_class = item.get("error_class")
                error_kind = item.get("error_kind")
                if (
                    not isinstance(stage, str)
                    or not SAFE_REF.fullmatch(stage)
                    or not isinstance(error_code, str)
                    or not SAFE_REF.fullmatch(error_code)
                    or error_class
                    not in {
                        "SourceFailure",
                        "Error",
                        "TimeoutError",
                        "TypeError",
                        "ReferenceError",
                        "SyntaxError",
                        "RangeError",
                        "URIError",
                        "EvalError",
                        "AggregateError",
                        "TargetClosedError",
                        "BrowserClosedError",
                        "ProtocolError",
                        "other",
                    }
                    or error_kind
                    not in {
                        "network_error",
                        "timeout",
                        "target_closed",
                        "page_closed",
                        "navigation_interrupted",
                        "protocol_error",
                        "browser_launch_error",
                        "other_error",
                    }
                ):
                    continue
                event = {
                    "event": "public_source_failure",
                    "stage": stage,
                    "error_code": error_code,
                    "error_class": error_class,
                    "error_kind": error_kind,
                }
                network_code = item.get("network_code")
                if isinstance(network_code, str) and re.fullmatch(
                    r"ERR_[A-Z0-9_]{1,64}", network_code
                ):
                    event["network_code"] = network_code
                row_index = item.get("row_index")
                if (
                    isinstance(row_index, int)
                    and not isinstance(row_index, bool)
                    and 1 <= row_index <= 50
                ):
                    event["row_index"] = row_index
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
            for key in (
                "loaded_posts",
                "accepted_posts",
                "row_index",
                "body_children",
                "search_candidate_count",
                "visible_input_count",
                "label_locator_match_count",
                "search_button_count",
                "explore_button_count",
                "period_option_count",
                "retry_after_seconds",
            ):
                if (
                    isinstance(item.get(key), int)
                    and not isinstance(item[key], bool)
                    and item[key] >= 0
                    and (key != "row_index" or 1 <= item[key] <= 50)
                    and (
                        key
                        not in {
                            "search_candidate_count",
                            "visible_input_count",
                            "label_locator_match_count",
                            "search_button_count",
                            "explore_button_count",
                            "period_option_count",
                        }
                        or item[key] <= 100
                    )
                ):
                    event[key] = item[key]
            document_ready_state = item.get("document_ready_state")
            if document_ready_state in {"loading", "interactive", "complete"}:
                event["document_ready_state"] = document_ready_state
            if isinstance(item.get("body_text_nonempty"), bool):
                event["body_text_nonempty"] = item["body_text_nonempty"]
            if isinstance(item.get("retry_after_present"), bool):
                event["retry_after_present"] = item["retry_after_present"]
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
