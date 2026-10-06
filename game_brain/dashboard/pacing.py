"""Display pacing for the live dashboard: how fast the loop steps and how often it ships a frame.

Two modes, switched from the page with a ``view_config`` message:

* ``manual``: the loop runs ``fps`` steps per second (1-60) and attaches a screenshot every
  ``screenshot_every`` steps (the CLI value).
* ``auto``: the slider value is a ceiling. A screenshot is only attached when the page has
  acknowledged (``frame_ack``) the previous one, so frames never pile up in the socket or the
  browser; the step rate follows how long the page takes to receive and draw a frame
  (send -> ack round trip, smoothed), i.e. ``1 / latency`` clamped to 1..ceiling. With no tab open, or an adapter without screenshots,
  auto simply runs at the ceiling.

Before any page message the CLI's ``--step-delay`` is honoured exactly (``mode == "cli"``), so
scripts and tests behave as before. Pacing only changes wall-clock sleeps and whether a PNG is
attached to the live envelope; it never touches the emulator, the arbiter or the run log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

FPS_MIN, FPS_MAX = 1, 60
MODES = ("manual", "auto")
ACK_TIMEOUT = 1.0  # s; an unacknowledged frame older than this no longer blocks the next one


class PacingError(ValueError):
    pass


@dataclass
class ViewConfig:
    mode: str
    fps: float

    @classmethod
    def from_envelope(cls, env: Dict[str, Any]) -> "ViewConfig":
        p = env.get("payload")
        if not isinstance(p, dict):
            raise PacingError("view_config needs a payload object")
        mode = p.get("mode")
        if mode not in MODES:
            raise PacingError(f"view_config mode must be one of {MODES}, got {mode!r}")
        fps = p.get("fps")
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps != fps:  # NaN
            raise PacingError(f"view_config fps must be a number, got {fps!r}")
        return cls(mode, float(min(FPS_MAX, max(FPS_MIN, fps))))  # out of range -> clamped


@dataclass
class FrameAck:
    frame: int
    received: float

    @classmethod
    def from_envelope(cls, env: Dict[str, Any], now: Optional[float] = None) -> "FrameAck":
        f = env.get("frame")
        if isinstance(f, bool) or not isinstance(f, int) or f < 0:
            raise PacingError(f"frame_ack frame must be a non-negative integer, got {f!r}")
        return cls(f, time.monotonic() if now is None else now)


class _Rate:
    """Exponential moving average of events per second."""

    def __init__(self, alpha: float = 0.3):
        self.alpha, self.last, self.value = alpha, None, None

    def tick(self, now: float) -> None:
        if self.last is not None and now > self.last:
            inst = 1.0 / (now - self.last)
            self.value = inst if self.value is None else self.value + self.alpha * (inst - self.value)
        self.last = now


class Pacer:
    def __init__(self, step_delay: float = 0.25, screenshot_every: int = 1, clock=time.monotonic):
        self.clock = clock
        self.mode = "cli"
        self.step_delay = max(0.0, step_delay)
        self.fps = min(FPS_MAX, max(FPS_MIN, 1.0 / step_delay)) if step_delay > 0 else FPS_MAX
        self.screenshot_every = max(0, screenshot_every)
        self._inflight: Optional[tuple] = None  # (frame, sent_at)
        self.step_rate, self.frame_rate = _Rate(), _Rate()
        self.latency: Optional[float] = None  # smoothed send -> ack seconds (auto mode)

    # ---------------------------------------------------------------- inputs
    def apply(self, cfg: ViewConfig) -> str:
        self.mode, self.fps = cfg.mode, cfg.fps
        self._inflight = None
        self.latency = None
        return f"display -> {self.mode} {self.fps:g} fps"

    def ack(self, a: FrameAck) -> None:
        if self._inflight and a.frame >= self._inflight[0]:
            rtt = max(1e-3, a.received - self._inflight[1])
            self.latency = rtt if self.latency is None else self.latency + 0.3 * (rtt - self.latency)
            self._inflight = None

    # ---------------------------------------------------------------- per step
    def want_screenshot(self, step: int, has_clients: bool = True) -> bool:
        if self.mode != "auto":
            return bool(self.screenshot_every) and step % self.screenshot_every == 0
        if not has_clients:
            return False
        return True

    def sent_screenshot(self, frame: int) -> None:
        now = self.clock()
        self.frame_rate.tick(now)
        if self.mode == "auto":
            self._inflight = (frame, now)

    def target_fps(self) -> Optional[float]:
        """Steps per second the loop aims for; None = honour the CLI step delay."""
        if self.mode == "cli":
            return None
        if self.mode == "auto" and self.latency is not None:
            return min(self.fps, max(FPS_MIN, 1.0 / self.latency))
        return self.fps

    def sleep_after(self, step_started: float) -> float:
        """Seconds to sleep after a step that began at ``step_started`` (clock time)."""
        target = self.target_fps()
        if target is None:
            return self.step_delay
        return max(0.0, 1.0 / target - (self.clock() - step_started))

    def stepped(self) -> None:
        self.step_rate.tick(self.clock())

    def status(self) -> Dict[str, Any]:
        r = lambda x: None if x.value is None else round(x.value, 1)
        t = self.target_fps()
        return {"mode": self.mode, "fps": round(self.fps, 1),
                "target_fps": round(t, 1) if t is not None else (round(1 / self.step_delay, 1) if self.step_delay else None),
                "actual_fps": r(self.step_rate), "frame_fps": r(self.frame_rate),
                "page_ms": None if self.latency is None else round(self.latency * 1000)}
