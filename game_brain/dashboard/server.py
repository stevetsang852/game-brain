"""Dashboard HTTP + WebSocket server (standard library only).

* ``GET /``    -> the single-page dashboard (``static/index.html``)
* ``GET /ws``  -> WebSocket. Server pushes envelopes ``{type, frame, ts, payload}``;
  the page sends back ``mode_command`` and ``action`` envelopes.

Security: binds to 127.0.0.1 by default and refuses to bind a non-loopback address;
WebSocket upgrades from a non-local ``Origin`` are rejected (stops other web pages
in your browser from driving the game). Inbound frames are capped at 64 KiB.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..schema import Action, ModeCommand, SchemaError, from_envelope
from . import ws

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")

#: envelope types the page may send. Everything else is refused.
INBOUND_TYPES = ("mode_command", "action")
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _is_loopback(host: str) -> bool:
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


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
        if not _is_loopback(host):
            raise ValueError(f"dashboard only binds loopback addresses, not {host!r}")
        self._clients: List[_Client] = []
        self._clients_lock = threading.Lock()
        self._inbox: "queue.Queue[Any]" = queue.Queue()
        self._snapshot: Dict[str, str] = {}  # latest envelope per type, replayed to new tabs
        self.refused: List[str] = []  # audit trail of refused inbound messages
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
                else:
                    self.send_error(404)

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._httpd.daemon_threads = True
        self.host, self.port = self._httpd.server_address[:2]
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

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
