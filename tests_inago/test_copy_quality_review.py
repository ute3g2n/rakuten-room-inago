"""Copy review wiring and persistence tests, using explicit reviewer fixtures.

These do not claim that a live AI evaluated the literary quality of a fixture.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from automation.inago.ai_runner import (
    _ObservedSubagent,
    _final_payload_hash,
    compute_input_hash,
)
from automation.inago import copy_service
from tests_inago.copy_quality_test_support import review_subjob
from tests_inago.test_step08 import _base_request, _delegation, _runner, _spawn_event
from tests_inago.test_step12 import (
    FakeCopyRunner,
    RUN_ID,
    make_spec,
    run_copy,
    runtime_case,
)


@pytest.mark.parametrize("mode", ["missing", "other_body", "revise", "wrong_product", "missing_sources"])
def test_unapproved_or_unbound_body_cannot_be_committed(runtime_case, mode):
    class InvalidReview(FakeCopyRunner):
        def run(self, request):
            result = super().run(request)
            author, review = result.subjobs
            if mode == "missing":
                return replace(result, subjobs=(author,))
            payload = dict(review.payload)
            if mode == "other_body":
                payload["reviewed_body"] += " changed after review"
            elif mode == "revise":
                payload["verdict"] = "revise"
            elif mode == "wrong_product":
                payload["offer_key"] = "another:product"
            elif mode == "missing_sources":
                review = replace(review, input_refs=())
            return replace(result, subjobs=(author, replace(review, payload=payload)))

    runner = InvalidReview()
    result = run_copy(runtime_case, runner)
    assert result.status == "contract_error"
    assert len(runner.requests) == 1
    assert runtime_case.connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
    assert runtime_case.connection.execute("SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)).fetchone()[0] == 0


def test_insufficient_information_is_normal_and_not_retried_or_committed(runtime_case):
    class InformationMissing(FakeCopyRunner):
        def run(self, request):
            result = super().run(request)
            author = result.subjobs[0]
            author = replace(author, payload={
                **author.payload,
                "status": "insufficient_information",
                "body": "",
                "selection_reason": "All sources contain only the title and size.",
                "omitted_unknowns": ["No grounded description beyond title and overview."],
            })
            return replace(result, subjobs=(author, review_subjob(author, verdict="insufficient_information")))

    runner = InformationMissing()
    first = run_copy(runtime_case, runner)
    assert first.status == "insufficient_information"
    assert first.commit_result is None
    assert first.attempts_used == 1
    assert len(runner.requests) == 1
    second = run_copy(runtime_case, runner)
    assert second.status == "insufficient_information" and second.resumed
    assert len(runner.requests) == 1
    attempt = runtime_case.connection.execute("SELECT outcome,error_code FROM generation_attempt").fetchone()
    assert tuple(attempt) == ("empty", "insufficient_information")
    assert runtime_case.connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
    review = runtime_case.connection.execute("SELECT payload_json FROM ai_job WHERE agent_id='AG-COPY-REVIEW'").fetchone()
    assert json.loads(review[0])["verdict"] == "insufficient_information"


def test_final_revised_body_and_independent_review_are_saved(runtime_case):
    runner = FakeCopyRunner(body="Fixture: source-grounded revised description.")
    result = run_copy(runtime_case, runner)
    assert result.status == "committed"
    review = runtime_case.connection.execute("SELECT payload_json FROM ai_job WHERE agent_id='AG-COPY-REVIEW'").fetchone()
    body = runtime_case.connection.execute("SELECT body FROM draft").fetchone()[0]
    assert json.loads(review[0])["reviewed_body"] == body == runner.body


def test_new_writer_response_without_quality_status_is_not_legacy_accepted(runtime_case):
    class LegacyWriter(FakeCopyRunner):
        def run(self, request):
            result = super().run(request)
            author = result.subjobs[0]
            payload = dict(author.payload)
            payload.pop("status")
            return replace(result, subjobs=(replace(author, payload=payload), *result.subjobs[1:]))

    result = run_copy(runtime_case, LegacyWriter())
    assert result.status == "contract_error"
    assert result.error_code == "copy_payload_contract_invalid"


@pytest.mark.parametrize("native", ["not_spawned", "different_result", "matching_result"])
def test_native_reviewer_result_is_observed_instead_of_trusting_parent(tmp_path, native):
    request = _base_request()
    payload = {
        "offer_key": "shop-A:item-1", "draft_version": 1,
        "verdict": "pass", "reviewed_body": "Fixture description.",
        "reason": "Fixture source-based judgment.",
        "evidence_refs": list(request.evidence_refs),
        "used_input_refs": list(request.input_refs), "findings": [],
    }
    runner, _, _ = _runner(tmp_path, response=None)
    declaration = _delegation("AG-COPY-REVIEW", "T02", "rr-inago-copy-reviewer", payload)
    events = [] if native == "not_spawned" else [_spawn_event("rr-inago-copy-reviewer")]
    observed = (
        _ObservedSubagent(
            "rr-inago-copy-reviewer", "native-review-thread", "fixture-model", "fixture",
            compute_input_hash(payload if native == "matching_result" else {**payload, "verdict": "revise"}),
        ),
    ) if events else ()
    subjobs, error = runner._validate_delegations(
        request, compute_input_hash(request.input_payload),
        {"delegations": [declaration]}, events, child_traces=observed,
    )
    if native == "matching_result":
        assert error is None and subjobs[0].payload == payload
    else:
        assert error is not None and not subjobs


def test_child_final_json_is_extracted_after_native_revision():
    old = {"verdict": "revise", "reviewed_body": "old"}
    final = {"verdict": "pass", "reviewed_body": "revised"}
    events = [
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "channel": "final",
            "content": [{"type": "output_text", "text": json.dumps(value)}],
        }} for value in (old, final)
    ]
    assert _final_payload_hash(events) == compute_input_hash(final)
    events.append({"type": "event_msg", "payload": {
        "type": "task_complete", "last_agent_message": "not JSON",
    }})
    assert _final_payload_hash(events) is None
    events[-1]["payload"]["last_agent_message"] = '{"verdict":NaN}'
    assert _final_payload_hash(events) is None


def test_independent_review_schema_is_the_same_in_native_root_output():
    root = Path(__file__).resolve().parents[1] / "automation" / "inago" / "schemas"
    child = json.loads((root / "ai-copy-review.schema.json").read_text(encoding="utf-8"))
    envelope = json.loads((root / "ai-job.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(child)
    normalized = {key: value for key, value in child.items() if key not in {"$schema", "$id", "title"}}
    assert normalized in envelope["$defs"]["delegation_payload"]["anyOf"]


def _source_spec():
    original = make_spec()
    return replace(
        original,
        input_refs=("INPUT-A1", "INPUT-A2"),
        input_payload={
            **original.input_payload,
            "product_page_html": {"selectors": [{"elements": [{"outer_html": "<span>original detail</span>"}]}]},
            "product_page_ocr": {"images": [{"ref": "INPUT-A2", "text": "original OCR detail"}]},
        },
    )


def _saved_checkpoint(case, monkeypatch, spec):
    commit = copy_service.commit_draft

    def fail_before_commit(*args, **kwargs):
        return commit(*args, failpoint="before_commit", **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(copy_service, "commit_draft", fail_before_commit)
        first = run_copy(case, FakeCopyRunner(), spec=spec)
    assert first.status == "pending_commit"
    artifact = case.connection.execute(
        "SELECT artifact_ref,path FROM artifact WHERE artifact_ref="
        "(SELECT artifact_ref FROM generation_attempt WHERE run_id=?)", (RUN_ID,),
    ).fetchone()
    return artifact[0], Path(artifact[1]), Path(artifact[1]).read_bytes()


@pytest.mark.parametrize("mutation", [
    "missing_review", "missing_root", "review_body", "review_verdict",
    "review_source_refs", "author_source_refs", "added_input_ref", "removed_input_ref",
    "added_evidence_ref", "changed_html_same_refs", "changed_ocr_same_refs",
])
def test_unverified_or_changed_checkpoint_cannot_bypass_review(runtime_case, monkeypatch, mutation):
    spec = _source_spec()
    artifact_ref, artifact_path, artifact_bytes = _saved_checkpoint(runtime_case, monkeypatch, spec)
    connection = runtime_case.connection
    if mutation in {"missing_review", "missing_root"}:
        connection.execute("DELETE FROM ai_job WHERE agent_id=?", (
            "AG-COPY-REVIEW" if mutation == "missing_review" else "AI-O",
        ))
    elif mutation in {"review_body", "review_verdict"}:
        row = connection.execute("SELECT job_id,payload_json FROM ai_job WHERE agent_id='AG-COPY-REVIEW'").fetchone()
        payload = json.loads(row[1])
        payload["reviewed_body" if mutation == "review_body" else "verdict"] = (
            "different body" if mutation == "review_body" else "revise"
        )
        connection.execute("UPDATE ai_job SET payload_json=? WHERE job_id=?", (json.dumps(payload), row[0]))
    elif mutation in {"review_source_refs", "author_source_refs"}:
        connection.execute("UPDATE ai_job SET input_refs_json=? WHERE agent_id=?", (
            '["INPUT-A1"]', "AG-COPY-REVIEW" if mutation == "review_source_refs" else "AG-COPY",
        ))
    elif mutation == "added_input_ref":
        spec = replace(spec, input_refs=(*spec.input_refs, "INPUT-A3"))
    elif mutation == "removed_input_ref":
        spec = replace(spec, input_refs=("INPUT-A1",))
    elif mutation == "added_evidence_ref":
        spec = replace(spec, evidence_refs=("INPUT-A1", "INPUT-A2"))
    elif mutation in {"changed_html_same_refs", "changed_ocr_same_refs"}:
        payload = deepcopy(spec.input_payload)
        if mutation == "changed_html_same_refs":
            payload["product_page_html"]["selectors"][0]["elements"][0]["outer_html"] = "<span>new detail</span>"
        else:
            payload["product_page_ocr"]["images"][0]["text"] = "new OCR detail"
        spec = replace(spec, input_payload=payload)
    connection.commit()

    runner = FakeCopyRunner()
    result = run_copy(runtime_case, runner, spec=spec)
    assert result.status == "contract_error" and result.resumed
    assert result.error_code.startswith("copy_checkpoint_")
    assert runner.requests == []
    assert connection.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 0
    assert connection.execute("SELECT committed_count FROM run WHERE run_id=?", (RUN_ID,)).fetchone()[0] == 0
    assert tuple(connection.execute("SELECT outcome,artifact_ref FROM generation_attempt").fetchone()) == ("success", artifact_ref)
    assert artifact_path.read_bytes() == artifact_bytes


def test_verified_checkpoint_resumes_identical_body_without_an_ai_call(runtime_case, monkeypatch):
    spec = _source_spec()
    artifact_ref, artifact_path, artifact_bytes = _saved_checkpoint(runtime_case, monkeypatch, spec)
    # A later scheduling window alone does not change reviewed product sources.
    spec = replace(spec, deadline_at="2026-10-12T18:30:00+09:00")
    runner = FakeCopyRunner(["error"])
    result = run_copy(runtime_case, runner, spec=spec)
    assert result.status == "committed" and result.resumed
    assert runner.requests == []
    saved = runtime_case.connection.execute("SELECT body,artifact_ref FROM draft").fetchone()
    assert saved[0] == json.loads(artifact_bytes)["body"] and saved[1] == artifact_ref
    assert artifact_path.read_bytes() == artifact_bytes


def test_already_committed_body_is_not_requalified_or_rewritten(runtime_case):
    first = run_copy(runtime_case, FakeCopyRunner())
    assert first.status == "committed"
    body = runtime_case.connection.execute("SELECT body FROM draft").fetchone()[0]
    runtime_case.connection.execute("DELETE FROM ai_job WHERE agent_id='AG-COPY-REVIEW'")
    runtime_case.connection.commit()
    runner = FakeCopyRunner(["error"])
    outcome = run_copy(runtime_case, runner)
    assert outcome.status == "already_committed" and runner.requests == []
    assert runtime_case.connection.execute("SELECT body FROM draft").fetchone()[0] == body
