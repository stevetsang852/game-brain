"""Live loop: adapter -> arbiter -> adapter, mirrored to the dashboard.

    python -m game_brain.dashboard                       # real game if $GAME_BRAIN_ROM is set, else mock
    python -m game_brain.dashboard --adapter mgba --brains battle,path,rule --save-every 500
    python -m game_brain.dashboard --adapter mgba --resume latest
    # then open http://127.0.0.1:8765/

Adapter, brains (battle,path,rule + FireRed milestones), --battle-confidence and the save
settings (--save-dir / --save-every / --no-save / --resume) are the CLI's: both use
game_brain/setup.py.

Each step: drain dashboard commands (mode switches, manual presses), let the arbiter
decide, execute, write the JSONL run log, and push ``observation``, ``decision`` and
``status`` envelopes to every open tab.
"""

from __future__ import annotations

import argparse
import base64
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from .. import savestate
from ..arbiter import Arbiter
from ..runlog import RunLogWriter
from ..setup import FULL_BRAINS, ForcedStop, Session, StopSignals, add_run_args, save_dir_from_args
from ..schema import Action, ModeCommand, Mode, to_envelope
from .pacing import FrameAck, Pacer, ViewConfig
from .server import DashboardServer, LoadSaveCommand, PersistenceCommand
from .roms import use_remembered_rom


def _screenshot_b64(adapter, tmpdir: Path) -> Optional[str]:
    path = adapter.screenshot(str(tmpdir / "live.png"))
    if not path:
        return None
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def apply_commands(server: DashboardServer, arbiter: Arbiter, log=None, step: int = 0, frame: int = 0,
                   pacer: Optional[Pacer] = None, session=None, milestones=None):
    """Feed dashboard input into the arbiter. Returns a list of human-readable outcomes."""
    outcomes = []
    for msg in server.poll():
        if isinstance(msg, ModeCommand):
            msg.issued_by = "dashboard"
            arbiter.apply_mode(msg)
            outcomes.append(f"mode -> {arbiter.mode.value}")
            if log:
                log.event("mode_change", step=step, frame=frame, mode=arbiter.mode.value, issued_by="dashboard")
        elif isinstance(msg, ViewConfig):  # display only: never logged, never reaches the arbiter
            if pacer:
                outcomes.append(pacer.apply(msg))
        elif isinstance(msg, FrameAck):
            if pacer:
                pacer.ack(msg)
        elif isinstance(msg, LoadSaveCommand):
            try:
                if session is None:
                    raise RuntimeError("game save loading is unavailable")
                session.load_imported_save(msg, log, step)
            except (OSError, RuntimeError, ValueError) as exc:
                outcomes.append(f"error: 遊戲存檔載入失敗：{exc}")
                if log:
                    log.event("resume_error", step=step, frame=frame, source=msg.source_name, error=str(exc))
            else:
                outcomes.append(f"已載入本機存檔 {msg.source_name} · step {msg.sidecar['step']}")
        elif isinstance(msg, PersistenceCommand):
            if msg.kind == "save_game":
                try:
                    if session is None:
                        raise RuntimeError("game save is unavailable")
                    saved = session.save_game(step, milestones)
                except (OSError, ValueError, RuntimeError) as exc:
                    reason = f"error: 遊戲存檔失敗：{exc}"
                    outcomes.append(reason)
                    if log:
                        log.event("save_error", step=step, frame=frame, save_kind="game", error=str(exc))
                else:
                    outcomes.append(f"已保存遊戲 · step {step}")
                    if log:
                        log.event("save", step=step, frame=saved["frame"], reason=saved["reason"],
                                  path=saved["_path"])
            elif msg.kind == "save_learning":
                try:
                    if session is None:
                        raise RuntimeError("AI learning memory is unavailable")
                    if session.memory is None:
                        raise RuntimeError("AI 學習記憶未啟用")
                    session.memory.save()
                except (OSError, RuntimeError, sqlite3.Error) as exc:
                    reason = f"error: AI 學習資料保存失敗：{exc}"
                    outcomes.append(reason)
                    if log:
                        log.event("save_error", step=step, frame=frame, save_kind="learning", error=str(exc))
                else:
                    outcomes.append(f"已保存 AI 學習資料 · {session.memory.path}")
                    if log:
                        log.event("learning_save", step=step, frame=frame, path=str(session.memory.path))
        elif isinstance(msg, Action):
            ok = arbiter.submit_manual(msg, origin="dashboard")
            buttons = "+".join(p.button for p in msg.presses) or "(empty)"
            outcomes.append(f"manual {buttons} {'queued' if ok else 'REJECTED: ' + arbiter.rejected[-1]}")
    return outcomes


def run(server: DashboardServer, adapter_name: str = "mock", mode: str = "auto", brains: str = FULL_BRAINS,
        seed: Optional[int] = 0, steps: int = 0, step_delay: float = 0.25, screenshot_every: int = 1,
        out_dir: str = "runs", quiet: bool = False, battle_confidence: Optional[float] = None,
        save_dir: Optional[str] = None, save_every: int = savestate.DEFAULT_SAVE_EVERY,
        resume: Optional[str] = None, keep_periodic: int = savestate.DEFAULT_KEEP_PERIODIC,
        starter: Optional[str] = None, memory_dir: Optional[str] = None, no_memory: bool = False) -> dict:
    """Adapter / brains (battle, path + FireRed milestones, rule) / saves come from
    :class:`game_brain.setup.Session`, the same setup the CLI uses. ``save_dir`` None = no saves."""
    sess = Session(adapter_name, brains, mode, seed, battle_confidence, out_dir, save_dir, save_every, resume,
                   quiet=quiet, keep_periodic=keep_periodic, starter=starter,
                   memory_dir=memory_dir, no_memory=no_memory)
    adapter, arbiter = sess.adapter, sess.arbiter
    if not quiet:
        c = sess.config()
        print(f"setup: adapter={c['adapter']} brains={','.join(n for n, _ in c['brains'])} mode={c['mode']} "
              f"starter={c['starter']['requested']}->{c['starter_plan']} (seed {c['starter']['seed']}) "
              f"milestones={len(c['milestones'] or [])} saves={c['save_dir'] or 'off'}"
              + (f" every={c['save_every']}" if c['saving'] else "")
              + (f" resumed_from={c['resumed_from']}" if c['resumed_from'] else ""))
    tmp = Path(tempfile.mkdtemp(prefix="gb-dash-"))
    pacer = Pacer(step_delay, screenshot_every)
    step = sess.start_step
    result = None
    t0 = time.time()
    with sess, RunLogWriter(sess.log_path) as log, StopSignals() as stop:
        log.header(**sess.header_info(steps=steps, dashboard=server.url))
        obs = sess.start(log)
        # the stop flag (SIGTERM / Ctrl-C) is checked only here, after a step completed in full
        while (not steps or step < sess.start_step + steps) and not stop.requested:
            started = pacer.clock()
            outcomes = apply_commands(server, arbiter, log, step, obs.frame, pacer, sess,
                                      result.decision.milestones if result is not None else None)
            obs = adapter.observe()
            if pacer.want_screenshot(step, getattr(server, "client_count", 1) > 0):
                obs.screenshot_b64 = _screenshot_b64(adapter, tmp)
            result = arbiter.step(obs)
            advanced = adapter.act(result.executed) if result.executed else 0
            sess.record_step(log, step, obs, result, advanced)
            sess.after_step(log, step + 1, result)
            server.broadcast(to_envelope(obs, obs.frame))
            if obs.screenshot_b64:  # time the page from the moment the frame actually leaves
                pacer.sent_screenshot(obs.frame)
            server.broadcast(to_envelope(result.decision, obs.frame))
            server.broadcast({"type": "status", "frame": obs.frame, "ts": time.time(), "payload": {
                "step": step, "mode": arbiter.mode.value, "adapter": adapter.name,
                "proposed_action": result.proposed.to_dict() if result.proposed else None,
                "executed_action": result.executed.to_dict() if result.executed else None,
                "frames_advanced": advanced, "pending_manual": arbiter.pending_manual,
                "notes": list(result.notes) + outcomes, "display": pacer.status(),
                "starter": dict(sess.starter_info),
                "persistence": sess.dashboard_status(),
            }})
            if not quiet and outcomes:
                print(f"step {step} frame {obs.frame}: " + "; ".join(outcomes))
            step += 1
            pacer.stepped()
            delay = pacer.sleep_after(started)
            if delay:
                time.sleep(delay)
        if stop.name:
            log.event("stopped", signal=stop.name, step=step)
            # stderr, even with -q: say why the run ended early
            print(f"{stop.name}: stopping after step {step} (final save + summary)", file=sys.stderr)
        sess.finish(log, step, result, reason=stop.name or "step_limit")
        summary = {"steps": step - sess.start_step, "final_frame": adapter.frame, "mode_final": arbiter.mode.value,
                   "log": str(sess.log_path), "saves": sess.saves,
                   "resumed_from": sess.resumed_from["sidecar"] if sess.resumed_from else None,
                   "wall_seconds": round(time.time() - t0, 3), "stopped_by": stop.name,
                   "starter": dict(sess.starter_info), "seed": sess.seed,
                   "memory": str(sess.memory.path) if sess.memory else None}
        log.event("summary", **summary)
        server.broadcast({"type": "status", "frame": adapter.frame, "ts": time.time(), "payload": {
            "step": step, "mode": arbiter.mode.value, "adapter": adapter.name,
            "pending_manual": arbiter.pending_manual, "notes": [], "display": pacer.status(),
            "starter": dict(sess.starter_info), "persistence": sess.dashboard_status(),
            "finished": True,
        }})
    return summary


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m game_brain.dashboard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # same flags as the CLI (game_brain/setup.py); defaults = the Docker image: the real game, full brains
    add_run_args(ap, adapter_default="auto", brains_default=FULL_BRAINS)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="loopback only; 0.0.0.0 is accepted only inside the game-brain container")
    ap.add_argument("--steps", type=int, default=0, help="0 = run until Ctrl-C")
    ap.add_argument("--step-delay", type=float, default=0.25, help="seconds between steps (so humans can watch)")
    ap.add_argument("--screenshot-every", type=int, default=1, help="attach a live frame every N steps (0 = never)")
    ap.add_argument("-q", "--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        use_remembered_rom()
    except (ValueError, OSError) as exc:
        print(f"warning: {exc} (using configured adapter/ROM until a new file is selected)", file=sys.stderr)
    try:
        server_cm = DashboardServer(a.host, a.port)
    except OSError as exc:
        if getattr(exc, "errno", None) in (98, 48, 10048):
            print(f"error: {a.host}:{a.port} is already in use. The dashboard is probably still running.", file=sys.stderr)
            print(f"open http://127.0.0.1:{a.port}/ or stop the old process, then start again.", file=sys.stderr)
            print("WSL: ss -ltnp | grep 8765    then kill <pid>", file=sys.stderr)
            return 1
        raise
    with server_cm as server:
        print(f"dashboard: {server.url}  (Ctrl-C to stop)")
        try:
            s = run(server, a.adapter, a.mode, a.brains, a.seed, a.steps, a.step_delay,
                    a.screenshot_every, a.out, a.quiet, battle_confidence=a.battle_confidence,
                    save_dir=save_dir_from_args(a), save_every=a.save_every, resume=a.resume,
                    keep_periodic=a.keep_periodic, starter=a.starter,
                    memory_dir=a.memory_dir, no_memory=a.no_memory)
        except (ValueError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except ForcedStop as exc:
            print(f"{exc.args[0]} again: forced stop (no final save; resume from the last save)", file=sys.stderr)
            return 128 + exc.signum
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
