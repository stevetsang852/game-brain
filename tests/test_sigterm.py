"""SIGTERM (``docker compose down``) is handled like Ctrl-C by the CLI and the dashboard: the
current step finishes, then the final save and the summary are written and the process exits 0."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from game_brain.runlog import iter_steps, read_log, replay
from game_brain.adapters import make_adapter
from game_brain.setup import StopSignals

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name == "nt", reason="POSIX signals")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _logs(out):
    return sorted(Path(out).glob("*/run.jsonl"))


def _wait_steps(out, n, proc, timeout=60):
    t = time.time() + timeout
    while time.time() < t:
        assert proc.poll() is None, proc.communicate()
        logs = _logs(out)
        if logs and logs[0].read_text().count("\"kind\":\"step\"") >= n:
            return
        time.sleep(0.05)
    raise AssertionError("run did not start")


def _sigterm_and_check(cmd, tmp_path, signum=signal.SIGTERM):
    out, saves = tmp_path / "runs", tmp_path / "saves"
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.Popen([sys.executable, "-m", *cmd, "--adapter", "mock-house", "--out", str(out),
                             "--save-dir", str(saves), "--save-every", "0"],
                            cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_steps(out, 20, proc)
        t0 = time.time()
        proc.send_signal(signum)
        stdout, stderr = proc.communicate(timeout=10)
        took = time.time() - t0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, stderr
    assert took < 5
    assert f"{signal.Signals(signum).name}: stopping after step" in stderr
    recs = list(read_log(str(_logs(out)[0])))
    kinds = [r["kind"] for r in recs]
    assert kinds[-1] == "summary"
    summ = recs[-1]
    assert summ["stopped_by"] == signal.Signals(signum).name
    n = len(list(iter_steps(str(_logs(out)[0]))))
    assert summ["steps"] == n > 0
    stopped = [r for r in recs if r["kind"] == "stopped"]
    assert stopped and stopped[0]["step"] == n
    finals = sorted(saves.glob(f"*/{n:07d}_final.json"))
    assert len(finals) == 1 and finals[0].with_suffix(".state").is_file()
    side = json.loads(finals[0].read_text())
    assert side["step"] == n and side["reason"] == "final"
    assert replay(str(_logs(out)[0]), make_adapter("mock-house")) == []
    return stdout


def test_cli_sigterm_writes_final_save_and_summary(tmp_path):
    stdout = _sigterm_and_check(["game_brain.demo", "--steps", "100000000", "-q"], tmp_path)
    assert "=== summary ===" in stdout and "stopped_by: SIGTERM" in stdout


def test_cli_ctrl_c_is_handled_the_same(tmp_path):
    _sigterm_and_check(["game_brain.demo", "--steps", "100000000", "-q"], tmp_path, signal.SIGINT)


def test_dashboard_sigterm_writes_final_save_and_summary(tmp_path):
    stdout = _sigterm_and_check(["game_brain.dashboard", "--steps", "0", "--step-delay", "0.01",
                                 "--screenshot-every", "0", "--port", str(_free_port()), "-q"], tmp_path)
    assert "'stopped_by': 'SIGTERM'" in stdout


def test_stop_signals_only_set_a_flag_and_restore_handlers():
    before = signal.getsignal(signal.SIGTERM)
    with StopSignals() as stop:
        assert not stop.requested
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.01)
        assert stop.requested and stop.name == "SIGTERM"
        os.kill(os.getpid(), signal.SIGINT)        # a second signal changes nothing (no KeyboardInterrupt)
        time.sleep(0.01)
        assert stop.name == "SIGTERM"
    assert signal.getsignal(signal.SIGTERM) is before


class _Capture:
    url = "test://capture"
    client_count = 0

    def broadcast(self, env):
        pass

    def poll(self):
        return []


def _signal_inside_act(monkeypatch, at_call, signum):
    """Deliver ``signum`` from inside adapter.act() on its ``at_call``-th call, i.e. after the
    emulator advanced but before the step is logged and counted."""
    cls = type(make_adapter("mock-house"))
    orig, calls = cls.act, {"n": 0}

    def act(self, action):
        out = orig(self, action)
        calls["n"] += 1
        if calls["n"] == at_call:
            os.kill(os.getpid(), signum)
            time.sleep(0.01)                       # the handler has run before act() returns
        return out
    monkeypatch.setattr(cls, "act", act)


@pytest.mark.parametrize("entry", ["cli", "dashboard"])
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_between_act_and_step_increment_finishes_that_step(tmp_path, monkeypatch, entry, signum):
    from game_brain import demo
    from game_brain.dashboard import live

    def go(out, steps, resume=None, saves=None):
        kw = dict(out_dir=str(tmp_path / out), quiet=True, save_dir=str(saves or tmp_path / "saves"),
                  save_every=0, resume=resume, brains="battle,path,rule")
        if entry == "cli":
            return demo.run("mock-house", steps, "auto", **kw)
        return live.run(_Capture(), "mock-house", "auto", steps=steps, step_delay=0, screenshot_every=0, **kw)

    ref = go("ref", 80, saves=tmp_path / "ref-saves")          # uninterrupted reference
    ref_steps = {r["step"]: r for r in iter_steps(ref["log"])}
    monkeypatch.undo()
    _signal_inside_act(monkeypatch, 30, signum)
    s = go("a", 1000)
    monkeypatch.undo()
    assert s["stopped_by"] == signal.Signals(signum).name
    steps = list(iter_steps(s["log"]))
    n = len(steps)
    assert s["steps"] == n and steps[-1]["step"] == n - 1      # the interrupted step was logged in full
    assert 29 <= n < 80                                        # stopped early (act() may skip no-op steps)
    final = s["saves"][-1]
    assert Path(final).stem == f"{n:07d}_final"
    side = json.loads(Path(final).read_text())
    assert side["step"] == n
    recs = list(read_log(s["log"]))
    assert [r for r in recs if r["kind"] == "stopped"][0]["step"] == n and recs[-1]["kind"] == "summary"
    assert replay(s["log"], make_adapter("mock-house")) == []
    # game state at the final save == the reference run at that step; resuming repeats/skips nothing
    for a, b in zip(steps, list(ref_steps.values())[:n]):
        assert a["observation"]["ram"] == b["observation"]["ram"] and a["frame"] == b["frame"]
    r = go("b", 20, resume=final)
    nxt = list(iter_steps(r["log"]))
    assert nxt[0]["step"] == n
    assert nxt[0]["observation"]["ram"] == ref_steps[n]["observation"]["ram"]
    assert nxt[0]["frame"] == ref_steps[n]["frame"]
