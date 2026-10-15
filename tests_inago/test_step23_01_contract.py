"""Failing, behavior-level reproducers for gaps found before STEP23 work."""

from __future__ import annotations

import re
from pathlib import Path

from automation.inago.acceptance import load_acceptance_registry
from automation.inago.dashboard_export import render_standalone_dashboard_html
from automation.inago.dashboard_history import render_dashboard_history_html


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_TEST_PATH = PROJECT_ROOT / "tests_inago" / "test_step22_ui.py"
FILE_BROWSER_TEST_PATHS = (
    PROJECT_ROOT / "tests_inago" / "test_step23_03_html.py",
    PROJECT_ROOT / "tests_inago" / "test_step23_04_dates.py",
)

DAILY_ACCEPTANCE_IDS = tuple(f"H-{number:02d}" for number in range(1, 9))


def test_daily_completion_acceptance_ids_are_registered() -> None:
    registry = load_acceptance_registry(PROJECT_ROOT)

    assert set(DAILY_ACCEPTANCE_IDS).issubset(registry)
    for case_id in DAILY_ACCEPTANCE_IDS:
        assert registry[case_id].expected_observation.strip()


def test_standalone_dashboard_embeds_data_and_display_assets() -> None:
    html = render_standalone_dashboard_html(
        {
            "latest_run": None,
            "items": [
                {
                    "draft_status": "committed",
                    "product_name": "匿名fixture商品",
                    "product_url": "https://item.rakuten.co.jp/shop/item/",
                    "body": "匿名fixture本文",
                    "draft_version": 1,
                    "target_date": "2026-10-15",
                }
            ],
        }
    )

    assert 'id="dashboard-data"' in html
    assert "dashboard-assets/app.js" not in html
    assert "dashboard-assets/style.css" not in html


def test_dashboard_has_date_navigation_and_day_scoped_rendering() -> None:
    html = render_dashboard_history_html(
        {
            "days": [
                {
                    "target_date": "2026-10-15",
                    "run_status": "complete",
                    "items": [],
                }
            ]
        }
    )

    assert re.search(r'<nav[^>]+aria-label="日付を選択"', html)
    assert "data-target-date" in html
    assert "function renderDay(day)" in html


def test_browser_e2e_opens_generated_dashboard_as_a_file() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (UI_TEST_PATH, *FILE_BROWSER_TEST_PATHS)
        if path.is_file()
    )

    assert "as_uri()" in source
    assert "output_path.resolve().as_uri()" in source
    assert re.search(r"page\.goto\([^\n]*fileUrl", source)
