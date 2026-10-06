"""Conservative, tabular behavior-cloning brain trained from human actions."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Optional

from ..cache import cache_json
from ..schema import Action, Decision, Observation
from .base import Brain, BrainUnavailable

MODEL_VERSION = 1


def state_features(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return the stable, game-observable fields used to match an offline demonstration."""
    ram = state.get("ram") or {}
    return {
        "game": state.get("game", ""),
        "scene": ram.get("scene"),
        "in_battle": ram.get("in_battle"),
        "map_bank": ram.get("map_bank"),
        "map_id": ram.get("map_id"),
        "player_x": ram.get("player_x"),
        "player_y": ram.get("player_y"),
        "facing": ram.get("facing"),
        "party_count": ram.get("party_count"),
        "battle": ram.get("battle"),
    }


def state_key(features: Dict[str, Any]) -> str:
    return json.dumps(features, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _action_key(action: Dict[str, Any]) -> str:
    presses = [{"button": p["button"], "frames": p["frames"],
                "release_frames": p.get("release_frames", 0)} for p in action.get("presses", [])]
    return json.dumps(presses, sort_keys=True, separators=(",", ":"))


class ImitationBrain(Brain):
    """Repeat a demonstrated action only at an exactly matching observed state.

    This first behavior-cloning baseline deliberately has no location generalization: a
    missing/unseen state falls through to the next configured brain.
    """

    name = "imitation"

    def __init__(self, model_path: Optional[str] = None, namespace: Optional[str] = None,
                 confidence_threshold: float = 0.6):
        try:
            confidence_threshold = float(confidence_threshold)
        except (TypeError, ValueError):
            raise ValueError("imitation confidence threshold must be in [0, 1]") from None
        if not math.isfinite(confidence_threshold) or not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("imitation confidence threshold must be in [0, 1]")
        self.model_path = Path(model_path).expanduser() if model_path else None
        self.namespace = namespace
        self.confidence_threshold = confidence_threshold
        self.states: Dict[str, list] = {}
        if self.model_path is not None:
            try:
                model = cache_json(self.model_path)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot load imitation model {self.model_path}: {exc}") from exc
            if not isinstance(model, dict):
                raise ValueError("imitation model must be a JSON object")
            if model.get("format_version") != MODEL_VERSION:
                raise ValueError(f"unsupported imitation model version: {model.get('format_version')!r}")
            if not isinstance(model.get("namespace"), str) or not isinstance(model.get("states"), dict):
                raise ValueError("imitation model must contain a namespace and states object")
            if namespace is not None and model.get("namespace") != namespace:
                raise ValueError("imitation model namespace does not match this adapter/ROM "
                                 f"({model.get('namespace')!r} != {namespace!r})")
            self.namespace = model.get("namespace")
            self.states = {}
            for key, votes in model["states"].items():
                if not isinstance(key, str) or not isinstance(votes, list) or not votes:
                    raise ValueError("imitation model contains an invalid state entry")
                checked = []
                for vote in votes:
                    count = vote.get("count") if isinstance(vote, dict) else None
                    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                        raise ValueError("imitation model action counts must be positive integers")
                    action = Action.from_dict({"type": "action", "v": 1,
                                               "source": "brain:imitation",
                                               "presses": vote.get("presses")})
                    if not action.presses:
                        raise ValueError("imitation model actions must contain at least one press")
                    checked.append({"presses": [{"button": p.button, "frames": p.frames,
                                                 "release_frames": p.release_frames}
                                                for p in action.presses], "count": count})
                self.states[key] = checked

    def decide(self, obs: Observation):
        if not self.states:
            raise BrainUnavailable("imitation model is not configured")
        key = state_key(state_features(obs.summary()))
        votes = self.states.get(key)
        if not votes:
            raise BrainUnavailable("no human demonstration for this state")
        total = sum(int(v["count"]) for v in votes)
        winner = max(votes, key=lambda v: int(v["count"]))
        confidence = int(winner["count"]) / total if total else 0.0
        if confidence < self.confidence_threshold:
            raise BrainUnavailable(f"demonstrated action confidence {confidence:.2f} is below "
                                   f"{self.confidence_threshold:.2f}")
        action = Action.from_dict({"type": "action", "v": 1,
                                   "source": "brain:imitation", "presses": winner["presses"]})
        buttons = "+".join(p.button for p in action.presses) or "wait"
        return action, Decision(brain=self.name, plan="imitate a human action",
                                reason=f"matched a demonstrated state; chose {buttons}",
                                confidence=confidence)
