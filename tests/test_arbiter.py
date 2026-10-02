from game_brain.arbiter import Arbiter
from game_brain.brain import LLMBrain, RandomBrain, RuleBrain
from game_brain.brain.base import Brain
from game_brain.schema import Action, ButtonPress, Mode, ModeCommand, Observation

OBS = Observation(frame=0, ram={})


def test_auto_executes_brain_action():
    r = Arbiter([RuleBrain()], mode="auto").step(OBS)
    assert r.executed is r.proposed and r.decision.executed and r.decision.brain == "rule"


def test_shadow_logs_but_does_not_execute():
    r = Arbiter([RuleBrain()], mode=Mode.SHADOW).step(OBS)
    assert r.proposed.presses[0].button == "A"
    assert r.decision.executed is False and r.decision.mode == "shadow"
    # time advances with an idle wait of the same length, but no buttons are pressed
    assert [p.button for p in r.executed.presses] == ["NONE"]
    assert r.executed.total_frames == r.proposed.total_frames


def test_manual_never_consults_brains_and_runs_manual_actions():
    class Exploding(Brain):
        name = "boom"

        def decide(self, obs):
            raise AssertionError("brain consulted in manual mode")

    arb = Arbiter([Exploding()], mode="manual")
    r = arb.step(OBS)
    assert r.proposed is None and [p.button for p in r.executed.presses] == ["NONE"]
    assert arb.submit_manual(Action([ButtonPress("START", 2)], source="manual"))
    r = arb.step(OBS)
    assert r.executed.presses[0].button == "START" and r.decision.brain == "human"


def test_manual_rejects_brain_sourced_actions():
    arb = Arbiter([RuleBrain()], mode="manual")
    assert not arb.submit_manual(Action.tap("A", source="brain:rule"))
    assert arb.pending_manual == 0 and arb.rejected


def test_dashboard_actions_rejected_outside_manual():
    for mode in (Mode.AUTO, Mode.ASSIST, Mode.SHADOW):
        arb = Arbiter([RuleBrain()], mode=mode)
        assert arb.submit_manual(Action.tap("B", source="manual")) is False
        assert arb.pending_manual == 0
        assert "rejected" in arb.rejected[-1]


def test_switching_out_of_manual_drops_queued_actions():
    arb = Arbiter([RuleBrain()], mode="manual")
    arb.submit_manual(Action.tap("B", source="manual"))
    arb.apply_mode(ModeCommand(Mode.AUTO, issued_by="dashboard"))
    assert arb.pending_manual == 0
    assert arb.step(OBS).decision.brain == "rule"
    assert [c.mode for c in arb.mode_history] == [Mode.MANUAL, Mode.AUTO]


def test_fallback_when_llm_unavailable():
    r = Arbiter([LLMBrain(), RandomBrain(seed=3)], mode="auto").step(OBS)
    assert r.decision.brain == "random"
    assert any("llm unavailable" in n for n in r.notes)


def test_all_brains_unavailable_idles():
    r = Arbiter([LLMBrain()], mode="auto").step(OBS)
    assert r.decision.brain == "none" and r.executed.presses[0].button == "NONE"
