"""Run-log decision dedupe: ``decision.milestones`` written on change only and
``executed_action`` omitted when equal to ``proposed_action`` (``executed_same`` marker).
The reader restores both; old logs and the live dashboard envelopes are unaffected."""

import json

from game_brain import demo
from game_brain.adapters.mock import MockAdapter, MockHouseAdapter
from game_brain.arbiter import Arbiter, StepResult
from game_brain.brain import RuleBrain
from game_brain.dashboard.live import run as live_run
from game_brain.runlog import RunLogWriter, iter_steps, read_log, replay
from game_brain.schema import Action, ButtonPress, Decision, Mode, Observation

MS_A = [{"id": "a", "label": "A", "done": False}, {"id": "b", "label": "B", "done": False}]
MS_B = [{"id": "a", "label": "A", "done": True}, {"id": "b", "label": "B", "done": False}]


def _res(mode, ms=None, proposed=None, executed=None, executed_flag=True, actor="brain"):
    dec = Decision(brain="path", plan="p", reason="r", mode=mode.value, executed=executed_flag,
                   actor=actor, milestones=ms)
    return StepResult(mode=mode, decision=dec, proposed=proposed, executed=executed)


def _write(path, results):
    with RunLogWriter(path) as log:
        log.header(adapter="test")
        for i, r in enumerate(results):
            log.step(i, Observation(frame=i, ram={"x": i}), r, 1, ts=0.0)


def _expected(results):
    """What iter_steps must yield for each step (the pre-dedupe record contents)."""
    return [{"decision": r.decision.to_dict(),
             "proposed_action": r.proposed.to_dict() if r.proposed else None,
             "executed_action": r.executed.to_dict() if r.executed else None} for r in results]


def _restored(path):
    return [{k: rec[k] for k in ("decision", "proposed_action", "executed_action")} for rec in iter_steps(path)]


def test_roundtrip_milestones_written_on_change_only(tmp_path):
    up, a = Action.tap("UP"), Action.tap("A")
    results = [_res(Mode.AUTO, MS_A, up, up), _res(Mode.AUTO, MS_A, up, up), _res(Mode.AUTO, MS_A, a, a),
               _res(Mode.AUTO, MS_B, up, up), _res(Mode.AUTO, MS_B, up, up),
               _res(Mode.AUTO, None, a, a),                 # brain without milestones
               _res(Mode.AUTO, MS_B, up, up),               # same as the last *written* list -> rewritten
               _res(Mode.AUTO, MS_A, up, up), _res(Mode.AUTO, MS_A, up, up)]
    p = tmp_path / "run.jsonl"
    _write(p, results)
    raw = [r for r in read_log(p) if r["kind"] == "step"]
    written = [("milestones" in r["decision"], r.get("milestones_same", False)) for r in raw]
    assert written == [(True, False), (False, True), (False, True), (True, False), (False, True),
                       (False, False), (True, False), (True, False), (False, True)]
    assert all("executed_action" not in r and r["executed_same"] is True for r in raw)
    assert _restored(p) == _expected(results)
    assert all("milestones_same" not in r and "executed_same" not in r for r in iter_steps(p))


def test_shadow_manual_assist_and_missing_actions_are_unambiguous(tmp_path):
    up, idle = Action.tap("UP"), Action.wait(8)
    human = Action([ButtonPress("LEFT", 8)], source="manual")
    results = [
        _res(Mode.SHADOW, MS_A, up, idle, executed_flag=False),      # shadow: proposed != executed (idle)
        _res(Mode.SHADOW, MS_A, up, None, executed_flag=False),      # nothing executed at all
        _res(Mode.MANUAL, None, None, human, actor="human"),         # manual: no proposal
        _res(Mode.MANUAL, None, None, None, actor="none"),           # no proposal, no action
        _res(Mode.ASSIST, MS_A, None, human, actor="human"),         # human preempt
        _res(Mode.AUTO, MS_A, up, up),                               # the only dedupable one
    ]
    p = tmp_path / "run.jsonl"
    _write(p, results)
    raw = [r for r in read_log(p) if r["kind"] == "step"]
    assert [r.get("executed_same", False) for r in raw] == [False] * 5 + [True]
    assert raw[1]["executed_action"] is None and raw[3]["executed_action"] is None  # explicit null stays
    assert raw[0]["executed_action"] == idle.to_dict()
    assert "executed_action" not in raw[5]
    assert _restored(p) == _expected(results)


def test_old_logs_read_identically(tmp_path):
    """A log written without the dedupe (old writer) passes through iter_steps unchanged."""
    up = Action.tap("UP")
    results = [_res(Mode.AUTO, MS_A, up, up), _res(Mode.AUTO, MS_A, up, up), _res(Mode.SHADOW, MS_A, up, None)]
    p = tmp_path / "old.jsonl"
    with open(p, "w") as f:
        f.write(json.dumps({"kind": "header"}) + "\n")
        for i, (r, e) in enumerate(zip(results, _expected(results))):
            f.write(json.dumps({"kind": "step", "step": i, "frame": i, "observation": {"ram": {}}, **e}) + "\n")
    assert _restored(p) == _expected(results)
    assert [r for r in iter_steps(p)] == [r for r in read_log(p) if r["kind"] == "step"]


def test_checked_in_old_example_still_replays():
    from pathlib import Path
    ex = Path(__file__).resolve().parent.parent / "examples" / "mock_run.jsonl"
    assert not any("executed_same" in r or "milestones_same" in r for r in read_log(ex))
    assert replay(ex, MockAdapter()) == []


def test_new_logs_replay_and_restore_actions(tmp_path):
    s = demo.run("mock-house", steps=200, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    raw = [r for r in read_log(s["log"]) if r["kind"] == "step"]
    assert sum(r.get("executed_same", False) for r in raw) > 150
    assert sum(r.get("milestones_same", False) for r in raw) > 150
    for r in iter_steps(s["log"]):
        Action.from_dict(r["executed_action"])
        dec = Decision.from_dict(r["decision"])
        assert dec.milestones  # every step, incl. RuleBrain fallback (arbiter copies PathBrain's)
    assert replay(s["log"], MockHouseAdapter()) == []

    sh = demo.run("mock-house", steps=60, mode="shadow", brains="path,rule", out_dir=str(tmp_path / "sh"), quiet=True)
    assert not any(r.get("executed_same") for r in read_log(sh["log"]))
    assert replay(sh["log"], MockHouseAdapter()) == []


def _size_without_dedupe(path):
    """Bytes the same log would take with the old writer (restored steps re-serialised)."""
    steps = iter(iter_steps(path))
    n = 0
    for rec in read_log(path):
        if rec["kind"] == "step":
            rec = next(steps)
        n += len(json.dumps(rec, separators=(",", ":"), sort_keys=True)) + 1
    return n


def test_size_reduction_on_mock_house_run(tmp_path):
    s = demo.run("mock-house", steps=700, mode="auto", brains="path,rule", out_dir=str(tmp_path), quiet=True)
    from pathlib import Path
    new = Path(s["log"]).stat().st_size
    old = _size_without_dedupe(s["log"])
    assert new < 0.75 * old, (new, old)


class _CaptureServer:
    """Stand-in for DashboardServer: records every broadcast envelope, no dashboard input."""
    url = "test://capture"

    def __init__(self):
        self.sent = []

    def broadcast(self, env):
        self.sent.append(json.loads(json.dumps(env)))

    def poll(self):
        return []


def test_live_dashboard_envelopes_keep_full_decision_and_actions(tmp_path):
    """Frontend contract: the dedupe is log-only. Every live decision envelope still carries
    the full milestones and every status envelope both actions, step after step."""
    srv = _CaptureServer()
    s = live_run(srv, "mock-house", mode="auto", brains="path,rule", steps=120, step_delay=0,
                 screenshot_every=0, out_dir=str(tmp_path), quiet=True)
    decisions = [e["payload"] for e in srv.sent if e["type"] == "decision"]
    statuses = [e["payload"] for e in srv.sent if e["type"] == "status"]
    step_statuses = [s for s in statuses if not s.get("finished")]
    assert len(decisions) == len(step_statuses) == 120
    assert statuses[-1]["finished"] is True
    assert sum(d["brain"] == "path" for d in decisions) > 100
    for d in decisions:
        assert "type" not in d and not ({"milestones_same", "executed_same"} & d.keys())
        # every decision (PathBrain's, and RuleBrain's fallback via the arbiter) carries the full list
        assert len(d["milestones"]) == 17 and all({"id", "label", "done"} <= m.keys() for m in d["milestones"])
        Decision.from_dict({**d, "type": "decision"})
    for st in step_statuses:
        assert {"proposed_action", "executed_action"} <= st.keys()
        assert st["executed_action"] is not None and st["proposed_action"] is not None
        assert "executed_same" not in st
    # the log on disk *is* deduped, and restoring it gives exactly what went live
    raw = [r for r in read_log(s["log"]) if r["kind"] == "step"]
    assert sum(r.get("milestones_same", False) for r in raw) > 100
    assert sum(r.get("executed_same", False) for r in raw) > 100
    restored = list(iter_steps(s["log"]))
    assert [{**r["decision"], "type": None} for r in restored] == [{**d, "type": None} for d in decisions]
    assert [(r["proposed_action"], r["executed_action"]) for r in restored] == \
        [(st["proposed_action"], st["executed_action"]) for st in step_statuses]


def test_writer_does_not_mutate_live_objects(tmp_path):
    up = Action.tap("UP")
    r1, r2 = _res(Mode.AUTO, MS_A, up, up), _res(Mode.AUTO, MS_A, up, up)
    before = (r2.decision.to_dict(), r2.proposed.to_dict(), r2.executed.to_dict())
    _write(tmp_path / "run.jsonl", [r1, r2])
    assert (r2.decision.to_dict(), r2.proposed.to_dict(), r2.executed.to_dict()) == before
    assert r2.decision.milestones == MS_A
