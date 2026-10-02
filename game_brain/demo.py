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
from pathlib import Path
from typing import Dict, List, Optional

from .adapters import make_adapter
from .arbiter import Arbiter
from .brain import make_brains
from . import savestate
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
        screenshot_every: int = 0, quiet: bool = False, battle_confidence: Optional[float] = None,
        save_dir: Optional[str] = None, save_every: int = savestate.DEFAULT_SAVE_EVERY,
        resume: Optional[str] = None) -> dict:
    """``save_dir``: write save states there (None = no saves). ``resume``: "latest" (in
    ``save_dir``, default ~/.game-brain/saves) or a sidecar/state path; the run continues from it."""
    side = None
    kw = {}
    if resume:
        side = savestate.resolve_resume(resume, save_dir or savestate.default_save_dir())
        if side.get("_sav_path") and adapter_name in ("mgba", "gba_mgba", "firered"):
            kw["battery"] = open(side["_sav_path"], "rb").read()
    adapter = make_adapter(adapter_name, **kw)
    brain_objs = make_brains(brains, seed=seed, battle_confidence=battle_confidence)
    arbiter = Arbiter(brain_objs, mode=mode)
    arbiter.reset()
    switches = switches or {}
    start_step = 0
    resumed_from = None
    if side is not None:
        if side.get("adapter") != adapter.name:
            raise ValueError(f"save is for adapter {side.get('adapter')!r}, not {adapter.name!r}")
        rom = getattr(adapter, "rom_sha1", None)
        if side.get("rom_sha1") and rom and side["rom_sha1"] != rom:
            raise ValueError(f"save was made with ROM sha1 {side['rom_sha1']}, this ROM is {rom}")
        for b in brain_objs:          # milestone progress from the sidecar (PathBrain also recomputes)
            if hasattr(b, "planner"):
                b.planner.restore(side.get("milestones_done"))
        start_step = int(side["step"])
        resumed_from = {"sidecar": side["_path"], "state": side["_state_path"], "state_sha1": side["state_sha1"],
                        "step": start_step, "frame": side["frame"], "adapter_state": side.get("adapter_state"),
                        "sav": side.get("_sav_path"), "milestone": side.get("milestone")}

    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.jsonl"
    by_brain: collections.Counter = collections.Counter()
    executed_count = 0
    shots = []
    t0 = time.time()

    saver = None
    if save_dir:
        saver = savestate.SaveManager(save_dir, adapter, [b.name for b in brain_objs], run_id, every=save_every,
                                      resumed_from=side["_path"] if side else None)
        if not saver.enabled:
            print(f"warning: adapter {adapter.name} has no save states; --save-dir ignored", file=sys.stderr)
    saves = []

    with RunLogWriter(log_path) as log:
        log.header(adapter=adapter.name, brains=[b.name for b in brain_objs], mode=arbiter.mode.value,
                   steps=steps, seed=seed, **({"resumed_from": resumed_from} if resumed_from else {}))
        obs = adapter.reset()
        if side is not None:
            obs = adapter.load_state(savestate.read_state(side), frame=side["frame"],
                                     adapter_state=side.get("adapter_state"))
            log.event("resumed", **resumed_from)
            if not quiet:
                print(f"resumed from {side['_path']} (step {start_step}, frame {side['frame']}, "
                      f"map {side.get('map_bank')}/{side.get('map_id')} at ({side.get('x')}, {side.get('y')}))")
        for step in range(start_step, start_step + steps):
            if step in switches:
                arbiter.apply_mode(ModeCommand(switches[step], issued_by="demo-script"))
                log.event("mode_change", step=step, frame=obs.frame, mode=arbiter.mode.value)
            obs = adapter.observe()
            result = arbiter.step(obs)
            advanced = adapter.act(result.executed) if result.executed else 0
            by_brain[result.decision.brain] += 1
            executed_count += int(result.decision.executed)
            log.step(step, obs, result, advanced)
            if saver and saver.enabled:
                for sv in saver.after_step(step + 1, result.decision.milestones):
                    saves.append(sv["_path"])
                    log.event("save", step=step + 1, frame=sv["frame"], reason=sv["reason"], path=sv["_path"])
            if screenshot_every and step % screenshot_every == 0:
                p = adapter.screenshot(str(run_dir / f"step{step:05d}.png"))
                if p:
                    shots.append(p)
            if not quiet and (step - start_step < 3 or step % max(1, steps // 10) == 0):
                print(f"step {step:4d} frame {obs.frame:6d} [{result.mode.value:6s}] "
                      f"{result.decision.brain:6s}: {result.decision.reason}")
        if saver and saver.enabled:
            last_ms = result.decision.milestones if steps else None
            sv = saver.save(start_step + steps, last_ms, reason="final")
            saves.append(sv["_path"])
            log.event("save", step=start_step + steps, frame=sv["frame"], reason="final", path=sv["_path"])
        final = adapter.observe()
        summary = {
            "steps": steps, "final_frame": final.frame, "final_ram": final.ram,
            "decisions_by_brain": dict(by_brain), "executed_brain_or_manual_actions": executed_count,
            "mode_final": arbiter.mode.value, "log": str(log_path), "log_lines": log.lines + 1,
            "screenshots": shots, "wall_seconds": round(time.time() - t0, 3),
            "saves": saves, "resumed_from": resumed_from["sidecar"] if resumed_from else None,
        }
        log.event("summary", **summary)
    adapter.close()
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m game_brain.demo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="mock", help="adapter name: mock | mgba (mgba needs $GAME_BRAIN_ROM)")
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--mode", default="auto", choices=[m.value for m in Mode])
    ap.add_argument("--brains", default="rule,random",
                    help="priority list, e.g. battle,path,rule (battle first: it only acts in battle)")
    ap.add_argument("--battle-confidence", type=float, default=None, metavar="X",
                    help="RuleBattleBrain confidence threshold 0-1 (default 0.6); below it hands off in assist")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--switch", action="append", default=[], metavar="STEP:MODE",
                    help="switch mode at a step, e.g. --switch 30:shadow (repeatable)")
    ap.add_argument("--screenshot-every", type=int, default=0)
    ap.add_argument("--save-dir", default=None, metavar="DIR",
                    help="save states go here (default ~/.game-brain/saves or $GAME_BRAIN_SAVE_DIR; "
                         "refused if inside the repo)")
    ap.add_argument("--save-every", type=int, default=savestate.DEFAULT_SAVE_EVERY, metavar="N",
                    help=f"periodic save every N steps (default {savestate.DEFAULT_SAVE_EVERY}; 0 = only "
                         "milestones + end)")
    ap.add_argument("--no-save", action="store_true", help="don't write save states")
    ap.add_argument("--resume", default=None, metavar="PATH|latest",
                    help="continue from a save (sidecar .json or .state path, or 'latest' in --save-dir)")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    save_dir = None if a.no_save else (a.save_dir or str(savestate.default_save_dir()))
    try:
        s = run(a.adapter, a.steps, a.mode, a.brains, a.seed, a.out, _parse_switches(a.switch),
                a.screenshot_every, a.quiet, battle_confidence=a.battle_confidence,
                save_dir=save_dir, save_every=a.save_every,
                resume=a.resume)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n=== summary ===")
    for k, v in s.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
