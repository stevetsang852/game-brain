"""status.party: a passthrough of ram["party"] (firered_party.py shape), [] without data. No ROM."""
import hashlib
import json

import pytest

from game_brain import savestate
from game_brain.adapters.mock.house import MockHouseAdapter
from game_brain.dashboard import live
from game_brain.dashboard.server import LoadSaveCommand, PersistenceCommand

BULBA = {"slot": 0, "species_id": 1, "species": "BULBASAUR", "egg": False, "level": 5, "hp": 20,
         "max_hp": 20, "status": None, "active": False,
         "moves": [{"id": 33, "name": "TACKLE", "pp": 35, "max_pp": 35}, {"id": 45, "name": "GROWL", "pp": 40,
                                                                           "max_pp": 40}]}


class _Capture:
    url = "test://capture"
    client_count = 0

    def __init__(self):
        self.status = []

    def broadcast(self, env):
        if env["type"] == "status":
            self.status.append(json.loads(json.dumps(env["payload"])))

    def poll(self):
        return []


def test_party_status_copies_and_defaults():
    assert live.party_status({}) == []
    assert live.party_status({"party": []}, [BULBA]) == []                  # real empty party
    assert live.party_status({"party_count": 1}, [BULBA]) == [BULBA]       # read skipped: keep last
    p = live.party_status({"party": [BULBA]})
    assert p == [BULBA] and p is not BULBA and p[0]["moves"] is not BULBA["moves"]
    sleepy = dict(BULBA, status="sleep", sleep_turns=3)
    assert live.party_status({"party": [sleepy, {"slot": 1, "bad_egg": True}]}) == \
        [sleepy, {"slot": 1, "bad_egg": True}]                              # passed through as is


def test_status_party_is_empty_without_adapter_party_data(tmp_path):
    srv = _Capture()
    live.run(srv, "mock-house", "auto", steps=30, step_delay=0, screenshot_every=0, out_dir=str(tmp_path), quiet=True)
    assert srv.status and all(st["party"] == [] for st in srv.status)


def test_status_party_follows_ram_party_live(tmp_path, monkeypatch):
    """A mock adapter that reports ram["party"] like the mGBA one: [] before the starter, then the
    starter, HP changing in a "battle", and some frames without a read (kept)."""
    orig = MockHouseAdapter.observe
    n = {"i": 0}

    def observe(self):
        obs = orig(self)
        n["i"] += 1
        i = n["i"]
        if i % 7 == 0:
            return obs                                   # transition frame: no party read
        if self.party:
            mon = dict(BULBA, hp=max(1, 20 - i % 5), active=bool(i % 2))
            obs.ram["party"] = [mon]
        else:
            obs.ram["party"] = []
        return obs
    monkeypatch.setattr(MockHouseAdapter, "observe", observe)
    srv = _Capture()
    s = live.run(srv, "mock-house", "auto", brains="battle,path,rule", steps=400, step_delay=0,
                 screenshot_every=0, out_dir=str(tmp_path), quiet=True)
    parties = [st["party"] for st in srv.status]
    assert parties[0] == []
    first = next(i for i, p in enumerate(parties) if p)
    assert all(p == [] for p in parties[:first]) and all(len(p) == 1 for p in parties[first:])
    assert len({p[0]["hp"] for p in parties[first:]}) > 1                   # updates every step
    assert all(set(BULBA) <= set(p[0]) for p in parties[first:])
    assert srv.status[-1].get("finished") and srv.status[-1]["party"] == parties[-2]
    assert s["steps"] == 400


def _mock_house_save():
    a = MockHouseAdapter()
    a.reset()
    state = a.save_state()
    side = {"format": savestate.FORMAT, "format_version": savestate.FORMAT_VERSION, "adapter": a.name,
            "state_sha1": hashlib.sha1(state).hexdigest(), "step": 3, "frame": 30, "adapter_state": {},
            "milestones_done": [], "state_file": "x.state"}
    a.close()
    return LoadSaveCommand(side, state, None, "x.json")


@pytest.mark.parametrize("command", ["new_game", "load_save"])
def test_party_reset_on_new_game_and_load_save(tmp_path, monkeypatch, command):
    """After 'new game' / 'load save' the held party is dropped: frames without a party read show []
    (not the previous game's party) until the adapter reads the new game's party."""
    orig = MockHouseAdapter.observe
    state = {"switched_at": None, "i": 0}

    def observe(self):
        obs = orig(self)
        state["i"] += 1
        if state["switched_at"] is None:
            obs.ram["party"] = [BULBA]                        # previous game: has a starter
        elif state["i"] - state["switched_at"] > 5:
            obs.ram["party"] = []                             # new game's own (empty) party read
        return obs                                            # first frames after: no read
    monkeypatch.setattr(MockHouseAdapter, "observe", observe)

    class Srv(_Capture):
        def poll(self):
            if len(self.status) == 10 and state["switched_at"] is None:
                state["switched_at"] = state["i"]
                return [PersistenceCommand("new_game") if command == "new_game" else _mock_house_save()]
            return []
    srv = Srv()
    live.run(srv, "mock-house", "auto", brains="path,rule", steps=25, step_delay=0, screenshot_every=0,
             out_dir=str(tmp_path), quiet=True, save_dir=None, no_memory=True)
    parties = [st["party"] for st in srv.status]
    assert all(p == [BULBA] for p in parties[:10])
    notes = srv.status[10]["notes"]
    assert any(("新遊戲" in n) or ("已載入本機存檔" in n) for n in notes), notes
    assert all(p == [] for p in parties[10:])                 # never the old BULBA again
