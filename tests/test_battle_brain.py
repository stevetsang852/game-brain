"""RuleBattleBrain + PokeAPI tables (no ROM needed)."""
import json

import pytest

from game_brain import data, demo
from game_brain.adapters import make_adapter
from game_brain.adapters.mock import MockBattleAdapter
from game_brain.arbiter.arbiter import Arbiter
from game_brain.brain import BrainUnavailable, PathBrain, RuleBrain, make_brain, make_brains
from game_brain.brain.battle import BattleAction, BattleCompiler, BattleState, RuleBattleBrain
from game_brain.brain.battle.compiler import CompileError, nav_button
from game_brain.brain.battle.estimate import estimate, hp_stat, stat
from game_brain.runlog import iter_steps, replay
from game_brain.schema import Decision, Mode, Observation, SchemaError, from_json, to_json

TACKLE, VINE_WHIP, GROWL, METRONOME, EMBER = 33, 22, 45, 118, 52
BULBASAUR, CHARMANDER, SQUIRTLE = 1, 4, 7


def mon(species=BULBASAUR, level=5, hp=20, max_hp=22, moves=((TACKLE, 35),), **kw):
    d = {"species": species, "level": level, "hp": hp, "max_hp": max_hp,
         "moves": [{"id": i, "pp": p} for i, p in moves]}
    d.update(kw)
    return d


def obs(menu="action", cursor=0, player=None, opponent=None, outcome=None, in_battle=True, frame=0):
    b = {"menu": menu, "cursor": cursor, "player": player, "opponent": opponent, "outcome": outcome}
    return Observation(frame=frame, ram={"in_battle": in_battle, "scene": "other", "battle": b})


P = mon()
O = mon(CHARMANDER, hp=20, max_hp=20, moves=((METRONOME, 10),))


# ------------------------------------------------------------------ data tables
def test_type_chart_and_effectiveness():
    assert data.effectiveness("water", ["fire"]) == 2.0
    assert data.effectiveness("grass", ["fire", "flying"]) == 0.25
    assert data.effectiveness("normal", ["ghost"]) == 0.0
    assert data.effectiveness("electric", ["water", "flying"]) == 4.0
    assert data.effectiveness("ghost", ["steel"]) == 0.5 and data.effectiveness("dark", ["steel"]) == 0.5  # Gen III
    assert len(data.types()) == 17   # no fairy in Gen III


def test_species_moves_and_fr_index():
    b = data.species(BULBASAUR)
    assert b["name"] == "bulbasaur" and b["types"] == ["grass", "poison"] and b["base"]["spa"] == 65
    assert data.species("clefairy")["types"] == ["normal"]           # Gen III: no fairy
    assert data.species("butterfree")["base"]["spa"] == 80          # pre-Gen VI value
    assert data.species_by_game_index(277)["name"] == "treecko"     # FireRed internal index != dex
    assert data.species_by_game_index(1)["name"] == "bulbasaur"
    t = data.move(TACKLE)
    assert (t["power"], t["accuracy"], t["type"], t["category"]) == (35, 95, "normal", "physical")
    assert data.move(VINE_WHIP)["pp"] == 10 and data.move(VINE_WHIP)["category"] == "special"  # type split
    assert data.move(GROWL)["category"] == "status"
    assert data.move("metronome") == data.move(METRONOME)
    assert data.move(9999) is None and data.species(9999) is None


# ------------------------------------------------------------------ estimates
def test_stat_and_damage_estimate():
    assert hp_stat(45, 5) == 20 and stat(49, 5) == 10  # IV 15, no EVs
    bs = BattleState.from_ram(obs(player=P, opponent=O).ram)
    e = estimate(bs.player, bs.opponent, TACKLE)
    assert e.reliable and e.effectiveness == 1.0 and 1 < e.expected < 10
    assert estimate(bs.player, bs.opponent, VINE_WHIP).effectiveness == 0.5
    assert estimate(bs.player, bs.opponent, GROWL).status
    m = estimate(bs.player, bs.opponent, METRONOME)
    assert not m.reliable and m.expected > 0
    s = estimate(bs.player, bs.opponent, None)   # struggle
    assert s.reliable and s.expected > 0
    # opponent HP only as a percentage
    o2 = dict(O, hp=None, hp_pct=50)
    bs2 = BattleState.from_ram(obs(player=P, opponent=o2).ram)
    assert estimate(bs2.player, bs2.opponent, TACKLE).fraction > e.fraction


# ------------------------------------------------------------------ choosing
def decide(brain, o):
    return brain.decide(o)


def test_picks_best_damage_and_super_effective():
    b = RuleBattleBrain()
    p = mon(moves=((GROWL, 40), (TACKLE, 35), (VINE_WHIP, 10)))
    act, d = decide(b, obs(player=p, opponent=mon(SQUIRTLE, hp=20, max_hp=20)))
    assert d.intent == d.chosen_option == "FIGHT:2"          # vine whip x2 + STAB
    assert [o["id"] for o in d.battle_options] == ["FIGHT:0", "FIGHT:1", "FIGHT:2"]
    assert "x2" in d.battle_options[2]["label"] and d.confidence > 0.6
    assert act.presses[0].button == "A"                       # FIGHT from cursor 0
    b = RuleBattleBrain()
    _, d = decide(b, obs(player=p, opponent=O))
    assert d.intent == "FIGHT:1"                              # vine whip resisted -> tackle


def test_metronome_only_is_acted_on_without_handoff():
    b = RuleBattleBrain()
    b.set_mode(Mode.ASSIST)
    _, d = decide(b, obs(player=mon(moves=((METRONOME, 10),)), opponent=O))
    assert d.intent == "FIGHT:0" and d.confidence == 0.5 and not d.handoff
    assert d.reason.startswith("only legal option")


def test_pp_zero_moves_skipped_and_struggle():
    b = RuleBattleBrain()
    _, d = decide(b, obs(player=mon(moves=((VINE_WHIP, 0), (TACKLE, 3))), opponent=mon(SQUIRTLE)))
    assert [o["id"] for o in d.battle_options] == ["FIGHT:1"]
    b = RuleBattleBrain()
    act, d = decide(b, obs(player=mon(moves=((TACKLE, 0), (GROWL, 0))), opponent=O))
    assert d.intent == "FIGHT" and "struggle" in d.battle_options[0]["label"]
    assert act.presses[0].button == "A"   # FIGHT on the action menu; the game picks Struggle


def test_run_only_when_allowed():
    _, d = decide(RuleBattleBrain(), obs(player=P, opponent=O))
    assert "RUN" not in [o["id"] for o in d.battle_options]
    act, d = decide(RuleBattleBrain(allow_run=True), obs(player=P, opponent=O))
    assert d.intent == "RUN" and act.presses[0].button == "DOWN"   # 0 -> 3: DOWN, RIGHT, A


def close_call():
    # tackle vs. a move of similar estimated damage -> low confidence
    return obs(player=mon(moves=((TACKLE, 35), (33, 35))), opponent=O)


def test_low_confidence_hands_off_in_assist_and_acts_in_auto():
    b = RuleBattleBrain()
    b.set_mode(Mode.ASSIST)
    for i in range(3):
        act, d = decide(b, close_call())
        assert d.handoff is True and d.confidence < 0.6 and act.presses[0].button == "NONE"
        assert "handoff" in d.reason
    b2 = RuleBattleBrain(handoff_steps=2)
    b2.set_mode(Mode.ASSIST)
    decide(b2, close_call()), decide(b2, close_call())
    act, d = decide(b2, close_call())               # nobody came: act anyway
    assert d.handoff is False and act.presses[0].button == "A"
    b3 = RuleBattleBrain()
    b3.set_mode(Mode.AUTO)
    act, d = decide(b3, close_call())
    assert d.handoff is False and "no human" in d.reason and act.presses[0].button == "A"


def test_confidence_threshold_parameter():
    b = RuleBattleBrain(confidence_threshold=0.4)
    b.set_mode(Mode.ASSIST)
    _, d = decide(b, close_call())
    assert d.handoff is None and d.confidence >= 0.4
    with pytest.raises(ValueError):
        RuleBattleBrain(confidence_threshold=1.5)
    bs = make_brains("battle,path,rule", battle_confidence=0.3)
    assert [x.name for x in bs] == ["battle", "path", "rule"] and bs[0].confidence_threshold == 0.3
    assert make_brain("battle").confidence_threshold == 0.6


# ------------------------------------------------------------------ deferring / waiting
def test_defers_outside_battle_and_without_battle_ram():
    b = RuleBattleBrain()
    with pytest.raises(BrainUnavailable):
        b.decide(Observation(frame=0, ram={"in_battle": False, "player_x": 1, "player_y": 1}))
    with pytest.raises(BrainUnavailable):
        b.decide(Observation(frame=0, ram={}))   # in_battle unknown
    with pytest.raises(BrainUnavailable, match="ram\\['battle'\\]"):
        b.decide(Observation(frame=0, ram={"in_battle": True, "scene": "other"}))


def test_waits_for_mon_data_and_advances_text():
    b = RuleBattleBrain()
    act, d = b.decide(obs(menu="other", cursor=None))
    assert act.presses[0].button == "NONE" and "not known yet" in d.reason
    act, d = b.decide(obs(menu="other", cursor=None, player=P, opponent=O))
    assert act.presses[0].button == "B"
    b2 = RuleBattleBrain(max_wait_unready=2)
    for _ in range(2):
        assert b2.decide(obs(menu="other", cursor=None))[0].presses[0].button == "NONE"
    assert b2.decide(obs(menu="other", cursor=None))[0].presses[0].button == "B"


def test_stale_outcome_and_mon_data_from_previous_battle_are_ignored():
    """Backend: ``outcome`` and the mon data are not cleared after a battle."""
    b = RuleBattleBrain()
    won = dict(O, hp=0)            # previous battle: opponent fainted
    # a new battle starts: still the old values
    for opp in (won, O):           # inconsistent or not: never trusted before an input menu
        act, d = b.decide(obs(menu="other", cursor=None, player=P, opponent=opp, outcome="win"))
        assert act.presses[0].button == "B" and "not trusted" in d.reason and d.intent is None
    # the action menu opens with fresh data but outcome still says "win": fight anyway
    act, d = b.decide(obs(menu="action", cursor=0, player=P, opponent=O, outcome="win"))
    assert d.intent == "FIGHT:0" and act.presses[0].button == "A"
    act, d = b.decide(obs(menu="move", cursor=0, player=P, opponent=O, outcome="win"))
    assert d.intent == "FIGHT:0" and act.presses[0].button == "A"
    # leaving the battle resets trust
    with pytest.raises(BrainUnavailable):
        b.decide(obs(in_battle=False))
    act, d = b.decide(obs(menu="action", cursor=0, player=P, opponent=won, outcome="win"))
    assert act.presses[0].button == "NONE" and d.intent is None   # inconsistent (hp 0) -> wait
    act, d = b.decide(obs(menu="other", cursor=None, player=P, opponent=won, outcome="win"))
    assert d.intent is None


def test_text_in_battle_is_advanced_with_b_and_outcome_reported():
    b = RuleBattleBrain()
    b.decide(obs(player=P, opponent=O))              # trusted now: FIGHT -> A
    b.decide(obs(menu="move", player=P, opponent=O))  # move 0 -> A (committed)
    act, d = b.decide(obs(menu="other", cursor=None, player=P, opponent=dict(O, hp=0), outcome="win"))
    assert act.presses[0].button == "B" and "win" in d.plan and d.battle["outcome"] == "win"


# ------------------------------------------------------------------ compiler / navigation
@pytest.mark.parametrize("cursor", range(4))
@pytest.mark.parametrize("target", range(4))
def test_nav_button_reaches_every_target(cursor, target):
    c, n = cursor, 0
    while (btn := nav_button(c, target)) is not None:
        c = {"UP": c - 2, "DOWN": c + 2, "LEFT": c - 1, "RIGHT": c + 1}[btn]
        assert 0 <= c <= 3
        n += 1
    assert c == target and n <= 2


def run_compiler(intent, menus):
    """Drive the compiler over a scripted 2x2 menu model (no wrap) and return the presses."""
    comp = BattleCompiler(intent)
    menu, cursor = menus
    cur = {"action": cursor if menu == "action" else 0, "move": cursor if menu == "move" else 0}
    out = []
    for _ in range(12):
        if comp.done:
            break
        report = cur[menu] if menus[1] is not None else None
        act, _ = comp.step(BattleState.from_ram(obs(menu=menu, cursor=report, player=P, opponent=O).ram))
        btn = act.presses[0].button
        out.append(btn)
        r, c = divmod(cur[menu], 2)
        if btn == "UP":
            r = 0
        elif btn == "DOWN":
            r = 1
        elif btn == "LEFT":
            c = 0
        elif btn == "RIGHT":
            c = 1
        cur[menu] = r * 2 + c
        if btn == "A" and menu == "action" and cur["action"] == 0 and intent.kind == "FIGHT":
            menu = "move"
        elif btn == "B" and menu == "move":
            menu = "action"
    return out, cur


@pytest.mark.parametrize("start", range(4))
@pytest.mark.parametrize("slot", range(4))
def test_compiler_fight_from_any_cursor(start, slot):
    presses, cur = run_compiler(BattleAction("FIGHT", slot=slot), ("action", start))
    assert presses[-1] == "A" and cur["move"] == slot
    presses, cur = run_compiler(BattleAction("FIGHT", slot=slot), ("move", start))
    assert presses[-1] == "A" and cur["move"] == slot and "B" not in presses


@pytest.mark.parametrize("start", range(4))
def test_compiler_run_from_any_cursor(start):
    presses, cur = run_compiler(BattleAction("RUN"), ("action", start))
    assert presses[-1] == "A" and cur["action"] == 3
    presses, cur = run_compiler(BattleAction("RUN"), ("move", start))
    assert presses[0] == "B" and presses[-1] == "A" and cur["action"] == 3


def test_compiler_homes_when_cursor_unknown():
    comp = BattleCompiler(BattleAction("FIGHT", slot=3))
    blind = BattleState.from_ram(obs(menu="move", cursor=None, player=P, opponent=O).ram)
    seq = [comp.step(blind)[0].presses[0].button for _ in range(5)]
    assert seq == ["LEFT", "UP", "DOWN", "RIGHT", "A"] and comp.done


def test_compiler_rejects_switch_and_item():
    for kind in ("SWITCH", "ITEM"):
        with pytest.raises(CompileError):
            BattleCompiler(BattleAction(kind, slot=1) if kind == "SWITCH" else BattleAction(kind, item_id=1))
    assert BattleAction("FIGHT", slot=2).id == "FIGHT:2" and BattleAction("RUN").id == "RUN"


# ------------------------------------------------------------------ schema
def test_decision_battle_fields_roundtrip_and_validate():
    d = Decision(brain="battle", plan="p", reason="r", intent="FIGHT:0", battle={"menu": "action"},
                 battle_options=[{"id": "FIGHT:0", "label": "tackle", "score": 0.3}],
                 chosen_option="FIGHT:0", confidence=0.123456, handoff=False)
    back = from_json(to_json(d))
    assert back == d and back.confidence == pytest.approx(0.1235, abs=1e-4)
    plain = json.loads(to_json(Decision(brain="rule", plan="p")))
    for k in ("intent", "battle", "battle_options", "chosen_option", "confidence", "handoff"):
        assert k not in json.dumps(plain)
    with pytest.raises(SchemaError):
        Decision(brain="battle", plan="p", confidence=1.5)
    with pytest.raises(SchemaError):
        Decision(brain="battle", plan="p", battle_options=[{"label": "no id"}])


# ------------------------------------------------------------------ end to end
def test_mock_battle_end_to_end_and_replay(tmp_path):
    s = demo.run("mock-battle", steps=80, mode="auto", brains="battle,path,rule", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    inb = [r for r in steps if r["observation"]["ram"].get("in_battle")]
    assert inb and all(r["decision"]["brain"] == "battle" for r in inb)
    assert any(r["decision"].get("intent") == "FIGHT:0" for r in inb)
    outs = {r["observation"]["ram"]["battle"]["outcome"] for r in inb}
    assert outs & {"win", "lose"}
    after = steps[len(inb)]["observation"]["ram"]
    assert after["in_battle"] is False and (after["player_x"], after["player_y"]) == (7, 8)
    assert steps[len(inb)]["decision"]["brain"] in ("path", "rule")
    assert replay(s["log"], make_adapter("mock-battle")) == []


def test_mock_battle_respects_pp_and_run_rules():
    a = MockBattleAdapter(player={"species": 1, "level": 5, "max_hp": 22,
                                  "moves": [{"id": TACKLE, "pp": 0}, {"id": VINE_WHIP, "pp": 5}]},
                          opponent={"species": SQUIRTLE, "level": 5, "max_hp": 20, "moves": [{"id": TACKLE, "pp": 30}]})
    b = RuleBattleBrain()
    o = a.reset()
    intents = set()
    for _ in range(200):
        if not o.ram.get("in_battle"):
            break
        act, d = b.decide(o)
        intents.add(d.intent)
        a.act(act)
        o = a.observe()
    assert not o.ram.get("in_battle") and "FIGHT:0" not in intents and "FIGHT:1" in intents


def test_arbiter_routes_battle_path_rule():
    arb = Arbiter([RuleBattleBrain(), PathBrain(), RuleBrain()], mode="assist")
    r = arb.step(obs(player=P, opponent=O))
    assert r.decision.brain == "battle" and r.decision.milestones   # PathBrain context via observe()
    r = arb.step(Observation(frame=1, ram={"in_battle": True, "scene": "other"}))   # no ram["battle"]
    assert r.decision.brain == "rule"
    r = arb.step(Observation(frame=2, ram={"in_battle": False, "scene": "other"}))  # transition
    assert r.decision.brain == "rule"
    arb.brains[0].set_mode(Mode.AUTO)
    r = arb.step(close_call())
    assert r.decision.brain == "battle" and r.decision.handoff is True   # arbiter passed ASSIST


def test_firered_battle_menu_detects_rival_and_wild_controllers():
    """Rival battle (Oak's lab) and wild battles use different player controllers (M3)."""
    from game_brain.adapters.gba_mgba import firered_battle as fb
    from game_brain.adapters.gba_mgba.firered import FireRedRam

    for ctrl, menu in ((fb.CTRL_CHOOSE_ACTION, "action"), (fb.CTRL_CHOOSE_MOVE, "move"),
                       (fb.CTRL_CHOOSE_ACTION_WILD, "action"), (fb.CTRL_CHOOSE_MOVE_WILD, "move"),
                       (0x0802E311, "other")):
        mem = {fb.G_BATTLER_CONTROLLER_FUNCS: ctrl, fb.G_ACTION_SELECTION_CURSOR: 3, fb.G_MOVE_SELECTION_CURSOR: 1}
        ram = FireRedRam(lambda a: mem.get(a, 0) & 0xFF, lambda a: mem.get(a, 0) & 0xFFFF, lambda a: mem.get(a, 0))
        b = fb.read_battle(ram)
        assert b["menu"] == menu
        assert b["cursor"] == {"action": 3, "move": 1, "other": None}[menu]
