"""PathBrain on synthetic observations and on MockHouseAdapter (no ROM needed)."""

import os

import pytest

from game_brain import demo
from game_brain.adapters.mock import MockHouseAdapter
from game_brain.arbiter import Arbiter
from game_brain.brain import BrainUnavailable, GoalPlanner, Milestone, PathBrain, RuleBrain, Target
from game_brain.runlog import iter_steps, replay
from game_brain.schema import Observation

ROWS = ["#######",
        "#.....#",
        "#.....#",
        "#######"]


def obs(x, y, facing="UP", rows=ROWS, warps=(), bank=9, mid=9):
    return Observation(frame=0, ram={"player_x": x, "player_y": y, "facing": facing, "map_bank": bank,
                                     "map_id": mid, "map_w": len(rows[0]), "map_h": len(rows),
                                     "collision": list(rows), "warps": list(warps)})


def tile_brain(tx, ty, **kw):
    pl = GoalPlanner([Milestone("t", f"go to ({tx},{ty})", done=lambda o: o.position == (tx, ty),
                                target=lambda o: Target.at(tx, ty))])
    return PathBrain(planner=pl, settle_checks=0, **kw)


def settle(b, o):
    act, dec = b.decide(o)  # first observation of a map: settle wait
    assert [p.button for p in act.presses] == ["NONE"]


def test_turns_before_moving_then_steps_one_tile():
    b = tile_brain(4, 1)
    settle(b, obs(1, 1, "UP"))
    act, dec = b.decide(obs(1, 1, "UP"))
    (p,) = act.presses
    assert p.button == "RIGHT" and p.frames <= 3 and "turn" in dec.reason  # tap = turn only
    act, dec = b.decide(obs(1, 1, "RIGHT"))
    (p,) = act.presses
    assert p.button == "RIGHT" and 1 <= p.frames <= 16 and "step" in dec.reason  # one tile
    assert dec.path[0] == [1, 1] and dec.path[-1] == [4, 1] and dec.goal
    assert [m["id"] for m in dec.milestones] == ["t"]


def test_bump_presses_a_then_blocks_tile_and_replans():
    b = tile_brain(4, 1)
    settle(b, obs(1, 1, "RIGHT"))
    assert b.decide(obs(1, 1, "RIGHT"))[0].presses[0].button == "RIGHT"
    act, dec = b.decide(obs(1, 1, "RIGHT"))      # did not move: first failure -> A, retry
    assert act.presses[0].button == "A" and "retry" in dec.reason
    assert b.decide(obs(1, 1, "RIGHT"))[0].presses[0].button == "RIGHT"
    act, dec = b.decide(obs(1, 1, "RIGHT"))      # second failure -> blocked
    assert act.presses[0].button == "A" and "blocked" in dec.reason
    act, dec = b.decide(obs(1, 1, "RIGHT"))      # replan around (2,1) via row 2
    assert [2, 1] not in dec.path and dec.path[1] == [1, 2]


def test_frozen_player_waits_instead_of_blocking():
    b = tile_brain(4, 1)
    settle(b, obs(1, 1, "UP"))
    b.decide(obs(1, 1, "UP"))                     # turn RIGHT
    act, dec = b.decide(obs(1, 1, "UP"))          # still facing UP -> frozen, wait
    assert act.presses[0].button == "NONE" and "frozen" in dec.reason
    assert not b._blocked


def test_unavailable_cases():
    b = tile_brain(4, 1)
    with pytest.raises(BrainUnavailable):
        b.decide(Observation(frame=0, ram={}))            # intro: no position
    with pytest.raises(BrainUnavailable):
        b.decide(Observation(frame=0, ram={"player_x": 1, "player_y": 1}))  # no map data
    walled = ["#######", "#.#...#", "#.#...#", "#######"]
    settle(b, obs(1, 1, rows=walled))
    with pytest.raises(BrainUnavailable, match="no path"):
        b.decide(obs(1, 1, rows=walled))


def test_warp_push_and_untriggerable_warps_are_skipped():
    warps = [{"x": 5, "y": 1, "dest_bank": 4, "dest_map": 0, "behavior": 0, "enter": None},
             {"x": 1, "y": 2, "dest_bank": 4, "dest_map": 0, "behavior": 0x65, "enter": "DOWN"}]
    pl = GoalPlanner([Milestone("w", "warp", target=lambda o: Target.warp(4, 0))])
    b = PathBrain(planner=pl, settle_checks=0)
    settle(b, obs(4, 1, warps=warps))
    act, dec = b.decide(obs(4, 1, "LEFT", warps=warps))
    assert dec.path[-1] == [1, 2]  # went for the usable warp, not the closer enter=None one
    act, dec = b.decide(obs(1, 2, "DOWN", warps=warps))
    assert act.presses[0].button == "DOWN" and "take warp" in dec.reason


def test_frozen_on_the_warp_tile_is_not_a_dead_warp():
    """Viridian Mart: you arrive on the exit warp and the clerk's script starts at once; pressing
    the warp direction does not even turn you. That is a freeze (wait / A), not a failed warp try."""
    warps = [{"x": 2, "y": 2, "dest_bank": 3, "dest_map": 1, "behavior": 0x65, "enter": "DOWN"}]
    pl = GoalPlanner([Milestone("w", "leave", target=lambda o: Target.warp(3, 1))])
    b = PathBrain(planner=pl, settle_checks=0, max_warp_tries=2)
    settle(b, obs(2, 2, "UP", warps=warps))
    buttons = []
    for _ in range(8):                                  # frozen facing UP: never a dead warp
        act, dec = b.decide(obs(2, 2, "UP", warps=warps))
        buttons.append(act.presses[0].button)
        assert "no usable" not in dec.reason and "dead" not in dec.reason
    assert buttons[0] == "DOWN" and "A" in buttons and "NONE" in buttons
    assert buttons.count("DOWN") == 4                   # retried after every wait / A
    act, dec = b.decide(obs(2, 2, "DOWN", warps=warps))  # script over: free again
    assert act.presses[0].button == "DOWN" and "take warp" in dec.reason


def test_placeholder_milestone_free_explores():
    """Goal 17 (placeholder): outside battle, walk toward less-visited tiles instead of idling
    (PathBrain._free_explore, restored from 1252fcc)."""
    pl = GoalPlanner([Milestone("later", "not yet", placeholder=True, script_button="B")])
    b = PathBrain(planner=pl, settle_checks=0)
    settle(b, obs(1, 1, "UP"))
    act, dec = b.decide(obs(1, 1, "UP"))
    assert dec.plan == "free explore" and act.presses[0].button in ("DOWN", "RIGHT")  # UP/LEFT are walls
    assert "turn" in dec.reason and act.presses[0].frames <= 3
    d = act.presses[0].button
    act, dec = b.decide(obs(1, 1, d))                                       # facing it now: step
    assert act.presses[0].button == d and "walk" in dec.reason and act.presses[0].frames > 3
    assert dec.milestones == [{"id": "later", "label": "not yet", "done": False}]


def _explorer():
    return PathBrain(planner=GoalPlanner([Milestone("later", "not yet", placeholder=True)]), settle_checks=0)


def test_free_explore_takes_a_door_mat_warp():
    """New (not in 1252fcc): on a mat warp whose ``enter`` points into a wall (Oak's lab door,
    (6,12) + DOWN), pressing ``enter`` counts as a move, so free explore can leave the building."""
    rows = ["###",
            "#.#",
            "#.#",
            "###"]
    mat = {"x": 1, "y": 2, "dest_bank": 3, "dest_map": 0, "enter": "DOWN", "behavior": 101}
    b = _explorer()
    settle(b, obs(1, 1, "DOWN", rows=rows, warps=[mat]))
    act, dec = b.decide(obs(1, 1, "DOWN", rows=rows, warps=[mat]))
    assert act.presses[0].button == "DOWN" and "walk DOWN" in dec.reason      # only way: onto the mat
    act, dec = b.decide(obs(1, 2, "DOWN", rows=rows, warps=[mat]))
    assert act.presses[0].button == "DOWN" and "take warp (1,2) -> (3, 0)" in dec.reason
    act, dec = b.decide(Observation(frame=0, ram={"facing": "DOWN"}))          # fade: no position
    assert act.presses[0].button == "NONE" and dec.plan == "warp transition"


def test_free_explore_takes_a_door_from_the_tile_below():
    rows = ["#####",                                       # (2,0) is the door: blocked, enter UP
            "#...#",
            "#####"]
    door = {"x": 2, "y": 0, "dest_bank": 4, "dest_map": 3, "enter": "UP", "behavior": 0}
    b = _explorer()
    settle(b, obs(2, 1, "UP", rows=rows, warps=[door]))
    b._visits.update({((9, 9), 1, 1): 3, ((9, 9), 3, 1): 3})   # both side tiles already explored
    act, dec = b.decide(obs(2, 1, "UP", rows=rows, warps=[door]))
    assert act.presses[0].button == "UP" and "take warp (2,0) -> (4, 3)" in dec.reason


def test_free_explore_does_not_walk_into_an_unlisted_obstacle_forever():
    """New: a step that didn't move us marks the bumped tile visited (Pallet Town (7,19) looks
    walkable in the collision map but blocks), so another direction is tried."""
    rows = ["#######",
            "#.....#",
            "#.....#",
            "#######"]
    b = _explorer()
    settle(b, obs(3, 1, "RIGHT", rows=rows))
    facing, pressed = "RIGHT", []
    for _ in range(12):                                   # the position never changes
        act, dec = b.decide(obs(3, 1, facing, rows=rows))
        btn = act.presses[0].button
        facing = btn if btn in ("UP", "DOWN", "LEFT", "RIGHT") else facing
        pressed.append(btn)
    dirs = [p for p in pressed if p in ("UP", "DOWN", "LEFT", "RIGHT")]
    assert len(set(dirs)) >= 2, pressed                   # verbatim 1252fcc: one direction forever


def test_placeholder_milestone_in_battle_finishes_dialogue_first():
    pl = GoalPlanner([Milestone("later", "not yet", placeholder=True, script_button="B")])
    b = PathBrain(planner=pl, settle_checks=0)

    def bobs(*a):
        o = obs(*a)
        o.ram["in_battle"] = True
        return o
    settle(b, bobs(1, 1, "UP"))
    act, dec = b.decide(bobs(1, 1, "UP"))
    assert act.presses[0].button == "LEFT" and "probe" in dec.reason      # can we move?
    act, dec = b.decide(bobs(1, 1, "UP"))                                    # no: text box open
    assert act.presses[0].button == "B" and "text box" in dec.reason
    act, dec = b.decide(bobs(1, 1, "UP"))
    assert act.presses[0].button == "LEFT"
    act, dec = b.decide(bobs(1, 1, "LEFT"))                                  # turned: free -> explore
    assert dec.plan == "free explore"
    strict = PathBrain(planner=GoalPlanner([Milestone("later", "x", placeholder=True)]),
                       idle_on_placeholder=False, settle_checks=0)
    settle(strict, obs(1, 1))
    with pytest.raises(BrainUnavailable, match="not implemented"):
        strict.decide(obs(1, 1))


def test_npc_tiles_are_blocked_before_bumping():
    rows = ["#######",
            "#.....#",
            "#.....#",
            "#######"]
    npc = [{"x": 3, "y": 1, "prev_x": 3, "prev_y": 1}]
    b = tile_brain(5, 1)
    settle(b, obs(1, 1, "RIGHT", rows=rows))
    o = obs(1, 1, "RIGHT", rows=rows)
    o.ram["npcs"] = npc
    act, dec = b.decide(o)
    assert [3, 1] not in dec.path and dec.path[-1] == [5, 1]
    # without the npcs key the straight line is used (and bump/replan stays the fallback)
    b2 = tile_brain(5, 1)
    settle(b2, obs(1, 1, "RIGHT", rows=rows))
    act, dec = b2.decide(obs(1, 1, "RIGHT", rows=rows))
    assert [3, 1] in dec.path
    # a walking NPC occupies both its tiles
    tall = ["#######", "#.....#", "#.....#", "#.....#", "#######"]
    b3 = tile_brain(5, 1)
    settle(b3, obs(1, 1, "RIGHT", rows=tall))
    o = obs(1, 1, "RIGHT", rows=tall)
    o.ram["npcs"] = [{"x": 3, "y": 2, "prev_x": 3, "prev_y": 1}]
    act, dec = b3.decide(o)
    assert [3, 1] not in dec.path and [3, 2] not in dec.path and [3, 3] in dec.path


def test_npc_in_the_only_way_falls_back_to_walking_up():
    rows = ["#####",
            "#...#",
            "#####"]
    b = tile_brain(3, 1)
    settle(b, obs(1, 1, "RIGHT", rows=rows))
    o = obs(1, 1, "RIGHT", rows=rows)
    o.ram["npcs"] = [{"x": 2, "y": 1}]
    act, dec = b.decide(o)  # only way is through the NPC: plan through it, bump -> A -> replan later
    assert act.presses[0].button == "RIGHT" and dec.path == [[1, 1], [2, 1], [3, 1]]


def test_script_target_presses_script_button_with_budget():
    pl = GoalPlanner([Milestone("s", "cutscene", target=lambda o: Target.script())])
    b = PathBrain(planner=pl, settle_checks=0, max_script_decisions=3)
    settle(b, obs(1, 1))
    for _ in range(3):
        act, dec = b.decide(obs(1, 1))
        assert act.presses[0].button == "A" and "scripted event" in dec.reason
    with pytest.raises(BrainUnavailable, match="give up") as e:
        b.decide(obs(1, 1))
    assert e.value.context["milestones"][0]["id"] == "s"
    # position disappears during the script's warp -> wait (not RuleBrain)
    pl = GoalPlanner([Milestone("s", "cutscene", target=lambda o: Target.script())])
    b = PathBrain(planner=pl, settle_checks=0)
    settle(b, obs(1, 1)); b.decide(obs(1, 1))
    act, dec = b.decide(Observation(frame=0, ram={"scene": "other"}))
    assert act.presses[0].button == "NONE" and "warp" in dec.plan


def test_interact_target_walks_faces_and_presses():
    pl = GoalPlanner([Milestone("i", "pick ball", done=lambda o: o.ram.get("party_count") == 1,
                                target=lambda o: Target.interact(3, 2, "UP", "A"))])
    b = PathBrain(planner=pl, settle_checks=0, max_interact_presses=2)
    settle(b, obs(3, 1, "DOWN"))
    act, dec = b.decide(obs(3, 1, "DOWN"))
    assert act.presses[0].button == "DOWN" and dec.path == [[3, 1], [3, 2]]
    act, dec = b.decide(obs(3, 2, "DOWN"))
    assert act.presses[0].button == "UP" and "turn UP" in dec.reason
    for _ in range(2):
        act, dec = b.decide(obs(3, 2, "UP"))
        assert act.presses[0].button == "A" and "interact" in dec.reason
    with pytest.raises(BrainUnavailable, match="give up"):
        b.decide(obs(3, 2, "UP"))


def test_frozen_player_presses_script_button_then_gives_up():
    b = tile_brain(4, 1, frozen_press_every=2, max_frozen=4)
    settle(b, obs(1, 1, "UP"))
    buttons = []
    with pytest.raises(BrainUnavailable, match="frozen"):
        for _ in range(20):
            act, dec = b.decide(obs(1, 1, "UP"))   # turn RIGHT never takes
            buttons.append(act.presses[0].button)
    assert buttons[:4] == ["RIGHT", "NONE", "RIGHT", "A"]


def test_arbiter_copies_milestones_onto_fallback_decision():
    b = PathBrain()
    arb = Arbiter([b, RuleBrain()])
    res = arb.step(Observation(frame=0, ram={"scene": "other"}))  # intro: no position
    assert res.decision.brain == "rule"
    assert [m["id"] for m in res.decision.milestones][:2] == ["intro", "leave_bedroom"]
    assert res.decision.goal.startswith("Get through the intro")


def test_pathbrain_walks_mock_house_through_parcel_and_replays(tmp_path):
    s = demo.run("mock-house", steps=320, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    order = [m for i, m in enumerate(maps) if m[0] is not None and (i == 0 or maps[i - 1] != m)]
    known = [m for m in maps if m[0] is not None]
    visits = [m for i, m in enumerate(known) if i == 0 or known[i - 1] != m]
    # M3: two map connections north; Oak's Parcel: the mart, two connections south, the lab door
    assert visits == [(4, 1), (4, 0), (3, 0), (4, 3), (3, 0), (3, 19), (3, 1), (5, 3), (3, 1), (3, 19), (3, 0), (4, 3)]
    assert order[:4] == [(4, 1), (4, 0), (3, 0), (4, 3)]
    assert (s["final_ram"]["map_bank"], s["final_ram"]["map_id"]) == (4, 3)
    assert s["final_ram"]["party_count"] == 1 and s["final_ram"]["scene"] == "overworld"  # no naming screen
    reasons = [r["decision"]["reason"] for r in steps if r["decision"]["brain"] == "path"]
    assert any("treat as blocked" in t for t in reasons)  # 1F: unlisted NPC -> bump, replan (fallback)
    assert any("dialogue" in t for t in reasons)          # pressed A through the text box
    assert any("scripted event" in t for t in reasons)    # Oak's script
    assert any("press B" in t for t in reasons)           # declined the nickname
    lab = [r for r, m in zip(steps, maps) if m == (4, 3)]
    assert not any("no movement" in r["decision"]["reason"] for r in lab)  # listed NPCs: never bumped
    # never stood on the hidden NPC tile
    assert all((r["observation"]["ram"].get("player_x"), r["observation"]["ram"].get("player_y")) != (9, 4)
               or maps[i] != (4, 0) for i, r in enumerate(steps))
    assert all(r["decision"].get("milestones") for r in steps)  # every step, also RuleBrain's
    last = steps[-1]["decision"]
    assert last["brain"] == "path" and last["plan"] == "free explore"   # goal 17: walks, no idling
    done = {m["id"]: m["done"] for m in last["milestones"]}
    assert done["get_starter"] and done["rival_battle"] and done["rival_battle_over"]
    assert all(v for k, v in done.items() if k != "pewter_city") and not done["pewter_city"]
    assert sum("off the map edge" in t for t in reasons) == 4
    assert any("onto the warp (player frozen" in t for t in reasons)   # the mart clerk's script
    assert {n["local_id"] for n in s["final_ram"]["npcs"]} == {4, 8}      # Pokedexes taken off the table
    # the mock rival stopped us on row 8; the battle (no ram["battle"]) was RuleBrain's A presses
    battle = [r for r in steps if r["observation"]["ram"].get("in_battle") is True]
    assert battle and all(r["decision"]["brain"] == "rule" for r in battle)
    after = steps[steps.index(battle[-1]) + 1]["observation"]["ram"]
    assert (after["map_bank"], after["map_id"], after["player_x"], after["player_y"]) == (4, 3, 7, 8)
    assert any(r["decision"].get("path") for r in steps)
    assert replay(s["log"], MockHouseAdapter()) == []


def _edge_obs(x, y, facing, map_id=19):
    rows = ["##..##", "#....#", "#....#"]
    return Observation(frame=0, ram={"scene": "overworld", "in_battle": False, "map_bank": 3, "map_id": map_id,
                                     "player_x": x, "player_y": y, "facing": facing, "map_w": 6, "map_h": 3,
                                     "collision": rows, "warps": [], "party_count": 1})


def test_pathbrain_edge_target_turns_steps_off_and_gives_up_on_a_dead_edge():
    pl = GoalPlanner([Milestone("north", "walk off the top", target=lambda o: Target.edge("UP"))])
    pb = PathBrain(planner=pl, settle_checks=1)
    o = _edge_obs(3, 2, "UP")
    while "settle" in pb.decide(o)[1].reason or "stable" in pb.decide(o)[1].reason:
        pass
    a, d = pb.decide(_edge_obs(3, 1, "UP"))
    assert a.presses[0].button == "UP" and "step UP to (3, 0)" in d.reason
    a, d = pb.decide(_edge_obs(3, 0, "LEFT"))           # on the edge, facing the wrong way
    assert a.presses[0].button == "UP" and "turn UP" in d.reason
    for _ in range(3):                                   # pressing off the edge, map never changes
        a, d = pb.decide(_edge_obs(3, 0, "UP"))
        assert a.presses[0].button == "UP" and "off the map edge" in d.reason
    a, d = pb.decide(_edge_obs(3, 0, "UP"))              # (3,0) is dead now -> go to (2,0)
    assert a.presses[0].button == "LEFT" and "(2, 0)" in d.reason
    for x in (2,):
        pb.decide(_edge_obs(x, 0, "LEFT"))               # turn
        for _ in range(3):
            pb.decide(_edge_obs(x, 0, "UP"))
    with pytest.raises(BrainUnavailable, match="no walkable tile on the UP edge"):
        pb.decide(_edge_obs(2, 0, "UP"))


def test_existing_mock_adapter_still_has_no_map_so_path_falls_back(tmp_path):
    s = demo.run("mock", steps=20, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    assert s["decisions_by_brain"] == {"rule": 20}


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
def test_real_firered_pathbrain_gets_starter(tmp_path):
    """Real ROM: bedroom -> Pallet Town -> Oak's script -> lab -> Bulbasaur (party_count 1)."""
    from game_brain.adapters import make_adapter

    s = demo.run("mgba", steps=1100, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    for m in ((4, 1), (4, 0), (3, 0), (4, 3)):
        assert m in maps
    assert maps.index((4, 1)) < maps.index((4, 0)) < maps.index((3, 0)) < maps.index((4, 3))
    fr = s["final_ram"]
    assert (fr["map_bank"], fr["map_id"], fr["party_count"], fr["scene"]) == (4, 3, 1, "overworld")
    assert all(r["decision"].get("milestones") for r in steps)
    done = {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}
    assert done["get_starter"]   # next: walk to the exit (the rival battle: tests/test_battle_brain_real.py)
    assert any("press B" in r["decision"]["reason"] for r in steps)   # nickname declined
    a = make_adapter("mgba")
    assert replay(s["log"], a) == []
    # starter is Bulbasaur (species 1), not renamed: decrypt party slot 0 (gen-3 substructure layout)
    r, p = a.ram, 0x02024284
    pid, key = r.u32(p), r.u32(p) ^ r.u32(p + 4)
    orders = ["GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA", "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
              "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG", "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG"]
    growth = orders[pid % 24].index("G")
    assert (r.u32(p + 0x20 + 12 * growth) ^ key) & 0xFFFF == 1
    name = bytes(r.u8(p + 8 + i) for i in range(9))
    assert name == bytes([0xBC, 0xCF, 0xC6, 0xBC, 0xBB, 0xCD, 0xBB, 0xCF, 0xCC])  # "BULBASAUR"


def test_path_cache_reuses_the_same_route_after_a_step():
    b = tile_brain(4, 1)
    settle(b, obs(1, 1, "UP"))
    _, first = b.decide(obs(1, 1, "UP"))  # plans once, then turns
    assert b.stats["path_cache_hits"] == 0
    assert first.path[0] == [1, 1] and first.path[1] == [2, 1]
    _, stepped = b.decide(obs(1, 1, "RIGHT"))
    assert b.stats["path_cache_hits"] == 1
    assert stepped.path == first.path
    _, second = b.decide(obs(2, 1, "RIGHT"))
    assert b.stats["path_cache_hits"] == 2
    assert second.path[0] == [2, 1] and second.path[-1] == [4, 1]
    assert second.path == first.path[1:]
