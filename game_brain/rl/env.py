"""Gymnasium-shaped wrapper over an Adapter. Gymnasium itself is optional.

reset() can load a savestate path already supported by the adapter. step()
returns observation, progress reward, terminated, truncated, info.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..schema import Action, ButtonPress, Observation
from .anti_loop import AntiLoop
from .progress import progress_delta

BUTTONS = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B")


class FireRedEnv:
    def __init__(self, adapter, max_steps: int = 20000):
        self.adapter = adapter
        self.max_steps = max_steps
        self._steps = 0
        self._prev: Optional[dict] = None
        self._loop = AntiLoop()

    def reset(self, savestate: Optional[str] = None) -> Observation:
        if savestate and hasattr(self.adapter, "load_state"):
            self.adapter.load_state(savestate)
            obs = self.adapter.observe()
        else:
            obs = self.adapter.reset()
        self._steps = 0
        self._prev = obs.to_dict() if hasattr(obs, "to_dict") else {"ram": obs.ram}
        self._loop.reset()
        return obs

    def step(self, button: str, frames: int = 8) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        if button not in BUTTONS:
            raise ValueError(f"unsupported button {button}")
        self.adapter.act(Action([ButtonPress(button, frames, 8)], source="rl:env"))
        obs = self.adapter.observe()
        current = obs.to_dict() if hasattr(obs, "to_dict") else {"ram": obs.ram}
        pos = obs.position
        parts = progress_delta(self._prev or {}, current)
        parts["loop"] = self._loop.penalty(None if pos is None else tuple(pos), button)
        parts["reward"] += parts["loop"]
        self._prev = current
        self._steps += 1
        champion = bool(obs.ram.get("champion") or obs.ram.get("hall_of_fame"))
        return obs, parts["reward"], champion, self._steps >= self.max_steps, parts
