import json
import sqlite3
from pathlib import Path

import pytest

from game_brain.brain import BrainUnavailable, LLMBrain
from game_brain.rl.ppo import train_short_ppo
from game_brain.schema import Observation


def test_llm_brain_is_unavailable():
    with pytest.raises(BrainUnavailable):
        LLMBrain().decide(Observation(frame=0, ram={}))


def test_action_weight_baseline_reads_logged_transitions(tmp_path: Path):
    db = tmp_path / "experience.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE transitions (namespace TEXT, run_id INT, step_id INT, state_before TEXT, state_after TEXT, executed_action TEXT)")
    before = json.dumps({"ram": {"player_x": 1, "player_y": 1}})
    after = json.dumps({"ram": {"player_x": 1, "player_y": 0, "map_bank": 3, "map_id": 1}})
    action = json.dumps({"presses": [{"button": "UP", "frames": 8}]})
    conn.execute("INSERT INTO transitions VALUES (?,?,?,?,?,?)", ("gba_mgba/test", 1, 1, before, after, action))
    conn.commit()
    conn.close()
    result = train_short_ppo(tmp_path, "gba_mgba/test", epochs=1)
    assert result["updates"] == 1
    assert "UP" in result["logits"]
