"""Reinforcement learning for clear-the-game, then the Kanto Pokedex."""

from .curriculum import STAGES
from .progress import progress_delta
from .ppo import train_short_ppo

__all__ = ["STAGES", "progress_delta", "train_short_ppo"]
