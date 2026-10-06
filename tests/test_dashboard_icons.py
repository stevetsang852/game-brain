"""/icons/<id>.png: decoded from the local ROM, cached by ROM sha1, 404 otherwise.

The synthetic ROM below only fills the three tables the decoder reads; no game data is involved.
A real-ROM check runs when $GAME_BRAIN_ROM is a FireRed ROM.  No image is ever written to the repo.
"""
import hashlib
import os
import struct
import urllib.error
import urllib.request
import zlib

import pytest

from game_brain.dashboard import DashboardServer
from game_brain.dashboard import icons

BASE = 0x08000000
TILES = 0x500000        # synthetic icon data area
PALS = 0x480000


def png_rgba(data):
    """Decode the server's own PNGs (RGBA8, filter 0) -> (w, h, rows of RGBA tuples)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + body)
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h, depth, ctype = ihdr[:4]
    assert (depth, ctype) == (8, 6)
    raw = zlib.decompress(idat)
    rows = []
    for y in range(h):
        line = raw[y * (1 + 4 * w):(y + 1) * (1 + 4 * w)]
        assert line[0] == 0
        rows.append([tuple(line[1 + 4 * x:5 + 4 * x]) for x in range(w)])
    return w, h, rows


def synthetic_rom(variant=0):
    rom = bytearray(0x800000)
    rom[0xAC:0xB0], rom[0xBC] = b"BPRE", 0
    for p in range(3):  # palette p: colour c = (r=c+p, g=0, b=31) BGR555; colour 0 = white
        struct.pack_into("<I", rom, icons.MON_ICON_PALETTE_TABLE - BASE + 8 * p, BASE + PALS + 32 * p)
        cols = [0x7FFF] + [((c + p + variant) & 31) | (31 << 10) for c in range(1, 16)]
        struct.pack_into("<16H", rom, PALS + 32 * p, *cols)
    for i in range(icons.NUM_ICONS):
        # irregular spacing, like the real ROM: always follow the pointer
        off = TILES + i * 0x480 + (i % 3) * 0x10
        struct.pack_into("<I", rom, icons.MON_ICON_TABLE - BASE + 4 * i, BASE + off)
        rom[icons.MON_ICON_PALETTE_INDICES - BASE + i] = i % 3
        for t in range(32):  # tile t filled with colour (t % 15) + 1; tile 0 row 0 = colours 0,1,...
            v = (t % 15) + 1
            rom[off + 32 * t: off + 32 * t + 32] = bytes([v | (v << 4)]) * 32
        rom[off:off + 4] = bytes([0x10, 0x32, 0x54, 0x76])  # tile 0 row 0: 0,1,2,3,4,5,6,7
    return bytes(rom)


def test_decode_layout_palette_and_transparency():
    rom = synthetic_rom()
    px = icons.icon_rgba(rom, 4)  # palette 4 % 3 = 1
    assert len(px) == 64 and all(len(r) == 32 for r in px)
    assert px[0][0] == (0, 0, 0, 0)  # colour 0 transparent, even though the palette says white
    assert px[0][1] == (2 * 255 // 31, 0, 255, 255)  # colour 1 of palette 1 -> r = 1 + 1
    assert [p[0] for p in px[0][2:8]] == [(c + 1) * 255 // 31 for c in range(2, 8)]
    # tile 1 is right of tile 0 (colour 2); tile 4 starts row 8; tile 16 = frame 1 at row 32
    assert px[0][8][0] == 3 * 255 // 31 and px[8][0][0] == 6 * 255 // 31
    assert px[32][0] == (3 * 255 // 31, 0, 255, 255)  # tile 16 -> colour (16 % 15) + 1 = 2, r = 2 + 1
    png = icons.icon_png(rom, 4)
    assert png_rgba(png) == (32, 64, px)


@pytest.mark.parametrize("bad", [-1, 440, 9999])
def test_out_of_range_ids(bad):
    with pytest.raises(icons.IconError):
        icons.icon_rgba(synthetic_rom(), bad)


def test_rejects_non_firered_and_bad_pointers():
    rom = bytearray(synthetic_rom())
    rom[0xAC:0xB0] = b"BPGE"
    with pytest.raises(icons.IconError):
        icons.icon_rgba(bytes(rom), 1)
    rom = bytearray(synthetic_rom())
    struct.pack_into("<I", rom, icons.MON_ICON_TABLE - BASE + 4 * 7, 0x09FFFFF0)
    with pytest.raises(icons.IconError):
        icons.icon_rgba(bytes(rom), 7)
    rom[icons.MON_ICON_PALETTE_INDICES - BASE + 8] = 3
    with pytest.raises(icons.IconError):
        icons.icon_rgba(bytes(rom), 8)


def fetch(server, path, headers=None):
    req = urllib.request.Request(server.url + path.lstrip("/"), headers=headers or {})
    try:
        r = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        r = exc
    with r:
        return r.status, dict(r.headers), r.read()


def test_server_serves_caches_by_rom_hash_and_404s(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    monkeypatch.setenv("GAME_BRAIN_CONFIG_DIR", str(cfg))
    rom_path = tmp_path / "rom.gba"
    rom = synthetic_rom()
    rom_path.write_bytes(rom)
    monkeypatch.setenv("GAME_BRAIN_ROM", str(rom_path))
    digest = hashlib.sha1(rom).hexdigest()
    with DashboardServer(port=0) as server:
        code, hdr, body = fetch(server, "/icons/412.png")  # the egg
        assert code == 200 and hdr["Content-Type"] == "image/png"
        assert body == icons.icon_png(rom, 412)
        cached = cfg / "icons" / "v1" / digest / "412.png"
        assert cached.read_bytes() == body
        code, hdr2, _ = fetch(server, "/icons/412.png", {"If-None-Match": hdr["ETag"]})
        assert code == 304 and hdr["ETag"].startswith('"v1-')
        for path in ("/icons/440.png", "/icons/-1.png", "/icons/1.gif", "/icons/01a.png",
                     "/icons/1.png/x", "/icons/1234.png"):
            assert fetch(server, path)[0] == 404, path
        # the disk cache is used (decoder not called again) ...
        monkeypatch.setattr(icons, "icon_png", lambda *a: pytest.fail("decoded again"))
        server.icons._mem.clear()
        assert fetch(server, "/icons/412.png")[2] == body
        monkeypatch.undo()
        # ... and keyed by ROM hash: a different ROM gives a different cache entry
        monkeypatch.setenv("GAME_BRAIN_CONFIG_DIR", str(cfg))
        other = synthetic_rom(variant=5)
        rom2 = tmp_path / "rom2.gba"
        rom2.write_bytes(other)
        monkeypatch.setenv("GAME_BRAIN_ROM", str(rom2))
        code, _, body2 = fetch(server, "/icons/412.png")
        assert code == 200 and body2 == icons.icon_png(other, 412) and body2 != body
        assert (cfg / "icons" / "v1" / hashlib.sha1(other).hexdigest() / "412.png").is_file()
        assert not (cfg / "icons" / digest).exists()  # only under the version segment
        # unreadable / missing ROM -> 404
        monkeypatch.setenv("GAME_BRAIN_ROM", str(tmp_path / "missing.gba"))
        assert fetch(server, "/icons/1.png")[0] == 404
        rom_path.write_bytes(b"not a rom" * 100)
        monkeypatch.setenv("GAME_BRAIN_ROM", str(rom_path))
        assert fetch(server, "/icons/1.png")[0] == 404


def test_no_rom_at_all_is_404_and_selected_rom_is_used(tmp_path, monkeypatch):
    from game_brain.dashboard.roms import RomSelection

    monkeypatch.setenv("GAME_BRAIN_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("GAME_BRAIN_ROM", raising=False)
    with DashboardServer(port=0) as server:
        assert fetch(server, "/icons/1.png")[0] == 404
    # a ROM selected in the dashboard (#44) is used when no game is running with $GAME_BRAIN_ROM
    rom = bytearray(synthetic_rom())
    rom[0xB2] = 0x96
    rom[0xBD] = (-sum(rom[0xA0:0xBD]) - 0x19) & 0xFF
    RomSelection().store(bytes(rom))
    with DashboardServer(port=0) as server:
        code, _, body = fetch(server, "/icons/1.png")
        assert code == 200 and body == icons.icon_png(bytes(rom), 1)


def _real_rom():
    path = os.environ.get("GAME_BRAIN_ROM", "")
    if not os.path.isfile(path):
        return None
    data = open(path, "rb").read()
    return data if icons.is_bpre_10(data) else None


@pytest.mark.skipif(_real_rom() is None, reason="needs $GAME_BRAIN_ROM (FireRed US 1.0)")
def test_real_rom_icons():
    rom = _real_rom()
    for icon_id in (0, 1, 4, 7, 25, 201, 411, 412, 413, 439):
        px = icons.icon_rgba(rom, icon_id)
        flat = [p for row in px for p in row]
        opaque = [p for p in flat if p[3]]
        assert 50 < len(opaque) < len(flat) - 200, icon_id  # a sprite on a transparent background
        assert px[0][0][3] == 0 and px[63][31][3] == 0
        if icon_id != 0:  # the '?' placeholder has two identical frames
            assert px[:32] != px[32:]  # two different animation frames
    assert rom[icons.MON_ICON_PALETTE_INDICES - BASE + 1] == 1  # notes: Bulbasaur palette 1
    assert struct.unpack_from("<I", rom, icons.MON_ICON_TABLE - BASE + 4)[0] - BASE == 0xD3018C
    # Bulbasaur is mostly green/teal
    px = icons.icon_rgba(rom, 1)
    opaque = [p for row in px for p in row if p[3]]
    assert sum(p[1] > p[0] for p in opaque) > len(opaque) // 2
