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


def test_placeholder_milestone_idles():
    pl = GoalPlanner([Milestone("later", "not yet", placeholder=True)])
    b = PathBrain(planner=pl, settle_checks=0)
    settle(b, obs(1, 1))
    act, dec = b.decide(obs(1, 1))
    assert act.presses[0].button == "NONE" and "not implemented" in dec.reason
    strict = PathBrain(planner=GoalPlanner([Milestone("later", "x", placeholder=True)]),
                       idle_on_placeholder=False, settle_checks=0)
    settle(strict, obs(1, 1))
    with pytest.raises(BrainUnavailable, match="not implemented"):
        strict.decide(obs(1, 1))


def test_pathbrain_walks_mock_house_to_outside_and_replays(tmp_path):
    s = demo.run("mock-house", steps=120, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    assert (4, 0) in maps and (3, 0) in maps
    first_out = maps.index((3, 0))
    assert first_out < 80
    assert s["final_ram"]["map_bank"] == 3 and s["final_ram"]["map_id"] == 0
    reasons = [r["decision"]["reason"] for r in steps if r["decision"]["brain"] == "path"]
    assert any("treat as blocked" in t for t in reasons)  # bumped the NPC and replanned
    assert any("dialogue" in t for t in reasons)          # pressed A through the text box
    # never stood on the NPC tile
    assert all((r["observation"]["ram"].get("player_x"), r["observation"]["ram"].get("player_y")) != (9, 4)
               or maps[i] != (4, 0) for i, r in enumerate(steps))
    last = steps[-1]["decision"]
    assert last["brain"] == "path" and last["milestones"][3] == {"id": "pallet_town",
                                                               "label": "Stand in Pallet Town", "done": True}
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
def test_real_firered_pathbrain_reaches_pallet_town(tmp_path):
    from game_brain.adapters import make_adapter

    s = demo.run("mgba", steps=650, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    assert (4, 1) in maps and (4, 0) in maps and (3, 0) in maps
    assert maps.index((4, 1)) < maps.index((4, 0)) < maps.index((3, 0))
    assert (s["final_ram"]["map_bank"], s["final_ram"]["map_id"]) == (3, 0)
    assert replay(s["log"], make_adapter("mgba")) == []
