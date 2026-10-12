"""Deterministic product and theme selection contracts for STEP 07."""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from automation.inago.clock import JST
from automation.inago.contracts import IdempotencyConflict, RejectedContract


MIN_LIKES = 300
MAX_POSTS_PER_SEARCH = 50
PRIMARY_REASON_ORDER = (
    "non_product",
    "likes_unreadable",
    "below_like_threshold",
    "offer_id_unresolved",
    "same_search_offer_duplicate",
    "same_day_selected",
    "permanently_posted",
    "pending_posted_report",
    "active_reservation",
    "generation_failed",
    "completed",
    "eligible",
)
_PRIMARY_REASONS = frozenset(PRIMARY_REASON_ORDER)
_TRACKING_NAMES = frozenset(
    {"scid", "iasid", "gclid", "fbclid", "yclid", "s-id", "s_id"}
)
_SELECTION_EVENT_KINDS = frozenset({"initial", "additional"})
_EVENT_SAVEPOINT = "inago_theme_selection_events"


class _ProductUrlError(RejectedContract):
    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    """Observed shop/item identity; unresolved observations have no offer key."""

    status: str
    shop_id: str | None
    item_id: str | None
    offer_key: str | None
    normalized_url: str | None
    reason: str | None = None


def _identifier(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or ":" in value
        or any(character.isspace() for character in value)
        or "\x00" in value
    ):
        raise RejectedContract(f"{field} is not a reversible product identifier")
    return value


def make_offer_key(shop_id: str, item_id: str) -> str:
    """Build the reversible SQLite key from the two observed identifier fields."""

    shop = _identifier(shop_id, "shop_id")
    item = _identifier(item_id, "item_id")
    return f"{shop}:{item}"


def split_offer_key(offer_key: str) -> tuple[str, str]:
    """Decode a key without guessing when either identifier is ambiguous."""

    if not isinstance(offer_key, str) or offer_key.count(":") != 1:
        raise RejectedContract("offer_key must contain one shop/item delimiter")
    shop_id, item_id = offer_key.split(":", 1)
    make_offer_key(shop_id, item_id)
    return shop_id, item_id


def _product_url_parts(url: str) -> tuple[str, str, str]:
    if not isinstance(url, str) or not url.strip():
        raise _ProductUrlError("product URL is missing", "url_missing")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise _ProductUrlError(
            "product URL authority is malformed", "product_url_unreadable"
        ) from exc
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise _ProductUrlError(
            "product URL must use HTTP or HTTPS", "product_url_unreadable"
        )
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise _ProductUrlError(
            "product URL authority is not a supported product URL",
            "product_url_unreadable",
        )
    host = (parsed.hostname or "").casefold()
    if host != "item.rakuten.co.jp":
        raise _ProductUrlError(
            "product URL host is not the observed Rakuten item host",
            "unsupported_product_host",
        )
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise _ProductUrlError(
            "product URL does not identify a shop and item path",
            "product_url_unreadable",
        )
    try:
        shop_id = _identifier(parts[0], "shop_id")
        item_id = _identifier(parts[1], "item_id")
    except RejectedContract as exc:
        raise _ProductUrlError(
            "product URL path has an invalid shop/item identifier", "id_invalid"
        ) from exc
    return parsed.scheme.casefold(), parsed.path, f"{shop_id}:{item_id}"


def normalize_offer_url(url: str) -> str:
    """Normalize a Rakuten item URL and remove known tracking-only parameters."""

    scheme, path, _ = _product_url_parts(url)
    parsed = urlsplit(url.strip())
    kept_query = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        folded_name = name.casefold()
        if folded_name.startswith(("utm_", "icm_")) or folded_name in _TRACKING_NAMES:
            continue
        kept_query.append((name, value))
    query = urlencode(kept_query, doseq=True)
    return urlunsplit((scheme, "item.rakuten.co.jp", path, query, ""))


def identify_offer(
    item_url: str | None,
    *,
    observed_shop_id: str | None,
    observed_item_id: str | None,
) -> ProductIdentity:
    """Resolve an offer only when the URL and observed IDs agree exactly."""

    if not isinstance(item_url, str) or not item_url.strip():
        return ProductIdentity(
            "unresolved", observed_shop_id, observed_item_id, None, None, "url_missing"
        )
    if observed_shop_id is None or observed_item_id is None:
        return ProductIdentity(
            "unresolved", observed_shop_id, observed_item_id, None, None, "id_missing"
        )
    try:
        shop_id = _identifier(observed_shop_id, "shop_id")
        item_id = _identifier(observed_item_id, "item_id")
    except RejectedContract:
        return ProductIdentity(
            "unresolved", observed_shop_id, observed_item_id, None, None, "id_invalid"
        )
    try:
        normalized_url = normalize_offer_url(item_url)
        _, _, path_key = _product_url_parts(item_url)
    except _ProductUrlError as exc:
        return ProductIdentity("unresolved", shop_id, item_id, None, None, exc.reason)
    offer_key = make_offer_key(shop_id, item_id)
    if path_key != offer_key:
        return ProductIdentity(
            "unresolved", shop_id, item_id, None, normalized_url, "url_id_mismatch"
        )
    return ProductIdentity("identified", shop_id, item_id, offer_key, normalized_url)


@dataclass(frozen=True, slots=True)
class SourcePost:
    """One observed post within one search, before product-key aggregation."""

    source_key: str
    offer_key: str | None
    likes: int | None
    image_ref: str | None = None
    is_product: bool = True
    order_index: int | None = None


@dataclass(frozen=True, slots=True)
class OfferHistory:
    """The persisted exclusions that must be re-read before candidate selection."""

    same_day_selected: frozenset[str] = frozenset()
    permanently_posted: frozenset[str] = frozenset()
    pending_posted: frozenset[str] = frozenset()
    active_reservations: frozenset[str] = frozenset()
    generation_failed: frozenset[str] = frozenset()
    completed: frozenset[str] = frozenset()


def load_offer_history(connection: sqlite3.Connection, run_id: str) -> OfferHistory:
    """Read current-run and durable exclusions from one SQLite read snapshot."""

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN")
    else:
        connection.execute(f"SAVEPOINT {_EVENT_SAVEPOINT}_history")
    try:
        run = connection.execute(
            "SELECT mode, status FROM run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None or run["mode"] != "daily":
            raise RejectedContract("offer history requires a known daily run")
        if run["status"] not in {"running", "partial"}:
            raise RejectedContract("daily run is not accepting candidate selection")

        def keys(query: str, parameters: tuple[object, ...] = ()) -> frozenset[str]:
            return frozenset(
                str(row[0]) for row in connection.execute(query, parameters)
            )

        completed = keys(
            "SELECT DISTINCT offer_key FROM draft WHERE run_id = ?", (run_id,)
        )
        same_day = keys(
            """
            SELECT DISTINCT attempt.offer_key
              FROM generation_attempt AS attempt
             WHERE attempt.run_id = ?
               AND NOT EXISTS (
                   SELECT 1 FROM draft
                    WHERE draft.run_id = attempt.run_id
                      AND draft.offer_key = attempt.offer_key
               )
               AND NOT (
                   attempt.attempt_no = 3
                   AND attempt.outcome IN ('empty', 'error')
               )
            """,
            (run_id,),
        )
        posted = keys(
            """
            SELECT offer_key FROM exclusion_key WHERE effective = 1
            UNION
            SELECT offer_key FROM publication_event WHERE valid = 1
            """
        )
        pending_posted = keys(
            "SELECT DISTINCT offer_key FROM human_action "
            "WHERE action = 'posted' AND apply_status = 'pending'"
        )
        reservations = keys(
            "SELECT DISTINCT offer_key FROM reservation WHERE status = 'active'"
        )
        generation_failed = keys(
            """
            SELECT DISTINCT offer_key FROM generation_attempt
             WHERE run_id = ? AND attempt_no = 3 AND outcome IN ('empty', 'error')
            """,
            (run_id,),
        )
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_history")
        return OfferHistory(
            same_day_selected=same_day,
            permanently_posted=posted,
            pending_posted=pending_posted,
            active_reservations=reservations,
            generation_failed=generation_failed,
            completed=completed,
        )
    except BaseException:
        if owns_transaction:
            if connection.in_transaction:
                connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {_EVENT_SAVEPOINT}_history")
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_history")
        raise


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    source_key: str
    offer_key: str | None
    primary_reason: str
    secondary_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Representative:
    offer_key: str
    source_key: str
    likes: int
    image_ref: str | None
    order_index: int | None


@dataclass(frozen=True, slots=True)
class SelectionResult:
    seed: str
    observed_posts: int
    decisions: tuple[CandidateDecision, ...]
    representatives: tuple[Representative, ...]
    eligible_representatives: tuple[Representative, ...]

    @property
    def primary_reason_counts(self) -> dict[str, int]:
        counts = Counter(item.primary_reason for item in self.decisions)
        return {
            reason: counts[reason] for reason in PRIMARY_REASON_ORDER if counts[reason]
        }


_HISTORY_REASON_ORDER = (
    ("same_day_selected", "same_day_selected"),
    ("permanently_posted", "permanently_posted"),
    ("pending_posted", "pending_posted_report"),
    ("active_reservations", "active_reservation"),
    ("generation_failed", "generation_failed"),
    ("completed", "completed"),
)


def _history_reasons(offer_key: str, history: OfferHistory) -> tuple[str, ...]:
    return tuple(
        reason
        for field, reason in _HISTORY_REASON_ORDER
        if offer_key in getattr(history, field)
    )


def _seeded_first(values: Sequence[SourcePost], seed: str, context: str) -> SourcePost:
    tied = list(sorted(values, key=lambda post: post.source_key))
    random.Random(f"{seed}|{context}").shuffle(tied)
    return tied[0]


def _validate_source_post(post: SourcePost) -> None:
    if (
        not isinstance(post.source_key, str)
        or not post.source_key.strip()
        or post.source_key != post.source_key.strip()
    ):
        raise RejectedContract("source_key must be non-empty")
    if not isinstance(post.is_product, bool):
        raise RejectedContract("is_product must be a boolean observation")
    if post.likes is not None and (
        isinstance(post.likes, bool)
        or not isinstance(post.likes, int)
        or post.likes < 0
    ):
        raise RejectedContract("likes must be a non-negative integer or unknown")
    if post.order_index is not None and (
        isinstance(post.order_index, bool)
        or not isinstance(post.order_index, int)
        or not 1 <= post.order_index <= MAX_POSTS_PER_SEARCH
    ):
        raise RejectedContract("order_index must be within the observed 1..50 order")


def evaluate_sources(
    source_posts: Sequence[SourcePost],
    *,
    seed: str,
    history: OfferHistory,
) -> SelectionResult:
    """Classify every source once and rank one maximum-like representative per offer."""

    if not isinstance(seed, str) or not seed.strip():
        raise RejectedContract("selection requires the saved non-empty run seed")
    if not isinstance(source_posts, Sequence) or isinstance(source_posts, (str, bytes)):
        raise RejectedContract("source_posts must be a sequence")
    if len(source_posts) > MAX_POSTS_PER_SEARCH:
        raise RejectedContract("one search may contribute at most 50 observed posts")
    if not isinstance(history, OfferHistory):
        raise RejectedContract("fresh offer history is required before selection")
    seen_source_keys: set[str] = set()
    seen_order_indices: set[int] = set()
    decisions: dict[str, CandidateDecision] = {}
    grouped: dict[str, list[SourcePost]] = defaultdict(list)

    for post in source_posts:
        if not isinstance(post, SourcePost):
            raise RejectedContract("source_posts contain an unsupported record")
        _validate_source_post(post)
        if post.source_key in seen_source_keys:
            raise RejectedContract("one search contains a repeated source_key")
        seen_source_keys.add(post.source_key)
        if post.order_index is not None:
            if post.order_index in seen_order_indices:
                raise RejectedContract(
                    "one search cannot repeat an internal order index"
                )
            seen_order_indices.add(post.order_index)
        if not post.is_product:
            decisions[post.source_key] = CandidateDecision(
                post.source_key, post.offer_key, "non_product"
            )
        elif post.likes is None:
            decisions[post.source_key] = CandidateDecision(
                post.source_key, post.offer_key, "likes_unreadable"
            )
        elif post.likes < MIN_LIKES:
            decisions[post.source_key] = CandidateDecision(
                post.source_key, post.offer_key, "below_like_threshold"
            )
        else:
            try:
                if post.offer_key is None:
                    raise RejectedContract("offer key is not observed")
                split_offer_key(post.offer_key)
            except RejectedContract:
                decisions[post.source_key] = CandidateDecision(
                    post.source_key, None, "offer_id_unresolved"
                )
            else:
                grouped[post.offer_key].append(post)

    representative_by_source: dict[str, Representative] = {}
    for offer_key, posts in grouped.items():
        maximum = max(post.likes for post in posts if post.likes is not None)
        tied = [post for post in posts if post.likes == maximum]
        representative_post = _seeded_first(tied, seed, f"representative:{offer_key}")
        representative = Representative(
            offer_key=offer_key,
            source_key=representative_post.source_key,
            likes=maximum,
            image_ref=representative_post.image_ref,
            order_index=representative_post.order_index,
        )
        representative_by_source[representative.source_key] = representative
        history_reasons = _history_reasons(offer_key, history)
        if history_reasons:
            primary, *secondary = history_reasons
        else:
            primary, secondary = "eligible", []
        decisions[representative.source_key] = CandidateDecision(
            representative.source_key,
            offer_key,
            primary,
            tuple(secondary),
        )
        for post in posts:
            if post.source_key == representative.source_key:
                continue
            decisions[post.source_key] = CandidateDecision(
                post.source_key,
                offer_key,
                "same_search_offer_duplicate",
                history_reasons,
            )

    representatives = list(representative_by_source.values())
    by_likes: dict[int, list[Representative]] = defaultdict(list)
    for item in representatives:
        by_likes[item.likes].append(item)
    ranked: list[Representative] = []
    for likes in sorted(by_likes, reverse=True):
        tied = sorted(by_likes[likes], key=lambda item: item.offer_key)
        random.Random(f"{seed}|offer-rank:{likes}").shuffle(tied)
        ranked.extend(tied)

    decision_by_source = {key: decisions[key] for key in decisions}
    ordered_decisions = tuple(
        decision_by_source[post.source_key] for post in source_posts
    )
    eligible = tuple(
        item
        for item in ranked
        if decisions[item.source_key].primary_reason == "eligible"
    )
    if any(
        decision.primary_reason not in _PRIMARY_REASONS
        for decision in decisions.values()
    ):
        raise RejectedContract("candidate decision has an unsupported primary reason")
    return SelectionResult(
        seed=seed,
        observed_posts=len(source_posts),
        decisions=ordered_decisions,
        representatives=tuple(ranked),
        eligible_representatives=eligible,
    )


@dataclass(frozen=True, slots=True)
class ThemeCandidate:
    theme_id: str
    selection_count: int
    state: str = "active"


def choose_themes(
    themes: Sequence[ThemeCandidate], *, seed: str, count: int
) -> tuple[str, ...]:
    """Choose least-selected active themes, breaking ties with the saved seed."""

    if not isinstance(seed, str) or not seed.strip():
        raise RejectedContract("theme selection requires the saved non-empty run seed")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise RejectedContract("theme selection count must be a non-negative integer")
    seen: set[str] = set()
    active: list[ThemeCandidate] = []
    for theme in themes:
        if not isinstance(theme, ThemeCandidate):
            raise RejectedContract("theme list contains an unsupported record")
        if not isinstance(theme.theme_id, str) or not theme.theme_id.strip():
            raise RejectedContract("theme_id must be non-empty")
        if theme.theme_id in seen:
            raise RejectedContract("theme_id is duplicated")
        seen.add(theme.theme_id)
        if (
            isinstance(theme.selection_count, bool)
            or not isinstance(theme.selection_count, int)
            or theme.selection_count < 0
        ):
            raise RejectedContract("selection_count must be a non-negative integer")
        if not isinstance(theme.state, str) or theme.state not in {"active", "retired"}:
            raise RejectedContract("theme state is unsupported")
        if theme.state == "active":
            active.append(theme)
    selected: list[str] = []
    rng = random.Random(seed)
    for selection_count in sorted({theme.selection_count for theme in active}):
        tied = sorted(
            theme.theme_id
            for theme in active
            if theme.selection_count == selection_count
        )
        rng.shuffle(tied)
        remaining = count - len(selected)
        selected.extend(tied[:remaining])
        if len(selected) >= count:
            break
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class ThemeSelectionEvent:
    selection_id: str
    run_id: str
    theme_id: str
    event_kind: str
    count_event_id: str
    seed: str
    result_word_refs: tuple[str, ...]
    selected_at: str


def _offset_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RejectedContract("selected_at must be an offset-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RejectedContract("selected_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RejectedContract("selected_at must include a timezone offset")
    return parsed.astimezone(JST).isoformat()


def _decode_theme_candidates(themes_json: str) -> tuple[ThemeCandidate, ...]:
    try:
        themes = json.loads(themes_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RejectedContract("registered theme list is invalid JSON") from exc
    if not isinstance(themes, list):
        raise RejectedContract("registered theme list must be an array")
    candidates = []
    seen: set[str] = set()
    for item in themes:
        if not isinstance(item, dict):
            raise RejectedContract("registered theme item must be an object")
        candidate = ThemeCandidate(
            theme_id=item.get("theme_id"),
            selection_count=item.get("selection_count"),
            state=item.get("state"),
        )
        if (
            not isinstance(candidate.theme_id, str)
            or not candidate.theme_id.strip()
            or candidate.theme_id in seen
        ):
            raise RejectedContract("registered theme IDs must be unique and non-empty")
        if (
            isinstance(candidate.selection_count, bool)
            or not isinstance(candidate.selection_count, int)
            or candidate.selection_count < 0
        ):
            raise RejectedContract("registered selection_count is invalid")
        if not isinstance(candidate.state, str) or candidate.state not in {
            "active",
            "retired",
        }:
            raise RejectedContract("registered theme state is invalid")
        seen.add(candidate.theme_id)
        candidates.append(candidate)
    return tuple(candidates)


def load_theme_candidates(
    connection: sqlite3.Connection, run_id: str
) -> tuple[ThemeCandidate, ...]:
    """Load the run's registered themes and add its version's saved count events."""

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN")
    try:
        run = connection.execute(
            "SELECT mode, status, settings_version FROM run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None or run["mode"] != "daily":
            raise RejectedContract("theme choices require a known daily run")
        if run["status"] not in {"running", "partial"}:
            raise RejectedContract("daily run is not accepting theme choices")
        settings = connection.execute(
            "SELECT themes_json FROM settings_version WHERE version = ?",
            (run["settings_version"],),
        ).fetchone()
        if settings is None:
            raise RejectedContract("daily run settings version is missing")
        base = _decode_theme_candidates(settings["themes_json"])
        event_counts = {
            row["theme_id"]: int(row["event_count"])
            for row in connection.execute(
                """
                SELECT selection.theme_id, COUNT(*) AS event_count
                  FROM theme_selection AS selection
                  JOIN run AS event_run ON event_run.run_id = selection.run_id
                 WHERE event_run.settings_version = ?
                 GROUP BY selection.theme_id
                """,
                (run["settings_version"],),
            )
        }
        known_theme_ids = {theme.theme_id for theme in base}
        if event_counts.keys() - known_theme_ids:
            raise RejectedContract("selection events reference an unregistered theme")
        candidates = tuple(
            ThemeCandidate(
                theme_id=theme.theme_id,
                selection_count=theme.selection_count
                + event_counts.get(theme.theme_id, 0),
                state=theme.state,
            )
            for theme in base
        )
        if owns_transaction:
            connection.commit()
        return candidates
    except BaseException:
        if owns_transaction and connection.in_transaction:
            connection.rollback()
        raise


def _event_from_row(row: sqlite3.Row) -> ThemeSelectionEvent:
    try:
        refs = json.loads(row["result_word_refs_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RejectedContract(
            "stored theme result references are invalid JSON"
        ) from exc
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise RejectedContract("stored theme result references are invalid")
    return ThemeSelectionEvent(
        selection_id=row["selection_id"],
        run_id=row["run_id"],
        theme_id=row["theme_id"],
        event_kind=row["event_kind"],
        count_event_id=row["count_event_id"],
        seed=row["seed"],
        result_word_refs=tuple(refs),
        selected_at=row["selected_at"],
    )


def load_theme_selection_events(
    connection: sqlite3.Connection, run_id: str, *, event_kind: str | None = None
) -> tuple[ThemeSelectionEvent, ...]:
    """Return persisted event results for resume without drawing new choices."""

    run = connection.execute(
        "SELECT mode FROM run WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None or run["mode"] != "daily":
        raise RejectedContract("theme events require a known daily run")
    if event_kind is not None and event_kind not in _SELECTION_EVENT_KINDS:
        raise RejectedContract("event_kind must be initial or additional")
    if event_kind is None:
        rows = connection.execute(
            "SELECT * FROM theme_selection WHERE run_id=? ORDER BY event_kind, selection_id",
            (run_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM theme_selection
             WHERE run_id=? AND event_kind=? ORDER BY selection_id
            """,
            (run_id, event_kind),
        ).fetchall()
    return tuple(_event_from_row(row) for row in rows)


def persist_theme_selection_events(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    event_kind: str,
    selected_theme_ids: Sequence[str],
    seed: str,
    selected_at: str,
    result_word_refs: Mapping[str, Sequence[str]] | None = None,
    first_ordinal: int = 1,
) -> tuple[ThemeSelectionEvent, ...]:
    """Atomically store theme selection count events and make retries idempotent."""

    if event_kind not in _SELECTION_EVENT_KINDS:
        raise RejectedContract("event_kind must be initial or additional")
    if not isinstance(seed, str) or not seed.strip():
        raise RejectedContract("theme selection requires the saved non-empty run seed")
    if (
        isinstance(first_ordinal, bool)
        or not isinstance(first_ordinal, int)
        or first_ordinal < 1
    ):
        raise RejectedContract("first_ordinal must be a positive integer")
    if not isinstance(selected_theme_ids, Sequence) or isinstance(
        selected_theme_ids, (str, bytes)
    ):
        raise RejectedContract("selected_theme_ids must be a sequence")
    theme_ids = tuple(selected_theme_ids)
    if not theme_ids or any(
        not isinstance(theme_id, str) or not theme_id for theme_id in theme_ids
    ):
        raise RejectedContract("at least one non-empty theme_id is required")
    if len(set(theme_ids)) != len(theme_ids):
        raise RejectedContract("one selection batch cannot repeat a theme_id")
    if event_kind == "initial" and (first_ordinal != 1 or len(theme_ids) > 3):
        raise RejectedContract("initial selection must use slots 1 through 3")
    timestamp = _offset_timestamp(selected_at)
    refs_supplied = result_word_refs is not None
    refs_by_theme = result_word_refs if result_word_refs is not None else {}
    if not isinstance(refs_by_theme, Mapping) or set(refs_by_theme) - set(theme_ids):
        raise RejectedContract("result word references must match selected themes")
    normalized_refs: dict[str, tuple[str, ...]] = {}
    for theme_id in theme_ids:
        refs = refs_by_theme.get(theme_id, ())
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
            raise RejectedContract("result word references must be arrays")
        values = tuple(refs)
        if any(not isinstance(ref, str) or not ref.strip() for ref in values):
            raise RejectedContract("result word references must be non-empty strings")
        if len(set(values)) != len(values):
            raise RejectedContract("result word references cannot repeat")
        normalized_refs[theme_id] = values

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {_EVENT_SAVEPOINT}")
    try:
        run = connection.execute(
            "SELECT mode, status, settings_version, seed FROM run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None or run["mode"] != "daily":
            raise RejectedContract("theme selection events require a known daily run")
        if run["status"] not in {"running", "partial"}:
            raise RejectedContract("daily run is not accepting theme selection events")
        if run["seed"] != seed:
            raise RejectedContract("selection seed differs from the saved run seed")
        settings = connection.execute(
            "SELECT themes_json FROM settings_version WHERE version = ?",
            (run["settings_version"],),
        ).fetchone()
        if settings is None:
            raise RejectedContract("daily run settings version is missing")
        theme_by_id = {
            theme.theme_id: theme
            for theme in _decode_theme_candidates(settings["themes_json"])
        }
        active_theme_count = sum(
            theme.state == "active" for theme in theme_by_id.values()
        )
        if event_kind == "initial" and len(theme_ids) != min(3, active_theme_count):
            raise RejectedContract(
                "initial selection must record the first three active themes"
            )
        if event_kind == "additional" and len(theme_ids) != 1:
            raise RejectedContract("each additional theme selection is one count event")
        for theme_id in theme_ids:
            theme = theme_by_id.get(theme_id)
            if theme is None or theme.state != "active":
                raise RejectedContract(
                    "selected theme is not active in the run settings"
                )

        expected: list[tuple[str, str, tuple[str, ...]]] = []
        for offset, theme_id in enumerate(theme_ids):
            ordinal = first_ordinal + offset
            event_id = f"theme-selection:{run_id}:{event_kind}:{ordinal:02d}"
            expected.append((event_id, theme_id, normalized_refs[theme_id]))

        existing_rows = []
        for event_id, _, _ in expected:
            row = connection.execute(
                "SELECT * FROM theme_selection WHERE count_event_id = ?", (event_id,)
            ).fetchone()
            existing_rows.append(row)
        if any(row is not None for row in existing_rows):
            if not all(row is not None for row in existing_rows):
                raise IdempotencyConflict(
                    "theme selection batch is only partially present"
                )
            replayed = []
            for row, (event_id, theme_id, refs) in zip(existing_rows, expected):
                actual = _event_from_row(row)
                if (
                    actual.selection_id != event_id
                    or actual.run_id != run_id
                    or actual.theme_id != theme_id
                    or actual.event_kind != event_kind
                    or actual.count_event_id != event_id
                    or actual.seed != seed
                    or (refs_supplied and actual.result_word_refs != refs)
                ):
                    raise IdempotencyConflict(
                        "theme selection event was reused with different content"
                    )
                replayed.append(actual)
            if owns_transaction:
                connection.commit()
            else:
                connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}")
            return tuple(replayed)

        saved: list[ThemeSelectionEvent] = []
        for event_id, theme_id, refs in expected:
            refs_json = json.dumps(
                list(refs), ensure_ascii=False, separators=(",", ":")
            )
            connection.execute(
                """
                INSERT INTO theme_selection
                    (selection_id, run_id, theme_id, event_kind, count_event_id,
                     seed, result_word_refs_json, selected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    theme_id,
                    event_kind,
                    event_id,
                    seed,
                    refs_json,
                    timestamp,
                ),
            )
            saved.append(
                ThemeSelectionEvent(
                    selection_id=event_id,
                    run_id=run_id,
                    theme_id=theme_id,
                    event_kind=event_kind,
                    count_event_id=event_id,
                    seed=seed,
                    result_word_refs=refs,
                    selected_at=timestamp,
                )
            )
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}")
        return tuple(saved)
    except BaseException:
        if owns_transaction:
            if connection.in_transaction:
                connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {_EVENT_SAVEPOINT}")
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}")
        raise


def record_theme_result_word_refs(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    event_kind: str,
    ordinal: int,
    result_word_refs: Sequence[str],
) -> ThemeSelectionEvent:
    """Attach one observed result set to its already-persisted selection event."""

    if event_kind not in _SELECTION_EVENT_KINDS:
        raise RejectedContract("event_kind must be initial or additional")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise RejectedContract("ordinal must be a positive integer")
    if event_kind == "initial" and ordinal > 3:
        raise RejectedContract("initial selection ordinal must be between 1 and 3")
    if not isinstance(result_word_refs, Sequence) or isinstance(
        result_word_refs, (str, bytes)
    ):
        raise RejectedContract("result word references must be an array")
    refs = tuple(result_word_refs)
    if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise RejectedContract("result word references must be non-empty strings")
    if len(set(refs)) != len(refs):
        raise RejectedContract("result word references cannot repeat")
    refs_json = json.dumps(list(refs), ensure_ascii=False, separators=(",", ":"))
    event_id = f"theme-selection:{run_id}:{event_kind}:{ordinal:02d}"

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {_EVENT_SAVEPOINT}_results")
    try:
        row = connection.execute(
            "SELECT * FROM theme_selection WHERE count_event_id=? AND run_id=?",
            (event_id, run_id),
        ).fetchone()
        if row is None:
            raise RejectedContract(
                "theme selection event must exist before its results"
            )
        actual = _event_from_row(row)
        if actual.result_word_refs == refs:
            if owns_transaction:
                connection.commit()
            else:
                connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_results")
            return actual
        if actual.result_word_refs:
            raise IdempotencyConflict(
                "theme selection result refs were already recorded"
            )
        connection.execute(
            "UPDATE theme_selection SET result_word_refs_json=? "
            "WHERE selection_id=? AND result_word_refs_json='[]'",
            (refs_json, event_id),
        )
        updated = connection.execute(
            "SELECT * FROM theme_selection WHERE selection_id=?", (event_id,)
        ).fetchone()
        if updated is None or _event_from_row(updated).result_word_refs != refs:
            raise IdempotencyConflict(
                "theme selection result refs changed concurrently"
            )
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_results")
        return _event_from_row(updated)
    except BaseException:
        if owns_transaction:
            if connection.in_transaction:
                connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {_EVENT_SAVEPOINT}_results")
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_results")
        raise


def select_theme_batch(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    event_kind: str,
    selected_at: str,
    first_ordinal: int = 1,
) -> tuple[ThemeSelectionEvent, ...]:
    """Choose and persist theme events under one SQLite write lock for safe resume."""

    if event_kind not in _SELECTION_EVENT_KINDS:
        raise RejectedContract("event_kind must be initial or additional")
    if (
        isinstance(first_ordinal, bool)
        or not isinstance(first_ordinal, int)
        or first_ordinal < 1
    ):
        raise RejectedContract("first_ordinal must be a positive integer")
    if event_kind == "initial" and first_ordinal != 1:
        raise RejectedContract("initial selection must start at ordinal 1")
    timestamp = _offset_timestamp(selected_at)
    expected_count = 3 if event_kind == "initial" else 1
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {_EVENT_SAVEPOINT}_choose")
    try:
        run = connection.execute(
            "SELECT mode, status, settings_version, seed FROM run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None or run["mode"] != "daily":
            raise RejectedContract("theme choices require a known daily run")
        if run["status"] not in {"running", "partial"}:
            raise RejectedContract("daily run is not accepting theme choices")
        settings = connection.execute(
            "SELECT themes_json FROM settings_version WHERE version = ?",
            (run["settings_version"],),
        ).fetchone()
        if settings is None:
            raise RejectedContract("daily run settings version is missing")
        registered = _decode_theme_candidates(settings["themes_json"])
        active_count = sum(theme.state == "active" for theme in registered)
        if event_kind == "initial":
            expected_count = min(3, active_count)
        if expected_count < 1:
            raise RejectedContract("no active theme is available for selection")

        event_ids = [
            f"theme-selection:{run_id}:{event_kind}:{ordinal:02d}"
            for ordinal in range(first_ordinal, first_ordinal + expected_count)
        ]
        if event_kind == "additional" and first_ordinal > 1:
            prior_ids = [
                f"theme-selection:{run_id}:additional:{ordinal:02d}"
                for ordinal in range(1, first_ordinal)
            ]
            prior_count = connection.execute(
                "SELECT COUNT(*) FROM theme_selection WHERE run_id=? AND count_event_id IN ("
                + ",".join("?" for _ in prior_ids)
                + ")",
                (run_id, *prior_ids),
            ).fetchone()[0]
            if prior_count != len(prior_ids):
                raise RejectedContract(
                    "additional selection ordinals cannot skip events"
                )
        rows = [
            connection.execute(
                "SELECT * FROM theme_selection WHERE count_event_id=?", (event_id,)
            ).fetchone()
            for event_id in event_ids
        ]
        if any(row is not None for row in rows):
            if not all(row is not None for row in rows):
                raise IdempotencyConflict(
                    "theme selection batch is only partially present"
                )
            events = tuple(_event_from_row(row) for row in rows)
            if any(
                event.run_id != run_id
                or event.event_kind != event_kind
                or event.count_event_id != event_id
                or event.selection_id != event_id
                or event.seed != run["seed"]
                for event, event_id in zip(events, event_ids)
            ):
                raise IdempotencyConflict("saved theme batch conflicts with this run")
            if owns_transaction:
                connection.commit()
            else:
                connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_choose")
            return events

        candidates = load_theme_candidates(connection, run_id)
        selected = choose_themes(candidates, seed=run["seed"], count=expected_count)
        if len(selected) != expected_count:
            raise RejectedContract("active themes cannot fill the requested selection")
        events = persist_theme_selection_events(
            connection,
            run_id,
            event_kind=event_kind,
            selected_theme_ids=selected,
            seed=run["seed"],
            selected_at=timestamp,
            first_ordinal=first_ordinal,
        )
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_choose")
        return events
    except BaseException:
        if owns_transaction:
            if connection.in_transaction:
                connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {_EVENT_SAVEPOINT}_choose")
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}_choose")
        raise
