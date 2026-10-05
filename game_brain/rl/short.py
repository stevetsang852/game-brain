"""Short-horizon run: boot until the starter, with anti-loop reward.

This is the gate before deep PPO. It does not replace PathBrain.
"""

from __future__ import annotations

import argparse
from typing import Dict

from ..adapters import make_adapter
from .curriculum import STAGES
from .env import BUTTONS, FireRedEnv

_DELTA = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def run_short(adapter, stage: str = "starter", max_steps: int = 400) -> Dict:
    spec = next(item for item in STAGES if item["id"] == stage)
    env = FireRedEnv(adapter, max_steps=min(max_steps, spec["horizon"]))
    obs = env.reset()
    seen = {}
    total = 0.0
    got_starter = False
    for step in range(1, env.max_steps + 1):
        button = _choose(obs, seen)
        obs, reward, done, truncated, info = env.step(button)
        total += reward
        pos = obs.position
        if pos is not None:
            seen[(obs.ram.get("map_bank"), obs.ram.get("map_id"), int(pos[0]), int(pos[1]))] = seen.get(
                (obs.ram.get("map_bank"), obs.ram.get("map_id"), int(pos[0]), int(pos[1])), 0) + 1
        if (obs.ram.get("party_count") or 0) >= 1:
            got_starter = True
        if got_starter or done or truncated:
            return {"stage": stage, "steps": step, "reward": total, "starter": got_starter,
                    "champion": done, "loop": info.get("loop", 0.0)}
    return {"stage": stage, "steps": env.max_steps, "reward": total, "starter": got_starter, "champion": False, "loop": 0.0}


def _choose(obs, seen) -> str:
    if obs.position is None or obs.ram.get("in_battle"):
        return "A"
    x, y = (int(obs.position[0]), int(obs.position[1]))
    collision = obs.ram.get("collision")
    best = None
    for name, (dx, dy) in _DELTA.items():
        nx, ny = x + dx, y + dy
        if _blocked(collision, nx, ny):
            continue
        key = (obs.ram.get("map_bank"), obs.ram.get("map_id"), nx, ny)
        score = seen.get(key, 0)
        if best is None or score < best[0]:
            best = (score, name)
    return best[1] if best else "A"


def _blocked(collision, x: int, y: int) -> bool:
    if not collision or y < 0 or y >= len(collision):
        return False
    row = collision[y]
    return x < 0 or x >= len(row) or row[x] in ("#", "X", 1, True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the starter short horizon")
    ap.add_argument("--adapter", default="mock-house")
    ap.add_argument("--stage", default="starter", choices=[item["id"] for item in STAGES])
    ap.add_argument("--steps", type=int, default=400)
    args = ap.parse_args(argv)
    result = run_short(make_adapter(args.adapter), args.stage, args.steps)
    print(result)
    return 0 if result["starter"] or args.stage != "starter" else 2


if __name__ == "__main__":
    raise SystemExit(main())
