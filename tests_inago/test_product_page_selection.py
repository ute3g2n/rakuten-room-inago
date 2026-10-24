from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.inago import orchestrator
from automation.inago.clock import FakeClock
from automation.inago.orchestrator import run_daily
from automation.inago.paths import SAFE_FIXTURE_BASE, resolve_runtime_paths
from automation.inago.product_page_inputs import load_product_page_inputs
from automation.inago.repository import open_database
from automation.inago.settings import register_external_settings
from tests_inago.product_page_test_support import product_dir, write_ocr, write_page
from tests_inago.test_step13 import (
    BASIC_SETTINGS_PATH,
    DETAIL_RULES_PATH,
    NOW,
    RuntimeCase,
    SETTINGS_VERSION,
    _make_ports,
)


@pytest.fixture
def runtime_case(tmp_path: Path, fixture_config_path: Path) -> RuntimeCase:
    # Different pytest invocations can give this node the same tmp_path.name.
    # Include its full path identity so a local replay never shares state with
    # another invocation or a prior invocation's interrupted cleanup.
    invocation = hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()[:16]
    private_root = (
        SAFE_FIXTURE_BASE / "product-page-selection" / invocation / tmp_path.name
    )
    paths = resolve_runtime_paths(
        data_root=private_root / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    register_external_settings(
        connection,
        json.loads(BASIC_SETTINGS_PATH.read_text(encoding="utf-8")),
        json.loads(DETAIL_RULES_PATH.read_text(encoding="utf-8")),
        created_at=NOW,
    )
    case = RuntimeCase(
        connection=connection,
        data_root=paths.data_root,
        clock=FakeClock(datetime.fromisoformat(NOW)),
    )
    try:
        yield case
    finally:
        connection.close()
        if private_root.resolve().is_relative_to(SAFE_FIXTURE_BASE.resolve()):
            shutil.rmtree(private_root, ignore_errors=True)


class _FixtureOCR:
    fixture_only = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def process_product(
        self,
        _connection,
        *,
        run_id: str,
        offer_key: str,
        shop_id: str,
        item_id: str,
        page_ref: str,
        source_evidence_ref: str | None,
        data_root: Path,
    ) -> dict:
        del source_evidence_ref
        self.calls.append(offer_key)
        page = load_product_page_inputs(
            data_root, run_id, shop_id, item_id, expected_ref=page_ref
        )
        if page is None:
            return {"status": "failed", "images": [], "error_code": "page_missing"}
        ocr_images = [
            {
                "ref": f"ocrimg:fixture:{index + 1:02d}",
                "status": "empty",
                "text": "",
            }
            for index, _ in enumerate(page.get("images", []))
        ]
        _, ocr_ref = write_ocr(data_root, page, images=ocr_images)
        return {
            "status": "complete",
            "images": [
                {
                    "order": index,
                    "status": "empty",
                    "image_ref": image["ref"],
                }
                for index, image in enumerate(ocr_images)
            ],
            "ocr_ref": ocr_ref,
        }


class _FixtureProductPageCapture:
    fixture_only = True

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.requests = []

    def capture_product_page_inputs(self, request):
        self.requests.append(request)
        page = write_page(
            self.data_root,
            request.run_id,
            request.shop_id,
            request.item_id,
            source_evidence_ref=f"{request.attempt_id}:row:{request.row_index}",
        )
        return {
            "request_id": request.request_id,
            "run_id": request.run_id,
            "shop_id": request.shop_id,
            "item_id": request.item_id,
            "page_ref": page["page_ref"],
            "state": "captured",
            "image_count": 0,
        }


def _enable_product_page_pipeline(
    bundle,
    case: RuntimeCase,
    *,
    image_count: int = 0,
    capture_failed_offer: list[str] | None = None,
) -> None:
    facts: dict[str, dict] = {}
    original_search = bundle.room.search

    def search(request):
        response = original_search(request)
        for post_index, post in enumerate(response["posts"]):
            shop_id = post["observed_shop_id"]
            item_id = post["observed_item_id"]
            evidence_ref = post["evidence_ref"]
            page = write_page(
                case.data_root,
                request.run_id,
                shop_id,
                item_id,
                source_evidence_ref=evidence_ref,
                images=[
                    {
                        "index": index,
                        "url": f"https://images.test/{shop_id}/{item_id}/{index}.png",
                        "occurrences": [{"order": index, "tag": "img", "attribute": "src"}],
                        "status": "downloaded",
                        "file": f"images/{index + 1:04d}.png",
                        "content_type": "image/png",
                        "byte_length": 12,
                    }
                    for index in range(image_count)
                ],
            )
            if capture_failed_offer is not None and post_index == 0:
                page["html_state"] = "capture_failed"
                page["selectors"] = [
                    {
                        "key": "sale_desc",
                        "selector": "span.sale_desc",
                        "status": "unavailable",
                        "outer_html": [],
                    }
                ]
                page_path = (
                    product_dir(case.data_root, request.run_id, shop_id, item_id)
                    / "page.json"
                )
                page_path.write_text(
                    json.dumps(page, ensure_ascii=False), encoding="utf-8"
                )
                capture_failed_offer.append(f"{shop_id}:{item_id}")
            for index in range(image_count):
                image_path = (
                    product_dir(case.data_root, request.run_id, shop_id, item_id)
                    / "images"
                    / f"{index + 1:04d}.png"
                )
                image_path.parent.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(b"fixture-image")
            facts[evidence_ref] = {
                "product_page_identity": {
                    "run_id": request.run_id,
                    "shop_id": shop_id,
                    "item_id": item_id,
                },
                "product_page_ref": page["page_ref"],
            }
        return response

    bundle.room.search = search
    bundle.room.product_fact = lambda evidence_ref: facts.get(evidence_ref)


def _run(
    case: RuntimeCase,
    bundle,
    ocr: _FixtureOCR,
    *,
    run_id: str,
    manual: bool = False,
    target_date: str | None = None,
):
    bundle.ports = replace(bundle.ports, product_page_ocr=ocr)
    outcome = run_daily(
        case.connection,
        data_root=case.data_root,
        ports=bundle.ports,
        clock=case.clock,
        run_id_factory=lambda _target: run_id,
        seed_factory=lambda: f"{run_id}-SEED",
        manual=manual,
        target_date=target_date,
    )
    diagnostic_root = os.environ.get("INAGO_SELECTION_TEST_DIAGNOSTIC_ROOT")
    if diagnostic_root and outcome.error_count:
        state = _checkpoint(case.connection, outcome.run_id)
        diagnostic = {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "committed_count": outcome.committed_count,
            "error_count": outcome.error_count,
            "errors": state.get("errors"),
            "active_resume_errors": state.get("active_resume_errors"),
            "active_product_slot_failures": state.get("active_product_slot_failures"),
            "product_image_cleanup_failures": state.get("product_image_cleanup_failures"),
            "product_page_cleanup_failures": state.get("product_page_cleanup_failures"),
            "failed_image_cleanup_results": [
                {"offer_key": entry.get("offer_key"), "image_cleanup": entry["image_cleanup"]}
                for entry in state.get("selected_products", [])
                if isinstance(entry.get("image_cleanup"), dict)
                and entry["image_cleanup"].get("status") == "failed"
            ],
            "uncommitted_products": [
                entry
                for entry in state.get("selected_products", [])
                if entry.get("status") != "committed"
            ],
            "generation_errors": [
                dict(row)
                for row in case.connection.execute(
                    "SELECT offer_key,attempt_no,outcome,error_code FROM generation_attempt "
                    "WHERE run_id=? AND error_code IS NOT NULL",
                    (outcome.run_id,),
                )
            ],
        }
        folder = Path(diagnostic_root)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{outcome.run_id}-{uuid.uuid4().hex}.json"
        path.write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"selection test checkpoint diagnostic: {path}")
    return outcome


def _checkpoint(connection, run_id: str) -> dict:
    value = connection.execute(
        "SELECT checkpoint FROM run WHERE run_id=?", (run_id,)
    ).fetchone()
    return json.loads(value[0])["daily_orchestration"]


def test_first_copy_waits_for_fifteen_selected_and_all_ocr(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case)
    ocr = _FixtureOCR()
    original_copy = bundle.runner.run
    copy_ocr_counts: list[int] = []

    def copy_after_ocr(request):
        copy_ocr_counts.append(len(ocr.calls))
        assert len(ocr.calls) == 15
        return original_copy(request)

    bundle.runner.run = copy_after_ocr

    outcome = _run(runtime_case, bundle, ocr, run_id="PRODUCT-SELECT-15")

    assert outcome.status == "complete"
    assert outcome.committed_count == 15
    assert len(ocr.calls) == 15
    assert len(set(ocr.calls)) == 15
    assert copy_ocr_counts == [15] * 15
    assert len(bundle.runner.requests) == 15
    state = _checkpoint(runtime_case.connection, "PRODUCT-SELECT-15")
    selected = state["selected_products"]
    assert len(selected) == 15
    assert all(item["status"] == "committed" for item in selected)
    assert len({item["page_ref"] for item in selected}) == 15


def test_other_theme_supplement_does_not_resolve_failed_slot(
    runtime_case: RuntimeCase, monkeypatch
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case)
    ocr = _FixtureOCR()
    original_generate = orchestrator.generate_and_commit_copy
    failed_offer: list[str] = []

    def fail_one_selected(connection, spec, runner, **kwargs):
        if not failed_offer:
            failed_offer.append(spec.offer_key)
            return SimpleNamespace(
                status="contract_error",
                error_code="fixture_copy_failure",
                cause_code="fixture_failure",
                artifact_ref="fixture:copy:failure",
            )
        return original_generate(connection, spec, runner, **kwargs)

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", fail_one_selected)

    outcome = _run(runtime_case, bundle, ocr, run_id="PRODUCT-REPLENISH-15")

    failed_state = _checkpoint(runtime_case.connection, "PRODUCT-REPLENISH-15")
    assert outcome.status == "partial"
    assert outcome.committed_count == 15
    assert outcome.error_count == 1
    assert outcome.primary_error["code"] == "fixture_copy_failure"
    active = failed_state["active_product_slot_failures"]
    assert len(active) == 1
    assert active[0]["offer_key"] == failed_offer[0]
    assert active[0]["theme_id"] != failed_state["selected_products"][-1]["theme_id"]
    assert "replacement_offer_key" not in active[0]
    assert failed_state.get("resolved_product_slot_failures", []) == []
    selected = failed_state["selected_products"]
    assert len(selected) == 16
    replacement = selected[-1]
    assert replacement["offer_key"] != failed_offer[0]
    assert "replacement_slot_id" not in replacement
    assert len(ocr.calls) == 16


def test_capture_failed_page_is_skipped_and_replenished_before_copy(
    runtime_case: RuntimeCase, monkeypatch
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "_trace_product_stage",
        lambda *_args, **_kwargs: None,
    )
    bundle = _make_ports(runtime_case, posts_per_search=25)
    failed_offer: list[str] = []
    _enable_product_page_pipeline(
        bundle, runtime_case, capture_failed_offer=failed_offer
    )
    ocr = _FixtureOCR()
    original_copy = bundle.runner.run
    copied_offers: list[str] = []

    def copy_only_captured_products(request):
        offer_key = request.copy_targets[0].offer_key
        copied_offers.append(offer_key)
        assert offer_key != failed_offer[0]
        return original_copy(request)

    bundle.runner.run = copy_only_captured_products

    outcome = _run(runtime_case, bundle, ocr, run_id="PRODUCT-CAPTURE-REPLENISH")

    assert failed_offer
    assert failed_offer[0] not in copied_offers
    state = _checkpoint(runtime_case.connection, "PRODUCT-CAPTURE-REPLENISH")
    selected_offers = {entry["offer_key"] for entry in state["selected_products"]}
    assert failed_offer[0] not in selected_offers
    assert failed_offer[0] not in ocr.calls
    assert outcome.status == "complete", {
        "reason": outcome.reason,
        "committed_count": outcome.committed_count,
        "error_count": outcome.error_count,
        "primary_error": outcome.primary_error,
        "errors": state.get("errors", [])[-5:],
        "copy_count": len(copied_offers),
        "ocr_count": len(ocr.calls),
    }
    assert outcome.committed_count == 15
    assert len(ocr.calls) == 15
    assert len(copied_offers) == 15
    assert len(state["selected_products"]) == 15
    assert failed_offer[0] not in {
        entry["offer_key"] for entry in state["selected_products"]
    }
    failures = state["product_page_capture_failures"]
    assert failures[0]["offer_key"] == failed_offer[0]
    assert failures[0]["code"] == "product_page_capture_failed"
    failed_shop, failed_item = failed_offer[0].split(":", 1)
    failed_page = (
        product_dir(
            runtime_case.data_root,
            "PRODUCT-CAPTURE-REPLENISH",
            failed_shop,
            failed_item,
        )
        / "page.json"
    )
    assert not failed_page.exists()


def test_resume_recaptures_failed_selected_page_without_room_search(
    runtime_case: RuntimeCase, tmp_path: Path,
):
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case)
    ocr = _FixtureOCR()
    outcome = _run(runtime_case, bundle, ocr, run_id="PRODUCT-CAPTURE-RESUME")
    diagnostic = {
        "status": outcome.status,
        "committed_count": outcome.committed_count,
        "error_count": outcome.error_count,
        "errors": _checkpoint(runtime_case.connection, outcome.run_id).get("errors"),
    }
    if outcome.status != "complete":
        diagnostic_path = tmp_path / "capture-resume-errors.json"
        diagnostic_path.write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        diagnostic["diagnostic_path"] = str(diagnostic_path)
    assert outcome.status == "complete", json.dumps(diagnostic, ensure_ascii=False)

    state = _checkpoint(runtime_case.connection, "PRODUCT-CAPTURE-RESUME")
    entry = state["selected_products"][0]
    expected_page_ref = entry.pop("page_ref")
    page_directory = product_dir(
        runtime_case.data_root,
        "PRODUCT-CAPTURE-RESUME",
        entry["shop_id"],
        entry["item_id"],
    )
    shutil.rmtree(page_directory)
    entry["status"] = "failed"
    entry["ocr_state"] = "failed"
    entry["ocr_error_code"] = "product_page_capture_failed"
    state["product_page_capture_failures"] = [
        {
            "offer_key": entry["offer_key"],
            "page_ref": expected_page_ref,
            "source_attempt_id": entry["source_attempt_id"],
            "code": "product_page_capture_failed",
        }
    ]
    orchestrator._save_state(
        runtime_case.connection, "PRODUCT-CAPTURE-RESUME", state
    )
    capture = _FixtureProductPageCapture(runtime_case.data_root)
    ports = replace(
        bundle.ports,
        product_page_ocr=ocr,
        product_page_capture=capture,
    )

    recovered = orchestrator._recover_product_page_capture_failures(
        runtime_case.connection,
        run_id="PRODUCT-CAPTURE-RESUME",
        target_date="2026-10-23",
        state=state,
        ports=ports,
        data_root=runtime_case.data_root,
        clock=runtime_case.clock,
    )

    assert recovered == 1
    assert len(capture.requests) == 1
    assert capture.requests[0].item_url == runtime_case.connection.execute(
        "SELECT item_url FROM source_post WHERE source_key=?",
        (entry["source_key"],),
    ).fetchone()[0]
    assert entry["status"] == "selected"
    assert entry["page_ref"] == expected_page_ref
    assert "ocr_state" not in entry
    assert "ocr_error_code" not in entry
    assert state["product_page_capture_failures"] == []
    assert state["product_page_capture_recovery"] == {
        "capture_adapter_available": True,
        "selected_failed_before": 1,
        "selected_recovered": 1,
        "selected_failed_after": 0,
        "unselected_recovered": 0,
    }
    assert load_product_page_inputs(
        runtime_case.data_root,
        "PRODUCT-CAPTURE-RESUME",
        entry["shop_id"],
        entry["item_id"],
        expected_ref=expected_page_ref,
    )["html_state"] == "captured"


def test_resume_selects_a_recaptured_eligible_candidate_without_room_search(
    runtime_case: RuntimeCase, monkeypatch
):
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case)
    ocr = _FixtureOCR()
    outcome = _run(runtime_case, bundle, ocr, run_id="PRODUCT-CANDIDATE-RECOVERY")
    assert outcome.status == "complete"

    run_id = "PRODUCT-CANDIDATE-RECOVERY"
    state = _checkpoint(runtime_case.connection, run_id)
    selected = {entry["offer_key"] for entry in state["selected_products"]}
    theme_id = state["selected_products"][0]["theme_id"]
    state["theme_targets"][theme_id] = 6
    state["product_page_capture_failures"] = []
    candidate = None
    for task in state["tasks"]:
        if task["theme_id"] != theme_id:
            continue
        attempt_id = orchestrator._task_attempt_id(run_id, task["task_number"], 1)
        decision = runtime_case.connection.execute(
            "SELECT source_key,offer_key FROM candidate_decision "
            "WHERE run_id=? AND stage=? AND rule_version='selection-v1' "
            "AND primary_reason='eligible' ORDER BY source_key",
            (run_id, f"daily-search:{attempt_id}"),
        ).fetchall()
        candidate = next(
            (
                (attempt_id, str(row["source_key"]), str(row["offer_key"]))
                for row in decision
                if row["offer_key"] is not None and row["offer_key"] not in selected
            ),
            None,
        )
        if candidate is not None:
            break
    assert candidate is not None
    attempt_id, source_key, offer_key = candidate
    source = runtime_case.connection.execute(
        "SELECT item_url,order_index FROM source_post WHERE source_key=?",
        (source_key,),
    ).fetchone()
    offer = runtime_case.connection.execute(
        "SELECT shop_id,item_id FROM offer WHERE offer_key=?", (offer_key,)
    ).fetchone()
    assert source is not None and offer is not None
    candidate_page_dir = product_dir(
        runtime_case.data_root,
        run_id,
        offer["shop_id"],
        offer["item_id"],
    )
    if candidate_page_dir.exists():
        shutil.rmtree(candidate_page_dir)
    state["product_page_capture_failures"] = [
        {
            "offer_key": offer_key,
            "source_attempt_id": attempt_id,
            "code": "product_page_capture_failed",
        }
    ]
    orchestrator._save_state(runtime_case.connection, run_id, state)
    capture = _FixtureProductPageCapture(runtime_case.data_root)
    ports = replace(
        bundle.ports,
        product_page_ocr=ocr,
        product_page_capture=capture,
    )
    monkeypatch.setattr(orchestrator, "DAILY_TARGET", 16)

    original_save_state = orchestrator._save_state
    interrupted = False

    def interrupt_after_recovery_checkpoint(connection, saved_run_id, saved_state):
        nonlocal interrupted
        original_save_state(connection, saved_run_id, saved_state)
        if saved_run_id != run_id or interrupted:
            return
        persisted = _checkpoint(connection, run_id)
        persisted_candidate_selected = any(
            entry.get("offer_key") == offer_key
            and entry.get("status") == "selected"
            for entry in persisted["selected_products"]
        )
        persisted_failure = any(
            item.get("offer_key") == offer_key
            for item in persisted["product_page_capture_failures"]
        )
        if persisted_candidate_selected or not persisted_failure:
            interrupted = True
            raise KeyboardInterrupt("fixture recovery checkpoint interruption")

    monkeypatch.setattr(orchestrator, "_save_state", interrupt_after_recovery_checkpoint)

    with pytest.raises(KeyboardInterrupt, match="fixture recovery checkpoint interruption"):
        orchestrator._recover_product_page_capture_failures(
            runtime_case.connection,
            run_id=run_id,
            target_date="2026-10-23",
            state=state,
            ports=ports,
            data_root=runtime_case.data_root,
            clock=runtime_case.clock,
        )

    monkeypatch.setattr(orchestrator, "_save_state", original_save_state)
    state = _checkpoint(runtime_case.connection, run_id)
    resumed = orchestrator._recover_product_page_capture_failures(
        runtime_case.connection,
        run_id=run_id,
        target_date="2026-10-23",
        state=state,
        ports=ports,
        data_root=runtime_case.data_root,
        clock=runtime_case.clock,
    )

    assert interrupted
    assert resumed in {0, 1}
    assert len(capture.requests) in {1, 2}
    assert capture.requests[0].item_url == source["item_url"]
    assert any(
        entry.get("offer_key") == offer_key and entry.get("status") == "selected"
        for entry in state["selected_products"]
    )
    assert state["product_page_capture_failures"] == []


def test_resume_reuses_selected_registry_and_completed_ocr(
    runtime_case: RuntimeCase, monkeypatch
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case)
    ocr = _FixtureOCR()
    original_generate = orchestrator.generate_and_commit_copy
    calls = 0

    def interrupt_after_four_commits(connection, spec, runner, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("fixture copy interruption")
        return original_generate(connection, spec, runner, **kwargs)

    monkeypatch.setattr(
        orchestrator, "generate_and_commit_copy", interrupt_after_four_commits
    )
    first = _run(runtime_case, bundle, ocr, run_id="PRODUCT-RESUME-15")
    assert first.status == "partial"
    assert first.committed_count == 4
    assert len(ocr.calls) == 15
    saved = _checkpoint(runtime_case.connection, "PRODUCT-RESUME-15")
    assert len(saved["selected_products"]) == 15
    assert saved["active_resume_errors"]

    monkeypatch.setattr(orchestrator, "generate_and_commit_copy", original_generate)
    second = _run(runtime_case, bundle, ocr, run_id="PRODUCT-RESUME-15")

    assert second.status == "complete"
    assert second.resumed is True
    assert second.committed_count == 15
    assert second.error_count == 0
    assert second.primary_error is None
    assert len(ocr.calls) == 15
    assert len(bundle.runner.requests) == 15
    final_state = _checkpoint(runtime_case.connection, "PRODUCT-RESUME-15")
    assert [item["offer_key"] for item in final_state["selected_products"]] == [
        item["offer_key"] for item in saved["selected_products"]
    ]
    assert [item["ocr_ref"] for item in final_state["selected_products"]] == [
        item["ocr_ref"] for item in saved["selected_products"]
    ]
    assert all(item["status"] == "committed" for item in final_state["selected_products"])
    assert final_state["resolved_error_history"][-1]["resolution"] == (
        "same_run_target_reached_after_resume"
    )


def test_resume_rejects_legacy_selected_checkpoint_with_capture_failed_page(
    runtime_case: RuntimeCase,
) -> None:
    bundle = _make_ports(runtime_case, posts_per_search=25)
    _enable_product_page_pipeline(bundle, runtime_case)
    ocr = _FixtureOCR()
    run_id = "PRODUCT-LEGACY-CAPTURE-FAIL"

    initial = _run(runtime_case, bundle, ocr, run_id=run_id)
    assert initial.status == "complete"
    state = _checkpoint(runtime_case.connection, run_id)

    shop_id = "legacy-shop"
    item_id = "legacy-item"
    legacy_offer_key = f"{shop_id}:{item_id}"
    legacy_page = write_page(
        runtime_case.data_root,
        run_id,
        shop_id,
        item_id,
        images=[],
        source_evidence_ref="fixture:evidence:legacy",
    )
    entry = {
        "offer_key": legacy_offer_key,
        "run_id": run_id,
        "theme_id": "legacy-theme",
        "selection_order": 16,
        "shop_id": shop_id,
        "item_id": item_id,
        "page_ref": legacy_page["page_ref"],
        "source_evidence_ref": "fixture:evidence:legacy",
        "status": "ocr_complete",
    }
    state["selected_products"].append(entry)
    page_path = product_dir(runtime_case.data_root, run_id, shop_id, item_id) / "page.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    page["html_state"] = "capture_failed"
    page["images"] = []
    page_path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    orchestrator._save_state(runtime_case.connection, run_id, state)

    ocr_call_count = len(ocr.calls)
    reusable = orchestrator._ocr_selected_products(
        runtime_case.connection,
        run_id=run_id,
        target_date="2026-10-23",
        state=state,
        ports=bundle.ports,
        data_root=runtime_case.data_root,
        clock=runtime_case.clock,
    )

    assert reusable == []
    assert len(ocr.calls) == ocr_call_count
    saved = _checkpoint(runtime_case.connection, run_id)
    failed = next(
        item
        for item in saved["selected_products"]
        if item["offer_key"] == legacy_offer_key
    )
    assert failed["status"] == "failed"
    assert failed["ocr_error_code"] == "product_page_capture_failed"
    assert any(
        item["offer_key"] == legacy_offer_key
        and item["phase"] == "product_page_capture"
        for item in saved["active_product_slot_failures"]
    )


def test_unrelated_commit_does_not_resolve_active_slot_failure() -> None:
    failure = {
        "offer_key": "FAILED-OFFER",
        "phase": "product_page_ocr",
        "code": "ocr_failed",
        "page_ref": "page:failed",
        "selection_order": 1,
        "slot_id": "slot-001",
        "failed_at": "2026-10-23T13:00:00+09:00",
    }
    error = {
        "phase": failure["phase"],
        "code": failure["code"],
        "references": {
            "run_id": "PRODUCT-SLOT-CORRELATION",
            "offer_key": failure["offer_key"],
            "artifact_ref": failure["page_ref"],
        },
    }
    state = {
        "active_product_slot_failures": [failure.copy()],
        "resolved_product_slot_failures": [],
        "errors": [error.copy()],
        "error_count": 1,
        "primary_error": error.copy(),
    }

    orchestrator._resolve_product_slot_failure_on_commit(
        state,
        committed_entry={
            "offer_key": "UNRELATED-OFFER",
            "page_ref": "page:unrelated",
            "selection_order": 2,
        },
        committed_at="2026-10-23T13:01:00+09:00",
    )

    assert state["active_product_slot_failures"] == [failure]
    assert state["resolved_product_slot_failures"] == []
    assert state["error_count"] == 1
    assert state["errors"] == [error]
    assert state["primary_error"] == error


def test_replacement_slots_and_commits_require_matching_theme() -> None:
    failure = {
        "offer_key": "FAILED-OFFER",
        "phase": "copy",
        "code": "fixture_copy_failure",
        "page_ref": "page:failed",
        "selection_order": 1,
        "slot_id": "PRODUCT-THEME-SLOT:slot:001",
        "failed_at": "2026-10-23T13:00:00+09:00",
    }
    state = {
        "active_product_slot_failures": [failure.copy()],
        "resolved_product_slot_failures": [],
        "errors": [
            {
                "phase": "copy",
                "code": "fixture_copy_failure",
                "references": {
                    "run_id": "PRODUCT-THEME-SLOT",
                    "offer_key": "FAILED-OFFER",
                    "artifact_ref": "page:failed",
                },
            }
        ],
        "error_count": 1,
        "primary_error": None,
        "selected_products": [
            {"offer_key": "FAILED-OFFER", "theme_id": "theme-a"}
        ],
    }
    state["primary_error"] = state["errors"][0].copy()
    wrong_theme = {
        "run_id": "PRODUCT-THEME-SLOT",
        "offer_key": "REPLACEMENT-OFFER",
        "theme_id": "theme-b",
        "selection_order": 2,
        "page_ref": "page:replacement",
    }

    orchestrator._assign_product_slot_replacement(state, wrong_theme)

    assert "replacement_slot_id" not in wrong_theme
    assert "replacement_offer_key" not in state["active_product_slot_failures"][0]

    matching_theme = {**wrong_theme, "theme_id": "theme-a"}
    orchestrator._assign_product_slot_replacement(state, matching_theme)
    assert matching_theme["replacement_slot_id"] == failure["slot_id"]

    orchestrator._resolve_product_slot_failure_on_commit(
        state,
        committed_entry={**matching_theme, "theme_id": "theme-b"},
        committed_at="2026-10-23T13:01:00+09:00",
    )

    assert state["active_product_slot_failures"]
    assert state["resolved_product_slot_failures"] == []
    assert state["error_count"] == 1

    orchestrator._resolve_product_slot_failure_on_commit(
        state,
        committed_entry=matching_theme,
        committed_at="2026-10-23T13:02:00+09:00",
    )

    assert state["active_product_slot_failures"] == []
    assert state["resolved_product_slot_failures"][0]["resolution"] == (
        "impact_recovered_by_supplement"
    )
    assert state["error_count"] == 0
