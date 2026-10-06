from game_brain.brain.probe import ProbeBrain
from game_brain.schema import Observation


def test_probe_brain_records_new_map_and_npc():
    brain = ProbeBrain()
    first = Observation(frame=1, ram={"map_bank": 3, "map_id": 0, "npcs": []})
    brain.observe(first)
    obs = Observation(frame=2, ram={"map_bank": 3, "map_id": 19, "npcs": [{"local_id": 1}]})
    _, decision = brain.decide(obs)
    assert decision.goal.startswith("Probe NPC")
    assert any(goal["status"] == "unverified" for goal in brain.goals)
    assert "goals.json" not in decision.reason
