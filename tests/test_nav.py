"""A*, map grid contract, RamMapProvider caching, goal planner (no ROM needed)."""

import pytest

from game_brain.brain.goals import GoalPlanner, Milestone, Target, firered_milestones
from game_brain.nav import MapGrid, RamMapProvider, Warp, astar, direction
from game_brain.schema import Observation

OPEN = ["#######",
        "#.....#",
        "#.....#",
        "#.....#",
        "#######"]


def grid(rows, warps=()):
    return MapGrid.from_rows(0, 0, rows, warps)


def test_astar_shortest_path_on_open_grid():
    p = astar(grid(OPEN), (1, 1), [(5, 3)])
    assert p[0] == (1, 1) and p[-1] == (5, 3) and len(p) == 7  # manhattan 6 -> 7 tiles
    for a, b in zip(p, p[1:]):
        direction(a, b)  # every hop is a 4-neighbour


def test_astar_detours_around_walls():
    rows = ["#######",
            "#..#..#",
            "#..#..#",
            "#.....#",
            "#######"]
    p = astar(grid(rows), (1, 1), [(5, 1)])
    assert p is not None and (3, 3) in p and all(rows[y][x] == "." for x, y in p)
    assert len(p) == 9


def test_astar_unreachable_returns_none():
    rows = ["#####",
            "#.#.#",
            "#####"]
    assert astar(grid(rows), (1, 1), [(3, 1)]) is None
    assert astar(grid(rows), (1, 1), []) is None


def test_astar_replans_around_blocked_tiles():
    g = grid(OPEN)
    p1 = astar(g, (1, 2), [(5, 2)])
    assert p1 == [(x, 2) for x in range(1, 6)]
    p2 = astar(g, (1, 2), [(5, 2)], blocked=[(3, 2)])
    assert (3, 2) not in p2 and len(p2) == 7
    assert astar(g, (1, 2), [(5, 2)], blocked=[(3, 1), (3, 2), (3, 3)]) is None


def test_astar_picks_nearest_of_several_goals_and_is_deterministic():
    g = grid(OPEN)
    p = astar(g, (1, 1), [(5, 3), (2, 1)])
    assert p[-1] == (2, 1)
    assert astar(g, (1, 1), [(5, 3)]) == astar(g, (1, 1), [(5, 3)])
    assert astar(g, (2, 2), [(2, 2)]) == [(2, 2)]


def test_warp_kinds_from_enter_and_collision():
    rows = ["####",
            "#..#",
            "####"]
    g = grid(rows, [{"x": 2, "y": 1, "dest_bank": 4, "dest_map": 0, "behavior": 0x65, "enter": "DOWN"},
                    {"x": 1, "y": 0, "dest_bank": 3, "dest_map": 0, "behavior": 0x69, "enter": "UP"},
                    {"x": 1, "y": 1, "dest_bank": 3, "dest_map": 0, "behavior": 0, "enter": None}])
    push, door, dead = g.warps
    assert g.warp_kind(push) == "push" and g.warp_stand_tile(push) == (2, 1)
    assert g.warp_kind(door) == "door" and g.warp_stand_tile(door) == (1, 1)  # walk UP into it
    assert g.warp_kind(dead) is None and g.warp_stand_tile(dead) is None
    assert g.usable_warps((3, 0)) == [door]
    assert isinstance(push, Warp) and push.dest == (4, 0)


def _obs(bank=4, mid=1, rows=OPEN, warps=(), **extra):
    ram = {"map_bank": bank, "map_id": mid, "map_w": len(rows[0]), "map_h": len(rows),
           "collision": list(rows), "warps": list(warps), "player_x": 1, "player_y": 1}
    ram.update(extra)
    return Observation(frame=0, ram=ram)


def test_ram_map_provider_reads_backend_keys_and_caches_per_map():
    mp = RamMapProvider()
    assert mp.update(Observation(frame=0, ram={"player_x": 1})).current_map() is None
    g1 = mp.update(_obs()).current_map()
    assert g1.key == (4, 1) and g1.width == 7 and g1.height == 5 and g1.is_walkable(1, 1)
    for _ in range(5):
        assert mp.update(_obs()).current_map() is g1  # same map -> cached object, no re-parse
    assert mp.parses == 1
    g2 = mp.update(_obs(4, 0)).current_map()
    assert g2.key == (4, 0) and mp.parses == 2
    assert mp.update(_obs()).current_map() is g1 and mp.parses == 2  # back on 4/1: still cached
    changed = ["#######", "#.#...#", "#.....#", "#.....#", "#######"]
    assert not mp.update(_obs(rows=changed)).current_map().is_walkable(2, 1) and mp.parses == 3


def test_ram_map_provider_rejects_inconsistent_snapshot():
    bad = _obs()
    bad.ram["map_h"] = 9
    assert RamMapProvider().update(bad).current_map() is None


def test_goal_planner_is_sticky_and_ordered():
    pl = GoalPlanner()
    assert pl.update(Observation(frame=0, ram={})).id == "intro"
    on_2f = Observation(frame=1, ram={"player_x": 6, "player_y": 6, "map_bank": 4, "map_id": 1})
    m = pl.update(on_2f)
    assert m.id == "leave_bedroom" and m.target(on_2f) == Target.warp(4, 0)
    outside = Observation(frame=2, ram={"player_x": 6, "player_y": 8, "map_bank": 3, "map_id": 0})
    assert pl.update(outside).id == "oak_lab" and pl.current.placeholder
    # back indoors: done milestones stay done
    assert pl.update(on_2f).id == "oak_lab"
    done = {m["id"]: m["done"] for m in pl.summary()}
    assert done == {"intro": True, "leave_bedroom": True, "leave_house": True, "pallet_town": True,
                    "oak_lab": False, "get_starter": False, "first_battle": False}


def test_custom_milestones():
    pl = GoalPlanner([Milestone("a", "go to (2,2)", done=lambda o: o.position == (2, 2),
                                target=lambda o: Target.at(2, 2))])
    assert pl.update(Observation(frame=0, ram={"player_x": 1, "player_y": 1})).id == "a"
    assert pl.update(Observation(frame=0, ram={"player_x": 2, "player_y": 2})) is None
    assert len(firered_milestones()) == 7
