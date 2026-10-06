"""FireRedEnv.reset() from a start snapshot (bytes / .state / #28 sidecar), determinism,
the optional Gymnasium wrapper, and real-ROM variants (skipped without ROM + bindings)."""

import json
import os
from pathlib import Path

import pytest

from game_brain.adapters.mock.adapter import MockAdapter
from game_brain.adapters.mock.house import MockHouseAdapter
from game_brain.rl.env import BUTTONS, FireRedEnv, load_start
from game_brain.savestate import SaveManager, load_sidecar

WALK = ["A"] * 6 + ["LEFT", "DOWN", "DOWN", "RIGHT", "UP"] * 6


def _has_rom() -> bool:
    if not os.path.isfile(os.environ.get("GAME_BRAIN_ROM", "")):
        return False
    try:
        import mgba.core  # noqa: F401
    except ImportError:
        return False
    return True


real = pytest.mark.skipif(not _has_rom(), reason="needs $GAME_BRAIN_ROM and mGBA bindings")


def _advance(adapter, buttons):
    env = FireRedEnv(adapter)
    env.reset()
    for b in buttons:
        env.step(b)
    return adapter


def _episode(env, buttons, start=None):
    obs = env.reset(start)
    out = [(obs.frame, obs.ram, 0.0, None)]
    for b in buttons:
        o, r, done, trunc, parts = env.step(b)
        out.append((o.frame, o.ram, r, parts))
    return out


def _sidecar(tmp_path, adapter, step=12):
    sm = SaveManager(tmp_path / "saves", adapter, ["test"], "run-test", every=0, on_milestone=False)
    return load_sidecar(sm.save(step, None, reason="final")["_path"])


def test_reset_from_bytes_restores_state():
    a = _advance(MockHouseAdapter(), WALK[:10])
    snap, ram = a.save_state(), a.observe().ram
    env = FireRedEnv(MockHouseAdapter())
    obs = env.reset(snap)
    assert obs.ram == ram and obs.frame == 0


def test_reset_from_state_path_is_the_regression(tmp_path):
    """Before: reset('<path>') passed the str to load_state(bytes) -> AttributeError."""
    a = _advance(MockHouseAdapter(), WALK[:10])
    path = tmp_path / "start.state"
    path.write_bytes(a.save_state())
    env = FireRedEnv(MockHouseAdapter())
    assert env.reset(str(path)).ram == a.observe().ram
    assert env.reset(path).ram == a.observe().ram


def test_reset_from_sidecar_restores_frame_and_checks_sha1(tmp_path):
    a = _advance(MockHouseAdapter(), WALK[:10])
    side = _sidecar(tmp_path, a)
    env = FireRedEnv(MockHouseAdapter())
    obs = env.reset(side["_path"])
    assert obs.ram == a.observe().ram
    assert obs.frame == side["frame"] == a.frame > 0
    # a .state path next to its sidecar uses the sidecar too (frame comes along)
    assert FireRedEnv(MockHouseAdapter()).reset(side["_state_path"]).frame == side["frame"]
    Path(side["_state_path"]).write_bytes(b"{}")
    with pytest.raises(ValueError, match="sha1"):
        FireRedEnv(MockHouseAdapter()).reset(side["_path"])


def test_start_snapshot_is_read_once(tmp_path):
    a = _advance(MockHouseAdapter(), WALK[:10])
    path = tmp_path / "start.state"
    path.write_bytes(a.save_state())
    env = FireRedEnv(MockHouseAdapter(), start=path)
    first = env.reset()
    path.unlink()                         # later resets must not need the file
    env.step("DOWN")
    again = env.reset()
    assert again.ram == first.ram and again.frame == first.frame
    assert env.reset(path).ram == first.ram   # same path: cached, no disk read


def test_reset_without_start_still_powers_on():
    env = FireRedEnv(MockHouseAdapter())
    assert env.reset().frame == 0
    assert env.start_snapshot is None


def test_start_rejected_without_save_states():
    with pytest.raises(ValueError, match="no save states"):
        FireRedEnv(MockAdapter()).reset(b"x")


def test_missing_sidecar_json_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_start(tmp_path / "nope.json")


def test_two_episodes_from_the_same_start_are_identical(tmp_path):
    a = _advance(MockHouseAdapter(), WALK[:8])
    env = FireRedEnv(MockHouseAdapter(), start=a.save_state(), max_steps=100)
    first = _episode(env, WALK)
    second = _episode(env, WALK)
    assert first == second
    assert len(first) == len(WALK) + 1


def test_power_on_episodes_are_identical():
    env = FireRedEnv(MockHouseAdapter(), max_steps=100)
    assert _episode(env, WALK) == _episode(env, WALK)


# ---------------------------------------------------------------- Gymnasium wrapper (optional)

def test_gym_wrapper_passes_checker_and_is_deterministic():
    pytest.importorskip("gymnasium")
    np = pytest.importorskip("numpy")
    from gymnasium.utils.env_checker import check_env

    from game_brain.rl.gym_wrapper import RAM_FEATURES, SCREEN_SHAPE, FireRedGymEnv

    env = FireRedGymEnv("mock-house", max_steps=60)
    check_env(env, skip_render_check=True)
    assert env.action_space.n == len(BUTTONS)
    assert env.observation_space["screen"].shape == SCREEN_SHAPE
    assert env.observation_space["ram"].shape == (len(RAM_FEATURES),)

    acts = [BUTTONS.index(b) for b in WALK]

    def run(seed):
        o, _ = env.reset(seed=seed)
        out = [(o["ram"].tolist(), o["screen"].tobytes(), 0.0)]
        for x in acts:
            o, r, te, tr, info = env.step(x)
            assert set(info["reward_parts"]) >= {"reward", "loop"}
            out.append((o["ram"].tolist(), o["screen"].tobytes(), r))
        return out

    assert run(1) == run(2)               # seed only seeds action_space sampling
    assert not np.any(env._screen())      # mock: no screen -> zeros


def test_gym_wrapper_reward_equals_fireredenv():
    pytest.importorskip("gymnasium")
    from game_brain.rl.gym_wrapper import FireRedGymEnv

    plain = FireRedEnv(MockHouseAdapter(), max_steps=100)
    rewards = [r for _, _, r, _ in _episode(plain, WALK)[1:]]
    gym_env = FireRedGymEnv(MockHouseAdapter(), max_steps=100)
    gym_env.reset()
    assert [gym_env.step(BUTTONS.index(b))[1] for b in WALK] == rewards


# ---------------------------------------------------------------- real ROM

@real
def test_real_rom_reset_from_sidecar_is_deterministic_with_screen(tmp_path):
    from game_brain.adapters import make_adapter

    a = make_adapter("mgba")
    try:
        _advance(a, ["A"] * 120)
        side = _sidecar(tmp_path, a, step=120)
        ram = a.observe().ram
    finally:
        a.close()
    b = make_adapter("mgba")
    try:
        env = FireRedEnv(b, start=side["_path"], max_steps=1000)
        acts = (["A", "DOWN", "LEFT", "A", "B", "UP", "RIGHT"] * 15)

        def run():
            obs = env.reset()
            out = [(obs.frame, obs.ram, b.screen_rgbx())]
            for x in acts:
                o, r, *_ = env.step(x)
                out.append((o.frame, o.ram, r, b.screen_rgbx()))
            return out

        first, second = run(), run()
        assert first[0][1] == ram and first[0][0] == side["frame"]
        assert first == second
        assert any(first[0][2])           # first screen is a drawn frame, not zeros
    finally:
        b.close()


@real
def test_real_rom_gym_wrapper_screen_and_determinism():
    pytest.importorskip("gymnasium")
    np = pytest.importorskip("numpy")
    from game_brain.rl.gym_wrapper import FireRedGymEnv

    env = FireRedGymEnv("mgba", max_steps=10_000)
    try:
        rng = np.random.default_rng(7)
        acts = [int(x) for x in rng.integers(len(BUTTONS), size=300)]

        def run(seed):
            o, _ = env.reset(seed=seed)
            out = [(o["ram"].tolist(), o["screen"].tobytes())]
            for x in acts:
                o, r, te, tr, info = env.step(x)
                out.append((o["ram"].tolist(), o["screen"].tobytes(), r))
            return out

        a, b = run(1), run(2)
        assert a == b
        assert any(any(step[1]) for step in a)   # the screen is read (not all zeros)
    finally:
        env.close()
