import json
from pathlib import Path

from game_brain import demo
from game_brain.adapters.mock import MockAdapter
from game_brain.runlog import read_log, replay
from game_brain.schema import Action, Decision, from_json

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "mock_run.jsonl"


def test_demo_writes_log_and_replays_deterministically(tmp_path):
    s = demo.run("mock", steps=50, mode="auto", out_dir=str(tmp_path), quiet=True)
    recs = list(read_log(s["log"]))
    assert recs[0]["kind"] == "header" and recs[-1]["kind"] == "summary"
    steps = [r for r in recs if r["kind"] == "step"]
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
