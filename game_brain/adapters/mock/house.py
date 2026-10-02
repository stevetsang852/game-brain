"""MockHouseAdapter: a small synthetic, FireRed-shaped world for navigation tests (no ROM).

Three maps with the same ids as FireRed milestone 1 (layouts are made up, not game data):

* 4/1 "bedroom":  stairs warp you stand on and push LEFT -> 4/0
* 4/0 "1F":       an NPC ("mom") standing on a tile the collision grid shows as free
                  (PathBrain must bump, replan); a one-time dialogue lock when you first enter
                  row 6 (needs 3 A presses); a door mat pushed DOWN -> 3/0, and a decoy warp
                  with ``enter: None`` (listed but untriggerable, like FireRed's 1F (5,8))
* 3/0 "outside":  a door (blocked tile, walk UP into it) -> back to 4/0; arriving from the house
                  you appear on the door tile and auto-walk one tile down (like FireRed)

Rules mimic FireRed closely enough for PathBrain: pressing a direction you are not facing
only turns you (any hold length); when facing it, a 1-16 frame hold moves one tile and 17+
frames two; warps black out the position for ``warp_frames`` frames.
It emits the same ``Observation.ram`` keys as the real adapter (see notes/nav.md).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..base import Adapter
from ...schema import Action, Observation

_D = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}

MAPS: Dict[Tuple[int, int], dict] = {
    (4, 1): {
        "rows": ["##########",
                 "#....#...#",
                 "#.##.#...#",
                 "#.##.....#",
                 "#........#",
                 "#..#.....#",
                 "#........#",
                 "##########"],
        "warps": [{"x": 8, "y": 1, "dest": (4, 0), "enter": "LEFT", "arrive": (9, 2)}],
    },
    (4, 0): {
        "rows": ["############",
                 "#..........#",
                 "#.####.....#",
                 "#.####.....#",
                 "#..........#",
                 "#..........#",
                 "#..........#",
                 "#..........#",
                 "############"],
        "warps": [{"x": 9, "y": 2, "dest": (4, 1), "enter": "RIGHT", "arrive": (7, 1)},
                  {"x": 4, "y": 7, "dest": (3, 0), "enter": "DOWN", "arrive": (5, 4)},
                  {"x": 5, "y": 7, "dest": (3, 0), "enter": None, "arrive": (5, 4)}],
        "npcs": [(9, 4)],          # on a '.' tile in the collision grid: only found by bumping into it
        "dialogue_row": (6, 3),    # first time the player enters row 6: locked until 3 A presses
    },
    (3, 0): {
        "rows": ["##############",
                 "#............#",
                 "#..#######...#",
                 "#..#######...#",
                 "#..#######...#",
                 "#............#",
                 "#............#",
                 "##############"],
        # door tile (5,4) is '#'; walk UP into it from (5,5)
        "warps": [{"x": 5, "y": 4, "dest": (4, 0), "enter": "UP", "arrive": (4, 6), "door": True}],
    },
}


class MockHouseAdapter(Adapter):
    name = "mock-house"

    def __init__(self, intro_presses: int = 6, start_map: Tuple[int, int] = (4, 1),
                 start_pos: Tuple[int, int] = (4, 4), warp_frames: int = 24):
        self.intro_presses = intro_presses
        self.start_map, self.start_pos = start_map, start_pos
        self.warp_frames = warp_frames
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> Observation:
        self._frame = 0
        self.intro_left = self.intro_presses
        self.map = self.start_map
        self.x, self.y = self.start_pos
        self.facing = "UP"
        self.transition = 0            # frames of warp blackout left
        self.pending: Optional[tuple] = None  # (map, (x, y), auto_walk_dir) applied after blackout
        self.lock = 0                  # A presses needed to close a dialogue
        self.dialogues_seen = set()
        self.auto_walk: Optional[str] = None
        self.warps_taken: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        return self.observe()

    @property
    def frame(self) -> int:
        return self._frame

    def _m(self) -> dict:
        return MAPS[self.map]

    def _free(self, x: int, y: int) -> bool:
        rows = self._m()["rows"]
        if not (0 <= y < len(rows) and 0 <= x < len(rows[0])):
            return False
        return rows[y][x] == "." and (x, y) not in self._m().get("npcs", [])

    # ------------------------------------------------------------------ Adapter API
    def observe(self) -> Observation:
        if self.intro_left > 0 or self.transition > 0:
            return Observation(frame=self._frame, game="MOCK-HOUSE",
                               ram={"scene": "intro" if self.intro_left else "transition", "in_battle": False})
        m = self._m()
        warps = [{"x": w["x"], "y": w["y"], "dest_bank": w["dest"][0], "dest_map": w["dest"][1],
                  "behavior": 0, "enter": w["enter"]} for w in m["warps"]]
        ram = {"scene": "overworld", "in_battle": False, "map_bank": self.map[0], "map_id": self.map[1],
               "player_x": self.x, "player_y": self.y, "facing": self.facing,
               "map_w": len(m["rows"][0]), "map_h": len(m["rows"]), "collision": list(m["rows"]),
               "warps": warps}
        return Observation(frame=self._frame, game="MOCK-HOUSE", ram=ram)

    def act(self, action: Action) -> int:
        start = self._frame
        for p in action.presses:
            self._press(p.button, p.frames)
            self._advance(p.total_frames)
        return self._frame - start

    # ------------------------------------------------------------------ rules
    def _advance(self, n: int) -> None:
        for _ in range(n):
            self._frame += 1
            if self.transition > 0:
                self.transition -= 1
                if self.transition == 0 and self.pending:
                    self.map, (self.x, self.y), self.auto_walk = self.pending
                    self.pending = None
                    if self.auto_walk:  # doors: you appear on the door tile, then walk out
                        dx, dy = _D[self.auto_walk]
                        self.y += dy
                        self.x += dx
                        self.auto_walk = None

    def _warp_to(self, w: dict) -> None:
        self.warps_taken.append((self.map, w["dest"]))
        dest = MAPS[w["dest"]]
        door = next((d for d in dest["warps"] if d.get("door") and d["dest"] == self.map), None)
        if door:  # arriving through a door: appear on it, auto-walk away from it
            arrive, walk = (door["x"], door["y"]), "DOWN"
        else:
            arrive, walk = w["arrive"], None
        self.pending = (w["dest"], arrive, walk)
        self.transition = self.warp_frames

    def _press(self, button: str, frames: int) -> None:
        if self.intro_left > 0:
            if button == "A":
                self.intro_left -= 1
            return
        if self.transition > 0:
            return
        if self.lock > 0:
            if button == "A":
                self.lock -= 1
            return
        if button not in _D:
            return
        m = self._m()
        # push-warps trigger on the tile regardless of facing
        for w in m["warps"]:
            if w["enter"] == button and not w.get("door") and (w["x"], w["y"]) == (self.x, self.y):
                self.facing = button
                self._warp_to(w)
                return
        if self.facing != button:
            self.facing = button  # FireRed: a new direction only turns you
            return
        tiles = 2 if frames >= 17 else 1
        for _ in range(tiles):
            dx, dy = _D[button]
            nx, ny = self.x + dx, self.y + dy
            door = next((w for w in m["warps"] if w.get("door") and w["enter"] == button
                         and (w["x"], w["y"]) == (nx, ny)), None)
            if door:
                self._warp_to(door)
                return
            if not self._free(nx, ny):
                return
            self.x, self.y = nx, ny
            row = m.get("dialogue_row")
            if row and ny == row[0] and self.map not in self.dialogues_seen:
                self.dialogues_seen.add(self.map)
                self.lock = row[1]
                return
