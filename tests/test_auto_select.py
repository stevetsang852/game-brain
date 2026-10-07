from game_brain.arbiter.select import select_order
from game_brain.brain.base import Brain
from game_brain.schema import Observation


class Named(Brain):
    def __init__(self, name):
        self.name = name

    def decide(self, obs):
        raise AssertionError("not called")


def names(obs):
    brains = [Named(n) for n in ("rule", "path", "llm", "battle", "rl")]
    return [b.name for b in select_order(brains, obs)]


def test_battle_asks_llm_then_battle():
    assert names(Observation(frame=0, ram={"battle": {"menu": 1}}))[:2] == ["llm", "battle"]


def test_verified_path_asks_path_before_rule():
    assert names(Observation(frame=0, ram={"map_id": 3}))[:2] == ["path", "rule"]


def test_unverified_probe_asks_llm_then_path():
    assert names(Observation(frame=0, ram={"probe": True}))[:2] == ["llm", "path"]


def test_rl_ready_asks_rl_outside_battle():
    assert names(Observation(frame=0, ram={"rl_ready": True}))[0] == "rl"
