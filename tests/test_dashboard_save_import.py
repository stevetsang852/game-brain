import base64
import hashlib
import json
import urllib.error
import urllib.request

from game_brain.dashboard import DashboardServer
from game_brain.dashboard.server import LoadSaveCommand
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
