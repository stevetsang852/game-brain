"""Tiny map + battle learner. No ROM. This is the ML smoke test.

Map: 5x5 room, goal is the north door. Battle: press A until the opponent HP falls.
A tabular policy is trained with REINFORCE. Run:

    python -m game_brain.rl.learn_demo
"""

from __future__ import annotations

import argparse
import random
from typing import Dict, List, Tuple

BUTTONS = ("UP", "DOWN", "LEFT", "RIGHT", "A")


class MapBattle:
    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self.reset()

    def reset(self) -> Tuple[int, int, int, int]:
        self.x, self.y = 2, 4
        self.battle = 0
        self.hp = 3
        return self.state()

    def state(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.battle, self.hp)

    def step(self, button: str) -> Tuple[Tuple[int, int, int, int], float, bool]:
        reward, done = -0.01, False
        if self.battle:
            if button == "A":
                self.hp -= 1
                reward += 1.0
            if self.hp <= 0:
                self.battle = 0
                self.hp = 0
                reward += 5.0
            return self.state(), reward, done
        if button == "UP" and self.y > 0:
            self.y -= 1
        elif button == "DOWN" and self.y < 4:
            self.y += 1
        elif button == "LEFT" and self.x > 0:
            self.x -= 1
        elif button == "RIGHT" and self.x < 4:
            self.x += 1
        if (self.x, self.y) == (2, 2) and self.rng.random() < 0.35:
            self.battle, self.hp = 1, 3
        if (self.x, self.y) == (2, 0) and not self.battle:
            reward += 10.0
            done = True
        return self.state(), reward, done


def train(episodes: int = 200, seed: int = 0) -> Dict[str, float]:
    rng = random.Random(seed)
    prefs: Dict[Tuple, List[float]] = {}
    before = _success(prefs, seed)
    for ep in range(episodes):
        env = MapBattle(seed + ep)
        state = env.reset()
        logp: List[Tuple[Tuple, int]] = []
        total = 0.0
        for _ in range(40):
            weights = prefs.setdefault(state, [0.0] * len(BUTTONS))
            probs = _softmax(weights)
            index = _sample(probs, rng)
            logp.append((state, index))
            state, reward, done = env.step(BUTTONS[index])
            total += reward
            if done:
                break
        for state, index in logp:
            prefs[state][index] += 0.05 * total
    after = _success(prefs, seed + 1000)
    return {"before": before, "after": after, "states": float(len(prefs))}


def _softmax(weights: List[float]) -> List[float]:
    peak = max(weights)
    exps = [2.718281828 ** (w - peak) for w in weights]
    total = sum(exps) or 1.0
    return [x / total for x in exps]


def _sample(probs: List[float], rng: random.Random) -> int:
    pick = rng.random()
    acc = 0.0
    for i, prob in enumerate(probs):
        acc += prob
        if pick <= acc:
            return i
    return len(probs) - 1


def _success(prefs: Dict[Tuple, List[float]], seed: int) -> float:
    wins = 0
    for ep in range(20):
        env = MapBattle(seed + ep)
        state = env.reset()
        rng = random.Random(0)
        for _ in range(40):
            weights = prefs.get(state, [0.0] * len(BUTTONS))
            index = _sample(_softmax(weights), rng)
            state, _, done = env.step(BUTTONS[index])
            if done:
                wins += 1
                break
    return wins / 20


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Train the tiny map and battle policy")
    ap.add_argument("--episodes", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    result = train(args.episodes, args.seed)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
