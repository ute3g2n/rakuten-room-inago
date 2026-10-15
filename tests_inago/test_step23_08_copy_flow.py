from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from automation.inago.dashboard_history import export_dashboard_history_html
from automation.inago.orchestrator import run_daily
from automation.inago.production_runtime import _make_copy_spec_factory
from tests_inago.test_step13 import (
    PREPARATION_ROOT,
    SETTINGS_VERSION,
    _make_ports,
    runtime_case,
)
from tests_inago.test_step23_07_intake import (
    _EvidenceSelectingRunner,
    _MultiFactOfferIntake,
)


def test_daily_copy_commit_is_persisted_to_html_with_one_native_copy_job(
    runtime_case,
) -> None:
    basic = json.loads(
        (PREPARATION_ROOT / "初期設定引渡し.json").read_text(encoding="utf-8")
    )
    settings_runtime = SimpleNamespace(
        active_basic_version=SETTINGS_VERSION,
        themes=tuple(basic["themes"]),
    )
    production_factory = _make_copy_spec_factory(None, settings_runtime)
    intake = _MultiFactOfferIntake()
    bundle = _make_ports(
        runtime_case,
        posts_per_search=1,
        skip_first_theme_seed=True,
        offer_intake=intake,
    )
    ai_root = _EvidenceSelectingRunner(bundle.runner)
    created_specs = []

    def make_copy_spec(connection, **kwargs):
        spec = production_factory(connection, **kwargs)
        if spec is not None:
            created_specs.append(spec)
        return spec

    bundle.ports = replace(
        bundle.ports,
        copy_spec_factory=make_copy_spec,
        copy_runner=ai_root,
    )
    html_path = runtime_case.data_root / "daily-history.html"
    exports = []

    def export_after_commit(connection) -> None:
        exports.append(
            export_dashboard_history_html(
                connection,
                output_path=html_path,
                runtime_root=runtime_case.data_root,
            )
        )
        runtime_case.clock.current = datetime.fromisoformat(
            "2026-10-12T17:45:00+09:00"
        )

    outcome = run_daily(
        runtime_case.connection,
        data_root=runtime_case.data_root,
        ports=bundle.ports,
        clock=runtime_case.clock,
        run_id_factory=lambda _date: "STEP23-08-RUN",
        seed_factory=lambda: "STEP23-08-SEED",
        on_draft_commit=export_after_commit,
    )

    assert outcome.committed_count == 1
    assert len(created_specs) == 1
    spec = created_specs[0]
    assert len(ai_root.requests) == 1
    request = ai_root.requests[0]
    assert request.run_id == spec.run_id == outcome.run_id
    assert request.target_date == spec.target_date == outcome.target_date
    assert request.settings_version == spec.settings_version == SETTINGS_VERSION
    assert request.copy_targets[0].offer_key == spec.offer_key
    assert request.copy_targets[0].draft_version == spec.draft_version
    assert set(spec.evidence_refs).issubset(request.input_refs)
    assert request.evidence_refs == spec.evidence_refs

    persisted = runtime_case.connection.execute(
        """
        SELECT agent_id, run_id, target_date, settings_version
          FROM ai_job WHERE run_id=? ORDER BY agent_id
        """,
        ("STEP23-08-RUN",),
    ).fetchall()
    assert [row[0] for row in persisted] == ["AG-COPY", "AI-O"]
    assert all(row[1] == outcome.run_id for row in persisted)
    assert all(row[2] == outcome.target_date for row in persisted)
    assert all(row[3] == SETTINGS_VERSION for row in persisted)

    run = runtime_case.connection.execute(
        "SELECT committed_count FROM run WHERE run_id=?", (outcome.run_id,)
    ).fetchone()
    assert run[0] == 1
    assert runtime_case.connection.execute(
        "SELECT COUNT(*) FROM reservation WHERE offer_key=? AND status='active'",
        (spec.offer_key,),
    ).fetchone()[0] == 1
    assert len(exports) == 1
    assert exports[0].draft_count == 1
    html = html_path.read_text(encoding="utf-8")
    draft = runtime_case.connection.execute(
        "SELECT body FROM draft WHERE run_id=? AND offer_key=?",
        (outcome.run_id, spec.offer_key),
    ).fetchone()
    assert draft is not None
    embedded = re.search(
        r'<script id="dashboard-history-data" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert embedded is not None
    history = json.loads(embedded.group(1))
    assert history["days"][0]["items"][0]["body"] == draft[0]
