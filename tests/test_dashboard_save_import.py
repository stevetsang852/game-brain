import base64
import hashlib
import json
import urllib.error
import urllib.request

from game_brain.dashboard import DashboardServer
from game_brain.dashboard.server import LoadSaveCommand, PersistenceCommand, SavedGameCommand
from game_brain.setup import Session
from game_brain import savestate


def upload(server, payload, origin=None):
    body = json.dumps(payload).encode()
    headers = {
        "Origin": origin or server.url.rstrip("/"),
        "Content-Type": "application/json",
        "X-Game-Brain-Upload": "save",
    }
    request = urllib.request.Request(server.url + "api/save", data=body, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def payload_for(state=b"local state"):
    sidecar = {
        "format": savestate.FORMAT,
        "format_version": savestate.FORMAT_VERSION,
        "state_file": "save.state",
        "state_sha1": hashlib.sha1(state).hexdigest(),
        "sav_file": None,
        "step": 24,
        "frame": 512,
        "adapter": "mock-house",
        "milestones_done": [],
    }
    return {
        "source_name": "save.json",
        "sidecar": sidecar,
        "state": base64.b64encode(state).decode("ascii"),
        "battery": None,
    }


def test_local_save_upload_is_validated_and_queued():
    with DashboardServer(port=0) as server:
        code, info = upload(server, payload_for())
        assert code == 202 and info["queued"]
        command = server.poll()[0]
        assert isinstance(command, LoadSaveCommand)
        assert command.source_name == "save.json"
        assert command.sidecar["step"] == 24
        assert command.state == b"local state"


def test_local_save_upload_rejects_bad_origin_and_state_hash():
    with DashboardServer(port=0) as server:
        code, info = upload(server, payload_for(), origin="http://evil.example")
        assert code == 403 and info["error"]
        invalid = payload_for()
        invalid["sidecar"]["state_sha1"] = "0" * 40
        code, info = upload(server, invalid)
        assert code == 400 and "SHA1" in info["error"]
        assert server.poll() == []


def test_local_save_load_restores_live_adapter_and_milestones(tmp_path):
    class Log:
        def __init__(self):
            self.events = []

        def event(self, kind, **fields):
            self.events.append((kind, fields))

    session = Session("mock-house", brains="path,rule", seed=0,
                      out_dir=str(tmp_path / "runs"), save_dir=str(tmp_path / "saves"),
                      no_memory=True)
    try:
        adapter = session.adapter
        adapter.x, adapter.y = 5, 6
        state = adapter.save_state()
        sidecar = {
            "format": savestate.FORMAT,
            "format_version": savestate.FORMAT_VERSION,
            "adapter": adapter.name,
            "state_sha1": hashlib.sha1(state).hexdigest(),
            "step": 17,
            "frame": 200,
            "adapter_state": {},
            "milestones_done": [],
        }
        adapter.x, adapter.y = 1, 1
        log = Log()
        session.load_imported_save(LoadSaveCommand(sidecar, state, None, "save.json"), log, 8)
        assert (adapter.x, adapter.y, adapter.frame) == (5, 6, 200)
        assert any(kind == "local_save_loaded" for kind, _ in log.events)
    finally:
        session.__exit__()


def test_dashboard_lists_saves_loads_selection_and_starts_new_game(tmp_path):
    class Log:
        def __init__(self):
            self.events = []

        def event(self, kind, **fields):
            self.events.append((kind, fields))

    saves = tmp_path / "saves"
    session = Session("mock-house", brains="path,rule", seed=7,
                      out_dir=str(tmp_path / "runs"), save_dir=str(saves), no_memory=True)
    try:
        session.adapter.x, session.adapter.y = 7, 8
        saved = session.save_game(14, [])
        status = session.dashboard_status()
        assert len(status["available_game_saves"]) == 1
        item = status["available_game_saves"][0]
        assert item["latest"] and item["step"] == 14
        assert item["save_id"].endswith("0000014_manual.json")

        session.adapter.x, session.adapter.y = 1, 2
        log = Log()
        session.load_saved_game(item["save_id"], log, step=20)
        assert (session.adapter.x, session.adapter.y) == (7, 8)
        assert session.active_save_id == item["save_id"]

        session.adapter.x, session.adapter.y = 2, 3
        session.new_game(log, step=21)
        assert (session.adapter.x, session.adapter.y) == session.adapter.start_pos
        assert session.active_save_id is None
        assert any(kind == "new_game" for kind, _ in log.events)
        assert saved["_path"]
    finally:
        session.__exit__()


def test_dashboard_auto_selects_latest_compatible_save(tmp_path):
    from game_brain.dashboard.live import _latest_compatible_save

    session = Session("mock-house", brains="path,rule", seed=2,
                      out_dir=str(tmp_path / "runs"), save_dir=str(tmp_path / "saves"), no_memory=True)
    try:
        session.adapter.x, session.adapter.y = 4, 5
        saved = session.save_game(8, [])
        assert _latest_compatible_save("mock-house", tmp_path / "saves") == saved["_path"]
        assert _latest_compatible_save("mock", tmp_path / "saves") is None
    finally:
        session.__exit__()


def test_server_accepts_save_selection_and_new_game_commands():
    with DashboardServer(port=0) as server:
        server._handle_text(None, json.dumps({
            "type": "load_saved_game",
            "payload": {"save_id": "run-id/0000010_final.json"},
        }))
        server._handle_text(None, json.dumps({"type": "new_game", "payload": {}}))
        assert server.poll() == [
            SavedGameCommand("run-id/0000010_final.json"),
            PersistenceCommand("new_game"),
        ]
