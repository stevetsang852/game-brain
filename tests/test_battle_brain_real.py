"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): boot -> starter -> rival battle."""
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


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
def test_real_firered_rival_battle(tmp_path):
    """battle,path,rule from boot: the rival stops you at (7,8), RuleBattleBrain fights the whole
    battle (both starters only know METRONOME; the emulator is deterministic, so the result is
    the same every run), then you are back in the lab at (7,8) and PathBrain idles."""
    from game_brain.adapters import make_adapter

    s = demo.run("mgba", steps=2600, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    inb = [i for i, r in enumerate(steps) if r["observation"]["ram"].get("in_battle") is True]
    assert inb and inb == list(range(inb[0], inb[-1] + 1))          # one battle
    battle = [steps[i] for i in inb]
    assert Counter(r["decision"]["brain"] for r in battle) == {"battle": len(battle)}
    assert all(r["decision"]["intent"] == "FIGHT:0" for r in battle if r["decision"].get("battle_options"))
    outcome = next(r["observation"]["ram"]["battle"]["outcome"] for r in reversed(battle))
    assert outcome in ("win", "lose")
    after = next(r["observation"]["ram"] for r in steps[inb[-1] + 1:] if r["observation"]["ram"].get("player_x") is not None)
    assert (after["map_bank"], after["map_id"], after["player_x"], after["player_y"], after["party_count"]) == (4, 3, 7, 8, 1)
    done = {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}
    assert done["rival_battle"] and done["rival_battle_over"] and not done["route_1"]
    assert all(r["decision"].get("milestones") or r.get("milestones_same") for r in steps)
    assert replay(s["log"], make_adapter("mgba")) == []
