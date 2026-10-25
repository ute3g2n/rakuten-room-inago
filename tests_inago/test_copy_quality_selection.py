from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from automation.inago import orchestrator
from automation.inago import product_page_inputs
from tests_inago.product_page_test_support import product_dir
from tests_inago.test_product_page_selection import (
    _FixtureOCR,
    _checkpoint,
    _enable_product_page_pipeline,
    _run,
    runtime_case,
)
from tests_inago.test_step13 import RuntimeCase, _make_ports


def test_information_shortage_is_replaced_from_existing_candidates(
    runtime_case: RuntimeCase, monkeypatch,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case, image_count=1)
    ocr = _FixtureOCR()
    generate = orchestrator.generate_and_commit_copy
    calls: list[str] = []

    def omit_first(connection, spec, runner, **kwargs):
        calls.append(spec.offer_key)
        if len(calls) == 1:
            return SimpleNamespace(
                status="insufficient_information", error_code="insufficient_information"
            )
        return generate(connection, spec, runner, **kwargs)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", omit_first)
    outcome = _run(runtime_case, bundle, ocr, run_id="COPY-QUALITY-REPLENISH")

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert outcome.error_count == 0
    state = _checkpoint(runtime_case.connection, outcome.run_id)
    assert len(state["selected_products"]) == 16
    omitted = state["selected_products"][0]
    assert omitted["status"] == "insufficient_information"
    assert omitted["copy_omission_reason"] == "insufficient_information"
    assert omitted["offer_key"] in state["handled_offer_keys"]
    assert not state.get("active_product_slot_failures")
    assert not product_dir(
        runtime_case.data_root, outcome.run_id, omitted["shop_id"], omitted["item_id"]
    ).exists()
    assert len(calls) == len(set(calls)) == 16
    assert len(ocr.calls) == len(set(ocr.calls)) == 16
    # One unusable copy is replaced from the already collected initial pool.
    # The 25-post searches supply enough products, so an additional theme
    # search would be unnecessary.
    assert state["additional_events"] == 0
    assert all(
        entry["status"] == "committed"
        for entry in state["selected_products"][1:]
    )
    assert runtime_case.connection.execute(
        "SELECT COUNT(*) FROM draft WHERE run_id=? AND offer_key=?",
        (outcome.run_id, omitted["offer_key"]),
    ).fetchone()[0] == 0


def test_resume_never_regenerates_or_reselects_information_shortage(
    runtime_case: RuntimeCase, monkeypatch,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case, image_count=1)
    ocr = _FixtureOCR()
    generate = orchestrator.generate_and_commit_copy
    calls: list[str] = []
    interrupted = False

    def omit_then_interrupt(connection, spec, runner, **kwargs):
        nonlocal interrupted
        calls.append(spec.offer_key)
        if len(calls) == 1:
            return SimpleNamespace(
                status="insufficient_information", error_code="insufficient_information"
            )
        if not interrupted:
            interrupted = True
            raise RuntimeError("fixture technical interruption after normal exclusion")
        return generate(connection, spec, runner, **kwargs)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", omit_then_interrupt)
    first = _run(runtime_case, bundle, ocr, run_id="COPY-QUALITY-RESUME")
    assert first.status == "failed"
    assert first.committed_count == 0
    before = _checkpoint(runtime_case.connection, first.run_id)
    omitted = before["selected_products"][0]
    assert omitted["status"] == "insufficient_information"
    # Copies are processed one theme batch at a time; the interruption occurs
    # during the first five-product batch.
    assert len(ocr.calls) == 5

    second = _run(runtime_case, bundle, ocr, run_id=first.run_id)

    assert second.resumed is True
    assert second.status == "complete"
    assert second.committed_count == 15
    assert second.error_count == 0
    after = _checkpoint(runtime_case.connection, first.run_id)
    assert after["selected_products"][0] == omitted
    assert Counter(calls)[omitted["offer_key"]] == 1
    assert Counter(ocr.calls)[omitted["offer_key"]] == 1
    assert len(ocr.calls) == 16
    assert len({entry["offer_key"] for entry in after["selected_products"]}) == 16
    assert before["selected_products"][1]["ocr_ref"] == after["selected_products"][1]["ocr_ref"]


def test_resume_cleans_information_shortage_if_cleanup_was_interrupted(
    runtime_case: RuntimeCase, monkeypatch,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case, image_count=1)
    ocr = _FixtureOCR()
    generate = orchestrator.generate_and_commit_copy
    cleanup = orchestrator._cleanup_unselected_product_inputs
    omitted: list[str] = []

    def omit_once(connection, spec, runner, **kwargs):
        if not omitted:
            omitted.append(spec.offer_key)
            return SimpleNamespace(
                status="insufficient_information", error_code="insufficient_information"
            )
        assert spec.offer_key != omitted[0]
        return generate(connection, spec, runner, **kwargs)

    def interrupt_cleanup(connection, **kwargs):
        if any(
            entry.get("status") == "insufficient_information"
            for entry in kwargs["candidates"]
        ):
            raise KeyboardInterrupt("fixture cleanup interruption")
        return cleanup(connection, **kwargs)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", omit_once)
    monkeypatch.setattr(
        orchestrator, "_cleanup_unselected_product_inputs", interrupt_cleanup
    )
    with pytest.raises(KeyboardInterrupt, match="fixture cleanup interruption"):
        _run(runtime_case, bundle, ocr, run_id="COPY-QUALITY-CLEANUP-RESUME")
    before = _checkpoint(runtime_case.connection, "COPY-QUALITY-CLEANUP-RESUME")
    entry = before["selected_products"][0]
    folder = product_dir(
        runtime_case.data_root,
        "COPY-QUALITY-CLEANUP-RESUME",
        entry["shop_id"],
        entry["item_id"],
    )
    assert entry["status"] == "insufficient_information"
    assert folder.exists()
    monkeypatch.setattr(orchestrator, "_cleanup_unselected_product_inputs", cleanup)

    outcome = _run(runtime_case, bundle, ocr, run_id="COPY-QUALITY-CLEANUP-RESUME")

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert outcome.error_count == 0
    assert not folder.exists()
    assert len(ocr.calls) == 16


def test_information_shortage_cleanup_failure_is_resolved_on_resume(
    runtime_case: RuntimeCase, monkeypatch,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case, image_count=1)
    ocr = _FixtureOCR()
    generate = orchestrator.generate_and_commit_copy
    cleanup = product_page_inputs.cleanup_unselected_product_page_folders
    omitted: list[str] = []
    cleanup_failed = False

    def omit_once(connection, spec, runner, **kwargs):
        if not omitted:
            omitted.append(spec.offer_key)
            return SimpleNamespace(
                status="insufficient_information", error_code="insufficient_information"
            )
        assert spec.offer_key != omitted[0]
        return generate(connection, spec, runner, **kwargs)

    def fail_cleanup_once(data_root, run_id, candidates, keep):
        nonlocal cleanup_failed
        if (
            not cleanup_failed
            and omitted
            and any(entry.get("offer_key") == omitted[0] for entry in candidates)
        ):
            cleanup_failed = True
            return {
                "deleted": [],
                "failed": [{"offer_key": omitted[0], "error_class": "PermissionError"}],
            }
        return cleanup(data_root, run_id, candidates, keep)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", omit_once)
    monkeypatch.setattr(
        product_page_inputs, "cleanup_unselected_product_page_folders", fail_cleanup_once
    )
    first = _run(runtime_case, bundle, ocr, run_id="COPY-QUALITY-CLEANUP-RETRY")
    assert first.status == "partial"
    assert first.committed_count == 15
    state = _checkpoint(runtime_case.connection, first.run_id)
    assert first.error_count == 1, Counter(
        (entry.get("phase"), entry.get("code")) for entry in state["errors"]
    )
    assert state["product_page_cleanup_failures"][0]["offer_key"] == omitted[0]

    second = _run(runtime_case, bundle, ocr, run_id=first.run_id)

    assert second.status == "complete"
    assert second.committed_count == 15
    assert second.error_count == 0
    state = _checkpoint(runtime_case.connection, first.run_id)
    assert not state["product_page_cleanup_failures"]
    assert len(ocr.calls) == 16


@pytest.mark.parametrize("status", ["pending_commit", "saved_response", "in_progress"])
def test_technical_save_interruption_keeps_material_and_resumes_candidate(
    runtime_case: RuntimeCase, monkeypatch, status: str,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case, image_count=1)
    ocr = _FixtureOCR()
    generate = orchestrator.generate_and_commit_copy
    interrupted_offer: list[str] = []

    def pending_once(connection, spec, runner, **kwargs):
        if not interrupted_offer:
            interrupted_offer.append(spec.offer_key)
            return SimpleNamespace(status=status, error_code="fixture_save_failure")
        return generate(connection, spec, runner, **kwargs)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", pending_once)
    first = _run(runtime_case, bundle, ocr, run_id="COPY-QUALITY-SAVE-RESUME")
    assert first.status == "failed"
    before = _checkpoint(runtime_case.connection, first.run_id)
    entry = before["selected_products"][0]
    assert entry["status"] == "copy_in_progress"
    assert "copy_omission_reason" not in entry
    folder = product_dir(
        runtime_case.data_root, first.run_id, entry["shop_id"], entry["item_id"]
    )
    assert (folder / "images" / "0001.png").exists()

    second = _run(runtime_case, bundle, ocr, run_id=first.run_id)

    assert second.status == "complete"
    assert second.committed_count == 15
    assert second.error_count == 0
    assert len(ocr.calls) == 15
    after = _checkpoint(runtime_case.connection, first.run_id)
    assert after["selected_products"][0]["status"] == "committed"
    assert after["selected_products"][0]["ocr_ref"] == entry["ocr_ref"]
