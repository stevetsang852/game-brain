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


def test_dashboard_actions_rejected_in_auto_and_shadow():
    for mode in (Mode.AUTO, Mode.SHADOW):
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


def test_assist_without_human_input_behaves_like_auto():
    r = Arbiter([RuleBrain()], mode="assist").step(OBS)
    assert r.decision.brain == "rule" and r.decision.actor == "brain" and r.decision.executed
    assert r.executed is r.proposed


def test_assist_human_action_preempts_brain_then_brain_resumes():
    class Counting(RuleBrain):
        calls = 0

        def decide(self, obs):
            Counting.calls += 1
            return super().decide(obs)

    arb = Arbiter([Counting()], mode=Mode.ASSIST)
    assert arb.step(OBS).decision.actor == "brain" and Counting.calls == 1
    assert arb.submit_manual(Action.tap("B", source="manual"))
    assert arb.submit_manual(Action([ButtonPress("UP", 8)], source="manual"))
    assert arb.pending_manual == 2

    r1, r2 = arb.step(OBS), arb.step(OBS)
    assert [r.executed.presses[0].button for r in (r1, r2)] == ["B", "UP"]
    for r in (r1, r2):
        assert r.decision.actor == "human" and r.decision.brain == "human" and r.decision.executed
        assert r.decision.mode == "assist" and r.proposed is None
        assert r.executed.source == "manual"
    assert Counting.calls == 1  # brain not consulted while human actions are queued

    r3 = arb.step(OBS)  # queue drained -> brain resumes
    assert r3.decision.actor == "brain" and r3.decision.brain == "rule" and Counting.calls == 2
    assert arb.pending_manual == 0


def test_assist_still_rejects_brain_sourced_actions():
    arb = Arbiter([RuleBrain()], mode="assist")
    assert not arb.submit_manual(Action.tap("A", source="brain:rule"))
    assert arb.pending_manual == 0


def test_assist_queue_survives_switch_to_manual_but_not_to_auto():
    arb = Arbiter([RuleBrain()], mode="assist")
    arb.submit_manual(Action.tap("B", source="manual"))
    arb.apply_mode(Mode.MANUAL)
    assert arb.pending_manual == 1
    arb.apply_mode(Mode.AUTO)
    assert arb.pending_manual == 0


def test_actor_field_for_each_mode():
    assert Arbiter([RuleBrain()], mode="auto").step(OBS).decision.actor == "brain"
    assert Arbiter([RuleBrain()], mode="shadow").step(OBS).decision.actor == "brain"
    assert Arbiter([RuleBrain()], mode="manual").step(OBS).decision.actor == "none"
    assert Arbiter([LLMBrain()], mode="auto").step(OBS).decision.actor == "none"
