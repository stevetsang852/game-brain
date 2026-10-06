"""Stuck detection (auto-learn, part 1): the player's tile hasn't changed for N counted steps
while the brain is driving -> switch the path brain to free explore.

Counting rules (one call per step, with that step's observation, before the action):

* only in ``auto`` mode and only while auto-learn is enabled; in ``manual`` / ``assist`` /
  ``shadow`` (a human drives, or nothing is executed) and while disabled the counter is reset and
  nothing counts;
* steps that are not "the player could walk but didn't" are skipped -- neither counted nor a
  reset: no player position (warp fade, battle / menu screens, intro), ``in_battle``,
  ``controls_locked`` (FireRed: script / text box / START menu / door warp, see
  notes/mgba-bridge.md), and the path brain's own map-transition waits (``warp transition`` /
  ``arrived on a new map``);
* any other step at a different (map, x, y) than the last counted one restarts the count at 1.

``stuck_steps`` = counted steps in a row on the current tile. When it reaches ``threshold`` the
detector reports it once; the caller (Session.watch_stuck) does the switch.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

#: --stuck-steps default
DEFAULT_STUCK_STEPS = 300

#: PathBrain plans that are waits for a map transition, not "standing still"
TRANSITION_PLANS = ("warp transition", "arrived on a new map")


def skip_reason(obs, decision=None) -> Optional[str]:
    """Why this step does not count (None = it counts)."""
    ram = obs.ram if isinstance(getattr(obs, "ram", None), dict) else {}
    if obs.position is None or None in obs.position:
        return "no position (warp/fade, battle or menu screen, intro)"
    if ram.get("in_battle"):
        return "battle"
    if ram.get("controls_locked"):
        return "controls locked (script/text box/menu/warp)"
    if decision is not None and getattr(decision, "plan", None) in TRANSITION_PLANS:
        return "map transition"
    return None


class StuckDetector:
    def __init__(self, threshold: int = DEFAULT_STUCK_STEPS, enabled: bool = False):
        threshold = int(threshold)
        if threshold < 1:
            raise ValueError(f"stuck threshold must be >= 1 (got {threshold})")
        self.threshold = threshold
        self.enabled = bool(enabled)
        self.reset()

    def reset(self) -> None:
        self.tile: Optional[Tuple[Any, Any, int, int]] = None
        self.stuck_steps = 0
        self.fired = False

    def set_enabled(self, enabled: bool) -> None:
        if bool(enabled) != self.enabled:
            self.enabled = bool(enabled)
            self.reset()

    def status(self) -> Dict[str, Any]:
        """``status.auto_learn``."""
        return {"enabled": self.enabled, "stuck_steps": self.stuck_steps, "threshold": self.threshold}

    def observe(self, obs, mode: str, decision=None) -> bool:
        """Count one step. True exactly once per tile, on the step ``stuck_steps`` reaches ``threshold``."""
        mode = getattr(mode, "value", mode)
        if not self.enabled or mode != "auto":
            if self.tile is not None or self.stuck_steps:
                self.reset()
            return False
        if skip_reason(obs, decision) is not None:
            return False
        ram = obs.ram
        tile = (ram.get("map_bank"), ram.get("map_id"), int(obs.position[0]), int(obs.position[1]))
        if tile != self.tile:
            self.tile, self.stuck_steps, self.fired = tile, 1, False
        else:
            self.stuck_steps += 1
        if self.stuck_steps >= self.threshold and not self.fired:
            self.fired = True
            return True
        return False
