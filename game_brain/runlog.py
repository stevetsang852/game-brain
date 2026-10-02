"""JSONL run log: one line per step, so runs can be inspected and replayed.

Line kinds:
* ``{"kind": "header", ...}``  -- first line: adapter, brains, mode, schema version, start time
* ``{"kind": "step", "step", "frame", "ts", "mode", "observation", "decision",
     "proposed_action", "executed_action", "frames_advanced", "notes"}``
* ``{"kind": "map", "map_ref", "step", "frame", "ram": {...}}`` -- the per-map RAM keys in
  :data:`MAP_KEYS` (collision grid, warps, map size). Written only when they change (on a map
  change); step lines then omit those keys from ``observation.ram`` and carry
  ``observation.map_ref`` instead. :func:`iter_steps` puts them back.
* ``{"kind": "mode_change", ...}`` and ``{"kind": "summary", ...}``

Step-line ``decision`` dedupe (same idea as the map records above: write on change, the
reader puts it back). Only the *written log* is affected; live dashboard envelopes always
carry the full decision and both actions.

* ``decision.milestones`` is written only when it differs from the previous step's value
  (the first step that has milestones writes them). Otherwise the key is dropped and the
  step line carries ``"milestones_same": true``.
* ``executed_action`` is dropped when it equals ``proposed_action`` (both non-null); the step
  line then carries ``"executed_same": true`` instead. A genuinely absent executed action is
  still written explicitly as ``"executed_action": null``, and a different one (SHADOW idle
  wait, ASSIST human preempt, MANUAL) is written in full, so the two cases never collide.

:func:`iter_steps` (and so :func:`replay`) restores ``decision.milestones`` and
``executed_action`` on every step and strips both markers. Logs written before these
optimisations have no markers and pass through unchanged.

Determinism: replaying ``executed_action`` of every step against the same start state
reproduces the run (see :func:`replay`).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .schema import SCHEMA_VERSION, Action, Observation


#: Observation.ram keys that only change with the map; logged once per map, not per step.
MAP_KEYS = ("map_w", "map_h", "collision", "warps")


class RunLogWriter:
    def __init__(self, path: "str | Path"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8")
        self.lines = 0
        self._map_ref = -1
        self._last_map: Optional[Dict[str, Any]] = None
        self._last_milestones: Optional[List[Dict[str, Any]]] = None

    def _dedupe_map(self, step: int, frame: int, summary: Dict[str, Any]) -> None:
        ram = summary.get("ram") or {}
        m = {k: ram.pop(k) for k in MAP_KEYS if k in ram}
        if not m:
            return
        if m != self._last_map:
            self._map_ref += 1
            self._last_map = m
            self._write({"kind": "map", "map_ref": self._map_ref, "step": step, "frame": frame, "ram": m})
        summary["map_ref"] = self._map_ref

    def _dedupe_decision(self, rec: Dict[str, Any]) -> None:
        """Drop unchanged ``decision.milestones`` and ``executed_action == proposed_action``
        from a step record (in place), leaving markers for :func:`iter_steps`."""
        dec = rec["decision"]
        ms = dec.get("milestones")
        if ms is not None and ms == self._last_milestones:
            del dec["milestones"]
            rec["milestones_same"] = True
        self._last_milestones = ms
        if rec["executed_action"] is not None and rec["executed_action"] == rec["proposed_action"]:
            del rec["executed_action"]
            rec["executed_same"] = True

    def _write(self, rec: Dict[str, Any]) -> None:
        self._f.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
        self._f.flush()
        self.lines += 1

    def header(self, **info: Any) -> None:
        self._write({"kind": "header", "schema_v": SCHEMA_VERSION, "ts": time.time(), **info})

    def step(self, step: int, obs: Observation, result, frames_advanced: int,
             ts: Optional[float] = None) -> None:
        summary = obs.summary()
        self._dedupe_map(step, obs.frame, summary)
        rec = {
            "kind": "step",
            "step": step,
            "frame": obs.frame,
            "ts": time.time() if ts is None else ts,
            "mode": result.mode.value,
            "observation": summary,
            "decision": result.decision.to_dict(),
            "proposed_action": result.proposed.to_dict() if result.proposed else None,
            "executed_action": result.executed.to_dict() if result.executed else None,
            "frames_advanced": frames_advanced,
            "notes": list(result.notes),
        }
        # ``to_dict()`` builds fresh dicts, so popping keys here never touches the live
        # Decision/Action objects the dashboard broadcasts after this call.
        self._dedupe_decision(rec)
        self._write(rec)

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


def iter_steps(path: "str | Path") -> Iterator[Dict[str, Any]]:
    """Step records with ``observation.ram`` restored to what the adapter returned
    (map keys merged back from the matching ``map`` record), ``decision.milestones`` and
    ``executed_action`` restored where the writer deduped them. Old logs pass through unchanged."""
    maps: Dict[int, Dict[str, Any]] = {}
    last_ms: Optional[List[Dict[str, Any]]] = None
    for rec in read_log(path):
        kind = rec.get("kind")
        if kind == "map":
            maps[rec["map_ref"]] = rec["ram"]
        elif kind == "step":
            obs = rec["observation"]
            ref = obs.pop("map_ref", None)
            if ref is not None:
                obs["ram"] = {**obs.get("ram", {}), **json.loads(json.dumps(maps[ref]))}
            dec = rec.get("decision")
            if rec.pop("milestones_same", False):
                dec["milestones"] = json.loads(json.dumps(last_ms))
            elif isinstance(dec, dict):
                last_ms = dec.get("milestones")
            if rec.pop("executed_same", False):
                rec["executed_action"] = json.loads(json.dumps(rec["proposed_action"]))
            yield rec


def replay(path: "str | Path", adapter) -> List[str]:
    """Re-execute the executed actions of a log on a freshly reset adapter.

    Returns a list of mismatch descriptions (empty list == replay matched the log:
    same frame at every step and same RAM summary).
    """
    mismatches: List[str] = []
    adapter.reset()
    for rec in iter_steps(path):
        obs = adapter.observe()
        if obs.frame != rec["frame"]:
            mismatches.append(f"step {rec['step']}: frame {obs.frame} != logged {rec['frame']}")
        if obs.ram != rec["observation"]["ram"]:
            mismatches.append(f"step {rec['step']}: ram differs")
        if rec.get("executed_action"):
            adapter.act(Action.from_dict(rec["executed_action"]))
    return mismatches
