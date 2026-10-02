"""Type effectiveness + a simple Gen III damage estimate (no RAM stats needed).

Stats are estimated from base stats and level (IV 15, no EVs, neutral nature):
``stat = (2*base + 15) * L // 100 + 5``, ``hp = (2*base + 15) * L // 100 + L + 10``.
Damage: ``((2L/5 + 2) * power * A / D) / 50 + 2``, times STAB 1.5, type multiplier and the
average random factor 0.925, times accuracy. Physical/special follows the move's Gen III
type category (moves.json). Unknown things are reported via ``reliable=False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ... import data
from .state import BattleMon

AVG_RANDOM = 0.925
STAB = 1.5
#: damage that doesn't depend on power: id -> "level" | int | "half"
FIXED_DAMAGE = {69: "level", 101: "level", 82: 40, 49: 20, 162: "half"}  # seismic toss, night shade, dragon rage, sonic boom, super fang
#: moves whose effect is a random other move; estimated as a neutral 60-power hit, unreliable
RANDOM_MOVES = {118}  # metronome
RANDOM_MOVE_POWER = 60
STRUGGLE_POWER = 50


def stat(base: int, level: int) -> int:
    return (2 * base + 15) * level // 100 + 5


def hp_stat(base: int, level: int) -> int:
    return (2 * base + 15) * level // 100 + level + 10


@dataclass
class Estimate:
    expected: float          # expected damage (HP points)
    fraction: float          # of the opponent's remaining HP, capped at 1
    effectiveness: float
    reliable: bool           # False: power unknown / random (e.g. METRONOME)
    status: bool = False     # no damage (Growl etc.)
    note: str = ""


def opponent_hp(opp: BattleMon) -> float:
    if opp.hp is not None:
        return float(opp.hp)
    sp = opp.info
    full = hp_stat(sp["base"]["hp"], opp.level) if sp else 20 + opp.level * 2
    pct = 100.0 if opp.hp_pct is None else float(opp.hp_pct)
    return max(1.0, full * pct / 100.0)


def raw_damage(level: int, power: float, atk: float, dfn: float) -> float:
    return ((2 * level / 5 + 2) * power * atk / max(1.0, dfn)) / 50 + 2


def estimate(user: BattleMon, opp: BattleMon, move_id: Optional[int]) -> Estimate:
    """``move_id`` None = Struggle."""
    hp_left = opponent_hp(opp)
    us, them = user.info, opp.info
    if move_id is None:
        mv = {"name": "struggle", "type": "normal", "power": STRUGGLE_POWER, "accuracy": None,
              "category": "physical"}
    else:
        mv = data.move(move_id)
    if mv is None:
        return Estimate(0.0, 0.0, 1.0, False, note=f"unknown move {move_id}")
    eff = data.effectiveness(mv["type"], opp.types) if mv["type"] != "unknown" else 1.0
    acc = (mv["accuracy"] or 100) / 100.0
    if move_id in RANDOM_MOVES:
        if us and them:
            dmg = raw_damage(user.level, RANDOM_MOVE_POWER, stat(us["base"]["atk"], user.level),
                             stat(them["base"]["def"], opp.level)) * AVG_RANDOM
        else:
            dmg = 0.0
        return Estimate(dmg, min(1.0, dmg / hp_left), 1.0, False,
                        note=f"{mv['name']} calls a random move: damage unpredictable")
    fixed = FIXED_DAMAGE.get(move_id) if move_id is not None else None
    if fixed is not None:
        dmg = {"level": float(user.level), "half": hp_left / 2}.get(fixed, fixed) if isinstance(fixed, str) \
            else float(fixed)
        dmg = 0.0 if eff == 0 else dmg * acc
        return Estimate(dmg, min(1.0, dmg / hp_left), eff, True, note="fixed damage")
    if mv["category"] == "status" or not mv["power"]:
        return Estimate(0.0, 0.0, eff, mv["category"] == "status", status=True,
                        note="status move" if mv["category"] == "status" else "variable power")
    if not (us and them):
        return Estimate(0.0, 0.0, eff, False, note="unknown species")
    a_key, d_key = ("atk", "def") if mv["category"] == "physical" else ("spa", "spd")
    dmg = raw_damage(user.level, mv["power"], stat(us["base"][a_key], user.level),
                     stat(them["base"][d_key], opp.level))
    stab = STAB if mv["type"] in user.types else 1.0
    dmg = dmg * stab * eff * AVG_RANDOM * acc
    return Estimate(dmg, min(1.0, dmg / hp_left), eff, True)


def types_of(mon: BattleMon) -> List[str]:
    return mon.types
