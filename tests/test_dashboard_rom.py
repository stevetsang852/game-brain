import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from game_brain.dashboard import DashboardServer
from game_brain.dashboard.roms import MAX_ROM_BYTES, RomSelection, remembered_rom, use_remembered_rom


def synthetic_rom():
    data = bytearray(1024)
    data[0xA0:0xAC] = b"MOCK TEST   "
    data[0xAC:0xB0] = b"BPRE"
    data[0xB2] = 0x96
    data[0xBD] = (-sum(data[0xA0:0xBD]) - 0x19) & 0xFF
    return bytes(data)


def request(server, data=None, headers=None):
    req = urllib.request.Request(server.url + "api/rom", data=data, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def upload_headers(server):
    return {"Origin": server.url.rstrip("/"), "Content-Type": "application/octet-stream",
            "X-Game-Brain-Upload": "rom"}


def test_upload_is_local_persistent_and_only_applied_after_restart(tmp_path, monkeypatch):
    active = tmp_path / "active.gba"
    active.write_bytes(synthetic_rom())
    monkeypatch.setenv("GAME_BRAIN_ROM", str(active))
    data = synthetic_rom() + b"different"
    with DashboardServer(port=0) as server:
        assert request(server)[1]["selected_path"] is None
        code, info = request(server, data, upload_headers(server))
        assert code == 200 and info["restart_required"]
        assert info["sha1"] == hashlib.sha1(data).hexdigest()
        assert Path(info["selected_path"]).read_bytes() == data
        assert info["active_path"] == str(active)
        assert server.poll() == []  # never changes the running adapter or action queue
    with DashboardServer(port=0) as restarted:
        assert request(restarted)[1]["selected_path"] == info["selected_path"]
    use_remembered_rom()
    assert RomSelection().status()["active_path"] == info["selected_path"]
    assert not RomSelection().status()["restart_required"]
    assert remembered_rom() == Path(info["selected_path"])


@pytest.mark.parametrize("change", [
    {"Origin": "https://evil.example"},
    {"Origin": "http://localhost:9999"},
    {"X-Game-Brain-Upload": ""},
    {"Content-Type": "application/x-www-form-urlencoded"},
])
def test_cross_site_or_simple_uploads_rejected(change):
    with DashboardServer(port=0) as server:
        headers = {**upload_headers(server), **change}
        code, info = request(server, synthetic_rom(), headers)
        assert code == 403 and info["error"]
        assert request(server)[1]["selected_path"] is None


def test_invalid_upload_does_not_replace_good_selection():
    with DashboardServer(port=0) as server:
        assert request(server, synthetic_rom(), upload_headers(server))[0] == 200
        selected = remembered_rom()
        for data in (b"bad", b"\0" * 1024):
            code, info = request(server, data, upload_headers(server))
            assert code in (400, 413) and info["error"]
            assert remembered_rom() == selected
        code, info = request(server, b"x" * 192,
                             {**upload_headers(server), "Content-Length": str(MAX_ROM_BYTES + 1)})
        assert code == 413 and info["error"]


def test_missing_remembered_file_visible_and_not_silently_used(tmp_path, monkeypatch):
    root = tmp_path / "config"
    root.mkdir()
    (root / "rom-path.txt").write_text(str(tmp_path / "missing.gba"), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="remembered ROM"):
        use_remembered_rom()
    assert RomSelection().status()["error"]


def test_upload_refuses_repository_storage(monkeypatch):
    monkeypatch.setenv("GAME_BRAIN_CONFIG_DIR", str(Path.cwd() / "config"))
    with pytest.raises(ValueError, match="inside the repo"):
        RomSelection().store(synthetic_rom())


def test_dashboard_has_rom_picker():
    path = Path(__file__).parents[1] / "game_brain" / "dashboard" / "static" / "index.html"
    html = path.read_text(encoding="utf-8")
    for text in ('id="romFile"', 'id="romUpload"', 'id="romSelected"', 'loadRomSelection();',
                 'body: file', 'X-Game-Brain-Upload', 'async function uploadRom()'):
        assert text in html


def test_storage_failure_keeps_previous_pointer(monkeypatch):
    from game_brain.dashboard import roms
    selection = RomSelection()
    selection.store(synthetic_rom())
    original = remembered_rom()

    def fail(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(roms, "_atomic_write", fail)
    with DashboardServer(port=0) as server:
        code, info = request(server, synthetic_rom() + b"new", upload_headers(server))
        assert code == 500 and "disk full" in info["error"]
    assert remembered_rom() == original


def test_rom_selection_display_in_node():
    import shutil
    import subprocess
    from test_dashboard import NODE_HARNESS, _page_js

    if not shutil.which("node"):
        pytest.skip("node not installed")
    harness = NODE_HARNESS.split("const out = {};")[0] + r"""
eval(grab("renderRomSelection"));
renderRomSelection({ active_path: "old.gba", selected_path: "local/new.gba", restart_required: true });
const pending = { active: $("romActive").textContent, selected: $("romSelected").textContent,
                  message: $("romMessage").textContent };
renderRomSelection({ error: "missing file" });
console.log(JSON.stringify({ pending, error: $("romMessage").textContent, errorClass: $("romMessage").className }));
"""
    result = subprocess.run(["node", "-e", harness], input=_page_js(), text=True,
                            encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["pending"]["active"] == "old.gba"
    assert info["pending"]["selected"] == "local/new.gba"
    assert "重新啟動" in info["pending"]["message"]
    assert info["error"] == "missing file" and info["errorClass"] == "bad"
