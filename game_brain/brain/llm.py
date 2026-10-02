"""LLMBrain: placeholder. No API key is configured yet, and this module makes NO network calls.

Planned shape (see notes/design.md): build a prompt from the Observation (RAM summary +
optional screenshot), ask the model for a short plan + a list of (button, frames), validate
it through ``game_brain.schema`` and return it. Until then ``decide`` raises
``BrainUnavailable`` so the arbiter falls back to the next brain.
"""

from __future__ import annotations

import os
from typing import Tuple

from ..schema import Action, Decision, Observation
from .base import Brain, BrainUnavailable


class LLMBrain(Brain):
    name = "llm"

    def __init__(self, model: str = "", api_key_env: str = "GAME_BRAIN_LLM_API_KEY"):
        self.model = model
        self.api_key_env = api_key_env

    @property
    def configured(self) -> bool:
        return bool(os.environ.get(self.api_key_env)) and bool(self.model)

    def decide(self, obs: Observation) -> Tuple[Action, Decision]:
        # Deliberately not implemented: no LLM API calls in this milestone.
        raise BrainUnavailable(
            "LLMBrain is a stub (no model/API integration yet); falling back"
            if not self.configured else "LLMBrain integration not implemented yet")
