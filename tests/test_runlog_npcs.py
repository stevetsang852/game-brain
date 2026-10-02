"""Run-log NPC dedupe: ``observation.ram.npcs`` written in full only when needed,
otherwise ``npcs_same`` or ``npcs_delta`` (changed entries by index). The reader restores
the exact list; old logs and the live observation are unaffected."""

import json

from game_brain.arbiter import StepResult
from game_brain.runlog import RunLogWriter, iter_steps, read_log
from game_brain.schema import Action, Decision, Mode, Observation


def _npc(x, y, lid, px=None, py=None):
    return {"x": x, "y": y, "prev_x": x if px is None else px, "prev_y": y if py is None else py,
            "elevation": 3, "local_id": lid, "gfx": 7}


def _res():
    a = Action.tap("UP")
    return StepResult(mode=Mode.AUTO, decision=Decision(brain="path", plan="p", reason="r", mode="auto"),
                      proposed=a, executed=a)


def _write(path, rams):
    obs = [Observation(frame=i, ram=r) for i, r in enumerate(rams)]
    with RunLogWriter(path) as log:
        log.header(adapter="test")
        for i, o in enumerate(obs):
            log.step(i, o, _res(), 1, ts=0.0)
    return obs


TEN = [_npc(i, 2, i + 1) for i in range(10)]
MOVED = [dict(n) for n in TEN]
MOVED[3] = _npc(3, 3, 4, 3, 2)


def test_npcs_roundtrip_same_delta_full(tmp_path):
    rams = [
        {"player_x": 1},                       # no npcs key at all (e.g. battle / title)
        {"player_x": 1, "npcs": TEN},          # first -> full
        {"player_x": 1, "npcs": TEN},          # same
        {"player_x": 1, "npcs": MOVED},        # one moved -> delta
        {"player_x": 2},                       # npcs missing: baseline kept
        {"player_x": 2, "npcs": MOVED},        # same as last seen -> same
        {"player_x": 2, "npcs": TEN[:4]},      # count changed -> full
        {"player_x": 2, "npcs": []},           # empty map -> full (empty)
        {"player_x": 2, "npcs": []},           # same
        {"player_x": 2, "npcs": [_npc(9, 9, 1)]},
    ]
    p = tmp_path / "run.jsonl"
    obs = _write(p, rams)
    raw = [r["observation"]["ram"] for r in read_log(p) if r["kind"] == "step"]
    kinds = ["full" if "npcs" in r else "same" if r.get("npcs_same") else
             "delta" if "npcs_delta" in r else "none" for r in raw]
    assert kinds == ["none", "full", "same", "delta", "none", "same", "full", "full", "same", "full"]
    assert raw[3]["npcs_delta"] == {"3": MOVED[3]}
    restored = [r["observation"]["ram"] for r in iter_steps(p)]
    assert restored == rams
    assert all("npcs_same" not in r and "npcs_delta" not in r for r in restored)
    # the live observations (what the dashboard gets) still carry the full list
    assert [o.ram.get("npcs") for o in obs] == [r.get("npcs") for r in rams]


def test_delta_not_used_when_not_smaller(tmp_path):
    allmoved = [_npc(n["x"], 5, n["local_id"]) for n in TEN]
    p = tmp_path / "run.jsonl"
    _write(p, [{"npcs": TEN}, {"npcs": allmoved}])
    raw = [r["observation"]["ram"] for r in read_log(p) if r["kind"] == "step"]
    assert "npcs" in raw[1] and "npcs_delta" not in raw[1]
    assert [r["observation"]["ram"]["npcs"] for r in iter_steps(p)] == [TEN, allmoved]


def test_old_log_without_markers_reads_unchanged(tmp_path):
    p = tmp_path / "old.jsonl"
    step = {"kind": "step", "step": 0, "frame": 0, "ts": 0, "mode": "auto",
            "observation": {"ram": {"npcs": TEN}}, "decision": {}, "proposed_action": None,
            "executed_action": None, "frames_advanced": 1, "notes": []}
    p.write_text(json.dumps({"kind": "header"}) + "\n" + json.dumps(step) + "\n" + json.dumps(step) + "\n")
    assert [r["observation"]["ram"]["npcs"] for r in iter_steps(p)] == [TEN, TEN]
