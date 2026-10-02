"""Live loop: adapter -> arbiter -> adapter, mirrored to the dashboard.

    python -m game_brain.dashboard --adapter mock --mode auto
    # then open http://127.0.0.1:8765/

Each step: drain dashboard commands (mode switches, manual presses), let the arbiter
decide, execute, write the JSONL run log, and push ``observation``, ``decision`` and
``status`` envelopes to every open tab.
"""

from __future__ import annotations

import argparse
import base64
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..adapters import make_adapter
from ..arbiter import Arbiter
from ..brain import make_brain
from ..runlog import RunLogWriter
from ..schema import Action, ModeCommand, Mode, to_envelope
from .pacing import FrameAck, Pacer, ViewConfig
from .server import DashboardServer


def _screenshot_b64(adapter, tmpdir: Path) -> Optional[str]:
    path = adapter.screenshot(str(tmpdir / "live.png"))
    if not path:
        return None
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def apply_commands(server: DashboardServer, arbiter: Arbiter, log=None, step: int = 0, frame: int = 0,
                   pacer: Optional[Pacer] = None):
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
        elif isinstance(msg, Action):
            ok = arbiter.submit_manual(msg, origin="dashboard")
            buttons = "+".join(p.button for p in msg.presses) or "(empty)"
            outcomes.append(f"manual {buttons} {'queued' if ok else 'REJECTED: ' + arbiter.rejected[-1]}")
    return outcomes


def run(server: DashboardServer, adapter_name: str = "mock", mode: str = "auto", brains: str = "rule,random",
        seed: int = 0, steps: int = 0, step_delay: float = 0.25, screenshot_every: int = 1,
        out_dir: str = "runs", quiet: bool = False) -> dict:
    adapter = make_adapter(adapter_name)
    brain_objs = [make_brain(n.strip(), seed=seed) if n.strip() == "random" else make_brain(n.strip())
                  for n in brains.split(",")]
    arbiter = Arbiter(brain_objs, mode=mode)
    arbiter.reset()
    run_dir = Path(out_dir) / time.strftime("%Y%m%d-%H%M%S")
    tmp = Path(tempfile.mkdtemp(prefix="gb-dash-"))
    pacer = Pacer(step_delay, screenshot_every)
    step = 0
    with RunLogWriter(run_dir / "run.jsonl") as log:
        log.header(adapter=adapter.name, brains=[b.name for b in brain_objs], mode=arbiter.mode.value,
                   steps=steps, seed=seed, dashboard=server.url)
        obs = adapter.reset()
        try:
            while not steps or step < steps:
                started = pacer.clock()
                outcomes = apply_commands(server, arbiter, log, step, obs.frame, pacer)
                obs = adapter.observe()
                if pacer.want_screenshot(step, getattr(server, "client_count", 1) > 0):
                    obs.screenshot_b64 = _screenshot_b64(adapter, tmp)
                result = arbiter.step(obs)
                advanced = adapter.act(result.executed) if result.executed else 0
                log.step(step, obs, result, advanced)
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
                }})
                if not quiet and outcomes:
                    print(f"step {step} frame {obs.frame}: " + "; ".join(outcomes))
                step += 1
                pacer.stepped()
                delay = pacer.sleep_after(started)
                if delay:
                    time.sleep(delay)
        except KeyboardInterrupt:
            pass
    adapter.close()
    return {"steps": step, "final_frame": adapter.frame, "mode_final": arbiter.mode.value,
            "log": str(run_dir / "run.jsonl")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m game_brain.dashboard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="mock")
    ap.add_argument("--mode", default="auto", choices=[m.value for m in Mode])
    ap.add_argument("--brains", default="rule,random")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="loopback only; 0.0.0.0 is accepted only inside the game-brain container")
    ap.add_argument("--steps", type=int, default=0, help="0 = run until Ctrl-C")
    ap.add_argument("--step-delay", type=float, default=0.25, help="seconds between steps (so humans can watch)")
    ap.add_argument("--screenshot-every", type=int, default=1, help="attach a live frame every N steps (0 = never)")
    ap.add_argument("--out", default="runs")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    with DashboardServer(a.host, a.port) as server:
        print(f"dashboard: {server.url}  (Ctrl-C to stop)")
        s = run(server, a.adapter, a.mode, a.brains, a.seed, a.steps, a.step_delay,
                a.screenshot_every, a.out, a.quiet)
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
