"""Brains: observe -> decide -> (Action, Decision)."""

from .base import Brain, BrainUnavailable
from .battle import RuleBattleBrain
from .goals import GoalPlanner, Milestone, Target, firered_milestones
from .llm import LLMBrain
from .path import PathBrain
from .random_brain import RandomBrain
from .rule import RuleBrain

__all__ = ["Brain", "BrainUnavailable", "GoalPlanner", "LLMBrain", "Milestone", "PathBrain", "RandomBrain",
           "RuleBattleBrain", "RuleBrain", "Target", "firered_milestones", "make_brain", "make_brains"]


def make_brain(name: str, **kwargs) -> Brain:
    table = {"rule": RuleBrain, "random": RandomBrain, "llm": LLMBrain, "path": PathBrain,
             "battle": RuleBattleBrain}
    if name not in table:
        raise ValueError(f"unknown brain {name!r} (available: {sorted(table)})")
    return table[name](**kwargs)


def make_brains(spec: str, seed: int = 0, battle_confidence: "float | None" = None) -> list:
    """``"battle,path,rule"`` -> brain objects in priority order (battle first: it only acts while
    ``in_battle`` is True and defers otherwise)."""
    out = []
    for name in (n.strip() for n in spec.split(",") if n.strip()):
        kw: dict = {}
        if name == "random":
            kw["seed"] = seed
        if name == "battle" and battle_confidence is not None:
            kw["confidence_threshold"] = battle_confidence
        out.append(make_brain(name, **kw))
    return out
