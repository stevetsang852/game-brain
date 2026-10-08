from game_brain.brain.rule import RuleBrain
from game_brain.schema import Observation


def test_rule_brain_reports_progress_reward():
    brain = RuleBrain()
    _, decision = brain.decide(Observation(frame=1, ram={"player_x": 1, "player_y": 1, "map_bank": 3, "map_id": 0}))
    assert "reward" in decision.reason


def test_signal_diverts_a_stuck_repeat():
    from game_brain.rl.signal import ProgressSignal
    signal = ProgressSignal()
    assert signal.divert("UP", True) == "RIGHT"
    assert signal.divert("UP", False) == "UP"
