"""MockBattleAdapter: a synthetic single battle with the exact ``ram["battle"]`` shape the
FireRed adapter emits (Backend, firered_battle.py). No ROM, deterministic for a given seed.

Rules (mirroring what Backend verified on the rival battle):

* The first ``unready_steps`` observations: ``player``/``opponent`` are None, menu "other".
* Then intro text (A or B advances), then the action menu: 2x2, row-major
  (0 FIGHT, 1 BAG / 2 POKéMON, 3 RUN). The move menu is 2x2 too (slots 0 1 / 2 3).
  Pressing past an edge doesn't wrap, and each menu remembers its cursor between turns.
* FIGHT -> move menu (or Struggle at once if no move has PP). B in the move menu -> back.
  A on a slot with PP uses it; a slot with 0 PP is refused ("no PP left" text).
* RUN: refused with a text box when ``can_run`` is False (trainer battle).
  BAG/POKéMON: a text box ("not available in this mock"), then back to the action menu.
* After each move: text boxes, the foe's move, more text; at 0 HP ``outcome`` becomes "win" or
  "lose". After two more text boxes the battle ends: ``in_battle`` False, back in the lab (4/3)
  at (7,8), as on the real ROM after both a win and a loss.
* Damage: Gen III formula with base-stat estimates (PokeAPI tables) and a seeded random factor.
  METRONOME (118) picks a random damaging Gen III move.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from ..base import Adapter
from ...schema import Action, Observation
from ... import data

_DIRS = {"UP": -2, "DOWN": 2, "LEFT": -1, "RIGHT": 1}
METRONOME = 118


def _nav(cursor: int, button: str) -> int:
    r, c = divmod(cursor, 2)
    if button == "UP" and r == 1 or button == "DOWN" and r == 0:
        return cursor + _DIRS[button]
    if button == "LEFT" and c == 1 or button == "RIGHT" and c == 0:
        return cursor + _DIRS[button]
    return cursor  # edge: no wrap


def _stat(base: int, lv: int) -> int:
    return (2 * base + 15) * lv // 100 + 5


class MockBattleAdapter(Adapter):
    name = "mock-battle"

    def __init__(self, player: Optional[Dict[str, Any]] = None, opponent: Optional[Dict[str, Any]] = None,
                 seed: int = 0, can_run: bool = False, unready_steps: int = 5, intro_texts: int = 2):
        self.cfg_player = player or {"species": 1, "level": 5, "max_hp": 22, "moves": [{"id": METRONOME, "pp": 10}]}
        self.cfg_opp = opponent or {"species": 4, "level": 5, "max_hp": 20, "moves": [{"id": METRONOME, "pp": 10}]}
        self.seed, self.can_run = seed, can_run
        self.unready_steps, self.intro_texts = unready_steps, intro_texts
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> Observation:
        self._frame = 0
        self.rng = random.Random(self.seed)
        self.player = self._mon(self.cfg_player)
        self.opp = self._mon(self.cfg_opp)
        self.unready = self.unready_steps
        self.menu = "other"
        self.texts = self.intro_texts
        self.cursor = {"action": 0, "move": 0}
        self.outcome: Optional[str] = None
        self.in_battle = True
        self.log: List[str] = []
        return self.observe()

    @staticmethod
    def _mon(cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {"species": cfg["species"], "level": cfg["level"], "hp": cfg.get("hp", cfg["max_hp"]),
                "max_hp": cfg["max_hp"], "moves": [dict(m) for m in cfg["moves"]]}

    @property
    def frame(self) -> int:
        return self._frame

    def observe(self) -> Observation:
        if not self.in_battle:
            return Observation(frame=self._frame, game="MOCK-BATTLE", ram={
                "in_battle": False, "scene": "overworld", "map_bank": 4, "map_id": 3, "player_x": 7,
                "player_y": 8, "party_count": 1})
        ready = self.unready <= 0
        menu = self.menu if ready else "other"
        b = {"menu": menu, "cursor": self.cursor[menu] if menu in self.cursor else None,
             "player": None, "opponent": None, "outcome": self.outcome}
        if ready:
            b["player"] = {k: (list(map(dict, v)) if k == "moves" else v) for k, v in self.player.items()}
            o = {k: (list(map(dict, v)) if k == "moves" else v) for k, v in self.opp.items()}
            o["hp_pct"] = round(100 * o["hp"] / o["max_hp"]) if o["max_hp"] else 0
            b["opponent"] = o
        return Observation(frame=self._frame, game="MOCK-BATTLE",
                           ram={"in_battle": True, "scene": "other", "battle": b})

    def act(self, action: Action) -> int:
        start = self._frame
        if self.in_battle and self.unready > 0:
            self.unready -= 1
        for p in action.presses:
            if self.in_battle and self.unready <= 0:
                self._press(p.button)
            self._frame += p.total_frames
        return self._frame - start

    # ------------------------------------------------------------------ rules
    def _text(self, n: int, then: str = "action") -> None:
        self.menu, self.texts, self._after = "other", n, then

    def _press(self, button: str) -> None:
        if self.menu == "other":
            if button in ("A", "B"):
                self.texts -= 1
                if self.texts <= 0:
                    after = getattr(self, "_after", "action")
                    if self.outcome is not None:
                        self.in_battle = False
                    else:
                        self.menu = after
            return
        if button in _DIRS:
            self.cursor[self.menu] = _nav(self.cursor[self.menu], button)
            return
        if self.menu == "action" and button == "A":
            c = self.cursor["action"]
            if c == 0:
                if not any(m["pp"] > 0 for m in self.player["moves"]):
                    self._turn(None)
                else:
                    self.menu = "move"
            elif c == 3:
                if self.can_run:
                    self.outcome = "unknown"
                    self.log.append("got away safely")
                    self._text(1)
                else:
                    self.log.append("no running from a trainer battle")
                    self._text(1, "action")
            else:
                self.log.append("menu not available in this mock")
                self._text(1, "action")
        elif self.menu == "move":
            if button == "B":
                self.menu = "action"
            elif button == "A":
                k = self.cursor["move"]
                moves = self.player["moves"]
                if k >= len(moves):
                    return
                if moves[k]["pp"] <= 0:
                    self.log.append("no PP left for this move")
                    self._text(1, "move")
                    return
                moves[k]["pp"] -= 1
                self._turn(moves[k]["id"])

    def _damage(self, user: dict, target: dict, move_id: Optional[int]) -> int:
        if move_id == METRONOME:
            pool = [i for i in range(1, 355) if (data.move(i) or {}).get("power") and i != METRONOME]
            move_id = self.rng.choice(pool)
        mv = data.move(move_id) if move_id else {"type": "normal", "power": 50, "category": "physical",
                                                 "accuracy": None, "name": "struggle"}
        if not mv or not mv.get("power"):
            return 0
        if mv.get("accuracy") and self.rng.random() * 100 >= mv["accuracy"]:
            self.log.append(f"{mv['name']} missed")
            return 0
        us, them = data.species_by_game_index(user["species"]), data.species_by_game_index(target["species"])
        a, d = ("atk", "def") if mv["category"] == "physical" else ("spa", "spd")
        lv = user["level"]
        dmg = ((2 * lv / 5 + 2) * mv["power"] * _stat(us["base"][a], lv) / _stat(them["base"][d], target["level"])) / 50 + 2
        if mv["type"] in us["types"]:
            dmg *= 1.5
        dmg *= data.effectiveness(mv["type"], them["types"]) * self.rng.uniform(0.85, 1.0)
        self.log.append(f"{us['name']} used {mv['name']}: {int(dmg)}")
        return int(dmg)

    def _turn(self, move_id: Optional[int]) -> None:
        self.opp["hp"] = max(0, self.opp["hp"] - self._damage(self.player, self.opp, move_id))
        if self.opp["hp"] == 0:
            self.outcome = "win"
            self._text(2)
            return
        usable = [m for m in self.opp["moves"] if m["pp"] > 0]
        om = usable[0] if usable else None
        if om:
            om["pp"] -= 1
        self.player["hp"] = max(0, self.player["hp"] - self._damage(self.opp, self.player, om["id"] if om else None))
        if self.player["hp"] == 0:
            self.outcome = "lose"
        self._text(3)
