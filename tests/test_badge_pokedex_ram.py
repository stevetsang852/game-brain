"""Pokédex owned/seen on the verified FireRed ROM. Badge bits are not exposed.

The real-save test is skipped unless mGBA imports and $GAME_BRAIN_ROM is a file.
It loads local milestone sidecars (never committed) and checks the bits measured in
notes/badge-pokedex-ram.md. The bit decoder itself does not need the ROM.
"""
import json
import os
from pathlib import Path

import pytest

from game_brain.adapters.gba_mgba.firered import (
    G_SAVEBLOCK1_PTR,
    G_SAVEBLOCK2_PTR,
    POKEDEX_OWNED_OFFSET,
    POKEDEX_SEEN_OFFSET,
    FireRedRam,
    kanto_dex_species,
)

# pret hypotheses confirmed by the before/after in the note. Not ram keys.
_FLAGS_OFFSET = 0x0EE0
_BADGE_BYTE = 0x820 // 8          # flag ids 0x820-0x827; UNVERIFIED as badges (never set)
_SYS_BYTE = 0x828 // 8            # bit0 FLAG_SYS_POKEMON_GET, bit1 FLAG_SYS_POKEDEX_GET
_RUN = Path.home() / ".game-brain/saves/20261002-081843"
_ROM_SHA1 = "e0194282c427689768f8e618a285552f264524a4"


def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


def test_kanto_dex_bit0_is_species_1_and_stops_at_151():
    assert kanto_dex_species(b"\x01") == [1]
    assert kanto_dex_species(b"\x09\x80\x04") == [1, 4, 16, 19]
    assert kanto_dex_species(b"\x00\x00") == []
    # byte 18 bit 7 is national 152, past Kanto; bits 0-6 of that byte are 145-151
    flags = bytes(18) + bytes([0xFF])
    assert kanto_dex_species(flags) == list(range(145, 152))


def test_pokedex_keys_absent_until_saveblock2_pointer_is_in_ewram():
    mem = {}

    def u8(addr):
        return mem.get(addr, 0) & 0xFF

    def u16(addr):
        return u8(addr) | (u8(addr + 1) << 8)

    def u32(addr):
        return u16(addr) | (u16(addr + 2) << 16)

    ram = FireRedRam(u8, u16, u32)
    assert "pokedex_owned" not in ram.read()
    sb2 = 0x020245DC
    mem[G_SAVEBLOCK2_PTR] = sb2 & 0xFF
    mem[G_SAVEBLOCK2_PTR + 1] = (sb2 >> 8) & 0xFF
    mem[G_SAVEBLOCK2_PTR + 2] = (sb2 >> 16) & 0xFF
    mem[G_SAVEBLOCK2_PTR + 3] = (sb2 >> 24) & 0xFF
    mem[sb2 + POKEDEX_OWNED_OFFSET] = 0x01          # species 1
    mem[sb2 + POKEDEX_SEEN_OFFSET] = 0x09            # species 1 and 4
    got = ram.read()
    assert got["pokedex_owned"] == [1] and got["pokedex_seen"] == [1, 4]
    assert "badges" not in got


def _load(adapter, name):
    path = _RUN / name
    if not path.is_file():
        pytest.skip(f"local milestone save not present: {path}")
    side = json.loads(path.read_text())
    assert side["rom_sha1"] == _ROM_SHA1
    state = (path.parent / side["state_file"]).read_bytes()
    obs = adapter.load_state(state, frame=side["frame"], adapter_state=side.get("adapter_state"))
    return side, obs.ram


def _sys_and_badge(adapter):
    sb1 = adapter.ram.u32(G_SAVEBLOCK1_PTR)
    flags = adapter.ram.block(sb1 + _FLAGS_OFFSET, _SYS_BYTE + 1)
    return flags[_BADGE_BYTE], flags[_SYS_BYTE]


@pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")
def test_real_saves_pokedex_bits_and_badge_byte_stays_clear(monkeypatch):
    from game_brain.adapters.gba_mgba.adapter import MgbaFireRedAdapter

    monkeypatch.delenv("GAME_BRAIN_START_STATE", raising=False)
    adapter = MgbaFireRedAdapter()
    adapter.reset()

    # Before the starter: owned and seen clear, and the "got a Pokémon" flag is clear.
    side, ram = _load(adapter, "0000700_milestone-oak_lab.json")
    assert side["step"] == 700 and side["milestones_done"][-1] == "oak_lab"
    assert "deliver_parcel" not in side["milestones_done"]
    assert ram["party_count"] == 0 and ram["party"] == []
    assert ram["pokedex_owned"] == [] and ram["pokedex_seen"] == []
    assert (ram["map_bank"], ram["map_id"], ram["player_x"], ram["player_y"]) == (4, 3, 6, 12)
    assert _sys_and_badge(adapter) == (0x00, 0x00)
    assert "badges" not in ram

    # Starter caught, Pokédex not received yet. Owned and seen are exactly that species.
    side, ram = _load(adapter, "0000964_milestone-get_starter.json")
    assert side["step"] == 964 and "deliver_parcel" not in side["milestones_done"]
    assert ram["party"][0]["species_id"] == 1 and ram["party"][0]["species"] == "BULBASAUR"
    assert ram["pokedex_owned"] == [1] and ram["pokedex_seen"] == [1]
    assert _sys_and_badge(adapter) == (0x00, 0x01)   # FLAG_SYS_POKEMON_GET set, POKEDEX_GET clear

    # After the rival battle (Charmander was on screen) and before the Pokédex script sets its flag.
    side, ram = _load(adapter, "0002170_milestone-rival_battle_over.json")
    assert side["step"] == 2170 and "rival_battle_over" in side["milestones_done"]
    assert "deliver_parcel" not in side["milestones_done"]
    assert ram["party"][0]["species_id"] == 1
    assert ram["pokedex_owned"] == [1] and ram["pokedex_seen"] == [1, 4]

    # Milestone fires when the table Pokédexes vanish, one step-range before FLAG_SYS_POKEDEX_GET.
    side, ram = _load(adapter, "0003945_milestone-deliver_parcel.json")
    assert side["step"] == 3945 and "deliver_parcel" in side["milestones_done"]
    assert (ram["map_bank"], ram["map_id"], ram["player_x"], ram["player_y"]) == (4, 3, 6, 4)
    assert ram["party"][0]["species_id"] == 1
    assert ram["pokedex_owned"] == [1]
    assert ram["pokedex_seen"] == [1, 4, 16, 19]
    assert _sys_and_badge(adapter) == (0x00, 0x01)

    side, ram = _load(adapter, "0004000_periodic.json")
    assert side["step"] == 4000 and "deliver_parcel" in side["milestones_done"]
    assert (ram["player_x"], ram["player_y"]) == (6, 4)
    assert ram["pokedex_owned"] == [1] and ram["pokedex_seen"] == [1, 4, 16, 19]
    assert _sys_and_badge(adapter) == (0x00, 0x03)   # bit1 FLAG_SYS_POKEDEX_GET now set
    assert "badges" not in ram
