"""Reinforcement learning for clear-the-game, then the Kanto Pokedex.

Package import stays lazy so brain modules can use ``rl.signal`` without pulling
``rl.ppo`` -> ``memory`` -> ``arbiter`` (circular import).
"""

from __future__ import annotations

from typing import Any

__all__ = ["STAGES", "progress_delta", "train_short_ppo"]


def __getattr__(name: str) -> Any:
    if name == "STAGES":
        from .curriculum import STAGES
        return STAGES
    if name == "progress_delta":
        from .progress import progress_delta
        return progress_delta
    if name == "train_short_ppo":
        from .ppo import train_short_ppo
        return train_short_ppo
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
