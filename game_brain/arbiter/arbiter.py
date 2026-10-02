"""Arbiter.

Modes (``game_brain.schema.Mode``):

* AUTO   -- the first available brain decides; its action is executed.
* ASSIST -- the brain acts as in AUTO, but actions submitted via
            :meth:`Arbiter.submit_manual` (dashboard/human) jump the queue: while any are
            queued, the oldest one is executed instead of consulting the brain
            (``Decision.actor == "human"``). Once the queue is empty the brain resumes.
* MANUAL -- brains are not consulted and any brain action is rejected. Only actions
            submitted via :meth:`Arbiter.submit_manual` (dashboard/human) are executed;
            with none queued, the game idles for ``idle_frames``.
* SHADOW -- the brain decides and the Decision + proposed Action are logged, but the
            action is NOT executed; the game idles for the proposal's duration so time
            still moves forward. Dashboard actions are rejected.

Dashboard/manual actions are accepted only in MANUAL and ASSIST; AUTO and SHADOW reject them.
Every Decision carries ``actor`` ("brain" | "human" | "none") so the run log records
who actually acted; replay just re-executes ``executed_action`` and stays deterministic.

Brain selection: ``brains`` is a priority list. A brain raising ``BrainUnavailable``
(or any exception) is skipped and the next one is used; the skip is recorded.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Sequence

from ..brain import Brain, BrainUnavailable
from ..schema import Action, Decision, Mode, ModeCommand, Observation

#: Modes in which human/dashboard actions are accepted.
HUMAN_INPUT_MODES = (Mode.MANUAL, Mode.ASSIST)


@dataclass
class StepResult:
    mode: Mode
    decision: Decision
    proposed: Optional[Action]          # what a brain wanted (None in MANUAL)
    executed: Optional[Action]          # what was actually sent to the adapter (may be an idle wait)
    notes: List[str] = field(default_factory=list)  # fallbacks / rejections this step


class Arbiter:
    def __init__(self, brains: Sequence[Brain], mode: "Mode | str" = Mode.AUTO, idle_frames: int = 8):
        if not brains:
            raise ValueError("arbiter needs at least one brain")
        self.brains = list(brains)
        self.mode = Mode.parse(mode)
        self.idle_frames = idle_frames
        self._manual: Deque[Action] = deque()
        self.rejected: List[str] = []   # audit trail of rejected inputs
        self.mode_history: List[ModeCommand] = [ModeCommand(self.mode, issued_by="init")]

    # ------------------------------------------------------------------ inputs
    def apply_mode(self, cmd: "ModeCommand | Mode | str") -> Mode:
        if not isinstance(cmd, ModeCommand):
            cmd = ModeCommand(Mode.parse(cmd))
        self.mode = cmd.mode
        self.mode_history.append(cmd)
        if self.mode not in HUMAN_INPUT_MODES:
            self._manual.clear()  # don't let stale human input leak into brain-only modes
        return self.mode

    def submit_manual(self, action: Action, origin: str = "dashboard") -> bool:
        """Queue a human/dashboard action. Accepted only in MANUAL and ASSIST modes."""
        if self.mode not in HUMAN_INPUT_MODES:
            self.rejected.append(f"{origin} action rejected: mode is {self.mode.value}, "
                                 "actions only accepted in manual or assist")
            return False
        if action.source.startswith("brain"):
            self.rejected.append(f"{origin} action rejected: source {action.source!r} is a brain")
            return False
        self._manual.append(Action(list(action.presses), source="manual"))
        return True

    @property
    def pending_manual(self) -> int:
        return len(self._manual)

    # ------------------------------------------------------------------ step
    def _ask_brains(self, obs: Observation, notes: List[str]):
        for brain in self.brains:
            try:
                action, decision = brain.decide(obs)
                return brain, action, decision
            except BrainUnavailable as exc:
                notes.append(f"{brain.name} unavailable: {exc}")
            except Exception as exc:  # a buggy brain must not kill the run
                notes.append(f"{brain.name} error: {type(exc).__name__}: {exc}")
        return None, None, None

    def step(self, obs: Observation) -> StepResult:
        notes: List[str] = []
        mode = self.mode

        if mode is Mode.MANUAL:
            if self._manual:
                act = self._manual.popleft()
                dec = Decision(brain="human", plan="manual control", reason="executing queued manual action",
                               mode=mode.value, executed=True, actor="human")
            else:
                act = Action.wait(self.idle_frames)
                dec = Decision(brain="human", plan="manual control", reason="no manual input queued -> idle",
                               mode=mode.value, executed=True, actor="none")
            return StepResult(mode, dec, None, act, notes)

        if mode is Mode.ASSIST and self._manual:
            # Human takes over momentarily; the brain is not consulted this step.
            act = self._manual.popleft()
            dec = Decision(brain="human", plan="assist: human override",
                           reason=f"queued human action preempted the brain ({len(self._manual)} more queued)",
                           mode=mode.value, executed=True, actor="human")
            return StepResult(mode, dec, None, act, notes)

        brain, proposed, dec = self._ask_brains(obs, notes)
        if brain is None:
            act = Action.wait(self.idle_frames)
            dec = Decision(brain="none", plan="idle", reason="; ".join(notes) or "no brain available",
                           mode=mode.value, executed=True, actor="none")
            return StepResult(mode, dec, None, act, notes)

        dec.mode = mode.value
        dec.actor = "brain"
        if notes:
            dec.reason = f"{dec.reason} [fallback: {'; '.join(notes)}]"

        if mode is Mode.SHADOW:
            dec.executed = False
            return StepResult(mode, dec, proposed, Action.wait(max(1, proposed.total_frames)), notes)

        # AUTO and ASSIST
        dec.executed = True
        return StepResult(mode, dec, proposed, proposed, notes)

    def reset(self) -> None:
        for b in self.brains:
            b.reset()
        self._manual.clear()
