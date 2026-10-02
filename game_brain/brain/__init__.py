"""Brains: observe -> decide -> (Action, Decision)."""

from .base import Brain, BrainUnavailable
from .llm import LLMBrain
from .random_brain import RandomBrain
from .rule import RuleBrain

__all__ = ["Brain", "BrainUnavailable", "LLMBrain", "RandomBrain", "RuleBrain", "make_brain"]


def make_brain(name: str, **kwargs) -> Brain:
    table = {"rule": RuleBrain, "random": RandomBrain, "llm": LLMBrain}
    if name not in table:
        raise ValueError(f"unknown brain {name!r} (available: {sorted(table)})")
    return table[name](**kwargs)
