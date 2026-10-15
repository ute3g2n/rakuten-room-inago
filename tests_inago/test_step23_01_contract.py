"""Failing, behavior-level reproducers for gaps found before STEP23 work."""

from __future__ import annotations

import re
from pathlib import Path

from automation.inago.acceptance import load_acceptance_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "automation" / "inago" / "templates" / "posting-dashboard.html"
APP_PATH = PROJECT_ROOT / "rakuten-room-inago" / "dashboard-assets" / "app.js"
UI_TEST_PATH = PROJECT_ROOT / "tests_inago" / "test_step22_ui.py"

DAILY_ACCEPTANCE_IDS = tuple(f"H-{number:02d}" for number in range(1, 9))


def test_daily_completion_acceptance_ids_are_registered() -> None:
    registry = load_acceptance_registry(PROJECT_ROOT)

    assert set(DAILY_ACCEPTANCE_IDS).issubset(registry)
    for case_id in DAILY_ACCEPTANCE_IDS:
        assert registry[case_id].expected_observation.strip()


def test_standalone_dashboard_embeds_data_and_display_assets() -> None:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'id="dashboard-data"' in html
    assert "dashboard-assets/app.js" not in html
    assert "dashboard-assets/style.css" not in html


def test_dashboard_has_date_navigation_and_day_scoped_rendering() -> None:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    app = APP_PATH.read_text(encoding="utf-8")

    assert re.search(r'<nav[^>]+aria-label="日付を選択"', html)
    assert "data-target-date" in html
    assert "data-target-date" in app


def test_browser_e2e_opens_generated_dashboard_as_a_file() -> None:
    source = UI_TEST_PATH.read_text(encoding="utf-8")

    assert "as_uri()" in source
    assert re.search(r"page\.goto\([^\n]*as_uri\(\)", source)
