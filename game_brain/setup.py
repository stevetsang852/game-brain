"""Run setup shared by the CLI (``game_brain.demo``) and the dashboard (``game_brain.dashboard``).

Both entry points build the adapter (FireRed RAM reads incl. collision / NPCs / party / battle
live in the mGBA adapter), the brain list (``battle,path,rule`` with PathBrain's FireRed
milestones and ``--battle-confidence``), the arbiter and the save manager (``--save-dir``,
``--save-every``, ``--no-save``, ``--resume``) here, so they cannot drift apart.

    ap = argparse.ArgumentParser(); add_run_args(ap, adapter_default="mock", brains_default=...)
    sess = Session.from_args(a)        # or Session(adapter_name=..., ...)
    with RunLogWriter(sess.log_path) as log:
        log.header(**sess.header_info(steps=n))
        obs = sess.start(log)
        ... per step: obs = adapter.observe(); result = arbiter.step(obs); act; log.step(...)
                      sess.after_step(log, steps_done, result)
        sess.finish(log, steps_done, result)

SIGTERM (``docker compose down``) and Ctrl-C are handled the same way (:class:`StopSignals`): the
current step finishes, then the loop stops and the final save + summary are written.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import savestate
from .adapters import make_adapter
from .arbiter import Arbiter
from .brain import make_brains
from .schema import Mode

#: brains that get the full FireRed stack (battle in battle, A* + milestones outside, A-mash fallback)
FULL_BRAINS = "battle,path,rule"
MGBA_NAMES = ("mgba", "gba_mgba", "firered")


def resolve_adapter(name: str) -> str:
    """``auto`` -> ``mgba`` if $GAME_BRAIN_ROM points at a file, else ``mock`` (with a warning)."""
    if name != "auto":
        return name
    if os.path.isfile(os.environ.get("GAME_BRAIN_ROM", "")):
        return "mgba"
    print("warning: --adapter auto: $GAME_BRAIN_ROM is not set to a ROM file -> using the 'mock' adapter "
          "(no collision / milestones; the real game needs --adapter mgba and $GAME_BRAIN_ROM)", file=sys.stderr)
    return "mock"


def add_run_args(ap: argparse.ArgumentParser, *, adapter_default: str, brains_default: str) -> None:
    """The flags both entry points share (same names, types, meaning)."""
    ap.add_argument("--adapter", default=adapter_default,
                    help=f"adapter name: auto | mock | mock-house | mock-battle | mgba (mgba needs $GAME_BRAIN_ROM; "
                         f"auto = mgba if $GAME_BRAIN_ROM is a file, else mock). Default {adapter_default}")
    ap.add_argument("--mode", default="auto", choices=[m.value for m in Mode])
    ap.add_argument("--brains", default=brains_default,
                    help=f"priority list, e.g. {FULL_BRAINS} (battle first: it only acts in battle). "
                         f"Default {brains_default}")
    ap.add_argument("--battle-confidence", type=float, default=None, metavar="X",
                    help="RuleBattleBrain confidence threshold 0-1 (default 0.6); below it hands off in assist")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--save-dir", default=None, metavar="DIR",
                    help="save states go here (default ~/.game-brain/saves or $GAME_BRAIN_SAVE_DIR; "
                         "refused if inside the repo)")
    ap.add_argument("--save-every", type=int, default=savestate.DEFAULT_SAVE_EVERY, metavar="N",
                    help=f"periodic save every N steps (default {savestate.DEFAULT_SAVE_EVERY}; 0 = only "
                         "milestones + end)")
    ap.add_argument("--keep-periodic", type=int, default=savestate.DEFAULT_KEEP_PERIODIC, metavar="N",
                    help=f"keep only the newest N periodic saves per run (default {savestate.DEFAULT_KEEP_PERIODIC}; "
                         "0 = keep all). Milestone and final saves are never deleted")
    ap.add_argument("--no-save", action="store_true", help="don't write save states")
    ap.add_argument("--resume", default=None, metavar="PATH|latest",
                    help="continue from a save (sidecar .json or .state path, or 'latest' in --save-dir)")


class StopSignals:
    """``with StopSignals() as stop:`` ... ``while ... and not stop.requested:`` (checked at the top
    of every iteration).

    SIGTERM and SIGINT (Ctrl-C) handlers only set a flag. The loop sees it before starting the next
    step, so the step in progress always completes (act + log + step counter) and the final save's
    step matches the game state: a resume never repeats or skips a step. Repeated signals change
    nothing. Without this, Python as PID 1 in the container ignores SIGTERM and ``docker compose down``
    SIGKILLs it after 10 s (no final save, no summary). Handlers are only installed from the main
    thread (signal module rule); the previous ones are restored on exit."""

    SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGINT") if hasattr(signal, n))

    def __init__(self):
        self.signum: Optional[int] = None
        self._old: Dict[int, Any] = {}

    def __enter__(self) -> "StopSignals":
        if threading.current_thread() is threading.main_thread():
            for s in self.SIGNALS:
                self._old[s] = signal.signal(s, self._handle)
        return self

    def __exit__(self, *exc) -> None:
        for s, old in self._old.items():
            signal.signal(s, old)
        self._old.clear()

    def _handle(self, signum, frame) -> None:
        if self.signum is None:          # only set a flag; never raise in the middle of a step
            self.signum = signum

    @property
    def requested(self) -> bool:
        return self.signum is not None

    @property
    def name(self) -> Optional[str]:
        return signal.Signals(self.signum).name if self.signum is not None else None


def save_dir_from_args(a: argparse.Namespace) -> Optional[str]:
    return None if a.no_save else (a.save_dir or str(savestate.default_save_dir()))


class Session:
    """Adapter + brains + arbiter + save manager for one run (see module docstring)."""

    def __init__(self, adapter_name: str = "mock", brains: str = "rule,random", mode: str = "auto",
                 seed: int = 0, battle_confidence: Optional[float] = None, out_dir: str = "runs",
                 save_dir: Optional[str] = None, save_every: int = savestate.DEFAULT_SAVE_EVERY,
                 resume: Optional[str] = None, quiet: bool = False,
                 keep_periodic: int = savestate.DEFAULT_KEEP_PERIODIC):
        self.quiet = quiet
        self.seed = seed
        self.battle_confidence = battle_confidence
        self.save_dir, self.save_every = save_dir, save_every
        adapter_name = resolve_adapter(adapter_name)
        self.side: Optional[Dict[str, Any]] = None
        kw: Dict[str, Any] = {}
        if resume:
            self.side = savestate.resolve_resume(resume, save_dir or savestate.default_save_dir())
            if self.side.get("_sav_path") and adapter_name in MGBA_NAMES:
                kw["battery"] = open(self.side["_sav_path"], "rb").read()
        self.adapter = make_adapter(adapter_name, **kw)
        self.brains = make_brains(brains, seed=seed, battle_confidence=battle_confidence)
        self.arbiter = Arbiter(self.brains, mode=mode)
        self.arbiter.reset()
        self.start_step = 0
        self.resumed_from: Optional[Dict[str, Any]] = None
        side = self.side
        if side is not None:
            if side.get("adapter") != self.adapter.name:
                raise ValueError(f"save is for adapter {side.get('adapter')!r}, not {self.adapter.name!r}")
            rom = getattr(self.adapter, "rom_sha1", None)
            if side.get("rom_sha1") and rom and side["rom_sha1"] != rom:
                raise ValueError(f"save was made with ROM sha1 {side['rom_sha1']}, this ROM is {rom}")
            for b in self.brains:          # milestone progress from the sidecar (PathBrain also recomputes)
                if hasattr(b, "planner"):
                    b.planner.restore(side.get("milestones_done"))
            self.start_step = int(side["step"])
            self.resumed_from = {"sidecar": side["_path"], "state": side["_state_path"],
                                 "state_sha1": side["state_sha1"], "step": self.start_step, "frame": side["frame"],
                                 "adapter_state": side.get("adapter_state"), "sav": side.get("_sav_path"),
                                 "milestone": side.get("milestone")}
        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.run_dir = Path(out_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "run.jsonl"
        self.saver: Optional[savestate.SaveManager] = None
        if save_dir:
            self.saver = savestate.SaveManager(save_dir, self.adapter, [b.name for b in self.brains], self.run_id,
                                               every=save_every, resumed_from=side["_path"] if side else None,
                                               keep_periodic=keep_periodic)
            if not self.saver.enabled:
                print(f"warning: adapter {self.adapter.name} has no save states; --save-dir ignored",
                      file=sys.stderr)
        self.saves: List[str] = []

    @classmethod
    def from_args(cls, a: argparse.Namespace, quiet: Optional[bool] = None) -> "Session":
        return cls(a.adapter, a.brains, a.mode, a.seed, a.battle_confidence, a.out, save_dir_from_args(a),
                   a.save_every, a.resume, quiet=a.quiet if quiet is None else quiet,
                   keep_periodic=a.keep_periodic)

    # ------------------------------------------------------------------ run-loop hooks
    def header_info(self, **extra: Any) -> Dict[str, Any]:
        info = {"adapter": self.adapter.name, "brains": [b.name for b in self.brains],
                "mode": self.arbiter.mode.value, **extra}
        if self.resumed_from:
            info["resumed_from"] = self.resumed_from
        return info

    def start(self, log):
        """reset() the adapter (and load the resume state); returns the first observation."""
        obs = self.adapter.reset()
        side = self.side
        if side is not None:
            obs = self.adapter.load_state(savestate.read_state(side), frame=side["frame"],
                                          adapter_state=side.get("adapter_state"))
            log.event("resumed", **self.resumed_from)
            if not self.quiet:
                print(f"resumed from {side['_path']} (step {self.start_step}, frame {side['frame']}, "
                      f"map {side.get('map_bank')}/{side.get('map_id')} at ({side.get('x')}, {side.get('y')}))")
        return obs

    @property
    def saving(self) -> bool:
        return bool(self.saver and self.saver.enabled)

    def after_step(self, log, steps_done: int, result) -> None:
        """Call after log.step(): milestone / periodic saves (logged as ``save`` events)."""
        if not self.saving:
            return
        n_pruned = len(self.saver.pruned)
        for sv in self.saver.after_step(steps_done, result.decision.milestones):
            self.saves.append(sv["_path"])
            log.event("save", step=steps_done, frame=sv["frame"], reason=sv["reason"], path=sv["_path"])
        for p in self.saver.pruned[n_pruned:]:          # --keep-periodic retention
            log.event("save_pruned", step=steps_done, path=p)

    def finish(self, log, steps_done: int, result) -> None:
        """End of run: the ``final`` save."""
        if not self.saving:
            return
        sv = self.saver.save(steps_done, result.decision.milestones if result is not None else None, reason="final")
        self.saves.append(sv["_path"])
        log.event("save", step=steps_done, frame=sv["frame"], reason="final", path=sv["_path"])

    def config(self) -> Dict[str, Any]:
        """What was built (tests compare the CLI and the dashboard with this)."""
        out = {"adapter": self.adapter.name, "adapter_class": type(self.adapter).__name__,
               "brains": [(b.name, type(b).__name__) for b in self.brains], "mode": self.arbiter.mode.value,
               "seed": self.seed, "battle_confidence": None, "milestones": None,
               "save_dir": str(self.saver.root) if self.saver else None,
               "save_every": self.saver.every if self.saver else None, "saving": self.saving,
               "keep_periodic": self.saver.keep_periodic if self.saver else None,
               "resumed_from": self.resumed_from["sidecar"] if self.resumed_from else None}
        for b in self.brains:
            if hasattr(b, "confidence_threshold"):
                out["battle_confidence"] = b.confidence_threshold
            if hasattr(b, "planner"):
                out["milestones"] = [m.id for m in b.planner.milestones]
        return out
