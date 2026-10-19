"""Static U17 contract checks for maintenance requirements and references."""

from __future__ import annotations

import re
from pathlib import Path

from automation.inago.maintenance.ai_adapter import PARENT_ROLES
from automation.inago.maintenance.evidence import EVIDENCE_CLASS_IDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_c60_contract():
    maintenance_contract = (
        PROJECT_ROOT
        / "docs/specifications/inago/AT-MAINT-01_保守対応要件受入契約.md"
    ).read_text(encoding="utf-8")
    tracking = (
        PROJECT_ROOT / "docs/specifications/inago/要件追跡表.md"
    ).read_text(encoding="utf-8")
    trace_contract = (
        PROJECT_ROOT / "docs/specifications/inago/AT-TRACE-01_共通トレース契約.md"
    ).read_text(encoding="utf-8")
    settings = (
        PROJECT_ROOT / "docs/specifications/inago/保守運用設定仕様.md"
    ).read_text(encoding="utf-8")
    outputs = (
        PROJECT_ROOT / "docs/operations/日次タスクのトレース・ログ出力一覧.md"
    ).read_text(encoding="utf-8")
    plan = (
        PROJECT_ROOT
        / "plans/projects/maintenance/文書整理と日次自動不具合対応_実行計画_2026-10-17.md"
    ).read_text(encoding="utf-8")
    review_skill = (
        PROJECT_ROOT / ".agents/skills/common-review/SKILL.md"
    ).read_text(encoding="utf-8")
    planning_skill = (
        PROJECT_ROOT / ".agents/skills/common-execution-planning/SKILL.md"
    ).read_text(encoding="utf-8")
    requirements_html = (
        PROJECT_ROOT / "docs/specifications/inago/イナゴスキル要件定義書.html"
    ).read_text(encoding="utf-8")

    accepted_ids = set(re.findall(r"^\| MA-(\d{3}) \|", maintenance_contract, re.M))
    assert accepted_ids == {f"{index:03d}" for index in range(1, 29)}
    traced_ids = set(re.findall(r"^\| MR-(\d{3}) / MA-\d{3} \|", tracking, re.M))
    assert traced_ids == {f"{index:03d}" for index in range(1, 29)}

    expected_skills = {
        "rr-inago-incident-investigation",
        "common-execution-planning",
        "common-implementation",
        "common-testing",
        "common-review",
        "rr-inago-incident-report",
    }
    skill_root = PROJECT_ROOT / ".agents/skills"
    assert len(expected_skills) == 6
    assert all((skill_root / name / "SKILL.md").is_file() for name in expected_skills)
    role_names = {role for roles in PARENT_ROLES.values() for role in roles}
    assert len(PARENT_ROLES) == 2
    assert len(role_names) == 9
    assert all((PROJECT_ROOT / ".codex/agents" / f"{role}.toml").is_file() for role in role_names)
    assert len(EVIDENCE_CLASS_IDS) == 26

    for document in (maintenance_contract, trace_contract, settings, outputs, plan):
        assert "MN04" in document
        assert "maintenance/logs" in document
        assert "90" in document
    assert "4 MiB" in settings
    assert "JSONL ファイル数 | 上限なし" in settings
    assert "maintenance/logs/<実行日>/<maintenance_id>.jsonl" in outputs
    assert "L02とは別" in outputs
    assert "retention_expires_at" in trace_contract
    assert "ファイル数上限は設けない" in trace_contract
    assert "daily_file_quota" not in trace_contract

    review_policy = "テストコード自体は独立レビューしない"
    debug_policy = "テストコードのバグが実行やデバッグ中に判明した場合"
    assert review_policy in review_skill
    assert review_policy in planning_skill
    assert debug_policy in planning_skill
    assert "テストコードの独立レビューは行わない" in plan
    assert "テストコードの独立レビューは行わない" in maintenance_contract

    requirements_source = re.search(
        r"<script[^>]*id=[\"']requirements-source[\"'][^>]*>(.*?)</script>",
        requirements_html,
        re.DOTALL,
    )
    assert requirements_source is not None
    source_text = requirements_source.group(1)
    assert "MR-013" in source_text and "MR-014" in source_text
    assert "C18" in source_text and "C58" in source_text and "C60" in source_text
