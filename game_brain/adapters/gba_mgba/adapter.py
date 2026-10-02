"""``Adapter`` implementation on top of mGBA's Python bindings (libmgba 0.10.x).

The bindings are imported lazily, so this module (and its unit tests) import fine on a
machine without mGBA. Build/setup notes: notes/mgba-bridge.md.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from ..base import Adapter
from ...schema import BUTTONS, Action, Observation
from .firered import FireRedRam
from .firered_extra import read_extra

#: GBA KEYINPUT bit per logical button (A=0 ... L=9); "NONE" holds nothing.
KEY_BITS = {b: i for i, b in enumerate(BUTTONS) if b != "NONE"}


def keymask(button: str) -> int:
    return 0 if button == "NONE" else 1 << KEY_BITS[button]


class MgbaFireRedAdapter(Adapter):
    name = "gba_mgba/firered"

    def __init__(self, rom: Optional[str] = None, start_state: Optional[str] = None,
                 core=None, image=None):
        """``rom``: path to the FireRed ROM (default: $GAME_BRAIN_ROM). Never commit ROMs.
        ``start_state``: optional raw mGBA save-state file loaded on every reset() instead of
        power-on (local file, never committed). ``core``/``image`` are for tests (fake core)."""
        self.rom = rom or os.environ.get("GAME_BRAIN_ROM")
        self.start_state = start_state or os.environ.get("GAME_BRAIN_START_STATE") or None
        self._core = core
        self._image = image
        self._frame = 0
        self.rom_sha1: Optional[str] = None
        self.ram: Optional[FireRedRam] = None
        if self._core is None:
            self._open()

    def _open(self) -> None:
        if not self.rom or not os.path.isfile(self.rom):
            raise FileNotFoundError("FireRed ROM not found; pass rom= or set GAME_BRAIN_ROM")
        try:
            import mgba.core
            import mgba.image
            import mgba.log
        except ImportError as exc:
            raise ImportError("mGBA Python bindings not importable; see notes/mgba-bridge.md") from exc
        mgba.log.silence()
        with open(self.rom, "rb") as f:
            self.rom_sha1 = hashlib.sha1(f.read()).hexdigest()
        core = mgba.core.load_path(self.rom)
        if core is None:
            raise RuntimeError(f"mGBA could not load {self.rom}")
        w, h = core.desired_video_dimensions()
        self._image = mgba.image.Image(w, h)
        core.set_video_buffer(self._image)
        self._core = core

    # ------------------------------------------------------------------ Adapter API
    def reset(self) -> Observation:
        self._core.reset()  # power-on, no battery save loaded => deterministic
        if self.start_state:
            with open(self.start_state, "rb") as f:
                self._core.load_raw_state(f.read())
        self._core.set_keys(raw=0)
        self._frame = 0
        if self.ram is None:  # mGBA only exposes memory after the first reset()
            m = self._core.memory
            self.ram = FireRedRam(lambda a: m.u8[a], lambda a: m.u16[a], lambda a: m.u32[a])
        return self.observe()

    @property
    def frame(self) -> int:
        return self._frame

    def observe(self) -> Observation:
        if self.ram is None:
            raise RuntimeError("call reset() before observe()")
        ram = self.ram.read()
        ram.update(read_extra(self.ram, ram))  # M2 nav keys (npcs, party_count); firered_extra.py
        return Observation(frame=self._frame, game="POKEMON FIRE (BPRE)", ram=ram)

    def act(self, action: Action) -> int:
        start = self._frame
        for p in action.presses:
            self._run(keymask(p.button), p.frames)
            self._run(0, p.release_frames)
        self._core.set_keys(raw=0)
        return self._frame - start

    def _run(self, mask: int, frames: int) -> None:
        for _ in range(frames):
            self._core.set_keys(raw=mask)
            self._core.run_frame()
            self._frame += 1

    def screenshot(self, path: str) -> Optional[str]:
        if self._image is None or not hasattr(self._image, "save_png"):
            return None
        with open(path, "wb") as f:
            self._image.save_png(f)
        return path

    def close(self) -> None:
        self._core = None
        self._image = None
