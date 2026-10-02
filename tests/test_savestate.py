"""Save / resume (game_brain/savestate.py, notes/savestate-format.md) on MockHouseAdapter (no ROM)."""
import json
from pathlib import Path

import pytest

from game_brain import demo, savestate
from game_brain.adapters import make_adapter
from game_brain.runlog import iter_steps, read_log, replay

REPO = Path(__file__).resolve().parent.parent


def test_save_dir_inside_the_repo_is_refused(tmp_path):
    with pytest.raises(ValueError, match="inside the repo"):
        savestate.check_save_dir(REPO / "saves")
    with pytest.raises(ValueError, match="inside the repo"):
        savestate.check_save_dir(tmp_path / "x", roots=[tmp_path])
    assert savestate.check_save_dir(tmp_path / "saves", roots=[REPO]) == (tmp_path / "saves").resolve()
    assert demo.main(["--adapter", "mock-house", "--steps", "3", "--save-dir", str(REPO / "saves"), "-q",
                      "--out", str(tmp_path / "runs")]) == 2
    assert not (REPO / "saves").exists()


def test_default_save_dir_is_outside_the_repo(monkeypatch):
    monkeypatch.delenv("GAME_BRAIN_SAVE_DIR", raising=False)
    d = savestate.default_save_dir()
    assert d == Path.home() / ".game-brain" / "saves"
    assert REPO not in d.resolve().parents


def _run(tmp_path, steps, **kw):
    return demo.run("mock-house", steps=steps, mode="auto", brains="path,rule", out_dir=str(tmp_path / "runs"),
                    quiet=True, **kw)


def test_saves_at_milestones_periodic_and_end_with_sidecar(tmp_path):
    saves = tmp_path / "saves"
    s = _run(tmp_path, 120, save_dir=str(saves), save_every=50)
    names = [Path(p).stem for p in s["saves"]]
    assert "0000050_periodic" in names and "0000100_periodic" in names and names[-1] == "0000120_final"
    assert "0000007_milestone-intro" in names and any(n.endswith("milestone-get_starter") for n in names)
    assert not list(saves.rglob("*.tmp"))                       # atomic writes
    side = json.loads(Path(s["saves"][-1]).read_text())
    for k in ("step", "frame", "map_bank", "map_id", "x", "y", "milestone", "milestones_done", "party_hp",
              "rom_sha1", "brains", "git_commit", "timestamp", "state_sha1", "adapter", "format_version"):
        assert k in side
    assert side["step"] == 120 and side["brains"] == ["path", "rule"] and side["adapter"] == "mock-house"
    assert side["frame"] == s["final_frame"] and (side["x"], side["y"]) == (s["final_ram"]["player_x"],
                                                                             s["final_ram"]["player_y"])
    assert (saves / "latest").read_text().strip() == Path(s["saves"][-1]).relative_to(saves).as_posix()
    events = [r for r in read_log(s["log"]) if r["kind"] == "save"]
    assert [e["path"] for e in events] == s["saves"]


def test_resume_continues_from_the_save_moment_and_replays(tmp_path):
    saves = tmp_path / "saves"
    full = _run(tmp_path, 300)                                   # reference run, no saves
    a = _run(tmp_path, 160, save_dir=str(saves), save_every=150)  # "killed" 10 steps after its save at 150
    side = savestate.load_sidecar(next(p for p in a["saves"] if p.endswith("0000150_periodic.json")))
    ref = {r["step"]: r for r in iter_steps(full["log"])}[150]
    b = _run(tmp_path, 140, save_dir=str(saves), resume=side["_path"])
    steps = list(iter_steps(b["log"]))
    assert steps[0]["step"] == 150 and steps[0]["frame"] == ref["frame"] == side["frame"]
    assert steps[0]["observation"]["ram"] == ref["observation"]["ram"]   # same moment as the save
    head = next(read_log(b["log"]))
    assert head["resumed_from"]["sidecar"] == side["_path"] and head["resumed_from"]["step"] == 150
    assert any(r["kind"] == "resumed" for r in read_log(b["log"]))
    assert b["resumed_from"] == side["_path"]
    # milestone progress restored: already done on the first resumed step, and play goes on
    done0 = {m["id"] for m in steps[0]["decision"]["milestones"] if m["done"]}
    assert set(side["milestones_done"]) <= done0
    assert (b["final_ram"]["map_bank"], b["final_ram"]["map_id"]) == (4, 3)        # on to the Pokedex
    assert {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}["deliver_parcel"]
    assert json.loads(Path(b["saves"][-1]).read_text())["resumed_from"] == side["_path"]
    assert replay(b["log"], make_adapter("mock-house")) == []
    # "latest" = the newest save (the resumed run's final one)
    assert savestate.resolve_resume("latest", saves)["_path"] == str(Path(b["saves"][-1]).resolve())


def test_resume_rejects_a_modified_state_and_a_wrong_adapter(tmp_path):
    saves = tmp_path / "saves"
    a = _run(tmp_path, 20, save_dir=str(saves), save_every=0)
    side = savestate.load_sidecar(a["saves"][-1])
    Path(side["_state_path"]).write_bytes(b"{}")
    with pytest.raises(ValueError, match="sha1"):
        _run(tmp_path, 5, resume=side["_path"])
    with pytest.raises(ValueError, match="adapter"):
        demo.run("mock", steps=1, out_dir=str(tmp_path / "runs"), quiet=True, resume=side["_path"])


def test_adapter_without_save_states_skips_saving(tmp_path, capsys):
    s = demo.run("mock", steps=5, out_dir=str(tmp_path / "runs"), quiet=True, save_dir=str(tmp_path / "saves"))
    assert s["saves"] == [] and "no save states" in capsys.readouterr().err
    assert not list((tmp_path / "saves").rglob("*.state"))


def test_resume_mid_parcel_errand_restores_milestones_and_delivers(tmp_path):
    """Oak's Parcel: a milestone save in the mart. In the mart the map alone says nothing about
    Viridian / Route 1 progress, so the restored milestone list is what keeps the errand going."""
    saves = tmp_path / "saves"
    a = _run(tmp_path, 230, save_dir=str(saves), save_every=0)
    names = [Path(p).stem for p in a["saves"]]
    for mid in ("viridian_city", "viridian_mart", "oaks_parcel"):
        assert any(n.endswith("milestone-" + mid) for n in names)
    side = savestate.load_sidecar(next(p for p in a["saves"] if p.endswith("milestone-viridian_mart.json")))
    assert (side["map_bank"], side["map_id"]) == (5, 3) and side["milestone"] == "oaks_parcel"
    assert "viridian_mart" in side["milestones_done"] and "oaks_parcel" not in side["milestones_done"]
    b = _run(tmp_path, 120, save_dir=str(saves), save_every=0, resume=side["_path"])
    steps = list(iter_steps(b["log"]))
    first = {m["id"]: m["done"] for m in steps[0]["decision"]["milestones"]}
    assert first["viridian_city"] and first["viridian_mart"] and not first["oaks_parcel"]
    last = {m["id"]: m["done"] for m in steps[-1]["decision"]["milestones"]}
    assert last["oaks_parcel"] and last["back_to_pallet"] and last["deliver_parcel"] and not last["pewter_city"]
    assert {n["local_id"] for n in b["final_ram"]["npcs"]} == {4, 8}          # Pokedexes handed out
    assert any(Path(p).stem.endswith("milestone-deliver_parcel") for p in b["saves"])
    assert replay(b["log"], make_adapter("mock-house")) == []
