"""Adapter interface. One adapter per (game, emulator) pair.

This is the contract the mGBA bridge (owned by Backend, see notes/adapter-interface.md)
implements. Everything above this layer (brains, arbiter, run log, dashboard) is
game- and emulator-agnostic and only speaks the messages in ``game_brain.schema``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..schema import Action, Observation


class Adapter(ABC):
    #: short identifier written into run-log headers, e.g. "mock" or "gba_mgba/firered"
    name: str = "adapter"

    @abstractmethod
    def reset(self) -> Observation:
        """(Re)start the game from power-on (or a fixed start state) and return the first Observation."""

    @abstractmethod
    def observe(self) -> Observation:
        """Read the current game state. Must not advance the emulator."""

    @abstractmethod
    def act(self, action: Action) -> int:
        """Execute ``action`` frame-by-frame, in order.

        For each ButtonPress: hold ``button`` for ``frames`` frames, then hold nothing for
        ``release_frames`` frames ("NONE" = hold nothing). Must be deterministic: the same
        start state + the same Action sequence => the same frames. Returns frames advanced
        (must equal ``action.total_frames``).
        """

    @property
    @abstractmethod
    def frame(self) -> int:
        """Current emulator frame number (monotonic since reset)."""

    def screenshot(self, path: str) -> Optional[str]:
        """Write a PNG of the current frame to ``path``; return it, or None if unsupported."""
        return None

    def close(self) -> None:
        """Release emulator resources."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# Backwards-friendly alias
GameAdapter = Adapter
