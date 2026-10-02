"""--starter random (default) | bulbasaur | charmander | squirtle (game_brain/setup.py), no ROM:
flag parsing, deterministic random pick from --seed, the ``starter`` object
{"requested", "picked", "seed"} in the log / status / summary / save sidecars, and --resume."""
import json
from pathlib import Path

import pytest

from game_brain import demo, setup
from game_brain.dashboard import live
from game_brain.runlog import read_log

SPECIES = {"bulbasaur": 1, "squirtle": 7, "charmander": 4}      # mock-house ball species ids


class _Capture:
    url = "test://capture"
    client_count = 0

    def __init__(self):
        self.status = []

    def broadcast(self, env):
        if env["type"] == "status":
            self.status.append(json.loads(json.dumps(env["payload"])))

    def poll(self):
        return []


def test_flag_is_shared_default_random(monkeypatch, tmp_path):
    monkeypatch.delenv("GAME_BRAIN_STARTER", raising=False)
    for ap in (demo.build_parser(), live.build_parser()):
        assert ap.parse_args([]).starter is None                   # = random (or $GAME_BRAIN_STARTER)
        for s in ("bulbasaur", "charmander", "squirtle", "random", "Random"):
            assert ap.parse_args(["--starter", s]).starter == s.lower()
        with pytest.raises(SystemExit):
            ap.parse_args(["--starter", "pikachu"])
        a = ap.parse_args(["--adapter", "mock-house", "--no-save", "--out", str(tmp_path), "--seed", "1"])
        cfg = setup.Session.from_args(a, quiet=True).config()
        assert cfg["starter"] == {"requested": "random", "picked": None, "seed": 1}
        assert cfg["starter_plan"] == "charmander"
    monkeypatch.setenv("GAME_BRAIN_STARTER", "squirtle")           # Docker: compose.yaml environment
    a = live.build_parser().parse_args(["--adapter", "mock-house", "--no-save", "--out", str(tmp_path)])
    assert setup.Session.from_args(a, quiet=True).config()["starter"]["requested"] == "squirtle"


def test_random_is_deterministic_from_the_seed():
    picks = {seed: setup.resolve_starter("random", seed) for seed in range(30)}
    assert picks == {seed: setup.resolve_starter("random", seed) for seed in range(30)}
    assert set(picks.values()) == set(setup.STARTERS)               # every starter is reachable
    assert (picks[0], picks[1], picks[2]) == ("bulbasaur", "charmander", "squirtle")   # pinned
    assert setup.resolve_starter("charmander", 5) == "charmander"
    with pytest.raises(ValueError):
        setup.resolve_starter("mew", 0)


def _run(tmp_path, out, steps, **kw):
    return demo.run("mock-house", steps=steps, mode="auto", brains="battle,path,rule",
                    out_dir=str(tmp_path / out), quiet=True, **kw)


@pytest.mark.parametrize("requested,seed,picked", [("bulbasaur", 0, "bulbasaur"), ("charmander", 0, "charmander"),
                                                   ("squirtle", 0, "squirtle"), ("random", 1, "charmander"),
                                                   ("random", 2, "squirtle")])
def test_pick_is_made_logged_and_saved(tmp_path, monkeypatch, requested, seed, picked):
    got = {}
    orig = demo.Session.__init__

    def spy(self, *a, **k):
        orig(self, *a, **k)
        got["sess"] = self
    monkeypatch.setattr(demo.Session, "__init__", spy)
    s = _run(tmp_path, "r", 400, starter=requested, seed=seed, save_dir=str(tmp_path / "saves"))
    assert got["sess"].adapter.party == [SPECIES[picked]]
    want = {"requested": requested, "picked": picked, "seed": seed}
    assert s["starter"] == want
    recs = list(read_log(s["log"]))
    assert recs[0]["starter"] == dict(want, picked=None)            # header: before the pick
    ev = next(r for r in recs if r["kind"] == "starter")
    assert ev["picked"] is None and ev["plan"] == picked
    pk = [r for r in recs if r["kind"] == "starter_picked"]
    assert len(pk) == 1 and {k: pk[0][k] for k in want} == want
    sides = {Path(p).stem: json.loads(Path(p).read_text()) for p in s["saves"]}
    ms = next(n for n in sides if n.endswith("milestone-get_starter"))
    assert sides[ms]["starter"] == want                             # the pick is in the save at the pick
    assert pk[0]["step"] == sides[ms]["step"]
    early = [v for n, v in sides.items() if v["step"] < sides[ms]["step"]]
    assert early and all(v["starter"] == dict(want, picked=None) for v in early)
    assert all(v["starter"] == want for v in sides.values() if v["step"] >= sides[ms]["step"])
    assert any(n.endswith("milestone-rival_battle_over") for n in sides)


def test_dashboard_status_and_summary_carry_the_starter_object(tmp_path):
    srv = _Capture()
    s = live.run(srv, "mock-house", "auto", steps=400, step_delay=0, screenshot_every=0, out_dir=str(tmp_path),
                 quiet=True, seed=2)
    seen = [st["starter"] for st in srv.status]
    assert seen[0] == {"requested": "random", "picked": None, "seed": 2}
    assert seen[-1] == {"requested": "random", "picked": "squirtle", "seed": 2} == s["starter"]
    first = next(i for i, x in enumerate(seen) if x["picked"])
    assert all(x["picked"] is None for x in seen[:first]) and all(x["picked"] for x in seen[first:])
    assert [r for r in read_log(s["log"]) if r["kind"] == "summary"][-1]["starter"] == s["starter"]


def test_resume_reuses_the_recorded_choice(tmp_path):
    saves = tmp_path / "saves"
    a = _run(tmp_path, "a", 400, starter="random", seed=2, save_dir=str(saves), save_every=50)
    sides = {Path(p).stem: p for p in a["saves"]}
    before = sides["0000050_periodic"]                              # before the pick
    after = next(p for n, p in sides.items() if n.endswith("_final"))
    assert json.loads(Path(before).read_text())["starter"]["picked"] is None
    # other seeds / an explicit other starter on the command line do not re-roll
    for kw in ({"seed": 0}, {"starter": "charmander", "seed": 1}, {}):
        b = _run(tmp_path, "b", 400, save_dir=str(saves), resume=before, **kw)
        assert b["starter"] == {"requested": "random", "picked": "squirtle", "seed": 2}
        c = _run(tmp_path, "c", 5, save_dir=str(saves), resume=after, **kw)
        recs = list(read_log(c["log"]))
        assert recs[0]["starter"] == {"requested": "random", "picked": "squirtle", "seed": 2}
        assert not [r for r in recs if r["kind"] == "starter_picked"]   # already picked in the save
        assert next(r for r in recs if r["kind"] == "starter")["source"] == "resume"


def test_old_saves_without_a_starter_record_resume_as_bulbasaur(tmp_path):
    saves = tmp_path / "saves"
    a = _run(tmp_path, "a", 400, starter="bulbasaur", save_dir=str(saves), save_every=50)
    for stem, picked in (("0000050_periodic", None), ("_final", "bulbasaur")):
        side = Path(next(p for p in a["saves"] if Path(p).stem.endswith(stem)))
        d = json.loads(side.read_text())
        del d["starter"]
        side.write_text(json.dumps(d))
        b = _run(tmp_path, "b", 5, save_dir=str(saves), resume=str(side), seed=1)
        assert b["starter"]["requested"] == "bulbasaur"
        assert b["starter"]["picked"] == picked


def test_milestone_label_names_the_planned_ball(tmp_path):
    a = live.build_parser().parse_args(["--adapter", "mock-house", "--seed", "1", "--no-save", "--out", str(tmp_path)])
    sess = setup.Session.from_args(a, quiet=True)
    path = next(b for b in sess.brains if b.name == "path")
    label = next(m.label for m in path.planner.milestones if m.id == "get_starter")
    assert "Charmander" in label and "(10, 4)" in label
