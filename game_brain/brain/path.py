"""PathBrain: walk to the current milestone's target with A*.

Per decision (one Observation in, one small Action out):

1. No player position: if we just triggered a warp, wait for the transition; otherwise raise
   ``BrainUnavailable`` (intro / menus -> the next brain, e.g. RuleBrain, mashes A).
2. New map: wait until the position is stable for ``settle_checks`` observations (doors auto-walk you out).
3. Check the previous action. A turn that did not turn us means the player is frozen
   (fade-in, script, text box): wait, pressing the milestone's ``script_button`` every
   ``frozen_press_every``-th time. A step that did not move us means a text box/script or an
   NPC. Press ``script_button`` (advances dialogue) and retry; if the same tile
   fails twice, remember it as blocked for ``block_ttl`` decisions and replan around it.
4. Plan: A* (4-dir) to the target's stand tile. For a warp target, a usable warp's stand tile
   is the warp itself ("push": mats/stairs) or the tile behind a door ("door").
5. Emit one Action: turn first if not facing the next direction (FireRed turns on a tap
   before it walks), else one tile step. At the stand tile, press the warp's ``enter`` button.

Timings (frames) are constructor arguments; defaults follow what was measured on FireRed:
a 1-3 frame tap + >= 8 released frames turns; when already facing, a 1-16 frame hold moves
exactly one tile (16 frames per tile).

Milestone 2 additions:

* NPCs: tiles in ``ram["npcs"]`` (current and previous tile of each object) are blocked in A*
  *before* we bump into them. Bump -> A -> block -> replan stays as the fallback (NPCs the
  observation doesn't list, or ones that moved).
* ``Target.script()``: a cutscene moves the player; press the milestone's ``script_button``
  every decision (advances text) for at most ``max_script_decisions``, then give up
  (``BrainUnavailable`` -> fallback brain).
* ``Target.interact(x, y, face, button)``: walk to (x, y), turn to ``face``, press ``button``
  repeatedly (talk / pick a ball, then advance its text; YES is the default answer) until the
  milestone is done, at most ``max_interact_presses`` times.
* Frozen player (turn did not take): wait, pressing ``script_button`` every
  ``frozen_press_every``-th time; after ``max_frozen`` consecutive frozen decisions -> give up.
* Placeholder milestone (not implemented): finish any open dialogue first (probe with a
  horizontal turn; if it doesn't take, press the milestone's ``script_button``, e.g. B to answer
  NO to the nickname prompt), then idle.
* Every Decision -- and every ``BrainUnavailable`` (via ``context``, copied by the arbiter onto
  the fallback brain's Decision) -- carries the full ``milestones`` list.

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
                 idle_on_placeholder: bool = True, settle_checks: int = 4,
                 frozen_press_every: int = 2, max_frozen: int = 400, max_script_decisions: int = 600,
                 max_interact_presses: int = 200, max_placeholder_presses: int = 200,
                 avoid_npcs: bool = True):
        self.planner = planner or GoalPlanner()
        self.maps = map_provider or RamMapProvider()
        self.turn_frames, self.turn_release = turn_frames, turn_release
        self.step_frames, self.step_release = step_frames, step_release
        self.warp_release, self.settle_frames = warp_release, settle_frames
        self.block_ttl, self.max_warp_tries = block_ttl, max_warp_tries
        self.max_transition_waits, self.max_settle_waits = max_transition_waits, max_settle_waits
        self.idle_on_placeholder = idle_on_placeholder
        self.settle_checks = settle_checks
        self.frozen_press_every, self.max_frozen = frozen_press_every, max_frozen
        self.max_script_decisions, self.max_interact_presses = max_script_decisions, max_interact_presses
        self.max_placeholder_presses = max_placeholder_presses
        self.avoid_npcs = avoid_npcs
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
        self._edge_tries: Dict[Tuple[Tile, Tile], int] = {}  # (map, edge tile) -> presses that didn't leave
        self._dead_edges: Set[Tuple[Tile, Tile]] = set()
        self._fails: Dict[Tile, int] = {}   # failed moves towards a tile since we last moved
        self._turn_fails = 0
        self._transition_waits = 0
        self._settle_waits = 0
        self._settling = False
        self._stable = 0
        self._script_n: Dict[str, int] = {}    # milestone id -> script/interact/placeholder presses
        self._free: Set[str] = set()           # placeholder milestones whose dialogue is finished
        self.stats = {"steps": 0, "turns": 0, "bumps": 0, "a_presses": 0, "warps": 0, "replans": 0,
                      "script_presses": 0, "frozen": 0, "npc_blocked_tiles": 0}

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

    @staticmethod
    def _edge_tiles(grid, direction: str) -> Set[Tile]:
        w, h = grid.width, grid.height
        line = {"UP": [(x, 0) for x in range(w)], "DOWN": [(x, h - 1) for x in range(w)],
                "LEFT": [(0, y) for y in range(h)], "RIGHT": [(w - 1, y) for y in range(h)]}[direction]
        return {t for t in line if grid.is_walkable(*t)}

    def _blocked_tiles(self) -> Set[Tile]:
        self._blocked = {k: v for k, v in self._blocked.items() if v > self._t}
        return {tile for (m, tile) in self._blocked if m == self._map}

    def observe(self, obs: Observation) -> dict:
        """Called by the arbiter when another brain acted first (e.g. the battle brain): keep the
        milestones up to date and return them as decision context."""
        milestone = self.planner.update(obs)
        ctx = {"milestones": self.planner.summary()}
        if milestone is not None:
            ctx["goal"] = milestone.label
        return ctx

    def _unavailable(self, msg: str, milestone=None) -> BrainUnavailable:
        ctx = {"milestones": self.planner.summary()}
        if milestone is not None:
            ctx["goal"] = milestone.label
        return BrainUnavailable(msg, context=ctx)

    @staticmethod
    def _npc_tiles(obs: Observation) -> Set[Tile]:
        tiles: Set[Tile] = set()
        for n in obs.ram.get("npcs") or ():
            try:
                tiles.add((int(n["x"]), int(n["y"])))
                if "prev_x" in n and "prev_y" in n:
                    tiles.add((int(n["prev_x"]), int(n["prev_y"])))
            except (KeyError, TypeError, ValueError):
                continue
        return tiles

    def _press(self, button: str, kind: str, milestone) -> Action:
        self._script_n[milestone.id] = self._script_n.get(milestone.id, 0) + 1
        self.stats["script_presses"] += 1
        if button == "A":
            self.stats["a_presses"] += 1
        self._last = (kind, None)
        return self._act(button, 2, 14)

    def _placeholder(self, obs: Observation, milestone, pos: Tile):
        """Not-implemented milestone: finish any open dialogue (probe turn / script_button), then idle."""
        last, self._last = self._last, None
        facing = obs.ram.get("facing")
        mid = milestone.id
        if last and last[0] == "probe":
            if facing == last[1]:
                self._free.add(mid)
            elif self._script_n.get(mid, 0) < self.max_placeholder_presses:
                b = milestone.script_button
                return self._press(b, "placeholder_press", milestone), self._dec(
                    "finish dialogue", f"could not turn {last[1]} (text box open) -> press {b}",
                    goal=milestone.label, path=[pos])
        if mid in self._free or self._script_n.get(mid, 0) >= self.max_placeholder_presses:
            self._free.add(mid)
            return self._wait(8, "placeholder"), self._dec(
                "idle", f"milestone '{milestone.id}' not implemented yet -> idle",
                goal=milestone.label, path=[pos])
        d = "RIGHT" if facing == "LEFT" else "LEFT"   # horizontal: never moves a YES/NO cursor
        self._last = ("probe", d)
        return self._act(d, self.turn_frames, self.turn_release), self._dec(
            "finish dialogue", f"probe: tap {d} to check whether the player can move", goal=milestone.label,
            path=[pos])

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
            if self._last and self._last[0] in ("warp", "transition", "script") and \
                    self._transition_waits < self.max_transition_waits:
                self._transition_waits += 1
                return self._wait(8, "transition"), self._dec(
                    "warp transition", "no position while warping -> wait 8 frames")
            self._last = None
            raise self._unavailable("no player position (intro/menu/script)", milestone)
        self._transition_waits = 0

        grid = self.maps.update(obs).current_map()
        if grid is None:
            raise self._unavailable("observation has no map grid (map_w/map_h/collision)", milestone)

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
            raise self._unavailable("all milestones done")
        if milestone.placeholder:
            if not self.idle_on_placeholder:
                raise self._unavailable(f"milestone '{milestone.id}' not implemented yet", milestone)
            # Implemented milestones are done; finish the open dialogue, then stand still rather
            # than let a fallback brain wander off (e.g. back through the door).
            return self._placeholder(obs, milestone, pos)
        target = milestone.target(obs)
        if target is None:
            raise self._unavailable(f"milestone '{milestone.id}' has no navigation target", milestone)
        goal_txt = f"{milestone.label}: {target.describe()}"
        sb = milestone.script_button

        # scripted event: the game moves us; advance text, don't navigate
        if target.kind == "script":
            self._last_pos = pos
            if self._script_n.get(milestone.id, 0) >= self.max_script_decisions:
                raise self._unavailable(f"script '{milestone.id}' still running after "
                                        f"{self.max_script_decisions} presses -> give up", milestone)
            return self._press(sb, "script", milestone), self._dec(
                goal_txt, f"scripted event at {pos} -> press {sb} to advance text", goal=goal_txt, path=[pos])

        # 3. did our previous action do what we expected?
        last, self._last = self._last, None
        prev_pos, self._last_pos = self._last_pos, pos
        if last:
            kind, detail = last
            if kind == "turn" and obs.ram.get("facing") not in (None, detail[1]):
                # Could not even turn: the player is frozen (fade-in, script, text box), not blocked.
                # Wait a little; only every 4th time press A in case it is a text box.
                self._turn_fails += 1
                self.stats["frozen"] += 1
                if self._turn_fails > self.max_frozen:
                    raise self._unavailable(f"player frozen for {self.max_frozen} decisions -> give up",
                                            milestone)
                if self._turn_fails % self.frozen_press_every:
                    return self._wait(8, "frozen"), self._dec(
                        goal_txt, f"could not turn {detail[1]} (player frozen: script/text box) -> wait",
                        goal=goal_txt, path=[pos])
                if sb == "A":
                    self.stats["a_presses"] += 1
                self._last = ("a", None)
                return self._act(sb, 2, 8), self._dec(
                    goal_txt, f"still frozen (script/text box) -> press {sb} to advance", goal=goal_txt,
                    path=[pos])
            if kind in ("turn", "step") and (kind == "turn" or pos != prev_pos):
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
                    why = f"no movement towards {tile} again -> treat as blocked (NPC?), press {sb}, replan"
                else:
                    why = f"no movement towards {tile} -> press {sb} (dialogue?) and retry"
                return self._act(sb, 2, 8), self._dec(goal_txt, why, goal=goal_txt, path=[pos])
            if pos != prev_pos:
                self._fails.clear()
            if kind == "edge" and detail[0] == grid.key and pos == detail[1]:
                # pressed off the edge but still on the same map: a text box, an NPC, or no
                # connection there. After max_warp_tries, stop using that edge tile.
                key = (detail[0], detail[1])
                self._edge_tries[key] = self._edge_tries.get(key, 0) + 1
                if self._edge_tries[key] >= self.max_warp_tries:
                    self._dead_edges.add(key)
            if kind == "warp" and detail[0] == grid.key and pos == prev_pos and \
                    obs.ram.get("facing") not in (None, detail[1].enter):
                # Pressed the warp's direction but could not even turn that way: the player is
                # frozen (e.g. the Viridian Mart clerk's script right after arriving), not a dead
                # warp. Don't count the try; wait / press the script button like other freezes.
                self._turn_fails += 1
                self.stats["frozen"] += 1
                if self._turn_fails > self.max_frozen:
                    raise self._unavailable(f"player frozen for {self.max_frozen} decisions -> give up",
                                            milestone)
                if self._turn_fails % self.frozen_press_every:
                    return self._wait(8, "frozen"), self._dec(
                        goal_txt, f"could not turn {detail[1].enter} onto the warp (player frozen: "
                        "script/text box) -> wait", goal=goal_txt, path=[pos])
                self._last = ("a", None)
                return self._act(sb, 2, 8), self._dec(
                    goal_txt, f"still frozen on the warp tile (script/text box) -> press {sb} to advance",
                    goal=goal_txt, path=[pos])
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
                raise self._unavailable(f"no usable warp to {target.dest} on map {grid.key}", milestone)
            goals = set(stands)
        elif target.kind == "edge":
            stands = {}
            goals = {t for t in self._edge_tiles(grid, target.face) if (grid.key, t) not in self._dead_edges}
            if not goals:
                raise self._unavailable(f"no walkable tile on the {target.face} edge of map {grid.key}", milestone)
        else:
            stands, goals = {}, {target.tile}

        if pos in stands:  # 5a. at a warp's stand tile -> trigger it
            w = stands[pos]
            self._last = ("warp", (grid.key, w))
            return self._act(w.enter, self.step_frames, self.warp_release), self._dec(
                goal_txt, f"at {pos}: press {w.enter} to take warp ({w.x},{w.y}) -> {w.dest}",
                goal=goal_txt, path=[pos])
        if target.kind == "edge" and pos in goals:   # 5d. on the edge: face out, step off the map
            d = target.face
            if obs.ram.get("facing") != d:
                self.stats["turns"] += 1
                self._last = ("turn", (pos, d))
                return self._act(d, self.turn_frames, self.turn_release), self._dec(
                    goal_txt, f"at {pos} on the {d} edge: turn {d}", goal=goal_txt, path=[pos])
            self._last = ("edge", (grid.key, pos, d))
            return self._act(d, self.step_frames, self.step_release), self._dec(
                goal_txt, f"at {pos}: step {d} off the map edge (map connection)", goal=goal_txt, path=[pos])
        if target.kind == "tile" and pos == target.tile:
            raise self._unavailable(f"target tile {target.tile} reached", milestone)
        if target.kind == "interact" and pos == target.tile:   # 5c. face it, press the button
            facing = obs.ram.get("facing")
            if facing != target.face:
                self.stats["turns"] += 1
                self._last = ("turn", (pos, target.face))
                return self._act(target.face, self.turn_frames, self.turn_release), self._dec(
                    goal_txt, f"at {pos}: turn {target.face} to face the target", goal=goal_txt, path=[pos])
            if self._script_n.get(milestone.id, 0) >= self.max_interact_presses:
                raise self._unavailable(f"pressed {target.button} {self.max_interact_presses} times at "
                                        f"{pos} without finishing '{milestone.id}' -> give up", milestone)
            return self._press(target.button, "interact", milestone), self._dec(
                goal_txt, f"at {pos} facing {target.face}: press {target.button} (interact / advance text)",
                goal=goal_txt, path=[pos])

        npcs = self._npc_tiles(obs) if self.avoid_npcs else set()
        npcs.discard(pos)
        remembered = self._blocked_tiles()
        path = astar(grid, pos, goals, blocked=remembered | npcs)
        if path is None and remembered:
            self.stats["replans"] += 1
            path = astar(grid, pos, goals, blocked=npcs)  # the "NPC" may have moved: forget bumps
        if path is None and npcs:
            path = astar(grid, pos, goals)  # an NPC stands in the only way: walk up and bump
        if path is None or len(path) < 2:
            raise self._unavailable(f"no path from {pos} to {sorted(goals)}", milestone)
        if npcs:
            self.stats["npc_blocked_tiles"] = len(npcs)

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
