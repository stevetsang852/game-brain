"""High-level battle intents (notes/battle-brain.md section 2). Never sent to the adapter:
:mod:`.compiler` turns them into ordinary frame-based button ``Action``s."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

KINDS = ("FIGHT", "SWITCH", "ITEM", "RUN")
#: action-menu cursor of each kind (verified by Backend: 0 FIGHT, 1 BAG, 2 POKéMON, 3 RUN)
ACTION_CURSOR = {"FIGHT": 0, "ITEM": 1, "SWITCH": 2, "RUN": 3}


@dataclass(frozen=True)
class BattleAction:
    kind: str                     # FIGHT | SWITCH | ITEM | RUN
    slot: Optional[int] = None    # FIGHT: move slot 0-3 (None = no PP left -> Struggle); SWITCH: party slot
    item_id: Optional[int] = None  # ITEM
    target: Optional[int] = None  # ITEM: party slot it is used on

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")
        if self.kind == "FIGHT" and self.slot is not None and not 0 <= self.slot <= 3:
            raise ValueError("FIGHT slot must be 0-3 or None")

    @property
    def id(self) -> str:
        if self.kind == "FIGHT":
            return "FIGHT" if self.slot is None else f"FIGHT:{self.slot}"
        if self.kind == "SWITCH":
            return f"SWITCH:{self.slot}"
        if self.kind == "ITEM":
            return f"ITEM:{self.item_id}" + (f"@{self.target}" if self.target is not None else "")
        return "RUN"
