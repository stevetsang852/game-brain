"""Auto-learn PR2 (backend): the dashboard's ``set_auto_learn`` command. No ROM needed."""
import threading

import pytest

from game_brain.brain import PathBrain
from game_brain.dashboard import DashboardServer, live
from game_brain.dashboard.server import AutoLearnCommand, CommandError, parse_command
from game_brain.runlog import read_log, replay
from game_brain.schema import Action, Decision

from test_dashboard import Client, wait_for  # noqa: E402  (same tests/ dir)


def cmd(enabled, **extra):
    return {"type": "command", "cmd": "set_auto_learn", "enabled": enabled, **extra}


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("enabled", [True, False])
def test_parse_valid_command(enabled):
    got = parse_command(cmd(enabled, frame=3, ts=0))     # extra keys are ignored
    assert isinstance(got, AutoLearnCommand) and got.enabled is enabled and got.cmd == "set_auto_learn"


@pytest.mark.parametrize("env,err_cmd,text", [
    ({"type": "command", "cmd": "set_auto_learn"}, "set_auto_learn", "'enabled': true or false"),
    (cmd(1), "set_auto_learn", "true or false"),
    (cmd("true"), "set_auto_learn", "true or false"),
    (cmd(None), "set_auto_learn", "true or false"),
    ({"type": "command", "cmd": "reboot", "enabled": True}, "reboot", "unknown command"),
    ({"type": "command", "cmd": 7, "enabled": True}, None, "string 'cmd'"),
    ({"type": "command", "enabled": True}, None, "string 'cmd'"),
])
def test_parse_refuses_bad_commands(env, err_cmd, text):
    with pytest.raises(CommandError) as e:
        parse_command(env)
    assert e.value.cmd == err_cmd and text in str(e.value)


def test_server_queues_command_and_sends_error_with_cmd_only_to_the_sender():
    with DashboardServer("127.0.0.1", 0) as server:
        a, b = Client(server.port), Client(server.port)
        a.send(cmd(True))
        got = []
        assert wait_for(lambda: got.extend(server.poll()) or got)
        assert isinstance(got[0], AutoLearnCommand) and got[0].enabled is True
        a.send(cmd("yes"))
        err = a.recv_until(lambda e: e["type"] == "error")
        assert err["payload"]["cmd"] == "set_auto_learn" and "true or false" in err["payload"]["reason"]
        a.send({"type": "command", "cmd": "reboot", "enabled": True})
        err = a.recv_until(lambda e: e["type"] == "error")
        assert err["payload"]["cmd"] == "reboot"
        got[0].reply_error("set_auto_learn failed: test")      # apply-time failure -> same tab
        err = a.recv_until(lambda e: e["type"] == "error")
        assert err["payload"] == {"reason": "set_auto_learn failed: test", "cmd": "set_auto_learn"}
        assert "error" not in server._snapshot                  # never replayed to a new tab
        b.send({"type": "mode_command", "frame": 0, "ts": 0, "payload": {"mode": "bogus"}})
        err = b.recv_until(lambda e: e["type"] == "error")
        assert "cmd" not in err["payload"]                     # other refusals unchanged
        a.close()
        b.close()


# ------------------------------------------------------------------ live loop
class Scripted:
    """Capture server whose poll() hands out commands at given steps (by poll count)."""
    url = "http://127.0.0.1:0/"
    client_count = 0

    def __init__(self, script):
        self.script, self.n, self.status, self.errors = dict(script), 0, [], []

    def broadcast(self, env):
        if env["type"] == "status":
            self.status.append(env["payload"])

    def poll(self):
        out = self.script.get(self.n, [])
        self.n += 1
        return out


def al(enabled):
    return AutoLearnCommand(enabled)


def _stand_still(monkeypatch):
    real = PathBrain.decide

    def decide(self, o):
        if self.forced_explore or o.position is None:   # intro: the rule brain mashes A
            return real(self, o)
        return Action.wait(8, source="brain:path"), Decision(brain="path", plan="stand still (test)", reason="t")
    monkeypatch.setattr(PathBrain, "decide", decide)


def _run(tmp_path, srv, steps, **kw):
    return live.run(srv, "mock-house", steps=steps, step_delay=0, screenshot_every=0,
                    out_dir=str(tmp_path / "runs"), quiet=True, save_dir=None, no_memory=True, **kw)


def test_toggle_on_and_off_via_live_loop_status_log_and_replay(tmp_path, monkeypatch):
    _stand_still(monkeypatch)
    srv = Scripted({10: [al(True)], 14: [al(False)], 16: [al(True)]})
    s = _run(tmp_path, srv, 22, stuck_steps=50)
    st = srv.status
    en = [p["auto_learn"]["enabled"] for p in st]
    assert en[:10] == [False] * 10 and en[10:14] == [True] * 4 and en[14:16] == [False] * 2 and all(en[16:])
    steps = [p["auto_learn"]["stuck_steps"] for p in st]
    assert steps[:10] == [0] * 10 and steps[10:14] == [1, 2, 3, 4]      # counts from 0 after "on"
    assert steps[14:16] == [0, 0] and steps[16:18] == [1, 2]             # off -> reset, on -> from 0
    assert any("自動學習 開" in n for n in st[10]["notes"]) and any("自動學習 關" in n for n in st[14]["notes"])
    ev = [r for r in read_log(s["log"]) if r.get("kind") == "auto_learn"]
    assert [(e["step"], e["enabled"], e["issued_by"]) for e in ev] == [(10, True, "dashboard"), (14, False, "dashboard"),
                                                                       (16, True, "dashboard")]
    assert all(e["threshold"] == 50 and isinstance(e["frame"], int) for e in ev)
    from game_brain.adapters import make_adapter
    assert replay(s["log"], make_adapter("mock-house")) == []


def test_enabled_via_command_the_stuck_switch_fires(tmp_path, monkeypatch):
    _stand_still(monkeypatch)
    srv = Scripted({8: [al(True)]})
    s = _run(tmp_path, srv, 30, stuck_steps=5)
    st = srv.status
    assert "stuck" not in st[11] and st[12]["stuck"]["step"] == 12 and st[12]["phase"] == "free_explore"
    kinds = [r["kind"] for r in read_log(s["log"]) if r.get("kind") in ("auto_learn", "stuck", "phase")]
    assert kinds[:3] == ["auto_learn", "stuck", "phase"]


def test_manual_mode_accepts_the_toggle_but_does_not_count(tmp_path, monkeypatch):
    from game_brain.schema import ModeCommand
    _stand_still(monkeypatch)
    srv = Scripted({8: [ModeCommand("manual"), al(True)], 14: [ModeCommand("auto")]})
    _run(tmp_path, srv, 20, stuck_steps=50)
    st = srv.status
    assert all(p["auto_learn"]["enabled"] for p in st[8:])
    assert [p["auto_learn"]["stuck_steps"] for p in st[8:14]] == [0] * 6     # stored, inactive
    assert any("只喺 Auto 模式生效" in n for n in st[8]["notes"])
    assert [p["auto_learn"]["stuck_steps"] for p in st[14:17]] == [1, 2, 3]  # counts once in auto


def test_apply_failure_replies_with_error_and_notes():
    replies = []
    msg = AutoLearnCommand(True, replies.append)

    class One:
        def poll(self):
            return [msg]
    from game_brain.arbiter import Arbiter
    from game_brain.brain import RuleBrain
    outcomes = live.apply_commands(One(), Arbiter([RuleBrain()]), session=None)
    assert replies == ["set_auto_learn failed: auto-learn is unavailable"]
    assert outcomes == ["error: 自動學習切換失敗：auto-learn is unavailable"]
