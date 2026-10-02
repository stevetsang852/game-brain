"""Minimal RFC 6455 WebSocket helpers (text frames, ping/pong, close). Standard library only.

Only what the dashboard needs: the server never sends fragmented frames, and inbound
frames larger than ``MAX_INBOUND`` are refused (the dashboard only sends tiny commands).
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Optional, Tuple

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_INBOUND = 64 * 1024

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WSClosed(Exception):
    pass


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = OP_TEXT, mask: Optional[bytes] = None) -> bytes:
    """Encode one final frame. ``mask`` is only for client->server frames (tests)."""
    head = bytearray([0x80 | opcode])
    mbit = 0x80 if mask else 0
    n = len(payload)
    if n < 126:
        head.append(mbit | n)
    elif n < 1 << 16:
        head.append(mbit | 126)
        head += struct.pack("!H", n)
    else:
        head.append(mbit | 127)
        head += struct.pack("!Q", n)
    if mask:
        head += mask
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(head) + payload


def _read_exact(rfile, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = rfile.read(n - len(buf))
        if not chunk:
            raise WSClosed("connection closed")
        buf += chunk
    return buf


def read_frame(rfile, require_mask: bool = True) -> Tuple[int, bytes]:
    b1, b2 = _read_exact(rfile, 2)
    fin, opcode = b1 & 0x80, b1 & 0x0F
    masked, n = b2 & 0x80, b2 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", _read_exact(rfile, 2))
    elif n == 127:
        (n,) = struct.unpack("!Q", _read_exact(rfile, 8))
    if n > MAX_INBOUND:
        raise WSClosed(f"frame too large ({n} bytes)")
    if require_mask and not masked:
        raise WSClosed("client frames must be masked")
    mask = _read_exact(rfile, 4) if masked else None
    data = _read_exact(rfile, n)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    if not fin:
        raise WSClosed("fragmented frames are not supported")
    return opcode, data
