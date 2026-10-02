"""FireRed party RAM -> ``ram["party"]``, plus the ROM name/PP tables it uses.

Addresses verified on the supplied ROM (header ``POKEMON FIRE`` / ``BPRE`` / version 0, i.e.
US 1.0; SHA1 e0194282..., not the clean dump). Method and evidence: notes/party-and-icons.md.

Shape (one dict per party slot, ``len == min(gPlayerPartyCount, 6)``)::

    {"slot": 0-5, "species_id": int, "species": "BULBASAUR" | None, "egg": bool,
     "level": int, "hp": int, "max_hp": int,
     "status": None | "sleep" | "poison" | "burn" | "freeze" | "paralysis" | "toxic",
     "sleep_turns": int,            # only present when status == "sleep"
     "moves": [{"id", "name", "pp", "max_pp"}],   # empty slots (id 0) dropped
     "active": bool}                # True for the party mon that is battler 0 in battle

A slot whose checksum does not match (the game's "Bad Egg") is reported as
``{"slot": i, "bad_egg": True}`` instead of being decoded.

``species_id`` / move ``id`` are FireRed's internal numbers (same as ``ram["battle"]``).
``species``/move ``name`` and ``max_pp`` come from the ROM's own tables (this ROM is modified:
e.g. METRONOME has 40 PP, not 10), read once when the adapter opens the ROM; they are None if
the ROM is not a recognised BPRE 1.0 layout.

In battle: ``active`` marks the party mon whose personality equals ``gBattleMons[0]``'s (only
once the adapter trusts ``ram["battle"]["player"]``; before that every mon is ``active: False``).
``hp``/``pp`` are always the party struct's values. On the ROM they are the live in-battle
values: the game writes them back to the party after every hit and every move use (4000-step
demo run: party hp == gBattleMons[0].hp and pp equal on 1669 of 1677 trusted battle steps). The
other 8 steps are level-ups, where the party struct already has the new level/max_hp/hp a few
steps before gBattleMons, so the party values stay self-consistent. Status write-back was not
observed (no status condition happened in the run).
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Sequence

# --- RAM (EWRAM) ---
G_PLAYER_PARTY = 0x02024284      # struct Pokemon gPlayerParty[6]
PARTY_MON_SIZE = 100
PARTY_SIZE = 6
G_PLAYER_PARTY_COUNT = 0x02024029  # u8 (same address as firered_extra.G_PLAYER_PARTY_COUNT)

# struct Pokemon offsets
MON_PERSONALITY, MON_OTID, MON_CHECKSUM, MON_SECURE = 0x00, 0x04, 0x1C, 0x20
MON_FLAGS = 0x13                 # bit0 isBadEgg, bit1 hasSpecies, bit2 isEgg
MON_STATUS, MON_LEVEL, MON_HP, MON_MAX_HP = 0x50, 0x54, 0x56, 0x58

# struct BattlePokemon (gBattleMons, 0x58 bytes): used to find the active party mon
BM_PERSONALITY = 0x48           # u32, copied from the party mon when it is sent out

#: substruct order by personality % 24 (G growth, A attacks, E EVs/condition, M misc)
SUBSTRUCT_ORDERS = ("GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
                    "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
                    "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
                    "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG")
#: byte offset of each substruct inside the decrypted 48 bytes, per order
_OFFSETS = [{c: 12 * i for i, c in enumerate(o)} for o in SUBSTRUCT_ORDERS]

STATUS_SLEEP = 0x07              # bits 0-2: sleep turns left
STATUS_BITS = ((0x08, "poison"), (0x10, "burn"), (0x20, "freeze"), (0x40, "paralysis"), (0x80, "toxic"))

# --- ROM tables (BPRE 1.0, file offsets = address - 0x08000000) ---
ROM_SPECIES_NAMES = 0x08245EE0   # u8 gSpeciesNames[412][11]
ROM_MOVE_NAMES = 0x08247094      # u8 gMoveNames[355][13]
ROM_BATTLE_MOVES = 0x08250C04    # struct BattleMove gBattleMoves[355], 12 bytes, pp @ +4
NUM_SPECIES, NUM_MOVES = 412, 355
SPECIES_NAME_LEN, MOVE_NAME_LEN, BATTLE_MOVE_SIZE, BATTLE_MOVE_PP = 11, 13, 12, 4

#: Gen III English charset (subset used by species/move names); 0xFF terminates
CHARSET: Dict[int, str] = {0x00: " ", 0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-", 0xB0: "…",
                           0xB4: "'", 0xB5: "♂", 0xB6: "♀", 0xB8: ",", 0xBA: "/"}
CHARSET.update({0xA1 + i: str(i) for i in range(10)})
CHARSET.update({0xBB + i: chr(ord("A") + i) for i in range(26)})
CHARSET.update({0xD5 + i: chr(ord("a") + i) for i in range(26)})


def decode_text(raw: bytes) -> str:
    out = []
    for c in raw:
        if c == 0xFF:
            break
        out.append(CHARSET.get(c, "?"))
    return "".join(out)


class RomTables:
    """Species names, move names and base PP read from the ROM image (read once)."""

    def __init__(self, species_names: Sequence[str], move_names: Sequence[str], move_pp: Sequence[int]):
        self.species_names = list(species_names)
        self.move_names = list(move_names)
        self.move_pp = list(move_pp)

    @classmethod
    def from_rom(cls, rom: bytes) -> Optional["RomTables"]:
        """Parse the BPRE 1.0 tables; None if the header/tables don't look right."""
        if len(rom) < 0x01000000 // 2 or rom[0xAC:0xB0] != b"BPRE" or rom[0xBC] != 0:
            return None
        sn, mn, bm = (a - 0x08000000 for a in (ROM_SPECIES_NAMES, ROM_MOVE_NAMES, ROM_BATTLE_MOVES))
        species = [decode_text(rom[sn + SPECIES_NAME_LEN * i: sn + SPECIES_NAME_LEN * (i + 1)])
                   for i in range(NUM_SPECIES)]
        moves = [decode_text(rom[mn + MOVE_NAME_LEN * i: mn + MOVE_NAME_LEN * (i + 1)]) for i in range(NUM_MOVES)]
        pp = [rom[bm + BATTLE_MOVE_SIZE * i + BATTLE_MOVE_PP] for i in range(NUM_MOVES)]
        # sanity check against values that are the same in every BPRE 1.0 based ROM
        if (species[1], species[4], species[7], moves[1], moves[33], pp[1], pp[33]) != (
                "BULBASAUR", "CHARMANDER", "SQUIRTLE", "POUND", "TACKLE", 35, 35):
            return None
        species[0] = moves[0] = ""
        return cls(species, moves, pp)

    def species_name(self, sid: int) -> Optional[str]:
        return self.species_names[sid] or None if 0 < sid < len(self.species_names) else None

    def move_name(self, mid: int) -> Optional[str]:
        return self.move_names[mid] or None if 0 < mid < len(self.move_names) else None

    def base_pp(self, mid: int) -> Optional[int]:
        return self.move_pp[mid] if 0 < mid < len(self.move_pp) else None


def max_pp(base: Optional[int], bonus: int) -> Optional[int]:
    """Max PP after ``bonus`` (0-3) PP Ups: base + base*bonus//5 (CalculatePPWithBonus)."""
    return None if base is None else base + base * bonus // 5


def decode_status(status: int) -> Dict[str, Any]:
    if status & STATUS_SLEEP:
        return {"status": "sleep", "sleep_turns": status & STATUS_SLEEP}
    for bit, name in STATUS_BITS:
        if status & bit:
            return {"status": name}
    return {"status": None}


def decrypt_secure(raw: bytes) -> Optional[bytes]:
    """Decrypted 48-byte substruct block of a 100-byte party mon in G,A,E,M order, or None
    if the checksum does not match (Bad Egg)."""
    pid, otid = struct.unpack_from("<II", raw, MON_PERSONALITY)
    key = pid ^ otid
    words = struct.unpack_from("<12I", raw, MON_SECURE)
    dec = struct.pack("<12I", *(w ^ key for w in words))
    if sum(struct.unpack("<24H", dec)) & 0xFFFF != struct.unpack_from("<H", raw, MON_CHECKSUM)[0]:
        return None
    off = _OFFSETS[pid % 24]
    return b"".join(dec[off[c]: off[c] + 12] for c in "GAEM")


def decode_mon(raw: bytes, slot: int, tables: Optional[RomTables] = None) -> Dict[str, Any]:
    """One 100-byte ``struct Pokemon`` -> party dict (``active`` False; see read_party)."""
    sub = decrypt_secure(raw)
    if sub is None:
        return {"slot": slot, "bad_egg": True}
    species, _item, _exp, pp_bonuses = struct.unpack_from("<HHIB", sub, 0)
    move_ids = struct.unpack_from("<4H", sub, 12)
    pps = sub[20:24]
    iv_egg_ability = struct.unpack_from("<I", sub, 36 + 4)[0]
    status, level, hp, mhp = struct.unpack_from("<IBxHH", raw, MON_STATUS)
    moves = []
    for i, mid in enumerate(move_ids):
        if mid:
            moves.append({"id": mid, "name": tables.move_name(mid) if tables else None, "pp": pps[i],
                          "max_pp": max_pp(tables.base_pp(mid), (pp_bonuses >> (2 * i)) & 3) if tables else None})
    mon: Dict[str, Any] = {"slot": slot, "species_id": species,
                           "species": tables.species_name(species) if tables else None,
                           "egg": bool(iv_egg_ability >> 30 & 1), "level": level, "hp": hp, "max_hp": mhp}
    mon.update(decode_status(status))
    mon["moves"] = moves
    mon["active"] = False
    return mon


def read_party(block: bytes, count: int, tables: Optional[RomTables] = None,
               battler0: Optional[bytes] = None) -> List[Dict[str, Any]]:
    """``block``: the 600 bytes at gPlayerParty; ``count``: gPlayerPartyCount.
    ``battler0``: the 0x58-byte gBattleMons[0] when in battle and trusted, else None. The party
    mon with the same personality is marked ``active``. All values (hp, pp, status, level)
    stay the party struct's own: the game writes hp/pp back to it on every hit / move use, so
    it is the live value (see the module docstring)."""
    party = [decode_mon(block[PARTY_MON_SIZE * i: PARTY_MON_SIZE * (i + 1)], i, tables)
             for i in range(min(max(count, 0), PARTY_SIZE))]
    if battler0 is not None:
        bpid = struct.unpack_from("<I", battler0, BM_PERSONALITY)[0]
        for mon in party:
            if not mon.get("bad_egg") and struct.unpack_from("<I", block, PARTY_MON_SIZE * mon["slot"])[0] == bpid:
                mon["active"] = True
                break
    return party
