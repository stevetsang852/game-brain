"""Run-log battle dedupe: ``observation.ram.battle`` written in full only when needed,
otherwise ``battle_same`` or ``battle_delta`` (changed top-level keys). The reader restores
the exact dict; steps outside battle keep the baseline; old logs and live observations are
unaffected."""

import json

from game_brain.arbiter import StepResult
from game_brain.runlog import RunLogWriter, iter_steps, read_log
from game_brain.schema import Action, Decision, Mode, Observation


def _mon(hp, species=4):
    return {"species": species, "level": 5, "hp": hp, "max_hp": 20, "hp_pct": round(100 * hp / 20),
            "moves": [118, 0, 0, 0], "pp": [10, 0, 0, 0]}


def _b(menu="action", cursor=0, php=20, ohp=20, outcome=None):
    return {"menu": menu, "cursor": cursor, "player": _mon(php),
            "opponent": _mon(ohp, 7), "outcome": outcome}


HIDDEN = {"menu": "other", "cursor": None, "player": None, "opponent": None, "outcome": None}


def _res():
    a = Action.tap("A")
    return StepResult(mode=Mode.AUTO, decision=Decision(brain="battle", plan="p", reason="r", mode="auto"),
                      proposed=a, executed=a)


def _write(path, rams):
    obs = [Observation(frame=i, ram=r) for i, r in enumerate(rams)]
    with RunLogWriter(path) as log:
        log.header(adapter="test")
        for i, o in enumerate(obs):
            log.step(i, o, _res(), 1, ts=0.0)
    return obs


def _kind(r):
    return ("full" if "battle" in r else "same" if r.get("battle_same") else
            "delta" if "battle_delta" in r else "none")


def test_battle_roundtrip_same_delta_full(tmp_path):
    rams = [
        {"in_battle": False},                          # overworld: no battle key
        {"in_battle": True, "battle": HIDDEN},         # first -> full
        {"in_battle": True, "battle": HIDDEN},         # same
        {"in_battle": True, "battle": _b()},           # 4 of 5 keys changed -> delta (still smaller)
        {"in_battle": True, "battle": _b()},           # same
        {"in_battle": True, "battle": _b(cursor=1)},   # cursor only -> delta
        {"in_battle": True, "battle": _b(cursor=1, ohp=12)},  # opponent hp -> delta
        {"in_battle": False},                          # left battle: baseline kept
        {"in_battle": True, "battle": _b(cursor=1, ohp=12)},  # same as last seen -> same
        {"in_battle": True, "battle": {"menu": "other"}},     # key set changed -> full
    ]
    p = tmp_path / "run.jsonl"
    obs = _write(p, rams)
    raw = [r["observation"]["ram"] for r in read_log(p) if r["kind"] == "step"]
    assert [_kind(r) for r in raw] == ["none", "full", "same", "delta", "same", "delta", "delta",
                                       "none", "same", "full"]
    assert raw[5]["battle_delta"] == {"cursor": 1}
    assert raw[6]["battle_delta"] == {"opponent": _mon(12, 7)}
    restored = [r["observation"]["ram"] for r in iter_steps(p)]
    assert restored == rams
    assert all("battle_same" not in r and "battle_delta" not in r for r in restored)
    # live observations (dashboard) still carry the full dict
    assert [o.ram.get("battle") for o in obs] == [r.get("battle") for r in rams]


def test_battle_delta_not_used_when_not_smaller(tmp_path):
    a, b = _b(), _b(menu="move", cursor=2, php=3, ohp=1, outcome="win")
    p = tmp_path / "run.jsonl"
    _write(p, [{"battle": a}, {"battle": b}])
    raw = [r["observation"]["ram"] for r in read_log(p) if r["kind"] == "step"]
    assert "battle" in raw[1] and "battle_delta" not in raw[1]
    assert [r["observation"]["ram"]["battle"] for r in iter_steps(p)] == [a, b]


def test_battle_and_npcs_dedupe_together(tmp_path):
    npcs = [{"x": 1, "y": 2, "local_id": 1}]
    rams = [{"npcs": npcs, "battle": _b()}, {"npcs": npcs, "battle": _b(cursor=1)},
            {"npcs": npcs, "battle": _b(cursor=1)}]
    p = tmp_path / "run.jsonl"
    _write(p, rams)
    assert [r["observation"]["ram"] for r in iter_steps(p)] == rams


def test_old_log_without_battle_markers_reads_unchanged(tmp_path):
    p = tmp_path / "old.jsonl"
    step = {"kind": "step", "step": 0, "frame": 0, "ts": 0, "mode": "auto",
            "observation": {"ram": {"battle": _b()}}, "decision": {}, "proposed_action": None,
            "executed_action": None, "frames_advanced": 1, "notes": []}
    p.write_text(json.dumps({"kind": "header"}) + "\n" + json.dumps(step) + "\n" + json.dumps(step) + "\n")
    assert [r["observation"]["ram"]["battle"] for r in iter_steps(p)] == [_b(), _b()]
