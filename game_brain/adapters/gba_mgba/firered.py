"""FireRed (US, BPRE) RAM map -- only addresses verified on the supplied ROM.

The supplied ROM's SHA1 (e0194282...) is NOT the clean 1.0 dump, so every address here
was checked by running it headless (method in notes/mgba-bridge.md). Anything not yet
verified is deliberately left out rather than guessed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

# --- verified addresses (pokefirered symbol names) ---
G_MAIN = 0x030030F0              # struct Main gMain
MAIN_CALLBACK2 = G_MAIN + 0x04   # u32, current scene callback
MAIN_VBLANK_COUNTER2 = G_MAIN + 0x24  # u32, +1 per frame
MAIN_HELD_KEYS = G_MAIN + 0x2C   # u16, KEYINPUT bits of held buttons
G_SAVEBLOCK1_PTR = 0x03005008    # struct SaveBlock1 *; pos @+0 (s16 x,y), location @+4 (s8 group, s8 num)
G_PLAYER_AVATAR = 0x02037078     # struct PlayerAvatar; objectEventId @+5
G_OBJECT_EVENTS = 0x02036E38     # struct ObjectEvent[16], 0x24 bytes each; facingDirection = low nibble @+0x18

CB2_OVERWORLD = 0x080565B5       # gMain.callback2 while walking around

FACING = {1: "DOWN", 2: "UP", 3: "LEFT", 4: "RIGHT"}

EWRAM = range(0x02000000, 0x02040000)


class FireRedRam:
    """Reads FireRed state through ``read(width, addr)`` (width in bytes, signed for s16/s8 via helpers)."""

    def __init__(self, u8: Callable[[int], int], u16: Callable[[int], int], u32: Callable[[int], int]):
        self.u8, self.u16, self.u32 = u8, u16, u32

    def s8(self, addr: int) -> int:
        v = self.u8(addr)
        return v - 0x100 if v & 0x80 else v

    def s16(self, addr: int) -> int:
        v = self.u16(addr)
        return v - 0x10000 if v & 0x8000 else v

    def read(self) -> Dict[str, Any]:
        cb2 = self.u32(MAIN_CALLBACK2)
        ram: Dict[str, Any] = {
            "callback2": cb2,
            "vblank_counter": self.u32(MAIN_VBLANK_COUNTER2),
            "held_keys": self.u16(MAIN_HELD_KEYS),
        }
        if cb2 != CB2_OVERWORLD:
            ram["scene"] = "other"
            return ram  # no player_x/y outside the overworld (RuleBrain mashes A)
        sb1 = self.u32(G_SAVEBLOCK1_PTR)
        if sb1 not in EWRAM:
            ram["scene"] = "other"
            return ram
        ram["scene"] = "overworld"
        ram["player_x"] = self.s16(sb1)
        ram["player_y"] = self.s16(sb1 + 2)
        ram["map_bank"] = self.s8(sb1 + 4)
        ram["map_id"] = self.s8(sb1 + 5)
        oid = self.u8(G_PLAYER_AVATAR + 5)
        face = self.u8(G_OBJECT_EVENTS + 0x24 * oid + 0x18) & 0x0F
        if face in FACING:
            ram["facing"] = FACING[face]
        return ram
