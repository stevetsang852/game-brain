"""Shared progress signal for every brain. It does not choose the story route."""

from __future__ import annotations

from typing import Optional

from ..schema import Observation
from .anti_loop import AntiLoop
from .progress import progress_delta

_CYCLE = ("UP", "RIGHT", "DOWN", "LEFT", "A", "B")


class ProgressSignal:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.loop = AntiLoop()
        self.previous: Optional[dict] = None
        self.last_button = ""
        self.total = 0.0

    def note(self, obs: Observation, button: str) -> dict:
        current = {"ram": obs.ram}
        parts = progress_delta(self.previous or current, current, repeated=False)
        parts["loop"] = self.loop.penalty(None if obs.position is None else tuple(obs.position), button)
        parts["reward"] += parts["loop"]
        self.total += parts["reward"]
        self.previous = current
        self.last_button = button
        parts["total"] = self.total
        return parts

    def divert(self, button: str, stuck: bool) -> str:
        if not stuck or button not in _CYCLE:
            return button
        return _CYCLE[(_CYCLE.index(button) + 1) % len(_CYCLE)]
