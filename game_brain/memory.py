"""Persistent experience and exploration archive; no model training or policy selection."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from . import savestate
from .arbiter.arbiter import StepResult
from .brain.goals import GoalPlanner
from .schema import Observation

SCHEMA_VERSION = 1
REWARD_VERSION = "novelty-v1"


def default_memory_dir() -> Path:
    return Path(os.environ.get("GAME_BRAIN_MEMORY_DIR") or Path.home() / ".game-brain" / "memory")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _cell(obs: Observation, progress: list[str]) -> Optional[str]:
    ram = obs.ram
    fields = [ram.get(k) for k in ("map_bank", "map_id", "player_x", "player_y")]
    if obs.in_battle or ram.get("scene") != "overworld" or any(
            not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in fields):
        return None
    return _json([*fields, ram.get("party_count"), sorted(progress)])


class ExperienceMemory:
    """One connection per run. Each completed transition commits independently.

    ROM namespaces never share novelty or cells. Mock adapters use their adapter name instead
    of masquerading as a ROM. Cell saves live separately from ordinary saves and their latest
    pointer; the archive only saves a cell's first representative (not every visit).
    """

    def __init__(self, root: "str | Path", adapter, run_id: str, policy_version: Optional[str],
                 brains: list[str], log_path: Path, planner: Optional[GoalPlanner] = None,
                 side: Optional[Dict[str, Any]] = None, save_cells: bool = True):
        self.root = savestate.check_save_dir(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "experience.sqlite3"
        self.adapter = adapter
        self.run_id = run_id
        self.episode_id = uuid.uuid4().hex
        self.policy_version = policy_version
        self.rom_hash = getattr(adapter, "rom_sha1", None)
        self.namespace = f"{adapter.name}:{self.rom_hash or 'synthetic'}"
        self.planner = planner
        self.save_cells = save_cells
        self.last_step: Optional[int] = None
        self.last_terminated = False
        self.goal_seen = False
        self._dashboard_totals: Optional[Dict[str, Any]] = None
        self._new_discoveries = 0
        self._new_cells = 0
        self.origin = (side or {}).get("memory")
        self.db = sqlite3.connect(self.path, timeout=30)
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(f"memory schema {version} is not supported (expected {SCHEMA_VERSION})")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, rom_hash TEXT,
                    policy_version TEXT, log_path TEXT NOT NULL, started_at REAL NOT NULL,
                    ended_at REAL, resumed_from TEXT, schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs,
                    start_step INTEGER NOT NULL, initial_state TEXT NOT NULL,
                    parent_run_id TEXT, parent_episode_id TEXT, parent_step INTEGER,
                    end_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    run_id TEXT NOT NULL REFERENCES runs, episode_id TEXT NOT NULL REFERENCES episodes,
                    step_id INTEGER NOT NULL, namespace TEXT NOT NULL, state_before TEXT NOT NULL,
                    executed_action TEXT, state_after TEXT NOT NULL, reward_parts TEXT NOT NULL,
                    reward REAL NOT NULL, terminated INTEGER NOT NULL, truncated INTEGER NOT NULL,
                    actor TEXT NOT NULL, mode TEXT NOT NULL, brain TEXT NOT NULL, policy_version TEXT,
                    rom_hash TEXT, schema_version INTEGER NOT NULL, reward_version TEXT NOT NULL,
                    frames_advanced INTEGER NOT NULL, cell_key TEXT,
                    PRIMARY KEY (run_id, step_id)
                );
                CREATE INDEX IF NOT EXISTS transitions_cell ON transitions(namespace, cell_key);
                CREATE TABLE IF NOT EXISTS discoveries (
                    namespace TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
                    PRIMARY KEY (namespace, kind, key)
                );
                CREATE TABLE IF NOT EXISTS cells (
                    namespace TEXT NOT NULL, cell_key TEXT NOT NULL, visits INTEGER NOT NULL,
                    first_run_id TEXT NOT NULL, first_episode_id TEXT NOT NULL, first_step INTEGER NOT NULL,
                    representative TEXT, progress TEXT NOT NULL,
                    representative_run_id TEXT, representative_episode_id TEXT, representative_step INTEGER,
                    PRIMARY KEY (namespace, cell_key)
                );
            """)
            with self.db:
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,NULL,?,?)",
                                (run_id, self.namespace, self.rom_hash, policy_version, str(log_path),
                                 time.time(), (side or {}).get("_path"), SCHEMA_VERSION))
        except BaseException:
            self.db.close()
            raise
        self.cell_saver = savestate.SaveManager(
            self.root / "exploration", adapter, brains, run_id, every=0, on_milestone=False,
            extra={"policy_version": policy_version})

    def _progress(self, obs: Observation) -> list[str]:
        if self.planner is None:
            return []
        self.planner.update(obs)
        return [m["id"] for m in self.planner.summary() if m["done"]]

    def _discover(self, kind: str, key: Any) -> bool:
        added = self.db.execute("INSERT OR IGNORE INTO discoveries VALUES (?,?,?)",
                                (self.namespace, kind, _json(key))).rowcount == 1
        self._new_discoveries += int(added)
        return added

    def _seed(self, obs: Observation, progress: list[str]) -> None:
        ram = obs.ram
        key = _cell(obs, progress)
        if key is not None:
            self._discover("tile", [ram[k] for k in ("map_bank", "map_id", "player_x", "player_y")])
            self._discover("map", [ram["map_bank"], ram["map_id"]])
        for mid in progress:
            self._discover("milestone", mid)
        count = ram.get("party_count")
        if isinstance(count, int):
            for n in range(1, count + 1):
                self._discover("party", n)

    def start(self, obs: Observation, step: int) -> None:
        progress = self._progress(obs)
        self.goal_seen = self._success(obs)
        with self.db:
            self._seed(obs, progress)
            self._begin_episode(obs, step, self.origin)
            self._visit(obs, progress, step - 1)
        self._dashboard_totals = {
            "runs": self.db.execute("SELECT COUNT(*) FROM runs WHERE namespace=?",
                                    (self.namespace,)).fetchone()[0],
            "transitions": self.db.execute("SELECT COUNT(*) FROM transitions WHERE namespace=?",
                                           (self.namespace,)).fetchone()[0],
            "reward": self.db.execute("SELECT COALESCE(SUM(reward),0) FROM transitions WHERE namespace=?",
                                      (self.namespace,)).fetchone()[0],
            "cells": self.db.execute("SELECT COUNT(*) FROM cells WHERE namespace=?",
                                     (self.namespace,)).fetchone()[0],
            "discoveries": self.db.execute("SELECT COUNT(*) FROM discoveries WHERE namespace=?",
                                           (self.namespace,)).fetchone()[0],
        }

    def _begin_episode(self, obs: Observation, step: int, parent=None) -> None:
        parent = parent or {}
        self.db.execute("INSERT INTO episodes VALUES (?,?,?,?,?,?,?,NULL)",
                        (self.episode_id, self.run_id, step, _json(obs.summary()),
                         parent.get("run_id"), parent.get("episode_id"), parent.get("step")))

    @staticmethod
    def _success(obs: Observation) -> bool:
        ram = obs.ram
        return (ram.get("scene") == "overworld" and not obs.in_battle
                and (ram.get("map_bank"), ram.get("map_id")) == (3, 1)
                and isinstance(ram.get("party_count"), int) and ram["party_count"] >= 1)

    def _visit(self, obs: Observation, progress: list[str], step: int) -> Optional[str]:
        key = _cell(obs, progress)
        if key is None:
            return None
        row = self.db.execute("SELECT representative FROM cells WHERE namespace=? AND cell_key=?",
                              (self.namespace, key)).fetchone()
        if row is None:
            self.db.execute("INSERT INTO cells (namespace,cell_key,visits,first_run_id,first_episode_id,"
                            "first_step,representative,progress) VALUES (?,?,1,?,?,?,?,?)",
                            (self.namespace, key, self.run_id, self.episode_id, step, None, _json(progress)))
            self._new_cells += 1
        else:
            self.db.execute("UPDATE cells SET visits=visits+1 WHERE namespace=? AND cell_key=?",
                            (self.namespace, key))
        if (row is None or row[0] is None) and self.save_cells and self.cell_saver.enabled:
            # A stable index gives each snapshot a distinct name, even at the same step.
            index = self.db.execute("SELECT rowid FROM cells WHERE namespace=? AND cell_key=?",
                                    (self.namespace, key)).fetchone()[0]
            self.cell_saver.extra["memory"] = self.cursor(step + 1)
            self.cell_saver.extra["exploration_cell"] = json.loads(key)
            sv = self.cell_saver.save(step + 1, self.planner.summary() if self.planner else None,
                                      reason=f"exploration-cell-{index}")
            relative = Path(sv["_path"]).relative_to(self.root).as_posix()
            self.db.execute("UPDATE cells SET representative=?,representative_run_id=?,"
                            "representative_episode_id=?,representative_step=? "
                            "WHERE namespace=? AND cell_key=?",
                            (relative, self.run_id, self.episode_id, step, self.namespace, key))
        return key

    def record(self, step: int, before: Observation, result: StepResult, after: Observation,
               frames_advanced: int) -> Dict[str, Any]:
        if after.frame - before.frame != frames_advanced:
            raise ValueError("transition frame delta does not match the executed action")
        if self.last_step is not None and step != self.last_step + 1:
            raise ValueError("non-contiguous trajectory: start a new run after loading a state")
        if self.last_terminated:
            self.episode_id = uuid.uuid4().hex
        progress = self._progress(after)
        self._new_discoveries = 0
        self._new_cells = 0
        parts = {"new_tile": 0.0, "new_map": 0.0, "milestone": 0.0, "party": 0.0,
                 "battle_win": 0.0, "whiteout": 0.0}
        terminal = not self.goal_seen and self._success(after)
        actor = result.decision.actor if result.decision.executed else "none"
        with self.db:
            if self.last_terminated:
                self._begin_episode(before, step)
            ram = after.ram
            if _cell(after, progress) is not None:
                parts["new_tile"] = float(self._discover(
                    "tile", [ram[k] for k in ("map_bank", "map_id", "player_x", "player_y")]))
                parts["new_map"] = 5.0 * self._discover("map", [ram["map_bank"], ram["map_id"]])
            for mid in progress:
                parts["milestone"] += 10.0 * self._discover("milestone", mid)
            count = ram.get("party_count")
            if isinstance(count, int):
                for n in range(1, count + 1):
                    parts["party"] += 10.0 * self._discover("party", n)
            battle = ram.get("battle") or {}
            prior = before.ram.get("battle") or {}
            # Only explicit outcomes on an outcome edge count; HP zero alone is not a whiteout.
            outcome = battle.get("outcome")
            if before.in_battle and outcome != prior.get("outcome"):
                if outcome == "lose":
                    parts["whiteout"] = -10.0
                elif outcome == "win":
                    context = [before.ram.get("map_bank"), before.ram.get("map_id"),
                               (battle.get("opponent") or prior.get("opponent") or {}).get("species")]
                    parts["battle_win"] = 5.0 * self._discover("battle_win", context)
            key = self._visit(after, progress, step)
            data = {"run_id": self.run_id, "episode_id": self.episode_id, "step_id": step,
                    "reward_parts": parts, "reward": sum(parts.values()), "terminated": terminal,
                    "truncated": False, "actor": actor, "policy_version": self.policy_version,
                    "rom_hash": self.rom_hash, "schema_version": SCHEMA_VERSION,
                    "reward_version": REWARD_VERSION}
            self.db.execute("INSERT INTO transitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (self.run_id, self.episode_id, step, self.namespace, _json(before.summary()),
                             _json(result.executed.to_dict()) if result.executed else None,
                             _json(after.summary()), _json(parts), data["reward"], int(terminal), 0,
                             actor, result.mode.value, result.decision.brain, self.policy_version,
                             self.rom_hash, SCHEMA_VERSION, REWARD_VERSION, frames_advanced, key))
            if terminal:
                self.db.execute("UPDATE episodes SET end_reason='task_success' WHERE episode_id=?",
                                (self.episode_id,))
        self.last_step = step
        self.last_terminated = terminal
        self.goal_seen |= terminal
        if self._dashboard_totals is not None:
            self._dashboard_totals["transitions"] += 1
            self._dashboard_totals["reward"] += data["reward"]
            self._dashboard_totals["discoveries"] += self._new_discoveries
            self._dashboard_totals["cells"] += self._new_cells
        return data

    def cursor(self, steps_done: int) -> Dict[str, Any]:
        return {"run_id": self.run_id, "episode_id": self.episode_id, "step": steps_done,
                "namespace": self.namespace, "schema_version": SCHEMA_VERSION}

    def finish(self, reason: str) -> Dict[str, Any]:
        truncated = self.last_step is not None and not self.last_terminated
        with self.db:
            if truncated:
                self.db.execute("UPDATE transitions SET truncated=1 WHERE run_id=? AND step_id=?",
                                (self.run_id, self.last_step))
            self.db.execute("UPDATE episodes SET end_reason=COALESCE(end_reason,?) WHERE episode_id=?",
                            (reason, self.episode_id))
            self.db.execute("UPDATE runs SET ended_at=? WHERE run_id=?", (time.time(), self.run_id))
        return {"episode_id": self.episode_id, "step_id": self.last_step,
                "terminated": self.last_terminated, "truncated": truncated, "reason": reason}

    def close(self) -> None:
        self.db.close()

    def save(self) -> None:
        """Flush the experience database and checkpoint committed learning data to its main file."""
        self.db.commit()
        busy, _wal_frames, _checkpointed = self.db.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
        if busy:
            raise RuntimeError(f"could not checkpoint learning data at {self.path}: database is busy")

    def dashboard_status(self) -> Dict[str, Any]:
        """Bounded live summary; never sends game snapshots or full local paths."""
        totals = dict(self._dashboard_totals or {})
        recent = []
        for row in self.db.execute(
                "SELECT step_id,actor,brain,executed_action,reward,reward_parts,terminated,truncated,"
                "state_after FROM transitions WHERE run_id=? ORDER BY step_id DESC LIMIT 8",
                (self.run_id,)):
            step, actor, brain, action, reward, parts, terminated, truncated, after = row
            state = json.loads(after)
            ram = state.get("ram") or {}
            action = json.loads(action) if action else None
            buttons = [p["button"] for p in (action or {}).get("presses", [])]
            recent.append({"step": step, "actor": actor, "brain": brain,
                           "action": "+".join(buttons) or "等待", "reward": reward,
                           "reward_parts": json.loads(parts), "terminated": bool(terminated),
                           "truncated": bool(truncated), "scene": ram.get("scene"),
                           "map": [ram.get("map_bank"), ram.get("map_id")],
                           "position": [ram.get("player_x"), ram.get("player_y")]})
        cells = []
        for cell, visits, save in self.db.execute(
                "SELECT cell_key,visits,representative FROM cells WHERE namespace=? "
                "ORDER BY visits,cell_key LIMIT 6", (self.namespace,)):
            loc = json.loads(cell)
            cells.append({"map": loc[0:2], "position": loc[2:4], "party_count": loc[4],
                          "progress": loc[5], "visits": visits,
                          "save": Path(save).name if save else None})
        unique_cells = self.db.execute("SELECT COUNT(*) FROM cells WHERE namespace=?", (self.namespace,)).fetchone()[0]
        best = self.db.execute("SELECT MAX(reward) FROM transitions WHERE run_id=?", (self.run_id,)).fetchone()[0]
        return {"enabled": True, "namespace": self.namespace, "run_id": self.run_id,
                "episode_id": self.episode_id, "policy_version": self.policy_version,
                "rom_hash": self.rom_hash, "totals": totals,
                "unique_cells": unique_cells, "best_reward": best,
                "recent": list(reversed(recent)), "least_visited_cells": cells}


def inspect_memory(root: "str | Path", namespace: Optional[str] = None) -> Dict[str, Any]:
    """Read-only counts and least-visited resumable cells; paths follow a moved volume."""
    root = Path(root).expanduser().resolve()
    path = root / "experience.sqlite3"
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        namespaces = [r[0] for r in db.execute("SELECT DISTINCT namespace FROM runs ORDER BY namespace")]
        if namespace is None:
            if len(namespaces) != 1:
                raise ValueError(f"choose --namespace from {namespaces}")
            namespace = namespaces[0]
        if namespace not in namespaces:
            raise ValueError(f"unknown namespace {namespace!r}")
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE namespace=?",
                                   (namespace,)).fetchone()[0] for table in ("runs", "transitions", "cells")}
        cells = []
        for key, visits, save, run, episode, step in db.execute(
                "SELECT cell_key,visits,representative,COALESCE(representative_run_id,first_run_id),"
                "COALESCE(representative_episode_id,first_episode_id),COALESCE(representative_step,first_step) "
                "FROM cells WHERE namespace=? ORDER BY visits,cell_key LIMIT 20", (namespace,)):
            cells.append({"cell": json.loads(key), "visits": visits,
                          "save": str(root / save) if save else None,
                          "run_id": run, "episode_id": episode, "step_id": step})
        return {"namespace": namespace, "namespaces": namespaces, **counts, "cells": cells}
    finally:
        db.close()


def route(root: "str | Path", run_id: str, step_id: int) -> list[Dict[str, Any]]:
    """Executed transitions to a representative, following resume lineage (never the abandoned tail)."""
    path = Path(root).expanduser().resolve() / "experience.sqlite3"
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    seen = set()

    def collect(run: str, end: int) -> list[Dict[str, Any]]:
        if run in seen:
            raise ValueError("cyclic resume lineage")
        seen.add(run)
        ep = db.execute("SELECT * FROM episodes WHERE run_id=? ORDER BY start_step LIMIT 1", (run,)).fetchone()
        if ep is None:
            raise ValueError(f"missing route origin for {run}")
        if end < ep["start_step"] - 1:
            raise ValueError(f"step {end} precedes the route origin for {run}")
        out = []
        if ep["parent_run_id"]:
            out = collect(ep["parent_run_id"], ep["parent_step"] - 1)
        rows = db.execute("SELECT * FROM transitions WHERE run_id=? AND step_id<=? ORDER BY step_id",
                          (run, end)).fetchall()
        if len(rows) != end - ep["start_step"] + 1:
            raise ValueError(f"incomplete route to step {end} for {run}")
        for row in rows:
            rec = dict(row)
            for key in ("state_before", "state_after", "executed_action", "reward_parts"):
                rec[key] = json.loads(rec[key]) if rec[key] is not None else None
            out.append(rec)
        return out

    try:
        return collect(run_id, step_id)
    finally:
        db.close()


def main(argv=None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--memory-dir", default=str(default_memory_dir()))
    ap.add_argument("--namespace")
    ap.add_argument("--route", metavar="RUN_ID:STEP",
                    help="export executed transitions to a representative, including connected resume ancestors")
    args = ap.parse_args(argv)
    if args.route:
        run_id, separator, step = args.route.rpartition(":")
        if not separator:
            ap.error("--route requires RUN_ID:STEP")
        result = route(args.memory_dir, run_id, int(step))
    else:
        result = inspect_memory(args.memory_dir, args.namespace)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
