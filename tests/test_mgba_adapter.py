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
