"""--keep-periodic retention and the relative ``latest`` pointer (game_brain/savestate.py), no ROM."""
import json
import shutil
from pathlib import Path

from game_brain import demo, savestate, setup
from game_brain.dashboard import live
from game_brain.runlog import iter_steps, read_log


def _run(tmp_path, steps, out="runs", **kw):
    return demo.run("mock-house", steps=steps, mode="auto", brains="path,rule", out_dir=str(tmp_path / out),
                    quiet=True, **kw)


def _stems(d):
    return sorted(p.stem for p in Path(d).glob("*.json"))


def test_keep_periodic_is_a_shared_flag_default_10():
    for ap in (demo.build_parser(), live.build_parser()):
        assert ap.parse_args([]).keep_periodic == savestate.DEFAULT_KEEP_PERIODIC == 10
        assert ap.parse_args(["--keep-periodic", "3"]).keep_periodic == 3


def test_only_the_newest_periodic_saves_are_kept_milestones_and_final_never(tmp_path):
    saves = tmp_path / "saves"
    s = _run(tmp_path, 250, save_dir=str(saves), save_every=10, keep_periodic=3)
    run_dir = Path(s["saves"][-1]).parent
    stems = _stems(run_dir)
    periodic = [n for n in stems if n.endswith("_periodic")]
    written_periodic = sorted(Path(p).stem for p in s["saves"] if p.endswith("_periodic.json"))
    assert len(written_periodic) > 20 and periodic == written_periodic[-3:]    # the newest 3
    # every milestone save and the final save are still there
    written = [Path(p).stem for p in s["saves"]]
    milestones = [n for n in written if "_milestone-" in n]
    assert milestones and all(m in stems for m in milestones)
    assert written[-1].endswith("_final") and written[-1] in stems
    # state + sidecar (+ .sav) go together: no orphan .state / .sav
    for p in run_dir.iterdir():
        assert p.with_suffix(".json").is_file(), p
    pruned = [r for r in read_log(s["log"]) if r["kind"] == "save_pruned"]
    deleted = {Path(p).stem for p in s["saves"]} - set(stems)
    assert {Path(r["path"]).stem for r in pruned} == deleted and len(deleted) >= 20
    assert all(n.endswith("_periodic") for n in deleted)


def test_keep_periodic_0_keeps_everything(tmp_path):
    s = _run(tmp_path, 100, save_dir=str(tmp_path / "saves"), save_every=10, keep_periodic=0)
    assert sorted(Path(p).stem for p in s["saves"]) == _stems(Path(s["saves"][-1]).parent)


def test_prune_only_touches_reason_periodic(tmp_path):
    """Anything whose sidecar reason is not exactly "periodic" survives, whatever its file name says."""
    saves = tmp_path / "saves"
    s = _run(tmp_path, 40, save_dir=str(saves), save_every=10, keep_periodic=0)
    run_dir = Path(s["saves"][-1]).parent
    # forge look-alikes: a go-explore cell, an unknown reason, a "periodic"-named file with another reason
    src = next(p for p in s["saves"] if p.endswith("_periodic.json"))
    for name, reason in [("0000001_go-explore-cell", "go-explore-cell"), ("0000002_periodic", "manual"),
                         ("0000003_whatever", "Periodic")]:
        side = json.loads(Path(src).read_text())
        side.update(reason=reason, step=int(name[:7]), state_file=name + ".state", sav_file=None)
        (run_dir / (name + ".json")).write_text(json.dumps(side))
        shutil.copy(Path(src).with_suffix(".state"), run_dir / (name + ".state"))
    (run_dir / "notes.json").write_text("not a sidecar")
    before = set(_stems(run_dir))
    m = savestate.SaveManager(saves, object(), [], run_dir.name, keep_periodic=1)
    deleted = {Path(p).stem for p in m.prune_periodic()}
    after = set(_stems(run_dir))
    real_periodic = sorted(n for n in before if n.endswith("_periodic") and n != "0000002_periodic")
    assert deleted == set(real_periodic[:-1])
    assert after == before - deleted
    for keep in ("0000001_go-explore-cell", "0000002_periodic", "0000003_whatever", "notes"):
        assert keep in after
    assert any("_milestone-" in n for n in after) and any(n.endswith("_final") for n in after)


def test_latest_is_relative_and_resume_latest_works_after_moving_the_save_dir(tmp_path):
    saves = tmp_path / "saves"
    a = _run(tmp_path, 120, save_dir=str(saves), save_every=50)
    ptr = (saves / "latest").read_text().strip()
    assert not Path(ptr).is_absolute() and ptr == Path(a["saves"][-1]).relative_to(saves).as_posix()
    moved = tmp_path / "elsewhere" / "saves"          # e.g. container /saves -> host ~/.game-brain/saves
    moved.parent.mkdir()
    shutil.move(str(saves), str(moved))
    side = savestate.resolve_resume("latest", moved)
    assert Path(side["_path"]) == (moved / ptr).resolve() and side["step"] == 120
    b = _run(tmp_path, 10, out="runs-b", save_dir=str(moved), resume="latest")
    assert list(iter_steps(b["log"]))[0]["step"] == 120
    assert b["resumed_from"] == str((moved / ptr).resolve())


def test_old_absolute_latest_pointers_still_work(tmp_path):
    saves = tmp_path / "saves"
    a = _run(tmp_path, 60, save_dir=str(saves), save_every=50)
    final = Path(a["saves"][-1]).resolve()
    (saves / "latest").write_text(str(final) + "\n")                       # pre-keep-periodic format, inside
    assert Path(savestate.resolve_resume("latest", saves)["_path"]) == final
    (saves / "latest").write_text(f"/saves/{final.parent.name}/{final.name}\n")   # written inside Docker
    assert Path(savestate.resolve_resume("latest", saves)["_path"]) == final
    outside = tmp_path / "outside.json"                                    # absolute, outside save-dir: ignored
    shutil.copy(final, outside)
    (saves / "latest").write_text(str(outside) + "\n")
    assert savestate._read_latest(saves) is None
    assert Path(savestate.resolve_resume("latest", saves)["_path"]) == final   # falls back to newest by mtime
    (saves / "latest").write_text("../outside.json\n")                     # relative escape: ignored
    assert savestate._read_latest(saves) is None


def test_dashboard_session_gets_keep_periodic(tmp_path):
    a = live.build_parser().parse_args(["--adapter", "mock-house", "--save-dir", str(tmp_path / "s"),
                                        "--keep-periodic", "4", "--out", str(tmp_path / "r")])
    assert setup.Session.from_args(a, quiet=True).config()["keep_periodic"] == 4
