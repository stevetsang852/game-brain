from game_brain.adapters.mock.house import MockHouseAdapter
from game_brain.rl.env import FireRedEnv
from game_brain.rl.short import run_short


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
