"""Gymnasium-shaped wrapper over an Adapter. Gymnasium itself is optional.

reset() accepts a game-brain sidecar or its .state path and restores the emulator
snapshot, frame counter, and adapter state. step() returns observation, progress
reward, terminated, truncated, info.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .. import savestate as save_utils
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
        if savestate is None:
            obs = self.adapter.reset()
        else:
            if not self.adapter.supports_save_state:
                raise ValueError(f"adapter {self.adapter.name} does not support save states")
            side = save_utils.load_sidecar(savestate)
            if side["adapter"] != self.adapter.name:
                raise ValueError(f"save is for adapter {side['adapter']!r}, not {self.adapter.name!r}")
            rom = getattr(self.adapter, "rom_sha1", None)
            if side.get("rom_sha1") and rom and side["rom_sha1"] != rom:
                raise ValueError("save was made with a different ROM")
            data = save_utils.read_state(side)
            self.adapter.reset()
            obs = self.adapter.load_state(
                data, frame=int(side["frame"]), adapter_state=side.get("adapter_state"))
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
        if pos is not None:
            pos = (obs.ram.get("map_bank"), obs.ram.get("map_id"), *pos)
        parts = progress_delta(self._prev or {}, current)
        parts["loop"] = self._loop.penalty(pos, button)
        parts["reward"] += parts["loop"]
        self._prev = current
        self._steps += 1
        champion = bool(obs.ram.get("champion") or obs.ram.get("hall_of_fame"))
        return obs, parts["reward"], champion, self._steps >= self.max_steps, parts
