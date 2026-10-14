"""Validate persisted JSON and job results against the versioned schemas."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "rakuten-room-kai" / "orchestrator" / "schemas"


class SchemaValidationError(ValueError):
    pass


def validate_document(name: str, document: object) -> None:
    schema_path = SCHEMA_DIR / f"{name}.schema.json"
    if not schema_path.is_file():
        raise SchemaValidationError(f"unknown schema: {name}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        error = sorted(errors, key=lambda item: str(item.json_path))[0]
        raise SchemaValidationError(f"{name} {error.json_path}: {error.message}")


def validate_product_store(document: dict) -> None:
    validate_document("product-ledger", document)
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_codes: set[str] = set()
    seen_models: set[str] = set()
    highest_number = 0
    for product in document["products"]:
        product_id = product["id"]
        highest_number = max(highest_number, int(product_id[1:]))
        if product_id in seen_ids:
            raise SchemaValidationError(f"duplicate product ID: {product_id}")
        seen_ids.add(product_id)
        url = product["normalized_url"]
        if url in seen_urls:
            raise SchemaValidationError(f"duplicate normalized URL: {url}")
        seen_urls.add(url)
        code = product["item_code"]
        if code is not None and code in seen_codes:
            raise SchemaValidationError(f"duplicate item code: {code}")
        if code is not None:
            seen_codes.add(code)
        model = product["model_candidate"]
        if model is not None and model in seen_models:
            raise SchemaValidationError(f"duplicate model candidate: {model}")
        if model is not None:
            seen_models.add(model)
    if document["next_id"] <= highest_number:
        raise SchemaValidationError("next_id must exceed every assigned product ID")


def validate_json_file(name: str, path: Path) -> object:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if name == "product-ledger":
        validate_product_store(document)
    else:
        validate_document(name, document)
    return document
