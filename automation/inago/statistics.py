"""Conservation checks for observed posts, product representatives, and outcomes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from automation.inago.contracts import RejectedContract
from automation.inago.selection import (
    MAX_POSTS_PER_SEARCH,
    PRIMARY_REASON_ORDER,
    SelectionResult,
)


DAILY_TARGET = 15
MAX_DAILY_SEARCHES = 9
_SOURCE_STATUSES = frozenset({"observed", "partial", "unavailable", "invalid"})


@dataclass(frozen=True, slots=True)
class SearchAttempt:
    """One counted search attempt, including successful zero and failed attempts."""

    attempt_no: int
    source_status: str
    selection: SelectionResult


@dataclass(frozen=True, slots=True)
class DailyStatistics:
    seed: str | None
    search_attempts: int
    search_status_counts: dict[str, int]
    observed_posts: int
    representative_count: int
    distinct_offer_count: int
    committed_count: int
    posted_count: int
    planned_count: int
    shortfall_count: int
    primary_reason_counts: dict[str, int]


def summarize_daily(
    attempts: Sequence[SearchAttempt], *, committed_count: int, posted_count: int
) -> DailyStatistics:
    """Keep raw observations, per-search representatives, daily union, D, and P separate."""

    if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)):
        raise RejectedContract("search attempts must be a sequence")
    if len(attempts) > MAX_DAILY_SEARCHES:
        raise RejectedContract("daily search attempt limit 9 has been reached")
    attempt_numbers = []
    seeds: set[str] = set()
    observed_posts = 0
    representative_count = 0
    distinct_offer_keys: set[str] = set()
    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for attempt in attempts:
        if not isinstance(attempt, SearchAttempt):
            raise RejectedContract("daily statistics contain an unsupported attempt")
        if (
            isinstance(attempt.attempt_no, bool)
            or not isinstance(attempt.attempt_no, int)
            or not 1 <= attempt.attempt_no <= MAX_DAILY_SEARCHES
        ):
            raise RejectedContract("search attempt number must be between 1 and 9")
        attempt_numbers.append(attempt.attempt_no)
        if (
            not isinstance(attempt.source_status, str)
            or attempt.source_status not in _SOURCE_STATUSES
        ):
            raise RejectedContract("search attempt status is unsupported")
        result = attempt.selection
        if not isinstance(result, SelectionResult):
            raise RejectedContract("search attempt has no selection result")
        if not isinstance(result.seed, str) or not result.seed.strip():
            raise RejectedContract("selection result has no saved seed")
        seeds.add(result.seed)
        if not 0 <= result.observed_posts <= MAX_POSTS_PER_SEARCH:
            raise RejectedContract("one search attempt may contain at most 50 posts")
        if len(result.decisions) != result.observed_posts:
            raise RejectedContract("every observed post must have exactly one decision")
        per_attempt_reasons = Counter(
            decision.primary_reason for decision in result.decisions
        )
        if per_attempt_reasons.keys() - set(PRIMARY_REASON_ORDER):
            raise RejectedContract("primary reason is outside the registered order")
        if sum(per_attempt_reasons.values()) != result.observed_posts:
            raise RejectedContract("primary reasons do not conserve observed posts")
        if len(result.representatives) > result.observed_posts:
            raise RejectedContract("representatives cannot exceed observed posts")
        if len(result.eligible_representatives) > len(result.representatives):
            raise RejectedContract(
                "eligible representatives exceed all representatives"
            )
        representative_keys = [item.offer_key for item in result.representatives]
        if len(set(representative_keys)) != len(representative_keys):
            raise RejectedContract(
                "one search cannot have two representatives per offer"
            )
        if not {item.offer_key for item in result.eligible_representatives}.issubset(
            set(representative_keys)
        ):
            raise RejectedContract("eligible candidates must be search representatives")
        if any(
            not isinstance(decision.primary_reason, str)
            or not decision.primary_reason.strip()
            for decision in result.decisions
        ):
            raise RejectedContract("every observed post requires one primary reason")
        for representative in result.representatives:
            if not representative.offer_key:
                raise RejectedContract(
                    "a representative must have a resolved offer key"
                )
            distinct_offer_keys.add(representative.offer_key)
        observed_posts += result.observed_posts
        representative_count += len(result.representatives)
        reason_counts.update(per_attempt_reasons)
        status_counts.update((attempt.source_status,))

    if len(set(attempt_numbers)) != len(attempt_numbers):
        raise RejectedContract("search attempt numbers cannot repeat")
    if sorted(attempt_numbers) != list(range(1, len(attempt_numbers) + 1)):
        raise RejectedContract("search attempts must preserve the saved 1..N sequence")
    if len(seeds) > 1:
        raise RejectedContract("daily selection results must retain one saved run seed")
    if observed_posts > MAX_POSTS_PER_SEARCH * len(attempts):
        raise RejectedContract("observed posts exceed 50 times the counted attempts")
    if (
        isinstance(committed_count, bool)
        or not isinstance(committed_count, int)
        or not 0 <= committed_count <= DAILY_TARGET
    ):
        raise RejectedContract("committed_count must be between 0 and the 15 target")
    if (
        isinstance(posted_count, bool)
        or not isinstance(posted_count, int)
        or posted_count < 0
    ):
        raise RejectedContract("posted_count must be a separate non-negative count")

    return DailyStatistics(
        seed=next(iter(seeds)) if seeds else None,
        search_attempts=len(attempts),
        search_status_counts=dict(status_counts),
        observed_posts=observed_posts,
        representative_count=representative_count,
        distinct_offer_count=len(distinct_offer_keys),
        committed_count=committed_count,
        posted_count=posted_count,
        planned_count=DAILY_TARGET,
        shortfall_count=DAILY_TARGET - committed_count,
        primary_reason_counts={
            reason: reason_counts[reason]
            for reason in PRIMARY_REASON_ORDER
            if reason_counts[reason]
        },
    )
