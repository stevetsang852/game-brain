"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): losing a wild battle on Route 1.

Harness only (never in a brain): boot with battle,path,rule until the first wild battle on Route 1
shows its action menu, then a **TEST-ONLY RAM write** sets the player's battle HP
(gBattleMons[0].hp, ``0x02023BE4 + 0x28``, verified in notes/mgba-bridge.md) to 1 and a save
state is written to pytest's tmp dir (never committed). The run under test starts from that state,
so replay (from the same state) does not need the write.
"""
import os

import pytest

from game_brain import demo
from game_brain.runlog import iter_steps, replay

PLAYER_BATTLE_HP = 0x02023BE4 + 0x28   # gBattleMons[0].hp (u16)


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


def make_losing_state(path: str, max_steps: int = 4000) -> dict:
    """Boot -> first wild battle's action menu on Route 1 -> TEST-ONLY: player HP = 1 -> save state."""
    from game_brain.adapters import make_adapter
    from game_brain.arbiter.arbiter import Arbiter
    from game_brain.brain import make_brains

    a = make_adapter("mgba")
    o = a.reset()
    arb = Arbiter(make_brains("battle,path,rule"))
    last_map = None
    for step in range(max_steps):
        if o.ram.get("map_id") is not None:
            last_map = (o.ram["map_bank"], o.ram["map_id"])
        b = o.ram.get("battle") or {}
        if o.ram.get("in_battle") and last_map == (3, 19) and b.get("menu") == "action" and b.get("player"):
            break
        a.act(arb.step(o).executed)
        o = a.observe()
    else:
        raise AssertionError("no wild battle on Route 1")
    info = {"step": step, "frame": a.frame, "hp_before": a.ram.u16(PLAYER_BATTLE_HP)}
    a._core.memory.u16[PLAYER_BATTLE_HP] = 1          # TEST-ONLY RAM write (harness, not a brain)
    assert a.ram.u16(PLAYER_BATTLE_HP) == 1
    with open(path, "wb") as f:
        f.write(bytes(a._core.save_raw_state()))
    a.close()
    return info


def _battles(steps):
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
def test_real_whiteout_wakes_up_at_home_and_walks_back_to_viridian(tmp_path, monkeypatch):
    from game_brain.adapters import make_adapter

    state = str(tmp_path / "lose.state")              # tmp only, never committed
    info = make_losing_state(state)
    assert info["hp_before"] > 1
    monkeypatch.setenv("GAME_BRAIN_START_STATE", state)
    # Viridian at step 2103 (notes/nav.md "Whiteout"); after that the parcel errand starts
    s = demo.run("mgba", steps=2120, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    battles = _battles(steps)
    a, b = battles[0]
    assert a == 0
    last = [r["observation"]["ram"]["battle"] for r in steps[a:b + 1] if r["observation"]["ram"]["battle"]["player"]]
    assert last[0]["player"]["hp"] == 1 and last[-1]["player"]["hp"] == 0 and last[-1]["outcome"] == "lose"
    # whiteout: wake up in the player's house 1F (4/0) at (8,5), starter still in the party
    after = next(r["observation"]["ram"] for r in steps[b + 1:] if r["observation"]["ram"].get("player_x") is not None)
    assert (after["map_bank"], after["map_id"], after["player_x"], after["player_y"], after["party_count"]) == (4, 0, 8, 5, 1)
    # HP restored: the next battle starts at full HP
    a2, b2 = battles[1]
    first = next(r["observation"]["ram"]["battle"] for r in steps[a2:b2 + 1] if r["observation"]["ram"]["battle"]["player"])
    assert first["player"]["hp"] == first["player"]["max_hp"]
    # the AI goes back out and on to Viridian City
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    known = [m for m in maps if m[0] is not None]
    visits = [m for i, m in enumerate(known) if i == 0 or known[i - 1] != m]
    assert visits[:3] == [(4, 0), (3, 0), (3, 19)] and visits[-1] == (3, 1)
    assert all(r["decision"]["brain"] == "battle" for x, y in battles for r in steps[x:y + 1])
    done = {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}
    assert done["viridian_city"] and not done["viridian_mart"]
    assert all(r["decision"].get("milestones") for r in steps)
    assert replay(s["log"], make_adapter("mgba", start_state=state)) == []
