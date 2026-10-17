"""STEP 04 contracts for imported settings and versioned candidate rules."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from automation.inago.contracts import IdempotencyConflict
from automation.inago.paths import resolve_runtime_paths
from automation.inago.repository import open_database
from automation.inago.settings import (
    SettingsInputError,
    adopt_detailed_rules,
    load_detailed_rules_candidate,
    register_external_settings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASIC_PATH = PROJECT_ROOT / "docs" / "specifications" / "preparation" / "初期設定引渡し.json"
DETAIL_PATH = (
    PROJECT_ROOT
    / "worklogs"
    / "projects"
    / "preparation"
    / "candidates"
    / "文型詳細ルール.json"
)
REVIEW_ROOT = (
    PROJECT_ROOT / "worklogs" / "projects" / "preparation" / "レビュー"
)
SETTINGS_VERSION = "confirmed-0.3-step46-all132"
DETAILS_VERSION = "detailed-writing-rules-candidate-0.1-step68"
CREATED_AT = "2026-10-12T20:00:00+09:00"
P05_SOURCE_IDS = ("B0002", "B0201", "B0213", "B0235", "B0252")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def settings_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    return deepcopy(_read_json(BASIC_PATH)), deepcopy(_read_json(DETAIL_PATH))


@pytest.fixture
def settings_db(tmp_path: Path, fixture_config_path: Path):
    paths = resolve_runtime_paths(
        data_root=tmp_path / "settings-step04" / "state",
        config_path=fixture_config_path,
        fixture_mode=True,
    )
    connection = open_database(paths)
    try:
        yield connection
    finally:
        connection.close()


def _settings_row(connection, version: str):
    return connection.execute(
        "SELECT * FROM settings_version WHERE version = ?", (version,)
    ).fetchone()


def _seed_running_run(connection, version: str = SETTINGS_VERSION) -> None:
    connection.execute(
        """
        INSERT INTO run
            (run_id, target_date, mode, status, settings_version, seed,
             started_at, draft_deadline, end_deadline, attempt_used,
             committed_count, checkpoint, metrics_status, finished_at)
        VALUES ('RUN-STEP04', '2026-10-12', 'daily', 'running', ?,
                'SEED-STEP04', '2026-10-12T13:00:00+09:00',
                '2026-10-12T17:45:00+09:00', '2026-10-12T18:00:00+09:00',
                1, 0, NULL, 'not_started', NULL)
        """,
        (version,),
    )
    connection.execute(
        """
        INSERT INTO offer
            (offer_key, shop_id, item_id, name, url, image_refs_json,
             main_theme_id, aux_theme_ids_json, first_seen, last_seen, facts_json)
        VALUES ('shop_fixture:item_1', 'shop_fixture', 'item_1', '架空商品',
                'https://example.invalid/item_1', '[]', 'G01-T01', '[]',
                '2026-10-12T13:00:00+09:00', '2026-10-12T13:00:00+09:00', '[]')
        """
    )
    connection.execute(
        """
        INSERT INTO draft
            (draft_id, offer_key, run_id, version, body, generated_at,
             committed_at, generator_version, pattern_version, status,
             artifact_ref, pattern_id, evidence_refs_json)
        VALUES ('DRAFT-STEP04', 'shop_fixture:item_1', 'RUN-STEP04', 1,
                'fixture draft', '2026-10-12T13:01:00+09:00',
                '2026-10-12T13:01:00+09:00', 'fixture-generator',
                'fixture-patterns', 'committed', NULL, 'P01', '[]')
        """
    )
    connection.execute(
        """
        INSERT INTO reservation
            (reservation_id, offer_key, draft_id, status, created_at,
             released_at, release_reason)
        VALUES ('RES-STEP04', 'shop_fixture:item_1', 'DRAFT-STEP04', 'active',
                '2026-10-12T13:01:00+09:00', NULL, NULL)
        """
    )


def _reservation_state(connection) -> list[tuple[str, str, str]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT reservation_id, offer_key, status FROM reservation ORDER BY reservation_id"
        )
    ]


def test_registers_adopted_basic_settings_without_enabling_detailed_candidate(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    before_basic = deepcopy(basic)
    before_detailed = deepcopy(detailed)

    result = register_external_settings(
        settings_db, basic, detailed, created_at=CREATED_AT
    )

    assert result.registered_version == SETTINGS_VERSION
    assert result.active_basic_version == SETTINGS_VERSION
    assert result.confirmed_at is None
    assert result.confirmation_ref == "本人確認記録.md"
    assert result.detailed_rules_enabled is False
    assert result.detailed_rules_version is None
    assert result.detailed_rules_candidate.available is True
    assert result.detailed_rules_candidate.version == DETAILS_VERSION
    assert result.detailed_rules_candidate.adopted is False
    assert result.detailed_rules_candidate.enabled is False
    assert result.body_length_guidance == basic["body_length_guidance"]
    assert result.tag_guidance == basic["tag_guidance"]
    assert result.body_length_guidance["mandatory"] is False
    assert result.tag_guidance["mandatory"] is False
    assert result.copy_settings == {
        "mandatory": False,
        "include_url_in_body": False,
    }

    row = _settings_row(settings_db, SETTINGS_VERSION)
    assert row is not None
    assert row["confirmed_at"] is None
    assert json.loads(row["genres_json"]) == [
        {key: item[key] for key in ("genre_id", "name", "definition")}
        for item in basic["genres"]
    ]
    stored_themes = json.loads(row["themes_json"])
    assert len(stored_themes) == 30
    assert [theme["theme_id"] for theme in stored_themes] == [
        theme["theme_id"] for theme in basic["themes"]
    ]
    assert all(theme["selection_count"] == 0 for theme in stored_themes)
    assert json.loads(row["pattern_refs_json"]) == basic["pattern_refs"]
    assert row["created_at"] == CREATED_AT
    import_record = json.loads(row["change_reason"])
    assert import_record["format"] == "inago-settings-import-v1"
    assert import_record["source"] == {
        "settings_path": "docs/specifications/preparation/初期設定引渡し.json",
        "result_index_ref": "結果セット索引.json",
        "confirmation_ref": "本人確認記録.md",
    }
    assert basic == before_basic
    assert detailed == before_detailed
    assert settings_db.execute("SELECT COUNT(*) FROM search_attempt").fetchone()[0] == 0
    assert settings_db.execute("SELECT COUNT(*) FROM ai_job").fetchone()[0] == 0


def test_candidate_rules_are_keyed_by_pattern_id_and_explicitly_mapped(
    settings_inputs,
) -> None:
    basic, detailed = settings_inputs
    detailed["patterns"].reverse()

    candidate = load_detailed_rules_candidate(basic, detailed)

    assert candidate.available is True
    assert candidate.adopted is False
    assert candidate.version == DETAILS_VERSION
    assert set(candidate.pattern_rules) == {"P01", "P02", "P03", "P04", "P05"}
    assert sum(len(rules) for rules in candidate.pattern_rules.values()) == 85
    assert len(candidate.common_rules) == 14
    assert candidate.p05_source_body_ids == P05_SOURCE_IDS

    source_p01 = next(
        pattern for pattern in detailed["patterns"] if pattern["pattern_id"] == "P01"
    )
    source_rule = source_p01["rules"][0]
    mapped_rule = candidate.pattern_rules["P01"][0]
    assert mapped_rule["rule_id"] == "P01-W01"
    assert (
        mapped_rule["input_conditions"] == source_rule["input_conditions_and_decision"]
    )
    assert mapped_rule["examples"] == source_rule["concrete_examples"]
    assert "input_conditions_and_decision" not in mapped_rule
    assert (
        candidate.common_rules[0]["input_conditions"]
        == detailed["common_rules"][0]["input_conditions"]
    )
    assert "examples" not in candidate.common_rules[0]


def test_detailed_candidate_must_be_present_for_direct_candidate_loading(
    settings_inputs,
) -> None:
    basic, _detailed = settings_inputs

    with pytest.raises(SettingsInputError, match="not available"):
        load_detailed_rules_candidate(basic, None)


def test_import_preserves_null_material_data_and_does_not_use_observation_counts(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    basic["themes"][0]["material_count"] = None
    basic["themes"][0]["material_kind"] = None
    basic["selection_count"] = 0

    register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)

    stored = json.loads(_settings_row(settings_db, SETTINGS_VERSION)["themes_json"])
    assert stored[0]["material_count"] is None
    assert stored[0]["material_kind"] is None
    assert all(theme["selection_count"] == 0 for theme in stored)
    assert (
        "body_observation_count"
        not in json.loads(_settings_row(settings_db, SETTINGS_VERSION)["genres_json"])[
            0
        ]
    )
    assert basic["analysis_basis"]["base_full_body_observations"] == 132
    assert basic["analysis_basis"]["reaction_condition_status"] == {
        "match": 24,
        "unknown": 108,
        "exclusions": 0,
        "used_as_filter": False,
    }
    assert basic["analysis_basis"]["supplement_observations"] == 50
    assert basic["analysis_basis"]["supplement_is_separate_cohort"] is True


@pytest.mark.parametrize(
    "missing_field", ["genres", "themes", "patterns", "pattern_refs"]
)
def test_missing_required_basic_fields_report_field_and_write_nothing(
    settings_db, settings_inputs, missing_field: str
) -> None:
    basic, detailed = settings_inputs
    basic.pop(missing_field)

    with pytest.raises(SettingsInputError, match=missing_field):
        register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 0
    )
    assert settings_db.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0
    assert settings_db.execute("SELECT COUNT(*) FROM search_attempt").fetchone()[0] == 0
    assert settings_db.execute("SELECT COUNT(*) FROM ai_job").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("theme_count", "themes"),
        ("theme_membership", "genre_id"),
        ("pattern_ids", "patterns"),
        ("selection_count", "selection_count"),
        ("analysis_filter", "reaction_condition_status"),
    ],
)
def test_invalid_basic_shape_or_source_analysis_is_rejected_before_registration(
    settings_db, settings_inputs, mutation: str, message: str
) -> None:
    basic, detailed = settings_inputs
    if mutation == "theme_count":
        basic["themes"].pop()
    elif mutation == "theme_membership":
        basic["themes"][0]["genre_id"] = "G99"
    elif mutation == "pattern_ids":
        basic["patterns"][0]["pattern_id"] = "P99"
    elif mutation == "selection_count":
        basic["selection_count"] = 132
    else:
        basic["analysis_basis"]["reaction_condition_status"]["used_as_filter"] = True

    with pytest.raises(SettingsInputError, match=message):
        register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 0
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("genre_id", "G01–G05"),
        ("theme_id", "theme IDs"),
        ("uneven_membership", "six active themes"),
        ("duplicate_ref", "unique"),
        ("reference_fragment", "inconsistent"),
        ("detail_fragment", "identify a pattern by ID"),
        ("body_guidance", "optional 150–300"),
        ("tag_guidance", "optional 5–12"),
    ],
)
def test_basic_identity_membership_and_optional_guidance_are_checked(
    settings_db, settings_inputs, mutation: str, message: str
) -> None:
    basic, detailed = settings_inputs
    if mutation == "genre_id":
        basic["genres"][0]["genre_id"] = "G99"
    elif mutation == "theme_id":
        basic["themes"][0]["theme_id"] = "G01-T99"
    elif mutation == "uneven_membership":
        basic["themes"][0]["genre_id"] = "G02"
    elif mutation == "duplicate_ref":
        basic["pattern_refs"][0] = basic["pattern_refs"][1]
    elif mutation == "reference_fragment":
        basic["patterns"][0]["reference"] = "別の参照"
        basic["pattern_refs"][0] = "別の参照"
    elif mutation == "detail_fragment":
        basic["patterns"][0]["detailed_rules_ref"] = "文型詳細ルール.json#/patterns/other"
    elif mutation == "body_guidance":
        basic["body_length_guidance"]["min"] = 149
    else:
        basic["tag_guidance"]["max"] = 13

    with pytest.raises(SettingsInputError, match=message):
        register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 0
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("p05_sources", "analysis_basis"),
        ("p05_aggregate", "analysis_basis"),
        ("duplicate_pattern", "contain P01–P05 once"),
        ("markdown_ref", "source_markdown is inconsistent"),
        ("rule_sequence", "W01–W17 in order"),
        ("p05_basis", "rule_derivation_basis is out of scope"),
        ("common_rule_id", "W-C01–W-C14 in order"),
    ],
)
def test_detailed_candidate_rejects_scope_and_source_mapping_drift(
    settings_inputs, mutation: str, message: str
) -> None:
    basic, detailed = settings_inputs
    if mutation == "p05_sources":
        detailed["analysis_basis"]["p05_rule_derivation_source_body_ids"] = ["B0002"]
    elif mutation == "p05_aggregate":
        detailed["analysis_basis"][
            "p05_rule_derivation_uses_other_127_or_full_132_aggregate"
        ] = True
    elif mutation == "duplicate_pattern":
        detailed["patterns"][0]["pattern_id"] = detailed["patterns"][1]["pattern_id"]
    elif mutation == "markdown_ref":
        detailed["patterns"][0]["source_markdown"] = "詳細書き方/P99.md"
    elif mutation == "rule_sequence":
        detailed["patterns"][0]["rules"][0]["sequence"] = 2
    elif mutation == "p05_basis":
        detailed["patterns"][-1]["rule_derivation_basis"]["source_body_ids"] = ["B0002"]
    else:
        detailed["common_rules"][0]["rule_id"] = "W-C99"

    with pytest.raises(SettingsInputError, match=message):
        load_detailed_rules_candidate(basic, detailed)


@pytest.mark.parametrize("created_at", [None, "", "not-a-time", "2026-10-12T20:00:00"])
def test_import_rejects_missing_invalid_or_timezone_naive_timestamp(
    settings_db, settings_inputs, created_at: str | None
) -> None:
    basic, detailed = settings_inputs

    with pytest.raises(SettingsInputError, match="created_at"):
        register_external_settings(settings_db, basic, detailed, created_at=created_at)

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 0
    )


def test_unavailable_detailed_candidate_does_not_block_basic_registration(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    detailed["patterns"] = detailed["patterns"][:-1]

    result = register_external_settings(
        settings_db, basic, detailed, created_at=CREATED_AT
    )

    assert result.active_basic_version == SETTINGS_VERSION
    assert result.detailed_rules_enabled is False
    assert result.detailed_rules_confirmation_ref is None
    assert result.detailed_rules_candidate.available is False
    assert "patterns" in result.detailed_rules_candidate.unavailable_reason
    assert _settings_row(settings_db, SETTINGS_VERSION) is not None
    assert settings_db.execute("SELECT COUNT(*) FROM search_attempt").fetchone()[0] == 0
    assert settings_db.execute("SELECT COUNT(*) FROM ai_job").fetchone()[0] == 0


@pytest.mark.parametrize(
    "owner_adoption",
    [
        None,
        {
            "target_version": "different-candidate",
            "adopted": True,
            "confirmed_at": None,
            "confirmation_ref": "fixture-only:answer",
            "reason": "fixture adoption",
        },
        {
            "target_version": DETAILS_VERSION,
            "adopted": True,
            "confirmed_at": None,
            "confirmation_ref": " ",
            "reason": "fixture adoption",
        },
        {
            "target_version": DETAILS_VERSION,
            "adopted": True,
            "confirmed_at": None,
            "confirmation_ref": "fixture-only:answer",
            "reason": " ",
        },
        {
            "target_version": DETAILS_VERSION,
            "adopted": True,
            "confirmed_at": "2026-99-99T25:00:00+09:00",
            "confirmation_ref": "fixture-only:answer",
            "reason": "fixture adoption",
        },
    ],
)
def test_new_version_cannot_enable_candidate_without_matching_owner_answer(
    settings_db, settings_inputs, owner_adoption
) -> None:
    basic, detailed = settings_inputs
    initial = register_external_settings(
        settings_db, basic, detailed, created_at=CREATED_AT
    )

    with pytest.raises(SettingsInputError):
        adopt_detailed_rules(
            settings_db,
            basic,
            detailed,
            owner_adoption,
            new_version="fixture-settings-S2",
            created_at="2026-10-12T20:05:00+09:00",
        )

    assert initial.active_basic_version == SETTINGS_VERSION
    assert initial.detailed_rules_enabled is False
    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 1
    )


@pytest.mark.parametrize("new_version", ["", "   ", " fixture-settings-S2"])
def test_adoption_rejects_blank_or_padded_new_settings_version(
    settings_db, settings_inputs, new_version: str
) -> None:
    basic, detailed = settings_inputs
    register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)
    owner_answer = {
        "target_version": DETAILS_VERSION,
        "adopted": True,
        "confirmed_at": None,
        "confirmation_ref": "fixture-only:owner-answer-S2",
        "reason": "fixture-only explicit adoption of this candidate version",
    }

    with pytest.raises(SettingsInputError, match="new_version"):
        adopt_detailed_rules(
            settings_db,
            basic,
            detailed,
            owner_answer,
            new_version=new_version,
            created_at="2026-10-12T20:05:00+09:00",
        )

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 1
    )


def test_adoption_requires_a_distinct_registered_base_version(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)
    owner_answer = {
        "target_version": DETAILS_VERSION,
        "adopted": True,
        "confirmed_at": None,
        "confirmation_ref": "fixture-only:owner-answer-S2",
        "reason": "fixture-only explicit adoption of this candidate version",
    }

    with pytest.raises(SettingsInputError, match="requires a new settings version"):
        adopt_detailed_rules(
            settings_db,
            basic,
            detailed,
            owner_answer,
            new_version=SETTINGS_VERSION,
            created_at="2026-10-12T20:05:00+09:00",
        )

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 1
    )


def test_adoption_requires_the_adopted_base_to_be_registered(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    owner_answer = {
        "target_version": DETAILS_VERSION,
        "adopted": True,
        "confirmed_at": None,
        "confirmation_ref": "fixture-only:owner-answer-S2",
        "reason": "fixture-only explicit adoption of this candidate version",
    }

    with pytest.raises(
        SettingsInputError, match="basic settings version is not registered"
    ):
        adopt_detailed_rules(
            settings_db,
            basic,
            detailed,
            owner_answer,
            new_version="fixture-settings-S2",
            created_at="2026-10-12T20:05:00+09:00",
        )

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 0
    )


def test_adoption_creates_new_version_and_keeps_running_run_and_reservation(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    initial = register_external_settings(
        settings_db, basic, detailed, created_at=CREATED_AT
    )
    _seed_running_run(settings_db)
    reservations_before = _reservation_state(settings_db)
    owner_answer = {
        "target_version": DETAILS_VERSION,
        "adopted": True,
        "confirmed_at": None,
        "confirmation_ref": "fixture-only:owner-answer-S2",
        "reason": "fixture-only explicit adoption of this candidate version",
    }

    active = adopt_detailed_rules(
        settings_db,
        basic,
        detailed,
        owner_answer,
        new_version="fixture-settings-S2",
        created_at="2026-10-12T20:05:00+09:00",
    )

    assert active.active_basic_version == "fixture-settings-S2"
    assert active.detailed_rules_enabled is True
    assert active.detailed_rules_version == DETAILS_VERSION
    assert active.detailed_rules_confirmation_ref == owner_answer["confirmation_ref"]
    assert active.detailed_rules_candidate.adopted is False
    assert active.detailed_rules_candidate.enabled is True
    new_row = _settings_row(settings_db, "fixture-settings-S2")
    assert new_row is not None
    assert new_row["confirmed_at"] is None
    change_record = json.loads(new_row["change_reason"])
    assert change_record["format"] == "inago-settings-change-v1"
    assert change_record["source"]["confirmation_ref"] == "本人確認記録.md"
    assert change_record["detailed_rules_adoption"] == {
        "adopted": True,
        "version": DETAILS_VERSION,
        "reference": "文型詳細ルール.json",
        "confirmation_ref": owner_answer["confirmation_ref"],
        "confirmed_at": None,
    }
    assert change_record["diff"] == {
        "from_version": SETTINGS_VERSION,
        "to_version": "fixture-settings-S2",
        "pattern_refs_added": [f"文型詳細ルール.json@{DETAILS_VERSION}"],
        "detailed_rules_enabled": {"before": False, "after": True},
    }
    assert json.loads(new_row["pattern_refs_json"])[:5] == basic["pattern_refs"]
    assert any(
        reference.endswith("@" + DETAILS_VERSION)
        for reference in json.loads(new_row["pattern_refs_json"])
    )
    assert initial.active_basic_version == SETTINGS_VERSION
    assert (
        settings_db.execute(
            "SELECT settings_version FROM run WHERE run_id='RUN-STEP04'"
        ).fetchone()[0]
        == SETTINGS_VERSION
    )
    assert _reservation_state(settings_db) == reservations_before

    replay = adopt_detailed_rules(
        settings_db,
        basic,
        detailed,
        owner_answer,
        new_version="fixture-settings-S2",
        created_at="2026-10-12T20:06:00+09:00",
    )
    assert replay.active_basic_version == "fixture-settings-S2"
    assert replay.detailed_rules_confirmation_ref == owner_answer["confirmation_ref"]
    changed_answer = deepcopy(owner_answer)
    changed_answer["reason"] = "different owner response"
    with pytest.raises(IdempotencyConflict):
        adopt_detailed_rules(
            settings_db,
            basic,
            detailed,
            changed_answer,
            new_version="fixture-settings-S2",
            created_at="2026-10-12T20:06:30+09:00",
        )
    with pytest.raises(SettingsInputError, match="active settings version changed"):
        adopt_detailed_rules(
            settings_db,
            basic,
            detailed,
            owner_answer,
            new_version="fixture-settings-S3",
            created_at="2026-10-12T20:07:00+09:00",
        )
    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 2
    )

    reimported = register_external_settings(
        settings_db, basic, detailed, created_at="2026-10-12T21:00:00+09:00"
    )
    assert reimported.active_basic_version == "fixture-settings-S2"
    assert reimported.detailed_rules_enabled is True
    assert _settings_row(settings_db, SETTINGS_VERSION)["created_at"] == CREATED_AT
    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 2
    )


@pytest.mark.parametrize(
    ("field_path", "replacement"),
    [
        (("detailed_rules_adoption", "adopted"), False),
        (("detailed_rules_adoption", "version"), "another-candidate"),
        (("detailed_rules_adoption", "reference"), "別の候補.json"),
        (("detailed_rules_adoption", "confirmation_ref"), " "),
        (("detailed_rules_adoption", "confirmed_at"), "2026-10-12T20:00:00+09:00"),
        (("diff", "to_version"), "別の設定版"),
        (("diff", "pattern_refs_added"), []),
    ],
)
def test_candidate_activation_fails_closed_when_adoption_record_is_inconsistent(
    settings_db, settings_inputs, field_path: tuple[str, str], replacement: Any
) -> None:
    basic, detailed = settings_inputs
    register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)
    owner_answer = {
        "target_version": DETAILS_VERSION,
        "adopted": True,
        "confirmed_at": None,
        "confirmation_ref": "fixture-only:owner-answer-S2",
        "reason": "fixture-only explicit adoption of this candidate version",
    }
    adopt_detailed_rules(
        settings_db,
        basic,
        detailed,
        owner_answer,
        new_version="fixture-settings-S2",
        created_at="2026-10-12T20:05:00+09:00",
    )
    row = _settings_row(settings_db, "fixture-settings-S2")
    change_record = json.loads(row["change_reason"])
    change_record[field_path[0]][field_path[1]] = replacement
    settings_db.execute(
        "UPDATE settings_version SET change_reason = ? WHERE version = ?",
        (json.dumps(change_record), "fixture-settings-S2"),
    )

    result = register_external_settings(
        settings_db, basic, detailed, created_at="2026-10-12T21:00:00+09:00"
    )

    assert result.active_basic_version == "fixture-settings-S2"
    assert result.detailed_rules_enabled is False
    assert result.detailed_rules_version is None
    assert result.detailed_rules_confirmation_ref is None


@pytest.mark.parametrize(
    "change_reason",
    [
        "not-json",
        {
            "format": "inago-settings-change-v1",
            "detailed_rules_adoption": "not-an-object",
            "diff": [],
        },
    ],
)
def test_malformed_adoption_record_does_not_enable_detailed_candidate(
    settings_db, settings_inputs, change_reason: str | dict[str, Any]
) -> None:
    basic, detailed = settings_inputs
    register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)
    owner_answer = {
        "target_version": DETAILS_VERSION,
        "adopted": True,
        "confirmed_at": None,
        "confirmation_ref": "fixture-only:owner-answer-S2",
        "reason": "fixture-only explicit adoption of this candidate version",
    }
    adopt_detailed_rules(
        settings_db,
        basic,
        detailed,
        owner_answer,
        new_version="fixture-settings-S2",
        created_at="2026-10-12T20:05:00+09:00",
    )
    settings_db.execute(
        "UPDATE settings_version SET change_reason = ? WHERE version = ?",
        (
            json.dumps(change_reason)
            if isinstance(change_reason, dict)
            else change_reason,
            "fixture-settings-S2",
        ),
    )

    result = register_external_settings(
        settings_db, basic, detailed, created_at="2026-10-12T21:00:00+09:00"
    )

    assert result.active_basic_version == "fixture-settings-S2"
    assert result.detailed_rules_enabled is False
    assert result.detailed_rules_confirmation_ref is None


def test_same_settings_version_with_different_content_is_an_idempotency_conflict(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)
    changed = deepcopy(basic)
    changed["genres"][0]["name"] = "別の名前"

    with pytest.raises(IdempotencyConflict):
        register_external_settings(
            settings_db, changed, detailed, created_at="2026-10-12T20:10:00+09:00"
        )

    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 1
    )
    assert (
        json.loads(_settings_row(settings_db, SETTINGS_VERSION)["genres_json"])[0][
            "name"
        ]
        == "食品・飲料"
    )


def test_import_savepoint_rollback_preserves_the_callers_transaction(
    settings_db, settings_inputs
) -> None:
    basic, detailed = settings_inputs
    register_external_settings(settings_db, basic, detailed, created_at=CREATED_AT)
    settings_db.execute("BEGIN IMMEDIATE")
    changed = deepcopy(basic)
    changed["genres"][0]["name"] = "同じ版の異なる内容"

    with pytest.raises(IdempotencyConflict):
        register_external_settings(
            settings_db, changed, detailed, created_at="2026-10-12T20:10:00+09:00"
        )

    assert settings_db.in_transaction is True
    assert (
        settings_db.execute("SELECT COUNT(*) FROM settings_version").fetchone()[0] == 1
    )
    assert (
        json.loads(_settings_row(settings_db, SETTINGS_VERSION)["genres_json"])[0][
            "name"
        ]
        == "食品・飲料"
    )
    settings_db.rollback()


def test_settings_schema_is_valid_and_declares_basic_and_candidate_contracts() -> None:
    schema_path = (
        PROJECT_ROOT / "automation" / "inago" / "schemas" / "settings.schema.json"
    )
    schema = _read_json(schema_path)

    Draft202012Validator.check_schema(schema)
    assert "external_settings" in schema["$defs"]
    assert "detailed_rules_candidate" in schema["$defs"]
    assert "owner_adoption" in schema["$defs"]


def test_candidate_markdown_references_resolve_to_all_85_pattern_rules(
    settings_inputs,
) -> None:
    _basic, detailed = settings_inputs

    for pattern in detailed["patterns"]:
        markdown_path = DETAIL_PATH.parent / pattern["source_markdown"]
        markdown = markdown_path.read_text(encoding="utf-8")
        rules = pattern["rules"]
        assert len(rules) == 17
        for rule in rules:
            assert f"### {rule['rule_id']} " in markdown

    review = (REVIEW_ROOT / "書き方詳細レビュー.md").read_text(encoding="utf-8")
    final_check = (REVIEW_ROOT / "書き方詳細版最終確認.md").read_text(encoding="utf-8")
    assert "F66-01" in review
    assert DETAILS_VERSION in final_check
    assert "adopted=false" in final_check
