"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): --starter picks that ball in Oak's
lab (``starter.picked`` set) and the rival takes the counter starter (seen in the rival battle's RAM species)."""
import os

import pytest

from game_brain import demo
from game_brain.adapters import make_adapter
from game_brain.runlog import iter_steps, replay

SPECIES = {"bulbasaur": 1, "charmander": 4, "squirtle": 7}          # national dex = FireRed species ids
RIVAL = {"bulbasaur": "charmander", "charmander": "squirtle", "squirtle": "bulbasaur"}


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
@pytest.mark.parametrize("starter", ["charmander", "squirtle"])    # bulbasaur: test_battle_brain_real.py
def test_real_starter_choice_and_rival_counter(tmp_path, starter):
    s = demo.run("mgba", steps=1400, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path), quiet=True,
                 starter=starter)
    assert s["starter"] == {"requested": starter, "picked": starter, "seed": 0}
    steps = list(iter_steps(s["log"]))
    battle = next(r["observation"]["ram"]["battle"] for r in steps
                  if r["observation"]["ram"].get("in_battle") is True
                  and (r["observation"]["ram"].get("battle") or {}).get("opponent"))
    assert battle["player"]["species"] == SPECIES[starter]
    assert battle["opponent"]["species"] == SPECIES[RIVAL[starter]]
    assert replay(s["log"], make_adapter("mgba")) == []
