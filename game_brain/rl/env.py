"""Gymnasium-shaped wrapper over an Adapter. Gymnasium itself is optional.

``reset()`` starts from power-on, or from a start snapshot: raw save-state ``bytes``, a
``.state`` path, or a #28 sidecar ``.json`` path (notes/savestate-format.md; the state's sha1
is checked and the sidecar's ``frame`` / ``adapter_state`` are restored). The snapshot is read
from disk once and kept in memory, so repeated resets never touch the disk again.

``step()`` returns observation, progress reward, terminated, truncated, info. The reward is
:func:`progress_delta` plus the :class:`AntiLoop` penalty.

Screen: a save state restores the emulator, not the already drawn frame buffer. When the
adapter has ``screen_rgbx()`` and ``clear_screen()`` (mGBA), reset loads the state, zeroes the
buffer, runs one frame and loads the state again, so the first screen after reset is the same
every time while the game state is exactly the snapshot. Adapters without these hooks (mock)
are unaffected. For Gymnasium spaces see :mod:`game_brain.rl.gym_wrapper`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from .. import savestate as _savestate
from ..schema import Action, ButtonPress, Observation
from .anti_loop import AntiLoop
from .progress import progress_delta

BUTTONS = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B")

StartSpec = Union[bytes, bytearray, str, Path, Dict[str, Any]]


def load_start(spec: StartSpec) -> Dict[str, Any]:
    """Resolve a start spec to ``{"state", "frame", "adapter_state", "milestones_done", "source"}``.

    ``bytes``: a raw save state (frame 0). ``.json``: a #28 sidecar (sha1 checked by
    :func:`savestate.read_state`). Any other path: a raw ``.state`` file; if a sidecar sits next
    to it, the sidecar is used instead so frame / adapter_state / sha1 come along.
    """
    if isinstance(spec, dict):
        if not isinstance(spec.get("state"), (bytes, bytearray)):
            raise ValueError("start snapshot dict needs 'state' bytes")
        return {"state": bytes(spec["state"]), "frame": int(spec.get("frame") or 0),
                "adapter_state": spec.get("adapter_state"),
                "milestones_done": list(spec.get("milestones_done") or []), "source": spec.get("source")}
    if isinstance(spec, (bytes, bytearray)):
        return {"state": bytes(spec), "frame": 0, "adapter_state": None, "milestones_done": [],
                "source": "bytes"}
    path = Path(spec).expanduser()
    sidecar = path if path.suffix == ".json" else path.with_suffix(".json")
    if sidecar.is_file():
        side = _savestate.load_sidecar(sidecar)
        return {"state": _savestate.read_state(side), "frame": int(side.get("frame") or 0),
                "adapter_state": side.get("adapter_state"),
                "milestones_done": list(side.get("milestones_done") or []), "source": side["_path"]}
    if path.suffix == ".json":
        raise FileNotFoundError(f"save sidecar not found: {path}")
    return {"state": path.read_bytes(), "frame": 0, "adapter_state": None, "milestones_done": [],
            "source": str(path.resolve())}


class FireRedEnv:
    def __init__(self, adapter, max_steps: int = 20000, start: Optional[StartSpec] = None):
        self.adapter = adapter
        self.max_steps = max_steps
        self._steps = 0
        self._prev: Optional[dict] = None
        self._loop = AntiLoop()
        self._booted = False
        self._start: Optional[Dict[str, Any]] = None
        self._start_key: Any = None
        if start is not None:
            self._set_start(start)

    @property
    def start_snapshot(self) -> Optional[Dict[str, Any]]:
        return self._start

    def _set_start(self, spec: StartSpec) -> Dict[str, Any]:
        if not getattr(self.adapter, "supports_save_state", False):
            raise ValueError(f"adapter {getattr(self.adapter, 'name', self.adapter)!r} has no save states")
        key = str(Path(spec).expanduser().resolve()) if isinstance(spec, (str, Path)) else None
        if self._start is None or key is None or key != self._start_key:
            self._start = load_start(spec)
            self._start_key = key
        return self._start

    def _clear_screen(self) -> None:
        if hasattr(self.adapter, "screen_rgbx") and hasattr(self.adapter, "clear_screen"):
            self.adapter.clear_screen()

    def _load(self, snap: Dict[str, Any]) -> Observation:
        if not self._booted:          # mGBA exposes memory only after the first reset()
            self.adapter.reset()
            self._booted = True
        load = lambda: self.adapter.load_state(snap["state"], frame=snap["frame"],
                                               adapter_state=snap["adapter_state"])
        if hasattr(self.adapter, "screen_rgbx") and hasattr(self.adapter, "clear_screen"):
            load()
            self.adapter.clear_screen()
            self.adapter.act(Action.wait(1, source="rl:env"))
        return load()

    def reset(self, savestate: Optional[StartSpec] = None) -> Observation:
        if savestate is not None:
            obs = self._load(self._set_start(savestate))
        elif self._start is not None:
            obs = self._load(self._start)
        else:
            obs = self.adapter.reset()
            self._booted = True
            self._clear_screen()      # power-on is forced blank: don't keep the last episode's pixels
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
