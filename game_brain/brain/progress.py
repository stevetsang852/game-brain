"""Unsupervised progress brain: the only instruction is to clear the game.

It does not receive a route. It scores observations it has not seen (new map, new tile,
party growth) and avoids repeating an action that did not change the observation.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from ..schema import Action, ButtonPress, Decision, Observation
from .base import Brain

_DELTA = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


class ProgressBrain(Brain):
    name = "progress"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._seen: Dict[tuple, int] = {}
        self._last_sig: Optional[tuple] = None
        self._stuck = 0
        self._last_button = ""
        self._repeat = 0

    def decide(self, obs: Observation) -> Tuple[Action, Decision]:
        if obs.ram.get("in_battle"):
            return self._press("A"), self._decision(
                "clear the game", "in battle: press A and let the battle brain take over if it can")
        sig = self._signature(obs)
        self._seen[sig] = self._seen.get(sig, 0) + 1
        self._stuck = self._stuck + 1 if sig == self._last_sig else 0
        self._last_sig = sig
        button = self._choose(obs, sig)
        if button == self._last_button:
            self._repeat += 1
        else:
            self._repeat = 0
        self._last_button = button
        novel = sum(1 for n in self._seen.values() if n == 1)
        return self._press(button), self._decision(
            "clear the game",
            f"unsupervised: {button}; new states {novel}; visits here {self._seen[sig]}; stuck {self._stuck}")

    def _choose(self, obs: Observation, sig: tuple) -> str:
        if self._stuck and self._stuck % 4 == 0:
            return "A" if self._last_button != "A" else "B"
        pos = obs.position
        collision = obs.ram.get("collision")
        facing = obs.ram.get("facing")
        options = []
        if pos is not None:
            x, y = int(pos[0]), int(pos[1])
            for name, (dx, dy) in _DELTA.items():
                nx, ny = x + dx, y + dy
                if self._blocked(collision, nx, ny):
                    continue
                visits = self._seen.get(self._tile_sig(obs, nx, ny), 0)
                repeat_penalty = 5 if name == self._last_button and self._stuck else 0
                options.append((visits + repeat_penalty, 0 if name == facing else 1, name))
        if not options:
            cycle = ("UP", "RIGHT", "DOWN", "LEFT", "A")
            return cycle[self._stuck % len(cycle)]
        options.sort()
        return options[0][2]

    @staticmethod
    def _blocked(collision, x: int, y: int) -> bool:
        if not collision or y < 0 or y >= len(collision):
            return False
        row = collision[y]
        if x < 0 or x >= len(row):
            return True
        cell = row[x]
        return cell in ("#", "X", 1, True)

    @staticmethod
    def _tile_sig(obs: Observation, x: int, y: int) -> tuple:
        return (obs.ram.get("map_bank"), obs.ram.get("map_id"), x, y, obs.ram.get("party_count"))

    @staticmethod
    def _signature(obs: Observation) -> tuple:
        pos = obs.position
        return (obs.ram.get("scene"), obs.ram.get("map_bank"), obs.ram.get("map_id"),
                None if pos is None else (int(pos[0]), int(pos[1])),
                obs.ram.get("facing"), obs.ram.get("party_count"), obs.ram.get("in_battle"))

    def _press(self, button: str) -> Action:
        frames = 8 if button in _DELTA else 2
        return Action([ButtonPress(button, frames, 8)], source=f"brain:{self.name}")
