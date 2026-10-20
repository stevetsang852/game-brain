"""Free-explore from Viridian Forest 1/0 north mats into 15/3 (Fullstack next item).

Unit/mock only. No ROM, save, log, or screenshot.
"""

from game_brain.brain import GoalPlanner, Milestone, PathBrain
from game_brain.schema import Observation


def obs(x, y, facing="UP", rows=None, warps=(), bank=1, mid=0):
    if rows is None:
        rows = ["#######", "#.....#", "#.....#", "#######"]
    return Observation(frame=0, ram={
        "player_x": x, "player_y": y, "facing": facing,
        "map_bank": bank, "map_id": mid,
        "map_w": len(rows[0]), "map_h": len(rows),
        "collision": list(rows), "warps": list(warps),
    })


def settle(b, o):
    act, dec = b.decide(o)
    assert [p.button for p in act.presses] == ["NONE"]


def _forest_1_0_rows():
    rows = []
    for y in range(12):
        row = ["#"] * 15
        if 1 <= y <= 10:
            for x in range(3, 12):
                row[x] = "."
        rows.append("".join(row))
    return rows


def _forest_north_mats_warps():
    return [
        {"x": 5, "y": 9, "dest_bank": 15, "dest_map": 3, "enter": "UP", "behavior": 0x60},
        {"x": 4, "y": 9, "dest_bank": 15, "dest_map": 3, "enter": "UP", "behavior": 0x60},
        {"x": 6, "y": 9, "dest_bank": 15, "dest_map": 3, "enter": "UP", "behavior": 0x60},
    ]


def _forest_north_bldg_rows():
    rows = []
    for y in range(12):
        row = ["#"] * 15
        if 1 <= y <= 10:
            row[7] = "."
        rows.append("".join(row))
    return rows


def _forest_north_warps():
    return [
        {"x": 7, "y": 10, "dest_bank": 1, "dest_map": 0, "enter": "DOWN", "behavior": 0x65},
        {"x": 7, "y": 1, "dest_bank": 3, "dest_map": 20, "enter": "UP", "behavior": 0x60},
    ]


def test_free_explore_forest_north_mats_into_15_3():
    """From forest 1/0 north mats (4-6,9) 0x60 UP into 15/3; on arrival do not reverse DOWN."""
    forest_rows = _forest_1_0_rows()
    forest_warps = _forest_north_mats_warps()
    bldg_rows = _forest_north_bldg_rows()
    bldg_warps = _forest_north_warps()

    def o1(x, y, facing="UP"):
        return obs(x, y, facing, rows=forest_rows, warps=forest_warps, bank=1, mid=0)

    def o15(x, y, facing="UP"):
        return obs(x, y, facing, rows=bldg_rows, warps=bldg_warps, bank=15, mid=3)

    b = PathBrain(planner=GoalPlanner([Milestone("later", "not yet", placeholder=True)]),
                  settle_checks=0)
    settle(b, o1(5, 8, "UP"))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx or dy:
                b._visits[((1, 0), 5 + dx, 8 + dy)] = 4
    act, dec = b.decide(o1(5, 9, "UP"))
    assert dec.plan == "free explore"
    assert "take warp" in dec.reason
    assert act.presses[0].button == "UP"
    assert "-> (15, 3)" in dec.reason

    act, dec = b.decide(o15(7, 10, "UP"))
    assert act.presses[0].button == "NONE"
    assert b._prev_map == (1, 0)
    act, dec = b.decide(o15(7, 10, "UP"))
    assert dec.plan == "free explore"
    assert "take warp" not in dec.reason
    assert act.presses[0].button == "UP"
    assert "walk UP" in dec.reason or "turn UP" in dec.reason
