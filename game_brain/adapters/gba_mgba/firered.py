"""FireRed (US, BPRE) RAM map -- only addresses verified on the supplied ROM.

The supplied ROM's SHA1 (e0194282...) is NOT the clean 1.0 dump, so every address here
was checked by running it headless (method in notes/mgba-bridge.md). Anything not yet
verified is deliberately left out rather than guessed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

# --- verified addresses (pokefirered symbol names) ---
G_MAIN = 0x030030F0              # struct Main gMain
MAIN_CALLBACK2 = G_MAIN + 0x04   # u32, current scene callback
MAIN_VBLANK_COUNTER2 = G_MAIN + 0x24  # u32, +1 per frame
MAIN_HELD_KEYS = G_MAIN + 0x2C   # u16, KEYINPUT bits of held buttons
MAIN_FLAGS_439 = G_MAIN + 0x439  # u8 bitfield: bit1 = gMain.inBattle (set by the battle engine)
IN_BATTLE_BIT = 0x02
#: bool8 sScriptContext2Enabled (script.c): 1 while the player's field controls are locked -- a
#: script / text box (Oak's speech, talking to an NPC), the START menu, a door warp + auto-walk
SCRIPT_CONTEXT2_ENABLED = 0x03000F9C
G_SAVEBLOCK1_PTR = 0x03005008    # struct SaveBlock1 *; pos @+0 (s16 x,y), location @+4 (s8 group, s8 num)
G_SAVEBLOCK2_PTR = 0x0300500C    # struct SaveBlock2 *; pokedex owned/seen (notes/badge-pokedex-ram.md)
# SaveBlock2.pokedex is at +0x18. owned[] is pokedex+0x10, seen[] is pokedex+0x44.
# Bit 0 of byte 0 is national dex 1 (the game decrements the dex number before the bit test).
# Kanto national numbers 1-151 are FireRed's internal species ids. Hoenn bits are not decoded.
POKEDEX_OWNED_OFFSET = 0x28
POKEDEX_SEEN_OFFSET = 0x5C
KANTO_DEX_COUNT = 151
G_PLAYER_AVATAR = 0x02037078     # struct PlayerAvatar; objectEventId @+5
G_OBJECT_EVENTS = 0x02036E38     # struct ObjectEvent[16], 0x24 bytes each; facingDirection = low nibble @+0x18

G_MAP_HEADER = 0x02036DFC        # struct MapHeader gMapHeader; mapLayout* @+0, events* @+4
G_BACKUP_MAP_LAYOUT = 0x03005040 # VMap {s32 width, s32 height, u16 *map}; map coords are +7 (MAP_OFFSET)
MAP_OFFSET = 7
NUM_PRIMARY_METATILES = 640

CB2_OVERWORLD = 0x080565B5       # gMain.callback2 while walking around

#: metatile behavior -> button that triggers the warp from its tile (verified ones only, see notes)
WARP_ENTER = {
    0x65: "DOWN",   # MB_SOUTH_ARROW_WARP: house door mat, press DOWN while standing on it
    0x69: "UP",     # MB_ANIMATED_DOOR: building door, walk UP into it from the tile below
    0x6F: "LEFT",   # stairs in the player's house 2F, press LEFT on the stair tile
}

FACING = {1: "DOWN", 2: "UP", 3: "LEFT", 4: "RIGHT"}

EWRAM = range(0x02000000, 0x02040000)


def kanto_dex_species(flags: bytes) -> list:
    """Species ids whose Kanto dex bit is set.

    pokefirered stores national dex ``n`` at bit ``n - 1`` (it decrements before dividing by 8).
    For Kanto, that national number is also FireRed's internal species id. Bits past 151 are ignored.
    """
    out = []
    for i, byte in enumerate(flags):
        for bit in range(8):
            national = i * 8 + bit + 1
            if national > KANTO_DEX_COUNT:
                return out
            if byte & (1 << bit):
                out.append(national)
    return out


class FireRedRam:
    """Reads FireRed state through ``read(width, addr)`` (width in bytes, signed for s16/s8 via helpers)."""

    def __init__(self, u8: Callable[[int], int], u16: Callable[[int], int], u32: Callable[[int], int],
                 block: Optional[Callable[[int, int], bytes]] = None):
        self.u8, self.u16, self.u32 = u8, u16, u32
        self._block = block

    def block(self, addr: int, n: int) -> bytes:
        """``n`` bytes at ``addr`` (one fast copy if the adapter provided a block reader)."""
        if self._block is not None:
            return self._block(addr, n)
        return bytes(self.u8(addr + i) for i in range(n))

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
            # True from the battle intro until the battle ends; False during the screen
            # transition into it (callback2 is already not the overworld then). Verified on the
            # rival battle in Oak's lab; see notes/mgba-bridge.md "`in_battle`".
            "in_battle": bool(self.u8(MAIN_FLAGS_439) & IN_BATTLE_BIT),
            # field controls locked (script / text box / START menu / warp); notes/mgba-bridge.md
            "controls_locked": bool(self.u8(SCRIPT_CONTEXT2_ENABLED)),
        }
        ram.update(self._pokedex())
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
        ram.update(self._map_info(ram["map_bank"], ram["map_id"]))
        return ram

    def _pokedex(self) -> Dict[str, Any]:
        """``pokedex_owned`` / ``pokedex_seen``: Kanto species ids. Empty lists once SaveBlock2
        exists and nothing is registered. Absent when the pointer is not in EWRAM (title screen).
        Badge flags are not read: no gym has been beaten on this ROM, so those bits stay unverified
        (notes/badge-pokedex-ram.md)."""
        sb2 = self.u32(G_SAVEBLOCK2_PTR)
        if sb2 not in EWRAM:
            return {}
        return {
            "pokedex_owned": kanto_dex_species(self.block(sb2 + POKEDEX_OWNED_OFFSET, 19)),
            "pokedex_seen": kanto_dex_species(self.block(sb2 + POKEDEX_SEEN_OFFSET, 19)),
        }

    # ------------------------------------------------------------------ map layout
    def _map_info(self, bank: int, num: int) -> Dict[str, Any]:
        # re-read every observe (no cache): a few hundred u16 reads, and never stale mid-warp
        return self._read_map(self.u32(G_MAP_HEADER))

    def _metatile(self, x: int, y: int) -> int:
        vw = self.s32(G_BACKUP_MAP_LAYOUT)
        vp = self.u32(G_BACKUP_MAP_LAYOUT + 8)
        return self.u16(vp + 2 * ((y + MAP_OFFSET) * vw + x + MAP_OFFSET))

    def behavior(self, x: int, y: int) -> int:
        layout = self.u32(G_MAP_HEADER)
        mt = self._metatile(x, y) & 0x3FF
        tileset = self.u32(layout + 0x10) if mt < NUM_PRIMARY_METATILES else self.u32(layout + 0x14)
        attrs = self.u32(tileset + 0x14)
        return self.u32(attrs + 4 * (mt % NUM_PRIMARY_METATILES)) & 0x1FF

    def _read_map(self, layout: int) -> Dict[str, Any]:
        w, h = self.s32(layout), self.s32(layout + 4)
        rows = ["".join("#" if (self._metatile(x, y) >> 10) & 3 else "." for x in range(w))
                for y in range(h)]
        events = self.u32(G_MAP_HEADER + 4)
        n, base = self.u8(events + 1), self.u32(events + 8)
        warps = []
        for i in range(n):
            e = base + 8 * i
            x, y = self.s16(e), self.s16(e + 2)
            beh = self.behavior(x, y) if 0 <= x < w and 0 <= y < h else 0
            warps.append({"x": x, "y": y, "dest_bank": self.u8(e + 7), "dest_map": self.u8(e + 6),
                          "behavior": beh, "enter": WARP_ENTER.get(beh)})
        return {"map_w": w, "map_h": h, "collision": rows, "warps": warps}

    def s32(self, addr: int) -> int:
        v = self.u32(addr)
        return v - 0x100000000 if v & 0x80000000 else v
