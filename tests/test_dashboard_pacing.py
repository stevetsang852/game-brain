"""Display pacing (FPS): unit tests for Pacer, server validation, and the live loop end to end."""

import json
import threading
import time

import pytest

from game_brain.dashboard import DashboardServer
from game_brain.dashboard import live
from game_brain.dashboard.live import run
from game_brain.dashboard.pacing import FPS_MAX, FrameAck, Pacer, PacingError, ViewConfig
from game_brain.runlog import iter_steps

from test_dashboard import Client, wait_for  # noqa: E402  (same tests/ dir)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_view_config_validation_and_clamp():
    assert ViewConfig.from_envelope({"payload": {"mode": "manual", "fps": 500}}).fps == FPS_MAX
    assert ViewConfig.from_envelope({"payload": {"mode": "auto", "fps": 0}}).fps == 1
    assert ViewConfig.from_envelope({"payload": {"mode": "manual", "fps": 12.5}}).fps == 12.5
    for bad in ({"mode": "turbo", "fps": 5}, {"mode": "manual", "fps": "9"}, {"mode": "manual", "fps": True},
                {"mode": "manual", "fps": float("nan")}, {"mode": "manual"}):
        with pytest.raises(PacingError):
            ViewConfig.from_envelope({"payload": bad})
    with pytest.raises(PacingError):
        ViewConfig.from_envelope({})
    for bad in (-1, "3", None, True):
        with pytest.raises(PacingError):
            FrameAck.from_envelope({"frame": bad})


def test_cli_mode_keeps_step_delay_exactly():
    p = Pacer(step_delay=0.25, screenshot_every=2, clock=Clock())
    assert p.mode == "cli" and p.target_fps() is None and p.sleep_after(p.clock()) == 0.25
    assert [p.want_screenshot(i) for i in range(4)] == [True, False, True, False]
    assert Pacer(step_delay=0, clock=Clock()).sleep_after(0) == 0


def test_manual_fps_subtracts_step_time():
    clk = Clock(); p = Pacer(clock=clk)
    p.apply(ViewConfig("manual", 10))
    started = clk(); clk.t += 0.03
    assert p.sleep_after(started) == pytest.approx(0.07)
    clk.t += 0.5
    assert p.sleep_after(started) == 0.0  # slow step: never negative


def test_auto_does_not_wait_for_ack_and_follows_page_speed():
    clk = Clock(); p = Pacer(clock=clk)
    p.apply(ViewConfig("auto", 60))
    assert not p.want_screenshot(0, has_clients=False)  # nobody watching -> no PNGs
    assert p.want_screenshot(0) and p.target_fps() == 60
    p.sent_screenshot(10)
    clk.t += 0.05
    assert p.want_screenshot(1)  # an in-flight frame does not block the next screenshot
    p.ack(FrameAck(9, clk()))        # stale ack does not update latency
    assert p.want_screenshot(1)
    assert p.latency is None
    # page needs 0.2 s to receive + draw each frame -> ~5 steps/s
    for i in range(12):
        clk.t += 0.2
        p.ack(FrameAck(10 + i, clk()))
        assert p.want_screenshot(2 + i)
        p.sent_screenshot(11 + i)
    assert p.target_fps() == pytest.approx(5, abs=0.5)
    for i in range(20):                # fast page (5 ms) -> capped at the slider value
        clk.t += 0.005
        p.ack(FrameAck(22 + i, clk()))
        p.sent_screenshot(23 + i)
    assert p.target_fps() == 60
    p.apply(ViewConfig("auto", 15))
    assert p.target_fps() == 15
    p.sent_screenshot(50)
    clk.t += 1.5                       # page vanished: in-flight frame times out
    assert p.want_screenshot(99)


def test_server_queues_view_messages_and_refuses_bad_ones():
    with DashboardServer("127.0.0.1", 0) as server:
        c = Client(server.port)
        assert wait_for(lambda: server.client_count == 1)
        c.send({"type": "view_config", "frame": -1, "ts": 0, "payload": {"mode": "auto", "fps": 999}})
        c.send({"type": "frame_ack", "frame": 42, "ts": 0, "payload": {}})
        c.send({"type": "view_config", "frame": -1, "ts": 0, "payload": {"mode": "warp", "fps": 5}})
        err = c.recv_until(lambda e: e["type"] == "error")
        assert "view_config mode" in err["payload"]["reason"]
        got = []
        assert wait_for(lambda: got.extend(server.poll()) or len(got) >= 2)
        assert isinstance(got[0], ViewConfig) and got[0].fps == FPS_MAX
        assert isinstance(got[1], FrameAck) and got[1].frame == 42
        c.close()


def test_server_queues_save_commands_and_validates_payloads():
    from game_brain.dashboard.server import PersistenceCommand
    with DashboardServer("127.0.0.1", 0) as server:
        c = Client(server.port)
        c.send({"type": "save_game", "frame": 1, "ts": 0, "payload": {}})
        c.send({"type": "save_learning", "frame": 1, "ts": 0, "payload": {}})
        c.send({"type": "new_game", "frame": 1, "ts": 0, "payload": {}})
        c.send({"type": "save_game", "frame": 1, "ts": 0, "payload": {"path": "unsafe"}})
        err = c.recv_until(lambda e: e["type"] == "error")
        assert "payload must be an empty object" in err["payload"]["reason"]
        got = []
        assert wait_for(lambda: got.extend(server.poll()) or len(got) >= 3)
        assert got == [PersistenceCommand("save_game"), PersistenceCommand("save_learning"),
                       PersistenceCommand("new_game")]
        c.close()


def test_dashboard_manual_game_and_learning_saves(tmp_path):
    with DashboardServer("127.0.0.1", 0) as server:
        c = Client(server.port)
        result = {}
        t = threading.Thread(target=lambda: result.update(run(
            server, "mock-house", steps=80, step_delay=0.03, out_dir=str(tmp_path / "runs"), quiet=True,
            save_dir=str(tmp_path / "saves"), save_every=0, memory_dir=str(tmp_path / "memory"))))
        t.start()
        c.recv_until(lambda e: e["type"] == "status" and e["payload"]["step"] >= 2)
        c.send({"type": "save_game", "frame": 2, "ts": 0, "payload": {}})
        c.send({"type": "save_learning", "frame": 2, "ts": 0, "payload": {}})
        seen_game = seen_learning = False
        while not (seen_game and seen_learning):
            status = c.recv_until(lambda e: e["type"] == "status")
            notes = status["payload"]["notes"]
            seen_game |= any(note.startswith("已保存遊戲") for note in notes)
            seen_learning |= any(note.startswith("已保存 AI 學習資料") for note in notes)
        saves = status["payload"]["persistence"]["game_saves"]
        assert any(save["reason"] == "manual" for save in saves)
        c.close()
        t.join(timeout=10)
        assert not t.is_alive()

    events = [json.loads(line) for line in open(result["log"], encoding="utf-8") if '"kind"' in line]
    assert any(event["kind"] == "save" and event.get("reason") == "manual" for event in events)
    assert any(event["kind"] == "learning_save" for event in events)
    from game_brain.memory import inspect_memory
    memory = inspect_memory(tmp_path / "memory")
    assert memory["transitions"] > 0


def _steps(log):
    steps = []
    for s in iter_steps(log):
        s.pop("ts", None)
        if "experience" in s:
            s["experience"] = {k: v for k, v in s["experience"].items() if k not in ("run_id", "episode_id")}
        steps.append(s)
    return steps


def test_live_fps_change_takes_effect_and_log_is_unchanged(tmp_path):
    """Same seed, same steps: a run whose FPS is changed mid-way logs exactly what a plain run logs."""
    with DashboardServer("127.0.0.1", 0) as server:
        base = run(server, "mock", "auto", steps=40, step_delay=0, out_dir=str(tmp_path / "a"), quiet=True,
                   memory_dir=str(tmp_path / "a-memory"))
    with DashboardServer("127.0.0.1", 0) as server:
        c = Client(server.port)
        assert wait_for(lambda: server.client_count == 1)
        result = {}
        t = threading.Thread(target=lambda: result.update(
            # 0.05 s CLI delay: slow enough that the run cannot finish before view_config arrives
            run(server, "mock", "auto", steps=40, step_delay=0.05, out_dir=str(tmp_path / "b"), quiet=True,
                memory_dir=str(tmp_path / "b-memory"))))
        t.start()
        c.recv_until(lambda e: e["type"] == "status" and e["payload"]["step"] >= 2)
        c.send({"type": "view_config", "frame": -1, "ts": 0, "payload": {"mode": "manual", "fps": 20}})
        st = c.recv_until(lambda e: e["type"] == "status" and e["payload"]["display"]["mode"] == "manual")
        assert st["payload"]["display"]["fps"] == 20 and any("display -> manual 20" in n for n in st["payload"]["notes"])
        t0, s0 = time.monotonic(), st["payload"]["step"]
        st = c.recv_until(lambda e: e["type"] == "status" and e["payload"]["step"] >= s0 + 10)
        rate = 10 / (time.monotonic() - t0)
        assert 10 < rate < 30, rate  # ~20 steps/s instead of flat out
        t.join(timeout=20)
        c.close()
    assert _steps(base["log"]) == _steps(result["log"])
    events = [json.loads(l) for l in open(result["log"]) if '"kind"' in l]
    assert not any("display" in json.dumps(e) for e in events if e.get("kind") != "step")


def test_page_has_fps_controls():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "game_brain/dashboard/static/index.html").read_text(encoding="utf-8")
    for needle in ('id="fpsMode"', 'id="fpsSlider"', 'id="fpsNum"', '"view_config"', '"frame_ack"', 'max="60"'):
        assert needle in html


class _Server:
    url, client_count = "test://capture", 1

    def __init__(self):
        self.sent = []

    def broadcast(self, env):
        self.sent.append(env["type"])

    def poll(self):
        return [ViewConfig("auto", 60)] if not self.sent else []


def test_page_latency_clock_starts_after_the_frame_is_broadcast(tmp_path, monkeypatch):
    srv, seen = _Server(), []
    monkeypatch.setattr(live, "_screenshot_b64", lambda adapter, tmp: "iVBORw0KGgo=")
    orig = Pacer.sent_screenshot

    def spy(self, frame):
        seen.append(list(srv.sent))
        orig(self, frame)
    monkeypatch.setattr(Pacer, "sent_screenshot", spy)
    live.run(srv, "mock", "auto", steps=3, step_delay=0, out_dir=str(tmp_path), quiet=True)
    assert seen, "auto mode with a viewer should send a screenshot"
    assert all(s and s[-1] == "observation" for s in seen), seen


def test_page_asks_for_24_fps_by_default_only_while_the_run_is_on_cli_pacing():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "game_brain/dashboard/static/index.html").read_text(encoding="utf-8")
    assert "const DEFAULT_FPS = 24;" in html
    assert 'id="fpsSlider" type="range" min="1" max="60" step="1" value="24"' in html
    assert 'id="fpsNum" type="number" min="1" max="60" step="1" value="24"' in html
    # sent once per connection, and only when no tab has chosen a speed yet (mode "cli")
    assert 'if (d.mode === "cli") { $("fpsMode").value = "manual"; $("fpsNum").value = DEFAULT_FPS; sendDisplay(); }' in html


def test_page_has_separate_game_and_learning_save_buttons():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "game_brain/dashboard/static/index.html").read_text(encoding="utf-8")
    for needle in ('id="saveGameNow"', 'id="saveLearningNow"', '"save_game"', '"save_learning"'):
        assert needle in html
    assert "state.displayChecked = false;" in html
