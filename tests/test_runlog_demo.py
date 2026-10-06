import json
from pathlib import Path

from game_brain import demo
from game_brain.adapters.mock import MockAdapter
from game_brain.runlog import iter_steps, read_log, replay
from game_brain.schema import Action, Decision, from_json

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "mock_run.jsonl"


def test_demo_writes_log_and_replays_deterministically(tmp_path):
    s = demo.run("mock", steps=50, mode="auto", out_dir=str(tmp_path), quiet=True)
    recs = list(read_log(s["log"]))
    assert recs[0]["kind"] == "header" and recs[-1]["kind"] == "summary"
    steps = list(iter_steps(s["log"]))  # restores executed_action deduped against proposed_action
    assert len(steps) == 50
    for r in steps:
        Decision.from_dict(r["decision"])
        Action.from_dict(r["executed_action"])
        assert r["frames_advanced"] == Action.from_dict(r["executed_action"]).total_frames
    assert s["final_ram"]["scene"] != "intro"  # rule brain got through the mock intro
    assert replay(s["log"], MockAdapter()) == []


def test_replay_detects_divergence(tmp_path):
    s = demo.run("mock", steps=20, mode="auto", out_dir=str(tmp_path), quiet=True)
    assert replay(s["log"], MockAdapter(intro_presses=3)) != []


def test_shadow_run_does_not_progress(tmp_path):
    s = demo.run("mock", steps=30, mode="shadow", out_dir=str(tmp_path), quiet=True)
    assert s["final_ram"]["scene"] == "intro"  # brain proposed A presses, none executed
    assert s["executed_brain_or_manual_actions"] == 0


def test_mode_switch_mid_run(tmp_path):
    s = demo.run("mock", steps=40, mode="shadow", out_dir=str(tmp_path), quiet=True,
                 switches={20: demo.Mode.AUTO})
    kinds = [r["kind"] for r in read_log(s["log"])]
    assert "mode_change" in kinds and s["mode_final"] == "auto"


def test_example_log_is_valid():
    recs = [json.loads(l) for l in EXAMPLE.read_text().splitlines() if l.strip()]
    assert recs[0]["kind"] == "header"
    steps = [r for r in recs if r["kind"] == "step"]
    assert steps
    assert replay(EXAMPLE, MockAdapter()) == []


def test_replay_of_assist_run_with_injected_human_action(tmp_path):
    """Assist run where a 'dashboard' injects human actions mid-run; replay must match."""
    from game_brain.arbiter import Arbiter
    from game_brain.brain import RuleBrain
    from game_brain.runlog import RunLogWriter
    from game_brain.schema import ButtonPress

    adapter, arb = MockAdapter(battle_tiles=()), Arbiter([RuleBrain()], mode="assist")
    inject = {16: [Action([ButtonPress("LEFT", 8)], source="manual"),
                   Action([ButtonPress("LEFT", 8)], source="manual")],
              25: [Action.tap("B", source="manual")]}
    path = tmp_path / "assist.jsonl"
    adapter.reset()
    with RunLogWriter(path) as log:
        log.header(adapter=adapter.name, mode="assist")
        for step in range(40):
            for a in inject.get(step, []):
                assert arb.submit_manual(a, origin="dashboard")
            obs = adapter.observe()
            res = arb.step(obs)
            log.step(step, obs, res, adapter.act(res.executed))

    steps = [r for r in read_log(path) if r["kind"] == "step"]
    actors = [r["decision"]["actor"] for r in steps]
    assert [i for i, a in enumerate(actors) if a == "human"] == [16, 17, 25]
    assert all(a == "brain" for i, a in enumerate(actors) if i not in (16, 17, 25))
    assert steps[16]["executed_action"]["source"] == "manual"
    assert steps[18]["decision"]["brain"] == "rule"  # brain resumed
    # the two human LEFT steps actually moved the player two tiles left (walls clamp at x=0)
    before, after = steps[16]["observation"]["ram"], steps[18]["observation"]["ram"]
    assert after["player_x"] == max(before["player_x"] - 2, 0) and after["player_y"] == before["player_y"]
    assert replay(path, MockAdapter(battle_tiles=())) == []


def test_map_keys_logged_once_per_map_and_restored(tmp_path):
    import json as _json
    from game_brain.runlog import RunLogWriter, iter_steps, read_log
    from game_brain.schema import Observation

    class _R:  # minimal ArbiterResult stand-in
        def __init__(self):
            from game_brain.schema import Mode
            self.mode = Mode.AUTO
            self.decision = type("D", (), {"to_dict": lambda s: {"type": "decision"}})()
            self.proposed = self.executed = None
            self.notes = []

    grid_a = {"map_w": 2, "map_h": 1, "collision": [".#"], "warps": []}
    grid_b = {"map_w": 1, "map_h": 1, "collision": ["."], "warps": [{"x": 0, "y": 0}]}
    rams = [{"scene": "other"},
            {"scene": "overworld", "player_x": 0, **grid_a},
            {"scene": "overworld", "player_x": 1, **grid_a},
            {"scene": "other"},
            {"scene": "overworld", "player_x": 0, **grid_b},
            {"scene": "overworld", "player_x": 0, **grid_a}]
    p = tmp_path / "run.jsonl"
    with RunLogWriter(p) as log:
        for i, ram in enumerate(rams):
            log.step(i, Observation(frame=i, ram=_json.loads(_json.dumps(ram))), _R(), 1)
    recs = list(read_log(p))
    assert [r["kind"] for r in recs].count("map") == 3  # a, b, a again
    assert all("collision" not in r["observation"]["ram"] for r in recs if r["kind"] == "step")
    assert [r["observation"]["ram"] for r in iter_steps(p)] == rams


class _NewFieldMock(MockAdapter):
    """A newer adapter that reports one more RAM field than the one that wrote the log."""

    def __init__(self, locked=False, **kw):
        self.locked = locked
        super().__init__(**kw)

    def observe(self):
        obs = super().observe()
        obs.ram["controls_locked"] = self.locked
        return obs


def test_old_log_without_a_new_ram_field_still_replays(tmp_path):
    """Logs written before a RAM field existed (pre-#71 mGBA logs have no ``controls_locked``)
    replay with 0 mismatches: fields absent from the logged observation are skipped."""
    s = demo.run("mock", steps=40, mode="auto", out_dir=str(tmp_path), quiet=True)
    steps = list(iter_steps(s["log"]))
    assert all("controls_locked" not in r["observation"]["ram"] for r in steps)
    assert all("controls_locked" not in r["observation_after"]["ram"] for r in steps if r.get("observation_after"))
    assert replay(s["log"], _NewFieldMock()) == []


def test_a_logged_ram_field_that_differs_is_still_a_mismatch(tmp_path):
    from game_brain.arbiter import Arbiter
    from game_brain.brain import RuleBrain
    from game_brain.runlog import RunLogWriter, ram_differs
    adapter, arb = _NewFieldMock(locked=False), Arbiter([RuleBrain()], mode="auto")
    path = tmp_path / "new.jsonl"
    with RunLogWriter(path) as log:
        log.header(adapter="mock")
        adapter.reset()
        for step in range(5):
            obs = adapter.observe()
            res = arb.step(obs)
            advanced = adapter.act(res.executed)
            log.step(step, obs, res, advanced, observation_after=adapter.observe())
    assert all(r["observation"]["ram"]["controls_locked"] is False for r in iter_steps(path))
    assert replay(path, _NewFieldMock(locked=False)) == []
    mm = replay(path, _NewFieldMock(locked=True))          # present in the log but different
    assert any("ram differs" in m for m in mm) and any("post-action ram differs" in m for m in mm)
    assert replay(path, MockAdapter()) != []               # present in the log, missing on replay
    assert ram_differs({"a": 1, "b": 2}, {"a": 1}) is False
    assert ram_differs({"a": 1}, {"a": 2}) is True and ram_differs({}, {"a": None}) is True
