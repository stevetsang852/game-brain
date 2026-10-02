"""Real ROM (skipped without mGBA bindings + $GAME_BRAIN_ROM): the dashboard runner (live.run) with its
defaults (adapter auto -> mgba, brains battle,path,rule) plays boot -> Viridian City exactly like the CLI.
Regression for the dashboard wandering in the bedroom with "brain gave no goal" / no collision data."""
import json
import os
from pathlib import Path

import pytest

from game_brain.adapters import make_adapter
from game_brain.dashboard import live
from game_brain.runlog import iter_steps, replay


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


class _CaptureServer:
    url = "test://capture"
    client_count = 1

    def __init__(self):
        self.sent = []

    def broadcast(self, env):
        if env["type"] in ("decision", "observation"):
            self.sent.append(json.loads(json.dumps(env)))

    def poll(self):
        return []


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
def test_dashboard_defaults_reach_viridian_city(tmp_path):
    srv = _CaptureServer()
    s = live.run(srv, "auto", "auto", steps=3130, step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "runs"),
                 quiet=True, save_dir=str(tmp_path / "saves"))
    dec = [e["payload"] for e in srv.sent if e["type"] == "decision"]
    assert {d["brain"] for d in dec} >= {"battle", "path", "rule"}
    path_dec = [d for d in dec if d["brain"] == "path"]
    # a path decision only lacks a goal for the few steps it waits for the position to settle on a new map
    assert path_dec and sum(1 for d in path_dec if d.get("goal")) > 0.9 * len(path_dec)
    assert sum(1 for d in path_dec if d.get("path")) > 0.9 * len(path_dec)
    obs = [e["payload"]["ram"] for e in srv.sent if e["type"] == "observation"]
    assert any(o.get("collision") for o in obs)
    steps = list(iter_steps(s["log"]))
    maps = [(r["observation"]["ram"].get("map_bank"), r["observation"]["ram"].get("map_id")) for r in steps]
    assert (3, 19) in maps and (3, 1) in maps, "dashboard run should walk Route 1 into Viridian City"
    names = [Path(p).stem for p in s["saves"]]
    assert any(n.endswith("milestone-viridian_city") for n in names) and names[-1].endswith("_final")
    assert replay(s["log"], make_adapter("mgba")) == []
