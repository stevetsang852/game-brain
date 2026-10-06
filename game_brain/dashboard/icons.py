"""Party icons decoded from the user's own ROM (FireRed US BPRE 1.0), served as ``/icons/<id>.png``.

Recipe and addresses: notes/party-and-icons.md section 5. Nothing is shipped in the repo: every
PNG is decoded from the local ROM on first request and cached under
``~/.game-brain/icons/<rom sha1>/<id>.png`` (``$GAME_BRAIN_CONFIG_DIR`` overrides the root).

Icon ids: 0-411 species (the party's ``species_id``), 412 egg, 413-439 Unown B..Z, ``!``, ``?``.
Each PNG is 32x64 RGBA: animation frame 0 on top, frame 1 below; palette colour 0 is transparent.
"""

from __future__ import annotations

import hashlib
import os
import struct
import threading
import zlib
from pathlib import Path
from typing import Dict, Optional, Tuple

from .roms import config_dir, remembered_rom

ROM_BASE = 0x08000000
MON_ICON_TABLE = 0x083D37A0          # const u8 *gMonIconTable[440]
MON_ICON_PALETTE_INDICES = 0x083D3E80  # u8 gMonIconPaletteIndices[440], values 0-2
MON_ICON_PALETTE_TABLE = 0x083D4038  # struct SpritePalette[6] {const u16 *data; u16 tag; u16 pad}
NUM_ICONS = 440
EGG_ICON = 412
ICON_BYTES = 0x400                   # 2 frames x 16 tiles x 32 bytes, uncompressed 4bpp
NUM_PALETTES = 3                     # entries 3-5 of the palette table are unused in FireRed
WIDTH, HEIGHT = 32, 64


class IconError(Exception):
    """The ROM can't provide this icon (unreadable / not BPRE 1.0 / id out of range)."""


def is_bpre_10(rom: bytes) -> bool:
    return len(rom) >= 0x400000 and rom[0xAC:0xB0] == b"BPRE" and rom[0xBC] == 0


def _rom_offset(rom: bytes, pointer: int, size: int) -> int:
    off = pointer - ROM_BASE
    if not 0 <= off <= len(rom) - size:
        raise IconError(f"pointer {pointer:#x} is outside the ROM")
    return off


def icon_rgba(rom: bytes, icon_id: int):
    """64 rows x 32 RGBA tuples for ``icon_id`` (see notes/party-and-icons.md)."""
    if not is_bpre_10(rom):
        raise IconError("not a Pokemon FireRed (US, BPRE 1.0) ROM")
    if not 0 <= icon_id < NUM_ICONS:
        raise IconError(f"icon id {icon_id} out of range 0-{NUM_ICONS - 1}")
    ptr = struct.unpack_from("<I", rom, MON_ICON_TABLE - ROM_BASE + 4 * icon_id)[0]
    tiles_off = _rom_offset(rom, ptr, ICON_BYTES)
    pal_idx = rom[MON_ICON_PALETTE_INDICES - ROM_BASE + icon_id]
    if pal_idx >= NUM_PALETTES:
        raise IconError(f"palette index {pal_idx} out of range")
    pal_ptr = struct.unpack_from("<I", rom, MON_ICON_PALETTE_TABLE - ROM_BASE + 8 * pal_idx)[0]
    pal_off = _rom_offset(rom, pal_ptr, 32)
    pal = []
    for c in struct.unpack_from("<16H", rom, pal_off):  # BGR555
        r, g, b = c & 31, (c >> 5) & 31, (c >> 10) & 31
        pal.append((r * 255 // 31, g * 255 // 31, b * 255 // 31, 255))
    pal[0] = (0, 0, 0, 0)
    tiles = rom[tiles_off:tiles_off + ICON_BYTES]
    px = [[pal[0]] * WIDTH for _ in range(HEIGHT)]
    for t in range(32):  # tile t: frame t // 16; 4x4 tiles per frame, row-major
        frame, i = divmod(t, 16)
        ty, tx = divmod(i, 4)
        for y in range(8):
            row = px[frame * 32 + ty * 8 + y]
            for x in range(8):
                byte = tiles[32 * t + 4 * y + x // 2]
                row[tx * 8 + x] = pal[byte & 0xF if x % 2 == 0 else byte >> 4]
    return px


def encode_png(px) -> bytes:
    """Minimal RGBA8 PNG encoder (stdlib only)."""
    height, width = len(px), len(px[0])
    raw = b"".join(b"\x00" + bytes(v for p in row for v in p) for row in px)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def icon_png(rom: bytes, icon_id: int) -> bytes:
    return encode_png(icon_rgba(rom, icon_id))


def current_rom_path() -> Optional[Path]:
    """The ROM the running game uses (``$GAME_BRAIN_ROM``), else the one selected in the
    dashboard for the next launch (``~/.game-brain/rom-path.txt``)."""
    active = os.environ.get("GAME_BRAIN_ROM")
    if active:
        return Path(active).expanduser()
    try:
        return remembered_rom()
    except (OSError, ValueError):
        return None


class IconCache:
    """Decode-once cache: memory for the ROM bytes/hash, disk for the PNGs."""

    def __init__(self, rom_path_fn=current_rom_path, cache_root_fn=None):
        self._rom_path_fn = rom_path_fn
        self._cache_root_fn = cache_root_fn or (lambda: config_dir() / "icons")
        self._lock = threading.Lock()
        self._rom_key: Optional[Tuple[str, int, int]] = None
        self._rom: Optional[bytes] = None
        self._sha1: Optional[str] = None
        self._mem: Dict[Tuple[str, int], bytes] = {}

    def _load_rom(self) -> Tuple[bytes, str]:
        path = self._rom_path_fn()
        if path is None:
            raise IconError("no ROM selected")
        try:
            st = os.stat(path)
            key = (str(path), st.st_size, st.st_mtime_ns)
            if key != self._rom_key:
                data = Path(path).read_bytes()
                self._rom, self._sha1, self._rom_key = data, hashlib.sha1(data).hexdigest(), key
        except OSError as exc:
            raise IconError(f"ROM unreadable: {exc}") from exc
        return self._rom, self._sha1

    def get(self, icon_id: int) -> Tuple[bytes, str]:
        """``(png bytes, rom sha1)`` for ``icon_id``; raises :class:`IconError` (-> HTTP 404)."""
        if not 0 <= icon_id < NUM_ICONS:
            raise IconError(f"icon id {icon_id} out of range 0-{NUM_ICONS - 1}")
        with self._lock:
            rom, digest = self._load_rom()
            hit = self._mem.get((digest, icon_id))
            if hit is not None:
                return hit, digest
            cached = None
            try:
                cached = self._cache_root_fn() / digest / f"{icon_id}.png"
                if cached.is_file():
                    data = cached.read_bytes()
                    self._mem[(digest, icon_id)] = data
                    return data, digest
            except (OSError, ValueError):
                cached = None
            data = icon_png(rom, icon_id)
            self._mem[(digest, icon_id)] = data
            if cached is not None:
                try:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    tmp = cached.with_suffix(f".tmp{os.getpid()}")
                    tmp.write_bytes(data)
                    os.replace(tmp, cached)
                except OSError:
                    pass  # the cache is optional
            return data, digest
