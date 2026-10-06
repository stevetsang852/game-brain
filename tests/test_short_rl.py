from pathlib import Path

import pytest

from game_brain import savestate
from game_brain.adapters.mock.house import MockHouseAdapter
from game_brain.rl.anti_loop import AntiLoop
from game_brain.rl.env import FireRedEnv
from game_brain.rl.short import run_short
from game_brain.schema import Action


@pytest.mark.parametrize("extension", [".json", ".state"])
def test_env_reset_restores_savestate_frame_and_state(tmp_path, extension):
    adapter = MockHouseAdapter()
    adapter.reset()
    adapter.act(Action.wait(12))
    manager = savestate.SaveManager(tmp_path / "saves", adapter, [], "test", every=0)
    side = manager.save(steps_done=3, milestones=[], reason="test")
    save_path = Path(side["_path"]).with_suffix(extension)

    restored = MockHouseAdapter()
    obs = FireRedEnv(restored).reset(str(save_path))

    assert obs.frame == side["frame"] == 12
    assert obs.ram == adapter.observe().ram
    assert restored.frame == 12


def test_env_reset_rejects_a_savestate_from_another_adapter(tmp_path):
    adapter = MockHouseAdapter()
    adapter.reset()
    manager = savestate.SaveManager(tmp_path / "saves", adapter, [], "test", every=0)
    side = manager.save(steps_done=0, milestones=[], reason="test")

    class OtherAdapter(MockHouseAdapter):
        name = "other"

    with pytest.raises(ValueError, match="save is for adapter"):
        FireRedEnv(OtherAdapter()).reset(side["_path"])


def test_anti_loop_positions_include_map_identity():
    loop = AntiLoop()
    positions = [
        (0, 0, 0), (0, 1, 0), (0, 2, 0), (0, 0, 0),
        (1, 0, 0), (1, 1, 0), (1, 2, 0), (1, 0, 0),
    ]
    buttons = ("UP", "DOWN", "LEFT", "RIGHT") * 2

    penalties = [loop.penalty(position, button) for position, button in zip(positions, buttons)]

    # Coordinates repeat, but they are distinct cells on the two maps.
    assert penalties[-1] == 0


def test_env_step_returns_progress_parts():
    env = FireRedEnv(MockHouseAdapter(), max_steps=5)
    env.reset()
    obs, reward, done, truncated, info = env.step("A")
    assert obs.frame > 0
    assert "reward" in info
    assert done is False
    assert truncated is False
    assert isinstance(reward, float)


def test_short_run_reports_stage():
    result = run_short(MockHouseAdapter(), stage="starter", max_steps=30)
    assert result["stage"] == "starter"
    assert result["steps"] <= 30
