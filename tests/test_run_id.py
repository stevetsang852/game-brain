"""Run ids are UTC (``20261002T083408Z``); old local-time ids (``20261002-163408``) still resume.
The summary's ``saves`` lists only saves that still exist (not ones pruned by --keep-periodic)."""
import json
import re
import shutil
from pathlib import Path

from game_brain import demo, savestate, setup
from game_brain.runlog import iter_steps, read_log

RUN_ID = re.compile(r"^\d{8}T\d{6}Z$")


def _run(tmp_path, out, steps, **kw):
    return demo.run("mock-house", steps=steps, mode="auto", brains="path,rule", out_dir=str(tmp_path / out),
                    quiet=True, **kw)


def test_run_id_is_utc_with_z_and_sorts_chronologically():
    assert setup.new_run_id(0) == "19700101T000000Z"
    assert setup.new_run_id(1790930048) == "20261002T083408Z"         # 16:34:08 Taipei = 08:34:08Z
    times = [1790930048, 1790930048 + 59, 1790930048 + 3600 * 15, 1790930048 + 86400 * 40]
    ids = [setup.new_run_id(t) for t in times]
    assert all(RUN_ID.match(i) for i in ids) and sorted(ids) == ids


def test_runs_and_saves_use_the_utc_id(tmp_path):
    s = _run(tmp_path, "r", 60, save_dir=str(tmp_path / "saves"), save_every=50)
    run_id = Path(s["log"]).parent.name
    assert RUN_ID.match(run_id)
    side = json.loads(Path(s["saves"][-1]).read_text())
    assert side["run_id"] == run_id and Path(s["saves"][-1]).parent.name == run_id
    assert (tmp_path / "saves" / "latest").read_text().strip().startswith(run_id + "/")


def test_old_local_time_run_ids_still_resume(tmp_path):
    saves = tmp_path / "saves"
    a = _run(tmp_path, "a", 60, save_dir=str(saves), save_every=50)
    new_dir = Path(a["saves"][-1]).parent
    old_dir = saves / "20261002-163408"                                 # a pre-UTC run id
    shutil.move(str(new_dir), str(old_dir))
    (saves / "latest").write_text(f"{old_dir}/0000060_final.json\n")    # old absolute pointer
    for spec in ("latest", str(old_dir / "0000050_periodic.json")):
        b = _run(tmp_path, "b", 5, save_dir=str(saves), resume=spec)
        first = next(iter_steps(b["log"]))["step"]
        assert first in (50, 60) and Path(b["resumed_from"]).parent == old_dir.resolve()


def test_summary_saves_do_not_list_pruned_periodic_saves(tmp_path):
    s = _run(tmp_path, "r", 120, save_dir=str(tmp_path / "saves"), save_every=10, keep_periodic=2)
    assert all(Path(p).is_file() for p in s["saves"])
    periodic = [p for p in s["saves"] if p.endswith("_periodic.json")]
    assert len(periodic) == 2
    pruned = [r["path"] for r in read_log(s["log"]) if r["kind"] == "save_pruned"]
    assert pruned and not set(pruned) & set(s["saves"])
    assert any("_milestone-" in p for p in s["saves"]) and s["saves"][-1].endswith("_final.json")
    summ = [r for r in read_log(s["log"]) if r["kind"] == "summary"][-1]
    assert summ["saves"] == s["saves"]
