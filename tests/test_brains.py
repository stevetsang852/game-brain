import pytest

from game_brain.adapters.mock import MockAdapter
from game_brain.brain import BrainUnavailable, LLMBrain, RandomBrain, RuleBrain, make_brain
from game_brain.schema import Action, Decision, Observation


def test_rule_brain_mashes_a_in_intro():
    act, dec = RuleBrain().decide(Observation(frame=0, ram={}))
    assert [p.button for p in act.presses] == ["A"]
    assert dec.brain == "rule" and "intro" in dec.plan


def test_rule_brain_presses_a_in_battle():
    obs = Observation(frame=0, ram={"player_x": 1, "player_y": 1, "in_battle": True})
    act, _ = RuleBrain().decide(obs)
    assert act.presses[0].button == "A"


def test_rule_brain_walks_then_handles_stuck():
    b = RuleBrain(pattern=("UP",), stuck_after=2)
    obs = Observation(frame=0, ram={"player_x": 0, "player_y": 0})
    buttons = [b.decide(obs)[0].presses[0].button for _ in range(4)]
    # walk, walk (position unchanged twice -> stuck) -> A to clear a text box / wall, then resume
    assert buttons == ["UP", "UP", "A", "UP"]


def test_rule_brain_gets_through_mock_intro_and_moves():
    env, brain = MockAdapter(intro_presses=5), RuleBrain()
    env.reset()
    positions = set()
    for _ in range(40):
        act, _ = brain.decide(env.observe())
        env.act(act)
        if env.observe().position:
            positions.add(env.observe().position)
    assert env.intro_left == 0
    assert len(positions) > 3


def test_random_brain_is_seeded_and_valid():
    obs = Observation(frame=0)
    a = [RandomBrain(seed=1).decide(obs)[0] for _ in range(1)]
    r1, r2 = RandomBrain(seed=1), RandomBrain(seed=1)
    s1 = [r1.decide(obs)[0] for _ in range(20)]
    s2 = [r2.decide(obs)[0] for _ in range(20)]
    assert s1 == s2 and a[0] == s1[0]
    assert all(isinstance(x, Action) and x.total_frames > 0 for x in s1)


def test_llm_brain_stub_makes_no_calls_and_is_unavailable(monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("LLMBrain must not open network connections")

    monkeypatch.setattr(socket, "create_connection", boom)
    with pytest.raises(BrainUnavailable):
        LLMBrain().decide(Observation(frame=0))


def test_make_brain():
    assert isinstance(make_brain("rule"), RuleBrain)
    with pytest.raises(ValueError):
        make_brain("nope")
