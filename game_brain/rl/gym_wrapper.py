"""Thin Gymnasium wrapper over :class:`game_brain.rl.env.FireRedEnv`.

Optional dependency: ``pip install .[ml]`` (gymnasium, numpy). Nothing else in game-brain
imports this module, so the core package stays dependency-free.

* Actions: ``Discrete(len(BUTTONS))``, the same 6 buttons as :class:`FireRedEnv`.
* Observation: ``{"screen": uint8 (80, 120, 1) grayscale at half resolution,
  "ram": float32 (len(RAM_FEATURES),)}``. Adapters without ``screen_rgbx()`` (mock) give an
  all-zero screen.
* Reward, terminated, truncated: exactly :class:`FireRedEnv`'s (``progress_delta`` + anti-loop);
  ``info["reward_parts"]`` carries its parts.
* ``reset(options={"savestate": ...})`` takes the same start specs as ``FireRedEnv.reset``;
  a ``start=`` given to the constructor is used for every reset. ``seed`` only seeds
  ``action_space`` (the emulator is deterministic).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    import gymnasium as gym
    import numpy as np
    from gymnasium import spaces
except ImportError as exc:  # pragma: no cover - only without the extra
    raise ImportError("game_brain.rl.gym_wrapper needs `pip install gymnasium numpy` "
                      "(or `pip install .[ml]`)") from exc

from ..adapters import make_adapter
from .env import BUTTONS, FireRedEnv, StartSpec

SCREEN_SHAPE = (80, 120, 1)
RAM_FEATURES = ("map_bank", "map_id", "x", "y", "facing", "in_battle", "party_count",
                "battle_menu", "player_hp_pct", "opponent_hp_pct")
FACING = {"DOWN": 1, "UP": 2, "LEFT": 3, "RIGHT": 4}
MENUS = {"action": 1, "move": 2, "other": 3}


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _hp_pct(mon: Any) -> float:
    if not isinstance(mon, dict):
        return -1.0
    if isinstance(mon.get("hp_pct"), (int, float)):
        return float(mon["hp_pct"])
    hp, max_hp = mon.get("hp"), mon.get("max_hp")
    if isinstance(hp, int) and isinstance(max_hp, int) and max_hp > 0:
        return 100.0 * hp / max_hp
    return -1.0


def ram_features(ram: Dict[str, Any]) -> "np.ndarray":
    battle = ram.get("battle") or {}
    return np.asarray([
        _num(ram.get("map_bank")), _num(ram.get("map_id")), _num(ram.get("player_x")),
        _num(ram.get("player_y")), float(FACING.get(ram.get("facing"), 0)),
        float(bool(ram.get("in_battle"))), _num(ram.get("party_count")),
        float(MENUS.get(battle.get("menu"), 0)), _hp_pct(battle.get("player")),
        _hp_pct(battle.get("opponent")),
    ], dtype=np.float32)


class FireRedGymEnv(gym.Env):
    metadata = {"render_modes": ["rgb_array"]}

    def __init__(self, adapter: Any = "mgba", start: Optional[StartSpec] = None, max_steps: int = 20000,
                 frames: int = 8, render_mode: Optional[str] = None, **adapter_kwargs: Any):
        self.adapter = make_adapter(adapter, **adapter_kwargs) if isinstance(adapter, str) else adapter
        self.env = FireRedEnv(self.adapter, max_steps=max_steps, start=start)
        self.frames = frames
        self.render_mode = render_mode
        self.action_space = spaces.Discrete(len(BUTTONS))
        self.observation_space = spaces.Dict({
            "screen": spaces.Box(0, 255, SCREEN_SHAPE, np.uint8),
            "ram": spaces.Box(-1.0, 65535.0, (len(RAM_FEATURES),), np.float32),
        })
        self._obs = None

    def reset(self, *, seed: Optional[int] = None, options: Optional[Dict[str, Any]] = None):
        super().reset(seed=seed)
        if seed is not None:
            self.action_space.seed(seed)
        self._obs = self.env.reset((options or {}).get("savestate"))
        return self._observation(), {"step": 0, "frame": self._obs.frame}

    def step(self, action):
        if self._obs is None:
            raise RuntimeError("call reset() first")
        self._obs, reward, terminated, truncated, parts = self.env.step(BUTTONS[int(action)], self.frames)
        info = {"step": self.env._steps, "frame": self._obs.frame, "reward_parts": dict(parts)}
        return self._observation(), float(reward), bool(terminated), bool(truncated), info

    def render(self):
        return self._rgb() if self.render_mode == "rgb_array" else None

    def close(self):
        if self.adapter is not None:
            self.adapter.close()

    def _rgb(self) -> "np.ndarray":
        raw = self.adapter.screen_rgbx() if hasattr(self.adapter, "screen_rgbx") else None
        if not raw:
            return np.zeros((160, 240, 3), np.uint8)
        return np.frombuffer(raw, np.uint8).reshape(160, 240, 4)[:, :, :3].copy()

    def _screen(self) -> "np.ndarray":
        rgb = self._rgb()[::2, ::2].astype(np.uint16)
        gray = (rgb[..., 0] * 77 + rgb[..., 1] * 150 + rgb[..., 2] * 29) >> 8
        return gray.astype(np.uint8)[..., None]

    def _observation(self) -> Dict[str, "np.ndarray"]:
        return {"screen": self._screen(), "ram": ram_features(self._obs.ram)}
