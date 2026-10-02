"""Brain interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple

from ..schema import Action, Decision, Observation


class BrainUnavailable(RuntimeError):
    """Raised by a brain that cannot decide right now (e.g. LLM not configured).
    The arbiter catches this and falls back to the next brain."""


class Brain(ABC):
    name: str = "brain"

    @abstractmethod
    def decide(self, obs: Observation) -> Tuple[Action, Decision]:
        """Look at one Observation and return the Action to take plus a Decision explaining it.

        Brains must be pure w.r.t. the game: they never touch the adapter directly.
        Action.source should be ``f"brain:{self.name}"``.
        """

    def reset(self) -> None:
        """Forget per-run state (called when a run starts)."""

    def _decision(self, plan: str, reason: str) -> Decision:
        return Decision(brain=self.name, plan=plan, reason=reason)
