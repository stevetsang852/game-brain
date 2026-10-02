"""ram["party"] (firered_party.py): synthetic encrypted mons, no ROM needed.

The mons are built here the way the game stores them (substructs G/A/E/M in the order given by
personality % 24, each u32 XORed with personality ^ OTID, checksum over the decrypted u16s), so
decryption, substruct ordering, checksum, status decoding and the active flag are checked
without the ROM. The real-ROM party test is in test_party_real.py.
"""

import struct

import pytest

from game_brain.adapters.gba_mgba import MgbaFireRedAdapter
from game_brain.adapters.gba_mgba import firered_party as fp
from game_brain.adapters.gba_mgba import firered as fr
from game_brain.adapters.gba_mgba import firered_battle as fb
from game_brain.runlog import RunLogWriter, iter_steps

from test_mgba_adapter import FakeCore, overworld

TABLES = fp.RomTables(
    species_names=["", "BULBASAUR", "IVYSAUR", "VENUSAUR", "CHARMANDER"],
    move_names=[""] + [f"MOVE{i}" for i in range(1, 120)],
    move_pp=[0] + [35] * 32 + [35] + [20] * 84 + [40] + [10])   # id 118 (METRONOME here) -> 40


def make_mon(personality, otid, species, moves, pps, level=5, hp=22, max_hp=22, status=0,
             pp_bonuses=0, egg=False, corrupt=False):
    g = struct.pack("<HHIBBH", species, 0, 135, pp_bonuses, 70, 0)
    a = struct.pack("<4H4B", *moves, *pps)
    e = bytes(12)
    m = struct.pack("<BBHII", 0, 0, 0, (1 << 30) if egg else 0, 0)
    subs = {"G": g, "A": a, "E": e, "M": m}
    plain = b"".join(subs[c] for c in fp.SUBSTRUCT_ORDERS[personality % 24])
    checksum = sum(struct.unpack("<24H", plain)) & 0xFFFF
    key = personality ^ otid
    enc = struct.pack("<12I", *(w ^ key for w in struct.unpack("<12I", plain)))
    if corrupt:
        enc = bytes([enc[0] ^ 1]) + enc[1:]
    raw = bytearray(100)
    struct.pack_into("<II", raw, 0, personality, otid)
    raw[8:18] = b"\xbc\xcf\xc6\xbc\xbb\xcd\xbb\xcf\xcc\xff"
    raw[0x13] = 0x02 | (0x04 if egg else 0)
    struct.pack_into("<H", raw, 0x1C, checksum)
    raw[0x20:0x50] = enc
    struct.pack_into("<IBxHH", raw, 0x50, status, level, hp, max_hp)
    return bytes(raw)


def test_decrypt_every_substruct_order():
    for k in range(24):
        pid = 0x71D17281 - 0x71D17281 % 24 + k     # personality % 24 == k: all 24 orders
        raw = make_mon(pid, 0x1DE3791E, species=1, moves=(118, 33, 0, 0), pps=(40, 35, 0, 0))
        mon = fp.decode_mon(raw, 0, TABLES)
        assert mon["species_id"] == 1 and mon["species"] == "BULBASAUR", k
        assert [(m["id"], m["pp"]) for m in mon["moves"]] == [(118, 40), (33, 35)], k


def test_decode_fields_and_rom_values():
    # the real starter on this ROM: personality/OTID/values read from the rival-battle save
    raw = make_mon(0x71D17281, 0x1DE3791E, species=1, moves=(118, 0, 0, 0), pps=(40, 0, 0, 0),
                   level=5, hp=22, max_hp=22)
    assert fp.read_party(raw + bytes(500), 1, TABLES) == [{
        "slot": 0, "species_id": 1, "species": "BULBASAUR", "egg": False, "level": 5, "hp": 22,
        "max_hp": 22, "status": None, "active": False,
        "moves": [{"id": 118, "name": "MOVE118", "pp": 40, "max_pp": 40}]}]


def test_bad_checksum_is_flagged_not_decoded():
    good = make_mon(1234567, 89, species=4, moves=(10, 0, 0, 0), pps=(35, 0, 0, 0))
    bad = make_mon(7654321, 89, species=4, moves=(10, 0, 0, 0), pps=(35, 0, 0, 0), corrupt=True)
    party = fp.read_party(good + bad + bytes(400), 2, TABLES)
    assert party[0]["species_id"] == 4 and party[1] == {"slot": 1, "bad_egg": True}


def test_count_is_clamped_and_empty():
    assert fp.read_party(bytes(600), 0, TABLES) == []
    party = fp.read_party(bytes(600), 255, TABLES)     # garbage count: at most 6, zero mons = bad eggs?
    assert len(party) == 6
    # an all-zero slot has checksum 0 == sum of zeros, decodes as species 0 (never shown in a real party)
    assert party[0]["species_id"] == 0 and party[0]["species"] is None


@pytest.mark.parametrize("status,expect", [
    (0, {"status": None}), (3, {"status": "sleep", "sleep_turns": 3}), (0x08, {"status": "poison"}),
    (0x10, {"status": "burn"}), (0x20, {"status": "freeze"}), (0x40, {"status": "paralysis"}),
    (0x80, {"status": "toxic"}), (0x180, {"status": "toxic"})])  # 0x100: toxic counter bits (battle only)
def test_status_decode(status, expect):
    assert fp.decode_status(status) == expect
    raw = make_mon(42, 7, species=1, moves=(1, 0, 0, 0), pps=(35, 0, 0, 0), status=status)
    mon = fp.decode_mon(raw, 0, TABLES)
    assert {k: mon[k] for k in expect} == expect and ("sleep_turns" in mon) == ("sleep_turns" in expect)


def test_max_pp_with_pp_ups_and_egg_flag():
    # PP Ups: 2 bits per move slot; max = base + base * ups // 5
    raw = make_mon(99, 1, species=2, moves=(118, 33, 119, 0), pps=(40, 35, 10, 0),
                   pp_bonuses=(3 << 0) | (1 << 2) | (2 << 4), egg=True)
    mon = fp.decode_mon(raw, 0, TABLES)
    assert [m["max_pp"] for m in mon["moves"]] == [64, 42, 14] and mon["egg"] is True
    assert fp.decode_mon(raw, 0, None)["moves"][0] == {"id": 118, "name": None, "pp": 40, "max_pp": None}
    assert fp.decode_mon(raw, 0, None)["species"] is None


def battle_mon(personality, hp=0):
    b = bytearray(fb.BATTLE_MON_SIZE)
    struct.pack_into("<H", b, fb.BM_HP, hp)
    struct.pack_into("<I", b, fp.BM_PERSONALITY, personality)
    return bytes(b)


def test_battle_marks_active_by_personality_values_stay_party():
    m0 = make_mon(111, 5, species=1, moves=(118, 0, 0, 0), pps=(40, 0, 0, 0), hp=25, max_hp=25)
    m1 = make_mon(222, 5, species=4, moves=(118, 0, 0, 0), pps=(37, 0, 0, 0), hp=20, max_hp=20, status=0x40)
    block = m0 + m1 + bytes(400)
    party = fp.read_party(block, 2, TABLES, battle_mon(222, hp=9))
    assert [p["active"] for p in party] == [False, True]
    # values are the party struct's (the game writes hp/pp back during battle; see notes)
    assert (party[1]["hp"], party[1]["status"], party[1]["moves"][0]["pp"]) == (20, "paralysis", 37)
    assert not any(p["active"] for p in fp.read_party(block, 2, TABLES, battle_mon(333)))
    assert not any(p["active"] for p in fp.read_party(block, 2, TABLES, None))


def _fake_with_party(mons, count=None):
    core = FakeCore()
    overworld(core)
    core.memory.put(fp.G_PLAYER_PARTY_COUNT, len(mons) if count is None else count, 1)
    for i, b in enumerate(b"".join(mons)):
        core.memory.b[fp.G_PLAYER_PARTY + i] = b
    return core


def test_adapter_party_in_overworld_and_battle():
    m0 = make_mon(0x71D17281, 0x1DE3791E, species=1, moves=(118, 0, 0, 0), pps=(40, 0, 0, 0), hp=22, max_hp=22)
    core = _fake_with_party([m0])
    a = MgbaFireRedAdapter(core=core, rom_tables=TABLES)
    party = a.reset().ram["party"]
    assert len(party) == 1 and party[0]["hp"] == 22 and party[0]["active"] is False
    # battle starts: no overworld keys any more, party still reported but not active until trusted
    core.memory.put(fr.MAIN_CALLBACK2, 0, 4)
    core.memory.put(fr.MAIN_FLAGS_439, 0x02, 1)
    for i, b in enumerate(battle_mon(0x71D17281, hp=14)):
        core.memory.b[fb.G_BATTLE_MONS + i] = b
    core.memory.put(fb.G_BATTLE_MONS + fb.BM_SPECIES, 1, 2)
    core.memory.put(fb.G_BATTLE_MONS + fb.BATTLE_MON_SIZE + fb.BM_SPECIES, 16, 2)
    assert a.observe().ram["party"][0]["active"] is False            # stale gBattleMons: no overlay
    core.memory.put(fb.G_BATTLER_CONTROLLER_FUNCS, fb.CTRL_CHOOSE_ACTION, 4)
    p = a.observe().ram["party"][0]
    assert p["active"] is True and p["hp"] == 22 and p["moves"][0]["pp"] == 40
    # other scenes (no overworld, not in battle): no party key
    core.memory.put(fr.MAIN_FLAGS_439, 0x00, 1)
    assert "party" not in a.observe().ram


def test_runlog_party_dedupe_round_trip(tmp_path):
    from game_brain.schema import Observation

    class R:   # minimal arbiter result
        class mode:
            value = "auto"

        class decision:
            @staticmethod
            def to_dict():
                return {"brain": "x"}
        proposed = executed = None
        notes = []

    mon = {"slot": 0, "species_id": 1, "hp": 22, "moves": [{"id": 118, "pp": 40}]}
    two = [mon, dict(mon, slot=1)]
    rams = [{"party": two}, {"party": two}, {"party": [dict(mon, hp=10), two[1]]},
            {"party": [mon]}, {"x": 1}, {"party": [mon]}]
    path = tmp_path / "log.jsonl"
    with RunLogWriter(path) as w:
        for i, ram in enumerate(rams):
            w.step(i, Observation(frame=i, game="t", ram=ram), R, 0)
    lines = path.read_text().splitlines()
    assert '"party_same":true' in lines[1] and '"party_delta"' in lines[2] and '"party_same":true' in lines[5]
    assert [r["observation"]["ram"] for r in iter_steps(path)] == rams
