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
