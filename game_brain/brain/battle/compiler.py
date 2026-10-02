"""Closed-loop compiler: one :class:`BattleAction` -> a few ordinary button ``Action``s.

One button tap per step; every step looks at ``ram["battle"]`` again (menu + cursor) before
pressing the next button. Verified UI facts (Backend, mgba-bridge.md): both menus are 2x2,
row-major (action: 0 FIGHT 1 BAG / 2 POKéMON 3 RUN; move: slots 0 1 / 2 3); pressing past an
edge does not wrap; the cursor is remembered between turns; A and B both advance text.

Only FIGHT (incl. Struggle) and RUN are compiled end-to-end. SWITCH/ITEM would need the
party/bag screens, which are not observable yet (menu == "other"): they raise ``CompileError``.
"""

from __future__ import annotations

from typing import Optional, Tuple

from ...schema import Action, ButtonPress
from .actions import ACTION_CURSOR, BattleAction
from .state import BattleState


class CompileError(RuntimeError):
    pass


def nav_button(cursor: int, target: int) -> Optional[str]:
    """Next button to move a 2x2 row-major cursor towards ``target`` (None = already there)."""
    (r, c), (tr, tc) = divmod(cursor, 2), divmod(target, 2)
    if r != tr:
        return "DOWN" if tr > r else "UP"
    if c != tc:
        return "RIGHT" if tc > c else "LEFT"
    return None


class BattleCompiler:
    def __init__(self, intent: BattleAction, tap: int = 2, release: int = 14, max_steps: int = 40,
                 source: str = "brain:battle"):
        if intent.kind in ("SWITCH", "ITEM"):
            raise CompileError(f"{intent.kind} needs the party/bag screens, which are not observable yet")
        self.intent = intent
        self.tap, self.release, self.max_steps, self.source = tap, release, max_steps, source
        self.steps = 0
        self.committed = False     # the final A was pressed: the game is executing the intent
        self._blind: Optional[int] = None  # assumed cursor when ram gives none (after homing)
        self._homing = 0

    def _press(self, button: str) -> Action:
        return Action([ButtonPress(button, self.tap, self.release)], source=self.source)

    @property
    def done(self) -> bool:
        return self.committed

    def step(self, bs: BattleState) -> Tuple[Action, str]:
        """Next button. Call only while not ``done``."""
        self.steps += 1
        if self.steps > self.max_steps:
            raise CompileError(f"{self.intent.id}: not executed after {self.max_steps} steps")
        if bs.menu == "action":
            target = ACTION_CURSOR[self.intent.kind]
            return self._goto(bs.cursor, target, "action", commit=self.intent.kind != "FIGHT"
                              or self.intent.slot is None)
        if bs.menu == "move":
            if self.intent.kind != "FIGHT":
                return self._press("B"), "move menu open but intent is not FIGHT -> B (back)"
            if self.intent.slot is None:
                return self._press("B"), "no usable move slot -> back to the action menu"
            return self._goto(bs.cursor, self.intent.slot, "move", commit=True)
        return self._press("B"), "text / animation (menu 'other') -> B to advance"

    def _goto(self, cursor: Optional[int], target: int, menu: str, commit: bool) -> Tuple[Action, str]:
        if cursor is None:  # not reported: home to the top-left first (no wrap), then navigate blind
            if self._blind is None:
                self._homing += 1
                if self._homing <= 2:
                    return self._press("LEFT" if self._homing == 1 else "UP"), f"{menu} menu: cursor unknown -> home"
                self._blind = 0
            cursor = self._blind
        btn = nav_button(cursor, target)
        if btn is None:
            if commit:
                self.committed = True
            self._blind = None
            self._homing = 0
            return self._press("A"), f"{menu} menu: cursor on {target} -> A"
        if self._blind is not None:
            self._blind = {"UP": cursor - 2, "DOWN": cursor + 2, "LEFT": cursor - 1, "RIGHT": cursor + 1}[btn]
        return self._press(btn), f"{menu} menu: cursor {cursor} -> {target}: {btn}"
