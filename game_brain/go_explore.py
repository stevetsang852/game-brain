"""Persistent Go-Explore-style autonomous exploration for save-state-capable adapters.

Run with ``python -m game_brain.go_explore --adapter mgba --memory-dir ~/.game-brain/go-explore``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .adapters import make_adapter
from .brain import BrainUnavailable, RuleBattleBrain
from .brain.goals import GoalPlanner, firered_milestones
from .savestate import SaveManager, check_save_dir, load_sidecar, read_state
from .schema import Action, ButtonPress, Observation

ARCHIVE_VERSION = 1
_DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT")
_BUTTONS = (*_DIRECTIONS, "A", "B", "NONE")


def cell_key(obs: Observation, progress, grid: int = 2) -> Optional[str]:
    """Discretize an overworld observation; milestones only describe progress, never actions."""
    ram = obs.ram
    values = [ram.get(k) for k in ("map_bank", "map_id", "player_x", "player_y")]
    if (obs.in_battle is True or ram.get("scene") != "overworld"
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values)):
        return None
    bank, map_id, x, y = values
    return json.dumps([bank, map_id, x // grid, y // grid, ram.get("party_count"),
                       sorted(progress)], separators=(",", ":"))


def explorer_action(button: str) -> Action:
    """Create one short, deterministic FireRed-compatible exploratory action."""
    if button in _DIRECTIONS:
        press = ButtonPress(button, 8, 16)
    elif button in ("A", "B"):
        press = ButtonPress(button, 2, 14)
    else:
        press = ButtonPress("NONE", 16)
    return Action([press], source="brain:go-explore")


class GoExploreArchive:
    """ROM-scoped cell archive with a separately saved emulator state per discovered cell."""

    def __init__(self, root: "str | Path", adapter, grid: int = 2, seed: int = 0):
        if grid < 1:
            raise ValueError("cell grid must be positive")
        self.root = check_save_dir(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "archive.json"
        self.adapter = adapter
        self.grid = grid
        self.namespace = f"{adapter.name}:{getattr(adapter, 'rom_sha1', None) or 'synthetic'}"
        self.rng = random.Random(seed)
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot read Go-Explore archive {self.path}: {exc}") from exc
            if not isinstance(self.data, dict) or self.data.get("format_version") != ARCHIVE_VERSION:
                raise ValueError("unsupported Go-Explore archive format")
            if self.data.get("namespace") != self.namespace:
                raise ValueError("Go-Explore archive belongs to a different adapter/ROM "
                                 f"({self.data.get('namespace')!r} != {self.namespace!r})")
            if self.data.get("grid") != grid:
                raise ValueError("Go-Explore archive uses a different cell grid")
            if not isinstance(self.data.get("cells"), dict):
                raise ValueError("Go-Explore archive has an invalid cells index")
        else:
            self.data = {"format_version": ARCHIVE_VERSION, "namespace": self.namespace, "grid": grid,
                         "seed": seed, "steps": 0, "iterations": 0, "cells": {}, "boot_save": None,
                         "completed": False, "q_values": {}, "q_updates": 0}
        self.data.setdefault("q_values", {})
        self.data.setdefault("q_updates", 0)
        if not isinstance(self.data["q_values"], dict) or any(
                not isinstance(state, str) or not isinstance(values, dict)
                or any(action not in _BUTTONS or isinstance(value, bool)
                       or not isinstance(value, (int, float)) or not math.isfinite(value)
                       for action, value in values.items())
                for state, values in self.data["q_values"].items()):
            raise ValueError("Go-Explore archive has invalid Q-values")
        updates = self.data["q_updates"]
        if not isinstance(updates, int) or isinstance(updates, bool) or updates < 0:
            raise ValueError("Go-Explore archive has an invalid Q-update count")
        self.saver = SaveManager(self.root, adapter, ["go-explore"], "go-explore",
                                 every=0, on_milestone=False)

    def flush(self) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(self.data, sort_keys=True, separators=(",", ":")) + "\n",
                                 encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def best_action(self, state: Optional[str]) -> Optional[str]:
        """Return a seeded-random greedy action for a learned state, if one exists."""
        values = self.data["q_values"].get(state) if state is not None else None
        if not values:
            return None
        best = max(values.values())
        return self.rng.choice([action for action, value in values.items() if value == best])

    def learn(self, state: Optional[str], action: str, next_state: Optional[str],
              reward: float, terminal: bool, alpha: float = 0.2, gamma: float = 0.95) -> None:
        """Persist one tabular Q-learning update for an executed overworld action."""
        if state is None:
            return
        if action not in _BUTTONS or not math.isfinite(reward):
            raise ValueError("invalid Go-Explore learning transition")
        values = self.data["q_values"].setdefault(state, {})
        old_value = values.get(action, 0.0)
        future_values = self.data["q_values"].get(next_state, {}) if next_state else {}
        future_value = max(future_values.values(), default=0.0) if not terminal else 0.0
        values[action] = old_value + alpha * (reward + gamma * future_value - old_value)
        self.data["q_updates"] += 1

    def save_boot(self, obs: Observation, planner: GoalPlanner) -> Dict[str, Any]:
        sidecar = self.saver.save(self.data["steps"], planner.summary(), reason="go-explore-boot")
        self.data["boot_save"] = Path(sidecar["_path"]).relative_to(self.root).as_posix()
        self.flush()
        return sidecar

    def observe(self, obs: Observation, planner: GoalPlanner) -> Optional[str]:
        progress = [m["id"] for m in planner.summary() if m["done"]]
        key = cell_key(obs, progress, self.grid)
        if key is None:
            return None
        step = int(self.data["steps"])
        entry = self.data["cells"].get(key)
        if entry is None:
            sidecar = self.saver.save(step, planner.summary(), reason="go-explore-cell")
            entry = {"save": Path(sidecar["_path"]).relative_to(self.root).as_posix(),
                     "visits": 0, "times_chosen": 0, "chosen_since_new": 0,
                     "first_step": step, "last_seen": step, "progress": progress,
                     "map": [obs.ram["map_bank"], obs.ram["map_id"]]}
            self.data["cells"][key] = entry
            for other in self.data["cells"].values():
                other["chosen_since_new"] = 0
            self.flush()
        entry["visits"] = int(entry.get("visits", 0)) + 1
        entry["last_seen"] = step
        return key

    def select(self) -> Dict[str, Any]:
        """Sample from under-visited cells, with a multiplicative frontier bonus."""
        cells = self.data["cells"]
        if not cells:
            raise ValueError("the archive has no overworld cells yet")
        scores = [(len(entry.get("progress", [])), int(entry.get("last_seen", 0)))
                  for entry in cells.values()]
        best_progress = max(score[0] for score in scores)
        newest_map = max(int(entry.get("last_seen", 0)) for entry in cells.values())
        candidates = []
        for key, entry in cells.items():
            try:
                sidecar = self._cell_path(entry)
                if not sidecar.is_file():
                    continue
            except (KeyError, ValueError):
                continue
            weight = (1 / math.sqrt(1 + int(entry.get("times_chosen", 0)))
                      + 1 / math.sqrt(1 + int(entry.get("visits", 0)))
                      + 1 / math.sqrt(1 + int(entry.get("chosen_since_new", 0))))
            if len(entry.get("progress", [])) == best_progress or int(entry.get("last_seen", 0)) == newest_map:
                weight *= 2
            candidates.append((key, entry, weight))
        if not candidates:
            raise ValueError("the archive contains no resumable cell saves")
        choice = self.rng.random() * sum(item[2] for item in candidates)
        selected = candidates[-1]
        for item in candidates:
            choice -= item[2]
            if choice <= 0:
                selected = item
                break
        entry = selected[1]
        self.data["selected_cell"] = selected[0]
        entry["times_chosen"] = int(entry.get("times_chosen", 0)) + 1
        entry["chosen_since_new"] = int(entry.get("chosen_since_new", 0)) + 1
        self.flush()
        return load_sidecar(self._cell_path(entry))

    def _cell_path(self, entry: Dict[str, Any]) -> Path:
        relative = Path(entry["save"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("archive save path must be relative and stay within the archive")
        path = (self.root / relative).resolve()
        path.relative_to(self.root)
        return path

    def boot_sidecar(self) -> Dict[str, Any]:
        relative = self.data.get("boot_save")
        if not isinstance(relative, str):
            raise ValueError("archive is missing its boot save")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("archive boot save path is invalid")
        target = (self.root / path).resolve()
        target.relative_to(self.root)
        return load_sidecar(target)

    def load(self, sidecar: Dict[str, Any]) -> Observation:
        if sidecar.get("adapter") != self.adapter.name:
            raise ValueError("saved exploration state belongs to another adapter")
        rom_hash = getattr(self.adapter, "rom_sha1", None)
        if sidecar.get("rom_sha1") and sidecar["rom_sha1"] != rom_hash:
            raise ValueError("saved exploration state belongs to another ROM")
        self.adapter.reset()
        return self.adapter.load_state(read_state(sidecar), frame=int(sidecar["frame"]),
                                       adapter_state=sidecar.get("adapter_state"))


class GoExploreRunner:
    """Novelty-driven exploration with a persistent tabular Q-learning action policy."""

    def __init__(self, adapter, archive: GoExploreArchive, seed: int = 0, segment_steps: int = 100,
                 stuck_steps: int = 40, battle_policy: str = "rule",
                 stop_map: Optional[Tuple[int, int]] = None, epsilon: float = 0.15):
        if segment_steps < 1 or stuck_steps < 1:
            raise ValueError("segment and stuck limits must be positive")
        if battle_policy not in ("rule", "random"):
            raise ValueError("battle policy must be 'rule' or 'random'")
        if not math.isfinite(epsilon) or not 0 <= epsilon <= 1:
            raise ValueError("epsilon must be in [0, 1]")
        self.adapter, self.archive = adapter, archive
        self.rng = random.Random(seed)
        self.segment_steps, self.stuck_steps = segment_steps, stuck_steps
        self.battle_policy, self.stop_map, self.epsilon = battle_policy, stop_map, epsilon
        self.planner = GoalPlanner(firered_milestones())
        self.battle_brain = RuleBattleBrain()
        self.previous_button: Optional[str] = None
        self._last_cell: Optional[str] = None
        self._stuck = 0

    def _button(self, obs: Observation, state: Optional[str]) -> str:
        if obs.in_battle is True and self.battle_policy == "rule":
            try:
                action, _decision = self.battle_brain.decide(obs)
                if action.presses:
                    return action.presses[0].button
            except BrainUnavailable:
                pass
        learned_action = self.archive.best_action(state)
        if learned_action is not None and self.rng.random() >= self.epsilon:
            return learned_action
        if self.previous_button is not None and self.rng.random() < 0.9:
            return self.previous_button
        return self.rng.choices(
            list(_BUTTONS),
            weights=[0.175, 0.175, 0.175, 0.175, 0.2, 0.08, 0.02], k=1)[0]

    def _complete(self, obs: Observation) -> bool:
        if obs.ram.get("game_completed") is True:
            return True
        return (self.stop_map is not None
                and (obs.ram.get("map_bank"), obs.ram.get("map_id")) == self.stop_map)

    def run(self, max_steps: int = 2_000_000, max_hours: Optional[float] = 8.0) -> Dict[str, Any]:
        if max_steps < 0 or (max_hours is not None and max_hours <= 0):
            raise ValueError("step budget must be non-negative and hour budget must be positive")
        if not getattr(self.adapter, "supports_save_state", False):
            raise ValueError(f"adapter {self.adapter.name} does not support save states; Go-Explore needs them")
        started = time.monotonic()
        obs = self.adapter.reset()
        self.planner.update(obs)
        if self.archive.data["boot_save"]:
            if self.archive.data["cells"]:
                selected = self.archive.select()
                obs = self.archive.load(selected)
                self.planner.restore(selected.get("milestones_done", ()))
            else:
                selected = self.archive.boot_sidecar()
                obs = self.archive.load(selected)
                self.planner.restore(selected.get("milestones_done", ()))
        else:
            self.archive.save_boot(obs, self.planner)

        if self.archive.data["completed"]:
            return {"steps": self.archive.data["steps"], "iterations": self.archive.data["iterations"],
                    "cells": len(self.archive.data["cells"]), "completed": True,
                    "reason": "already_completed", "namespace": self.archive.namespace,
                    "archive": str(self.archive.path), "q_updates": self.archive.data["q_updates"]}
        reason = "step_budget"
        while self.archive.data["steps"] < max_steps:
            obs = self.adapter.observe()
            if self._complete(obs):
                self.archive.data["completed"] = True
                reason = "target_reached"
                break
            for _ in range(self.segment_steps):
                if self.archive.data["steps"] >= max_steps:
                    break
                if max_hours is not None and time.monotonic() - started >= max_hours * 3600:
                    reason = "time_budget"
                    break
                self.planner.update(obs)
                progress_before = [m["id"] for m in self.planner.summary() if m["done"]]
                state_before = cell_key(obs, progress_before, self.archive.grid)
                button = self._button(obs, state_before)
                action = explorer_action(button)
                self.adapter.act(action)
                obs_after = self.adapter.observe()
                self.archive.data["steps"] += 1
                self.planner.update(obs_after)
                progress_after = [m["id"] for m in self.planner.summary() if m["done"]]
                state_after = cell_key(obs_after, progress_after, self.archive.grid)
                new_cell = state_after is not None and state_after not in self.archive.data["cells"]
                cell = self.archive.observe(obs_after, self.planner)
                newly_completed = [mid for mid in progress_after if mid not in progress_before]
                completed = obs_after.ram.get("game_completed") is True
                reward = float(new_cell) + 5.0 * len(newly_completed) + 100.0 * completed
                self.archive.learn(state_before, button, state_after, reward, completed)
                if cell is None or cell == self._last_cell:
                    self._stuck += 1
                else:
                    self._stuck = 0
                self._last_cell = cell
                self.previous_button = button
                obs = obs_after
                if completed:
                    self.archive.data["completed"] = True
                    reason = "game_completed"
                    break
                if self.stop_map is not None and (obs.ram.get("map_bank"), obs.ram.get("map_id")) == self.stop_map:
                    reason = "target_reached"
                    break
                if self._stuck >= self.stuck_steps:
                    reason = "stuck_segment"
                    break
            self.archive.data["iterations"] += 1
            self.archive.flush()
            if reason in ("time_budget", "game_completed", "target_reached"):
                break
            if self.archive.data["steps"] >= max_steps:
                reason = "step_budget"
                break
            if self.archive.data["cells"]:
                selected = self.archive.select()
                obs = self.archive.load(selected)
                self.planner = GoalPlanner(firered_milestones())
                # Restore goal facts for cell scoring only; they never choose exploratory actions.
                self.planner.restore(selected.get("milestones_done", ()))
                progress = [m["id"] for m in self.planner.summary() if m["done"]]
                self._last_cell = cell_key(obs, progress, self.archive.grid)
                self._stuck = 0
                self.previous_button = None
            else:
                self.archive.save_boot(obs, self.planner)
                obs = self.adapter.observe()
        self.archive.flush()
        return {"steps": self.archive.data["steps"], "iterations": self.archive.data["iterations"],
                "cells": len(self.archive.data["cells"]), "completed": self.archive.data["completed"],
                "reason": reason, "namespace": self.archive.namespace, "archive": str(self.archive.path),
                "q_updates": self.archive.data["q_updates"]}


def _parse_map(value: Optional[str]) -> Optional[Tuple[int, int]]:
    if value is None:
        return None
    try:
        bank, map_id = (int(part) for part in value.split("/", 1))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("map must be BANK/ID, e.g. 3/1") from None
    if bank < 0 or map_id < 0:
        raise argparse.ArgumentTypeError("map bank and id must be non-negative")
    return bank, map_id


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default="mgba", help="save-state-capable adapter (default: mgba)")
    parser.add_argument("--memory-dir", default=str(Path.home() / ".game-brain" / "go-explore"))
    parser.add_argument("--steps", type=int, default=2_000_000, help="lifetime environment-step budget")
    parser.add_argument("--hours", type=float, default=8.0, help="wall-time budget; 0 disables this limit")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cell-grid", type=int, default=2)
    parser.add_argument("--segment-steps", type=int, default=100)
    parser.add_argument("--stuck-steps", type=int, default=40)
    parser.add_argument("--battle-policy", choices=("rule", "random"), default="rule")
    parser.add_argument("--epsilon", type=float, default=0.15,
                        help="probability of exploring instead of using a learned action")
    parser.add_argument("--stop-map", type=_parse_map, default=None,
                        help="optional map BANK/ID to stop on; reaching it is not proof of game completion")
    args = parser.parse_args(argv)
    adapter = make_adapter(args.adapter)
    try:
        if not getattr(adapter, "supports_save_state", False):
            parser.error(f"adapter {adapter.name} does not support save states")
        archive = GoExploreArchive(args.memory_dir, adapter, args.cell_grid, args.seed)
        runner = GoExploreRunner(adapter, archive, args.seed, args.segment_steps, args.stuck_steps,
                                 args.battle_policy, args.stop_map, args.epsilon)
        hours = args.hours if args.hours > 0 else None
        print(json.dumps(runner.run(args.steps, hours), indent=2))
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
