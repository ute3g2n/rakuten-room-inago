from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from automation.inago.contracts import RejectedContract
from automation.inago.copy_service import CopyGenerationSpec, _check_spec
from automation.inago.orchestrator import (
    _CopySpecContractRejected,
    _record_orchestration_error,
    run_daily,
)
from automation.inago.production_runtime import _make_copy_spec_factory
from tests_inago.test_step13 import (
    PREPARATION_ROOT,
    SETTINGS_VERSION,
    FakeOfferIntake,
    _make_ports,
    _unknown_facts,
    runtime_case,
)


def test_copy_spec_rejects_evidence_reference_missing_from_inputs() -> None:
    offer_key = "shop-fixture:item-01"
    spec = CopyGenerationSpec(
        run_id="STEP23-07-RUN",
        offer_key=offer_key,
        draft_version=1,
        target_date="2026-10-15",
        settings_version="fixture-settings-v1",
        input_refs=(f"offer:{offer_key}",),
        evidence_refs=("FACT:PRICE-01",),
        input_payload={
            "offer": {"offer_key": offer_key, "draft_version": 1},
            "purpose": "fixture copy request",
            "facts": [],
            "author_experience": {"availability": "unknown", "value": None},
        },
        deadline_at="2026-10-15T17:45:00+09:00",
        deadline_monotonic=1.0,
        generator_version="fixture-copy-v1",
        pattern_version="fixture-settings-v1",
    )

    with pytest.raises(
        RejectedContract,
        match="copy evidence references must be available inputs",
    ):
        _check_spec(spec)


def test_copy_spec_rejection_records_safe_stage_code_and_identifiers() -> None:
    state: dict[str, object] = {}
    rejection_message = "copy evidence references must be available inputs"
    rejection = _CopySpecContractRejected(
        run_id="STEP23-07-RUN",
        offer_key="shop-fixture:item-01",
        rejection=RejectedContract(rejection_message),
    )

    _record_orchestration_error(state, rejection)

    assert state["errors"] == [
        {
            "phase": "copy_spec",
            "code": "copy_evidence_refs_not_input",
            "references": {
                "run_id": "STEP23-07-RUN",
                "offer_key": "shop-fixture:item-01",
            },
        }
    ]
    assert rejection_message not in json.dumps(state)

    private_detail = "copy spec rejected for https://private.example/item"
    unknown = _CopySpecContractRejected(
        run_id="STEP23-07-RUN",
        offer_key="shop-fixture:item-01",
        rejection=RejectedContract(private_detail),
    )
    _record_orchestration_error(state, unknown)

    assert state["errors"][1] == {
        "phase": "copy_spec",
        "code": "copy_spec_contract_rejected",
        "references": {
            "run_id": "STEP23-07-RUN",
            "offer_key": "shop-fixture:item-01",
        },
    }
    assert "private.example" not in json.dumps(state)


class _MultiFactOfferIntake:
    fixture_only = True

    def __init__(self) -> None:
        self.delegate = FakeOfferIntake()
        self.requests = self.delegate.requests

    def persist_offer(self, connection, request) -> None:
        self.delegate.persist_offer(connection, request)
        facts = _unknown_facts()
        facts[0]["source_ref"] = request.evidence_ref
        facts[1]["source_ref"] = "PRODUCT-FACT:FIXTURE-01"
        connection.execute(
            "UPDATE offer SET facts_json=? WHERE offer_key=?",
            (json.dumps(facts, ensure_ascii=False), request.offer_key),
        )


class _EvidenceSelectingRunner:
    fixture_only = True

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        evidence_ref = request.evidence_refs[0]
        ordered_refs = (
            evidence_ref,
            *(ref for ref in request.input_refs if ref != evidence_ref),
        )
        result = self.delegate.run(replace(request, input_refs=ordered_refs))
        return replace(
            result,
            input_refs=request.input_refs,
            subjobs=tuple(
                replace(child, input_refs=request.input_refs)
                for child in result.subjobs
            ),
        )


def test_saved_room_offer_flows_through_copy_spec_and_commits_once(runtime_case) -> None:
    basic = json.loads(
        (PREPARATION_ROOT / "初期設定引渡し.json").read_text(encoding="utf-8")
    )
    settings_runtime = SimpleNamespace(
        active_basic_version=SETTINGS_VERSION,
        themes=tuple(basic["themes"]),
    )
    production_factory = _make_copy_spec_factory(None, settings_runtime)
    created_specs = []

    def make_copy_spec(connection, **kwargs):
        spec = production_factory(connection, **kwargs)
        if spec is not None:
            created_specs.append(spec)
        return spec

    intake = _MultiFactOfferIntake()
    bundle = _make_ports(
        runtime_case,
        posts_per_search=1,
        skip_first_theme_seed=True,
        offer_intake=intake,
    )
    evidence_runner = _EvidenceSelectingRunner(bundle.runner)
    bundle.ports = replace(
        bundle.ports,
        copy_spec_factory=make_copy_spec,
        copy_runner=evidence_runner,
    )

    def finish_after_first_commit(_connection) -> None:
        runtime_case.clock.current = datetime.fromisoformat(
            "2026-10-12T17:45:00+09:00"
        )

    outcome = run_daily(
        runtime_case.connection,
        data_root=runtime_case.data_root,
        ports=bundle.ports,
        clock=runtime_case.clock,
        run_id_factory=lambda _date: "STEP23-07-RUN",
        seed_factory=lambda: "STEP23-07-SEED",
        on_draft_commit=finish_after_first_commit,
    )

    assert outcome.status in {"complete", "partial"}
    assert outcome.committed_count >= 1
    assert intake.requests
    target_offer_key = intake.requests[0].offer_key
    target_specs = [spec for spec in created_specs if spec.offer_key == target_offer_key]
    assert len(target_specs) == 1
    spec = target_specs[0]
    assert spec.evidence_refs == (
        intake.requests[0].evidence_ref,
        "PRODUCT-FACT:FIXTURE-01",
    )
    assert set(spec.evidence_refs).issubset(spec.input_refs)
    target_requests = [
        request
        for request in evidence_runner.requests
        if request.copy_targets
        and request.copy_targets[0].offer_key == target_offer_key
    ]
    assert len(target_requests) == 1
    assert target_requests[0].input_refs == spec.input_refs
    assert target_requests[0].evidence_refs == spec.evidence_refs
    assert runtime_case.connection.execute(
        "SELECT COUNT(*) FROM offer WHERE offer_key=?", (spec.offer_key,)
    ).fetchone()[0] == 1
    assert runtime_case.connection.execute(
        "SELECT COUNT(*) FROM draft WHERE run_id=? AND offer_key=?",
        ("STEP23-07-RUN", spec.offer_key),
    ).fetchone()[0] == 1
    assert runtime_case.connection.execute(
        "SELECT COUNT(*) FROM reservation WHERE offer_key=? AND status='active'",
        (spec.offer_key,),
    ).fetchone()[0] == 1
