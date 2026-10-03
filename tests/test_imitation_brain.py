import json
import sqlite3

import pytest

from game_brain.brain import BrainUnavailable, ImitationBrain, make_brains
from game_brain.learning import train_imitation
from game_brain.schema import Observation


NAMESPACE = "mock:synthetic"


def _state(x=2, y=3):
    return Observation(frame=0, game="MOCK", ram={
        "scene": "overworld", "in_battle": False, "map_bank": 4, "map_id": 1,
        "player_x": x, "player_y": y, "facing": "UP", "party_count": 1,
    }).summary()


def _action(button):
    return {"type": "action", "v": 1, "source": "manual",
            "presses": [{"button": button, "frames": 2, "release_frames": 4}]}


def _database(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE transitions (namespace TEXT, actor TEXT, state_before TEXT, "
                   "executed_action TEXT, run_id TEXT, step_id INTEGER)")
        db.executemany("INSERT INTO transitions VALUES (?,?,?,?,?,?)", [
            (NAMESPACE, "human", json.dumps(_state()), json.dumps(_action("RIGHT")), "r1", 1),
            (NAMESPACE, "human", json.dumps(_state()), json.dumps(_action("RIGHT")), "r1", 2),
            (NAMESPACE, "human", json.dumps(_state()), json.dumps(_action("LEFT")), "r1", 3),
            (NAMESPACE, "brain", json.dumps(_state(8, 8)), json.dumps(_action("A")), "r1", 4),
            ("other:rom", "human", json.dumps(_state(9, 9)), json.dumps(_action("B")), "r2", 1),
        ])


def test_train_uses_only_human_actions_and_imitation_votes(tmp_path):
    _database(tmp_path / "experience.sqlite3")
    model_path = tmp_path / "imitation.json"

    result = train_imitation(tmp_path, NAMESPACE, model_path)

    assert result == {"namespace": NAMESPACE, "samples": 3, "states": 1, "actions": 2,
                      "model": str(model_path)}
    brain = ImitationBrain(str(model_path), namespace=NAMESPACE)
    action, decision = brain.decide(Observation.from_dict(_state()))
    assert action.source == "brain:imitation"
    assert [press.button for press in action.presses] == ["RIGHT"]
    assert decision.confidence == pytest.approx(0.6667)


def test_imitation_falls_back_for_unseen_or_ambiguous_states(tmp_path):
    _database(tmp_path / "experience.sqlite3")
    model_path = tmp_path / "imitation.json"
    train_imitation(tmp_path, NAMESPACE, model_path)

    brain = ImitationBrain(str(model_path), namespace=NAMESPACE)
    with pytest.raises(BrainUnavailable, match="no human demonstration"):
        brain.decide(Observation.from_dict(_state(x=3)))
    strict = ImitationBrain(str(model_path), namespace=NAMESPACE, confidence_threshold=0.8)
    with pytest.raises(BrainUnavailable, match="confidence"):
        strict.decide(Observation.from_dict(_state()))


def test_imitation_model_is_bound_to_its_adapter_rom(tmp_path):
    _database(tmp_path / "experience.sqlite3")
    model_path = tmp_path / "imitation.json"
    train_imitation(tmp_path, NAMESPACE, model_path)

    with pytest.raises(ValueError, match="namespace does not match"):
        ImitationBrain(str(model_path), namespace="mock:another-rom")


def test_imitation_brain_can_be_selected_in_the_priority_chain(tmp_path):
    _database(tmp_path / "experience.sqlite3")
    model_path = tmp_path / "imitation.json"
    train_imitation(tmp_path, NAMESPACE, model_path)

    brains = make_brains("imitation,rule", imitation_model=str(model_path), namespace=NAMESPACE)

    assert [brain.name for brain in brains] == ["imitation", "rule"]
    action, decision = brains[0].decide(Observation.from_dict(_state()))
    assert action.presses[0].button == "RIGHT" and decision.brain == "imitation"


def test_training_requires_human_examples(tmp_path):
    _database(tmp_path / "experience.sqlite3")
    with pytest.raises(ValueError, match="no human action examples"):
        train_imitation(tmp_path, "unknown:namespace", tmp_path / "model.json")
