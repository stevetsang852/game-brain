"""Map grid contract consumed by PathBrain (see notes/nav.md).

A ``MapProvider`` answers "what does the current map look like?" as a :class:`MapGrid`.
:class:`RamMapProvider` builds it straight from ``Observation.ram`` using these keys
(the format Backend's FireRed adapter emits, and that the mock house adapter mirrors):

* ``map_bank``, ``map_id`` -- current map
* ``map_w``, ``map_h``     -- size in tiles
* ``collision``            -- list of ``map_h`` strings of length ``map_w``: ``#`` blocked, ``.`` free;
  same coordinates as ``player_x`` / ``player_y``
* ``warps``                -- ``[{x, y, dest_bank, dest_map, behavior, enter}]``; ``enter`` is the
  button that triggers the warp, or ``None`` if unknown/untriggerable (such warps are ignored)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..schema import Observation

_DELTA = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


@dataclass(frozen=True)
class Warp:
    x: int
    y: int
    dest_bank: int
    dest_map: int
    enter: Optional[str] = None   # button that triggers it; None = not usable
    behavior: int = 0

    @property
    def dest(self) -> Tuple[int, int]:
        return (self.dest_bank, self.dest_map)


@dataclass
class MapGrid:
    map_bank: int
    map_id: int
    width: int
    height: int
    walkable: List[List[bool]]          # walkable[y][x]
    warps: List[Warp] = field(default_factory=list)

    @property
    def key(self) -> Tuple[int, int]:
        return (self.map_bank, self.map_id)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_walkable(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and self.walkable[y][x]

    def warp_kind(self, w: Warp) -> Optional[str]:
        """Derived from ``enter`` + the warp tile's collision:

        * ``"push"``: warp tile is walkable -- stand on it, then press ``enter`` (mats, stairs)
        * ``"door"``: warp tile is blocked -- from the tile behind it, walk ``enter`` into it
        * ``None``: ``enter`` unknown -> unusable
        """
        if w.enter not in _DELTA:
            return None
        return "push" if self.is_walkable(w.x, w.y) else "door"

    def warp_stand_tile(self, w: Warp) -> Optional[Tuple[int, int]]:
        kind = self.warp_kind(w)
        if kind == "push":
            return (w.x, w.y)
        if kind == "door":
            dx, dy = _DELTA[w.enter]
            return (w.x - dx, w.y - dy)
        return None

    def usable_warps(self, dest: Optional[Tuple[int, int]] = None) -> List[Warp]:
        return [w for w in self.warps if self.warp_kind(w) and (dest is None or w.dest == tuple(dest))]

    @classmethod
    def from_rows(cls, map_bank: int, map_id: int, rows: List[str], warps=()) -> "MapGrid":
        h = len(rows)
        w = len(rows[0]) if rows else 0
        if any(len(r) != w for r in rows):
            raise ValueError("collision rows must all have the same length")
        return cls(map_bank, map_id, w, h, [[c == "." for c in r] for r in rows],
                   [_warp(d) if isinstance(d, dict) else d for d in warps])


def _warp(d: Dict[str, Any]) -> Warp:
    enter = d.get("enter")
    return Warp(int(d["x"]), int(d["y"]), int(d["dest_bank"]), int(d["dest_map"]),
                enter.upper() if isinstance(enter, str) else None, int(d.get("behavior") or 0))


class MapProvider(ABC):
    @abstractmethod
    def current_map(self) -> Optional[MapGrid]:
        """The current map, or None if unknown right now (intro, menus, warp fade)."""


class RamMapProvider(MapProvider):
    """Reads the map from the latest Observation's ``ram`` (call :meth:`update` each step)."""

    REQUIRED = ("map_bank", "map_id", "map_w", "map_h", "collision")

    def __init__(self) -> None:
        self._ram: Dict[str, Any] = {}
        #: one parsed grid per (map_bank, map_id); re-parsed only if that map's rows/warps change
        self._cache: Dict[Tuple[int, int], Tuple[List[str], List[Dict[str, Any]], MapGrid]] = {}
        self.parses = 0  # how many times a grid was actually (re)built -- for tests / profiling

    def update(self, obs: Observation) -> "RamMapProvider":
        self._ram = obs.ram
        return self

    def current_map(self) -> Optional[MapGrid]:
        ram = self._ram
        if not all(k in ram for k in self.REQUIRED):
            return None
        rows = list(ram["collision"])
        if len(rows) != ram["map_h"] or any(len(r) != ram["map_w"] for r in rows):
            return None  # inconsistent snapshot (e.g. mid-transition) -> treat as unknown
        key = (int(ram["map_bank"]), int(ram["map_id"]))
        warps = list(ram.get("warps", []))
        hit = self._cache.get(key)
        if hit and hit[0] == rows and hit[1] == warps:
            return hit[2]
        grid = MapGrid.from_rows(key[0], key[1], rows, warps)
        self.parses += 1
        self._cache[key] = (rows, warps, grid)
        return grid
