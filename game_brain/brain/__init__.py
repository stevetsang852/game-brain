"""Brains: observe -> decide -> (Action, Decision)."""

from .base import Brain, BrainUnavailable
from .goals import GoalPlanner, Milestone, Target, firered_milestones
from .llm import LLMBrain
from .path import PathBrain
from .random_brain import RandomBrain
from .rule import RuleBrain

__all__ = ["Brain", "BrainUnavailable", "GoalPlanner", "LLMBrain", "Milestone", "PathBrain", "RandomBrain",
           "RuleBrain", "Target", "firered_milestones", "make_brain"]


def make_brain(name: str, **kwargs) -> Brain:
    table = {"rule": RuleBrain, "random": RandomBrain, "llm": LLMBrain, "path": PathBrain}
    if name not in table:
        raise ValueError(f"unknown brain {name!r} (available: {sorted(table)})")
    return table[name](**kwargs)
