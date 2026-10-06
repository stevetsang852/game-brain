"""Status ``phase`` (running -> free_explore -> stopped), the final ``stopped`` status, the
run-id collision suffix and the truncated-last-line tolerance of run logs. No ROM needed."""
import json
import os
import signal
import warnings

import pytest

from game_brain.dashboard import live
from game_brain.runlog import TruncatedLogWarning, iter_steps, read_log
from game_brain.setup import ForcedStop, Session, claim_run_dir


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


class Log:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


def _events(log_path, kind):
    return [r for r in read_log(log_path) if r.get("kind") == kind]


def _run(tmp_path, **kw):
    srv = Capture()
    kw.setdefault("steps", 8)
    s = live.run(srv, "mock-house", step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "runs"),
                 quiet=True, save_dir=None, no_memory=True, **kw)
    return srv, s


def test_main_milestones_done_ignores_placeholders(tmp_path):
    sess = Session("mock-house", brains="battle,path,rule", seed=0, out_dir=str(tmp_path / "runs"),
                   save_dir=None, no_memory=True)
    try:
        planner = next(b.planner for b in sess.brains if hasattr(b, "planner"))
        main = [m.id for m in planner.milestones if not m.placeholder]
        assert main and any(m.placeholder for m in planner.milestones)  # FireRed: 16 + pewter_city
        log = Log()
        assert sess.phase == "running" and not sess.main_milestones_done()
        planner.restore(main[:-1])
        sess.update_phase(log, 5)
        assert sess.phase == "running" and log.events == []
        planner.restore(main)  # the placeholder (goal 17) is never "done": free explore, not the end
        assert sess.main_milestones_done()
        sess.update_phase(log, 6)
        sess.update_phase(log, 7)  # logged once
        assert sess.phase == "free_explore"
        assert log.events == [("phase", {"phase": "free_explore", "step": 6, "reason": "milestones"})]
        sess.new_game(log, 9)  # a new game starts the route again
        assert sess.phase == "running" and log.events[-1] == (
            "phase", {"phase": "running", "step": 9, "reason": "new_game"})
    finally:
        sess.adapter.close()


def test_no_planner_stays_running(tmp_path):
    sess = Session("mock", brains="rule", seed=0, out_dir=str(tmp_path / "runs"), save_dir=None, no_memory=True)
    try:
        assert not sess.main_milestones_done()
    finally:
        sess.adapter.close()


def test_free_explore_keeps_running_then_stopped_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(Session, "main_milestones_done", lambda self: self.current_step >= 3)
    srv, summary = _run(tmp_path, steps=8)
    phases = [p.get("phase") for p in srv.status]
    # after_step of step 3 (4 steps done) sees it: the status of step 3 already says free_explore
    assert phases[:3] == ["running"] * 3 and phases[3:8] == ["free_explore"] * 5  # no auto-stop
    assert summary["steps"] == 8 and summary["stopped_by"] is None
    final = srv.status[-1]
    assert final["finished"] and final["phase"] == "stopped"
    assert final["stopped"] == {"reason": "steps", "step": 8}
    ev = _events(summary["log"], "phase")
    assert [(e["phase"], e["step"]) for e in ev] == [("free_explore", 4), ("stopped", 8)]
    assert ev[1]["reason"] == "steps" and ev[1]["previous"] == "free_explore"
    kinds = [r["kind"] for r in read_log(summary["log"])]
    assert kinds.index("summary") > max(i for i, k in enumerate(kinds) if k == "phase")


@pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_stop_final_status(tmp_path, monkeypatch, signum):
    real = Session.after_step

    def after_step(self, log, steps_done, result):
        real(self, log, steps_done, result)
        if steps_done == 3:
            os.kill(os.getpid(), signum)  # handled by StopSignals: the step completes, then stop

    monkeypatch.setattr(Session, "after_step", after_step)
    srv, summary = _run(tmp_path, steps=0)
    name = signal.Signals(signum).name
    assert summary["stopped_by"] == name and summary["steps"] == 3
    assert srv.status[-1]["phase"] == "stopped"
    assert srv.status[-1]["stopped"] == {"reason": name, "step": 3}
    assert _events(summary["log"], "phase")[-1]["reason"] == name


@pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name == "nt", reason="POSIX signals")
def test_forced_stop_still_broadcasts_stopped(tmp_path, monkeypatch):
    real = Session.after_step

    def after_step(self, log, steps_done, result):
        real(self, log, steps_done, result)
        if steps_done == 2:
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGTERM)  # second signal -> ForcedStop at once

    monkeypatch.setattr(Session, "after_step", after_step)
    srv = Capture()
    with pytest.raises(ForcedStop):
        live.run(srv, "mock-house", steps=0, step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "runs"),
                 quiet=True, save_dir=None, no_memory=True)
    last = srv.status[-1]
    assert last["phase"] == "stopped" and last["finished"]
    assert last["stopped"]["reason"] == "SIGTERM" and last["stopped"]["forced"] is True


def test_claim_run_dir_suffix(tmp_path):
    a = claim_run_dir(tmp_path, "20261006T000000Z-abc")
    b = claim_run_dir(tmp_path, "20261006T000000Z-abc")
    c = claim_run_dir(tmp_path, "20261006T000000Z-abc")
    assert [x[0] for x in (a, b, c)] == ["20261006T000000Z-abc", "20261006T000000Z-abc-2",
                                          "20261006T000000Z-abc-3"]
    assert all(p.is_dir() for _, p in (a, b, c))


def test_session_never_reuses_a_run_dir(tmp_path, monkeypatch):
    import game_brain.setup as setup_mod

    monkeypatch.setattr(setup_mod, "new_run_id", lambda now=None: "20261006T000000Z")
    monkeypatch.setattr(setup_mod.uuid, "uuid4", lambda: type("U", (), {"hex": "0" * 32})())
    ids = []
    for _ in range(2):
        sess = Session("mock", brains="rule", seed=0, out_dir=str(tmp_path / "runs"), save_dir=None, no_memory=True)
        ids.append(sess.run_id)
        sess.adapter.close()
    assert ids == ["20261006T000000Z-000000000000", "20261006T000000Z-000000000000-2"]


def test_truncated_last_line_is_skipped_with_warning(tmp_path):
    srv, summary = _run(tmp_path, steps=6)
    path = summary["log"]
    full = list(read_log(path))
    steps_full = list(iter_steps(path))
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines(keepends=True)
    # kill mid-write of the last line (no trailing newline)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines[:-1]) + lines[-1][: len(lines[-1]) // 2])
    with pytest.warns(TruncatedLogWarning, match="truncated last line"):
        assert list(read_log(path)) == full[:-1]
    # a truncated step line at the end: replay / iter_steps keep every complete step
    step_idx = max(i for i, r in enumerate(full) if r.get("kind") == "step")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines[:step_idx]) + lines[step_idx][:40])
    with pytest.warns(TruncatedLogWarning):
        got = list(iter_steps(path))
    assert got == steps_full[:-1]
    # trailing blank lines after the broken line still count as "last line"
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n\n")
    with pytest.warns(TruncatedLogWarning):
        list(read_log(path))
    # a complete log reads with no warning
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert list(read_log(path)) == full


def test_corrupt_line_in_the_middle_still_raises(tmp_path):
    srv, summary = _run(tmp_path, steps=4)
    path = summary["log"]
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    lines[2] = lines[2][:10] + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    with pytest.raises(ValueError, match=r":3: corrupt run log line"):
        list(read_log(path))


MON = {"slot": 0, "species_id": 1, "species": "BULBASAUR", "egg": False, "level": 5, "hp": 22, "max_hp": 22,
       "status": None, "active": False, "moves": [{"id": 33, "name": "TACKLE", "pp": 35, "max_pp": 35}]}


def _with_party(monkeypatch):
    from game_brain.adapters.mock.house import MockHouseAdapter
    orig = MockHouseAdapter.observe

    def observe(self):
        obs = orig(self)
        obs.ram["party"] = [dict(MON)]
        return obs
    monkeypatch.setattr(MockHouseAdapter, "observe", observe)


def test_final_status_carries_party_and_phase(tmp_path, monkeypatch):
    _with_party(monkeypatch)
    srv, _ = _run(tmp_path, steps=5)
    assert all(st["party"] == [MON] and "phase" in st for st in srv.status)
    final = srv.status[-1]
    assert final["finished"] and final["phase"] == "stopped" and final["party"] == [MON]
    assert final["stopped"] == {"reason": "steps", "step": 5}


@pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name == "nt", reason="POSIX signals")
def test_forced_stop_status_carries_party(tmp_path, monkeypatch):
    _with_party(monkeypatch)
    real = Session.after_step

    def after_step(self, log, steps_done, result):
        real(self, log, steps_done, result)
        if steps_done == 3:
            os.kill(os.getpid(), signal.SIGINT)
            os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(Session, "after_step", after_step)
    srv = Capture()
    with pytest.raises(ForcedStop):
        live.run(srv, "mock-house", steps=0, step_delay=0, screenshot_every=0, out_dir=str(tmp_path / "runs"),
                 quiet=True, save_dir=None, no_memory=True)
    last = srv.status[-1]
    assert last["stopped"]["forced"] is True and last["stopped"]["reason"] == "SIGINT"
    assert last["phase"] == "stopped" and last["party"] == [MON]
