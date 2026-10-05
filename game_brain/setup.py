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
import random
import secrets
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import savestate
from .adapters import make_adapter
from .arbiter import Arbiter
from .brain import make_brains
from .brain.goals import FR_STARTER, FR_STARTER_BALLS, GoalPlanner, firered_milestones
from .memory import ExperienceMemory, default_memory_dir
from .schema import Mode

#: brains that get the full FireRed stack (battle in battle, A* + milestones outside, A-mash fallback)
FULL_BRAINS = "battle,path,rule"
MGBA_NAMES = ("mgba", "gba_mgba", "firered")


#: --starter choices; the ball positions are in brain/goals.py (FR_STARTER_BALLS)
STARTERS = tuple(sorted(k.lower() for k in FR_STARTER_BALLS))      # bulbasaur, charmander, squirtle
#: --starter default (YIN): random, chosen from --seed (seed 0 -> bulbasaur, as before --starter)
DEFAULT_STARTER = "random"


def default_starter() -> str:
    """``--starter`` default: $GAME_BRAIN_STARTER (e.g. set by compose.yaml) or ``random``."""
    return (os.environ.get("GAME_BRAIN_STARTER") or DEFAULT_STARTER).strip().lower()


def resolve_starter(spec: str, seed: int) -> str:
    """``random`` -> one of :data:`STARTERS`, chosen from ``seed`` only (a string-seeded
    ``random.Random``, stable across Python versions and processes); a name -> itself."""
    spec = (spec or DEFAULT_STARTER).strip().lower()
    if spec == "random":
        return random.Random(f"game-brain-starter:{int(seed)}").choice(STARTERS)
    if spec not in STARTERS:
        raise ValueError(f"unknown starter {spec!r} (choose from {', '.join(STARTERS)} or random)")
    return spec


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
    ap.add_argument("--imitation-model", default=None, metavar="PATH",
                    help="offline behavior-cloning JSON model (train with python -m game_brain.learning)")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed (random brain, --starter random). Default: with --starter random a seed is "
                         "drawn at random and recorded (log header, status, saves); otherwise 0. "
                         "--resume reuses the save's seed unless --seed is given")
    ap.add_argument("--starter", default=None, type=str.lower, choices=list(STARTERS) + ["random"],
                    help="starter Pokemon to pick in Oak's lab: random (default, or $GAME_BRAIN_STARTER) = chosen "
                         "from --seed; or a fixed one. On --resume the save's recorded choice is used (no re-roll)")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--memory-dir", default=None, metavar="DIR",
                    help="persistent SQLite experience + exploration states (default ~/.game-brain/memory "
                         "or $GAME_BRAIN_MEMORY_DIR; must be outside the repo)")
    ap.add_argument("--no-memory", action="store_true", help="disable experience and exploration recording")
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


def new_run_id(now: Optional[float] = None) -> str:
    """Run id = UTC start time, ISO 8601 basic format with ``Z``: ``20261002T083408Z``. Same on the
    host and in the container (no local time zone), and sorting by name is chronological. Session
    appends a random suffix so simultaneous runs never overwrite logs or saves. Older
    runs used local time (``20261002-163408``); nothing parses run ids, so their saves / logs still
    resume and replay (they sort before every new id of the same UTC date or later)."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))


def claim_run_dir(out_dir: "str | Path", run_id: str) -> "tuple[str, Path]":
    """Create ``out_dir/<run_id>`` atomically; if it already exists (two runs started in the same
    second and drew the same suffix, or a copied runs/ tree) use ``<run_id>-2``, ``-3``, ...
    Never reuses an existing directory, so a run can't append to another run's log or saves."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        rid = run_id if n == 1 else f"{run_id}-{n}"
        try:
            (root / rid).mkdir()
            return rid, root / rid
        except FileExistsError:
            n += 1


class ForcedStop(KeyboardInterrupt):
    """Raised by :class:`StopSignals` on the *second* SIGTERM / SIGINT: stop now, mid-step if need
    be (e.g. a hung step). There is no final save (the game state may be mid-step); the last
    milestone / periodic save is the resume point."""

    def __init__(self, signum: int):
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


class StopSignals:
    """``with StopSignals() as stop:`` ... ``while ... and not stop.requested:`` (checked at the top
    of every iteration).

    SIGTERM and SIGINT (Ctrl-C) handlers only set a flag. The loop sees it before starting the next
    step, so the step in progress always completes (act + log + step counter) and the final save's
    step matches the game state: a resume never repeats or skips a step. A **second** signal raises
    :class:`ForcedStop` (a KeyboardInterrupt) at once, so a hung step can still be stopped; the
    entry points then exit with 128 + signal number and no final save. (Restoring the default
    handler instead would not work as PID 1 in a container, where SIG_DFL SIGTERM is ignored. Code
    stuck inside C, e.g. the emulator, only sees the exception when it returns to Python; SIGKILL is
    the last resort.) Without this, Python as PID 1 in the container ignores SIGTERM and ``docker compose down``
    SIGKILLs it after 10 s (no final save, no summary). Handlers are only installed from the main
    thread (signal module rule); the previous ones are restored on exit."""

    SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGINT") if hasattr(signal, n))

    def __init__(self):
        self.signum: Optional[int] = None
        self.forced: Optional[int] = None
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
        if self.signum is None:          # first signal: only set a flag; the step in progress completes
            self.signum = signum
            return
        self.forced = signum             # second signal: stop now
        raise ForcedStop(signum)

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
                 seed: Optional[int] = 0, battle_confidence: Optional[float] = None, out_dir: str = "runs",
                 save_dir: Optional[str] = None, save_every: int = savestate.DEFAULT_SAVE_EVERY,
                 resume: Optional[str] = None, quiet: bool = False,
                 keep_periodic: int = savestate.DEFAULT_KEEP_PERIODIC,
                 starter: Optional[str] = None, memory_dir: Optional[str] = None,
                 no_memory: bool = False, imitation_model: Optional[str] = None):
        self.quiet = quiet
        self.battle_confidence = battle_confidence
        self.save_dir, self.save_every = save_dir, save_every
        self.save_root = savestate.check_save_dir(save_dir or savestate.default_save_dir())
        self.brain_spec = brains
        self.imitation_model = imitation_model
        self.configured_starter = (starter or default_starter()).strip().lower()
        self.starter_requested = self.configured_starter
        self.seed_argument = seed
        self.memory_root = None if no_memory else savestate.check_save_dir(memory_dir or default_memory_dir())
        self.memory: Optional[ExperienceMemory] = None
        adapter_name = resolve_adapter(adapter_name)
        self.side: Optional[Dict[str, Any]] = None
        kw: Dict[str, Any] = {}
        if resume:
            self.side = savestate.resolve_resume(resume, save_dir or savestate.default_save_dir())
            if self.side.get("_sav_path") and adapter_name in MGBA_NAMES:
                kw["battery"] = open(self.side["_sav_path"], "rb").read()
        self.adapter = make_adapter(adapter_name, **kw)
        # the seed. ``seed=None`` (CLI / dashboard without --seed): on resume the save's recorded seed;
        # with --starter random a fresh one (secrets.randbits(32)), recorded everywhere so the run
        # (random brain, starter) can be reproduced; otherwise 0. An explicit seed is used as is.
        requested = self.configured_starter
        rec = None
        if self.side is not None:
            rec = self.side.get("starter")
            if not isinstance(rec, dict):   # saves from before --starter: bulbasaur was the only choice
                got = "get_starter" in (self.side.get("milestones_done") or ())
                rec = {"requested": "bulbasaur", "picked": "bulbasaur" if got else None,
                       "seed": self.side.get("seed", 0)}
        if seed is not None:
            self.seed, self.seed_source = int(seed), "flag"
        elif self.side is not None:
            self.seed = int(self.side.get("seed", rec.get("seed") if rec.get("seed") is not None else 0))
            self.seed_source = "resume"
        elif requested == "random":
            self.seed, self.seed_source = secrets.randbits(32), "auto"
        else:
            self.seed, self.seed_source = 0, "default"
        seed = self.seed
        # the starter. ``starter_info`` = {"requested", "picked", "seed"} (log header / status / summary /
        # every sidecar); "picked" stays None until the ball is taken in Oak's lab (get_starter done).
        # On resume the save's record is used (its own seed too): never a re-roll.
        if rec is not None:
            rseed = rec.get("seed") if rec.get("seed") is not None else seed
            self.starter_info = {"requested": rec.get("requested"), "picked": rec.get("picked"), "seed": rseed}
            self.starter = rec.get("picked") or resolve_starter(rec.get("requested"), rseed)
            self.starter_source = "resume"
            if starter is not None and starter.lower() not in ("random", self.starter):
                print(f"warning: --starter {starter} ignored: the save's starter is {self.starter} "
                      f"(requested {self.starter_info['requested']}, seed {self.starter_info['seed']}); "
                      "--resume keeps it", file=sys.stderr)
        else:
            self.starter = resolve_starter(requested, seed)
            self.starter_info = {"requested": requested, "picked": None, "seed": seed}
            self.starter_source = "random" if requested == "random" else "flag"
        namespace = f"{self.adapter.name}:{getattr(self.adapter, 'rom_sha1', None) or 'synthetic'}"
        self.brains = make_brains(brains, seed=seed, battle_confidence=battle_confidence, starter=self.starter,
                                  imitation_model=imitation_model, namespace=namespace)
        self.arbiter = Arbiter(self.brains, mode=mode)
        self.sidecar_extra = {"starter": self.starter_info, "seed": self.seed}   # live: copied at each save
        self.arbiter.reset()
        self.start_step = 0
        self.resumed_from: Optional[Dict[str, Any]] = None
        side = self.side
        self.active_save_id = None
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
            side_path = Path(side["_path"]).resolve()
            try:
                self.active_save_id = side_path.relative_to(self.save_root).as_posix()
            except ValueError:
                pass
        self.run_id, self.run_dir = claim_run_dir(out_dir, new_run_id() + "-" + uuid.uuid4().hex[:12])
        self.log_path = self.run_dir / "run.jsonl"
        #: "running" -> "free_explore" once every main (non-placeholder) milestone is done; the
        #: dashboard sets "stopped" in its final status. Nothing auto-stops on milestones.
        self.phase = "running"
        self.current_step = self.start_step  # steps completed so far (the dashboard loop updates it)
        self.saver: Optional[savestate.SaveManager] = None
        if save_dir:
            self.saver = savestate.SaveManager(save_dir, self.adapter, [b.name for b in self.brains], self.run_id,
                                               every=save_every, resumed_from=side["_path"] if side else None,
                                               keep_periodic=keep_periodic, extra=self.sidecar_extra)
            if not self.saver.enabled:
                print(f"warning: adapter {self.adapter.name} has no save states; --save-dir ignored",
                      file=sys.stderr)
        self.saves: List[str] = []
        self._available_game_saves = None

    @classmethod
    def from_args(cls, a: argparse.Namespace, quiet: Optional[bool] = None) -> "Session":
        return cls(a.adapter, a.brains, a.mode, a.seed, a.battle_confidence, a.out, save_dir_from_args(a),
                   a.save_every, a.resume, quiet=a.quiet if quiet is None else quiet,
                   keep_periodic=a.keep_periodic, starter=a.starter,
                   memory_dir=a.memory_dir, no_memory=a.no_memory, imitation_model=a.imitation_model)

    # ------------------------------------------------------------------ run-loop hooks
    def header_info(self, **extra: Any) -> Dict[str, Any]:
        info = {"adapter": self.adapter.name, "brains": [b.name for b in self.brains],
                "mode": self.arbiter.mode.value, "starter": dict(self.starter_info), **extra,
                "seed": self.seed, "seed_source": self.seed_source, "run_id": self.run_id,
                "rom_hash": getattr(self.adapter, "rom_sha1", None),
                "memory_dir": str(self.memory_root) if self.memory_root else None}
        if self.resumed_from:
            info["resumed_from"] = self.resumed_from
        return info

    def start(self, log):
        """reset() the adapter (and load the resume state); returns the first observation."""
        obs = self.adapter.reset()
        log.event("starter", **self.starter_info, plan=self.starter, source=self.starter_source,
                  seed_source=self.seed_source)
        side = self.side
        if side is not None:
            obs = self.adapter.load_state(savestate.read_state(side), frame=side["frame"],
                                          adapter_state=side.get("adapter_state"))
            log.event("resumed", **self.resumed_from)
            self.update_phase(log, self.start_step, reason="resumed")
            if not self.quiet:
                print(f"resumed from {side['_path']} (step {self.start_step}, frame {side['frame']}, "
                      f"map {side.get('map_bank')}/{side.get('map_id')} at ({side.get('x')}, {side.get('y')}))")
        if self.memory_root is not None:
            planner = next((GoalPlanner(b.planner.milestones) for b in self.brains if hasattr(b, "planner")), None)
            if planner is None and self.adapter.name in ("gba_mgba/firered", "mock-house"):
                planner = GoalPlanner(firered_milestones(self.starter))
            if planner and side:
                planner.restore(side.get("milestones_done"))
            self.memory = ExperienceMemory(self.memory_root, self.adapter, self.run_id, savestate.git_commit(),
                                           [b.name for b in self.brains], self.log_path, planner, side,
                                           save_cells=self.saving)
            self.memory.cell_saver.extra.update(self.sidecar_extra)
            self.memory.start(obs, self.start_step)
            self.sidecar_extra["memory"] = self.memory.cursor(self.start_step)
            log.event("episode_start", **self.sidecar_extra["memory"])
        return obs

    def record_step(self, log, step: int, obs, result, advanced: int) -> None:
        """Observe immediately after act(), including the run's final action."""
        after = self.adapter.observe()
        previous_episode = self.memory.episode_id if self.memory else None
        experience = self.memory.record(step, obs, result, after, advanced) if self.memory else None
        if self.memory and self.memory.episode_id != previous_episode:
            log.event("episode_start", **self.memory.cursor(step))
        log.step(step, obs, result, advanced, observation_after=after, experience=experience)
        if self.memory:
            self.sidecar_extra["memory"] = self.memory.cursor(step + 1)

    @property
    def saving(self) -> bool:
        return bool(self.saver and self.saver.enabled)

    def note_starter(self, log, steps_done: int, result) -> None:
        """``starter_info["picked"]`` is set once the get_starter milestone is done (party >= 1)."""
        if self.starter_info["picked"] is None and result is not None and any(
                m.get("id") == "get_starter" and m.get("done") for m in result.decision.milestones or ()):
            self.starter_info["picked"] = self.starter
            log.event("starter_picked", step=steps_done, **self.starter_info)

    def after_step(self, log, steps_done: int, result) -> None:
        """Call after log.step(): notes the starter pick, then milestone / periodic saves (``save``
        events); a save at the get_starter milestone already records the pick."""
        self.note_starter(log, steps_done, result)
        self.update_phase(log, steps_done)
        if not self.saving:
            return
        n_pruned = len(self.saver.pruned)
        for sv in self.saver.after_step(steps_done, result.decision.milestones):
            self.saves.append(sv["_path"])
            self._available_game_saves = None
            log.event("save", step=steps_done, frame=sv["frame"], reason=sv["reason"], path=sv["_path"])
        for p in self.saver.pruned[n_pruned:]:          # --keep-periodic retention
            log.event("save_pruned", step=steps_done, path=p)
            if p in self.saves:                         # the summary lists only saves that still exist
                self.saves.remove(p)

    def save_game(self, steps_done: int, milestones: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Write an on-demand resumable game snapshot."""
        if not self.saving:
            raise RuntimeError("game save states are not enabled for this run")
        reason = "manual"
        suffix = 1
        while (self.saver.dir / f"{steps_done:07d}_{reason}.json").exists():
            suffix += 1
            reason = f"manual-{suffix}"
        saved = self.saver.save(steps_done, milestones, reason=reason)
        self.saves.append(saved["_path"])
        self._available_game_saves = None
        return saved

    def _load_game_state(self, side: Dict[str, Any], state: bytes, battery: Optional[bytes],
                         source_name: str, log, step: int, save_id: Optional[str] = None) -> None:
        if not self.adapter.supports_save_state:
            raise RuntimeError(f"adapter {self.adapter.name} does not support save states")
        if side["adapter"] != self.adapter.name:
            raise ValueError(f"save is for adapter {side['adapter']!r}, not {self.adapter.name!r}")
        rom = getattr(self.adapter, "rom_sha1", None)
        if side.get("rom_sha1") and rom and side["rom_sha1"] != rom:
            raise ValueError("save was made with a different ROM")
        if battery is not None and not self.adapter.supports_battery_save:
            raise RuntimeError(f"adapter {self.adapter.name} cannot load a battery save")
        if self.adapter.supports_battery_save:
            self.adapter.load_battery_save(battery)
        obs = self.adapter.load_state(state, frame=side["frame"],
                                      adapter_state=side.get("adapter_state"))
        mode = self.arbiter.mode
        recorded_seed = side.get("seed")
        if isinstance(recorded_seed, int) and not isinstance(recorded_seed, bool):
            self.seed = recorded_seed
        recorded_starter = side.get("starter")
        if not isinstance(recorded_starter, dict):
            got_starter = "get_starter" in (side.get("milestones_done") or ())
            recorded_starter = {"requested": "bulbasaur",
                                "picked": "bulbasaur" if got_starter else None,
                                "seed": side.get("seed", 0)}
        starter_seed = recorded_starter.get("seed", self.seed)
        if not isinstance(starter_seed, int) or isinstance(starter_seed, bool):
            starter_seed = self.seed
        picked = recorded_starter.get("picked")
        requested = recorded_starter.get("requested", self.configured_starter)
        if picked not in STARTERS:
            picked = resolve_starter(requested if requested in STARTERS + ("random",) else "random",
                                     starter_seed)
        self.starter = picked
        self.starter_info = {"requested": requested, "picked": recorded_starter.get("picked"),
                             "seed": starter_seed}
        self._replace_brains(self.seed, self.starter, mode)
        self.active_save_id = save_id
        for brain in self.brains:
            if hasattr(brain, "planner"):
                brain.planner.restore(side.get("milestones_done"))
        self.sidecar_extra.update(starter=self.starter_info, seed=self.seed)
        if self.memory:
            end = self.memory.finish("local_save_loaded")
            log.event("episode_end", **end)
            self.memory.start(obs, step)
            self.sidecar_extra["memory"] = self.memory.cursor(step)
            log.event("episode_start", **self.sidecar_extra["memory"])
        log.event("local_save_loaded", step=step, save_step=side["step"], frame=side["frame"],
                  source=source_name, milestones_done=side.get("milestones_done", []))
        self.update_phase(log, step, reason="save_loaded")

    def _replace_brains(self, seed: int, starter: str, mode) -> None:
        namespace = f"{self.adapter.name}:{getattr(self.adapter, 'rom_sha1', None) or 'synthetic'}"
        self.brains = make_brains(self.brain_spec, seed=seed, battle_confidence=self.battle_confidence,
                                  starter=starter, imitation_model=self.imitation_model, namespace=namespace)
        self.arbiter = Arbiter(self.brains, mode=mode)

    def load_imported_save(self, command, log, step: int) -> None:
        """Load a browser-imported sidecar and emulator state into the active run."""
        self._load_game_state(command.sidecar, command.state, command.battery, command.source_name, log, step)

    def load_saved_game(self, save_id: str, log, step: int) -> None:
        """Load a game save selected from the configured local save directory."""
        parts = save_id.split("/")
        if (len(parts) != 2 or any(not part or part in (".", "..") for part in parts)
                or any("\\" in part for part in parts)):
            raise ValueError("invalid game save id")
        path = self.save_root.joinpath(*parts).resolve()
        try:
            path.relative_to(self.save_root)
        except ValueError as exc:
            raise ValueError("game save id escapes the configured save directory") from exc
        side = savestate.load_sidecar(path)
        if not savestate._inside(Path(side["_state_path"]), path.parent):
            raise ValueError("save state must be stored next to its sidecar")
        state = savestate.read_state(side)
        battery = None
        if side.get("_sav_path"):
            if not savestate._inside(Path(side["_sav_path"]), path.parent):
                raise ValueError("battery save must be stored next to its sidecar")
            battery = Path(side["_sav_path"]).read_bytes()
            if savestate.sha1(battery) != side.get("sav_sha1"):
                raise ValueError("battery save SHA1 does not match its sidecar")
        self._load_game_state(side, state, battery, path.name, log, step, save_id)

    def new_game(self, log, step: int) -> None:
        """Start a fresh game on the current ROM without stopping the live Dashboard."""
        if self.adapter.supports_battery_save:
            self.adapter.load_battery_save(None)
        obs = self.adapter.reset()
        mode = self.arbiter.mode
        if self.seed_argument is not None:
            seed = int(self.seed_argument)
        elif self.configured_starter == "random":
            seed = secrets.randbits(32)
        else:
            seed = 0
        starter = resolve_starter(self.configured_starter, seed)
        self.seed, self.seed_source = seed, "new_game"
        self.starter = starter
        self.starter_info = {"requested": self.configured_starter, "picked": None, "seed": seed}
        self.starter_source = "new_game"
        self._replace_brains(seed, starter, mode)
        self.side = None
        self.resumed_from = None
        self.active_save_id = None
        if self.saver:
            self.saver.resumed_from = None
            self.saver.extra.update(starter=self.starter_info, seed=self.seed)
        self.sidecar_extra.update(starter=self.starter_info, seed=self.seed)
        if self.memory:
            end = self.memory.finish("new_game")
            log.event("episode_end", **end)
            self.memory.start(obs, step)
            self.sidecar_extra["memory"] = self.memory.cursor(step)
            log.event("episode_start", **self.sidecar_extra["memory"])
        log.event("new_game", step=step, frame=obs.frame, starter=self.starter,
                  seed=seed, cleared_battery=bool(self.adapter.supports_battery_save))
        self.update_phase(log, step, reason="new_game")

    def main_milestones_done(self) -> bool:
        """Every non-placeholder milestone of the (first) planner is done, i.e. the scripted route
        is over (FireRed: goal 16 deliver_parcel; goal 17 pewter_city is a placeholder = free explore).
        False without a planner (no path brain / mock adapters without milestones)."""
        planner = next((b.planner for b in self.brains if hasattr(b, "planner")), None)
        if planner is None:
            return False
        main = [m.id for m in planner.milestones if not m.placeholder]
        done = {m["id"] for m in planner.summary() if m["done"]}
        return bool(main) and all(mid in done for mid in main)

    def update_phase(self, log, step: int, reason: str = "milestones") -> None:
        """Log a ``phase`` event when ``running`` <-> ``free_explore`` changes (back to running only
        on new game / loading an earlier save). The run keeps going either way."""
        phase = "free_explore" if self.main_milestones_done() else "running"
        if phase != self.phase:
            self.phase = phase
            log.event("phase", phase=phase, step=step, reason=reason)

    def finish(self, log, steps_done: int, result, reason: str = "run_end") -> None:
        """End of run: the ``final`` save."""
        if self.memory:
            end = self.memory.finish(reason)
            log.event("episode_end", **end)
        if not self.saving:
            return
        sv = self.saver.save(steps_done, result.decision.milestones if result is not None else None, reason="final")
        self.saves.append(sv["_path"])
        self._available_game_saves = None
        log.event("save", step=steps_done, frame=sv["frame"], reason="final", path=sv["_path"])

    def dashboard_status(self) -> Dict[str, Any]:
        if self._available_game_saves is None:
            self._available_game_saves = savestate.list_game_saves(self.save_root)
        available_saves = self._available_game_saves[:20]
        if self.active_save_id and all(item["save_id"] != self.active_save_id for item in available_saves):
            active = next((item for item in self._available_game_saves
                           if item["save_id"] == self.active_save_id), None)
            if active is not None:
                available_saves.append(active)
        saves = []
        if self.saver:
            existing = set(self.saves)
            for sv in reversed(self.saver.saved):
                if sv.get("_path") in existing:
                    path = Path(sv["_path"])
                    saves.append({"name": path.name, "reason": sv.get("reason"), "step": sv.get("step"),
                                  "frame": sv.get("frame"), "path": str(path),
                                  "battery_save": bool(sv.get("sav_file"))})
                if len(saves) == 8:
                    break
        return {
            "run_id": self.run_id, "adapter": self.adapter.name,
            "save_state_supported": bool(self.adapter.supports_save_state),
            "rom_hash": getattr(self.adapter, "rom_sha1", None),
            "log": str(self.log_path),
            "saving": self.saving, "save_every": self.saver.every if self.saver else None,
            "memory_dir": str(self.memory.root) if self.memory else None,
            "memory": self.memory.dashboard_status() if self.memory else {"enabled": False},
            "game_saves": list(reversed(saves)),
            "save_dir": str(self.save_root),
            "active_save_id": self.active_save_id,
            "available_game_saves": available_saves,
        }

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.memory:
                self.memory.close()
        finally:
            self.adapter.close()

    def config(self) -> Dict[str, Any]:
        """What was built (tests compare the CLI and the dashboard with this)."""
        out = {"adapter": self.adapter.name, "adapter_class": type(self.adapter).__name__,
               "brains": [(b.name, type(b).__name__) for b in self.brains], "mode": self.arbiter.mode.value,
               "seed": self.seed, "battle_confidence": None, "milestones": None, "starter": dict(self.starter_info),
               "starter_plan": self.starter,
               "memory_dir": str(self.memory_root) if self.memory_root else None,
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
