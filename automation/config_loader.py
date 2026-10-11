"""Read the versioned configuration shared by every entry point."""

import json
from pathlib import Path

from automation.schema_validator import validate_document


CONFIG_DIR = Path(__file__).resolve().parents[1] / "rakuten-room-kai" / "orchestrator"


def _read(name: str, base: Path = CONFIG_DIR) -> dict:
    path = base / name
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_document(path.stem, data)
    if data.get("version") != 1:
        raise ValueError(f"unsupported config version: {name}")
    return data


def load_config(base: Path = CONFIG_DIR) -> dict:
    data = _read("automation-config.json", base)
    if sum(data["genre_targets"].values()) != data["daily_target"]:
        raise ValueError("genre targets do not match daily target")
    if data["research_batch_size"] > data["research_max_page_checks"]:
        raise ValueError("research batch size exceeds the page-check limit")
    if data["research_max_page_checks"] < data["daily_target"]:
        raise ValueError("research page-check limit must cover the daily target")
    if data["operation_start_date"] <= data["preparation_date"]:
        raise ValueError("operation start must follow preparation date")
    return data


def load_model_policy(base: Path = CONFIG_DIR) -> dict:
    data = _read("model-policy.json", base)
    if not data["astra_exception_only"]:
        raise ValueError("Astra must remain exception only")
    for name, job in data["jobs"].items():
        if job["normal_model"].endswith("astra"):
            raise ValueError(f"Astra in normal policy: {name}")
    return data


def load_workflow(base: Path = CONFIG_DIR) -> dict:
    data = _read("workflow.json", base)
    for name, flow in data["flows"].items():
        seen = set()
        for stage in flow["stages"]:
            if stage["id"] in seen or not set(stage["depends_on"]) <= seen:
                raise ValueError(f"invalid workflow DAG: {name}/{stage['id']}")
            seen.add(stage["id"])
    return data
