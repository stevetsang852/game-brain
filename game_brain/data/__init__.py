"""Static Gen III battle tables (type chart, species, moves) generated from PokéAPI.

BSD-3-Clause; source, license text and regeneration steps: ``game_brain/data/NOTICE.md``.
Lookups accept a numeric id (national dex number / move id, int or str) or a PokeAPI
identifier (``"bulbasaur"``, ``"vine-whip"``; case-insensitive, spaces/underscores -> ``-``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Optional, Union

_DIR = Path(__file__).resolve().parent
Key = Union[int, str]


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    data = json.loads((_DIR / name).read_text(encoding="utf-8"))
    data.pop("_meta", None)
    return data


@lru_cache(maxsize=None)
def _by_name(name: str) -> Dict[str, str]:
    return {v["name"]: k for k, v in _load(name).items() if isinstance(v, dict) and "name" in v}


def _lookup(name: str, key: Key) -> Optional[dict]:
    table = _load(name)
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        return table.get(str(int(key)))
    ident = str(key).strip().lower().replace(" ", "-").replace("_", "-")
    k = _by_name(name).get(ident)
    return table.get(k) if k else None


def species(key: Key) -> Optional[dict]:
    """``{name, types, base: {hp, atk, def, spa, spd, spe}, fr_index}`` by national dex number or name."""
    return _lookup("species.json", key)


@lru_cache(maxsize=None)
def _by_fr_index() -> Dict[int, str]:
    return {v["fr_index"]: k for k, v in _load("species.json").items() if "fr_index" in v}


def species_by_game_index(index: int) -> Optional[dict]:
    """Species by FireRed's internal species number (what ``ram["battle"]`` reports).
    Same as the national dex number up to 251; Hoenn species differ (e.g. 277 = Treecko)."""
    k = _by_fr_index().get(int(index))
    return _load("species.json").get(k) if k else None


def move(key: Key) -> Optional[dict]:
    """``{name, type, power, accuracy, pp, priority, category}`` or None."""
    return _lookup("moves.json", key)


def types() -> list:
    return list(_load("type_chart.json")["types"])


def effectiveness(attack_type: str, defend_types: Iterable[str]) -> float:
    """Damage multiplier of an ``attack_type`` move against a mon with ``defend_types``
    (product over both types: 0, 0.25, 0.5, 1, 2 or 4). Unknown types count as 1."""
    chart = _load("type_chart.json")["chart"]
    m = 1.0
    for t in defend_types:
        m *= chart.get(attack_type, {}).get(t, 1.0)
    return m
