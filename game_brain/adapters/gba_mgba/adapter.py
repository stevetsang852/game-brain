"""``Adapter`` implementation on top of mGBA's Python bindings (libmgba 0.10.x).

The bindings are imported lazily, so this module (and its unit tests) import fine on a
machine without mGBA. Build/setup notes: notes/mgba-bridge.md.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional

from ..base import Adapter
from ...schema import BUTTONS, Action, Observation
from .firered import FireRedRam
from .firered_battle import read_battle
from .firered_extra import read_extra

#: GBA KEYINPUT bit per logical button (A=0 ... L=9); "NONE" holds nothing.
KEY_BITS = {b: i for i, b in enumerate(BUTTONS) if b != "NONE"}


def keymask(button: str) -> int:
    return 0 if button == "NONE" else 1 << KEY_BITS[button]


class MgbaFireRedAdapter(Adapter):
    name = "gba_mgba/firered"
    supports_save_state = True

    def __init__(self, rom: Optional[str] = None, start_state: Optional[str] = None,
                 core=None, image=None, battery: Optional[bytes] = None):
        """``rom``: path to the FireRed ROM (default: $GAME_BRAIN_ROM). Never commit ROMs.
        ``start_state``: optional raw mGBA save-state file loaded on every reset() instead of
        power-on (local file, never committed). ``core``/``image`` are for tests (fake core).
        ``battery``: optional .sav contents for the in-memory battery file (resume).

        The battery save lives in an in-memory file, never next to the ROM: an empty one by
        default (boot is identical to "no save loaded", checked on this ROM), so battery_save()
        can back up what the in-game SAVE writes without touching any file on disk."""
        self.rom = rom or os.environ.get("GAME_BRAIN_ROM")
        self.start_state = start_state or os.environ.get("GAME_BRAIN_START_STATE") or None
        self._core = core
        self._image = image
        self._frame = 0
        self.rom_sha1: Optional[str] = None
        self.ram: Optional[FireRedRam] = None
        self._battle_ready = False  # see observe(): battle data is stale until this battle's first menu
        self._battery_vf = None
        self._battery = battery
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
        try:
            import mgba.vfs
            vf = mgba.vfs.VFile.fromEmpty()
            if self._battery:
                vf.write(self._battery, len(self._battery))
                vf.seek(0, 0)
            if core.load_save(vf):
                self._battery_vf = vf
        except Exception:   # battery backup is optional
            self._battery_vf = None
        self._core = core

    # ------------------------------------------------------------------ Adapter API
    def reset(self) -> Observation:
        self._core.reset()  # power-on, no battery save loaded => deterministic
        if self.start_state:
            with open(self.start_state, "rb") as f:
                self._core.load_raw_state(f.read())
        self._core.set_keys(raw=0)
        self._frame = 0
        self._battle_ready = False
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
        if ram.get("in_battle"):
            battle = read_battle(self.ram)  # firered_battle.py
            # gBattleMons and gBattleOutcome keep the previous battle's values for the first
            # few observations of a new battle (verified on the ROM, notes/mgba-bridge.md), so
            # only trust them once this battle has shown its first action/move menu.
            if battle["menu"] in ("action", "move"):
                self._battle_ready = True
            if not self._battle_ready:
                battle["player"] = battle["opponent"] = battle["outcome"] = None
            ram["battle"] = battle
        else:
            self._battle_ready = False
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

    # ------------------------------------------------------------------ save states
    def save_state(self) -> bytes:
        return bytes(self._core.save_raw_state())

    def adapter_state(self) -> Dict[str, Any]:
        return {"battle_ready": self._battle_ready}

    def load_state(self, data: bytes, frame: int = 0, adapter_state: Optional[Dict[str, Any]] = None) -> Observation:
        self._core.load_raw_state(data)
        self._core.set_keys(raw=0)
        self._frame = int(frame)
        self._battle_ready = bool((adapter_state or {}).get("battle_ready", False))
        return self.observe()

    def battery_save(self) -> Optional[bytes]:
        vf = self._battery_vf
        if vf is None:
            return None
        try:
            vf.seek(0, 0)
            data = bytes(vf.read_all())
        except Exception:
            return None
        if not data or data.count(0xFF) == len(data) or data.count(0) == len(data):
            return None   # blank flash: the game was never saved
        return data

    def screenshot(self, path: str) -> Optional[str]:
        if self._image is None or not hasattr(self._image, "save_png"):
            return None
        with open(path, "wb") as f:
            self._image.save_png(f)
        return path

    def close(self) -> None:
        self._core = None
        self._image = None
