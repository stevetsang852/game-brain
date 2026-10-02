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


def test_placeholder_milestone_finishes_dialogue_then_idles():
    pl = GoalPlanner([Milestone("later", "not yet", placeholder=True, script_button="B")])
    b = PathBrain(planner=pl, settle_checks=0)
    settle(b, obs(1, 1, "UP"))
    act, dec = b.decide(obs(1, 1, "UP"))
    assert act.presses[0].button == "LEFT" and "probe" in dec.reason      # can we move?
    act, dec = b.decide(obs(1, 1, "UP"))                                    # no: text box open
    assert act.presses[0].button == "B" and "text box" in dec.reason
    act, dec = b.decide(obs(1, 1, "UP"))
    assert act.presses[0].button == "LEFT"
    act, dec = b.decide(obs(1, 1, "LEFT"))                                  # turned: free
    assert act.presses[0].button == "NONE" and "not implemented" in dec.reason
    act, dec = b.decide(obs(1, 1, "LEFT"))
    assert act.presses[0].button == "NONE" and dec.milestones == [{"id": "later", "label": "not yet",
                                                                  "done": False}]
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


def test_pathbrain_walks_mock_house_through_m2_and_replays(tmp_path):
    s = demo.run("mock-house", steps=250, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    order = [m for i, m in enumerate(maps) if m[0] is not None and (i == 0 or maps[i - 1] != m)]
    assert order[:4] == [(4, 1), (4, 0), (3, 0), (4, 3)]
    assert s["final_ram"]["map_bank"] == 4 and s["final_ram"]["map_id"] == 3
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
    assert last["brain"] == "path" and "not implemented yet -> idle" in last["reason"]
    done = {m["id"]: m["done"] for m in last["milestones"]}
    assert done["get_starter"] and not done["rival_battle"]
    assert any(r["decision"].get("path") for r in steps)
    assert replay(s["log"], MockHouseAdapter()) == []


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
    last = steps[-1]["decision"]
    assert "not implemented yet -> idle" in last["reason"]   # dialogue finished, nickname declined
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
