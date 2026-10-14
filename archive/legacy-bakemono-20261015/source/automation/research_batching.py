"""Bound and batch research candidates before page verification."""

from collections import defaultdict
from collections.abc import Iterator


def distribute_target(target_count: int, genre_weights: dict[str, int]) -> dict[str, int]:
    if not isinstance(target_count, int) or isinstance(target_count, bool) or target_count < 0:
        raise ValueError("target count must be a non-negative integer")
    weights = {genre: weight for genre, weight in genre_weights.items()
               if isinstance(weight, int) and not isinstance(weight, bool) and weight > 0}
    result = {genre: 0 for genre in genre_weights}
    slots = [genre for genre, weight in weights.items() for _ in range(weight)]
    if not slots:
        return result
    for index in range(target_count):
        result[slots[index % len(slots)]] += 1
    return result


def genre_shortfalls(genre_targets: dict[str, int], genre_counts: dict[str, int]) -> dict[str, int]:
    return {genre: max(0, target - genre_counts.get(genre, 0))
            for genre, target in genre_targets.items()}


def select_candidate_pool(candidates: list[dict], genre_weights: dict[str, int],
                          limit: int) -> list[dict]:
    """Keep a weighted, genre-balanced pool while preserving per-genre API order."""
    if not isinstance(candidates, list) or not isinstance(genre_weights, dict):
        raise ValueError("candidate pool inputs are invalid")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("candidate pool limit must be a non-negative integer")
    weights = {genre: weight for genre, weight in genre_weights.items()
               if isinstance(weight, int) and not isinstance(weight, bool) and weight > 0}
    if not weights or limit == 0:
        return []

    queues: dict[str, list[dict]] = defaultdict(list)
    seen_ids = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        genre = candidate.get("genre")
        product_id = candidate.get("product_id")
        if genre not in weights or not isinstance(product_id, str) or product_id in seen_ids:
            continue
        queues[genre].append(candidate)
        seen_ids.add(product_id)

    selected = []
    while len(selected) < limit:
        added = False
        for genre, weight in weights.items():
            take = min(weight, limit - len(selected))
            for _ in range(take):
                if not queues[genre]:
                    break
                selected.append(queues[genre].pop(0))
                added = True
        if not added:
            break
    return selected


def chunk_candidates(candidates: list[dict], batch_size: int) -> Iterator[list[dict]]:
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch size must be a positive integer")
    for offset in range(0, len(candidates), batch_size):
        yield candidates[offset:offset + batch_size]


def order_triaged_candidates(pool: list[dict], triage: dict, limit: int) -> list[dict]:
    """Try initial selections first, then the same triage's unused candidates."""
    by_id = {item["product_id"]: item for item in pool}
    groups = ("selected", "uncertain", "excluded")
    return [by_id[entry["product_id"]] for group in groups
            for entry in triage[group]][:limit]


def candidate_is_postable(score: dict | None, snapshot: dict | None) -> bool:
    """Check full or provisional A/B scoring and observed stock before model review."""
    if not isinstance(score, dict):
        return False
    points = score.get("score")
    if not isinstance(points, int) or isinstance(points, bool) or not 0 <= points <= 12:
        return False
    expected_grade = "A" if points >= 9 else "B" if points >= 6 else "C"
    if expected_grade not in {"A", "B"} or score.get("full_max") != 12:
        return False

    confirmed_factors = score.get("confirmed_factors")
    confirmed_max = score.get("confirmed_max")
    unconfirmed_factors = score.get("unconfirmed_factors")
    if (not isinstance(confirmed_factors, int) or isinstance(confirmed_factors, bool) or
            not isinstance(confirmed_max, int) or isinstance(confirmed_max, bool) or
            not isinstance(unconfirmed_factors, list) or
            confirmed_max != confirmed_factors * 2 or points > confirmed_max or
            len(unconfirmed_factors) != 6 - confirmed_factors):
        return False

    status = score.get("status")
    if status == "complete":
        if (confirmed_factors != 6 or confirmed_max != 12 or unconfirmed_factors or
                score.get("grade") != expected_grade or
                score.get("provisional_grade") != expected_grade):
            return False
    elif status == "provisional":
        if (not 0 < confirmed_factors < 6 or score.get("grade") is not None or
                score.get("provisional_grade") != expected_grade):
            return False
    else:
        return False

    return (isinstance(snapshot, dict) and snapshot.get("status") == "observed" and
            snapshot.get("availability") == "in_stock" and
            isinstance(snapshot.get("name"), str) and bool(snapshot["name"].strip()) and
            isinstance(snapshot.get("price_yen"), int) and not isinstance(snapshot.get("price_yen"), bool) and
            snapshot["price_yen"] > 0)
