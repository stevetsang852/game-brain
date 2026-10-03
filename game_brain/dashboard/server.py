"""Dashboard HTTP + WebSocket server (standard library only).

* ``GET /``    -> the single-page dashboard (``static/index.html``)
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

import ipaddress
import json
import logging
import os
import queue
import socket
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..schema import Action, ModeCommand, SchemaError, from_envelope
from . import ws
from .pacing import FrameAck, ViewConfig
from .roms import MAX_ROM_BYTES, MIN_ROM_BYTES, RomSelection

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")

#: envelope types the page may send. Everything else is refused.
INBOUND_TYPES = ("mode_command", "action", "view_config", "frame_ack", "save_game", "save_learning")
#: dashboard-only display messages, never part of the game schema (see pacing.py)
_VIEW_TYPES = {"view_config": ViewConfig.from_envelope, "frame_ack": FrameAck.from_envelope}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


@dataclass(frozen=True)
class PersistenceCommand:
    kind: str


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

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        if not (_is_loopback(host) or container_bind_allowed(host)):
            raise ValueError(f"dashboard only binds loopback addresses, not {host!r} "
                             f"(0.0.0.0 is allowed only inside the game-brain container)")
        self._clients: List[_Client] = []
        self._clients_lock = threading.Lock()
        self._inbox: "queue.Queue[Any]" = queue.Queue()
        self._snapshot: Dict[str, str] = {}  # latest envelope per type, replayed to new tabs
        self.refused: List[str] = []  # audit trail of refused inbound messages
        self.rom_selection = RomSelection()
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
                else:
                    self.send_error(404)

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
                if urlparse(self.path).path != "/api/rom":
                    self.send_json(404, {"error": "Not found"})
                    return
                origin = urlparse(self.headers.get("Origin", ""))
                if (not _is_loopback(origin.hostname or "") or origin.scheme != "http"
                        or origin.netloc != self.headers.get("Host")
                        or self.headers.get("X-Game-Brain-Upload") != "rom"
                        or self.headers.get("Content-Type") != "application/octet-stream"):
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
            if t in ("save_game", "save_learning"):
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
            client.send_text(json.dumps({"type": "error", "frame": -1, "ts": 0, "payload": {"reason": reason}}))

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
