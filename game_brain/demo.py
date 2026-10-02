"""End-to-end demo loop: adapter -> arbiter(brains) -> adapter, with a JSONL run log.

    python -m game_brain.demo --adapter mock --steps 60 --mode auto
    python -m game_brain.demo --adapter mock --steps 60 --brains llm,rule --switch 30:shadow

Writes ``runs/<timestamp>/run.jsonl`` (gitignored) and prints a summary.
"""

from __future__ import annotations

import argparse
import collections
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .adapters import make_adapter
from .arbiter import Arbiter
from .brain import make_brain
from .runlog import RunLogWriter
from .schema import Mode, ModeCommand


def _parse_switches(items: List[str]) -> Dict[int, Mode]:
    out = {}
    for it in items:
        step, _, mode = it.partition(":")
        out[int(step)] = Mode.parse(mode)
    return out


def run(adapter_name: str = "mock", steps: int = 60, mode: str = "auto", brains: str = "rule,random",
        seed: int = 0, out_dir: str = "runs", switches: Optional[Dict[int, Mode]] = None,
        screenshot_every: int = 0, quiet: bool = False) -> dict:
    adapter = make_adapter(adapter_name)
    brain_objs = []
    for name in brains.split(","):
        name = name.strip()
        brain_objs.append(make_brain(name, seed=seed) if name == "random" else make_brain(name))
    arbiter = Arbiter(brain_objs, mode=mode)
    arbiter.reset()
    switches = switches or {}

    run_dir = Path(out_dir) / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.jsonl"
    by_brain: collections.Counter = collections.Counter()
    executed_count = 0
    shots = []
    t0 = time.time()

    with RunLogWriter(log_path) as log:
        log.header(adapter=adapter.name, brains=[b.name for b in brain_objs], mode=arbiter.mode.value,
                   steps=steps, seed=seed)
        obs = adapter.reset()
        for step in range(steps):
            if step in switches:
                arbiter.apply_mode(ModeCommand(switches[step], issued_by="demo-script"))
                log.event("mode_change", step=step, frame=obs.frame, mode=arbiter.mode.value)
            obs = adapter.observe()
            result = arbiter.step(obs)
            advanced = adapter.act(result.executed) if result.executed else 0
            by_brain[result.decision.brain] += 1
            executed_count += int(result.decision.executed)
            log.step(step, obs, result, advanced)
            if screenshot_every and step % screenshot_every == 0:
                p = adapter.screenshot(str(run_dir / f"step{step:05d}.png"))
                if p:
                    shots.append(p)
            if not quiet and (step < 3 or step % max(1, steps // 10) == 0):
                print(f"step {step:4d} frame {obs.frame:6d} [{result.mode.value:6s}] "
                      f"{result.decision.brain:6s}: {result.decision.reason}")
        final = adapter.observe()
        summary = {
            "steps": steps, "final_frame": final.frame, "final_ram": final.ram,
            "decisions_by_brain": dict(by_brain), "executed_brain_or_manual_actions": executed_count,
            "mode_final": arbiter.mode.value, "log": str(log_path), "log_lines": log.lines + 1,
            "screenshots": shots, "wall_seconds": round(time.time() - t0, 3),
        }
        log.event("summary", **summary)
    adapter.close()
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m game_brain.demo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="mock", help="adapter name (only 'mock' ships here)")
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--mode", default="auto", choices=[m.value for m in Mode])
    ap.add_argument("--brains", default="rule,random", help="priority list, e.g. llm,rule,random")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--switch", action="append", default=[], metavar="STEP:MODE",
                    help="switch mode at a step, e.g. --switch 30:shadow (repeatable)")
    ap.add_argument("--screenshot-every", type=int, default=0)
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    s = run(a.adapter, a.steps, a.mode, a.brains, a.seed, a.out, _parse_switches(a.switch),
            a.screenshot_every, a.quiet)
    print("\n=== summary ===")
    for k, v in s.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
