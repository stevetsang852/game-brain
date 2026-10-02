"""Extra FireRed RAM readers for milestone 2 (NPC positions, party count).

TEMPORARY, written by the navigation PR (M2), **for Backend to take over**: this module is
separate from ``firered.py`` on purpose so Backend can move or rename it. The adapter calls it
through one additive line in ``MgbaFireRedAdapter.observe``.

Only addresses verified on the supplied ROM (SHA1 e0194282..., not the clean 1.0 dump) are
used. Method and results: notes/nav.md, section "Verified RAM (M2)".

Keys added to ``Observation.ram`` (overworld only, i.e. when ``player_x`` is present):

* ``npcs``: ``[{"x", "y", "prev_x", "prev_y", "elevation", "local_id", "gfx"}]``, every
  active object event on the current map except the player. Coordinates are map tiles (same
  system as ``player_x``/``collision``). ``prev_*`` is the tile an NPC is walking from; FireRed
  treats both tiles as occupied during a step.
* ``party_count``: ``u8 gPlayerPartyCount`` (0 before the starter, 1 after).
"""

from __future__ import annotations

from typing import Any, Dict, List

from .firered import G_OBJECT_EVENTS, G_PLAYER_AVATAR, MAP_OFFSET, FireRedRam

OBJECT_EVENT_SIZE = 0x24
NUM_OBJECT_EVENTS = 16
# struct ObjectEvent offsets (pokefirered): flags u32 @0 (bit0 = active), graphicsId u8 @5,
# localId u8 @8, mapNum u8 @9, mapGroup u8 @0xA, elevation low nibble @0xB,
# currentCoords s16 x,y @0x10, previousCoords s16 x,y @0x14 (map coords + MAP_OFFSET)
G_PLAYER_PARTY_COUNT = 0x02024029  # u8 gPlayerPartyCount (verified 0 -> 1 at "received BULBASAUR")


def read_npcs(ram: FireRedRam, map_bank: int, map_id: int) -> List[Dict[str, int]]:
    player = ram.u8(G_PLAYER_AVATAR + 5)
    out = []
    for i in range(NUM_OBJECT_EVENTS):
        b = G_OBJECT_EVENTS + OBJECT_EVENT_SIZE * i
        if i == player or not ram.u8(b) & 1:
            continue
        if ram.u8(b + 0xA) != map_bank & 0xFF or ram.u8(b + 9) != map_id & 0xFF:
            continue
        out.append({
            "x": ram.s16(b + 0x10) - MAP_OFFSET, "y": ram.s16(b + 0x12) - MAP_OFFSET,
            "prev_x": ram.s16(b + 0x14) - MAP_OFFSET, "prev_y": ram.s16(b + 0x16) - MAP_OFFSET,
            "elevation": ram.u8(b + 0xB) & 0x0F, "local_id": ram.u8(b + 8), "gfx": ram.u8(b + 5),
        })
    return out


def read_extra(ram: FireRedRam, base: Dict[str, Any]) -> Dict[str, Any]:
    """Extra keys for an observation whose standard keys are ``base`` (from FireRedRam.read)."""
    if "player_x" not in base:
        return {}
    return {"npcs": read_npcs(ram, base["map_bank"], base["map_id"]),
            "party_count": ram.u8(G_PLAYER_PARTY_COUNT)}
