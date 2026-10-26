"""Owner-requested copy/check/open action in an isolated real browser."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from tests_inago.test_history_web import running_history, ticket


_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "mode", ["success", "clipboard-failure", "offline", "missing-url"]
)
def test_combined_copy_check_and_open_preserves_on_state(tmp_path, mode):
    evidence = (
        _ROOT
        / "非公開作業データ/イナゴ実装試験/history-combined-action-20261026"
        / tmp_path.parent.name
        / tmp_path.name
    )
    evidence.mkdir(parents=True, exist_ok=True)
    with running_history(tmp_path / "runtime") as app:
        connection = app.connect()
        try:
            connection.execute(
                "UPDATE offer SET url=? WHERE offer_key='shop_test:item_A01'",
                (
                    (
                        "http://example.invalid/insecure-product"
                        if mode == "missing-url"
                        else "https://example.invalid/combined-product"
                    ),
                ),
            )
        finally:
            connection.close()
        environment = os.environ.copy()
        environment.update(
            {
                "INAGO_TEST_HISTORY_URL": app.origin
                + "/posting-history.html#launch="
                + ticket(app),
                "INAGO_TEST_HISTORY_EVIDENCE": str(evidence),
                "INAGO_TEST_COMBINED_MODE": mode,
            }
        )
        result = subprocess.run(
            ["node", str(_ROOT / "tests_inago/fixtures/history_combined_browser.cjs")],
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed["mode"] == mode
        assert observed["new_tabs"] == (
            0 if mode == "missing-url" else 2 if mode == "success" else 1
        )
        connection = app.connect()
        try:
            rows = connection.execute(
                "SELECT action, receipt_revision FROM human_action ORDER BY receipt_revision"
            ).fetchall()
            assert [tuple(row) for row in rows] == (
                [] if mode == "missing-url" else [("posted", 1)]
            )
        finally:
            connection.close()
