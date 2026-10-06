"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): boot -> starter -> rival battle ->
Pallet Town -> Route 1 (wild battles) -> Viridian City (M2 + M3) -> Viridian Mart (Oak's Parcel) ->
back south over Route 1 -> Oak's lab: the Pokedex."""
import os
from collections import Counter
from pathlib import Path

import pytest

from game_brain import demo, savestate
from game_brain.runlog import iter_steps, replay


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


def _battles(steps):
    """[(first index, last index)] of consecutive in_battle runs."""
    out, start = [], None
    for i, r in enumerate(steps):
        ib = r["observation"]["ram"].get("in_battle") is True
        if ib and start is None:
            start = i
        if not ib and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(steps) - 1))
    return out


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
def test_real_firered_rival_battle_route_1_viridian_parcel_pokedex(tmp_path, monkeypatch):
    """battle,path,rule from boot. The emulator is deterministic, so every run is the same:
    the rival stops you at (7,8) and RuleBattleBrain fights (METRONOME only); then out of the
    lab, off Pallet Town's north edge onto Route 1 (3/19), through the grass (wild battles,
    also RuleBattleBrain) and off Route 1's north edge into Viridian City (3/1). Then the Oak's
    Parcel errand: into the Viridian Mart (5/3; the clerk's script freezes you on the exit warp and
    hands over the parcel), back out, south over Route 1 (ledges: A* treats them as walls) to Pallet
    Town and the lab, where Oak takes the two Pokedexes off the table (objects local_id 9/10
    disappear from ram["npcs"]) and gives you one; goal 17 is a placeholder -> free explore (at step
    4300 his speech is still running)."""
    from game_brain.adapters import make_adapter

    monkeypatch.delenv("GAME_BRAIN_START_STATE", raising=False)
    saves = tmp_path / "saves"                                       # tmp only, never the repo
    s = demo.run("mgba", steps=4300, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path), quiet=True,
                 save_dir=str(saves), save_every=0)
    steps = list(iter_steps(s["log"]))
    battles = _battles(steps)
    assert len(battles) >= 2                                         # rival + at least one wild battle
    for a, b in battles:
        battle = steps[a:b + 1]
        assert Counter(r["decision"]["brain"] for r in battle) == {"battle": len(battle)}
        chosen = [r for r in battle if r["decision"].get("battle_options")]
        assert chosen and all(r["decision"]["intent"] == "FIGHT:0" for r in chosen)
        outcome = next(r["observation"]["ram"]["battle"]["outcome"] for r in reversed(battle)
                       if r["observation"]["ram"]["battle"]["outcome"])
        # "unknown": raw gBattleOutcome 5 on this run (step ~3580) -- pokefirered's
        # B_OUTCOME_PLAYER_TELEPORTED (METRONOME -> TELEPORT, our reading; Backend to verify / map)
        assert outcome in ("win", "lose", "unknown")
    a, b = battles[0]
    after = next(r["observation"]["ram"] for r in steps[b + 1:] if r["observation"]["ram"].get("player_x") is not None)
    assert (after["map_bank"], after["map_id"], after["player_x"], after["player_y"], after["party_count"]) == (4, 3, 7, 8, 1)
    for a, b in battles[1:]:                                          # wild battles on Route 1
        before = next(r["observation"]["ram"] for r in reversed(steps[:a]) if r["observation"]["ram"].get("map_id") is not None)
        assert (before["map_bank"], before["map_id"]) == (3, 19)
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    known = [m for m in maps if m[0] is not None]
    visits = [m for i, m in enumerate(known) if i == 0 or known[i - 1] != m]
    assert visits[-9:] == [(4, 3), (3, 0), (3, 19), (3, 1), (5, 3), (3, 1), (3, 19), (3, 0), (4, 3)]
    fr = s["final_ram"]
    assert (fr["map_bank"], fr["map_id"], fr["scene"], fr["player_x"], fr["player_y"]) == (4, 3, "overworld", 6, 4)
    done = {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}
    assert all(v for k, v in done.items() if k != "pewter_city") and not done["pewter_city"]
    reasons = [r["decision"]["reason"] for r in steps]
    assert sum("off the map edge" in t for t in reasons) >= 4
    assert any("onto the warp (player frozen" in t for t in reasons)     # mart clerk's script
    # Pokedex evidence: at Oak's stand (6,4) the table objects were listed, then gone (Oak still there)
    first = next(i for i, r in enumerate(steps) if any(m["id"] == "deliver_parcel" and m["done"]
                                                       for m in r["decision"]["milestones"]))
    ids = lambda r: {n["local_id"] for n in r["observation"]["ram"].get("npcs") or ()}  # noqa: E731
    at_stand = [r for r in steps[:first] if (r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id"),
                r["observation"]["ram"].get("player_x"), r["observation"]["ram"].get("player_y")) == (4, 3, 6, 4)]
    assert any({4, 9, 10} <= ids(r) for r in at_stand)            # (one disappears a step before the other)
    assert 4 in ids(steps[first]) and not {9, 10} & ids(steps[first])
    assert steps[-1]["decision"]["plan"] == "free explore"   # goal 17 placeholder: free explore, no idling
    assert all(r["decision"].get("milestones") for r in steps)
    assert replay(s["log"], make_adapter("mgba")) == []
    # milestone saves for the parcel errand; resume from the one after leaving the mart (parcel in
    # hand: only the restored milestone list says so) and the parcel still gets delivered
    names = {Path(p).stem.split("_", 1)[1]: p for p in s["saves"]}
    for mid in ("viridian_city", "viridian_mart", "oaks_parcel", "back_to_pallet", "deliver_parcel"):
        assert "milestone-" + mid in names
    side = savestate.load_sidecar(names["milestone-oaks_parcel"])
    assert (side["map_bank"], side["map_id"]) == (3, 1) and side["milestone"] == "back_to_pallet"
    ref = {r["step"]: r for r in steps}
    b = demo.run("mgba", steps=900, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path / "b"),
                 quiet=True, save_dir=str(saves), save_every=0, resume=side["_path"])
    bsteps = list(iter_steps(b["log"]))
    assert bsteps[0]["step"] == side["step"] and bsteps[0]["observation"]["ram"] == ref[side["step"]]["observation"]["ram"]
    first = {m["id"]: m["done"] for m in bsteps[0]["decision"]["milestones"]}
    assert first["oaks_parcel"] and not first["back_to_pallet"]
    bdone = {m["id"]: m["done"] for m in bsteps[-1]["decision"]["milestones"]}
    assert bdone["deliver_parcel"] and not bdone["pewter_city"]
    assert (b["final_ram"]["map_bank"], b["final_ram"]["map_id"]) == (4, 3)
    assert replay(b["log"], make_adapter("mgba")) == []
