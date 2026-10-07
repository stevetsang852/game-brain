"""Arbiter.

Modes (``game_brain.schema.Mode``):

* AUTO   -- choose a low-level brain from the situation, then execute its action.
* ASSIST -- same choice as AUTO, but queued human actions preempt the brain.
* MANUAL -- brains are not consulted. Only dashboard actions are executed.
* SHADOW -- same choice as AUTO, but the action is not executed.

Brain selection in AUTO, ASSIST, and SHADOW uses :func:`select_order`: battle asks
``llm`` then ``battle``; a verified route asks ``path``; an unverified probe asks
``llm`` then ``path``; ``rl_ready`` asks ``rl`` outside battle. A brain that raises
``BrainUnavailable`` or any exception is skipped. Original order is kept inside a role.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Sequence

from ..brain import Brain, BrainUnavailable
from ..schema import Action, Decision, Mode, ModeCommand, Observation
from .select import select_order

HUMAN_INPUT_MODES = (Mode.MANUAL, Mode.ASSIST)
SITUATION_MODES = (Mode.AUTO, Mode.ASSIST, Mode.SHADOW)


@dataclass
class StepResult:
    mode: Mode
    decision: Decision
    proposed: Optional[Action]
    executed: Optional[Action]
    notes: List[str] = field(default_factory=list)


class Arbiter:
    def __init__(self, brains: Sequence[Brain], mode: "Mode | str" = Mode.AUTO, idle_frames: int = 8):
        if not brains:
            raise ValueError("arbiter needs at least one brain")
        self.brains = list(brains)
        self.mode = Mode.parse(mode)
        self.idle_frames = idle_frames
        self._manual: Deque[Action] = deque()
        self.rejected: List[str] = []
        self.mode_history: List[ModeCommand] = [ModeCommand(self.mode, issued_by="init")]

    def apply_mode(self, cmd: "ModeCommand | Mode | str") -> Mode:
        if not isinstance(cmd, ModeCommand):
            cmd = ModeCommand(Mode.parse(cmd))
        self.mode = cmd.mode
        self.mode_history.append(cmd)
        if self.mode not in HUMAN_INPUT_MODES:
            self._manual.clear()
        return self.mode

    def submit_manual(self, action: Action, origin: str = "dashboard") -> bool:
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

    def _ask_brains(self, obs: Observation, notes: List[str]):
        context: dict = {}
        brains = select_order(self.brains, obs) if self.mode in SITUATION_MODES else list(self.brains)
        for brain in brains:
            try:
                if hasattr(brain, "set_mode"):
                    brain.set_mode(self.mode)
                action, decision = brain.decide(obs)
                for later in self.brains:
                    if later is brain or not hasattr(later, "observe"):
                        continue
                    try:
                        for k, v in (later.observe(obs) or {}).items():
                            context.setdefault(k, v)
                    except Exception as exc:
                        notes.append(f"{later.name} observe error: {type(exc).__name__}: {exc}")
                for k, v in context.items():
                    if getattr(decision, k, None) is None:
                        setattr(decision, k, v)
                return brain, action, decision
            except BrainUnavailable as exc:
                notes.append(f"{brain.name} unavailable: {exc}")
                for k, v in getattr(exc, "context", {}).items():
                    context.setdefault(k, v)
            except Exception as exc:
                notes.append(f"{brain.name} error: {type(exc).__name__}: {exc}")
        self._unavailable_context = context
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
            for k, v in getattr(self, "_unavailable_context", {}).items():
                setattr(dec, k, v)
            return StepResult(mode, dec, None, act, notes)

        dec.mode = mode.value
        dec.actor = "brain"
        if notes:
            dec.reason = f"{dec.reason} [fallback: {'; '.join(notes)}]"

        if mode is Mode.SHADOW:
            dec.executed = False
            return StepResult(mode, dec, proposed, Action.wait(max(1, proposed.total_frames)), notes)

        dec.executed = True
        return StepResult(mode, dec, proposed, proposed, notes)

    def reset(self) -> None:
        for b in self.brains:
            b.reset()
        self._manual.clear()
