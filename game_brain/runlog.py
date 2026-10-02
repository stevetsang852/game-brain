"""JSONL run log: one line per step, so runs can be inspected and replayed.

Line kinds:
* ``{"kind": "header", ...}``  -- first line: adapter, brains, mode, schema version, start time
* ``{"kind": "step", "step", "frame", "ts", "mode", "observation", "decision",
     "proposed_action", "executed_action", "frames_advanced", "notes"}``
* ``{"kind": "mode_change", ...}`` and ``{"kind": "summary", ...}``

Determinism: replaying ``executed_action`` of every step against the same start state
reproduces the run (see :func:`replay`).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .schema import SCHEMA_VERSION, Action, Observation


class RunLogWriter:
    def __init__(self, path: "str | Path"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8")
        self.lines = 0

    def _write(self, rec: Dict[str, Any]) -> None:
        self._f.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
        self._f.flush()
        self.lines += 1

    def header(self, **info: Any) -> None:
        self._write({"kind": "header", "schema_v": SCHEMA_VERSION, "ts": time.time(), **info})

    def step(self, step: int, obs: Observation, result, frames_advanced: int,
             ts: Optional[float] = None) -> None:
        self._write({
            "kind": "step",
            "step": step,
            "frame": obs.frame,
            "ts": time.time() if ts is None else ts,
            "mode": result.mode.value,
            "observation": obs.summary(),
            "decision": result.decision.to_dict(),
            "proposed_action": result.proposed.to_dict() if result.proposed else None,
            "executed_action": result.executed.to_dict() if result.executed else None,
            "frames_advanced": frames_advanced,
            "notes": list(result.notes),
        })

    def event(self, kind: str, **data: Any) -> None:
        self._write({"kind": kind, "ts": time.time(), **data})

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_log(path: "str | Path") -> Iterator[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def replay(path: "str | Path", adapter) -> List[str]:
    """Re-execute the executed actions of a log on a freshly reset adapter.

    Returns a list of mismatch descriptions (empty list == replay matched the log:
    same frame at every step and same RAM summary).
    """
    mismatches: List[str] = []
    adapter.reset()
    for rec in read_log(path):
        if rec.get("kind") != "step":
            continue
        obs = adapter.observe()
        if obs.frame != rec["frame"]:
            mismatches.append(f"step {rec['step']}: frame {obs.frame} != logged {rec['frame']}")
        if obs.ram != rec["observation"]["ram"]:
            mismatches.append(f"step {rec['step']}: ram differs")
        if rec.get("executed_action"):
            adapter.act(Action.from_dict(rec["executed_action"]))
    return mismatches
