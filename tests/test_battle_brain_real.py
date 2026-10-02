"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): boot -> starter -> rival battle ->
Pallet Town -> Route 1 (wild battles) -> Viridian City (M2 + M3)."""
import os
from collections import Counter

import pytest

from game_brain import demo
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
def test_real_firered_rival_battle_route_1_to_viridian(tmp_path):
    """battle,path,rule from boot. The emulator is deterministic, so every run is the same:
    the rival stops you at (7,8) and RuleBattleBrain fights (METRONOME only); then out of the
    lab, off Pallet Town's north edge onto Route 1 (3/19), through the grass (wild battles,
    also RuleBattleBrain) and off Route 1's north edge into Viridian City (3/1)."""
    from game_brain.adapters import make_adapter

    s = demo.run("mgba", steps=3300, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path), quiet=True)
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
        assert outcome in ("win", "lose")
    a, b = battles[0]
    after = next(r["observation"]["ram"] for r in steps[b + 1:] if r["observation"]["ram"].get("player_x") is not None)
    assert (after["map_bank"], after["map_id"], after["player_x"], after["player_y"], after["party_count"]) == (4, 3, 7, 8, 1)
    for a, b in battles[1:]:                                          # wild battles on Route 1
        before = next(r["observation"]["ram"] for r in reversed(steps[:a]) if r["observation"]["ram"].get("map_id") is not None)
        assert (before["map_bank"], before["map_id"]) == (3, 19)
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    known = [m for m in maps if m[0] is not None]
    visits = [m for i, m in enumerate(known) if i == 0 or known[i - 1] != m]
    assert visits[-4:] == [(4, 3), (3, 0), (3, 19), (3, 1)]
    fr = s["final_ram"]
    assert (fr["map_bank"], fr["map_id"], fr["scene"]) == (3, 1, "overworld")
    done = {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}
    assert done["rival_battle"] and done["route_1"] and done["viridian_city"] and not done["oaks_parcel"]
    reasons = [r["decision"]["reason"] for r in steps]
    assert sum("off the map edge" in t for t in reasons) >= 2
    assert all(r["decision"].get("milestones") for r in steps)
    assert replay(s["log"], make_adapter("mgba")) == []
