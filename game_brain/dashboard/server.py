"""Dashboard HTTP + WebSocket server (standard library only).

* ``GET /``    -> the single-page dashboard (``static/index.html``)
* ``GET /icons/<id>.png`` -> 32x64 party icon decoded from the current ROM (see :mod:`.icons`);
  404 when no readable FireRed (BPRE 1.0) ROM is available or the id is outside 0-439.
* ``GET /ws``  -> WebSocket. Server pushes envelopes ``{type, frame, ts, payload}``;
  the page sends back ``mode_command`` and ``action`` envelopes, plus the display-only
  ``view_config`` / ``frame_ack`` (see :mod:`.pacing`).

Security: binds to 127.0.0.1 by default and refuses to bind a non-loopback address;
the one exception is ``0.0.0.0`` *inside a container* (see :func:`container_bind_allowed`),
where the host side must still publish the port on 127.0.0.1 only;
WebSocket upgrades from a non-local ``Origin`` are rejected (stops other web pages
in your browser from driving the game). Inbound frames are capped at 64 KiB.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import os
import queue
import re
import socket
import threading
from dataclasses import dataclass
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .. import savestate
from ..schema import Action, ModeCommand, SchemaError, from_envelope
from . import ws
from .icons import CACHE_VERSION, IconCache, IconError
from .pacing import FrameAck, ViewConfig
from .roms import MAX_ROM_BYTES, MIN_ROM_BYTES, RomSelection

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")

#: envelope types the page may send. Everything else is refused.
INBOUND_TYPES = ("mode_command", "action", "view_config", "frame_ack", "save_game", "save_learning",
                 "load_saved_game", "new_game", "command")
#: ``{"type": "command", "cmd": ...}`` names the backend accepts (notes/dashboard-protocol.md)
COMMANDS = ("set_auto_learn",)
#: dashboard-only display messages, never part of the game schema (see pacing.py)
_VIEW_TYPES = {"view_config": ViewConfig.from_envelope, "frame_ack": FrameAck.from_envelope}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_IMPORTED_SAVE_BYTES = 32 * 1024 * 1024
MAX_SAVE_UPLOAD_BYTES = 48 * 1024 * 1024
_ICON_PATH = re.compile(r"/icons/([0-9]{1,3})\.png")


@dataclass(frozen=True)
class PersistenceCommand:
    kind: str


@dataclass(frozen=True)
class LoadSaveCommand:
    sidecar: Dict[str, Any]
    state: bytes
    battery: Optional[bytes]
    source_name: str
    save_id: Optional[str] = None


@dataclass(frozen=True)
class SavedGameCommand:
    save_id: str


class CommandError(SchemaError):
    """A refused ``command`` message; the ``error`` envelope carries its ``cmd``."""

    def __init__(self, msg: str, cmd: Optional[str]):
        super().__init__(msg)
        self.cmd = cmd


@dataclass(frozen=True)
class AutoLearnCommand:
    """``{"type": "command", "cmd": "set_auto_learn", "enabled": <bool>}``. ``reply_error(reason)``
    sends an ``error`` envelope with ``cmd: "set_auto_learn"`` to the tab that sent it, if
    applying it fails in the live loop (the page then rolls its switch back)."""
    enabled: bool
    reply_error: Callable[[str], None] = lambda reason: None

    cmd = "set_auto_learn"


def parse_command(env: Dict[str, Any], reply_error: Callable[[str], None] = lambda reason: None) -> AutoLearnCommand:
    """Validate a flat ``command`` message (other keys are ignored)."""
    cmd = env.get("cmd")
    if not isinstance(cmd, str):
        raise CommandError("command requires a string 'cmd'", None)
    if cmd not in COMMANDS:
        raise CommandError(f"unknown command {cmd[:64]!r} (known: {', '.join(COMMANDS)})", cmd[:64])
    enabled = env.get("enabled")
    if type(enabled) is not bool:   # noqa: E721 -- 1 / "true" are not booleans
        raise CommandError("set_auto_learn requires 'enabled': true or false", cmd)
    return AutoLearnCommand(enabled, reply_error)


def imported_save_command(payload: Any) -> LoadSaveCommand:
    if not isinstance(payload, dict) or not isinstance(payload.get("sidecar"), dict):
        raise ValueError("sidecar must be a JSON object")
    sidecar = payload["sidecar"]
    if sidecar.get("format") != savestate.FORMAT:
        raise ValueError("not a game-brain save sidecar")
    version = sidecar.get("format_version")
    if (not isinstance(version, int) or isinstance(version, bool) or
            not 1 <= version <= savestate.FORMAT_VERSION):
        raise ValueError("unsupported save format version")
    if not isinstance(sidecar.get("adapter"), str) or not sidecar["adapter"]:
        raise ValueError("sidecar is missing its adapter")
    if "adapter_state" in sidecar and not isinstance(sidecar["adapter_state"], dict):
        raise ValueError("adapter_state must be a JSON object")
    milestones = sidecar.get("milestones_done", [])
    if not isinstance(milestones, list) or any(not isinstance(item, str) for item in milestones):
        raise ValueError("milestones_done must be a list of strings")
    if sidecar.get("rom_sha1") is not None and not isinstance(sidecar["rom_sha1"], str):
        raise ValueError("rom_sha1 must be a string")
    for field in ("step", "frame"):
        value = sidecar.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    if not isinstance(sidecar.get("state_file"), str) or not sidecar["state_file"]:
        raise ValueError("state_file is required")
    for field in ("state_file", "sav_file"):
        name = sidecar.get(field)
        if name is not None and (not isinstance(name, str) or not name or
                                 name in (".", "..") or Path(name).name != name or
                                 "/" in name or "\\" in name):
            raise ValueError(f"{field} must be a file name, not a path")
    if not sidecar["state_file"].lower().endswith(".state"):
        raise ValueError("state_file must use the .state extension")
    if sidecar.get("sav_file") and not sidecar["sav_file"].lower().endswith(".sav"):
        raise ValueError("sav_file must use the .sav extension")
    state_encoded = payload.get("state")
    if not isinstance(state_encoded, str):
        raise ValueError("state data is required")
    state = base64.b64decode(state_encoded, validate=True)
    if not state or len(state) > MAX_IMPORTED_SAVE_BYTES:
        raise ValueError("state must be between 1 byte and 32 MiB")
    if sidecar.get("state_sha1") != sha1(state).hexdigest():
        raise ValueError("state SHA1 does not match the sidecar")
    battery_encoded = payload.get("battery")
    battery = base64.b64decode(battery_encoded, validate=True) if battery_encoded is not None else None
    if sidecar.get("sav_file") and battery is None:
        raise ValueError("sidecar references a .sav file; select it with the JSON and .state files")
    if battery is not None:
        if not battery or len(battery) > 2 * 1024 * 1024:
            raise ValueError("battery save must be between 1 byte and 2 MiB")
        if sidecar.get("sav_sha1") != sha1(battery).hexdigest():
            raise ValueError("battery SHA1 does not match the sidecar")
    source_name = payload.get("source_name")
    if (not isinstance(source_name, str) or Path(source_name).name != source_name or
            not source_name.lower().endswith(".json")):
        raise ValueError("source_name must be a JSON file name")
    # Do not trust browser-supplied paths or derived fields in a sidecar.
    sidecar = {key: value for key, value in sidecar.items() if not key.startswith("_")}
    return LoadSaveCommand(sidecar, state, battery, source_name)


def saved_game_command(save_id: str, save_dir: Path) -> LoadSaveCommand:
    relative = PurePosixPath(save_id)
    if (relative.is_absolute() or len(relative.parts) != 2 or
            any(part in ("", ".", "..") for part in relative.parts) or
            not relative.name.lower().endswith(".json")):
        raise ValueError("save_id must identify a sidecar inside the save directory")
    root = Path(save_dir).expanduser().resolve()
    path = (root / Path(*relative.parts)).resolve()
    if path.parent.parent != root or not path.is_file():
        raise FileNotFoundError(f"save not found: {save_id}")
    side = savestate.load_sidecar(path)
    if not savestate._inside(Path(side["_state_path"]), root):
        raise ValueError("save state is outside the save directory")
    state = savestate.read_state(side)
    battery = None
    if side.get("_sav_path"):
        battery_path = Path(side["_sav_path"])
        if not savestate._inside(battery_path, root):
            raise ValueError("battery save is outside the save directory")
        battery = battery_path.read_bytes()
    command = imported_save_command({
        "source_name": path.name,
        "sidecar": side,
        "state": base64.b64encode(state).decode("ascii"),
        "battery": base64.b64encode(battery).decode("ascii") if battery is not None else None,
    })
    return LoadSaveCommand(command.sidecar, command.state, command.battery, command.source_name, save_id)


def _is_loopback(host: str) -> bool:
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


#: set by the Dockerfile; together with a container marker file it allows ``0.0.0.0``.
CONTAINER_ENV = "GAME_BRAIN_IN_CONTAINER"
_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def container_bind_allowed(host: str, env=None, markers=_CONTAINER_MARKERS) -> bool:
    """``0.0.0.0`` is allowed only inside a container, i.e. when BOTH the env flag
    ``GAME_BRAIN_IN_CONTAINER=1`` is set AND a docker/podman marker file exists.

    Docker's ``-p 127.0.0.1:8765:8765`` forwards to the container's own network
    interface, not its loopback, so the server must listen on 0.0.0.0 *in the
    container*; the host still only exposes 127.0.0.1. Any other address (a LAN IP,
    ``::``) stays refused, and the env flag alone does nothing on a bare machine.
    """
    env = os.environ if env is None else env
    if host != "0.0.0.0" or env.get(CONTAINER_ENV) != "1":
        return False
    return any(os.path.exists(m) for m in markers)


class _Client:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.lock = threading.Lock()
        self.alive = True

    def send_text(self, text: str) -> None:
        data = ws.encode_frame(text.encode("utf-8"))
        with self.lock:
            if not self.alive:
                return
            try:
                self.sock.sendall(data)
            except OSError:
                self.alive = False

    def send_raw(self, opcode: int, payload: bytes = b"") -> None:
        with self.lock:
            try:
                self.sock.sendall(ws.encode_frame(payload, opcode))
            except OSError:
                self.alive = False


class DashboardServer:
    """Runs in a background thread. Use :meth:`broadcast` to push, :meth:`poll` to read commands."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, save_dir=None):
        if not (_is_loopback(host) or container_bind_allowed(host)):
            raise ValueError(f"dashboard only binds loopback addresses, not {host!r} "
                             f"(0.0.0.0 is allowed only inside the game-brain container)")
        self._clients: List[_Client] = []
        self._clients_lock = threading.Lock()
        self._inbox: "queue.Queue[Any]" = queue.Queue()
        self._snapshot: Dict[str, str] = {}  # latest envelope per type, replayed to new tabs
        self.refused: List[str] = []  # audit trail of refused inbound messages
        self.rom_selection = RomSelection()
        self.icons = IconCache()
        self.save_dir = Path(save_dir).expanduser().resolve() if save_dir else None
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # keep the console quiet
                log.debug("http: " + fmt, *args)

            def do_GET(self):
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    body = (STATIC / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/ws":
                    server._upgrade(self)
                elif path == "/api/rom":
                    self.send_json(200, server.rom_selection.status())
                elif _ICON_PATH.fullmatch(path):
                    self.send_icon(int(_ICON_PATH.fullmatch(path).group(1)))
                elif path == "/api/saves":
                    self.send_json(200, {"saves": savestate.list_game_saves(server.save_dir)
                                         if server.save_dir else []})
                else:
                    self.send_error(404)

            def send_icon(self, icon_id):
                try:
                    body, digest = server.icons.get(icon_id)
                except IconError as exc:
                    log.debug("icon %s: %s", icon_id, exc)
                    self.send_error(404)
                    return
                etag = f'"{CACHE_VERSION}-{digest[:16]}-{icon_id}"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("ETag", etag)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def send_json(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                # Custom header + same-origin check prevents cross-site form uploads.
                self.close_connection = True
                path = urlparse(self.path).path
                if path == "/api/save/load":
                    self.post_save_load()
                    return
                if path == "/api/save":
                    self.post_save()
                    return
                if path != "/api/rom":
                    self.send_json(404, {"error": "Not found"})
                    return
                origin = urlparse(self.headers.get("Origin", ""))
                if (not _is_loopback(origin.hostname or "") or origin.scheme != "http"
                        or origin.netloc != self.headers.get("Host")
                        or self.headers.get("X-Game-Brain-Upload") != "rom"
                        or self.headers.get("Content-Type") != "application/octet-stream"):
                    # Consume small rejected bodies before closing to avoid a TCP reset that
                    # can hide the 403 from the browser on Windows.
                    try:
                        length = int(self.headers.get("Content-Length", ""))
                        if not self.headers.get("Transfer-Encoding") and 0 <= length <= 64 * 1024:
                            self.connection.settimeout(1)
                            self.rfile.read(length)
                    except (OSError, ValueError):
                        pass
                    self.send_json(403, {"error": "ROM uploads require a same-origin Dashboard request"})
                    return
                if self.headers.get("Transfer-Encoding"):
                    self.send_json(400, {"error": "Chunked ROM uploads are not supported"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self.send_json(411, {"error": "ROM upload requires Content-Length"})
                    return
                if not MIN_ROM_BYTES <= size <= MAX_ROM_BYTES:
                    self.send_json(413, {"error": "ROM must be between 192 bytes and 32 MiB"})
                    return
                try:
                    self.connection.settimeout(30)
                    data = self.rfile.read(size)
                    if len(data) != size:
                        raise ValueError("ROM upload was incomplete")
                    result = server.rom_selection.store(data)
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                except OSError as exc:
                    log.error("ROM upload failed: %s", exc)
                    self.send_json(500, {"error": f"Could not save ROM locally: {exc}"})
                    return
                self.send_json(200, result)

            def post_save(self):
                origin = urlparse(self.headers.get("Origin", ""))
                if (not _is_loopback(origin.hostname or "") or origin.scheme != "http"
                        or origin.netloc != self.headers.get("Host")
                        or self.headers.get("X-Game-Brain-Upload") != "save"
                        or self.headers.get("Content-Type") != "application/json"):
                    # Consume small rejected bodies before closing, avoiding a TCP reset
                    # that can hide the 403 from browser fetch on Windows.
                    try:
                        length = int(self.headers.get("Content-Length", ""))
                        if not self.headers.get("Transfer-Encoding") and 0 <= length <= 64 * 1024:
                            self.connection.settimeout(1)
                            self.rfile.read(length)
                    except (OSError, ValueError):
                        pass
                    self.send_json(403, {"error": "Save uploads require a same-origin Dashboard request"})
                    return
                if self.headers.get("Transfer-Encoding"):
                    self.send_json(400, {"error": "Chunked save uploads are not supported"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self.send_json(411, {"error": "Save upload requires Content-Length"})
                    return
                if not 0 < size <= MAX_SAVE_UPLOAD_BYTES:
                    self.send_json(413, {"error": "Save upload is too large"})
                    return
                try:
                    self.connection.settimeout(30)
                    data = self.rfile.read(size)
                    if len(data) != size:
                        raise ValueError("Save upload was incomplete")
                    command = imported_save_command(json.loads(data))
                except (ValueError, TypeError, KeyError, binascii.Error) as exc:
                    self.send_json(400, {"error": f"Invalid save upload: {exc}"})
                    return
                server._inbox.put(command)
                self.send_json(202, {"queued": True, "source_name": command.source_name})

            def post_save_load(self):
                origin = urlparse(self.headers.get("Origin", ""))
                if (not _is_loopback(origin.hostname or "") or origin.scheme != "http"
                        or origin.netloc != self.headers.get("Host")
                        or self.headers.get("X-Game-Brain-Upload") != "save"
                        or self.headers.get("Content-Type") != "application/json"):
                    self.send_json(403, {"error": "Save loading requires a same-origin Dashboard request"})
                    return
                if self.headers.get("Transfer-Encoding"):
                    self.send_json(400, {"error": "Chunked save requests are not supported"})
                    return
                try:
                    size = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self.send_json(411, {"error": "Save request requires Content-Length"})
                    return
                if not 0 < size <= 4096:
                    self.send_json(413, {"error": "Save request is too large"})
                    return
                try:
                    self.connection.settimeout(5)
                    data = self.rfile.read(size)
                    if len(data) != size:
                        raise ValueError("Save request was incomplete")
                    request = json.loads(data)
                    save_id = request.get("save_id") if isinstance(request, dict) else None
                    if not isinstance(save_id, str) or not server.save_dir:
                        raise ValueError("save_id is required")
                    command = saved_game_command(save_id, server.save_dir)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    self.send_json(400, {"error": f"Could not load selected save: {exc}"})
                    return
                server._inbox.put(command)
                self.send_json(202, {"queued": True, "source_name": command.source_name})

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._httpd.daemon_threads = True
        self.host, self.port = self._httpd.server_address[:2]
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def url(self) -> str:
        # in the container we listen on 0.0.0.0, but the host publishes 127.0.0.1 only
        host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
        return f"http://{host}:{self.port}/"

    def start(self) -> "DashboardServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="dashboard", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for c in clients:
            c.send_raw(ws.OP_CLOSE)
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return sum(c.alive for c in self._clients)

    # ------------------------------------------------------------------ outbound
    def broadcast(self, envelope: Dict[str, Any]) -> None:
        text = json.dumps(envelope, separators=(",", ":"))
        self._snapshot[envelope["type"]] = text
        with self._clients_lock:
            self._clients = [c for c in self._clients if c.alive]
            clients = list(self._clients)
        for c in clients:
            c.send_text(text)

    # ------------------------------------------------------------------ inbound
    def poll(self) -> List[Any]:
        """Return every validated ModeCommand / Action received since the last call."""
        out = []
        while True:
            try:
                out.append(self._inbox.get_nowait())
            except queue.Empty:
                return out

    def _handle_text(self, client: _Client, text: str) -> None:
        try:
            env = json.loads(text)
            t = env.get("type") if isinstance(env, dict) else None
            if t not in INBOUND_TYPES:
                raise SchemaError(f"type {t!r} may not be sent by the dashboard (allowed: {INBOUND_TYPES})")
            if t in _VIEW_TYPES:
                self._inbox.put(_VIEW_TYPES[t](env))
                return
            if t == "load_saved_game":
                payload = env.get("payload")
                save_id = payload.get("save_id") if isinstance(payload, dict) else None
                parts = save_id.split("/") if isinstance(save_id, str) else []
                if (len(parts) != 2 or any(not part or part in (".", "..") for part in parts)
                        or any("\\" in part for part in parts)):
                    raise SchemaError("load_saved_game requires a save id from the Dashboard")
                self._inbox.put(SavedGameCommand(save_id))
                return
            if t == "command":
                self._inbox.put(parse_command(
                    env, lambda reason: self.send_error(client, reason, cmd="set_auto_learn")))
                return
            if t in ("save_game", "save_learning", "new_game"):
                if not isinstance(env.get("payload"), dict) or env["payload"]:
                    raise SchemaError(f"{t} payload must be an empty object")
                self._inbox.put(PersistenceCommand(t))
                return
            msg = from_envelope(env)
            if isinstance(msg, Action):  # the page can never claim to be a brain
                msg = Action(list(msg.presses), source="manual")
            self._inbox.put(msg)
        except (ValueError, TypeError, KeyError, SchemaError) as exc:
            reason = f"refused inbound message: {exc}"
            self.refused.append(reason)
            if isinstance(exc, CommandError) and exc.cmd is not None:
                self.send_error(client, reason, cmd=exc.cmd)
            else:
                self.send_error(client, reason)

    def send_error(self, client: "_Client", reason: str, **extra: Any) -> None:
        """``error`` envelope to one tab only (never broadcast, never part of the snapshot);
        ``extra`` e.g. ``cmd`` for a refused / failed ``command``."""
        client.send_text(json.dumps({"type": "error", "frame": -1, "ts": 0, "payload": {"reason": reason, **extra}}))

    def _upgrade(self, h: BaseHTTPRequestHandler) -> None:
        key = h.headers.get("Sec-WebSocket-Key")
        if h.headers.get("Upgrade", "").lower() != "websocket" or not key:
            h.send_error(400, "expected a WebSocket upgrade")
            return
        origin = h.headers.get("Origin")
        if origin and not _is_loopback(urlparse(origin).hostname or ""):
            h.send_error(403, "non-local origin")
            return
        h.send_response(101, "Switching Protocols")
        h.send_header("Upgrade", "websocket")
        h.send_header("Connection", "Upgrade")
        h.send_header("Sec-WebSocket-Accept", ws.accept_key(key))
        h.end_headers()
        h.wfile.flush()
        h.close_connection = True

        client = _Client(h.connection)
        for text in list(self._snapshot.values()):
            client.send_text(text)
        with self._clients_lock:
            self._clients.append(client)
        try:
            while client.alive:
                opcode, data = ws.read_frame(h.rfile)
                if opcode == ws.OP_TEXT:
                    self._handle_text(client, data.decode("utf-8", "replace"))
                elif opcode == ws.OP_PING:
                    client.send_raw(ws.OP_PONG, data)
                elif opcode == ws.OP_CLOSE:
                    client.send_raw(ws.OP_CLOSE)
                    break
        except (ws.WSClosed, OSError):
            pass
        finally:
            client.alive = False
