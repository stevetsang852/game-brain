"""4-directional A* on a MapGrid (deterministic tie-breaking)."""

from __future__ import annotations

import heapq
from typing import Iterable, List, Optional, Set, Tuple

from .map_provider import MapGrid

Tile = Tuple[int, int]
#: fixed expansion order => identical paths on every run (replay determinism)
DIRS = (("UP", 0, -1), ("DOWN", 0, 1), ("LEFT", -1, 0), ("RIGHT", 1, 0))


def direction(a: Tile, b: Tile) -> str:
    for name, dx, dy in DIRS:
        if (a[0] + dx, a[1] + dy) == tuple(b):
            return name
    raise ValueError(f"{a} and {b} are not 4-neighbours")


def astar(grid: MapGrid, start: Tile, goals: Iterable[Tile], blocked: Iterable[Tile] = (),
          max_nodes: int = 100_000) -> Optional[List[Tile]]:
    """Shortest path ``[start, ..., goal]`` to the nearest of ``goals``, or None.

    Walkable = ``grid.is_walkable`` and not in ``blocked`` (e.g. NPC tiles learnt by bumping).
    ``start`` itself is always allowed. Goals must be walkable to be reachable.
    """
    start = tuple(start)
    goal_set: Set[Tile] = {tuple(g) for g in goals}
    if not goal_set:
        return None
    block = {tuple(b) for b in blocked}
    if start in goal_set:
        return [start]
    walkable = grid.is_walkable
    goals = tuple(goal_set)
    if len(goals) == 1:
        gx, gy = goals[0]

        def h(t: Tile) -> int:
            return abs(t[0] - gx) + abs(t[1] - gy)
    else:
        def h(t: Tile) -> int:
            return min(abs(t[0] - g[0]) + abs(t[1] - g[1]) for g in goals)

    counter = 0
    open_heap = [(h(start), 0, counter, start)]
    came = {start: None}
    cost = {start: 0}
    while open_heap and counter < max_nodes:
        _, g, _, cur = heapq.heappop(open_heap)
        if cur in goal_set:
            path = [cur]
            while came[path[-1]] is not None:
                path.append(came[path[-1]])
            return path[::-1]
        if g > cost[cur]:
            continue
        for _, dx, dy in DIRS:
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in block or not walkable(*nxt):
                continue
            ng = g + 1
            if ng < cost.get(nxt, 1 << 30):
                cost[nxt] = ng
                came[nxt] = cur
                counter += 1
                heapq.heappush(open_heap, (ng + h(nxt), ng, counter, nxt))
    return None
