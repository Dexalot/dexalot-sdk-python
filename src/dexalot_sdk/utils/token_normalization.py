"""Normalize user-supplied token symbols and trading pairs for SDK and MCP use.

Applies ASCII case-folding (uppercase), trims whitespace, and maps optional
synonyms from ``data/token_aliases.json`` (canonical → list of aliases) to canonical symbols.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _load_token_alias_map() -> dict[str, str]:
    registry_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "token_aliases.json",
    )
    with open(registry_path) as f:
        registry = json.load(f)

    raw = registry.get("aliases")
    if not isinstance(raw, dict):
        raise ValueError("token_aliases.json must contain a top-level 'aliases' object.")

    out: dict[str, str] = {}
    for canonical, aliases in raw.items():
        if not isinstance(canonical, str) or not isinstance(aliases, list):
            continue
        cu = canonical.strip().upper()
        if not cu:
            continue
        for alias in aliases:
            if not isinstance(alias, str):
                continue
            au = alias.strip().upper()
            if au:
                out[au] = cu
    return out


def normalize_token_symbol_for_sdk(symbol: str) -> str:
    """Return canonical token symbol (strip, upper, apply alias map)."""
    s = symbol.strip().upper()
    return _load_token_alias_map().get(s, s)


def normalize_trading_pair_for_sdk(pair: str) -> str:
    """Return canonical ``BASE/QUOTE`` (each leg normalized like a token symbol)."""
    trimmed = pair.strip()
    parts = trimmed.split("/", 1)
    if len(parts) != 2:
        return trimmed.upper()
    base, quote = parts[0].strip(), parts[1].strip()
    return f"{normalize_token_symbol_for_sdk(base)}/{normalize_token_symbol_for_sdk(quote)}"
