"""Import the adopted external settings and isolate detailed-rule candidates."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from automation.inago.contracts import IdempotencyConflict, RejectedContract
from automation.inago.repository import validate_entity
from automation.jsonschema_formats import rfc3339_format_checker


SETTINGS_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "settings.schema.json"
)
PATTERN_IDS = ("P01", "P02", "P03", "P04", "P05")
P05_SOURCE_BODY_IDS = ("B0002", "B0201", "B0213", "B0235", "B0252")
DETAIL_CANDIDATE_STATUS = "reviewed_candidate_not_new_owner_confirmation"
DETAIL_CANDIDATE_VERSION = "detailed-writing-rules-candidate-0.1-step68"
BASE_SETTINGS_VERSION = "confirmed-0.3-step46-all132"
PATTERN_FIELD_MAP = {
    "input_conditions": "input_conditions_and_decision",
    "procedure": "procedure",
    "wording_and_syntax": "wording_and_syntax",
    "omissions_and_switching": "omissions_and_switching",
    "examples": "concrete_examples",
    "evidence_and_classification": "evidence_and_classification",
    "content_checks": "content_checks",
}
COMMON_RULE_FIELDS = (
    "input_conditions",
    "procedure",
    "wording_and_syntax",
    "omissions_and_switching",
    "evidence_and_classification",
    "content_checks",
)
_SETTINGS_SAVEPOINT = "inago_settings_import"
_ADOPTION_SAVEPOINT = "inago_settings_adoption"
_RUN_CHANGE_REASON_FORMAT = "inago-settings-change-v1"
_INITIAL_IMPORT_REASON_FORMAT = "inago-settings-import-v1"


class SettingsInputError(RejectedContract):
    """An external settings document or owner change is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class DetailedRulesCandidate:
    """A validated, inactive detailed-rule source mapped by P01–P05 IDs."""

    available: bool
    version: str | None
    status: str
    adopted: bool
    enabled: bool
    reference: str | None
    pattern_rules: Mapping[str, tuple[dict[str, Any], ...]]
    common_rules: tuple[dict[str, Any], ...]
    p05_source_body_ids: tuple[str, ...]
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SettingsRuntime:
    """Versioned settings supplied to later selection and copy components."""

    registered_version: str
    active_basic_version: str
    confirmed_at: str | None
    confirmation_ref: str
    genres: tuple[dict[str, Any], ...]
    themes: tuple[dict[str, Any], ...]
    pattern_refs: tuple[str, ...]
    patterns: tuple[dict[str, Any], ...]
    body_length_guidance: dict[str, Any]
    tag_guidance: dict[str, Any]
    copy_settings: dict[str, bool]
    analysis_basis: dict[str, Any]
    detailed_rules_candidate: DetailedRulesCandidate
    detailed_rules_enabled: bool
    detailed_rules_version: str | None
    detailed_rules_confirmation_ref: str | None


@lru_cache(maxsize=1)
def _settings_schema() -> dict[str, Any]:
    try:
        schema = json.loads(SETTINGS_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise SettingsInputError("settings.schema.json cannot be loaded") from exc
    return schema


def _format_path(parts: list[Any]) -> str:
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += ("." if path else "") + str(part)
    return path


def _validation_message(error: ValidationError, root: str) -> str:
    parts = list(error.absolute_path)
    if error.validator == "required" and isinstance(error.instance, Mapping):
        missing = sorted(set(error.validator_value) - set(error.instance))
        if missing:
            parts.append(missing[0])
    field = _format_path(parts) or root
    return f"{root}.{field}: {error.message}"


def _validate_document(name: str, document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise SettingsInputError(f"{name} must be a JSON object")
    root_schema = {
        "$schema": _settings_schema()["$schema"],
        "$defs": _settings_schema()["$defs"],
        "$ref": f"#/$defs/{name}",
    }
    try:
        Draft202012Validator(
            root_schema, format_checker=rfc3339_format_checker()
        ).validate(dict(document))
    except ValidationError as exc:
        raise SettingsInputError(_validation_message(exc, name)) from exc
    return dict(document)


def _validate_basic_settings(source: Mapping[str, Any]) -> dict[str, Any]:
    settings = _validate_document("external_settings", source)
    if settings["version"] != BASE_SETTINGS_VERSION:
        raise SettingsInputError(
            "externalSettings.version must be the adopted base version "
            f"{BASE_SETTINGS_VERSION}"
        )

    genres = settings["genres"]
    themes = settings["themes"]
    genre_ids = [item["genre_id"] for item in genres]
    if len(set(genre_ids)) != 5 or set(genre_ids) != {
        "G01",
        "G02",
        "G03",
        "G04",
        "G05",
    }:
        raise SettingsInputError("externalSettings.genres must contain G01–G05 once")

    theme_ids = [item["theme_id"] for item in themes]
    expected_theme_ids = {
        f"G{genre_no:02d}-T{theme_no:02d}"
        for genre_no in range(1, 6)
        for theme_no in range(1, 7)
    }
    if len(set(theme_ids)) != 30 or set(theme_ids) != expected_theme_ids:
        raise SettingsInputError(
            "externalSettings.themes must contain the 30 adopted theme IDs once"
        )
    active_counts = {genre_id: 0 for genre_id in genre_ids}
    for theme in themes:
        if theme["genre_id"] not in active_counts:
            raise SettingsInputError(
                f"externalSettings.themes[{theme['theme_id']}].genre_id is unknown"
            )
        active_counts[theme["genre_id"]] += 1
    if any(count != 6 for count in active_counts.values()):
        raise SettingsInputError(
            "externalSettings.themes must contain six active themes per genre"
        )

    patterns = settings["patterns"]
    pattern_ids = [item["pattern_id"] for item in patterns]
    if len(set(pattern_ids)) != 5 or set(pattern_ids) != set(PATTERN_IDS):
        raise SettingsInputError("externalSettings.patterns must contain P01–P05 once")
    pattern_by_id = {item["pattern_id"]: item for item in patterns}
    refs = settings["pattern_refs"]
    if len(set(refs)) != 5:
        raise SettingsInputError("externalSettings.pattern_refs must be unique")
    if set(refs) != {
        pattern_by_id[pattern_id]["reference"] for pattern_id in PATTERN_IDS
    }:
        raise SettingsInputError(
            "externalSettings.pattern_refs must refer to the five adopted patterns"
        )
    for pattern_id in PATTERN_IDS:
        reference = pattern_by_id[pattern_id]["reference"]
        if not reference.endswith(f"#{pattern_id}"):
            raise SettingsInputError(
                f"externalSettings.patterns[{pattern_id}].reference is inconsistent"
            )
        detail_ref = pattern_by_id[pattern_id]["detailed_rules_ref"]
        if not detail_ref.endswith(f"#/patterns/{pattern_id}"):
            raise SettingsInputError(
                f"externalSettings.patterns[{pattern_id}].detailed_rules_ref "
                "must identify a pattern by ID"
            )

    body_guidance = settings["body_length_guidance"]
    tag_guidance = settings["tag_guidance"]
    if (
        body_guidance["min"],
        body_guidance["max"],
        body_guidance["unit"],
        body_guidance["mandatory"],
    ) != (150, 300, "Unicode code points", False):
        raise SettingsInputError(
            "externalSettings.body_length_guidance must preserve the optional 150–300 range"
        )
    if (
        tag_guidance["min"],
        tag_guidance["max"],
        tag_guidance["unit"],
        tag_guidance["mandatory"],
    ) != (5, 12, "hashtag tokens per post", False):
        raise SettingsInputError(
            "externalSettings.tag_guidance must preserve the optional 5–12 range"
        )
    return settings


def _confirmation_reference_target(value: object) -> str | None:
    """Normalize the legacy basename and relocated links to one known report."""

    if not isinstance(value, str):
        return None
    target = PurePosixPath("reports/projects/preparation/本人確認記録.md")
    if value == target.name:
        return target.as_posix()
    parts = PurePosixPath(unquote(value).replace("\\", "/")).parts
    target_parts = target.parts
    if (
        len(parts) > len(target_parts)
        and parts[-len(target_parts) :] == target_parts
        and all(part == ".." for part in parts[: -len(target_parts)])
    ):
        return target.as_posix()
    return None


def _unavailable_candidate(
    settings: Mapping[str, Any], reason: str
) -> DetailedRulesCandidate:
    return DetailedRulesCandidate(
        available=False,
        version=settings.get("detailed_rules_version"),
        status="unavailable",
        adopted=False,
        enabled=False,
        reference=settings.get("detailed_rules_ref"),
        pattern_rules={},
        common_rules=(),
        p05_source_body_ids=(),
        unavailable_reason=reason,
    )


def _map_pattern_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    mapped = {
        "rule_id": rule["rule_id"],
        "sequence": rule["sequence"],
        "detail_dimension": rule["detail_dimension"],
    }
    mapped.update(
        {
            internal_field: rule[source_field]
            for internal_field, source_field in PATTERN_FIELD_MAP.items()
        }
    )
    return mapped


def _map_common_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": rule["rule_id"],
        "detail_dimension": rule["detail_dimension"],
        **{field: rule[field] for field in COMMON_RULE_FIELDS},
    }


def load_detailed_rules_candidate(
    adopted_settings: Mapping[str, Any],
    detailed_rules: Mapping[str, Any] | None,
) -> DetailedRulesCandidate:
    """Validate and map the optional detailed candidate without activating it."""

    settings = _validate_basic_settings(adopted_settings)
    if detailed_rules is None:
        raise SettingsInputError("detailed rules candidate is not available")
    candidate = _validate_document("detailed_rules_candidate", detailed_rules)

    if candidate["version"] != settings["detailed_rules_version"]:
        raise SettingsInputError(
            "detailedRulesCandidate.version does not match the base reference"
        )
    if candidate["status"] != settings["detailed_rules_status"]:
        raise SettingsInputError(
            "detailedRulesCandidate.status does not match the base reference"
        )
    if candidate["base_confirmed_version"] != settings["version"]:
        raise SettingsInputError(
            "detailedRulesCandidate.base_confirmed_version does not match the adopted base"
        )
    base_reference_target = _confirmation_reference_target(settings["confirmation_ref"])
    candidate_reference_target = _confirmation_reference_target(
        candidate["base_confirmation_ref"]
    )
    if (
        base_reference_target is None
        or candidate_reference_target != base_reference_target
    ):
        raise SettingsInputError(
            "detailedRulesCandidate.base_confirmation_ref does not match the base reference"
        )

    analysis = candidate["analysis_basis"]
    if analysis["p05_rule_derivation_source_body_ids"] != list(P05_SOURCE_BODY_IDS):
        raise SettingsInputError(
            "detailedRulesCandidate.analysis_basis.p05_rule_derivation_source_body_ids "
            "must be the five specified source IDs"
        )
    if (
        analysis["p05_rule_derivation_uses_other_127_or_full_132_aggregate"]
        is not False
    ):
        raise SettingsInputError(
            "detailedRulesCandidate.analysis_basis must exclude other records from P05 derivation"
        )

    detail_patterns = candidate["patterns"]
    detail_ids = [item["pattern_id"] for item in detail_patterns]
    if len(set(detail_ids)) != 5 or set(detail_ids) != set(PATTERN_IDS):
        raise SettingsInputError(
            "detailedRulesCandidate.patterns must contain P01–P05 once"
        )
    detail_by_id = {item["pattern_id"]: item for item in detail_patterns}
    base_by_id = {item["pattern_id"]: item for item in settings["patterns"]}
    mapped_patterns: dict[str, tuple[dict[str, Any], ...]] = {}
    for pattern_id in PATTERN_IDS:
        # The source uses an ID fragment such as #/patterns/P01, not an array index.
        # Match the candidate row by pattern_id even if its array order changes.
        base_detail_ref = base_by_id[pattern_id]["detailed_rules_ref"]
        if not base_detail_ref.endswith(f"#/patterns/{pattern_id}"):
            raise SettingsInputError(
                f"externalSettings.patterns[{pattern_id}].detailed_rules_ref is invalid"
            )
        detail_pattern = detail_by_id[pattern_id]
        markdown_ref = f"../詳細書き方/{pattern_id}.md"
        if unquote(detail_pattern["source_markdown"]) != markdown_ref:
            raise SettingsInputError(
                f"detailedRulesCandidate.patterns[{pattern_id}].source_markdown is inconsistent"
            )
        rules = detail_pattern["rules"]
        sequences = [rule["sequence"] for rule in rules]
        rule_ids = [rule["rule_id"] for rule in rules]
        expected_rule_ids = [
            f"{pattern_id}-W{sequence:02d}" for sequence in range(1, 18)
        ]
        if sequences != list(range(1, 18)) or rule_ids != expected_rule_ids:
            raise SettingsInputError(
                f"detailedRulesCandidate.patterns[{pattern_id}].rules must contain W01–W17 in order"
            )
        mapped_patterns[pattern_id] = tuple(_map_pattern_rule(rule) for rule in rules)

    p05_basis = detail_by_id["P05"]["rule_derivation_basis"]
    if (
        p05_basis.get("source_body_ids") != list(P05_SOURCE_BODY_IDS)
        or p05_basis.get("other_127_or_full_132_used_to_derive_P05_rules") is not False
    ):
        raise SettingsInputError(
            "detailedRulesCandidate.patterns[P05].rule_derivation_basis is out of scope"
        )

    common_rules = candidate["common_rules"]
    common_ids = [rule["rule_id"] for rule in common_rules]
    if common_ids != [f"W-C{sequence:02d}" for sequence in range(1, 15)]:
        raise SettingsInputError(
            "detailedRulesCandidate.common_rules must contain W-C01–W-C14 in order"
        )
    if "concrete_examples" in common_rules[0]:
        raise SettingsInputError(
            "detailedRulesCandidate.common_rules must retain its distinct field contract"
        )

    return DetailedRulesCandidate(
        available=True,
        version=candidate["version"],
        status=candidate["status"],
        adopted=False,
        enabled=False,
        reference=settings["detailed_rules_ref"],
        pattern_rules=mapped_patterns,
        common_rules=tuple(_map_common_rule(rule) for rule in common_rules),
        p05_source_body_ids=P05_SOURCE_BODY_IDS,
    )


def _serialize(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SettingsInputError("settings contain a non-JSON value") from exc


def _validate_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsInputError(f"{field} must be an offset-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SettingsInputError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SettingsInputError(f"{field} must include a timezone offset")
    return value


def _settings_entity(settings: Mapping[str, Any], created_at: str) -> dict[str, Any]:
    entity = {
        "version": settings["version"],
        "confirmed_at": settings["confirmed_at"],
        "genres": [
            {key: source[key] for key in ("genre_id", "name", "definition")}
            for source in settings["genres"]
        ],
        "themes": [
            {
                key: source[key]
                for key in (
                    "theme_id",
                    "genre_id",
                    "name",
                    "definition",
                    "included_examples",
                    "boundary_examples",
                    "evidence_refs",
                    "material_count",
                    "material_kind",
                    "state",
                    "selection_count",
                )
            }
            for source in settings["themes"]
        ],
        "pattern_refs": list(settings["pattern_refs"]),
        "change_reason": _serialize(
            {
                "format": _INITIAL_IMPORT_REASON_FORMAT,
                "reason": "Register the adopted external analysis result as settings.",
                "source": {
                    "settings_path": "docs/specifications/preparation/初期設定引渡し.json",
                    "result_index_ref": settings["result_index_ref"]["path"],
                    "confirmation_ref": settings["confirmation_ref"],
                },
            }
        ),
        "created_at": created_at,
    }
    validate_entity("settings_version", entity)
    return entity


def _row_entity(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "version": row["version"],
        "confirmed_at": row["confirmed_at"],
        "genres": json.loads(row["genres_json"]),
        "themes": json.loads(row["themes_json"]),
        "pattern_refs": json.loads(row["pattern_refs_json"]),
        "change_reason": row["change_reason"],
        "created_at": row["created_at"],
    }


def _normalized_import_reason(value: str) -> Any:
    """Treat the documented settings relocation as the same import source."""
    try:
        reason = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value
    if (
        isinstance(reason, dict)
        and reason.get("format") == _INITIAL_IMPORT_REASON_FORMAT
    ):
        source = reason.get("source")
        if (
            isinstance(source, dict)
            and source.get("settings_path")
            == "plan/イナゴスキル事前準備/初期設定引渡し.json"
        ):
            source["settings_path"] = (
                "docs/specifications/preparation/初期設定引渡し.json"
            )
    return reason


def _row_matches_entity(row: sqlite3.Row, entity: Mapping[str, Any]) -> bool:
    return (
        row["confirmed_at"] == entity["confirmed_at"]
        and json.loads(row["genres_json"]) == entity["genres"]
        and json.loads(row["themes_json"]) == entity["themes"]
        and json.loads(row["pattern_refs_json"]) == entity["pattern_refs"]
        and _normalized_import_reason(row["change_reason"])
        == _normalized_import_reason(entity["change_reason"])
    )


def _store_settings_version(
    connection: sqlite3.Connection, entity: Mapping[str, Any]
) -> None:
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {_SETTINGS_SAVEPOINT}")
    try:
        existing = connection.execute(
            "SELECT * FROM settings_version WHERE version = ?", (entity["version"],)
        ).fetchone()
        if existing is not None:
            if not _row_matches_entity(existing, entity):
                raise IdempotencyConflict(
                    f"settings version {entity['version']} already has different content"
                )
        else:
            connection.execute(
                """
                INSERT INTO settings_version
                    (version, confirmed_at, genres_json, themes_json,
                     pattern_refs_json, change_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity["version"],
                    entity["confirmed_at"],
                    _serialize(entity["genres"]),
                    _serialize(entity["themes"]),
                    _serialize(entity["pattern_refs"]),
                    entity["change_reason"],
                    entity["created_at"],
                ),
            )
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {_SETTINGS_SAVEPOINT}")
    except BaseException:
        if owns_transaction and connection.in_transaction:
            connection.rollback()
        elif connection.in_transaction:
            connection.execute(f"ROLLBACK TO SAVEPOINT {_SETTINGS_SAVEPOINT}")
            connection.execute(f"RELEASE SAVEPOINT {_SETTINGS_SAVEPOINT}")
        raise


def _active_settings_row(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM settings_version ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise SettingsInputError("no settings_version is registered")
    return row


def _detailed_reference(source: Mapping[str, Any]) -> str:
    return f"{source['detailed_rules_ref']}@{source['detailed_rules_version']}"


def _read_change_reason(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _runtime_from_active_row(
    connection: sqlite3.Connection,
    source: Mapping[str, Any],
    candidate: DetailedRulesCandidate,
) -> SettingsRuntime:
    active_row = _active_settings_row(connection)
    active_entity = _row_entity(active_row)
    change_record = _read_change_reason(active_row["change_reason"])
    activation = change_record.get("detailed_rules_adoption", {})
    diff = change_record.get("diff", {})
    if not isinstance(activation, Mapping):
        activation = {}
    if not isinstance(diff, Mapping):
        diff = {}
    active_detail_version = activation.get("version")
    confirmation_ref = activation.get("confirmation_ref")
    detail_ref = _detailed_reference(source)
    active_enabled = (
        candidate.available
        and candidate.adopted is False
        and change_record.get("format") == _RUN_CHANGE_REASON_FORMAT
        and activation.get("adopted") is True
        and active_detail_version == candidate.version
        and activation.get("reference") == candidate.reference
        and isinstance(confirmation_ref, str)
        and bool(confirmation_ref.strip())
        and activation.get("confirmed_at") == active_entity["confirmed_at"]
        and diff.get("from_version") == source["version"]
        and diff.get("to_version") == active_entity["version"]
        and diff.get("pattern_refs_added") == [detail_ref]
        and diff.get("detailed_rules_enabled") == {"before": False, "after": True}
        and detail_ref in active_entity["pattern_refs"]
    )
    candidate = replace(candidate, enabled=active_enabled)
    base_by_id = {item["pattern_id"]: item for item in source["patterns"]}
    basic_patterns = tuple(
        {
            "pattern_id": pattern_id,
            "name": base_by_id[pattern_id]["name"],
            "structure_candidate": base_by_id[pattern_id]["structure_candidate"],
            "reference": base_by_id[pattern_id]["reference"],
        }
        for pattern_id in PATTERN_IDS
    )
    return SettingsRuntime(
        registered_version=source["version"],
        active_basic_version=active_entity["version"],
        confirmed_at=active_entity["confirmed_at"],
        confirmation_ref=source["confirmation_ref"],
        genres=tuple(active_entity["genres"]),
        themes=tuple(active_entity["themes"]),
        pattern_refs=tuple(active_entity["pattern_refs"]),
        patterns=basic_patterns,
        body_length_guidance=dict(source["body_length_guidance"]),
        tag_guidance=dict(source["tag_guidance"]),
        copy_settings={"mandatory": False, "include_url_in_body": False},
        analysis_basis=dict(source["analysis_basis"]),
        detailed_rules_candidate=candidate,
        detailed_rules_enabled=active_enabled,
        detailed_rules_version=candidate.version if active_enabled else None,
        detailed_rules_confirmation_ref=confirmation_ref if active_enabled else None,
    )


def register_external_settings(
    connection: sqlite3.Connection,
    adopted_settings: Mapping[str, Any],
    detailed_rules: Mapping[str, Any] | None = None,
    *,
    created_at: str,
) -> SettingsRuntime:
    """Validate and idempotently register the adopted basic settings version."""

    settings = _validate_basic_settings(adopted_settings)
    created_at = _validate_timestamp(created_at, "created_at")
    entity = _settings_entity(settings, created_at)

    try:
        candidate = load_detailed_rules_candidate(settings, detailed_rules)
    except SettingsInputError as exc:
        # The basic adopted settings remain usable when the optional candidate is
        # absent or incomplete; candidate data is never enabled by this path.
        candidate = _unavailable_candidate(settings, str(exc))

    _store_settings_version(connection, entity)
    return _runtime_from_active_row(connection, settings, candidate)


def _build_adopted_version(
    base_row: sqlite3.Row,
    source: Mapping[str, Any],
    candidate: DetailedRulesCandidate,
    owner_adoption: Mapping[str, Any],
    new_version: str,
    created_at: str,
) -> dict[str, Any]:
    if (
        not isinstance(new_version, str)
        or not new_version.strip()
        or new_version != new_version.strip()
    ):
        raise SettingsInputError("new_version is required")
    if new_version == base_row["version"]:
        raise SettingsInputError(
            "detailed-rule adoption requires a new settings version"
        )
    adoption = _validate_document("owner_adoption", owner_adoption)
    if adoption["target_version"] != candidate.version:
        raise SettingsInputError(
            "ownerAdoption.target_version must match the exact detailed candidate version"
        )
    if not adoption["confirmation_ref"].strip():
        raise SettingsInputError("ownerAdoption.confirmation_ref is required")
    if not adoption["reason"].strip():
        raise SettingsInputError("ownerAdoption.reason is required")
    created_at = _validate_timestamp(created_at, "created_at")

    base_entity = _row_entity(base_row)
    detail_ref = _detailed_reference(source)
    adoption_metadata = {
        "format": _RUN_CHANGE_REASON_FORMAT,
        "reason": adoption["reason"],
        "source": _read_change_reason(base_entity["change_reason"]).get("source", {}),
        "diff": {
            "from_version": base_entity["version"],
            "to_version": new_version,
            "pattern_refs_added": [detail_ref],
            "detailed_rules_enabled": {"before": False, "after": True},
        },
        "detailed_rules_adoption": {
            "adopted": True,
            "version": candidate.version,
            "reference": candidate.reference,
            "confirmation_ref": adoption["confirmation_ref"],
            "confirmed_at": adoption["confirmed_at"],
        },
    }
    refs = list(base_entity["pattern_refs"])
    if detail_ref not in refs:
        refs.append(detail_ref)
    entity = {
        **base_entity,
        "version": new_version,
        "confirmed_at": adoption["confirmed_at"],
        "pattern_refs": refs,
        "change_reason": _serialize(adoption_metadata),
        "created_at": created_at,
    }
    validate_entity("settings_version", entity)
    return entity


def adopt_detailed_rules(
    connection: sqlite3.Connection,
    adopted_settings: Mapping[str, Any],
    detailed_rules: Mapping[str, Any],
    owner_adoption: Mapping[str, Any] | None,
    *,
    new_version: str,
    created_at: str,
) -> SettingsRuntime:
    """Create a new settings version only for a recorded, exact owner decision."""

    source = _validate_basic_settings(adopted_settings)
    if owner_adoption is None:
        raise SettingsInputError("a recorded owner adoption answer is required")
    candidate = load_detailed_rules_candidate(source, detailed_rules)
    if not candidate.available or candidate.adopted:
        raise SettingsInputError("the detailed rules are not an inactive candidate")

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {_ADOPTION_SAVEPOINT}")
    try:
        base_version = source["version"]
        base_row = connection.execute(
            "SELECT * FROM settings_version WHERE version = ?", (base_version,)
        ).fetchone()
        if base_row is None:
            raise SettingsInputError(
                "the adopted basic settings version is not registered"
            )
        existing_new = connection.execute(
            "SELECT * FROM settings_version WHERE version = ?", (new_version,)
        ).fetchone()
        active_row = _active_settings_row(connection)
        if existing_new is None and active_row["version"] != base_version:
            raise SettingsInputError(
                "the active settings version changed; create a decision from the current version"
            )

        entity = _build_adopted_version(
            base_row,
            source,
            candidate,
            owner_adoption,
            new_version,
            created_at,
        )
        _store_settings_version(connection, entity)
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {_ADOPTION_SAVEPOINT}")
    except BaseException:
        if owns_transaction and connection.in_transaction:
            connection.rollback()
        elif connection.in_transaction:
            connection.execute(f"ROLLBACK TO SAVEPOINT {_ADOPTION_SAVEPOINT}")
            connection.execute(f"RELEASE SAVEPOINT {_ADOPTION_SAVEPOINT}")
        raise
    return _runtime_from_active_row(connection, source, candidate)
