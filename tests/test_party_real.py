"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): ``ram["party"]`` from boot through
getting the starter and into the rival battle (~1400 steps, ~15 s). Saves/logs go to tmp_path."""
import os
import struct

import pytest

from game_brain import demo
from game_brain.runlog import iter_steps


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
def test_real_party_starter_and_rival_battle(tmp_path, monkeypatch):
    from game_brain.adapters import make_adapter
    from game_brain.adapters.gba_mgba import firered_party as fp

    monkeypatch.delenv("GAME_BRAIN_START_STATE", raising=False)
    s = demo.run("mgba", steps=1400, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path),
                 quiet=True, save_dir=None, save_every=0)
    steps = list(iter_steps(s["log"]))
    rams = [r["observation"]["ram"] for r in steps]
    with_party = [r for r in rams if r.get("party")]
    assert with_party, "never got the starter"
    for r in rams:
        if "party_count" in r:
            assert len(r["party"]) == r["party_count"]
    # the starter, as seen on screen: BULBASAUR Lv5 22/22; in this (modified) ROM it knows only
    # METRONOME (move 118) with 40/40 PP (the ROM's gBattleMoves says 40; vanilla is 10)
    first = with_party[0]["party"]
    assert first == [{"slot": 0, "species_id": 1, "species": "BULBASAUR", "egg": False, "level": 5,
                      "hp": 22, "max_hp": 22, "status": None, "active": False,
                      "moves": [{"id": 118, "name": "METRONOME", "pp": 40, "max_pp": 40}]}]
    # rival battle: once ram["battle"]["player"] is trusted the starter is active and the party
    # struct's hp/pp equal gBattleMons[0] (the game writes them back on every hit / move use)
    trusted = [r for r in rams if r.get("in_battle") and (r.get("battle") or {}).get("player")]
    assert len(trusted) > 20
    for r in trusted:
        p, b = r["party"][0], r["battle"]["player"]
        assert p["active"] is True and (p["species_id"], p["level"], p["max_hp"]) == (b["species"], b["level"], b["max_hp"])
        assert p["hp"] == b["hp"] and [m["pp"] for m in p["moves"]] == [m["pp"] for m in b["moves"]]
    assert min(r["party"][0]["moves"][0]["pp"] for r in trusted) < 40       # PP went down during the battle
    untrusted = [r for r in rams if r.get("in_battle") and not (r.get("battle") or {}).get("player")]
    assert untrusted and all(r["party"][0]["active"] is False for r in untrusted)

    # the fast EWRAM block read (cffi view) agrees with bus reads, also after load_state; ROM tables
    a = make_adapter("mgba")
    a.reset()
    a.load_state(a.save_state())        # smoke: block reader survives load_state
    assert a.ram.block(fp.G_PLAYER_PARTY, 8) == bytes(a.ram.u8(fp.G_PLAYER_PARTY + i) for i in range(8))
    assert a.rom_tables is not None and a.rom_tables.species_name(277) == "TREECKO"
    assert a.rom_tables.move_name(118) == "METRONOME" and a.rom_tables.base_pp(118) == 40
