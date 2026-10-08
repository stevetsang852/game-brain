"""RuleBrain: hand-written heuristics, game-agnostic as far as possible.

* No player position yet (intro / title / dialogue before the map loads) -> mash A.
* ``in_battle`` -> mash A (pick first option / advance text).
* Overworld -> walk a fixed pattern of tile steps. If the position doesn't change after
  a walk step, assume a wall or a text box: press A once and rotate to the next direction.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ..schema import Action, ButtonPress, Decision, Observation
from ..rl.signal import ProgressSignal
from .base import Brain

DEFAULT_PATTERN = ("DOWN", "DOWN", "LEFT", "LEFT", "UP", "UP", "RIGHT", "RIGHT")


class RuleBrain(Brain):
    name = "rule"

    def __init__(self, pattern: Sequence[str] = DEFAULT_PATTERN, step_frames: int = 16,
                 tap_frames: int = 2, tap_release: int = 6, stuck_after: int = 2):
        self.pattern = tuple(p.upper() for p in pattern)
        self.step_frames = step_frames
        self.tap_frames = tap_frames
        self.tap_release = tap_release
        self.stuck_after = stuck_after
        self.signal = ProgressSignal()
        self.reset()

    def reset(self) -> None:
        self._i = 0
        self._last_pos: Optional[tuple] = None
        self._last_was_walk = False
        self._stuck = 0
        self.signal.reset()

    @property
    def src(self) -> str:
        return f"brain:{self.name}"

    def _tap_a(self) -> Action:
        return Action([ButtonPress("A", self.tap_frames, self.tap_release)], source=self.src)

    def decide(self, obs: Observation) -> Tuple[Action, Decision]:
        pos = obs.position
        if pos is None:
            self._last_was_walk = False
            return self._tap_a(), self._decision(
                "get through intro/dialogue", "no player position in RAM yet -> mash A")
        if obs.in_battle:
            self._last_was_walk = False
            return self._tap_a(), self._decision("battle: advance", "in_battle flag set -> press A")

        if self._last_was_walk and pos == self._last_pos:
            self._stuck += 1
        else:
            self._stuck = 0
        self._last_pos = pos

        if self._stuck >= self.stuck_after:
            self._stuck = 0
            self._i = (self._i + 1) % len(self.pattern)
            self._last_was_walk = False
            return self._tap_a(), self._decision(
                "explore with walk pattern",
                f"position {pos} unchanged after {self.stuck_after} steps -> press A, rotate direction")

        direction = self.signal.divert(self.pattern[self._i % len(self.pattern)], self._stuck > 0)
        self._i = (self._i + 1) % len(self.pattern)
        self._last_was_walk = True
        parts = self.signal.note(obs, direction)
        act = Action([ButtonPress(direction, self.step_frames, 0)], source=self.src)
        return act, self._decision("explore with walk pattern", f"overworld at {pos} -> step {direction}; reward {parts['reward']:.2f}")
