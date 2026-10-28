"""Source-reference omissions are observable without imposing an equality gate."""

import json

import pytest

from automation.inago import ai_runner
from automation.inago.delegation_audit import (
    build_source_ref_audit,
    rollout_user_messages,
)
from tests_inago.test_step08 import (
    _base_request,
    _delegation,
    _root_response,
    _runner,
    _spawn_event,
)


def _partial_response(request):
    payload = {
        "offer_key": "shop-A:item-1",
        "draft_version": 1,
        "status": "ready",
        "body": "Synthetic grounded description.",
        "pattern_id": "P01",
        "evidence_refs": ["EVIDENCE-ROOM-1"],
        "used_input_refs": ["INPUT-OFFERS-1"],
        "selection_reason": "Synthetic fixture rationale.",
        "omitted_unknowns": [],
    }
    author = _delegation("AG-COPY", "T02", "rr-inago-copywriter", payload)
    response = _root_response(request, delegations=[author])
    response["payload"]["details"] = [
        json.dumps(
            {
                "kind": "source_ref_omission",
                "declaration_index": 0,
                "agent_id": "AG-COPY",
                "reason": "used_refs_only",
                "detail": "PRIVATE-EXPLANATION-SENTINEL",
            }
        )
    ]
    return response


def _request():
    return _base_request(
        input_refs=("INPUT-OFFERS-1", "html:source2"),
        evidence_refs=("EVIDENCE-ROOM-1", "html:source2"),
    )


@pytest.mark.parametrize(
    "reported_reason", ["used_refs_only", None, ["unexpected diagnostic shape"]]
)
def test_partial_refs_return_success_and_audit_without_new_diagnostic_gate(
    tmp_path, reported_reason
):
    request = _request()
    response = _partial_response(request)
    if reported_reason is None:
        response["payload"]["details"] = []
    else:
        note = json.loads(response["payload"]["details"][0])
        note["reason"] = reported_reason
        response["payload"]["details"] = [json.dumps(note)]
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode(),
        events=[_spawn_event("rr-inago-copywriter")],
    )
    result = runner.run(request)
    assert result.result_status == "success" and result.adoption_status == "pending"
    assert result.subjobs[0].input_refs == ("INPUT-OFFERS-1",)
    path = (
        runner.data_root
        / "artifacts"
        / "daily"
        / f"{request.job_id}-a1-source-refs.json"
    )
    audit = json.loads(path.read_text(encoding="utf-8"))
    record = audit["declarations"][0]
    assert record["input_refs"]["missing_refs"] == ["html:source2"]
    assert record["evidence_refs"]["missing_refs"] == ["html:source2"]
    assert record["reported_omission_reason"] == (
        "used_refs_only" if reported_reason == "used_refs_only" else "not_reported"
    )
    assert audit["root_thread_id"] is None and audit["native_children"] == []
    assert "PRIVATE-EXPLANATION-SENTINEL" not in path.read_text(encoding="utf-8")
    trace = runner._trace_path(request).read_text(encoding="utf-8")
    assert '"source_ref_mismatch_count":1' in trace
    assert "PRIVATE-EXPLANATION-SENTINEL" not in trace
    assert "Synthetic grounded description" not in trace


@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError])
def test_audit_write_failure_does_not_discard_specialist_result(
    tmp_path, monkeypatch, error_type
):
    request = _request()
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(_partial_response(request)).encode(),
        events=[_spawn_event("rr-inago-copywriter")],
    )

    def fail(**kwargs):
        raise error_type("PRIVATE-ERROR-SENTINEL")

    monkeypatch.setattr(ai_runner, "write_temp_response", fail)
    result = runner.run(request)
    assert result.result_status == "success"
    trace = runner._trace_path(request).read_text(encoding="utf-8")
    assert (
        "delegation_source_ref_audit_failed" in trace and error_type.__name__ in trace
    )
    assert "PRIVATE-ERROR-SENTINEL" not in trace


def test_unreadable_diagnostic_json_does_not_discard_result(tmp_path):
    request = _request()
    response = _partial_response(request)
    response["payload"]["details"] = ["[" * 10000 + "0" + "]" * 10000]
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode(),
        events=[_spawn_event("rr-inago-copywriter")],
    )
    result = runner.run(request)
    assert result.result_status == "success"
    path = (
        runner.data_root / "artifacts/daily" / f"{request.job_id}-a1-source-refs.json"
    )
    audit = json.loads(path.read_text(encoding="utf-8"))
    assert audit["declarations"][0]["reported_omission_reason"] == "not_reported"


def test_native_message_observations_show_where_ids_disappear_without_logging_text():
    request = _request()
    event = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "spawn_agent",
            "call_id": "call-copy",
            "arguments": json.dumps(
                {
                    "agent_type": "rr-inago-copywriter",
                    "message": "INPUT-OFFERS-1 EVIDENCE-ROOM-1 html:source2 PRIVATE-SOURCE-SENTINEL",
                }
            ),
        },
    }
    audit = build_source_ref_audit(
        request,
        _partial_response(request),
        response_artifact_ref="raw-response",
        root_thread_id="root-thread",
        native_events=[event],
        child_messages=[
            (
                "rr-inago-copywriter",
                "child-thread",
                ("INPUT-OFFERS-1 EVIDENCE-ROOM-1 html:source20",),
            )
        ],
    )
    assert (
        audit["native_handoffs"][0]["observation"]["input_refs"]["missing_refs"] == []
    )
    child = audit["native_children"][0]
    assert child["thread_id"] == "child-thread"
    assert child["observation"]["input_refs"]["missing_refs"] == ["html:source2"]
    assert len(child["observation"]["messages_sha256"]) == 64
    assert "PRIVATE-SOURCE-SENTINEL" not in json.dumps(audit)


def test_runner_links_native_handoff_and_child_observation_to_audit(
    tmp_path, monkeypatch
):
    request = _request()
    message = "INPUT-OFFERS-1 EVIDENCE-ROOM-1 html:source2"
    event = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "spawn_agent",
            "call_id": "call-copy",
            "arguments": json.dumps(
                {"agent_type": "rr-inago-copywriter", "message": message}
            ),
        },
    }
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(_partial_response(request)).encode(),
        events=[_spawn_event("rr-inago-copywriter")],
    )
    native = ai_runner._CodexSessionTrace(
        thread_id="root-thread",
        root_events=(event,),
        spawn_events=(event,),
        root_model="fixture-model",
        root_reasoning_setting="high",
        children=(
            ai_runner._ObservedSubagent(
                "rr-inago-copywriter",
                "copy-child",
                "fixture-model",
                "high",
                source_messages=(message,),
            ),
        ),
    )
    monkeypatch.setattr(runner, "_load_session_trace", lambda *a, **kw: native)
    result = runner.run(request)
    assert result.result_status == "success"
    audit_path = (
        runner.data_root / "artifacts/daily" / f"{request.job_id}-a1-source-refs.json"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["root_thread_id"] == "root-thread"
    assert audit["native_handoffs"][0]["call_id"] == "call-copy"
    assert audit["native_children"][0]["thread_id"] == "copy-child"
    assert (
        audit["native_children"][0]["observation"]["input_refs"]["missing_refs"] == []
    )


def test_rollout_observation_excludes_inherited_history_and_marks_unknown_boundary():
    events = [
        {"type": "session_meta", "payload": {"id": "child"}},
        {"type": "session_meta", "payload": {"id": "parent"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"text": "inherited sources"}],
            },
        },
    ]
    assert rollout_user_messages(events) is None
    events += [
        {
            "type": "event_msg",
            "payload": {"type": "thread_settings_applied", "thread_id": "child"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"text": "actual handoff"}],
            },
        },
    ]
    assert rollout_user_messages(events) == ("actual handoff",)


def test_unprovided_reference_boundary_is_still_enforced(tmp_path):
    request = _request()
    response = _partial_response(request)
    response["delegations"][0]["input_refs"].append("UNPROVIDED-REF")
    runner, _, _ = _runner(
        tmp_path,
        response=json.dumps(response).encode(),
        events=[_spawn_event("rr-inago-copywriter")],
    )
    result = runner.run(request)
    assert result.adoption_status == "rejected_contract"
    assert result.error["code"] == "delegations[0].refs"
