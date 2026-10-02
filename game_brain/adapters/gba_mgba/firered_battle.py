"""FireRed battle RAM -> ``ram["battle"]`` (only while ``in_battle`` is True).

Every address below was verified on the supplied ROM (SHA1 e0194282..., not the clean 1.0
dump) in the rival battle in Oak's lab. Method and results: notes/mgba-bridge.md, section
"`ram["battle"]`". Anything not verified (status, party, bag, battle type, turn) is left out.

Shape::

    {"menu": "action" | "move" | "other",
     "cursor": 0-3 | None,          # action: 0 FIGHT 1 BAG 2 POKEMON 3 RUN; move: slot 0-3 (2x2, row-major)
     "player":   {"species", "level", "hp", "max_hp", "moves": [{"id", "pp"}]} | None,
     "opponent": {"species", "level", "hp", "max_hp", "hp_pct", "moves": [{"id", "pp"}]} | None,
     "outcome": None | "win" | "lose" | "unknown"}

``species`` and move ``id`` are the game's internal numbers. Empty move slots (id 0) are
dropped, so ``moves[i]`` is move-menu slot ``i``. The adapter sets ``player``, ``opponent``
and ``outcome`` to None until the current battle's first action/move menu, because the game
keeps the previous battle's values for the first few observations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .firered import FireRedRam

# struct BattlePokemon gBattleMons[4], 0x58 bytes each; battler 0 = player, 1 = opponent (singles)
G_BATTLE_MONS = 0x02023BE4
BATTLE_MON_SIZE = 0x58
# offsets inside struct BattlePokemon (pokefirered)
BM_SPECIES, BM_MOVES, BM_PP, BM_HP, BM_LEVEL, BM_MAX_HP = 0x00, 0x0C, 0x24, 0x28, 0x2A, 0x2C

G_ACTION_SELECTION_CURSOR = 0x02023FF8  # u8[4], battler 0: 0 FIGHT, 1 BAG, 2 POKEMON, 3 RUN
G_MOVE_SELECTION_CURSOR = 0x02023FFC    # u8[4], battler 0: move slot 0-3
G_BATTLE_OUTCOME = 0x02023E8A           # u8: 0 ongoing, 1 won, 2 lost (not cleared after the battle)
G_BATTLER_CONTROLLER_FUNCS = 0x03004FE0  # void (*[4])(void); battler 0 = player controller

# Player controller function while it waits for input. These are code addresses in the
# supplied ROM (measured, not taken from a symbol file).
CTRL_CHOOSE_ACTION = 0x080E763D
CTRL_CHOOSE_MOVE = 0x080E7989

OUTCOMES = {0: None, 1: "win", 2: "lose"}


def _mon(ram: FireRedRam, battler: int) -> Dict[str, Any]:
    b = G_BATTLE_MONS + BATTLE_MON_SIZE * battler
    moves: List[Dict[str, int]] = []
    for i in range(4):
        mid = ram.u16(b + BM_MOVES + 2 * i)
        if mid:
            moves.append({"id": mid, "pp": ram.u8(b + BM_PP + i)})
    return {"species": ram.u16(b + BM_SPECIES), "level": ram.u8(b + BM_LEVEL),
            "hp": ram.u16(b + BM_HP), "max_hp": ram.u16(b + BM_MAX_HP), "moves": moves}


def read_battle(ram: FireRedRam) -> Dict[str, Any]:
    ctrl = ram.u32(G_BATTLER_CONTROLLER_FUNCS)
    menu = {CTRL_CHOOSE_ACTION: "action", CTRL_CHOOSE_MOVE: "move"}.get(ctrl, "other")
    cursor: Optional[int] = None
    if menu == "action":
        cursor = ram.u8(G_ACTION_SELECTION_CURSOR)
    elif menu == "move":
        cursor = ram.u8(G_MOVE_SELECTION_CURSOR)
    player: Optional[Dict[str, Any]] = _mon(ram, 0)
    opp: Optional[Dict[str, Any]] = _mon(ram, 1)
    if not player["species"] or not opp["species"]:
        # the first ~5 observations after in_battle turns True: gBattleMons not filled yet (all 0)
        player = opp = None
    else:
        opp["hp_pct"] = round(100 * opp["hp"] / opp["max_hp"]) if opp["max_hp"] else 0
    raw = ram.u8(G_BATTLE_OUTCOME)
    return {"menu": menu, "cursor": cursor, "player": player, "opponent": opp,
            "outcome": OUTCOMES.get(raw, "unknown")}
