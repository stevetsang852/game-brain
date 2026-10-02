"""Typed view of ``ram["battle"]`` (shape fixed by Backend, see firered_battle.py / mgba-bridge.md)::

    {"menu": "action" | "move" | "other", "cursor": 0-3 | None,
     "player":   {"species", "level", "hp", "max_hp", "moves": [{"id", "pp"}]} | None,
     "opponent": {"species", "level", "hp", "max_hp", "hp_pct", "moves": [...]} | None,
     "outcome": None | "win" | "lose" | "unknown"}

Only present while ``in_battle`` is True. ``species``/move ``id`` are the game's numbers;
types come from the PokeAPI tables (``game_brain.data``). Status, max PP, battle type and turn
are not available and nothing here depends on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ... import data

MENUS = ("action", "move", "other")


@dataclass
class MoveSlot:
    id: int
    pp: int

    @property
    def info(self) -> Optional[dict]:
        return data.move(self.id)

    @property
    def name(self) -> str:
        m = self.info
        return m["name"] if m else f"move#{self.id}"


@dataclass
class BattleMon:
    species: int
    level: int
    hp: Optional[int] = None
    max_hp: Optional[int] = None
    hp_pct: Optional[float] = None
    moves: List[MoveSlot] = field(default_factory=list)

    @property
    def info(self) -> Optional[dict]:
        return data.species_by_game_index(self.species)

    @property
    def name(self) -> str:
        s = self.info
        return s["name"] if s else f"species#{self.species}"

    @property
    def types(self) -> List[str]:
        s = self.info
        return list(s["types"]) if s else []

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Optional["BattleMon"]:
        if not isinstance(d, dict) or not d.get("species"):
            return None
        moves = [MoveSlot(int(m["id"]), int(m.get("pp", 0))) for m in d.get("moves") or [] if m.get("id")]
        return cls(species=int(d["species"]), level=int(d.get("level") or 1), hp=d.get("hp"),
                   max_hp=d.get("max_hp"), hp_pct=d.get("hp_pct"), moves=moves[:4])

    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"species": self.species, "name": self.name, "level": self.level}
        for k in ("hp", "max_hp", "hp_pct"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        out["moves"] = [{"id": m.id, "name": m.name, "pp": m.pp} for m in self.moves]
        return out


@dataclass
class BattleState:
    menu: str
    cursor: Optional[int]
    player: Optional[BattleMon]
    opponent: Optional[BattleMon]
    outcome: Optional[str]

    @property
    def ready(self) -> bool:
        """Both mons filled in (the first ~5 observations of a battle have neither)."""
        return self.player is not None and self.opponent is not None

    @classmethod
    def from_ram(cls, ram: Dict[str, Any]) -> Optional["BattleState"]:
        b = ram.get("battle")
        if not isinstance(b, dict):
            return None
        menu = b.get("menu") if b.get("menu") in MENUS else "other"
        cur = b.get("cursor")
        cur = int(cur) if isinstance(cur, int) and 0 <= cur <= 3 else None
        return cls(menu=menu, cursor=cur, player=BattleMon.from_dict(b.get("player")),
                   opponent=BattleMon.from_dict(b.get("opponent")), outcome=b.get("outcome"))

    def summary(self) -> Dict[str, Any]:
        return {"menu": self.menu, "cursor": self.cursor, "outcome": self.outcome,
                "player": self.player.summary() if self.player else None,
                "opponent": self.opponent.summary() if self.opponent else None}
