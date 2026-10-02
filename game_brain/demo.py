"""End-to-end demo loop: adapter -> arbiter(brains) -> adapter, with a JSONL run log.

    python -m game_brain.demo --adapter mock --steps 60 --mode auto
    python -m game_brain.demo --adapter mock --steps 60 --brains llm,rule --switch 30:shadow
    python -m game_brain.demo --adapter mgba --brains battle,path,rule --steps 3000 --save-every 500
    python -m game_brain.demo --adapter mgba --brains battle,path,rule --steps 1000 --resume latest

Save states (``--save-dir``, default ~/.game-brain/saves, never inside the repo): one at every
milestone reached, every ``--save-every`` steps and at the end; format in notes/savestate-format.md.

Writes ``runs/<timestamp>/run.jsonl`` (gitignored) and prints a summary.
"""

from __future__ import annotations

import argparse
import collections
import sys
import time
from typing import Dict, List, Optional

from . import savestate
from .runlog import RunLogWriter
from .schema import Mode, ModeCommand
from .setup import Session, StopSignals, add_run_args, save_dir_from_args


def _parse_switches(items: List[str]) -> Dict[int, Mode]:
    out = {}
    for it in items:
        step, _, mode = it.partition(":")
        out[int(step)] = Mode.parse(mode)
    return out


def run(adapter_name: str = "mock", steps: int = 60, mode: str = "auto", brains: str = "rule,random",
        seed: int = 0, out_dir: str = "runs", switches: Optional[Dict[int, Mode]] = None,
        screenshot_every: int = 0, quiet: bool = False, battle_confidence: Optional[float] = None,
        save_dir: Optional[str] = None, save_every: int = savestate.DEFAULT_SAVE_EVERY,
        resume: Optional[str] = None, keep_periodic: int = savestate.DEFAULT_KEEP_PERIODIC) -> dict:
    """``save_dir``: write save states there (None = no saves). ``resume``: "latest" (in
    ``save_dir``, default ~/.game-brain/saves) or a sidecar/state path; the run continues from it.
    Adapter / brains / saves are built by :class:`game_brain.setup.Session` (shared with the dashboard)."""
    sess = Session(adapter_name, brains, mode, seed, battle_confidence, out_dir, save_dir, save_every, resume,
                   quiet=quiet, keep_periodic=keep_periodic)
    adapter, arbiter = sess.adapter, sess.arbiter
    switches = switches or {}
    start_step = sess.start_step
    by_brain: collections.Counter = collections.Counter()
    executed_count = 0
    shots = []
    t0 = time.time()
    result = None

    steps_done = 0
    stopped_by = None
    with RunLogWriter(sess.log_path) as log, StopSignals() as stop:
        log.header(**sess.header_info(steps=steps, seed=seed))
        obs = sess.start(log)
        for step in range(start_step, start_step + steps):
            if stop.requested:          # checked only here: the previous step completed in full
                break
            if step in switches:
                arbiter.apply_mode(ModeCommand(switches[step], issued_by="demo-script"))
                log.event("mode_change", step=step, frame=obs.frame, mode=arbiter.mode.value)
            obs = adapter.observe()
            result = arbiter.step(obs)
            advanced = adapter.act(result.executed) if result.executed else 0
            by_brain[result.decision.brain] += 1
            executed_count += int(result.decision.executed)
            log.step(step, obs, result, advanced)
            steps_done += 1
            sess.after_step(log, step + 1, result)
            if screenshot_every and step % screenshot_every == 0:
                p = adapter.screenshot(str(sess.run_dir / f"step{step:05d}.png"))
                if p:
                    shots.append(p)
            if not quiet and (step - start_step < 3 or step % max(1, steps // 10) == 0):
                print(f"step {step:4d} frame {obs.frame:6d} [{result.mode.value:6s}] "
                      f"{result.decision.brain:6s}: {result.decision.reason}")
        stopped_by = stop.name
        if stopped_by:
            log.event("stopped", signal=stopped_by, step=start_step + steps_done)
            # stderr, even with -q: say why the run ended early
            print(f"{stopped_by}: stopping after step {start_step + steps_done} (final save + summary)",
                  file=sys.stderr)
        sess.finish(log, start_step + steps_done, result)
        final = adapter.observe()
        summary = {
            "steps": steps_done, "final_frame": final.frame, "final_ram": final.ram,
            "decisions_by_brain": dict(by_brain), "executed_brain_or_manual_actions": executed_count,
            "mode_final": arbiter.mode.value, "log": str(sess.log_path), "log_lines": log.lines + 1,
            "screenshots": shots, "wall_seconds": round(time.time() - t0, 3),
            "saves": sess.saves, "resumed_from": sess.resumed_from["sidecar"] if sess.resumed_from else None,
            "stopped_by": stopped_by,
        }
        log.event("summary", **summary)
    adapter.close()
    return summary


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m game_brain.demo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_run_args(ap, adapter_default="mock", brains_default="rule,random")   # shared with the dashboard
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--switch", action="append", default=[], metavar="STEP:MODE",
                    help="switch mode at a step, e.g. --switch 30:shadow (repeatable)")
    ap.add_argument("--screenshot-every", type=int, default=0)
    ap.add_argument("-q", "--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    save_dir = save_dir_from_args(a)
    try:
        s = run(a.adapter, a.steps, a.mode, a.brains, a.seed, a.out, _parse_switches(a.switch),
                a.screenshot_every, a.quiet, battle_confidence=a.battle_confidence,
                save_dir=save_dir, save_every=a.save_every,
                resume=a.resume, keep_periodic=a.keep_periodic)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n=== summary ===")
    for k, v in s.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
