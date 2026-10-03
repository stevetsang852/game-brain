import json
import sqlite3
from pathlib import Path

import pytest

from game_brain import demo, savestate
from game_brain.adapters import make_adapter
from game_brain.arbiter import Arbiter
from game_brain.brain import RuleBrain
from game_brain.memory import ExperienceMemory, inspect_memory, route
from game_brain.runlog import iter_steps, read_log, replay
from game_brain.schema import Action, Observation


def rows(path, sql="SELECT * FROM transitions ORDER BY run_id,step_id"):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql)]


def test_complete_transitions_include_last_step_and_replay(tmp_path):
    s = demo.run(steps=20, quiet=True, out_dir=str(tmp_path / "runs"))
    transitions = rows(s["memory"])
    steps = list(iter_steps(s["log"]))
    assert len(transitions) == len(steps) == 20
    for db, log in zip(transitions, steps):
        before, after = json.loads(db["state_before"]), json.loads(db["state_after"])
        assert before == log["observation"] and after == log["observation_after"]
        assert after["frame"] - before["frame"] == log["frames_advanced"]
        assert json.loads(db["executed_action"]) == log["executed_action"]
        assert bool(db["truncated"]) == log["experience"]["truncated"]
    assert steps[-1]["observation_after"]["ram"] == s["final_ram"]
    assert transitions[-1]["truncated"] == 1 and not transitions[-1]["terminated"]
    assert sum(r["truncated"] for r in transitions) == 1
    assert replay(s["log"], make_adapter("mock")) == []


def test_final_after_state_is_verified_not_just_before(tmp_path):
    s = demo.run(steps=1, quiet=True, out_dir=str(tmp_path / "runs"))
    records = list(read_log(s["log"]))
    step = next(r for r in records if r["kind"] == "step")
    step["observation_after"]["ram"]["party_count"] = 99
    Path(s["log"]).write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    assert any("post-action ram" in m for m in replay(s["log"], make_adapter("mock")))


def test_shadow_stores_only_executed_idle(tmp_path):
    s = demo.run(steps=4, mode="shadow", quiet=True, out_dir=str(tmp_path))
    db = rows(s["memory"])
    assert all(r["actor"] == "none" for r in db)
    assert all(json.loads(r["executed_action"])["presses"][0]["button"] == "NONE" for r in db)
    assert all(r["reward"] == 0 for r in db)
    assert all(s["proposed_action"]["presses"][0]["button"] == "A" for s in iter_steps(s["log"]))


def test_manual_and_assist_use_human_action(tmp_path):
    adapter = make_adapter("mock")
    arbiter = Arbiter([RuleBrain()], mode="assist")
    memory = ExperienceMemory(tmp_path / "archive", adapter, "human-run", "rules-v1",
                              ["rule"], tmp_path / "run.jsonl")
    try:
        before = adapter.observe()
        memory.start(before, 0)
        assert arbiter.submit_manual(Action.tap("B", source="manual"))
        result = arbiter.step(before)
        advanced = adapter.act(result.executed)
        data = memory.record(0, before, result, adapter.observe(), advanced)
        assert data["actor"] == "human"
        saved = rows(memory.path)[0]
        assert saved["brain"] == "human" and json.loads(saved["executed_action"])["source"] == "manual"
        assert json.loads(saved["executed_action"])["presses"][0]["button"] == "B"
    finally:
        memory.close()


def test_novelty_persists_no_loop_or_reload_farming(tmp_path):
    args = dict(steps=45, quiet=True, out_dir=str(tmp_path / "runs"))
    a = demo.run(**args)
    b = demo.run(**args)
    assert a["log"] != b["log"]  # same-second runs cannot overwrite logs or SQLite identities
    db = rows(a["memory"])
    arun = next(read_log(a["log"]))["run_id"]
    brun = next(read_log(b["log"]))["run_id"]
    first = [r for r in db if r["run_id"] == arun]
    second = [r for r in db if r["run_id"] == brun]
    assert any(r["reward"] > 0 for r in first)
    assert all(r["reward"] == 0 for r in second)
    stats = inspect_memory(Path(a["memory"]).parent)
    assert stats["runs"] == 2 and stats["transitions"] == 90 and stats["cells"]


def test_resume_cuts_episode_and_route_excludes_abandoned_tail(tmp_path):
    root = tmp_path / "memory"
    saves = tmp_path / "saves"
    a = demo.run("mock-house", steps=80, brains="path,rule", quiet=True, save_dir=str(saves),
                 save_every=50, out_dir=str(tmp_path / "a"))
    side = next(p for p in a["saves"] if p.endswith("0000050_periodic.json"))
    b = demo.run("mock-house", steps=6, brains="path,rule", quiet=True, resume=side,
                 out_dir=str(tmp_path / "b"))
    arun = next(read_log(a["log"]))["run_id"]
    brun = next(read_log(b["log"]))["run_id"]
    episodes = rows(a["memory"], "SELECT * FROM episodes ORDER BY rowid")
    assert episodes[0]["episode_id"] != episodes[1]["episode_id"]
    assert episodes[1]["parent_run_id"] == arun and episodes[1]["parent_step"] == 50
    assert json.loads(episodes[1]["initial_state"]) == next(iter_steps(b["log"]))["observation"]
    actions = route(root, brun, 55)
    assert len(actions) == 56
    assert [r["run_id"] for r in actions[:50]] == [arun] * 50
    assert [r["run_id"] for r in actions[50:]] == [brun] * 6
    adapter = make_adapter("mock-house")
    adapter.reset()
    for rec in actions:
        assert adapter.observe().summary() == rec["state_before"]
        adapter.act(Action.from_dict(rec["executed_action"]))
        assert adapter.observe().summary() == rec["state_after"]
    assert replay(b["log"], make_adapter("mock-house")) == []


def test_exploration_saves_resumable_and_not_in_normal_latest(tmp_path):
    saves = tmp_path / "saves"
    s = demo.run("mock-house", steps=8, brains="path,rule", quiet=True,
                 save_dir=str(saves), out_dir=str(tmp_path / "runs"))
    root = Path(s["memory"]).parent
    stats = inspect_memory(root)
    assert stats["cells"] and all(c["save"] for c in stats["cells"])
    cell = next(c for c in stats["cells"] if c["step_id"] >= 0)
    sv = savestate.load_sidecar(cell["save"])
    assert sv["memory"]["step"] == cell["step_id"] + 1
    assert sv["starter"]["seed"] == 0
    assert sv["reason"].startswith("exploration-cell-")
    latest = savestate.resolve_resume("latest", saves)
    assert latest["reason"] == "final"
    r = demo.run("mock-house", steps=1, brains="path,rule", quiet=True, resume=cell["save"],
                 out_dir=str(tmp_path / "resume"))
    assert next(iter_steps(r["log"]))["step"] == cell["step_id"] + 1
    assert replay(r["log"], make_adapter("mock-house")) == []


def test_rom_namespaces_and_newer_schema_rejected(tmp_path):
    adapter = make_adapter("mock")
    root = tmp_path / "archive"
    for i, rom in enumerate(("rom-a", "rom-b")):
        adapter.rom_sha1 = rom
        memory = ExperienceMemory(root, adapter, str(i), "policy", [], tmp_path / "run.jsonl")
        try:
            before = adapter.reset()
            memory.start(before, 0)
            arb = Arbiter([RuleBrain()])
            for step in range(12):
                result = arb.step(before)
                advanced = adapter.act(result.executed)
                after = adapter.observe()
                memory.record(step, before, result, after, advanced)
                before = after
        finally:
            memory.close()
    with pytest.raises(ValueError, match="namespace"):
        inspect_memory(root)
    assert inspect_memory(root, "mock:rom-a")["cells"]
    assert inspect_memory(root, "mock:rom-b")["cells"]
    with sqlite3.connect(root / "experience.sqlite3") as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        ExperienceMemory(root, adapter, "bad", "policy", [], tmp_path / "bad.jsonl")


def test_memory_refuses_repo_and_no_memory_still_has_after_state(tmp_path):
    with pytest.raises(ValueError, match="inside the repo"):
        demo.run(steps=1, quiet=True, memory_dir=str(Path.cwd() / "memory"))
    s = demo.run(steps=1, quiet=True, out_dir=str(tmp_path), no_memory=True)
    assert s["memory"] is None
    assert next(iter_steps(s["log"]))["observation_after"]["frame"] == s["final_frame"]


def test_task_success_terminates_then_starts_new_episode(tmp_path):
    adapter = make_adapter("mock")
    memory = ExperienceMemory(tmp_path / "archive", adapter, "goal", "policy", [], tmp_path / "run.jsonl")
    try:
        before = Observation(0, ram={"scene": "overworld", "in_battle": False, "map_bank": 3,
                                    "map_id": 19, "player_x": 1, "player_y": 1, "party_count": 1})
        memory.start(before, 0)
        arb = Arbiter([RuleBrain()])
        after = Observation(8, ram={**before.ram, "map_id": 1})
        data = memory.record(0, before, arb.step(before), after, 8)
        assert data["terminated"] and not data["truncated"]
        first_episode = data["episode_id"]
        final = Observation(16, ram=dict(after.ram))
        data = memory.record(1, after, arb.step(after), final, 8)
        assert data["episode_id"] != first_episode and not data["terminated"]
        memory.finish("step_limit")
        db = rows(memory.path)
        assert db[0]["terminated"] == 1 and db[0]["truncated"] == 0
        assert db[1]["terminated"] == 0 and db[1]["truncated"] == 1
    finally:
        memory.close()


def test_no_save_cells_later_get_representative_from_correct_run(tmp_path):
    args = dict(adapter_name="mock-house", steps=8, brains="path,rule",
                quiet=True, out_dir=str(tmp_path / "runs"))
    first = demo.run(**args)
    assert all(c["save"] is None for c in inspect_memory(Path(first["memory"]).parent)["cells"])
    second = demo.run(**args, save_dir=str(tmp_path / "saves"))
    second_id = next(read_log(second["log"]))["run_id"]
    stats = inspect_memory(Path(second["memory"]).parent)
    for cell in stats["cells"]:
        assert cell["run_id"] == second_id
        side = savestate.load_sidecar(cell["save"])
        assert side["memory"]["run_id"] == cell["run_id"]
        assert side["memory"]["step"] == cell["step_id"] + 1
        assert side["exploration_cell"] == cell["cell"]


def test_committed_experience_survives_unfinished_run(tmp_path):
    adapter = make_adapter("mock")
    root = tmp_path / "archive"
    memory = ExperienceMemory(root, adapter, "unfinished", "policy", [], tmp_path / "run.jsonl")
    before = adapter.reset()
    memory.start(before, 0)
    result = Arbiter([RuleBrain()]).step(before)
    advanced = adapter.act(result.executed)
    memory.record(0, before, result, adapter.observe(), advanced)
    memory.close()  # no finish event: equivalent to losing the process after its committed step
    db = rows(root / "experience.sqlite3")
    assert len(db) == 1 and not db[0]["truncated"] and not db[0]["terminated"]
    assert rows(root / "experience.sqlite3", "SELECT ended_at FROM runs")[0]["ended_at"] is None
    assert inspect_memory(root)["transitions"] == 1


def test_battle_rewards_require_outcome_edge_and_do_not_repeat(tmp_path):
    adapter = make_adapter("mock")
    memory = ExperienceMemory(tmp_path / "archive", adapter, "battle", "policy", [], tmp_path / "run.jsonl")
    try:
        ram = {"scene": "battle", "in_battle": True, "map_bank": 3, "map_id": 19,
               "battle": {"outcome": None, "opponent": {"species": 1}}}
        before = Observation(0, ram=ram)
        memory.start(before, 0)
        arb = Arbiter([RuleBrain()])
        after = Observation(8, ram={**ram, "battle": {**ram["battle"], "outcome": "win"}})
        assert memory.record(0, before, arb.step(before), after, 8)["reward_parts"]["battle_win"] == 5
        repeat = Observation(16, ram=after.ram)
        assert memory.record(1, after, arb.step(after), repeat, 8)["reward_parts"]["battle_win"] == 0
        next_battle = Observation(24, ram=ram)
        memory.record(2, repeat, arb.step(repeat), next_battle, 8)
        won = Observation(32, ram=after.ram)
        assert memory.record(3, next_battle, arb.step(next_battle), won, 8)["reward_parts"]["battle_win"] == 0
        started = Observation(40, ram=ram)
        memory.record(4, won, arb.step(won), started, 8)
        lost = Observation(48, ram={**ram, "battle": {**ram["battle"], "outcome": "lose"}})
        assert memory.record(5, started, arb.step(started), lost, 8)["reward_parts"]["whiteout"] == -10
    finally:
        memory.close()


def test_route_rejects_missing_requested_steps(tmp_path):
    s = demo.run(steps=3, quiet=True, out_dir=str(tmp_path / "runs"))
    run_id = next(read_log(s["log"]))["run_id"]
    with pytest.raises(ValueError, match="incomplete route"):
        route(Path(s["memory"]).parent, run_id, 10)
