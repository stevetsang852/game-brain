"""Raw changed-region frames. Header GBF1, no PNG and no base64."""

from __future__ import annotations

import struct
from typing import List, Tuple

TILE = 16
WIDTH, HEIGHT = 240, 160


def changed_tiles(previous: bytes, current: bytes, stride: int = 4) -> List[Tuple[int, int, int, int, bytes]]:
    tiles = []
    for ty in range(0, HEIGHT, TILE):
        for tx in range(0, WIDTH, TILE):
            block = bytearray()
            changed = previous == b""
            th = min(TILE, HEIGHT - ty)
            tw = min(TILE, WIDTH - tx)
            for y in range(ty, ty + th):
                start = (y * WIDTH + tx) * stride
                row = current[start:start + tw * stride]
                if not changed and row != previous[start:start + tw * stride]:
                    changed = True
                for i in range(0, len(row), stride):
                    block += row[i:i + 3]
            if changed:
                tiles.append((tx, ty, tw, th, bytes(block)))
    return tiles


def pack_frame(frame: int, tiles: List[Tuple[int, int, int, int, bytes]]) -> bytes:
    body = bytearray(struct.pack("<4sIH", b"GBF1", frame, len(tiles)))
    for x, y, w, h, rgb in tiles:
        body += struct.pack("<BBBB", x, y, w, h)
        body += rgb
    return bytes(body)
