"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): save mid-Route-1 (in a wild battle),
stop, resume from the save, and still reach Viridian City. Saves go to tmp_path, never the repo."""
import json
import os
from pathlib import Path

import pytest

from game_brain import demo, savestate
from game_brain.runlog import iter_steps, read_log, replay


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
def test_real_save_resume_mid_route_1_reaches_viridian(tmp_path, monkeypatch):
    from game_brain.adapters import make_adapter

    monkeypatch.delenv("GAME_BRAIN_START_STATE", raising=False)
    saves = tmp_path / "saves"
    # boot run, "killed" 10 steps after the periodic save at 2500 (deterministic: a Route 1 wild battle)
    a = demo.run("mgba", steps=2510, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path / "a"),
                 quiet=True, save_dir=str(saves), save_every=2500)
    names = [Path(p).stem for p in a["saves"]]
    assert "0002315_milestone-route_1" in names and "0002500_periodic" in names
    ref = {r["step"]: r for r in iter_steps(a["log"])}
    # milestone save: position recorded = the observation after that step's action
    m = json.loads(Path(a["saves"][names.index("0002315_milestone-route_1")]).read_text())
    assert (m["map_bank"], m["map_id"], m["x"], m["y"]) == (3, 19, 13, 39)
    r2315 = ref[2315]["observation"]["ram"]
    assert (r2315["map_bank"], r2315["map_id"], r2315["player_x"], r2315["player_y"]) == (3, 19, 13, 39)
    side = savestate.load_sidecar(a["saves"][names.index("0002500_periodic")])
    assert side["in_battle"] is True and side["party_hp"][0]["hp"] > 0 and side["rom_sha1"]
    assert side["adapter_state"] == {"battle_ready": True} and side["milestone"] == "viridian_city"

    b = demo.run("mgba", steps=800, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path / "b"),
                 quiet=True, save_dir=str(saves), save_every=0, resume=side["_path"])
    steps = list(iter_steps(b["log"]))
    first = steps[0]
    assert first["step"] == 2500 and first["frame"] == ref[2500]["frame"] == side["frame"]
    assert first["observation"]["ram"] == ref[2500]["observation"]["ram"]       # identical moment
    hp = first["observation"]["ram"]["battle"]["player"]
    # party_hp comes from ram["party"] now (the party struct's hp is the live in-battle hp)
    assert [{"hp": hp["hp"], "max_hp": hp["max_hp"]}] == side["party_hp"]
    assert next(read_log(b["log"]))["resumed_from"]["sidecar"] == side["_path"]
    done = {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}
    assert done["route_1"] and done["viridian_city"]
    fr = b["final_ram"]
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    assert (3, 1) in maps and fr["scene"] == "overworld"      # Viridian City (then on into the Poke Mart)
    assert replay(b["log"], make_adapter("mgba")) == []
