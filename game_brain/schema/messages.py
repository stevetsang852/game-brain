"""Message dataclasses + (de)serialisation.

Design rules:
* Plain JSON-serialisable dicts on the wire, with a ``type`` tag and ``v`` (schema version).
* Durations are always in **emulator frames**, never milliseconds. mGBA advances one
  frame at a time, so a list of (button, frames) is fully deterministic and replayable.
* Game-specific data lives in ``Observation.ram`` (a flat dict) so the schema itself stays
  game-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

#: Logical button names. "NONE" = hold nothing (wait). Adapters map these to native keys.
BUTTONS = ("A", "B", "SELECT", "START", "RIGHT", "LEFT", "UP", "DOWN", "R", "L", "NONE")

#: Upper bound to catch unit mistakes (e.g. someone passing milliseconds). 600 f = 10 s @60fps.
MAX_PRESS_FRAMES = 600


class SchemaError(ValueError):
    """Raised when a message fails validation."""


class Mode(str, Enum):
    AUTO = "auto"      # brain decides and its actions are executed
    ASSIST = "assist"  # brain decides; a human action, when given, overrides it
    MANUAL = "manual"  # no brain actions at all; only human actions are executed
    SHADOW = "shadow"  # brain decides but its actions are only logged, never executed

    @classmethod
    def parse(cls, value: "str | Mode") -> "Mode":
        if isinstance(value, Mode):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise SchemaError(f"unknown mode {value!r}; expected one of {[m.value for m in cls]}") from exc


# --------------------------------------------------------------------------- Action

@dataclass(frozen=True)
class ButtonPress:
    """Hold ``button`` for ``frames`` frames, then release everything for ``release_frames``."""

    button: str
    frames: int = 1
    release_frames: int = 0

    def __post_init__(self) -> None:
        btn = str(self.button).upper()
        object.__setattr__(self, "button", btn)
        if btn not in BUTTONS:
            raise SchemaError(f"unknown button {self.button!r}; expected one of {BUTTONS}")
        for name in ("frames", "release_frames"):
            val = getattr(self, name)
            if not isinstance(val, int) or isinstance(val, bool):
                raise SchemaError(f"{name} must be an int number of frames, got {val!r}")
        if not (1 <= self.frames <= MAX_PRESS_FRAMES):
            raise SchemaError(f"frames must be in [1, {MAX_PRESS_FRAMES}], got {self.frames}")
        if not (0 <= self.release_frames <= MAX_PRESS_FRAMES):
            raise SchemaError(f"release_frames must be in [0, {MAX_PRESS_FRAMES}], got {self.release_frames}")

    @property
    def total_frames(self) -> int:
        return self.frames + self.release_frames


@dataclass(frozen=True)
class Action:
    presses: List[ButtonPress] = field(default_factory=list)
    source: str = "brain"  # "brain:<name>" | "manual" | "noop"

    TYPE = "action"

    @property
    def total_frames(self) -> int:
        return sum(p.total_frames for p in self.presses)

    @classmethod
    def wait(cls, frames: int, source: str = "noop") -> "Action":
        return cls([ButtonPress("NONE", frames)], source=source)

    @classmethod
    def tap(cls, button: str, frames: int = 2, release_frames: int = 4, source: str = "brain") -> "Action":
        return cls([ButtonPress(button, frames, release_frames)], source=source)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE, "v": SCHEMA_VERSION, "source": self.source,
                "presses": [asdict(p) for p in self.presses]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Action":
        _check_type(d, cls.TYPE)
        return cls([ButtonPress(**p) for p in d.get("presses", [])], source=d.get("source", "brain"))


# --------------------------------------------------------------------------- Observation

@dataclass
class Observation:
    frame: int
    game: str = ""
    ram: Dict[str, Any] = field(default_factory=dict)
    screenshot_path: Optional[str] = None
    screenshot_b64: Optional[str] = None  # PNG, base64; optional (dashboard live view)

    TYPE = "observation"

    def __post_init__(self) -> None:
        if not isinstance(self.frame, int) or self.frame < 0:
            raise SchemaError(f"frame must be a non-negative int, got {self.frame!r}")
        if not isinstance(self.ram, dict):
            raise SchemaError("ram must be a dict")

    # Convenience accessors for common fields (None when the adapter doesn't provide them)
    @property
    def in_battle(self) -> Optional[bool]:
        return self.ram.get("in_battle")

    @property
    def position(self) -> Optional[tuple]:
        if "player_x" in self.ram and "player_y" in self.ram:
            return (self.ram["player_x"], self.ram["player_y"])
        return None

    def summary(self) -> Dict[str, Any]:
        """Compact form for the run log (drops the base64 image)."""
        d = self.to_dict()
        d.pop("screenshot_b64", None)
        return d

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE, "v": SCHEMA_VERSION, "frame": self.frame, "game": self.game,
                "ram": dict(self.ram), "screenshot_path": self.screenshot_path,
                "screenshot_b64": self.screenshot_b64}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Observation":
        _check_type(d, cls.TYPE)
        return cls(frame=d["frame"], game=d.get("game", ""), ram=dict(d.get("ram", {})),
                   screenshot_path=d.get("screenshot_path"), screenshot_b64=d.get("screenshot_b64"))


# --------------------------------------------------------------------------- Decision

@dataclass
class Decision:
    brain: str            # which brain is in charge / produced this
    plan: str             # current plan, human readable
    reason: str = ""      # why this action
    mode: str = Mode.AUTO.value
    executed: bool = True  # False in SHADOW mode / when overridden by a human

    TYPE = "decision"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE, "v": SCHEMA_VERSION, "brain": self.brain, "plan": self.plan,
                "reason": self.reason, "mode": self.mode, "executed": self.executed}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Decision":
        _check_type(d, cls.TYPE)
        return cls(brain=d["brain"], plan=d.get("plan", ""), reason=d.get("reason", ""),
                   mode=Mode.parse(d.get("mode", "auto")).value, executed=bool(d.get("executed", True)))


# --------------------------------------------------------------------------- ModeCommand

@dataclass
class ModeCommand:
    mode: Mode
    issued_by: str = "dashboard"
    reason: str = ""

    TYPE = "mode_command"

    def __post_init__(self) -> None:
        self.mode = Mode.parse(self.mode)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE, "v": SCHEMA_VERSION, "mode": self.mode.value,
                "issued_by": self.issued_by, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ModeCommand":
        _check_type(d, cls.TYPE)
        return cls(mode=d["mode"], issued_by=d.get("issued_by", "dashboard"), reason=d.get("reason", ""))


# --------------------------------------------------------------------------- helpers

_TYPES = {c.TYPE: c for c in (Action, Observation, Decision, ModeCommand)}


def _check_type(d: Dict[str, Any], expected: str) -> None:
    if not isinstance(d, dict):
        raise SchemaError(f"expected a dict for {expected}, got {type(d).__name__}")
    t = d.get("type", expected)
    if t != expected:
        raise SchemaError(f"expected type {expected!r}, got {t!r}")
    v = d.get("v", SCHEMA_VERSION)
    if v != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema version {v} (this code speaks v{SCHEMA_VERSION})")


def to_json(msg: Any) -> str:
    return json.dumps(msg.to_dict(), separators=(",", ":"), sort_keys=True)


def from_json(text: str) -> Any:
    d = json.loads(text)
    t = d.get("type")
    if t not in _TYPES:
        raise SchemaError(f"unknown message type {t!r}")
    return _TYPES[t].from_dict(d)


# --------------------------------------------------------------------------- dashboard envelope
#
# Frontend's WebSocket envelope: {"type", "frame", "ts", "payload"} with
# type in {"observation", "decision", "mode_command", "action"}.
# payload = the message dict without its "type" tag (type lives on the envelope).

ENVELOPE_TYPES = tuple(_TYPES)


def to_envelope(msg: Any, frame: int, ts: Optional[float] = None) -> Dict[str, Any]:
    import time

    payload = msg.to_dict()
    t = payload.pop("type")
    return {"type": t, "frame": int(frame), "ts": time.time() if ts is None else float(ts),
            "payload": payload}


def from_envelope(env: Dict[str, Any]) -> Any:
    if not isinstance(env, dict) or not {"type", "payload"} <= set(env):
        raise SchemaError("envelope must be a dict with at least 'type' and 'payload'")
    t = env["type"]
    if t not in _TYPES:
        raise SchemaError(f"unknown envelope type {t!r}; expected one of {ENVELOPE_TYPES}")
    payload = dict(env["payload"])
    payload["type"] = t
    return _TYPES[t].from_dict(payload)
