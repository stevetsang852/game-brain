"""RuleBattleBrain: type chart + simple damage estimate (notes/battle-brain.md section 4).

Acts only while ``ram["in_battle"] is True`` *and* ``ram["battle"]`` is present; otherwise it
raises ``BrainUnavailable`` so the next brain decides (``--brains battle,path,rule``).

Per step:

1. ``player``/``opponent`` still None (first ~5 observations of a battle) -> wait, no buttons.
2. ``menu == "other"`` (text, animations) -> B (advances text; harmless in the menus).

Stale data: ``outcome`` and the mon data in RAM are **not cleared** after a battle (Backend), so
the first observations of a later battle can show the previous battle's values. Therefore:

* ``outcome`` is reported but never used as a terminal signal: the battle is over when
  ``in_battle`` turns False.
* Mon data is only trusted after this battle has shown an input menu (``action``/``move``,
  which the game only opens once ``gBattleMons`` is filled in) with both HP > 0. Before that,
  the brain only waits or advances text, and never chooses an option.
3. Executing an intent -> next button from :class:`BattleCompiler` (closed-loop on menu/cursor).
4. In the action or move menu -> enumerate legal options, score, choose, start the compiler.

Options. FIGHT is one option per move slot with PP > 0, read from ``ram["battle"]`` and never
assumed from the species; Struggle if no PP is left. RUN only with ``allow_run=True``, because
the battle type is not observable and trainer battles refuse to run. SWITCH/ITEM are not offered:
party and bag are not observable yet.

Confidence = (softmax share of the chosen option's score) x (1 if its damage estimate is
reliable else 0.5). A single legal option is a forced choice, so there is no handoff, but the
confidence still reports how unpredictable it is (METRONOME -> 0.5). Below
``confidence_threshold`` (default 0.6) with more than one option:

* in ASSIST mode -> ``handoff=True``: wait (no buttons) up to ``handoff_steps`` for the human,
  then act on the brain's own choice;
* in AUTO/SHADOW (nobody to hand to) -> act, with the low confidence logged.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ...schema import Action, ButtonPress, Decision, Mode, Observation
from ..base import Brain, BrainUnavailable
from .actions import BattleAction
from .compiler import BattleCompiler, CompileError
from .estimate import Estimate, estimate
from .state import BattleState

STATUS_SCORE = 0.02   # Growl & co.: only if nothing does damage
RUN_SCORE = 0.9       # allow_run=True: prefer running (e.g. avoiding wild battles)


class RuleBattleBrain(Brain):
    name = "battle"

    def __init__(self, confidence_threshold: float = 0.6, handoff_steps: int = 20, allow_run: bool = False,
                 temperature: float = 0.1, wait_frames: int = 8, max_wait_unready: int = 6):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be in [0, 1]")
        self.confidence_threshold = confidence_threshold
        self.handoff_steps = handoff_steps
        self.allow_run = allow_run
        self.temperature = temperature
        self.wait_frames = wait_frames
        self.max_wait_unready = max_wait_unready
        self.mode: Mode = Mode.AUTO
        self.reset()

    def reset(self) -> None:
        self._compiler: Optional[BattleCompiler] = None
        self._handoff_waits = 0
        self._unready = 0
        self._trusted = False   # this battle has shown an input menu with consistent mon data
        self.stats = {"choices": 0, "handoffs": 0, "presses": 0, "low_confidence": 0}

    def set_mode(self, mode: Mode) -> None:
        """Called by the arbiter before every decide() (handoff only makes sense in ASSIST)."""
        self.mode = Mode.parse(mode) if not isinstance(mode, Mode) else mode

    def observe(self, obs: Observation) -> dict:
        if obs.ram.get("in_battle") is not True:
            self._compiler, self._handoff_waits, self._unready, self._trusted = None, 0, 0, False
        return {}

    @property
    def src(self) -> str:
        return f"brain:{self.name}"

    # ------------------------------------------------------------------ options
    def options(self, bs: BattleState) -> List[Tuple[BattleAction, str, float, Estimate]]:
        """[(action, label, score, estimate)] of the legal options right now."""
        out = []
        usable = [(i, m) for i, m in enumerate(bs.player.moves) if m.pp > 0]
        for i, m in usable:
            est = estimate(bs.player, bs.opponent, m.id)
            score = STATUS_SCORE if est.status else est.fraction
            label = f"{m.name} (PP {m.pp})"
            if est.effectiveness != 1.0 and not est.status:
                label += f" x{est.effectiveness:g}"
            out.append((BattleAction("FIGHT", slot=i), label, score, est))
        if not usable:
            est = estimate(bs.player, bs.opponent, None)
            out.append((BattleAction("FIGHT"), "struggle (no PP left)", est.fraction, est))
        if self.allow_run:
            out.append((BattleAction("RUN"), "run", RUN_SCORE, Estimate(0, 0, 1, True, note="run")))
        return out

    def choose(self, opts) -> Tuple[int, float, List[float]]:
        """(index of the chosen option, confidence, softmax shares)."""
        scores = [o[2] for o in opts]
        best = max(range(len(opts)), key=lambda i: (scores[i], -i))   # ties -> lowest slot (deterministic)
        mx = max(scores)
        ex = [math.exp((s - mx) / self.temperature) for s in scores]
        shares = [e / sum(ex) for e in ex]
        conf = shares[best] * (1.0 if opts[best][3].reliable else 0.5)
        return best, conf, shares

    # ------------------------------------------------------------------ decide
    def _dec(self, bs: BattleState, plan: str, reason: str, **kw) -> Decision:
        return Decision(brain=self.name, plan=plan, reason=reason, battle=bs.summary(), **kw)

    def _wait(self) -> Action:
        return Action.wait(self.wait_frames, source=self.src)

    def decide(self, obs: Observation):
        ram = obs.ram
        if ram.get("in_battle") is not True:
            self._compiler, self._handoff_waits, self._unready, self._trusted = None, 0, 0, False
            raise BrainUnavailable("not in battle (in_battle is not True)")
        bs = BattleState.from_ram(ram)
        if bs is None:
            raise BrainUnavailable("in battle but ram['battle'] is not available")

        if not self._trusted and bs.ready and bs.menu in ("action", "move") and self._consistent(bs):
            self._trusted = True
        if not self._trusted:
            if bs.ready and bs.menu == "other":
                return self._tap("B"), self._dec(
                    bs, "battle starting", "mon data not trusted yet (may be the previous battle's) -> B "
                    "to advance text")
            bs = BattleState(bs.menu, bs.cursor, None, None, bs.outcome) if not self._consistent(bs) else bs
        if not bs.ready:   # first observations of a battle: gBattleMons not filled in yet
            # The adapter hides mon data until the battle's first menu, so "not ready" is the
            # intro text ("Wild X appeared!", "Go! ..."): after a few waits, press B every other step.
            self._unready += 1
            if self._unready <= self.max_wait_unready or (self._unready - self.max_wait_unready) % 2 == 0:
                return self._wait(), self._dec(bs, "battle starting", "player/opponent not known yet -> wait")
            return self._tap("B"), self._dec(bs, "battle starting",
                                             "still no battle data after waiting -> B (advance text)")
        self._unready = 0

        if self._compiler is not None and not self._compiler.done:
            try:
                act, why = self._compiler.step(bs)
            except CompileError as exc:
                self._compiler = None
                raise BrainUnavailable(str(exc))
            intent = self._compiler.intent.id
            if self._compiler.done:
                self._compiler = None
            return act, self._dec(bs, f"executing {intent}", why, intent=intent)
        self._compiler = None

        if bs.menu == "other":
            plan = f"battle text (outcome: {bs.outcome})" if bs.outcome else "battle text"
            return self._tap("B"), self._dec(bs, plan, "menu 'other' (text/animation) -> B to advance")

        opts = self.options(bs)
        best, conf, shares = self.choose(opts)
        chosen, label, score, est = opts[best]
        options = [{"id": o[0].id, "label": o[1], "score": o[2]} for o in opts]
        why = (f"{label}: est. {est.expected:.1f} dmg = {est.fraction:.0%} of foe HP"
               + (f"; {est.note}" if est.note else ""))
        if len(opts) == 1:
            why = f"only legal option -> {why}"
        common = dict(battle_options=options, chosen_option=chosen.id, confidence=conf)

        low = conf < self.confidence_threshold and len(opts) > 1
        if low:
            self.stats["low_confidence"] += 1
            if self.mode is Mode.ASSIST and self._handoff_waits < self.handoff_steps:
                self._handoff_waits += 1
                self.stats["handoffs"] += 1
                return self._wait(), self._dec(
                    bs, "waiting for a human", f"confidence {conf:.2f} < {self.confidence_threshold} -> "
                    f"handoff ({self._handoff_waits}/{self.handoff_steps}); my pick: {chosen.id} ({why})",
                    handoff=True, **common)
            why += f"; confidence {conf:.2f} < {self.confidence_threshold}, no human ({self.mode.value}) -> act"
        self._handoff_waits = 0
        self.stats["choices"] += 1
        self._compiler = BattleCompiler(chosen, source=self.src)
        try:
            act, how = self._compiler.step(bs)
        except CompileError as exc:
            self._compiler = None
            raise BrainUnavailable(str(exc))
        if self._compiler.done:
            self._compiler = None
        return act, self._dec(bs, f"{chosen.id}: {label}", f"{why} | {how}", intent=chosen.id,
                              handoff=False if low else None, **common)

    @staticmethod
    def _consistent(bs: BattleState) -> bool:
        if not bs.ready:
            return False
        p, o = bs.player, bs.opponent
        o_hp = o.hp if o.hp is not None else o.hp_pct
        return bool(p.hp and p.hp > 0 and o_hp and o_hp > 0 and (p.max_hp is None or p.hp <= p.max_hp))

    def _tap(self, button: str) -> Action:
        self.stats["presses"] += 1
        return Action([ButtonPress(button, 2, 14)], source=self.src)
