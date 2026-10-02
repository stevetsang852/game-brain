"""Battle panel: the page reads the battle fields and they survive the live envelope."""

import json
from pathlib import Path

from game_brain.schema import Decision, to_envelope

HTML = (Path(__file__).parents[1] / "game_brain/dashboard/static/index.html").read_text(encoding="utf-8")


def test_page_has_battle_panel_and_reads_battle_fields():
    for needle in ('id="battleSection"', 'id="monPlayer"', 'id="monOpponent"', 'id="handoff"', 'id="bopts"',
                   'id="bConf"', "function renderBattle", "ram.battle", "d.battle_options", "d.chosen_option",
                   "d.confidence", "d.handoff", "hp_pct", "max_hp"):
        assert needle in HTML, needle
    # Panel must come before the mini-map so it is visible without scrolling during a battle.
    assert HTML.index('id="battleSection"') < HTML.index('id="minimap"')


def test_battle_decision_fields_reach_the_page():
    d = Decision(brain="battle", plan="FIGHT:0: metronome (PP 39)", reason="only legal option",
                 intent="FIGHT:0", battle={"menu": "action", "cursor": 0, "outcome": None,
                                           "player": {"species": 1, "name": "bulbasaur", "level": 5, "hp": 12, "max_hp": 22,
                                                      "moves": [{"id": 118, "name": "metronome", "pp": 39}]},
                                           "opponent": None},
                 battle_options=[{"id": "FIGHT:0", "label": "metronome (PP 39)", "score": 0.4}],
                 chosen_option="FIGHT:0", confidence=0.5, handoff=True)
    p = json.loads(json.dumps(to_envelope(d, 7)))["payload"]
    assert p["battle"]["player"]["name"] == "bulbasaur" and p["battle_options"][0]["id"] == "FIGHT:0"
    assert p["chosen_option"] == "FIGHT:0" and p["confidence"] == 0.5 and p["handoff"] is True and p["intent"] == "FIGHT:0"
