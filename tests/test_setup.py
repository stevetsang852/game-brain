"""The CLI (game_brain.demo) and the dashboard (game_brain.dashboard.live) build the same run setup
(game_brain/setup.py), so they cannot drift: same flags, same adapter / brains / milestones / saves."""
import json
from pathlib import Path

from game_brain import demo, setup
from game_brain.dashboard import live
from game_brain.runlog import iter_steps, read_log, replay
from game_brain.adapters import make_adapter


class _CaptureServer:
    url = "test://capture"
    client_count = 0

    def __init__(self):
        self.sent = []

    def broadcast(self, env):
        self.sent.append(json.loads(json.dumps(env)))

    def poll(self):
        return []


SHARED = {"--adapter", "--mode", "--brains", "--battle-confidence", "--seed", "--out", "--save-dir", "--keep-periodic",
          "--save-every", "--no-save", "--resume"}


def _flags(ap):
    return {s for a in ap._actions for s in a.option_strings}


def test_cli_and_dashboard_share_the_run_flags():
    d, l = demo.build_parser(), live.build_parser()
    assert SHARED <= _flags(d) and SHARED <= _flags(l)
    for flag in SHARED - {"--adapter", "--brains"}:      # same defaults too, except the two below
        dest = flag.lstrip("-").replace("-", "_")
        assert d.get_default(dest) == l.get_default(dest), flag
    # dashboard defaults = the Docker image: real game when a ROM is configured, full brains
    assert l.get_default("brains") == "battle,path,rule" == setup.FULL_BRAINS
    assert l.get_default("adapter") == "auto"
    assert d.get_default("brains") == "rule,random" and d.get_default("adapter") == "mock"   # CLI unchanged


def test_same_args_give_the_same_setup(tmp_path):
    argv = ["--adapter", "mock-house", "--brains", "battle,path,rule", "--battle-confidence", "0.7",
            "--save-dir", str(tmp_path / "saves"), "--save-every", "100", "--out", str(tmp_path / "runs")]
    a_cli = demo.build_parser().parse_args(argv)
    a_live = live.build_parser().parse_args(argv)
    c_cli = setup.Session.from_args(a_cli, quiet=True).config()
    c_live = setup.Session.from_args(a_live, quiet=True).config()
    assert c_cli == c_live
    assert c_cli["brains"] == [("battle", "RuleBattleBrain"), ("path", "PathBrain"), ("rule", "RuleBrain")]
    assert c_cli["battle_confidence"] == 0.7 and c_cli["milestones"][0] == "intro"
    assert "deliver_parcel" in c_cli["milestones"] and c_cli["saving"] and c_cli["save_every"] == 100


def test_dashboard_defaults_build_the_full_stack(tmp_path, monkeypatch):
    rom = tmp_path / "x.gba"
    rom.write_bytes(b"\0")
    monkeypatch.setenv("GAME_BRAIN_ROM", str(rom))
    assert setup.resolve_adapter("auto") == "mgba"
    monkeypatch.delenv("GAME_BRAIN_ROM")
    assert setup.resolve_adapter("auto") == "mock"            # with a warning on stderr
    a = live.build_parser().parse_args(["--no-save", "--out", str(tmp_path)])
    c = setup.Session.from_args(a, quiet=True).config()
    assert [n for n, _ in c["brains"]] == ["battle", "path", "rule"] and c["milestones"] and not c["saving"]


def test_dashboard_run_uses_milestones_saves_and_resume(tmp_path):
    """The bug: the dashboard used to build its own (rule,random, no milestones, no saves) setup."""
    saves = tmp_path / "saves"
    srv = _CaptureServer()
    s = live.run(srv, "mock-house", "auto", steps=160, step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "a"),
                 quiet=True, save_dir=str(saves), save_every=150)
    dec = [e["payload"] for e in srv.sent if e["type"] == "decision"]
    assert any(d["brain"] == "path" and d.get("goal") and d.get("path") for d in dec)
    assert all(d.get("milestones") for d in dec)
    obs = [e["payload"]["ram"] for e in srv.sent if e["type"] == "observation"]
    assert any(o.get("collision") for o in obs)
    names = [Path(p).stem for p in s["saves"]]
    assert "0000150_periodic" in names and names[-1] == "0000160_final" and any("milestone-" in n for n in names)
    side = next(p for p in s["saves"] if p.endswith("0000150_periodic.json"))
    srv2 = _CaptureServer()
    r = live.run(srv2, "mock-house", "auto", steps=40, step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "b"),
                 quiet=True, resume=side)
    assert r["resumed_from"] == str(Path(side).resolve())
    head = next(read_log(r["log"]))
    assert head["resumed_from"]["step"] == 150 and head["dashboard"] == srv2.url
    st = [e["payload"] for e in srv2.sent if e["type"] == "status"]
    assert st[0]["step"] == 150
    first = list(iter_steps(r["log"]))[0]
    ref = {x["step"]: x for x in iter_steps(s["log"])}[150]
    assert first["observation"]["ram"] == ref["observation"]["ram"]
    assert replay(r["log"], make_adapter("mock-house")) == []


def test_cli_and_dashboard_logs_match_step_for_step(tmp_path):
    """Same setup + same (no) input -> the dashboard plays exactly what the CLI plays."""
    a = demo.run("mock-house", steps=200, brains="battle,path,rule", out_dir=str(tmp_path / "cli"), quiet=True)
    b = live.run(_CaptureServer(), "mock-house", "auto", brains="battle,path,rule", steps=200, step_delay=0,
                 screenshot_every=0, out_dir=str(tmp_path / "live"), quiet=True)
    strip = lambda r: {k: v for k, v in r.items() if k != "ts"}   # noqa: E731
    assert [strip(r) for r in iter_steps(a["log"])] == [strip(r) for r in iter_steps(b["log"])]
