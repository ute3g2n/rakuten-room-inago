"""Saved-query recovery keeps the replenishment theme and its captured inputs."""

from dataclasses import replace

from automation.inago import orchestrator
from tests_inago.test_step13 import _make_ports
from tests_inago.test_product_page_selection import (
    _FixtureOCR,
    _FixtureProductPageCapture,
    _checkpoint,
    _enable_product_page_pipeline,
    _run,
    runtime_case,
)


def test_recovery_of_two_themes_from_one_attempt_keeps_later_group_inputs(
    runtime_case, monkeypatch
):
    bundle = _make_ports(runtime_case, posts_per_search=7)
    _enable_product_page_pipeline(bundle, runtime_case)

    def interrupt_before_copy(*_args, **_kwargs):
        raise RuntimeError("fixture interruption before copy")

    monkeypatch.setattr(orchestrator, "_process_selected_copies", interrupt_before_copy)
    run_id = "FALLBACK-RECOVERY-GROUPS"
    first = _run(runtime_case, bundle, _FixtureOCR(), run_id=run_id)
    assert first.committed_count == 0
    state = _checkpoint(runtime_case.connection, run_id)
    theme_a = state["selected_products"][0]["theme_id"]
    theme_b = next(t for t in state["initial_theme_ids"] if t != theme_a)
    state["selected_products"][4]["status"] = "insufficient_information"
    state["theme_targets"][theme_a] = 5
    state["theme_targets"][theme_b] = 1
    attempt_id = bundle.room.requests[0].attempt_id
    source_rows = runtime_case.connection.execute(
        "SELECT source_key,evidence_ref,item_url FROM source_post "
        "WHERE attempt_id=? ORDER BY order_index", (attempt_id,)
    ).fetchall()
    expected = {}
    state["product_page_capture_failures"] = []
    for index, theme in [(5, theme_a), (6, theme_b)]:
        row = source_rows[index]
        parts = row["item_url"].rstrip("/").split("/")
        offer_key = f"{parts[-2]}:{parts[-1]}"
        fact = bundle.room.product_fact(row["evidence_ref"])
        state["product_page_capture_failures"].append({
            "offer_key": offer_key, "theme_id": theme,
            "source_attempt_id": attempt_id,
            "page_ref": fact["product_page_ref"], "code": "product_page_capture_failed",
        })
        expected[offer_key] = theme
    orchestrator._save_state(runtime_case.connection, run_id, state)
    capture = _FixtureProductPageCapture(runtime_case.data_root)

    orchestrator._recover_product_page_capture_failures(
        runtime_case.connection, run_id=run_id, target_date=first.target_date,
        state=state, ports=replace(bundle.ports, product_page_capture=capture),
        data_root=runtime_case.data_root, clock=runtime_case.clock,
    )

    selected = {x["offer_key"]: x for x in state["selected_products"]}
    for offer_key, theme in expected.items():
        assert selected[offer_key]["theme_id"] == theme
        assert selected[offer_key]["status"] == "selected"
    assert state["product_page_capture_failures"] == []
    # Existing captured pages are reusable; recovery need not fetch them again.
    assert capture.requests == []
    assert len(bundle.room.requests) == 1
