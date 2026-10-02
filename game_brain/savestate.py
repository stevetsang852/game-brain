"""Save / resume: emulator save states with a small JSON sidecar (format: notes/savestate-format.md).

* ``SaveManager`` writes ``<save_dir>/<run_id>/<step>_<reason>.state`` (primary: the adapter's
  emulator snapshot), ``.json`` (sidecar) and, if the game has written its battery save, ``.sav``
  (backup only: it is written by the in-game SAVE, never by us). Files are written to a temp name
  and renamed, sidecar last, so a sidecar always points at a complete state even if the process
  is killed mid-save. ``<save_dir>/latest`` holds the path of the newest sidecar, relative to
  ``save_dir`` (so the directory can be moved / mounted elsewhere, e.g. Docker ``/saves``).
* Retention: only the newest ``keep_periodic`` saves whose sidecar ``reason`` is exactly
  ``"periodic"`` are kept per run directory; milestone, final and any other saves are never deleted.
* ``resolve_resume("latest" | path, save_dir)`` -> sidecar dict (with ``_state_path`` etc.).
* Save dirs must be outside the repo tree (the repo is public): ``check_save_dir`` refuses
  anything inside the checkout or inside the current git work tree.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FORMAT = "game-brain-savestate"
FORMAT_VERSION = 1
DEFAULT_SAVE_EVERY = 500
DEFAULT_KEEP_PERIODIC = 10
_PKG_ROOT = Path(__file__).resolve().parent.parent      # the checkout (or site-packages) dir


def default_save_dir() -> Path:
    return Path(os.environ.get("GAME_BRAIN_SAVE_DIR") or Path.home() / ".game-brain" / "saves")


def _git_toplevel(path: Path) -> Optional[Path]:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None


def repo_roots() -> List[Path]:
    """Directories saves must never go into: the git work tree of this package and of the cwd."""
    roots = []
    for p in (_PKG_ROOT, Path.cwd()):
        top = _git_toplevel(p)
        if top and top not in roots:
            roots.append(top)
    if (_PKG_ROOT / ".git").exists() and _PKG_ROOT not in roots:
        roots.append(_PKG_ROOT)
    return roots


def check_save_dir(save_dir: "str | Path", roots: Optional[Iterable[Path]] = None) -> Path:
    """Resolve ``save_dir``; raise ValueError if it is inside a repo tree (never commit saves)."""
    d = Path(save_dir).expanduser().resolve()
    for r in (repo_roots() if roots is None else roots):
        r = Path(r).resolve()
        if d == r or r in d.parents:
            raise ValueError(f"save dir {d} is inside the repo tree {r}; saves (ROM-derived game data) "
                             "must stay outside the repo -- use e.g. ~/.game-brain/saves")
    return d


def git_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(_PKG_ROOT), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5)
        if out.returncode != 0:
            return None
        dirty = subprocess.run(["git", "-C", str(_PKG_ROOT), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except Exception:
        return None


def party_hp(ram: Dict[str, Any]) -> Optional[List[Dict[str, int]]]:
    """Party HP if the observation has it: ``ram["party"]`` (mGBA adapter, overworld and battle), else the
    active battler's HP while in battle (``ram["battle"]["player"]``). None = not available."""
    party = ram.get("party")
    if isinstance(party, list) and party and all(isinstance(p, dict) and "hp" in p for p in party):
        return [{"hp": p["hp"], "max_hp": p.get("max_hp")} for p in party]
    player = (ram.get("battle") or {}).get("player")
    if isinstance(player, dict) and "hp" in player:
        return [{"hp": player["hp"], "max_hp": player.get("max_hp"), "source": "battle"}]
    return None


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class SaveManager:
    """Writes save states for one run. ``every`` = periodic interval in steps (0 = off).
    ``keep_periodic`` = after each periodic save, keep only the newest N periodic saves of this run
    (0 = keep all); see :meth:`prune_periodic`."""

    def __init__(self, save_dir: "str | Path", adapter, brains: List[str], run_id: str,
                 every: int = DEFAULT_SAVE_EVERY, on_milestone: bool = True,
                 resumed_from: Optional[str] = None, roots: Optional[Iterable[Path]] = None,
                 keep_periodic: int = DEFAULT_KEEP_PERIODIC):
        self.root = check_save_dir(save_dir, roots)
        self.dir = self.root / run_id
        self.adapter = adapter
        self.brains = list(brains)
        self.run_id = run_id
        self.every = int(every)
        self.keep_periodic = int(keep_periodic)
        self.pruned: List[str] = []          # sidecar paths deleted by prune_periodic()
        self.on_milestone = on_milestone
        self.resumed_from = resumed_from
        self.commit = git_commit()
        self.saved: List[Dict[str, Any]] = []
        self._done: Optional[set] = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.adapter, "supports_save_state", False))

    def after_step(self, steps_done: int, milestones: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Call after a step's action was executed; ``steps_done`` = steps executed so far."""
        if not self.enabled:
            return []
        out = []
        done = {m["id"] for m in milestones or () if m.get("done")}
        if self._done is None:
            self._done = done          # first step: what was already done is not "reached" now
        new = [m["id"] for m in milestones or () if m.get("done") and m["id"] not in self._done]
        self._done |= done
        if self.on_milestone and new:
            out.append(self.save(steps_done, milestones, reason="milestone-" + new[-1], new_milestones=new))
        elif self.every and steps_done % self.every == 0:
            out.append(self.save(steps_done, milestones, reason="periodic"))
            self.prune_periodic()
        return out

    def prune_periodic(self) -> List[str]:
        """Delete this run's oldest periodic saves beyond ``keep_periodic`` (state + sidecar + .sav).

        Only sidecars that parse as ours and have ``reason == "periodic"`` exactly are candidates:
        milestone / final / anything else (e.g. future go-explore cells) is never touched, nor is the
        save ``latest`` points at, nor files outside this run's directory. The sidecar is removed
        first, so an interrupted prune never leaves a sidecar without its state. Returns the
        deleted sidecar paths."""
        if self.keep_periodic <= 0 or not self.dir.is_dir():
            return []
        periodic = []
        for p in self.dir.glob("*.json"):
            try:
                side = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(side, dict) and side.get("format") == FORMAT and side.get("reason") == "periodic":
                periodic.append((int(side.get("step") or 0), p, side))
        periodic.sort(key=lambda t: (t[0], t[1].name))
        latest = _read_latest(self.root)
        deleted = []
        for _step, p, side in periodic[:max(0, len(periodic) - self.keep_periodic)]:
            if latest is not None and latest.resolve() == p.resolve():
                continue
            p.unlink(missing_ok=True)
            for key in ("state_file", "sav_file"):
                name = side.get(key)
                if name and Path(name).name == name:         # a bare file name in this directory only
                    (self.dir / name).unlink(missing_ok=True)
            deleted.append(str(p))
        self.pruned.extend(deleted)
        return deleted

    def save(self, steps_done: int, milestones: Optional[List[Dict[str, Any]]], reason: str,
             new_milestones: Optional[List[str]] = None) -> Dict[str, Any]:
        a = self.adapter
        obs = a.observe()
        ram = obs.ram
        state = a.save_state()
        self.dir.mkdir(parents=True, exist_ok=True)
        base = self.dir / f"{steps_done:07d}_{reason}"
        _atomic_write(base.with_suffix(".state"), state)
        sav = a.battery_save()
        if sav:
            _atomic_write(base.with_suffix(".sav"), sav)
        ms = milestones or []
        current = next((m["id"] for m in ms if not m.get("done")), None)
        side = {
            "format": FORMAT, "format_version": FORMAT_VERSION,
            "state_file": base.with_suffix(".state").name, "state_sha1": sha1(state),
            "sav_file": base.with_suffix(".sav").name if sav else None, "sav_sha1": sha1(sav) if sav else None,
            "reason": reason, "new_milestones": new_milestones or [],
            "step": steps_done, "frame": obs.frame,
            "scene": ram.get("scene"), "in_battle": ram.get("in_battle"),
            "map_bank": ram.get("map_bank"), "map_id": ram.get("map_id"),
            "x": ram.get("player_x"), "y": ram.get("player_y"), "facing": ram.get("facing"),
            "milestone": current, "milestones_done": [m["id"] for m in ms if m.get("done")],
            "party_count": ram.get("party_count"), "party_hp": party_hp(ram),
            "adapter": a.name, "adapter_state": a.adapter_state(),
            "rom_sha1": getattr(a, "rom_sha1", None), "brains": self.brains,
            "git_commit": self.commit, "run_id": self.run_id, "resumed_from": self.resumed_from,
            "timestamp": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        side_path = base.with_suffix(".json")
        _atomic_write(side_path, (json.dumps(side, indent=1) + "\n").encode())
        _atomic_write(self.root / "latest", (side_path.relative_to(self.root).as_posix() + "\n").encode())
        side["_path"] = str(side_path)
        self.saved.append(side)
        return side


def load_sidecar(path: "str | Path") -> Dict[str, Any]:
    p = Path(path).expanduser()
    if p.suffix in (".state", ".sav"):
        p = p.with_suffix(".json")
    side = json.loads(p.read_text())
    if side.get("format") != FORMAT:
        raise ValueError(f"{p}: not a game-brain save sidecar")
    if side.get("format_version", 0) > FORMAT_VERSION:
        raise ValueError(f"{p}: format_version {side['format_version']} is newer than {FORMAT_VERSION}")
    side["_path"] = str(p.resolve())
    side["_state_path"] = str(p.with_name(side["state_file"]).resolve())
    side["_sav_path"] = str(p.with_name(side["sav_file"]).resolve()) if side.get("sav_file") else None
    return side


def _read_latest(root: Path) -> Optional[Path]:
    """The sidecar ``<root>/latest`` points at, or None. The pointer is relative to ``root``
    (format 1 since keep-periodic); older pointers hold an absolute path, accepted only if it is an
    existing file inside ``root``. An absolute path from elsewhere (e.g. the container's ``/saves``
    after the directory was moved) is mapped to ``<root>/<run_id>/<file>`` if that exists."""
    ptr = root / "latest"
    try:
        text = ptr.read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    p = Path(text)
    if not p.is_absolute():
        p = root / p
        if ".." in Path(text).parts or not _inside(p, root):
            return None
        return p if p.is_file() else None
    if p.is_file() and _inside(p, root):
        return p
    if len(p.parts) >= 2:
        q = root / p.parts[-2] / p.parts[-1]
        if q.is_file():
            return q
    return None


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_resume(spec: str, save_dir: "str | Path") -> Dict[str, Any]:
    """``spec`` = "latest" (newest save in ``save_dir``, via the ``latest`` pointer resolved relative to
    ``save_dir``) or a sidecar/state path."""
    if spec != "latest":
        return load_sidecar(spec)
    root = Path(save_dir).expanduser()
    p = _read_latest(root)
    if p is not None:
        return load_sidecar(p)
    sides = sorted(root.glob("*/*.json"), key=lambda p: p.stat().st_mtime)
    if not sides:
        raise FileNotFoundError(f"no saves in {root}")
    return load_sidecar(sides[-1])


def read_state(side: Dict[str, Any]) -> bytes:
    data = Path(side["_state_path"]).read_bytes()
    if sha1(data) != side["state_sha1"]:
        raise ValueError(f"{side['_state_path']}: sha1 does not match the sidecar")
    return data
