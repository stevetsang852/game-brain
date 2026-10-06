"""Dashboard New Game / load save restart the goals (YIN): the live loop must step with the
session's *new* arbiter (fresh brains + milestone planner), not the one captured at start. No ROM."""
import json
from pathlib import Path

from game_brain.dashboard import live
from game_brain.dashboard.server import PersistenceCommand, SavedGameCommand
from game_brain.runlog import iter_steps
from game_brain.schema import Mode, ModeCommand
from game_brain.setup import Session

NEW_GAME_AT, LOAD_AT, STEPS = 60, 120, 130


def _done(ms):
    return [m["id"] for m in ms or () if m["done"]]


class _Srv:
    url = "test://capture"
    client_count = 0

    def __init__(self, save_root: Path):
        self.save_root = save_root
        self.decisions, self.status, self.polls, self.loaded = [], [], 0, None

    def broadcast(self, env):
        if env["type"] == "decision":
            self.decisions.append(env["payload"])
        elif env["type"] == "status":
            self.status.append(env["payload"])

    def poll(self):
        self.polls += 1
        step = self.polls - 1
        if step == NEW_GAME_AT:            # mode switch after the new game in the same batch too
            return [PersistenceCommand("new_game"), ModeCommand(Mode.ASSIST)]
        if step == NEW_GAME_AT + 1:
            return [ModeCommand(Mode.AUTO)]
        if step == LOAD_AT:                # load the first game's (earlier) leave_bedroom milestone save
            side = sorted(self.save_root.glob("*/*_milestone-leave_bedroom.json"))[0]
            self.loaded = json.loads(side.read_text())
            return [SavedGameCommand(f"{side.parent.name}/{side.name}")]
        return []


def test_new_game_restarts_goals_and_load_save_restores_them(tmp_path):
    saves = tmp_path / "saves"
    srv = _Srv(saves)
    s = live.run(srv, "mock-house", "auto", brains="battle,path,rule", steps=STEPS, step_delay=0,
                 screenshot_every=0, out_dir=str(tmp_path / "runs"), quiet=True, save_dir=str(saves))
    dec = srv.decisions
    before = _done(dec[NEW_GAME_AT - 1]["milestones"])
    assert {"intro", "leave_bedroom", "leave_house", "pallet_town"} <= set(before)
    # the step right after New Game: goal 1 again, nothing done (was: the old planner's progress)
    assert _done(dec[NEW_GAME_AT]["milestones"]) == []
    assert dec[NEW_GAME_AT]["goal"].startswith("Get through the intro")
    assert srv.status[NEW_GAME_AT]["mode"] == "assist" and srv.status[NEW_GAME_AT + 1]["mode"] == "auto"
    # the new game plays the route from the start again (and writes its own milestone saves)
    assert set(before) <= set(_done(dec[LOAD_AT - 1]["milestones"]))
    first_run = sorted(p.name for p in saves.glob("*/*_milestone-leave_bedroom.json"))
    assert len(first_run) == 2, first_run
    # load the earlier save: the goals return to that save's progress
    assert _done(dec[LOAD_AT]["milestones"]) == srv.loaded["milestones_done"]
    assert srv.loaded["step"] < NEW_GAME_AT and srv.loaded["milestones_done"] == ["intro", "leave_bedroom"]
    # the run log says the same
    steps = {r["step"]: r for r in iter_steps(s["log"])}
    assert set(before) <= set(_done(steps[NEW_GAME_AT - 1]["decision"]["milestones"]))
    assert _done(steps[NEW_GAME_AT]["decision"]["milestones"]) == []
    assert _done(steps[LOAD_AT]["decision"]["milestones"]) == srv.loaded["milestones_done"]


def test_new_game_restarts_memory_progress(tmp_path):
    class Log:
        def event(self, kind, **fields):
            pass

        def step(self, *args, **kwargs):
            pass

    sess = Session("mock-house", brains="path,rule", seed=0, out_dir=str(tmp_path / "runs"),
                   save_dir=str(tmp_path / "saves"), memory_dir=str(tmp_path / "memory"))
    try:
        log = Log()
        obs = sess.start(log)
        for step in range(60):
            obs = sess.adapter.observe()
            result = sess.arbiter.step(obs)
            sess.record_step(log, step, obs, result, sess.adapter.act(result.executed) if result.executed else 0)
            sess.after_step(log, step + 1, result)
        assert "leave_house" in _done(sess.memory.planner.summary())
        assert sess.saver._done and "leave_house" in sess.saver._done
        sess.new_game(log, 60)
        assert _done(sess.memory.planner.summary()) == []
        assert sess.saver._done is None
    finally:
        sess.__exit__()
