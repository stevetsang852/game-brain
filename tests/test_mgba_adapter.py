"""mGBA bridge tests.

* Unit tests use a fake core (no ROM, no mGBA) and always run.
* Integration tests run the real ROM headless; they are skipped unless the mGBA Python
  bindings import and $GAME_BRAIN_ROM points at the FireRed ROM (never committed).
"""

import os

import pytest

from game_brain.adapters import make_adapter
from game_brain.adapters.gba_mgba import MgbaFireRedAdapter
from game_brain.adapters.gba_mgba import firered as fr
from game_brain.adapters.gba_mgba import firered_battle as fb
from game_brain.adapters.gba_mgba.adapter import keymask
from game_brain.schema import Action, ButtonPress


# ----------------------------------------------------------------- fake core

class _View:
    def __init__(self, mem, width):
        self.mem, self.width = mem, width

    def __getitem__(self, addr):
        return int.from_bytes(bytes(self.mem.get(addr + i, 0) for i in range(self.width)), "little")


class _Mem:
    def __init__(self):
        self.b = {}
        self.u8, self.u16, self.u32 = _View(self.b, 1), _View(self.b, 2), _View(self.b, 4)

    def put(self, addr, value, width):
        for i, byte in enumerate(int(value).to_bytes(width, "little", signed=value < 0)):
            self.b[addr + i] = byte


class FakeCore:
    def __init__(self):
        self.memory = _Mem()
        self.keys_per_frame = []
        self.keys = 0
        self.resets = 0

    def reset(self):
        self.resets += 1

    def set_keys(self, raw=0):
        self.keys = raw

    def run_frame(self):
        self.keys_per_frame.append(self.keys)


def overworld(core, x=6, y=6, group=4, num=1, face=2):
    m = core.memory
    sb1 = 0x02025000
    m.put(fr.MAIN_CALLBACK2, fr.CB2_OVERWORLD, 4)
    m.put(fr.G_SAVEBLOCK1_PTR, sb1, 4)
    m.put(sb1, x, 2); m.put(sb1 + 2, y, 2); m.put(sb1 + 4, group, 1); m.put(sb1 + 5, num, 1)
    m.put(fr.G_PLAYER_AVATAR + 5, 0, 1)
    m.put(fr.G_OBJECT_EVENTS + 0x18, face, 1)


def test_keymask_matches_gba_keyinput_bits():
    assert keymask("A") == 1 and keymask("B") == 2 and keymask("START") == 8
    assert keymask("RIGHT") == 16 and keymask("DOWN") == 128 and keymask("L") == 512
    assert keymask("NONE") == 0


def test_act_runs_exact_frames_hold_then_release():
    core = FakeCore()
    a = MgbaFireRedAdapter(core=core)
    a.reset()
    act = Action([ButtonPress("A", 3, 2), ButtonPress("NONE", 1), ButtonPress("UP", 2)])
    assert a.act(act) == act.total_frames == 8
    assert core.keys_per_frame == [1, 1, 1, 0, 0, 0, 64, 64]
    assert a.frame == 8 and core.keys == 0


def test_observe_does_not_advance_and_hides_position_outside_overworld():
    core = FakeCore()
    a = MgbaFireRedAdapter(core=core)
    obs = a.reset()
    assert obs.frame == 0 and obs.position is None and obs.ram["scene"] == "other"
    a.observe(); a.observe()
    assert core.keys_per_frame == [] and a.frame == 0


def test_overworld_fields():
    core = FakeCore()
    overworld(core, x=5, y=7, face=3)
    obs = MgbaFireRedAdapter(core=core).reset()
    assert obs.position == (5, 7)
    assert (obs.ram["map_bank"], obs.ram["map_id"], obs.ram["facing"]) == (4, 1, "LEFT")


def test_in_battle_is_bit1_of_gmain_439_only():
    core = FakeCore()
    a = MgbaFireRedAdapter(core=core)
    assert a.reset().ram["in_battle"] is False
    core.memory.put(fr.MAIN_FLAGS_439, 0x01, 1)        # bit0 (oamLoadDisabled) is not a battle
    assert a.observe().ram["in_battle"] is False
    core.memory.put(fr.MAIN_FLAGS_439, 0x03, 1)
    assert a.observe().ram["in_battle"] is True
    overworld(core)                                    # also reported in the overworld
    core.memory.put(fr.MAIN_FLAGS_439, 0x00, 1)
    assert a.observe().ram["in_battle"] is False


def _put_mon(core, battler, species, level, hp, max_hp, moves):
    b = fb.G_BATTLE_MONS + fb.BATTLE_MON_SIZE * battler
    core.memory.put(b + fb.BM_SPECIES, species, 2)
    core.memory.put(b + fb.BM_LEVEL, level, 1)
    core.memory.put(b + fb.BM_HP, hp, 2)
    core.memory.put(b + fb.BM_MAX_HP, max_hp, 2)
    for i, (mid, pp) in enumerate(moves):
        core.memory.put(b + fb.BM_MOVES + 2 * i, mid, 2)
        core.memory.put(b + fb.BM_PP + i, pp, 1)


def test_battle_dict_only_while_in_battle():
    core = FakeCore()
    a = MgbaFireRedAdapter(core=core)
    assert "battle" not in a.reset().ram
    core.memory.put(fr.MAIN_FLAGS_439, 0x02, 1)              # battle started, mons not filled yet
    b = a.observe().ram["battle"]
    assert b["player"] is None and b["opponent"] is None
    _put_mon(core, 0, 1, 5, 22, 22, [(118, 40)])            # values seen in the rival battle
    _put_mon(core, 1, 4, 5, 14, 20, [(118, 40)])
    core.memory.put(fb.G_BATTLER_CONTROLLER_FUNCS, fb.CTRL_CHOOSE_ACTION, 4)
    a.observe()                                              # first menu of this battle
    core.memory.put(fb.G_BATTLER_CONTROLLER_FUNCS, 0, 4)
    b = a.observe().ram["battle"]
    assert b["player"] == {"species": 1, "level": 5, "hp": 22, "max_hp": 22, "moves": [{"id": 118, "pp": 40}]}
    assert b["opponent"]["hp_pct"] == 70 and b["opponent"]["species"] == 4
    assert b["menu"] == "other" and b["cursor"] is None and b["outcome"] is None
    core.memory.put(fr.MAIN_FLAGS_439, 0x00, 1)              # outcome byte is not cleared, battle key goes away
    core.memory.put(fb.G_BATTLE_OUTCOME, 1, 1)
    assert "battle" not in a.observe().ram


def test_battle_menu_cursor_and_outcome():
    core = FakeCore()
    a = MgbaFireRedAdapter(core=core)
    a.reset()
    core.memory.put(fr.MAIN_FLAGS_439, 0x02, 1)
    core.memory.put(fb.G_ACTION_SELECTION_CURSOR, 3, 1)
    core.memory.put(fb.G_MOVE_SELECTION_CURSOR, 2, 1)
    core.memory.put(fb.G_BATTLER_CONTROLLER_FUNCS, fb.CTRL_CHOOSE_ACTION, 4)
    b = a.observe().ram["battle"]
    assert (b["menu"], b["cursor"]) == ("action", 3)
    core.memory.put(fb.G_BATTLER_CONTROLLER_FUNCS, fb.CTRL_CHOOSE_MOVE, 4)
    b = a.observe().ram["battle"]
    assert (b["menu"], b["cursor"]) == ("move", 2)
    _put_mon(core, 0, 1, 5, 22, 22, [(118, 40), (33, 35), (0, 0), (10, 35)])
    _put_mon(core, 1, 4, 5, 20, 20, [(118, 40)])
    assert [m["id"] for m in a.observe().ram["battle"]["player"]["moves"]] == [118, 33, 10]
    for raw, want in ((0, None), (1, "win"), (2, "lose"), (4, "unknown")):
        core.memory.put(fb.G_BATTLE_OUTCOME, raw, 1)
        assert a.observe().ram["battle"]["outcome"] == want


def test_battle_data_hidden_until_first_menu_of_each_battle():
    # gBattleMons / gBattleOutcome still hold the previous battle for a few observations
    core = FakeCore()
    a = MgbaFireRedAdapter(core=core)
    a.reset()
    _put_mon(core, 0, 25, 9, 7, 30, [(33, 35)])              # leftovers from a "previous battle"
    _put_mon(core, 1, 19, 3, 0, 12, [(33, 35)])
    core.memory.put(fb.G_BATTLE_OUTCOME, 1, 1)
    core.memory.put(fr.MAIN_FLAGS_439, 0x02, 1)
    b = a.observe().ram["battle"]
    assert b["player"] is None and b["opponent"] is None and b["outcome"] is None
    _put_mon(core, 0, 1, 5, 22, 22, [(118, 40)])
    _put_mon(core, 1, 4, 5, 20, 20, [(118, 40)])
    core.memory.put(fb.G_BATTLE_OUTCOME, 0, 1)
    core.memory.put(fb.G_BATTLER_CONTROLLER_FUNCS, fb.CTRL_CHOOSE_ACTION, 4)
    assert a.observe().ram["battle"]["player"]["species"] == 1
    core.memory.put(fb.G_BATTLER_CONTROLLER_FUNCS, 0, 4)      # text after the menu: still trusted
    core.memory.put(fb.G_BATTLE_OUTCOME, 2, 1)
    assert a.observe().ram["battle"]["outcome"] == "lose"
    core.memory.put(fr.MAIN_FLAGS_439, 0x00, 1)               # battle over -> next battle starts untrusted
    assert "battle" not in a.observe().ram
    core.memory.put(fr.MAIN_FLAGS_439, 0x02, 1)
    b = a.observe().ram["battle"]
    assert b["player"] is None and b["outcome"] is None


def test_reset_restarts_frame_counter():
    core = FakeCore()
    a = MgbaFireRedAdapter(core=core)
    a.reset(); a.act(Action.wait(10))
    assert a.reset().frame == 0 and core.resets == 2


def test_missing_rom_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GAME_BRAIN_ROM", raising=False)
    with pytest.raises(FileNotFoundError):
        make_adapter("mgba")


# ----------------------------------------------------------------- real ROM

def _real_available():
    try:
        import mgba.core  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(os.environ.get("GAME_BRAIN_ROM", ""))


real = pytest.mark.skipif(not _real_available(), reason="needs mGBA bindings + $GAME_BRAIN_ROM")


def _mash_to_overworld(a, limit=600):
    for _ in range(limit):
        obs = a.observe()
        if obs.position is not None:
            return obs
        a.act(Action.tap("A", 8, 8))
    raise AssertionError("never reached the overworld")


@real
def test_real_not_in_battle_from_boot_to_overworld():
    a = make_adapter("mgba")
    a.reset()
    for _ in range(600):
        obs = a.observe()
        assert obs.ram["in_battle"] is False and "battle" not in obs.ram
        if obs.position is not None:
            return
        a.act(Action.tap("A", 8, 8))
    raise AssertionError("never reached the overworld")


@real
def test_real_frames_and_vblank_counter():
    a = make_adapter("mgba")
    a.reset()
    a.observe()
    assert a.frame == 0
    assert a.act(Action.wait(120)) == 120 and a.frame == 120
    # the game only starts counting ~13 frames after power-on; after that it is 1/frame
    v1 = a.observe().ram["vblank_counter"]
    a.act(Action.wait(300))
    assert a.observe().ram["vblank_counter"] - v1 == 300
    a.close()


@real
def test_real_held_keys_mirror_input():
    a = make_adapter("mgba")
    a.reset()
    a.act(Action.wait(300))
    a.act(Action([ButtonPress("B", 3)]))
    # gMain.heldKeys is updated in the frame's VBlank-driven ReadKeys, so B shows while held
    assert a.observe().ram["held_keys"] & keymask("B")
    a.close()


@real
def test_real_intro_to_players_room_and_walk():
    a = make_adapter("mgba")
    a.reset()
    obs = _mash_to_overworld(a)
    assert (obs.ram["map_bank"], obs.ram["map_id"]) == (4, 1)  # Pallet Town, player's house 2F
    assert obs.position == (6, 6)
    a.act(Action.wait(120))
    a.act(Action([ButtonPress("DOWN", 16, 30)]))
    o = a.observe()
    assert o.position == (6, 7) and o.ram["facing"] == "DOWN"
    a.act(Action([ButtonPress("LEFT", 16, 30)]))
    o = a.observe()
    assert o.position == (5, 7) and o.ram["facing"] == "LEFT"
    a.close()


@real
def test_real_is_deterministic():
    def run():
        a = make_adapter("mgba")
        a.reset()
        trace = []
        for i in range(250):
            a.act(Action.tap("A" if i % 3 else "DOWN", 8, 8))
            trace.append((a.frame, tuple(sorted(a.observe().ram.items()))))
        a.close()
        return trace
    assert run() == run()


@real
def test_real_demo_log_replays(tmp_path):
    from game_brain.demo import run
    from game_brain.runlog import replay
    s = run("mgba", steps=150, mode="auto", out_dir=str(tmp_path), quiet=True)
    assert replay(s["log"], make_adapter("mgba")) == []


# ----------------------------------------------------------------- collision + warps

import collections

_DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def test_fake_map_layout_collision_and_warps():
    core = FakeCore()
    overworld(core, x=1, y=1)
    m = core.memory
    layout, vmap, events, warps, ts, attrs = 0x08100000, 0x02031000, 0x08200000, 0x08300000, 0x08400000, 0x08500000
    m.put(fr.G_MAP_HEADER, layout, 4); m.put(fr.G_MAP_HEADER + 4, events, 4)
    m.put(layout, 3, 4); m.put(layout + 4, 2, 4); m.put(layout + 0x10, ts, 4)
    m.put(ts + 0x14, attrs, 4)
    vw = 3 + 15
    m.put(fr.G_BACKUP_MAP_LAYOUT, vw, 4); m.put(fr.G_BACKUP_MAP_LAYOUT + 8, vmap, 4)
    def tile(x, y, metatile, coll):
        m.put(vmap + 2 * ((y + 7) * vw + x + 7), metatile | (coll << 10), 2)
    tile(0, 0, 1, 1); tile(1, 0, 1, 1); tile(2, 0, 1, 1)
    tile(0, 1, 2, 0); tile(1, 1, 2, 0); tile(2, 1, 3, 0)
    m.put(attrs + 4 * 3, 0x65, 4)  # metatile 3 = south arrow warp
    m.put(events + 1, 1, 1); m.put(events + 8, warps, 4)
    m.put(warps, 2, 2); m.put(warps + 2, 1, 2); m.put(warps + 6, 0, 1); m.put(warps + 7, 3, 1)
    ram = MgbaFireRedAdapter(core=core).reset().ram
    assert (ram["map_w"], ram["map_h"]) == (3, 2)
    assert ram["collision"] == ["###", "..."]
    assert ram["warps"] == [{"x": 2, "y": 1, "dest_bank": 3, "dest_map": 0, "behavior": 0x65, "enter": "DOWN"}]


def _bfs(ram, goal):
    """Shortest path over ram['collision'] (goal tile allowed even if blocked)."""
    start = (ram["player_x"], ram["player_y"])
    grid, w, h = ram["collision"], ram["map_w"], ram["map_h"]
    prev = {start: None}
    q = collections.deque([start])
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        for d, (dx, dy) in _DIRS.items():
            n = (cur[0] + dx, cur[1] + dy)
            if 0 <= n[0] < w and 0 <= n[1] < h and n not in prev and (grid[n[1]][n[0]] == "." or n == goal):
                prev[n] = (cur, d)
                q.append(n)
    path, n = [], goal
    while prev.get(n):
        n, d = prev[n]
        path.append(d)
    return list(reversed(path)) if goal in prev else None


def _walk(a, path):
    for d in path:
        a.act(Action([ButtonPress(d, 16, 16)]))


def _take_warp(a, warp):
    path = _bfs(a.observe().ram, (warp["x"], warp["y"]))
    assert path is not None, f"no path to {warp}"
    if warp["enter"] == "UP" and path and path[-1] == "UP":
        path = path[:-1]  # doors: stand below, then walk UP into the door
    _walk(a, path)
    a.act(Action([ButtonPress(warp["enter"], 90, 0)]))
    a.act(Action.wait(300))
    return a.observe()


@real
def test_real_collision_matches_movement():
    a = make_adapter("mgba")
    a.reset()
    _mash_to_overworld(a)
    a.act(Action.wait(120))
    checked = 0
    for d in ["UP", "LEFT", "DOWN", "RIGHT", "RIGHT", "UP", "UP", "LEFT"]:
        o = a.observe().ram
        dx, dy = _DIRS[d]
        tx, ty = o["player_x"] + dx, o["player_y"] + dy
        free = 0 <= tx < o["map_w"] and 0 <= ty < o["map_h"] and o["collision"][ty][tx] == "."
        a.act(Action([ButtonPress(d, 16, 30)]))
        n = a.observe().ram
        moved = (n["player_x"], n["player_y"]) != (o["player_x"], o["player_y"])
        assert moved == free, (d, (o["player_x"], o["player_y"]), free, moved)
        checked += 1
    assert checked == 8
    a.close()


@real
def test_real_pathfind_out_of_the_house():
    """2F -> stairs -> 1F -> door mat -> Pallet Town, using only collision + warps from RAM."""
    a = make_adapter("mgba")
    a.reset()
    _mash_to_overworld(a)
    a.act(Action.wait(120))
    stairs = [w for w in a.observe().ram["warps"] if w["enter"]]
    assert [(w["x"], w["y"], w["dest_bank"], w["dest_map"], w["enter"]) for w in stairs] == [(10, 2, 4, 0, "LEFT")]
    o = _take_warp(a, stairs[0])
    assert (o.ram["map_bank"], o.ram["map_id"]) == (4, 0)
    exits = [w for w in o.ram["warps"] if w["enter"] and (w["dest_bank"], w["dest_map"]) == (3, 0)]
    assert exits, o.ram["warps"]
    o = _take_warp(a, exits[0])
    assert (o.ram["map_bank"], o.ram["map_id"]) == (3, 0)  # Pallet Town
    assert o.position == (6, 8)
    home = [w for w in o.ram["warps"] if (w["dest_bank"], w["dest_map"]) == (4, 0)]
    assert home and home[0]["enter"] == "UP" and (home[0]["x"], home[0]["y"]) == (6, 7)
    o = _take_warp(a, home[0])
    assert (o.ram["map_bank"], o.ram["map_id"]) == (4, 0)  # back inside through the door
    a.close()
