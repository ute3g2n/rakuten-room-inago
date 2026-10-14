"""Persist only the public product observation tied to a ROOM source row."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping, Protocol

from automation.inago.orchestrator import OfferIntakeRequest
from automation.inago.repository import (
    LEGACY_UNASSIGNED_THEME_ID,
    offer_needs_name_observation,
    validate_entity,
)
from automation.inago.selection import (
    identify_offer_from_saved_url,
    normalize_offer_url,
)


class OfferIntakeError(ValueError):
    """The product observation cannot safely be joined to its ROOM row."""


class ProductFactPort(Protocol):
    fixture_only: bool

    def product_fact(self, evidence_ref: str) -> dict[str, Any] | None:
        """Return the exact non-expired fact captured for one source row."""


class SQLitePublicOfferIntake:
    """Join a captured product label to its URL identity and save one offer."""

    _SAVEPOINT = "inago_public_offer_intake"

    def __init__(self, source: ProductFactPort) -> None:
        if not callable(getattr(source, "product_fact", None)):
            raise TypeError("source must provide product_fact(evidence_ref)")
        self.source = source
        self.fixture_only = getattr(source, "fixture_only", False) is True

    def persist_offer(
        self, connection: sqlite3.Connection, request: OfferIntakeRequest
    ) -> None:
        fact = self.source.product_fact(request.evidence_ref)
        validated = self._validate_fact(request, fact)
        shop_id, item_id = request.offer_key.split(":", maxsplit=1)
        name = validated["name"] or "商品名未取得"
        image_ref = request.image_ref or validated["image_ref"]
        facts = [
            {
                "field": "name",
                "value": validated["name"],
                "availability": "observed" if validated["name"] else "unknown",
                "source_ref": request.evidence_ref,
                "observed_at": request.observed_at,
                "original_display": validated["name"],
            }
        ]
        entity = {
            "offer_key": request.offer_key,
            "shop_id": shop_id,
            "item_id": item_id,
            "name": name,
            "url": validated["url"],
            "image_refs": [image_ref] if image_ref else [],
            "main_theme_id": request.theme_id,
            "aux_theme_ids": [],
            "first_seen": request.observed_at,
            "last_seen": request.observed_at,
            "facts": facts,
        }
        validate_entity("offer", entity)
        encoded_facts = json.dumps(
            facts, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        encoded_images = json.dumps(
            entity["image_refs"],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        owns_transaction = not connection.in_transaction
        if owns_transaction:
            connection.execute("BEGIN IMMEDIATE")
        else:
            connection.execute(f"SAVEPOINT {self._SAVEPOINT}")
        try:
            existing = connection.execute(
                "SELECT shop_id,item_id,url,main_theme_id,first_seen,facts_json,"
                "image_refs_json "
                "FROM offer WHERE offer_key=?",
                (request.offer_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing[0] != shop_id
                    or existing[1] != item_id
                    or normalize_offer_url(str(existing[2])) != validated["url"]
                ):
                    raise OfferIntakeError("persisted_identity_mismatch")
                if offer_needs_name_observation(existing[3], existing[5]):
                    existing_images = json.loads(existing[6])
                    observed_images = list(
                        dict.fromkeys([*existing_images, *entity["image_refs"]])
                    )
                    enriched = {
                        **entity,
                        "image_refs": observed_images,
                        "first_seen": str(existing[4]),
                    }
                    validate_entity("offer", enriched)
                    encoded_updated_images = json.dumps(
                        observed_images,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    connection.execute(
                        """UPDATE offer SET name=?,url=?,image_refs_json=?,
                               main_theme_id=?,last_seen=?,facts_json=?
                           WHERE offer_key=?""",
                        (
                            name,
                            validated["url"],
                            encoded_updated_images,
                            (
                                request.theme_id
                                if existing[3] == LEGACY_UNASSIGNED_THEME_ID
                                else existing[3]
                            ),
                            request.observed_at,
                            encoded_facts,
                            request.offer_key,
                        ),
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO offer
                        (offer_key,shop_id,item_id,name,url,image_refs_json,
                         main_theme_id,aux_theme_ids_json,first_seen,last_seen,facts_json)
                    VALUES (?,?,?,?,?,?,?,'[]',?,?,?)
                    """,
                    (
                        request.offer_key,
                        shop_id,
                        item_id,
                        name,
                        validated["url"],
                        encoded_images,
                        request.theme_id,
                        request.observed_at,
                        request.observed_at,
                        encoded_facts,
                    ),
                )
            if owns_transaction:
                connection.commit()
            else:
                connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
        except BaseException:
            if owns_transaction:
                if connection.in_transaction:
                    connection.rollback()
            elif connection.in_transaction:
                connection.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
                connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
            raise

    @staticmethod
    def _validate_fact(
        request: OfferIntakeRequest, fact: Mapping[str, Any] | None
    ) -> dict[str, str | None]:
        if not isinstance(fact, Mapping):
            raise OfferIntakeError("product_fact_missing")
        try:
            identity = identify_offer_from_saved_url(request.item_url)
            fact_identity = identify_offer_from_saved_url(fact.get("item_url"))
            normalized_url = normalize_offer_url(request.item_url)
            fact_url = normalize_offer_url(str(fact.get("item_url")))
        except (TypeError, ValueError) as exc:
            raise OfferIntakeError("product_url_invalid") from exc
        if (
            identity.offer_key != request.offer_key
            or fact_identity.offer_key != request.offer_key
            or fact.get("offer_key") != request.offer_key
        ):
            raise OfferIntakeError("identity_mismatch")
        if (
            fact.get("evidence_ref") != request.evidence_ref
            or fact_url != normalized_url
            or fact.get("observed_at") != request.observed_at
        ):
            raise OfferIntakeError("evidence_mismatch")
        if fact.get("origin") != "public_product_image_label":
            raise OfferIntakeError("product_fact_origin_invalid")
        name = fact.get("name")
        if name is not None and not isinstance(name, str):
            raise OfferIntakeError("product_name_invalid")
        normalized_name = name.strip() if isinstance(name, str) else None
        if normalized_name == "":
            normalized_name = None
        image_ref = fact.get("image_ref")
        if image_ref is not None and not isinstance(image_ref, str):
            raise OfferIntakeError("image_reference_invalid")
        if request.image_ref and image_ref and request.image_ref != image_ref:
            raise OfferIntakeError("evidence_mismatch")
        return {
            "name": normalized_name,
            "url": normalized_url,
            "image_ref": image_ref,
        }
