"""A tiny deterministic fake "game" so brains/arbiter/log/demo run without a ROM or emulator.

Model (loosely shaped like a Pokemon start):
* ``intro``: ``intro_presses`` presses of A are needed to get through the intro/dialogue.
* ``overworld``: player on a ``size`` x ``size`` grid in map (bank 4, id 1). Holding a
  d-pad direction for >= 8 frames moves one tile (shorter holds only turn the player).
  Walls are the grid edges, so a brain that walks into one gets "stuck".
* Stepping on a tile listed in ``battle_tiles`` sets ``in_battle`` until A is pressed
  ``battle_presses`` times.
Frames advance exactly by ``action.total_frames``.
"""

from __future__ import annotations

from typing import Iterable, Tuple

from ..base import Adapter
from ...schema import Action, Observation

_DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
STEP_HOLD_FRAMES = 8


class MockAdapter(Adapter):
    name = "mock"

    def __init__(self, intro_presses: int = 12, size: int = 8,
                 battle_tiles: Iterable[Tuple[int, int]] = ((3, 6),), battle_presses: int = 3):
        self.intro_presses = intro_presses
        self.size = size
        self.battle_tiles = set(map(tuple, battle_tiles))
        self.battle_presses = battle_presses
        self.reset()

    def reset(self) -> Observation:
        self._frame = 0
        self.intro_left = self.intro_presses
        self.battle_left = 0
        self.x, self.y = self.size // 2, self.size // 2
        self.facing = "DOWN"
        self.executed = []  # every Action executed, for assertions
        return self.observe()

    @property
    def frame(self) -> int:
        return self._frame

    def observe(self) -> Observation:
        in_intro = self.intro_left > 0
        ram = {
            "scene": "intro" if in_intro else ("battle" if self.battle_left else "overworld"),
            "in_battle": self.battle_left > 0,
            "party_count": 0 if in_intro else 1,
        }
        if not in_intro:
            ram.update({"map_bank": 4, "map_id": 1, "player_x": self.x, "player_y": self.y,
                        "facing": self.facing})
        return Observation(frame=self._frame, game="MOCK", ram=ram)

    def act(self, action: Action) -> int:
        self.executed.append(action)
        start = self._frame
        for p in action.presses:
            self._apply(p.button, p.frames)
            self._frame += p.total_frames
        return self._frame - start

    def _apply(self, button: str, frames: int) -> None:
        if self.intro_left > 0:
            if button == "A":
                self.intro_left -= 1
            return
        if self.battle_left > 0:
            if button == "A":
                self.battle_left -= 1
            return
        if button in _DIRS:
            dx, dy = _DIRS[button]
            self.facing = button
            if frames >= STEP_HOLD_FRAMES:
                nx = min(max(self.x + dx, 0), self.size - 1)
                ny = min(max(self.y + dy, 0), self.size - 1)
                moved = (nx, ny) != (self.x, self.y)
                self.x, self.y = nx, ny
                if moved and (nx, ny) in self.battle_tiles:
                    self.battle_left = self.battle_presses
