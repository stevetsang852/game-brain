"""Progress score. Clearing the game outranks completing the Pokedex.

The score is computed only from observations. It does not name the next route.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

# Primary objective dominates the secondary one.
CLEAR_WEIGHT = 1000.0
POKEDEX_WEIGHT = 20.0
STEP_PENALTY = 0.01


def _ram(state: Mapping[str, Any]) -> Mapping[str, Any]:
    return state.get("ram") or state


def progress_features(state: Mapping[str, Any]) -> Dict[str, float]:
    ram = _ram(state)
    badges = ram.get("badges")
    species = ram.get("pokedex_seen") or ram.get("pokedex_owned") or []
    return {
        "champion": 1.0 if ram.get("champion") or ram.get("hall_of_fame") else 0.0,
        "badges": float(badges if isinstance(badges, int) else len(badges or [])),
        "pokedex": float(len(species) if isinstance(species, (list, tuple, set)) else species or 0),
        "party": float(ram.get("party_count") or 0),
        "map": float((ram.get("map_bank") or 0) * 1000 + (ram.get("map_id") or 0)),
    }


def progress_delta(before: Mapping[str, Any], after: Mapping[str, Any], repeated: bool = False) -> Dict[str, float]:
    """Return the scalar reward and the parts that produced it."""
    old, new = progress_features(before), progress_features(after)
    clear = CLEAR_WEIGHT * max(0.0, new["champion"] - old["champion"])
    clear += 50.0 * max(0.0, new["badges"] - old["badges"])
    pokedex = POKEDEX_WEIGHT * max(0.0, new["pokedex"] - old["pokedex"])
    party = 2.0 * max(0.0, new["party"] - old["party"])
    new_map = 1.0 if new["map"] != old["map"] else 0.0
    repeat = -0.2 if repeated else 0.0
    total = clear + pokedex + party + new_map + repeat - STEP_PENALTY
    return {"reward": total, "clear": clear, "pokedex": pokedex, "party": party,
            "new_map": new_map, "repeat": repeat, "step_penalty": -STEP_PENALTY}
