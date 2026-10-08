"""RandomBrain: uniformly random button taps (seeded => reproducible). Baseline / fallback."""

from __future__ import annotations

import random
from typing import Sequence, Tuple

from ..schema import Action, ButtonPress, Decision, Observation
from ..rl.signal import ProgressSignal
from .base import Brain

DEFAULT_BUTTONS = ("A", "B", "UP", "DOWN", "LEFT", "RIGHT")


class RandomBrain(Brain):
    name = "random"

    def __init__(self, seed: int = 0, buttons: Sequence[str] = DEFAULT_BUTTONS,
                 min_frames: int = 2, max_frames: int = 16):
        self.seed = seed
        self.buttons = tuple(buttons)
        self.min_frames, self.max_frames = min_frames, max_frames
        self.signal = ProgressSignal()
        self.reset()

    def reset(self) -> None:
        self._rng = random.Random(self.seed)
        self.signal.reset()

    def decide(self, obs: Observation) -> Tuple[Action, Decision]:
        b = self._rng.choice(self.buttons)
        stuck = obs.position is not None and obs.position == getattr(self, "_last_pos", None)
        b = self.signal.divert(b, stuck and b == self.signal.last_button)
        self._last_pos = obs.position
        n = self._rng.randint(self.min_frames, self.max_frames)
        parts = self.signal.note(obs, b)
        act = Action([ButtonPress(b, n, 2)], source=f"brain:{self.name}")
        return act, self._decision("random exploration", f"random {b} for {n} frames; reward {parts['reward']:.2f}")
