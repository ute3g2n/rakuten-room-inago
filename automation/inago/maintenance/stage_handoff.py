"""Content-bearing fallback results for maintenance workflow stages.

These records distinguish an agent's answer from controller-created recovery
content. They preserve the supplied identity (including a null source run),
describe what is known and unknown, and give the next role concrete work.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import traceback
from typing import Any, Mapping
from uuid import uuid4

from automation.inago.maintenance.code_revision import (
    current_code_revision,
    current_code_revision_reason,
)
from automation.inago.maintenance.failure_journal import append_failure_event


_IDENTITY_FIELDS = (
    "job_id",
    "incident_id",
    "source_launch_id",
    "source_run_id",
    "target_date",
)


def exception_details(error: BaseException) -> dict[str, object]:
    """Return safe exception classes and source frames, never message/locals."""

    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(type(current).__name__[:64])
        current = current.__cause__ or current.__context__
    frames = [
        {
            "file": Path(frame.filename).name[:128],
            "line": frame.lineno,
            "function": frame.name[:128],
        }
        for frame in traceback.extract_tb(error.__traceback__)[-24:]
    ]
    return {"exception_chain": chain, "stack_frames": frames}


def make_stage_handoff(
    *,
    stage: str,
    invocation_id: str,
    identity: Mapping[str, object],
    operation: str,
    operation_result: str,
    summary: str,
    facts: list[str],
    limitations: list[str],
    next_actions: list[str],
    input_refs: list[str] | None = None,
    output_refs: list[str] | None = None,
    error: BaseException | None = None,
    reason_code: str | None = None,
    correlation_id: str | None = None,
    artifact_origin: str = "controller_fallback",
    captured_at: str | None = None,
    error_capture_location: str = "maintenance.stage_handoff.make_stage_handoff",
) -> dict[str, Any]:
    """Build a substantive handoff with operation outcome separate from progress."""

    if operation_result not in {"succeeded", "failed", "unknown", "not_performed"}:
        raise ValueError("operation_result_invalid")
    if artifact_origin not in {"agent", "controller_fallback"}:
        raise ValueError("artifact_origin_invalid")
    result: dict[str, Any] = {
        "schema_version": "maintenance-stage-handoff-v1",
        "handoff_id": uuid4().hex,
        "invocation_id": invocation_id,
        "stage": stage,
        "identity": {field: identity.get(field) for field in _IDENTITY_FIELDS},
        "attempt_id": identity.get("attempt_id"),
        "operation": operation,
        "operation_result": operation_result,
        "stage_progress": "content_ready_handoff_pending",
        "artifact_origin": artifact_origin,
        "correlation_id": correlation_id or uuid4().hex,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "code_revision": current_code_revision(),
        "code_revision_reason": current_code_revision_reason() or None,
        "input_refs": list(input_refs or []),
        "output_refs": list(output_refs or []),
        "content_result": {
            "summary": summary,
            "facts": list(facts),
            "limitations": list(limitations),
            "next_actions": list(next_actions),
        },
        "errors": [],
        "unknowns": list(limitations),
    }
    if reason_code:
        result["reason_code"] = reason_code
    if error is not None:
        result["errors"].append(
            {
                "operation": operation,
                "reason_code": reason_code,
                "capture_location": error_capture_location,
                **exception_details(error),
            }
        )
    return result


def record_stage_failure(
    *,
    data_root: str | Path | None,
    identity: Mapping[str, object],
    stage: str,
    operation: str,
    error: BaseException,
    reason_code: str,
    correlation_id: str | None = None,
    input_refs: tuple[str, ...] = (),
    capture_location: str = "production_workflow.stage_boundary",
) -> Mapping[str, object]:
    """Write a safe correlated failure event; return an observation on loss."""

    if data_root is None:
        return {"status": "unavailable", "reason": "data_root_unknown"}
    try:
        return append_failure_event(
            data_root,
            phase="failed",
            operation=operation,
            correlation_id=correlation_id,
            job_id=_text(identity.get("job_id")),
            source_launch_id=_text(identity.get("source_launch_id")),
            source_run_id=_text(identity.get("source_run_id")),
            incident_id=_text(identity.get("incident_id")),
            stage=stage,
            attempt_id=_text(identity.get("attempt_id")),
            reason_code=reason_code,
            error=error,
            capture_location=capture_location,
            input_refs=input_refs,
        )
    except Exception as journal_error:  # the fallback sink must never mask work
        return {
            "status": "unavailable",
            "exception_class": type(journal_error).__name__,
            "capture_location": "maintenance.stage_handoff.record_stage_failure",
        }


def render_investigation_fallback(
    *, identity: Mapping[str, object], checked_refs: list[str],
    limits: list[str], next_actions: list[str], failure_summary: str,
) -> str:
    """Render a nine-part Japanese investigation that clearly states unknowns."""

    sections = [
        ("1. 今回の確認について", "自動調査の通常の回答を受け取れなかったため、確認できた範囲と不足を管理側で記録しました。原因を特定したとは扱いません。"),
        ("2. 対象", f"対象日は{identity.get('target_date') or '未確認'}です。元の実行番号は提供されておらず、値を補っていません。"),
        ("3. 確認した資料", _bullets(checked_refs, "確認できた資料参照はありません。")),
        ("4. 分かったこと", f"通常調査の実行結果: {failure_summary}。この記録は調査役の回答ではなく、管理側が作成した引継ぎです。"),
        ("5. 影響", "元の日次処理への影響と、業務が回復したかどうかは、この引継ぎだけでは確認できません。"),
        ("6. 原因の見立て", "根本原因は不明です。原因を示す証拠を確認できていないため、推測で断定しません。"),
        ("7. 調べ切れなかったこと", _bullets(limits, "追加で確認できない項目は記録されていません。")),
        ("8. 次に行うこと", _bullets(next_actions, "利用できる証拠を使って調査役を再起動します。")),
        ("9. この報告の限界", "ここに書かれたのは現在取得できた範囲だけです。過去に記録されなかった情報を復元したものではありません。"),
    ]
    return "# 障害調査の引継ぎ\n\n" + "\n\n".join(
        f"## {title}\n\n{body}" for title, body in sections
    ) + "\n"


def render_instrumentation_plan(*, limits: list[str], owner_paths: list[str]) -> str:
    """Create an actionable plan even when the incident cause remains unknown."""

    actions = [
        "障害原因の修正は、根拠がそろうまで実施しない。",
        "調査・AI呼出し・応答検証・成果物保存・DB登録・引継ぎの各境界で、操作名、相関ID、例外の種類と発生箇所、入力/出力参照を記録する。",
        "通常ログが失敗しても独立した記録先へ退避し、復旧後に同じ相関IDで取り込む。全保存先が同時に使えない期間はunknownとして追記する。",
        "各失敗時に内容ある調査/計画/実装不能説明を保存し、次担当を起動する。品質結果や過去のblockedを起動条件にしない。",
        "focused fault injection、製品コード独立review、正式適用と同一jobの新記録を順に確認する。",
    ]
    return "# 原因不明時の実行計画\n\n" + "\n".join(
        f"{index}. {action}" for index, action in enumerate(actions, 1)
    ) + "\n\n確認制限:\n" + _bullets(limits, "なし") + "\n\n対象範囲:\n" + _bullets(owner_paths, "未確定")


def render_no_change_explanation_html(*, what_was_checked: list[str], why_unchanged: list[str], next_actions: list[str]) -> str:
    """Fallback for the rare case where neither repair nor useful instrumentation fits."""

    parts = [
        "<!doctype html><html lang=\"ja\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>障害対応の状況</title><main>",
        "<h1>障害対応の状況</h1><p>確認した範囲では、安全に行える修正や記録追加を決められませんでした。障害が直ったという意味ではありません。</p>",
        _html_list("確認したこと", what_was_checked),
        _html_list("変更しなかった理由", why_unchanged),
        _html_list("次に行うこと", next_actions),
        "<p>原因と業務の回復は未確認です。新しい証拠が得られたら、同じ対応記録から続きを行います。</p></main></html>",
    ]
    return "\n".join(parts) + "\n"


def dumps_handoff(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bullets(values: list[str], empty: str) -> str:
    return "\n".join(f"- {value}" for value in values) if values else f"- {empty}"


def _html_list(title: str, values: list[str]) -> str:
    items = "".join(f"<li>{_escape(value)}</li>" for value in values) or "<li>情報なし</li>"
    return f"<section><h2>{_escape(title)}</h2><ul>{items}</ul></section>"


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
