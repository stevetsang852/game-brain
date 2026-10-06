"""Experimental global action-weighting baseline on discrete buttons.

Despite this module's historical name and CLI path, this is not PPO: it fits one
shared set of action logits from logged rewards, without a state-conditioned policy,
rollouts, value estimates, or a clipped objective. CPU only, no torch.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from ..memory import default_memory_dir, memory_database_path
from .progress import progress_delta

BUTTONS = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B")


def _softmax(logits: List[float]) -> List[float]:
    peak = max(logits)
    exps = [math.exp(x - peak) for x in logits]
    total = sum(exps) or 1.0
    return [x / total for x in exps]


def train_short_ppo(memory_dir: str | Path, namespace: str, epochs: int = 4, seed: int = 0) -> Dict[str, Any]:
    database = memory_database_path(memory_dir)
    if not database.is_file():
        raise FileNotFoundError(f"experience database not found: {database}")
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    rows = []
    try:
        for before, after, action in db.execute(
                "SELECT state_before,state_after,executed_action FROM transitions "
                "WHERE namespace=? AND executed_action IS NOT NULL ORDER BY run_id,step_id", (namespace,)):
            rows.append((json.loads(before), json.loads(after), json.loads(action)))
    finally:
        db.close()
    if not rows:
        raise ValueError(f"no transitions for namespace {namespace}")
    logits = {button: 0.0 for button in BUTTONS}
    rng = random.Random(seed)
    updates = 0
    for _ in range(epochs):
        rng.shuffle(rows)
        for before, after, action in rows:
            presses = action.get("presses") or []
            button = presses[0].get("button") if presses else "A"
            if button not in logits:
                continue
            repeated = (before.get("ram") or {}).get("player_x") == (after.get("ram") or {}).get("player_x") and \
                (before.get("ram") or {}).get("player_y") == (after.get("ram") or {}).get("player_y")
            reward = progress_delta(before, after, repeated)["reward"]
            probs = _softmax([logits[b] for b in BUTTONS])
            advantage = reward - 0.0
            index = BUTTONS.index(button)
            for i, name in enumerate(BUTTONS):
                grad = (1.0 if i == index else 0.0) - probs[i]
                logits[name] += 0.05 * advantage * grad
            updates += 1
    return {"algorithm": "global-action-reward-weighting-v1", "namespace": namespace, "updates": updates,
            "epochs": epochs, "logits": logits, "objective": ["clear", "pokedex"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Train the global action-weighting baseline (not PPO)")
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)
    result = train_short_ppo(args.memory_dir or default_memory_dir(), args.namespace, args.epochs)
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {args.output} updates={result['updates']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
