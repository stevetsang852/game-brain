"""PathBrain: walk to the current milestone's target with A*.

Per decision (one Observation in, one small Action out):

1. No player position: if we just triggered a warp, wait for the transition; otherwise raise
   ``BrainUnavailable`` (intro / menus -> the next brain, e.g. RuleBrain, mashes A).
2. New map: wait until the position is stable for two observations (doors auto-walk you out).
3. Check the previous action. A turn that did not turn us means the player is frozen
   (fade-in, script, text box): wait, pressing A only every 4th time. A step that did not
   move us means a text box/script or an NPC. Press A (advances dialogue) and retry; if the same tile
   fails twice, remember it as blocked for ``block_ttl`` decisions and replan around it.
4. Plan: A* (4-dir) to the target's stand tile. For a warp target, a usable warp's stand tile
   is the warp itself ("push": mats/stairs) or the tile behind a door ("door").
5. Emit one Action: turn first if not facing the next direction (FireRed turns on a tap
   before it walks), else one tile step. At the stand tile, press the warp's ``enter`` button.

Timings (frames) are constructor arguments; defaults follow what was measured on FireRed:
a 1-3 frame tap + >= 8 released frames turns; when already facing, a 1-16 frame hold moves
exactly one tile (16 frames per tile).

Map data comes from ``Observation.ram`` via :class:`~game_brain.nav.RamMapProvider`, so the
brain stays pure (it never touches the adapter).
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

from ..nav import RamMapProvider, astar, direction
from ..nav.map_provider import MapGrid, Warp
from ..schema import Action, ButtonPress, Decision, Observation
from .base import Brain, BrainUnavailable
from .goals import GoalPlanner

Tile = Tuple[int, int]
_DELTA = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


class PathBrain(Brain):
    name = "path"

    def __init__(self, planner: Optional[GoalPlanner] = None, map_provider: Optional[RamMapProvider] = None,
                 turn_frames: int = 2, turn_release: int = 8, step_frames: int = 8, step_release: int = 16,
                 warp_release: int = 32, settle_frames: int = 16, block_ttl: int = 12,
                 max_warp_tries: int = 3, max_transition_waits: int = 20, max_settle_waits: int = 12,
                 idle_on_placeholder: bool = True, settle_checks: int = 4):
        self.planner = planner or GoalPlanner()
        self.maps = map_provider or RamMapProvider()
        self.turn_frames, self.turn_release = turn_frames, turn_release
        self.step_frames, self.step_release = step_frames, step_release
        self.warp_release, self.settle_frames = warp_release, settle_frames
        self.block_ttl, self.max_warp_tries = block_ttl, max_warp_tries
        self.max_transition_waits, self.max_settle_waits = max_transition_waits, max_settle_waits
        self.idle_on_placeholder = idle_on_placeholder
        self.settle_checks = settle_checks
        self.reset()

    def reset(self) -> None:
        self.planner.reset()
        self._t = 0
        self._map: Optional[Tile] = None
        self._last_pos: Optional[Tile] = None
        self._last: Optional[Tuple[str, object]] = None   # (kind, detail) of our previous action
        self._blocked: Dict[Tuple[Tile, Tile], int] = {}  # ((map), tile) -> expiry decision index
        self._warp_tries: Dict[Tuple[Tile, Warp], int] = {}
        self._dead_warps: Set[Tuple[Tile, Warp]] = set()
        self._fails: Dict[Tile, int] = {}   # failed moves towards a tile since we last moved
        self._turn_fails = 0
        self._transition_waits = 0
        self._settle_waits = 0
        self._settling = False
        self._stable = 0
        self.stats = {"steps": 0, "turns": 0, "bumps": 0, "a_presses": 0, "warps": 0, "replans": 0}

    # ------------------------------------------------------------------ helpers
    @property
    def src(self) -> str:
        return f"brain:{self.name}"

    def _act(self, button: str, frames: int, release: int) -> Action:
        return Action([ButtonPress(button, frames, release)], source=self.src)

    def _dec(self, plan: str, reason: str, goal: Optional[str] = None, path=None) -> Decision:
        return Decision(brain=self.name, plan=plan, reason=reason, goal=goal,
                        path=[list(p) for p in path] if path else None,
                        milestones=self.planner.summary())

    def _blocked_tiles(self) -> Set[Tile]:
        self._blocked = {k: v for k, v in self._blocked.items() if v > self._t}
        return {tile for (m, tile) in self._blocked if m == self._map}

    def _wait(self, frames: int, kind: str) -> Action:
        self._last = (kind, None)
        return Action.wait(frames, source=self.src)

    # ------------------------------------------------------------------ decide
    def decide(self, obs: Observation):
        self._t += 1
        milestone = self.planner.update(obs)
        pos = obs.position
        pos = (int(pos[0]), int(pos[1])) if pos is not None else None

        # 1. no position: warp fade or not in the overworld
        if pos is None:
            if self._last and self._last[0] in ("warp", "transition") and \
                    self._transition_waits < self.max_transition_waits:
                self._transition_waits += 1
                return self._wait(8, "transition"), self._dec(
                    "warp transition", "no position while warping -> wait 8 frames")
            self._last = None
            raise BrainUnavailable("no player position (intro/menu/script)")
        self._transition_waits = 0

        grid = self.maps.update(obs).current_map()
        if grid is None:
            raise BrainUnavailable("observation has no map grid (map_w/map_h/collision)")

        # 2. map changed -> wait until the position stops changing (door auto-walk etc.)
        if grid.key != self._map:
            if self._map is not None:
                self.stats["warps"] += 1
            self._map, self._settling, self._settle_waits, self._stable = grid.key, True, 0, 0
            self._fails.clear()
            self._last_pos = pos
            return self._wait(self.settle_frames, "settle"), self._dec(
                "arrived on a new map", f"map {grid.key[0]}/{grid.key[1]} at {pos}: wait to settle")
        if self._settling:
            # need ``settle_checks`` consecutive observations at the same tile (doors auto-walk
            # you out ~3 waits after arrival on FireRed)
            self._stable = self._stable + 1 if pos == self._last_pos else 0
            self._last_pos = pos
            if self._stable < self.settle_checks and self._settle_waits < self.max_settle_waits:
                self._settle_waits += 1
                return self._wait(self.settle_frames, "settle"), self._dec(
                    "arrived on a new map", f"at {pos}, waiting until the position is stable")
            self._settling = False

        if milestone is None:
            raise BrainUnavailable("all milestones done")
        if milestone.placeholder:
            if not self.idle_on_placeholder:
                raise BrainUnavailable(f"milestone '{milestone.id}' not implemented yet")
            # Implemented milestones are done; stand still rather than let a fallback brain
            # wander off (e.g. back through the door).
            return self._wait(self.settle_frames, "idle"), self._dec(
                "implemented milestones done",
                f"next milestone '{milestone.id}' ({milestone.label}) is not implemented yet -> idle",
                goal=milestone.label)
        target = milestone.target(obs)
        if target is None:
            raise BrainUnavailable(f"milestone '{milestone.id}' has no navigation target")
        goal_txt = f"{milestone.label}: {target.describe()}"

        # 3. did our previous action do what we expected?
        last, self._last = self._last, None
        prev_pos, self._last_pos = self._last_pos, pos
        if last:
            kind, detail = last
            if kind == "turn" and obs.ram.get("facing") not in (None, detail[1]):
                # Could not even turn: the player is frozen (fade-in, script, text box), not blocked.
                # Wait a little; only every 4th time press A in case it is a text box.
                self._turn_fails += 1
                if self._turn_fails % 4:
                    return self._wait(8, "frozen"), self._dec(
                        goal_txt, f"could not turn {detail[1]} (player frozen?) -> wait", goal=goal_txt, path=[pos])
                self.stats["a_presses"] += 1
                self._last = ("a", None)
                return self._act("A", 2, 8), self._dec(
                    goal_txt, "still frozen after several waits -> press A (text box?)", goal=goal_txt, path=[pos])
            if kind == "turn":
                self._turn_fails = 0
            stuck = kind == "step" and pos == prev_pos
            if stuck:
                # First failure towards a tile: assume a text box / script -> press A and retry.
                # Second failure towards the same tile: assume an NPC/obstacle -> block it for a while.
                tile = detail[0]
                self.stats["bumps"] += 1
                self.stats["a_presses"] += 1
                n = self._fails[tile] = self._fails.get(tile, 0) + 1
                self._last = ("a", None)
                if n >= 2:
                    self._blocked[(grid.key, tile)] = self._t + self.block_ttl
                    why = f"no movement towards {tile} again -> treat as blocked (NPC?), press A, replan"
                else:
                    why = f"no movement towards {tile} -> press A (dialogue?) and retry"
                return self._act("A", 2, 8), self._dec(goal_txt, why, goal=goal_txt, path=[pos])
            if pos != prev_pos:
                self._fails.clear()
            if kind == "warp":
                key = detail
                self._warp_tries[key] = self._warp_tries.get(key, 0) + 1
                if self._warp_tries[key] >= self.max_warp_tries:
                    self._dead_warps.add(key)

        # 4. plan
        if target.kind == "warp":
            warps = [w for w in grid.usable_warps(target.dest) if (grid.key, w) not in self._dead_warps]
            stands = {}
            for w in warps:
                st = grid.warp_stand_tile(w)
                if st is not None and grid.is_walkable(*st):
                    stands.setdefault(st, w)
            if not stands:
                raise BrainUnavailable(f"no usable warp to {target.dest} on map {grid.key}")
            goals = set(stands)
        else:
            stands, goals = {}, {target.tile}

        if pos in stands:  # 5a. at a warp's stand tile -> trigger it
            w = stands[pos]
            self._last = ("warp", (grid.key, w))
            return self._act(w.enter, self.step_frames, self.warp_release), self._dec(
                goal_txt, f"at {pos}: press {w.enter} to take warp ({w.x},{w.y}) -> {w.dest}",
                goal=goal_txt, path=[pos])
        if target.kind == "tile" and pos == target.tile:
            raise BrainUnavailable(f"target tile {target.tile} reached")

        path = astar(grid, pos, goals, blocked=self._blocked_tiles())
        if path is None and self._blocked:
            self.stats["replans"] += 1
            path = astar(grid, pos, goals)  # maybe the "NPC" has moved: try without remembered blocks
        if path is None or len(path) < 2:
            raise BrainUnavailable(f"no path from {pos} to {sorted(goals)}")

        # 5b. one tile: turn first if needed
        nxt = path[1]
        d = direction(pos, nxt)
        facing = obs.ram.get("facing")
        if facing != d:
            self.stats["turns"] += 1
            self._last = ("turn", (nxt, d))
            return self._act(d, self.turn_frames, self.turn_release), self._dec(
                goal_txt, f"turn {d} before stepping to {nxt} ({len(path) - 1} tiles left)",
                goal=goal_txt, path=path)
        self.stats["steps"] += 1
        self._last = ("step", (nxt, d))
        return self._act(d, self.step_frames, self.step_release), self._dec(
            goal_txt, f"step {d} to {nxt} ({len(path) - 1} tiles left)", goal=goal_txt, path=path)
