"""Stuck detection (auto-learn PR1): counting / ignoring rules, the switch to free explore, status
fields and CLI flags. No ROM needed."""
import pytest

from game_brain import demo
from game_brain.brain import GoalPlanner, Milestone, PathBrain, Target
from game_brain.dashboard import live
from game_brain.runlog import iter_steps, read_log, replay
from game_brain.schema import Action, Decision, Observation
from game_brain.setup import Session
from game_brain.stuck import DEFAULT_STUCK_STEPS, StuckDetector, skip_reason

ROWS = ["#######",
        "#.....#",
        "#.....#",
        "#######"]


def obs(x=2, y=1, mid=9, **ram):
    base = {"player_x": x, "player_y": y, "facing": "UP", "map_bank": 9, "map_id": mid,
            "map_w": len(ROWS[0]), "map_h": len(ROWS), "collision": list(ROWS), "warps": []}
    base.update(ram)
    return Observation(frame=0, ram=base)


def fade():
    return Observation(frame=0, ram={"in_battle": False})       # no player position


def dec(plan="go somewhere"):
    return Decision(brain="path", plan=plan, reason="test")


class Log:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


class Result:
    def __init__(self, plan="go somewhere"):
        self.decision = dec(plan)


# ------------------------------------------------------------------ counting rules
def test_counts_steps_on_the_same_tile_and_restarts_on_a_new_tile():
    d = StuckDetector(5, enabled=True)
    for i in range(1, 4):
        assert d.observe(obs(), "auto", dec()) is False and d.stuck_steps == i
    d.observe(obs(3, 1), "auto", dec())                   # moved: restart at 1
    assert d.stuck_steps == 1 and d.tile == (9, 9, 3, 1)
    d.observe(obs(3, 1, mid=10), "auto", dec())           # same x/y, other map: a new tile
    assert d.stuck_steps == 1 and d.tile == (9, 10, 3, 1)
    assert d.status() == {"enabled": True, "stuck_steps": 1, "threshold": 5}


@pytest.mark.parametrize("o,plan,why", [
    (fade(), "go somewhere", "no position"),
    (obs(in_battle=True), "go somewhere", "battle"),
    (obs(controls_locked=True), "go somewhere", "controls locked"),
    (obs(), "warp transition", "map transition"),
    (obs(), "arrived on a new map", "map transition"),
])
def test_warp_dialogue_battle_menu_steps_are_neither_counted_nor_a_reset(o, plan, why):
    assert why in skip_reason(o, dec(plan))
    d = StuckDetector(10, enabled=True)
    d.observe(obs(), "auto", dec())
    d.observe(obs(), "auto", dec())
    for _ in range(50):
        assert d.observe(o, "auto", dec(plan)) is False
    assert d.stuck_steps == 2                              # not counted, not reset
    d.observe(obs(), "auto", dec())
    assert d.stuck_steps == 3


def test_a_skipped_warp_that_moved_the_player_restarts_the_count():
    d = StuckDetector(10, enabled=True)
    for _ in range(4):
        d.observe(obs(), "auto", dec())
    d.observe(fade(), "auto", dec("warp transition"))
    d.observe(obs(1, 2, mid=3), "auto", dec())
    assert d.stuck_steps == 1


def test_idle_steps_with_no_brain_available_count():
    """``actor: none`` idle (every brain unavailable) is exactly the stopped AI: it counts."""
    d = StuckDetector(3, enabled=True)
    idle = Decision(brain="none", plan="idle", reason="no brain available")
    assert [d.observe(obs(), "auto", idle) for _ in range(3)] == [False, False, True]


@pytest.mark.parametrize("mode", ["manual", "assist", "shadow"])
def test_only_auto_mode_counts(mode):
    d = StuckDetector(3, enabled=True)
    d.observe(obs(), "auto", dec())
    d.observe(obs(), "auto", dec())
    for _ in range(10):
        assert d.observe(obs(), mode, dec()) is False
    assert d.stuck_steps == 0 and d.tile is None           # a human drove: start over
    assert [d.observe(obs(), "auto", dec()) for _ in range(3)] == [False, False, True]


def test_disabled_never_counts_and_toggling_restarts():
    d = StuckDetector(2)
    assert d.enabled is False and d.threshold == 2
    assert not any(d.observe(obs(), "auto", dec()) for _ in range(10)) and d.stuck_steps == 0
    d.set_enabled(True)
    d.observe(obs(), "auto", dec())
    assert d.stuck_steps == 1
    d.set_enabled(False)
    assert d.status() == {"enabled": False, "stuck_steps": 0, "threshold": 2}


def test_fires_once_at_the_threshold_then_again_only_after_moving():
    d = StuckDetector(3, enabled=True)
    fired = [d.observe(obs(), "auto", dec()) for _ in range(8)]
    assert fired == [False, False, True, False, False, False, False, False] and d.stuck_steps == 8
    d.observe(obs(3, 1), "auto", dec())
    assert [d.observe(obs(3, 1), "auto", dec()) for _ in range(2)] == [False, True]


def test_threshold_must_be_positive_and_defaults_to_300():
    assert DEFAULT_STUCK_STEPS == 300 and StuckDetector().threshold == 300
    with pytest.raises(ValueError):
        StuckDetector(0)


# ------------------------------------------------------------------ the switch
def tile_brain():
    pl = GoalPlanner([Milestone("t", "go to (5,2)", done=lambda o: o.position == (5, 2),
                                target=lambda o: Target.at(5, 2))])
    return PathBrain(planner=pl, settle_checks=0)


def test_force_free_explore_replaces_the_milestone_but_still_waits_out_a_warp_fade():
    b = tile_brain()
    b.decide(obs(1, 1))                                    # settle
    assert b.decide(obs(1, 1))[1].plan.startswith("go to (5,2)")
    b.force_free_explore("stuck at (1, 1)")
    act, d = b.decide(obs(1, 1))
    assert d.plan == "free explore" and d.goal == "自由探索（卡住後自動切換）"
    assert [m["id"] for m in d.milestones] == ["t"]        # milestones still reported
    b._last = ("warp", None)
    act, d = b.decide(fade())
    assert d.plan == "warp transition" and act.presses[0].button == "NONE"
    b.reset()
    assert b.forced_explore is None


def _session(tmp_path, **kw):
    return Session("mock-house", brains="battle,path,rule", seed=0, out_dir=str(tmp_path / "runs"),
                   save_dir=None, no_memory=True, **kw)


def test_session_switches_to_free_explore_logs_stuck_and_phase(tmp_path):
    sess = _session(tmp_path, auto_learn=True, stuck_steps=4)
    try:
        log = Log()
        path = next(b for b in sess.brains if isinstance(b, PathBrain))
        got = [sess.watch_stuck(log, 10 + i, obs(2, 1, mid=1, map_bank=4), Result()) for i in range(6)]
        assert got[:3] == [None, None, None] and got[4:] == [None, None]
        stuck = got[3]
        assert stuck["step"] == 13 and stuck["map"] == [4, 1] and (stuck["x"], stuck["y"]) == (2, 1)
        assert "unchanged for 4 counted steps" in stuck["reason"] and sess.stuck == stuck
        assert path.forced_explore == stuck["reason"] and sess.phase == "free_explore"
        kinds = [k for k, _ in log.events]
        assert kinds == ["stuck", "phase"]                 # once
        ev = log.events[0][1]
        assert ev["switched_to"] == "free_explore" and ev["stuck_steps"] == 4 and ev["threshold"] == 4
        assert {k: ev[k] for k in ("step", "map", "x", "y", "reason")} == stuck
        assert log.events[1][1] == {"phase": "free_explore", "step": 13, "reason": "stuck"}
        assert sess.auto_learn_status() == {"enabled": True, "stuck_steps": 6, "threshold": 4}
        sess.new_game(log, 20)                             # new brains, no forced explore
        assert sess.stuck is None and sess.phase == "running" and log.events[-1][1]["reason"] == "new_game"
        assert not any(getattr(b, "forced_explore", None) for b in sess.brains)
        assert sess.auto_learn_status()["stuck_steps"] == 0
    finally:
        sess.adapter.close()


def test_session_detector_is_off_by_default_and_inactive_in_manual(tmp_path):
    sess = _session(tmp_path)
    try:
        log = Log()
        assert sess.auto_learn_status() == {"enabled": False, "stuck_steps": 0, "threshold": 300}
        assert all(sess.watch_stuck(log, i, obs(), Result()) is None for i in range(400))
        sess.set_auto_learn(True, log, 400)
        sess.stuck_detector.threshold = 3
        sess.arbiter.apply_mode("manual")
        assert all(sess.watch_stuck(log, i, obs(), Result()) is None for i in range(10))
        assert sess.stuck is None and log.events == [("auto_learn", {"step": 400, "enabled": True, "threshold": 300})]
        sess.arbiter.apply_mode("auto")
        assert [sess.watch_stuck(log, i, obs(), Result()) is not None for i in range(3)] == [False, False, True]
    finally:
        sess.adapter.close()


def test_no_switch_when_already_free_exploring(tmp_path):
    sess = _session(tmp_path, auto_learn=True, stuck_steps=2)
    try:
        planner = next(b.planner for b in sess.brains if hasattr(b, "planner"))
        planner.restore([m.id for m in planner.milestones if not m.placeholder])
        log = Log()
        sess.update_phase(log, 1)
        assert sess.phase == "free_explore"
        assert all(sess.watch_stuck(log, i, obs(), Result()) is None for i in range(5))
        assert sess.stuck is None and [k for k, _ in log.events] == ["phase"]
    finally:
        sess.adapter.close()


# ------------------------------------------------------------------ live loop + status
class Capture:
    url = "http://127.0.0.1:0/"
    client_count = 0

    def __init__(self):
        self.status = []

    def broadcast(self, env):
        if env["type"] == "status":
            self.status.append(env["payload"])

    def poll(self):
        return []


def test_live_run_stuck_switch_status_and_replay(tmp_path, monkeypatch):
    """A path brain that stands still until it is forced to explore: the run switches at the
    threshold, moves again, and status / log / replay show it."""
    real = PathBrain.decide

    def stand_still(self, o):
        if self.forced_explore or o.position is None:      # intro: the rule brain mashes A
            return real(self, o)
        return Action.wait(8, source="brain:path"), Decision(brain="path", plan="stand still (test)", reason="t")
    monkeypatch.setattr(PathBrain, "decide", stand_still)
    srv = Capture()
    s = live.run(srv, "mock-house", steps=30, step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "runs"),
                 quiet=True, save_dir=None, no_memory=True, auto_learn=True, stuck_steps=5)
    st = srv.status
    assert all(p["auto_learn"]["enabled"] and p["auto_learn"]["threshold"] == 5 for p in st)
    k = next(i for i, p in enumerate(st) if p["auto_learn"]["stuck_steps"] == 1)   # first step in the house
    assert k > 0 and all(p["auto_learn"]["stuck_steps"] == 0 for p in st[:k])      # intro: not counted
    assert [p["auto_learn"]["stuck_steps"] for p in st[k:k + 5]] == [1, 2, 3, 4, 5]
    sw = k + 4
    assert "stuck" not in st[sw - 1] and st[sw - 1]["phase"] == "running"
    assert st[sw]["stuck"]["step"] == st[sw]["step"] and st[sw]["phase"] == "free_explore"
    assert all(p["stuck"] == st[sw]["stuck"] for p in st[sw:]) and st[-1]["finished"]
    ev = [r for r in read_log(s["log"]) if r.get("kind") in ("stuck", "phase")]
    assert [r["kind"] for r in ev] == ["stuck", "phase", "phase"] and ev[1]["reason"] == "stuck"
    steps = list(iter_steps(s["log"]))
    xy = lambda r: (r["observation"]["ram"]["player_x"], r["observation"]["ram"]["player_y"])
    assert len({xy(r) for r in steps[k:sw + 1]}) == 1 and len({xy(r) for r in steps[sw + 1:]}) > 1  # moving again
    plans = [r["decision"]["plan"] for r in steps[sw + 1:]]
    assert set(plans) == {"arrived on a new map", "free explore"} and plans[-1] == "free explore"
    from game_brain.adapters import make_adapter
    assert replay(s["log"], make_adapter("mock-house")) == []


def test_live_status_has_auto_learn_but_no_stuck_by_default(tmp_path):
    srv = Capture()
    live.run(srv, "mock-house", steps=5, step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "runs"),
             quiet=True, save_dir=None, no_memory=True)
    assert srv.status and all(p["auto_learn"] == {"enabled": False, "stuck_steps": 0, "threshold": 300}
                              and "stuck" not in p for p in srv.status)


def test_cli_and_dashboard_flags(tmp_path):
    for parser in (live.build_parser(), demo.build_parser()):
        a = parser.parse_args([])
        assert a.auto_learn is False and a.stuck_steps == 300
        a = parser.parse_args(["--auto-learn", "--stuck-steps", "50", "--adapter", "mock-house",
                               "--out", str(tmp_path / "runs"), "--no-save", "--no-memory"])
        assert a.auto_learn is True and a.stuck_steps == 50
        sess = Session.from_args(a)
        try:
            assert sess.config()["auto_learn"] is True and sess.config()["stuck_steps"] == 50
        finally:
            sess.adapter.close()
