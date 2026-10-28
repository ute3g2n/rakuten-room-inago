"""Diagnose source-reference handoffs without accepting or rejecting AI results."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from automation.inago.contracts import AIJobRequest


OMISSION_REASONS = (
    "none",
    "used_refs_only",
    "output_shortening",
    "context_limit",
    "source_unavailable",
    "other",
    "unknown",
)
_COPY_AGENTS = {"AG-COPY", "AG-COPY-REVIEW"}
_HANDOFF_TOOLS = {"spawn_agent", "followup_task", "send_input", "send_message"}
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_./:-]{1,160}\Z")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identifier(value: Any) -> str | None:
    return (
        value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else None
    )


def rollout_user_messages(
    events: Sequence[Mapping[str, Any]],
) -> tuple[str, ...] | None:
    """Observe child messages only after its own context, excluding fork history."""

    metas = [e.get("payload") for e in events if e.get("type") == "session_meta"]
    metas = [m for m in metas if isinstance(m, Mapping)]
    if not metas:
        return None
    thread_id = metas[0].get("id")
    if any(m.get("id") != thread_id for m in metas):
        boundary = next(
            (
                i
                for i, e in enumerate(events)
                if (
                    e.get("type") == "event_msg"
                    and isinstance(e.get("payload"), Mapping)
                    and e["payload"].get("type") == "thread_settings_applied"
                    and e["payload"].get("thread_id") == thread_id
                )
            ),
            None,
        )
        if boundary is None:
            return None
        events = events[boundary + 1 :]
    messages = []
    fallback = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event.get("type") == "response_item" and (
            payload.get("type") == "message" and payload.get("role") == "user"
        ):
            content = payload.get("content")
            if isinstance(content, list):
                text = "".join(
                    item["text"]
                    for item in content
                    if (isinstance(item, Mapping) and isinstance(item.get("text"), str))
                )
                if text:
                    messages.append(text)
        elif event.get("type") == "event_msg" and payload.get("type") == "user_message":
            if isinstance(payload.get("message"), str):
                fallback.append(payload["message"])
    return tuple(messages or fallback)


def _ref_difference(expected: Sequence[str], recorded: Any) -> dict[str, Any]:
    values = recorded if isinstance(recorded, list) else []
    known = set(expected)
    valid = [v for v in values if isinstance(v, str)]
    return {
        "expected_count": len(expected),
        "recorded_count": len(values),
        "format_valid": isinstance(recorded, list) and len(valid) == len(values),
        "recorded_allowed_refs": [v for v in valid if v in known],
        "missing_refs": sorted(known - set(valid)),
        # Unexpected strings can contain secrets; never copy them into diagnostics.
        "unexpected_ref_hashes": [_digest(v) for v in valid if v not in known],
    }


def _message_observation(
    request: AIJobRequest, messages: Sequence[str] | None
) -> dict[str, Any]:
    if messages is None:
        return {"status": "unobserved"}
    text = "\n".join(messages)

    def mentions(refs: Sequence[str]) -> dict[str, Any]:
        found = [
            ref
            for ref in refs
            if re.search(
                r"(?<![A-Za-z0-9._:-])" + re.escape(ref) + r"(?![A-Za-z0-9._:-])", text
            )
        ]
        return {
            "mentioned_count": len(found),
            "missing_refs": sorted(set(refs) - set(found)),
        }

    return {
        "status": "literal_ref_mentions" if messages else "no_user_message",
        "message_count": len(messages),
        "messages_sha256": _digest(list(messages)),
        "input_refs": mentions(request.input_refs),
        "evidence_refs": mentions(request.evidence_refs),
    }


def build_source_ref_audit(
    request: AIJobRequest,
    response: Mapping[str, Any],
    *,
    response_artifact_ref: str,
    root_thread_id: str | None,
    native_events: Sequence[Mapping[str, Any]],
    child_messages: Sequence[tuple[str, str, tuple[str, ...] | None]],
) -> dict[str, Any]:
    """Keep original IDs and observations; a stated reason is not a proven cause."""

    notes = {}
    payload = response.get("payload")
    details = payload.get("details") if isinstance(payload, Mapping) else None
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, str):
            continue
        try:
            note = json.loads(detail)
        except (ValueError, RecursionError):
            continue
        if not isinstance(note, Mapping) or note.get("kind") != "source_ref_omission":
            continue
        index = note.get("declaration_index")
        agent_id = note.get("agent_id")
        if (
            type(index) is int
            and isinstance(agent_id, str)
            and agent_id in _COPY_AGENTS
        ):
            notes[index, agent_id] = note
    declarations = []
    raw = response.get("delegations")
    for index, declaration in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(declaration, Mapping):
            continue
        agent_id = declaration.get("agent_id")
        if not isinstance(agent_id, str) or agent_id not in _COPY_AGENTS:
            continue
        note = notes.get((index, agent_id), {})
        reason = note.get("reason")
        declarations.append(
            {
                "declaration_index": index,
                "agent_id": declaration["agent_id"],
                "input_refs": _ref_difference(
                    request.input_refs, declaration.get("input_refs")
                ),
                "evidence_refs": _ref_difference(
                    request.evidence_refs, declaration.get("evidence_refs")
                ),
                "reported_omission_reason": (
                    reason if reason in OMISSION_REASONS else "not_reported"
                ),
                "reason_is_self_reported": True,
                "reason_detail_present": isinstance(note.get("detail"), str),
            }
        )
    handoffs = []
    for event in native_events:
        payload = event.get("payload")
        if event.get("type") != "response_item" or not isinstance(payload, Mapping):
            continue
        if payload.get("type") != "function_call":
            continue
        tool = str(payload.get("name", "")).rsplit(".", 1)[-1]
        if tool not in _HANDOFF_TOOLS:
            continue
        args = payload.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, RecursionError):
                continue
        if not isinstance(args, Mapping):
            continue
        message = args.get("message")
        handoffs.append(
            {
                "tool": tool,
                "call_id": _identifier(payload.get("call_id")),
                "agent_type": _identifier(args.get("agent_type")),
                "target": _identifier(args.get("target", args.get("id"))),
                "observation": _message_observation(
                    request, (message,) if isinstance(message, str) else None
                ),
            }
        )
    return {
        "schema_version": 1,
        "run_id": request.run_id,
        "job_id": request.job_id,
        "attempt": request.attempt,
        "response_artifact_ref": response_artifact_ref,
        "root_thread_id": _identifier(root_thread_id),
        "expected_input_refs": list(request.input_refs),
        "expected_evidence_refs": list(request.evidence_refs),
        "declarations": declarations,
        "native_handoffs": handoffs,
        "native_children": [
            {
                "agent_type": _identifier(agent_type),
                "thread_id": _identifier(thread_id),
                "observation": _message_observation(request, messages),
            }
            for agent_type, thread_id, messages in child_messages
        ],
        "observation_limit": "Literal ID mentions do not prove complete source delivery or reading. Reasons are AI self-reports.",
    }
