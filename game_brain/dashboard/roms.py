"""Local ROM selection for the next dashboard launch (never hot-swap a running game)."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from ..savestate import _atomic_write, check_save_dir

MAX_ROM_BYTES = 32 * 1024 * 1024
MIN_ROM_BYTES = 0xC0


def config_dir() -> Path:
    return check_save_dir(os.environ.get("GAME_BRAIN_CONFIG_DIR") or Path.home() / ".game-brain")


def remembered_rom() -> Path | None:
    pointer = config_dir() / "rom-path.txt"
    if not pointer.exists():
        return None
    text = pointer.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"remembered ROM path is empty: {pointer}")
    path = Path(text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"remembered ROM not found: {path}; select a new file in the Dashboard")
    return path.resolve()


def use_remembered_rom() -> None:
    path = remembered_rom()
    if path is not None:
        os.environ["GAME_BRAIN_ROM"] = str(path)


def validate_rom(data: bytes) -> None:
    if not MIN_ROM_BYTES <= len(data) <= MAX_ROM_BYTES:
        raise ValueError("ROM must be between 192 bytes and 32 MiB")
    if data[0xB2] != 0x96 or ((sum(data[0xA0:0xBD]) + data[0xBD] + 0x19) & 0xFF):
        raise ValueError("Invalid GBA ROM header or checksum; select a .gba file")


class RomSelection:
    def __init__(self):
        self.lock = threading.Lock()

    def status(self) -> dict:
        with self.lock:
            active = os.environ.get("GAME_BRAIN_ROM")
            try:
                path = remembered_rom()
                error = None
            except (ValueError, OSError) as exc:
                path, error = None, str(exc)
            return {"active_path": active, "selected_path": str(path) if path else None,
                    "restart_required": bool(path and (not active or Path(active).resolve() != path)),
                    "error": error, "max_bytes": MAX_ROM_BYTES}

    def store(self, data: bytes) -> dict:
        validate_rom(data)
        digest = hashlib.sha1(data).hexdigest()
        with self.lock:
            root = config_dir()
            directory = root / "roms"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{digest}.gba"
            _atomic_write(path, data)
            _atomic_write(root / "rom-path.txt", (str(path) + "\n").encode("utf-8"))
        return {**self.status(), "sha1": digest}
