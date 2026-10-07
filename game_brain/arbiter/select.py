"""Auto-mode choice among low-level brains.

The arbiter still executes one action. This only orders who is asked.
"""

from __future__ import annotations

from typing import Sequence

ROLES = {
    "llm": "llm",
    "battle": "battle",
    "path": "path",
    "rl": "rl",
    "ppo": "rl",
    "rule": "rule",
}


def role_of(brain) -> str:
    name = getattr(brain, "name", "") or ""
    return ROLES.get(name, "other")


def situation(obs) -> str:
    ram = getattr(obs, "ram", None) or {}
    if ram.get("battle") or ram.get("in_battle"):
        return "battle"
    if ram.get("rl_ready"):
        return "rl"
    if ram.get("probe") or ram.get("unverified"):
        return "unverified"
    return "path"


PREFER = {
    "battle": ("llm", "battle", "path", "rule", "rl", "other"),
    "rl": ("rl", "path", "rule", "llm", "battle", "other"),
    "unverified": ("llm", "path", "rule", "rl", "battle", "other"),
    "path": ("path", "rule", "llm", "rl", "battle", "other"),
}


def select_order(brains: Sequence, obs) -> list:
    """Preferred brains first. Original order is kept inside a role."""
    prefer = PREFER[situation(obs)]
    ranked = []
    for role in prefer:
        for brain in brains:
            if role_of(brain) == role and brain not in ranked:
                ranked.append(brain)
    return ranked
