"""Dashboard server + live loop, end to end over a real loopback WebSocket."""

import base64
import json
import os
import socket
import threading
import time

import pytest

from game_brain.arbiter import Arbiter
from game_brain.brain import make_brain
from game_brain.dashboard import DashboardServer
from game_brain.dashboard import ws
from game_brain.dashboard.live import apply_commands, run
from game_brain.schema import Action, ModeCommand, Mode, Observation, from_envelope, to_envelope


class Client:
    """Tiny blocking WebSocket client (masked frames, as browsers send)."""

    def __init__(self, port, origin=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        hdr = (f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n")
        if origin:
            hdr += f"Origin: {origin}\r\n"
        self.sock.sendall((hdr + "\r\n").encode())
        self.rfile = self.sock.makefile("rb")
        self.status_line = self.rfile.readline().decode()
        while self.rfile.readline() not in (b"\r\n", b""):
            pass
        self.expected_accept = ws.accept_key(key)

    def send(self, obj):
        self.sock.sendall(ws.encode_frame(json.dumps(obj).encode(), mask=os.urandom(4)))

    def recv(self):
        op, data = ws.read_frame(self.rfile, require_mask=False)
        assert op == ws.OP_TEXT
        return json.loads(data)

    def recv_until(self, pred, limit=200):
        for _ in range(limit):
            env = self.recv()
            if pred(env):
                return env
        raise AssertionError("message not received")

    def close(self):
        try:
            self.sock.sendall(ws.encode_frame(b"", ws.OP_CLOSE, mask=os.urandom(4)))
        finally:
            self.sock.close()


@pytest.fixture
def server():
    s = DashboardServer("127.0.0.1", 0).start()
    yield s
    s.stop()


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_frame_roundtrip_all_lengths():
    import io
    for n in (0, 5, 125, 126, 1000, 70000):
        payload = os.urandom(n)
        for mask in (None, b"\x01\x02\x03\x04"):
            raw = ws.encode_frame(payload, mask=mask)
            if n > ws.MAX_INBOUND:
                with pytest.raises(ws.WSClosed):
                    ws.read_frame(io.BytesIO(raw), require_mask=False)
                continue
            op, data = ws.read_frame(io.BytesIO(raw), require_mask=False)
            assert (op, data) == (ws.OP_TEXT, payload)


def test_unmasked_client_frame_refused():
    import io
    with pytest.raises(ws.WSClosed):
        ws.read_frame(io.BytesIO(ws.encode_frame(b"hi")), require_mask=True)


def test_refuses_non_loopback_bind(monkeypatch):
    monkeypatch.delenv("GAME_BRAIN_IN_CONTAINER", raising=False)
    with pytest.raises(ValueError):
        DashboardServer("0.0.0.0", 0)


def test_container_bind_needs_env_and_marker(tmp_path):
    from game_brain.dashboard.server import container_bind_allowed
    marker = tmp_path / ".dockerenv"
    on = {"GAME_BRAIN_IN_CONTAINER": "1"}
    assert not container_bind_allowed("0.0.0.0", env=on, markers=(str(marker),))  # env alone: no
    marker.touch()
    assert not container_bind_allowed("0.0.0.0", env={}, markers=(str(marker),))  # marker alone: no
    assert container_bind_allowed("0.0.0.0", env=on, markers=(str(marker),))      # both: yes
    for other in ("192.168.1.5", "::", "10.0.0.1"):                               # only 0.0.0.0
        assert not container_bind_allowed(other, env=on, markers=(str(marker),))


def test_serves_page(server):
    import urllib.request
    html = urllib.request.urlopen(server.url, timeout=5).read().decode()
    assert "game-brain" in html and "/ws" in html


def test_handshake_and_broadcast(server):
    c = Client(server.port)
    assert "101" in c.status_line
    assert wait_for(lambda: server.client_count == 1)
    obs = Observation(frame=42, game="MOCK", ram={"player_x": 1, "player_y": 2})
    server.broadcast(to_envelope(obs, 42, ts=1.0))
    env = c.recv()
    assert env["type"] == "observation" and env["frame"] == 42
    assert from_envelope(env).ram == {"player_x": 1, "player_y": 2}
    c.close()


def test_new_tab_gets_latest_snapshot(server):
    server.broadcast(to_envelope(Observation(frame=7), 7))
    c = Client(server.port)
    assert c.recv()["frame"] == 7
    c.close()


def test_foreign_origin_rejected(server):
    c = Client(server.port, origin="https://evil.example")
    assert "403" in c.status_line
    c.sock.close()
    assert server.client_count == 0


def test_local_origin_accepted(server):
    c = Client(server.port, origin=f"http://localhost:{server.port}")
    assert "101" in c.status_line
    c.close()


def test_inbound_commands_validated(server):
    c = Client(server.port)
    c.send(to_envelope(ModeCommand(Mode.MANUAL), 0))
    # a page claiming to be a brain is downgraded to "manual"
    c.send(to_envelope(Action.tap("A", source="brain:rule"), 0))
    c.send({"type": "observation", "frame": 0, "ts": 0, "payload": {"v": 1, "frame": 0}})  # not allowed inbound
    c.send({"type": "action", "frame": 0, "ts": 0,
            "payload": {"v": 1, "presses": [{"button": "A", "frames": 6000}]}})  # ms by mistake
    err1 = c.recv_until(lambda e: e["type"] == "error")
    err2 = c.recv_until(lambda e: e["type"] == "error")
    assert "may not be sent" in err1["payload"]["reason"]
    assert "frames" in err2["payload"]["reason"]
    msgs = []
    assert wait_for(lambda: msgs.extend(server.poll()) or len(msgs) >= 2)
    assert isinstance(msgs[0], ModeCommand) and msgs[0].mode is Mode.MANUAL
    assert isinstance(msgs[1], Action) and msgs[1].source == "manual"
    assert len(server.refused) == 2
    c.close()


def test_apply_commands_respects_arbiter_modes(server):
    arb = Arbiter([make_brain("rule")], mode="auto")
    c = Client(server.port)
    c.send(to_envelope(Action.tap("A", source="manual"), 0))
    assert wait_for(lambda: not server._inbox.empty())
    out = apply_commands(server, arb)
    assert out and "REJECTED" in out[0] and arb.pending_manual == 0
    c.send(to_envelope(ModeCommand(Mode.MANUAL), 0))
    c.send(to_envelope(Action.tap("UP", frames=8, source="manual"), 0))
    assert wait_for(lambda: server._inbox.qsize() >= 2)
    out = apply_commands(server, arb)
    assert out[0] == "mode -> manual" and "queued" in out[1] and arb.pending_manual == 1
    c.close()


def test_live_loop_with_mock_end_to_end(server, tmp_path):
    """Run the mock live; from the 'page' switch to manual and walk one tile."""
    c = Client(server.port)
    result = {}
    t = threading.Thread(target=lambda: result.update(
        run(server, "mock", mode="auto", steps=60, step_delay=0.01, out_dir=str(tmp_path), quiet=True)))
    t.start()
    first = c.recv()
    assert first["type"] in ("observation", "decision", "status")
    c.recv_until(lambda e: e["type"] == "observation" and "player_x" in e["payload"]["ram"])
    c.send(to_envelope(ModeCommand(Mode.MANUAL), 0))
    st = c.recv_until(lambda e: e["type"] == "status" and e["payload"]["mode"] == "manual")
    obs = c.recv_until(lambda e: e["type"] == "observation")
    y0 = obs["payload"]["ram"]["player_y"]
    c.send(to_envelope(Action([__import__("game_brain.schema", fromlist=["ButtonPress"]).ButtonPress("UP", 8)],
                              source="manual"), 0))
    moved = c.recv_until(lambda e: e["type"] == "observation" and e["payload"]["ram"].get("player_y") != y0)
    assert moved["payload"]["ram"]["player_y"] == y0 - 1
    t.join(timeout=10)
    assert result["mode_final"] == "manual" and result["steps"] == 60
    log_lines = [json.loads(l) for l in open(result["log"])]
    assert any(l["kind"] == "mode_change" and l.get("issued_by") == "dashboard" for l in log_lines)
    from game_brain.runlog import iter_steps  # executed_action may be deduped in the raw line
    assert any(l["executed_action"]["source"] == "manual" for l in iter_steps(result["log"]))
    c.close()


def test_live_assist_preempt_from_page(server, tmp_path):
    """Assist: brain keeps playing; one press from the page preempts it for one step (actor=human)."""
    from game_brain.schema import ButtonPress
    c = Client(server.port)
    result = {}
    t = threading.Thread(target=lambda: result.update(
        run(server, "mock", mode="assist", steps=80, step_delay=0.01, out_dir=str(tmp_path), quiet=True)))
    t.start()
    c.recv_until(lambda e: e["type"] == "observation" and "player_x" in e["payload"]["ram"])
    c.send(to_envelope(Action([ButtonPress("UP", 8)], source="manual"), 0))
    human = c.recv_until(lambda e: e["type"] == "decision" and e["payload"].get("actor") == "human")
    assert human["payload"]["mode"] == "assist" and human["payload"]["executed"] is True
    back = c.recv_until(lambda e: e["type"] == "decision")
    assert back["payload"]["actor"] == "brain"  # queue drained -> brain resumes
    t.join(timeout=10)
    steps = [json.loads(l) for l in open(result["log"]) if '"kind":"step"' in l]
    actors = [s["decision"]["actor"] for s in steps]
    assert actors.count("human") == 1 and actors.count("brain") >= 70
    assert result["mode_final"] == "assist"
    c.close()


def test_page_enables_pad_in_manual_and_assist_and_shows_actor():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "game_brain/dashboard/static/index.html").read_text(encoding="utf-8")
    assert 'const HUMAN_MODES = ["manual", "assist"]' in html
    assert 'state.mode !== "manual"' not in html  # no leftover manual-only gate
    assert 'id="actor"' in html and "d.actor ||" in html


# --------------------------------------------------------------------------- goal / path / milestones

def test_nav_fields_relayed_and_omitted_when_absent(server):
    from game_brain.schema import Decision
    c = Client(server.port)
    assert wait_for(lambda: server.client_count == 1)
    d = Decision(brain="path", plan="go", goal="出門到真新鎮", path=[[6, 6], [7, 6], [7, 5]],
                 milestones=[{"id": "M1.1", "label": "下樓", "done": True}, {"id": "M1.2", "label": "出門"}])
    server.broadcast(to_envelope(d, 1))
    p = c.recv()["payload"]
    assert p["goal"] == "出門到真新鎮" and p["path"][0] == [6, 6]
    assert p["milestones"][1] == {"id": "M1.2", "label": "出門", "done": False}
    server.broadcast(to_envelope(Decision(brain="rule", plan="x"), 2))
    p = c.recv()["payload"]
    assert not {"goal", "path", "milestones"} & p.keys()  # old-style decisions stay small
    c.close()


def _page_js():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "game_brain/dashboard/static/index.html").read_text(encoding="utf-8")
    return html.split("<script>")[1].split("</script>")[0]


NODE_HARNESS = r"""
const src = require("fs").readFileSync(0, "utf8");
const grab = name => { const i = src.indexOf("function " + name + "(");
  let depth = 0, j = src.indexOf("{", i);
  for (let k = j; k < src.length; k++) { if (src[k] === "{") depth++; else if (src[k] === "}" && --depth === 0) return src.slice(i, k + 1); } };
const calls = [];
const ctx = new Proxy({}, { get: (t, k) => k in t ? t[k] : (...a) => calls.push([k, ...a]), set: (t, k, v) => (t[k] = v, true) });
const cv = { width: 320, height: 240, getContext: () => ctx };
const els = {};
const el = () => { const e = { textContent: "", innerHTML: "", style: {}, children: [], appendChild(c) { this.children.push(c); } }; return e; };
const $ = id => els[id] || (els[id] = el());
const document = { createElement: () => el() };
eval(grab("drawMap") + grab("onMilestones") + grab("fmtRam"));
const out = {};
out.withRows = drawMap(cv, { player_x: 4, player_y: 5, map_bank: 4, map_id: 1,
  collision: ["#####", "#...#", "#...#", "#...#", "#...#", "#...#"], warps: [{ x: 3, y: 5, enter: "DOWN" }, { x: 1, y: 5, enter: null }] },
  [[4, 5], [3, 5]]);
out.lines = calls.filter(c => c[0] === "lineTo").length;
out.strokeRects = calls.filter(c => c[0] === "strokeRect").length;  // unverified warp outlined
out.noPos = drawMap(cv, { scene: "intro" }, null);
out.mockNoRows = drawMap(cv, { player_x: 2, player_y: 3 }, [[2, 3], [2, 2]]);
onMilestones({ goal: "g", milestones: [{ id: "a", label: "A", done: true }, { id: "b", label: "B", done: false }, { id: "c", label: "C", done: false }] });
out.classes = $("milestones").children.map(c => c.className);
out.bar = $("msBar").style.width; out.count = $("msCount").textContent;
onMilestones({});
out.emptyGoal = $("goal").textContent; out.emptyMs = $("milestones").innerHTML;
out.fmtCollision = fmtRam("collision", ["....", "...."]);
out.fmtWarps = fmtRam("warps", [{ enter: "UP" }, { enter: null }]);
console.log(JSON.stringify(out));
"""


def test_page_nav_rendering_logic_in_node():
    import shutil, subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(["node", "-e", NODE_HARNESS], input=_page_js(), capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["withRows"] is True and out["lines"] == 1 and out["strokeRects"] == 1
    assert out["noPos"] is False and out["mockNoRows"] is False
    assert out["classes"] == ["done", "current", ""]
    assert out["bar"] == "33%" and out["count"] == "完成 1 / 3"
    assert out["emptyGoal"] == "（大腦沒有提供目標）" and "沒有提供里程碑" in out["emptyMs"]
    assert out["fmtCollision"].startswith("2 行 × 4 格") and out["fmtWarps"] == "2 個（1 個可用）"


def test_page_has_nav_panels():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "game_brain/dashboard/static/index.html").read_text(encoding="utf-8")
    for needle in ('id="minimap"', 'id="goal"', 'id="milestones"', 'id="msBar"', "onMilestones(d)", "redrawMaps()"):
        assert needle in html
    assert "drawGrid" not in html
