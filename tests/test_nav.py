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
    m = pl.update(outside)
    assert m.id == "oak_stops_you" and m.target(outside) == Target.at(12, 1)
    # back indoors: done milestones stay done
    assert pl.update(on_2f).id == "oak_stops_you"
    trigger = Observation(frame=3, ram={"player_x": 12, "player_y": 1, "map_bank": 3, "map_id": 0})
    m = pl.update(trigger)
    assert m.id == "oak_lab" and m.target(trigger) == Target.script()
    lab = Observation(frame=4, ram={"player_x": 6, "player_y": 4, "map_bank": 4, "map_id": 3, "party_count": 0})
    m = pl.update(lab)
    assert m.id == "get_starter" and m.target(lab) == Target.interact(8, 5, "UP", "A")
    got = Observation(frame=5, ram={"player_x": 8, "player_y": 5, "map_bank": 4, "map_id": 3, "party_count": 1})
    m = pl.update(got)
    assert m.id == "rival_battle" and not m.placeholder and m.script_button == "B"
    assert m.target(got) == Target.warp(3, 0)   # walk to the lab exit; the rival stops you
    battle = Observation(frame=6, ram={"in_battle": True, "scene": "other"})
    assert pl.update(battle).id == "rival_battle_over"
    assert pl.update(Observation(frame=7, ram={"in_battle": True, "scene": "other"})).id == "rival_battle_over"
    after = Observation(frame=8, ram={"player_x": 7, "player_y": 8, "map_bank": 4, "map_id": 3,
                                      "party_count": 1, "in_battle": False})
    m = pl.update(after)
    assert m.id == "leave_lab" and m.target(after) == Target.warp(3, 0)
    # M3: Pallet Town -> (north edge) Route 1 3/19 -> (north edge) Viridian City 3/1
    pallet = Observation(frame=9, ram={"player_x": 16, "player_y": 14, "map_bank": 3, "map_id": 0, "party_count": 1})
    m = pl.update(pallet)
    assert m.id == "route_1" and m.target(pallet) == Target.edge("UP")
    route = Observation(frame=10, ram={"player_x": 13, "player_y": 39, "map_bank": 3, "map_id": 19, "party_count": 1})
    m = pl.update(route)
    assert m.id == "viridian_city" and m.target(route) == Target.edge("UP")
    # whiteout: you wake up at home; the targets lead back out (2F -> 1F -> Pallet -> north)
    for bank_map, t in (((4, 1), Target.warp(4, 0)), ((4, 0), Target.warp(3, 0)), ((3, 0), Target.edge("UP"))):
        o = Observation(frame=11, ram={"player_x": 3, "player_y": 3, "map_bank": bank_map[0],
                                       "map_id": bank_map[1], "party_count": 1})
        assert pl.update(o).id == "viridian_city" and pl.update(o).target(o) == t
    viridian = Observation(frame=12, ram={"player_x": 25, "player_y": 39, "map_bank": 3, "map_id": 1, "party_count": 1})
    m = pl.update(viridian)
    assert m.id == "oaks_parcel" and m.placeholder
    done = {m["id"]: m["done"] for m in pl.summary()}
    assert all(v for k, v in done.items() if k != "oaks_parcel") and not done["oaks_parcel"]
    assert list(done) == ["intro", "leave_bedroom", "leave_house", "pallet_town", "oak_stops_you", "oak_lab",
                          "get_starter", "rival_battle", "rival_battle_over", "leave_lab", "route_1",
                          "viridian_city", "oaks_parcel"]


def test_goal_planner_resumed_after_the_rival_battle():
    # a save state outside the lab with the starter: the rival battle is implied
    pl = GoalPlanner()
    o = Observation(frame=0, ram={"player_x": 12, "player_y": 3, "map_bank": 3, "map_id": 0, "party_count": 1})
    assert pl.update(o).id == "route_1"
    with pytest.raises(ValueError):
        Target.edge("NORTH")
    assert Target.edge("UP").describe() == "walk off the map edge (UP)"


def test_goal_planner_skips_ahead_on_later_evidence():
    # e.g. a run resumed from a save state in the lab: earlier milestones are implied
    pl = GoalPlanner()
    lab = Observation(frame=0, ram={"player_x": 6, "player_y": 4, "map_bank": 4, "map_id": 3, "party_count": 0})
    assert pl.update(lab).id == "get_starter"
    other = firered_milestones("CHARMANDER")
    assert other[6].target(lab) == Target.interact(10, 5, "UP", "A")


def test_custom_milestones():
    pl = GoalPlanner([Milestone("a", "go to (2,2)", done=lambda o: o.position == (2, 2),
                                target=lambda o: Target.at(2, 2))])
    assert pl.update(Observation(frame=0, ram={"player_x": 1, "player_y": 1})).id == "a"
    assert pl.update(Observation(frame=0, ram={"player_x": 2, "player_y": 2})) is None
    assert len(firered_milestones()) == 13


def test_firered_extra_reads_npcs_and_party_count():
    from game_brain.adapters.gba_mgba import firered as fr
    from game_brain.adapters.gba_mgba.firered_extra import G_PLAYER_PARTY_COUNT, read_extra

    mem = {}

    def put(addr, value, width):
        for i, byte in enumerate(int(value).to_bytes(width, "little", signed=value < 0)):
            mem[addr + i] = byte

    def rd(width):
        return lambda a: int.from_bytes(bytes(mem.get(a + i, 0) for i in range(width)), "little")

    ram = fr.FireRedRam(rd(1), rd(2), rd(4))
    put(fr.G_PLAYER_AVATAR + 5, 0, 1)            # player is object 0
    for i, (x, y, px, py, grp, num) in enumerate([(6, 4, 6, 4, 4, 3),     # player (skipped)
                                                  (5, 4, 5, 4, 4, 3),     # rival
                                                  (2, 10, 2, 11, 4, 3),   # walking aide
                                                  (9, 9, 9, 9, 3, 0)]):   # other map (skipped)
        b = fr.G_OBJECT_EVENTS + 0x24 * i
        put(b, 1, 1); put(b + 5, 70 + i, 1); put(b + 8, i, 1); put(b + 9, num, 1); put(b + 0xA, grp, 1)
        put(b + 0xB, 3, 1)
        put(b + 0x10, x + 7, 2); put(b + 0x12, y + 7, 2); put(b + 0x14, px + 7, 2); put(b + 0x16, py + 7, 2)
    b = fr.G_OBJECT_EVENTS + 0x24 * 4                   # inactive object (bit0 clear)
    put(b, 0, 1); put(b + 9, 3, 1); put(b + 0xA, 4, 1)
    put(G_PLAYER_PARTY_COUNT, 1, 1)
    extra = read_extra(ram, {"player_x": 6, "player_y": 4, "map_bank": 4, "map_id": 3})
    assert extra["party_count"] == 1
    assert [(n["x"], n["y"], n["prev_x"], n["prev_y"], n["local_id"]) for n in extra["npcs"]] == \
        [(5, 4, 5, 4, 1), (2, 10, 2, 11, 2)]
    assert read_extra(ram, {"scene": "other"}) == {}
