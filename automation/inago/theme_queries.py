"""Reproducible public Trends queries derived from adopted theme labels."""

from __future__ import annotations

import random


# These fragments share context in the adopted labels. A bare "用品" or
# "器具" would broaden the search beyond its theme.
_QUALIFIED_COMPONENTS = {
    "ボディ・セルフケア": ("ボディケア", "セルフケア"),
    "美容ツール・器具": ("美容ツール", "美容器具"),
    "ペットケア・用品": ("ペットケア", "ペット用品"),
    "子ども移動・車載安全用品": ("子ども移動", "子ども車載安全用品"),
    "ペット家電・見守り機器": ("ペット家電", "ペット見守り機器"),
}


def theme_query_candidates(theme_name: str) -> tuple[str, ...]:
    """Split a theme label, retaining context for known abbreviated fragments."""
    name = theme_name.strip()
    if not name:
        raise ValueError("theme name must not be empty")
    qualified = _QUALIFIED_COMPONENTS.get(name)
    if qualified is not None:
        return qualified
    return tuple(
        dict.fromkeys(part.strip() for part in name.split("・") if part.strip())
    )


def choose_theme_query(theme_name: str, seed: str, acquisition_id: str) -> str:
    """Choose once per acquisition without advancing the theme-selection RNG."""
    candidates = theme_query_candidates(theme_name)
    if not candidates:
        raise ValueError("theme has no searchable component")
    return random.Random(f"theme-query-v1:{seed}:{acquisition_id}").choice(candidates)
