"""Goal planner: an ordered milestone list; the first milestone not yet done is the current goal.

Milestones are sticky: once ``done`` is observed true it stays done. A milestone gives a
``Target`` for PathBrain:

* ``Target.warp(dest_bank, dest_map)`` -- reach any usable warp leading to that map
* ``Target.tile(x, y)``                -- reach a tile on the current map
* ``None``                             -- nothing to navigate (e.g. the intro: RuleBrain's job)

``placeholder=True`` milestones are listed (so the dashboard can show the road ahead) but are
not implemented yet; PathBrain reports itself unavailable when one becomes current.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..schema import Observation


@dataclass(frozen=True)
class Target:
    kind: str                     # "warp" | "tile"
    dest: Tuple[int, int] = (0, 0)  # warp: destination (bank, map)
    tile: Tuple[int, int] = (0, 0)  # tile: (x, y) on the current map

    @classmethod
    def warp(cls, bank: int, map_id: int) -> "Target":
        return cls("warp", dest=(bank, map_id))

    @classmethod
    def at(cls, x: int, y: int) -> "Target":
        return cls("tile", tile=(x, y))

    def describe(self) -> str:
        return f"warp to map {self.dest[0]}/{self.dest[1]}" if self.kind == "warp" else f"tile {self.tile}"


@dataclass
class Milestone:
    id: str
    label: str
    done: Callable[[Observation], bool] = lambda obs: False
    target: Callable[[Observation], Optional[Target]] = lambda obs: None
    placeholder: bool = False


def _map(obs: Observation) -> Optional[Tuple[int, int]]:
    if "map_bank" in obs.ram and "map_id" in obs.ram and obs.position is not None:
        return (obs.ram["map_bank"], obs.ram["map_id"])
    return None


# FireRed (BPRE) map ids used by milestone 1. 4/1 = player's house 2F (verified, notes/mgba-bridge.md);
# 4/0 = house 1F and 3/0 = Pallet Town are the warp destinations Backend verified on this ROM.
FR_HOUSE_2F = (4, 1)
FR_HOUSE_1F = (4, 0)
FR_PALLET_TOWN = (3, 0)


def firered_milestones() -> List[Milestone]:
    m = _map
    return [
        Milestone("intro", "Get through the intro (RuleBrain mashes A)",
                  done=lambda o: o.position is not None),
        Milestone("leave_bedroom", "Leave the bedroom (2F stairs -> 1F)",
                  done=lambda o: m(o) in (FR_HOUSE_1F, FR_PALLET_TOWN),
                  target=lambda o: Target.warp(*FR_HOUSE_1F)),
        Milestone("leave_house", "Leave the house (1F door mat -> outside)",
                  done=lambda o: m(o) == FR_PALLET_TOWN,
                  target=lambda o: Target.warp(*FR_PALLET_TOWN)),
        Milestone("pallet_town", "Stand in Pallet Town",
                  done=lambda o: m(o) == FR_PALLET_TOWN),
        # --- milestone 2: placeholders, not implemented yet ---
        Milestone("oak_lab", "Go to Prof. Oak's lab", placeholder=True),
        Milestone("get_starter", "Get a starter Pokémon", placeholder=True),
        Milestone("first_battle", "Win the first (rival) battle", placeholder=True),
    ]


class GoalPlanner:
    def __init__(self, milestones: Optional[List[Milestone]] = None):
        self.milestones = milestones if milestones is not None else firered_milestones()
        self.reset()

    def reset(self) -> None:
        self._done: Dict[str, bool] = {m.id: False for m in self.milestones}

    def update(self, obs: Observation) -> Optional[Milestone]:
        """Mark milestones done (in order) and return the current one (None = all done)."""
        for m in self.milestones:
            if self._done[m.id]:
                continue
            if not m.placeholder and m.done(obs):
                self._done[m.id] = True
                continue
            return m
        return None

    @property
    def current(self) -> Optional[Milestone]:
        return next((m for m in self.milestones if not self._done[m.id]), None)

    def summary(self) -> List[Dict[str, object]]:
        return [{"id": m.id, "label": m.label, "done": self._done[m.id]} for m in self.milestones]
