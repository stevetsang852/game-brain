"""Propose the next story probe from new observations. It does not write a verified script.

A new map or NPC becomes an unverified goal. PathBrain still owns known milestones.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..schema import Action, Decision, Observation
from .base import Brain, BrainUnavailable


class ProbeBrain(Brain):
    name = "probe"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.seen_maps = set()
        self.seen_npcs = set()
        self.goals: List[Dict[str, str]] = []

    def observe(self, obs: Observation) -> dict:
        self._note(obs)
        return {"probe_goals": list(self.goals)}

    def decide(self, obs: Observation):
        added = self._note(obs)
        if not self.goals:
            raise BrainUnavailable("no new map or NPC to probe")
        goal = self.goals[-1]
        reason = "new probe" if added else "continue unverified probe"
        return Action.wait(8, source="brain:probe"), Decision(
            brain=self.name, plan="propose next story probe", reason=f"{reason}: {goal['label']}", goal=goal["label"])

    def _note(self, obs: Observation) -> bool:
        ram = obs.ram
        added = False
        bank, map_id = ram.get("map_bank"), ram.get("map_id")
        if bank is not None and map_id is not None:
            key = (int(bank), int(map_id))
            if key not in self.seen_maps:
                self.seen_maps.add(key)
                if len(self.seen_maps) > 1:
                    self.goals.append({"id": f"map-{key[0]}-{key[1]}", "status": "unverified",
                                       "label": f"Probe new map {key[0]}/{key[1]}"})
                    added = True
        for npc in ram.get("npcs") or ():
            local = npc.get("local_id")
            if local is None:
                continue
            npc_key = (bank, map_id, local)
            if npc_key in self.seen_npcs:
                continue
            self.seen_npcs.add(npc_key)
            self.goals.append({"id": f"npc-{bank}-{map_id}-{local}", "status": "unverified",
                               "label": f"Probe NPC {local} on map {bank}/{map_id}"})
            added = True
        return added
