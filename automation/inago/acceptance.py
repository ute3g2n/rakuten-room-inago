"""STEP 19 fixture acceptance registry and offline verification helpers.

This module reads the current plan and source documents. It never starts a
production run, opens a production database, contacts an external service, or
changes the adopted state of preparation inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAN_RELATIVE_PATH = Path("plan/イナゴスキル実装・テスト実行計画.md")
REQUIREMENTS_RELATIVE_PATH = Path("plan/イナゴ戦略/イナゴスキル要件定義書.html")
TRACE_RELATIVE_PATH = Path("plan/イナゴ戦略/要件追跡表.md")
DECISION_RELATIVE_PATH = Path("plan/イナゴ戦略/意思決定台帳.md")
PREPARATION_ROOT = Path("plan/イナゴスキル事前準備")
SIMULATION_ROOT = Path("plan/イナゴ戦略/simulation")
PRIVATE_EVIDENCE_RELATIVE_PATH = Path("非公開作業データ/イナゴ実装試験/step-19")

_REQUIREMENT_PATTERN = re.compile(r"\bIR-\d{3}\b")
_ACCEPTANCE_PATTERN = re.compile(r"\bAT-(?:\d{3}|S\d{2}|A\d{2})\b")
_X_TEST_STEPS: dict[str, tuple[int, ...]] = {
    "X-01": (4, 9, 12),
    "X-02": (9, 12),
    "X-03": (3, 12),
    "X-04": (5, 6),
    "X-05": (17,),
    "X-06": (3, 13),
    "X-07": (),
    "X-08": (13, 16),
}

DOCUMENT_CASE_IDS = ("C-003", "C-027")
_MATRIX_CASE_IDS = (
    *(f"C-{number:03d}" for number in range(1, 29)),
    *(f"S-{number:02d}" for number in range(1, 32)),
    *(f"A-{number:02d}" for number in range(1, 11)),
)
EXPECTED_CASE_IDS = (*_MATRIX_CASE_IDS, *(f"X-{number:02d}" for number in range(1, 9)))
FIXTURE_CASE_IDS = tuple(
    case_id for case_id in EXPECTED_CASE_IDS if case_id not in DOCUMENT_CASE_IDS
)


@dataclass(frozen=True)
class AcceptanceCase:
    """One plan acceptance row and the existing offline test modules it covers."""

    case_id: str
    name: str
    requirement_refs: tuple[str, ...]
    acceptance_refs: tuple[str, ...]
    implementation_steps: tuple[int, ...]
    test_refs: tuple[str, ...]
    expected_observation: str
    phase: str
    module_paths: tuple[str, ...]
    live_evidence: str


@dataclass(frozen=True)
class FixtureRunResult:
    """Aggregate result for the selected case modules executed in an isolated child pytest."""

    selected_case_ids: tuple[str, ...]
    module_paths: tuple[str, ...]
    exit_code: int
    collected_test_count: int
    failures: tuple[str, ...]
    command: tuple[str, ...]
    evidence_directory: str


class _VisibleHtmlText(HTMLParser):
    """Collect HTML text, including collapsed details, while ignoring scripts/styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._ignored and tag == self._ignored[-1]:
            self._ignored.pop()

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _next_evidence_directory(project_root: Path, prefix: str) -> Path:
    configured_root = os.environ.get("INAGO_STEP19_EVIDENCE_ROOT")
    evidence_root = (
        Path(configured_root)
        if configured_root
        else project_root / PRIVATE_EVIDENCE_RELATIVE_PATH
    )
    evidence_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%dT%H%M%S")
    base_name = f"{prefix}-{timestamp}"
    candidate = evidence_root / base_name
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = evidence_root / f"{base_name}-{suffix:02d}"
    candidate.mkdir()
    return candidate


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _extract_table_cases(
    plan_text: str, project_root: Path
) -> dict[str, AcceptanceCase]:
    heading = "### 10.2 IR・受入・実装STEP・テストSTEP対応表"
    try:
        table_text = plan_text.split(heading, 1)[1].split("\n### ", 1)[0]
    except IndexError as error:
        raise ValueError("計画書の10.2受入対応表を特定できません") from error

    cases: dict[str, AcceptanceCase] = {}
    for line in table_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 5 or cells[0] == "要件":
            continue
        case_refs = [
            case_id
            for case_id in re.findall(r"\b(?:C-\d{3}|S-\d{2}|A-\d{2})\b", cells[3])
        ]
        if len(case_refs) != 1:
            continue
        case_id = case_refs[0]
        implementation_steps = tuple(
            int(step) for step in re.findall(r"(?<!\d)(\d{2})(?!\d)", cells[2])
        )
        module_paths = _existing_test_modules(project_root, implementation_steps)
        cases[case_id] = AcceptanceCase(
            case_id=case_id,
            name=f"{case_id} 対応表ケース",
            requirement_refs=tuple(
                dict.fromkeys(_REQUIREMENT_PATTERN.findall(cells[0]))
            ),
            acceptance_refs=tuple(dict.fromkeys(_ACCEPTANCE_PATTERN.findall(cells[1]))),
            implementation_steps=implementation_steps,
            test_refs=(case_id,),
            expected_observation=cells[4],
            phase=_phase_for_matrix_case(case_id),
            module_paths=module_paths,
            live_evidence=_live_status(case_id),
        )
    return cases


def _extract_extended_cases(
    plan_text: str, project_root: Path
) -> dict[str, AcceptanceCase]:
    heading = "### 10.4 追加の境界・実接続ケース"
    try:
        extended_text = plan_text.split(heading, 1)[1].split("\n### ", 1)[0]
    except IndexError as error:
        raise ValueError("計画書の10.4追加ケースを特定できません") from error

    headings = list(re.finditer(r"^#### (X-\d{2}) (.+)$", extended_text, re.MULTILINE))
    cases: dict[str, AcceptanceCase] = {}
    for index, match in enumerate(headings):
        case_id, name = match.groups()
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(extended_text)
        )
        body = extended_text[match.end() : end]
        module_paths = _existing_test_modules(project_root, _X_TEST_STEPS[case_id])
        objective_match = re.search(r"\*\*対応・目的\*\*：([^\n]+)", body)
        objective = objective_match.group(1) if objective_match else ""
        expected_parts = re.findall(
            r"\*\*期待(?:出力|DB・件数・予約・版・ログ)\*\*：([^\n]+)", body
        )
        cases[case_id] = AcceptanceCase(
            case_id=case_id,
            name=name.strip(),
            requirement_refs=tuple(
                dict.fromkeys(_REQUIREMENT_PATTERN.findall(objective))
            ),
            acceptance_refs=tuple(
                dict.fromkeys(_ACCEPTANCE_PATTERN.findall(objective))
            ),
            implementation_steps=_X_TEST_STEPS[case_id],
            test_refs=(case_id,),
            expected_observation=" / ".join(expected_parts) or objective,
            phase="arithmetic" if case_id == "X-07" else "fixture_partial",
            module_paths=module_paths,
            live_evidence=_live_status(case_id),
        )
    return cases


def _existing_test_modules(
    project_root: Path, step_numbers: Iterable[int]
) -> tuple[str, ...]:
    paths: list[str] = []
    for step_number in step_numbers:
        if step_number == 21:
            # Current Q1.4 contracts are split by responsibility within STEP21.
            current = sorted((project_root / "tests_inago").glob("test_step21_*.py"))
            if not current:
                raise FileNotFoundError("STEP21 の新仕様契約テストがありません")
            for path in current:
                normalized = path.relative_to(project_root).as_posix()
                if normalized not in paths:
                    paths.append(normalized)
            continue
        relative_path = Path("tests_inago") / f"test_step{step_number:02d}.py"
        if (project_root / relative_path).is_file():
            normalized = relative_path.as_posix()
            if normalized not in paths:
                paths.append(normalized)
        elif 2 <= step_number <= 18:
            raise FileNotFoundError(
                f"既完了STEP {step_number:02d} の受入テストがありません: {relative_path}"
            )
    return tuple(paths)


def _phase_for_matrix_case(case_id: str) -> str:
    if case_id in DOCUMENT_CASE_IDS:
        return "document"
    if case_id == "C-026":
        return "registry_only"
    if case_id in {"C-025", "C-028"}:
        return "fixture_partial"
    return "fixture"


def _live_status(case_id: str) -> str:
    if case_id == "C-003":
        return "文書参照のみ確認。V-001～003の実能力は未実証。"
    if case_id == "C-025":
        return "fixture移行部分のみ。実バックアップ復元・本番移行は未実施。"
    if case_id == "C-026":
        return "registryのID/phaseのみ確認。全実観測はSTEP23で未実施。"
    if case_id == "C-028":
        return (
            "STEP20で認証済みAI-O root/native subagentを実証し、STEP21.4/21.5で"
            "同rootのKEY委譲・採用を確認。13:00日次全体の実運転は未確認。"
        )
    if case_id in {"A-02", "X-02"}:
        return (
            "STEP20でChatGPT認証のAI-Oとnative専門役委譲を実証。"
            "全日次実行と本番起動は未確認。"
        )
    if case_id == "A-03":
        return (
            "STEP21.5で公開Explore Top20取得・隔離保存と、AI-O/AG-KEYの"
            "重複分類・採用を確認。日次全体への接続は未確認。"
        )
    if case_id == "A-10":
        return (
            "STEP21.5で公開ROOM検索条件反映と商品詳細描画を読み取り確認。"
            "実行記録上の50件試験は50/50、別試験は20/20。日次intake確定は未確認。"
        )
    if case_id == "X-07":
        return "匿名算術入力/結果の整合性だけ確認。市場成果・実運用供給は未実証。"
    return "STEP19はfixture/document範囲のみ。実AI・外部取得・本番接続は未実証。"


def load_acceptance_registry(
    project_root: Path = PROJECT_ROOT,
) -> dict[str, AcceptanceCase]:
    """Parse the 69-row acceptance matrix plus the eight X cases from the plan."""

    plan_text = _read_text(project_root / PLAN_RELATIVE_PATH)
    cases = _extract_table_cases(plan_text, project_root)
    expected_matrix = set(_MATRIX_CASE_IDS)
    if set(cases) != expected_matrix:
        missing = sorted(expected_matrix - set(cases))
        extra = sorted(set(cases) - expected_matrix)
        raise ValueError(f"受入対応表ID不一致: missing={missing}, extra={extra}")

    extended_cases = _extract_extended_cases(plan_text, project_root)
    expected_extended = {f"X-{number:02d}" for number in range(1, 9)}
    if set(extended_cases) != expected_extended:
        missing = sorted(expected_extended - set(extended_cases))
        extra = sorted(set(extended_cases) - expected_extended)
        raise ValueError(f"追加受入ID不一致: missing={missing}, extra={extra}")

    cases.update(extended_cases)
    for case_id in EXPECTED_CASE_IDS:
        case = cases[case_id]
        if not case.expected_observation.strip():
            raise ValueError(f"期待観測が空です: {case_id}")
        if case.test_refs != (case_id,):
            raise ValueError(f"case idとplan test refが一致しません: {case_id}")
    return cases


def _extract_visible_html_text(path: Path) -> str:
    parser = _VisibleHtmlText()
    parser.feed(_read_text(path))
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _json_text(path: Path) -> tuple[dict[str, Any], str]:
    value = json.loads(_read_text(path))
    return value, json.dumps(value, ensure_ascii=False, sort_keys=True)


def validate_document_contract(
    project_root: Path,
    case_id: str,
) -> dict[str, Any]:
    """Check the complete source decision/acceptance trace without exporting text."""

    if case_id not in DOCUMENT_CASE_IDS:
        raise ValueError(f"文書契約caseではありません: {case_id}")
    requirements_path = project_root / REQUIREMENTS_RELATIVE_PATH
    trace_path = project_root / TRACE_RELATIVE_PATH
    decision_path = project_root / DECISION_RELATIVE_PATH
    plan_path = project_root / PLAN_RELATIVE_PATH
    html_text = _extract_visible_html_text(requirements_path)
    trace_text = _read_text(trace_path)
    decision_text = _read_text(decision_path)
    plan_text = _read_text(plan_path)

    requirement_ids = tuple(f"IR-{number:03d}" for number in range(1, 29))
    acceptance_ids = (
        *(f"AT-{number:03d}" for number in range(1, 29)),
        *(f"AT-S{number:02d}" for number in range(1, 32)),
        *(f"AT-A{number:02d}" for number in range(1, 11)),
    )
    decision_ids = tuple(f"D-{number:03d}" for number in range(1, 61))

    missing_requirement_ids = [
        identifier for identifier in requirement_ids if identifier not in html_text
    ]
    missing_acceptance_ids = [
        identifier for identifier in acceptance_ids if identifier not in html_text
    ]
    missing_decision_ids_html = [
        identifier for identifier in decision_ids if identifier not in html_text
    ]
    missing_decision_ids_trace = [
        identifier for identifier in decision_ids if identifier not in trace_text
    ]
    missing_decision_ids_ledger = [
        identifier for identifier in decision_ids if identifier not in decision_text
    ]
    missing_decision_ids = sorted(
        set(missing_decision_ids_html)
        | set(missing_decision_ids_trace)
        | set(missing_decision_ids_ledger)
    )

    candidate_path = project_root / PREPARATION_ROOT / "文型詳細ルール.json"
    basic_path = project_root / PREPARATION_ROOT / "初期設定引渡し.json"
    owner_record_path = project_root / PREPARATION_ROOT / "本人確認記録.md"
    candidate, _ = _json_text(candidate_path)
    basic, basic_serialized = _json_text(basic_path)
    owner_record = _read_text(owner_record_path)
    analysis = candidate.get("analysis_basis", {})
    required_p05_ids = ["B0002", "B0201", "B0213", "B0235", "B0252"]
    conflicts: list[str] = []

    if "confirmed-0.3-step46-all132" not in basic_serialized:
        conflicts.append(
            "採用済み基本版confirmed-0.3-step46-all132が初期設定引渡しにない"
        )
    if basic.get("status") != "confirmed" or basic.get("adopted") is not True:
        conflicts.append("初期設定引渡しがconfirmed/adoptedではない")
    owner_confirmation_version = basic.get("source_candidate_versions", {}).get(
        "owner_confirmation"
    )
    if owner_confirmation_version != "candidate-0.3-step43-all132":
        conflicts.append(
            "本人確認記録の採用対象版candidate-0.3-step43-all132と一致しない"
        )
    if (
        "candidate-0.3-step43-all132" not in owner_record
        or "採用済み" not in owner_record
    ):
        conflicts.append("本人確認記録にcandidate-0.3-step43-all132の採用回答がない")
    if candidate.get("version") != "detailed-writing-rules-candidate-0.1-step68":
        conflicts.append("詳細書き方候補のversionが想定と異なる")
    if candidate.get("status") != "reviewed_candidate_not_new_owner_confirmation":
        conflicts.append("詳細書き方候補のstatusが未採用候補を示していない")
    if candidate.get("adopted") is not False:
        conflicts.append("詳細書き方候補のadoptedがfalseではない")
    if candidate.get("base_confirmed_version") != "confirmed-0.3-step46-all132":
        conflicts.append("詳細書き方候補の基本版参照が一致しない")
    if analysis.get("base_full_body_records") != 132:
        conflicts.append("基礎分析の本文母集団が132件ではない")
    if analysis.get("reaction_condition_match") != 24:
        conflicts.append("反応条件一致件数が24件ではない")
    if analysis.get("reaction_condition_unknown") != 108:
        conflicts.append("反応条件不明件数が108件ではない")
    if analysis.get("reaction_condition_exclusions") != 0:
        conflicts.append("基礎標本の除外数が0ではない")
    if analysis.get("reaction_conditions_used_as_filter_or_weight") is not False:
        conflicts.append("反応条件を標本filter/weightに使用している")
    if analysis.get("p05_rule_derivation_source_body_ids") != required_p05_ids:
        conflicts.append("P05根拠が指定5件と一致しない")

    precedence_text = "\n".join((plan_text, trace_text, decision_text))
    precedence_refs = (
        "D-007",
        "D-023",
        "D-043",
        "D-052",
        "U-20261003-02",
        "U-20261003-07",
    )
    missing_precedence_refs = [
        ref for ref in precedence_refs if ref not in precedence_text
    ]
    if missing_precedence_refs:
        conflicts.append(
            "現行解釈の優先順位参照が欠落: " + ", ".join(missing_precedence_refs)
        )
    if not re.search(r"1\.3", html_text):
        conflicts.append("要件HTMLの版1.3を本文から確認できない")

    report = {
        "case_id": case_id,
        "requirement_source": REQUIREMENTS_RELATIVE_PATH.as_posix(),
        "trace_source": TRACE_RELATIVE_PATH.as_posix(),
        "decision_source": DECISION_RELATIVE_PATH.as_posix(),
        "visible_html_characters": len(html_text),
        "requirement_ids_expected": len(requirement_ids),
        "acceptance_ids_expected": len(acceptance_ids),
        "decision_ids_expected": len(decision_ids),
        "missing_requirement_ids": missing_requirement_ids,
        "missing_acceptance_ids": missing_acceptance_ids,
        "missing_decision_ids": missing_decision_ids,
        "decision_reference_gaps_by_source": {
            "requirements_html": missing_decision_ids_html,
            "trace_table": missing_decision_ids_trace,
            "decision_ledger": missing_decision_ids_ledger,
        },
        "adopted_candidate_conflicts": conflicts,
        "base_settings_version": "confirmed-0.3-step46-all132",
        "owner_confirmation_candidate_version": owner_confirmation_version,
        "detailed_rules_version": candidate.get("version"),
        "detailed_rules_adopted": candidate.get("adopted"),
        "base_sample_counts": {
            "all": analysis.get("base_full_body_records"),
            "reaction_condition_match": analysis.get("reaction_condition_match"),
            "reaction_condition_unknown": analysis.get("reaction_condition_unknown"),
            "excluded": analysis.get("reaction_condition_exclusions"),
        },
        "p05_source_ids": analysis.get("p05_rule_derivation_source_body_ids"),
        "live_capabilities": "NOT_RUN",
        "private_source_text_copied_to_report": False,
    }
    return report


def validate_simulation_result(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Check all 33 canonical X-07 rows and their input-bound accounting invariants."""

    input_path = project_root / SIMULATION_ROOT / "回答後計算入力.json"
    result_path = project_root / SIMULATION_ROOT / "回答後計算結果.json"
    inputs = json.loads(_read_text(input_path))
    result = json.loads(_read_text(result_path))
    expected_days = tuple(inputs.get("days", ()))
    input_cases = inputs.get("cases", [])
    rows = result.get("results", [])
    expected_pairs = {
        (case.get("id"), day) for case in input_cases for day in expected_days
    }
    actual_pairs = [(row.get("case_id"), row.get("days")) for row in rows]
    failed: list[str] = []

    if inputs.get("version") != result.get("version"):
        failed.append("simulation input/result version mismatch")
    if len(input_cases) != 11 or expected_days != (30, 90, 365):
        failed.append("input does not contain 11 cases × 30/90/365 days")
    if (
        len(rows) != 33
        or len(set(actual_pairs)) != 33
        or set(actual_pairs) != expected_pairs
    ):
        failed.append("result does not contain exactly 33 unique input case/day rows")
    if result.get("checks", {}).get("row_count") != 33:
        failed.append("canonical result row_count check is not 33")
    if result.get("checks", {}).get("same_input_same_result") is not True:
        failed.append("canonical result does not record same_input_same_result=true")
    if result.get("checks", {}).get("invariants") is not True:
        failed.append("canonical result does not record invariants=true")

    max_attempts = inputs.get("max_room_attempts")
    max_posts = inputs.get("max_posts_per_attempt")
    target_per_day = inputs.get("target")
    for row in rows:
        label = f"{row.get('case_id')}:{row.get('days')}"
        target = target_per_day * row["days"]
        if row.get("target") != target:
            failed.append(f"{label}: target != configured target × days")
        if not 0 <= row.get("drafts", -1) <= target:
            failed.append(f"{label}: drafts outside [0, target]")
        if not 0 <= row.get("posted", -1) <= row.get("drafts", -1):
            failed.append(f"{label}: posted exceeds drafts")
        if row.get("shortfall") != target - row.get("drafts", 0):
            failed.append(f"{label}: shortfall != target - drafts")
        if (
            row.get("room_attempts", max_attempts * row["days"] + 1)
            > max_attempts * row["days"]
        ):
            failed.append(f"{label}: room attempts exceed configured finite limit")
        if row.get("post_slots") != max_posts * row.get("room_attempts", 0):
            failed.append(
                f"{label}: post slots do not equal attempts × per-attempt cap"
            )
        if row.get("drafts") != (
            row.get("posted", 0)
            + row.get("explicit_skips", 0)
            + row.get("reserved_end", 0)
        ):
            failed.append(
                f"{label}: draft/post/skip/reservation accounting does not balance"
            )
        if row.get("reserved_peak", -1) < row.get("reserved_end", 0):
            failed.append(f"{label}: reserved peak is below ending reservation")
        if row.get("generation_calls", 0) > 3 * row.get(
            "distinct_candidate_processing", 0
        ):
            failed.append(f"{label}: generation calls exceed initial plus two retries")
        if row.get("generation_failed_candidates", 0) > row.get(
            "distinct_candidate_processing", 0
        ):
            failed.append(f"{label}: failed candidates exceed processed candidates")
        if (
            row.get("product_fact_checks") != 0
            or row.get("product_fact_exclusions") != 0
        ):
            failed.append(f"{label}: product re-verification was added")
        if row.get("copy_quality_gate_exclusions") != 0:
            failed.append(f"{label}: a copy quality completion gate was added")
        if row.get("additional_service_yen") != inputs.get("additional_service_yen"):
            failed.append(f"{label}: additional service cost differs from input")
        if row.get("pool_end", -1) < 0:
            failed.append(f"{label}: ending pool is negative")

    return {
        "input_path": input_path.relative_to(project_root).as_posix(),
        "result_path": result_path.relative_to(project_root).as_posix(),
        "input_version": inputs.get("version"),
        "result_version": result.get("version"),
        "input_case_count": len(input_cases),
        "day_horizons": list(expected_days),
        "rows_checked": len(rows),
        "unique_case_day_pairs": len(set(actual_pairs)),
        "recomputed_fields": [
            "target=configured_daily_target×days",
            "shortfall=target-drafts",
            "post_slots=max_posts_per_attempt×room_attempts",
            "drafts=posted+explicit_skips+reserved_end",
        ],
        "failed_invariants": failed,
        "additional_service_yen": inputs.get("additional_service_yen"),
        "market_claims_added": False,
        "interpretation": "匿名の仮定入力と対になった結果行の整合・保存則を検査。ROOM供給や売上を予測/保証しない。",
    }


def _jst_timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")


def run_fixture_subset_for_selected_cases(
    project_root: Path,
    registry: dict[str, AcceptanceCase],
    selected_case_ids: Iterable[str],
) -> FixtureRunResult:
    """Run the union of mapped existing fixture test modules once in a child pytest."""

    case_ids = tuple(dict.fromkeys(selected_case_ids))
    if not case_ids:
        raise ValueError("fixture_contractで選択されたcase IDがありません")
    unknown = [case_id for case_id in case_ids if case_id not in registry]
    if unknown:
        raise ValueError(f"registryにないcase ID: {unknown}")
    invalid = [case_id for case_id in case_ids if registry[case_id].phase == "document"]
    if invalid:
        raise ValueError(
            f"document_contract caseをfixture実行へ混ぜられません: {invalid}"
        )

    modules = tuple(
        sorted(
            {
                module_path
                for case_id in case_ids
                for module_path in registry[case_id].module_paths
            }
        )
    )
    evidence_directory = _next_evidence_directory(project_root, "fixture-suite")
    suite_basetemp = evidence_directory / "pytest-basetemp"
    junit_path = evidence_directory / "pytest-junit.xml"
    stdout_path = evidence_directory / "pytest-stdout.txt"
    stderr_path = evidence_directory / "pytest-stderr.txt"
    command = (
        sys.executable,
        "-m",
        "pytest",
        *modules,
        "-q",
        "-p",
        "no:cacheprovider",
        f"--basetemp={suite_basetemp}",
        f"--junitxml={junit_path}",
    )

    if modules:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            command,
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        exit_code = completed.returncode
        failures = _junit_failures(junit_path)
        collected_count = _junit_test_count(junit_path)
    else:
        stdout_path.write_text(
            "registry-only case; no child test module required\n", encoding="utf-8"
        )
        stderr_path.write_text("", encoding="utf-8")
        exit_code = 0
        failures = ()
        collected_count = 0

    arithmetic_cases = [
        case_id for case_id in case_ids if registry[case_id].phase == "arithmetic"
    ]
    arithmetic_reports = {
        case_id: validate_simulation_result(project_root)
        for case_id in arithmetic_cases
    }
    for case_id, arithmetic_report in arithmetic_reports.items():
        failures = (*failures, *arithmetic_report["failed_invariants"])
        if arithmetic_report["failed_invariants"]:
            exit_code = 1

    report = {
        "step": "19",
        "created_at_jst": _jst_timestamp(),
        "selected_case_ids": list(case_ids),
        "case_registry": {
            case_id: asdict(registry[case_id]) for case_id in EXPECTED_CASE_IDS
        },
        "test_modules": list(modules),
        "command": list(command) if modules else [],
        "exit_code": exit_code,
        "collected_test_count": collected_count,
        "failures": list(failures),
        "arithmetic_case_reports": arithmetic_reports,
        "evidence_mode": "offline fixture only",
        "production_database_opened": False,
        "external_service_calls": 0,
        "live_ai_room_trends_metrics_evidence": "NOT_RUN",
        "settings_version": "confirmed-0.3-step46-all132",
        "detailed_rules_version": "detailed-writing-rules-candidate-0.1-step68",
        "detailed_rules_adopted": False,
    }
    _write_json(evidence_directory / "fixture-suite-report.json", report)
    return FixtureRunResult(
        selected_case_ids=case_ids,
        module_paths=modules,
        exit_code=exit_code,
        collected_test_count=collected_count,
        failures=tuple(failures),
        command=command,
        evidence_directory=str(evidence_directory),
    )


def _junit_test_count(path: Path) -> int:
    if not path.is_file():
        return 0
    root = ElementTree.parse(path).getroot()
    return len(root.findall(".//testcase"))


def _junit_failures(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ("JUnit report missing",)
    root = ElementTree.parse(path).getroot()
    failures: list[str] = []
    for testcase in root.findall(".//testcase"):
        failure = testcase.find("failure")
        error = testcase.find("error")
        issue = failure if failure is not None else error
        if issue is not None:
            failures.append(
                f"{testcase.get('classname')}::{testcase.get('name')}: "
                f"{(issue.get('message') or issue.tag)[:300]}"
            )
    return tuple(failures)


def _write_document_evidence(project_root: Path, report: dict[str, Any]) -> None:
    evidence_directory = _next_evidence_directory(
        project_root, f"document-{report['case_id']}"
    )
    _write_json(evidence_directory / "document-contract-report.json", report)


def record_document_evidence(project_root: Path, report: dict[str, Any]) -> None:
    """Save an aggregate document check without copying any source text."""

    _write_document_evidence(project_root, report)


def main(argv: list[str] | None = None) -> int:
    """Allow an acceptance case to be invoked alone with --case CASE-ID."""

    parser = argparse.ArgumentParser(
        description="イナゴ STEP19 fixture acceptance runner"
    )
    parser.add_argument(
        "--list", action="store_true", help="77件のcase IDとphaseを表示"
    )
    parser.add_argument("--case", choices=EXPECTED_CASE_IDS, help="単独実行するcase ID")
    args = parser.parse_args(argv)
    registry = load_acceptance_registry(PROJECT_ROOT)

    if args.list:
        print(
            json.dumps(
                {
                    case_id: {
                        "name": registry[case_id].name,
                        "phase": registry[case_id].phase,
                        "test_modules": list(registry[case_id].module_paths),
                        "live_evidence": registry[case_id].live_evidence,
                    }
                    for case_id in EXPECTED_CASE_IDS
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.case:
        parser.error("--listまたは--case CASE-IDを指定してください")
    case = registry[args.case]
    if case.phase == "document":
        report = validate_document_contract(PROJECT_ROOT, args.case)
        _write_document_evidence(PROJECT_ROOT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return (
            0
            if not report["missing_requirement_ids"]
            and not report["missing_acceptance_ids"]
            and not report["missing_decision_ids"]
            and not report["adopted_candidate_conflicts"]
            else 1
        )
    if case.phase == "arithmetic":
        report = validate_simulation_result(PROJECT_ROOT)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report["failed_invariants"] else 1

    result = run_fixture_subset_for_selected_cases(PROJECT_ROOT, registry, (args.case,))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
